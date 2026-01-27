# Ghibli Portrait API V1

Production-ready API for transforming portraits to Ghibli-style art with QR code generation. Built with FastAPI, unified response format, and comprehensive validation.

## What It Does

Transforms regular portrait photos into artistic Ghibli-style images and generates QR codes with lock screen designs. Features automated two-stage pipeline for seamless Ghibli + QR composition.

## Key Features

- **Unified V1 API** - All endpoints under `/v1/*` prefix
- **Consistent Response Format** - `{success, data, message, errors, timestamp}` for all endpoints
- **camelCase API Surface** - Clean, JavaScript-friendly field naming
- **Comprehensive Validation** - Multi-layer validation gate with structured error codes
- **Explicit Model Selection** - `qwen/image-edit` and `seedream` with no fallbacks
- **Request ID Tracking** - Unique identifier for every request
- **Webhook-Based Async Processing** - Efficient task completion handling

---

## High-Level Architecture

The system operates as a **four-layer pipeline** with strict separation of responsibilities:

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
│                        (qwen/image-edit model)                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              LAYER 3B: Stage 2 Validation (Input Trust)                  │
│            (Stage 1 output is TRUSTED - minimal validation)              │
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
| **Stage 2 (QR)** | Compose Ghibli image with QR lock screen | Re-validate human faces |
| **Orchestration** | Coordinate stages, format responses | Add new validation rules |

---

## Stage 1: Human Portrait Validation

### Face Detection Technology

Stage 1 uses **MediaPipe BlazeFace** (CPU-only) for face detection:

- **Model**: `blaze_face_short_range.tflite` (auto-downloaded on first use)
- **Runtime**: MediaPipe Tasks API (`mediapipe.tasks.python.vision.FaceDetector`)
- **Confidence Threshold**: 0.35 minimum detection confidence

**Note**: OpenCV Haar Cascade has been **fully removed** from the codebase.

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
| Background elements | Posters, logos, prints (ignored) |
| Face size | Any size (small/distant faces accepted) |
| Face position | Any position in frame |

### Rejection Rules

The system rejects images **only** in these cases:

| Condition | Error Code | Description |
|-----------|-----------|-------------|
| No face detected | `NO_FACE_DETECTED` | MediaPipe found zero faces in the image |
| Multiple prominent faces | `MULTIPLE_FACES` | Secondary face is ≥65% area AND ≥60% confidence of primary |
| Detector failure | `FACE_DETECTOR_FAILURE` | MediaPipe runtime error (SYSTEM_ERROR) |

### Primary Face Selection

When multiple faces are detected, the **primary face** is selected by:

1. **Largest bounding box area** (highest priority)
2. **Highest confidence score**
3. **Closest to image center** (lowest priority)

### Multiple Face Logic

Multiple faces are **accepted** unless the secondary face is "visually significant":
- Secondary face area ≥ 65% of primary face area
- Secondary face confidence ≥ 60% of primary face confidence

Both conditions must be true for rejection. Background faces in posters, photos, or artwork are typically ignored because they fail one or both thresholds.

---

## Known Limitation: Animal vs Human Detection

### Current Behavior

MediaPipe BlazeFace is a **face detector**, not a **human classifier**. It detects facial structures but cannot distinguish between:

- Human faces
- Certain primate faces (e.g., monkeys, apes)
- Realistic animal illustrations with human-like facial features

**Result**: Some animal images may occasionally pass validation if they contain facial structures that match human face geometry.

### Design Decision

> **The system prioritizes human inclusivity over aggressive animal rejection.**

This means:
- Real human photos are **never** falsely rejected due to appearance, ethnicity, or facial features
- Some edge-case animal images may pass validation (accepted limitation)

### Why This Trade-off?

| Approach | Pros | Cons |
|----------|------|------|
| **Strict filtering** | Blocks more animals | Risk of rejecting real humans (unacceptable) |
| **Inclusive filtering** (current) | Never rejects real humans | Some animals may pass |

Rejecting a real human due to facial features is a **worse outcome** than accepting an occasional animal photo.

### Future Enhancement Path

A dedicated **human vs non-human classifier** could be added as an optional layer:

- Run after face detection (only if face detected)
- Use a trained image classification model
- Return `ANIMAL_OR_CARTOON_DETECTED` only when high confidence
- Maintain human inclusivity as the priority

