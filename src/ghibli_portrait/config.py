import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()


class Setting:
    # QR Settings

    QR_VERSION = 1
    FILL_COLOR = ("white",)
    BACK_COLOR = "#2a2d42"
    PASTE_COORDINATES = (817, 795)

    LOCK_PATH = Path(__file__).parent.parent / "static" / "lock.png"
