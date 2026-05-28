# Ghibli QR API — Overlay Architecture Test Results
**Date:** 2026-05-24  
**Endpoint:** POST /v1/ghibli-qr (full 2-stage pipeline + deterministic QR overlay)  
**Server:** http://localhost:8010  
**Total requests:** 37 concurrent  
**Wall time:** 134.8s (all 37 fired simultaneously)  
**QR URL encoded:** https://example.com/profile  
**Architecture:** QR overlay applied deterministically POST Stage 2 AI generation

---

## What Changed vs Previous Test (test_results_37.md)

| Metric | Before (test_results_37.md) | This run (overlay) |
|---|---|---|
| Architecture | AI paints QR (probabilistic) | Deterministic QR overlay post-AI |
| ✅ PASS | 19 (first run) / 26 (after tuning) | **26** |
| ❌ Rate-limit failures | 7 → 0 | **0** |
| ❌ Timeouts | 0 | **0** |
| ✅ QR integrity | Probabilistic (AI may blur/repaint) | **Deterministic (always readable)** |
| Wall time | 151.1s | **134.8s** |
| Avg successful duration | 87.6s | **89.4s** |

---

## Input Images (37 URLs)

| # | Category | URL |
|---|---|---|
| 01 | Real portrait | https://i.ibb.co/4ZHjcZLp/istockphoto-944986244-612x612.jpg |
| 02 | Real portrait | https://i.ibb.co/twRyFjVx/van.jpg |
| 03 | Real portrait | https://i.ibb.co/LhC1PGpM/istockphoto-1784543440-612x612.jpg |
| 04 | Real portrait | https://i.ibb.co/rGt7L47C/37403.jpg |
| 05 | White man | https://i.ibb.co/mCHT1Rb6/whiteman1.jpg |
| 06 | White man | https://i.ibb.co/2pmYnzb/whiteman2.jpg |
| 07 | White man | https://i.ibb.co/Xkrn6s7f/whiteman3.jpg |
| 08 | White woman | https://i.ibb.co/DHZN0Q5t/whitewoman1.jpg |
| 09 | White woman | https://i.ibb.co/RkDPv6jf/whitewoman2.jpg |
| 10 | White woman | https://i.ibb.co/S4XcXmcn/whitewoman3.png |
| 11 | Black woman | https://i.ibb.co/cKM1stSZ/blackwoman1.jpg |
| 12 | Black woman | https://i.ibb.co/BKSG8VGc/blackwoman2.jpg |
| 13 | Black woman | https://i.ibb.co/LXzSHJnd/blackwoman3.jpg |
| 14 | Black woman (dup) | https://i.ibb.co/LXzSHJnd/blackwoman3.jpg |
| 15 | Hijabi girl | https://i.ibb.co/CKmf6hn7/hijabigirl3.jpg |
| 16 | Hijabi girl | https://i.ibb.co/VcCbvTZb/hijabigirl4.jpg |
| 17 | Hijabi girl | https://i.ibb.co/xK4MvVPd/hijabigirl5.jpg |
| 18 | Hijabi girl | https://i.ibb.co/VcjRV3Rm/hijabigirl6.jpg |
| 19 | Minecraft (synthetic) | https://i.ibb.co/b5nGjTHh/minecraft1.jpg |
| 20 | Minecraft (synthetic) | https://i.ibb.co/zT7j7wM6/minecraft4.jpg |
| 21 | Minecraft dup | https://i.ibb.co/zT7j7wM6/minecraft4.jpg |
| 22 | Selfie man | https://i.ibb.co/vxDLh4RS/selfiman.jpg |
| 23 | Group (3 men) | https://i.ibb.co/7tXRgDyH/threemens.jpg |
| 24 | Group (3 women) | https://i.ibb.co/9HRCFNwq/threewoman.jpg |
| 25 | Group (3 girls) | https://i.ibb.co/tyDJhqH/threegirls.jpg |
| 26 | Night man | https://i.ibb.co/LX5J3gCy/nghtman.jpg |
| 27 | Masked man | https://i.ibb.co/r2F26p5C/maskman.jpg |
| 28 | Night man | https://i.ibb.co/gbsLPTKG/nightman.jpg |
| 29 | Masked man | https://i.ibb.co/kgbXdcdQ/maskman2.jpg |
| 30 | Night man | https://i.ibb.co/Fb34x2nd/nightman2.jpg |
| 31 | Night man | https://i.ibb.co/ywq4wSz/nightman3.jpg |
| 32 | Boy | https://i.ibb.co/ym8RdLzS/aboy.jpg |
| 33 | Baby boy | https://i.ibb.co/gsSL1JY/babyboy.jpg |
| 34 | Baby boy | https://i.ibb.co/v6XFM1zN/babyboy2.jpg |
| 35 | Baby girl | https://i.ibb.co/jP2dkmzq/babygirl.jpg |
| 36 | Baby girl | https://i.ibb.co/VWdY2g36/babygirl2.jpg |
| 37 | Baby girl | https://i.ibb.co/1JJ4Z8mP/babygirl3.jpg |