This is **not currently implemented** and would require careful testing to avoid false rejections.

---

## Stage 2: QR Composition

### Behavior

Stage 2 receives the **Ghibli-transformed image** from Stage 1 and composes it with a QR code lock screen.

### Trust Model

- **Stage 1 output is trusted** - no face detection or human validation occurs in Stage 2
- Stage 2 only validates that the Stage 1 URL is well-formed and reachable
- No image downloading or decoding occurs in Stage 2 validation

### QR Code Integrity

The QR composition model (seedream/4.5-edit) is prompted to:

- Position the person holding the lock screen with both hands
- Ensure the face and head remain visible
- Maintain QR code scannability (no distortion or blur)

---

## Error Handling Philosophy

### Core Principles

1. **Errors are stage-scoped**: Each error includes the pipeline stage where it occurred
2. **No silent fallbacks**: Every failure is explicitly reported with a structured error
3. **Deterministic validation**: Same input always produces same validation result
4. **Single source of truth**: MediaPipe is the only face detection system

### Error Classification

| Type | Description | Example |
|------|-------------|---------|
| `VALIDATION_ERROR` | Input failed validation rules | No face detected, invalid URL |
| `EXTERNAL_ERROR` | External API failure | KIE API error, timeout |
| `SYSTEM_ERROR` | Internal system failure | Face detector crash |
| `UNSUPPORTED_CASE` | Valid but unsupported input | (Reserved for future use) |

### Error Stages

| Stage | Scope |
|-------|-------|
| `INPUT` | Schema validation, URL format |
| `SOURCE_RESOLUTION` | URL reachability, download |
| `STAGE1_GHIBLI` | Face validation, Ghibli generation |
| `STAGE2_QR` | QR composition |
| `ORCHESTRATION` | Pipeline coordination |

### No Mixed Responsibility

- Source resolution errors are isolated from validation errors
- Stage 1 errors never leak into Stage 2
- Each layer handles only its designated errors

---

## API Contract

### Unified Response Envelope

All V1 endpoints return responses with this exact structure:

**Success Response**
```json
{
  "success": true,
  "data": {
    "resultUrls": ["https://..."],
    "model": "qwen/image-edit",
    "costTime": 12
  },
  "message": "Ghibli portrait generated successfully",
  "errors": null,
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

**Error Response**
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
      "message": "No human face detected. Please provide a clear portrait photo of a person."
    }
  ],
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

### Error Object Structure

| Field | Type | Description |
|-------|------|-------------|
| `code` | string | `SCREAMING_SNAKE_CASE` error identifier |
| `type` | enum | `VALIDATION_ERROR`, `EXTERNAL_ERROR`, `SYSTEM_ERROR` |
| `stage` | enum | `INPUT`, `SOURCE_RESOLUTION`, `STAGE1_GHIBLI`, `STAGE2_QR`, `ORCHESTRATION` |
| `field` | string\|null | camelCase field name (if applicable) |
| `message` | string | Human-readable error message |

---

## Swagger / API Organization

### Endpoint Groups

The Swagger documentation (`/docs`) organizes endpoints into these groups:

| Tag | Description |
|-----|-------------|
| **Api Production** | Primary production endpoint (`POST /v1/ghibli-qr`) |
| **Core APIs** | Individual transformation endpoints (`/v1/ghibli`, `/v1/qr-lock`) |
| **Internal / System** | Webhooks and system endpoints (not for external use) |
| **Health & Utilities** | Health checks, URL shortening |

### Primary Production Endpoint

For production use, the recommended endpoint is:

```
POST /v1/ghibli-qr
```

This single endpoint handles the complete pipeline:
1. Validates input image (face detection)
2. Transforms to Ghibli style (Stage 1)
3. Composes with QR lock screen (Stage 2)
4. Returns final composed image

---

## Installation

### Prerequisites
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) package manager

### Setup Steps

1. **Clone the repository**
```bash
git clone https://github.com/cyber-ai-dep/ghibli_qr
cd ghibli_qr
```

2. **Install dependencies**
```bash
pip install uv
uv sync
```

3. **Configure environment variables**
```bash
cp .env.example .env
```

Edit `.env` file with your configuration:
```env
DOMAIN=https://your-domain.com
KIE_API_KEY=your_kie_api_key

