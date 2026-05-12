# Ghibli Portrait API V1

Production-ready API for transforming portraits into Ghibli-style art with QR code generation. Built with FastAPI, unified response format, multi-layer validation, and identity-preserving style transfer.

## What It Does

Transforms real portrait photos into hand-drawn Studio Ghibli-style illustrations and generates QR codes embedded in a lock-screen overlay. Features an automated two-stage async pipeline with webhook-based task handling.

---

## What's New (Latest Release)

### Ghibli Style Tuning — Visible Style Transfer with Identity Preservation
The Stage 1 generation parameters have been rebalanced to produce actual Ghibli-style art while keeping the person's identity intact:

- **`guidance_scale` raised from `3.0` → `7.5`** — the model now follows the Ghibli style prompt strongly instead of barely applying any style change
- **`image_strength` raised from `0.35` → `0.65`** — allows real artistic transformation (at 0.35 the photo barely changed)
- **`denoise` raised from `0.30` → `0.65`** — allows stylistic rendering instead of staying photo-like
- **`fidelity` / `reference_strength` kept high (`0.90`)** — still anchors face structure and identity
- **Richer style prompt** — the prompt now describes concrete Ghibli visual qualities: soft watercolor backgrounds, warm painterly palette, cel-shaded lighting, clean expressive linework, atmospheric depth. Previously it only listed identity preservation rules with no visual style description.
- **Negative prompt updated** — added `photorealistic, photograph, realistic lighting, camera photo` to push the model away from producing a photo-realistic output

### Identity-Preserving Style Transfer
The Stage 1 prompt system enforces identity fidelity during Ghibli transformation:

- **Identity lock rules** — explicitly instructs the model to preserve exact skin tone, ethnicity, facial geometry, hair, clothing, pose, and composition
- **Negative prompt support** — passed to models that support it, forbidding generic anime faces, identity drift, skin tone changes, and facial simplification
- **Fidelity controls** — `image_strength`, `denoise`, `fidelity`, `reference_strength`, `preserve_identity`, `preserve_face` are injected into Stage 1 API calls

### Post-Generation Identity Drift Detection
A heuristic identity check can run after Stage 1 output is downloaded:

- Detects **face absence** in the output (full identity replacement)
- Detects **face area drift** > 30% (extreme recomposition)
- Detects **skin-tone hue shift** > 40° in the face region
- On drift detection, **automatically retries Stage 1 once** before rejecting
- Controlled by `ENABLE_IDENTITY_CHECK` env var (default `false` — disabled because `qwen/image-edit` triggers false positives with Ghibli style; re-enable if switching to a high-fidelity model)

### Flux Kontext Model Support (Code-Ready)
Full support for `flux-kontext-pro` / `flux-kontext-max` is implemented in the generation layer:

- Correct flat-under-`input` payload structure (`inputImage`, `aspectRatio`, `outputFormat`, `safetyTolerance`)
- Flux Kontext callback result format (`info.resultImageUrl`) handled alongside standard `resultUrls`
- Set `KIE_GHIBLI_MODEL=flux-kontext-pro` in `.env` to activate — **verify the model ID is available on your KIE account first**
- Currently **not active** — `KIE_GHIBLI_MODEL=qwen/image-edit` is the working model

### Stage 2 Download Timeout Fix
Resolved KIE Stage 2 (Seedream) timing out when downloading input images:

- **Stage 1 output re-hosted locally** — CDN URL is temporary and often unreachable cross-service. Stage 1 output is downloaded immediately, resized to max 1024px, saved as JPEG, and served from the local server
- **QR lock image resized to JPEG** — original PNG lock file resized to max 1024px JPEG before being passed to Stage 2

### QR Proportional Sizing
- `QR_LOCK_TARGET_WIDTH_RATIO = 0.28` — QR targets 28% of lock image width
- `QR_LOCK_MIN_WIDTH_RATIO = 0.22` / `QR_LOCK_MAX_WIDTH_RATIO = 0.32` — clamped range
- Works correctly with any lock image resolution

### API Contract & Swagger Fixes
- All route decorators carry `response_model=ApiSuccessResponse` and `responses={422, 500, 504}`
- Global `RequestValidationError` handler converts Pydantic validation errors to the V1 unified envelope
- Tag corrected to `"API Production"` (was `"Api Production"`)

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         LAYER 0: Schema Validation                       │
│                    (Pydantic: field types, required fields)              │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      LAYER 1: Source Resolution                          │
│            (URL format, reachability, download, content-type)            │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        LAYER 2: Image Decoding                           │
│                  (PIL decode, format validation, integrity)              │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              LAYER 3A: Stage 1 Validation (Human Portrait)               │
│               (MediaPipe BlazeFace: face detection only)                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STAGE 1: Ghibli Transformation                        │
│                  (Active model: qwen/image-edit)                         │
│     Rich Ghibli style prompt + identity lock + fidelity controls         │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│          IDENTITY DRIFT CHECK (optional — disabled by default)           │
│     Face presence · Area stability · Skin-tone hue — auto-retry once    │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              LAYER 3B: Stage 2 Validation (Input Trust)                  │
│            (Stage 1 output is TRUSTED — minimal validation)              │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      STAGE 2: QR Composition                             │
│                      (seedream/4.5-edit model)                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    LAYER 4: Orchestration & Response                     │
│          (Coordinates stages, formats responses, handles errors)         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Stage Responsibilities

