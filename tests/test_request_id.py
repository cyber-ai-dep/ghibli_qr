"""Request correlation: X-Request-ID on every response, contextvar propagation."""

import asyncio

import pytest

from src.ghibli_portrait.diagnostics.context import (
    get_request_id,
    sanitize_request_id,
    set_request_id,
)


def test_health_carries_request_id(client):
    r = client.get("/v1/health")
    assert r.headers.get("x-request-id")


def test_supplied_request_id_is_echoed(client):
    r = client.get("/v1/health", headers={"X-Request-ID": "probe-001"})
    assert r.headers["x-request-id"] == "probe-001"


@pytest.mark.parametrize(
    "hostile",
    [
        "bad\nvalue",           # CRLF would forge lines in the line-oriented stdout stream
        "bad\r\nInjected: 1",
        "x" * 200,              # over the 64-char cap
        "has spaces",
    ],
)
def test_hostile_request_id_is_rejected_and_regenerated(client, hostile):
    """Log-injection guard — the value must never be used verbatim."""
    r = client.get("/v1/health", headers={"X-Request-ID": hostile})
    returned = r.headers["x-request-id"]

    assert returned != hostile
    assert returned and "\n" not in returned and "\r" not in returned


def test_sanitize_accepts_reasonable_ids():
    assert sanitize_request_id("abc-123_x.y:z") == "abc-123_x.y:z"
    assert sanitize_request_id("") is None
    assert sanitize_request_id(None) is None
    assert sanitize_request_id("bad value") is None


def test_unknown_path_404_carries_request_id(client):
    r = client.get("/definitely-not-a-route")
    assert r.status_code == 404
    assert r.headers.get("x-request-id")


def test_validation_error_422_carries_request_id(client):
    r = client.post("/v1/ghibli-qr", json={"imgUrl": "x"})   # missing 'url'
    assert r.status_code == 422
    assert r.headers.get("x-request-id")


def test_internal_error_500_carries_request_id_and_body_is_unchanged(monkeypatch):
    """ServerErrorMiddleware writes above our middleware, so the handler must set
    the header itself — and the response body must stay byte-shape identical."""
    from fastapi.testclient import TestClient

    from src.ghibli_portrait.api import routes
    from src.ghibli_portrait.main import app

    def boom(*args, **kwargs):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(routes, "shorten", boom)

    # raise_server_exceptions=False so the real exception handler runs, instead of
    # TestClient re-raising past it (which is the default).
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/v1/qr-url/", params={"url": "https://example.com"})

    assert r.status_code == 500
    assert r.headers.get("x-request-id")

    body = r.json()
    assert list(body.keys()) == ["success", "data", "message", "errors", "timestamp"]
    assert body["success"] is False
    assert body["errors"][0]["code"] == "INTERNAL_ERROR"
    assert body["errors"][0]["type"] == "SYSTEM_ERROR"
    assert body["errors"][0]["stage"] == "ORCHESTRATION"


def test_request_id_survives_to_thread():
    """The pipeline hands work to worker threads at 18 asyncio.to_thread sites;
    correlation depends on the context being copied across that boundary."""

    async def scenario():
        token = set_request_id("ctx-probe")
        try:
            return await asyncio.to_thread(get_request_id)
        finally:
            from src.ghibli_portrait.diagnostics.context import reset_request_id
            reset_request_id(token)

    assert asyncio.run(scenario()) == "ctx-probe"
