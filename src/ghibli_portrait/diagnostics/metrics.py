"""Process-lifetime request/error counters and memory introspection.

Deliberately dependency-free: no psutil, no prometheus_client, no database. RSS is
read from /proc (Linux, which is what the container runs) with a `resource` fallback.

Counter updates happen on the event-loop thread only — every HTTP request is handled
there — so plain integer increments are safe without a lock. Cost is a few ns per
request against a pipeline whose median is ~48 seconds.
"""

from __future__ import annotations

import os
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional

_STARTED_AT = time.time()
_STARTED_MONOTONIC = time.monotonic()

# Status-class buckets. Kept coarse on purpose — this is an operational health
# signal, not a metrics backend, and per-path cardinality would grow unbounded.
_BUCKETS = ("2xx", "3xx", "4xx", "5xx")


class RequestMetrics:
    """In-process counters covering the lifetime of this worker."""

    def __init__(self) -> None:
        self.total = 0
        self.active = 0
        self.peak_active = 0
        self.by_class: Dict[str, int] = {bucket: 0 for bucket in _BUCKETS}
        self.by_endpoint: Dict[str, int] = {}
        self.errors_total = 0
        self.unhandled_exceptions = 0
        self.last_error_at: Optional[float] = None
        self.total_duration_ms = 0.0

    def request_started(self) -> None:
        self.active += 1
        if self.active > self.peak_active:
            self.peak_active = self.active

    def request_finished(self, method: str, path: str, status: int, duration_ms: float) -> None:
        self.active = max(0, self.active - 1)
        self.total += 1
        self.total_duration_ms += duration_ms

        bucket = f"{status // 100}xx"
        if bucket in self.by_class:
            self.by_class[bucket] += 1

        if status >= 400:
            self.errors_total += 1
            self.last_error_at = time.time()

        # Endpoint keys are bounded: only routes this service actually declares are
        # counted, so a scan of random URLs cannot grow this dict.
        key = f"{method} {path}"
        if key in self.by_endpoint or len(self.by_endpoint) < 64:
            self.by_endpoint[key] = self.by_endpoint.get(key, 0) + 1

    def unhandled_exception(self) -> None:
        self.unhandled_exceptions += 1

    def snapshot(self) -> dict:
        avg = (self.total_duration_ms / self.total) if self.total else 0.0
        error_rate = (self.errors_total / self.total) if self.total else 0.0
        return {
            "totalRequests": self.total,
            "activeRequests": self.active,
            "peakActiveRequests": self.peak_active,
            "byStatusClass": dict(self.by_class),
            "byEndpoint": dict(sorted(self.by_endpoint.items(), key=lambda kv: -kv[1])),
            "errorsTotal": self.errors_total,
            "unhandledExceptions": self.unhandled_exceptions,
            "errorRate": round(error_rate, 4),
            "avgDurationMs": round(avg, 1),
            "lastErrorAt": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.last_error_at))
                if self.last_error_at
                else None
            ),
        }


# One instance per process (--workers 1 is a hard requirement of this service).
METRICS = RequestMetrics()


# ---------------------------------------------------------------------------
# Process introspection
# ---------------------------------------------------------------------------

def memory_usage() -> dict:
    """Current and peak RSS. Reads /proc on Linux; falls back to `resource`."""
    out: dict = {}

    try:
        # VmRSS is the resident set right now — the number that matters against a
        # container memory limit. `resource` only reports the peak.
        with open("/proc/self/status", "r") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    out["rssBytes"] = int(line.split()[1]) * 1024
                elif line.startswith("VmSize:"):
                    out["virtualBytes"] = int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass

    try:
        peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KB, macOS reports bytes.
        out["peakRssBytes"] = peak_kb * 1024 if sys.platform != "darwin" else peak_kb
    except Exception:
        pass

    if "rssBytes" in out:
        out["rssMb"] = round(out["rssBytes"] / (1024 * 1024), 1)
    if "peakRssBytes" in out:
        out["peakRssMb"] = round(out["peakRssBytes"] / (1024 * 1024), 1)
    return out


_git_commit_cache: Optional[str] = None


def git_commit() -> Optional[str]:
    """Short commit SHA, resolved once and cached.

    Prefers the GIT_COMMIT env var, because `.dockerignore` excludes `.git/` from
    the build context — inside a container the env var is the ONLY source. The
    local `git` fallback exists so this still works in development.
    """
    global _git_commit_cache
    if _git_commit_cache is not None:
        return _git_commit_cache or None

    for var in ("GIT_COMMIT", "GIT_SHA", "SOURCE_COMMIT", "COMMIT_SHA"):
        value = os.getenv(var, "").strip()
        if value:
            _git_commit_cache = value[:12]
            return _git_commit_cache

    try:
        repo_root = Path(__file__).resolve().parents[3]
        if (repo_root / ".git").exists():
            result = subprocess.run(
                ["git", "rev-parse", "--short=12", "HEAD"],
                cwd=repo_root, capture_output=True, text=True, timeout=2, check=False,
            )
            if result.returncode == 0:
                _git_commit_cache = result.stdout.strip()
                return _git_commit_cache or None
    except Exception:
        pass

    _git_commit_cache = ""  # negative-cache so we never re-shell out
    return None


def environment_name() -> str:
    """Deployment environment label, for telling prod from staging at a glance."""
    for var in ("ENVIRONMENT", "ENV", "APP_ENV", "DEPLOY_ENV"):
        value = os.getenv(var, "").strip()
        if value:
            return value.lower()
    return "unknown"


def uptime_seconds() -> float:
    return time.monotonic() - _STARTED_MONOTONIC


def started_at_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_STARTED_AT))


__all__ = [
    "METRICS",
    "RequestMetrics",
    "memory_usage",
    "git_commit",
    "environment_name",
    "uptime_seconds",
    "started_at_iso",
]
