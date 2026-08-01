# Ghibli Portrait API V1

Production API that turns a real portrait photo into a Studio Ghibli–style
illustration and composes it with a scannable QR-code lock image. Built with
FastAPI, fully async I/O, multi-layer validation, and **BytePlus ARK (Seedream)**
for image generation.

## What It Does

Two-stage pipeline:
1. **Stage 1** — portrait → Ghibli-style illustration.
2. **Stage 2** — Ghibli illustration + QR-lock → final "person holding the QR" image.

Generation runs against the **BytePlus ARK Seedream** `images/generations` endpoint,
which is **synchronous** — the result image URL is returned inline in the HTTP
response (no webhook/callback, no ngrok required).

---

## High-Level Architecture

```
┌────────────────────────────────────────────────────────────┐
│  LAYER 0  Schema validation (Pydantic, camelCase surface)    │
├────────────────────────────────────────────────────────────┤
│  LAYER 1  Source resolution (URL format, localhost/private   │
│           IP rejection — no download)                        │
├────────────────────────────────────────────────────────────┤
│  LAYER 2  Decode (httpx async download, PIL decode)          │
├────────────────────────────────────────────────────────────┤
│  LAYER 3A Stage 1 validation (MediaPipe face detection +     │
│           synthetic-image check + skin-tone extraction)      │
├────────────────────────────────────────────────────────────┤
│  STAGE 1  Portrait → Ghibli   (BytePlus ARK / Seedream)      │
├────────────────────────────────────────────────────────────┤
│  IDENTITY DRIFT CHECK (optional, ENABLE_IDENTITY_CHECK)       │
├────────────────────────────────────────────────────────────┤
│  LAYER 3B Stage 2 validation (Stage 1 output trusted)        │
├────────────────────────────────────────────────────────────┤
│  STAGE 2  Ghibli + QR-lock composition (ARK / Seedream)      │
├────────────────────────────────────────────────────────────┤
│  LAYER 4  Orchestration, QR-scannability check, response     │
└────────────────────────────────────────────────────────────┘
```

### Generation model (synchronous)

```
Client → POST /v1/ghibli-qr
   → validate → Stage 1 ARK call (inline result URL)
   → re-host Stage 1 output locally → Stage 2 ARK call (inline result URL)
   → re-host final image → QR scannability check → response
```

Internally, the ARK call result is delivered to the orchestrator through an
in-process Future (`pending_tasks`) — the same contract the old async backend
used, so the pipeline logic is unchanged. **No public callback URL is needed.**

---

## Concurrency Model

All network I/O uses **httpx async**. CPU-bound work (MediaPipe, PIL) runs in a
thread pool. Two semaphores cap load:

| Limit | Env var | Default | Purpose |
|---|---|---|---|
| Face detection | `MAX_MEDIAPIPE_CONCURRENCY` | 15 | CPU ceiling for MediaPipe |
| Generation submissions | `GENERATION_CONCURRENCY_LIMIT` | 8 | Concurrent ARK calls (ARK allows ≤10/model/account) |

**Single-process limitation**: `pending_tasks` is an in-memory dict — run with
`--workers 1` (horizontal scaling requires moving it to Redis).

---

## Generation backend

- Provider: **BytePlus ARK** — `images/generations` (synchronous REST).
- Model + settings are configured in `seedream_service.py` via env vars:
  `ARK_API_KEY`, `ARK_MODEL` (default `seedream-4-5-251128`), `ARK_IMAGE_SIZE`
  (default `2K`), `ARK_SEED` (default `42`), `ARK_WATERMARK` (default `false`).
- Both stages send images **inlined as base64 data URIs**, so ARK never fetches
  from this server (no public hosting needed for generation).

### Prompts

Stage 1 (`PROMPT_PIC_TO_GHIBLI`) and Stage 2 (`PROMPT_GHIBLI_LOCK`) live in
`config.py`. The exact skin-tone hex is measured from the input image (YCbCr) and
injected into the Stage 1 prompt so the model reproduces the real skin color.

---

## Stage 1: Human Portrait Validation

Uses **MediaPipe BlazeFace** (CPU-only). Model is pre-downloaded at startup to
`src/ghibli_portrait/models/`.

| Condition | Error Code |
|---|---|
| No face detected | `NO_FACE_DETECTED` |
| More than one prominent face (secondary ≥2% area, conf ≥0.45) | `MULTIPLE_FACES` |
| Synthetic / 3D render / cartoon (color-diversity + pixel-uniformity) | `NOT_REAL_PHOTO` |
| Detector runtime error | `FACE_DETECTOR_FAILURE` |

