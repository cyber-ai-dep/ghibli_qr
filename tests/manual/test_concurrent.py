"""
Concurrent load test for the Ghibli Portrait API.

Sends multiple requests simultaneously and reports timing + results.

Usage:
    uv run python test_concurrent.py                        # fires ALL test images at once
    uv run python test_concurrent.py --count 3              # only the first 3 images
    uv run python test_concurrent.py --endpoint /v1/ghibli  # Stage 1 only (faster)
Results stream live as each request finishes.
"""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Test images — loaded from test_links.txt (one URL per line, blank lines ignored)
# ---------------------------------------------------------------------------
TEST_IMAGES = [
    # --- Real portraits (should PASS) ---
    "https://i.ibb.co/4ZHjcZLp/istockphoto-944986244-612x612.jpg",  # 01
    "https://i.ibb.co/twRyFjVx/van.jpg",                              # 02
    "https://i.ibb.co/LhC1PGpM/istockphoto-1784543440-612x612.jpg",  # 03
    "https://i.ibb.co/rGt7L47C/37403.jpg",                           # 04
    "https://i.ibb.co/mCHT1Rb6/whiteman1.jpg",                       # 05
    "https://i.ibb.co/2pmYnzb/whiteman2.jpg",                        # 06
    "https://i.ibb.co/Xkrn6s7f/whiteman3.jpg",                       # 07
    "https://i.ibb.co/DHZN0Q5t/whitewoman1.jpg",                     # 08
    "https://i.ibb.co/RkDPv6jf/whitewoman2.jpg",                     # 09
    "https://i.ibb.co/S4XcXmcn/whitewoman3.png",                     # 10
    "https://i.ibb.co/cKM1stSZ/blackwoman1.jpg",                     # 11
    "https://i.ibb.co/BKSG8VGc/blackwoman2.jpg",                     # 12
    "https://i.ibb.co/LXzSHJnd/blackwoman3.jpg",                     # 13
    "https://i.ibb.co/LXzSHJnd/blackwoman3.jpg",                     # 14 (duplicate — stress test)
    "https://i.ibb.co/CKmf6hn7/hijabigirl3.jpg",                     # 15
    "https://i.ibb.co/VcCbvTZb/hijabigirl4.jpg",                     # 16
    "https://i.ibb.co/xK4MvVPd/hijabigirl5.jpg",                     # 17
    "https://i.ibb.co/VcjRV3Rm/hijabigirl6.jpg",                     # 18
    # --- Synthetic / should be REJECTED (NOT_REAL_PHOTO) ---
    "https://i.ibb.co/b5nGjTHh/minecraft1.jpg",                      # 19
    "https://i.ibb.co/zT7j7wM6/minecraft4.jpg",                      # 20
    "https://i.ibb.co/zT7j7wM6/minecraft4.jpg",                      # 21 (duplicate — stress test)
    # --- Single real person (should PASS) ---
    "https://i.ibb.co/vxDLh4RS/selfiman.jpg",                        # 22
    # --- Multiple faces — should be REJECTED (MULTIPLE_FACES) ---
    "https://i.ibb.co/7tXRgDyH/threemens.jpg",                       # 23
    "https://i.ibb.co/9HRCFNwq/threewoman.jpg",                      # 24
    "https://i.ibb.co/tyDJhqH/threegirls.jpg",                       # 25
    # --- Night / masked — outcome depends on face detection ---
    "https://i.ibb.co/LX5J3gCy/nghtman.jpg",                         # 26
    "https://i.ibb.co/r2F26p5C/maskman.jpg",                         # 27
    "https://i.ibb.co/gbsLPTKG/nightman.jpg",                        # 28
    "https://i.ibb.co/kgbXdcdQ/maskman2.jpg",                        # 29
    "https://i.ibb.co/Fb34x2nd/nightman2.jpg",                       # 30
    "https://i.ibb.co/ywq4wSz/nightman3.jpg",                        # 31
    # --- Children / babies (should PASS after threshold fix) ---
    "https://i.ibb.co/ym8RdLzS/aboy.jpg",                            # 32
    "https://i.ibb.co/gsSL1JY/babyboy.jpg",                          # 33
    "https://i.ibb.co/v6XFM1zN/babyboy2.jpg",                        # 34
    "https://i.ibb.co/jP2dkmzq/babygirl.jpg",                        # 35
    "https://i.ibb.co/VWdY2g36/babygirl2.jpg",                       # 36
    "https://i.ibb.co/1JJ4Z8mP/babygirl3.jpg",                       # 37
]

