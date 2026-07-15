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
from src.ghibli_portrait.services.clip_validation_service import preload as _preload_clip

_log = logging.getLogger(__name__)
s = Settings()


async def _tmp_cleanup_loop():
    """Delete tmp files respecting prefix-based TTL, runs every 30 minutes.

    Prefixes and their TTLs (all configurable via env vars):
      final_   → FINAL_IMAGE_TTL_HOURS (default 24h) — skipped entirely if PERSIST_FINAL_IMAGES=true
      stage1_  → STAGE1_TTL_HOURS  (default 2h)
      qrlock_  → QRLOCK_TTL_HOURS  (default 2h)
      (other)  → 2h fallback for legacy/unnamed files

    Extend this function when adding new asset categories — add a new
    startswith() branch with its own TTL setting and prefix constant.
    """
    while True:
        await asyncio.sleep(30 * 60)
        now = time.time()
        intermediate_deleted = 0
        final_deleted = 0
        # Logged at most once per cycle to avoid log spam when many final_ files exist.
        _persist_skip_logged = False

        for ext in ("*.jpg", "*.png"):
            for f in s.TMP_PATH.glob(ext):
                try:
                    name = f.name
                    mtime = f.stat().st_mtime

                    if name.startswith("final_"):
                        if s.PERSIST_FINAL_IMAGES:
                            if not _persist_skip_logged:
                                _log.info(
                                    "Skipping final image cleanup because persistence mode is enabled"
                                )
                                _persist_skip_logged = True
                            continue
                        cutoff = now - s.FINAL_IMAGE_TTL_HOURS * 3600
                        if mtime < cutoff:
                            f.unlink(missing_ok=True)
                            _log.info("Deleted expired final image: %s", name)
                            final_deleted += 1
                        continue

                    elif name.startswith("stage1_"):
                        cutoff = now - s.STAGE1_TTL_HOURS * 3600
                    elif name.startswith("qrlock_"):
                        cutoff = now - s.QRLOCK_TTL_HOURS * 3600
                    else:
                        cutoff = now - 2 * 3600

                    if mtime < cutoff:
                        f.unlink(missing_ok=True)
                        _log.debug("Cleanup: deleted intermediate file %s", name)
                        intermediate_deleted += 1

                except Exception:
                    pass

        if final_deleted:
            _log.info("Cleanup: deleted %d expired final image(s)", final_deleted)
        if intermediate_deleted:
            _log.info("Cleanup: deleted %d stale intermediate file(s)", intermediate_deleted)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Expand thread pool for CPU-bound work (MediaPipe face detection, PIL).
    # Default is min(32, cpu+4) — insufficient when many requests run concurrently.
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=100))

    # Ensure tmp directory exists (missing on fresh clone → StaticFiles mount fails at startup).
    s.TMP_PATH.mkdir(parents=True, exist_ok=True)

    # Preload CLIP (model + text embeddings) so the first request doesn't pay
    # the ~2.3s load cost. Exceptions are intentionally not caught here — a
    # load failure should crash startup so restart: on-failure retries,
    # instead of serving an instance where every validation call will fail.
    await asyncio.to_thread(_preload_clip)
    _log.info("CLIP model preloaded at startup")

    # Log final image retention policy once at startup so operators can confirm config.
    if s.PERSIST_FINAL_IMAGES:
        _log.info("Final image retention: persistence mode enabled — final_ images will never be auto-deleted")
    else:
        _log.info("Final image TTL cleanup active: %d hours (FINAL_IMAGE_TTL_HOURS)", s.FINAL_IMAGE_TTL_HOURS)

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
