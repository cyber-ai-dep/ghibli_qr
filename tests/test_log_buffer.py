"""Ring buffer: capture, bounds, redaction, thread safety.

Every test attaches the handler to a PRIVATE logger with propagate=False — never
to root — so these cannot interfere with pytest's caplog or with other tests.
"""

import gc
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.ghibli_portrait.diagnostics.log_buffer import RingBufferHandler


@pytest.fixture
def buffered():
    """(logger, handler) pair isolated from the root logger."""
    handler = RingBufferHandler(capacity=100)
    logger = logging.getLogger(f"test.buffer.{id(handler)}")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    yield logger, handler
    logger.removeHandler(handler)


def test_captures_record_fields(buffered):
    logger, handler = buffered
    logger.warning("hello %s", "world")

    entry = handler.snapshot()[0]
    assert entry.message == "hello world"
    assert entry.level == "WARNING"
    assert entry.levelno == logging.WARNING
    assert entry.logger == logger.name
    assert entry.line > 0


def test_evicts_at_capacity_and_seq_stays_monotonic(buffered):
    logger, handler = buffered
    for i in range(250):
        logger.info("msg %d", i)

    entries = handler.snapshot()
    assert len(entries) == 100                 # bounded by capacity
    assert handler.total_captured == 250       # but the total still counts them all
    assert handler.dropped_count == 150        # so a poller can detect the gap
    # seq must survive eviction so sinceSeq polling cannot silently repeat entries.
    assert entries[0].seq == 151 and entries[-1].seq == 250


def test_data_uri_collapsed_before_truncation(buffered):
    """The rule that keeps the buffer's memory ceiling real.

    image_service inlines ~200-400KB base64 data URIs; ARK errors echo them back.
    Truncating first would still store max_message_chars of base64.
    """
    logger, handler = buffered
    payload = "data:image/jpeg;base64," + ("A" * 50_000)
    logger.info("inlining ref %s done", payload)

    entry = handler.snapshot()[0]
    assert len(entry.message) < 1000
    assert "chars elided" in entry.message
    assert "AAAAAAAAAA" not in entry.message


def test_redacts_secrets(buffered, monkeypatch):
    logger, handler = buffered
    monkeypatch.setenv("ARK_API_KEY", "ark-SECRET-abcdef123456")
    handler.redactor = type(handler.redactor)("query")  # re-snapshot env

    logger.info("key ark-SECRET-abcdef123456 with Authorization: Bearer tok_abcdef123456")
    message = handler.snapshot()[0].message

    assert "ark-SECRET-abcdef123456" not in message
    assert "tok_abcdef123456" not in message


def test_redacts_url_query_but_keeps_host_and_path(buffered):
    """Signed-URL credentials go; the part needed to reproduce a failure stays."""
    logger, handler = buffered
    logger.info("fetch https://cdn.example.com/a/b.jpg?X-Amz-Signature=deadbeef&exp=9")

    message = handler.snapshot()[0].message
    assert "X-Amz-Signature" not in message
    assert "cdn.example.com/a/b.jpg" in message


def test_exception_stored_as_text_without_pinning_frames(buffered):
    """exc_info holds live frames whose locals include decoded images and base64
    payloads — storing the tuple would pin megabytes per entry."""
    logger, handler = buffered
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("failed")

    entry = handler.snapshot()[0]
    assert isinstance(entry.exc, str)
    assert "ValueError: boom" in entry.exc
    referents = gc.get_referents(entry)
    assert not any(hasattr(r, "tb_frame") or hasattr(r, "f_locals") for r in referents)


def test_recursion_guard(buffered):
    """A log call raised from inside emit() must not recurse."""
    logger, handler = buffered
    real = handler.redactor

    class LoggingRedactor:
        url_mode = "query"

        def collapse_data_uris(self, text):
            return real.collapse_data_uris(text)

        def __call__(self, text):
            logger.error("redactor logged from inside emit")
            return real(text)

    handler.redactor = LoggingRedactor()
    logger.info("trigger")
    handler.redactor = real

    assert len(handler.snapshot()) == 1  # the nested call was swallowed


def test_thread_safety():
    """Records arrive from the event loop, 18 to_thread sites, and a 100-worker pool."""
    handler = RingBufferHandler(capacity=1000)
    logger = logging.getLogger("test.buffer.threads")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    def work(n):
        for i in range(500):
            logger.info("t%d-%d", n, i)

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(work, range(8)))
        assert handler.total_captured == 4000
        assert len(handler.snapshot()) == 1000
    finally:
        logger.removeHandler(handler)


def test_clear_empties_buffer(buffered):
    logger, handler = buffered
    logger.info("one")
    logger.info("two")

    assert handler.clear() == 2
    assert handler.snapshot() == []