TEST_QR_URL = "https://example.com/profile"


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class RequestResult:
    index: int
    img_url: str
    success: bool
    status_code: int = 0
    duration_s: float = 0.0
    result_urls: list = field(default_factory=list)
    error_code: Optional[str] = None
    error_msg: Optional[str] = None
    cost_time: int = 0


# ---------------------------------------------------------------------------
# Single request coroutine
# ---------------------------------------------------------------------------

async def run_request(
    client: httpx.AsyncClient,
    index: int,
    img_url: str,
    base_url: str,
    endpoint: str,
) -> RequestResult:
    start = time.monotonic()

    if endpoint == "/v1/ghibli-qr":
        payload = {"imgUrl": img_url, "url": TEST_QR_URL}
    else:
        # /v1/ghibli — Stage 1 only
        payload = {"imgUrls": [img_url]}

    print(f"  [{index+1}] → sent")

    try:
        resp = await client.post(f"{base_url}{endpoint}", json=payload)
        duration = time.monotonic() - start
        body = resp.json()

        if resp.status_code == 200 and body.get("success"):
            data = body.get("data", {})
            result_urls = data.get("resultUrls", [])
            return RequestResult(
                index=index,
                img_url=img_url,
                success=True,
                status_code=resp.status_code,
                duration_s=duration,
                result_urls=result_urls,
                cost_time=data.get("costTime", 0),
            )
        else:
            errors = body.get("errors") or []
            first_err = errors[0] if errors else {}
            return RequestResult(
                index=index,
                img_url=img_url,
                success=False,
                status_code=resp.status_code,
                duration_s=duration,
                error_code=first_err.get("code", "UNKNOWN"),
                error_msg=first_err.get("message", body.get("message", "unknown error")),
            )

    except httpx.TimeoutException:
        duration = time.monotonic() - start
        return RequestResult(
            index=index,
            img_url=img_url,
            success=False,
            duration_s=duration,
            error_code="CLIENT_TIMEOUT",
            error_msg=f"Client-side timeout after {duration:.0f}s",
        )
    except Exception as exc:
        duration = time.monotonic() - start
        return RequestResult(
            index=index,
            img_url=img_url,
            success=False,
            duration_s=duration,
            error_code="CLIENT_ERROR",
            error_msg=str(exc),
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_concurrent(base_url: str, endpoint: str, timeout: int, count: int = None,
                         save_path: str = None):
    # Fire one request per test image, all at once. `count` limits to the first N.
    images = list(TEST_IMAGES)
    if count is not None:
        images = images[:count]
    n = len(images)

    print(f"\n{'='*60}")
    print(f"  Ghibli API — Concurrent Load Test")
    print(f"{'='*60}")
    print(f"  Server   : {base_url}")
    print(f"  Endpoint : {endpoint}")
    print(f"  Requests : {n} concurrent")
    print(f"  Timeout  : {timeout}s per request")
    print(f"{'='*60}\n")

    # Verify server is reachable before starting
    try:
        async with httpx.AsyncClient(timeout=5) as probe:
            r = await probe.get(f"{base_url}/v1/health")
            r.raise_for_status()
        print("  ✓ Server is healthy — starting test\n")
    except Exception as e:
        print(f"  ✗ Server not reachable: {e}")
        print(f"    Make sure the server is running at {base_url}")
        sys.exit(1)

    wall_start = time.monotonic()
    print(f"  Firing {n} requests simultaneously (results stream as they finish)...\n")

    results: list[RequestResult] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [
            asyncio.ensure_future(run_request(client, i, images[i], base_url, endpoint))
            for i in range(n)
        ]
        # Print each result the moment it completes — live feedback, no silent wait.
        for fut in asyncio.as_completed(tasks):
            r = await fut
            results.append(r)
            done = len(results)
            status = "✓" if r.success else "✗"
            img_short = r.img_url.split("/")[-1][:30]
            if r.success:
                print(
                    f"  {status} ({done:02d}/{n}) [{r.index+1:02d}] {img_short:<32} "
                    f"{r.duration_s:6.1f}s  ARK:{r.cost_time}s  "
                    f"→ {r.result_urls[0][:55] if r.result_urls else 'no URL'}"
                )
            else:
                print(
                    f"  {status} ({done:02d}/{n}) [{r.index+1:02d}] {img_short:<32} "
                    f"{r.duration_s:6.1f}s  [{r.error_code}] {r.error_msg[:55]}"
                )

    wall_time = time.monotonic() - wall_start

    passed = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    print(f"\n{'='*60}")
    print("  Summary")
    print(f"{'='*60}")
    print(f"  Total requests : {n}")
    print(f"  Passed         : {len(passed)}")
    print(f"  Failed         : {len(failed)}")
    print(f"  Wall time      : {wall_time:.1f}s  (all {n} ran in parallel)")

    if passed:
        avg = sum(r.duration_s for r in passed) / len(passed)
        fastest = min(passed, key=lambda r: r.duration_s)
        slowest = max(passed, key=lambda r: r.duration_s)
        print(f"  Avg duration   : {avg:.1f}s")
        print(f"  Fastest        : {fastest.duration_s:.1f}s  [req {fastest.index+1}]")
        print(f"  Slowest        : {slowest.duration_s:.1f}s  [req {slowest.index+1}]")

    if failed:
        print(f"\n  Failed requests:")
        for r in failed:
            print(f"    [{r.index+1}] {r.error_code}: {r.error_msg}")

    print(f"{'='*60}\n")

    # Full machine-readable results. The live stream above truncates result URLs
    # to 55 chars for readability, so without this there is no way to recover the
    # complete URLs (or build before/after reports) from a finished run.
    if save_path:
        payload = {
            "baseUrl": base_url,
            "endpoint": endpoint,
            "requests": n,
            "wallTimeS": round(wall_time, 1),
            "passed": len(passed),
            "failed": len(failed),
            "results": [
                {
                    "index": r.index + 1,
                    "imgUrl": r.img_url,
                    "success": r.success,
                    "statusCode": r.status_code,
                    "durationS": round(r.duration_s, 1),
                    "arkCostTimeS": r.cost_time,
                    "resultUrls": r.result_urls,
                    "errorCode": r.error_code,
                    "errorMsg": r.error_msg,
                }
                for r in sorted(results, key=lambda r: r.index)
            ],
        }
        with open(save_path, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"  Full results saved to {save_path}\n")

    return len(failed)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ghibli API concurrent load test")
    parser.add_argument(
        "--url",
        default="http://localhost:30820",
        help="Base server URL (default: http://localhost:30820)",
    )
    parser.add_argument(
        "--endpoint",
        default="/v1/ghibli-qr",
        choices=["/v1/ghibli-qr", "/v1/ghibli"],
        help="Endpoint to test (default: /v1/ghibli-qr — full pipeline)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=700,
        help="Per-request timeout in seconds (default: 700)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Limit to the first N test images (default: all)",
    )
    parser.add_argument(
        "--save",
        default=None,
        help="Write full untruncated results (JSON) to this path",
    )
    args = parser.parse_args()

    failures = asyncio.run(
        run_concurrent(
            base_url=args.url.rstrip("/"),
            endpoint=args.endpoint,
            timeout=args.timeout,
            count=args.count,
            save_path=args.save,
        )
    )
    sys.exit(failures)


if __name__ == "__main__":
    main()
