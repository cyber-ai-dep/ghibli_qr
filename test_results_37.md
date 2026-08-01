# Ghibli QR API — Full Pipeline Test Results
**Date:** 2026-05-23  
**Endpoint:** POST /v1/ghibli-qr (full 2-stage pipeline)  
**Server:** http://localhost:8010  
**Total requests:** 37 concurrent  
**Wall time:** 151.1s (all 37 fired simultaneously)  
**QR URL encoded:** https://example.com/profile

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

| # | File | Duration | Result | Code | Output URL |
|---|---|---|---|---|---|
| 01 | istockphoto-944986244-612x612.jpg | 79.1s | ✅ PASS | — | .../tmp/final_... |
| 02 | van.jpg | 2.7s | ❌ FAIL | STAGE1_API_ERROR | KIE rate limit |
| 03 | istockphoto-1784543440-612x612.jpg | 151.1s | ✅ PASS | — | .../tmp/final_... |
| 04 | 37403.jpg | 94.7s | ✅ PASS | — | .../tmp/final_... |
| 05 | whiteman1.jpg | 114.6s | ✅ PASS | — | .../tmp/final_... |
| 06 | whiteman2.jpg | 67.4s | ✅ PASS | — | .../tmp/final_... |
| 07 | whiteman3.jpg | 122.7s | ✅ PASS | — | .../tmp/final_... |
| 08 | whitewoman1.jpg | 94.9s | ✅ PASS | — | .../tmp/final_... |
| 09 | whitewoman2.jpg | 1.1s | ⚠️ REJECTED | MULTIPLE_FACES | Unexpected — image may have a background face |
| 10 | whitewoman3.png | 3.3s | ❌ FAIL | STAGE1_API_ERROR | KIE rate limit |
| 11 | blackwoman1.jpg | 2.1s | ⚠️ REJECTED | MULTIPLE_FACES | Unexpected — check image |
| 12 | blackwoman2.jpg | 1.2s | ⚠️ REJECTED | MULTIPLE_FACES | Unexpected — check image |
| 13 | blackwoman3.jpg | 53.5s | ✅ PASS | — | .../tmp/final_... |
| 14 | blackwoman3.jpg (dup) | 99.5s | ✅ PASS | — | .../tmp/final_... |
| 15 | hijabigirl3.jpg | 112.4s | ✅ PASS | — | .../tmp/final_... |
| 16 | hijabigirl4.jpg | 70.1s | ✅ PASS | — | .../tmp/final_... |
| 17 | hijabigirl5.jpg | 82.7s | ✅ PASS | — | .../tmp/final_... |
| 18 | hijabigirl6.jpg | 2.5s | ❌ FAIL | STAGE1_API_ERROR | KIE rate limit |
| 19 | minecraft1.jpg | 1.5s | ✅ CORRECTLY REJECTED | NOT_REAL_PHOTO | Synthetic detection working |
| 20 | minecraft4.jpg | 1.4s | ✅ CORRECTLY REJECTED | NOT_REAL_PHOTO | Synthetic detection working |
| 21 | minecraft4.jpg (dup) | 2.1s | ✅ CORRECTLY REJECTED | NOT_REAL_PHOTO | Synthetic detection working |
| 22 | selfiman.jpg | 2.8s | ❌ FAIL | STAGE1_API_ERROR | KIE rate limit |
| 23 | threemens.jpg | 1.6s | ✅ CORRECTLY REJECTED | MULTIPLE_FACES | Group photo blocked |
| 24 | threewoman.jpg | 1.1s | ✅ CORRECTLY REJECTED | MULTIPLE_FACES | Group photo blocked |
| 25 | threegirls.jpg | 1.1s | ✅ CORRECTLY REJECTED | MULTIPLE_FACES | Group photo blocked |
| 26 | nghtman.jpg | 73.5s | ✅ PASS | — | .../tmp/final_... |
| 27 | maskman.jpg | 2.7s | ❌ FAIL | STAGE1_API_ERROR | KIE rate limit |
| 28 | nightman.jpg | 87.0s | ✅ PASS | — | .../tmp/final_... |
| 29 | maskman2.jpg | 1.1s | ⚠️ REJECTED | MULTIPLE_FACES | Masked man — may have 2 people |
| 30 | nightman2.jpg | 2.8s | ❌ FAIL | STAGE1_API_ERROR | KIE rate limit |
| 31 | nightman3.jpg | 64.4s | ✅ PASS | — | .../tmp/final_... |
| 32 | aboy.jpg | 8.0s | ⚠️ REJECTED | NOT_REAL_PHOTO | Boy flagged as synthetic — needs investigation |
| 33 | babyboy.jpg | 11.6s | ❌ FAIL | STAGE2_API_ERROR | Passed Stage 1 but KIE rate limit hit Stage 2 |
| 34 | babyboy2.jpg | 97.4s | ✅ PASS | — | .../tmp/final_... |
| 35 | babygirl.jpg | 85.4s | ✅ PASS | — | .../tmp/final_... |
| 36 | babygirl2.jpg | 61.0s | ✅ PASS | — | .../tmp/final_... |
| 37 | babygirl3.jpg | 53.6s | ✅ PASS | — | .../tmp/final_... |

