# Review of the CLIP Integration Plan — `docs/clip_integration.md`

> This document is a **review of the plan file** [`docs/clip_integration.md`](clip_integration.md), which proposes **fully replacing MediaPipe with CLIP** (CLIP-only) for Stage 1 validation in both `/v1/ghibli` and `/v1/ghibli-qr`.
> Scope: concurrency, behavior under load, deployment on a **Hostinger VPS** (top KVM plan = 8 vCPU / 32GB), API-contract safety for the external team, and the impact on human-acceptance accuracy.
> All numbers are taken directly from `tests/manual/clip_validation_results_*.csv` (145 images: 53 single humans, 23 groups, 69 non-human) and from direct measurements against the code.

---

## 0. Executive verdict

**What is sound in the plan:** architecturally clean and minimally invasive — it rewrites only the body of `validate_stage1_human_portrait`, deletes no files, preserves the `REQUIRE_HUMAN_FACE` bypass and the Stage1/Stage2 separation, and keeps MediaPipe in the file (unreferenced). Removing the identity check is safe (`ENABLE_IDENTITY_CHECK` already defaults to `false`). Its claim that `test_routes_flow.py` needs no changes is **correct** (it mocks at the routes level).

**What it misses:** the plan is correct as a "wiring" change, but it is **silent about the runtime layer under load** and about the **accuracy cost**:

| # | Gap | Severity | Affected by concurrency? |
|---|-----|----------|--------------------------|
| G1 | `_load_clip()` has no lock → load race + partial-state error (and OOM on small plans) | 🔴 Critical | Yes |
| G2 | No preload → first request pays ~2.3s (and timeout if a download happens) | 🔴 Critical | Yes (first burst) |
| G3 | Inherited semaphore of 15 + torch grabs every core | 🔴 Critical | Yes |
| G4 | CLIP weights not baked into the image/volume | 🟠 High | No (but every rebuild) |
| G5 | Memory (~1.5GB/worker) and Docker image size (+~1.3GB) | 🟠 High | No |
| G6 | New contract code `CLIP_CLASSIFIER_FAILURE` | 🟡 Medium | No |
| G7 | CLIP-only accuracy cost: 7.5% biased false-reject — the plan neither quantifies nor mitigates it | 🔴 Critical for the goal | No |

**Bottom line:** the plan is implementable, but **do not ship it as-is**. G1–G3 will affect concurrent requests on Hostinger even on the top plan, and G7 is an accuracy cost that must be acknowledged clearly (it cannot be fixed with a threshold — see §3). §4 provides the mandatory concurrency/deployment fixes. All performance figures are measured on a 32-core dev machine; a VPS vCPU is typically slower — read them as order-of-magnitude.

---

## 1. How the system runs (the context the gaps are measured against)