Any image with a single detectable real human face is accepted regardless of
gender, ethnicity, facial hair, head covering, glasses, or background.

---

## Stage 2: Re-hosting + QR Composition

Before/after each stage, generated images are **re-hosted locally** (downloaded,
saved under `static/tmp/`, served from this server). The QR-lock image is built
locally with PIL (`qr_service.get_qr`). Optionally, final images are also saved
to a local `OUTPUT_DIR` (see `SAVE_OUTPUT_LOCAL`).

---

## API Endpoints

All endpoints are prefixed with `/v1`.

| Endpoint | Method | Description |
|---|---|---|
| `/v1/health` | GET | Liveness/readiness probe |
| `/v1/ghibli` | POST | Transform portrait to Ghibli style |
| `/v1/qr-lock` | POST | Generate a QR-code lock image |
| `/v1/qr-lock/{imgId}` | DELETE | Delete a temporary QR image |
| `/v1/qr-url/` | GET | Deterministic URL shortening |
| `/v1/ghibli-qr` | POST | **Primary** — full Ghibli + QR pipeline |

### Primary Endpoint: `POST /v1/ghibli-qr`

**Request:**
```json
{ "imgUrl": "https://example.com/portrait.jpg", "url": "https://your-profile.com" }
```

**Response:**
```json
{
  "success": true,
  "data": {
    "resultUrls": ["http://<host>/tmp/final_....jpg"],
    "model": "seedream",
    "costTime": 62,
    "quality": "basic",
    "aspectRatio": "1:1",
    "qrValidation": { "ok": true, "expectedPayload": "...", "detectedPayload": "...", "reason": "..." }
  },
  "message": "Ghibli + QR pipeline completed successfully",
  "errors": null,
  "timestamp": "..."
}
```

---

## Error Codes Reference

| Code | HTTP | Stage | Description |
|---|---|---|---|
| `SINGLE_IMAGE_REQUIRED` | 422 | INPUT | Exactly one image URL required |
| `INVALID_IMAGE_URL` | 422 | SOURCE_RESOLUTION | URL not public or malformed |
| `IMAGE_DOWNLOAD_FAILED` | 422 | SOURCE_RESOLUTION | Failed to download the image |
| `NO_FACE_DETECTED` | 422 | STAGE1_GHIBLI | No human face found |
| `MULTIPLE_FACES` | 422 | STAGE1_GHIBLI | Multiple prominent faces |
| `NOT_REAL_PHOTO` | 422 | STAGE1_GHIBLI | 3D render / cartoon, not a real photo |
| `FACE_DETECTOR_FAILURE` | 500 | STAGE1_GHIBLI | MediaPipe runtime error |
| `STAGE1_API_ERROR` | 500 | STAGE1_GHIBLI | Provider rejected Stage 1 submission |
| `STAGE1_TIMEOUT` | 504 | STAGE1_GHIBLI | Stage 1 timed out |
| `IDENTITY_DRIFT_DETECTED` | 500 | STAGE1_GHIBLI | Identity not preserved after retry |
| `STAGE2_API_ERROR` | 500 | STAGE2_QR | Provider rejected Stage 2 submission |
| `STAGE2_TIMEOUT` | 504 | STAGE2_QR | Stage 2 timed out |
| `INTERNAL_ERROR` | 500 | ORCHESTRATION | Unexpected server error |

---

## Installation (local)

