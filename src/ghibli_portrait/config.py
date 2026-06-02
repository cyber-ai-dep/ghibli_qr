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
    QR_VERSION = 1 # Ranges from 1 - 40
    QR_FILL_COLOR = "white"
    QR_BACK_COLOR = "#2a2d42"
    QR_SHORT_CODE_LENGTH = int(os.getenv('SHORT_CODE_LENGTH', 8))

    # QR lock proportional sizing ratios (relative to the lock image width)
    QR_LOCK_TARGET_WIDTH_RATIO = 0.28
    QR_LOCK_MIN_WIDTH_RATIO = 0.22
    QR_LOCK_MAX_WIDTH_RATIO = 0.32

    BASE_PATH = Path(__file__).parent.parent
    STATIC_PATH = BASE_PATH / 'static'
    LOCK_PATH = STATIC_PATH / 'lock.png'
    TMP_PATH = STATIC_PATH / 'tmp'

    # Kie Settings
    KIE_API_KEY = os.getenv("KIE_API_KEY")
    # Backward-compat single model (legacy)
    KIE_IMG_MODEL = os.getenv("KIE_IMG_MODEL")

    # Stage 1 model — recommended: flux-kontext-pro (subject-consistent style transfer)
    #   flux-kontext-pro  — balanced quality + speed, preserves subject identity
    #   flux-kontext-max  — highest quality, slower
    #   qwen/image-edit   — legacy fallback, known to drift identity
    # Stage 2 model — seedream handles multi-image composition (QR lock overlay)
    KIE_GHIBLI_MODEL = os.getenv("KIE_GHIBLI_MODEL", KIE_IMG_MODEL)
    KIE_COMPOSE_MODEL = os.getenv("KIE_COMPOSE_MODEL", KIE_IMG_MODEL)
    KIE_CREATE_TASK_API = 'https://api.kie.ai/api/v1/jobs/createTask'

    # Server Settings
    DOMAIN = os.getenv("DOMAIN")
    CALL_BACK = (DOMAIN.rstrip('/') if DOMAIN else "") +  '/v1/ghibli/callback'

    # Validation Settings
    # If enabled, requests without a detectable face will be rejected before calling KIE.
    REQUIRE_HUMAN_FACE = os.getenv("REQUIRE_HUMAN_FACE", "true").lower() in {"1", "true", "yes"}
    # Reject if more than this number of faces are detected (set to 0 to disable the limit).
    MAX_FACES = int(os.getenv("MAX_FACES", "1"))
    # Minimum face area ratio (face_bbox_area / image_area). Helps reject tiny/far faces.
    MIN_FACE_AREA_RATIO = float(os.getenv("MIN_FACE_AREA_RATIO", "0.03"))
    # Max concurrent MediaPipe face-detection operations.
    # Controls CPU ceiling on shared servers: lower = less CPU, more queue wait (~2.5s/slot).
    MAX_MEDIAPIPE_CONCURRENCY = int(os.getenv("MAX_MEDIAPIPE_CONCURRENCY", "15"))
    # Max concurrent KIE API task submissions (all stages — Stage 1, Stage 2, identity retry —
    # share one limit because they share the same API key and the same KIE rate limit).
    # Prevents burst rate-limit failures under concurrent load. Lower = safer vs rate limit;
    # higher = shorter queue wait. 4 is conservative — webhook waits (50–150 s) dominate wall time.
    KIE_CONCURRENCY_LIMIT = int(os.getenv("KIE_CONCURRENCY_LIMIT", "4"))

    # Enable post-generation identity drift check. Disable when using a model that
    # consistently triggers false positives (e.g. qwen/image-edit with Ghibli style).
    # Re-enable once a proper img2img model (e.g. flux-kontext-pro) is configured.
    ENABLE_IDENTITY_CHECK = os.getenv("ENABLE_IDENTITY_CHECK", "false").lower() in {"1", "true", "yes"}

    # Stage 1 fidelity controls — maximize identity preservation.
    # Passed to the model if supported; silently ignored otherwise.
    STAGE1_IMAGE_STRENGTH = 0.35      # Low = closer to source (less transformation)
    STAGE1_DENOISE = 0.30             # Low denoising preserves original structure
    STAGE1_FIDELITY = 0.95            # High fidelity to reference image
    STAGE1_REFERENCE_STRENGTH = 0.95  # Max reference/guidance strength
    # Qwen-specific generation quality controls
    STAGE1_GUIDANCE_SCALE = float(os.getenv("STAGE1_GUIDANCE_SCALE", "4.0"))
    STAGE1_NUM_INFERENCE_STEPS = int(os.getenv("STAGE1_NUM_INFERENCE_STEPS", "28"))
    STAGE1_ACCELERATION = os.getenv("STAGE1_ACCELERATION", "none")
    # Output stability — fixed seed and format for consistent results
    KIE_SEED = int(os.getenv("KIE_SEED", "42"))
    KIE_OUTPUT_FORMAT = os.getenv("KIE_OUTPUT_FORMAT", "jpeg")
    KIE_IMAGE_SIZE = os.getenv("KIE_IMAGE_SIZE", "square")

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

    # Negative prompt for Stage 1 — passed when the model supports it (qwen).
    # flux-kontext models ignore this field entirely.
    NEGATIVE_PROMPT_PIC_TO_GHIBLI = (
        "photorealistic, photograph, realistic lighting, camera photo, "
        "generic anime face, identity drift, race change, skin tone change, beautification, "
        "face replacement, facial simplification, different person, "
        "altered ethnicity, altered hairstyle, altered expression"
    )

    PROMPT_GHIBLI_LOCK = (
        "Compose the two images: the Ghibli illustrated person from the first image is holding "
        "the colorful QR code lock from the second image with both hands at chest level. "
        "The QR code lock must appear clearly — sharp, high-contrast, square, and fully scannable. "
        "STRICT: preserve the person's exact skin color, face structure, and Ghibli illustration style from the first image exactly. "
        "Do NOT lighten, darken, or alter skin tone in any way. "
        "Face and head must remain fully visible and unobstructed. "
        "Clean solid background RGB(255, 255, 255). No shadows, no gradients."
    )


# Warn early so misconfiguration is visible in startup logs, not at first request.
if not Settings.DOMAIN:
    _log.warning(
        "DOMAIN env var is not set. CALL_BACK will be a relative URL and KIE webhooks "
        "will never reach this server — all tasks will time out."
    )
if not Settings.KIE_API_KEY:
    _log.warning("KIE_API_KEY env var is not set. All image generation requests will fail.")
if Settings.KIE_COMPOSE_MODEL and Settings.KIE_COMPOSE_MODEL.startswith("flux-kontext"):
    _log.warning(
        "KIE_COMPOSE_MODEL is set to '%s' (flux-kontext). "
        "flux-kontext only accepts a single image — Stage 2 QR composition requires a multi-image model "
        "(e.g. seedream). Set KIE_COMPOSE_MODEL to the correct model or Stage 2 will always fail.",
        Settings.KIE_COMPOSE_MODEL,
    )
if Settings.PERSIST_FINAL_IMAGES:
    _log.info("PERSIST_FINAL_IMAGES=true — final_ images will never be auto-deleted.")
else:
    _log.info("Final image retention TTL: %dh (set PERSIST_FINAL_IMAGES=true to keep indefinitely).", Settings.FINAL_IMAGE_TTL_HOURS)