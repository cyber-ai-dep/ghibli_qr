# Ghibli Portrait API V1

Production-ready API for transforming portraits into Ghibli-style art with QR code generation. Built with FastAPI, fully async I/O, multi-layer validation, and identity-preserving style transfer.

## What It Does

Transforms real portrait photos into hand-drawn Studio Ghibli-style illustrations and generates QR codes embedded in a lock-screen overlay. Features an automated two-stage async pipeline with webhook-based result delivery.

---

## High-Level Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                         LAYER 0: Schema Validation                       │
│                    (Pydantic: field types, required fields)              │
└────────────────────────────────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────┐
│                      LAYER 1: Source Resolution                          │
│            (URL format, localhost rejection — no download)               │
└────────────────────────────────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────┐
│                        LAYER 2: Image Decoding                           │
│               (httpx async download, PIL decode, format check)          │
└────────────────────────────────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────┐
│              LAYER 3A: Stage 1 Validation (Human Portrait)               │
│        (MediaPipe BlazeFace — CPU-bound, runs in thread pool)            │
└────────────────────────────────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────┐
│                    STAGE 1: Ghibli Transformation                        │
│        (configurable model — flux-kontext-pro recommended)               │
│      Strict identity-preserving prompt + fidelity controls               │
│              httpx async POST to KIE.ai — no thread used                │
└────────────────────────────────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────┐
│              IDENTITY DRIFT CHECK (optional, post-Stage 1)               │
│     Face presence · Area stability · Skin-tone hue — auto-retry once    │
│         async download + NumPy/MediaPipe compute in thread pool          │
└────────────────────────────────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────┐
│              LAYER 3B: Stage 2 Validation (Input Trust)                  │
│            (Stage 1 output is TRUSTED — URL sanity check only)           │
└────────────────────────────────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────┐
│                      STAGE 2: QR Composition                             │
│              (seedream/4.5-edit — httpx async POST to KIE.ai)           │
└────────────────────────────────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────┐
│                    LAYER 4: Orchestration & Response                     │
│          (Coordinates stages, formats responses, handles errors)         │
└────────────────────────────────────────────────────────────────┘
```

### Webhook Processing Model

```
Client Request → FastAPI → KIE.ai API (async httpx POST — task created)
                    │
          asyncio.Future waits (zero resource use while waiting)
                    │
KIE.ai Processing → Webhook Callback → Future.set_result()
                    │
            Result returned to client
```

---

## Concurrency Model

All network I/O uses **httpx async** — the event loop is never blocked by HTTP calls. The thread pool (100 workers) is reserved exclusively for CPU-bound work:

| Operation | Where it runs | Duration |
|---|---|---|
| Image download (validation, rehost) | async event loop (httpx) | ~1-10s |
| KIE.ai API submission | async event loop (httpx) | ~1-2s |
| MediaPipe face detection | thread pool + semaphore | ~2s |
| PIL resize + JPEG save | thread pool | ~0.5s |
| Waiting for KIE webhook | async Future (zero cost) | 30-120s |

### MediaPipe Concurrency Semaphore

`asyncio.Semaphore(MAX_MEDIAPIPE_CONCURRENCY)` wraps the face-detection step in both pipeline endpoints. This caps the number of simultaneous CPU-heavy MediaPipe operations regardless of how many requests arrive.

```
500 requests arrive simultaneously (MAX_MEDIAPIPE_CONCURRENCY=15):

