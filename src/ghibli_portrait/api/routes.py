import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter

from src.ghibli_portrait.api.responses import (
    success_response,
    error_response,
    validation_error_response,
    internal_error_response,
)
from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import (
    CallbackRequest,
    GhibliQRRequest,
    Image2GhibliRequest,
    QRLockRequest,
    ApiError,
)
from src.ghibli_portrait.services.image_service import generate_img
from src.ghibli_portrait.services.qr_service import get_qr
from src.ghibli_portrait.services.validation_service import (
    validate_human_face,
    validate_public_url,
    validate_single_image_url_list,
    validate_real_human_image,
)
from src.ghibli_portrait.utils.url_utils import shorten

router = APIRouter(prefix="/v1", tags=["v1"])
pending_tasks: Dict[str, asyncio.Future] = {}
s = Settings()


@router.get(
    "/health",
    summary="Service health check",
    description="Returns service status and timestamp",
)
async def health():
    """Health check endpoint for monitoring and liveness probes"""
    return JSONResponse(
        content=success_response(
            message="Ghibli Portrait API V1 is running",
            data={
                "status": "healthy",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        ).model_dump()
    )


@router.post(
    "/ghibli",
    summary="Transform portrait to Ghibli style",
    description=(
        "Transform a single portrait image to Ghibli-style art. "
        f"Powered by {s.KIE_GHIBLI_MODEL}. "
        "Validation: public URL, single image, face detection, human verification."
    ),
)
async def transform2ghibli(request: Image2GhibliRequest):
    """
    Transform portrait to Ghibli style with comprehensive validation.
    Model: qwen/image-edit (explicitly set, no fallback)
    """
    request_id = str(uuid.uuid4())

    try:
        # Validation gate: Single image check
        v = validate_single_image_url_list(request.img_urls)
        if not v.ok:
            return JSONResponse(
                status_code=422,
                content=validation_error_response(
                    field="imgUrls",
                    message=v.reason,
                    code="SINGLE_IMAGE_REQUIRED",
                    request_id=request_id
                ).model_dump()
            )

        # Comprehensive validation gate
        validation_result = validate_real_human_image(request.img_urls[0], settings=s)
        if not validation_result.ok:
            return JSONResponse(
                status_code=422,
                content=validation_error_response(
                    field="imgUrls",
                    message=validation_result.message,
                    code=validation_result.code,
                    request_id=request_id
                ).model_dump()
            )

        # Explicit model check (no fallback allowed)
        if not s.KIE_GHIBLI_MODEL:
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message="KIE_GHIBLI_MODEL not configured",
                    request_id=request_id
                ).model_dump()
            )

        # Generate image with explicit model
        res = generate_img(**request.model_dump(), model=s.KIE_GHIBLI_MODEL)

        if res["code"] != 200:
            return JSONResponse(
                status_code=500,
                content=error_response(
                    message="Image generation API error",
                    errors=[ApiError(
                        code="GENERATION_API_ERROR",
                        field=None,
                        message=res.get("msg", "External API returned an error")
                    )],
                    request_id=request_id
                ).model_dump()
            )

        task_id = res["data"]["taskId"]

        # Webhook wait (async flow unchanged)
        future = asyncio.get_event_loop().create_future()
        pending_tasks[task_id] = future

        try:
            webhook_result: CallbackRequest = await asyncio.wait_for(future, timeout=300)

            if webhook_result.is_failure:
                return JSONResponse(
                    status_code=500,
                    content=error_response(
                        message="Image generation task failed",
                        errors=[ApiError(
                            code="GENERATION_TASK_FAILED",
                            field=None,
                            message=webhook_result.data.failMsg or webhook_result.msg
                        )],
                        request_id=request_id
                    ).model_dump()
                )

            # Parse result (logic unchanged)
            params = json.loads(json.loads(webhook_result.data.param)["input"])
            result_urls = json.loads(webhook_result.data.resultJson)["resultUrls"]

            # Return unified response (camelCase will be applied in schema update)
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
                    request_id=request_id
                ).model_dump()
            )

        except asyncio.TimeoutError:
            pending_tasks.pop(task_id, None)
            return JSONResponse(
                status_code=504,
                content=error_response(
                    message="Request timeout",
                    errors=[ApiError(
                        code="WEBHOOK_TIMEOUT",
                        field=None,
                        message=f"Task timed out after 5 minutes (taskId: {task_id})"
                    )],
                    request_id=request_id
                ).model_dump()
            )

        except Exception as e:
            pending_tasks.pop(task_id, None)
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message=f"Unexpected error: {str(e)}",
                    request_id=request_id
                ).model_dump()
            )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=internal_error_response(
                message=f"Unexpected error: {str(e)}",
                request_id=request_id
            ).model_dump()
        )


