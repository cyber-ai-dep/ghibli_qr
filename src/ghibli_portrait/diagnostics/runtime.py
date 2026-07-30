"""Live runtime state collectors for the diagnostics snapshot.

Import rule: routes.py imports TrackedSemaphore from here, so this module must NOT
import routes.py at module level. The pipeline collector does the import inside the
function body — the same pattern image_service._deliver() already uses for this reason.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import os
import platform
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Wall-clock and monotonic start, captured at import (i.e. process start).
_STARTED_AT = time.time()
_STARTED_MONOTONIC = time.monotonic()

# Files scanned per storage snapshot before giving up — a full glob of a large
# tmp dir is a blocking syscall storm we do not want on a diagnostics call.
_MAX_FILES_SCANNED = 5000


class TrackedSemaphore(asyncio.Semaphore):
    """asyncio.Semaphore that exposes how saturated it is.

    Exists because reading `Semaphore._value` is private API. `async with sem`
    goes through `__aenter__` -> `acquire()`, so overriding acquire/release here
    captures every use site. Cost is two integer operations against a
    multi-second network call.
    """

    def __init__(self, value: int = 1, *, name: str = ""):
        super().__init__(value)
        self.limit = value
        self.name = name
        self.in_use = 0
        self.waiting = 0

    async def acquire(self) -> bool:
        self.waiting += 1
        try:
            result = await super().acquire()
        finally:
            # try/finally so a cancelled waiter still decrements.
            self.waiting -= 1
        self.in_use += 1
        return result

    def release(self) -> None:
        super().release()
        if self.in_use > 0:
            self.in_use -= 1

    def snapshot(self) -> dict:
        return {"limit": self.limit, "inUse": self.in_use, "waiting": self.waiting}


def uptime_seconds() -> float:
    return time.monotonic() - _STARTED_MONOTONIC


def _fingerprint(secret: str) -> Optional[str]:
    """First 8 hex of sha256 — answers 'is the right key mounted?' non-invertibly."""
    if not secret:
        return None
    return hashlib.sha256(secret.encode()).hexdigest()[:8]


def collect_service(app_version: str = "1.0.0") -> dict:
    from src.ghibli_portrait.diagnostics import metrics

    return {
        "name": "ghibli-portrait-api",
        "version": app_version,
        "gitCommit": metrics.git_commit(),
        "environment": metrics.environment_name(),
        "pid": os.getpid(),
        "hostname": platform.node(),
        "pythonVersion": sys.version.split()[0],
        "platform": platform.platform(),
        "startedAt": metrics.started_at_iso(),
        "uptimeSeconds": round(metrics.uptime_seconds(), 1),
        "uptimeHuman": _human_duration(metrics.uptime_seconds()),
        # Hard requirement, not an observation: pending_tasks is in-process state.
        "workers": 1,
    }


def _human_duration(seconds: float) -> str:
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def collect_health(models: dict, concurrency: dict, config: dict) -> dict:
    """Roll the individual signals into one at-a-glance verdict.

    Intentionally derived from state this process already knows — it makes no
    outbound calls, so hitting diagnostics can never itself cost an ARK request
    or add latency to the pipeline.
    """
    checks = {
        "clipLoaded": bool(models.get("clipLoaded")),
        "clipTextFeaturesReady": bool(models.get("clipTextFeaturesReady")),
        "qreaderLoaded": bool(models.get("qreaderLoaded")),
        "arkApiKeyConfigured": bool(config.get("arkApiKeyConfigured")),
        "domainConfigured": bool(config.get("domain")),
    }

    # Saturation is a warning, not a failure: a full queue still serves requests.
    saturated = [
        name
        for name in ("clip", "download", "generation")
        if (sem := concurrency.get(name, {})) and sem.get("waiting")
    ]

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        status = "unhealthy"
    elif saturated:
        status = "degraded"
    else:
        status = "healthy"

    return {
        "status": status,
        "checks": checks,
        "failedChecks": failed,
        "saturatedSemaphores": saturated,
    }


def collect_concurrency() -> dict:
    """Semaphore saturation + event-loop/thread counts."""
    from src.ghibli_portrait.api import routes  # late import: avoids a cycle

    out: dict = {}
    for key, sem in (
        ("clip", routes._clip_sem),
        ("download", routes._download_sem),
        ("generation", routes._gen_sem),
    ):
        if isinstance(sem, TrackedSemaphore):
            out[key] = sem.snapshot()
        else:
            # Fallback if the semaphores are ever swapped back to plain ones.
            out[key] = {"limit": None, "inUse": None, "waiting": None, "locked": sem.locked()}

    try:
        out["eventLoopTasks"] = len(asyncio.all_tasks())
    except RuntimeError:
        out["eventLoopTasks"] = None
    out["threads"] = {"active": threading.active_count()}
    return out


def collect_pending_tasks() -> dict:
    """Snapshot the in-flight generation registry.

    No `await` between the reads so the dict cannot mutate mid-iteration.
    """
    from src.ghibli_portrait.api import routes

    tasks = routes.pending_tasks
    count = len(tasks)
    items = list(tasks.items())[:20]

    now = time.monotonic()
    ages = [
        now - created
        for _, fut in items
        if (created := getattr(fut, "_created_at", None)) is not None
    ]
    return {
        "count": count,
        "oldestAgeSeconds": round(max(ages), 1) if ages else None,
        "taskIds": [task_id for task_id, _ in items],
        "truncated": count > len(items),
    }


def collect_models() -> dict:
    """Whether the two heavy CPU models are actually loaded in this process."""
    out: dict = {}
    try:
        from src.ghibli_portrait.services import clip_validation_service as clip

        out.update(clip.model_status())
    except Exception as exc:
        out["clipError"] = str(exc)[:200]

    try:
        from src.ghibli_portrait.services import qr_validation

        out["qreaderLoaded"] = getattr(qr_validation, "_qreader", None) is not None
        out["qreaderModelSize"] = getattr(qr_validation, "QR_MODEL_SIZE", None)
    except Exception as exc:
        out["qreaderError"] = str(exc)[:200]

    return out


def collect_storage(tmp_path: Path) -> dict:
    """Scan the served tmp dir. BLOCKING — always call via asyncio.to_thread()."""
    counts = {"final": 0, "stage1": 0, "qrlock": 0, "other": 0}
    total_bytes = 0
    oldest: Optional[float] = None
    scanned = 0
    truncated = False

    try:
        now = time.time()
        for path in itertools.islice(tmp_path.glob("*"), _MAX_FILES_SCANNED + 1):
            if scanned >= _MAX_FILES_SCANNED:
                truncated = True
                break
            if not path.is_file():
                continue
            scanned += 1
            try:
                stat = path.stat()
            except OSError:
                continue
            total_bytes += stat.st_size
            age = now - stat.st_mtime
            oldest = age if oldest is None else max(oldest, age)
            name = path.name
            if name.startswith("final_"):
                counts["final"] += 1
            elif name.startswith("stage1_"):
                counts["stage1"] += 1
            elif name.startswith("qrlock_"):
                counts["qrlock"] += 1
            else:
                counts["other"] += 1
    except Exception as exc:
        return {"error": str(exc)[:200], "tmpPath": str(tmp_path)}

    result = {
        "tmpPath": str(tmp_path),
        "fileCounts": counts,
        "totalBytes": total_bytes,
        "oldestFileAgeSeconds": round(oldest, 1) if oldest is not None else None,
        "scannedFiles": scanned,
        "scanTruncated": truncated,
    }
    try:
        usage = shutil.disk_usage(tmp_path)
        result["diskFreeBytes"] = usage.free
        result["diskTotalBytes"] = usage.total
    except Exception:
        pass
    return result


def collect_config(settings) -> dict:
    """Explicit ALLOW-LIST of settings.

    Never dump os.environ, whole or filtered: an allow-list fails closed when a new
    secret env var is added, a deny-list fails open. Secret VALUES never appear —
    only a boolean and a non-invertible fingerprint.
    """
    from src.ghibli_portrait.services import seedream_service as ark

    return {
        "logLevel": os.getenv("LOG_LEVEL", "INFO").upper(),
        "domain": settings.DOMAIN,
        "ghibliModel": settings.GHIBLI_MODEL,
        "composeModel": settings.COMPOSE_MODEL,
        "arkApiUrl": ark.ARK_API_URL,
        "arkModel": ark.ARK_MODEL,
        "arkImageSize": ark.ARK_IMAGE_SIZE,
        "arkSeed": ark.ARK_SEED,
        "arkWatermark": ark.ARK_WATERMARK,
        "requireHumanFace": settings.REQUIRE_HUMAN_FACE,
        "enableIdentityCheck": settings.ENABLE_IDENTITY_CHECK,
        "clipConcurrencyLimit": settings.CLIP_CONCURRENCY_LIMIT,
        "downloadConcurrencyLimit": settings.DOWNLOAD_CONCURRENCY_LIMIT,
        "generationConcurrencyLimit": settings.GENERATION_CONCURRENCY_LIMIT,
        "saveOutputLocal": settings.SAVE_OUTPUT_LOCAL,
        "outputDir": str(settings.OUTPUT_DIR),
        "persistFinalImages": settings.PERSIST_FINAL_IMAGES,
        "stage1TtlHours": settings.STAGE1_TTL_HOURS,
        "qrlockTtlHours": settings.QRLOCK_TTL_HOURS,
        "finalImageTtlHours": settings.FINAL_IMAGE_TTL_HOURS,
        # Secrets: presence + fingerprint only, never the value.
        "arkApiKeyConfigured": bool(ark.ARK_API_KEY),
        "arkApiKeyFingerprint": _fingerprint(ark.ARK_API_KEY),
        "diagnosticsTokenConfigured": bool(os.getenv("DIAGNOSTICS_TOKEN", "")),
    }


__all__ = [
    "TrackedSemaphore",
    "collect_service",
    "collect_concurrency",
    "collect_pending_tasks",
    "collect_models",
    "collect_storage",
    "collect_config",
    "uptime_seconds",
]
