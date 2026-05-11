from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from src.ghibli_portrait.api.responses import error_response
from src.ghibli_portrait.api.routes import router, TAGS_METADATA
from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import ApiError, ErrorStage, ErrorType

s = Settings()

app = FastAPI(
    title="Ghibli Portrait API V1",
    description="Production-ready Ghibli portrait transformation API with unified response format",
    version="1.0.0"
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        loc = error.get("loc", [])
        field_parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
        field = field_parts[-1] if field_parts else None
        pydantic_type = error.get("type", "validation_error")
        code = pydantic_type.upper().replace(".", "_").replace("-", "_")
        errors.append(ApiError(
            code=code,
            type=ErrorType.VALIDATION_ERROR,
            stage=ErrorStage.INPUT,
            field=field,
            message=error.get("msg", "Validation error"),
        ))
    response = error_response(
        message="Request validation failed",
        errors=errors,
    )
    return JSONResponse(
        status_code=422,
        content=response.model_dump(by_alias=True),
    )


app.include_router(router)

app.mount("/tmp", StaticFiles(directory=str(s.TMP_PATH)), name="tmp")
