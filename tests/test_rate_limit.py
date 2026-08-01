"""
Tests for the rate-limit middleware in main.py.

The middleware runs before routing/body-parsing, so it intercepts requests to
/v1/ghibli-qr based on path alone — the tests below send minimal/invalid
bodies since only the middleware's own counting behavior is under test here,
not the route's business logic.
"""
from src.ghibli_portrait import main


def test_rate_limit_allows_requests_under_threshold(client, monkeypatch):
    monkeypatch.setattr(main, "_request_timestamps", main.deque())
    monkeypatch.setattr(main.s, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(main.s, "RATE_LIMIT_MAX_REQUESTS", 3)
    monkeypatch.setattr(main.s, "RATE_LIMIT_WINDOW_SECONDS", 60)

    for _ in range(3):
        resp = client.post("/v1/ghibli-qr", json={})
        assert resp.status_code != 429


def test_rate_limit_blocks_over_threshold(client, monkeypatch):
    monkeypatch.setattr(main, "_request_timestamps", main.deque())
    monkeypatch.setattr(main.s, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(main.s, "RATE_LIMIT_MAX_REQUESTS", 2)
    monkeypatch.setattr(main.s, "RATE_LIMIT_WINDOW_SECONDS", 60)

    client.post("/v1/ghibli-qr", json={})
    client.post("/v1/ghibli-qr", json={})
    resp = client.post("/v1/ghibli-qr", json={})

    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"
    body = resp.json()
    assert body["errors"][0]["code"] == "RATE_LIMIT_EXCEEDED"


def test_rate_limit_disabled_allows_unlimited(client, monkeypatch):
    monkeypatch.setattr(main, "_request_timestamps", main.deque())
    monkeypatch.setattr(main.s, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(main.s, "RATE_LIMIT_MAX_REQUESTS", 1)
    monkeypatch.setattr(main.s, "RATE_LIMIT_WINDOW_SECONDS", 60)

    for _ in range(5):
        resp = client.post("/v1/ghibli-qr", json={})
        assert resp.status_code != 429


def test_rate_limit_does_not_apply_to_health(client, monkeypatch):
    monkeypatch.setattr(main, "_request_timestamps", main.deque())
    monkeypatch.setattr(main.s, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(main.s, "RATE_LIMIT_MAX_REQUESTS", 1)
    monkeypatch.setattr(main.s, "RATE_LIMIT_WINDOW_SECONDS", 60)

    for _ in range(5):
        resp = client.get("/v1/health")
        assert resp.status_code == 200
