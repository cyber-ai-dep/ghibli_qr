"""Request correlation — a contextvar carrying the current request id.

Why a contextvar rather than passing an id argument through every call: the
pipeline hands work to worker threads at 18 separate `asyncio.to_thread` sites
(CLIP classification, PIL saves, QR decode, skin extraction). `asyncio.to_thread`
copies the current context into the worker thread, so a contextvar set once by the
middleware is visible in all of them without touching a single service signature.

`loop.run_in_executor` does NOT copy context — it is not used anywhere in this
codebase, and adding it would silently break correlation.
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from uuid import uuid4

# "-" renders as a stable-width placeholder for records emitted outside a request
# (startup, the tmp cleanup loop, shutdown).
NO_REQUEST_ID = "-"

_request_id: ContextVar[str] = ContextVar("request_id", default=NO_REQUEST_ID)

# Inbound X-Request-ID is only honoured if it matches this. This is a log-injection
# guard, not cosmetics: stdout is line-oriented, so a CR/LF inside a client-supplied
# id would let that client forge arbitrary log lines.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def new_request_id() -> str:
    """Generate a short request id — same shape routes.py has always used."""
    return uuid4().hex[:8]


def sanitize_request_id(candidate: str | None) -> str | None:
    """Return `candidate` if it is safe to use verbatim, else None."""
    if not candidate:
        return None
    return candidate if _SAFE_REQUEST_ID.match(candidate) else None


def get_request_id() -> str:
    """Current request id, or NO_REQUEST_ID outside a request."""
    return _request_id.get()


def set_request_id(request_id: str):
    """Bind a request id to the current context. Returns the reset token."""
    return _request_id.set(request_id)


def reset_request_id(token) -> None:
    _request_id.reset(token)


class RequestIdFilter(logging.Filter):
    """Injects `request_id` onto every record so formatters can render it.

    Attached to HANDLERS, not loggers: a handler filter sees every record that
    reaches it, including ones propagated up from child loggers and from
    third-party libraries (httpx, PIL, torch). A logger filter would only see
    records logged directly on that logger.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return True


def install_request_id_filter() -> None:
    """Attach RequestIdFilter to every root handler. Idempotent."""
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, RequestIdFilter) for f in handler.filters):
            handler.addFilter(RequestIdFilter())
