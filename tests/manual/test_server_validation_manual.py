"""
Server-communicating variant of test_clip_validation_manual.py.

The original script imports validate_human_portrait() and calls it IN-PROCESS —
it never talks to a deployed server. This script instead sends real HTTP requests
to a live server's POST /v1/ghibli-qr, reusing the exact same IMAGES ground-truth
table (imported, not duplicated) so results are directly comparable.

Cost-conscious by design: images whose ground truth is NOT_HUMAN / ANIMAL /
HUMAN_MULTIFACES are rejected by Stage 1 validation before any ARK call, so
running ALL of them costs nothing. Images whose ground truth is HUMAN pass
validation and trigger two real, billed ARK calls (Stage 1 + Stage 2) — so only
a small, fixed sample of those is sent for real, not the full set. This mirrors
the cost/coverage tradeoff already agreed on for this project.

Run from the repo root:
    SERVER_BASE_URL=http://<host>:<port> PYTHONPATH=. .venv/bin/python \
        tests/manual/test_server_validation_manual.py
"""

import ast
import csv as _csv
import os
import random
import time
from datetime import datetime as _datetime
from pathlib import Path

import requests


def _load_images_without_executing_the_module():
    """Extract IMAGES from test_clip_validation_manual.py's source text via ast,
    without importing/executing that module — it has no `if __name__ == "__main__"`
    guard, so a plain import would immediately run its own 144-image in-process
    CLIP pass (downloads + local model load) as a side effect."""
    source = (Path(__file__).parent / "test_clip_validation_manual.py").read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "IMAGES" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise RuntimeError("IMAGES assignment not found in test_clip_validation_manual.py")


IMAGES = _load_images_without_executing_the_module()
_EXPECTED_TO_CATEGORY = {
    "HUMAN": "human",
    "HUMAN_MULTIFACES": "human_multifaces",
    "NOT_HUMAN": "not_human",
    "ANIMAL": "animals",
}

SERVER_BASE_URL = os.getenv("SERVER_BASE_URL", "http://localhost:30820").rstrip("/")
ENDPOINT = f"{SERVER_BASE_URL}/v1/ghibli-qr"

# How many HUMAN (accept -> real billed generation) images to actually send for
# real. Every REJECT-expected image (NOT_HUMAN/ANIMAL/HUMAN_MULTIFACES) is free
# and always sent in full — only ACCEPT-expected ones cost money.
HUMAN_SAMPLE_SIZE = int(os.getenv("HUMAN_SAMPLE_SIZE", "8"))
_RANDOM_SEED = 42


def _pick_human_sample(images):
    humans = [row for row in images if row[2] == "HUMAN"]
    rest = [row for row in images if row[2] != "HUMAN"]
    rng = random.Random(_RANDOM_SEED)
    sample = rng.sample(humans, min(HUMAN_SAMPLE_SIZE, len(humans)))
    skipped = len(humans) - len(sample)
    return rest + sample, skipped


# The live HTTP contract only exposes a coarse rejection code — NOT the internal
# CLIP label that produced it. ANIMAL and generic NOT_HUMAN both surface as
# NO_FACE_DETECTED (see clip_validation_service._REJECTION_CODE), so this script
# CANNOT reconstruct the fine-grained ground-truth category the in-process test
# checks. Comparing at that granularity would flag every correctly-rejected
# animal photo as a false "mismatch". The only thing the API actually
# guarantees and exposes is accept-vs-reject, so that is what gets compared —
# this is the true, honest contract-level check for a server-communicating test.
_ACCEPT_CATEGORIES = {"human"}


def _classify_response(status: int, body: dict) -> tuple[str, str]:
    """Return (outcome, code) — outcome is 'accepted' or 'rejected', from the
    live API's actual HTTP status. No attempt is made to guess the internal
    CLIP label; the API does not expose it."""
    if status == 200:
        return "accepted", ""
    errors = body.get("errors") or []
    code = errors[0].get("code") if errors else ""
    return "rejected", code


def main() -> None:
    run_set, skipped = _pick_human_sample(IMAGES)
    print(f"Server under test: {ENDPOINT}")
    print(
        f"Running {len(run_set)}/{len(IMAGES)} images against the live server "
        f"(HUMAN sampled to {HUMAN_SAMPLE_SIZE} of {HUMAN_SAMPLE_SIZE + skipped} "
        f"to avoid {skipped} unnecessary billed ARK calls)."
    )
    print(f"{'file':<28} {'expected':<18} {'outcome':<10} {'match':<7} {'code':<20} time")
    print("-" * 100)

    rows = []
    for name, url, expected in run_set:
        expected_outcome = "accepted" if _EXPECTED_TO_CATEGORY.get(expected, expected.lower()) in _ACCEPT_CATEGORIES else "rejected"

        t0 = time.perf_counter()
        try:
            resp = requests.post(
                ENDPOINT,
                json={"imgUrl": url, "url": "https://example.com/server-validation-test"},
                timeout=180,
            )
            dt = (time.perf_counter() - t0) * 1000
            body = resp.json()
            outcome, code = _classify_response(resp.status_code, body)
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000
            print(f"{name:<28} {expected:<18} REQUEST FAILED: {e}")
            rows.append({
                "file": name, "url": url, "expected": expected, "outcome": "",
                "match": "", "httpStatus": "", "code": "", "time_ms": round(dt, 1),
                "error": f"REQUEST FAILED: {e}",
            })
            continue

        match = expected_outcome == outcome

        print(f"{name:<28} {expected:<18} {outcome:<10} {str(match):<7} {code:<20} {dt:.0f}ms")

        rows.append({
            "file": name, "url": url, "expected": expected, "outcome": outcome,
            "match": match, "httpStatus": resp.status_code, "code": code,
            "time_ms": round(dt, 1), "error": "",
        })

    print("\nDone.")

    if rows:
        out_path = f"tests/manual/server_validation_results_{_datetime.now():%Y%m%d_%H%M%S}.csv"
        fieldnames = ["file", "expected", "outcome", "match", "httpStatus", "code", "time_ms", "error", "url"]
        with open(out_path, "w", newline="") as f:
            writer = _csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        mismatches = [r for r in rows if r["match"] is False]
        print(f"Saved {len(rows)} results to {out_path} ({len(mismatches)} mismatches)")
        for r in mismatches:
            print(f"  MISMATCH: {r['file']} expected={r['expected']} got={r['outcome']} code={r['code']}")


if __name__ == "__main__":
    main()
