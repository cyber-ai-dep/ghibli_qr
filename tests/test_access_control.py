"""
Tests for the optional PRIVATE_MODE access-control middleware in main.py.

Default (PRIVATE_MODE=false) must stay fully open — that's the zero-risk
guarantee the feature is built on. When enabled, every route except
/v1/health must reject IPs not in ALLOWED_IPS.
"""
from src.ghibli_portrait import main


def test_private_mode_off_by_default_allows_everything(client):
    resp = client.get("/v1/qr-url/?url=https://example.com")
    assert resp.status_code == 200


def test_private_mode_blocks_unlisted_ip(client, monkeypatch):
    monkeypatch.setattr(main.s, "PRIVATE_MODE", True)
    monkeypatch.setattr(main.s, "ALLOWED_IPS", {"9.9.9.9"})  # TestClient is "testclient"
    resp = client.get("/v1/qr-url/?url=https://example.com")
    assert resp.status_code == 403
    body = resp.json()
    assert body["errors"][0]["code"] == "FORBIDDEN"


def test_private_mode_allows_listed_ip(client, monkeypatch):
    monkeypatch.setattr(main.s, "PRIVATE_MODE", True)
    monkeypatch.setattr(main.s, "ALLOWED_IPS", {"testclient"})
    resp = client.get("/v1/qr-url/?url=https://example.com")
    assert resp.status_code == 200


def test_private_mode_never_blocks_health(client, monkeypatch):
    monkeypatch.setattr(main.s, "PRIVATE_MODE", True)
    monkeypatch.setattr(main.s, "ALLOWED_IPS", set())  # nobody allowed
    resp = client.get("/v1/health")
    assert resp.status_code == 200
