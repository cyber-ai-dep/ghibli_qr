# ✅ isolated validation service (keeps your logic EXACTLY, only adds the URL download wrapper)
# file: src/ghibli_portrait/services/qr_validation_service.py

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union
from uuid import uuid4

import httpx
import numpy as np
from PIL import Image
from qreader import QReader


# -----------------------------
# CONSTANTS
# -----------------------------
QR_MODEL_SIZE = "l"   # FIXED TO LARGE MODEL


# -----------------------------
# Result object (kept for debugging / future use)
# -----------------------------
@dataclass
class QRValidationResult:
    found_payload: bool
    decoded_payload: Optional[str]
    expected_payload: str
    is_match: bool
    raw_results: Any = None


# -----------------------------
# Payload extraction (UNCHANGED – proven working)
# -----------------------------
def extract_qr_payload(qreader_results: Any) -> Optional[str]:
    """
    Extracts the first valid QR payload (string) from QReader results.
    Ignores detection-only metadata.
    """
    if not qreader_results:
        return None

    for qr in qreader_results:
        if isinstance(qr, str):
            return qr

        if isinstance(qr, dict):
            text = qr.get("text")
            if isinstance(text, str):
                return text

        if isinstance(qr, (tuple, list)) and len(qr) > 0:
            if isinstance(qr[0], str):
                return qr[0]

    return None


# -----------------------------
# ✅ MAIN VALIDATION FUNCTION (BOOLEAN RETURN)
# -----------------------------
def validate_qr_in_image(
    image_path: Union[str, Path],
    expected_qr_data: str,
) -> bool:
    """
    Returns:
      True  -> QR payload exists AND matches expected_qr_data
      False -> No payload OR mismatch
    """
    image_path = Path(image_path)

    if not image_path.exists():
        return False

    image = Image.open(image_path).convert("RGB")
    img_np = np.array(image)

    qreader = QReader(model_size=QR_MODEL_SIZE)

    results = qreader.detect_and_decode(
        image=img_np,
        return_detections=True
    )

    payload = extract_qr_payload(results)

    if not payload:
        return False

    return payload == expected_qr_data


# -----------------------------
# Layout-only detection (optional, unchanged)
# -----------------------------
def has_any_qr_layout(image_path: Union[str, Path]) -> bool:
    """
    Returns True if ANY QR candidate is detected (even if payload is unreadable).
    """
    image_path = Path(image_path)

    if not image_path.exists():
        return False

    image = Image.open(image_path).convert("RGB")
    img_np = np.array(image)

    qreader = QReader(model_size=QR_MODEL_SIZE)
    results = qreader.detect_and_decode(
        image=img_np,
        return_detections=True
    )

    return bool(results)


# -----------------------------
# ✅ NEW: validate from MERGED IMAGE URL (download + validate + delete)
# -----------------------------
async def validate_qr_from_merged_url(
    merged_image_url: str,
    expected_qr_data: str,
    tmp_dir: Union[str, Path],
    *,
    timeout: float = 30.0,
) -> bool:
    """
    ✅ keeps main validation logic unchanged:
      - downloads merged image into tmp_dir
      - calls validate_qr_in_image(local_path, expected_qr_data)
      - deletes the downloaded merged file always
      - returns boolean result
    """
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    merged_local_path = tmp_dir / f"merged_{uuid4()}.png"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(merged_image_url)
            r.raise_for_status()
            merged_local_path.write_bytes(r.content)

        return validate_qr_in_image(merged_local_path, expected_qr_data)

    finally:
        try:
            merged_local_path.unlink(missing_ok=True)
        except Exception:
            pass


# -----------------------------
# ✅ CLI
# -----------------------------
def _build_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog="qr_validation_service",
        description="QR validation / layout detection using QReader (large model).",
    )

    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", help="Local image path to validate/detect.")
    src.add_argument("--url", help="Merged image URL to download + validate.")

    p.add_argument(
        "--expected",
        help="Expected QR payload (required for validate mode).",
        default=None,
    )

    p.add_argument(
        "--mode",
        choices=["validate", "layout"],
        default="validate",
        help="validate: payload must match --expected | layout: any QR candidate detection only",
    )

    p.add_argument(
        "--tmp-dir",
        default="/tmp/qr_validation",
        help="Temp dir used when --url is provided.",
    )

    p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds for --url mode.",
    )

    p.add_argument(
        "--quiet",
        action="store_true",
        help="Only print 1/0 (success/fail).",
    )

    return p


async def _run_cli_async(args) -> int:
    # layout mode
    if args.mode == "layout":
        if args.image:
            ok = has_any_qr_layout(args.image)
        else:
            # for URL layout check: download -> run has_any_qr_layout -> delete
            from pathlib import Path
            from uuid import uuid4
            import httpx

            tmp_dir = Path(args.tmp_dir)
            tmp_dir.mkdir(parents=True, exist_ok=True)
            local_path = tmp_dir / f"merged_{uuid4()}.png"

            try:
                async with httpx.AsyncClient(timeout=args.timeout) as client:
                    r = await client.get(args.url)
                    r.raise_for_status()
                    local_path.write_bytes(r.content)

                ok = has_any_qr_layout(local_path)
            finally:
                try:
                    local_path.unlink(missing_ok=True)
                except Exception:
                    pass

        if args.quiet:
            print("1" if ok else "0")
        else:
            print("✅ QR layout found" if ok else "❌ No QR layout found")
        return 0 if ok else 1

    # validate mode
    if not args.expected:
        if args.quiet:
            print("0")
        else:
            print("❌ --expected is required for validate mode")
        return 2

    if args.image:
        ok = validate_qr_in_image(args.image, args.expected)
    else:
        ok = await validate_qr_from_merged_url(
            merged_image_url=args.url,
            expected_qr_data=args.expected,
            tmp_dir=args.tmp_dir,
            timeout=args.timeout,
        )

    if args.quiet:
        print("1" if ok else "0")
    else:
        print("✅ MATCH" if ok else "❌ NO MATCH / NOT FOUND")

    return 0 if ok else 1


def main() -> None:
    import asyncio

    parser = _build_parser()
    args = parser.parse_args()

    exit_code = asyncio.run(_run_cli_async(args))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
