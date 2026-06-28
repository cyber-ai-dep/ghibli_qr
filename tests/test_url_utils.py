"""URL shortener — deterministic hashing."""

from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.utils.url_utils import shorten

s = Settings()


def test_shorten_is_deterministic():
    a = shorten("https://example.com/profile")
    b = shorten("https://example.com/profile")
    assert a.code == b.code
    assert a.url == b.url


def test_shorten_code_length():
    data = shorten("https://example.com")
    assert len(data.code) == s.QR_SHORT_CODE_LENGTH


def test_shorten_different_urls_differ():
    assert shorten("https://a.com").code != shorten("https://b.com").code


def test_shorten_url_contains_domain_and_code():
    data = shorten("https://example.com")
    assert data.code in data.url
    if s.DOMAIN:
        assert data.url.startswith(s.DOMAIN)
