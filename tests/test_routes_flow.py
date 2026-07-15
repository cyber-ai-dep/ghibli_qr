"""End-to-end route flow via FastAPI TestClient.

Covers health, utilities, local file saving, the validation-rejection path, and a
full /v1/ghibli-qr happy path with ARK + downloads + QR-decode mocked at the
boundaries (so the REAL orchestration, adapter and webhook-style delivery run).
"""

import io

import pytest
from PIL import Image

from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import ErrorStage
from src.ghibli_portrait.services import image_service as isvc
from src.ghibli_portrait.services.validation_service import ValidationResultV1
from src.ghibli_portrait.services.qr_validation import QRValidationResult
from src.ghibli_portrait.api import routes

s = Settings()


def _jpeg(size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 150, 120)).save(buf, format="JPEG")
    return buf.getvalue()


# ----------------------------------------------------------------- basics
def test_health(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["status"] == "healthy"


def test_qr_url_shortener(client):
    r = client.get("/v1/qr-url/", params={"url": "https://example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["code"]


def test_qr_lock_creates_local_file(client):
    r = client.post("/v1/qr-lock", json={"url": "https://example.com"})
    assert r.status_code == 200
    qr_url = r.json()["data"]["qrUrl"]
    filename = qr_url.rsplit("/tmp/", 1)[1]
    assert (s.TMP_PATH / filename).exists()  # saved on the same server


# ----------------------------------------------------- validation rejection
def test_ghibli_qr_rejects_localhost_url(client):
    r = client.post("/v1/ghibli-qr", json={"imgUrl": "http://localhost/a.jpg", "url": "https://example.com"})
    assert r.status_code == 422
    body = r.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "INVALID_IMAGE_URL"


def test_ghibli_qr_rejects_missing_field(client):
    r = client.post("/v1/ghibli-qr", json={"url": "https://example.com"})  # no imgUrl
    assert r.status_code == 422


# ----------------------------------------------------- full happy path
class _Resp:
    def __init__(self, content):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        return None


class _FakeAsyncClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return _Resp(_jpeg())


class _FakeHttpx:
    AsyncClient = _FakeAsyncClient


def test_ghibli_qr_happy_path(client, monkeypatch):
    fake_img = Image.new("RGB", (64, 64), (200, 150, 120))

    # 1) validation passes, returns a decoded source image
    async def fake_validate(image_url, *, settings=None, clip_sem=None):
        return ValidationResultV1(ok=True, stage=ErrorStage.STAGE1_GHIBLI), fake_img
    monkeypatch.setattr(routes, "validate_real_human_image_async", fake_validate)

    # 2) skip skin-tone extraction
    monkeypatch.setattr(routes, "extract_skin_color_hex", lambda img: None)

    # 3) ARK call + inlining mocked (no network, no paid call) — real adapter runs
    async def fake_seedream(prompt, images=None, **kw):
        return {"data": [{"url": "https://cdn.fake/out.jpg"}]}
    async def fake_inline(ref):
        return "data:image/jpeg;base64,AAAA"
    monkeypatch.setattr(isvc, "seedream_generate", fake_seedream)
    monkeypatch.setattr(isvc, "_inline_ref", fake_inline)

    # 4) downloads (re-host of ARK result URLs) mocked
    monkeypatch.setattr(routes, "httpx", _FakeHttpx)

    # 5) QR decode of the (fake) merged image mocked OK
    def fake_qr(img=None, expected_payload=None, **k):
        return QRValidationResult(ok=True, detected_payload=expected_payload, expected_payload=expected_payload, reason="ok")
    monkeypatch.setattr(routes, "validate_qr_from_image", fake_qr)

    r = client.post("/v1/ghibli-qr", json={"imgUrl": "https://i.ibb.co/x/photo.jpg", "url": "https://example.com"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["data"]["resultUrls"]
    assert body["data"]["qrValidation"]["ok"] is True
    # final image re-hosted on the same server
    assert "/tmp/final_" in body["data"]["resultUrls"][0]


def test_ghibli_qr_stage1_api_error(client, monkeypatch):
    fake_img = Image.new("RGB", (64, 64), (200, 150, 120))

    async def fake_validate(image_url, *, settings=None, clip_sem=None):
        return ValidationResultV1(ok=True, stage=ErrorStage.STAGE1_GHIBLI), fake_img
    monkeypatch.setattr(routes, "validate_real_human_image_async", fake_validate)
    monkeypatch.setattr(routes, "extract_skin_color_hex", lambda img: None)

    # ARK fails on submission -> generate_img returns code 501
    async def boom(*a, **k):
        raise RuntimeError("ark down")
    monkeypatch.setattr(isvc, "seedream_generate", boom)
    async def fake_inline(ref):
        return "data:image/jpeg;base64,AAAA"
    monkeypatch.setattr(isvc, "_inline_ref", fake_inline)

    r = client.post("/v1/ghibli-qr", json={"imgUrl": "https://i.ibb.co/x/photo.jpg", "url": "https://example.com"})
    assert r.status_code == 500
    assert r.json()["errors"][0]["code"] == "STAGE1_API_ERROR"
