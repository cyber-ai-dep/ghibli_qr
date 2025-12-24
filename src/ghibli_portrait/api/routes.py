from fastapi.routing import APIRouter
from src.ghibli_portrait.api.responses import GenericResponse

router = APIRouter()


@router.get(
    "/health",
    response_model=GenericResponse,
    summary="Liveness probe",
    description="Returns 200 if the service is alive.",
)
def health():
    return GenericResponse()
