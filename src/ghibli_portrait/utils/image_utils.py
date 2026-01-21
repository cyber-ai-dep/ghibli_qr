# img utils (e.g., bg removing, image normalization)

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

import requests
from PIL import Image

_logger = logging.getLogger("image_utils")


@dataclass
class NormalizedImageResult:
    """Result of image normalization."""
    ok: bool
    url: Optional[str] = None
    filepath: Optional[Path] = None
    error: Optional[str] = None


def normalize_image_url(
    image_url: str,
    tmp_path: Path,
    domain: str,
    timeout_s: int = 15
) -> NormalizedImageResult:
    """
    Download an external image, normalize it for Stage 2 (Seedream) compatibility,
    and save it to a local temp directory with a public URL.

    Normalization steps:
    - Download the image
    - Open with PIL (handles most formats)
    - Convert to RGB (removes alpha, handles CMYK)
    - Save as baseline JPEG (non-progressive, standard encoding)
    - Return a public URL to the normalized image

    This fixes issues with:
    - Progressive JPEGs
    - CMYK color space
    - Unsupported headers
    - Invalid content-type
    - Alpha channels (RGBA/LA)

    Args:
        image_url: External image URL to normalize
        tmp_path: Local path for temp file storage
        domain: Domain for constructing public URL
        timeout_s: Download timeout in seconds

    Returns:
        NormalizedImageResult with ok status, url, filepath, or error
    """
    try:
        # Download the image
        headers = {"User-Agent": "ghibli-qr/0.1"}
        response = requests.get(image_url, timeout=timeout_s, headers=headers)
        response.raise_for_status()

        # Open with PIL (handles most formats automatically)
        try:
            img = Image.open(io.BytesIO(response.content))
        except Exception as e:
            return NormalizedImageResult(
                ok=False,
                error=f"Failed to decode image: {e}"
            )

        # Convert to RGB (handles RGBA, LA, CMYK, P, etc.)
        if img.mode in ("RGBA", "LA"):
            # Flatten alpha on white background
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                background.paste(img, mask=img.split()[3])
            else:  # LA mode
                background.paste(img, mask=img.split()[1])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Save as baseline JPEG (non-progressive, standard encoding)
        filename = f"normalized_{uuid4()}.jpg"
        filepath = tmp_path / filename

        # Ensure tmp directory exists
        tmp_path.mkdir(parents=True, exist_ok=True)

        # Save with standard JPEG settings (no progressive, quality 95)
        img.save(
            filepath,
            format="JPEG",
            quality=95,
            progressive=False,
            optimize=False
        )

        # Construct public URL
        public_url = f"{domain.rstrip('/')}/tmp/{filename}"

        _logger.info(f"Normalized image: {image_url} -> {public_url}")

        return NormalizedImageResult(
            ok=True,
            url=public_url,
            filepath=filepath
        )

    except requests.exceptions.RequestException as e:
        return NormalizedImageResult(
            ok=False,
            error=f"Failed to download image: {e}"
        )
    except Exception as e:
        _logger.exception(f"Unexpected error normalizing image: {e}")
        return NormalizedImageResult(
            ok=False,
            error=f"Image normalization failed: {e}"
        )
