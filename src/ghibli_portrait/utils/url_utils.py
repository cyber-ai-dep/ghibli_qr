import uuid
from uuid import uuid5

from src.ghibli_portrait.api.public_url import get_public_base_url
from src.ghibli_portrait.api.responses import ShortURLData
from src.ghibli_portrait.config import Settings


def shorten(url: str):
    """Deterministic short URL for a target URL.

    DOMAIN is preferred here on purpose, unlike the asset URLs in routes.py
    which follow the incoming request. The value this builds is handed to an
    END USER — /v1/qr-lock encodes it into the QR image a phone camera scans —
    so it has to be an address reachable from the public internet. Deriving it
    from the request would give an internal caller its own view of the service
    (e.g. http://ghibli-api:8010/abc12345 inside a cluster), which is correct
    for a link that caller fetches itself but useless inside a QR code.

    The request-derived base is only a fallback for when DOMAIN is unset, so
    that an unconfigured deployment produces a usable link instead of the
    literal string "None/abc12345". config.py already logs a startup warning
    when DOMAIN is empty.
    """
    s = Settings()
    uid = uuid5(uuid.NAMESPACE_URL, url)

    short_code = str(uid)[: s.QR_SHORT_CODE_LENGTH]

    base = (s.DOMAIN or "").rstrip("/") or get_public_base_url()

    return ShortURLData(url=f"{base}/{short_code}", code=short_code)
