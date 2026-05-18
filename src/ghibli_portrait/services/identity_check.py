"""
Identity drift detection for Stage 1 output validation.

Heuristic checks using MediaPipe face detection and skin-tone hue analysis
to catch cases where the model replaced the subject's identity instead of
performing a pure style transfer.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image

from src.ghibli_portrait.services.validation_service import _detect_faces

_logger = logging.getLogger("identity_check")


@dataclass
class IdentityCheckResult:
    ok: bool
    drift_detected: bool = False
    reason: str = ""
    details: dict = field(default_factory=dict)


def _crop_face_region(img: Image.Image, bbox: tuple, img_w: int, img_h: int) -> Image.Image:
    x, y, w, h = bbox
    pad_x = int(w * 0.10)
    pad_y = int(h * 0.10)
    return img.crop((
        max(0, x - pad_x),
        max(0, y - pad_y),
        min(img_w, x + w + pad_x),
        min(img_h, y + h + pad_y),
    ))


def _mean_hue(img: Image.Image) -> float:
    """
    Return the mean hue (0–360°) of colorful, visible pixels in the face crop.
    Returns -1 if no colorful pixels exist (greyscale / near-white face region).
    """
    arr = np.array(img.resize((64, 64), Image.LANCZOS)).astype(float) / 255.0
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    max_c = np.maximum(np.maximum(r, g), b)
    min_c = np.minimum(np.minimum(r, g), b)
    delta = max_c - min_c

    mask = (delta > 0.08) & (max_c > 0.10)
    if not np.any(mask):
        return -1.0

    h = np.zeros_like(r)
    with np.errstate(divide="ignore", invalid="ignore"):
        m_r = mask & (max_c == r)
        m_g = mask & (max_c == g) & ~m_r
        m_b = mask & ~m_r & ~m_g
        h[m_r] = (60.0 * ((g[m_r] - b[m_r]) / delta[m_r])) % 360.0
        h[m_g] = 60.0 * ((b[m_g] - r[m_g]) / delta[m_g]) + 120.0
        h[m_b] = 60.0 * ((r[m_b] - g[m_b]) / delta[m_b]) + 240.0

    return float(np.mean(h[mask]))


def _hue_distance(h1: float, h2: float) -> float:
    d = abs(h1 - h2)
    return min(d, 360.0 - d)


def check_identity_drift(
    source_img: Image.Image,
    output_img: Image.Image,
) -> IdentityCheckResult:
    """
    Heuristic identity drift check: source photo vs Stage 1 illustration output.

    Checks (in order of reliability):
    1. Face is present in the output.
    2. Face area ratio hasn't changed by more than 30%.
    3. Skin-tone hue in the face region hasn't shifted by more than 40°.

    On detector failure, returns ok=True to avoid false rejection on system errors.
    """
    src_w, src_h = source_img.size
    out_w, out_h = output_img.size

    src_det = _detect_faces(source_img)
    out_det = _detect_faces(output_img)

    if not out_det.ok or not src_det.ok:
        return IdentityCheckResult(ok=True, reason="detector_unavailable")

    if out_det.face_count == 0:
        return IdentityCheckResult(
            ok=False,
            drift_detected=True,
            reason="no_face_in_output",
            details={"source_faces": src_det.face_count, "output_faces": 0},
        )

    if src_det.face_count == 0:
        return IdentityCheckResult(ok=True, reason="source_no_face")

    src_face = src_det.faces[0]
    out_face = out_det.faces[0]

    area_delta = abs(src_face["area_ratio"] - out_face["area_ratio"])
    if area_delta > 0.30:
        return IdentityCheckResult(
            ok=False,
            drift_detected=True,
            reason="face_area_drift",
            details={
                "src_area_ratio": round(src_face["area_ratio"], 3),
                "out_area_ratio": round(out_face["area_ratio"], 3),
                "delta": round(area_delta, 3),
            },
        )

    try:
        src_crop = _crop_face_region(source_img, src_face["bbox"], src_w, src_h)
        out_crop = _crop_face_region(output_img, out_face["bbox"], out_w, out_h)

        src_hue = _mean_hue(src_crop)
        out_hue = _mean_hue(out_crop)

        if src_hue >= 0 and out_hue >= 0:
            hue_diff = _hue_distance(src_hue, out_hue)
            if hue_diff > 40.0:
                return IdentityCheckResult(
                    ok=False,
                    drift_detected=True,
                    reason="skin_tone_drift",
                    details={
                        "src_hue": round(src_hue, 1),
                        "out_hue": round(out_hue, 1),
                        "hue_diff": round(hue_diff, 1),
                    },
                )
    except Exception as exc:
        _logger.warning("Skin tone hue check failed: %s", exc)

    return IdentityCheckResult(ok=True, drift_detected=False)


async def check_identity_drift_async(
    source_img: Image.Image,
    output_img: Image.Image,
) -> IdentityCheckResult:
    """Run identity drift check in a thread (CPU-bound: NumPy + MediaPipe)."""
    return await asyncio.to_thread(check_identity_drift, source_img, output_img)
