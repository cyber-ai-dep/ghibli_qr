"""
Traffic Test Audit — runs the exact validation logic on every failed/uncertain image
and prints a full structured report.
"""

import io
import math
import sys
import requests
from PIL import Image

# ── images to audit ──────────────────────────────────────────────────────────
IMAGES = [
    # Unexpected MULTIPLE_FACES (single-person photos)
    ("09", "whitewoman2.jpg",  "https://i.ibb.co/RkDPv6jf/whitewoman2.jpg",   "SHOULD_PASS"),
    ("11", "blackwoman1.jpg",  "https://i.ibb.co/cKM1stSZ/blackwoman1.jpg",   "SHOULD_PASS"),
    ("12", "blackwoman2.jpg",  "https://i.ibb.co/BKSG8VGc/blackwoman2.jpg",   "SHOULD_PASS"),
    # Uncertain MULTIPLE_FACES
    ("29", "maskman2.jpg",     "https://i.ibb.co/kgbXdcdQ/maskman2.jpg",      "UNCERTAIN"),
    # False NOT_REAL_PHOTO
    ("32", "aboy.jpg",         "https://i.ibb.co/ym8RdLzS/aboy.jpg",          "SHOULD_PASS"),
    # Correct NOT_REAL_PHOTO (synthetic)
    ("19", "minecraft1.jpg",   "https://i.ibb.co/b5nGjTHh/minecraft1.jpg",    "SHOULD_REJECT_SYNTHETIC"),
    ("20", "minecraft4.jpg",   "https://i.ibb.co/zT7j7wM6/minecraft4.jpg",    "SHOULD_REJECT_SYNTHETIC"),
    # Correct MULTIPLE_FACES (group photos)
    ("23", "threemens.jpg",    "https://i.ibb.co/7tXRgDyH/threemens.jpg",     "SHOULD_REJECT_MULTI"),
    ("24", "threewoman.jpg",   "https://i.ibb.co/9HRCFNwq/threewoman.jpg",    "SHOULD_REJECT_MULTI"),
    ("25", "threegirls.jpg",   "https://i.ibb.co/tyDJhqH/threegirls.jpg",     "SHOULD_REJECT_MULTI"),
]

# ── face detector (copy of production singleton) ─────────────────────────────
try:
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.vision import FaceDetector, FaceDetectorOptions
    from mediapipe.tasks.python.core.base_options import BaseOptions
    import mediapipe as mp
    import numpy as np
    _MP = True
except ImportError:
    _MP = False
    print("ERROR: mediapipe not available", file=sys.stderr)
    sys.exit(1)

_MODEL_PATH = "/home/cyber_netixs3/Downloads/ghibli_qr-updated/src/ghibli_portrait/models/blaze_face_short_range.tflite"
_options = FaceDetectorOptions(
    base_options=BaseOptions(model_asset_path=_MODEL_PATH),
    min_detection_confidence=0.35,
)
_detector = FaceDetector.create_from_options(_options)


def detect_faces(img: Image.Image):
    arr = np.array(img)
    h, w = arr.shape[:2]
    mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=arr)
    result = _detector.detect(mp_img)
    detections = result.detections or []
    img_area = float(h * w)
    faces = []
    for det in detections:
        score = det.categories[0].score if det.categories else 0.0
        bb = det.bounding_box
        x, y = max(0, bb.origin_x), max(0, bb.origin_y)
        fw, fh = max(1, bb.width), max(1, bb.height)
        area = float(fw * fh)
        area_ratio = area / img_area
        cx = (x + fw / 2) / w
        cy = (y + fh / 2) / h
        cd = math.sqrt((cx - 0.5) ** 2 + (cy - 0.5) ** 2)
        faces.append({"bbox": (x, y, fw, fh), "score": round(score, 3),
                      "area": int(area), "area_ratio": round(area_ratio, 4),
                      "center_distance": round(cd, 3)})
    faces.sort(key=lambda f: (-f["area"], -f["score"], f["center_distance"]))
    return faces


def synthetic_scores(img: Image.Image, bbox):
    x, y, w, h = bbox
    margin = int(min(w, h) * 0.2)
    iw, ih = img.size
    x1, y1 = max(0, x - margin), max(0, y - margin)
    x2, y2 = min(iw, x + w + margin), min(ih, y + h + margin)
    region = img.crop((x1, y1, x2, y2)).convert("RGB")
    arr = np.array(region)
    hp, wp = arr.shape[:2]
    total = hp * wp
    if total < 400:
        return None, None, False
    unique_colors = len(np.unique(arr.reshape(-1, 3), axis=0))
    diversity = unique_colors / total
    h_same = np.mean(np.all(arr[:, :-1] == arr[:, 1:], axis=2))
    v_same = np.mean(np.all(arr[:-1, :] == arr[1:, :], axis=2))
    uniformity = (h_same + v_same) / 2
    rule_a = diversity < 0.05 and uniformity > 0.25
    rule_b = diversity < 0.10 and uniformity > 0.18
    is_synthetic = rule_a or rule_b
    reason = ("Rule A (pixel art)" if rule_a else "Rule B (rendered face)" if rule_b else "—")
    return round(diversity, 4), round(uniformity, 4), is_synthetic, reason


# ── main audit ───────────────────────────────────────────────────────────────
MULTI_AREA  = 0.03
MULTI_SCORE = 0.45

