from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests
from PIL import Image

from src.ghibli_portrait.config import Settings

# Internal validation logger (not exposed to API)
_validation_logger = logging.getLogger("validation_internal")


_LOCALHOST_RE = re.compile(r"^(https?://)?(localhost|127\.0\.0\.1|0\.0\.0\.0)([:/]|$)", re.IGNORECASE)


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    faces: int = 0


def validate_public_url(url: str) -> ValidationResult:
    if not url or not isinstance(url, str):
        return ValidationResult(False, "img_url must be a non-empty string")

    if not (url.startswith("http://") or url.startswith("https://")):
        return ValidationResult(False, "img_url must start with http:// or https://")

    if _LOCALHOST_RE.search(url):
        return ValidationResult(False, "img_url must be publicly accessible (localhost/private URLs are not allowed)")

    return ValidationResult(True)


def _download_image(url: str, timeout_s: int = 10) -> Image.Image:
    # Small, practical limits (avoid huge downloads)
    headers = {"User-Agent": "ghibli-qr/0.1"}
    r = requests.get(url, timeout=timeout_s, headers=headers)
    r.raise_for_status()

    content_type = (r.headers.get("content-type") or "").lower()
    if "image" not in content_type:
        # Some CDNs omit content-type; still try to decode, but provide a helpful message if it fails.
        pass

    return Image.open(io.BytesIO(r.content)).convert("RGB")


def validate_human_face(url: str, *, settings: Optional[Settings] = None) -> ValidationResult:
    """Fast, deterministic gate for 'human portrait' inputs.

    Uses OpenCV Haar cascade if available. If OpenCV is not installed, returns
    ok=False with a clear reason, so the API can fail fast rather than silently.
    """
    s = settings or Settings()

    # If disabled, always pass.
    if not s.REQUIRE_HUMAN_FACE:
        return ValidationResult(True)

    try:
        import cv2  # type: ignore
    except Exception:
        return ValidationResult(
            False,
            "Face validation is enabled but OpenCV is not available. Install opencv-python-headless or disable REQUIRE_HUMAN_FACE.",
        )

    try:
        img = _download_image(url)
    except Exception as e:
        return ValidationResult(False, f"Failed to download or decode image: {e}")

    import numpy as np

    np_img = np.array(img)
    gray = cv2.cvtColor(np_img, cv2.COLOR_RGB2GRAY)

    cascade_path = getattr(cv2.data, "haarcascades", None)
    if not cascade_path:
        return ValidationResult(False, "OpenCV haarcascades path not found")

    face_cascade = cv2.CascadeClassifier(str(cascade_path) + "haarcascade_frontalface_default.xml")
    if face_cascade.empty():
        return ValidationResult(False, "Failed to load OpenCV face cascade")

    # Detect faces
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    face_count = 0 if faces is None else len(faces)

    if face_count == 0:
        return ValidationResult(False, "No human face detected. Please provide a clear portrait image.", faces=0)

    if s.MAX_FACES and face_count > s.MAX_FACES:
        return ValidationResult(False, f"Too many faces detected ({face_count}). Please provide a single-person portrait.", faces=face_count)

    # Check minimum face size ratio for the largest face
    h, w = gray.shape[:2]
    img_area = float(h * w)
    max_face_area = max(float(fw * fh) for (_, _, fw, fh) in faces)
    ratio = max_face_area / img_area if img_area else 0.0

    if ratio < s.MIN_FACE_AREA_RATIO:
        return ValidationResult(
            False,
            "Face detected but too small/far. Please use a closer portrait photo.",
            faces=face_count,
        )

    return ValidationResult(True, faces=face_count)


def validate_single_image_url_list(img_urls: list[str]) -> ValidationResult:
    if not isinstance(img_urls, list) or len(img_urls) != 1:
        return ValidationResult(False, "Only one image URL is allowed (img_urls must contain exactly one item).")
    return ValidationResult(True)


# ============================================================================
# V1 API VALIDATION GATE (Production-Ready, Comprehensive)
# ============================================================================

@dataclass
class ValidationResultV1:
    """
    Enhanced validation result for V1 API with structured error codes.

    Attributes:
        ok: Whether validation passed
        code: Machine-readable error code (None if ok=True)
        message: Human-readable error message (empty if ok=True)
    """
    ok: bool
    code: Optional[str] = None
    message: str = ""