├── 15 enter MediaPipe immediately  → CPU ~15%
├── 485 wait in async queue         → CPU 0%, zero threads used
├── Every ~2.5s: 15 slots free → next 15 proceed
└── All 500 then await KIE async    → CPU 0%
```

The semaphore is applied **only around MediaPipe (~2s)**, not the image download (~10s). Downloads run concurrently with no limit — the slot is held for ~2s instead of ~12s, reducing wait time significantly.

**Tuning `MAX_MEDIAPIPE_CONCURRENCY`:**

| Value | Wait time (500 requests) | CPU peak |
|---|---|---|
| `15` *(default)* | ~67s | ~15% |
| `30` | ~33s | ~30% |
| `50` | ~20s | ~50% |
| `REQUIRE_HUMAN_FACE=false` | ~0s | ~0% |

**Single-process limitation**: `pending_tasks` is an in-memory dict — all requests must hit the same process. Horizontal scaling (multiple workers/instances) requires replacing it with Redis pub/sub.

---

## Stage 1: Model Selection

| Model | Identity Preservation | Speed | Notes |
|---|---|---|---|
| `flux-kontext-pro` | **Excellent** | Medium | Recommended — subject-consistent style transfer |
| `flux-kontext-max` | **Excellent** | Slow | Highest quality |
| `qwen/image-edit` | Poor | Fast | Legacy fallback — known identity drift |

Set `KIE_GHIBLI_MODEL` in `.env`. The code handles both payload structures automatically.

### Prompt System (Stage 1)

**Positive prompt** — static base with dynamic skin hex injected at runtime:
> *"Convert this photo into a Studio Ghibli hand-painted illustration. Apply the full Ghibli visual style: soft watercolor backgrounds, warm painterly color palette, clean expressive linework, cel-shaded lighting, lush atmospheric depth, and the characteristic hand-drawn Ghibli texture throughout every surface.*
>
> *IDENTITY LOCK: same person, same face structure, same skin tone, same ethnicity, same race, same hairstyle, same expression, same clothing, same pose.*
>
> *SKIN COLOR IS ABSOLUTE: reproduce the exact skin tone from the photo. Dark skin stays dark. Light skin stays light. Zero tolerance for lightening or whitening.*
>
> *EXACT SKIN COLOR: `#RRGGBB`. [injected at runtime — measured from the input image via YCbCr skin detection] You MUST reproduce this exact color. Do NOT lighten, darken, whiten, or shift this color in any direction."*

The exact skin hex is extracted from the input image at request time using YCbCr color space analysis (covers all skin tones from very dark to light). It is appended to the prompt for both Stage 1 (Ghibli generation) and Stage 2 (QR composition) so neither stage can drift the skin color.

**Negative prompt** (applied when supported by the model):
> *"photorealistic, photograph, generic anime face, identity drift, race change, skin tone change, beautification, face replacement, facial simplification, different person, altered ethnicity, altered hairstyle, altered expression."*

### Fidelity Parameters (qwen)

| Parameter | Value | Effect |
|---|---|---|
| `guidance_scale` | `4.0` | KIE default — balanced quality vs identity |
| `num_inference_steps` | `28` | Quality/speed balance |
| `acceleration` | `none` | No speed reduction |
| `seed` | `42` | Fixed for reproducible results |
| `image_size` | `square` | Intermediate output — Stage 2 handles final resolution |
| `output_format` | `jpeg` | Saves credits vs PNG |
| `image_strength` | `0.35` | Low = minimal deviation from source |
| `denoise` | `0.30` | Preserves original structure |
| `fidelity` | `0.95` | High fidelity to reference |
| `reference_strength` | `0.95` | Max guidance strength |

---

## Stage 1: Human Portrait Validation

Uses **MediaPipe BlazeFace** (CPU-only). Model pre-downloaded at server startup to `src/ghibli_portrait/models/`.

### Acceptance Rules

Any image containing a detectable human face is accepted — regardless of gender, ethnicity, facial hair, head covering, glasses, background, or face size.

### Rejection Rules

| Condition | Error Code |
|---|---|
| No face detected | `NO_FACE_DETECTED` |
| More than one face detected (secondary face ≥2% of image area, confidence ≥0.45) | `MULTIPLE_FACES` |
| Synthetic/3D render or game character (color-diversity + pixel-uniformity analysis) | `NOT_REAL_PHOTO` |
| Detector runtime error | `FACE_DETECTOR_FAILURE` |

---

## Stage 2: Re-hosting + QR Composition

Before Stage 2 submission, Stage 1 output is **re-hosted locally**:
- Qwen CDN URLs (`tempfile.aiquickdraw.com`) are temporary and often unreachable cross-service
- Stage 1 output: downloaded async via httpx, resized to max 1024px, saved as JPEG
- QR lock image: generated locally (PIL), resized to max 1024px, saved as JPEG

Both inputs are served from the local server so Seedream can reliably download them.

---

## API Endpoints

All endpoints prefixed with `/v1`.

