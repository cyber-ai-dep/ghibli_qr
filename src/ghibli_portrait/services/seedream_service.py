"""
Direct BytePlus ARK Seedream image generation (synchronous REST — NO webhook).

Unlike the KIE integration (`image_service.py`) which creates a task and waits for a
callback, the BytePlus ARK `images/generations` endpoint returns the result image URL
inline in the HTTP response. This makes the whole flow a simple request/response.

Two-stage pipeline:
  Stage 1: portrait  ->  Ghibli-style art
  Stage 2: Ghibli art + QR-lock image  ->  "person holding a QR lock" (merged)

The QR-lock image is reused from the existing project code (`qr_service.get_qr`).
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import httpx
from PIL import Image as _PILImage

# Reuse the project's QR-on-lock generator (Layer: services/qr_service.py)
from src.ghibli_portrait.services.qr_service import get_qr

_log = logging.getLogger(__name__)

# ============================================================================
# CONFIG (adjustable — override via env vars)
# ============================================================================
ARK_API_URL = os.getenv(
    "ARK_API_URL",
    "https://ark.ap-southeast.bytepluses.com/api/v3/images/generations",
)
# NOTE: for production move this into .env. Left here for quick testing only.
ARK_API_KEY = os.getenv("ARK_API_KEY", "")
ARK_MODEL = os.getenv("ARK_MODEL", "seedream-4-5-251128")
ARK_IMAGE_SIZE = os.getenv("ARK_IMAGE_SIZE", "2K")
# Default to NO watermark (requested). Set ARK_WATERMARK=true to re-enable.
ARK_WATERMARK = os.getenv("ARK_WATERMARK", "false").lower() in {"1", "true", "yes"}
# Fixed seed -> reproducible output (same face every run). Set ARK_SEED=-1 for random.
ARK_SEED = int(os.getenv("ARK_SEED", "42"))

# Cap base64 payload size — large reference images make the request slow/huge.
_MAX_REF_SIDE = 1024

# ============================================================================
# PROMPTS (as provided)
# ============================================================================
PROMPT_PIC_TO_GHIBLI = (
    "Apply a light Studio Ghibli anime art style to this photo while keeping the EXACT same person. "
    "Treat this as a style filter, NOT a redraw of the face. "
    "Preserve the identical likeness: same face shape, same eyes, same nose, same mouth, same eyebrows, "
    "same skin tone, same ethnicity, same facial hair, same hairstyle, same expression, "
    "same head angle and the same facial proportions as the original photo. "
    "Only change the rendering style: soft cel-shaded coloring, gentle clean linework, and warm Ghibli color grading. "
    "Keep the eyes their natural real size — do NOT enlarge them into anime eyes, do not make the face younger or prettier. "
    "The result MUST be instantly recognizable as the SAME real person. "
    "Use a clean solid background RGB(238, 240, 248) — no scenery, no shadows, no gradients."
)

NEGATIVE_PROMPT_PIC_TO_GHIBLI = (
    "different person, new face, face replacement, changed face shape, changed facial features, "
    "identity drift, generic anime face, big anime eyes, manga style, beautification, "
    "younger face, idealized face, prettier face, skin tone change, race change, "
    "altered ethnicity, altered hairstyle, altered expression"
)

PROMPT_GHIBLI_LOCK = (
    "Studio Ghibli illustration of the SAME person, framed from the head down to the waist, centered. "
    "The person holds a lock-shaped sign containing the QR code with both hands at chest level, "
    "in front of the chest and clearly BELOW the chin. "
    "Make the lock sign LARGE and prominent: it should span roughly the central third of the image width "
    "and fill the chest-to-waist area, so the QR is big and easy to scan. "
    "The QR code must be sharp, high-contrast, perfectly square, flat and facing the camera "
    "(not tilted, not warped, not in perspective), and fully scannable. "
    "Keep the face, head and hair COMPLETELY visible and unobstructed — "
    "the lock sign must NOT cover, overlap or touch the face, neck or hair. "
    "Keep the Studio Ghibli hand-painted style throughout. "
    "Clean solid background RGB(238, 240, 248), no scenery, no shadows, no gradients."
)

# Negative guidance for the composition stage — keeps the QR readable and off the face.
NEGATIVE_PROMPT_GHIBLI_LOCK = (
    "lock covering the face, sign over the face, face hidden, head cropped, "
    "small qr, tiny qr, qr far away, blurry qr, distorted qr, tilted qr, warped qr, "
    "perspective qr, low-contrast qr, unreadable qr, duplicated hands, extra fingers"
)

# Single-shot prompt: does BOTH jobs (Ghibli restyle + QR merge) in ONE API call.
# Image 1 = the real person's photo. Image 2 = the lock-shaped QR sign.
PROMPT_GHIBLI_QR_SINGLE = (
    "You are given two images. Image 1 is a real person. Image 2 is a colorful lock-shaped QR sign.\n\n"
    "STYLE — Convert the person from Image 1 into a Studio Ghibli hand-painted illustration. Apply the "
    "full Ghibli visual style: warm painterly color palette, clean expressive linework, cel-shaded "
    "lighting, and the characteristic hand-drawn Ghibli texture. Make it look like a frame from a Studio "
    "Ghibli film.\n\n"
    "IDENTITY LOCK — never change these: same person, same face structure, same skin tone, same ethnicity, "
    "same race, same hairstyle, same facial hair, same expression, natural real-size eyes (not anime eyes). "
    "Do NOT replace the face, beautify, make younger, or alter facial proportions. The result must be the "
    "exact same person rendered as a Ghibli film character.\n\n"
    "COMPOSITION — Show ONLY the upper body of the person (head, shoulders and chest), centered and "
    "cropped at the chest just below the held sign. Do NOT show the waist, legs or lower body. The person "
    "holds the colorful lock-shaped QR sign from Image 2 in front of the chest, from the SIDES, with one "
    "hand gripping each edge, arms lowered so the sign rests at chest level, well BELOW the chin. The sign "
    "is held in the hands in front of the body — it is NOT worn or hung around the neck and is NOT a "
    "necklace; the hands do not go near the face or chin.\n\n"
    "QR SIGN — Reproduce the sign from Image 2 EXACTLY: same rounded lock shape, same dark navy body and "
    "colorful blue/orange/red curved ring, same colors and proportions. Do NOT redraw it as a plain "
    "metal/silver padlock, do NOT change its colors, and do NOT put the QR on a white card. Add NOTHING "
    "extra to the sign: no added parts, protrusions, straps, buttons, badges, keyholes, text or "
    "decorations — only the exact lock design from Image 2. Keep the sign LARGE (about the central third "
    "of the image width). The QR code must stay sharp, high-contrast, perfectly square, flat, facing the "
    "camera, and fully scannable.\n\n"
    "Keep the face, head and hair fully visible and unobstructed — the sign must not cover or touch them. "
    "Anatomically correct: two arms, two hands with five fingers each, natural grip, no logical errors and "
    "no artifacts. The background MUST be a single, completely flat and uniform solid color RGB(238, 240, "
    "248) filling the whole background identically — no gradient, no shading, no texture, no scenery, no "
    "shadows."
)

NEGATIVE_PROMPT_GHIBLI_QR_SINGLE = (
    "different person, new face, face replacement, changed facial features, identity drift, "
    "generic anime face, big anime eyes, beautification, younger face, skin tone change, "
    "plain metal padlock, silver padlock, generic padlock, redrawn sign, restyled sign, "
    "changed lock colors, white qr card, different lock design, "
    "lock around the neck, sign hanging from the neck, necklace, worn on the neck, "
    "sign held up near the chin, hands near the face, hands near the chin, holding from the top, "
    "shrunken sign, tiny sign, "
    "lock covering the face, sign over the face, face hidden, head cropped, "
    "small qr, blurry qr, distorted qr, tilted qr, warped qr, unreadable qr, "
    "extra fingers, missing fingers, fused fingers, deformed hands, malformed hands, "
    "extra arms, three hands, anatomical errors, distorted body, "
    "extra parts on the lock, protrusions, straps, buttons, badges, keyhole, text on the sign, "
    "decorations on the sign, extra objects, "
    "gradient background, textured background, scenery, blue gradient, teal background, "
    "full body, waist visible, legs visible, lower body"
)


# ============================================================================
# RESULT OBJECT
# ============================================================================
@dataclass
class PipelineResult:
    ghibli_url: str           # Stage 1 output (BytePlus CDN URL)
    final_url: str            # Stage 2 output (merged image URL)
    raw_stage1: dict          # full API response for Stage 1
    raw_stage2: dict          # full API response for Stage 2


# ============================================================================
# IMAGE REFERENCE HELPERS
# ============================================================================
def _to_image_ref(path_or_url: str) -> str:
    """Turn an input into a value usable in the BytePlus `image` field.

    - An http(s) URL is passed through unchanged (BytePlus fetches it).
    - A local file path is read and inlined as a base64 data URI.
    """
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url

    p = Path(path_or_url)
    if not p.is_file():
        raise FileNotFoundError(f"Input image not found: {path_or_url}")

    # Downscale oversized local images before base64 to keep the request small.
    with _PILImage.open(p) as im:
        im = im.convert("RGB")
        if max(im.size) > _MAX_REF_SIDE:
            im.thumbnail((_MAX_REF_SIDE, _MAX_REF_SIDE), _PILImage.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=92, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


def _pil_to_data_uri(img: "_PILImage.Image") -> str:
    """Inline a PIL image (e.g. the QR-lock) as a base64 JPEG data URI."""
    img = img.convert("RGB")
    if max(img.size) > _MAX_REF_SIDE:
        img.thumbnail((_MAX_REF_SIDE, _MAX_REF_SIDE), _PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


def _first_url(api_result: dict) -> Optional[str]:
    """Extract the first generated image URL from a BytePlus response."""
    data = api_result.get("data")
    if isinstance(data, list) and data:
        return data[0].get("url")
    return None


# ============================================================================
# CORE: single BytePlus generation call
# ============================================================================
async def seedream_generate(
    prompt: str,
    images: Optional[List[str]] = None,
    *,
    size: str = ARK_IMAGE_SIZE,
    watermark: bool = ARK_WATERMARK,
    negative_prompt: Optional[str] = None,
    seed: int = ARK_SEED,
) -> dict:
    """One call to the BytePlus ARK images/generations endpoint.

    `images`: list of image references (URL or data URI). Single ref is sent as a
    string, multiple refs as an array — both accepted by Seedream for editing/merge.

    The REST endpoint has no dedicated negative-prompt field, so the negative prompt
    is appended to the main prompt as an explicit "Avoid:" clause.
    """
    full_prompt = prompt
    if negative_prompt:
        full_prompt = f"{prompt}\n\nAvoid: {negative_prompt}"

    body = {
        "model": ARK_MODEL,
        "prompt": full_prompt,
        "sequential_image_generation": "disabled",
        "response_format": "url",
        "size": size,
        "stream": False,
        "watermark": watermark,
    }
    # Fixed seed -> reproducible face across runs (-1 = random).
    if seed is not None and seed >= 0:
        body["seed"] = seed
    if images:
        body["image"] = images[0] if len(images) == 1 else images

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ARK_API_KEY}",
    }

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(ARK_API_URL, json=body, headers=headers)

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    if resp.status_code != 200:
        raise RuntimeError(f"BytePlus API error {resp.status_code}: {data}")
    return data


# ============================================================================
# STAGE 1: portrait -> Ghibli
# ============================================================================
async def stage1_to_ghibli(image_path_or_url: str) -> tuple[str, dict]:
    ref = _to_image_ref(image_path_or_url)
    result = await seedream_generate(
        PROMPT_PIC_TO_GHIBLI,
        images=[ref],
        negative_prompt=NEGATIVE_PROMPT_PIC_TO_GHIBLI,
    )
    url = _first_url(result)
    if not url:
        raise RuntimeError(f"Stage 1 returned no image URL: {result}")
    _log.info("Stage 1 (Ghibli) done -> %s", url)
    return url, result


# ============================================================================
# STAGE 2: Ghibli + QR-lock -> merged
# ============================================================================
async def stage2_merge_qr(ghibli_url: str, qr_target_url: str) -> tuple[str, dict]:
    # get_qr is pure PIL (blocking) — run it off the event loop.
    qr_img = await asyncio.to_thread(get_qr, qr_target_url)
    qr_ref = _pil_to_data_uri(qr_img)

    result = await seedream_generate(
        PROMPT_GHIBLI_LOCK,
        images=[ghibli_url, qr_ref],
        negative_prompt=NEGATIVE_PROMPT_GHIBLI_LOCK,
    )
    url = _first_url(result)
    if not url:
        raise RuntimeError(f"Stage 2 returned no image URL: {result}")
    _log.info("Stage 2 (QR merge) done -> %s", url)
    return url, result


# ============================================================================
# SINGLE-SHOT: Ghibli restyle + QR merge in ONE API call
# ============================================================================
async def single_shot_ghibli_qr(image_path_or_url: str, qr_target_url: str) -> tuple[str, dict]:
    """Do both jobs (portrait -> Ghibli AND merge the QR lock) in a SINGLE Seedream call.

    Sends two input images at once: [original photo, QR-lock] with a combined prompt.
    Useful to compare accuracy vs. the two-stage pipeline (run_pipeline).
    """
    photo_ref = _to_image_ref(image_path_or_url)
    qr_img = await asyncio.to_thread(get_qr, qr_target_url)
    qr_ref = _pil_to_data_uri(qr_img)

    result = await seedream_generate(
        PROMPT_GHIBLI_QR_SINGLE,
        images=[photo_ref, qr_ref],
        negative_prompt=NEGATIVE_PROMPT_GHIBLI_QR_SINGLE,
    )
    url = _first_url(result)
    if not url:
        raise RuntimeError(f"Single-shot returned no image URL: {result}")
    _log.info("Single-shot (Ghibli+QR) done -> %s", url)
    return url, result


# ============================================================================
# FULL PIPELINE
# ============================================================================
async def run_pipeline(image_path_or_url: str, qr_url: str) -> PipelineResult:
    ghibli_url, raw1 = await stage1_to_ghibli(image_path_or_url)
    final_url, raw2 = await stage2_merge_qr(ghibli_url, qr_url)
    return PipelineResult(
        ghibli_url=ghibli_url,
        final_url=final_url,
        raw_stage1=raw1,
        raw_stage2=raw2,
    )


# ============================================================================
# UTILITY: download a result URL to a local file
# ============================================================================
async def download_to(url: str, dest: str | Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.get(url)
        r.raise_for_status()
    dest.write_bytes(r.content)
    return dest
