# Ghibli Portrait API V1

Production-ready API for transforming portraits to Ghibli-style art with QR code generation. Built with FastAPI, unified response format, and comprehensive validation.

## What It Does

Transforms regular portrait photos into artistic Ghibli-style images and generates QR codes with lock screen designs. Features automated two-stage pipeline for seamless Ghibli + QR composition.

## Key Features

- ✅ **Unified V1 API** - All endpoints under `/v1/*` prefix
- ✅ **Consistent Response Format** - `{success, message, requestId, data/errors}` for all endpoints
- ✅ **camelCase API Surface** - Clean, JavaScript-friendly field naming
- ✅ **Comprehensive Validation** - Multi-layer validation gate with structured error codes
- ✅ **Explicit Model Selection** - `qwen/image-edit` and `seedream` with no fallbacks
- ✅ **Request ID Tracking** - Unique identifier for every request
- ✅ **Webhook-Based Async Processing** - Efficient task completion handling

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
MIN_FACE_AREA_RATIO=0.03
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

## Unified Response Format

All V1 endpoints return responses in this format:

### Success Response
```json
{
  "success": true,
  "message": "Human-readable success message",
  "requestId": "req_550e8400-e29b-41d4-a716-446655440000",
  "data": {
    // Response payload (varies by endpoint)
  }
}
```

### Error Response
```json
{
  "success": false,
  "message": "High-level error summary",
  "requestId": "req_550e8400-e29b-41d4-a716-446655440000",
  "errors": [
    {
      "code": "MACHINE_READABLE_CODE",
      "field": "fieldName",
      "message": "Human-readable error message"
    }
  ]
}
```

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
  "message": "Ghibli Portrait API V1 is running",
  "requestId": "req_abc123",
  "data": {
    "status": "healthy",
    "timestamp": "2026-01-19T12:00:00Z"
  }
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
  "message": "Ghibli portrait generated successfully",
  "requestId": "req_550e8400",
  "data": {
    "resultUrls": ["https://tempfile.aiquickdraw.com/ghibli.png"],
    "model": "qwen/image-edit",
    "costTime": 12,
    "quality": "basic",
    "aspectRatio": "1:1"
  }
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
  "message": "QR code with lock screen generated successfully",
  "requestId": "req_abc123",
  "data": {
    "qrUrl": "https://your-domain.com/tmp/uuid.png",
    "encodedUrl": "https://example.com",
    "shortUrl": {
      "url": "https://your-domain.com/s/abc123",
      "code": "abc123"
    }
  }
}
```

### 4. Get Shortened URL

**Endpoint:** `GET /v1/qr-url?url=https://example.com`

**Response:**
```json
{
  "success": true,
  "message": "Short URL generated successfully",
  "requestId": "req_abc123",
  "data": {
    "url": "https://your-domain.com/s/abc123",
    "code": "abc123"
  }
}
```

### 5. Delete QR Image

**Endpoint:** `DELETE /v1/qr-lock/{imgId}`

**Response:**
```json
{
  "success": true,
  "message": "Image deleted successfully",
  "requestId": "req_abc123",
  "data": {
    "deletedId": "uuid-here"
  }
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
  "message": "Ghibli + QR pipeline completed successfully",
  "requestId": "req_abc123",
  "data": {
    "resultUrls": ["https://tempfile.aiquickdraw.com/final.png"],
    "model": "seedream/4.5-edit",
    "costTime": 45,
    "quality": "basic",
    "aspectRatio": "1:1"
  }
}
```

**Processing Time:** 15-30 seconds for the full pipeline

## Validation Policy

The V1 API enforces strict image quality requirements for Ghibli endpoints:

1. **Public URL Check** ✅ Implemented
   - URL must start with `http://` or `https://`
   - No localhost or private IPs allowed
   - Must be publicly accessible

2. **Single Image Requirement** ✅ Implemented
   - Only one image URL allowed per request
   - Enforced at validation layer

3. **Face Detection** ✅ Implemented
   - At least one human face must be detected (OpenCV Haar cascade)
   - Maximum 1 face by default (configurable via `MAX_FACES`)
   - Minimum face size ratio enforced (configurable via `MIN_FACE_AREA_RATIO`)

4. **Human vs. Animal/Cartoon** 🚧 ML Stub (Ready for Integration)
   - Prevents animal photos and cartoon/anime characters
   - Stub implementation with detailed TODO for ML model integration
   - Error code: `ANIMAL_OR_CARTOON_DETECTED`

5. **Real Photo vs. Non-Real Image** 🚧 ML Stub (Ready for Integration)
   - Ensures input is a real photograph (not AI-generated, painting, or 3D render)
   - Stub implementation with detailed TODO for ML model integration
   - Error code: `NON_REAL_IMAGE_DETECTED`

> **Note:** ML-based validation layers (4 & 5) are implemented as stubs with clear integration points. They currently pass all images but are ready for ML model integration when required. See `src/ghibli_portrait/services/validation_service.py` for implementation details.

## Error Codes Reference

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `SINGLE_IMAGE_REQUIRED` | 422 | Request must contain exactly one image URL |
| `INVALID_IMAGE_URL` | 422 | URL is not publicly accessible or invalid format |
| `NO_FACE_DETECTED` | 422 | No human face found in the image |
| `MULTIPLE_FACES` | 422 | More than the allowed number of faces detected |
| `FACE_TOO_SMALL` | 422 | Face is too small/distant in the image |
| `IMAGE_DOWNLOAD_FAILED` | 422 | Failed to download or decode the image |
| `ANIMAL_OR_CARTOON_DETECTED` | 422 | Image contains animal or cartoon (not real human) |
| `NON_REAL_IMAGE_DETECTED` | 422 | Image is not a real photograph |
| `GENERATION_API_ERROR` | 500 | External AI API returned an error |
| `GENERATION_TASK_FAILED` | 500 | AI generation task failed on external service |
| `STAGE1_API_ERROR` | 500 | Stage 1 (Ghibli) API error |
| `STAGE1_TASK_FAILED` | 500 | Stage 1 (Ghibli) task failed |
| `STAGE1_TIMEOUT` | 504 | Stage 1 exceeded 5-minute timeout |
| `STAGE2_API_ERROR` | 500 | Stage 2 (composition) API error |
| `STAGE2_TASK_FAILED` | 500 | Stage 2 (composition) task failed |
| `STAGE2_TIMEOUT` | 504 | Stage 2 exceeded 5-minute timeout |
| `WEBHOOK_TIMEOUT` | 504 | Processing exceeded 5-minute timeout |
| `IMAGE_NOT_FOUND` | 404 | QR image not found for deletion |
| `INTERNAL_ERROR` | 500 | Unexpected server error |

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
| `MIN_FACE_AREA_RATIO` | No | Minimum face size ratio | `0.03` |
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

## Tech Stack

- **FastAPI** - Modern async web framework
- **Pydantic** - Data validation with camelCase support
- **Pillow** - Image processing for QR generation
- **OpenCV** - Face detection validation
- **KIE.ai API** - AI image generation (qwen + seedream)
- **Webhooks** - Async task completion flow
- **Python 3.10+** - Runtime environment

## Development

### Project Structure

```
src/ghibli_portrait/
├── api/
│   ├── routes.py           # V1 API endpoints (unified, /v1/*)
│   └── responses.py        # Unified response helpers
├── models/
│   └── schemas.py          # Request/response schemas (camelCase)
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

## Documentation

- **OpenAPI/Swagger**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/openapi.json

## License

See LICENSE file for details.

## Support

For issues, questions, or feature requests, please open an issue on GitHub.
