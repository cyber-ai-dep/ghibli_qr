"""Request-scoped context and access logging.

Implemented as a pure ASGI middleware rather than `@app.middleware("http")` /
`BaseHTTPMiddleware` on purpose: BaseHTTPMiddleware runs the app in a sub-task and
buffers the response body, which breaks streaming and range requests. This service
serves every generated image through the StaticFiles mount at /tmp, so that matters.
"""

from __future__ import annotations

import logging
import time

from src.ghibli_portrait.diagnostics.context import (
    NO_REQUEST_ID,
    new_request_id,
    reset_request_id,
    sanitize_request_id,
    set_request_id,
)
from src.ghibli_portrait.diagnostics.metrics import METRICS

_log = logging.getLogger(__name__)

_REQUEST_ID_HEADER = b"x-request-id"

# Paths logged at DEBUG instead of INFO. The k8s readiness probe hits /v1/health
# every 10s and the compose healthcheck every 30s — at INFO they would dominate a
# 2000-entry log buffer and push out the request traces it exists to hold.
_QUIET_PREFIXES = ("/v1/health", "/tmp/", "/docs", "/redoc", "/openapi.json", "/favicon.ico")


def _is_quiet(path: str) -> bool:
    return any(path.startswith(p) for p in _QUIET_PREFIXES)


def _level_for(status: int, path: str) -> int:
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    return logging.DEBUG if _is_quiet(path) else logging.INFO


def _endpoint_key(scope, fallback_path: str) -> str:
    """Prefer the matched route's path template over the concrete path.

    `/v1/qr-lock/{img_id}` counts as one endpoint rather than one per image id,
    which is what keeps the per-endpoint counter's cardinality bounded.
    """
    route = scope.get("route")
    template = getattr(route, "path", None) if route is not None else None
    return template or fallback_path


class RequestContextMiddleware:
    """Binds a request id to the context, echoes X-Request-ID, logs completion."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = None
        for key, value in scope.get("headers", []):
            if key.lower() == _REQUEST_ID_HEADER:
                inbound = value.decode("latin-1", "replace")
                break

        request_id = sanitize_request_id(inbound) or new_request_id()
        token = set_request_id(request_id)

        method = scope.get("method", "-")
        path = scope.get("path", "-")
        started = time.monotonic()
        seen_status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                seen_status["code"] = message["status"]
                headers = message.setdefault("headers", [])
                if not any(k.lower() == _REQUEST_ID_HEADER for k, _ in headers):
                    headers.append((_REQUEST_ID_HEADER, request_id.encode()))
            await send(message)

        METRICS.request_started()
        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException:
            # Deliberately not resetting the contextvar here: Starlette's
            # ServerErrorMiddleware sits ABOVE this middleware and runs the 500
            # handler in this same task, and that handler reads the request id to
            # put it on the response. Uvicorn gives each request its own task, so
            # nothing leaks between requests.
            elapsed_ms = (time.monotonic() - started) * 1000
            METRICS.unhandled_exception()
            METRICS.request_finished(method, _endpoint_key(scope, path), 500, elapsed_ms)
            _log.error("%s %s → 500 in %.0fms (unhandled)", method, path, elapsed_ms)
            raise

        status = seen_status["code"]
        elapsed_ms = (time.monotonic() - started) * 1000
        METRICS.request_finished(method, _endpoint_key(scope, path), status, elapsed_ms)
        _log.log(
            _level_for(status, path),
            "%s %s → %d in %.0fms",
            method, path, status, elapsed_ms,
        )
        reset_request_id(token)


__all__ = ["RequestContextMiddleware", "NO_REQUEST_ID"]