| Stage | Responsibility | MUST NOT Do |
|-------|---------------|-------------|
| **Source Resolution** | Validate URL format, download image bytes | Interpret image content |
| **Image Decoding** | Decode bytes to PIL Image, validate format | Apply business rules |
| **Stage 1 (Ghibli)** | Detect human faces, generate Ghibli art | Perform QR operations |
| **Identity Check** | Verify skin tone / face preserved post-generation | Block on system errors |
| **Stage 2 (QR)** | Compose Ghibli image with QR lock screen | Re-validate human faces |
| **Orchestration** | Coordinate stages, format responses | Add new validation rules |

---

## Stage 1: Ghibli Style Transfer

### Active Model

| Model | Status | Identity Preservation | Notes |
|-------|--------|-----------------------|-------|
| `qwen/image-edit` | **Active** | Moderate | Current working model — tuned with high guidance_scale and style prompt |
| `flux-kontext-pro` | Code-ready | Excellent | Better identity preservation — activate once model ID confirmed on your KIE account |
| `flux-kontext-max` | Code-ready | Excellent | Higher quality, slower — same requirement |

Set `KIE_GHIBLI_MODEL` in `.env` to switch models. The code handles Qwen, Flux Kontext, and Seedream API payload structures automatically.

### Prompt Strategy

Stage 1 uses a two-part prompt system:

**Positive prompt** — describes the Ghibli style and what identity traits to preserve:
> *"Convert this photo into a Studio Ghibli hand-painted illustration. Apply the full Ghibli visual style: soft watercolor backgrounds, warm painterly color palette, clean expressive linework, cel-shaded lighting, lush atmospheric depth, and the characteristic hand-drawn Ghibli texture throughout every surface.*
>
> *IDENTITY LOCK — never change: same person, same face structure, same skin tone, same ethnicity, same race, same hairstyle, same facial hair, same expression, same clothing, same pose, same hands, same background composition.*
>
> *Result: the exact same person rendered as a Ghibli film character."*

**Negative prompt** (applied when supported by the model):
> *"photorealistic, photograph, realistic lighting, camera photo, generic anime face, identity drift, race change, skin tone change, beautification, face replacement, facial simplification, different person, altered ethnicity, altered hairstyle, altered expression"*

### Fidelity Parameters (Active Values)

The following parameters are injected into every Stage 1 API call (silently ignored if unsupported):

| Parameter | Value | Effect |
|-----------|-------|--------|
| `guidance_scale` | `7.5` | Strong prompt adherence — model follows Ghibli style instruction |
| `num_inference_steps` | `30` | Generation quality |
| `image_strength` | `0.65` | Allows full artistic transformation while referencing source |
| `denoise` | `0.65` | Allows stylistic rendering |
| `fidelity` | `0.90` | High structural fidelity to reference image |
| `reference_strength` | `0.90` | Strong reference/guidance anchoring identity |
| `preserve_identity` | `true` | Explicit identity lock |
| `preserve_face` | `true` | Explicit face lock |

`guidance_scale` and `num_inference_steps` are overridable via environment variables.

---

## Stage 1: Human Portrait Validation

### Face Detection Technology

Stage 1 uses **MediaPipe BlazeFace** (CPU-only) for face detection:

- **Model**: `blaze_face_short_range.tflite` (auto-downloaded on first use)
- **Runtime**: MediaPipe Tasks API (`mediapipe.tasks.python.vision.FaceDetector`)
- **Confidence Threshold**: 0.35 minimum detection confidence

### Acceptance Rules

The system accepts **any image containing a human face**, regardless of:

| Attribute | Accepted |
|-----------|----------|
| Gender | Male, female, non-binary |
| Ethnicity | All ethnicities |
| Facial hair | Beard, mustache, clean-shaven |
| Head covering | Hijab, turban, hat, no covering |
| Hairstyle | Any hairstyle, bald |
| Glasses | With or without |
| Background | Simple or complex |
| Face size | Any size (small/distant faces accepted) |
| Face position | Any position in frame |

### Rejection Rules

