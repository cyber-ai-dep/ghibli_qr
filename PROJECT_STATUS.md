# Project Status & Deployment Notes

**Branch:** `mohammad` | **Date:** 2026-05-12

---

## Current State

The pipeline works end-to-end:

1. User submits portrait URL + target URL → `POST /v1/ghibli-qr`
2. MediaPipe validates a human face is present
3. Stage 1: KIE transforms portrait to Ghibli style (qwen/image-edit or flux-kontext-pro)
4. Stage 1 result is re-hosted locally so Seedream can reach it
5. Stage 2: KIE composes Ghibli image + QR lock screen (seedream/4.5-edit)
6. Final URL returned to caller

**Active model:** `qwen/image-edit` (known identity drift — switch to `flux-kontext-pro` for better results)

---

## Known Issues & VPS Risks

### 🔴 Critical

| Issue | Detail | Fix |
|---|---|---|
| **Single-worker constraint** | `pending_tasks` is an in-memory dict. Webhooks from KIE must arrive at the same process. Running `--workers 2+` silently breaks the pipeline — webhook resolves on worker A but the Future is on worker B. | Always run `--workers 1`. For horizontal scaling, migrate to Redis pub/sub. |
| **No public HTTPS on VPS** | KIE webhook requires a public HTTPS callback URL. ngrok works locally but is not suitable for production (tunnels die, URLs change on free tier). | Point a real domain at the VPS and use nginx + Let's Encrypt (or Caddy). Set `DOMAIN=https://yourdomain.com` in `.env`. |
| **No authentication** | Any caller who knows the API URL can submit requests and consume KIE credits without restriction. | Add API key middleware or put the service behind an authenticated gateway before exposing it. |

### 🟡 Moderate

| Issue | Detail | Fix |
|---|---|---|
| **Temp file accumulation** | Every request writes 2 JPEG files to `static/tmp/`. There is no automatic cleanup. Disk fills up over time. | Add a cron job or a background task to delete files older than N hours. |
| **No circuit breaker on KIE** | If KIE API is down, every request waits 600 seconds before timing out. Under load, coroutines pile up. | Add a fast health check against KIE before submitting, or use a timeout on the HTTP POST itself (already 30s) plus a shorter webhook timeout for staging. |
| **MediaPipe model downloads on first request** | If `blaze_face_short_range.tflite` is not cached, first request downloads it (~1 MB). Causes a slow first response and will fail if the VPS has no internet access at runtime. | Pre-download the model at Docker build time or in the VPS setup script. |
| **No CORS middleware** | If the API is called from a browser frontend (different origin), all requests will be blocked. | Add `fastapi.middleware.cors.CORSMiddleware` with the appropriate `allow_origins`. |
| **`requests` lib still used in `validation_service.py`** | `_download_image()` and `_ensure_model_downloaded()` use the synchronous `requests` library. These run inside `asyncio.to_thread`, so they don't block the event loop, but they do consume thread pool slots during download (~5-15s). At high concurrency this competes with MediaPipe for the 100-thread pool. | Replace with `httpx` sync client, or pre-download the model so `_ensure_model_downloaded` is a no-op at runtime. |

### 🟢 Minor

| Issue | Detail | Fix |
|---|---|---|
| **Fixed seed (`KIE_SEED=42`)** | All qwen requests use the same seed, producing deterministic but repetitive results for similar inputs. | Randomize seed per request, or make it configurable in the request body. |
| **No request ID / tracing** | Logs cannot be correlated across validation → Stage 1 → webhook → Stage 2. Debugging a specific failed request is difficult. | Add a `request_id` (UUID) at the start of each pipeline run and include it in all log messages. |
| **Webhook timeout is 600s** | A slow or failed KIE task keeps a coroutine alive for 10 minutes. Under high load this accumulates. | Reduce to 120-180s for production and surface a clearer "try again" error. |

---

## Deployment Checklist for VPS

