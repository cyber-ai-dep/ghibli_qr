from fastapi.routing import APIRouter
from fastapi import Request
from src.ghibli_portrait.api.responses import GenericResponse
from src.ghibli_portrait.models.schemas import Image2GhibliRequest
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
    summary="Ghibli-style portrait generator",
    description=(
        "Submit one or more images to Ghibli-stylised version "
        f"powered by the external {s.KIE_IMG_MODEL} model. "
        "The generated portrait URL is returned"
    ),
)
async def transform2ghibli(request: Image2GhibliRequest):
    res = get_ghibli(**request.dict())
    print(res)
    return res


@router.post('/ghibli/callback', tags=['ghibli'])
async def check(req: Request):
    pass
