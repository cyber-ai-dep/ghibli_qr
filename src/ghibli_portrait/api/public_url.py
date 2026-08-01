"""Resolve the base URL used to build the asset links this API returns.

This service returns URLs that point back at itself (`resultUrls`, `stage1Url`,
`qrUrl`) and serves those files from its own StaticFiles mount at `/tmp`. So the
base URL must be one the *caller* can actually open — which is not a single fixed
value when the same deployment is reachable several ways (a public domain over
HTTPS, and the raw `IP:port` from inside the same host or network).

A static `DOMAIN` cannot satisfy both: whichever one it is set to, callers
arriving by the other route receive links they cannot open, while the API still
answers `200 OK`. That failure is silent, which makes it expensive to diagnose.

So the base URL is derived per request from the inbound headers, honouring the
`X-Forwarded-*` pair a reverse proxy sets, and `DOMAIN` becomes the fallback for
requests with no usable Host (and the value used outside any request, e.g. from
the cleanup loop).

Carried on a ContextVar rather than threaded through call signatures: the URLs
are built inside nested helpers that run under `asyncio.to_thread`, and
`to_thread` copies the current context into the worker thread — the same
mechanism `diagnostics/context.py` already relies on for the request id.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Iterable, Optional, Sequence, Tuple

_public_base_url: ContextVar[str] = ContextVar("public_base_url", default="")

_HOST = b"host"
_FWD_HOST = b"x-forwarded-host"
_FWD_PROTO = b"x-forwarded-proto"


def _header(headers: Sequence[Tuple[bytes, bytes]], name: bytes) -> Optional[str]:
    for key, value in headers:
        if key.lower() == name:
            decoded = value.decode("latin-1", "replace").strip()
            return decoded or None
    return None


def _first(value: str) -> str:
    """Take the first entry of a possibly comma-joined proxy header.

    A request that traverses two proxies arrives as `X-Forwarded-Proto: https, http`;
    the leftmost value is the one the original client used.
    """
    return value.split(",")[0].strip()


def resolve_from_headers(
    headers: Sequence[Tuple[bytes, bytes]],
    *,
    fallback: str,
    trusted_hosts: Optional[Iterable[str]] = None,
) -> str:
    """Build `scheme://authority` for this request, or `fallback` if not derivable.

    `trusted_hosts` is an optional allow-list of hostnames (no port). When set, a
    Host header outside it is ignored in favour of `fallback` — Host is
    client-controlled, and reflecting it unchecked lets a caller decide what
    hostname appears in the links this API hands back. Left unset it accepts
    whatever host the request arrived on, which is what makes a single deployment
    answer correctly on both a domain and a bare IP without configuration.
    """
    raw_host = _header(headers, _FWD_HOST) or _header(headers, _HOST)
    if not raw_host:
        return fallback

    host = _first(raw_host)
    if not host:
        return fallback

    if trusted_hosts:
        # Compare on the hostname alone: the same host is legitimately reached
        # both with and without an explicit port.
        hostname = host.rsplit(":", 1)[0] if ":" in host and not host.endswith("]") else host
        if hostname.strip("[]").lower() not in {h.lower() for h in trusted_hosts}:
            return fallback

    proto = _header(headers, _FWD_PROTO)
    scheme = _first(proto).lower() if proto else "http"
    if scheme not in ("http", "https"):
        scheme = "http"

    return f"{scheme}://{host}"


def set_public_base_url(value: str):
    """Bind the base URL for the current request. Returns the reset token."""
    return _public_base_url.set(value.rstrip("/"))


def reset_public_base_url(token) -> None:
    _public_base_url.reset(token)


def get_public_base_url() -> str:
    """Base URL for asset links, or the configured DOMAIN outside a request.

    Settings is imported lazily so this module stays importable from middleware
    that loads before configuration.
    """
    bound = _public_base_url.get()
    if bound:
        return bound

    from src.ghibli_portrait.config import Settings

    return (Settings.DOMAIN or "").rstrip("/")


def asset_url(filename: str) -> str:
    """Full URL for a file served from the /tmp mount."""
    return f"{get_public_base_url()}/tmp/{filename}"


__all__ = [
    "asset_url",
    "get_public_base_url",
    "resolve_from_headers",
    "reset_public_base_url",
    "set_public_base_url",
]