- Single process: `uvicorn --workers 1` (mandatory because `pending_tasks` is in-memory) → **one event loop**.
- Validation is CPU-bound, run inside `asyncio.to_thread(...)`, bounded by an async semaphore: [`routes.py:99`](../src/ghibli_portrait/api/routes.py#L99) `Semaphore(15)`, passed at both call sites [`routes.py:270`](../src/ghibli_portrait/api/routes.py#L270) and [`routes.py:503`](../src/ghibli_portrait/api/routes.py#L503).
- The lifespan [`main.py:84`](../src/ghibli_portrait/main.py#L84) warms only MediaPipe and expands the thread pool to 100.
- After the plan: CLIP runs inside the same `to_thread` (it does not block the loop) but is governed by the same inherited `Semaphore(15)` — which is the root of G3.

---

## 2. Gaps in detail

### 🔴 G1 — `_load_clip()` has no lock
**Location:** [`clip_validation_service.py:170`](../src/ghibli_portrait/services/clip_validation_service.py#L170)

The first burst of concurrent requests enters `_load_clip()` in different threads at once, and the function has no `Lock`:
1. **Wasted resources:** each thread builds its own model copy (~1.5GB RSS measured; base 44MB → 1557MB). With K concurrent requests, the transient peak ≈ K×1.5GB: on 4–8GB plans K=3–4 is enough for an OOM; on 32GB it is wasteful and spikes memory but usually does not kill.
2. **State bug (independent of the plan):** the assignment is non-atomic — `_clip_model` is set first (line 197) and `_text_features` last (199). A thread can see `_clip_model` ready while `_text_features` is still `None` → `image_features @ None.T` → transient failure.

**Fix:** lock + re-check + publish the guard variable last (§4.1).

### 🔴 G2 — No preload for CLIP
**Location:** [`main.py:84`](../src/ghibli_portrait/main.py#L84) warms only MediaPipe.

Without a preload, the first request pays the load. Measured (weights cached): import ~1.35s + build ~0.94s = **~2.3s** for the first request vs ~0.1s when warm. The very-first-ever download (**~578MB** on disk, measured; open_clip 3.3.0 pulls from the `timm` repo) adds seconds to tens of seconds depending on the network — that alone is what can cause a timeout for the first client.

**Fix:** preload in the lifespan (§4.2) paired with baking the weights (§4.4).

### 🔴 G3 — Inherited semaphore of 15 + torch grabs every core (the most severe concurrency point)
**Location:** [`routes.py:99`](../src/ghibli_portrait/api/routes.py#L99). There is no `set_num_threads` anywhere in the project (verified).

- The plan keeps the `Semaphore(15)` inherited from MediaPipe. But CLIP via torch uses **every core per inference** by default (measured: `torch.get_num_threads()=24` on a 32-core box; on 8 vCPU it is ≈ 8).
- Under a burst: 15 inferences × ~8 torch threads = ~120 threads competing for 8 cores → oversubscription and a tangible latency degradation that also hits other requests going through the same thread pool. (A burst scenario, not steady state.)

**Fix (§4.3):** `set_num_threads(1)` + a dedicated semaphore sized ≈ the core count.

### 🟠 G4 — CLIP weights not baked in
**Location:** [`docker-compose.yml:29`](../docker-compose.yml#L29) provides a volume for MediaPipe only.

CLIP weights (~578MB) download into the container cache, so they are lost on every `rebuild` and re-downloaded; if egress is constrained the first startup is delayed/fails.

**Fix:** bake the weights at build time + an explicit cache path (§4.4).

### 🟠 G5 — Memory and image size (all measured)
- **Memory:** one worker after load = **~1.5GB RSS** (measured). Comfortable on 16/32GB, tight on 4GB.
- **Docker image:** the torch stack on disk is **~740MB** (CPU without CUDA thanks to the `pytorch-cpu` index) + weights **~578MB** = **~1.3GB** increase.
  > Distinction: **578MB** = the weights file on disk (this is what grows the Docker layer); **605MB** = the weights in memory as fp32 (151.3M params × 4). Both are measured, ~0.6GB, for two different contexts.
- Confirm `--workers 1`; each extra worker = an independent ~1.5GB model copy. `mem_limit` **~2GB**.

### 🟡 G6 — New contract code
**Location:** [`clip_validation_service.py:312`](../src/ghibli_portrait/services/clip_validation_service.py#L312) returns `CLIP_CLASSIFIER_FAILURE` — a code the external team has never seen; the current path uses `FACE_DETECTOR_FAILURE` on detector failure.

**Fix:** map it to `FACE_DETECTOR_FAILURE` (§4.5). The other codes (`NOT_REAL_PHOTO`, `MULTIPLE_FACES`, `NO_FACE_DETECTED`) already match — they are fine.

### 🔴 G7 — CLIP-only accuracy cost: biased false-reject, and the plan does not quantify it
CLIP-only with argmax (as the plan proposes) gives:
- **7.5% false-reject** on real humans (4/53), **biased** against dark skin / hijab / grayscale photos.
- **~0% false-accept** on non-human — which is good for the cost-saving goal.

Scores of the four wrongly rejected images (from the CSV):

| Image | argmax | Score | human |
|-------|--------|-------|-------|
| `girl_gray_image` (grayscale) | cartoon | 0.657 | 0.089 |
| `black_weman_black_hijab` | render | 0.594 | 0.342 |
| `girl_black_dress` | no_human | 0.451 | 0.431 |
| `black_boy863574` | multiple | 0.766 | 0.041 |

**Bias signal:** all four are in the dark-skin/hijab/grayscale category, and none is light-skinned; the highest "render" scores among humans are all dark-skinned subjects (`black_man1`=0.454, `black_man4`=0.398). The sample is limited (53) but the direction is consistent. **The plan does not mention this number nor propose any mitigation** — a gap that must be closed by explicit acknowledgment (§3 shows a threshold does not solve it).

---

## 3. CLIP-only accuracy assessment: what is achievable and what is not, within CLIP

The system is **CLIP-only**, so the question is: can the 7.5% false-reject be reduced **inside CLIP** without destroying the goal?

### A confidence threshold does **not** solve it (measured)
Rule: "reject only if a reject class wins with score ≥ T, otherwise accept":

| T | False-reject / human | False-accept / non-human (wasted cost) | False-accept / groups |
|---|----------------------|----------------------------------------|-----------------------|
| argmax | 7.5% | **0%** | 4.3% |
| 0.60 | 3.8% | **26%** 🔴 | 17% |
| 0.70 | 1.9% | **52%** 🔴 | 39% |
| 0.80 | 0% | **67%** 🔴🔴 | 70% |

**Decisive finding:** every 1% reduction in false-reject costs ~10–15% more false-accepts. At a threshold that halves the false-reject (0.60), **26% of non-human images pass** = a huge amount of wasted generation cost, defeating the very reason the gate exists. **No threshold fixes the false-reject without destroying the cost goal.** Two cases (`girl_gray` cartoon 0.657, `black_boy` multiple 0.766) are only rescued above 0.66/0.77, where the false-accept rate is already catastrophic.

### The only effective lever: tuning the prompts (the root cause, and not guaranteed)
The false-rejects are caused by the `render`/`cartoon`/`no_human` classes being overly aggressive on dark skin / hijab / grayscale. Editing the prompt lists in [`clip_validation_service.py:83-159`](../src/ghibli_portrait/services/clip_validation_service.py#L83) may shift those scores without raising the false-accept rate. However:
- It is expected to help `black_weman` (render) and `girl_black_dress` (no_human).
- The grayscale case (`girl_gray` → cartoon 0.657) is **inherently hard for CLIP** (grayscale resembles anime line art in CLIP space) — it may not be fixable by prompts.
- Any prompt change **must** be followed by re-running the benchmark, because it shifts the entire distribution (§4.7).

### Honest conclusion for CLIP-only
- **argmax (the plan as-is) is the least-bad option for the cost goal** (~0% false-accept), but at the price of a 7.5% biased false-reject — an intrinsic limit of CLIP-only that must be acknowledged, not hidden.
- The only lever to cut the false-reject without a cost blowup is improving the prompts, and its success is **partial and not guaranteed** (especially grayscale and single-vs-group).
- If a 7.5% (biased) rejection of real humans is commercially unacceptable, that is the limit of CLIP-only itself — to be documented as a conscious decision.

---

## 4. Mandatory concurrency/deployment package (fixes G1–G6)

Summary table, then details:

| # | Fix | Where | Fixes | Effect |
|---|-----|-------|-------|--------|
| 4.1 | Lock the load + publish the guard last | `clip_validation_service.py` | G1 | Single load; no race, no partial state |
| 4.2 | Public `preload()` in the lifespan | `clip_validation_service.py` + `main.py` | G2 | Model ready; no ~2.3s on the first client |
| 4.3 | `set_num_threads(1)` + dedicated `_clip_sem` | `config.py` + `routes.py` | G3 | Single thread per inference; no oversubscription |
| 4.4 | Bake weights + `OMP/MKL_NUM_THREADS=1` | `Dockerfile` + `docker-compose.yml` | G3/G4 | No runtime download; threads pinned before startup |
| 4.5 | Failure code → `FACE_DETECTOR_FAILURE` | `validation_service.py` | G6 | Does not break the external-team contract |
| 4.6 | Documented fail-open/closed decision | Business decision | — | Explicit behavior when CLIP is down |
| 4.7 | Bind prompts (and threshold, if used) as one contract | Service + test | G7 | No silent accuracy drift |

### 4.1 Lock the load + publish the guard last (fixes G1)
**Purpose:** a single load no matter how many requests are concurrent, and prevent seeing a half-initialized state.
```python
# clip_validation_service.py
import os, threading
_load_lock = threading.Lock()
_CLIP_CACHE_DIR = os.getenv("CLIP_CACHE_DIR", "/app/.cache/clip")

def _load_clip() -> None:
    global _clip_model, _clip_preprocess, _text_features
    if _clip_model is not None:            # fast path, no lock
        return
    with _load_lock:
        if _clip_model is not None:        # re-check inside the lock
            return
        import torch, open_clip
        torch.set_num_threads(1)           # (G3) single-threaded inference
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        model, _, preprocess = open_clip.create_model_and_transforms(
            _MODEL_NAME, pretrained=_PRETRAINED, cache_dir=_CLIP_CACHE_DIR,
        )
        model.eval()
        # ... build text_features as before ...
        _clip_preprocess = preprocess
        _text_features = text_features
        _clip_model = model                # published last: the guard is safe
```

### 4.2 Preload in the lifespan (fixes G2)
**Purpose:** pay the load cost once at startup instead of on the first client.
```python
# clip_validation_service.py — a public API instead of importing the private _load_clip
def preload() -> None:
    """Public warm-up entry point; called once from the app lifespan."""
    _load_clip()

# main.py  lifespan()
from ...services.clip_validation_service import preload
await asyncio.to_thread(preload)
_log.info("CLIP model preloaded at startup")
```
> **Failure behavior (intentional):** do not catch the exception — let a load failure crash startup so `restart: on-failure` retries, instead of serving a broken instance.
> **CLIP-only:** remove the MediaPipe preload (line 94) and the `ghibli_models` volume — MediaPipe is no longer called.
> Raise the healthcheck `start_period` to absorb the load time.

### 4.3 A correctly-sized CLIP semaphore (fixes G3)
**Purpose:** size parallelism to the VPS cores instead of the inherited 15.
```python
# config.py
CLIP_CONCURRENCY_LIMIT = int(os.getenv("CLIP_CONCURRENCY_LIMIT", "4"))  # ≈ vCPUs with margin

# routes.py
_clip_sem = asyncio.Semaphore(s.CLIP_CONCURRENCY_LIMIT)
# pass _clip_sem instead of _mediapipe_sem in both validate_real_human_image_async calls (lines 270 and 503)
```
With `set_num_threads(1)`: each slot = one thread on one core → parallelism = the semaphore size with no thrashing. On 8 vCPU start at **6**.
**Measured trade-off:** single-threading raises one inference from ~25ms to **~75ms**, but it enables parallelism without collapse. Even at ~150ms on a slower VPS with semaphore=4, capacity stays in the tens of images/s — above the paid-generation ceiling (8). So CLIP will not be the bottleneck when tuned.

### 4.4 Bake the weights + thread environment variables (fixes G3/G4)
**Purpose:** eliminate the runtime download and pin torch/OpenMP thread counts before the process starts.
```dockerfile
# Dockerfile
ENV OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
ENV CLIP_CACHE_DIR=/app/.cache/clip
RUN python -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-32-quickgelu', pretrained='openai', cache_dir='/app/.cache/clip')"
```
```yaml
# docker-compose.yml
environment:
  - OMP_NUM_THREADS=1
  - MKL_NUM_THREADS=1
```

### 4.5 Error contract (fixes G6)
Map `CLIP_CLASSIFIER_FAILURE` → `FACE_DETECTOR_FAILURE` when building `ValidationResultV1` in the rewritten body.

### 4.6 Fail-open / fail-closed decision
The current path is **fail-closed**: detector failure → `ValidationResultV1(ok=False)` → **HTTP 422** with `errorType=SYSTEM_ERROR` (no wasted generation, but no service). Source: [`routes.py:271-279`](../src/ghibli_portrait/api/routes.py#L271) returns `status_code=422` for any validation failure; 500 is reserved for generation/ARK errors. The distinction matters to the external team (422 = do not retry, 5xx = retry). If you want fail-open (accept when CLIP fails), that is an explicit business decision — document it.

### 4.7 Bind prompts (and threshold, if used) as one contract
**Purpose:** prevent silent accuracy drift. Scores are a softmax over all six classes together; any change to the prompt lists [`clip_validation_service.py:83-159`](../src/ghibli_portrait/services/clip_validation_service.py#L83) shifts argmax decisions **without failing any test**.
**How:** treat `_PROMPT_TEMPLATES` as a versioned exported unit, add a **regression test** on a fixed sample (the four sensitive humans + known synthetic) asserting the current decision, and require re-running the `tests/manual/` benchmark on any change.

---

## 5. Pre-deploy checklist

- [ ] Lock in `_load_clip` + guard published last (G1).
- [ ] Public `preload()` in the lifespan; startup fails loudly if the load fails (G2).
- [ ] `torch.set_num_threads(1)` + `OMP_NUM_THREADS=1` in the environment (G3).
- [ ] Dedicated `_clip_sem` sized ≈ vCPUs (start at 6), not the inherited `Semaphore(15)` (G3).
- [ ] CLIP weights baked into the image; no runtime download (G4).
- [ ] `--workers 1` confirmed; `mem_limit` ~2GB (G5).
- [ ] `CLIP_CLASSIFIER_FAILURE` → `FACE_DETECTOR_FAILURE`, response HTTP 422 (G6).
- [ ] Remove the MediaPipe preload and volume (no longer called in CLIP-only).
- [ ] **Documented decision on the 7.5% biased false-reject**: accept it as a CLIP-only limit, or invest in prompt tuning (§3).
- [ ] Bind prompts as a contract + a regression test guarding it (§4.7).
- [ ] Expand the evaluation set in the sensitive categories (dark skin / hijab / grayscale) — the bias is based on only 53 images.
- [ ] Load test: N concurrent (2× the ceiling) and confirm p95 is stable and RSS does not grow unbounded.
- [ ] Rewrite the `test_validation_service.py` tests to mock CLIP instead of `_detect_faces`.

---

## 6. Decision table within CLIP-only

| Setting | False-reject / human | False-accept / non-human (cost) | Verdict |
|---------|----------------------|----------------------------------|---------|
| **argmax (the plan as-is)** | 7.5% biased | **~0%** ✅ | Least-bad for cost; accept the bias or improve the prompts |
| Threshold 0.60 | 3.8% | 26% 🔴 | Destroys the cost-saving goal |
| Threshold 0.80 | 0% | 67% 🔴🔴 | Unacceptable |
| argmax + improved prompts | hopefully < 7.5% (not guaranteed for grayscale/group) | ~0% | The only safe lever — requires experimentation and a benchmark |

> **Baseline:** any CLIP integration requires the full §4 package before shipping. And within CLIP-only, the 7.5% biased false-reject is an intrinsic limit that a threshold cannot remove — it can only be (partially) mitigated by prompt tuning, or accepted as a documented, conscious decision.

---

## 7. Out of scope but worth saying honestly: the "large-scale production" ceiling is not CLIP

For a **single VPS**: the §4 package is sufficient. For horizontal scaling the real blocker is **not CLIP** but **`pending_tasks` in memory**, which is what forces `--workers 1`. Any scale-out starts by **moving that state into Redis**; only then does CLIP become a cost factor (each worker = a ~1.5GB private copy, or split validation into a standalone service loaded once and shared). The correct order: **Redis for state first → then distribute CLIP**.
