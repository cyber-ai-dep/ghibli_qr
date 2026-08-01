"""Scrubs secrets out of log text before it enters the in-memory buffer.

Redaction happens at CAPTURE time, not read time, so a secret never sits in the
buffer at all. It applies only to the buffer — the root StreamHandler formats each
record independently, so container logs (`kubectl logs`, the compose json-file
driver) keep full fidelity. That split is deliberate: unredacted where access is
already controlled by the platform, redacted where a token-gated HTTP API can read it.
"""

from __future__ import annotations

import os
import re
from typing import List, Pattern, Tuple

# Base64 data URIs are collapsed FIRST, before truncation. image_service._inline_ref
# builds ~200-400 KB data URIs, and seedream_service raises RuntimeError(f"...: {data}")
# which is logged with a traceback — one such record would otherwise fill a fifth of
# the buffer, and truncating first would just leave 2000 chars of base64.
_DATA_URI_RE = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=]+)")

# Query strings carry X-Amz-Signature and expiring CDN credentials; the host+path is
# what you actually need to reproduce a failure, so only the query is stripped.
_URL_QUERY_RE = re.compile(r"(https?://[^\s\"'<>]+?)\?[^\s\"'<>]*")
_URL_FULL_RE = re.compile(r"(https?://[^/\s\"'<>]+)(/[^\s\"'<>]*)?")

_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/=]{8,}")
_KV_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|authorization|token|secret|password|passwd)\b"
    r"(\s*[=:]\s*)([\"']?)([\w\-.=+/]{6,})"
)

REDACT_URL_MODES = ("off", "query", "full")


def _collapse_data_uris(text: str) -> str:
    return _DATA_URI_RE.sub(
        lambda m: f"data:image/…;base64,<{len(m.group(1))} chars elided>", text
    )


def _literal_patterns() -> List[Tuple[Pattern[str], str]]:
    """Exact-value patterns for secrets we can read from the environment."""
    out: List[Tuple[Pattern[str], str]] = []
    ark = os.getenv("ARK_API_KEY", "").strip()
    if len(ark) >= 8:
        out.append((re.compile(re.escape(ark)), "***REDACTED_ARK_KEY***"))
    diag = os.getenv("DIAGNOSTICS_TOKEN", "").strip()
    if len(diag) >= 8:
        out.append((re.compile(re.escape(diag)), "***REDACTED_DIAG_TOKEN***"))
    return out


class Redactor:
    """Applies the redaction rules in a fixed order. Cheap: all patterns precompiled."""

    def __init__(self, url_mode: str = "query"):
        self.url_mode = url_mode if url_mode in REDACT_URL_MODES else "query"
        # Snapshotted at construction — the handler is built once at startup, and
        # re-reading os.getenv per record would be pure overhead on the hot path.
        self._literals = _literal_patterns()

    def collapse_data_uris(self, text: str) -> str:
        """Exposed separately because it must run BEFORE truncation."""
        return _collapse_data_uris(text)

    def __call__(self, text: str) -> str:
        if not text:
            return text
        for pattern, replacement in self._literals:
            text = pattern.sub(replacement, text)
        text = _BEARER_RE.sub("Bearer ***", text)
        text = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}***", text)
        if self.url_mode == "query":
            text = _URL_QUERY_RE.sub(r"\1?<redacted>", text)
        elif self.url_mode == "full":
            text = _URL_FULL_RE.sub(lambda m: f"{m.group(1)}/…", text)
        return text


__all__ = ["Redactor", "REDACT_URL_MODES"]
