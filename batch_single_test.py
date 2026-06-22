"""Batch test for the single-shot (ONE API call) Ghibli+QR generation.

Runs single_shot_ghibli_qr on several images, verifies each used exactly ONE
Seedream API call, saves every result, and checks QR scannability.
"""
import asyncio
import os
import time

import httpx
import numpy as np
from PIL import Image
from qreader import QReader

from src.ghibli_portrait.services import seedream_service as s

IMAGES = [
    "https://i.ibb.co/gL7nVMXZ/Screenshot-2025-04-24-200200.png",
    "https://i.ibb.co/kgCnQg7S/Screenshot-2025-05-14-143517.png",
    "https://i.ibb.co/Kj4G0WQx/d11c6e5f-c245-472d-a728-6c198ef4b1cb.jpg",
    "https://i.ibb.co/jZ1GSyB3/seorang-wanita-dengan-jilbab-di-kepalanya-berpose-untuk-foto-1015384-76939.jpg",
    "https://i.ibb.co/vx50VRrC/download.jpg",
]
QR_URL = "https://example.com/profile"
# Suffix for output filenames so new runs don't overwrite previous ones.
OUT_SUFFIX = os.getenv("OUT_SUFFIX", "")

# Count ONLY Seedream generation calls (wrap the real post so generation still happens).
ark_calls = []
_real_post = httpx.AsyncClient.post


async def _counting_post(self, url, **kwargs):
    if str(url) == s.ARK_API_URL:
        ark_calls.append(url)
    return await _real_post(self, url, **kwargs)


httpx.AsyncClient.post = _counting_post

_qreader = QReader(model_size="s")


def _scan(path):
    img = np.array(Image.open(path).convert("RGB"))
    res = _qreader.detect_and_decode(image=img)
    for r in res:
        if isinstance(r, str):
            return r
    return None


async def _process(i, img):
    t0 = time.perf_counter()
    try:
        url, _ = await s.single_shot_ghibli_qr(img, QR_URL)
        dest = f"output/batch_single_{i}{OUT_SUFFIX}.jpg"
        await s.download_to(url, dest)
        return {"i": i, "img": img, "secs": time.perf_counter() - t0,
                "dest": dest, "url": url, "ok": True}
    except Exception as e:
        return {"i": i, "img": img, "error": str(e), "ok": False}


async def main():
    t0 = time.perf_counter()
    # Run ALL images in parallel.
    results = await asyncio.gather(*(_process(i, img) for i, img in enumerate(IMAGES, 1)))
    total_wall = time.perf_counter() - t0

    for r in sorted(results, key=lambda x: x["i"]):
        print(f"--- IMAGE {r['i']} ---")
        print(f"  source     : {r['img']}")
        if not r["ok"]:
            print(f"  FAILED     : {r['error']}")
            continue
        print(f"  seconds    : {r['secs']:.1f}")
        print(f"  saved      : {r['dest']}")
        print(f"  qr_decoded : {_scan(r['dest'])}")
        print(f"  result_url : {r['url']}")

    print("=========================")
    print(f"WALL_CLOCK_SECONDS  = {total_wall:.1f}  (parallel)")
    print(f"TOTAL_IMAGES        = {len(IMAGES)}")
    print(f"TOTAL_SEEDREAM_CALLS= {len(ark_calls)}  (expected = {len(IMAGES)}, i.e. 1 per image)")


if __name__ == "__main__":
    asyncio.run(main())
