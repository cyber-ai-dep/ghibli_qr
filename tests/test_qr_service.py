"""QR-on-lock image generation (requires the real lock.png asset)."""

from PIL import Image

from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.services.qr_service import get_qr

s = Settings()


def test_lock_asset_exists_and_is_image():
    assert s.LOCK_PATH.exists(), f"lock.png missing at {s.LOCK_PATH}"
    with Image.open(s.LOCK_PATH) as im:
        assert im.size[0] > 0 and im.size[1] > 0


def test_get_qr_returns_rgb_lock_sized_image():
    img = get_qr("https://example.com/profile")
    assert isinstance(img, Image.Image)
    assert img.mode == "RGB"  # Seedream does not accept RGBA
    with Image.open(s.LOCK_PATH) as lock:
        assert img.size == lock.size


def test_get_qr_handles_long_url():
    img = get_qr("https://example.com/" + "x" * 300)
    assert isinstance(img, Image.Image) and img.mode == "RGB"
