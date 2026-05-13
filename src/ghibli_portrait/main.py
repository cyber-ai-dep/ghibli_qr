import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from src.ghibli_portrait.api.responses import error_response
from src.ghibli_portrait.api.routes import router, TAGS_METADATA
from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import ApiError, ErrorStage, ErrorType
from src.ghibli_portrait.services.validation_service import _ensure_model_downloaded

_log = logging.getLogger(__name__)
s = Settings()


async def _tmp_cleanup_loop():
    """Delete tmp files older than 2 hours, runs every 30 minutes."""
    while True:
        await asyncio.sleep(30 * 60)
        cutoff = time.time() - 2 * 3600
        deleted = 0
        for ext in ("*.jpg", "*.png"):
            for f in s.TMP_PATH.glob(ext):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink(missing_ok=True)
                        deleted += 1
                except Exception:
                    pass
        if deleted:
            _log.info("Cleanup: deleted %d stale tmp files", deleted)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Expand thread pool for CPU-bound work (MediaPipe face detection, PIL).
    # Default is min(32, cpu+4) — insufficient when many requests run concurrently.
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=100))

    # Ensure tmp directory exists (missing on fresh clone → StaticFiles mount fails at startup).
    s.TMP_PATH.mkdir(parents=True, exist_ok=True)

    # Pre-download MediaPipe model so the first request doesn't trigger a download.
    await asyncio.to_thread(_ensure_model_downloaded)

    cleanup_task = asyncio.create_task(_tmp_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()


app = FastAPI(
    lifespan=lifespan,
    title="Ghibli Portrait API V1",
    description="Production-ready Ghibli portrait transformation API with unified response format",
    version="1.0.0",
    openapi_tags=TAGS_METADATA,
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, asyncio.CancelledError):
        raise exc
    _log.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=error_response(
            message="Internal server error",
            errors=[ApiError(
                code="INTERNAL_ERROR",
                type=ErrorType.SYSTEM_ERROR,
                stage=ErrorStage.ORCHESTRATION,
                message=str(exc),
            )],
        ).model_dump(by_alias=True),
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
