# Implementation Guide — Ghibli Portrait API

Complete technical reference for the internal implementation.

## Table of Contents
- [Concurrency Model](#concurrency-model)
- [Async Architecture](#async-architecture)
- [Validation Implementation](#validation-implementation)
- [Stage Separation Guarantees](#stage-separation-guarantees)
- [Error Handling & Failure Isolation](#error-handling--failure-isolation)
- [File & Responsibility Mapping](#file--responsibility-mapping)
- [Configuration](#configuration)
- [Scaling Path (Redis)](#scaling-path-redis)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)

---

## Concurrency Model

The server is built for high concurrency without blocking the event loop.

### I/O Classification

| Operation | Mechanism | Rationale |
|---|---|---|
| Image downloads (validation, rehost, identity) | `httpx` async | Network I/O — never block event loop |
| KIE.ai API submission | `httpx` async | Network I/O — never block event loop |
| Waiting for KIE webhook | `asyncio.Future` | Zero resource cost while waiting |
| MediaPipe face detection | `asyncio.to_thread` | CPU-bound — must run off event loop |
| PIL resize + JPEG save | `asyncio.to_thread` | File I/O + CPU — keep event loop free |
| QR code generation | `asyncio.to_thread` | PIL + local disk I/O |

### Thread Pool

Configured in `main.py` via the `lifespan` context:

```python
loop.set_default_executor(ThreadPoolExecutor(max_workers=100))
```

The default Python pool is `min(32, cpu+4)` — on a 4-core machine that's 8 threads. This saturates almost instantly under any real load.

### MediaPipe Concurrency Semaphore

An `asyncio.Semaphore` wraps the face-detection call in both pipeline endpoints:

```python
# routes.py — module level
_mediapipe_sem = asyncio.Semaphore(int(s.MAX_MEDIAPIPE_CONCURRENCY))  # default 15

# inside automated_pipeline and transform2ghibli
# Semaphore is passed as parameter — applied inside validate_real_human_image_async
# only around the MediaPipe call (~2s), not the image download (~10s).
validation_result = await validate_real_human_image_async(
    url, settings=s, mediapipe_sem=_mediapipe_sem
)
```

This caps concurrent CPU usage regardless of thread pool size. Requests beyond the limit wait in the asyncio event loop (zero threads, zero CPU) until a slot opens (~2.5s per slot).

The semaphore wraps **only the MediaPipe thread call** — not the download. A request holds the slot for ~2s instead of ~12s, so 500 requests clear in ~67s instead of ~400s.

**Why semaphore and not just reducing thread pool size?**
The thread pool is shared by PIL and QR generation too. Reducing it would also throttle those operations. The semaphore targets only MediaPipe.

**Tuning `MAX_MEDIAPIPE_CONCURRENCY` (set in `.env`):**

| Value | Peak CPU | Extra wait at 100 req | Use case |
|---|---|---|---|
| `5` | ~5% | ~50s | Heavily shared server |
| `15` | ~15% | ~17s | Default — shared server |
| `30` | ~30% | ~8s | Dedicated server, high load |
| `100` | ~100% | ~0s | No limit (same as before) |

### Thread Occupation Per Request — Before vs After

The core improvement: network I/O no longer occupies threads. Threads are reserved for CPU work only.

| Phase | Before (fix/ghibli-pipeline) | After (mohammad) |
|---|---|---|
| Image download — validation | ~10s in thread (`requests`) | 0s — async httpx |
| MediaPipe face detection | ~2s in thread | ~2s in thread (CPU, unavoidable) |
| KIE API submission | ~2s in thread (`requests`) | 0s — async httpx |
| Waiting for Stage 1 webhook | 0s (async Future) | 0s (async Future) |
| Stage 1 rehost download | ~8s in thread (`requests`) | 0s — async httpx |
| PIL resize + JPEG save | ~0.5s in thread | ~0.5s in thread (CPU, unavoidable) |
| Identity check download | ~8s in thread (`requests`) | 0s — async httpx |
| Identity check compute | ~2s in thread | ~2s in thread (CPU, unavoidable) |
| Stage 2 API submission | ~2s in thread (`requests`) | 0s — async httpx |
| Waiting for Stage 2 webhook | 0s (async Future) | 0s (async Future) |
| **Total thread time per request** | **~34s** | **~2.5s** |

> Note: identity check is disabled by default (`ENABLE_IDENTITY_CHECK=false`). Without it, total thread time is ~2.5s per request.

### Concurrent Request Capacity

```
Before (fix/ghibli-pipeline):
  8 threads / 34s thread time = ~0.2 req/s → 2-3 simultaneous requests max

After (mohammad):
  Semaphore(15) / 2.5s thread time = 6 req/s through MediaPipe
  → requests queue in async (zero cost), all then await KIE async
  → real bottleneck at high load is KIE API rate limits, not the server
```

### Single-Process Constraint

`pending_tasks: Dict[str, asyncio.Future]` is an in-memory dict. All requests and their webhook callbacks **must hit the same OS process**. Running multiple uvicorn workers (`--workers N`) breaks the webhook resolution — a callback arriving at Worker 2 cannot resolve a Future registered by Worker 1.

See [Scaling Path (Redis)](#scaling-path-redis) for how to remove this constraint.

---

## Async Architecture

### Webhook Processing Model

```
POST /v1/ghibli-qr
    │
    ├── validate_real_human_image_async()
    │       ├── httpx.AsyncClient.get(img_url)   ← async, no thread
    │       └── asyncio.to_thread(mediapipe)     ← ~2s thread
    │
    ├── await generate_img(...)                  ← async httpx POST to KIE
    │
    ├── asyncio.get_running_loop().create_future()
    ├── pending_tasks[task_id] = future
    │
    ├── asyncio.to_thread(_gen_qr_lock)          ← ~0.5s thread (parallel with KIE)
    │
    ├── await asyncio.wait_for(future, 600)      ← sleeps, 0 resources used
    │       └── webhook arrives → future.set_result(req)
    │
    ├── httpx.AsyncClient.get(ghibli_url)        ← async rehost download
    ├── asyncio.to_thread(_save_stage1)          ← ~0.5s PIL thread
    │
    ├── await generate_img(...)                  ← async httpx POST Stage 2
    ├── pending_tasks[task_id_2] = future_2
    ├── await asyncio.wait_for(future_2, 600)    ← sleeps, 0 resources
    │
    └── return JSONResponse
```

### Future Pattern

```python
# Register before the webhook can arrive
task_id = res["data"]["taskId"]
future = asyncio.get_running_loop().create_future()
pending_tasks[task_id] = future

# Webhook handler — accepts raw Request to prevent Pydantic 422 before handler runs.
# Always returns 200 to KIE regardless of payload validity.
async def webhook(raw_request: Request):
    body = await raw_request.json()
    req = CallbackRequest.model_validate(body)   # inside try/except in real code
    future = pending_tasks.pop(req.data.taskId, None)
    if future and not future.done():
        future.set_result(req)
    return JSONResponse(content={"code": 200, "msg": "ok"})

# Timeout cleanup handled by finally block
try:
    result = await asyncio.wait_for(future, timeout=600)
except asyncio.TimeoutError:
    ...
finally:
    pending_tasks.pop(task_id, None)
```

---

## Validation Implementation

### Layer Architecture

```
validate_real_human_image_async(image_url)          ← async entry point
    │
    ├── Layer 1: validate_source_resolution(url)    ← instant, pure Python
    │       └── reject localhost, private IPs, non-HTTP
    │
    ├── Layer 2: httpx.AsyncClient.get(url)         ← async download
    │       └── PIL.Image.open(bytes)               ← decode
    │
    └── Layer 3A: asyncio.to_thread(                ← CPU thread
                    validate_stage1_human_portrait,
                    img, url, settings
                  )
```

`validate_real_human_image` (sync version) still exists for legacy use. New code should use `validate_real_human_image_async`.

### Face Detection

**Technology**: MediaPipe BlazeFace (`blaze_face_short_range.tflite`)
- Pre-downloaded to `src/ghibli_portrait/models/` at server startup (via `lifespan`)
- CPU-only, no GPU dependency
- Confidence threshold: 0.35

**Decision logic**:
1. If detector fails → `FACE_DETECTOR_FAILURE` (SYSTEM_ERROR, not VALIDATION_ERROR)
2. If 0 faces → `NO_FACE_DETECTED`
3. If >1 face AND secondary ≥65% area AND ≥60% confidence of primary → `MULTIPLE_FACES`
4. Otherwise → ACCEPT

**Priority**: Human inclusivity over aggressive rejection. Accepting an edge-case animal photo is preferable to rejecting a real human due to appearance or detector sensitivity.

### Stage 2 Trust Model

`validate_stage2_input()` performs URL sanity only — no download, no face detection. Stage 1 output is fully trusted.

---

## Stage Separation Guarantees

| Stage | Does | Does NOT |
|---|---|---|
| Layer 1 (Source Resolution) | Validate URL format | Download anything |
| Layer 2 (Decode) | Download and decode | Apply business rules |
| Layer 3A (Stage 1) | Face detection, count | Handle QR, Stage 2 logic |
| Stage 1 (Ghibli gen) | Submit to KIE, wait webhook | Re-validate its own output |
| Identity Check | Heuristic post-gen check | Block on system errors |
| Layer 3B (Stage 2) | URL sanity on Stage 1 output | Re-download or re-validate |
| Stage 2 (QR comp) | Compose with Seedream | Re-run face detection |
| Layer 4 (Orchestration) | Coordinate stages, format responses | Add new validation rules |

---

## Error Handling & Failure Isolation

### Principles

1. Every error includes the pipeline stage where it occurred
2. Failures exit immediately — no partial processing
3. Same input → same validation result (deterministic)
4. No silent fallbacks — every failure is explicitly reported
5. Task futures cleaned up in `finally` blocks regardless of outcome

### Classification

| Type | HTTP | Description |
|---|---|---|
| `VALIDATION_ERROR` | 422 | Input failed a validation rule |
| `EXTERNAL_ERROR` | 500/504 | KIE.ai API failure or timeout |
| `SYSTEM_ERROR` | 500 | Internal component failure (MediaPipe, etc.) |

---

## File & Responsibility Mapping

### `main.py`

- FastAPI app with two exception handlers: `RequestValidationError` (→ 422 JSON) and global `Exception` (→ 500 JSON instead of plain-text)
- `lifespan` context (runs before any request is accepted):
  - Creates `static/tmp/` directory if missing (`TMP_PATH.mkdir(parents=True, exist_ok=True)`) — safe on fresh clone
  - Sets `ThreadPoolExecutor(max_workers=100)` on the event loop
  - Pre-downloads MediaPipe model via `asyncio.to_thread(_ensure_model_downloaded)`
  - Starts `_tmp_cleanup_loop` background task (deletes `.jpg`/`.png` files older than 2h every 30 min)
- Mounts `/tmp` as static file server (directory check is lazy — first request, so lifespan mkdir runs first)

### `api/routes.py`

- All endpoints: `/v1/health`, `/v1/ghibli`, `/v1/qr-lock`, `/v1/ghibli/callback`, `/v1/qr-lock/{id}`, `/v1/ghibli-qr`, `/v1/qr-url/`
- Orchestrates validation → Stage 1 → rehost → identity check → Stage 2
- `pending_tasks: Dict[str, asyncio.Future]` — in-memory webhook registry
- No `import requests` — all HTTP via `httpx`

### `services/image_service.py`

```python
async def generate_img(img_urls, prompt, aspect_ratio, quality, model, negative_prompt) -> dict
```

- `async` function — uses `httpx.AsyncClient` for the KIE API POST
- Module-level `_settings = Settings()` singleton (not re-instantiated per call)
- Three payload branches: `flux-kontext`, `qwen/*`, default (seedream)
- Logs at `DEBUG` level (not WARNING) — won't pollute production logs
- No `requests` import

### `services/validation_service.py`

| Function | Type | Layer |
|---|---|---|
| `validate_source_resolution(url)` | sync | 1 |
| `validate_image_accessibility(url)` | sync (uses `requests`) | 2 |
| `_detect_faces(img)` | sync, CPU | 3A |
| `validate_stage1_human_portrait(img, url, settings)` | sync, CPU | 3A |
| `validate_stage2_input(url)` | sync | 3B |
| `validate_real_human_image(url, settings)` | sync (legacy) | 1+2+3A |
| `validate_real_human_image_async(url, settings)` | **async** | 1+2+3A |
| `validate_single_image_url_list(urls)` | sync | — |

`validate_real_human_image_async` does the download with `httpx` (async), then calls MediaPipe via `asyncio.to_thread`.

### `services/identity_check.py`

```python
async def check_identity_drift_from_url(source_url, output_img, source_timeout) -> IdentityCheckResult
```

- Downloads source image with `httpx.AsyncClient` (async)
- Runs `check_identity_drift(source_img, output_img)` via `asyncio.to_thread` (NumPy + MediaPipe, CPU-bound)
- Controlled by `ENABLE_IDENTITY_CHECK` env var (default `false`)

**Drift checks (in order):**
1. Face must exist in output
2. Face area ratio delta ≤ 30%
3. Skin-tone mean hue delta ≤ 40°

On drift detection, `automated_pipeline` retries Stage 1 once before returning `IDENTITY_DRIFT_DETECTED`.

### `services/qr_service.py`

```python
def get_qr(url, version) -> Image
```

- Synchronous PIL function — called via `asyncio.to_thread` from routes
- Generates QR, overlays on `static/lock.png`
- Proportional sizing: target 28% of lock width, clamped to [22%, 32%]
- Paste position: centered horizontally, ~46% down (torso area)

### `config.py`

Module-level `Settings` class (not a Pydantic BaseSettings — plain class with `os.getenv` reads). Instantiated once at module import time in each service that needs it.

`DOMAIN` is read with `.strip().rstrip('/')` — leading/trailing spaces in `.env` (e.g., `DOMAIN= https://...`) are silently removed so `CALL_BACK` and all `/tmp/` URLs are always valid.

Key settings groups:
- **QR**: version, fill/back color, proportional sizing ratios
- **KIE API**: key, models, callback URL, create-task endpoint
- **Validation**: `REQUIRE_HUMAN_FACE`, `MAX_FACES`, `MIN_FACE_AREA_RATIO`, `ENABLE_IDENTITY_CHECK`
- **Stage 1 qwen controls**: guidance scale, steps, acceleration, seed, format, size, fidelity params
- **Prompts**: `PROMPT_PIC_TO_GHIBLI`, `NEGATIVE_PROMPT_PIC_TO_GHIBLI`, `PROMPT_GHIBLI_LOCK`

---

## Configuration

### Full `.env` Reference

```env
# Required
DOMAIN=https://your-domain.com
KIE_API_KEY=your_key

# Models
KIE_GHIBLI_MODEL=flux-kontext-pro   # flux-kontext-pro recommended; qwen/image-edit is legacy
KIE_COMPOSE_MODEL=seedream/4.5-edit

# Validation
REQUIRE_HUMAN_FACE=true
MAX_FACES=1
MIN_FACE_AREA_RATIO=0.03
SHORT_CODE_LENGTH=8
ENABLE_IDENTITY_CHECK=false          # Enable only with flux-kontext models

# Stage 1 qwen quality controls
STAGE1_GUIDANCE_SCALE=4.0
STAGE1_NUM_INFERENCE_STEPS=28
STAGE1_ACCELERATION=none
KIE_SEED=42
KIE_OUTPUT_FORMAT=jpeg               # jpeg saves credits vs png
KIE_IMAGE_SIZE=square                # square is sufficient for intermediate Stage 1 output
```

### Model Payload Differences

| Field | flux-kontext | qwen | seedream (Stage 2) |
|---|---|---|---|
| Image input key | `input.inputImage` | `input.image_url` | `input.image_urls` (array) |
| Aspect ratio key | `input.aspectRatio` | `input.image_size` | `input.aspect_ratio` |
| Supports negative_prompt | No | Yes | — |
| Supports fidelity params | No | Yes (silently ignored) | — |

---

## Scaling Path (Redis)

### Why the Current Architecture Cannot Scale Horizontally

`pending_tasks: Dict[str, asyncio.Future]` lives in the RAM of one OS process. When a request arrives:

1. Worker A registers `pending_tasks[task_id] = future`
2. KIE finishes and sends the webhook
3. The webhook hits **any** available worker (load balancer decides)
4. If it hits Worker B — `task_id` is not in Worker B's `pending_tasks` → future never resolves → 10-minute timeout

```
Single process (works):                  Multi-process (breaks):
──────────────────────                   ───────────────────────────────
Client → Worker A                        Client → Worker A
  register future in A                     register future in A's RAM
  KIE webhook → Worker A ✓                KIE webhook → Worker B
  future resolved ✓                        task_id not in B's dict ✗
                                           timeout after 600s ✗
```

### The Fix: Redis Pub/Sub

Replace the in-memory dict with a Redis channel. Any worker can publish, and the correct waiting worker receives.

```
Client → Worker A → submit to KIE → subscribe to Redis channel "task:{id}"
                                           │
KIE webhook → Worker B (any) → publish to "task:{id}" → Worker A wakes up ✓
```

**Implementation:**

```python
# routes.py — replace pending_tasks dict with these two functions

import redis.asyncio as redis

_redis: redis.Redis = None  # initialised in lifespan

async def _wait_for_webhook(task_id: str, timeout: int) -> CallbackRequest:
    async with _redis.pubsub() as ps:
        await ps.subscribe(f"task:{task_id}")
        try:
            async for msg in ps.listen():
                if msg["type"] == "message":
                    return CallbackRequest.model_validate_json(msg["data"])
        finally:
            await ps.unsubscribe(f"task:{task_id}")

# Webhook handler — publish instead of future.set_result
async def webhook(req: CallbackRequest):
    await _redis.publish(f"task:{req.data.taskId}", req.model_dump_json())
    return JSONResponse(content=success_response(...).model_dump(by_alias=True))
```

**Required changes:**

1. Add dependency:
   ```bash
   uv add redis
   ```

2. Add Redis connection in `main.py` lifespan:
   ```python
   @asynccontextmanager
   async def lifespan(app: FastAPI):
       global _redis
       _redis = redis.Redis.from_url("redis://localhost:6379")
       loop.set_default_executor(ThreadPoolExecutor(max_workers=100))
       yield
       await _redis.aclose()
   ```

3. Replace all `pending_tasks[task_id] = future` / `await asyncio.wait_for(future, ...)` blocks in `routes.py` with calls to `_wait_for_webhook(task_id, timeout)`

4. Replace `future.set_result(req)` in the webhook handler with `await _redis.publish(...)`

5. Remove `pending_tasks` dict from module level

6. Run Redis alongside the server (Docker Compose example):
   ```yaml
   services:
     api:
       build: .
       ports: ["8010:8010"]
       environment:
         - REDIS_URL=redis://redis:6379
       depends_on: [redis]
     redis:
       image: redis:7-alpine
       ports: ["6379:6379"]
   ```

7. Start with multiple workers:
   ```bash
   uvicorn src.ghibli_portrait.main:app --workers 4 --host 0.0.0.0 --port 8010
   ```

**After Redis migration: capacity scales linearly with workers.** 4 workers × 40 concurrent each = 160 simultaneous requests without queueing, with no shared state issues.

---

## Deployment

### Local Development

```bash
# Terminal 1: ngrok tunnel
ngrok http 8010

# Terminal 2: server
uv run uvicorn src.ghibli_portrait.main:app \
  --host 0.0.0.0 --port 8010 \
  --workers 1 \
  --log-level info
```

- **Swagger UI**: http://localhost:8010/docs
- **ReDoc**: http://localhost:8010/redoc

> Do not use `--reload` during pipeline testing — server restart wipes `pending_tasks` mid-request.

### Production (single instance)

```bash
uv run uvicorn src.ghibli_portrait.main:app \
  --host 0.0.0.0 --port 8010 \
  --workers 1 \
  --log-level warning
```

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install uv && uv sync
RUN mkdir -p src/ghibli_portrait/static/tmp
CMD ["uv", "run", "uvicorn", "src.ghibli_portrait.main:app", "--host", "0.0.0.0", "--port", "8010", "--log-level", "warning"]
```

---

## Troubleshooting

### Webhook Timeout (`STAGE1_TIMEOUT` / `STAGE2_TIMEOUT`)

1. Verify ngrok is running: `curl https://your-ngrok-url/v1/health`
2. `DOMAIN` in `.env` must match ngrok URL exactly (no trailing slash)
3. Restart server after changing `DOMAIN`
4. Check ngrok dashboard at `http://127.0.0.1:4040`

### `NO_FACE_DETECTED`

- Image must contain a detectable human face
- Face may be too small, obscured, at extreme angle, or image is not a photo (cartoon, illustration)

### `FACE_DETECTOR_FAILURE`

- MediaPipe model download failed — check network access to `storage.googleapis.com`
- Verify `src/ghibli_portrait/models/` directory is writable

### `MULTIPLE_FACES`

- Use a single-person portrait
- Secondary face must be ≥65% area AND ≥60% confidence of primary to reject

### Stage 1 result drifts from original person

- Switch to `flux-kontext-pro` (set `KIE_GHIBLI_MODEL=flux-kontext-pro`)
- `qwen/image-edit` has known identity drift — it is a legacy fallback

### Stage 1 takes longer than expected

- KIE processing time varies with server load (30-120s is normal)
- The code itself adds ≤3s of overhead (httpx submit + PIL rehost)
- No code-side fix for KIE processing time variability

### Dependencies not found

```bash
uv sync
```
