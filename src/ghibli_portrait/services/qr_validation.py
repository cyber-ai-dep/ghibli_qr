# NOTE: This service is intentionally synchronous.
# It uses blocking I/O (httpx, PIL, QReader).
# Always call validate_qr_from_image_url() via asyncio.to_thread()
# from async contexts (e.g. FastAPI route handlers).

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Optional

import httpx
import numpy as np
from PIL import Image
from qreader import QReader


# ---------- CONFIG ----------
QR_MODEL_SIZE = "s"

# Module-level singleton — loaded once per process (~1-2s on CPU).
_qreader = QReader(model_size=QR_MODEL_SIZE)


# ---------- RESULT OBJECT ----------
@dataclass
class QRValidationResult:
    ok: bool
    detected_payload: Optional[str]
    expected_payload: str
    raw_results: Any = None
    reason: Optional[str] = None


# ---------- DECODE ----------
def _extract_qr_payload(qreader_results: Any) -> Optional[str]:
    if not qreader_results:
        return None
    for qr in qreader_results:
        if isinstance(qr, str):
            return qr
        if isinstance(qr, dict):
            text = qr.get("text")
            if isinstance(text, str):
                return text
        if isinstance(qr, (tuple, list)) and len(qr) > 0:
            if isinstance(qr[0], str):
                return qr[0]
    return None


def _decode_qr(img_np: np.ndarray) -> Optional[str]:
    """Try pyzbar first (~5ms). Fall back to QReader/YOLO (~1-2s) only if it fails."""
    try:
        from pyzbar.pyzbar import ZBarSymbol
        from pyzbar.pyzbar import decode as pyzbar_decode
        results = pyzbar_decode(img_np, symbols=[ZBarSymbol.QRCODE])
        if results:
            return results[0].data.decode("utf-8")
    except Exception:
        pass
    # YOLO fallback: handles artistic/degraded QRs that pyzbar cannot locate.
    raw = _qreader.detect_and_decode(image=img_np, return_detections=True)
    return _extract_qr_payload(raw)


# ---------- CORE VALIDATION (PIL Image → result) ----------
def validate_qr_from_image(
    img: Image.Image,
    expected_payload: str,
) -> QRValidationResult:
    """
    Core QR validation logic. Accepts a PIL Image directly — no download.
    Call this when the image is already in memory to avoid duplicate downloads.
    Always call via asyncio.to_thread() from async contexts.
    """
    try:
        img_np = np.array(img.convert("RGB"))
        detected_payload = _decode_qr(img_np)

        if not detected_payload:
            return QRValidationResult(
                ok=False,
                detected_payload=None,
                expected_payload=expected_payload,
                reason="no valid qr payload detected in merged image",
            )

        if detected_payload != expected_payload:
            return QRValidationResult(
                ok=False,
                detected_payload=detected_payload,
                expected_payload=expected_payload,
                reason="qr payload mismatch",
            )

        return QRValidationResult(
            ok=True,
            detected_payload=detected_payload,
            expected_payload=expected_payload,
        )

    except Exception as e:
        return QRValidationResult(
            ok=False,
            detected_payload=None,
            expected_payload=expected_payload,
            reason=f"qr validation error: {str(e)}",
        )


# ---------- URL WRAPPER (download → validate_qr_from_image) ----------
def validate_qr_from_image_url(
    image_url: str,
    expected_payload: str,
) -> QRValidationResult:
    """
    Downloads merged image into memory, detects QR payload,
    and validates it against the expected payload.
    Delegates to validate_qr_from_image() after download.
    """
    try:
        with httpx.stream("GET", image_url, timeout=30) as r:
            r.raise_for_status()
            content = r.read()

        img = Image.open(BytesIO(content)).convert("RGB")
        return validate_qr_from_image(img, expected_payload)

    except Exception as e:
        return QRValidationResult(
            ok=False,
            detected_payload=None,
            expected_payload=expected_payload,
            reason=f"qr validation error: {str(e)}",
        )