# Required models (no fallbacks):
KIE_GHIBLI_MODEL=qwen/image-edit
KIE_COMPOSE_MODEL=seedream/4.5-edit

# Validation settings:
REQUIRE_HUMAN_FACE=true
MAX_FACES=1
SHORT_CODE_LENGTH=8
```

4. **Create required directories**
```bash
mkdir -p src/static/tmp
```

5. **Run the server**
```bash
uv run uvicorn src.ghibli_portrait.main:app --reload --host 0.0.0.0 --port 8000
```

Server starts at `http://localhost:8000`

API documentation at `http://localhost:8000/docs`

## Webhook Setup with Ngrok

The system uses webhooks for receiving AI processing results. For local development:

1. **Install Ngrok**
```bash
sudo snap install ngrok
```

2. **Start Ngrok tunnel**
```bash
ngrok http 8000
```

3. **Update .env with Ngrok URL**
```env
DOMAIN=https://your-ngrok-url.ngrok-free.dev
```

4. **Restart server** to apply new domain

---

## API Endpoints

All endpoints are prefixed with `/v1`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/health` | GET | Service health check with timestamp |
| `/v1/ghibli` | POST | Transform portrait to Ghibli style (qwen/image-edit) |
| `/v1/ghibli/callback` | POST | Webhook callback (internal - KIE API use only) |
| `/v1/qr-lock` | POST | Generate QR code with lock screen overlay |
| `/v1/qr-lock/{imgId}` | DELETE | Delete temporary QR code image |
| `/v1/qr-url` | GET | Get shortened URL (deterministic hashing) |
| `/v1/ghibli-qr` | POST | Automated Ghibli + QR pipeline (qwen + seedream) |

## API Usage

### 1. Health Check

```bash
curl -X GET "http://localhost:8000/v1/health"
```

**Response:**
```json
{
  "success": true,
  "data": {
    "status": "healthy"
  },
  "message": "Ghibli Portrait API V1 is running",
  "errors": null,
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

### 2. Transform Portrait to Ghibli Style

**Endpoint:** `POST /v1/ghibli`

**Model:** `qwen/image-edit` (explicitly configured, no fallback)

**Request:**
```json
{
  "imgUrls": ["https://i.ibb.co/2JKZ4fC/portrait.jpg"],
  "quality": "basic",
  "aspectRatio": "1:1",
  "prompt": "Convert to Ghibli style art"
}
```

**Fields:**
- `imgUrls` (required): Array with single image URL (public HTTP/HTTPS)
- `quality` (optional): `"basic"` (2K, default) or `"high"` (4K)
- `aspectRatio` (optional): `"1:1"` (default), `"4:3"`, `"3:4"`, `"16:9"`, `"9:16"`, `"2:3"`, `"3:2"`, `"21:9"`
- `prompt` (optional): Custom prompt (uses default if not provided)

**Response (Success):**
```json
{
  "success": true,
  "data": {
    "resultUrls": ["https://tempfile.aiquickdraw.com/ghibli.png"],
    "model": "qwen/image-edit",
    "costTime": 12,
    "quality": "basic",
    "aspectRatio": "1:1"
  },
  "message": "Ghibli portrait generated successfully",
  "errors": null,
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

**cURL Example:**
```bash
curl -X POST "http://localhost:8000/v1/ghibli" \
  -H "Content-Type: application/json" \
  -d '{
    "imgUrls": ["https://i.ibb.co/2JKZ4fC/portrait.jpg"],
    "quality": "high",
    "aspectRatio": "16:9"
  }'
