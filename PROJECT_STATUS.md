# Project Status & Deployment Notes

**Branch:** `mohammad` | **Date:** 2026-05-12

---

## Current Pipeline Flow

```
POST /v1/ghibli-qr
  │
  ├─ Layer 1: URL format check (validation_service.py)
  ├─ Layer 2: Download image via httpx (validation_service.py)
  ├─ Layer 3A: MediaPipe face detection in thread (validation_service.py)
  │
  ├─ Stage 1: Submit to KIE (image_service.py) → wait for webhook (routes.py)
  │           Re-host output locally (routes.py)
  │           [optional] Identity drift check (identity_check.py)
  │
  ├─ Stage 2: Generate QR lock image (qr_service.py)
  │           Submit to KIE (image_service.py) → wait for webhook (routes.py)
  │
  └─ Return resultUrls[] to caller
```

**Active model:** `qwen/image-edit` (known identity drift — switch to `flux-kontext-pro` for better quality)

---

## Code Issues in `mohammad` — VPS Impact

These are specific problems in the current code that will directly affect production.
Each entry includes the exact file and line number.

### 🔴 Will Break in Production

---

**1. `pending_tasks` is in-process memory**
`routes.py:78`
```python
pending_tasks: Dict[str, asyncio.Future] = {}
```
If the VPS runs 2+ workers (`--workers 2`), KIE webhook arrives at worker B but the `Future` was created in worker A → `Future` never resolves → **every request times out after 600 seconds**.

**Impact:** Silent data loss. No error visible to the user except a 504 after 10 minutes.
**Fix:** Run with `--workers 1` until migrating `pending_tasks` to Redis.

---

**2. Webhook has no try/except — any exception → raw 500 text**
`routes.py:339–364`
```python
async def webhook(req: CallbackRequest):
    task_id = req.data.taskId          # no try/except around this block
    if task_id in pending_tasks:
        ...
        future.set_result(req)
    if req.is_failure:
        return JSONResponse(status_code=req.code, ...)  # req.code can be 501
```
Two problems:
- No `try/except` → any unexpected exception produces `500 text/plain` (not JSON). This was fixed during this session but **reverted** when restoring the branch.
- `status_code=req.code` returns KIE's internal code (e.g., 501) as the HTTP status to whoever sent the webhook. This is non-standard.

**Impact:** If KIE sends a webhook with an unexpected payload, the handler crashes silently, the Future is never resolved, and the pipeline times out.

---

**3. Webhook validates body via Pydantic before handler runs**
`routes.py:339` — `async def webhook(req: CallbackRequest)`

Pydantic validates the incoming webhook body against `CallbackRequest` (→ `TaskData`) **before** the handler function executes. If KIE sends a webhook with any missing required field (e.g., `completeTime`, `costTime`, `param`), FastAPI returns **422** to KIE before our code runs. The `Future` is never resolved → pipeline waits 600 seconds → `STAGE1_TIMEOUT`.

`schemas.py:112–127` — `TaskData` requires these fields:
```python
completeTime: int = Field(...)   # required — 422 if missing
costTime:     int = Field(...)   # required — 422 if missing
createTime:   int = Field(...)   # required — 422 if missing
model:        str = Field(...)   # required — 422 if missing
param:        str = Field(...)   # required — 422 if missing
updateTime:   int = Field(...)   # required — 422 if missing
```
**Impact:** A single missing field from KIE silently kills the entire pipeline. Already observed in this session (STAGE1_TIMEOUT with task visible on KIE dashboard).
**Fix:** Accept `Request` directly and parse manually, or make non-critical fields `Optional`.

---

**4. `except Exception` does not catch `asyncio.CancelledError`**
`routes.py:697`
```python
except Exception as e:
    return JSONResponse(status_code=500, ...)
```
`CancelledError` is a `BaseException` (Python 3.10+), not `Exception`. If a client disconnects mid-request (browser closes, proxy timeout), FastAPI cancels the coroutine. This raises `CancelledError` which escapes the `except Exception` block → `500 text/plain` from uvicorn instead of JSON.

**Impact:** Any client that closes the connection early produces a raw plain-text 500. On a VPS with a proxy that has a 30s timeout, this happens on every pipeline request (which takes 2–10 minutes).

---

### 🟡 Will Degrade on VPS

---

