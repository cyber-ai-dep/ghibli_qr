"""
Manual test for clip_validation_service.py — standalone, not yet wired into the pipeline.

Downloads the same known-tricky images used in audit_failures.py (real single-person
photos previously flagged MULTIPLE_FACES, the aboy.jpg false NOT_REAL_PHOTO, Minecraft
renders, group photos) plus a couple of animal/cartoon/render sanity cases, and runs
them through clip_validation_service.validate_human_portrait() directly.

This hits the network (image downloads + first-run CLIP weight download, ~350MB) and
is not collected by the automated test suite (see pyproject.toml addopts).

Run from the repo root:
    PYTHONPATH=. .venv/bin/python tests/manual/test_clip_validation_manual.py
"""

import io
import sys
import time

import requests
from PIL import Image

from src.ghibli_portrait.services.clip_validation_service import validate_human_portrait

# (name, url, expectation) — expectation is the human-readable ground truth,
# not a code, since this script checks CLIP's ACCEPT/REJECT call, not exact codes.
IMAGES = [
    # Real single-person photos the old MediaPipe heuristic flagged MULTIPLE_FACES on.
    # CLIP can't fix MULTIPLE_FACES (it doesn't count faces) but it SHOULD say "human".
    ("whitewoman2.jpg", "https://i.ibb.co/RkDPv6jf/whitewoman2.jpg", "HUMAN"),
    ("blackwoman1.jpg", "https://i.ibb.co/cKM1stSZ/blackwoman1.jpg", "HUMAN"),
    ("blackwoman2.jpg", "https://i.ibb.co/BKSG8VGc/blackwoman2.jpg", "HUMAN"),
    ("maskman2.jpg", "https://i.ibb.co/kgbXdcdQ/maskman2.jpg", "HUMAN"),
    # Real photo the old _is_synthetic_face heuristic false-positived on (NOT_REAL_PHOTO).
    ("aboy.jpg", "https://i.ibb.co/ym8RdLzS/aboy.jpg", "HUMAN"),
    # Synthetic renders the old heuristic correctly caught — CLIP should also reject.
    ("minecraft1.jpg", "https://i.ibb.co/b5nGjTHh/minecraft1.jpg", "NOT_HUMAN"),
    ("minecraft4.jpg", "https://i.ibb.co/zT7j7wM6/minecraft4.jpg", "NOT_HUMAN"),
    # Group photos — old heuristic correctly rejected via MULTIPLE_FACES.
    # CLIP alone can't detect "multiple people"; expect it to still say HUMAN here
    # (that's fine — face counting stays MediaPipe's job if this is ever integrated).
    ("threemens.jpg", "https://i.ibb.co/7tXRgDyH/threemens.jpg", "HUMAN (but has 3 people — CLIP won't catch that)"),
    ("threewoman.jpg", "https://i.ibb.co/9HRCFNwq/threewoman.jpg", "HUMAN (but has 3 people — CLIP won't catch that)"),
    # Additional real-world batch — diverse skin tones, hijab/headscarf, studio and
    # candid photography styles. These are the images that matter most: the app's
    # own prompts already single out hijab/skin-tone fidelity as a sensitive case,
    # so a validator that disproportionately rejects these would be a real problem.
    ("pexels_17191688", "https://images.pexels.com/photos/17191688/pexels-photo-17191688.jpeg", "HUMAN"),
    ("pexels_36383643", "https://images.pexels.com/photos/36383643/pexels-photo-36383643.jpeg", "HUMAN"),
    ("istock_businessman", "https://media.istockphoto.com/id/2166802740/photo/confident-businessman-smiling-in-sunlit-urban-environment.jpg?b=1&s=612x612&w=0&k=20&c=mY22TWcBedLCzlfeboW8RIZdS44V13qTC7wlkMcEJWw=", "HUMAN"),
    ("pexels_325685", "https://images.pexels.com/photos/325685/pexels-photo-325685.jpeg", "HUMAN"),
    ("pexels_11719178", "https://images.pexels.com/photos/11719178/pexels-photo-11719178.jpeg", "HUMAN"),
    ("pexels_29118567", "https://images.pexels.com/photos/29118567/pexels-photo-29118567.jpeg", "HUMAN"),
    ("pexels_12771905", "https://images.pexels.com/photos/12771905/pexels-photo-12771905.jpeg", "HUMAN"),
    ("istock_muslim_girl", "https://media.istockphoto.com/id/956842252/photo/portrait-of-a-confident-muslim-girl.jpg?b=1&s=612x612&w=0&k=20&c=0WYTuT2YbtXMQjnnUKjNOy2_G2AWP_Swpb-ErbDYxwg=", "HUMAN"),
    ("hijabigirl2", "https://i.ibb.co/FkhVnk7T/hijabigirl2.jpg", "HUMAN"),
    ("pexels_38101763", "https://images.pexels.com/photos/38101763/pexels-photo-38101763.jpeg", "HUMAN"),
    ("pexels_16339418", "https://images.pexels.com/photos/16339418/pexels-photo-16339418.jpeg", "HUMAN"),
    ("pexels_34673724", "https://images.pexels.com/photos/34673724/pexels-photo-34673724.jpeg", "HUMAN"),
    ("pexels_34913247", "https://images.pexels.com/photos/34913247/pexels-photo-34913247.png", "HUMAN"),
    ("pexels_33762211", "https://images.pexels.com/photos/33762211/pexels-photo-33762211.jpeg", "HUMAN"),
    ("pexels_36437007", "https://images.pexels.com/photos/36437007/pexels-photo-36437007.jpeg", "HUMAN"),
    ("pexels_30124372", "https://images.pexels.com/photos/30124372/pexels-photo-30124372.jpeg", "HUMAN"),
    ("pexels_5082975", "https://images.pexels.com/photos/5082975/pexels-photo-5082975.jpeg", "HUMAN"),
    ("pexels_15946547", "https://images.pexels.com/photos/15946547/pexels-photo-15946547.jpeg", "HUMAN"),
    ("pexels_30509132", "https://images.pexels.com/photos/30509132/pexels-photo-30509132.jpeg", "HUMAN"),
    ("istock_schoolgirl", "https://media.istockphoto.com/id/1345020578/photo/cheerful-caucasian-schoolgirl-teenager-pupil-student-smiling-with-toothy-smile-looking-at.jpg?b=1&s=612x612&w=0&k=20&c=jsQRZP9z4YCAUH8UBHZ3CPva7RllTuFrAUJLEOam8ZU=", "HUMAN"),
    ("istock_aa_girl", "https://media.istockphoto.com/id/1353379172/photo/cute-little-african-american-girl-looking-at-camera.jpg?b=1&s=612x612&w=0&k=20&c=3qahdCVthwy9Q1lCY96GQHh8DipUWt7H7fJaVaRXsFs=", "HUMAN"),
]

print(f"{'file':<20} {'expected':<45} {'top label':<38} {'ok':<6} {'code':<18} time")
print("-" * 135)

for name, url, expected in IMAGES:
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "clip-manual-test/1.0"})
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        print(f"{name:<20} DOWNLOAD FAILED: {e}")
        continue

    t0 = time.perf_counter()
    result = validate_human_portrait(img, image_url=name)
    dt = (time.perf_counter() - t0) * 1000

    print(f"{name:<20} {expected:<45} {result.label:<38} {str(result.ok):<6} {str(result.code):<18} {dt:.0f}ms")
    print(f"    scores: { {k: round(v, 3) for k, v in result.scores.items()} }")

print("\nDone.")