| Endpoint | Method | Description |
|---|---|---|
| `/v1/health` | GET | Liveness/readiness probe |
| `/v1/ghibli` | POST | Transform portrait to Ghibli style |
| `/v1/ghibli/callback` | POST | Webhook callback (KIE internal use only) |
| `/v1/qr-lock` | POST | Generate QR code with lock screen overlay |
| `/v1/qr-lock/{imgId}` | DELETE | Delete temporary QR image |
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
    "resultUrls": ["https://..."],
    "model": "seedream/4.5-edit",
    "costTime": 85,
    "quality": "basic",
    "aspectRatio": "1:1"
  },
  "message": "Ghibli + QR pipeline completed successfully",
  "errors": null,
  "timestamp": "2026-05-11T08:00:00.000Z"
}
```

### Unified Response Envelope

**Success:**
```json
{ "success": true, "data": { ... }, "message": "...", "errors": null, "timestamp": "..." }
```

**Error:**
```json
{
  "success": false, "data": null, "message": "...",
  "errors": [{ "code": "NO_FACE_DETECTED", "type": "VALIDATION_ERROR", "stage": "STAGE1_GHIBLI", "field": "imgUrl", "message": "..." }],
  "timestamp": "..."
}
```

---

## Error Codes Reference

| Code | HTTP | Stage | Description |
|---|---|---|---|
| `SINGLE_IMAGE_REQUIRED` | 422 | INPUT | Request must contain exactly one image URL |
| `INVALID_IMAGE_URL` | 422 | SOURCE_RESOLUTION | URL is not publicly accessible or malformed |
| `IMAGE_DOWNLOAD_FAILED` | 422 | SOURCE_RESOLUTION | Failed to download the image |
| `IMAGE_DECODE_FAILED` | 422 | SOURCE_RESOLUTION | Image bytes are corrupt, unsupported format, or invalid payload |
| `NO_FACE_DETECTED` | 422 | STAGE1_GHIBLI | No human face found in the image |
| `MULTIPLE_FACES` | 422 | STAGE1_GHIBLI | Multiple prominent human faces detected |
| `NOT_REAL_PHOTO` | 422 | STAGE1_GHIBLI | Image is a 3D render, game character, or cartoon — not a real photo |
| `FACE_DETECTOR_FAILURE` | 500 | STAGE1_GHIBLI | MediaPipe runtime error |
| `STAGE1_API_ERROR` | 500 | STAGE1_GHIBLI | KIE API rejected Stage 1 submission |
| `STAGE1_TASK_FAILED` | 500 | STAGE1_GHIBLI | Stage 1 generation task failed |
| `STAGE1_TIMEOUT` | 504 | STAGE1_GHIBLI | Stage 1 exceeded 10-minute timeout |
| `IDENTITY_DRIFT_DETECTED` | 500 | STAGE1_GHIBLI | Identity not preserved after retry |
| `STAGE2_API_ERROR` | 500 | STAGE2_QR | KIE API rejected Stage 2 submission |
| `STAGE2_TASK_FAILED` | 500 | STAGE2_QR | Stage 2 composition task failed |
| `STAGE2_TIMEOUT` | 504 | STAGE2_QR | Stage 2 exceeded 10-minute timeout |
| `INTERNAL_ERROR` | 500 | ORCHESTRATION | Unexpected server error |

---

## Installation

### Prerequisites
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) package manager

### Setup

```bash
git clone https://github.com/cyber-ai-dep/ghibli_qr
cd ghibli_qr
pip install uv
uv sync
cp .env.example .env
```

> `static/tmp/` is created automatically on first server startup — no manual `mkdir` needed.

Edit `.env`:
```env
DOMAIN=https://your-domain.com
KIE_API_KEY=your_kie_api_key

# Stage 1 — flux-kontext-pro recommended for identity preservation
KIE_GHIBLI_MODEL=flux-kontext-pro
# Stage 2 — QR composition
KIE_COMPOSE_MODEL=seedream/4.5-edit

# Validation
REQUIRE_HUMAN_FACE=true
MAX_FACES=1
MIN_FACE_AREA_RATIO=0.03
SHORT_CODE_LENGTH=8

# Identity drift check (enable when using flux-kontext)
ENABLE_IDENTITY_CHECK=false

# Concurrency — max simultaneous MediaPipe face-detection operations
# Lower = less CPU pressure on shared servers, more queue wait (~2.5s/slot)
MAX_MEDIAPIPE_CONCURRENCY=15

# Stage 1 quality controls (qwen only)
STAGE1_GUIDANCE_SCALE=4.0
STAGE1_NUM_INFERENCE_STEPS=28
STAGE1_ACCELERATION=none
KIE_SEED=42
KIE_OUTPUT_FORMAT=jpeg
KIE_IMAGE_SIZE=square
```

Run:
```bash
# Production — minimal logs
uv run uvicorn src.ghibli_portrait.main:app \
  --host 0.0.0.0 --port 8010 \
  --workers 1 \
  --log-level warning

# Development / request tracking — shows every incoming request
uv run uvicorn src.ghibli_portrait.main:app \
  --host 0.0.0.0 --port 8010 \
  --workers 1 \
  --log-level info
