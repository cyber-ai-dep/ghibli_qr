import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_log = logging.getLogger(__name__)


def _parse_ttl_hours(env_var: str, default: int) -> int:
    """Parse a positive-integer TTL (hours) from an env var.

    Returns the default and logs a warning when the value is missing,
    non-integer, or non-positive. Using a helper keeps individual
    Settings assignments clean and easy to extend with new prefixes later.
    """
    raw = os.getenv(env_var)
    if raw is None:
        return default
    try:
        val = int(raw)
    except (ValueError, TypeError):
        _log.warning("%s has non-integer value %r — using default %dh", env_var, raw, default)
        return default
    if val <= 0:
        _log.warning("%s must be > 0, got %d — using default %dh", env_var, val, default)
        return default
    return val


class Settings:
    # QR Settings
    QR_VERSION = 1  # Ranges from 1 - 40
    QR_FILL_COLOR = "white"
    QR_BACK_COLOR = "#2a2d42"
    QR_SHORT_CODE_LENGTH = int(os.getenv('SHORT_CODE_LENGTH', 8))

    # QR lock proportional sizing ratios (relative to the lock image width)
    QR_LOCK_TARGET_WIDTH_RATIO = 0.28
    QR_LOCK_MIN_WIDTH_RATIO = 0.22
    QR_LOCK_MAX_WIDTH_RATIO = 0.32

    # Paths — static assets and the served temp directory.
    BASE_PATH = Path(__file__).parent.parent
    STATIC_PATH = BASE_PATH / 'static'
    LOCK_PATH = STATIC_PATH / 'lock.png'
    TMP_PATH = STATIC_PATH / 'tmp'

    # Optional: also save final images on THIS machine (not only served via URL).
    # When SAVE_OUTPUT_LOCAL is enabled, every final_ image is additionally written
    # to OUTPUT_DIR. The API response and the served /tmp URL are unchanged.
    SAVE_OUTPUT_LOCAL = os.getenv("SAVE_OUTPUT_LOCAL", "false").lower() in {"1", "true", "yes"}
    OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))

    # Generation model LABELS reported in the API response's "model" field.
    # Kept at the previous values for backward-compatible response contract — the
    # external integration may read this string. The ACTUAL model is ARK_MODEL
    # (see seedream_service); these are response labels only.
    GHIBLI_MODEL = os.getenv("GHIBLI_MODEL", "qwen/image-edit")     # Stage 1 (/v1/ghibli response model)
    COMPOSE_MODEL = os.getenv("COMPOSE_MODEL", "seedream/4.5-edit") # Stage 2 (/v1/ghibli-qr response model)

    # Server Settings — public/base address used to build the returned image URLs.
    DOMAIN = os.getenv("DOMAIN")

    # Validation Settings
    # If enabled, requests without a detectable face are rejected before generation.
    REQUIRE_HUMAN_FACE = os.getenv("REQUIRE_HUMAN_FACE", "true").lower() in {"1", "true", "yes"}
    # Reject if more than this number of faces are detected (set to 0 to disable the limit).
    MAX_FACES = int(os.getenv("MAX_FACES", "1"))
    # Minimum face area ratio (face_bbox_area / image_area). Helps reject tiny/far faces.
    MIN_FACE_AREA_RATIO = float(os.getenv("MIN_FACE_AREA_RATIO", "0.03"))
    # Max concurrent MediaPipe face-detection operations.
    # Controls CPU ceiling on shared servers: lower = less CPU, more queue wait (~2.5s/slot).
    MAX_MEDIAPIPE_CONCURRENCY = int(os.getenv("MAX_MEDIAPIPE_CONCURRENCY", "15"))

    # Max concurrent image-generation submissions to the provider (BytePlus ARK).
    # ARK allows up to 10 concurrent requests per model per primary account; staying
    # below that avoids queueing/429. 8 leaves headroom; lower it if you hit rate limits.
    GENERATION_CONCURRENCY_LIMIT = int(os.getenv("GENERATION_CONCURRENCY_LIMIT", "8"))

    # Enable post-generation identity drift check. Disable when the model triggers
    # false positives consistently. Re-enable with a strong identity-preserving model.
    ENABLE_IDENTITY_CHECK = os.getenv("ENABLE_IDENTITY_CHECK", "false").lower() in {"1", "true", "yes"}

    # TTL for files in static/tmp/ — differentiated by filename prefix.
    # stage1_* and qrlock_* are intermediate assets; final_* are client deliverables.
    # All values must be positive integers (hours). Invalid values fall back to the default.
    STAGE1_TTL_HOURS: int = _parse_ttl_hours("STAGE1_TTL_HOURS", 2)
    QRLOCK_TTL_HOURS: int = _parse_ttl_hours("QRLOCK_TTL_HOURS", 2)
    FINAL_IMAGE_TTL_HOURS: int = _parse_ttl_hours("FINAL_IMAGE_TTL_HOURS", 24)
    # When true, final_ images are never auto-deleted regardless of TTL.
    # Use explicit boolean — never encode "never delete" as a magic TTL value.
    PERSIST_FINAL_IMAGES: bool = os.getenv("PERSIST_FINAL_IMAGES", "false").lower() in {"1", "true", "yes"}

    # Prompts
    PROMPT_PIC_TO_GHIBLI = (
        "Convert this photo into a Studio Ghibli hand-painted illustration. "
        "Apply the full Ghibli visual style: soft watercolor backgrounds, warm painterly color palette, "
        "clean expressive linework, cel-shaded lighting, lush atmospheric depth, and the characteristic "
        "hand-drawn Ghibli texture throughout every surface.\n\n"
        "IDENTITY LOCK — never change these:\n"
        "Same person, same face structure, same skin tone, same ethnicity, same race.\n"
        "Same hairstyle, same facial hair, same expression.\n"
        "Same clothing, same pose, same hands, same background composition.\n"
        "SKIN COLOR IS ABSOLUTE: reproduce the exact skin tone from the photo. "
        "Dark skin stays dark. Light skin stays light. Zero tolerance for lightening or whitening.\n\n"
        "STYLE CHANGE — only these:\n"
        "Render everything as a hand-painted Ghibli illustration.\n"
        "Apply Ghibli color grading, line art, and painterly texture.\n"
        "Make it look like a frame from a Studio Ghibli film.\n\n"
        "DO NOT: replace the face, change ethnicity, lighten/darken skin, "
        "use a generic anime face, beautify, or alter facial proportions.\n\n"
        "Result: the exact same person rendered as a Ghibli film character.\n"
        "Flat solid background RGB(255, 255, 255). No shadows, no gradients, no scenery."
    )

    # Negative prompt for Stage 1 — passed when the model supports it.
    NEGATIVE_PROMPT_PIC_TO_GHIBLI = (
        "photorealistic, photograph, realistic lighting, camera photo, "
        "generic anime face, identity drift, race change, skin tone change, beautification, "
        "face replacement, facial simplification, different person, "
        "altered ethnicity, altered hairstyle, altered expression"
    )

    PROMPT_GHIBLI_LOCK = (
        "Compose these two images: the Ghibli illustrated person from the first image "
        "is holding the QR code lock from the second image with both hands in front of their body.\n\n"
        "The QR code lock must be fully visible, centered, and not cropped — "
        "sharp, high-contrast, square, and fully scannable.\n\n"
        "SKIN COLOR IS ABSOLUTE — this is the most critical rule:\n"
        "The skin tone must exactly match the person in the first image. "
        "Dark skin stays dark. Light skin stays light. "
        "Zero tolerance for lightening, whitening, brightening, or any skin tone shift.\n\n"
        "IDENTITY LOCK — preserve exactly from the first image:\n"
        "Same face, same skin tone, same ethnicity, same race.\n"
        "Same hair, same clothing, same expression, same proportions.\n"
        "Same Ghibli illustration style and painterly texture throughout.\n\n"
        "DO NOT change: face, skin tone, hair, clothing, or Ghibli art style.\n"
        "DO NOT lighten, darken, or shift any colors on the person.\n\n"
        "Clean solid background RGB(255, 255, 255). No shadows, no gradients."
    )


# Warn early so misconfiguration is visible in startup logs, not at first request.
if not Settings.DOMAIN:
    _log.warning(
        "DOMAIN env var is not set. Returned image URLs will be relative/unreachable — "
        "set DOMAIN to this server's reachable address (e.g. http://<host>:<port>)."
    )
if Settings.PERSIST_FINAL_IMAGES:
    _log.info("PERSIST_FINAL_IMAGES=true — final_ images will never be auto-deleted.")
else:
    _log.info("Final image retention TTL: %dh (set PERSIST_FINAL_IMAGES=true to keep indefinitely).", Settings.FINAL_IMAGE_TTL_HOURS)
if Settings.SAVE_OUTPUT_LOCAL:
    _log.info("SAVE_OUTPUT_LOCAL=true — final images are also saved to %s", Settings.OUTPUT_DIR)
