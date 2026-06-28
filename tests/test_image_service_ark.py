"""ARK adapter (drop-in for KIE) — submission envelope, webhook-style delivery,
image inlining, and error handling. ARK + network are mocked (no paid calls)."""

import asyncio
import json

import pytest

from src.ghibli_portrait.services import image_service as isvc
from src.ghibli_portrait.api import routes


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Mock image inlining so no real download happens for any test here."""
    async def fake_inline(ref):
        return "data:image/jpeg;base64,AAAA"
    monkeypatch.setattr(isvc, "_inline_ref", fake_inline)


async def test_generate_img_returns_kie_style_envelope(monkeypatch):
    async def fake_seedream(prompt, images=None, **kw):
        return {"data": [{"url": "https://cdn.fake/out.jpg"}]}
    monkeypatch.setattr(isvc, "seedream_generate", fake_seedream)

    res = await isvc.generate_img(["https://x/in.jpg"], "prompt", model="qwen/image-edit")
    assert res["code"] == 200
    assert "taskId" in res["data"]


async def test_delivery_resolves_pending_future_like_webhook(monkeypatch):
    async def fake_seedream(prompt, images=None, **kw):
        return {"data": [{"url": "https://cdn.fake/out.jpg"}]}
    monkeypatch.setattr(isvc, "seedream_generate", fake_seedream)

    loop = asyncio.get_running_loop()
    res = await isvc.generate_img(["https://x/in.jpg"], "prompt", model="seedream/4.5-edit")
    task_id = res["data"]["taskId"]

    # Mimic routes.py: register the Future AFTER submission, then await it.
    fut = loop.create_future()
    routes.pending_tasks[task_id] = fut
    callback = await asyncio.wait_for(fut, timeout=5)

    assert callback.code == 200
    assert callback.is_failure is False
    assert callback.data.get_result_urls() == ["https://cdn.fake/out.jpg"]
    assert callback.data.model == "seedream/4.5-edit"
    assert task_id not in routes.pending_tasks  # popped on delivery


async def test_multi_image_stage2_payload(monkeypatch):
    captured = {}

    async def fake_seedream(prompt, images=None, **kw):
        captured["images"] = images
        return {"data": [{"url": "https://cdn.fake/merged.jpg"}]}
    monkeypatch.setattr(isvc, "seedream_generate", fake_seedream)

    res = await isvc.generate_img(
        ["https://x/stage1.jpg", "https://x/qrlock.jpg"], "merge", model="seedream/4.5-edit"
    )
    assert res["code"] == 200
    assert len(captured["images"]) == 2  # both references forwarded to ARK


async def test_error_when_ark_returns_no_url(monkeypatch):
    async def fake_seedream(prompt, images=None, **kw):
        return {"data": []}
    monkeypatch.setattr(isvc, "seedream_generate", fake_seedream)

    res = await isvc.generate_img(["https://x/in.jpg"], "p")
    assert res["code"] != 200
    assert "msg" in res


async def test_error_when_ark_raises(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("ark down")
    monkeypatch.setattr(isvc, "seedream_generate", boom)

    res = await isvc.generate_img(["https://x/in.jpg"], "p")
    assert res["code"] == 501
    assert "ark down" in res["msg"]


async def test_empty_input_is_rejected():
    res = await isvc.generate_img([], "p")
    assert res["code"] != 200
