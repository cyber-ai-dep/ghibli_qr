"""
Validation Service - Layered Validation Architecture

This module implements a strict, non-overlapping validation architecture:

Layer 0 — Request Shape:
    Schema validation only (required fields, types)
    Handled by Pydantic schemas - NOT in this file

Layer 1 — Image Source Resolution:
    Determine what the image source is
    Allow only http/https public URLs
    Reject localhost/private IPs
    Do NOT download or decode images here

Layer 2 — Accessibility & Decode:
    Only for direct user-provided images
    Download image
    Validate MIME type
    Decode image
    NO human/face logic

Layer 3A — Stage 1 (Ghibli / qwen):
    ONLY place where human validation is allowed
    Face detection
    Single face
    Face size
    Real human photo
    Not animal / cartoon

Layer 3B — Stage 2 (QR / seedream):
    Technical validation only
    NO face detection
    NO human validation
    NO reprocessing Stage 1 output
    Stage 1 output is TRUSTED

Layer 4 — Orchestration:
    Coordinate stages only
    No new validation rules
    Do not reinterpret errors
    Handled in routes.py - NOT in this file

Layer 5 — Response Integrity:
    Enforce response contract
    Enforce camelCase
    Enforce success/data consistency
    Handled in responses.py - NOT in this file
"""

from __future__ import annotations

import io
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import requests
from PIL import Image

from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import ErrorType, ErrorStage
from src.ghibli_portrait.services.clip_validation_service import validate_human_portrait

# MediaPipe Tasks API imports
try:
    from mediapipe.tasks.python.vision import FaceDetector, FaceDetectorOptions
    from mediapipe.tasks.python.core.base_options import BaseOptions
    import mediapipe as mp
    _MEDIAPIPE_AVAILABLE = True
except ImportError:
    _MEDIAPIPE_AVAILABLE = False

# Model configuration
_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
_MODEL_CACHE_DIR = Path(__file__).parent.parent / "models"
_MODEL_PATH = _MODEL_CACHE_DIR / "blaze_face_short_range.tflite"

_validation_logger = logging.getLogger(__name__)

# Regex patterns for URL validation
_LOCALHOST_RE = re.compile(
    r"^(https?://)?(localhost|127\.0\.0\.1|0\.0\.0\.0)([:/]|$)",
    re.IGNORECASE
)
_PRIVATE_IP_RE = re.compile(
    r"^(https?://)?(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.)",
    re.IGNORECASE
)

# Module-level Settings singleton — avoids constructing per request.
_settings = Settings()

# Module-level FaceDetector singleton — avoids ~0.5-1s re-instantiation per call.
# Lazy: initialized on first use via _get_face_detector().
_face_detector: Optional["FaceDetector"] = None


# ============================================================================
# VALIDATION RESULT TYPES
# ============================================================================

@dataclass
class ValidationResult:
    """Legacy validation result for backward compatibility."""
    ok: bool
    reason: str = ""
    faces: int = 0


@dataclass
class ValidationResultV1:
    """
    Enhanced validation result for V1 API with structured error codes.

    Attributes:
        ok: Whether validation passed
        code: SCREAMING_SNAKE_CASE error code (None if ok=True)
        message: Human-readable error message (empty if ok=True)
        error_type: Error classification type
        stage: Pipeline stage where error occurred
    """
    ok: bool
    code: Optional[str] = None
    message: str = ""
    error_type: ErrorType = ErrorType.VALIDATION_ERROR
    stage: ErrorStage = ErrorStage.INPUT


# ============================================================================
# LAYER 1: IMAGE SOURCE RESOLUTION
# ============================================================================

