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
from PIL import Image, ImageChops
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


def _pyzbar_decode(img_np: np.ndarray) -> Optional[str]:
    """pyzbar fast path (~5ms). Returns None on any failure."""
    try:
        from pyzbar.pyzbar import ZBarSymbol
        from pyzbar.pyzbar import decode as pyzbar_decode
        results = pyzbar_decode(img_np, symbols=[ZBarSymbol.QRCODE])
        if results:
            return results[0].data.decode("utf-8")
    except Exception:
        pass
    return None


def _qreader_decode(img_np: np.ndarray) -> Optional[str]:
    """QReader/YOLO slow path (~1-2s). Handles artistic/degraded QRs pyzbar cannot find."""
    try:
        raw = _qreader.detect_and_decode(image=img_np, return_detections=True)
        return _extract_qr_payload(raw)
    except Exception:
        return None


def _decode_qr(img_np: np.ndarray) -> Optional[str]:
    """Try pyzbar first (~5ms). Fall back to QReader/YOLO (~1-2s) only if it fails."""
    return _pyzbar_decode(img_np) or _qreader_decode(img_np)


# ---------- CORE VALIDATION (PIL Image → result) ----------
def validate_qr_from_image(
    img: Image.Image,
    expected_payload: str,
) -> QRValidationResult:
    """Two-tier multi-pass QR validation. Accepts a PIL Image — no download required.

    Tier 1 (fast, ~15ms): run pyzbar on original, grayscale, and inverted.
    Tier 2 (slow, ~2s each): run QReader/YOLO only on passes that pyzbar could not decode.
    This avoids paying the YOLO cost when pyzbar succeeds on any variant.

    Returns success on first decode match. Returns payload mismatch immediately
    when a wrong payload is decoded — never retries a mismatch.
    Always call via asyncio.to_thread() from async contexts.
    """
    try:
        rgb = img.convert("RGB")
        variants = [
            ("original", np.array(rgb)),
            ("grayscale", np.array(rgb.convert("L").convert("RGB"))),
            ("inverted", np.array(ImageChops.invert(rgb))),
        ]

        def _check(decoded: Optional[str], pass_name: str) -> Optional[QRValidationResult]:
            if not decoded:
                return None
            if decoded == expected_payload:
                return QRValidationResult(
                    ok=True,
                    detected_payload=decoded,
                    expected_payload=expected_payload,
                    reason=f"decoded on {pass_name} pass",
                )
            return QRValidationResult(
                ok=False,
                detected_payload=decoded,
                expected_payload=expected_payload,
                reason=f"qr payload mismatch on {pass_name} pass",
            )

        # Tier 1: pyzbar on all variants (~5ms each, no YOLO cost)
        for pass_name, arr in variants:
            result = _check(_pyzbar_decode(arr), pass_name)
            if result is not None:
                return result

        # Tier 2: QReader/YOLO on all variants (~1-2s each, only reached if pyzbar failed all)
        for pass_name, arr in variants:
            result = _check(_qreader_decode(arr), pass_name)
            if result is not None:
                return result

        return QRValidationResult(
            ok=False,
            detected_payload=None,
            expected_payload=expected_payload,
            reason="no valid qr payload detected in merged image",
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
