# Ghibli Portrait API - Developer Documentation

FastAPI service for Ghibli-style image transformation and QR code generation with lock screen overlay.

## Quick Start

```bash
# Install dependencies
uv sync

# Set up environment variables
cp .env.example .env
# Edit .env with your KIE_API_KEY and other settings

# Run the server
uvicorn src.ghibli_portrait.main:app --reload
```

## API Endpoints

### Health Check
```
GET /health
```
Returns service liveness status.

### Ghibli Portrait Generation
```
POST /ghibli
```
Transforms images to Ghibli-style art using external KIE API.

**Request:**
```json
{
  "img_urls": ["https://example.com/image.jpg"],
  "prompt": "Convert this image to Ghibli style art.",
  "quality": "basic",  // "basic" (2K) or "high" (4K)
  "aspect_ratio": "1:1"  // Options: 1:1, 4:3, 3:4, 16:9, 9:16, 2:3, 3:2, 21:9
}
```

**Response:**
Returns `CallbackRequest` with task results after webhook completes.

**Notes:**
- Async operation - waits for KIE API callback
- Uses webhook at `/ghibli/callback` (internal)
- Timeout: configurable via settings

### Ghibli Webhook (Internal)
```
POST /ghibli/callback
```
Receives callbacks from KIE API. **Not for direct use.**

### QR Code Generation
```
POST /qr-lock
```
Generates QR code embedded in lock screen image.

**Request:**
```json
{
  "url": "https://example.com",
  "version": 1  // Optional: 1-40, auto-determined if null
}
```

**Response:**
```json
{
  "url": "https://your-domain.com/tmp/{uuid}.png"
}
```

### Delete QR Image
```
DELETE /qr-lock/{img_id}
```
Deletes generated QR code image by ID.


## Configuration

Environment variables (`.env`):
```
KIE_API_KEY=your_api_key
DOMAIN=https://your-domain.com
KIE_IMG_MODEL=seedream/4.5-edit
```

## Development

**Commit Messages:**
Project uses `gipt` for AI-generated commits with gitmoji style.

**Dependencies:**
- FastAPI + Uvicorn
- python-dotenv
- httpx (for external API calls)
- Pillow (for image processing)
- qrcode (for QR generation)


## Key Behaviors

1. **Async Ghibli Processing:**
   - POST `/ghibli` initiates task with KIE API
   - Server waits for callback at `/ghibli/callback`
   - Tracks pending tasks in memory
   - Returns final result or timeout error

2. **QR Code Storage:**
   - Images saved to `src/static/tmp/`
   - Publicly accessible via server
   - Manual cleanup via DELETE endpoint

3. **Lock Screen Overlay:**
   - Base lock image: `src/static/lock.png`
   - QR code embedded at calculated position
   - Output: PNG with transparency

## Troubleshooting

**Callback timeout:**
- Check `KIE_API_KEY` is valid
- Verify `DOMAIN` is publicly accessible
- Review webhook endpoint logs

**QR generation fails:**
- Ensure `src/static/lock.png` exists
- Check write permissions for `tmp/` directory
- Verify URL length vs QR version

## API Documentation

Interactive docs available when server is running:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`