"""Validation layers — URL resolution, single-image rule, Stage 2 trust,
synthetic-image detection, skin extraction, and the Stage 1 human-portrait gate."""

import pytest

from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import ErrorStage, ErrorType
from src.ghibli_portrait.services import validation_service as vs
from src.ghibli_portrait.services.clip_validation_service import ClipValidationResult


# ---------------------------------------------------------------- Layer 1: URL
@pytest.mark.parametrize("url", [
    "https://example.com/a.jpg",
    "http://cdn.site.org/photo.png",
])
def test_public_url_accepts_public(url):
    assert vs.validate_public_url(url).ok is True


@pytest.mark.parametrize("url", [
    "http://localhost/a.jpg",
    "http://127.0.0.1:8010/a.jpg",
    "http://10.0.0.5/a.jpg",
    "http://192.168.1.4/a.jpg",
    "http://172.16.0.9/a.jpg",
    "ftp://example.com/a.jpg",
    "not-a-url",
    "",
])
def test_public_url_rejects_bad(url):
    assert vs.validate_public_url(url).ok is False


def test_source_resolution_error_code():
    r = vs.validate_source_resolution("http://localhost/a.jpg")
    assert r.ok is False
    assert r.code == "INVALID_IMAGE_URL"
    assert r.stage == ErrorStage.SOURCE_RESOLUTION


# ------------------------------------------------- single-image list rule
def test_single_image_list_ok():
    assert vs.validate_single_image_url_list(["https://a/x.jpg"]).ok is True


@pytest.mark.parametrize("urls", [[], None, ["a", "b"]])
def test_single_image_list_rejects(urls):
    assert vs.validate_single_image_url_list(urls).ok is False


# ------------------------------------------------- Layer 3B: Stage 2 trust
def test_stage2_input_accepts_url():
    assert vs.validate_stage2_input("https://a/stage1.jpg").ok is True


@pytest.mark.parametrize("bad", ["", None])
def test_stage2_input_rejects_empty(bad):
    r = vs.validate_stage2_input(bad)
    assert r.ok is False
    assert r.code == "INVALID_STAGE1_OUTPUT"


# ------------------------------------------------- synthetic-image detector
def test_is_synthetic_true_for_flat_image(synthetic_image):
    w, h = synthetic_image.size
    assert vs._is_synthetic_face(synthetic_image, (0, 0, w, h)) is True


def test_is_synthetic_false_for_noisy_image(noisy_image):
    w, h = noisy_image.size
    assert vs._is_synthetic_face(noisy_image, (0, 0, w, h)) is False


# ------------------------------------------------- skin color extraction
def test_extract_skin_hex_for_skin_image(skin_image):
    hexv = vs.extract_skin_color_hex(skin_image)
    assert isinstance(hexv, str) and hexv.startswith("#") and len(hexv) == 7


def test_extract_skin_hex_none_for_gray(gray_image):
    assert vs.extract_skin_color_hex(gray_image) is None


# ------------------------------------------------- Layer 3A: Stage 1 gate
def _settings(**over):
    s = Settings()
    for k, v in over.items():
        setattr(s, k, v)
    return s


def _fake_clip(**over):
    """Build a ClipValidationResult with sane accept defaults, overridden per test."""
    defaults = dict(ok=True, label="a real photograph of one human person", category="human")
    defaults.update(over)
    return ClipValidationResult(**defaults)


def test_stage1_accepts_human(monkeypatch, skin_image):
    monkeypatch.setattr(vs, "validate_human_portrait", lambda img, url: _fake_clip())
    r = vs.validate_stage1_human_portrait(skin_image, "https://a/x.jpg", _settings())
    assert r.ok is True


def test_stage1_rejects_no_human(monkeypatch, skin_image):
    monkeypatch.setattr(vs, "validate_human_portrait", lambda img, url: _fake_clip(
        ok=False, code="NO_FACE_DETECTED", message="No human face detected.", category="not_human",
    ))
    r = vs.validate_stage1_human_portrait(skin_image, "https://a/x.jpg", _settings())
    assert r.ok is False and r.code == "NO_FACE_DETECTED"


def test_stage1_rejects_multiple_people(monkeypatch, skin_image):
    monkeypatch.setattr(vs, "validate_human_portrait", lambda img, url: _fake_clip(
        ok=False, code="MULTIPLE_FACES", message="Multiple people detected.", category="human_multifaces",
    ))
    r = vs.validate_stage1_human_portrait(skin_image, "https://a/x.jpg", _settings())
    assert r.ok is False and r.code == "MULTIPLE_FACES"


def test_stage1_rejects_synthetic(monkeypatch, skin_image):
    monkeypatch.setattr(vs, "validate_human_portrait", lambda img, url: _fake_clip(
        ok=False, code="NOT_REAL_PHOTO", message="Image appears to be a cartoon or render.", category="not_human",
    ))
    r = vs.validate_stage1_human_portrait(skin_image, "https://a/x.jpg", _settings())
    assert r.ok is False and r.code == "NOT_REAL_PHOTO"


def test_stage1_maps_clip_failure_to_face_detector_failure(monkeypatch, skin_image):
    """CLIP runtime failure must stay on the pre-existing FACE_DETECTOR_FAILURE /
    SYSTEM_ERROR contract so the external API's error codes don't change."""
    monkeypatch.setattr(vs, "validate_human_portrait", lambda img, url: _fake_clip(
        ok=False, code="CLIP_CLASSIFIER_FAILURE", error="boom",
    ))
    r = vs.validate_stage1_human_portrait(skin_image, "https://a/x.jpg", _settings())
    assert r.ok is False
    assert r.code == "FACE_DETECTOR_FAILURE"
    assert r.error_type == ErrorType.SYSTEM_ERROR


def test_stage1_skipped_when_face_not_required(monkeypatch, skin_image):
    r = vs.validate_stage1_human_portrait(skin_image, "https://a/x.jpg", _settings(REQUIRE_HUMAN_FACE=False))
    assert r.ok is True
