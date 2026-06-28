# Implementation Guide — Ghibli Portrait API

Technical reference for the internal implementation. Generation backend:
**BytePlus ARK (Seedream)** — synchronous REST (no webhook, no ngrok).

## Table of Contents
1. [Concurrency Model](#concurrency-model)
2. [Generation Architecture](#generation-architecture)
3. [Validation Implementation](#validation-implementation)
4. [Stage Separation](#stage-separation)
5. [Error Handling](#error-handling)
6. [File & Responsibility Mapping](#file--responsibility-mapping)
7. [Configuration](#configuration)

---

## Concurrency Model

All network I/O is **async (httpx)** — the event loop is never blocked by HTTP.
Only CPU-bound work (MediaPipe face detection, PIL) runs in a thread pool
(`ThreadPoolExecutor(max_workers=100)` set in `main.py` lifespan).

Two semaphores cap load:

| Semaphore | Env var | Default | Scope |
|---|---|---|---|
| `_mediapipe_sem` | `MAX_MEDIAPIPE_CONCURRENCY` | 15 | wraps only the MediaPipe call (~2s), not the download |
| `_gen_sem` | `GENERATION_CONCURRENCY_LIMIT` | 8 | concurrent ARK submissions (ARK allows ≤10/model/account) |

Requests beyond a semaphore's limit **wait** (queue) and proceed as slots free —
nothing is rejected for concurrency. `_submit_generation` additionally retries up
to 3× on a provider rate-limit response (5s backoff).

**Single-process constraint:** `pending_tasks` (in `routes.py`) is an in-memory
dict, so the app must run with `--workers 1`. Horizontal scaling requires moving
`pending_tasks` to a shared store (e.g. Redis).

---

## Generation Architecture

BytePlus ARK is **synchronous** — `images/generations` returns the result image
URL inline. To keep the orchestration in `routes.py` unchanged, the result is
delivered through an in-process Future:

```
routes.py:
    res = await _submit_generation([img], prompt, model=...)   # → image_service.generate_img
    task_id = res["data"]["taskId"]
    fut = loop.create_future(); pending_tasks[task_id] = fut
    result = await asyncio.wait_for(fut, timeout=600)          # CallbackRequest

image_service.generate_img (ARK adapter):
    1. inline images as base64 (so ARK needn't fetch from this server)
    2. call seedream_service.seedream_generate(...)            # synchronous ARK REST
    3. build a CallbackRequest with the result URL + a synthetic taskId
    4. schedule _deliver() → resolves pending_tasks[taskId] (mirrors the old webhook)
    5. return {"code": 200, "data": {"taskId": ...}}           # KIE-style envelope
```

This preserves the previous request/response contract exactly: `generate_img`
returns the same `{code, data:{taskId}}` shape, and the Future resolves with the
same `CallbackRequest` object. **No public callback URL / ngrok is needed.**

`seedream_service.py` holds the minimal ARK core (`seedream_generate`, `_first_url`,
`ARK_*` settings). Test-only pipeline helpers + refined prompts live in
`tests/manual/seedream_pipeline.py`.

---

## Validation Implementation

Layered, non-overlapping (`services/validation_service.py`):

- **Layer 1 — Source resolution:** http/https only; reject localhost & private IPs.
- **Layer 2 — Decode:** async httpx download + PIL decode.
- **Layer 3A — Stage 1 (human portrait):** MediaPipe BlazeFace (CPU). Rules:
  - 0 faces → `NO_FACE_DETECTED`
  - multiple prominent reliable faces → `MULTIPLE_FACES`
  - synthetic/3D/cartoon (color-diversity + pixel-uniformity) → `NOT_REAL_PHOTO`
  - detector error → `FACE_DETECTOR_FAILURE` (SYSTEM_ERROR)
- **Layer 3B — Stage 2 (trust):** Stage 1 output is trusted; only a URL sanity check.

The FaceDetector is a module-level singleton. Skin tone is extracted (YCbCr) from
the validated image and injected into the Stage 1 prompt.

> `MAX_FACES` / `MIN_FACE_AREA_RATIO` exist in config but are not enforced in the
> current validation logic (matches the latest branch behavior).

---

## Stage Separation

- Stage 1 (Ghibli) and Stage 2 (QR composition) are independent ARK calls.
- Stage 1 output is **re-hosted locally** (downloaded, saved under `static/tmp/`,
  served from this server) before Stage 2 and before returning to the client.
- The QR-lock image is generated locally with PIL (`qr_service.get_qr`).
- Stage 2 retries (up to 3×) only when the QR is not detected in the merged image.

---

## Error Handling

- Validation failures → 422 with a structured `ApiError`.
- Provider/submission failures → `STAGE1_API_ERROR` / `STAGE2_API_ERROR` (500).
- Stage timeouts → `STAGE1_TIMEOUT` / `STAGE2_TIMEOUT` (504).
- Any unhandled exception → `INTERNAL_ERROR` (500) via the global handler.
- All responses use the unified envelope (`responses.py`).

---

## File & Responsibility Mapping

| File | Responsibility |
|---|---|
| `main.py` | FastAPI app, lifespan (thread pool, tmp cleanup loop), exception handlers, `/tmp` mount |
| `api/routes.py` | V1 endpoints, pipeline orchestration, `pending_tasks`, `_gen_sem`, `_submit_generation` |
| `api/responses.py` | Unified response/error envelope helpers |
| `services/image_service.py` | `generate_img` — **BytePlus ARK adapter** (inline images, deliver via pending_tasks) |
| `services/seedream_service.py` | ARK core: `seedream_generate`, `_first_url`, `ARK_*` settings |
| `services/validation_service.py` | URL / face / synthetic / skin-tone validation |
| `services/identity_check.py` | Optional post-Stage-1 identity drift check |
| `services/qr_service.py` | QR-on-lock image (PIL) |
| `services/qr_validation.py` | QR scannability check (QReader/pyzbar) |
| `utils/url_utils.py` | Deterministic URL shortening |
| `config.py` | Settings + prompts |
| `models/schemas.py` | Request/response schemas (camelCase) |
| `tests/manual/seedream_pipeline.py` | Test-only pipeline helpers + refined prompts |

---

## Configuration

See `.env.example` for the full list. Key variables:

```bash
DOMAIN=http://<host>:30820     # base address for returned image URLs
ARK_API_KEY=<byteplus-ark-key> # generation credential
# ARK_MODEL / ARK_IMAGE_SIZE / ARK_SEED / ARK_WATERMARK  (optional overrides)
GHIBLI_MODEL=qwen/image-edit   # response "model" label (Stage 1)
COMPOSE_MODEL=seedream/4.5-edit# response "model" label (Stage 2)
REQUIRE_HUMAN_FACE=true
MAX_MEDIAPIPE_CONCURRENCY=15
GENERATION_CONCURRENCY_LIMIT=8 # ARK allows ≤10 concurrent per model
SAVE_OUTPUT_LOCAL=false        # also save final images under OUTPUT_DIR
OUTPUT_DIR=output
```

Prompts (`PROMPT_PIC_TO_GHIBLI`, `PROMPT_GHIBLI_LOCK`) live in `config.py` and are
used by the `/v1` API. The refined prompt variants used by the manual test scripts
live in `tests/manual/seedream_pipeline.py`.
