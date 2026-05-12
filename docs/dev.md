# Ghibli Portrait API — Developer Reference

FastAPI service for Ghibli-style portrait transformation and QR code generation.

---

## Quick Start

```bash
uv sync
cp .env.example .env
# Edit .env — set KIE_API_KEY, DOMAIN, KIE_GHIBLI_MODEL, KIE_COMPOSE_MODEL

uv run uvicorn src.ghibli_portrait.main:app --host 0.0.0.0 --port 8010 --log-level info
```

Swagger UI: `http://localhost:8010/docs`

---

## Environment Variables

```env
# Required
DOMAIN=https://your-domain.com        # Public URL — used for webhook callback and static file URLs
KIE_API_KEY=your_key

# Models
KIE_GHIBLI_MODEL=flux-kontext-pro     # Stage 1 (portrait → Ghibli). flux-kontext-pro recommended.
KIE_COMPOSE_MODEL=seedream/4.5-edit   # Stage 2 (Ghibli + QR composition)

# Validation
REQUIRE_HUMAN_FACE=true
MAX_FACES=1
MIN_FACE_AREA_RATIO=0.03
ENABLE_IDENTITY_CHECK=false

# Stage 1 qwen-specific controls (ignored by flux-kontext)
STAGE1_GUIDANCE_SCALE=4.0
STAGE1_NUM_INFERENCE_STEPS=28
STAGE1_ACCELERATION=none
KIE_SEED=42
KIE_OUTPUT_FORMAT=jpeg
KIE_IMAGE_SIZE=square
```

---

## Response Envelope

All endpoints return the same unified JSON structure.

**Success:**
```json
{
  "success": true,
  "data": { ... },
  "message": "Human-readable message",
  "errors": null,
  "timestamp": "2026-05-12T10:00:00.000Z"
}
```

**Error:**
```json
{
  "success": false,
  "data": null,
  "message": "High-level summary",
  "errors": [
    {
      "code": "NO_FACE_DETECTED",
      "type": "VALIDATION_ERROR",
      "stage": "STAGE1_GHIBLI",
      "field": "imgUrl",
      "message": "No human face detected in the image"
    }
  ],
  "timestamp": "2026-05-12T10:00:00.000Z"
}
```

---

## API Endpoints

All endpoints are prefixed with `/v1`.

### `GET /v1/health`

Liveness probe.

```json
{ "success": true, "data": { "status": "healthy" }, "message": "Ghibli Portrait API V1 is running", ... }
```

---

### `POST /v1/ghibli-qr` — Primary endpoint

Full two-stage pipeline: portrait → Ghibli → compose with QR lock screen.

**Request:**
```json
{
  "imgUrl": "https://example.com/portrait.jpg",
  "url": "https://your-profile.com"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "resultUrls": ["https://your-domain.com/tmp/...jpg"],
    "model": "seedream/4.5-edit",
    "costTime": 95,
    "quality": "basic",
    "aspectRatio": "1:1"
  },
  "message": "Ghibli + QR pipeline completed successfully",
  "errors": null,
  "timestamp": "..."
}
```

---

### `POST /v1/ghibli`

Transform a single portrait to Ghibli style (Stage 1 only, no QR).

**Request:**
```json
{
  "imgUrls": ["https://example.com/portrait.jpg"],
  "prompt": "...",
  "quality": "basic",
  "aspectRatio": "1:1"
}
```

**Response:** Same envelope, `data.resultUrls` contains the Ghibli image URL.

---

### `POST /v1/qr-lock`

Generate a QR code embedded in the lock screen overlay image.

**Request:**
```json
{
  "url": "https://example.com",
  "version": null,
  "shortenUrl": false
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "qrUrl": "https://your-domain.com/tmp/{uuid}.png",
    "encodedUrl": "https://example.com",
    "shortUrl": { "url": "...", "code": "..." }
  },
  ...
}
```

---

### `DELETE /v1/qr-lock/{imgId}`

Delete a generated QR image by UUID (with or without `.png` extension).

---

### `GET /v1/qr-url/?url={url}`

Deterministic URL shortening. Same URL always returns the same short code.

---

### `POST /v1/ghibli/callback` — Internal

KIE webhook endpoint. Do not call directly. Receives task completion notifications from KIE.ai and resolves the waiting `asyncio.Future` in the pipeline.

---

## Error Codes

| Code | HTTP | Stage | Cause |
|------|------|-------|-------|
| `INVALID_IMAGE_URL` | 422 | SOURCE_RESOLUTION | Malformed or non-public URL |
| `IMAGE_DOWNLOAD_FAILED` | 422 | SOURCE_RESOLUTION | Could not download image |
| `NO_FACE_DETECTED` | 422 | STAGE1_GHIBLI | No human face in image |
| `MULTIPLE_FACES` | 422 | STAGE1_GHIBLI | Multiple prominent faces detected |
| `FACE_DETECTOR_FAILURE` | 500 | STAGE1_GHIBLI | MediaPipe runtime error |
| `STAGE1_API_ERROR` | 500 | STAGE1_GHIBLI | KIE rejected Stage 1 submission |
| `STAGE1_TASK_FAILED` | 500 | STAGE1_GHIBLI | Stage 1 generation failed |
| `STAGE1_TIMEOUT` | 504 | STAGE1_GHIBLI | No webhook received within 10 min |
| `STAGE2_API_ERROR` | 500 | STAGE2_QR | KIE rejected Stage 2 submission |
| `STAGE2_TASK_FAILED` | 500 | STAGE2_QR | Stage 2 composition failed |
| `STAGE2_TIMEOUT` | 504 | STAGE2_QR | No webhook received within 10 min |
| `IDENTITY_DRIFT_DETECTED` | 500 | STAGE1_GHIBLI | Person identity not preserved after retry |
| `INTERNAL_ERROR` | 500 | ORCHESTRATION | Unhandled server exception |

---

## Troubleshooting

**`STAGE1_TIMEOUT` / `STAGE2_TIMEOUT`**
- Check `DOMAIN` in `.env` matches the public URL exactly (no trailing slash)
- Restart server after changing `DOMAIN`
- For local dev: verify ngrok is running and dashboard at `http://127.0.0.1:4040` shows requests arriving

**`NO_FACE_DETECTED`**
- Image must be a real photo with a visible human face
- Face may be too small (`MIN_FACE_AREA_RATIO=0.03`), heavily obscured, or at an extreme angle

**`FACE_DETECTOR_FAILURE`**
- Server couldn't reach `storage.googleapis.com` at startup to download MediaPipe model
- Check `src/ghibli_portrait/models/blaze_face_short_range.tflite` exists

**Identity drift (person looks different after Stage 1)**
- Switch to `flux-kontext-pro`: `KIE_GHIBLI_MODEL=flux-kontext-pro`
- `qwen/image-edit` has known identity drift — it is a legacy fallback only

**`ModuleNotFoundError: No module named 'src.ghibli_portrait'`**
- Run uvicorn from the `ghibli_qr/` directory, not from `ghibli/`

**Do not use `--workers N > 1`**
- `pending_tasks` is in-memory — webhooks arriving at a different worker will never resolve → timeout
- Single worker handles many concurrent requests fine via asyncio