def validate_public_url(url: str) -> ValidationResult:
    """
    Layer 1: Validate that URL is a publicly accessible HTTP/HTTPS URL.

    This layer does NOT download, decode, or perform any content validation.
    """
    if not url or not isinstance(url, str):
        return ValidationResult(False, "URL must be a non-empty string")

    url_stripped = url.strip()

    if not (url_stripped.startswith("http://") or url_stripped.startswith("https://")):
        return ValidationResult(False, "URL must start with http:// or https://")

    if _LOCALHOST_RE.search(url_stripped):
        return ValidationResult(
            False,
            "URL must be publicly accessible (localhost URLs are not allowed)"
        )

    if _PRIVATE_IP_RE.search(url_stripped):
        return ValidationResult(
            False,
            "URL must be publicly accessible (private network IPs are not allowed)"
        )

    return ValidationResult(True)


def validate_source_resolution(url: str) -> ValidationResultV1:
    """Layer 1: Image source resolution for V1 API."""
    result = validate_public_url(url)
    if not result.ok:
        return ValidationResultV1(
            ok=False,
            code="INVALID_IMAGE_URL",
            message=result.reason,
            error_type=ErrorType.VALIDATION_ERROR,
            stage=ErrorStage.SOURCE_RESOLUTION
        )
    return ValidationResultV1(ok=True, stage=ErrorStage.SOURCE_RESOLUTION)


# ============================================================================
# LAYER 3A: STAGE 1 VALIDATION (Ghibli / qwen)
# ============================================================================

def _ensure_model_downloaded() -> Optional[str]:
    """Ensure the MediaPipe face detection model is downloaded and cached."""
    if _MODEL_PATH.exists():
        return str(_MODEL_PATH)

    try:
        _MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        response = requests.get(_MODEL_URL, timeout=30)
        response.raise_for_status()
        _MODEL_PATH.write_bytes(response.content)
        _validation_logger.info(f"Downloaded MediaPipe model to {_MODEL_PATH}")
        return str(_MODEL_PATH)
    except Exception as e:
        _validation_logger.error(f"Failed to download MediaPipe model: {e}")
        return None


def _get_face_detector() -> Optional["FaceDetector"]:
    """Return the module-level FaceDetector singleton, initializing it on first call."""
    global _face_detector
    if not _MEDIAPIPE_AVAILABLE:
        return None
    model_path = _ensure_model_downloaded()
    if not model_path:
        return None
    if _face_detector is None:
        options = FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            min_detection_confidence=0.35,
        )
        _face_detector = FaceDetector.create_from_options(options)
    return _face_detector


@dataclass
class FaceDetectionResult:
    """Result of face detection operation."""
    ok: bool
    face_count: int = 0
    primary_face_area_ratio: float = 0.0
    faces: List[Dict] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.faces is None:
            self.faces = []


def _detect_faces(img: Image.Image) -> FaceDetectionResult:
    """
    Detect faces in image using MediaPipe Tasks API (CPU-only).

    Primary face selection priority:
        1. Largest bounding box area
        2. Highest confidence
        3. Closest to image center
    """
    if not _MEDIAPIPE_AVAILABLE:
        return FaceDetectionResult(ok=False, error="MediaPipe is not available")

    detector = _get_face_detector()
    if detector is None:
        return FaceDetectionResult(ok=False, error="Failed to load face detection model")

    try:
        import numpy as np

        np_img = np.array(img)
        if np_img.size == 0:
            return FaceDetectionResult(ok=True, face_count=0)

        height, width = np_img.shape[:2]
        if height == 0 or width == 0:
            return FaceDetectionResult(ok=True, face_count=0)

        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np_img)
        detection_result = detector.detect(mp_image)

        detections = detection_result.detections or []
        if not detections:
            return FaceDetectionResult(ok=True, face_count=0)

        img_area = float(height * width)

        faces_info: List[Dict] = []
        for det in detections:
            score = det.categories[0].score if det.categories else 0.0
            bbox = det.bounding_box
            x = max(0, bbox.origin_x)
            y = max(0, bbox.origin_y)
            w = max(1, bbox.width)
            h = max(1, bbox.height)
            area = float(w * h)
            area_ratio = area / img_area if img_area > 0 else 0.0
            cx = (x + (w / 2)) / width
            cy = (y + (h / 2)) / height
            center_distance = math.sqrt((cx - 0.5) ** 2 + (cy - 0.5) ** 2)
            faces_info.append({
                "bbox": (x, y, w, h),
                "score": score,
                "area": area,
                "area_ratio": area_ratio,
                "center_distance": center_distance,
            })

        faces_info.sort(key=lambda f: (-f["area"], -f["score"], f["center_distance"]))

        return FaceDetectionResult(
            ok=True,
            face_count=len(faces_info),
            primary_face_area_ratio=faces_info[0]["area_ratio"] if faces_info else 0.0,
            faces=faces_info,
        )

    except Exception as e:
        _validation_logger.error(f"Face detection failed: {e}")
        return FaceDetectionResult(ok=False, error=f"Face detection runtime error: {e}")