---

## Results by Request

| # | File | Duration | KIE Time | Result | Code | Notes |
|---|---|---|---|---|---|---|
| 01 | istockphoto-944986244-612x612.jpg | 74.7s | 70s | ✅ PASS | — | QR overlay applied |
| 02 | van.jpg | 77.3s | 71s | ✅ PASS | — | Previously rate-limited — now passes |
| 03 | istockphoto-1784543440-612x612.jpg | 107.9s | 101s | ✅ PASS | — | QR overlay applied |
| 04 | 37403.jpg | 113.8s | 93s | ✅ PASS | — | QR overlay applied |
| 05 | whiteman1.jpg | 65.5s | 59s | ✅ PASS | — | QR overlay applied |
| 06 | whiteman2.jpg | 134.8s | 109s | ✅ PASS | — | Slowest — QR overlay applied |
| 07 | whiteman3.jpg | 86.6s | 80s | ✅ PASS | — | QR overlay applied |
| 08 | whitewoman1.jpg | 112.4s | 105s | ✅ PASS | — | QR overlay applied |
| 09 | whitewoman2.jpg | 1.5s | — | ⚠️ REJECTED | MULTIPLE_FACES | Pre-existing: background face detected |
| 10 | whitewoman3.png | 134.5s | 108s | ✅ PASS | — | Previously rate-limited — now passes |
| 11 | blackwoman1.jpg | 1.9s | — | ⚠️ REJECTED | MULTIPLE_FACES | Pre-existing: background face detected |
| 12 | blackwoman2.jpg | 1.0s | — | ⚠️ REJECTED | MULTIPLE_FACES | Pre-existing: background face detected |
| 13 | blackwoman3.jpg | 100.4s | 95s | ✅ PASS | — | QR overlay applied |
| 14 | blackwoman3.jpg (dup) | 66.2s | 61s | ✅ PASS | — | QR overlay applied |
| 15 | hijabigirl3.jpg | 57.9s | 51s | ✅ PASS | — | QR overlay applied |
| 16 | hijabigirl4.jpg | 117.5s | 98s | ✅ PASS | — | QR overlay applied |
| 17 | hijabigirl5.jpg | 49.7s | 44s | ✅ PASS | — | QR overlay applied |
| 18 | hijabigirl6.jpg | 61.2s | 54s | ✅ PASS | — | Previously rate-limited — now passes |
| 19 | minecraft1.jpg | 1.1s | — | ✅ CORRECTLY REJECTED | NOT_REAL_PHOTO | Synthetic detection working |
| 20 | minecraft4.jpg | 1.5s | — | ✅ CORRECTLY REJECTED | NOT_REAL_PHOTO | Synthetic detection working |
| 21 | minecraft4.jpg (dup) | 1.4s | — | ✅ CORRECTLY REJECTED | NOT_REAL_PHOTO | Synthetic detection working |
| 22 | selfiman.jpg | 120.2s | 99s | ✅ PASS | — | Previously rate-limited — now passes |
| 23 | threemens.jpg | 1.5s | — | ✅ CORRECTLY REJECTED | MULTIPLE_FACES | Group photo blocked |
| 24 | threewoman.jpg | 1.4s | — | ✅ CORRECTLY REJECTED | MULTIPLE_FACES | Group photo blocked |
| 25 | threegirls.jpg | 1.0s | — | ✅ CORRECTLY REJECTED | MULTIPLE_FACES | Group photo blocked |
| 26 | nghtman.jpg | 100.7s | 95s | ✅ PASS | — | QR overlay applied |
| 27 | maskman.jpg | 40.5s | 33s | ✅ PASS | — | Fastest — QR overlay applied |
| 28 | nightman.jpg | 65.2s | 60s | ✅ PASS | — | QR overlay applied |
| 29 | maskman2.jpg | 1.4s | — | ⚠️ REJECTED | MULTIPLE_FACES | Pre-existing: may have 2 people |
| 30 | nightman2.jpg | 98.6s | 94s | ✅ PASS | — | Previously rate-limited — now passes |
| 31 | nightman3.jpg | 94.3s | 88s | ✅ PASS | — | QR overlay applied |
| 32 | aboy.jpg | 10.9s | — | ⚠️ REJECTED | NOT_REAL_PHOTO | Pre-existing: synthetic threshold too tight for this image |
| 33 | babyboy.jpg | 51.6s | 46s | ✅ PASS | — | Previously rate-limited Stage 2 — now passes |
| 34 | babyboy2.jpg | 125.6s | 104s | ✅ PASS | — | QR overlay applied |
| 35 | babygirl.jpg | 110.7s | 103s | ✅ PASS | — | QR overlay applied |
| 36 | babygirl2.jpg | 61.2s | 52s | ✅ PASS | — | QR overlay applied |
| 37 | babygirl3.jpg | 95.2s | 90s | ✅ PASS | — | QR overlay applied |

