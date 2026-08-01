# CLIP Integration Plan — Stage 1 Validation

## Decision

CLIP (`clip_validation_service.py`) fully replaces MediaPipe as the thing that gets
**called** for Stage 1 human-portrait validation, in both `/v1/ghibli` and
`/v1/ghibli-qr`. The identity-drift check (`identity_check.py`) stops being
called, with no replacement. QR / Stage 2 validation (`qr_validation.py`) is
untouched. **No file is deleted** — only call sites change; unused code (MediaPipe
helpers, `identity_check.py`) stays in the repo, just unreferenced from the
request path.

## Changes by file

### 1. `src/ghibli_portrait/services/validation_service.py`
- Add an import of `validate_human_portrait` from `clip_validation_service.py`.
- Rewrite the **body** of `validate_stage1_human_portrait()` only: instead of
  calling `_detect_faces()` + `_is_synthetic_face()`, it calls CLIP's
  `validate_human_portrait(img, image_url)` and maps the result onto the
  existing `ValidationResultV1` shape (`ok` / `code` / `message`), preserving:
  - the `REQUIRE_HUMAN_FACE=False` bypass (checked first, no CLIP call in that case),
  - `CLIP_CLASSIFIER_FAILURE` → `SYSTEM_ERROR` (same treatment as today's
    detector-failure path),
  - CLIP's existing codes (`NOT_REAL_PHOTO`, `MULTIPLE_FACES`,
    `NO_FACE_DETECTED`) pass through unchanged, so the API's error contract to
    callers doesn't change.
- Everything else in the file — Layer 1 URL checks, `validate_stage2_input`,
  `extract_skin_color_hex`, `validate_single_image_url_list`, the
  download/threading logic in `validate_real_human_image_async` — stays as-is.
- All MediaPipe code (`_detect_faces`, `_is_synthetic_face`,
  `FaceDetectionResult`, model download/singleton) **stays in the file**, just
  no longer called from `validate_stage1_human_portrait`.

### 2. `src/ghibli_portrait/services/identity_check.py`
- No edits. File stays exactly as-is, just unreferenced.

### 3. `src/ghibli_portrait/api/routes.py`
- Remove the `check_identity_drift_async` **import line**.
- Remove the **call site**: the "IDENTITY DRIFT GUARD" block inside
  `automated_pipeline()` (the drift check, the drift-retry generation, the
  `IDENTITY_DRIFT_DETECTED` error response). Flow goes straight from the
  Stage 1 re-host into the Stage 2 compose loop.

### 4. `src/ghibli_portrait/config.py`
- No edits. `ENABLE_IDENTITY_CHECK` stays defined, just unread.

### 5. `src/ghibli_portrait/services/clip_validation_service.py`
- One docstring line updated ("standalone, not yet wired in" → now wired in).
  No logic change.

### 6. Tests
- `tests/test_validation_service.py`: rewrite the 4 `validate_stage1_human_portrait`
  tests that currently mock `vs._detect_faces` / `vs._is_synthetic_face` (those
  mocks go silently dead once the function stops calling them) to instead mock
  the CLIP call. The 2 direct `_is_synthetic_face` unit tests are untouched.
- `tests/conftest.py`, `tests/test_routes_flow.py`: no changes needed.

## Dependency note

`open_clip` / `torch` become a hard runtime dependency of the live request
path (first-call ~350MB weights download) instead of a standalone script's
dependency — worth confirming they're already declared in `pyproject.toml`
before this ships.

## Explicitly out of scope

- No hybrid MediaPipe + CLIP split (MediaPipe's tuned multi-face area-ratio
  gate is not preserved for face counting — CLIP's coarser "multiple people"
  semantic label is used instead).
- No CLIP-based identity/similarity replacement for the removed drift check.
- No file deletions.
- No env-var / parameter renames.
