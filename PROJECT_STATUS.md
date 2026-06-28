# Project Status & Deployment Notes

**Date:** 2026-06-28

Production API: portrait → Studio Ghibli illustration holding a scannable QR-code
lock. Generation backend is **BytePlus ARK (Seedream)**, synchronous (no webhook,
no ngrok). The previous KIE.ai async/webhook integration has been fully removed.

---

## Current Pipeline Flow (`POST /v1/ghibli-qr`)

```
1. Validate     URL (no localhost/private IP) → download → decode (PIL)
2. Stage 1A     MediaPipe face detection + synthetic-image (NOT_REAL_PHOTO) check
3. Skin tone    extract exact hex (YCbCr) → inject into Stage 1 prompt
4. Stage 1      portrait → Ghibli  (BytePlus ARK)  → re-host result locally
5. Stage 2      Ghibli + QR-lock   (BytePlus ARK)  → re-host final locally
6. QR check     verify the QR is scannable (QReader/pyzbar)
7. Response     unified envelope { success, data{ resultUrls, model, costTime,
                quality, aspectRatio, qrValidation }, message, errors, timestamp }
```

The ARK call is synchronous; its result is delivered to the awaiting request via
the in-process `pending_tasks` Future. `image_service.generate_img` is the ARK
adapter that preserves the original request/response/orchestration contract.

---

## What's Done

- ✅ KIE.ai backend replaced by BytePlus ARK as a drop-in layer (no API contract change).
- ✅ KIE-specific code/config/webhook removed (`/v1/ghibli/callback` deleted).
- ✅ `lock.png` lives at `src/static/lock.png`; Docker paths aligned to the code.
- ✅ Optional local saving of final images (`SAVE_OUTPUT_LOCAL` + `OUTPUT_DIR`).
- ✅ No ngrok required (synchronous generation, internal result delivery).
- ✅ Unit test suite in `tests/`; manual scripts in `tests/manual/`.
- ✅ Docs updated to the ARK system (README, QUICK_SETUP, docs/usage, docs/dev).

---

## VPS Deployment Checklist

- [ ] `.env` present with `DOMAIN` (this server's reachable address) and `ARK_API_KEY`.
- [ ] `src/static/lock.png` exists as a real PNG (Stage 2 overlay).
- [ ] Firewall allows inbound on `HOST_PORT` (default 30820).
- [ ] First Docker build has internet (base image, deps, MediaPipe + QR models).
- [ ] `docker-compose up -d --build` → `curl http://<host>:30820/v1/health` is healthy.
- [ ] `GENERATION_CONCURRENCY_LIMIT` ≤ 10 (ARK per-model concurrency ceiling).

---

## Known Considerations

- **Single worker** (`--workers 1`): `pending_tasks` is in-memory. Horizontal scaling
  needs a shared store (e.g. Redis).
- **Download size**: input images are fetched without a hard byte cap — keep this in
  mind for untrusted input on small VPS instances.
- **Synthetic detection** is heuristic; some stylized inputs (e.g. certain game
  screenshots) may pass `NOT_REAL_PHOTO`.
- **Result URLs** use `DOMAIN`; set it to the address clients can actually reach.
