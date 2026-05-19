"""
Benchmark YOLO (qreader) on CPU only — simulates the Hostinger KVM 2 server
(2 shared cores, no GPU).

Run:
    python benchmark_yolo.py [path/to/image.png]

If no image is provided, a synthetic QR code is generated.
"""

import os
import sys
import time
import tracemalloc

# Force CPU — server has no GPU
os.environ["CUDA_VISIBLE_DEVICES"] = ""

import numpy as np
from PIL import Image

# ── optional: generate a synthetic QR if no image given ─────────────────────
def _make_test_image(path: str) -> np.ndarray:
    try:
        import qrcode
        qr = qrcode.make("https://example.com/test-qr-payload")
        qr = qr.convert("RGB")
        qr.save(path)
        return np.array(qr)
    except Exception:
        # plain white square fallback (no QR, just measures model overhead)
        img = Image.new("RGB", (512, 512), color=(255, 255, 255))
        img.save(path)
        return np.array(img)


# ── load image ───────────────────────────────────────────────────────────────
if len(sys.argv) > 1:
    img_path = sys.argv[1]
    img_np = np.array(Image.open(img_path).convert("RGB"))
    print(f"Using image: {img_path}  ({img_np.shape[1]}x{img_np.shape[0]} px)")
else:
    tmp = "/tmp/_bench_qr.png"
    img_np = _make_test_image(tmp)
    print(f"No image supplied — generated synthetic QR at {tmp}")

print()

# ── 1. MODEL LOAD TIME & RAM ─────────────────────────────────────────────────
print("=" * 55)
print("1. MODEL LOAD  (happens once per worker process)")
print("=" * 55)

tracemalloc.start()
t0 = time.perf_counter()

from qreader import QReader
qreader_l = QReader(model_size="l")

load_time = time.perf_counter() - t0
cur, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()

print(f"   model_size='l'  load time : {load_time:.2f}s")
print(f"   RAM after load  (current) : {cur / 1e6:.1f} MB")
print(f"   RAM after load  (peak)    : {peak / 1e6:.1f} MB")

# also load small for comparison
t0 = time.perf_counter()
qreader_s = QReader(model_size="s")
load_time_s = time.perf_counter() - t0
print()
print(f"   model_size='s'  load time : {load_time_s:.2f}s  (for comparison)")

# ── 2. INFERENCE TIME (LARGE MODEL) ─────────────────────────────────────────
print()
print("=" * 55)
print("2. INFERENCE  model_size='l'  (CPU, simulating server)")
print("=" * 55)

RUNS = 5
times_l = []
for i in range(RUNS):
    t0 = time.perf_counter()
    result = qreader_l.detect_and_decode(image=img_np, return_detections=True)
    elapsed = time.perf_counter() - t0
    times_l.append(elapsed)
    print(f"   run {i+1}: {elapsed:.3f}s  → result: {result}")

avg_l = sum(times_l) / len(times_l)
print(f"\n   avg over {RUNS} runs: {avg_l:.3f}s")

# ── 3. INFERENCE TIME (SMALL MODEL) ─────────────────────────────────────────
print()
print("=" * 55)
print("3. INFERENCE  model_size='s'  (CPU, for comparison)")
print("=" * 55)

times_s = []
for i in range(RUNS):
    t0 = time.perf_counter()
    result = qreader_s.detect_and_decode(image=img_np, return_detections=True)
    elapsed = time.perf_counter() - t0
    times_s.append(elapsed)
    print(f"   run {i+1}: {elapsed:.3f}s  → result: {result}")

avg_s = sum(times_s) / len(times_s)
print(f"\n   avg over {RUNS} runs: {avg_s:.3f}s")

# ── 4. SUMMARY ───────────────────────────────────────────────────────────────
print()
print("=" * 55)
print("SUMMARY")
print("=" * 55)
print(f"  Large model avg inference : {avg_l:.3f}s / request")
print(f"  Small model avg inference : {avg_s:.3f}s / request")
print(f"  Speed gain (s→l)          : {avg_l/avg_s:.1f}x faster with 's'")
print()
print("  Server context (2 shared CPU cores):")

def rps(avg):
    # with 2 cores and blocking inference, max throughput ≈ 2 / avg_time
    return 2 / avg

print(f"  Max req/s  large model : ~{rps(avg_l):.1f} req/s  ({rps(avg_l)*60:.0f}/min)")
print(f"  Max req/s  small model : ~{rps(avg_s):.1f} req/s  ({rps(avg_s)*60:.0f}/min)")
print()
print("  NOTE: These are CPU-only numbers (CUDA disabled).")
print("  Numbers on your local machine with GPU would be much faster.")
print("  The server will match these CPU numbers closely.")
print()
print("  Use a real merged output image for the most accurate result:")
print("  python benchmark_yolo.py path/to/your/merged_output.png")