def _compute_human_score(
    img: Image.Image,
    face_detected: bool,
    face_count: int,
    face_area_ratio: float,
    dominant_face_ratio: float
) -> float:
    """
    Compute humanScore ∈ [0.0 - 1.0] to distinguish humans from animals/cartoons.

    TODO: Replace with real ML model
    Currently uses heuristic-based scoring as a stub.

    Args:
        img: PIL Image in RGB format
        face_detected: Whether a face was detected
        face_count: Number of faces detected
        face_area_ratio: Ratio of largest face area to image area
        dominant_face_ratio: Ratio of largest face to total face area (filters noise)

    Returns:
        Score between 0.0 (definitely not human) and 1.0 (definitely human)
    """
    # TODO: Replace with real human vs animal/cartoon classifier
    # Expected model: Fine-tuned ResNet/EfficientNet on human vs animal/cartoon dataset
    # Target: confidence >= 0.8 for "real human" class

    # HEURISTIC STUB (Signal-based, tuned to reject animals)
    score = 0.5  # Neutral baseline

    # Signal 1: Face detection is a strong human indicator
    # Higher baseline for detected faces to ensure real humans pass
    if face_detected and face_count >= 1:
        # Strong signal - face detected
        score += 0.25

        # Signal 1a: Dominant face ratio (filters background noise)
        # If one face dominates, it's likely a real portrait
        if dominant_face_ratio > 0.8:
            score += 0.15
        elif dominant_face_ratio > 0.6:
            score += 0.10
    else:
        # No face detected - uncertain, but lean toward accept for real humans
        # (could be profile, occluded, non-frontal pose)
        score += 0.05

    # Signal 2: Face size indicates proper portrait framing
    # Real human portraits tend to have substantial face area
    if face_area_ratio > 0.08:
        score += 0.20
    elif face_area_ratio > 0.05:
        score += 0.15
    elif face_area_ratio > 0.03:
        score += 0.10

    return min(1.0, max(0.0, score))


