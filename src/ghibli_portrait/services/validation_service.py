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
import os
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

# Internal validation logger (not exposed to API)
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


@dataclass
class ImageDecodeResult:
    """Result of image download and decode operation."""
    ok: bool
    image: Optional[Image.Image] = None
    code: Optional[str] = None
    message: str = ""


# ============================================================================
# LAYER 1: IMAGE SOURCE RESOLUTION
# ============================================================================

def validate_public_url(url: str) -> ValidationResult:
    """
    Layer 1: Validate that URL is a publicly accessible HTTP/HTTPS URL.

    This layer does NOT:
    - Download the image
    - Decode the image
    - Perform any content validation

    Args:
        url: URL string to validate

    Returns:
        ValidationResult with ok status and reason
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
    """
    Layer 1: Image source resolution for V1 API.

    Validates URL is publicly accessible without downloading.

    Args:
        url: URL string to validate

    Returns:
        ValidationResultV1 with structured error info
    """
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
# LAYER 2: ACCESSIBILITY & DECODE
# ============================================================================

def _download_image(url: str, timeout_s: int = 10) -> Image.Image:
    """
    Download and decode an image from URL.

    Internal helper - raises exceptions on failure.

    Args:
        url: Image URL
        timeout_s: Download timeout in seconds

    Returns:
        PIL Image in RGB format

    Raises:
        requests.RequestException: Download failed
        PIL.UnidentifiedImageError: Image decode failed
    """
    headers = {"User-Agent": "ghibli-qr/0.1"}
    response = requests.get(url, timeout=timeout_s, headers=headers)
    response.raise_for_status()

    return Image.open(io.BytesIO(response.content)).convert("RGB")


def validate_image_accessibility(url: str, timeout_s: int = 10) -> ImageDecodeResult:
    """
    Layer 2: Download and decode image to verify accessibility.

    This layer does NOT:
    - Perform face detection
    - Perform human validation
    - Make any content-based decisions

    Args:
        url: Image URL to download
        timeout_s: Download timeout

    Returns:
        ImageDecodeResult with decoded image or error
    """
    try:
        img = _download_image(url, timeout_s)
        return ImageDecodeResult(ok=True, image=img)
    except requests.RequestException as e:
        return ImageDecodeResult(
            ok=False,
            code="IMAGE_DOWNLOAD_FAILED",
            message=f"Failed to download image: {e}"
        )
    except Exception as e:
        return ImageDecodeResult(
            ok=False,
            code="IMAGE_DECODE_FAILED",
            message=f"Failed to decode image: {e}"
        )


# ============================================================================
# LAYER 3A: STAGE 1 VALIDATION (Ghibli / qwen)
# ============================================================================

def _ensure_model_downloaded() -> Optional[str]:
    """
    Ensure the MediaPipe face detection model is downloaded and cached.

    Returns:
        Path to the model file, or None if download failed.
    """
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

    Returns:
        FaceDetectionResult with detection info or error state.

    Primary face selection priority:
        1. Largest bounding box area
        2. Highest confidence
        3. Closest to image center
    """
    # Check MediaPipe availability
    if not _MEDIAPIPE_AVAILABLE:
        return FaceDetectionResult(
            ok=False,
            error="MediaPipe is not available"
        )

    # Ensure model is downloaded
    model_path = _ensure_model_downloaded()
    if not model_path:
        return FaceDetectionResult(
            ok=False,
            error="Failed to load face detection model"
        )

    try:
        import numpy as np

        np_img = np.array(img)
        if np_img.size == 0:
            return FaceDetectionResult(ok=True, face_count=0)

        height, width = np_img.shape[:2]
        if height == 0 or width == 0:
            return FaceDetectionResult(ok=True, face_count=0)

        # Create MediaPipe Image from numpy array
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np_img)

        # Configure and run face detector
        base_options = BaseOptions(model_asset_path=model_path)
        options = FaceDetectorOptions(
            base_options=base_options,
            min_detection_confidence=0.35
        )

        with FaceDetector.create_from_options(options) as detector:
            detection_result = detector.detect(mp_image)

        detections = detection_result.detections or []
        if not detections:
            return FaceDetectionResult(ok=True, face_count=0)

        img_area = float(height * width)
        center_norm = math.sqrt(2) / 2  # max possible normalized center distance

        faces_info: List[Dict] = []
        for det in detections:
            # Get confidence score
            score = det.categories[0].score if det.categories else 0.0

            # Get bounding box (in pixels)
            bbox = det.bounding_box
            x = max(0, bbox.origin_x)
            y = max(0, bbox.origin_y)
            w = max(1, bbox.width)
            h = max(1, bbox.height)

            area = float(w * h)
            area_ratio = area / img_area if img_area > 0 else 0.0

            # Calculate center distance (normalized)
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

        # Sort by primary face selection priority:
        # 1. Largest area, 2. Highest confidence, 3. Closest to center
        faces_info.sort(
            key=lambda f: (-f["area"], -f["score"], f["center_distance"])
        )

        face_count = len(faces_info)
        primary_area_ratio = faces_info[0]["area_ratio"] if faces_info else 0.0

        return FaceDetectionResult(
            ok=True,
            face_count=face_count,
            primary_face_area_ratio=primary_area_ratio,
            faces=faces_info
        )

    except Exception as e:
        _validation_logger.error(f"Face detection failed: {e}")
        return FaceDetectionResult(
            ok=False,
            error=f"Face detection runtime error: {e}"
        )


