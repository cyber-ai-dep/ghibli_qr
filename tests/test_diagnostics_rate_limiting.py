"""Diagnostics reports the REAL production rate limiter, not a copy of it.

The limiter under test is the one defined in main.py: a hand-rolled sliding
window over a module-level `collections.deque` of monotonic timestamps, global
across all callers. These tests mutate that actual deque and assert diagnostics
observes the change — which is what proves there is no shadow implementation.
"""

import time

import pytest

from src.ghibli_portrait import main as app_main
from src.ghibli_portrait.diagnostics.runtime import collect_rate_limiting


class _Cfg:
    def __init__(self, enabled=True, max_requests=60, window=60):
        self.RATE_LIMIT_ENABLED = enabled
        self.RATE_LIMIT_MAX_REQUESTS = max_requests
        self.RATE_LIMIT_WINDOW_SECONDS = window


@pytest.fixture
def clean_limiter():
    """Restore the real limiter's deque after each test."""
    saved = list(app_main._request_timestamps)
    app_main._request_timestamps.clear()
    yield app_main._request_timestamps
    app_main._request_timestamps.clear()
    app_main._request_timestamps.extend(saved)


# ---------------------------------------------------------------------------
# It reads the real limiter
# ---------------------------------------------------------------------------

def test_reads_the_actual_limiter_deque(clean_limiter):
    """Push entries into the limiter's own deque; diagnostics must see them."""
    now = time.monotonic()
    for offset in (0.1, 0.2, 0.3):
        clean_limiter.append(now - offset)

    report = collect_rate_limiting(_Cfg(max_requests=60, window=60))

    assert report["trackedEntries"] == 3
    assert report["requestsInWindow"] == 3
    assert report["remainingCapacity"] == 57


def test_reports_the_real_configured_policy():
    report = collect_rate_limiting(_Cfg(max_requests=42, window=7))

    assert report["maxRequests"] == 42
    assert report["windowSeconds"] == 7
    assert report["policy"] == "42 requests per 7s"


def test_reports_the_real_limited_paths():
    """Paths come from main._RATE_LIMITED_PATHS, not a hardcoded list."""
    report = collect_rate_limiting(_Cfg())
    assert set(report["limitedPaths"]) == set(app_main._RATE_LIMITED_PATHS)


def test_enabled_flag_follows_configuration():
    assert collect_rate_limiting(_Cfg(enabled=True))["enabled"] is True
    assert collect_rate_limiting(_Cfg(enabled=False))["enabled"] is False


# ---------------------------------------------------------------------------
# Accuracy: lazy pruning must not be mistaken for live load
# ---------------------------------------------------------------------------

def test_expired_entries_are_excluded_from_the_window_count(clean_limiter):
    """The limiter prunes lazily, so raw len() over-reports once traffic stops."""
    now = time.monotonic()
    clean_limiter.append(now - 500)      # long expired
    clean_limiter.append(now - 400)      # long expired
    clean_limiter.append(now - 1)        # live

    report = collect_rate_limiting(_Cfg(max_requests=60, window=60))

    assert report["trackedEntries"] == 3           # physically held
    assert report["requestsInWindow"] == 1         # actually governing admission
    assert report["expiredEntriesPendingPrune"] == 2
    assert report["remainingCapacity"] == 59


def test_reading_diagnostics_never_mutates_the_limiter(clean_limiter):
    """A read-only endpoint must not change request-admission behaviour."""
    now = time.monotonic()
    clean_limiter.append(now - 500)      # expired but NOT yet pruned
    clean_limiter.append(now - 1)

    before = list(clean_limiter)
    collect_rate_limiting(_Cfg(window=60))
    collect_rate_limiting(_Cfg(window=60))

    assert list(clean_limiter) == before, "diagnostics pruned the limiter's deque"


def test_saturation_is_reported(clean_limiter):
    now = time.monotonic()
    for _ in range(5):
        clean_limiter.append(now)

    report = collect_rate_limiting(_Cfg(max_requests=5, window=60))

    assert report["currentlyLimiting"] is True
    assert report["remainingCapacity"] == 0
    assert report["utilization"] == 1.0


def test_not_limiting_when_disabled_even_if_window_is_full(clean_limiter):
    now = time.monotonic()
    for _ in range(5):
        clean_limiter.append(now)

    report = collect_rate_limiting(_Cfg(enabled=False, max_requests=5, window=60))
    assert report["currentlyLimiting"] is False


def test_window_reset_is_derived_from_the_oldest_live_entry(clean_limiter):
    clean_limiter.append(time.monotonic() - 10)

    report = collect_rate_limiting(_Cfg(max_requests=60, window=60))

    assert 9 <= report["oldestRequestAgeSeconds"] <= 11
    assert 49 <= report["windowResetInSeconds"] <= 51


def test_empty_limiter_reports_zero_state(clean_limiter):
    report = collect_rate_limiting(_Cfg(max_requests=60, window=60))

    assert report["requestsInWindow"] == 0
    assert report["remainingCapacity"] == 60
    assert report["currentlyLimiting"] is False
    assert report["oldestRequestAgeSeconds"] is None
    assert report["windowResetInSeconds"] == 0.0


# ---------------------------------------------------------------------------
# Security: nothing caller-identifying exists, and nothing is leaked
# ---------------------------------------------------------------------------

