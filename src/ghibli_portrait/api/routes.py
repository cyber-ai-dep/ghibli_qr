"""
API Routes - Ghibli Portrait API V1

This module implements Layer 4 (Orchestration) of the validation architecture.
It coordinates validation layers and stages without adding new validation rules.

Swagger Tags:
- API Production: Primary production endpoint (POST /v1/ghibli-qr)
- Core APIs: Core transformation endpoints
- Internal / System: Internal webhooks and system endpoints
- Health & Utilities: Health checks and utility endpoints
"""

import asyncio
import io as _io
import json
import logging
import time as _time
from typing import Dict
from uuid import uuid4

import httpx
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter

_log = logging.getLogger(__name__)
# Uvicorn's dictConfig does not attach handlers to application loggers, only to
# its own "uvicorn.*" loggers. Add a StreamHandler here so [GEN] retry events
# are visible in the log file (stderr → redirected by the nohup launch command).
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)
    _log.propagate = False  # prevent double-printing if root gets a handler later
from PIL import Image as _PILImage

from src.ghibli_portrait.api.responses import (
    success_response,
    error_response,
    validation_error_response,
    external_error_response,
    internal_error_response,
)
from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import (
    CallbackRequest,
    GhibliQRRequest,
    Image2GhibliRequest,
    QRLockRequest,
    ApiError,
    ApiErrorResponse,
    ApiSuccessResponse,
    ErrorType,
    ErrorStage,
)
from src.ghibli_portrait.services.image_service import generate_img
from src.ghibli_portrait.services.qr_service import get_qr
from src.ghibli_portrait.services.qr_validation import validate_qr_from_image, validate_qr_from_image_url, QRValidationResult
from src.ghibli_portrait.services.validation_service import (
    validate_single_image_url_list,
    validate_real_human_image_async,
    validate_stage2_input,
    extract_skin_color_hex,
)
from src.ghibli_portrait.utils.url_utils import shorten

# ============================================================================
# SWAGGER TAG DEFINITIONS
# ============================================================================

TAGS_METADATA = [
    {
        "name": "API Production",
        "description": "**Primary production endpoint.** Use `POST /v1/ghibli-qr` for the complete Ghibli + QR pipeline.",
    },
    {
        "name": "Core APIs",
        "description": "Core transformation endpoints for individual operations.",
    },
    {
        "name": "Internal / System",
        "description": "Internal webhooks and system endpoints. Not for external use.",
    },
    {
        "name": "Health & Utilities",
        "description": "Health checks and utility endpoints.",
    },
]

router = APIRouter(prefix="/v1")
pending_tasks: Dict[str, asyncio.Future] = {}
s = Settings()

# Limits concurrent CLIP classification calls to cap CPU usage on shared servers.
# CLIP inference is pinned to 1 torch thread per call, so this semaphore's size IS
# the real parallelism — oversized values oversubscribe the host's cores under a burst.
# Tune via CLIP_CONCURRENCY_LIMIT env var (default 4; start near vCPU count with margin).
_clip_sem = asyncio.Semaphore(int(s.CLIP_CONCURRENCY_LIMIT))

# Limits concurrent Layer-2 image downloads. I/O-bound (not CPU), so this is an
# internal safety cap against an accidental burst rather than a CPU concern.
# Tune via DOWNLOAD_CONCURRENCY_LIMIT env var (default 24).
_download_sem = asyncio.Semaphore(int(s.DOWNLOAD_CONCURRENCY_LIMIT))

# Limits concurrent image-generation submissions to the provider (BytePlus ARK) to
# prevent rate-limit errors under burst load. All stages (Stage 1, Stage 2, identity
# retry) share this one semaphore. Tune via GENERATION_CONCURRENCY_LIMIT env var (default 24).
_gen_sem = asyncio.Semaphore(s.GENERATION_CONCURRENCY_LIMIT)

# Substrings in the provider's error message that indicate a rate-limit response.
_RATE_LIMIT_SIGNALS = frozenset({"call frequency", "too high", "rate limit", "rate_limit"})


