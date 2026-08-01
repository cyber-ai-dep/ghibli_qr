"""Shared pytest fixtures and helpers for the Ghibli QR test suite.

All tests run WITHOUT real network calls or paid ARK/KIE requests — external
boundaries (ARK generation, image downloads, QR decode) are mocked where needed.
"""

import io

import numpy as np
import pytest
from PIL import Image


@pytest.fixture
def jpeg_bytes() -> bytes:
    """A small valid JPEG payload (used to fake image downloads)."""
    img = Image.new("RGB", (64, 64), (200, 150, 120))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def skin_image() -> Image.Image:
    """A solid skin-tone image (passes the YCbCr skin mask)."""
    return Image.new("RGB", (120, 120), (200, 150, 120))


@pytest.fixture
def gray_image() -> Image.Image:
    """A neutral gray image (no skin pixels -> skin extraction returns None)."""
    return Image.new("RGB", (120, 120), (128, 128, 128))


@pytest.fixture
def synthetic_image() -> Image.Image:
    """A flat solid-color image — low color diversity, high pixel uniformity."""
    return Image.new("RGB", (200, 200), (40, 90, 160))


@pytest.fixture
def noisy_image() -> Image.Image:
    """A high-entropy random image — high diversity, low uniformity (real-photo-like)."""
    arr = (np.random.default_rng(7).integers(0, 256, (200, 200, 3))).astype("uint8")
    return Image.fromarray(arr, "RGB")


@pytest.fixture
def client():
    """FastAPI TestClient for the main app."""
    from fastapi.testclient import TestClient
    from src.ghibli_portrait.main import app

    with TestClient(app) as c:
        yield c