### Prerequisites
- Python 3.10+
- [uv](https://github.com/astral-sh/uv)

```bash
git clone <repo-url>
cd ghibli_qr
pip install uv
uv sync
cp .env.example .env
# edit .env: set DOMAIN and ARK_API_KEY
```

Run:
```bash
uv run uvicorn src.ghibli_portrait.main:app --host 0.0.0.0 --port 30820 --workers 1
```

- Swagger UI: `http://localhost:30820/docs`
- Health: `http://localhost:30820/v1/health`

> No ngrok needed — generation is synchronous. Set `DOMAIN` to this server's
> reachable address (e.g. `http://localhost:30820` locally, or `http://<ip>:<port>`).

---

## Docker Deployment

### Prerequisites
- Docker 20.10+, Docker Compose v2
- `.env` (copy from `.env.example`, set `DOMAIN` + `ARK_API_KEY`)
- `src/static/lock.png` must exist as a real PNG (Stage 2 lock overlay; read via
  `config.LOCK_PATH`). It is bind-mounted read-only by `docker-compose.yml`.

```bash
cp .env.example .env          # edit DOMAIN + ARK_API_KEY
docker-compose up -d --build
curl http://localhost:30820/v1/health
```

| Command | Purpose |
|---|---|
| `docker-compose up -d --build` | Build/rebuild and start (use after any code change) |
| `docker-compose down && docker-compose up -d` | Apply `.env` changes |
| `docker-compose logs -f ghibli-api` | Live logs |

Host port is configurable: `HOST_PORT=8090 docker-compose up -d`.

---

## Configuration Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `DOMAIN` | Yes | — | Base address used to build returned image URLs (no trailing slash) |
| `ARK_API_KEY` | Yes | — | BytePlus ARK (Seedream) API key |
| `GHIBLI_MODEL` | No | `seedream-4-5-251128` | Real model for Stage 1 (also reported in response `model`) |
| `COMPOSE_MODEL` | No | `seedream-4-5-251128` | Real model for Stage 2 (also reported in response `model`) |
| `ARK_MODEL` | No | `seedream-4-5-251128` | Fallback ARK model id when no per-stage model is given |
| `ARK_IMAGE_SIZE` | No | `2K` | Output size |
| `ARK_SEED` | No | `42` | Fixed seed (`-1` = random) |
| `ARK_WATERMARK` | No | `false` | Add ARK watermark |
| `REQUIRE_HUMAN_FACE` | No | `true` | Face-detection gate |
| `MAX_FACES` | No | `1` | Max prominent faces (0 = unlimited) |
| `MIN_FACE_AREA_RATIO` | No | `0.03` | Minimum face-to-image area ratio |
| `SHORT_CODE_LENGTH` | No | `8` | URL shortener code length |
| `ENABLE_IDENTITY_CHECK` | No | `false` | Post-generation identity drift check |
| `MAX_MEDIAPIPE_CONCURRENCY` | No | `15` | Max concurrent face-detection ops |
| `GENERATION_CONCURRENCY_LIMIT` | No | `8` | Max concurrent ARK submissions (ARK allows ≤10/model) |
| `SAVE_OUTPUT_LOCAL` | No | `false` | Also save each final image under `OUTPUT_DIR` |
| `OUTPUT_DIR` | No | `output` | Local directory for saved final images |
| `STAGE1_TTL_HOURS` / `QRLOCK_TTL_HOURS` | No | `2` | Intermediate file TTL |
| `FINAL_IMAGE_TTL_HOURS` | No | `24` | Final image TTL |
| `PERSIST_FINAL_IMAGES` | No | `false` | Never auto-delete final images |
| `HOST_PORT` | No | `30820` | Docker host port (compose only) |

---

## Tech Stack

- **FastAPI** — async web framework with OpenAPI/Swagger
- **httpx** — async HTTP client (downloads, ARK calls)
- **Pydantic v2** — schema validation (camelCase API surface)
- **Pillow** — image processing, JPEG optimization, QR placement
- **MediaPipe** — BlazeFace CPU face detection
- **qrcode** / **QReader + pyzbar** — QR generation and scannability check
- **BytePlus ARK (Seedream)** — synchronous AI image generation
- **uv** — packaging & venv · **Docker** — containerization · **Python 3.10+**

---

## Project Structure

```
src/ghibli_portrait/
├── api/
│   ├── routes.py             # V1 endpoints + pipeline orchestration (async)
│   └── responses.py          # Unified response helpers
├── models/schemas.py         # Request/response schemas (camelCase)
├── services/
│   ├── image_service.py      # generate_img — BytePlus ARK adapter (drop-in)
│   ├── seedream_service.py   # BytePlus ARK call + ARK settings
│   ├── identity_check.py     # Optional identity drift detection
│   ├── qr_service.py         # QR-on-lock generation (PIL)
│   ├── qr_validation.py      # QR scannability check (QReader/pyzbar)
│   └── validation_service.py # Face / skin / synthetic validation
├── utils/url_utils.py        # Deterministic URL shortening
├── config.py                 # Settings + prompts
└── main.py                   # FastAPI app, lifespan, thread pool, tmp cleanup
src/static/lock.png           # Lock-screen template (required asset)
```

---

## Testing

```bash
PYTHONPATH= uv run --group test pytest -q
```

Covers validation layers, QR generation + decode, skin extraction, synthetic
detection, the ARK adapter (mocked — no paid calls), and the full `/v1/ghibli-qr`
flow including local file saving.

---

## Documentation

- **Quick Setup (Docker)**: [QUICK_SETUP.md](QUICK_SETUP.md)
- **Swagger UI**: `http://localhost:30820/docs`
- **Implementation Guide**: [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md)
