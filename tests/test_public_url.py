"""
Tests for request-derived asset URLs (api/public_url.py).

The service returns links to files it serves itself, so the base URL has to match
however the caller reached it — a domain over HTTPS through a proxy, or a bare
IP:port directly. These cover both, plus the fallback and allow-list behaviour.
"""
import pytest

from src.ghibli_portrait.api.public_url import (
    asset_url,
    get_public_base_url,
    resolve_from_headers,
    reset_public_base_url,
    set_public_base_url,
)

FALLBACK = "https://fallback.example.com"


def _h(**kwargs):
    """Build ASGI-style header pairs from keyword names (underscore -> dash)."""
    return [(k.replace("_", "-").encode(), v.encode()) for k, v in kwargs.items()]


def test_direct_ip_request_keeps_ip_and_port():
    result = resolve_from_headers(_h(host="72.61.181.1:30820"), fallback=FALLBACK)
    assert result == "http://72.61.181.1:30820"


def test_proxied_https_request_uses_forwarded_scheme_and_host():
    headers = _h(host="ghibli-api:8010", x_forwarded_proto="https", x_forwarded_host="api.3lababee.com")
    assert resolve_from_headers(headers, fallback=FALLBACK) == "https://api.3lababee.com"


def test_forwarded_proto_alone_upgrades_scheme():
    """nginx commonly sets X-Forwarded-Proto but passes Host through unchanged."""
    headers = _h(host="api.3lababee.com", x_forwarded_proto="https")
    assert resolve_from_headers(headers, fallback=FALLBACK) == "https://api.3lababee.com"


def test_multi_proxy_chain_takes_leftmost_value():
    headers = _h(host="a.example.com", x_forwarded_proto="https, http")
    assert resolve_from_headers(headers, fallback=FALLBACK) == "https://a.example.com"


def test_missing_host_falls_back():
    assert resolve_from_headers([], fallback=FALLBACK) == FALLBACK


def test_unknown_scheme_is_not_reflected():
    headers = _h(host="a.example.com", x_forwarded_proto="javascript")
    assert resolve_from_headers(headers, fallback=FALLBACK) == "http://a.example.com"


def test_untrusted_host_falls_back_when_allowlist_set():
    headers = _h(host="attacker.example.net")
    result = resolve_from_headers(headers, fallback=FALLBACK, trusted_hosts={"api.3lababee.com"})
    assert result == FALLBACK


def test_trusted_host_allowed_even_with_explicit_port():
    headers = _h(host="api.3lababee.com:8443", x_forwarded_proto="https")
    result = resolve_from_headers(headers, fallback=FALLBACK, trusted_hosts={"api.3lababee.com"})
    assert result == "https://api.3lababee.com:8443"


def test_bound_base_url_drives_asset_url():
    token = set_public_base_url("https://api.3lababee.com")
    try:
        assert get_public_base_url() == "https://api.3lababee.com"
        assert asset_url("final_abc.jpg") == "https://api.3lababee.com/tmp/final_abc.jpg"
    finally:
        reset_public_base_url(token)


def test_trailing_slash_is_normalised():
    token = set_public_base_url("https://api.3lababee.com/")
    try:
        assert asset_url("x.jpg") == "https://api.3lababee.com/tmp/x.jpg"
    finally:
        reset_public_base_url(token)


def test_falls_back_to_domain_outside_a_request(monkeypatch):
    from src.ghibli_portrait.config import Settings

    monkeypatch.setattr(Settings, "DOMAIN", "https://configured.example.com", raising=False)
    assert get_public_base_url() == "https://configured.example.com"


@pytest.mark.parametrize("path", ["/v1/health", "/v1/qr-url/?url=https://example.com"])
def test_live_requests_still_succeed(client, path):
    """The middleware binds/resets the context var around every request."""
    assert client.get(path).status_code == 200