def test_exposed_fields_are_an_explicit_allow_list(clean_limiter):
    """Locks the shape down: a future field carrying caller data fails here.

    An allow-list fails closed — a deny-list of forbidden words would not catch
    a new field named something unanticipated.
    """
    clean_limiter.append(time.monotonic())
    report = collect_rate_limiting(_Cfg())

    assert set(report) == {
        "enabled", "backend", "scope", "policy", "maxRequests", "windowSeconds",
        "limitedPaths", "storageHealthy", "trackedEntries", "requestsInWindow",
        "expiredEntriesPendingPrune", "remainingCapacity", "utilization",
        "currentlyLimiting", "oldestRequestAgeSeconds", "windowResetInSeconds",
        "rejectedResponsesObserved", "rejectedCounterSource",
    }


def test_exposes_no_ip_addresses_or_raw_timestamps(clean_limiter):
    """The limiter stores only monotonic floats — verify none of them escape."""
    import re

    now = time.monotonic()
    clean_limiter.append(now)
    clean_limiter.append(now - 5)

    report = collect_rate_limiting(_Cfg())
    serialized = repr(report)

    # Nothing that looks like a client address.
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", serialized)
    # Raw monotonic values are process-relative and never exposed; only ages.
    for stamp in clean_limiter:
        assert str(stamp) not in serialized
    # Every value is a scalar or a flat list of route templates — no per-caller
    # structure could hide inside a nested object.
    for key, value in report.items():
        assert isinstance(value, (str, int, float, bool, type(None), list)), key
        if isinstance(value, list):
            assert all(item in app_main._RATE_LIMITED_PATHS for item in value), key

    assert report["scope"].startswith("global")


# ---------------------------------------------------------------------------
# The rejection counter, and its stated provenance
# ---------------------------------------------------------------------------

def test_rejected_count_comes_from_http_observation_and_says_so(clean_limiter):
    from src.ghibli_portrait.diagnostics.metrics import METRICS

    before = METRICS.rate_limited_responses
    METRICS.request_finished("POST", "/v1/ghibli-qr", 429, 1.0)
    try:
        report = collect_rate_limiting(_Cfg())
        assert report["rejectedResponsesObserved"] == before + 1
        # The limitation is disclosed rather than papered over.
        assert "limiter keeps no rejection counter" in report["rejectedCounterSource"]
    finally:
        METRICS.rate_limited_responses = before


def test_only_429_increments_the_rejection_counter():
    from src.ghibli_portrait.diagnostics.metrics import METRICS

    before = METRICS.rate_limited_responses
    try:
        for status in (200, 403, 422, 500, 503):
            METRICS.request_finished("POST", "/v1/ghibli-qr", status, 1.0)
        assert METRICS.rate_limited_responses == before
    finally:
        METRICS.rate_limited_responses = before


# ---------------------------------------------------------------------------
# End-to-end through the API
# ---------------------------------------------------------------------------

def test_diagnostics_endpoint_includes_rate_limiting_section(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.ghibli_portrait.api.diagnostics_routes import register_diagnostics
    from src.ghibli_portrait.config import Settings

    token = "t" * 32

    class Cfg:
        def __init__(self):
            base = Settings()
            for name in dir(base):
                if name.isupper():
                    setattr(self, name, getattr(base, name))
            self.DIAGNOSTICS_TOKEN = token
            self.DIAGNOSTICS_MIN_TOKEN_LENGTH = 16

    app = FastAPI()
    register_diagnostics(app, Cfg())
    client = TestClient(app, raise_server_exceptions=False)

    data = client.get("/v1/diagnostics", headers={"X-Diagnostics-Token": token}).json()["data"]

    assert "rateLimiting" in data
    section = data["rateLimiting"]
    assert {
        "enabled", "backend", "scope", "policy", "maxRequests", "windowSeconds",
        "limitedPaths", "storageHealthy", "trackedEntries", "requestsInWindow",
        "remainingCapacity", "currentlyLimiting", "rejectedResponsesObserved",
    } <= set(section)
    assert section["storageHealthy"] is True
    assert "deque" in section["backend"]


def test_live_traffic_is_reflected_in_diagnostics(monkeypatch, clean_limiter):
    """Drive the real limiter via HTTP, then read it back through diagnostics."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app_main.s, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(app_main.s, "RATE_LIMIT_MAX_REQUESTS", 2)
    monkeypatch.setattr(app_main.s, "RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(app_main.s, "DIAGNOSTICS_TOKEN", "t" * 32)

    with TestClient(app_main.app, raise_server_exceptions=False) as client:
        headers = {"X-Diagnostics-Token": "t" * 32}
        payload = {"imgUrl": "https://example.com/x.jpg", "url": "https://example.com"}

        # Two admitted (they fail validation later, but the limiter counted them).
        client.post("/v1/ghibli-qr", json=payload)
        client.post("/v1/ghibli-qr", json=payload)

        section = client.get("/v1/diagnostics", headers=headers).json()["data"]["rateLimiting"]
        assert section["requestsInWindow"] == 2
        assert section["remainingCapacity"] == 0
        assert section["currentlyLimiting"] is True

        # The third must be rejected by the real limiter.
        assert client.post("/v1/ghibli-qr", json=payload).status_code == 429

        section = client.get("/v1/diagnostics", headers=headers).json()["data"]["rateLimiting"]
        assert section["rejectedResponsesObserved"] >= 1