@router.post(
    "/ghibli/callback",
    summary="Webhook callback (internal use only)",
    description=(
        "Receives automatic notifications from KIE API when tasks complete. "
        "**For KIE API use only - do not call directly.**"
    ),
)
async def webhook(req: CallbackRequest):
    """
    Internal webhook endpoint for KIE API callbacks.
    This endpoint maintains the async task completion flow.
    Logic unchanged - no unified response wrapper needed (internal endpoint).
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
            content=req.model_dump(),
        )

    return req


@router.post(
    "/qr-lock",
    summary="Generate QR code with lock screen overlay",
    description="Generates a QR code image with lock screen background and optional URL shortening",
)
async def get_qr_lock(req: QRLockRequest):
    """Generate QR code with lock screen overlay"""
    request_id = str(uuid.uuid4())

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
                request_id=request_id
            ).model_dump()
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=internal_error_response(
                message=f"QR generation failed: {str(e)}",
                request_id=request_id
            ).model_dump()
        )


@router.delete(
    "/qr-lock/{img_id}",
    summary="Delete QR code image",
    description="Deletes a previously generated QR code image by its filename",
)
async def delete_qr_lock(img_id: str):
    """Delete a temporary QR code image"""
    request_id = str(uuid.uuid4())

    img_id = img_id.replace(".png", "")
    imgpath = s.TMP_PATH / f"{img_id}.png"

    if not imgpath.exists():
        return JSONResponse(
            status_code=404,
            content=error_response(
                message="Image not found",
                errors=[ApiError(
                    code="IMAGE_NOT_FOUND",
                    field="imgId",
                    message=f"Image {img_id} does not exist"
                )],
                request_id=request_id
            ).model_dump()
        )

    imgpath.unlink()

    return JSONResponse(
        content=success_response(
            message="Image deleted successfully",
            data={"deletedId": img_id},
            request_id=request_id
        ).model_dump()
    )

@router.get(
    "/qr-url/",
    summary="Get shortened URL (deterministic)",
    description=(
        "Returns a shortened URL using deterministic hashing. "
        "The same URL always produces the same short code. "
        "No validation performed - works for any URL string."
    ),
)
async def get_short_url(url: str):
    """Get or generate shortened URL using deterministic hashing"""
    request_id = str(uuid.uuid4())

    short_data = shorten(url)

    return JSONResponse(
        content=success_response(
            message="Short URL generated successfully",
            data={
                "url": short_data.url,
                "code": short_data.code
            },
            request_id=request_id
        ).model_dump()
    )

@router.post(
    "/ghibli-qr",
    summary="Automated Ghibli + QR pipeline",
    description=(
        "Two-stage pipeline: (1) Transform portrait to Ghibli style, "
        "(2) Merge with QR code lock screen. "
        f"Stage 1: {s.KIE_GHIBLI_MODEL}, Stage 2: {s.KIE_COMPOSE_MODEL}"
    ),
)
async def automated_pipeline(request: GhibliQRRequest):
    """
    Automated two-stage pipeline for Ghibli + QR composition.
    Models: qwen/image-edit (stage 1), seedream (stage 2) - both explicit, no fallback.
    """
    request_id = str(uuid.uuid4())
    task_id_1 = ''
    task_id_2 = ''

    try:
        # Comprehensive validation gate
        validation_result = validate_real_human_image(request.img_url, settings=s)
        if not validation_result.ok:
            return JSONResponse(
                status_code=422,
                content=validation_error_response(
                    field="imgUrl",
                    message=validation_result.message,
                    code=validation_result.code,
                    request_id=request_id
                ).model_dump()
            )

        # Explicit model checks (no fallbacks)
        if not s.KIE_GHIBLI_MODEL:
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message="KIE_GHIBLI_MODEL not configured",
                    request_id=request_id
                ).model_dump()
            )

        if not s.KIE_COMPOSE_MODEL:
            return JSONResponse(
                status_code=500,
                content=internal_error_response(
                    message="KIE_COMPOSE_MODEL not configured",
                    request_id=request_id
                ).model_dump()
            )

        # Stage 1: Generate Ghibli portrait (qwen/image-edit)
        res = generate_img([request.img_url], s.PROMPT_PIC_TO_GHIBLI, model=s.KIE_GHIBLI_MODEL)
        if res["code"] != 200:
            return JSONResponse(
                status_code=500,
                content=error_response(
                    message="Stage 1 (Ghibli generation) API error",
                    errors=[ApiError(
                        code="STAGE1_API_ERROR",
                        field=None,
                        message=res.get("msg", "External API error")
                    )],
                    request_id=request_id
                ).model_dump()
            )

        task_id_1 = res["data"]["taskId"]
        future = asyncio.get_event_loop().create_future()

        # Generate QR code while waiting for stage 1
        img = get_qr(request.url)
        filename = f"{uuid4()}.png"
        filepath = s.TMP_PATH / filename
        img.save(filepath)
        qr_lock_url_path = s.DOMAIN + "/tmp/" + filename

        pending_tasks[task_id_1] = future

        try:
            webhook_result = await asyncio.wait_for(future, timeout=300)
        except asyncio.TimeoutError:
            pending_tasks.pop(task_id_1, None)
            return JSONResponse(
                status_code=504,
                content=error_response(
                    message="Stage 1 timeout",
                    errors=[ApiError(
                        code="STAGE1_TIMEOUT",
                        field=None,
                        message=f"Stage 1 timed out after 5 minutes (taskId: {task_id_1})"
                    )],
                    request_id=request_id
                ).model_dump()
            )
        finally:
            pending_tasks.pop(task_id_1, None)

        if webhook_result.is_failure:
            return JSONResponse(
                status_code=500,
                content=error_response(
                    message="Stage 1 task failed",
                    errors=[ApiError(
                        code="STAGE1_TASK_FAILED",
                        field=None,
                        message=webhook_result.data.failMsg or webhook_result.msg
                    )],
                    request_id=request_id
                ).model_dump()
            )

        ghibli_qr_url = json.loads(webhook_result.data.resultJson)["resultUrls"][0]
        to_ghibli_cost_time = webhook_result.data.costTime

        # Stage 2: Compose Ghibli + QR lock (seedream)
        res = generate_img([ghibli_qr_url, qr_lock_url_path], s.PROMPT_GHIBLI_LOCK, model=s.KIE_COMPOSE_MODEL)
        if res["code"] != 200:
            return JSONResponse(
                status_code=500,
                content=error_response(
                    message="Stage 2 (composition) API error",
                    errors=[ApiError(
                        code="STAGE2_API_ERROR",
                        field=None,
                        message=res.get("msg", "External API error")
                    )],
                    request_id=request_id
                ).model_dump()
            )

        task_id_2 = res["data"]["taskId"]
        future = asyncio.get_event_loop().create_future()
        pending_tasks[task_id_2] = future

        try:
            webhook_result = await asyncio.wait_for(future, timeout=300)
        except asyncio.TimeoutError:
            pending_tasks.pop(task_id_2, None)
            return JSONResponse(
                status_code=504,
                content=error_response(
                    message="Stage 2 timeout",
                    errors=[ApiError(
                        code="STAGE2_TIMEOUT",
                        field=None,
                        message=f"Stage 2 timed out after 5 minutes (taskId: {task_id_2})"
                    )],
                    request_id=request_id
                ).model_dump()
            )
        finally:
            pending_tasks.pop(task_id_2, None)

        if webhook_result.is_failure:
            return JSONResponse(
                status_code=500,
                content=error_response(
                    message="Stage 2 task failed",
                    errors=[ApiError(
                        code="STAGE2_TASK_FAILED",
                        field=None,
                        message=webhook_result.data.failMsg or webhook_result.msg
                    )],
                    request_id=request_id
                ).model_dump()
            )

        # Success: Return final composed image
        params = json.loads(json.loads(webhook_result.data.param)["input"])
        result_urls = json.loads(webhook_result.data.resultJson)["resultUrls"]

        return JSONResponse(
            content=success_response(
                message="Ghibli + QR pipeline completed successfully",
                data={
                    "resultUrls": result_urls,
                    "model": webhook_result.data.model,
                    "costTime": webhook_result.data.costTime + to_ghibli_cost_time,
                    "quality": params.get("quality", "basic"),
                    "aspectRatio": params.get("aspect_ratio", "1:1"),
                },
                request_id=request_id
            ).model_dump()
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=internal_error_response(
                message=f"Unexpected error: {str(e)}",
                request_id=request_id
            ).model_dump()
        )