```

### 3. Generate QR Code with Lock Screen

**Endpoint:** `POST /v1/qr-lock`

**Request:**
```json
{
  "url": "https://example.com",
  "shortenUrl": true,
  "version": 1
}
```

**Fields:**
- `url` (required): URL to encode in QR code
- `shortenUrl` (optional): Whether to shorten the URL (default: false)
- `version` (optional): QR code version 1-40 (auto-determined if not provided)

**Response:**
```json
{
  "success": true,
  "data": {
    "qrUrl": "https://your-domain.com/tmp/uuid.png",
    "encodedUrl": "https://example.com",
    "shortUrl": {
      "url": "https://your-domain.com/s/abc123",
      "code": "abc123"
    }
  },
  "message": "QR code with lock screen generated successfully",
  "errors": null,
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

### 4. Get Shortened URL

**Endpoint:** `GET /v1/qr-url?url=https://example.com`

**Response:**
```json
{
  "success": true,
  "data": {
    "url": "https://your-domain.com/s/abc123",
    "code": "abc123"
  },
  "message": "Short URL generated successfully",
  "errors": null,
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

### 5. Delete QR Image

**Endpoint:** `DELETE /v1/qr-lock/{imgId}`

**Response:**
```json
{
  "success": true,
  "data": {
    "deletedId": "uuid-here"
  },
  "message": "Image deleted successfully",
  "errors": null,
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

### 6. Automated Ghibli + QR Pipeline

**Endpoint:** `POST /v1/ghibli-qr`

**Models:**
- Stage 1: `qwen/image-edit` (Ghibli transformation)
- Stage 2: `seedream/4.5-edit` (QR composition)

**Request:**
```json
{
  "imgUrl": "https://i.ibb.co/2JKZ4fC/portrait.jpg",
  "url": "https://your-profile.com"
}
```

**Fields:**
- `imgUrl` (required): Portrait image URL to transform
- `url` (required): URL to encode in QR code

**Response:**
```json
{
  "success": true,
  "data": {
    "resultUrls": ["https://tempfile.aiquickdraw.com/final.png"],
    "model": "seedream/4.5-edit",
    "costTime": 45,
    "quality": "basic",
    "aspectRatio": "1:1"
  },
  "message": "Ghibli + QR pipeline completed successfully",
  "errors": null,
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

---

## Validation Policy

The V1 API enforces these validation requirements:

### Layer 1: Source Resolution

- URL must start with `http://` or `https://`
- No localhost or private IPs allowed
- Must be publicly accessible
- Must return valid HTTP response

### Layer 2: Image Decoding

- Image must be decodable by PIL
- Supported formats: JPEG, PNG, WebP, GIF
- Image integrity must be valid

### Layer 3A: Human Portrait (Stage 1)

- At least one human face must be detected (MediaPipe BlazeFace)
- Maximum 1 prominent face (configurable via `MAX_FACES`)
- Face detection uses CPU-only runtime

### Layer 3B: Stage 2 Input

- Stage 1 output URL must be well-formed
- No face re-validation (Stage 1 output is trusted)

---

## Error Codes Reference

| Code | HTTP Status | Stage | Description |
|------|-------------|-------|-------------|
| `SINGLE_IMAGE_REQUIRED` | 422 | INPUT | Request must contain exactly one image URL |
| `INVALID_IMAGE_URL` | 422 | SOURCE_RESOLUTION | URL is not publicly accessible or invalid format |
| `IMAGE_DOWNLOAD_FAILED` | 422 | SOURCE_RESOLUTION | Failed to download or decode the image |
| `NO_FACE_DETECTED` | 422 | STAGE1_GHIBLI | No human face found in the image |
| `MULTIPLE_FACES` | 422 | STAGE1_GHIBLI | Multiple prominent human faces detected |
| `FACE_DETECTOR_FAILURE` | 500 | STAGE1_GHIBLI | Face detection system unavailable (SYSTEM_ERROR) |
| `GENERATION_API_ERROR` | 500 | STAGE1_GHIBLI | External AI API returned an error |
| `GENERATION_TASK_FAILED` | 500 | STAGE1_GHIBLI | AI generation task failed on external service |
| `STAGE1_API_ERROR` | 500 | STAGE1_GHIBLI | Stage 1 (Ghibli) API error |
| `STAGE1_TASK_FAILED` | 500 | STAGE1_GHIBLI | Stage 1 (Ghibli) task failed |
| `STAGE1_TIMEOUT` | 504 | STAGE1_GHIBLI | Stage 1 exceeded 5-minute timeout |
| `STAGE2_API_ERROR` | 500 | STAGE2_QR | Stage 2 (composition) API error |
| `STAGE2_TASK_FAILED` | 500 | STAGE2_QR | Stage 2 (composition) task failed |
| `STAGE2_TIMEOUT` | 504 | STAGE2_QR | Stage 2 exceeded 5-minute timeout |
| `WEBHOOK_TIMEOUT` | 504 | varies | Processing exceeded 5-minute timeout |
| `IMAGE_NOT_FOUND` | 404 | INPUT | QR image not found for deletion |
| `INTERNAL_ERROR` | 500 | ORCHESTRATION | Unexpected server error |

---

## Model Configuration

### Required Models (No Fallbacks)

The API requires explicit model configuration. **No automatic fallback is allowed.**

1. **qwen/image-edit**
   - Used for: `/v1/ghibli`, Stage 1 of `/v1/ghibli-qr`
   - Purpose: Ghibli-style transformation
   - Processing time: 5-15 seconds
   - Input: Single image URL
   - Configuration: `KIE_GHIBLI_MODEL=qwen/image-edit`

2. **seedream/4.5-edit**
   - Used for: Stage 2 of `/v1/ghibli-qr`
   - Purpose: QR code + Ghibli image composition
   - Processing time: 20-40 seconds
   - Input: Multiple image URLs (Ghibli result + QR lock)
   - Configuration: `KIE_COMPOSE_MODEL=seedream/4.5-edit`

**Important:** If a model is not configured, the API will return an error. There is no fallback logic.

---

## Image Hosting for Input URLs

All image URLs must be publicly accessible. Recommended free hosting:

### ImgBB (Example)

1. Visit https://imgbb.com/
2. Upload your image
3. Copy the direct image URL (must end with `.jpg`, `.png`, etc.)
4. Use this URL in your API request

**Example URL:**
```
https://i.ibb.co/2JKZ4fC/portrait.jpg
```

---

## Configuration

### Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `DOMAIN` | Yes | Public URL for webhooks and static files | - |
| `KIE_API_KEY` | Yes | KIE.ai API authentication token | - |
| `KIE_GHIBLI_MODEL` | Yes | Stage 1 model (qwen/image-edit) | - |
| `KIE_COMPOSE_MODEL` | Yes | Stage 2 model (seedream/4.5-edit) | - |
| `REQUIRE_HUMAN_FACE` | No | Enable face detection validation | `true` |
| `MAX_FACES` | No | Maximum faces allowed (0 = unlimited) | `1` |
| `SHORT_CODE_LENGTH` | No | URL shortener code length | `8` |

### Quality Options

- `basic` - 2K resolution (faster, recommended)
- `high` - 4K resolution (slower, model dependent)

### Aspect Ratios

- `1:1` - Square (default)
- `4:3` - Standard landscape
- `3:4` - Standard portrait
- `16:9` - Widescreen
- `9:16` - Vertical video
- `2:3`, `3:2`, `21:9` - Additional options

---

## Deployment

### Production Deployment

1. **Deploy to cloud platform** (Railway, Render, Fly.io, AWS, GCP, Azure)
2. **Set environment variables** with production domain and API keys
3. **Ensure DOMAIN is publicly accessible** for webhook callbacks
4. **Configure monitoring** for request IDs and error tracking
5. **Set up log aggregation** for unified response tracking

### Docker Deployment

```bash
docker build -t ghibli-api-v1 .
docker run -p 8000:8000 --env-file .env ghibli-api-v1
```

---

## Tech Stack

- **FastAPI** - Modern async web framework
- **Pydantic** - Data validation with camelCase support
- **Pillow** - Image processing for QR generation
- **MediaPipe** - Face detection (BlazeFace, CPU-only)
- **KIE.ai API** - AI image generation (qwen + seedream)
- **Webhooks** - Async task completion flow
- **Python 3.10+** - Runtime environment

---

## Development

### Project Structure

```
src/ghibli_portrait/
├── api/
│   ├── routes.py           # V1 API endpoints (unified, /v1/*)
│   └── responses.py        # Unified response helpers
├── models/
│   ├── schemas.py          # Request/response schemas (camelCase)
│   └── blaze_face_short_range.tflite  # MediaPipe model (auto-downloaded)
├── services/
│   ├── image_service.py    # Image generation logic
│   ├── qr_service.py       # QR code generation
│   └── validation_service.py # Validation gate (multi-layer)
├── utils/
│   ├── url_utils.py        # URL shortening
│   └── image_utils.py      # Image utilities
├── config.py               # Settings management
└── main.py                 # FastAPI app entry point
```

### Running Tests

```bash
# Install test dependencies
uv sync --dev

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/ghibli_portrait
```

---

## Documentation

- **OpenAPI/Swagger**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/openapi.json

---

## License

See LICENSE file for details.

## Support

For issues, questions, or feature requests, please open an issue on GitHub.
