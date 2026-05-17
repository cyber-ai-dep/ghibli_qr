"""
Concurrent load test for the Ghibli Portrait API.

Sends multiple requests simultaneously and reports timing + results.

Usage:
    uv run python test_concurrent.py                        # default: 3 requests
    uv run python test_concurrent.py --count 5             # 5 concurrent requests
    uv run python test_concurrent.py --count 10 --url http://localhost:8010
    uv run python test_concurrent.py --endpoint /v1/ghibli  # Stage 1 only (faster)
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
    "https://i.ibb.co/gL7nVMXZ/Screenshot-2025-04-24-200200.png",
    "https://i.ibb.co/jZ1GSyB3/seorang-wanita-dengan-jilbab-di-kepalanya-berpose-untuk-foto-1015384-76939.jpg",
    "https://i.ibb.co/LKtb0rG/mo.png",
    "https://i.ibb.co/7JDhFXF4/IMG-20260509-WA0006.jpg",
    "https://i.ibb.co/0yDJbSpg/Screenshot-2026-01-11-195950.png",
    "https://i.ibb.co/kgCnQg7S/Screenshot-2025-05-14-143517.png",
    "https://i.ibb.co/Y7HxnD2m/download.jpg",
    "https://i.ibb.co/67skZ5V8/download.jpg",
    "https://i.ibb.co/vx50VRrC/download.jpg",
    "https://i.ibb.co/CKX1Tq2N/images.jpg",
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

async def run_concurrent(base_url: str, endpoint: str, count: int, timeout: int):
    images = [TEST_IMAGES[i % len(TEST_IMAGES)] for i in range(count)]

    print(f"\n{'='*60}")
    print(f"  Ghibli API — Concurrent Load Test")
    print(f"{'='*60}")
    print(f"  Server   : {base_url}")
    print(f"  Endpoint : {endpoint}")
    print(f"  Requests : {count} concurrent")
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
    print(f"  Firing {count} requests simultaneously...\n")

    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [
            run_request(client, i, images[i], base_url, endpoint)
            for i in range(count)
        ]
        results: list[RequestResult] = await asyncio.gather(*tasks)

    wall_time = time.monotonic() - wall_start

    # ---------------------------------------------------------------------------
    # Print results
    # ---------------------------------------------------------------------------
    print(f"\n{'='*60}")
    print("  Results")
    print(f"{'='*60}")

    passed = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    for r in sorted(results, key=lambda x: x.index):
        status = "✓" if r.success else "✗"
        img_short = r.img_url.split("/")[-1][:30]
        if r.success:
            print(
                f"  {status} [{r.index+1:02d}] {img_short:<32} "
                f"{r.duration_s:6.1f}s  KIE:{r.cost_time}s  "
                f"→ {r.result_urls[0][:60] if r.result_urls else 'no URL'}..."
            )
        else:
            print(
                f"  {status} [{r.index+1:02d}] {img_short:<32} "
                f"{r.duration_s:6.1f}s  "
                f"[{r.error_code}] {r.error_msg[:60]}"
            )

    print(f"\n{'='*60}")
    print("  Summary")
    print(f"{'='*60}")
    print(f"  Total requests : {count}")
    print(f"  Passed         : {len(passed)}")
    print(f"  Failed         : {len(failed)}")
    print(f"  Wall time      : {wall_time:.1f}s  (all {count} ran in parallel)")

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

    return len(failed)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ghibli API concurrent load test")
    parser.add_argument(
        "--url",
        default="http://localhost:8010",
        help="Base server URL (default: http://localhost:8010)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Number of concurrent requests to send (default: 3)",
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
    args = parser.parse_args()

    failures = asyncio.run(
        run_concurrent(
            base_url=args.url.rstrip("/"),
            endpoint=args.endpoint,
            count=args.count,
            timeout=args.timeout,
        )
    )
    sys.exit(failures)


if __name__ == "__main__":
    main()