def _is_synthetic_face(img: Image.Image, bbox: tuple) -> bool:
    """
    Returns True if the face region appears to be a synthetic render, 3D game character,
    or cartoon — not a real human photograph (including B&W photos).

    Four rules — any one can reject:

    Rule A — flat-color pixel art (simple cartoons, 8-bit sprites):
        diversity < 0.05  AND  uniformity > 0.25

    Rule B — extreme uniformity (Minecraft pixel art, classic sprites):
        diversity < 0.20  AND  uniformity > 0.60
        Real photos — including smooth studio shots and beauty-filtered portraits —
        never reach uniformity > 0.60 regardless of skin tone.

    Rule C — high color dominance (3D rendered game faces, CGI):
        diversity < 0.10  AND  max_color_freq > 0.08  AND  skin_ratio < 0.08
        Rendered faces have large flat-color blocks where one exact RGB value
        covers >8% of pixels. The YCbCr skin-pixel guard (skin_ratio) makes
        this rule skin-tone neutral: real dark skin and pale skin both contain
        enough skin-range pixels to pass (skin_ratio >= 0.08). Synthetic renders
        and cartoons with artificial palettes do not.

    Rule D — safety net for extreme renders:
        diversity < 0.10  AND  uniformity > 0.55
    """
    try:
        import numpy as np

        x, y, w, h = bbox
        margin = int(min(w, h) * 0.2)
        img_w, img_h = img.size
        x1 = max(0, x - margin)
        y1 = max(0, y - margin)
        x2 = min(img_w, x + w + margin)
        y2 = min(img_h, y + h + margin)

        region = img.crop((x1, y1, x2, y2)).convert("RGB")
        arr = np.array(region)
        h_px, w_px = arr.shape[:2]
        total = h_px * w_px

        if total < 400:
            return False

        # Signal 1: unique RGB triplets as fraction of total pixels
        unique_colors, counts = np.unique(arr.reshape(-1, 3), axis=0, return_counts=True)
        diversity = len(unique_colors) / total

        # Signal 2: fraction of adjacent pixel pairs with identical RGB
        h_same = np.mean(np.all(arr[:, :-1] == arr[:, 1:], axis=2))
        v_same = np.mean(np.all(arr[:-1, :] == arr[1:, :], axis=2))
        uniformity = (h_same + v_same) / 2

        # Signal 3: fraction of pixels occupied by the single most common color
        max_color_freq = counts.max() / total

        # Rule A: flat-color pixel art
        if diversity < 0.05 and uniformity > 0.25:
            return True

        # Rule B: Minecraft pixel art / extreme uniformity
        if diversity < 0.20 and uniformity > 0.60:
            return True

        # Rule C: 3D rendered game face / high color dominance.
        # Skin-tone guard: if YCbCr skin pixels cover >= 8% of the crop the region
        # contains real human skin and must not be flagged as synthetic. This guard
        # is skin-tone neutral — very dark skin and very pale skin both satisfy it.
        if diversity < 0.10 and max_color_freq > 0.08:
            ycbcr = np.array(region.convert("YCbCr")).astype(np.int16)
            Y_ch  = ycbcr[:, :, 0]
            Cb_ch = ycbcr[:, :, 1]
            Cr_ch = ycbcr[:, :, 2]
            skin_mask = (
                (Y_ch > 40) & (Cb_ch >= 77) & (Cb_ch <= 130) & (Cr_ch >= 130) & (Cr_ch <= 175)
            )
            skin_ratio = skin_mask.sum() / total
            if skin_ratio < 0.08:
                return True

        # Rule D: safety net for extreme renders
        if diversity < 0.10 and uniformity > 0.55:
            return True

        return False

    except Exception:
        return False