| Condition | Error Code | Description |
|-----------|-----------|-------------|
| No face detected | `NO_FACE_DETECTED` | MediaPipe found zero faces in the image |
| Multiple prominent faces | `MULTIPLE_FACES` | Secondary face ≥65% area AND ≥60% confidence of primary |
| Detector failure | `FACE_DETECTOR_FAILURE` | MediaPipe runtime error (SYSTEM_ERROR) |

---

## Stage 2: QR Composition

Stage 2 receives the Ghibli-transformed image and composes it with a QR code lock screen.

- **Stage 1 output is trusted** — no face detection or re-validation occurs
- Both inputs (Ghibli JPEG + QR lock JPEG) are resized to max 1024px before submission to ensure KIE's download completes within its internal timeout window
- QR lock overlay uses proportional sizing relative to lock image dimensions (not hardcoded pixels)

---

## Error Handling

### Error Classification

| Type | Description | Example |
|------|-------------|---------|
| `VALIDATION_ERROR` | Input failed validation rules | No face detected, invalid URL |
| `EXTERNAL_ERROR` | External API failure | KIE API error, timeout, identity drift |
| `SYSTEM_ERROR` | Internal system failure | Face detector crash |
| `UNSUPPORTED_CASE` | Valid but unsupported input | Reserved |

### Error Stages

| Stage | Scope |
|-------|-------|
| `INPUT` | Schema validation, URL format |
| `SOURCE_RESOLUTION` | URL reachability, download |
| `STAGE1_GHIBLI` | Face validation, Ghibli generation, identity check |
| `STAGE2_QR` | QR composition, re-hosting |
| `ORCHESTRATION` | Pipeline coordination |

---

## API Contract

### Unified Response Envelope

All V1 endpoints return:

**Success**
```json
{
  "success": true,
  "data": { "resultUrls": ["https://..."], "model": "...", "costTime": 42 },
  "message": "Ghibli + QR pipeline completed successfully",
  "errors": null,
  "timestamp": "2026-05-11T08:00:00.000Z"
}
```

**Error**
```json
{
  "success": false,
  "data": null,
  "message": "Request validation failed",
  "errors": [
    {
      "code": "NO_FACE_DETECTED",
      "type": "VALIDATION_ERROR",
      "stage": "STAGE1_GHIBLI",
      "field": "imgUrl",
      "message": "No human face detected. Please provide a clear portrait photo."
    }
  ],
  "timestamp": "2026-05-11T08:00:00.000Z"
}
```

---

## API Endpoints

All endpoints are prefixed with `/v1`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/health` | GET | Service health check |
| `/v1/ghibli` | POST | Transform portrait to Ghibli style |
| `/v1/ghibli/callback` | POST | Webhook callback (internal — KIE use only) |
| `/v1/qr-lock` | POST | Generate QR code with lock screen overlay |
| `/v1/qr-lock/{imgId}` | DELETE | Delete temporary QR code image |
| `/v1/qr-url` | GET | Get shortened URL (deterministic hashing) |
| `/v1/ghibli-qr` | POST | **Primary** — automated Ghibli + QR pipeline |

### Primary Endpoint: `POST /v1/ghibli-qr`

**Request:**
```json
{
  "imgUrl": "https://i.ibb.co/2JKZ4fC/portrait.jpg",
  "url": "https://your-profile.com"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "resultUrls": ["https://..."],
    "model": "seedream/4.5-edit",
    "costTime": 45,
    "quality": "basic",
    "aspectRatio": "1:1"
  },
  "message": "Ghibli + QR pipeline completed successfully",
  "errors": null,
  "timestamp": "2026-05-11T08:00:00.000Z"
}
```

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
mkdir -p src/ghibli_portrait/static/tmp
```

Edit `.env`:
```env
DOMAIN=https://your-domain.com
KIE_API_KEY=your_kie_api_key

# Stage 1 — active model (qwen/image-edit confirmed working)
# Switch to flux-kontext-pro once confirmed available on your KIE account
KIE_GHIBLI_MODEL=qwen/image-edit

# Stage 2 — QR composition
KIE_COMPOSE_MODEL=seedream/4.5-edit

# Validation
REQUIRE_HUMAN_FACE=true
MAX_FACES=1
MIN_FACE_AREA_RATIO=0.03
SHORT_CODE_LENGTH=8

# Identity drift check (disabled — qwen triggers false positives with Ghibli style)
# Enable once using flux-kontext-pro
ENABLE_IDENTITY_CHECK=false
```

Run:
```bash
uvicorn src.ghibli_portrait.main:app --host 0.0.0.0 --port 8010 --reload
```

### Webhook Setup (local development)

```bash
ngrok http 8010
# Copy the HTTPS URL → set as DOMAIN in .env → restart server
```

