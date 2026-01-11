# Ghibli Portrait API

Transform photos into Ghibli-style portraits holding personalized QR code locks using AI-powered image generation.

## What It Does

Converts regular photos into artistic Ghibli-style images and generates QR codes embedded in lock screen designs, with automated pipeline support for seamless processing.

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
KIE_IMG_MODEL=qwen/image-edit
SHORT_CODE_LENGTH=8
```

**Supported Models:**
- `qwen/image-edit` - Fast semantic editing (5-10s, single image)
- `seedream/4.5-edit` - High quality 4K generation (40-60s, multiple images)

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

## Quick Start

## Image Hosting for Input URLs

All image URLs provided to the API **must be publicly accessible**.

### Recommended Free Image Hosting (Example)

You can use **ImgBB** to upload images and obtain a direct image URL.

Website:
https://imgbb.com/

### How to Use ImgBB

1. Open https://imgbb.com/
2. Upload your image
3. After upload, copy the **direct image URL** (must end with `.jpg`, `.png`, etc.)
4. Use this URL as input for the API

### Example

```json
{
  "img_urls": [
    "https://i.ibb.co/2JKZ4fC/example-image.png"
  ],
  "prompt": "Convert this image to Ghibli style"
}




### Transform Image to Ghibli Style

```bash
POST /ghibli
```

**Request:**
```json
{
  "img_urls": ["https://example.com/photo.jpg"],
  "prompt": "Convert to Ghibli style art",
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
    "model": "qwen/image-edit",
    "quality": "basic",
    "aspect_ratio": "1:1"
  }
}
```

### Generate QR Code with Lock Screen

```bash
POST /qr-lock
```

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
  "message": "QR/Lock image created successfully.",
  "data": {
    "qr_url": "https://your-domain.com/tmp/uuid.png",
    "encoded_url": "https://example.com",
    "short_url": {
      "url": "https://your-domain.com/s/abc123",
      "code": "abc123"
    }
  }
}
```

### Automated Pipeline

```bash
POST /ghibli-qr
```

**Request:**
```json
{
  "img_url": "https://example.com/photo.jpg",
  "url": "https://your-profile.com"
}
```

**Process:**
1. Transforms image to Ghibli style
2. Generates QR code with lock screen
3. Combines both images (via webhook)

**Response time:** 15-30 seconds with Qwen model

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Service health check |
| `/ghibli` | POST | Transform image to Ghibli style via webhook |
| `/qr-lock` | POST | Generate QR code with lock screen |
| `/qr-url` | GET | Get shortened URL (deterministic hashing) |
| `/ghibli-qr` | POST | Automated Ghibli + QR pipeline |
| `/qr-lock/{img_id}` | DELETE | Delete temporary QR image |
| `/ghibli/callback` | POST | Webhook callback (internal use only) |

## Model Comparison

| Feature | Qwen image-edit | Seedream 4.5-edit |
|---------|-----------------|-------------------|
| Input | `image_url` (single) | `image_urls` (array) |
| Parameters | `image_size` | `aspect_ratio`, `quality` |
| Processing Time | 5-10 seconds | 40-60 seconds |
| Multiple Images | No | Yes |
| Quality | Fixed | 2K/4K options |

## Configuration

### Environment Variables

- `DOMAIN` - Public URL for webhooks (use Ngrok for localhost)
- `KIE_API_KEY` - KIE.ai API authentication token
- `KIE_IMG_MODEL` - AI model selection (`qwen/image-edit` or `seedream/4.5-edit`)
- `SHORT_CODE_LENGTH` - URL shortener code length (default: 8)

### Quality Options

- `basic` - 2K resolution (faster)
- `high` - 4K resolution (slower, Seedream only)

### Aspect Ratios

- `1:1` - Square
- `4:3` - Standard landscape
- `3:4` - Standard portrait
- `16:9` - Widescreen
- `9:16` - Vertical video
- `2:3`, `3:2`, `21:9` - Additional options

## Deployment

### Production Deployment

1. **Deploy to cloud platform** (Railway, Render, Fly.io)
2. **Set environment variables** with production domain
3. **Ensure DOMAIN is publicly accessible** for webhooks
4. **Monitor webhook endpoint** for KIE.ai callbacks

### Docker Deployment

```bash
docker build -t ghibli-qr .
docker run -p 8000:8000 --env-file .env ghibli-qr
```

## Features

- AI-powered Ghibli-style image transformation
- QR code generation with custom lock screen overlay
- URL shortening with deterministic hashing
- Webhook-based async processing
- Multi-model support (Qwen/Seedream)
- Automated pipeline for end-to-end processing
- FastAPI with automatic OpenAPI documentation

## Tech Stack

- **FastAPI** - Async web framework
- **Pydantic** - Data validation
- **Pillow** - Image processing
- **KIE.ai API** - AI image generation
- **Webhooks** - Async task completion
- **Requests** - HTTP client
- **Python 3.10+** - Runtime

## Documentation

- [Usage Guide](./docs/usage.md) - Detailed API examples
- [Developer Docs](./docs/dev.md) - Technical documentation

## License

See LICENSE file for details.