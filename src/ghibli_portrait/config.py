import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # QR Settings
    QR_VERSION = 1
    QR_FILL_COLOR = "white"
    QR_BACK_COLOR = "#2a2d42"
    QR_PASTE_COORDINATES = (817, 795)
    QR_SIDE_LEN = 685

    LOCK_PATH = Path(__file__).parent.parent / "static" / "lock.png"

    # Kie Settings
    KIE_API_KEY = os.getenv["KIE_API_KEY"]
    KIE_IMG_MODEL = os.getenv["KIE_IMG_MODEL"]
    KIE_CREATE_TASK_API = 'https://api.kie.ai/api/v1/jobs/createTask'

    # Server Settings
    DOMAIN = os.getenv("DOMAIN")
    CALL_BACK = DOMAIN + '/api/callback'
