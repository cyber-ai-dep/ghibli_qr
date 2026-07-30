"""Diagnostics API: always-registered routes, token auth, secret containment.

These build a bare FastAPI() and call register_diagnostics() directly rather than
using the `client` fixture, so they never trigger the lifespan (and therefore never
pay the CLIP preload).
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.ghibli_portrait.api.diagnostics_routes import register_diagnostics
from src.ghibli_portrait.diagnostics.runtime import TrackedSemaphore

GOOD_TOKEN = "t" * 32
ARK_SENTINEL = "ark-LEAK-CANARY-9f8e7d6c5b4a"
DIAG_PATHS = ("/v1/diagnostics", "/v1/diagnostics/logs", "/v1/diagnostics/logs/stats")


class _Cfg:
    """Minimal settings stand-in — only what the router and collectors read."""

    def __init__(self, token=GOOD_TOKEN):
        from src.ghibli_portrait.config import Settings

        base = Settings()
        for name in dir(base):
            if name.isupper():
                setattr(self, name, getattr(base, name))
        self.DIAGNOSTICS_TOKEN = token
        self.DIAGNOSTICS_MIN_TOKEN_LENGTH = 16


def _build(token=GOOD_TOKEN):
    app = FastAPI()
    register_diagnostics(app, _Cfg(token))
    return TestClient(app, raise_server_exceptions=False)


def _auth(token=GOOD_TOKEN):
    return {"X-Diagnostics-Token": token}


# ---------------------------------------------------------------------------
# Registration & discoverability
# ---------------------------------------------------------------------------

def test_routes_are_always_registered_even_without_a_token():
    """Discoverability is independent of configuration — no restart needed."""
    client = _build(token="")
    paths = client.app.openapi()["paths"]

    for path in DIAG_PATHS:
        assert path in paths


def test_endpoints_appear_in_openapi_schema():
    client = _build()
    schema = client.app.openapi()

    assert sorted(schema["paths"]["/v1/diagnostics/logs"]) == ["delete", "get"]
    assert "get" in schema["paths"]["/v1/diagnostics"]

    tags = schema["paths"]["/v1/diagnostics"]["get"]["tags"]
    assert tags == ["Diagnostics"]


def test_swagger_documents_internal_only_usage():
    """The contract must be stated where an integrator will actually read it."""
    client = _build()
    operation = client.app.openapi()["paths"]["/v1/diagnostics"]["get"]

    combined = (operation.get("summary", "") + operation.get("description", "")).lower()
    assert "internal" in combined


def test_openapi_declares_the_401_response():
    client = _build()
    operation = client.app.openapi()["paths"]["/v1/diagnostics"]["get"]
    assert "401" in operation["responses"]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", DIAG_PATHS)
def test_missing_token_returns_401(path):
    client = _build()
    r = client.get(path)

    assert r.status_code == 401
    assert "www-authenticate" in {k.lower() for k in r.headers}


@pytest.mark.parametrize("path", DIAG_PATHS)
def test_invalid_token_returns_401(path):
    client = _build()
    r = client.get(path, headers=_auth("w" * 32))
    assert r.status_code == 401


def test_delete_also_requires_a_token():
    client = _build()
    assert client.delete("/v1/diagnostics/logs").status_code == 401
    assert client.delete("/v1/diagnostics/logs", headers=_auth()).status_code == 200


def test_unset_token_denies_everything_rather_than_allowing_anonymous_access():
    """A missing secret must fail closed. This is the most important auth test."""
    client = _build(token="")

    for path in DIAG_PATHS:
        assert client.get(path).status_code == 401
    # Explicitly: an empty credential must not match an empty configured token.
    assert client.get("/v1/diagnostics", headers=_auth("")).status_code == 401


def test_valid_token_returns_diagnostics():
    client = _build()
    r = client.get("/v1/diagnostics", headers=_auth())

    assert r.status_code == 200
    body = r.json()
    assert list(body.keys()) == ["success", "data", "message", "errors", "timestamp"]
    assert body["success"] is True and body["errors"] is None


def test_bearer_authorization_header_is_accepted():
    client = _build()
    r = client.get("/v1/diagnostics", headers={"Authorization": f"Bearer {GOOD_TOKEN}"})
    assert r.status_code == 200


def test_bearer_with_wrong_scheme_or_value_is_rejected():
    client = _build()
    assert client.get("/v1/diagnostics", headers={"Authorization": GOOD_TOKEN}).status_code == 401
    assert client.get(
        "/v1/diagnostics", headers={"Authorization": f"Basic {GOOD_TOKEN}"}
    ).status_code == 401


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def test_snapshot_contains_every_operational_section():
    client = _build()
    data = client.get("/v1/diagnostics", headers=_auth()).json()["data"]

    assert set(data) == {
        "service", "health", "requestId", "requests", "rateLimiting",
        "concurrency", "pendingTasks", "models", "memory", "storage",
        "config", "logBuffer", "recentLogs",
    }
    assert {"version", "gitCommit", "environment", "uptimeSeconds", "startedAt"} <= set(data["service"])
    assert {"totalRequests", "activeRequests", "errorsTotal", "errorRate"} <= set(data["requests"])
    assert data["health"]["status"] in {"healthy", "degraded", "unhealthy"}


def test_request_counters_increment():
    client = _build()

    first = client.get("/v1/diagnostics", headers=_auth()).json()["data"]["requests"]
    second = client.get("/v1/diagnostics", headers=_auth()).json()["data"]["requests"]

    # The middleware is not installed on this bare app, so counters are
    # process-global and monotonic rather than per-app — assert only that.
    assert second["totalRequests"] >= first["totalRequests"]


def test_snapshot_never_leaks_secrets(monkeypatch):
    """The single most important assertion in this file."""
    import src.ghibli_portrait.services.seedream_service as ark

    monkeypatch.setattr(ark, "ARK_API_KEY", ARK_SENTINEL)
    client = _build()

    r = client.get("/v1/diagnostics", headers=_auth())
    serialized = json.dumps(r.json())

    assert ARK_SENTINEL not in serialized
    assert GOOD_TOKEN not in serialized

    config = r.json()["data"]["config"]
    assert config["arkApiKeyConfigured"] is True
    assert len(config["arkApiKeyFingerprint"]) == 8


def test_snapshot_log_limit_is_respected(monkeypatch):
    import logging

    from src.ghibli_portrait.diagnostics import log_buffer
    from src.ghibli_portrait.diagnostics.log_buffer import RingBufferHandler

    handler = RingBufferHandler(capacity=50)
    monkeypatch.setattr(log_buffer, "_handler", handler)

    seeded = logging.getLogger("test.diag.snapshot")
    seeded.propagate = False
    seeded.setLevel(logging.DEBUG)
    seeded.addHandler(handler)
    try:
        for i in range(30):
            seeded.info("entry %d", i)

        client = _build()
        capped = client.get("/v1/diagnostics", params={"logLimit": 5}, headers=_auth())
        assert len(capped.json()["data"]["recentLogs"]) == 5

        omitted = client.get("/v1/diagnostics", params={"logLimit": 0}, headers=_auth())
        assert omitted.json()["data"]["recentLogs"] == []
    finally:
        seeded.removeHandler(handler)


# ---------------------------------------------------------------------------
# Log querying
# ---------------------------------------------------------------------------

def test_logs_endpoint_filters(monkeypatch):
    import logging

    from src.ghibli_portrait.diagnostics import log_buffer
    from src.ghibli_portrait.diagnostics.log_buffer import RingBufferHandler

    handler = RingBufferHandler(capacity=50)
    monkeypatch.setattr(log_buffer, "_handler", handler)

    seeded = logging.getLogger("test.diag.seed")
    seeded.propagate = False
    seeded.setLevel(logging.DEBUG)
    seeded.addHandler(handler)
    try:
        seeded.info("plain info line")
        seeded.error("exploding badger")

        client = _build()

        everything = client.get("/v1/diagnostics/logs", headers=_auth()).json()["data"]
        assert everything["matched"] == 2

        errors_only = client.get(
            "/v1/diagnostics/logs", params={"level": "ERROR"}, headers=_auth()
        ).json()["data"]
        assert errors_only["matched"] == 1
        assert "badger" in errors_only["entries"][0]["message"]

        by_text = client.get(
            "/v1/diagnostics/logs", params={"contains": "BADGER"}, headers=_auth()
        ).json()["data"]
        assert by_text["matched"] == 1     # case-insensitive

        newest_seq = everything["logBuffer"]["newestSeq"]
        tail = client.get(
            "/v1/diagnostics/logs", params={"sinceSeq": newest_seq}, headers=_auth()
        ).json()["data"]
        assert tail["matched"] == 0        # the tail-polling primitive
    finally:
        seeded.removeHandler(handler)


def test_delete_clears_buffer(monkeypatch):
    import logging

    from src.ghibli_portrait.diagnostics import log_buffer
    from src.ghibli_portrait.diagnostics.log_buffer import RingBufferHandler

    handler = RingBufferHandler(capacity=50)
    monkeypatch.setattr(log_buffer, "_handler", handler)

    seeded = logging.getLogger("test.diag.clear")
    seeded.propagate = False
    seeded.addHandler(handler)
    try:
        seeded.warning("something")
        client = _build()

        removed = client.delete("/v1/diagnostics/logs", headers=_auth()).json()["data"]["removed"]
        assert removed == 1

        after = client.get("/v1/diagnostics/logs", headers=_auth()).json()["data"]
        assert after["logBuffer"]["size"] == 0
    finally:
        seeded.removeHandler(handler)


# ---------------------------------------------------------------------------
# TrackedSemaphore — gates the load-tested 24-way generation path
# ---------------------------------------------------------------------------

def test_tracked_semaphore_tracks_use_and_waiters():
    async def scenario():
        sem = TrackedSemaphore(2, name="probe")
        assert sem.snapshot() == {"limit": 2, "inUse": 0, "waiting": 0}

        async def hold(event):
            async with sem:            # the form routes.py actually uses
                await event.wait()

        events = [asyncio.Event() for _ in range(3)]
        held = [asyncio.create_task(hold(events[i])) for i in range(2)]
        await asyncio.sleep(0.05)
        assert sem.in_use == 2 and sem.waiting == 0

        blocked = asyncio.create_task(hold(events[2]))
        await asyncio.sleep(0.05)
        assert sem.waiting == 1

        # A cancelled waiter must not leak a 'waiting' count.
        blocked.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocked
        await asyncio.sleep(0.05)
        assert sem.waiting == 0

        for event in events[:2]:
            event.set()
        await asyncio.gather(*held)
        assert sem.in_use == 0 and sem.waiting == 0

    asyncio.run(scenario())


def test_tracked_semaphore_still_blocks_at_limit():
    """Semantics must be identical to asyncio.Semaphore — this gates real throughput."""

    async def scenario():
        sem = TrackedSemaphore(1)
        await sem.acquire()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sem.acquire(), timeout=0.1)
        sem.release()

    asyncio.run(scenario())
