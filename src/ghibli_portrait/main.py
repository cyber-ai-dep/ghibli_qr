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
    description=(
        "Production-ready Ghibli portrait transformation API with unified response format.\n\n"
        "## Primary Endpoint\n"
        "Use **POST /v1/ghibli-qr** for the complete Ghibli + QR pipeline.\n\n"
        "## Response Contract\n"
        "All responses follow a unified envelope:\n"
        "```json\n"
        '{"success": bool, "data": object|null, "message": string, "errors": array|null, "timestamp": string}\n'
        "```\n\n"
        "## Error Structure\n"
        "```json\n"
        '{"code": "SCREAMING_SNAKE_CASE", "type": "VALIDATION_ERROR|EXTERNAL_ERROR|SYSTEM_ERROR", '
        '"stage": "INPUT|SOURCE_RESOLUTION|STAGE1_GHIBLI|STAGE2_QR|ORCHESTRATION", "field": "camelCase", "message": "..."}\n'
        "```"
    ),
    version="1.0.0",
    openapi_tags=TAGS_METADATA,
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

app.mount('/tmp', StaticFiles(directory=s.TMP_PATH), name='tmp')