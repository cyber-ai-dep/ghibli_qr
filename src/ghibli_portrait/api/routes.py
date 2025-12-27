import asyncio
from typing import Dict

from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter

from src.ghibli_portrait.api.responses import CreatedTaskResponse, GenericResponse
from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import CallbackRequest, Image2GhibliRequest, QRLockRequest
from src.ghibli_portrait.services.image_service import generate_img
from src.ghibli_portrait.services.qr_service import get_qr

router = APIRouter()
pending_tasks: Dict[str, asyncio.Future] = {}
s = Settings()


@router.get(
    "/health",
    response_model=GenericResponse,
    summary="Liveness probe",
    description="Returns 200 if the service is alive.",
)
async def health():
    return GenericResponse()


@router.post(
    "/ghibli",
    tags=["ghibli"],
    response_model=CallbackRequest,
    responses={
        500: {
            "description": "Internal Server Error",
            "content": {
                "application/json": {
                    "example": {
                        "code": 500,
                        "message": "Internal server error",
                        "data": {},
                    }
                }
            },
        }
    },
    summary="Ghibli-style portrait generator",
    description=(
        "Submit one or more images to Ghibli-stylised version "
        f"powered by the external {s.KIE_IMG_MODEL} model. "
        "The generated portrait URL is returned"
    ),
)
async def transform2ghibli(request: Image2GhibliRequest):
    res = generate_img(**request.model_dump())
    if res["code"] != 200:
        return JSONResponse(
            status_code=res["code"],
            content=GenericResponse(code=res["code"], message=res["msg"]).model_dump(),
        )

    task_res = CreatedTaskResponse(**res)
    task_id = task_res.data.taskId

    future = asyncio.get_event_loop().create_future()
    pending_tasks[task_id] = future

    try:
        webhook_result = await asyncio.wait_for(future, timeout=300)
        return webhook_result.model_dump()

    except asyncio.TimeoutError:
        pending_tasks.pop(task_id, None)

        raise HTTPException(
            status_code=504, detail=f"Webhook timeout for task {task_id}"
        )
    except Exception as e:
        pending_tasks.pop(task_id, None)

        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/ghibli/callback",
    tags=["ghibli"],
    response_model=CallbackRequest,
    responses={501: {"model": CallbackRequest, "description": "Task failed"}},
    summary="Ghibli task completion webhook (called by external API)",
    description=(
        "Receives automatic notifications from KIE API when image transformation tasks complete. "
        "Includes result URLs on success or error details on failure. "
        "**Intended for KIE API callbacks only.**"
    ),
)
async def webhook(req: CallbackRequest):
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

    return req.model_dump()


@router.post(
    "/qr-lock",
    tags=["qr"],
    response_model=None,
    responses={
        200: {
            "description": "QR code successfully generated",
            "content": {
                "application/json": {
                    "example": {
                        "url": "https://example.com/tmp/a6100b93-a8f8-4dce-90fc-39e900864a58.png"
                    }
                }
            },
        },
        500: {
            "description": "Internal Server Error",
            "content": {
                "application/json": {
                    "example": {
                        "code": 500,
                        "message": "Failed to generate QR code",
                        "data": {},
                    }
                }
            },
        }
    },
    summary="Generate QR code with lock screen",
    description=(
        "Generates a QR code image with lock screen."
        "Returns a publicly accessible URL to the generated PNG image."
    ),
)
async def get_qr_lock(req: QRLockRequest):
    img = get_qr(**req.model_dump())

    filename = f'{uuid4()}.png'
    filepath = s.TMP_PATH / filename

    img.save(filepath)

    url_path = s.DOMAIN + '/tmp/' + filename

    return JSONResponse(content={'url': url_path})