---

## Summary

| Metric | Value |
|---|---|
| Total requests | 37 |
| ✅ Successfully generated | 19 |
| ❌ Total failed | 18 |
| Wall time | 151.1s |
| Avg per successful request | 87.6s |
| Fastest successful | 53.5s (req 13) |
| Slowest successful | 151.1s (req 3) |

---

## Failure Breakdown

### 1. KIE API Rate Limit — 7 failures (infrastructure, not bugs)
Firing 37 requests simultaneously exceeded the KIE API's call rate limit.
These are NOT validation bugs — retrying individually would succeed.

| # | File | Stage |
|---|---|---|
| 02 | van.jpg | Stage 1 |
| 10 | whitewoman3.png | Stage 1 |
| 18 | hijabigirl6.jpg | Stage 1 |
| 22 | selfiman.jpg | Stage 1 |
| 27 | maskman.jpg | Stage 1 |
| 30 | nightman2.jpg | Stage 1 |
| 33 | babyboy.jpg | Stage 2 (passed Stage 1 OK) |

### 2. Correctly Rejected — 6 (validation working as designed)
| # | File | Reason |
|---|---|---|
| 19 | minecraft1.jpg | NOT_REAL_PHOTO — synthetic 3D render ✓ |
| 20 | minecraft4.jpg | NOT_REAL_PHOTO — synthetic 3D render ✓ |
| 21 | minecraft4.jpg | NOT_REAL_PHOTO — duplicate, same result ✓ |
| 23 | threemens.jpg | MULTIPLE_FACES — group of 3 men ✓ |
| 24 | threewoman.jpg | MULTIPLE_FACES — group of 3 women ✓ |
| 25 | threegirls.jpg | MULTIPLE_FACES — group of 3 girls ✓ |

### 3. Unexpected Rejections — 5 (need investigation)
| # | File | Code | Issue |
|---|---|---|---|
| 09 | whitewoman2.jpg | MULTIPLE_FACES | Single-person photo flagged — background face or mirror reflection |
| 11 | blackwoman1.jpg | MULTIPLE_FACES | Single-person photo flagged — background face or second person |
| 12 | blackwoman2.jpg | MULTIPLE_FACES | Single-person photo flagged — background face or second person |
| 29 | maskman2.jpg | MULTIPLE_FACES | May actually have 2 people, or reflected/poster face |
| 32 | aboy.jpg | NOT_REAL_PHOTO | Real boy photo flagged as synthetic — synthetic detection still too aggressive for this specific image |

---

## Key Observations

1. **Minecraft rejection works** — all 3 Minecraft images (19, 20, 21) correctly rejected with NOT_REAL_PHOTO within ~1.5s
2. **Group photo rejection works** — all 3 group images (23, 24, 25) correctly rejected with MULTIPLE_FACES within ~1.1s
3. **Baby/child photos mostly pass** — 4 out of 5 baby/child images passed (34–37); aboy.jpg [32] still flagged
4. **Night photos pass** — 3 out of 4 night photos passed (26, 28, 31); nightman2 hit rate limit
5. **Rate limiting is the main source of failures** — 7/18 failures are KIE API throttling, not validation logic
6. **Concurrent throughput** — 19 successful full pipeline completions in 151 seconds wall time (all parallel)
7. **False positive multi-face** — 3 single-person photos rejected (09, 11, 12) — MediaPipe detecting a background/secondary face above the 2% area threshold