```

> `--workers 1` is required — see [Deployment](#deployment).

- **Swagger UI**: http://localhost:8010/docs
- **ReDoc**: http://localhost:8010/redoc

### Webhook Setup (local development)

```bash
# Must specify port 8010 — ngrok defaults to port 80 which will break webhooks
ngrok http 8010
```

Verify the forwarding line shows the correct port:
```
Forwarding  https://xxxx.ngrok-free.app -> http://localhost:8010  ✅
Forwarding  https://xxxx.ngrok-free.app -> http://localhost:80    ❌ wrong port
```

Then:
1. Copy the HTTPS URL → set as `DOMAIN` in `.env`
2. Restart the server (required to reload `DOMAIN` and rebuild the callback URL)

---

## Docker Deployment

### Prerequisites

- Docker 20.10+
- Docker Compose v2 (`docker-compose --version`)
- `.env` file (copy from `.env.example` and fill `DOMAIN` + `KIE_API_KEY`)
- `src/static/lock.png` must exist as a **real PNG file** (the Stage 2 lock overlay, read via `config.LOCK_PATH`). `docker-compose.yml` bind-mounts it read-only; if it is missing, Docker creates an empty directory in its place and Stage 2 fails with `IsADirectoryError`.

### Setup

```bash
git clone https://github.com/cyber-ai-dep/ghibli_qr
cd ghibli_qr
cp .env.example .env
# Edit DOMAIN and KIE_API_KEY in .env
docker-compose up -d --build
curl http://localhost:30820/v1/health
```

First build takes ~3-5 minutes (downloads base image, installs dependencies).

### Common Commands

| Command | Purpose |
|---|---|
| `docker-compose up -d` | Start (after first build) |
| `docker-compose up -d --build` | Rebuild after code changes |
| `docker-compose down && docker-compose up -d` | Apply `.env` changes |
| `docker-compose restart` | Restart process (does NOT reload `.env`) |
| `docker-compose logs -f ghibli-api` | Live logs |
| `docker-compose ps` | Container status |
| `docker-compose exec ghibli-api bash` | Shell inside container |

### Configurable Host Port

Default published port is `30820`. Override without editing files:

```bash
HOST_PORT=8090 docker-compose up -d
```

### Persistent Volumes

Named volumes survive rebuilds and stay on disk after `docker-compose down`:

| Volume | Contents |
|---|---|
| `ghibli_qr_ghibli_tmp` | Generated images (cleaned by TTL) |
| `ghibli_qr_ghibli_models` | MediaPipe model (avoids re-download) |

`down -v` removes them — use only for a clean reset.

### Native Libraries (auto-installed in the image)

The Dockerfile installs all system libraries needed by OpenCV, MediaPipe, and pyzbar:
`libsm6`, `libxext6`, `libxrender-dev`, `libgomp1`, `libgl1`, `libglib2.0-0`, `libgles2`, `libegl1`, `libzbar0`.

No host-side installation needed.

### Troubleshooting

| Issue | Fix |
|---|---|
| Port already in use | `HOST_PORT=8090 docker-compose up -d` (default is 30820) |
| `.env` changes not applied | `docker-compose down && docker-compose up -d` (not `restart`) |
| Container `unhealthy` | `docker-compose logs ghibli-api` |
| Pipeline times out at Stage 1 | `DOMAIN` is not reachable by KIE — verify externally with `curl https://<DOMAIN>/v1/health` |

---

## Server Migration

When deploying to a new server or changing the public URL:

1. **Update `.env`** on the new server with the new `DOMAIN` (no trailing slash).
2. **(Optional)** Set `HOST_PORT` if `30820` is taken.
3. **Open firewall** for the chosen `HOST_PORT`.
4. **Build and run:** `docker-compose up -d --build`
5. **Verify externally:** `curl https://<DOMAIN>/v1/health` from a different machine.

Skipping `DOMAIN` update is the most common failure — KIE webhooks hit the old host and all Stage 1 tasks time out after 10 minutes.

### Migration Checklist

- [ ] `.env` exists in project root with updated `DOMAIN` and `KIE_API_KEY`
- [ ] Firewall allows inbound on `HOST_PORT` (default 30820)
- [ ] `DOMAIN` is publicly reachable (test with external `curl`)
- [ ] `docker-compose ps` shows `healthy`
- [ ] End-to-end test request returns 200 within 3 minutes

---

