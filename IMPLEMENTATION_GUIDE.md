# Implementation Guide - Ghibli Portrait API

Complete technical documentation for the Ghibli Portrait API with Qwen model integration.

## Table of Contents
- [Overview](#overview)
- [Architecture](#architecture)
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
- QR code generation with custom lock screen
- URL shortening with deterministic hashing
- Automated pipeline processing
- Multi-model support (Qwen/Seedream)

---

## Architecture

### System Flow

```
Client Request → FastAPI Server → KIE.ai API (creates task)
                      ↓
            Waits for webhook callback
                      ↓
KIE.ai Processing → Webhook Callback → Server
                      ↓
            Returns result to client
```

### Webhook vs Polling

The system uses **webhooks** for optimal performance:

**Webhook Benefits:**
- Real-time notifications
- No polling overhead
- Instant result delivery
- Resource efficient

**Requirements:**
- Public domain URL (use Ngrok for localhost)
- `/ghibli/callback` endpoint accessible to KIE.ai

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
- `uuid>=1.30` - UUID generation
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
KIE_IMG_MODEL=qwen/image-edit
SHORT_CODE_LENGTH=8
```

---

## Configuration

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `DOMAIN` | Public URL for webhooks | `https://abc.ngrok-free.dev` |
| `KIE_API_KEY` | KIE.ai API key | `d01cff285e432f75...` |
| `KIE_IMG_MODEL` | AI model selection | `qwen/image-edit` |
| `SHORT_CODE_LENGTH` | URL shortener length | `8` |

### Model Selection

#### Qwen Image-Edit
```env
KIE_IMG_MODEL=qwen/image-edit
```
- **Speed**: 5-10 seconds
- **Input**: Single image (`image_url`)
- **Parameters**: `image_size`, `prompt`
- **Use case**: Fast style transfer

#### Seedream 4.5-Edit
```env
KIE_IMG_MODEL=seedream/4.5-edit
```
- **Speed**: 40-60 seconds
- **Input**: Multiple images (`image_urls`)
- **Parameters**: `aspect_ratio`, `quality`, `prompt`
- **Use case**: High-quality 4K generation

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
│   │   │   ├── routes.py          # API endpoints
│   │   │   └── responses.py       # Response models
│   │   ├── models/
│   │   │   └── schemas.py         # Pydantic schemas
│   │   ├── services/
│   │   │   ├── image_service.py   # KIE.ai integration
│   │   │   └── qr_service.py      # QR generation
│   │   ├── utils/
│   │   │   └── url_utils.py       # URL shortening
│   │   ├── config.py              # Settings
│   │   └── main.py                # FastAPI app
│   └── static/
│       ├── lock.png               # Lock screen template
│       └── tmp/                   # Generated QR codes
├── .env                           # Environment config
├── pyproject.toml                 # Dependencies
└── README.md                      # Documentation
```

### Key Files

#### `src/ghibli_portrait/models/schemas.py`

**QwenImageSize Enum** (Lines 28-50):
```python
class QwenImageSize(str, Enum):
    SQUARE = "square"
    LANDSCAPE_4_3 = "landscape_4_3"
    PORTRAIT_4_3 = "portrait_4_3"
    # ... more options

    @staticmethod
    def from_aspect_ratio(aspect_ratio: AspectRatio) -> QwenImageSize:
        mapping = {
            AspectRatio._1_1: QwenImageSize.SQUARE,
            # ... mappings
        }
        return mapping.get(aspect_ratio, QwenImageSize.SQUARE)
```

#### `src/ghibli_portrait/services/image_service.py`

**Dual Model Support** (Lines 19-47):
```python
is_qwen_model = s.KIE_IMG_MODEL.startswith("qwen/")

if is_qwen_model:
    # Qwen: single image, image_size
    input_params = {
        "prompt": prompt,
        "image_url": img_urls[0],
        "image_size": QwenImageSize.from_aspect_ratio(aspect_ratio).value
    }
else:
    # Seedream: multiple images, aspect_ratio, quality
    input_params = {
        "prompt": prompt,
        "image_urls": img_urls,
        "aspect_ratio": aspect_ratio,
        "quality": quality
    }
```

#### `src/ghibli_portrait/api/routes.py`

**Webhook Handler** (Lines 95-145):
```python
@router.post("/ghibli")
async def transform2ghibli(request: Image2GhibliRequest):
    # Create task with KIE.ai
    res = generate_img(**request.model_dump())
    task_id = res["data"]["taskId"]

    # Wait for webhook callback
    future = asyncio.get_event_loop().create_future()
    pending_tasks[task_id] = future

    webhook_result = await asyncio.wait_for(future, timeout=300)

    # Return result
    return ImageGenerationResponse(...)
```

**Webhook Callback** (Lines 148-173):
```python
@router.post("/ghibli/callback")
async def webhook(req: CallbackRequest):
    task_id = req.data.taskId
    if task_id in pending_tasks:
        future = pending_tasks[task_id]
        pending_tasks.pop(task_id)
        if not future.done():
            future.set_result(req)
    return req
```

---

## Key Features

### 1. Multi-Model Support

Automatically detects and adapts to different AI models:

```python
# Qwen parameters
{
  "image_url": "single_url",
  "image_size": "square",
  "prompt": "text"
}

# Seedream parameters
{
  "image_urls": ["url1", "url2"],
  "aspect_ratio": "1:1",
  "quality": "basic",
  "prompt": "text"
}
```

### 2. Webhook Processing

Async task handling with Future pattern:

```python
# Create task
pending_tasks[task_id] = asyncio.Future()

# Wait for webhook
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

### 4. Parameter Compatibility

Handles model-specific parameters gracefully:

```python
# Works with both models
quality = params.get("quality", "basic")
aspect_ratio = params.get("aspect_ratio", "1:1")
```

---

## API Reference

### POST /ghibli

Transform image to Ghibli style.

**Request:**
```json
{
  "img_urls": ["https://example.com/photo.jpg"],
  "prompt": "Convert to Ghibli style",
  "quality": "basic",
  "aspect_ratio": "1:1"
}
```

**Response:**
```json
{
  "code": 200,
  "message": "Task completed successfully",
  "data": {
    "result_urls": ["https://tempfile.aiquickdraw.com/result.png"],
    "cost_time": 5,
    "model": "qwen/image-edit"
  }
}
```

**Processing Time:**
- Qwen: 5-10 seconds
- Seedream: 40-60 seconds

### POST /qr-lock

Generate QR code with lock screen.

**Request:**
```json
{
  "url": "https://example.com",
  "shorten_url": true,
  "version": 1
}
```

**Response:**
```json
{
  "code": 200,
  "data": {
    "qr_url": "https://domain.com/tmp/uuid.png",
    "short_url": {
      "url": "https://domain.com/s/abc123",
      "code": "abc123"
    }
  }
}
```

### POST /ghibli-qr

Automated pipeline: Ghibli transformation + QR generation.

**Request:**
```json
{
  "img_url": "https://example.com/photo.jpg",
  "url": "https://profile.com"
}
```

**Process:**
1. Transform to Ghibli (webhook 1)
2. Generate QR lock (local)
3. Merge images (webhook 2)

**Total Time:** 15-30 seconds (Qwen), 90-120 seconds (Seedream)

### GET /qr-url

Get shortened URL using deterministic hashing.

**Request:**
```
GET /qr-url/?url=https://very-long-url.com
```

**Response:**
```json
{
  "code": 200,
  "data": {
    "url": "https://domain.com/s/abc123",
    "code": "abc123"
  }
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

#### Railway
```bash
railway login
railway init
railway up
```

Set environment variables in Railway dashboard.

#### Render
1. Connect GitHub repo
2. Set build command: `uv sync`
3. Set start command: `uv run uvicorn src.ghibli_portrait.main:app --host 0.0.0.0 --port $PORT`
4. Add environment variables

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

---

## Troubleshooting

### Webhook Timeout

**Error:** `Webhook timeout for task {task_id}`

**Causes:**
- Ngrok not running
- Wrong DOMAIN in .env
- KIE.ai cannot reach callback URL

**Solutions:**
1. Verify Ngrok is running: `curl https://your-ngrok-url.ngrok-free.dev/health`
2. Check `.env` DOMAIN matches Ngrok URL
3. Restart server after changing DOMAIN
4. Monitor Ngrok dashboard at `http://127.0.0.1:4040`

### Parameter Mismatch

**Error:** `KeyError: 'quality'` or `KeyError: 'aspect_ratio'`

**Cause:** Qwen model doesn't have `quality`/`aspect_ratio` in response

**Solution:** Already fixed in routes.py lines 82-83, 289-290 with `.get()` defaults

### Module Not Found

**Error:** `ModuleNotFoundError: No module named 'requests'`

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

### Task Failed

**Error:** `Task failed: {error_message}`

**Causes:**
- Invalid image URL
- Unsupported image format
- API quota exceeded

**Solutions:**
1. Verify image URL is publicly accessible
2. Check image format (JPEG, PNG supported)
3. Verify KIE_API_KEY has sufficient credits

---

## Performance Optimization

### Response Times

| Endpoint | Qwen | Seedream |
|----------|------|----------|
| `/ghibli` | 5-10s | 40-60s |
| `/qr-lock` | <1s | <1s |
| `/ghibli-qr` | 15-30s | 90-120s |

### Caching Strategy

Consider implementing:
- Redis cache for identical requests
- CDN for result images
- Database for short URL mappings

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

2. **Webhook Validation**
   - Verify requests come from KIE.ai IP ranges
   - Implement request signing if available

3. **Rate Limiting**
   - Add rate limiting middleware
   - Monitor API usage

4. **Input Validation**
   - Pydantic validates all inputs
   - Sanitize URLs before processing

---

## Monitoring

### Health Check
```bash
curl http://localhost:8000/health
```

### Webhook Dashboard
Monitor incoming webhooks at Ngrok dashboard:
```
http://127.0.0.1:4040
```

### Logs
Server logs show:
- Request details
- Task IDs
- Webhook callbacks
- Errors

---

## Summary

The Ghibli Portrait API provides:

✅ Dual model support (Qwen/Seedream)
✅ Webhook-based async processing
✅ QR code generation with lock screen
✅ URL shortening with deterministic hashing
✅ Automated pipeline processing
✅ FastAPI with OpenAPI docs
✅ Production-ready deployment

For questions or issues, refer to the documentation or check server logs.