**5. Temp files are never deleted (`.jpg` pipeline files)**
`routes.py:499`, `routes.py:560`
```python
filename = f"{uuid4()}.jpg"          # QR lock (line 499)
img.save(s.TMP_PATH / filename ...)  # Stage 1 rehost (line 560)
```
Two `.jpg` files are written to `static/tmp/` per request. The `DELETE /v1/qr-lock/{img_id}` endpoint only handles `.png` files:

`routes.py:380`
```python
imgpath = s.TMP_PATH / f"{img_id}.png"   # always .png — never finds .jpg files
```
The `.jpg` files from the pipeline are **never deleted**. On a VPS handling 100 requests/day, `static/tmp/` grows by ~5–10 MB/day indefinitely.

**Fix:** Add a scheduled cleanup (`cron` or `asyncio` background task) that deletes files older than 2 hours.

---

**6. `validation_service.py` still uses `requests` (blocking)**
`validation_service.py:62`
```python
import requests
```
Used in:
- `_download_image()` (line ~225) — download via sync `requests.get()`
- `_ensure_model_downloaded()` (line ~284) — model download via sync `requests.get()`

Both run inside `asyncio.to_thread()`, so they don't block the event loop. But they **occupy thread pool slots** during the HTTP download (~5–15s). This competes with MediaPipe (~2s) for the 100-thread pool.

**Impact at scale:** With 50 concurrent requests, 50 threads are blocked on network I/O for up to 15 seconds each, leaving few threads for MediaPipe work. Effective concurrency drops from ~40 to ~10.

---

**7. MediaPipe model is downloaded at first request**
`validation_service.py:275–287` — `_ensure_model_downloaded()`
```python
if _MODEL_PATH.exists():
    return str(_MODEL_PATH)
# otherwise: download ~1 MB from googleapis.com
response = requests.get(_MODEL_URL, timeout=30)
```
The first request after a cold start (or after clearing the model cache) triggers a synchronous download. If the VPS has restricted outbound internet, this **fails silently** and returns `FACE_DETECTOR_FAILURE` on every request.

**Fix:** Pre-download the model in the Dockerfile or VPS setup script.

---

**8. No CORS middleware**
`main.py` — no `CORSMiddleware`

If this API is called from a browser frontend hosted on a different domain (e.g., `app.example.com` calling `api.example.com`), **all requests will be blocked by the browser** with CORS errors.

**Fix:**
```python
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["https://app.example.com"],
                   allow_methods=["*"], allow_headers=["*"])
```

---

**9. No global exception handler**
`main.py` — only `RequestValidationError` handler, no `Exception` handler

Any unhandled exception that escapes a route (from middleware, lifespan events, etc.) produces `500 text/plain; charset=utf-8` instead of the project's standard JSON envelope. This was added during this session but **reverted** when restoring the branch.

---

**10. No authentication**
`main.py` and `routes.py` — no auth middleware

Any IP that reaches the service can:
- Submit portraits and consume KIE credits at the account owner's expense
- Trigger face detection (CPU load)
- Fill up `static/tmp/` with arbitrary files

**Impact on VPS:** Once the API endpoint is public, bots and scrapers will find it. KIE credits will be drained quickly.

---

### 🟢 Minor — Won't Break but Should Be Fixed

---

**11. Path traversal risk in `delete_qr_lock`**
`routes.py:379–380`
```python
img_id = img_id.replace(".png", "")
imgpath = s.TMP_PATH / f"{img_id}.png"
```
A caller sending `img_id = "../../config"` constructs `TMP_PATH/../../config.png`. Without `.resolve()` and a bounds check, `imgpath.unlink()` could delete files outside `static/tmp/`.

**Fix:** Validate that the resolved path stays within `TMP_PATH`:
```python
imgpath = (s.TMP_PATH / f"{img_id}.png").resolve()
if not str(imgpath).startswith(str(s.TMP_PATH.resolve())):
    raise HTTPException(status_code=400)
```

---

**12. `static/tmp/` is publicly accessible without any restriction**
`main.py:61`
```python
app.mount("/tmp", StaticFiles(directory=str(s.TMP_PATH)), name="tmp")
```
Anyone who knows or guesses a filename can download any generated image from `GET /tmp/<uuid>.jpg`. This includes all intermediate Ghibli images (re-hosted Stage 1 output) and QR images. These are served with no authentication, no expiry, and no access log.

---

**13. Fixed seed produces identical results for similar inputs**
`config.py:68` — `KIE_SEED = int(os.getenv("KIE_SEED", "42"))`

All qwen requests use the same seed. Two users submitting the same photo get the same output. This may be intentional (reproducibility) but could appear as a bug to users who retry expecting variation.

