"""
API Routes - Ghibli Portrait API V1

This module implements Layer 4 (Orchestration) of the validation architecture.
It coordinates validation layers and stages without adding new validation rules.

Swagger Tags:
- Api Production: Primary production endpoint (POST /v1/ghibli-qr)
- Core APIs: Core transformation endpoints
- Internal / System: Internal webhooks and system endpoints
- Health & Utilities: Health checks and utility endpoints
"""

import asyncio
import io as _io
import json
import time
from typing import Dict
from uuid import uuid4

import requests as _requests
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter
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
from src.ghibli_portrait.services.identity_check import (
    check_identity_drift,
    check_identity_drift_from_url,
)
from src.ghibli_portrait.services.image_service import generate_img
from src.ghibli_portrait.services.qr_service import get_qr
from src.ghibli_portrait.services.validation_service import (
    validate_single_image_url_list,
    validate_real_human_image,
    validate_source_resolution,
    validate_image_accessibility,
    validate_stage1_human_portrait,
    validate_stage2_input,
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
            data={
                "status": "healthy",
            }
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
            data={
                "url": short_data.url,
                "code": short_data.code
            }
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
        f"Powered by {s.KIE_GHIBLI_MODEL}. "
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
    """
    Transform portrait to Ghibli style with comprehensive validation.
    Model: qwen/image-edit (explicitly set, no fallback)
    """
    try:
        # Layer 0: Schema validation (handled by Pydantic)

        # Validation gate: Single image check
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

        # Layers 1, 2, 3A: Comprehensive validation
        validation_result = validate_real_human_image(request.img_urls[0], settings=s)
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

        # Configuration check
        if not s.KIE_GHIBLI_MODEL:
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message="KIE_GHIBLI_MODEL not configured",
                    stage=ErrorStage.ORCHESTRATION,
                ).model_dump(by_alias=True)
            )

        # Generate image with explicit model
        res = generate_img(**request.model_dump(), model=s.KIE_GHIBLI_MODEL)

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

        # Webhook wait (async flow)
        future = asyncio.get_event_loop().create_future()
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

            # Parse result — param wrapper differs by model (Qwen nests under "input", Flux Kontext is flat)
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

        except Exception as e:
            pending_tasks.pop(task_id, None)
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message=f"Unexpected error: {str(e)}",
                    stage=ErrorStage.STAGE1_GHIBLI,
                ).model_dump(by_alias=True)
            )

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

        img = get_qr(
            url=short_url_data.url if req.shorten_url is True else req.url,
            version=req.version
        )

        filename = f"{uuid4()}.png"
        filepath = s.TMP_PATH / filename
        img.save(filepath)

        url_path = s.DOMAIN + "/tmp/" + filename

        response_data = {
            "qrUrl": url_path,
            "encodedUrl": req.url,
        }

        if short_url_data:
            response_data["shortUrl"] = {
                "url": short_url_data.url,
                "code": short_url_data.code
            }

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

@router.post(
    "/ghibli/callback",
    tags=["Internal / System"],
    summary="Webhook callback (internal use only)",
    description=(
        "Receives automatic notifications from KIE API when tasks complete. "
        "**For KIE API use only - do not call directly.**"
    ),
    include_in_schema=True,
    response_model=ApiSuccessResponse,
    responses={
        500: {"model": ApiErrorResponse, "description": "Task execution failure reported by KIE API"},
    },
)
async def webhook(req: CallbackRequest):
    """
    Internal webhook endpoint for KIE API callbacks.
    This endpoint maintains the async task completion flow.
    """
    task_id = req.data.taskId
    if task_id in pending_tasks:
        future = pending_tasks[task_id]
        pending_tasks.pop(task_id)

        if not future.done():
            future.set_result(req)

    if req.is_failure:
        return JSONResponse(
            status_code=req.code,
            content=external_error_response(
                message="Task execution failed",
                code=req.data.failCode or "TASK_FAILED",
                stage=ErrorStage.ORCHESTRATION,
                detail=req.data.failMsg or req.msg,
            ).model_dump(by_alias=True)
        )

    return JSONResponse(
        content=success_response(
            message="Webhook received successfully",
            data={"taskId": req.data.taskId},
        ).model_dump(by_alias=True)
    )


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


