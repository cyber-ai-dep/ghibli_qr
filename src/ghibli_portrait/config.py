import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


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
    STAGE1_GUIDANCE_SCALE = float(os.getenv("STAGE1_GUIDANCE_SCALE", "3.0"))
    STAGE1_NUM_INFERENCE_STEPS = int(os.getenv("STAGE1_NUM_INFERENCE_STEPS", "30"))

    # Prompts
    PROMPT_PIC_TO_GHIBLI = (
        "Convert this image to Ghibli style art  ,"  
        "Use a clean solid background RGB(238, 240, 248) "

    )

    # Negative prompt for Stage 1 — passed when the model supports it.
    NEGATIVE_PROMPT_PIC_TO_GHIBLI = (
        "generic anime face, identity drift, race change, skin tone change, beautification, "
        "face replacement, facial simplification, cartoon redesign, different person, "
        "altered ethnicity, altered hairstyle, altered expression"
    )

    PROMPT_GHIBLI_LOCK = (
        "The person is holding a colorful lock-shaped QR sign with both hands at torso level. "
        "Keep the Studio Ghibli animated illustration style throughout. Ensure the face and head "
        "remain clearly visible and unobstructed. The QR code must stay sharp, high-contrast, "
        "square, and scannable."
        "Use a clean solid background RGB(238, 240, 248) "
    )