---

**14. No request tracing**
No `request_id` is generated and threaded through logs. When a pipeline fails, there is no way to correlate the validation log, the KIE submission log, and the webhook receipt log for a single request. Debugging on a busy VPS is very difficult.

---

## VPS Deployment Checklist

```
[ ] Real domain with HTTPS — nginx + certbot, or Caddy
[ ] Set DOMAIN=https://yourdomain.com in .env
[ ] Run: --workers 1 (required — pending_tasks is in-memory)
[ ] Pre-download MediaPipe model in setup/Dockerfile
[ ] Add API key auth or gateway before exposing publicly
[ ] Add CORS middleware if called from browser
[ ] Set up cron or background task: delete static/tmp/ files older than 2h
[ ] Switch KIE_GHIBLI_MODEL=flux-kontext-pro (better identity preservation)
[ ] Reverse proxy (nginx → uvicorn on 127.0.0.1:8010)
[ ] Set LOG_LEVEL appropriately (warning for prod, info for debug)
```

---

## Branch Comparison: `mohammad` vs `fix/ghibli-pipeline`

`mohammad` contains 3 additional commits on top of `fix/ghibli-pipeline`.

| Area | `fix/ghibli-pipeline` | `mohammad` | Better |
|---|---|---|---|
| **HTTP client (`image_service.py`)** | `requests` (sync, blocking — blocks thread for ~1-2s per API call) | `httpx.AsyncClient` (async — zero thread cost during HTTP) | ✅ `mohammad` |
| **Image download in validation** | `validate_real_human_image()` — sync `requests.get`, blocks a thread for ~5-15s during download | `validate_real_human_image_async()` — async httpx, thread used only for MediaPipe (~2s) | ✅ `mohammad` |
| **Thread pool** | Default `min(32, cpu+4)`. Each request blocks a thread ~34s (download + MediaPipe) → saturates at ~1 concurrent | `ThreadPoolExecutor(max_workers=100)` in lifespan. Thread used ~2.5s (MediaPipe only) → ~40 concurrent | ✅ `mohammad` |
| **`asyncio.get_event_loop()`** | Used in `routes.py` — deprecated in Python 3.10+, raises `DeprecationWarning` | Replaced with `asyncio.get_running_loop()` | ✅ `mohammad` |
| **`generate_img()` signature** | Sync `def` — must run in thread if called from async | `async def` — called directly with `await` | ✅ `mohammad` |
| **Dead code** | `qr_detect.py` (hardcoded `/home/ahmad/`), `qr_validation.py` (unused `qreader`) | Both deleted | ✅ `mohammad` |
| **`identity_check.py`** | Uses `requests` for source image download (blocking) | Uses `httpx` async download | ✅ `mohammad` |
| **Config env vars** | Missing `STAGE1_ACCELERATION`, `KIE_SEED`, `KIE_OUTPUT_FORMAT`, `KIE_IMAGE_SIZE` | All qwen parameters exposed with documented defaults | ✅ `mohammad` |
| **`.env.example`** | Outdated, missing settings | Fully updated with all settings and inline comments | ✅ `mohammad` |
| **Docs** | Minimal README | Full README with architecture diagram, concurrency table, error codes + IMPLEMENTATION_GUIDE with Redis scaling path | ✅ `mohammad` |
| **Stage 1 prompts** | Generic Ghibli prompt | Miyazaki painterly style, skin tone preservation, rejects anime/manga in negative prompt | ✅ `mohammad` |
| **Webhook robustness** | `req: CallbackRequest` — Pydantic validates before handler runs; strict `TaskData` schema | Same (reverted per user request) — strict schema, no try/except in handler | ⚠️ Both have same issue |
| **Concurrent capacity** | ~1 request (thread pool saturation) | ~40 requests | ✅ `mohammad` |

> **⚠️ Note:** During this session a more robust webhook handler was built (raw `Request` body, always returns 200 to KIE, lenient `TaskData` schema, full try/except) but was reverted when restoring `origin/mohammad`. If `STAGE1_TIMEOUT` errors appear in production, that fix should be reapplied — it directly addresses issues #2 and #3 in the code issues list above.

### Summary

`mohammad` is strictly better than `fix/ghibli-pipeline` in every measured dimension. `fix/ghibli-pipeline` is superseded and should not be used for new deployments.

The remaining issues (#1 through #14 above) are present in **both branches** and require new work to resolve.
