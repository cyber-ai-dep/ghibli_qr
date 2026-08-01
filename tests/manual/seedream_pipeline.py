"""
Test-only Seedream pipeline helpers + improved prompts.

Separated from the production core (`src/ghibli_portrait/services/seedream_service.py`),
which only keeps the synchronous ARK call (`seedream_generate`, `_first_url`, ARK_*).

Everything here is used ONLY by the manual test scripts in tests/manual/
(seedream_direct_test.py, batch_single_test.py) — NOT by the /v1 API pipeline.
The two-stage / single-shot helpers and the refined prompts live here so the
production module stays minimal.

Two-stage pipeline:
  Stage 1: portrait  ->  Ghibli-style art
  Stage 2: Ghibli art + QR-lock image  ->  "person holding a QR lock" (merged)
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from dataclasses import dataclass
from pathlib import Path

# Make the project root importable when used from tests/manual/.
import sys as _sys
import pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[2]))

import httpx
from PIL import Image as _PILImage

# Production ARK core (kept minimal in seedream_service.py). Re-export the ARK_*
# settings here too so test scripts can reach them via this single module.
from src.ghibli_portrait.services.seedream_service import (
    seedream_generate,
    _first_url,
    ARK_API_URL,
    ARK_API_KEY,
    ARK_MODEL,
    ARK_IMAGE_SIZE,
    ARK_WATERMARK,
    ARK_SEED,
)
# Reuse the project's QR-on-lock generator.
from src.ghibli_portrait.services.qr_service import get_qr

_log = logging.getLogger(__name__)

# Cap base64 payload size — large reference images make the request slow/huge.
_MAX_REF_SIDE = 1024

# Public surface (incl. ARK_* re-exports for the manual test scripts).
__all__ = [
    "seedream_generate", "_first_url",
    "ARK_API_URL", "ARK_API_KEY", "ARK_MODEL", "ARK_IMAGE_SIZE", "ARK_WATERMARK", "ARK_SEED",
    "PROMPT_PIC_TO_GHIBLI", "NEGATIVE_PROMPT_PIC_TO_GHIBLI",
    "PROMPT_GHIBLI_LOCK", "NEGATIVE_PROMPT_GHIBLI_LOCK",
    "PROMPT_GHIBLI_QR_SINGLE", "NEGATIVE_PROMPT_GHIBLI_QR_SINGLE",
    "PipelineResult", "stage1_to_ghibli", "stage2_merge_qr",
    "single_shot_ghibli_qr", "run_pipeline", "download_to",
]


# ============================================================================
# PROMPTS (refined — test scripts only; the /v1 API uses config.py prompts)
# ============================================================================

# STAGE 1 — Goal: restyle the portrait into Ghibli/anime as a STYLE FILTER while
# locking identity (face, eyes, proportions), exact skin tone, clothing and the
# person's distinctive features (glasses, beard, etc.). No beautify, no redraw.
PROMPT_PIC_TO_GHIBLI = (
    "Convert this photo into a Studio Ghibli / anime hand-painted illustration. "
    "Treat this as a STYLE FILTER applied over the real photo, NOT a redraw of the face from scratch. "
    "Apply the Ghibli/anime look everywhere: soft cel-shaded coloring, clean gentle linework, "
    "warm painterly Ghibli color grading and hand-drawn texture.\n\n"
    "IDENTITY LOCK — keep the EXACT same person: same face shape, same eyes (natural real size, "
    "NOT enlarged anime eyes), same nose, mouth, eyebrows, same head angle and facial proportions, "
    "same expression. Do not beautify, do not make the face younger or prettier. The result MUST be "
    "instantly recognizable as the SAME real person.\n\n"
    "PRESERVE THE ESSENTIAL ATTRIBUTES exactly:\n"
    "- Same skin tone / person's color and ethnicity — dark stays dark, light stays light, no shift.\n"
    "- Same clothing: same shape, type, cut and colors as in the photo.\n"
    "- Same hairstyle and facial hair.\n"
    "- Keep the distinctive details that make THIS specific person recognizable (e.g. glasses, beard, "
    "moustache, marks, accessories) — render them in the Ghibli style without removing them, "
    "and without breaking the clean Ghibli/anime look.\n\n"
    "Use a clean solid background RGB(238, 240, 248) — no scenery, no shadows, no gradients."
)

# STAGE 1 negative — Goal: forbid identity drift, beautification, skin/clothing
# changes, generic anime faces, and any photorealistic/3D output.
NEGATIVE_PROMPT_PIC_TO_GHIBLI = (
    "different person, new face, face replacement, changed face shape, changed facial features, "
    "identity drift, generic anime face, big anime eyes, manga style, beautification, "
    "younger face, idealized face, prettier face, skin tone change, person color change, race change, "
    "altered ethnicity, altered hairstyle, changed clothing, changed clothing color, changed clothing shape, "
    "removed glasses, removed beard, missing distinctive features, altered expression, "
    "photorealistic, realistic photo, 3d render"
)

# STAGE 2 — Goal: compose the SAME Ghibli person holding the QR-lock. Person looks
# forward; both hands grip the lock from its sides; the lock sits LOW so the whole
# face stays visible; skin/clothing colors locked; hijab modesty when applicable.
PROMPT_GHIBLI_LOCK = (
    "Studio Ghibli / anime illustration of the SAME person from the first image, framed from the top of "
    "the head down to mid-chest, centered.\n\n"
    "IDENTITY & COLOR LOCK — keep the exact same identity, face, hairstyle and clothing from the first "
    "image, and reproduce the person's EXACT skin tone and colors from the first image. Do NOT lighten, "
    "darken, whiten or shift the person's skin color in any way. Keep the same clothing colors too.\n\n"
    "GAZE — The person looks STRAIGHT ahead directly at the camera/viewer: head upright and level, face "
    "forward, eyes looking forward. The person must NOT look down, must NOT tilt the head down, and must "
    "NOT look at the lock.\n\n"
    "HANDS & GRIP — The person holds the colorful lock-shaped QR sign (second image) with BOTH hands "
    "gripping it from its LEFT and RIGHT SIDES: one hand on each side edge, fingers wrapping the side "
    "naturally and realistically. Anatomically correct: two arms, two hands with five fingers each, a "
    "natural realistic grip.\n\n"
    "LOCK POSITION — Hold the lock LOW, down at the lower chest area near the bottom of the frame. There "
    "must be a clear, generous empty gap between the top of the lock and the chin, so the WHOLE face, chin, "
    "neck and hair stay fully visible and completely unobstructed. The lock must NEVER reach, touch, "
    "overlap or cover the face, chin, neck or hair, and must NOT hang around the neck like a necklace — it "
    "is held in the hands in front of the lower chest only.\n\n"
    "LOCK SIZE — A natural, moderate size, about the central third of the image width: NOT tiny or far "
    "away, and NOT oversized (it must not be big enough to reach the face).\n\n"
    "QR — Reproduce the lock sign from the second image EXACTLY (same shape and colors). The QR code must be "
    "sharp, high-contrast, perfectly square, flat and facing the camera (not tilted, not warped, not in "
    "perspective), and fully scannable.\n\n"
    "MODESTY (only if the person is a woman wearing a hijab) — keep the hijab fully covering ALL of her hair "
    "so that NO hair is visible at all, and cover her wrists and forearms with sleeves in the SAME color as "
    "her clothing, in a natural realistic way.\n\n"
    "Keep the Studio Ghibli hand-painted style throughout. Clean solid background RGB(238, 240, 248), "
    "no scenery, no shadows, no gradients."
)

# STAGE 2 negative — Goal: prevent the lock covering the face, downward gaze, skin
# recolor, necklace/neck placement, oversized/tiny lock, bad hands, and (for hijab)
# any visible hair or bare wrists.
NEGATIVE_PROMPT_GHIBLI_LOCK = (
    "lock covering the face, sign over the face, lock touching the chin, lock near the face, face hidden, "
    "face partly hidden, head cropped, "
    "looking down, head tilted down, eyes looking down, gazing at the lock, looking at the sign, downward gaze, "
    "skin tone change, person color change, lightened skin, darkened skin, whitened skin, recolored skin, "
    "changed clothing color, "
    "lock around the neck, sign hanging from the neck, necklace, worn on the neck, "
    "holding from the top, hands on top of the lock, hands near the face, hands near the chin, "
    "oversized lock, lock too high, tiny lock, small qr, qr far away, "
    "blurry qr, distorted qr, tilted qr, warped qr, perspective qr, low-contrast qr, unreadable qr, "
    "duplicated hands, extra fingers, missing fingers, fused fingers, deformed hands, three hands, extra arms, "
    "visible hair under hijab, hair showing, uncovered hair, bare wrists, exposed forearms, short sleeves"
)

# SINGLE-SHOT — Goal: do BOTH jobs (Ghibli restyle + QR-lock merge) in ONE ARK call.
# Used to compare quality/cost vs the two-stage pipeline. Image 1 = person, Image 2 = lock.
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

# SINGLE-SHOT negative — Goal: avoid all single-call pitfalls: identity drift,
# wrong/redrawn lock, lock on the face/neck, full body, bad hands, busy background.
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
    """Do both jobs (portrait -> Ghibli AND merge the QR lock) in a SINGLE Seedream call."""
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
async def download_to(url: str, dest: "str | Path") -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.get(url)
        r.raise_for_status()
    dest.write_bytes(r.content)
    return dest
