# Implementation Guide - Ghibli Portrait API

Complete technical documentation for the Ghibli Portrait API internal implementation.

## Table of Contents
- [Overview](#overview)
- [Architecture](#architecture)
- [Validation Implementation](#validation-implementation)
- [Stage Separation Guarantees](#stage-separation-guarantees)
- [Error Handling & Failure Isolation](#error-handling--failure-isolation)
- [File & Responsibility Mapping](#file--responsibility-mapping)
- [Installation](#installation)
- [Configuration](#configuration)
- [Code Structure](#code-structure)
- [Key Features](#key-features)
- [API Reference](#api-reference)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)

---

## Overview

The Ghibli Portrait API transforms photos into Ghibli-style artwork and generates QR codes with lock screen overlays. The system supports multiple AI models through KIE.ai API and uses webhook-based async processing.

### Key Capabilities
- Ghibli-style image transformation via AI
- Human portrait validation (MediaPipe BlazeFace)
- QR code generation with custom lock screen
- URL shortening with deterministic hashing
- Automated two-stage pipeline processing
- Multi-model support (Qwen/Seedream)

---

## Architecture

### System Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              CLIENT REQUEST                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         LAYER 0: Schema Validation                       │
│                    (Pydantic: field types, required fields)              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      LAYER 1: Source Resolution                          │
│         (URL format, localhost rejection, download, content-type)        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        LAYER 2: Image Decoding                           │
│                  (PIL decode, format validation, integrity)              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              LAYER 3A: Stage 1 Validation (Human Portrait)               │
│               (MediaPipe BlazeFace: face detection only)                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STAGE 1: Ghibli Transformation                        │
│                    (KIE.ai API → qwen/image-edit)                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              LAYER 3B: Stage 2 Validation (Input Trust)                  │
│            (Stage 1 output is TRUSTED - URL check only)                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      STAGE 2: QR Composition                             │
│                   (KIE.ai API → seedream/4.5-edit)                       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    LAYER 4: Orchestration & Response                     │
│          (Coordinates stages, formats responses, handles errors)         │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                              CLIENT RESPONSE                             │
└─────────────────────────────────────────────────────────────────────────┘
```

### Webhook Processing Model

The system uses **webhooks** for optimal performance:

```
Client Request → FastAPI Server → KIE.ai API (creates task)
                      ↓
            asyncio.Future waits for webhook
                      ↓
KIE.ai Processing → Webhook Callback → Future.set_result()
                      ↓
            Returns result to client
```

**Webhook Benefits:**
- Real-time notifications
- No polling overhead
- Instant result delivery
- Resource efficient

**Requirements:**
- Public domain URL (use Ngrok for localhost)
- `/v1/ghibli/callback` endpoint accessible to KIE.ai

---

## Validation Implementation

### Face Detection Technology

The system uses **MediaPipe BlazeFace** (CPU-only) as the **sole** face detection system.

| Component | Value |
|-----------|-------|
| **Model** | `blaze_face_short_range.tflite` |
| **Runtime** | MediaPipe Tasks API |
| **Confidence Threshold** | 0.35 |
| **Execution** | CPU-only (no GPU required) |
| **Model Location** | Auto-downloaded to `src/ghibli_portrait/models/` |

**Important**: OpenCV Haar Cascade has been **fully removed** from the codebase. MediaPipe BlazeFace is the single source of truth for face detection.

### Model Auto-Download

The BlazeFace model is automatically downloaded on first use:

```python
_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
_MODEL_CACHE_DIR = Path(__file__).parent.parent / "models"
_MODEL_PATH = _MODEL_CACHE_DIR / "blaze_face_short_range.tflite"
```

If the model file exists, it is used directly. Otherwise, it is downloaded and cached.

### Face Detection Output

The `_detect_faces()` function returns a `FaceDetectionResult` dataclass:

```python
@dataclass
class FaceDetectionResult:
    ok: bool                           # Detection succeeded (not validation result)
    face_count: int = 0                # Number of faces detected
    primary_face_area_ratio: float = 0.0  # Primary face area / image area
    faces: List[Dict] = None           # Sorted list of face info
    error: Optional[str] = None        # Error message if ok=False
```

Each face in the `faces` list contains:

```python
{
    "bbox": (x, y, width, height),     # Bounding box in pixels
    "score": float,                     # Detection confidence (0.0-1.0)
    "area": float,                      # Bounding box area in pixels
    "area_ratio": float,                # area / image_area
    "center_distance": float,           # Normalized distance from image center
}
```

### Primary Face Selection

When multiple faces are detected, the **primary face** is selected using this priority:

| Priority | Criterion | Rationale |
|----------|-----------|-----------|
| 1 (highest) | Largest bounding box area | Main subject is typically largest |
| 2 | Highest confidence score | More reliable detection |
| 3 (lowest) | Closest to image center | Compositional convention |

Implementation:

```python
faces_info.sort(
    key=lambda f: (-f["area"], -f["score"], f["center_distance"])
)
```

### Multiple Face Rejection Logic

Multiple faces are **accepted** unless the secondary face is "visually significant":

| Condition | Threshold | Rationale |
|-----------|-----------|-----------|
| Area ratio | Secondary ≥ 65% of primary area | Comparable visual prominence |
| Confidence ratio | Secondary ≥ 60% of primary confidence | Reliable detection |

**Both conditions must be true** for rejection. This design:
- Accepts images with background faces (posters, photos, logos)
- Rejects true multi-person portraits
- Minimizes false rejections

### What Face Detection Does NOT Do

MediaPipe BlazeFace detects **facial geometry only**. It does NOT:

- Classify species (human vs animal)
- Detect cartoon/illustration vs real photo
- Measure image quality or resolution
- Evaluate face size (no FACE_TOO_SMALL rejection)
- Apply realism heuristics

### Human vs Animal Detection: Accepted Limitation

#### The Reality

MediaPipe BlazeFace is a **face detector**, not a **human classifier**. It detects facial structures based on geometric patterns, which means:

- Human faces are detected
- Certain primate faces (monkeys, apes) may occasionally be detected
- Realistic animal illustrations with human-like facial features may pass

#### Design Decision

> **The system prioritizes human inclusivity over aggressive animal rejection.**

This trade-off was chosen because:

| Risk | Impact | Decision |
|------|--------|----------|
| Rejecting real humans | Unacceptable (false negatives) | AVOID |
| Accepting some animals | Tolerable (false positives) | ACCEPT |

Rejecting a real human due to facial features, ethnicity, appearance, or edge-case detection is a **worse outcome** than occasionally accepting an animal photo.

#### What This Means in Practice

1. If MediaPipe detects a face → image is treated as human
2. No secondary heuristics are applied after face detection
3. No "realism score" or "human likelihood score" is computed
4. Zero faces detected → reject as `NO_FACE_DETECTED`

#### Future Enhancement Path

A dedicated human vs non-human classifier could be added as an optional layer:

- Run after face detection (only if face detected)
- Use a trained image classification model
- Maintain human inclusivity as priority
- Require high confidence before rejection

This is **not currently implemented**.

---

## Stage Separation Guarantees

### Stage 1: Ghibli Transformation

**Responsibility**: Human portrait validation and Ghibli-style art generation

| Does | Does NOT |
|------|----------|
| Validates face existence | Handle QR code logic |
| Counts detected faces | Apply composition rules |
| Rejects multiple prominent faces | Use Stage 2 prompts |
| Calls qwen/image-edit model | Download/decode Stage 2 inputs |
| Returns Ghibli-style image URL | Re-validate its own output |

**Validation Flow** (Layer 3A):

```
Input Image URL
    │
    ▼
Source Resolution (Layer 1)
    │ ✗ → INVALID_IMAGE_URL / IMAGE_DOWNLOAD_FAILED
    ▼
Image Decoding (Layer 2)
    │ ✗ → IMAGE_DECODE_FAILED
    ▼
Face Detection (Layer 3A)
    │ ✗ (detector failure) → FACE_DETECTOR_FAILURE (SYSTEM_ERROR)
    │ ✗ (0 faces) → NO_FACE_DETECTED
    │ ✗ (multiple prominent) → MULTIPLE_FACES
    ▼
ACCEPT → Proceed to Ghibli generation
```

### Stage 2: QR Composition

**Responsibility**: Compose Ghibli image with QR lock screen

| Does | Does NOT |
|------|----------|
| Receives Stage 1 output as trusted | Re-run face detection |
| Validates Stage 1 URL is well-formed | Download/decode Stage 1 output |
| Generates QR code locally | Apply human validation |
| Calls seedream/4.5-edit model | Reject based on image content |
| Composes final image | Modify Stage 1 output |

**Trust Model**:

Stage 2 **trusts Stage 1 output completely**. The only validation is:
- Stage 1 URL is a valid HTTPS URL
- URL is not localhost/private IP

No image downloading, decoding, or face re-validation occurs in Stage 2.

### Why This Separation Matters

1. **Single Responsibility**: Each stage has one clear job
2. **No Redundant Work**: Face detection runs once, not twice
3. **Clear Error Attribution**: Errors are stage-scoped
4. **Predictable Behavior**: Same input → same output
5. **Testability**: Each stage can be tested independently

---

## Error Handling & Failure Isolation

### Core Principles

1. **Errors are stage-scoped**: Every error includes the pipeline stage where it occurred
2. **No silent fallbacks**: Every failure is explicitly reported
3. **Deterministic validation**: Same input always produces same validation result
4. **Immediate exit**: Failures exit immediately without partial processing
5. **No stale state**: Each request is isolated

### Error Classification

| Type | Description | HTTP Status |
|------|-------------|-------------|
| `VALIDATION_ERROR` | Input failed validation rules | 422 |
| `EXTERNAL_ERROR` | External API failure (KIE.ai) | 500/504 |
| `SYSTEM_ERROR` | Internal system failure | 500 |

### Error Stages

| Stage | Scope | Example Errors |
|-------|-------|----------------|
| `INPUT` | Schema validation, URL format | `SINGLE_IMAGE_REQUIRED` |
| `SOURCE_RESOLUTION` | URL reachability, download | `INVALID_IMAGE_URL`, `IMAGE_DOWNLOAD_FAILED` |
| `STAGE1_GHIBLI` | Face validation, Ghibli generation | `NO_FACE_DETECTED`, `STAGE1_API_ERROR` |
| `STAGE2_QR` | QR composition | `STAGE2_API_ERROR`, `STAGE2_TIMEOUT` |
| `ORCHESTRATION` | Pipeline coordination | `INTERNAL_ERROR` |

### Failure Isolation

```
Source Resolution Failure
    └── Returns immediately with SOURCE_RESOLUTION error
    └── Does NOT proceed to image decoding
    └── Does NOT call any external API

Face Detection Failure (detector crash)
    └── Returns FACE_DETECTOR_FAILURE (SYSTEM_ERROR)
    └── Does NOT return VALIDATION_ERROR
    └── Does NOT attempt fallback detection

Stage 1 API Failure
    └── Returns STAGE1_API_ERROR or STAGE1_TIMEOUT
    └── Does NOT proceed to Stage 2
    └── Does NOT leave orphaned tasks

Stage 2 API Failure
    └── Returns STAGE2_API_ERROR or STAGE2_TIMEOUT
    └── Does NOT retry Stage 1
    └── Clean task cleanup
```

### Error Response Format

All errors follow the unified V1 API envelope:

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

---

## File & Responsibility Mapping

### `validation_service.py`

**Location**: `src/ghibli_portrait/services/validation_service.py`

**Responsibilities**:

| Function | Layer | Purpose |
|----------|-------|---------|
| `validate_single_image_url_list()` | 0 | Ensures exactly one URL in list |
| `validate_url_format()` | 1 | URL format, localhost rejection |
| `_download_image()` | 1 | HTTP download with timeout |
| `_decode_image()` | 2 | PIL decode, format validation |
| `_detect_faces()` | 3A | MediaPipe face detection |
| `validate_stage1_human_portrait()` | 3A | Full Stage 1 validation |
| `validate_stage2_input()` | 3B | Stage 2 URL trust validation |
| `validate_real_human_image()` | 1+2+3A | Combined validation entry point |
| `validate_human_face()` | Legacy | Backward-compatible wrapper |

**Key Data Structures**:

```python
@dataclass
class ValidationResultV1:
    ok: bool
    code: str = ""
    message: str = ""
    error_type: ErrorType = ErrorType.VALIDATION_ERROR
    stage: ErrorStage = ErrorStage.INPUT

@dataclass
class FaceDetectionResult:
    ok: bool
    face_count: int = 0
    primary_face_area_ratio: float = 0.0
    faces: List[Dict] = None
    error: Optional[str] = None
```

### `routes.py`

**Location**: `src/ghibli_portrait/api/routes.py`

**Responsibilities**:

| Endpoint | Tag | Purpose |
|----------|-----|---------|
| `GET /v1/health` | Health & Utilities | Liveness probe |
| `GET /v1/qr-url/` | Health & Utilities | URL shortening |
| `POST /v1/ghibli` | Core APIs | Single-stage Ghibli transformation |
| `POST /v1/qr-lock` | Core APIs | QR code generation |
| `POST /v1/ghibli/callback` | Internal / System | Webhook handler |
| `DELETE /v1/qr-lock/{img_id}` | Internal / System | Image deletion |
| `POST /v1/ghibli-qr` | Api Production | Full pipeline |

**Orchestration Logic**:

```python
# Layer 4: Orchestration (routes.py)
#
# 1. Calls validation layers (does NOT add new rules)
# 2. Coordinates Stage 1 → Stage 2 flow
# 3. Formats responses using responses.py helpers
# 4. Handles webhook Future pattern
# 5. Manages task cleanup on failure
```

### `responses.py`

**Location**: `src/ghibli_portrait/api/responses.py`

**Responsibilities**:

| Function | Purpose |
|----------|---------|
| `success_response()` | Creates unified success envelope |
| `error_response()` | Creates unified error envelope |
| `validation_error_response()` | Single validation error helper |
| `external_error_response()` | External API error helper |
| `internal_error_response()` | System error helper |

**Unified Envelope**:

```python
class ApiSuccessResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None
    message: str
    errors: None = None
    timestamp: str

class ApiErrorResponse(BaseModel):
    success: bool = False
    data: None = None
    message: str
    errors: List[ApiError]
    timestamp: str
```

### `image_service.py`

**Location**: `src/ghibli_portrait/services/image_service.py`

**Responsibilities**:

| Function | Purpose |
|----------|---------|
| `generate_img()` | Builds and sends KIE.ai API request |

**Model-Aware Logic**:

```python
# Qwen models: single image, image_size parameter
if is_qwen_model:
    input_params = {
        "prompt": prompt,
        "image_url": img_urls[0],
        "image_size": QwenImageSize.from_aspect_ratio(aspect_ratio).value
    }
# Seedream models: multiple images, aspect_ratio, quality
else:
    input_params = {
        "prompt": prompt,
        "image_urls": img_urls,
        "aspect_ratio": aspect_ratio,
        "quality": quality
    }
```

### `qr_service.py`

**Location**: `src/ghibli_portrait/services/qr_service.py`

**Responsibilities**:

| Function | Purpose |
|----------|---------|
| `get_qr()` | Generates QR code with lock screen overlay |

**Process**:
1. Generate QR code from URL
2. Resize to configured dimensions
3. Overlay on lock screen template
4. Return PIL Image

### `schemas.py`

**Location**: `src/ghibli_portrait/models/schemas.py`

**Responsibilities**:

| Schema | Purpose |
|--------|---------|
| `Image2GhibliRequest` | `/v1/ghibli` request validation |
| `QRLockRequest` | `/v1/qr-lock` request validation |
| `GhibliQRRequest` | `/v1/ghibli-qr` request validation |
| `CallbackRequest` | Webhook payload parsing |
| `ApiSuccessResponse` | Success response envelope |
| `ApiErrorResponse` | Error response envelope |
| `ApiError` | Individual error object |
| `ErrorType`, `ErrorStage` | Error classification enums |

---

## Installation

### Prerequisites
```bash
Python 3.10+
uv package manager
```

### Setup

1. **Clone repository**
```bash
git clone https://github.com/cyber-ai-dep/ghibli_qr
cd ghibli_qr
```

2. **Install dependencies**
```bash
pip install uv
uv sync
```

Dependencies installed:
- `fastapi>=0.127.0` - Web framework
- `pillow>=12.0.0` - Image processing
- `python-dotenv>=1.2.1` - Environment config
- `qrcode>=8.2` - QR code generation
- `requests>=2.32.0` - HTTP client
- `mediapipe>=0.10.0` - Face detection
- `uvicorn>=0.40.0` - ASGI server

3. **Create directories**
```bash
mkdir -p src/static/tmp
```

4. **Configure environment**
```bash
cp .env.example .env
```

Edit `.env`:
```env
DOMAIN=https://your-ngrok-url.ngrok-free.dev
KIE_API_KEY=your_api_key_here
KIE_GHIBLI_MODEL=qwen/image-edit
KIE_COMPOSE_MODEL=seedream/4.5-edit
REQUIRE_HUMAN_FACE=true
MAX_FACES=1
SHORT_CODE_LENGTH=8
```

---

## Configuration

### Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `DOMAIN` | Yes | Public URL for webhooks | - |
| `KIE_API_KEY` | Yes | KIE.ai API key | - |
| `KIE_GHIBLI_MODEL` | Yes | Stage 1 model | - |
| `KIE_COMPOSE_MODEL` | Yes | Stage 2 model | - |
| `REQUIRE_HUMAN_FACE` | No | Enable face validation | `true` |
| `MAX_FACES` | No | Max faces (0=unlimited) | `1` |
| `SHORT_CODE_LENGTH` | No | URL shortener length | `8` |

### Model Configuration

#### Stage 1: Qwen Image-Edit
```env
KIE_GHIBLI_MODEL=qwen/image-edit
```
- **Speed**: 5-15 seconds
- **Input**: Single image (`image_url`)
- **Parameters**: `image_size`, `prompt`
- **Use case**: Ghibli-style transformation

#### Stage 2: Seedream 4.5-Edit
```env
KIE_COMPOSE_MODEL=seedream/4.5-edit
```
- **Speed**: 20-40 seconds
- **Input**: Multiple images (`image_urls`)
- **Parameters**: `aspect_ratio`, `quality`, `prompt`
- **Use case**: QR + Ghibli composition

**Important**: No fallback logic exists. If a model is not configured, the API returns an error.

### Ngrok Setup (Localhost)

1. Install Ngrok:
```bash
sudo snap install ngrok
```

2. Start tunnel:
```bash
ngrok http 8000
```

3. Copy URL and update `.env`:
```env
DOMAIN=https://your-url.ngrok-free.dev
```

4. Restart server

---

## Code Structure

### Project Layout
```
ghibli_qr/
├── src/
│   ├── ghibli_portrait/
│   │   ├── api/
│   │   │   ├── routes.py              # V1 API endpoints
│   │   │   └── responses.py           # Response envelope helpers
│   │   ├── models/
│   │   │   ├── schemas.py             # Pydantic schemas
│   │   │   └── blaze_face_short_range.tflite  # MediaPipe model (auto-downloaded)
│   │   ├── services/
│   │   │   ├── image_service.py       # KIE.ai integration
│   │   │   ├── qr_service.py          # QR generation
│   │   │   └── validation_service.py  # Multi-layer validation
│   │   ├── utils/
│   │   │   ├── url_utils.py           # URL shortening
│   │   │   └── image_utils.py         # Image utilities
│   │   ├── config.py                  # Settings
│   │   └── main.py                    # FastAPI app
│   └── static/
│       ├── lock.png                   # Lock screen template
│       └── tmp/                       # Generated QR codes
├── .env                               # Environment config
├── pyproject.toml                     # Dependencies
├── README.md                          # User documentation
└── IMPLEMENTATION_GUIDE.md            # This file
```

---

## Key Features

### 1. Multi-Layer Validation

Validation is implemented as a strict layer pipeline:

```python
# Layer 0: Schema (Pydantic)
# Layer 1: Source Resolution
# Layer 2: Image Decoding
# Layer 3A: Stage 1 Human Portrait
# Layer 3B: Stage 2 Input Trust
# Layer 4: Orchestration
```

Each layer has a single responsibility and does NOT perform validation from other layers.

### 2. Webhook Processing

Async task handling with Future pattern:

```python
# Create task and wait
pending_tasks[task_id] = asyncio.Future()
result = await asyncio.wait_for(future, timeout=300)

# Webhook completes Future
future.set_result(callback_data)
```

### 3. URL Shortening

Deterministic hashing for consistent short codes:

```python
# Same URL always produces same code
short_code = hashlib.sha256(url.encode()).hexdigest()[:8]
```

### 4. Face Detection with MediaPipe

CPU-only face detection with auto-downloaded model:

```python
# Configure and run detector
base_options = BaseOptions(model_asset_path=model_path)
options = FaceDetectorOptions(
    base_options=base_options,
    min_detection_confidence=0.35
)

with FaceDetector.create_from_options(options) as detector:
    detection_result = detector.detect(mp_image)
```

---

## API Reference

### Unified Response Format

All V1 endpoints return this structure:

**Success**:
```json
{
  "success": true,
  "data": { ... },
  "message": "Human-readable success message",
  "errors": null,
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

**Error**:
```json
{
  "success": false,
  "data": null,
  "message": "High-level error summary",
  "errors": [
    {
      "code": "SCREAMING_SNAKE_CASE",
      "type": "VALIDATION_ERROR",
      "stage": "STAGE1_GHIBLI",
      "field": "imgUrl",
      "message": "Human-readable error message"
    }
  ],
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

### POST /v1/ghibli

Transform image to Ghibli style.

**Request:**
```json
{
  "imgUrls": ["https://example.com/photo.jpg"],
  "prompt": "Convert to Ghibli style",
  "quality": "basic",
  "aspectRatio": "1:1"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "resultUrls": ["https://tempfile.aiquickdraw.com/result.png"],
    "costTime": 12,
    "model": "qwen/image-edit",
    "quality": "basic",
    "aspectRatio": "1:1"
  },
  "message": "Ghibli portrait generated successfully",
  "errors": null,
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

### POST /v1/qr-lock

Generate QR code with lock screen.

**Request:**
```json
{
  "url": "https://example.com",
  "shortenUrl": true,
  "version": 1
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "qrUrl": "https://domain.com/tmp/uuid.png",
    "encodedUrl": "https://example.com",
    "shortUrl": {
      "url": "https://domain.com/s/abc123",
      "code": "abc123"
    }
  },
  "message": "QR code with lock screen generated successfully",
  "errors": null,
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

### POST /v1/ghibli-qr

**Primary Production Endpoint**: Full Ghibli + QR pipeline.

**Request:**
```json
{
  "imgUrl": "https://example.com/photo.jpg",
  "url": "https://profile.com"
}
```

**Process:**
1. Validate input image (Layers 1, 2, 3A)
2. Transform to Ghibli (Stage 1, webhook wait)
3. Generate QR lock (local, parallel with Stage 1)
4. Compose images (Stage 2, webhook wait)

**Response:**
```json
{
  "success": true,
  "data": {
    "resultUrls": ["https://tempfile.aiquickdraw.com/final.png"],
    "costTime": 45,
    "model": "seedream/4.5-edit",
    "quality": "basic",
    "aspectRatio": "1:1"
  },
  "message": "Ghibli + QR pipeline completed successfully",
  "errors": null,
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

### GET /v1/qr-url

Get shortened URL using deterministic hashing.

**Request:**
```
GET /v1/qr-url/?url=https://very-long-url.com
```

**Response:**
```json
{
  "success": true,
  "data": {
    "url": "https://domain.com/s/abc123",
    "code": "abc123"
  },
  "message": "Short URL generated successfully",
  "errors": null,
  "timestamp": "2026-01-27T12:00:00.000Z"
}
```

---

## Deployment

### Local Development

```bash
# Terminal 1: Start Ngrok
ngrok http 8000

# Terminal 2: Update .env with Ngrok URL
nano .env

# Terminal 3: Start server
uv run uvicorn src.ghibli_portrait.main:app --reload --host 0.0.0.0 --port 8000
```

### Production Deployment

#### Docker
```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY . .
RUN pip install uv && uv sync
CMD ["uv", "run", "uvicorn", "src.ghibli_portrait.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t ghibli-qr .
docker run -p 8000:8000 --env-file .env ghibli-qr
```

#### Cloud Platforms

1. **Set environment variables** with production domain and API keys
2. **Ensure DOMAIN is publicly accessible** for webhook callbacks
3. **Configure monitoring** for error tracking

---

## Troubleshooting

### Webhook Timeout

**Error:** `WEBHOOK_TIMEOUT` or `STAGE1_TIMEOUT`

**Causes:**
- Ngrok not running
- Wrong DOMAIN in .env
- KIE.ai cannot reach callback URL

**Solutions:**
1. Verify Ngrok is running: `curl https://your-ngrok-url.ngrok-free.dev/v1/health`
2. Check `.env` DOMAIN matches Ngrok URL exactly
3. Restart server after changing DOMAIN
4. Monitor Ngrok dashboard at `http://127.0.0.1:4040`

### Face Detection Errors

**Error:** `NO_FACE_DETECTED`

**Causes:**
- Image does not contain a human face
- Face is obscured, too small, or at extreme angle
- Image is an animal, cartoon, or illustration

**Note:** This is expected behavior. The system only accepts images with detectable human faces.

**Error:** `FACE_DETECTOR_FAILURE`

**Causes:**
- MediaPipe model download failed
- MediaPipe runtime error

**Solutions:**
1. Check network connectivity for model download
2. Verify `src/ghibli_portrait/models/` directory is writable
3. Check server logs for detailed error

### Multiple Faces Rejection

**Error:** `MULTIPLE_FACES`

**Cause:** Image contains multiple prominent human faces (secondary face is ≥65% area AND ≥60% confidence of primary)

**Solution:** Use a single-person portrait image

### Module Not Found

**Error:** `ModuleNotFoundError: No module named 'mediapipe'`

**Solution:**
```bash
uv sync
```

### Directory Not Found

**Error:** `Directory '/path/to/tmp' does not exist`

**Solution:**
```bash
mkdir -p src/static/tmp
```

---

## Performance

### Response Times

| Endpoint | Expected Time |
|----------|---------------|
| `/v1/health` | <100ms |
| `/v1/qr-lock` | <1s |
| `/v1/ghibli` | 5-15s |
| `/v1/ghibli-qr` | 25-55s |

### Concurrent Requests

FastAPI handles concurrent requests asynchronously:
- Multiple clients can wait for webhooks simultaneously
- Each task has unique Future in `pending_tasks` dict
- No blocking between requests

---

## Security Considerations

1. **API Key Protection**
   - Never commit `.env` to git
   - Use environment variables in production
   - Rotate keys regularly

2. **Input Validation**
   - All inputs validated by Pydantic
   - URLs validated for format and reachability
   - Localhost/private IPs rejected

3. **Error Information**
   - Errors provide enough detail for debugging
   - No internal stack traces exposed to clients
   - Stage-scoped errors for attribution

---

## Summary

The Ghibli Portrait API provides:

- **MediaPipe BlazeFace** face detection (CPU-only, no fallback)
- **Multi-layer validation** with strict separation of concerns
- **Two-stage pipeline** with explicit trust boundaries
- **Webhook-based async processing** for optimal performance
- **Unified V1 API** with consistent response envelope
- **Human-inclusive validation** prioritizing no false rejections

### Design Principles

1. **Single Source of Truth**: MediaPipe is the only face detector
2. **Explicit > Implicit**: All behavior is documented and deterministic
3. **Fail Fast**: Errors exit immediately with clear attribution
4. **Trust Boundaries**: Stage 1 output is trusted by Stage 2
5. **Human Inclusivity**: Accept edge cases rather than reject real humans

For questions or issues, refer to the README.md or check server logs.
