import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # QR Settings
    QR_VERSION = 1 # Ranges from 1 - 40
    QR_FILL_COLOR = "white"
    QR_BACK_COLOR = "#2a2d42"
    QR_PASTE_COORDINATES = (817, 795)
    QR_SIDE_LEN = 685
    QR_SHORT_CODE_LENGTH = int(os.getenv('SHORT_CODE_LENGTH', 8))  

    BASE_PATH = Path(__file__).parent.parent
    STATIC_PATH = BASE_PATH / 'static'
    LOCK_PATH = STATIC_PATH / 'lock.png'
    TMP_PATH = STATIC_PATH / 'tmp'

    # Kie Settings
    KIE_API_KEY = os.getenv("KIE_API_KEY")
    # Backward-compat single model (legacy)
    KIE_IMG_MODEL = os.getenv("KIE_IMG_MODEL")

    # Preferred split-model configuration
    # - Qwen for Stage 1: generate ghibli portrait
    # - Seedream for Stage 2: compose/edit final output
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

    # Prompts
    PROMPT_PIC_TO_GHIBLI = "Convert this image to Ghibli style art."

    PROMPT_GHIBLI_LOCK = "The person is holding the lock with both hands. Ensure the face and head remain visible."
