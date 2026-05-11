# qrrr.py
from qreader import QReader
from PIL import Image
import numpy as np
from pathlib import Path

# ---------- CONFIG ----------
IMAGE_PATH = Path("/home/ahmad/Desktop/projects/cyberai/ghibli_qr/mm.jpg")

# ---------- LOAD IMAGE ----------
if not IMAGE_PATH.exists():
    raise FileNotFoundError(f"Image not found: {IMAGE_PATH}")

image = Image.open(IMAGE_PATH).convert("RGB")
img_np = np.array(image)

# ---------- INIT QR READER ----------
qreader = QReader(model_size="l")  # l = large model for best accuracy

# ---------- DETECT & DECODE ----------
results = qreader.detect_and_decode(image=img_np, return_detections=True)

# ---------- OUTPUT ----------
if not results:
    print("❌ No QR code detected")
else:
    print(f"✅ {len(results)} QR code(s) detected\n")

    for idx, qr in enumerate(results, start=1):
        # --- Safe unpacking ---
        text = None
        confidence = None
        bbox = None

        # Case 1: dict (newer versions)
        if isinstance(qr, dict):
            text       = qr.get('text')
            confidence = qr.get('confidence')
            bbox       = qr.get('bbox_xyxy')
        # Case 2: tuple/list (older versions)
        elif isinstance(qr, (tuple, list)):
            if len(qr) >= 1: text       = qr[0]
            if len(qr) >= 2: confidence = qr[1]
            if len(qr) >= 3: bbox       = qr[2]
        # Case 3: plain string (minimal return)
        else:
            text = str(qr)

        print(f"QR #{idx}")
        print("QR payload :", text)
        print("confidence :", confidence)
        print("bbox       :", bbox)
        print("——— scanable ✔ ———\n")
