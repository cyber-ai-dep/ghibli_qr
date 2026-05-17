from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np
from PIL import Image
from qreader import QReader


# ---------- CONFIG ----------
QR_MODEL_SIZE = "s"   # small model — fast, accurate enough for QR codes
TMP_DIR = Path("/tmp")

# Module-level singleton — loaded once on first call, reused for all requests.
# Previously re-created on every call, causing 92MB YOLO reload each time.
_qreader: QReader | None = None


def _get_qreader() -> QReader:
    global _qreader
    if _qreader is None:
        _qreader = QReader(model_size=QR_MODEL_SIZE)
    return _qreader


# ---------- RESULT OBJECT ----------
@dataclass
class QRValidationResult:
    ok: bool
    detected_payload: Optional[str]
    expected_payload: str
    raw_results: Any = None
    reason: Optional[str] = None


# ---------- PAYLOAD EXTRACTION (UNCHANGED LOGIC) ----------
def extract_qr_payload(qreader_results: Any) -> Optional[str]:
    """
    Extracts the first valid QR payload (string) from QReader results.
    Ignores detection-only metadata.
    """
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


# ---------- MAIN VALIDATION FUNCTION ----------
def validate_qr_from_image_url(
    image_url: str,
    expected_payload: str,
) -> QRValidationResult:
    """
    Downloads merged image, detects QR payload using QReader,
    and validates it against the expected payload.
    """

    tmp_path = TMP_DIR / f"{uuid4()}.png"

    try:
        # ---------- DOWNLOAD IMAGE ----------
        with httpx.stream("GET", image_url, timeout=30) as r:
            r.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)

        if not tmp_path.exists():
            return QRValidationResult(
                ok=False,
                detected_payload=None,
                expected_payload=expected_payload,
                reason="downloaded image not found on disk",
            )

        # ---------- LOAD IMAGE ----------
        image = Image.open(tmp_path).convert("RGB")
        img_np = np.array(image)

        # ---------- DETECT & DECODE ----------
        results = _get_qreader().detect_and_decode(
            image=img_np,
            return_detections=True,
        )

        detected_payload = extract_qr_payload(results)

        if not detected_payload:
            return QRValidationResult(
                ok=False,
                detected_payload=None,
                expected_payload=expected_payload,
                raw_results=results,
                reason="no valid qr payload detected in merged image",
            )

        # ---------- VALIDATION ----------
        if detected_payload != expected_payload:
            return QRValidationResult(
                ok=False,
                detected_payload=detected_payload,
                expected_payload=expected_payload,
                raw_results=results,
                reason="qr payload mismatch",
            )

        return QRValidationResult(
            ok=True,
            detected_payload=detected_payload,
            expected_payload=expected_payload,
            raw_results=results,
        )

    except Exception as e:
        return QRValidationResult(
            ok=False,
            detected_payload=None,
            expected_payload=expected_payload,
            reason=f"qr validation error: {str(e)}",
        )

    finally:
        if tmp_path.exists():
            tmp_path.unlink()
