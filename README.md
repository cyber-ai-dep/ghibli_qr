# Ghibli Portrait API 🎨

Transform photos into Ghibli-style portraits holding personalized QR code locks.

## ✨ What It Does

<div align="center">
  <img src="./docs/imgs/pexels-italo-melo-881954-2379004.jpg" width="200">
  <span style="font-size:28px">→</span>
  <img src="./docs/imgs/a0b12df9-23d8-406e-b45e-6cfdfe9c9a58_0.png" width="200">
  <span style="font-size:28px">→</span>
  <img src="./docs/imgs/a0b130ba-d7b4-494c-9cac-0e8e06cd0ac2_0.png" width="200">
</div>

## 📦 Installation

### Prerequisites
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (Fast Python package installer)

### Setup

1. **Clone the repository**
```bash
git clone https://github.com/cyber-ai-dep/ghibli_qr
cd ghibli_qr
```

2. **Install dependencies with uv**
```bash
pip install uv # ignore if uv is installed
uv venv
uv sync
```

3. **Configure environment**
```bash
cp .env.example .env
# Edit .env with your configuration
```

Required environment variables:
- `KIE_API_KEY` - Your KIE API key
- `KIE_IMG_MODEL` - Model name (default: seedream/4.5-edit)
- `DOMAIN` - Your domain URL

4. **Run the server**
```bash
uv run uvicorn src.ghibli_portrait.main:app --reload
```

Server will start at `http://localhost:8000`

View API docs at `http://localhost:8000/docs`

## 🚀 Quick Start

### Automated Pipeline (One Request)

```bash
POST /ghibli-qr
```

```json
{
  "img_url": "https://example.com/photo.jpg",
  "url": "https://your-profile.com"
}
```

**Response in ~90-120 seconds:**
```json
{
  "code": 200,
  "data": {
    "result_urls": ["https://cdn.example.com/final-portrait.png"],
    "cost_time": 97,
    "model": "seedream/4.5-edit"
  }
}
```

### Generate QR Code with Optional URL Shortening

```bash
POST /qr-lock
```

**With URL shortening:**
```json
{
  "url": "https://your-very-long-url.com/path/to/resource",
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
    "qr_url": "https://cdn.example.com/tmp/uuid.png",
    "encoded_url": "https://your-very-long-url.com/path/to/resource",
    "short_url": {
      "url": "https://your-domain.com/s/abc123",
      "code": "abc123"
    }
  }
}
```

### Get Shortened URL

```bash
GET /qr-url/?url=https://your-long-url.com
```

**Response:**
```json
{
  "code": 200,
  "message": "Short URL is retrieved successfully",
  "data": {
    "url": "https://your-domain.com/s/abc123",
    "code": "abc123"
  }
}
```

> **Note:** The `/qr-url` endpoint uses deterministic hashing, so the same URL will always produce the same short code, regardless of whether it was previously shortened.

## 📋 Available Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Service health check |
| `/ghibli` | POST | Transform image to Ghibli style |
| `/qr-lock` | POST | Generate QR code with lock screen (with optional URL shortening) |
| `/qr-url` | GET | Get or recover shortened URL using deterministic hashing |
| `/ghibli-qr` | POST | Full automated pipeline |
| `/qr-lock/{img_id}` | DELETE | Delete temporary QR image |

## 📖 Documentation

See [usage.md](./docs/usage.md) for detailed API documentation and examples.

## 🎨 Features

- **Ghibli-style transformation** powered by KIE Image Model
- **QR code generation** with custom lock screen design
- **URL shortening** with deterministic hashing for consistent short codes
- **Automated pipeline** for one-step processing
- **Quality options**: Basic (2K) or High (4K)
- **Multiple aspect ratios**: 1:1, 4:3, 16:9, and more

## ⚙️ Tech Stack

- **FastAPI** - Modern async Python web framework
- **Pydantic** - Data validation
- **External KIE API** - AI image generation
- **Async webhooks** - Real-time task completion