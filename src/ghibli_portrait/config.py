import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()


class Settings:
    # QR Settings

    QR_VERSION = 1
    QR_FILL_COLOR = "white"
    QR_BACK_COLOR = "#2a2d42"
    QR_PASTE_COORDINATES = (817, 795)
    QR_SIDE_LEN = 685

    LOCK_PATH = Path(__file__).parent.parent / "static" / "lock.png"