---

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DOMAIN` | Yes | — | Public URL for webhooks and static file serving |
| `KIE_API_KEY` | Yes | — | KIE.ai API authentication key |
| `KIE_GHIBLI_MODEL` | Yes | — | Stage 1 model (`qwen/image-edit` active; `flux-kontext-pro` when available) |
| `KIE_COMPOSE_MODEL` | Yes | — | Stage 2 model (`seedream/4.5-edit`) |
| `REQUIRE_HUMAN_FACE` | No | `true` | Enable face detection gate |
| `MAX_FACES` | No | `1` | Max prominent faces allowed (0 = unlimited) |
| `MIN_FACE_AREA_RATIO` | No | `0.03` | Minimum face-to-image area ratio |
| `SHORT_CODE_LENGTH` | No | `8` | URL shortener code length |
| `ENABLE_IDENTITY_CHECK` | No | `false` | Post-generation identity drift detection (disable for qwen) |
| `STAGE1_GUIDANCE_SCALE` | No | `7.5` | Qwen guidance scale — higher = stronger Ghibli style |
| `STAGE1_NUM_INFERENCE_STEPS` | No | `30` | Qwen inference steps |

---

## Error Codes Reference

| Code | HTTP | Stage | Description |
|------|------|-------|-------------|
| `SINGLE_IMAGE_REQUIRED` | 422 | INPUT | Request must contain exactly one image URL |
| `INVALID_IMAGE_URL` | 422 | SOURCE_RESOLUTION | URL is not publicly accessible or invalid |
| `IMAGE_DOWNLOAD_FAILED` | 422 | SOURCE_RESOLUTION | Failed to download the image |
| `NO_FACE_DETECTED` | 422 | STAGE1_GHIBLI | No human face found in the image |
| `MULTIPLE_FACES` | 422 | STAGE1_GHIBLI | Multiple prominent human faces detected |
| `FACE_DETECTOR_FAILURE` | 500 | STAGE1_GHIBLI | Face detection system unavailable |
| `STAGE1_API_ERROR` | 500 | STAGE1_GHIBLI | Stage 1 API returned an error |
| `STAGE1_TASK_FAILED` | 500 | STAGE1_GHIBLI | Stage 1 generation task failed |
| `STAGE1_TIMEOUT` | 504 | STAGE1_GHIBLI | Stage 1 exceeded 5-minute timeout |
| `IDENTITY_DRIFT_DETECTED` | 500 | STAGE1_GHIBLI | Stage 1 output failed identity check after retry |
| `STAGE2_API_ERROR` | 500 | STAGE2_QR | Stage 2 API returned an error |
| `STAGE2_TASK_FAILED` | 500 | STAGE2_QR | Stage 2 composition task failed |
| `STAGE2_TIMEOUT` | 504 | STAGE2_QR | Stage 2 exceeded 5-minute timeout |
| `INTERNAL_ERROR` | 500 | ORCHESTRATION | Unexpected server error |

---

## Tech Stack

- **FastAPI** — async web framework with OpenAPI/Swagger
- **Pydantic v2** — schema validation with camelCase API surface
- **Pillow** — image processing, JPEG optimization, QR placement
- **MediaPipe** — BlazeFace CPU-only face detection
- **KIE.ai API** — AI image generation (Qwen / Seedream; Flux Kontext code-ready)
- **Python 3.10+**

---

## Project Structure

```
src/ghibli_portrait/
├── api/
│   ├── routes.py             # V1 endpoints, pipeline orchestration
│   └── responses.py          # Unified response helpers
├── models/
│   └── schemas.py            # Request/response schemas (camelCase)
├── services/
│   ├── image_service.py      # KIE API calls (Qwen / Flux Kontext / Seedream)
│   ├── identity_check.py     # Post-generation identity drift detection
│   ├── qr_service.py         # QR code generation (proportional sizing)
│   └── validation_service.py # Multi-layer validation gate
├── utils/
│   ├── url_utils.py          # URL shortening
│   └── image_utils.py        # Image utilities
├── config.py                 # Settings and prompt configuration
└── main.py                   # FastAPI app + global error handlers
```

---

## Deployment

```bash
# Docker
docker build -t ghibli-api-v1 .
docker run -p 8010:8010 --env-file .env ghibli-api-v1
```

Deploy to any platform that supports Python (Railway, Render, Fly.io, AWS, GCP, Azure). Ensure `DOMAIN` is publicly reachable for KIE webhook callbacks.

---

## Documentation

- **Swagger UI**: `http://localhost:8010/docs`
- **ReDoc**: `http://localhost:8010/redoc`
- **OpenAPI JSON**: `http://localhost:8010/openapi.json`

---

## License

See LICENSE file for details.
