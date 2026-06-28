# Ghibli Portrait API — Usage Guide

Create a Ghibli-style portrait of a person holding a personalized QR-code lock.

All endpoints are prefixed with `/v1` and return the **unified response envelope**:

```json
{ "success": true, "data": { }, "message": "...", "errors": null, "timestamp": "..." }
```
On error: `success:false`, `data:null`, and `errors:[{code,type,stage,field,message}]`.

Image generation runs on **BytePlus ARK (Seedream)** — synchronous (no webhook, no
ngrok). `DOMAIN` is only used to build the returned image URLs.

---

## Health Check

`GET /v1/health`
```json
{ "success": true, "data": { "status": "healthy" }, "message": "Ghibli Portrait API V1 is running", "errors": null, "timestamp": "..." }
```

---

## Primary: Automated Pipeline

One request → Ghibli restyle + QR-lock composition.

`POST /v1/ghibli-qr`

**Request:**
```json
{ "imgUrl": "https://example.com/person.jpg", "url": "https://your-profile.com" }
```

**Response:**
```json
{
  "success": true,
  "data": {
    "resultUrls": ["http://<host>/tmp/final_....jpg"],
    "model": "seedream/4.5-edit",
    "costTime": 62,
    "quality": "basic",
    "aspectRatio": "1:1",
    "qrValidation": {
      "ok": true,
      "expectedPayload": "https://your-profile.com",
      "detectedPayload": "https://your-profile.com",
      "reason": "decoded on inverted pass"
    }
  },
  "message": "Ghibli + QR pipeline completed successfully",
  "errors": null,
  "timestamp": "..."
}
```

The input must be a **real single-person portrait**. Validation rejects it (HTTP 422)
when there is no face, multiple faces, or a synthetic/3D/cartoon image.

---

## Stage 1 only: Portrait → Ghibli

`POST /v1/ghibli`

**Request** (camelCase; `prompt`/`quality`/`aspectRatio` optional):
```json
{ "imgUrls": ["https://example.com/person.jpg"], "quality": "basic", "aspectRatio": "1:1" }
```

**Response** `data`: `{ resultUrls, model, costTime, quality, aspectRatio }`.

---

## QR Lock image

`POST /v1/qr-lock`

**Request:**
```json
{ "url": "https://google.com", "version": null, "shortenUrl": false }
```

**Response:**
```json
{
  "success": true,
  "data": {
    "qrUrl": "http://<host>/tmp/<uuid>.png",
    "encodedUrl": "https://google.com",
    "shortUrl": { "url": "http://<host>/<code>", "code": "abc12345" }
  },
  "message": "QR code with lock screen generated successfully",
  "errors": null,
  "timestamp": "..."
}
```
`shortUrl` is present only when `shortenUrl: true`.

---

## URL Shortener (deterministic)

`GET /v1/qr-url/?url=<url>` → same URL always yields the same code.
```json
{ "success": true, "data": { "url": "http://<host>/<code>", "code": "abc12345" }, "message": "...", "errors": null, "timestamp": "..." }
```

---

## Delete a temporary QR image

`DELETE /v1/qr-lock/{imgId}`
```json
{ "success": true, "data": { "deletedId": "<uuid>" }, "message": "Image deleted successfully", "errors": null, "timestamp": "..." }
```

---

## Error Codes

| Code | HTTP | Meaning |
|---|---|---|
| `INVALID_IMAGE_URL` | 422 | URL not public / malformed (localhost & private IPs rejected) |
| `IMAGE_DOWNLOAD_FAILED` | 422 | Could not download the image |
| `NO_FACE_DETECTED` | 422 | No human face |
| `MULTIPLE_FACES` | 422 | More than one prominent face |
| `NOT_REAL_PHOTO` | 422 | 3D render / cartoon, not a real photo |
| `FACE_DETECTOR_FAILURE` | 500 | Face detector runtime error |
| `STAGE1_API_ERROR` / `STAGE2_API_ERROR` | 500 | Generation provider rejected the request |
| `STAGE1_TIMEOUT` / `STAGE2_TIMEOUT` | 504 | Stage timed out |
| `INTERNAL_ERROR` | 500 | Unexpected server error |

Error body example:
```json
{
  "success": false, "data": null, "message": "Request validation failed",
  "errors": [{ "code": "MULTIPLE_FACES", "type": "VALIDATION_ERROR", "stage": "STAGE1_GHIBLI", "field": "imgUrl", "message": "..." }],
  "timestamp": "..."
}
```

---

## Customization

- **Quality:** `basic` (2K, recommended) · `high` (4K)
- **Aspect ratio:** `1:1`, `4:3`, `3:4`, `16:9`, `9:16`, `2:3`, `3:2`, `21:9`
- **Local copy:** set `SAVE_OUTPUT_LOCAL=true` to also save every final image under `OUTPUT_DIR`.

---

## Tips

1. Use `/v1/ghibli-qr` for the full flow in a single call.
2. Use a real, reasonably-sized portrait — heavily compressed images can trip `NOT_REAL_PHOTO`.
3. Enable `shortenUrl: true` for long URLs → simpler, more scannable QR codes.
4. Each generation takes ~40–60s; the response includes the QR scannability check (`qrValidation`).
```
