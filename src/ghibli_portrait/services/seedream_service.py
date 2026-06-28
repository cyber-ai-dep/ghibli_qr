"""
BytePlus ARK (Seedream) image generation — production core (synchronous REST).

The ARK `images/generations` endpoint returns the result image URL inline in the
HTTP response (no webhook). This module is the minimal generation layer used by
`image_service.generate_img` (the /v1 API backend):

    seedream_generate(prompt, images, ...) -> dict   # raw ARK response
    _first_url(response)                   -> str     # first result image URL

Test-only helpers (two-stage / single-shot pipeline + refined prompts) live in
`tests/manual/seedream_pipeline.py`, NOT here.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

import httpx
from dotenv import load_dotenv

# Self-contained: ensure .env is loaded even if this module is imported first.
load_dotenv()

_log = logging.getLogger(__name__)

# ============================================================================
# CONFIG (override via env vars)
# ============================================================================
ARK_API_URL = os.getenv(
    "ARK_API_URL",
    "https://ark.ap-southeast.bytepluses.com/api/v3/images/generations",
)
ARK_API_KEY = os.getenv("ARK_API_KEY", "")
ARK_MODEL = os.getenv("ARK_MODEL", "seedream-4-5-251128")
ARK_IMAGE_SIZE = os.getenv("ARK_IMAGE_SIZE", "2K")
# Default to NO watermark. Set ARK_WATERMARK=true to re-enable.
ARK_WATERMARK = os.getenv("ARK_WATERMARK", "false").lower() in {"1", "true", "yes"}
# Fixed seed -> reproducible output. Set ARK_SEED=-1 for random.
ARK_SEED = int(os.getenv("ARK_SEED", "42"))


# ============================================================================
# RESULT HELPER
# ============================================================================
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

    `images`: list of image references (URL or data URI). A single ref is sent as a
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