## Configuration Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `DOMAIN` | Yes | — | Public URL for webhooks and static file serving. No trailing slash. Leading/trailing spaces are stripped automatically. |
| `KIE_API_KEY` | Yes | — | KIE.ai API authentication key |
| `KIE_GHIBLI_MODEL` | Yes | — | Stage 1 model (`flux-kontext-pro` recommended) |
| `KIE_COMPOSE_MODEL` | Yes | — | Stage 2 model (`seedream/4.5-edit`) |
| `REQUIRE_HUMAN_FACE` | No | `true` | Enable face detection gate |
| `MAX_FACES` | No | `1` | Max prominent faces (0 = unlimited) |
| `MIN_FACE_AREA_RATIO` | No | `0.03` | Minimum face-to-image area ratio |
| `SHORT_CODE_LENGTH` | No | `8` | URL shortener code length |
| `ENABLE_IDENTITY_CHECK` | No | `false` | Post-generation identity drift detection |
| `MAX_MEDIAPIPE_CONCURRENCY` | No | `15` | Max simultaneous MediaPipe operations — tune to cap CPU on shared servers |
| `KIE_CONCURRENCY_LIMIT` | No | `4` | Max concurrent KIE task submissions across all stages — guards against burst rate-limit errors |
| `STAGE1_GUIDANCE_SCALE` | No | `4.0` | Qwen guidance scale |
| `STAGE1_NUM_INFERENCE_STEPS` | No | `28` | Qwen inference steps |
| `STAGE1_ACCELERATION` | No | `none` | Qwen acceleration (`none`/`regular`/`high`) |
| `KIE_SEED` | No | `42` | Fixed seed for reproducible Stage 1 results |
| `KIE_OUTPUT_FORMAT` | No | `jpeg` | Stage 1 output format (`jpeg`/`png`) |
| `KIE_IMAGE_SIZE` | No | `square` | Stage 1 output size (`square`/`square_hd`) |

---

## Tech Stack

- **FastAPI** — async web framework with OpenAPI/Swagger
- **httpx** — async HTTP client for all network I/O (KIE API, image downloads)
- **Pydantic v2** — schema validation with camelCase API surface
- **Pillow** — image processing, JPEG optimization, QR placement
- **MediaPipe** — BlazeFace CPU-only face detection (thread pool)
- **qrcode** — QR code generation
- **KIE.ai API** — AI image generation (Flux Kontext / Qwen / Seedream)
- **uv** — package management and virtual env
- **Docker & Docker Compose** — containerization and orchestration
- **Python 3.10+**

---

## Project Structure

```
src/ghibli_portrait/
├── api/
│   ├── routes.py             # V1 endpoints, pipeline orchestration (async)
│   └── responses.py          # Unified response helpers
├── models/
│   ├── schemas.py            # Request/response schemas (camelCase)
│   └── blaze_face_short_range.tflite  # MediaPipe model (auto-downloaded)
├── services/
│   ├── image_service.py      # KIE API calls — async generate_img (httpx)
│   ├── identity_check.py     # Post-generation identity drift detection (async)
│   ├── qr_service.py         # QR code generation (proportional sizing)
│   └── validation_service.py # Multi-layer validation (async + thread)
├── utils/
│   └── url_utils.py          # Deterministic URL shortening
├── static/
│   ├── lock.png              # Lock screen template
│   └── tmp/                  # Temporary generated files
├── config.py                 # Settings and prompt configuration
└── main.py                   # FastAPI app, lifespan, thread pool config
```

---

## Deployment

### Single Process (Required)

```bash
# Single worker (required — pending_tasks is in-memory)
uv run uvicorn src.ghibli_portrait.main:app \
  --host 0.0.0.0 --port 8010 \
  --workers 1 \
  --log-level warning
```

**Important**: Do not use `--workers N > 1` or multiple instances without first migrating `pending_tasks` to Redis. Webhooks arriving at a different worker will never resolve the waiting Future.

### Production Platforms

Deploy to any platform supporting Python (Railway, Render, Fly.io, AWS, GCP, Azure). Ensure:
- `DOMAIN` is publicly reachable for KIE webhook callbacks
- Environment variables are set (especially `KIE_API_KEY`, `DOMAIN`)
- Port 8010 is exposed

---

## Documentation

- **Quick Setup (Docker)**: [QUICK_SETUP.md](QUICK_SETUP.md)
- **Swagger UI**: `http://localhost:8010/docs`
- **ReDoc**: `http://localhost:8010/redoc`
- **Implementation Guide**: [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md)

---

## License

See LICENSE file for details.
