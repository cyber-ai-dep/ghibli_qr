# Review of "Engineering Ownership Matrix" (OWN-2026-07)

> This is a companion review to [`docs/clip_integration_review.md`](clip_integration_review.md) and the earlier Production Deployment Dossier review. The source document (an "Engineering Ownership Matrix" referencing `ADR-2026-07`) was shared as an external artifact, not a file in this repo.

## Scope decision (confirmed with the project owner)

The source document assumes this service sits inside a larger organization with a dedicated Platform/DevOps team, an API Gateway, a service mesh, and mobile-app consumers, all governed by a ratified `ADR-2026-07`. **None of this exists in this repository or this project's actual deployment** (single VPS, Docker Compose, one external partner team as the sole caller). The project owner confirmed: ignore anything about gateways/platform teams that isn't part of this actual project.

As a result, **Sections 02, 03, 04, 05 of that document (Platform responsibilities, Shared responsibilities, Open Decisions tied to the ADR, and the re-attributed readiness table) do not apply** and should not drive any work here.

## What's actually useful and actionable (Section 01 / 06, filtered)

| Priority | Item | Status at time of review |
|---|---|---|
| High | Add a request-size cap on image downloads (`Content-Length` check + byte ceiling) in `validate_real_human_image_async` / `_inline_ref` | Missing — real memory-exhaustion vector against the single `--workers 1` process |
| Medium | Deepen `/v1/health` (or add `/v1/ready`) to reflect real CLIP-loaded / ARK-reachable state | Planned in a prior session turn (design only, not yet implemented) |
| Low | Remove dead MediaPipe code path (`_detect_faces`, `_is_synthetic_face`, `_ensure_model_downloaded`) and the orphaned `blaze_face_short_range.tflite` asset | Confirmed unused; `mediapipe` already removed from `pyproject.toml` |
| Low | Publish/version the OpenAPI spec for the external partner team | FastAPI already generates it at `/docs`; just needs to be shared/pinned per release |
| Low | Confirm whether the partner team still calls `/v1/ghibli` and `/v1/qr-lock` standalone, or only the combined `/v1/ghibli-qr` | Both are legitimate, documented endpoints (README.md, docs/usage.md) — not dead code, contrary to the source doc calling them "legacy" |

## Errors in the source document — do not act on these

1. **"Correct the stale `--workers 1` Dockerfile comment (resource-multiplication, not a race/delivery issue)"** — verified against [`Dockerfile:121-124`](../Dockerfile#L121): the existing comment is accurate (`pending_tasks` is in-process, so a second worker cannot deliver a result to the request that submitted it). Applying this "fix" would replace an accurate explanation with a less accurate one. **Do not apply.**
2. ~~"Remove/never activate this repo's drafted `k8s/ingress.yaml`" — verified: does not exist in this repo.~~ **Superseded 2026-07-30:** commit `098bf5b "Add Ingress manifest and input image spec"` added `k8s/ingress.yaml`, matching almost verbatim what the original Dossier described (nginx ingress class, 120s proxy timeouts, TLS commented out pending cert-manager, `host: "<set-per-environment>"`). The file now genuinely exists — re-evaluate this ownership-matrix bullet against the live file rather than treating it as inapplicable. Its own top comment states the reason: *"Required because this API is publicly consumed directly... a ClusterIP Service alone is only reachable inside the cluster."* This directly supports the project's stated default of public/global access (see the public/private access-toggle discussion below) rather than contradicting it.
3. **Framing Docker Compose as "not the real production path, which is Kubernetes"** — inverted for this project: the actual production path today is Docker Compose on a single VPS; the Kubernetes manifests are drafted and have never been applied to a live cluster. Compose should not be deprioritized as a "dev convenience only" artifact.

## Standing note for future sessions

If this project ever gains a real API gateway, a dedicated platform/DevOps team, or a ratified ADR process, re-evaluate Sections 02–05 of the source document against that new reality — they were dismissed here only because that context did not exist at review time (2026-07-27), not because the framework itself is wrong.
