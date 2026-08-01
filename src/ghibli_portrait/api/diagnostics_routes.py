"""Operational diagnostics — internal use only.

Design contract (deliberate, and different from a public API endpoint):

  * The routes are ALWAYS registered and ALWAYS visible in Swagger/OpenAPI, so an
    operator or a newly-onboarded engineer can discover them without reading the
    source or changing configuration.
  * Discoverability is NOT access. Every request requires the diagnostics token;
    there is no anonymous mode and no fallback.
  * If DIAGNOSTICS_TOKEN is unset, the API is unreachable by design — an absent
    secret means "deny everything", never "allow everyone".

Auth accepts `X-Diagnostics-Token: <token>` (preferred) or
`Authorization: Bearer <token>`. The dedicated header is preferred because this
service is destined to sit behind a platform gateway that owns `Authorization`
for end-user identity; keeping the diagnostics credential on its own header means
the two can never collide or be confused for one another.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter

from src.ghibli_portrait.api.responses import _utc_timestamp, success_response
from src.ghibli_portrait.diagnostics import runtime
from src.ghibli_portrait.diagnostics.context import get_request_id
from src.ghibli_portrait.diagnostics.log_buffer import get_buffer
from src.ghibli_portrait.diagnostics.metrics import METRICS, memory_usage
from src.ghibli_portrait.models.schemas import ApiSuccessResponse

_log = logging.getLogger(__name__)

DIAGNOSTICS_TAG = {
    "name": "Diagnostics",
    "description": (
        "**Internal operational use only.** Not part of the public API contract and "
        "not intended for end-user clients.\n\n"
        "Every endpoint requires the diagnostics token, sent as either "
        "`X-Diagnostics-Token: <token>` (preferred) or `Authorization: Bearer <token>`. "
        "Requests without a valid token receive `401 Unauthorized`.\n\n"
        "These endpoints read in-process state only — no database, no filesystem "
        "logging, and no outbound calls — so polling them never affects generation traffic."
    ),
}

# How many log entries the aggregated snapshot embeds. Kept small so the default
# response stays a readable dashboard; use /v1/diagnostics/logs for real querying.
_SNAPSHOT_LOG_LIMIT = 25


def _unauthorized() -> HTTPException:
    """401 with a proper challenge header, and no hint about what was wrong."""
    return HTTPException(
        status_code=401,
        detail="Missing or invalid diagnostics token",
        headers={"WWW-Authenticate": 'Bearer realm="diagnostics"'},
    )


def _entry_to_dict(entry) -> dict:
    return {
        "seq": entry.seq,
        "timestamp": _utc_timestamp(datetime.fromtimestamp(entry.created, tz=timezone.utc)),
        "level": entry.level,
        "logger": entry.logger,
        "requestId": entry.request_id,
        "message": entry.message,
        "module": entry.module,
        "function": entry.func,
        "line": entry.line,
        "thread": entry.thread,
        "exception": entry.exc,
    }


def make_token_dependency(settings):
    """Auth dependency bound to the configured token.

    Reads the token off `settings` at call time rather than closing over its value,
    so a test (or a future hot-reload) that swaps the setting takes effect.
    """

    async def require_diagnostics_token(
        x_diagnostics_token: Optional[str] = Header(
            default=None,
            alias="X-Diagnostics-Token",
            description="Diagnostics token (preferred header).",
        ),
        authorization: Optional[str] = Header(
            default=None,
            alias="Authorization",
            description="Alternative to X-Diagnostics-Token: `Bearer <token>`.",
        ),
    ) -> None:
        expected = (getattr(settings, "DIAGNOSTICS_TOKEN", "") or "").strip()

        # No configured token => deny everything. An unset secret must never
        # degrade into anonymous access.
        if not expected:
            _log.error(
                "Diagnostics request denied — DIAGNOSTICS_TOKEN is not configured. "
                "Set it to enable access (openssl rand -hex 32)."
            )
            raise _unauthorized()

        presented = x_diagnostics_token
        if presented is None and authorization:
            scheme, _, value = authorization.partition(" ")
            if scheme.lower() == "bearer":
                presented = value.strip()

        # Always run the comparison — including when nothing was presented — so
        # "no credential" and "wrong credential" cannot be told apart by timing.
        # Bytes on both sides: compare_digest raises TypeError on non-ASCII str,
        # and the caller fully controls these headers.
        if not secrets.compare_digest((presented or "").encode(), expected.encode()):
            _log.warning(
                "Diagnostics auth failed — credentialPresented=%s", bool(presented)
            )
            raise _unauthorized()

    return require_diagnostics_token


def build_router(settings) -> APIRouter:
    router = APIRouter(
        prefix="/v1/diagnostics",
        tags=["Diagnostics"],
        dependencies=[Depends(make_token_dependency(settings))],
        responses={401: {"description": "Missing or invalid diagnostics token"}},
    )

    # ------------------------------------------------------------------
    # Primary endpoint — the operational dashboard
    # ------------------------------------------------------------------
    @router.get(
        "",
        summary="Full operational snapshot (internal use only)",
        description=(
            "**Internal operational use only.** Aggregates everything needed to assess "
            "this service in one call: build identity and uptime, a rolled-up health "
            "verdict, request/error counters, **live rate-limiter state**, concurrency "
            "saturation, in-flight generation tasks, model load state, memory usage, "
            "temp-storage usage, effective configuration, and the most recent log entries."
            "\n\n"
            "**`rateLimiting`** reflects the real production limiter that protects the "
            "billed generation endpoints — it reads that limiter's own sliding-window "
            "deque, not a copy. Key fields:\n"
            "- `requestsInWindow` / `remainingCapacity` / `utilization` — how close the "
            "service is to rejecting traffic right now.\n"
            "- `currentlyLimiting` — whether it is actively rejecting.\n"
            "- `windowResetInSeconds` — when capacity frees up.\n"
            "- `trackedEntries` vs `requestsInWindow` — the limiter prunes expired "
            "entries lazily, so these differ when traffic is idle; "
            "`requestsInWindow` is the number that governs admission.\n"
            "- `rejectedResponsesObserved` — count of 429s. The limiter stores only "
            "*allowed* request timestamps and keeps no rejection counter, so this is "
            "observed at the HTTP layer; it is exact because that limiter is the only "
            "source of HTTP 429 in this service.\n\n"
            "The limiter is global (one shared window, not per-IP or per-key), so there "
            "are no buckets and no client identifiers — nothing caller-specific exists "
            "to expose.\n\n"
            "Secrets are never included — API keys are reported as a presence boolean "
            "plus a non-invertible fingerprint. Reads in-process state only; makes no "
            "outbound calls and adds no load to the generation pipeline."
        ),
        response_model=ApiSuccessResponse,
    )
    async def diagnostics_snapshot(
        log_limit: int = Query(
            _SNAPSHOT_LOG_LIMIT, alias="logLimit", ge=0, le=200,
            description="How many recent log entries to embed. 0 omits them.",
        ),
        log_level: Optional[str] = Query(
            None, alias="logLevel",
            description="Minimum level for the embedded log entries, e.g. WARNING.",
        ),
    ):
        buffer = get_buffer()

        models = runtime.collect_models()
        concurrency = runtime.collect_concurrency()
        config = runtime.collect_config(settings)
        # Blocking stat/glob/disk_usage syscalls — never on the event loop.
        storage = await asyncio.to_thread(runtime.collect_storage, settings.TMP_PATH)

        recent_logs = []
        if buffer is not None and log_limit:
            entries = buffer.snapshot()
            if log_level:
                threshold = logging.getLevelName(log_level.strip().upper())
                if isinstance(threshold, int):
                    entries = [e for e in entries if e.levelno >= threshold]
            recent_logs = [_entry_to_dict(e) for e in entries[-log_limit:][::-1]]

        return JSONResponse(
            content=success_response(
                message="Diagnostics snapshot",
                data={
                    "service": runtime.collect_service(),
                    "health": runtime.collect_health(models, concurrency, config),
                    "requestId": get_request_id(),
                    "requests": METRICS.snapshot(),
                    "rateLimiting": runtime.collect_rate_limiting(settings),
                    "concurrency": concurrency,
                    "pendingTasks": runtime.collect_pending_tasks(),
                    "models": models,
                    "memory": memory_usage(),
                    "storage": storage,
                    "config": config,
                    "logBuffer": buffer.stats() if buffer else {"enabled": False},
                    "recentLogs": recent_logs,
                },
            ).model_dump(by_alias=True)
        )

    # ------------------------------------------------------------------
    # Log querying
    # ------------------------------------------------------------------
    @router.get(
        "/logs",
        summary="Query the in-memory log buffer (internal use only)",
        description=(
            "**Internal operational use only.** Reads the bounded in-memory ring buffer "
            "populated by the application's own loggers. Filter by level, logger, "
            "request id, or message text; poll incrementally with `sinceSeq`.\n\n"
            "Secrets are redacted when each entry is captured, so they are never held "
            "in the buffer at all. The buffer is per-process and is lost on restart — "
            "container stdout remains the system of record."
        ),
        response_model=ApiSuccessResponse,
    )
    async def diagnostics_logs(
        limit: int = Query(100, ge=1, le=1000),
        level: Optional[str] = Query(None, description="Minimum level, e.g. WARNING"),
        logger: Optional[str] = Query(None, description="Case-insensitive substring of the logger name"),
        request_id: Optional[str] = Query(None, alias="requestId", description="Exact match — the full trace of one request"),
        contains: Optional[str] = Query(None, description="Case-insensitive substring of the message"),
        since_seconds: Optional[int] = Query(None, alias="sinceSeconds", ge=1),
        since_seq: Optional[int] = Query(None, alias="sinceSeq", ge=0, description="Only entries newer than this sequence number"),
        order: str = Query("desc", pattern="^(asc|desc)$"),
    ):
        buffer = get_buffer()
        if buffer is None:
            return JSONResponse(
                content=success_response(
                    message="Log buffer is not available",
                    data={"entries": [], "returned": 0, "matched": 0,
                          "logBuffer": {"enabled": False}},
                ).model_dump(by_alias=True)
            )

        entries = buffer.snapshot()

        if level:
            threshold = logging.getLevelName(level.strip().upper())
            if isinstance(threshold, int):
                entries = [e for e in entries if e.levelno >= threshold]
        if logger:
            needle = logger.lower()
            entries = [e for e in entries if needle in e.logger.lower()]
        if request_id:
            entries = [e for e in entries if e.request_id == request_id]
        if contains:
            needle = contains.lower()
            entries = [e for e in entries if needle in e.message.lower()]
        if since_seconds:
            cutoff = time.time() - since_seconds
            entries = [e for e in entries if e.created >= cutoff]
        if since_seq is not None:
            entries = [e for e in entries if e.seq > since_seq]

        matched = len(entries)
        if order == "desc":
            entries = entries[::-1]
        page = entries[:limit]

        return JSONResponse(
            content=success_response(
                message=f"{len(page)} log entr{'y' if len(page) == 1 else 'ies'}",
                data={
                    "entries": [_entry_to_dict(e) for e in page],
                    "returned": len(page),
                    "matched": matched,
                    "truncated": matched > len(page),
                    "logBuffer": buffer.stats(),
                    "filters": {
                        "limit": limit, "level": level, "logger": logger,
                        "requestId": request_id, "contains": contains,
                        "sinceSeconds": since_seconds, "sinceSeq": since_seq,
                        "order": order,
                    },
                },
            ).model_dump(by_alias=True)
        )

    @router.get(
        "/logs/stats",
        summary="Log buffer aggregates (internal use only)",
        description=(
            "**Internal operational use only.** Counts by level and by logger across the "
            "whole buffer, the busiest request ids, and the most recent errors — a fast "
            "way to see what is failing without paging through entries."
        ),
        response_model=ApiSuccessResponse,
    )
    async def diagnostics_log_stats():
        buffer = get_buffer()
        if buffer is None:
            return JSONResponse(
                content=success_response(
                    message="Log buffer is not available",
                    data={"logBuffer": {"enabled": False}},
                ).model_dump(by_alias=True)
            )

        entries = buffer.snapshot()
        by_logger: dict = {}
        by_request: dict = {}
        for entry in entries:
            by_logger[entry.logger] = by_logger.get(entry.logger, 0) + 1
            if entry.request_id and entry.request_id != "-":
                by_request[entry.request_id] = by_request.get(entry.request_id, 0) + 1

        errors = [e for e in entries if e.levelno >= logging.ERROR]
        return JSONResponse(
            content=success_response(
                message="Log buffer statistics",
                data={
                    "logBuffer": buffer.stats(),
                    "countsByLogger": dict(sorted(by_logger.items(), key=lambda kv: -kv[1])),
                    "topRequestIds": dict(sorted(by_request.items(), key=lambda kv: -kv[1])[:20]),
                    "errorCount": len(errors),
                    "recentErrors": [_entry_to_dict(e) for e in errors[-10:]],
                },
            ).model_dump(by_alias=True)
        )

    # ------------------------------------------------------------------
    # Administrative operations
    # ------------------------------------------------------------------
    @router.delete(
        "/logs",
        summary="Clear the in-memory log buffer (internal use only)",
        description=(
            "**Internal operational use only.** Empties the in-memory buffer. Container "
            "stdout is unaffected and remains the durable record. This discards "
            "diagnostic evidence, so the operation is itself logged."
        ),
        response_model=ApiSuccessResponse,
    )
    async def diagnostics_clear_logs():
        buffer = get_buffer()
        removed = buffer.clear() if buffer else 0
        _log.warning("Diagnostics log buffer cleared — %d entr(ies) removed", removed)
        return JSONResponse(
            content=success_response(
                message="Log buffer cleared",
                data={"removed": removed},
            ).model_dump(by_alias=True)
        )

    return router


def register_diagnostics(app: FastAPI, settings) -> bool:
    """Always register the diagnostics router.

    Registration is unconditional so the endpoints are discoverable in Swagger and
    reachable without a restart. Access is governed solely by DIAGNOSTICS_TOKEN,
    enforced per request by the auth dependency — an unset token denies everything.
    """
    app.include_router(build_router(settings))

    token = (getattr(settings, "DIAGNOSTICS_TOKEN", "") or "").strip()
    if not token:
        _log.warning(
            "Diagnostics API registered at /v1/diagnostics but DIAGNOSTICS_TOKEN is "
            "NOT set — every request will be rejected with 401. Set it to enable access."
        )
    else:
        minimum = getattr(settings, "DIAGNOSTICS_MIN_TOKEN_LENGTH", 16)
        if len(token) < minimum:
            _log.warning(
                "Diagnostics API registered at /v1/diagnostics with a weak "
                "DIAGNOSTICS_TOKEN (%d chars, recommended >= %d). "
                "Generate a strong one with: openssl rand -hex 32",
                len(token), minimum,
            )
        else:
            _log.info(
                "Diagnostics API registered at /v1/diagnostics (token configured, %d chars)",
                len(token),
            )
    return True


__all__ = ["register_diagnostics", "build_router", "DIAGNOSTICS_TAG", "make_token_dependency"]
