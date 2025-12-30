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

**Response:**
```json
{
  "code": 200,
  "data": {
    "status": "healthy",
    "timestamp": "2025-12-28T12:00:00"
  },
  "message": "Service is running"
}
```

### Ghibli Portrait Generation
```
POST /ghibli
```
Transforms images to Ghibli-style art using external KIE API (model: `seedream/4.5-edit`).

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
```json
{
  "code": 200,
  "data": {
    "result_urls": ["https://example.com/generated.jpg"],
    "cost_time": 45,
    "model": "seedream/4.5-edit",
    "quality": "basic",
    "aspect_ratio": "1:1"
  },
  "message": "Task completed successfully"
}
```

**Notes:**
- Async operation - waits for KIE API callback
- Uses webhook at `/ghibli/callback` (internal)
- Default timeout: 300 seconds (5 minutes)

### Ghibli Webhook (Internal)
```
POST /ghibli/callback
```
Receives callbacks from KIE API. **Not for direct use.**

### QR Code Generation
```
POST /qr-lock
```
Generates QR code embedded in lock screen image with optional URL shortening.

**Request:**
```json
{
  "url": "https://example.com",
  "version": 1,  // Optional: 1-40, auto-determined if null
  "shorten_url": true  // Optional: if true, URL is shortened before encoding in QR
}
```

**Response:**
```json
{
  "code": 200,
  "data": {
    "qr_url": "https://your-domain.com/tmp/{uuid}.png",
    "encoded_url": "https://example.com",
    "short_url": {  // Present only if shorten_url was true
      "url": "https://your-domain.com/s/{code}",
      "code": "{short_code}"
    }
  },
  "message": "QR/Lock image created successfully."
}
```

**Notes:**
- When `shorten_url` is true, the QR code encodes the shortened URL instead of the original
- URL shortening uses deterministic hashing - same URL always produces same short code

### Delete QR Image
```
DELETE /qr-lock/{img_id}
```
Deletes generated QR code image by ID (with or without .png extension).

**Response:**
```json
{
  "code": 200,
  "data": {
    "deleted_id": "{uuid}"
  },
  "message": "Image {uuid} deleted successfully"
}
```

### Get Shortened URL
```
GET /qr-url/?url={url}
```
Returns a shortened URL for any given URL using deterministic hashing.

**Request:**
```
GET /qr-url/?url=https://example.com/very/long/path
```

**Response:**
```json
{
  "code": 200,
  "data": {
    "url": "https://your-domain.com/s/{code}",
    "code": "{short_code}"
  },
  "message": "Short URL is retrieved successfully"
}
```

**Important Notes:**
- **No validation performed**: This endpoint always returns a short code, even for invalid or non-existent URLs
- **Deterministic**: The same URL will always produce the same short code
- **Idempotent**: Can be called multiple times for the same URL without side effects
- **No persistence check**: Returns short code regardless of whether the URL was previously shortened

### Automated Ghibli + QR Pipeline
```
POST /ghibli-qr
```
Fully automated pipeline that:
1. Transforms input image to Ghibli style
2. Generates QR code with lock screen overlay
3. Combines Ghibli image with QR lock screen
4. Returns final composite image URL

**Request:**
```json
{
  "img_url": "https://example.com/photo.jpg",
  "url": "https://example.com/destination"
}
```

**Response:**
```json
{
  "code": 200,
  "data": {
    "result_urls": ["https://example.com/final-ghibli-qr.jpg"],
    "cost_time": 95,
    "model": "seedream/4.5-edit",
    "quality": "basic",
    "aspect_ratio": "1:1"
  },
  "message": "Task completed successfully"
}
```

**Notes:**
- Executes two image generation tasks sequentially
- Total cost_time includes both transformations
- Uses predefined prompts from settings (PROMPT_PIC_TO_GHIBLI, PROMPT_GHIBLI_LOCK)
- Automatically cleans up intermediate QR lock image reference


## Configuration

Environment variables (`.env`):
```
KIE_API_KEY=your_api_key
DOMAIN=https://your-domain.com
KIE_IMG_MODEL=seedream/4.5-edit
SHORT_CODE_LENGTH=8
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
   - Returns final result or timeout error (300s default)

2. **QR Code Storage:**
   - Images saved to `src/static/tmp/`
   - Publicly accessible via server
   - Manual cleanup via DELETE endpoint

3. **URL Shortening:**
   - Deterministic hashing: same URL always produces same short code
   - No validation or persistence checks performed
   - Works for any URL (valid or invalid, existing or non-existing)
   - Optional integration with QR code generation

4. **Lock Screen Overlay:**
   - Base lock image: `src/static/lock.png`
   - QR code embedded at calculated position
   - Output: PNG with transparency

5. **Automated Pipeline (`/ghibli-qr`):**
   - Step 1: Transform input image to Ghibli style
   - Step 2: Generate QR lock screen image
   - Step 3: Combine Ghibli image with QR lock overlay
   - Sequential task execution with separate callbacks
   - Aggregates total processing time from both transformations
   - Returns final composite image URL

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