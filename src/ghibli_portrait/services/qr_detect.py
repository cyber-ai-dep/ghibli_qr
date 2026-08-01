# Standalone sync script for local QR detection testing.
# Not imported by the application. Run directly: python qr_detect.py

if __name__ == "__main__":
    from qreader import QReader
    from PIL import Image
    import numpy as np
    from pathlib import Path

    IMAGE_PATH = Path(input("Image path: ").strip())

    if not IMAGE_PATH.exists():
        raise FileNotFoundError(f"Image not found: {IMAGE_PATH}")

    image = Image.open(IMAGE_PATH).convert("RGB")
    img_np = np.array(image)

    qreader = QReader(model_size="l")
    results = qreader.detect_and_decode(image=img_np, return_detections=True)

    if not results:
        print("No QR code detected")
    else:
        print(f"{len(results)} QR code(s) detected\n")

        for idx, qr in enumerate(results, start=1):
            text = confidence = bbox = None

            if isinstance(qr, dict):
                text       = qr.get("text")
                confidence = qr.get("confidence")
                bbox       = qr.get("bbox_xyxy")
            elif isinstance(qr, (tuple, list)):
                if len(qr) >= 1: text       = qr[0]
                if len(qr) >= 2: confidence = qr[1]
                if len(qr) >= 3: bbox       = qr[2]
            else:
                text = str(qr)

            print(f"QR #{idx}")
            print("payload    :", text)
            print("confidence :", confidence)
            print("bbox       :", bbox)
            print()