# ============================================================================
# API PRODUCTION (Primary Endpoint)
# ============================================================================

@router.post(
    "/ghibli-qr",
    tags=["Api Production"],
    summary="Ghibli + QR Pipeline (Production Endpoint)",
    description=(
        "**Primary production endpoint.** "
        "Two-stage pipeline: (1) Transform portrait to Ghibli style, "
        "(2) Compose with QR code lock screen. "
        f"Stage 1: {s.KIE_GHIBLI_MODEL}, Stage 2: {s.KIE_COMPOSE_MODEL}. "
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
    - Does NOT add new validation rules
    - Does NOT reinterpret errors from lower layers

    Models: qwen/image-edit (stage 1), seedream (stage 2) - both explicit, no fallback.
    """
    task_id_1 = ''
    task_id_2 = ''
    _timings: dict = {}
    _pipeline_start = time.perf_counter()

    try:
        # Layer 0: Schema validation (handled by Pydantic)

        # Layer 1: URL validation (source resolution)
        _t = time.perf_counter()
        _l1 = validate_source_resolution(request.img_url)
        _timings["validation_url_check"] = round(time.perf_counter() - _t, 3)
        if not _l1.ok:
            return JSONResponse(
                status_code=422,
                content=validation_error_response(
                    field="imgUrl",
                    message=_l1.message,
                    code=_l1.code,
                    stage=_l1.stage,
                    error_type=_l1.error_type,
                ).model_dump(by_alias=True)
            )

        # Layer 2: Image download & decode
        _t = time.perf_counter()
        _l2 = validate_image_accessibility(request.img_url)
        _timings["validation_download"] = round(time.perf_counter() - _t, 3)
        if not _l2.ok:
            return JSONResponse(
                status_code=422,
                content=validation_error_response(
                    field="imgUrl",
                    message=_l2.message,
                    code=_l2.code,
                    stage=ErrorStage.SOURCE_RESOLUTION,
                    error_type=ErrorType.VALIDATION_ERROR,
                ).model_dump(by_alias=True)
            )

        # Layer 3A: Face detection (MediaPipe)
        _t = time.perf_counter()
        _l3a = validate_stage1_human_portrait(_l2.image, request.img_url, settings=s)
        _timings["validation_face_detect"] = round(time.perf_counter() - _t, 3)
        if not _l3a.ok:
            return JSONResponse(
                status_code=422,
                content=validation_error_response(
                    field="imgUrl",
                    message=_l3a.message,
                    code=_l3a.code,
                    stage=_l3a.stage,
                    error_type=_l3a.error_type,
                ).model_dump(by_alias=True)
            )

        _timings["validation"] = round(
            _timings["validation_url_check"]
            + _timings["validation_download"]
            + _timings["validation_face_detect"],
            3,
        )

        # Configuration checks
        if not s.KIE_GHIBLI_MODEL:
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message="KIE_GHIBLI_MODEL not configured",
                    stage=ErrorStage.ORCHESTRATION,
                ).model_dump(by_alias=True)
            )

        if not s.KIE_COMPOSE_MODEL:
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message="KIE_COMPOSE_MODEL not configured",
                    stage=ErrorStage.ORCHESTRATION,
                ).model_dump(by_alias=True)
            )

        # =====================================================================
        # STAGE 1: Generate Ghibli portrait (qwen/image-edit)
        # =====================================================================
        _t = time.perf_counter()
        res = generate_img(
            [request.img_url],
            s.PROMPT_PIC_TO_GHIBLI,
            model=s.KIE_GHIBLI_MODEL,
            negative_prompt=s.NEGATIVE_PROMPT_PIC_TO_GHIBLI,
        )
        _timings["stage1_api_submit"] = round(time.perf_counter() - _t, 3)
        if res["code"] != 200:
            return JSONResponse(
                status_code=500,
                content=external_error_response(
                    message="Stage 1 (Ghibli generation) API error",
                    code="STAGE1_API_ERROR",
                    stage=ErrorStage.STAGE1_GHIBLI,
                    detail=res.get("msg", "External API error"),
                ).model_dump(by_alias=True)
            )

        task_id_1 = res["data"]["taskId"]
        future = asyncio.get_event_loop().create_future()

        # Generate QR code while waiting for Stage 1 (parallel optimization).
        # Resize to max 1024px and save as JPEG — the full-size 2304×1728 PNG
        # was timing out when KIE Stage 2 tried to download it over the tunnel.
        _t = time.perf_counter()
        img = get_qr(request.url)
        if max(img.size) > 1024:
            img.thumbnail((1024, 1024), _PILImage.LANCZOS)
        filename = f"{uuid4()}.jpg"
        filepath = s.TMP_PATH / filename
        img.save(filepath, format="JPEG", quality=92, optimize=True)
        qr_lock_url_path = s.DOMAIN + "/tmp/" + filename
        _timings["qr_generation"] = round(time.perf_counter() - _t, 3)

        pending_tasks[task_id_1] = future

        _t = time.perf_counter()
        try:
            webhook_result = await asyncio.wait_for(future, timeout=300)
        except asyncio.TimeoutError:
            pending_tasks.pop(task_id_1, None)
            return JSONResponse(
                status_code=504,
                content=external_error_response(
                    message="Stage 1 timeout",
                    code="STAGE1_TIMEOUT",
                    stage=ErrorStage.STAGE1_GHIBLI,
                    detail=f"Stage 1 timed out after 5 minutes (taskId: {task_id_1})",
                ).model_dump(by_alias=True)
            )
        finally:
            pending_tasks.pop(task_id_1, None)
        _timings["stage1_wait"] = round(time.perf_counter() - _t, 3)

        if webhook_result.is_failure:
            return JSONResponse(
                status_code=500,
                content=external_error_response(
                    message="Stage 1 task failed",
                    code="STAGE1_TASK_FAILED",
                    stage=ErrorStage.STAGE1_GHIBLI,
                    detail=webhook_result.data.failMsg or webhook_result.msg,
                ).model_dump(by_alias=True)
            )

        # Extract Stage 1 output URL — handles both Qwen (resultUrls[]) and Flux Kontext (info.resultImageUrl)
        _s1_urls = webhook_result.data.get_result_urls() or []
        ghibli_url = _s1_urls[0] if _s1_urls else None
        to_ghibli_cost_time = webhook_result.data.costTime

        # =====================================================================
        # LAYER 3B: Stage 2 input validation
        # CRITICAL: Stage 1 output is TRUSTED - NO reprocessing
        # =====================================================================
        stage2_validation = validate_stage2_input(ghibli_url)
        if not stage2_validation.ok:
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message=stage2_validation.message,
                    stage=ErrorStage.STAGE2_QR,
                ).model_dump(by_alias=True)
            )

        # =====================================================================
        # RE-HOST Stage 1 output on our server before passing to Stage 2.
        # The Qwen CDN URL (tempfile.aiquickdraw.com) is temporary and often
        # times out when Seedream tries to pull it cross-service.
        # We also resize to max 1024px and save as JPEG so the file is small
        # enough for KIE's download to complete within their internal timeout
        # window (large PNG over a tunneled connection was timing out mid-stream
        # even when our server returned 200 OK).
        # =====================================================================
        _t = time.perf_counter()
        try:
            ghibli_resp = _requests.get(
                ghibli_url, timeout=60, headers={"User-Agent": "ghibli-qr/0.1"}
            )
            ghibli_resp.raise_for_status()
            ghibli_img = _PILImage.open(_io.BytesIO(ghibli_resp.content)).convert("RGB")
            if max(ghibli_img.size) > 1024:
                ghibli_img.thumbnail((1024, 1024), _PILImage.LANCZOS)
            ghibli_filename = f"{uuid4()}.jpg"
            ghibli_filepath = s.TMP_PATH / ghibli_filename
            ghibli_img.save(ghibli_filepath, format="JPEG", quality=92, optimize=True)
            ghibli_local_url = s.DOMAIN + "/tmp/" + ghibli_filename
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message=f"Failed to re-host Stage 1 output: {str(e)}",
                    stage=ErrorStage.STAGE2_QR,
                ).model_dump(by_alias=True)
            )
        _timings["stage1_rehost"] = round(time.perf_counter() - _t, 3)

        # =====================================================================
        # IDENTITY DRIFT GUARD (controlled by ENABLE_IDENTITY_CHECK env var)
        # Disabled by default — qwen/image-edit always triggers skin_tone_drift.
        # Re-enable once a proper img2img model (flux-kontext-pro) is active.
        # =====================================================================
        _t = time.perf_counter()
        if s.ENABLE_IDENTITY_CHECK:
            _t_dl = time.perf_counter()
            try:
                _id_resp = _requests.get(
                    request.img_url, timeout=15, headers={"User-Agent": "ghibli-qr/0.1"}
                )
                _id_resp.raise_for_status()
                _identity_source = _PILImage.open(_io.BytesIO(_id_resp.content)).convert("RGB")
                _timings["identity_src_download"] = round(time.perf_counter() - _t_dl, 3)
            except Exception:
                _identity_source = None
                _timings["identity_src_download"] = round(time.perf_counter() - _t_dl, 3)

            _t_det = time.perf_counter()
            identity_result = (
                check_identity_drift(_identity_source, ghibli_img)
                if _identity_source is not None
                else check_identity_drift_from_url(request.img_url, ghibli_img)
            )
            _timings["identity_face_detect"] = round(time.perf_counter() - _t_det, 3)
        else:
            identity_result = None
            _timings["identity_src_download"] = 0
            _timings["identity_face_detect"] = 0
        _timings["identity_check"] = round(time.perf_counter() - _t, 3)
        if s.ENABLE_IDENTITY_CHECK and identity_result.drift_detected:
            retry_accepted = False
            retry_res = generate_img(
                [request.img_url],
                s.PROMPT_PIC_TO_GHIBLI,
                model=s.KIE_GHIBLI_MODEL,
                negative_prompt=s.NEGATIVE_PROMPT_PIC_TO_GHIBLI,
            )
            if retry_res["code"] == 200:
                task_id_retry = retry_res["data"]["taskId"]
                retry_future = asyncio.get_event_loop().create_future()
                pending_tasks[task_id_retry] = retry_future
                try:
                    retry_webhook = await asyncio.wait_for(retry_future, timeout=300)
                    if not retry_webhook.is_failure:
                        _retry_urls = retry_webhook.data.get_result_urls() or []
                        retry_url = _retry_urls[0] if _retry_urls else None
                        retry_resp = _requests.get(
                            retry_url, timeout=60, headers={"User-Agent": "ghibli-qr/0.1"}
                        )
                        retry_resp.raise_for_status()
                        retry_img = _PILImage.open(_io.BytesIO(retry_resp.content)).convert("RGB")
                        if max(retry_img.size) > 1024:
                            retry_img.thumbnail((1024, 1024), _PILImage.LANCZOS)
                        retry_filename = f"{uuid4()}.jpg"
                        (s.TMP_PATH / retry_filename).parent.mkdir(parents=True, exist_ok=True)
                        retry_img.save(s.TMP_PATH / retry_filename, format="JPEG", quality=92, optimize=True)
                        retry_identity = check_identity_drift_from_url(request.img_url, retry_img)
                        if not retry_identity.drift_detected:
                            ghibli_local_url = s.DOMAIN + "/tmp/" + retry_filename
                            to_ghibli_cost_time += retry_webhook.data.costTime
                            retry_accepted = True
                except (asyncio.TimeoutError, Exception):
                    pass
                finally:
                    pending_tasks.pop(task_id_retry, None)

            if not retry_accepted:
                return JSONResponse(
                    status_code=500,
                    content=external_error_response(
                        message="Stage 1 could not preserve the subject's identity",
                        code="IDENTITY_DRIFT_DETECTED",
                        stage=ErrorStage.STAGE1_GHIBLI,
                        detail=f"Identity drift: {identity_result.reason if identity_result else 'unknown'}",
                    ).model_dump(by_alias=True)
                )

        # =====================================================================
        # STAGE 2: Compose Ghibli + QR lock (seedream)
        # =====================================================================
        _t = time.perf_counter()
        res = generate_img([ghibli_local_url, qr_lock_url_path], s.PROMPT_GHIBLI_LOCK, model=s.KIE_COMPOSE_MODEL)
        _timings["stage2_api_submit"] = round(time.perf_counter() - _t, 3)
        if res["code"] != 200:
            return JSONResponse(
                status_code=500,
                content=external_error_response(
                    message="Stage 2 (composition) API error",
                    code="STAGE2_API_ERROR",
                    stage=ErrorStage.STAGE2_QR,
                    detail=res.get("msg", "External API error"),
                ).model_dump(by_alias=True)
            )

        task_id_2 = res["data"]["taskId"]
        future = asyncio.get_event_loop().create_future()
        pending_tasks[task_id_2] = future

        _t = time.perf_counter()
        try:
            webhook_result = await asyncio.wait_for(future, timeout=300)
        except asyncio.TimeoutError:
            pending_tasks.pop(task_id_2, None)
            return JSONResponse(
                status_code=504,
                content=external_error_response(
                    message="Stage 2 timeout",
                    code="STAGE2_TIMEOUT",
                    stage=ErrorStage.STAGE2_QR,
                    detail=f"Stage 2 timed out after 5 minutes (taskId: {task_id_2})",
                ).model_dump(by_alias=True)
            )
        finally:
            pending_tasks.pop(task_id_2, None)
        _timings["stage2_wait"] = round(time.perf_counter() - _t, 3)

        if webhook_result.is_failure:
            return JSONResponse(
                status_code=500,
                content=external_error_response(
                    message="Stage 2 task failed",
                    code="STAGE2_TASK_FAILED",
                    stage=ErrorStage.STAGE2_QR,
                    detail=webhook_result.data.failMsg or webhook_result.msg,
                ).model_dump(by_alias=True)
            )

        # Success: Return final composed image
        params = json.loads(json.loads(webhook_result.data.param)["input"])
        result_urls = json.loads(webhook_result.data.resultJson)["resultUrls"]

        _timings["stage1_model_ms"] = to_ghibli_cost_time
        _timings["stage2_model_ms"] = webhook_result.data.costTime
        _timings["total"] = round(time.perf_counter() - _pipeline_start, 3)

        return JSONResponse(
            content=success_response(
                message="Ghibli + QR pipeline completed successfully",
                data={
                    "resultUrls": result_urls,
                    "model": webhook_result.data.model,
                    "costTime": webhook_result.data.costTime + to_ghibli_cost_time,
                    "quality": params.get("quality", "basic"),
                    "aspectRatio": params.get("aspect_ratio", "1:1"),
                    "timings": _timings,
                },
            ).model_dump(by_alias=True)
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=internal_error_response(
                message=f"Unexpected error: {str(e)}",
                stage=ErrorStage.ORCHESTRATION,
            ).model_dump(by_alias=True)
        )