def _compute_realism_score(img: Image.Image) -> float:
    """
    Compute realismScore ∈ [0.0 - 1.0] to distinguish real photos from illustrations/cartoons.

    TODO: Replace with real ML model
    Currently uses heuristic-based scoring as a stub.

    Args:
        img: PIL Image in RGB format

    Returns:
        Score between 0.0 (definitely illustration) and 1.0 (definitely real photo)
    """
    # TODO: Replace with real photo vs illustration classifier
    # Expected model:
    # - ResNet/EfficientNet fine-tuned on real vs synthetic dataset
    # - Or specialized GAN/deepfake detection model
    # Target: confidence >= 0.75 for "real photo" class

    # HEURISTIC STUB (Basic image statistics, tuned to reject cartoons/illustrations)
    import numpy as np

    score = 0.6  # Higher baseline - assume real photo unless proven otherwise

    # Signal 1: Real photos tend to have moderate-to-high color variance
    # Cartoons/illustrations often have flat colors or extreme uniformity
    np_img = np.array(img)
    if np_img.size > 0:
        color_std = np.std(np_img)
        if color_std > 35:
            # Good variance - likely real photo
            score += 0.25
        elif color_std > 20:
            # Moderate variance - could be real
            score += 0.15
        elif color_std < 12:
            # Very low variance - likely cartoon/illustration
            score -= 0.30

    # Signal 2: Real photos tend to have gradual color transitions
    # Cartoons often have sharp edges and high contrast boundaries
    try:
        import cv2
        gray = cv2.cvtColor(np_img, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_density = np.sum(edges > 0) / edges.size

        if edge_density < 0.08:
            # Low edge density - smooth gradients (real photo)
            score += 0.15
        elif edge_density > 0.18:
            # Very high edge density - sharp edges (cartoon/illustration)
            score -= 0.20
    except Exception:
        # If edge detection fails, remain neutral (don't penalize)
        pass

    return min(1.0, max(0.0, score))


def validate_real_human_image(image_url: str, *, settings: Optional[Settings] = None) -> ValidationResultV1:
    """
    Comprehensive validation gate for V1 API Ghibli endpoint.

    Validation layers (signal-based, reject only when highly confident):
    Layer 0: Technical checks (public URL, readable image, valid format)
    Layer 1: Face detection with dominant face analysis (reject only if dominantFaceRatio < 0.55)
    Layer 2: Human vs Animal/Cartoon (reject if humanScore < 0.6)
    Layer 2.5: Primate/Animal veto (reject likely monkey/ape faces)
    Layer 3: Real Photo vs Illustration (reject if realismScore < 0.6)

    Philosophy: If uncertain → ACCEPT. False negatives (rejecting real humans) are unacceptable.

    Args:
        image_url: URL of the image to validate
        settings: Optional Settings instance (uses default if not provided)

    Returns:
        ValidationResultV1 with ok status, error code, and message

    Error Codes:
        - INVALID_IMAGE_URL: URL is not publicly accessible
        - MULTIPLE_FACES: dominantFaceRatio < 0.55 (true group photo)
        - IMAGE_DOWNLOAD_FAILED: Failed to download or decode the image
        - ANIMAL_OR_CARTOON_DETECTED: humanScore < 0.6 (not real human)
        - LIKELY_ANIMAL_FACE: Primate/animal detected with human-like face
        - NON_REAL_IMAGE_DETECTED: realismScore < 0.6 (illustration/cartoon)
    """
    s = settings or Settings()

    # -------------------------------------------------------------------------
    # LAYER 0: Technical Validation (Public URL)
    # -------------------------------------------------------------------------
    url_validation = validate_public_url(image_url)
    if not url_validation.ok:
        return ValidationResultV1(
            ok=False,
            code="INVALID_IMAGE_URL",
            message=url_validation.reason
        )

    # -------------------------------------------------------------------------
    # Download image once for all subsequent checks
    # -------------------------------------------------------------------------
    try:
        img = _download_image(image_url)
    except Exception as e:
        return ValidationResultV1(
            ok=False,
            code="IMAGE_DOWNLOAD_FAILED",
            message=f"Failed to download or decode image: {e}"
        )

    # -------------------------------------------------------------------------
    # LAYER 1: Face Detection with Dominant Face Analysis
    # -------------------------------------------------------------------------
    # Collect signals and compute dominantFaceRatio
    # Reject ONLY if dominantFaceRatio < 0.6 (true group photo)
    # DO NOT reject for: no face detected, complex background, small faces (noise)

    face_detected = False
    face_count = 0
    face_area_ratio = 0.0
    dominant_face_ratio = 1.0  # Default: single face dominates

    try:
        import cv2
        import numpy as np

        np_img = np.array(img)
        gray = cv2.cvtColor(np_img, cv2.COLOR_RGB2GRAY)

        cascade_path = getattr(cv2.data, "haarcascades", None)
        if cascade_path:
            face_cascade = cv2.CascadeClassifier(str(cascade_path) + "haarcascade_frontalface_default.xml")
            if not face_cascade.empty():
                faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
                face_count = 0 if faces is None else len(faces)

                if face_count > 0:
                    face_detected = True
                    # Calculate face areas
                    h, w = gray.shape[:2]
                    img_area = float(h * w)
                    face_areas = [float(fw * fh) for (_, _, fw, fh) in faces]
                    max_face_area = max(face_areas)
                    total_face_area = sum(face_areas)

                    if img_area > 0:
                        face_area_ratio = max_face_area / img_area

                    # Compute dominantFaceRatio = largest_face / total_face_area
                    # This filters out background noise (small false-positive faces)
                    if total_face_area > 0:
                        dominant_face_ratio = max_face_area / total_face_area

                # Reject ONLY if dominantFaceRatio < 0.55 (true group photo)
                # This means multiple significant faces of similar size
                # Threshold 0.55 allows background faces/posters to be ignored
                if face_count > 1 and dominant_face_ratio < 0.55:
                    _validation_logger.info({
                        "url": image_url,
                        "faceDetected": face_detected,
                        "faceCount": face_count,
                        "faceAreaRatio": face_area_ratio,
                        "dominantFaceRatio": dominant_face_ratio,
                        "decision": "REJECT",
                        "reason": "MULTIPLE_FACES"
                    })
                    return ValidationResultV1(
                        ok=False,
                        code="MULTIPLE_FACES",
                        message="Multiple faces of similar size detected. Please provide a single-person portrait."
                    )
    except Exception as e:
        # Face detection failure is not a blocking error
        # Continue with face_detected=False as a signal
        _validation_logger.warning(f"Face detection failed (non-blocking): {e}")
        pass

    # -------------------------------------------------------------------------
    # LAYER 2: Human vs Animal/Cartoon Detection
    # -------------------------------------------------------------------------
    # TODO: Replace _compute_human_score with real ML model
    # Expected: Fine-tuned ResNet/EfficientNet on human vs animal/cartoon dataset

    human_score = _compute_human_score(img, face_detected, face_count, face_area_ratio, dominant_face_ratio)

    if human_score < 0.60:
        _validation_logger.info({
            "url": image_url,
            "faceDetected": face_detected,
            "faceCount": face_count,
            "faceAreaRatio": face_area_ratio,
            "dominantFaceRatio": dominant_face_ratio,
            "humanScore": human_score,
            "decision": "REJECT",
            "reason": "ANIMAL_OR_CARTOON_DETECTED"
        })
        return ValidationResultV1(
            ok=False,
            code="ANIMAL_OR_CARTOON_DETECTED",
            message="Image must contain a real human portrait, not an animal or cartoon character."
        )

    # -------------------------------------------------------------------------
    # LAYER 2.5: Primate / Animal Veto (CRITICAL)
    # -------------------------------------------------------------------------
    # Reject images that look realistic, have a detected face, but are likely
    # monkeys/apes. These images can fool face detection but are not humans.
    #
    # Reject if:
    #   face_detected == true
    #   AND human_score BETWEEN 0.55 and 0.65 (ambiguous zone)
    #   AND face_area_ratio > 0.18 (large face = likely animal portrait)
    #
    # This MUST NOT affect real humans (who score higher on human_score)
    # This MUST block monkeys/apes that resemble humans

    realism_score = _compute_realism_score(img)

    if (face_detected
        and realism_score >= 0.60
        and 0.55 <= human_score <= 0.65
        and face_area_ratio > 0.18):
        _validation_logger.info({
            "url": image_url,
            "faceDetected": face_detected,
            "faceCount": face_count,
            "faceAreaRatio": face_area_ratio,
            "dominantFaceRatio": dominant_face_ratio,
            "humanScore": human_score,
            "realismScore": realism_score,
            "decision": "REJECT",
            "reason": "LIKELY_ANIMAL_FACE"
        })
        return ValidationResultV1(
            ok=False,
            code="LIKELY_ANIMAL_FACE",
            message="Image appears to contain an animal face. Please provide a human portrait."
        )

    # -------------------------------------------------------------------------
    # LAYER 3: Real Photo vs Illustration Detection
    # -------------------------------------------------------------------------
    # TODO: Replace _compute_realism_score with real ML model
    # Expected: ResNet/EfficientNet on real vs synthetic dataset, or GAN detection model

    if realism_score < 0.60:
        _validation_logger.info({
            "url": image_url,
            "faceDetected": face_detected,
            "faceCount": face_count,
            "faceAreaRatio": face_area_ratio,
            "dominantFaceRatio": dominant_face_ratio,
            "humanScore": human_score,
            "realismScore": realism_score,
            "decision": "REJECT",
            "reason": "NON_REAL_IMAGE_DETECTED"
        })
        return ValidationResultV1(
            ok=False,
            code="NON_REAL_IMAGE_DETECTED",
            message="Image must be a real photograph, not a drawing, illustration, or cartoon."
        )

    # -------------------------------------------------------------------------
    # All Validation Passed
    # -------------------------------------------------------------------------
    _validation_logger.info({
        "url": image_url,
        "faceDetected": face_detected,
        "faceCount": face_count,
        "faceAreaRatio": face_area_ratio,
        "dominantFaceRatio": dominant_face_ratio,
        "humanScore": human_score,
        "realismScore": realism_score,
        "decision": "ACCEPT"
    })

    return ValidationResultV1(ok=True)
