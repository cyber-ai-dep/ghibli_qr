from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter
from fastapi import Request
from src.ghibli_portrait.api.responses import CreatedTaskResponse, GenericResponse
from src.ghibli_portrait.models.schemas import CallbackRequest, Image2GhibliRequest
from src.ghibli_portrait.services.image_service import get_ghibli
from src.ghibli_portrait.config import Settings

router = APIRouter()
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
    response_model=GenericResponse,
    responses={
        500: {
            "model": GenericResponse,
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
    res = get_ghibli(**request.model_dump())
    if res["code"] == 200:
        return CreatedTaskResponse(**res)

    return JSONResponse(
        status_code=res["code"],
        content=GenericResponse(code=res["code"], message=res["msg"]).model_dump(),
    )


@router.post("/ghibli/callback", tags=["ghibli"])
async def check(req: CallbackRequest):
    pass