def _is_rate_limited(result: dict) -> bool:
    """Return True when the provider's response indicates a submission rate limit."""
    msg = str(result.get("msg", "")).lower()
    return result.get("code") == 429 or any(sig in msg for sig in _RATE_LIMIT_SIGNALS)


async def _submit_generation(*args, **kwargs) -> dict:
    """Wrap generate_img() with the concurrency semaphore and rate-limit retry.

    Flow per attempt (up to 3 total, 2 retries):
      - Acquire _gen_sem  →  call generate_img()  →  release _gen_sem
      - code 200            → return immediately (success)
      - rate-limit response → log + sleep 5 s + retry (if attempts remain)
      - any other non-200   → return immediately (do not retry)
    """
    result: dict = {}
    for attempt in range(1, 4):
        _log.info("[GEN] Submission attempt %d/3", attempt)
        async with _gen_sem:
            result = await generate_img(*args, **kwargs)
        if result.get("code") == 200:
            _log.info("[GEN] Submission succeeded (attempt %d/3)", attempt)
            return result
        if _is_rate_limited(result):
            if attempt < 3:
                _log.warning("[GEN] Rate limit detected — retrying in 5 s (attempt %d/3)", attempt)
                await asyncio.sleep(5)
                continue
            _log.error("[GEN] Retry budget exhausted after 3 attempts — rate limit persists")
        return result  # non-rate-limit error OR exhausted retries: return as-is
    return result


_DOWNLOAD_HEADERS = {"User-Agent": "ghibli-qr/0.1"}


def _save_local_copy(img: _PILImage.Image, filename: str, quality: int = 92) -> None:
    """When SAVE_OUTPUT_LOCAL is enabled, also write an on-disk copy to OUTPUT_DIR.

    OUTPUT_DIR is not covered by the /tmp TTL cleanup loop (see main.py
    _tmp_cleanup_loop), so this is how stage1_/final_ images can be kept for
    local inspection past their tmp TTL without growing static/tmp unbounded.
    """
    if not s.SAVE_OUTPUT_LOCAL:
        return
    out_dir = s.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    img.save(out_dir / filename, format="JPEG", quality=quality, optimize=True)
    _log.info("Saved local copy: %s", out_dir / filename)