def validate_stage1_human_portrait(
    img: Image.Image,
    image_url: str,
    settings: Optional[Settings] = None
) -> ValidationResultV1:
    """
    Layer 3A: Stage 1 (Ghibli) human portrait validation.

    CLIP zero-shot classification (see clip_validation_service.py): a single
    semantic call decides human / cartoon / animal / render / multiple-people /
    no-human. MediaPipe face detection and the pixel-statistics synthetic-image
    check are no longer called here (both stay defined above, unreferenced,
    for tests and possible future use) — CLIP's "multiple people" label now
    covers what the MediaPipe area-ratio multi-face gate used to.

    Fail-closed: a CLIP runtime failure is mapped to the same
    FACE_DETECTOR_FAILURE / SYSTEM_ERROR contract the old detector-failure
    path used, so the external API contract is unchanged.
    """
    s = settings or _settings

    if not s.REQUIRE_HUMAN_FACE:
        return ValidationResultV1(ok=True, stage=ErrorStage.STAGE1_GHIBLI)

    clip_result = validate_human_portrait(img, image_url)

    if clip_result.code == "CLIP_CLASSIFIER_FAILURE":
        _validation_logger.error({
            "url": image_url,
            "error": clip_result.error,
            "decision": "SYSTEM_ERROR",
            "reason": "FACE_DETECTOR_FAILURE"
        })
        return ValidationResultV1(
            ok=False,
            code="FACE_DETECTOR_FAILURE",
            message="Face detection system is temporarily unavailable. Please try again.",
            error_type=ErrorType.SYSTEM_ERROR,
            stage=ErrorStage.STAGE1_GHIBLI
        )

    if clip_result.ok:
        return ValidationResultV1(ok=True, stage=ErrorStage.STAGE1_GHIBLI)

    return ValidationResultV1(
        ok=False,
        code=clip_result.code,
        message=clip_result.message,
        error_type=ErrorType.VALIDATION_ERROR,
        stage=ErrorStage.STAGE1_GHIBLI
    )


# ============================================================================
# LAYER 3B: STAGE 2 VALIDATION (QR / seedream)
# ============================================================================

def validate_stage2_input(stage1_output_url: str) -> ValidationResultV1:
    """
    Layer 3B: Stage 2 (QR/seedream) input validation.

    CRITICAL: Stage 1 output is TRUSTED. Only checks that URL is non-empty.
    """
    if not stage1_output_url or not isinstance(stage1_output_url, str):
        return ValidationResultV1(
            ok=False,
            code="INVALID_STAGE1_OUTPUT",
            message="Stage 1 output URL is missing or invalid",
            error_type=ErrorType.SYSTEM_ERROR,
            stage=ErrorStage.STAGE2_QR
        )
    return ValidationResultV1(ok=True, stage=ErrorStage.STAGE2_QR)


# ============================================================================
# SKIN COLOR EXTRACTION
# ============================================================================

