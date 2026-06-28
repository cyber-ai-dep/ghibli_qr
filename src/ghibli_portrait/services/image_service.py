"""
Image generation layer — BytePlus ARK (Seedream) backend.

This module is a DROP-IN replacement for the previous KIE integration. It keeps
the exact same public contract that the orchestration layer (routes.py) relies on:

    generate_img(...) -> dict   # returns {"code": 200, "data": {"taskId": ...}}
                                #   or  {"code": <non-200>, "msg": ...} on error

The KIE backend was asynchronous (create task -> wait for a webhook callback).
BytePlus ARK is SYNCHRONOUS — the generated image URL comes back inline. To avoid
changing any routes.py logic, the webhook contract is preserved internally:

  1. generate_img() calls ARK synchronously and gets the result URL.
  2. It builds a `CallbackRequest` (the SAME object the webhook used to deliver).
  3. It schedules `_deliver()` which resolves the matching `pending_tasks` Future
     exactly like the webhook did — so `routes.py` keeps awaiting the Future and
     never knows the backend changed.

ngrok is no longer required: input/intermediate images are inlined as base64 data
URIs before being sent to ARK (so ARK never has to fetch them from this server),
and the completion is delivered in-process (no public callback URL needed).
DOMAIN is still used only to build the local result URLs returned to the client.
"""

import asyncio
import base64
import io
import json
import logging
import time
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image as _PILImage

from src.ghibli_portrait.config import Settings
from src.ghibli_portrait.models.schemas import (
    AspectRatio,
    Quality,
    ImgURLs,
    CallbackRequest,
    TaskData,
    TaskState,
)
# Reuse the tested BytePlus ARK call + URL extractor.
from src.ghibli_portrait.services.seedream_service import (
    ARK_IMAGE_SIZE,
    ARK_MODEL,
    ARK_SEED,
    ARK_WATERMARK,
    _first_url,
    seedream_generate,
)

_log = logging.getLogger(__name__)
_settings = Settings()

# Downscale references before base64 to keep ARK request payloads small.
_MAX_REF_SIDE = 1024

# Default User-Agent for fetching external input images.
_DOWNLOAD_HEADERS = {"User-Agent": "ghibli-qr/0.1"}


# ============================================================================
# IMAGE INLINING — turn any reference into a base64 data URI for ARK
# (so ARK never needs to fetch from this server → no public DOMAIN/ngrok needed)
# ============================================================================
def _img_bytes_to_data_uri(raw: bytes) -> str:
    """Decode -> downscale -> re-encode JPEG -> base64 data URI."""
    img = _PILImage.open(io.BytesIO(raw)).convert("RGB")
    if max(img.size) > _MAX_REF_SIDE:
        img.thumbnail((_MAX_REF_SIDE, _MAX_REF_SIDE), _PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


def _file_to_data_uri(path: Path) -> str:
    return _img_bytes_to_data_uri(path.read_bytes())


def _local_tmp_path(ref: str) -> "Path | None":
    """If `ref` points at this server's /tmp mount, return the on-disk file path.

    Lets us read intermediate images (stage1_*, qrlock_*) straight from disk
    instead of fetching them over the network — works even with no public DOMAIN.
    """
    if "/tmp/" in ref:
        name = ref.rsplit("/tmp/", 1)[1].split("?")[0].split("#")[0]
        candidate = _settings.TMP_PATH / name
        if candidate.is_file():
            return candidate
    return None


async def _inline_ref(ref: str) -> str:
    """Return a base64 data URI for an image reference (URL / local path / data URI)."""
    if not isinstance(ref, str) or ref.startswith("data:"):
        return ref

    # Our own /tmp file → read from disk (no network).
    local = _local_tmp_path(ref)
    if local is not None:
        return await asyncio.to_thread(_file_to_data_uri, local)

    # External URL → download once, then inline.
    if ref.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(ref, headers=_DOWNLOAD_HEADERS)
            resp.raise_for_status()
        content = resp.content
        return await asyncio.to_thread(_img_bytes_to_data_uri, content)

    # Local filesystem path.
    return await asyncio.to_thread(_file_to_data_uri, Path(ref))


# ============================================================================
# WEBHOOK-CONTRACT DELIVERY — resolve the pending Future like the old callback
# ============================================================================
def _build_success_callback(task_id: str, url: str, cost_time: int, model: str) -> CallbackRequest:
    """Build the SAME CallbackRequest object the KIE webhook used to deliver."""
    return CallbackRequest(
        code=200,
        msg="ok",
        data=TaskData(
            taskId=task_id,
            state=TaskState.SUCCESS,
            costTime=cost_time,
            model=model or "",
            # get_result_urls() parses this exact shape ({"resultUrls": [...]}).
            resultJson=json.dumps({"resultUrls": [url]}),
        ),
    )


async def _deliver(task_id: str, callback: CallbackRequest) -> None:
    """Resolve the routes.pending_tasks Future for this task — mirrors the webhook.

    Late import avoids a circular import at module load (routes imports this module).
    Polls because routes creates the Future just after generate_img() returns.
    """
    from src.ghibli_portrait.api import routes

    for _ in range(200):  # up to ~10s
        future = routes.pending_tasks.get(task_id)
        if future is not None:
            if not future.done():
                future.set_result(callback)
            routes.pending_tasks.pop(task_id, None)
            return
        await asyncio.sleep(0.05)
    _log.error("[ARK] delivery gave up — taskId %s was never registered", task_id)


# ============================================================================
# PUBLIC API — same signature & return contract as the old KIE generate_img
# ============================================================================
async def generate_img(
    img_urls: ImgURLs,
    prompt: str,
    aspect_ratio: AspectRatio = AspectRatio._1_1,
    quality: Quality = Quality.BASIC,
    model: str | None = None,
    negative_prompt: str | None = None,
) -> dict:
    """Generate via BytePlus ARK, returning the KIE-style submission envelope.

    On success returns {"code": 200, "data": {"taskId": ...}} and schedules
    in-process delivery of the result to the awaiting pending_tasks Future.
    On error returns {"code": 501, "msg": ...} (same shape routes.py already handles).
    """
    try:
        refs = img_urls if isinstance(img_urls, list) else [img_urls]
        if not refs:
            return {"code": 501, "msg": "No input image provided"}

        # Inline all references so ARK does not need to fetch from this server.
        inlined = [await _inline_ref(r) for r in refs]

        # The model label from the caller (GHIBLI_MODEL / COMPOSE_MODEL) is the real
        # model used for generation AND reported back in the response.
        chosen_model = model or ARK_MODEL

        t0 = time.monotonic()
        ark_result = await seedream_generate(
            prompt,
            images=inlined,
            model=chosen_model,
            size=ARK_IMAGE_SIZE,
            watermark=ARK_WATERMARK,
            negative_prompt=negative_prompt,
            seed=ARK_SEED,
        )
        cost_time = int(time.monotonic() - t0)

        url = _first_url(ark_result)
        if not url:
            _log.error("[ARK] no image URL in response: %s", ark_result)
            return {"code": 501, "msg": f"ARK returned no image URL: {ark_result}"}

        task_id = uuid4().hex
        callback = _build_success_callback(task_id, url, cost_time, chosen_model)
        asyncio.create_task(_deliver(task_id, callback))

        _log.info("[ARK] task %s done in %ss -> %s", task_id, cost_time, url[:60])
        return {"code": 200, "data": {"taskId": task_id}}

    except Exception as e:
        _log.exception("[ARK] generate_img failed")
        return {"code": 501, "msg": f"ARK error: {e}"}