---

## Summary

| Metric | Value |
|---|---|
| Total requests | 37 |
| ✅ Successfully generated | 26 |
| ✅ Correctly rejected (designed) | 6 |
| ⚠️ Unexpected rejections (pre-existing) | 5 |
| ❌ Rate-limit failures | **0** (was 7) |
| ❌ Timeouts | **0** |
| ❌ QR validation failures | **0** |
| Wall time | 134.8s |
| Avg per successful request | 89.4s |
| Fastest successful | 40.5s (req 27 — maskman.jpg) |
| Slowest successful | 134.8s (req 06 — whiteman2.jpg) |

---

## Failure Breakdown

### 1. Correctly Rejected — 6 (validation working as designed)

| # | File | Reason |
|---|---|---|
| 19 | minecraft1.jpg | NOT_REAL_PHOTO — synthetic 3D render ✓ |
| 20 | minecraft4.jpg | NOT_REAL_PHOTO — synthetic 3D render ✓ |
| 21 | minecraft4.jpg (dup) | NOT_REAL_PHOTO — duplicate, same result ✓ |
| 23 | threemens.jpg | MULTIPLE_FACES — group of 3 men ✓ |
| 24 | threewoman.jpg | MULTIPLE_FACES — group of 3 women ✓ |
| 25 | threegirls.jpg | MULTIPLE_FACES — group of 3 girls ✓ |

### 2. Pre-existing Unexpected Rejections — 5 (unchanged from before, need investigation)

| # | File | Code | Issue |
|---|---|---|---|
| 09 | whitewoman2.jpg | MULTIPLE_FACES | Single-person photo — background face above 2% area threshold |
| 11 | blackwoman1.jpg | MULTIPLE_FACES | Single-person photo — background face or second person |
| 12 | blackwoman2.jpg | MULTIPLE_FACES | Single-person photo — background face or second person |
| 29 | maskman2.jpg | MULTIPLE_FACES | May actually have 2 people, or reflected/poster face |
| 32 | aboy.jpg | NOT_REAL_PHOTO | Real boy flagged as synthetic — threshold still too tight for this image |

---

## QR Overlay Architecture — Key Facts

**Pipeline per successful request:**
```
stage1_*.jpg (Ghibli output, 2h TTL)
qrlock_*.jpg (QR lock reference image, 2h TTL)
stage2_*.jpg (AI composition output, 2h TTL — intermediate)
final_*.jpg  (deterministic QR overlay, 24h TTL — client deliverable)
```

**Overlay parameters:**
- QR size: `max(220px, 22% of image width)` — scales with output resolution
- Quiet zone: 20px white border on all sides (QR spec compliance)
- Position: centered horizontally, vertical center at 55% of image height (chest area)
- Interpolation: NEAREST — no antialiasing, no blur, sharp module edges
- Error correction: M level (15% damage recovery)
- JPEG quality: 95 (client deliverable)

**QR validation: multi-pass**
1. Original image
2. Grayscale conversion
3. Unsharp mask sharpened

All 26 successful requests passed QR validation on the first pass (original image).

---

## Key Observations

1. **Rate-limit failures eliminated** — 0/37 hit rate-limit errors (was 7/37 in first test)
2. **QR integrity is now deterministic** — every successful output has a guaranteed readable QR
3. **26 PASS** — +7 vs original test, same as best-tuned run
4. **Wall time improved** — 134.8s vs 151.1s original (37 concurrent)
5. **Overlay adds ~0ms observable latency** — PIL composite is negligible vs KIE time
6. **File pipeline is clean** — stage2_ intermediate and final_ deliverable are distinct, with appropriate TTLs
7. **No regressions** — all previously passing images still pass; no new failures introduced
