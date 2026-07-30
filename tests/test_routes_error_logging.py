"""Regression guard: error paths that used to return HTTP errors silently.

Each test asserts BOTH that a log line is now emitted at the right level AND that
the response body is unchanged — the logging work must not alter API behaviour.
"""

import logging

from PIL import Image

from src.ghibli_portrait.api import routes
from src.ghibli_portrait.models.schemas import ErrorStage
from src.ghibli_portrait.services.validation_service import ValidationResultV1


def _records(caplog, level, needle):
    return [
        r for r in caplog.records
        if r.levelno == level and needle in r.getMessage()
    ]


def test_ghibli_multiple_images_logs_warning(client, caplog):
    with caplog.at_level(logging.DEBUG):
        r = client.post(
            "/v1/ghibli",
            json={"imgUrls": ["https://a.example/x.jpg", "https://b.example/y.jpg"]},
        )

    assert r.status_code == 422
    assert r.json()["errors"][0]["code"] == "SINGLE_IMAGE_REQUIRED"
    assert len(_records(caplog, logging.WARNING, "SINGLE_IMAGE_REQUIRED")) == 1


def test_ghibli_validation_reject_logs_warning(client, caplog, monkeypatch):
    async def fake_validate(image_url, *, settings=None, clip_sem=None, download_sem=None):
        return (
            ValidationResultV1(
                ok=False,
                code="NOT_REAL_PHOTO",
                message="Image appears to be a cartoon.",
                stage=ErrorStage.STAGE1_GHIBLI,
            ),
            None,
        )

    monkeypatch.setattr(routes, "validate_real_human_image_async", fake_validate)

    with caplog.at_level(logging.DEBUG):
        r = client.post("/v1/ghibli", json={"imgUrls": ["https://a.example/x.jpg"]})

    assert r.status_code == 422
    assert r.json()["errors"][0]["code"] == "NOT_REAL_PHOTO"
    assert len(_records(caplog, logging.WARNING, "NOT_REAL_PHOTO")) == 1


def test_ghibli_stage1_api_error_logs_error(client, caplog, monkeypatch):
    fake_img = Image.new("RGB", (64, 64), (200, 150, 120))

    async def fake_validate(image_url, *, settings=None, clip_sem=None, download_sem=None):
        return ValidationResultV1(ok=True, stage=ErrorStage.STAGE1_GHIBLI), fake_img

    async def fake_submit(*args, **kwargs):
        return {"code": 501, "msg": "ark exploded"}

    monkeypatch.setattr(routes, "validate_real_human_image_async", fake_validate)
    monkeypatch.setattr(routes, "extract_skin_color_hex", lambda img: None)
    monkeypatch.setattr(routes, "_submit_generation", fake_submit)

    with caplog.at_level(logging.DEBUG):
        r = client.post("/v1/ghibli", json={"imgUrls": ["https://a.example/x.jpg"]})

    assert r.status_code == 500
    assert r.json()["errors"][0]["code"] == "GENERATION_API_ERROR"
    assert len(_records(caplog, logging.ERROR, "GENERATION_API_ERROR")) == 1


def test_pipeline_stage1_api_error_logs_error(client, caplog, monkeypatch):
    fake_img = Image.new("RGB", (64, 64), (200, 150, 120))

    async def fake_validate(image_url, *, settings=None, clip_sem=None, download_sem=None):
        return ValidationResultV1(ok=True, stage=ErrorStage.STAGE1_GHIBLI), fake_img

    async def fake_submit(*args, **kwargs):
        return {"code": 501, "msg": "ark down"}

    monkeypatch.setattr(routes, "validate_real_human_image_async", fake_validate)
    monkeypatch.setattr(routes, "extract_skin_color_hex", lambda img: None)
    monkeypatch.setattr(routes, "_submit_generation", fake_submit)

    with caplog.at_level(logging.DEBUG):
        r = client.post(
            "/v1/ghibli-qr",
            json={"imgUrl": "https://a.example/x.jpg", "url": "https://example.com"},
        )

    assert r.status_code == 500
    assert r.json()["errors"][0]["code"] == "STAGE1_API_ERROR"
    assert len(_records(caplog, logging.ERROR, "STAGE1_API_ERROR")) == 1


def test_delete_missing_qr_lock_logs_warning(client, caplog):
    with caplog.at_level(logging.DEBUG):
        r = client.delete("/v1/qr-lock/definitely-does-not-exist")

    assert r.status_code == 404
    assert r.json()["errors"][0]["code"] == "IMAGE_NOT_FOUND"
    assert len(_records(caplog, logging.WARNING, "IMAGE_NOT_FOUND")) == 1


def test_request_validation_error_logs_codes_not_values(client, caplog):
    """A rejected body carries the caller's imgUrl — codes only, never values."""
    secret_url = "https://private.example.com/very-secret-photo.jpg"

    with caplog.at_level(logging.DEBUG):
        r = client.post("/v1/ghibli-qr", json={"imgUrl": secret_url})   # 'url' missing

    assert r.status_code == 422
    logged = _records(caplog, logging.WARNING, "Request validation failed")
    assert len(logged) == 1
    message = logged[0].getMessage()
    assert "url:MISSING" in message
    assert secret_url not in message
