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

    # Stage 1 fidelity controls — balance identity preservation with visible style transfer.
    # Passed to the model if supported; silently ignored otherwise.
    # image_strength/denoise at ~0.65 allow full Ghibli stylization while still referencing
    # the source face structure. Too low (0.35) and the photo barely changes; identity preserved
    # but no Ghibli style shows through.
    STAGE1_IMAGE_STRENGTH = 0.65      # Allow artistic transformation; lower = less change
    STAGE1_DENOISE = 0.65             # Allow stylistic rendering; lower = closer to photo
    STAGE1_FIDELITY = 0.90            # High structural fidelity to reference image
    STAGE1_REFERENCE_STRENGTH = 0.90  # Strong reference/guidance
    # Qwen-specific generation quality controls
    STAGE1_GUIDANCE_SCALE = float(os.getenv("STAGE1_GUIDANCE_SCALE", "7.5"))
    STAGE1_NUM_INFERENCE_STEPS = int(os.getenv("STAGE1_NUM_INFERENCE_STEPS", "20"))

    # Prompts
    PROMPT_PIC_TO_GHIBLI = (
        "Convert this photo into a Studio Ghibli hand-painted illustration. "
        "Apply the full Ghibli visual style: soft watercolor backgrounds, warm painterly color palette, "
        "clean expressive linework, cel-shaded lighting, lush atmospheric depth, and the characteristic "
        "hand-drawn Ghibli texture throughout every surface.\n\n"
        "IDENTITY LOCK — never change these:\n"
        "Same person, same face structure, same skin tone, same ethnicity, same race.\n"
        "Same hairstyle, same facial hair, same expression.\n"
        "Same clothing, same pose, same hands, same background composition.\n\n"
        "STYLE CHANGE — only these:\n"
        "Render everything as a hand-painted Ghibli illustration.\n"
        "Apply Ghibli color grading, line art, and painterly texture.\n"
        "Make it look like a frame from a Studio Ghibli film.\n\n"
        "DO NOT: replace the face, change ethnicity, lighten/darken skin, "
        "use a generic anime face, beautify, or alter facial proportions.\n\n"
        "Result: the exact same person rendered as a Ghibli film character."
    )

    # Negative prompt for Stage 1 — passed when the model supports it.
    NEGATIVE_PROMPT_PIC_TO_GHIBLI = (
        "photorealistic, photograph, realistic lighting, camera photo, "
        "generic anime face, identity drift, race change, skin tone change, beautification, "
        "face replacement, facial simplification, different person, "
        "altered ethnicity, altered hairstyle, altered expression"
    )

    PROMPT_GHIBLI_LOCK = (
        "The person is holding a colorful lock-shaped QR sign with both hands at torso level. "
        "Keep the Studio Ghibli animated illustration style throughout. Ensure the face and head "
        "remain clearly visible and unobstructed. The QR code must stay sharp, high-contrast, "
        "square, and scannable."
        "Use a clean solid background RGB(238, 240, 248) "
    )