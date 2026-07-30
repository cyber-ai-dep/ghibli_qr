"""Bounded in-memory log buffer backing the diagnostics API.

Attached to the root logger, so it captures every application logger plus any
third-party one (httpx, PIL, torch) without touching a single call site. Records
arrive from the event loop, from the 18 `asyncio.to_thread` sites, and from the
100-worker default executor — `logging.Handler.handle()` already serialises
`emit()` behind its own RLock, so no additional locking is needed here.

The buffer is per-process and dies with it. stdout remains the system of record;
this exists so an operator can ask a live pod what just happened over HTTP.

Self-contained on purpose (own `load_dotenv`, no `config.Settings` import): main.py
installs this before any project module is imported, so Settings does not exist yet.
"""

from __future__ import annotations

import itertools
import logging
import os
import threading
import traceback
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional

from dotenv import load_dotenv

from src.ghibli_portrait.diagnostics.context import NO_REQUEST_ID, get_request_id
from src.ghibli_portrait.diagnostics.redaction import Redactor

# Self-contained: ensure .env is loaded even if this module is imported first.
# override=False, so this cannot change how LOG_LEVEL was already resolved.
load_dotenv()


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


DEFAULT_CAPACITY = _int_env("DIAG_LOG_BUFFER_SIZE", 2000)
DEFAULT_MAX_MESSAGE_CHARS = _int_env("DIAG_LOG_MAX_MESSAGE_CHARS", 2000)
DEFAULT_MAX_EXC_CHARS = _int_env("DIAG_LOG_MAX_EXC_CHARS", 4000)
DEFAULT_REDACT_URLS = os.getenv("DIAG_REDACT_URLS", "query").strip().lower()

# Guards against a log call raised from inside emit() (e.g. a redaction bug)
# recursing forever. Thread-local because emit runs on many threads.
_in_emit = threading.local()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… <truncated, {len(text) - limit} more chars>"


@dataclass(slots=True)
class LogEntry:
    """One captured record. `slots` keeps 2000 of these small (no per-instance dict)."""

    seq: int
    created: float
    levelno: int
    level: str
    logger: str
    request_id: str
    message: str
    module: str
    func: str
    line: int
    thread: str
    exc: Optional[str]


class RingBufferHandler(logging.Handler):
    """Keeps the last N formatted, redacted log records in memory."""

    def __init__(
        self,
        capacity: int = DEFAULT_CAPACITY,
        max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
        max_exc_chars: int = DEFAULT_MAX_EXC_CHARS,
        redact_urls: str = DEFAULT_REDACT_URLS,
    ):
        super().__init__(level=logging.NOTSET)  # root's LOG_LEVEL governs
        self.capacity = capacity
        self.max_message_chars = max_message_chars
        self.max_exc_chars = max_exc_chars
        self._buf: Deque[LogEntry] = deque(maxlen=capacity)
        self._seq = itertools.count(1)
        self._total = 0
        self.redactor = Redactor(redact_urls)

    # ---------------- capture ----------------

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(_in_emit, "active", False):
            return
        _in_emit.active = True
        try:
            message = record.getMessage()
            # Collapse data URIs BEFORE truncating, or truncation just keeps base64.
            message = self.redactor.collapse_data_uris(message)
            message = _truncate(message, self.max_message_chars)
            message = self.redactor(message)

            exc_text = None
            if record.exc_info:
                # Format to text and drop the tuple: exc_info holds live frames, whose
                # locals in this codebase include decoded PIL images and huge base64
                # data URIs. Storing it would pin megabytes per entry.
                raw = "".join(traceback.format_exception(*record.exc_info))
                raw = self.redactor.collapse_data_uris(raw)
                exc_text = self.redactor(_truncate(raw, self.max_exc_chars))

            request_id = getattr(record, "request_id", None) or get_request_id() or NO_REQUEST_ID

            self._buf.append(
                LogEntry(
                    seq=next(self._seq),
                    created=record.created,
                    levelno=record.levelno,
                    level=record.levelname,
                    logger=record.name,
                    request_id=request_id,
                    message=message,
                    module=record.module,
                    func=record.funcName,
                    line=record.lineno,
                    thread=record.threadName or "-",
                    exc=exc_text,
                )
            )
            self._total += 1
        except Exception:
            # Writes to stderr directly — never back through logging.
            self.handleError(record)
        finally:
            _in_emit.active = False

    # ---------------- read ----------------

    def snapshot(self) -> List[LogEntry]:
        """Copy the buffer under the handler's own lock. Never logs while holding it."""
        self.acquire()
        try:
            return list(self._buf)
        finally:
            self.release()

    def clear(self) -> int:
        self.acquire()
        try:
            removed = len(self._buf)
            self._buf.clear()
            return removed
        finally:
            self.release()

    @property
    def total_captured(self) -> int:
        return self._total

    @property
    def dropped_count(self) -> int:
        """Records evicted by the ring. Lets a poller detect it missed entries."""
        return max(0, self._total - len(self._buf))

    def stats(self) -> dict:
        entries = self.snapshot()
        by_level: dict = {}
        for e in entries:
            by_level[e.level] = by_level.get(e.level, 0) + 1
        return {
            "enabled": True,
            "capacity": self.capacity,
            "size": len(entries),
            "totalCaptured": self._total,
            "droppedCount": self.dropped_count,
            "oldestSeq": entries[0].seq if entries else None,
            "newestSeq": entries[-1].seq if entries else None,
            "countsByLevel": by_level,
            "redactUrls": self.redactor.url_mode,
            "maxMessageChars": self.max_message_chars,
            "maxExcChars": self.max_exc_chars,
        }


# ---------------- installation ----------------

_handler: Optional[RingBufferHandler] = None


def install_ring_buffer(**kwargs) -> RingBufferHandler:
    """Attach the buffer to the root logger. Idempotent."""
    global _handler
    root = logging.getLogger()
    for existing in root.handlers:
        if isinstance(existing, RingBufferHandler):
            _handler = existing
            return existing

    from src.ghibli_portrait.diagnostics.context import RequestIdFilter

    handler = RingBufferHandler(**kwargs)
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)
    _handler = handler
    return handler


def get_buffer() -> Optional[RingBufferHandler]:
    """The installed buffer, or None when diagnostics are disabled."""
    return _handler


__all__ = [
    "LogEntry",
    "RingBufferHandler",
    "install_ring_buffer",
    "get_buffer",
    "DEFAULT_CAPACITY",
]