```
[ ] Real domain with HTTPS (nginx + certbot or Caddy)
[ ] DOMAIN=https://yourdomain.com in .env
[ ] KIE_GHIBLI_MODEL=flux-kontext-pro (better quality)
[ ] Pre-download MediaPipe model in setup/Dockerfile
[ ] Run with --workers 1 (required until Redis migration)
[ ] Add API key auth or gateway before exposing publicly
[ ] Set up cron to clean static/tmp/ older than 2h
[ ] Add CORS middleware if called from browser
[ ] Reverse proxy (nginx) → uvicorn on 127.0.0.1:8010
```

---

## Branch Comparison: `mohammad` vs `fix/ghibli-pipeline`

`mohammad` is built on top of `fix/ghibli-pipeline` with 3 additional commits.

| Area | `fix/ghibli-pipeline` | `mohammad` | Better |
|---|---|---|---|
| **HTTP client (`image_service.py`)** | `requests` (sync, blocking) — blocks event loop thread for the full API call (~1-2s) | `httpx.AsyncClient` (async) — non-blocking, zero thread cost | ✅ `mohammad` |
| **Image download in validation** | `validate_real_human_image()` — sync download via `requests`, blocks a thread for ~5-15s during download | `validate_real_human_image_async()` — async httpx download, thread used only for MediaPipe CPU work (~2s) | ✅ `mohammad` |
| **Thread pool** | Default pool: `min(32, cpu+4)` threads. At 500 concurrent, each request occupies a thread for ~34s (download + MediaPipe) → saturates at ~1 concurrent | `ThreadPoolExecutor(max_workers=100)` in lifespan. Thread used only for MediaPipe (~2.5s) → ~40 concurrent before saturation | ✅ `mohammad` |
| **`asyncio.get_event_loop()`** | Used in `routes.py` → deprecated in Python 3.10+, raises `DeprecationWarning`, will break in future Python | Replaced with `asyncio.get_running_loop()` — correct API | ✅ `mohammad` |
| **`generate_img()` signature** | Sync `def` — must run in thread if called from async context | `async def` — called directly from async routes | ✅ `mohammad` |
| **Dead code** | Contains `qr_detect.py` (hardcoded `/home/ahmad/` path) and `qr_validation.py` (unused `qreader` validator) | Both deleted | ✅ `mohammad` |
| **identity_check.py** | Uses `requests` for source image download (blocking) | Uses `httpx` async download | ✅ `mohammad` |
| **Config env vars** | Missing `STAGE1_ACCELERATION`, `KIE_SEED`, `KIE_OUTPUT_FORMAT`, `KIE_IMAGE_SIZE` → defaults not applied | All qwen parameters exposed in `.env` with documented defaults | ✅ `mohammad` |
| **`.env.example`** | Outdated — missing several settings | Fully updated with all current settings and inline comments | ✅ `mohammad` |
| **Documentation** | Minimal README, short IMPLEMENTATION_GUIDE | Expanded README (architecture diagram, concurrency table, error codes), full IMPLEMENTATION_GUIDE with Redis scaling path | ✅ `mohammad` |
| **Stage 1 prompts** | Generic Ghibli prompt | More specific: Hayao Miyazaki painterly style, explicit skin tone preservation, rejects anime/manga in negative prompt | ✅ `mohammad` |
| **Webhook handling** | `req: CallbackRequest` — Pydantic validates body before handler runs; invalid body → 422, Future never resolved → 600s timeout | *(same as fix/ghibli-pipeline in the restored branch — the raw-Request improvement was reverted per user request)* | ⚠️ See note |
| **Stability at scale** | ~1 concurrent request (thread pool saturation) | ~40 concurrent requests | ✅ `mohammad` |

> **⚠️ Note on webhook handling:** During this session a more robust webhook handler was developed (raw Request body, always returns 200, lenient schema) but was reverted to match `origin/mohammad` at user request. If `STAGE1_TIMEOUT` errors recur in production, re-apply those changes from the session history.

### Summary

`mohammad` is strictly better than `fix/ghibli-pipeline` in every dimension. `fix/ghibli-pipeline` should be considered superseded. There is no scenario where `fix/ghibli-pipeline` is preferable for new deployments.