rows = []
print("\nDownloading and analysing images...\n")

for num, name, url, expectation in IMAGES:
    sys.stdout.write(f"  [{num}] {name} ... ")
    sys.stdout.flush()
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "audit/1.0"})
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        print(f"DOWNLOAD FAILED: {e}")
        rows.append((num, name, "N/A", f"DOWNLOAD_FAILED: {e}", expectation,
                     "External Provider Failure", "NO", [], None, None, None, None))
        continue

    faces = detect_faces(img)
    face_count = len(faces)

    # MULTIPLE_FACES decision
    significant = [f for f in faces[1:] if f["area_ratio"] >= MULTI_AREA and f["score"] >= MULTI_SCORE]
    multi_rejected = face_count > 1 and len(significant) > 0

    # Synthetic decision (on primary face)
    synth_result = None
    div, uni, is_synth, synth_reason = None, None, False, "—"
    if face_count > 0 and not multi_rejected:
        primary_bbox = faces[0]["bbox"]
        div, uni, is_synth, synth_reason = synthetic_scores(img, primary_bbox)
        synth_result = is_synth

    # Final decision
    if multi_rejected:
        decision = "MULTIPLE_FACES"
    elif face_count == 0:
        decision = "NO_FACE_DETECTED"
    elif synth_result:
        decision = "NOT_REAL_PHOTO"
    else:
        decision = "PASS"

    # Classify
    if expectation == "SHOULD_PASS":
        if decision == "PASS":
            classification = "Correct Pass"
            correct = "YES"
        else:
            classification = "False Rejection"
            correct = "NO"
    elif expectation == "SHOULD_REJECT_SYNTHETIC":
        if decision == "NOT_REAL_PHOTO":
            classification = "Correct Rejection"
            correct = "YES"
        else:
            classification = "Missed Synthetic"
            correct = "NO"
    elif expectation == "SHOULD_REJECT_MULTI":
        if decision == "MULTIPLE_FACES":
            classification = "Correct Rejection"
            correct = "YES"
        else:
            classification = "Missed Multi-Face"
            correct = "NO"
    else:
        classification = "Uncertain"
        correct = "?"

    print(f"{decision}  ({classification})")
    rows.append((num, name, decision, classification, expectation, correct,
                 faces, div, uni, is_synth, synth_reason, significant))


# ── print report ─────────────────────────────────────────────────────────────
SEP = "=" * 90

print(f"\n{SEP}")
print("  TRAFFIC TEST AUDIT — FULL REPORT")
print(SEP)

print(f"\n{'#':<4} {'Image':<22} {'Decision':<20} {'Classification':<25} {'Correct?':<8}")
print("-" * 85)
for row in rows:
    num, name, dec, cls, exp, correct, *_ = row
    print(f"{num:<4} {name:<22} {dec:<20} {cls:<25} {correct}")

# ── MULTIPLE_FACES detail ─────────────────────────────────────────────────────
print(f"\n{SEP}")
print("  MULTIPLE_FACES DETAIL")
print(SEP)
for row in rows:
    num, name, dec, cls, exp, correct, faces, div, uni, is_synth, synth_reason, sig = row
    if not isinstance(faces, list) or len(faces) == 0:
        continue
    if len(faces) < 2 and dec != "MULTIPLE_FACES":
        continue
    print(f"\n  [{num}] {name}  — face_count={len(faces)}  decision={dec}")
    for i, f in enumerate(faces):
        tag = "PRIMARY" if i == 0 else ("SIGNIFICANT" if f in sig else "suppressed")
        print(f"    face[{i}] bbox={f['bbox']}  area_ratio={f['area_ratio']:.4f}  score={f['score']:.3f}  → {tag}")

# ── NOT_REAL_PHOTO detail ─────────────────────────────────────────────────────
print(f"\n{SEP}")
print("  SYNTHETIC DETECTION DETAIL")
print(SEP)
for row in rows:
    num, name, dec, cls, exp, correct, faces, div, uni, is_synth, synth_reason, sig = row
    if div is None:
        continue
    print(f"\n  [{num}] {name}")
    print(f"    diversity={div:.4f}   uniformity={uni:.4f}")
    print(f"    is_synthetic={is_synth}   triggered_by={synth_reason}")
    print(f"    decision={dec}")

# ── summary statistics ────────────────────────────────────────────────────────
print(f"\n{SEP}")
print("  SUMMARY STATISTICS")
print(SEP)

correct_rejections   = sum(1 for r in rows if r[5] == "YES" and "Rejection" in r[3])
false_rejections     = sum(1 for r in rows if r[3] == "False Rejection")
provider_failures    = sum(1 for r in rows if "DOWNLOAD_FAILED" in r[3])
missed_synthetic     = sum(1 for r in rows if r[3] == "Missed Synthetic")
missed_multi         = sum(1 for r in rows if r[3] == "Missed Multi-Face")
correct_passes       = sum(1 for r in rows if r[3] == "Correct Pass")

print(f"  Images audited       : {len(rows)}")
print(f"  Correct Rejections   : {correct_rejections}")
print(f"  Correct Passes       : {correct_passes}")
print(f"  False Rejections     : {false_rejections}")
print(f"  Missed Synthetic     : {missed_synthetic}")
print(f"  Missed Multi-Face    : {missed_multi}")
print(f"  Provider Failures    : {provider_failures}")
print(SEP)