async def _rehost_stage2(remote_url: str):
    """Download Stage 2 output and save locally at full resolution.

    Returns (pil_image, local_url). Raises on any failure — caller must
    catch and fall back to the original provider URL.

    When SAVE_OUTPUT_LOCAL is enabled, the same final image is ALSO written to
    OUTPUT_DIR on this machine (in addition to the served /tmp copy). The served
    URL returned in the API response is unchanged.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(remote_url, headers=_DOWNLOAD_HEADERS)
        resp.raise_for_status()
    content = resp.content

    def _save():
        img = _PILImage.open(_io.BytesIO(content)).convert("RGB")
        # Full resolution — final client deliverable, no thumbnail
        filename = f"final_{uuid4()}.jpg"
        path = s.TMP_PATH / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, format="JPEG", quality=95, optimize=True)
        _save_local_copy(img, filename, quality=95)
        return img, s.DOMAIN + "/tmp/" + filename

    img, local_url = await asyncio.to_thread(_save)
    return img, local_url


# ============================================================================
# HEALTH & UTILITIES
# ============================================================================

@router.get(
    "/health",
    tags=["Health & Utilities"],
    summary="Service health check",
    description="Returns service status and timestamp. Use for liveness/readiness probes.",
    response_model=ApiSuccessResponse,
)
async def health():
    """Health check endpoint for monitoring and liveness probes."""
    return JSONResponse(
        content=success_response(
            message="Ghibli Portrait API V1 is running",
            data={"status": "healthy"},
        ).model_dump(by_alias=True)
    )


@router.get(
    "/qr-url/",
    tags=["Health & Utilities"],
    summary="Generate shortened URL (deterministic)",
    description=(
        "Returns a shortened URL using deterministic hashing. "
        "The same URL always produces the same short code. "
        "No validation performed - works for any URL string."
    ),
    response_model=ApiSuccessResponse,
)
async def get_short_url(url: str):
    """Get or generate shortened URL using deterministic hashing."""
    short_data = shorten(url)
    return JSONResponse(
        content=success_response(
            message="Short URL generated successfully",
            data={"url": short_data.url, "code": short_data.code},
        ).model_dump(by_alias=True)
    )


# ============================================================================
# CORE APIs
# ============================================================================

@router.post(
    "/ghibli",
    tags=["Core APIs"],
    summary="Transform portrait to Ghibli style",
    description=(
        "Transform a single portrait image to Ghibli-style art. "
        "Powered by BytePlus ARK (Seedream). "
        "Validation: public URL, single image, face detection, human verification."
    ),
    response_model=ApiSuccessResponse,
    responses={
        422: {"model": ApiErrorResponse, "description": "Validation or business rule failure"},
        500: {"model": ApiErrorResponse, "description": "Internal or external API error"},
        504: {"model": ApiErrorResponse, "description": "Stage 1 timeout"},
    },
)
async def transform2ghibli(request: Image2GhibliRequest):
    """Transform portrait to Ghibli style with comprehensive validation."""
    try:
        v = validate_single_image_url_list(request.img_urls)
        if not v.ok:
            return JSONResponse(
                status_code=422,
                content=validation_error_response(
                    field="imgUrls",
                    message=v.reason,
                    code="SINGLE_IMAGE_REQUIRED",
                    stage=ErrorStage.INPUT,
                ).model_dump(by_alias=True)
            )

        # Async download + CLIP classification in thread (no event loop blocking)
        # download_sem caps concurrent downloads, clip_sem caps concurrent CLIP inference
        validation_result, source_img = await validate_real_human_image_async(request.img_urls[0], settings=s, clip_sem=_clip_sem, download_sem=_download_sem)
        if not validation_result.ok:
            return JSONResponse(
                status_code=422,
                content=validation_error_response(
                    field="imgUrls",
                    message=validation_result.message,
                    code=validation_result.code,
                    stage=validation_result.stage,
                    error_type=validation_result.error_type,
                ).model_dump(by_alias=True)
            )

        # Skin color/tone from the source photo — reuses the already-decoded
        # image so no extra download is needed.
        skin_color_hex = None
        if source_img is not None:
            skin_color_hex = await asyncio.to_thread(extract_skin_color_hex, source_img)

        # Semaphore + rate-limit retry via _submit_generation.
        res = await _submit_generation(**request.model_dump(), model=s.GHIBLI_MODEL)

        if res["code"] != 200:
            return JSONResponse(
                status_code=500,
                content=external_error_response(
                    message="Image generation API error",
                    code="GENERATION_API_ERROR",
                    stage=ErrorStage.STAGE1_GHIBLI,
                    detail=res.get("msg", "External API returned an error"),
                ).model_dump(by_alias=True)
            )

        task_id = res["data"]["taskId"]
        future = asyncio.get_running_loop().create_future()
        pending_tasks[task_id] = future

        try:
            webhook_result: CallbackRequest = await asyncio.wait_for(future, timeout=300)

            if webhook_result.is_failure:
                return JSONResponse(
                    status_code=500,
                    content=external_error_response(
                        message="Image generation task failed",
                        code="GENERATION_TASK_FAILED",
                        stage=ErrorStage.STAGE1_GHIBLI,
                        detail=webhook_result.data.failMsg or webhook_result.msg,
                    ).model_dump(by_alias=True)
                )

            try:
                _param_raw = json.loads(webhook_result.data.param)
                params = json.loads(_param_raw["input"]) if "input" in _param_raw else _param_raw
            except Exception:
                params = {}
            result_urls = webhook_result.data.get_result_urls() or []

            return JSONResponse(
                content=success_response(
                    message="Ghibli portrait generated successfully",
                    data={
                        "resultUrls": result_urls,
                        "model": webhook_result.data.model,
                        "costTime": webhook_result.data.costTime,
                        "quality": params.get("quality", "basic"),
                        "aspectRatio": params.get("aspect_ratio", "1:1"),
                        "skinColor": skin_color_hex,
                    },
                ).model_dump(by_alias=True)
            )

        except asyncio.TimeoutError:
            pending_tasks.pop(task_id, None)
            return JSONResponse(
                status_code=504,
                content=external_error_response(
                    message="Request timeout",
                    code="WEBHOOK_TIMEOUT",
                    stage=ErrorStage.STAGE1_GHIBLI,
                    detail=f"Task timed out after 5 minutes (taskId: {task_id})",
                ).model_dump(by_alias=True)
            )

        except asyncio.CancelledError:
            pending_tasks.pop(task_id, None)
            raise
        except Exception as e:
            pending_tasks.pop(task_id, None)
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message=f"Unexpected error: {str(e)}",
                    stage=ErrorStage.STAGE1_GHIBLI,
                ).model_dump(by_alias=True)
            )

    except asyncio.CancelledError:
        raise
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=internal_error_response(
                message=f"Unexpected error: {str(e)}",
                stage=ErrorStage.ORCHESTRATION,
            ).model_dump(by_alias=True)
        )


@router.post(
    "/qr-lock",
    tags=["Core APIs"],
    summary="Generate QR code with lock screen overlay",
    description="Generates a QR code image with lock screen background and optional URL shortening.",
    response_model=ApiSuccessResponse,
    responses={
        422: {"model": ApiErrorResponse, "description": "Request validation failure"},
        500: {"model": ApiErrorResponse, "description": "QR generation error"},
    },
)
async def get_qr_lock(req: QRLockRequest):
    """Generate QR code with lock screen overlay."""
    try:
        short_url_data = None
        if req.shorten_url is True:
            short_url_data = shorten(req.url)

        url_to_encode = short_url_data.url if req.shorten_url is True else req.url
        version = req.version

        # Pure local PIL work — thread keeps event loop free
        def _gen_qr():
            img = get_qr(url=url_to_encode, version=version)
            filename = f"{uuid4()}.png"
            img.save(s.TMP_PATH / filename)
            return filename

        filename = await asyncio.to_thread(_gen_qr)
        url_path = s.DOMAIN + "/tmp/" + filename

        response_data = {"qrUrl": url_path, "encodedUrl": req.url}
        if short_url_data:
            response_data["shortUrl"] = {"url": short_url_data.url, "code": short_url_data.code}

        return JSONResponse(
            content=success_response(
                message="QR code with lock screen generated successfully",
                data=response_data,
            ).model_dump(by_alias=True)
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=internal_error_response(
                message=f"QR generation failed: {str(e)}",
                stage=ErrorStage.ORCHESTRATION,
            ).model_dump(by_alias=True)
        )


# ============================================================================
# INTERNAL / SYSTEM
# ============================================================================

@router.delete(
    "/qr-lock/{img_id}",
    tags=["Internal / System"],
    summary="Delete QR code image",
    description="Deletes a previously generated QR code image by its filename.",
    response_model=ApiSuccessResponse,
    responses={
        404: {"model": ApiErrorResponse, "description": "Image not found"},
    },
)
async def delete_qr_lock(img_id: str):
    """Delete a temporary QR code image."""
    img_id = img_id.replace(".png", "")
    imgpath = s.TMP_PATH / f"{img_id}.png"

    if not imgpath.exists():
        return JSONResponse(
            status_code=404,
            content=error_response(
                message="Image not found",
                errors=[ApiError(
                    code="IMAGE_NOT_FOUND",
                    type=ErrorType.VALIDATION_ERROR,
                    stage=ErrorStage.INPUT,
                    field="imgId",
                    message=f"Image {img_id} does not exist"
                )],
            ).model_dump(by_alias=True)
        )

    imgpath.unlink()
    return JSONResponse(
        content=success_response(
            message="Image deleted successfully",
            data={"deletedId": img_id},
        ).model_dump(by_alias=True)
    )


@router.post(
    "/ghibli-qr",
    tags=["API Production"],
    summary="Ghibli + QR Pipeline (Production Endpoint)",
    description=(
        "**Primary production endpoint.** "
        "Two-stage pipeline: (1) Transform portrait to Ghibli style, "
        "(2) Compose with QR code lock screen. "
        "Powered by BytePlus ARK (Seedream). "
        "Input image must be a real human portrait photo."
    ),
    response_model=ApiSuccessResponse,
    responses={
        422: {"model": ApiErrorResponse, "description": "Validation or business rule failure"},
        500: {"model": ApiErrorResponse, "description": "Internal or external API error"},
        504: {"model": ApiErrorResponse, "description": "Stage timeout"},
    },
)
async def automated_pipeline(request: GhibliQRRequest):
    """
    Automated two-stage pipeline for Ghibli + QR composition.

    Layer 4 (Orchestration): Coordinates validation and generation stages.
    Both stages generate via BytePlus ARK (Seedream); the response reports ARK_MODEL.
    """
    _req_id = uuid4().hex[:8]
    _t0 = _time.monotonic()
    _log.info("[%s] Pipeline start — img=%s qr_url=%s", _req_id, request.img_url.split("/")[-1], request.url[:40])
    try:
        # ---------------------------------------------------------------------
        # Layers 1,2,3A: validate (async download + CLIP classification in thread)
        # Two separate semaphores: download_sem caps concurrent downloads (I/O,
        # DOWNLOAD_CONCURRENCY_LIMIT), clip_sem caps concurrent CLIP inference (CPU).
        # ---------------------------------------------------------------------
        _t_val = _time.monotonic()
        validation_result, source_img = await validate_real_human_image_async(request.img_url, settings=s, clip_sem=_clip_sem, download_sem=_download_sem)
        _log.info("[%s] Validation done in %.1fs — ok=%s code=%s", _req_id, _time.monotonic() - _t_val, validation_result.ok, getattr(validation_result, "code", None))
        if not validation_result.ok:
            return JSONResponse(
                status_code=422,
                content=validation_error_response(
                    field="imgUrl",
                    message=validation_result.message,
                    code=validation_result.code,
                    stage=validation_result.stage,
                    error_type=validation_result.error_type,
                ).model_dump(by_alias=True),
            )

        # Skin color/tone from the source photo — reuses the already-decoded
        # image so no extra download is needed.
        skin_color_hex = None
        if source_img is not None:
            skin_color_hex = await asyncio.to_thread(extract_skin_color_hex, source_img)

        _stage1_prompt = s.PROMPT_PIC_TO_GHIBLI
        if skin_color_hex:
            _stage1_prompt += (
                f"\n\nEXACT SKIN COLOR: {skin_color_hex}. "
                "This is the person's real measured skin tone. "
                "You MUST reproduce this exact color in the illustration. "
                "Do NOT lighten, darken, whiten, or shift this color in any direction."
            )
            _log.info("[%s] Stage 1 skin tone injected into prompt: %s", _req_id, skin_color_hex)

        # =====================================================================
        # STAGE 1: Submit Ghibli generation (native async HTTP)
        # =====================================================================
        _t_s1_submit = _time.monotonic()
        _log.info("[%s] Stage 1 attempt 1/1 — model=%s", _req_id, s.GHIBLI_MODEL)
        res = await _submit_generation(
            [request.img_url],
            _stage1_prompt,
            model=s.GHIBLI_MODEL,
            negative_prompt=s.NEGATIVE_PROMPT_PIC_TO_GHIBLI,
        )
        _log.info("[%s] Stage 1 attempt 1 submit done in %.2fs — code=%s", _req_id, _time.monotonic() - _t_s1_submit, res.get("code"))
        if res["code"] != 200:
            return JSONResponse(
                status_code=500,
                content=external_error_response(
                    message="Stage 1 (Ghibli generation) API error",
                    code="STAGE1_API_ERROR",
                    stage=ErrorStage.STAGE1_GHIBLI,
                    detail=res.get("msg", "External API error"),
                ).model_dump(by_alias=True),
            )

        task_id_1 = res["data"]["taskId"]
        future_1 = asyncio.get_running_loop().create_future()
        pending_tasks[task_id_1] = future_1

        # Generate QR lock image while Stage 1 runs.
        # Pure local PIL work — thread keeps event loop free (~0.5s).
        _qr_url = request.url
        _t_qr = _time.monotonic()

        def _gen_qr_lock():
            img = get_qr(_qr_url)
            if max(img.size) > 1024:
                img.thumbnail((1024, 1024), _PILImage.LANCZOS)
            filename = f"qrlock_{uuid4()}.jpg"
            img.save(s.TMP_PATH / filename, format="JPEG", quality=92, optimize=True)
            return s.DOMAIN + "/tmp/" + filename

        qr_lock_url_path = await asyncio.to_thread(_gen_qr_lock)
        _log.info("[%s] QR lock generated in %.2fs — saved as qrlock_*.jpg size=%dpx", _req_id, _time.monotonic() - _t_qr, 645)

        _t_s1_wait = _time.monotonic()
        try:
            webhook_result_1: CallbackRequest = await asyncio.wait_for(future_1, timeout=600)
            _log.info("[%s] Stage 1 webhook received in %.1fs — taskId=%s", _req_id, _time.monotonic() - _t_s1_wait, task_id_1)
        except asyncio.TimeoutError:
            pending_tasks.pop(task_id_1, None)
            _log.warning("[%s] Stage 1 TIMEOUT after %.0fs — taskId=%s", _req_id, _time.monotonic() - _t_s1_wait, task_id_1)
            return JSONResponse(
                status_code=504,
                content=external_error_response(
                    message="Stage 1 timeout",
                    code="STAGE1_TIMEOUT",
                    stage=ErrorStage.STAGE1_GHIBLI,
                    detail=f"Stage 1 timed out after 10 minutes (taskId: {task_id_1})",
                ).model_dump(by_alias=True),
            )
        finally:
            pending_tasks.pop(task_id_1, None)

        if webhook_result_1.is_failure:
            return JSONResponse(
                status_code=500,
                content=external_error_response(
                    message="Stage 1 task failed",
                    code="STAGE1_TASK_FAILED",
                    stage=ErrorStage.STAGE1_GHIBLI,
                    detail=webhook_result_1.data.failMsg or webhook_result_1.msg,
                ).model_dump(by_alias=True),
            )

        _s1_urls = webhook_result_1.data.get_result_urls() or []
        ghibli_url = _s1_urls[0] if _s1_urls else None
        stage1_cost = webhook_result_1.data.costTime

        stage2_validation = validate_stage2_input(ghibli_url)
        if not stage2_validation.ok:
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message=stage2_validation.message,
                    stage=ErrorStage.STAGE2_QR,
                ).model_dump(by_alias=True),
            )

        # =====================================================================
        # RE-HOST Stage 1 output: async download + PIL in thread (~0.5s)
        # Qwen CDN URL is temporary and often times out when Seedream pulls it.
        # =====================================================================
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                ghibli_resp = await client.get(ghibli_url, headers=_DOWNLOAD_HEADERS)
                ghibli_resp.raise_for_status()
            _s1_content = ghibli_resp.content

            def _save_stage1():
                img = _PILImage.open(_io.BytesIO(_s1_content)).convert("RGB")
                if max(img.size) > 1024:
                    img.thumbnail((1024, 1024), _PILImage.LANCZOS)
                filename = f"stage1_{uuid4()}.jpg"
                img.save(s.TMP_PATH / filename, format="JPEG", quality=92, optimize=True)
                _save_local_copy(img, filename)
                return img, s.DOMAIN + "/tmp/" + filename

            ghibli_img, ghibli_local_url = await asyncio.to_thread(_save_stage1)
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message=f"Failed to re-host Stage 1 output: {str(e)}",
                    stage=ErrorStage.STAGE2_QR,
                ).model_dump(by_alias=True)
            )

        # =====================================================================
        # STAGE 2: Compose Ghibli + QR lock (native async HTTP) — up to 3 attempts
        # Retry ONLY when QR payload is not detected at all in the merged image.
        # =====================================================================
        last_result_urls = None
        last_webhook_result_2 = None
        last_qr_validation = None
        stage2_cost = 0

        _stage2_prompt = s.PROMPT_GHIBLI_LOCK
        if skin_color_hex:
            _stage2_prompt += (
                f"\n\nEXACT SKIN COLOR: {skin_color_hex}. "
                "This is the person's real measured skin tone from the first image. "
                "You MUST reproduce this exact color on all skin in the composed image. "
                "Do NOT lighten, darken, whiten, or shift this color in any direction."
            )

        for attempt in range(1, 4):
            _t_s2_submit = _time.monotonic()
            _log.info("[%s] Stage 2 attempt %d/3 — model=%s", _req_id, attempt, s.COMPOSE_MODEL)
            res2 = await _submit_generation(
                [ghibli_local_url, qr_lock_url_path], _stage2_prompt, model=s.COMPOSE_MODEL
            )
            _log.info("[%s] Stage 2 attempt %d submit done in %.2fs — code=%s", _req_id, attempt, _time.monotonic() - _t_s2_submit, res2.get("code"))
            if res2.get("code") != 200:
                return JSONResponse(
                    status_code=500,
                    content=external_error_response(
                        message="Stage 2 (composition) API error",
                        code="STAGE2_API_ERROR",
                        stage=ErrorStage.STAGE2_QR,
                        detail=res2.get("msg", "External API error"),
                    ).model_dump(by_alias=True),
                )

            task_id_2 = res2["data"]["taskId"]
            future_2 = asyncio.get_running_loop().create_future()
            pending_tasks[task_id_2] = future_2

            _t_s2_wait = _time.monotonic()
            try:
                webhook_result_2: CallbackRequest = await asyncio.wait_for(future_2, timeout=600)
                _log.info("[%s] Stage 2 attempt %d webhook received in %.1fs — taskId=%s costTime=%s", _req_id, attempt, _time.monotonic() - _t_s2_wait, task_id_2, getattr(webhook_result_2.data, "costTime", "?"))
            except asyncio.TimeoutError:
                pending_tasks.pop(task_id_2, None)
                _log.warning(
                    "[%s] Stage 2 attempt %d/3 timed out after %.0fs — taskId=%s",
                    _req_id, attempt, _time.monotonic() - _t_s2_wait, task_id_2,
                )
                if attempt < 3:
                    _log.warning(
                        "[%s] Stage 2 retrying with fresh task (attempt %d/3 exhausted)",
                        _req_id, attempt,
                    )
                    continue
                _log.error("[%s] Stage 2 retry budget exhausted — all 3 attempts timed out", _req_id)
                return JSONResponse(
                    status_code=504,
                    content=external_error_response(
                        message="Stage 2 timeout",
                        code="STAGE2_TIMEOUT",
                        stage=ErrorStage.STAGE2_QR,
                        detail=f"Stage 2 timed out on all 3 attempts (last taskId: {task_id_2})",
                    ).model_dump(by_alias=True),
                )
            finally:
                pending_tasks.pop(task_id_2, None)

            if webhook_result_2.is_failure:
                return JSONResponse(
                    status_code=500,
                    content=external_error_response(
                        message="Stage 2 task failed",
                        code="STAGE2_TASK_FAILED",
                        stage=ErrorStage.STAGE2_QR,
                        detail=webhook_result_2.data.failMsg or webhook_result_2.msg,
                    ).model_dump(by_alias=True),
                )

            result_urls = webhook_result_2.data.get_result_urls() or []
            final_image_url = result_urls[0] if result_urls else None

            if not final_image_url:
                last_result_urls = result_urls
                last_webhook_result_2 = webhook_result_2
                last_qr_validation = QRValidationResult(
                    ok=False,
                    detected_payload=None,
                    expected_payload=request.url,
                    reason="no result url returned by stage 2",
                )
                stage2_cost += webhook_result_2.data.costTime
                continue

            # --- Attempt local re-host of final image ---
            _rehosted_img = None
            _rehosted_url = None
            try:
                _rehosted_img, _rehosted_url = await _rehost_stage2(final_image_url)
                _log.info("Stage 2 re-hosted: %s", _rehosted_url)
            except Exception as _rh_err:
                _log.warning("Stage 2 re-host failed, falling back to provider URL: %s", _rh_err)

            if _rehosted_img is not None and _rehosted_url is not None:
                # Validate from in-memory PIL image — no duplicate download
                qr_validation = await asyncio.to_thread(
                    validate_qr_from_image,
                    img=_rehosted_img,
                    expected_payload=request.url,
                )
                response_url = _rehosted_url
            else:
                # Fallback: validate directly from the original provider URL
                qr_validation = await asyncio.to_thread(
                    validate_qr_from_image_url,
                    image_url=final_image_url,
                    expected_payload=request.url,
                )
                response_url = final_image_url

            last_result_urls = [response_url]
            last_webhook_result_2 = webhook_result_2
            last_qr_validation = qr_validation
            stage2_cost += webhook_result_2.data.costTime

            _log.info(
                "[%s] Stage 2 attempt %d QR validation — ok=%s detected=%s reason=%s",
                _req_id, attempt, qr_validation.ok,
                repr(qr_validation.detected_payload), qr_validation.reason,
            )

            if qr_validation.ok:
                break

            if (
                qr_validation.detected_payload is None
                and qr_validation.reason == "no valid qr payload detected in merged image"
            ):
                _log.info("[%s] QR not detected — retrying (attempt %d/3)", _req_id, attempt)
                continue

            # Payload mismatch or any other reason — do not retry
            _log.warning("[%s] QR mismatch — not retrying (attempt %d/3): %s", _req_id, attempt, qr_validation.reason)
            break

        _total = _time.monotonic() - _t0
        _qr_ok = last_qr_validation.ok if last_qr_validation else False
        _log.info(
            "[%s] Pipeline DONE in %.1fs — qr_ok=%s s1_cost=%ss s2_cost=%ss pending_tasks=%d",
            _req_id, _total, _qr_ok, stage1_cost, stage2_cost, len(pending_tasks),
        )
        return JSONResponse(
            content=success_response(
                message=(
                    "Ghibli + QR pipeline completed successfully"
                    if last_qr_validation and last_qr_validation.ok
                    else "Ghibli + QR pipeline completed, but QR validation failed"
                ),
                data={
                    "resultUrls": last_result_urls,
                    "stage1Url": ghibli_local_url,
                    "model": last_webhook_result_2.data.model if last_webhook_result_2 else None,
                    "costTime": stage1_cost + stage2_cost,
                    "quality": "basic",
                    "aspectRatio": "1:1",
                    "skinColor": skin_color_hex,
                    "qrValidation": {
                        "ok": last_qr_validation.ok if last_qr_validation else False,
                        "expectedPayload": request.url,
                        "detectedPayload": last_qr_validation.detected_payload if last_qr_validation else None,
                        "reason": last_qr_validation.reason if last_qr_validation else "qr validation did not run",
                    },
                },
            ).model_dump(by_alias=True)
        )

    except asyncio.CancelledError:
        raise
    except Exception as e:
        _log.exception("[pipeline] Unhandled exception in automated_pipeline: %s", e)
        return JSONResponse(
            status_code=500,
            content=internal_error_response(
                message=f"Unexpected error: {str(e)}",
                stage=ErrorStage.ORCHESTRATION,
            ).model_dump(by_alias=True),
        )