def _compute_human_score(
    face_detected: bool,
    face_count: int,
    face_area_ratio: float,
    dominant_face_ratio: float
) -> float:
    """
    Compute humanScore to distinguish humans from animals/cartoons.

    Args:
        face_detected: Whether a face was detected
        face_count: Number of faces detected
        face_area_ratio: Ratio of largest face area to image area
        dominant_face_ratio: Ratio of largest face to total face area

    Returns:
        Score between 0.0 (not human) and 1.0 (definitely human)
    """
    score = 0.5  # Neutral baseline

    if face_detected and face_count >= 1:
        score += 0.25

        if dominant_face_ratio > 0.8:
            score += 0.15
        elif dominant_face_ratio > 0.6:
            score += 0.10
    else:
        score += 0.05

    if face_area_ratio > 0.08:
        score += 0.20
    elif face_area_ratio > 0.05:
        score += 0.15
    elif face_area_ratio > 0.03:
        score += 0.10

    return min(1.0, max(0.0, score))


def _compute_realism_score(img: Image.Image) -> float:
    """
    Compute realismScore to distinguish real photos from illustrations.

    Args:
        img: PIL Image in RGB format

    Returns:
        Score between 0.0 (illustration) and 1.0 (real photo)
    """
    import numpy as np

    score = 0.6  # Baseline - assume real unless proven otherwise

    np_img = np.array(img)
    if np_img.size > 0:
        color_std = np.std(np_img)
        if color_std > 35:
            score += 0.25
        elif color_std > 20:
            score += 0.15
        elif color_std < 12:
            score -= 0.30

    try:
        import cv2
        gray = cv2.cvtColor(np_img, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_density = np.sum(edges > 0) / edges.size

        if edge_density < 0.08:
            score += 0.15
        elif edge_density > 0.18:
            score -= 0.20
    except Exception:
        pass

    return min(1.0, max(0.0, score))


def validate_stage1_human_portrait(
    img: Image.Image,
    image_url: str,
    settings: Optional[Settings] = None
) -> ValidationResultV1:
    """
    Layer 3A: Stage 1 (Ghibli) human portrait validation.

    MediaPipe-only face detection with strict rules:
    - If MediaPipe detects a face → image is treated as human (no further rejection)
    - Multiple faces rejected only when secondary is visually significant
    - Animal/cartoon rejection only when ZERO faces detected
    - Detector failure returns SYSTEM_ERROR, not validation error

    Args:
        img: Pre-decoded PIL Image (from Layer 2)
        image_url: Original URL for logging
        settings: Optional Settings instance

    Returns:
        ValidationResultV1 with structured error info
    """
    s = settings or Settings()

    # If validation is disabled, pass through
    if not s.REQUIRE_HUMAN_FACE:
        return ValidationResultV1(ok=True, stage=ErrorStage.STAGE1_GHIBLI)

    # Run MediaPipe face detection
    detection = _detect_faces(img)

    # Handle detector failure as SYSTEM_ERROR (not validation error)
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

    # =========================================================================
    # RULE: If MediaPipe detected at least one face, treat as REAL HUMAN
    # No animal/cartoon/realism checks apply after this point
    # =========================================================================
    if face_detected:
        primary = faces[0]

        # Check for multiple prominent faces
        # Reject ONLY if secondary face is visually significant:
        # - area ≥ 65% of primary face area
        # - confidence ≥ 60% of primary face confidence
        if face_count > 1:
            secondary = faces[1]
            primary_area = primary.get("area", 1.0)
            secondary_area = secondary.get("area", 0.0)
            primary_score = primary.get("score", 1.0)
            secondary_score = secondary.get("score", 0.0)

            area_ratio = secondary_area / primary_area if primary_area > 0 else 0.0
            confidence_ratio = secondary_score / primary_score if primary_score > 0 else 0.0

            if area_ratio >= 0.65 and confidence_ratio >= 0.60:
                _validation_logger.info({
                    "url": image_url,
                    "faceCount": face_count,
                    "secondaryAreaRatio": area_ratio,
                    "secondaryConfidenceRatio": confidence_ratio,
                    "decision": "REJECT",
                    "reason": "MULTIPLE_PROMINENT_FACES"
                })
                return ValidationResultV1(
                    ok=False,
                    code="MULTIPLE_FACES",
                    message="Multiple prominent human faces detected. Please provide a single-person portrait.",
                    error_type=ErrorType.VALIDATION_ERROR,
                    stage=ErrorStage.STAGE1_GHIBLI
                )

        # Face detected → ACCEPT (face size is NOT a rejection criterion)
        # Cropping and framing are handled in Stage 2, not Stage 1
        _validation_logger.info({
            "url": image_url,
            "faceCount": face_count,
            "decision": "ACCEPT"
        })
        return ValidationResultV1(ok=True, stage=ErrorStage.STAGE1_GHIBLI)

    # =========================================================================
    # NO FACE DETECTED: Reject as non-human (animal/cartoon/illustration)
    # MediaPipe is the single source of truth - zero faces = non-human
    # =========================================================================
    _validation_logger.info({
        "url": image_url,
        "faceCount": 0,
        "decision": "REJECT",
        "reason": "NO_FACE_DETECTED"
    })
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

    CRITICAL: Stage 1 output is TRUSTED.

    This layer does NOT:
    - Download the Stage 1 output
    - Decode the Stage 1 output
    - Perform face detection
    - Perform human validation
    - Reprocess the image in any way

    Only performs minimal sanity check that URL is non-empty.

    Args:
        stage1_output_url: URL from Stage 1 result (TRUSTED)

    Returns:
        ValidationResultV1 (always passes for valid URLs)
    """
    if not stage1_output_url or not isinstance(stage1_output_url, str):
        return ValidationResultV1(
            ok=False,
            code="INVALID_STAGE1_OUTPUT",
            message="Stage 1 output URL is missing or invalid",
            error_type=ErrorType.SYSTEM_ERROR,
            stage=ErrorStage.STAGE2_QR
        )

    # Stage 1 output is TRUSTED - no further validation
    return ValidationResultV1(ok=True, stage=ErrorStage.STAGE2_QR)


# ============================================================================
# COMPOSITE VALIDATION FUNCTIONS
# ============================================================================

def validate_single_image_url_list(img_urls: list[str]) -> ValidationResult:
    """
    Legacy helper: Validate that exactly one image URL is provided.

    Args:
        img_urls: List of image URLs

    Returns:
        ValidationResult with ok status and reason
    """
    if not isinstance(img_urls, list) or len(img_urls) != 1:
        return ValidationResult(
            False,
            "Only one image URL is allowed (imgUrls must contain exactly one item)."
        )
    return ValidationResult(True)


def validate_real_human_image(
    image_url: str,
    *,
    settings: Optional[Settings] = None
) -> ValidationResultV1:
    """
    Comprehensive validation for user-provided images (Stage 1 input).

    Executes Layers 1, 2, and 3A in sequence:
    - Layer 1: Source resolution (URL validation)
    - Layer 2: Accessibility & decode (download and decode)
    - Layer 3A: Human portrait validation (face/human checks)

    Args:
        image_url: User-provided image URL
        settings: Optional Settings instance

    Returns:
        ValidationResultV1 with structured error info
    """
    s = settings or Settings()

    # Layer 1: Source resolution
    source_result = validate_source_resolution(image_url)
    if not source_result.ok:
        return source_result

    # Layer 2: Accessibility & decode
    decode_result = validate_image_accessibility(image_url)
    if not decode_result.ok:
        return ValidationResultV1(
            ok=False,
            code=decode_result.code,
            message=decode_result.message,
            error_type=ErrorType.VALIDATION_ERROR,
            stage=ErrorStage.SOURCE_RESOLUTION
        )

    # Layer 3A: Stage 1 human portrait validation
    return validate_stage1_human_portrait(decode_result.image, image_url, settings=s)


async def validate_real_human_image_async(
    image_url: str,
    *,
    settings: Optional[Settings] = None,
    mediapipe_sem=None,
) -> ValidationResultV1:
    """
    Async version of validate_real_human_image.

    Downloads the image with httpx (non-blocking), then runs MediaPipe
    face detection in a thread (CPU-bound). Avoids occupying the thread
    pool during the network download (~5-15s).

    mediapipe_sem: optional asyncio.Semaphore applied only around the
    MediaPipe thread call (~2s), not the download (~10s). This allows
    unlimited concurrent downloads while still capping CPU usage.
    """
    import asyncio
    import httpx

    s = settings or Settings()

    # Layer 1: Source resolution (instant, no I/O)
    source_result = validate_source_resolution(image_url)
    if not source_result.ok:
        return source_result

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
        )

    # Layer 3A: MediaPipe — CPU-bound (~2s). Semaphore applied here only.
    if mediapipe_sem:
        async with mediapipe_sem:
            return await asyncio.to_thread(validate_stage1_human_portrait, img, image_url, settings=s)
    return await asyncio.to_thread(validate_stage1_human_portrait, img, image_url, settings=s)


def validate_human_face(url: str, *, settings: Optional[Settings] = None) -> ValidationResult:
    """
    Legacy validation function for backward compatibility.

    Uses MediaPipe Tasks API for face detection (CPU-only).

    Args:
        url: Image URL
        settings: Optional Settings instance

    Returns:
        ValidationResult with ok status, reason, and face count
    """
    s = settings or Settings()

    if not s.REQUIRE_HUMAN_FACE:
        return ValidationResult(True)

    try:
        img = _download_image(url)
    except Exception as e:
        return ValidationResult(False, f"Failed to download or decode image: {e}")

    detection = _detect_faces(img)

    # Handle detector failure
    if not detection.ok:
        return ValidationResult(
            False,
            f"Face detection failed: {detection.error}",
            faces=0
        )

    face_count = detection.face_count

    if face_count == 0:
        return ValidationResult(
            False,
            "No human face detected. Please provide a clear portrait image.",
            faces=0
        )

    if s.MAX_FACES and face_count > s.MAX_FACES:
        return ValidationResult(
            False,
            f"Too many faces detected ({face_count}). Please provide a single-person portrait.",
            faces=face_count
        )

    # Face detected → ACCEPT (face size is NOT a rejection criterion)
    return ValidationResult(True, faces=face_count)
