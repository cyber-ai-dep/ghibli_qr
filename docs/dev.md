# Ghibli Portrait API — Developer Reference

FastAPI service that turns a portrait into Ghibli art holding a QR-code lock.
Image generation uses **BytePlus ARK (Seedream)** — synchronous (no webhook/ngrok).

## Quick Start

```bash
uv sync
cp .env.example .env
# Edit .env — set DOMAIN and ARK_API_KEY
uv run uvicorn src.ghibli_portrait.main:app --host 0.0.0.0 --port 30820 --workers 1
```
Swagger: `http://localhost:30820/docs` · Health: `/v1/health`

## Workers

`--workers 1` is mandatory: the in-memory `pending_tasks` dict delivers each
generation result to the awaiting request in-process. Multiple workers wouldn't
share it. Scaling beyond one worker requires moving `pending_tasks` to a shared
store (e.g. Redis). A single worker is not a throughput bottleneck — all network
I/O is async (httpx); only MediaPipe/PIL run in a thread pool, capped by a semaphore.

## Environment Variables

```bash
# Required
DOMAIN=http://<host>:30820          # base address for returned image URLs (no webhook)
ARK_API_KEY=<byteplus-ark-key>

# Generation (BytePlus ARK / Seedream)
GHIBLI_MODEL=seedream-4-5-251128      # real model for Stage 1 (also reported in response)
COMPOSE_MODEL=seedream-4-5-251128     # real model for Stage 2 (also reported in response)
# ARK_IMAGE_SIZE=2K                   # optional ARK overrides (defaults in seedream_service.py)
# ARK_SEED=42                         # -1 = random
# ARK_WATERMARK=false

# Validation
REQUIRE_HUMAN_FACE=true
MAX_FACES=1
MIN_FACE_AREA_RATIO=0.03
SHORT_CODE_LENGTH=8
ENABLE_IDENTITY_CHECK=false

# Concurrency
MAX_MEDIAPIPE_CONCURRENCY=15          # CPU ceiling for face detection
GENERATION_CONCURRENCY_LIMIT=8        # concurrent ARK calls (ARK allows ≤10/model)

# Local saving + retention
SAVE_OUTPUT_LOCAL=false               # also save final images under OUTPUT_DIR
OUTPUT_DIR=output
STAGE1_TTL_HOURS=2
QRLOCK_TTL_HOURS=2
FINAL_IMAGE_TTL_HOURS=24
PERSIST_FINAL_IMAGES=false

# Docker
HOST_PORT=30820
```

> `GHIBLI_MODEL` / `COMPOSE_MODEL` are the real ARK model used per stage (set in
> `.env`) and are reported in the response `model` field. `ARK_MODEL` is only the
> fallback default when no per-stage model is given.

## Response Envelope

Every endpoint returns: `{ success, data, message, errors, timestamp }`
(camelCase `data`). On error: `success:false`, `data:null`,
`errors:[{code,type,stage,field,message}]`.

## API Endpoints (prefix `/v1`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/health` | Liveness/readiness |
| POST | `/v1/ghibli-qr` | **Primary** — full Ghibli + QR pipeline (`{imgUrl, url}`) |
| POST | `/v1/ghibli` | Stage 1 only (`{imgUrls:[...]}`) |
| POST | `/v1/qr-lock` | QR-on-lock image (`{url, version?, shortenUrl?}`) |
| DELETE | `/v1/qr-lock/{imgId}` | Delete a temp QR image |
| GET | `/v1/qr-url/?url=` | Deterministic URL shortener |

See [usage.md](usage.md) for request/response examples.

## Pipeline flow (`/v1/ghibli-qr`)

```
validate (URL → download → MediaPipe face + synthetic check)
  → extract skin-tone hex → inject into Stage 1 prompt
  → Stage 1 ARK call (portrait → Ghibli)  → re-host result locally
  → Stage 2 ARK call (Ghibli + QR-lock)   → re-host final locally
  → QR scannability check → response
```
The ARK call is synchronous; its result is delivered to the orchestrator through the
in-process `pending_tasks` Future. `routes.py` is unchanged —
`image_service.generate_img` is the ARK adapter behind that contract.

## Error Codes

`INVALID_IMAGE_URL`, `IMAGE_DOWNLOAD_FAILED`, `NO_FACE_DETECTED`, `MULTIPLE_FACES`,
`NOT_REAL_PHOTO`, `FACE_DETECTOR_FAILURE`, `STAGE1_API_ERROR`, `STAGE1_TIMEOUT`,
`IDENTITY_DRIFT_DETECTED`, `STAGE2_API_ERROR`, `STAGE2_TIMEOUT`, `INTERNAL_ERROR`.

## Testing

```bash
PYTHONPATH= uv run --group test pytest -q                  # unit tests (tests/)
uv run python tests/manual/test_concurrent.py --count 3   # manual load test
```
Manual/integration scripts live in `tests/manual/` (ignored by pytest).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `git pull` changes don't take effect (Docker) | `docker-compose up -d --build` |
| `.env` changes ignored | `docker-compose down && docker-compose up -d` |
| `STAGE*_API_ERROR` / rate limit | lower `GENERATION_CONCURRENCY_LIMIT` (ARK ≤10/model) |
| Stage 2 `IsADirectoryError ... lock.png` | ensure `src/static/lock.png` is a real PNG |