def extract_skin_color_hex(img: Image.Image) -> Optional[str]:
    """
    Extract the dominant skin color from an image using YCbCr-based skin detection.

    Returns a hex string like '#8B4513' representing the median skin pixel color,
    or None if too few skin pixels are found.

    Covers the full human skin tone spectrum — from very dark to very light skin.
    Works on both color and B&W photos (B&W returns None — no color to extract).
    Always call via asyncio.to_thread() from async contexts.
    """
    try:
        import numpy as np

        # Downscale for speed — color statistics don't need full resolution.
        thumb = img.convert("RGB")
        if max(thumb.size) > 512:
            thumb.thumbnail((512, 512), Image.LANCZOS)

        arr = np.array(thumb)
        ycbcr = np.array(thumb.convert("YCbCr"))
        Y  = ycbcr[:, :, 0].astype(np.int16)
        Cb = ycbcr[:, :, 1].astype(np.int16)
        Cr = ycbcr[:, :, 2].astype(np.int16)

        # Established YCbCr skin range — validated across dark, medium, and light tones.
        # Y > 40 catches very dark skin (avoids clipping deep brown/black tones).
        skin_mask = (Y > 40) & (Cb >= 77) & (Cb <= 130) & (Cr >= 130) & (Cr <= 175)

        if skin_mask.sum() < 200:
            return None

        skin_pixels = arr[skin_mask]
        r = int(np.median(skin_pixels[:, 0]))
        g = int(np.median(skin_pixels[:, 1]))
        b = int(np.median(skin_pixels[:, 2]))

        return f"#{r:02X}{g:02X}{b:02X}"

    except Exception as exc:
        # Without this the response silently carries skinColor: null and the
        # skin-tone prompt injection is skipped, with no trace of why.
        _validation_logger.warning("Skin color extraction failed — %s", exc)
        return None


# ============================================================================
# COMPOSITE VALIDATION FUNCTIONS
# ============================================================================

def validate_single_image_url_list(img_urls: list[str]) -> ValidationResult:
    """Validate that exactly one image URL is provided."""
    if not isinstance(img_urls, list) or len(img_urls) != 1:
        return ValidationResult(
            False,
            "Only one image URL is allowed (imgUrls must contain exactly one item)."
        )
    return ValidationResult(True)


async def validate_real_human_image_async(
    image_url: str,
    *,
    settings: Optional[Settings] = None,
    clip_sem=None,
    download_sem=None,
) -> tuple[ValidationResultV1, Optional[Image.Image]]:
    """
    Async validation for user-provided images (Layers 1, 2, 3A).

    Returns (ValidationResultV1, source_img):
    - source_img is the decoded PIL Image when validation passes (ok=True).
    - source_img is None when validation fails early (Layer 1 or download error).

    The caller can reuse source_img (e.g. for skin-tone extraction) to avoid
    downloading the same URL a second time.

    Downloads the image with httpx (non-blocking), then runs CLIP
    classification in a thread (CPU-bound). download_sem caps concurrent
    outbound downloads (I/O-bound — a safety cap against an accidental burst,
    not CPU pressure); clip_sem caps concurrent CPU usage separately.
    """
    import asyncio
    import httpx

    s = settings or _settings

    # Layer 1: Source resolution (instant, no I/O)
    source_result = validate_source_resolution(image_url)
    if not source_result.ok:
        return source_result, None

    # Layer 2: Download async — capped by download_sem to prevent an unbounded
    # burst of simultaneous outbound connections (see DOWNLOAD_CONCURRENCY_LIMIT).
    async def _download() -> Image.Image:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(image_url, headers={"User-Agent": "ghibli-qr/0.1"})
            resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGB")

    try:
        if download_sem:
            async with download_sem:
                img = await _download()
        else:
            img = await _download()
    except Exception as e:
        _validation_logger.warning(
            "Image download failed — code=IMAGE_DOWNLOAD_FAILED url=%s err=%s", image_url, e
        )
        return ValidationResultV1(
            ok=False,
            code="IMAGE_DOWNLOAD_FAILED",
            message=f"Failed to download image: {e}",
            error_type=ErrorType.VALIDATION_ERROR,
            stage=ErrorStage.SOURCE_RESOLUTION,
        ), None

    # Layer 3A: CLIP classification — CPU-bound. Semaphore applied here only.
    if clip_sem:
        async with clip_sem:
            result = await asyncio.to_thread(validate_stage1_human_portrait, img, image_url, s)
    else:
        result = await asyncio.to_thread(validate_stage1_human_portrait, img, image_url, s)

    return result, img
