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

# MediaPipe Tasks API imports
try:
    from mediapipe.tasks.python import vision
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

_validation_logger = logging.getLogger("validation_internal")

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
        diversity < 0.20  AND  uniformity > 0.50
        Real photos never reach uniformity > 0.50 regardless of beauty mode.

    Rule C — high color dominance (3D rendered game faces, CGI):
        diversity < 0.10  AND  max_color_freq > 0.08
        Rendered faces have large flat-color blocks where one exact RGB value
        covers >8% of pixels. Real photos — even heavily processed — have no
        single exact color dominating more than ~5% of the face region.

    Rule D — safety net for extreme renders:
        diversity < 0.10  AND  uniformity > 0.45
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
        if diversity < 0.20 and uniformity > 0.50:
            return True

        # Rule C: 3D rendered game face / high color dominance
        if diversity < 0.10 and max_color_freq > 0.08:
            return True

        # Rule D: safety net
        if diversity < 0.10 and uniformity > 0.45:
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

    MediaPipe-only face detection with strict rules:
    - If MediaPipe detects a face → run synthetic-image check, then accept if real
    - Multiple faces rejected only when secondary is visually significant
    - Animal/cartoon rejection only when ZERO faces detected
    - Detector failure returns SYSTEM_ERROR, not validation error
    """
    s = settings or _settings

    if not s.REQUIRE_HUMAN_FACE:
        return ValidationResultV1(ok=True, stage=ErrorStage.STAGE1_GHIBLI)

    detection = _detect_faces(img)

    if not detection.ok:
        _validation_logger.error({
            "url": image_url,
            "error": detection.error,
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

    face_count = detection.face_count
    faces = detection.faces
    face_detected = face_count > 0

    if face_detected:
        primary = faces[0]

        if face_count > 1:
            # Reject if any secondary face covers >= 2% of the image AND has confidence >= 0.45.
            # 2% area floor: filters background clutter and MediaPipe ghost boxes that appear
            # on reflections, clothing patterns, or partially occluded objects in single-person
            # portraits. A real second person in frame will always be >= 2%. Audit confirmed
            # zero new false positives vs the 37-image dataset when lowered from 3% → 2%;
            # fixes threemens.jpg (faces at 2.35–2.75% area) that was missed at 3%.
            # 0.45 confidence floor: above the 0.35 detection minimum — low-confidence
            # secondary detections in single-person photos are suppressed.
            significant_secondary = [
                f for f in faces[1:]
                if f.get("area_ratio", 0.0) >= 0.02 and f.get("score", 0.0) >= 0.45
            ]
            if significant_secondary:
                _validation_logger.info({
                    "url": image_url,
                    "faceCount": face_count,
                    "significantSecondary": len(significant_secondary),
                    "decision": "REJECT",
                    "reason": "MULTIPLE_FACES",
                })
                return ValidationResultV1(
                    ok=False,
                    code="MULTIPLE_FACES",
                    message="Multiple human faces detected. Please provide a single-person portrait.",
                    error_type=ErrorType.VALIDATION_ERROR,
                    stage=ErrorStage.STAGE1_GHIBLI
                )

        # Reject synthetic renders (3D game characters, cartoons) that trick MediaPipe.
        face_bbox = primary.get("bbox")
        if face_bbox and _is_synthetic_face(img, face_bbox):
            _validation_logger.info({
                "url": image_url,
                "faceCount": face_count,
                "decision": "REJECT",
                "reason": "SYNTHETIC_IMAGE_DETECTED"
            })
            return ValidationResultV1(
                ok=False,
                code="NOT_REAL_PHOTO",
                message="Image appears to be a 3D render, game character, or cartoon. Please provide a real human portrait photo.",
                error_type=ErrorType.VALIDATION_ERROR,
                stage=ErrorStage.STAGE1_GHIBLI
            )

        _validation_logger.info({"url": image_url, "faceCount": face_count, "decision": "ACCEPT"})
        return ValidationResultV1(ok=True, stage=ErrorStage.STAGE1_GHIBLI)

    _validation_logger.info({"url": image_url, "faceCount": 0, "decision": "REJECT", "reason": "NO_FACE_DETECTED"})
    return ValidationResultV1(
        ok=False,
        code="NO_FACE_DETECTED",
        message="No human face detected. Please provide a clear portrait photo of a person.",
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

    except Exception:
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
    mediapipe_sem=None,
) -> tuple[ValidationResultV1, Optional[Image.Image]]:
    """
    Async validation for user-provided images (Layers 1, 2, 3A).

    Returns (ValidationResultV1, source_img):
    - source_img is the decoded PIL Image when validation passes (ok=True).
    - source_img is None when validation fails early (Layer 1 or download error).

    The caller can reuse source_img (e.g. for identity drift check) to avoid
    downloading the same URL a second time.

    Downloads the image with httpx (non-blocking), then runs MediaPipe
    face detection in a thread (CPU-bound). mediapipe_sem caps concurrent
    CPU usage without blocking downloads.
    """
    import asyncio
    import httpx

    s = settings or _settings

    # Layer 1: Source resolution (instant, no I/O)
    source_result = validate_source_resolution(image_url)
    if not source_result.ok:
        return source_result, None

    # Layer 2: Download async — no thread, no semaphore, unlimited concurrency
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(image_url, headers={"User-Agent": "ghibli-qr/0.1"})
            resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        return ValidationResultV1(
            ok=False,
            code="IMAGE_DOWNLOAD_FAILED",
            message=f"Failed to download image: {e}",
            error_type=ErrorType.VALIDATION_ERROR,
            stage=ErrorStage.SOURCE_RESOLUTION,
        ), None

    # Layer 3A: MediaPipe — CPU-bound (~2s). Semaphore applied here only.
    if mediapipe_sem:
        async with mediapipe_sem:
            result = await asyncio.to_thread(validate_stage1_human_portrait, img, image_url, s)
    else:
        result = await asyncio.to_thread(validate_stage1_human_portrait, img, image_url, s)

    return result, img
