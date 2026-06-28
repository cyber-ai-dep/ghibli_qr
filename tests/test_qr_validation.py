"""QR detection — generate a real QR-lock and decode it back (no network).

Exercises the actual QReader/pyzbar decode path used by the pipeline.
"""

from src.ghibli_portrait.services.qr_service import get_qr
from src.ghibli_portrait.services.qr_validation import validate_qr_from_image


def test_generated_qr_is_detected_and_matches():
    url = "https://example.com/profile"
    img = get_qr(url)
    result = validate_qr_from_image(img=img, expected_payload=url)
    assert result.ok is True
    assert result.detected_payload == url


def test_qr_payload_mismatch_is_flagged():
    img = get_qr("https://example.com/real")
    result = validate_qr_from_image(img=img, expected_payload="https://example.com/different")
    # Detected something, but it does not match the expected payload.
    assert result.ok is False
