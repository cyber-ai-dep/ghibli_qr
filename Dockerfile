# ============================================================================
# Multi-stage Docker build for Ghibli Portrait API V1
# Stage 1: Build — Install dependencies in isolated environment
# Stage 2: Runtime — Minimal image with only production requirements
# ============================================================================

# ============================================================================
# STAGE 1: Builder — Install dependencies and create virtual environment
# ============================================================================
FROM python:3.10-slim as builder

WORKDIR /tmp

# Install build dependencies required for compiling Python packages
# These are only needed during build, not in final image
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv — fast Python package manager
# Faster than pip and more reliable dependency resolution
RUN pip install --no-cache-dir uv

# Copy project dependency file
COPY pyproject.toml pyproject.toml

# Create virtual environment and install all dependencies
# The venv is later copied to runtime stage — reduces final image size
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv pip install --all-extras -r pyproject.toml --no-cache


# ============================================================================
# STAGE 2: Runtime — Minimal production image
# ============================================================================
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install only runtime dependencies (no build tools)
# - libsm6, libxext6, libxrender-dev: Required by OpenCV (cv2)
# - libgl1, libglib2.0-0: Required by cv2 itself (qreader -> cv2 imports it
#   unconditionally at module load, even with the "headless"
#   opencv-python-headless wheel) — confirmed by a from-scratch build+run:
#   without both, the app crashes immediately on startup ("ImportError:
#   libGL.so.1" / "libgthread-2.0.so.0: cannot open shared object file"),
#   before uvicorn even finishes loading the app module. This is NOT
#   MediaPipe-related; do not remove these again for that reason.
# - libgomp1: Required by NumPy/torch for parallel processing
# - libzbar0: Required by pyzbar (fast-path QR decoder; without it
#   pyzbar silently fails and every QR check falls back to YOLO ~1-2s)
# - tini: correct PID 1 signal handling (SIGTERM -> graceful uvicorn shutdown)
#   and zombie reaping, baked into the image itself so it works under ANY
#   orchestrator (Kubernetes, plain `docker run`), not just Docker Compose's
#   own `init: true` (which only takes effect when launched via Compose).
# MediaPipe is no longer a dependency (Stage 1 validation runs on CLIP) — only
# its GLES/EGL packages (libgles2, libegl1) were actually MediaPipe-specific
# and are correctly dropped; libgl1/libglib2.0-0 stay because cv2 needs them
# independently of MediaPipe (see above).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libzbar0 \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Copy pre-built virtual environment from builder stage
# This avoids reinstalling 50+ dependencies in runtime stage
COPY --from=builder /opt/venv /opt/venv

# Set environment variables
# - PATH: Use venv Python first
# - PYTHONUNBUFFERED: Log output immediately (no buffering)
# - PYTHONDONTWRITEBYTECODE: Don't create .pyc files (saves space in container)
# - OMP/MKL_NUM_THREADS=1: pin OpenMP/MKL thread pools before the process starts,
#   so torch's per-inference thread count (see clip_validation_service._load_clip,
#   which also calls torch.set_num_threads(1)) can't oversubscribe the host's cores
#   under a burst of concurrent CLIP calls.
# - CLIP_CACHE_DIR: where CLIP weights are baked in below, and where the app
#   loads them from at runtime — must match on both sides.
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    CLIP_CACHE_DIR=/app/.cache/clip

# Build identity surfaced by GET /v1/diagnostics. .dockerignore excludes .git/ from
# the build context, so this ARG is the only way the running container can know
# which commit it is. Pass it in CI:
#   docker build --build-arg GIT_COMMIT=$(git rev-parse --short=12 HEAD) .
ARG GIT_COMMIT=""
ENV GIT_COMMIT=${GIT_COMMIT}

# Copy entire application code from host to container
COPY . .

# Create temporary directory for generated files (QR codes, rehosted images)
# This directory is writable at runtime and holds transient files
# On first startup, it will auto-create if it doesn't exist
RUN mkdir -p src/static/tmp

# Bake CLIP weights (~578MB) into the image at build time so no container ever
# downloads them at runtime — a cold runtime download can take seconds to tens
# of seconds depending on egress and would otherwise time out the first client
# request. Must run after COPY (needs the app's dependency versions resolved
# in the venv) and requires build-time network access to fetch the checkpoint.
RUN python -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-32-quickgelu', pretrained='openai', cache_dir='/app/.cache/clip')"

# Bake the QReader/qrdet YOLO weights the same way and for the same reason.
# qrdet's default weights_folder lives under site-packages (inside /opt/venv,
# which stays root-owned — only /app is chown'd below), and QReader() is
# instantiated at MODULE IMPORT TIME in qr_validation.py, not lazily. Without
# this bake, the appuser process crashes on startup with PermissionError
# trying to os.makedirs() that root-owned path (confirmed by a from-scratch
# build+run). Baking here downloads it once, as root, matching the exact
# model_size the app uses — at runtime the weights file + release marker
# already match, so qrdet's own code takes its "already downloaded" path and
# never attempts to write anywhere, regardless of the appuser/root ownership
# split. This must stay in sync with QR_MODEL_SIZE in qr_validation.py.
RUN python -c "from qreader import QReader; QReader(model_size='s')"

# Run as a non-root user — reduces blast radius if the process is ever
# compromised. Created after the CLIP/QReader bake steps (both need root to
# write into their respective cache paths) and chown'd so the app can still
# write to static/tmp/ and .cache.
#
# The UID/GID are pinned numerically and USER is set to the NUMBER, not the
# name, because Kubernetes requires it. When a Pod spec sets
# `securityContext.runAsNonRoot: true` — which k8s/deployment.yaml does, and
# which the "restricted" Pod Security Standard mandates cluster-wide — the
# kubelet must prove the image's user is non-root BEFORE starting it. It reads
# the User field from the image config and cannot resolve a name against the
# image's /etc/passwd, so `USER appuser` fails the check outright:
#   CreateContainerConfigError: container has runAsNonRoot and image has
#   non-numeric user (appuser), cannot verify user is non-root
# The Pod then never reaches Running and emits no application log to diagnose
# from. Docker/Compose resolve the name themselves, so this only ever surfaces
# on Kubernetes. 10001 must stay in sync with runAsUser/runAsGroup/fsGroup in
# k8s/deployment.yaml.
RUN groupadd -r -g 10001 appuser \
    && useradd -r -u 10001 -g appuser -d /app -s /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER 10001

# Expose the API port
# Default port is 8010 (configurable via docker run -p)
EXPOSE 8010

# Configure health check for Docker Compose / Swarm.
# NOTE: Kubernetes does NOT read this HEALTHCHECK instruction at all — the
# kubelet only uses liveness/readiness probes defined in the Pod spec itself.
# Whoever wires this image into Kubernetes needs exactly two facts, both
# already fixed by this image regardless of orchestrator:
#   - Port:        8010 (see EXPOSE above)
#   - Health path: GET /v1/health (returns 200 once the process is up;
#                  does not deep-check CLIP/model state)
# - Tests: GET /v1/health endpoint (liveness probe)
# - Interval: Check every 30 seconds
# - Timeout: Fail if response takes >10 seconds
# - Start grace: Wait 60 seconds after container start before first check
#   (covers CLIP preload at startup — import + model build, weights already baked in)
# - Retries: Mark unhealthy after 3 consecutive failures
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8010/v1/health', timeout=5)" || exit 1

# ============================================================================
# Startup command — Run the FastAPI application
# ============================================================================
# tini as PID 1 (see the tini apt-get install above): forwards SIGTERM to
# uvicorn for graceful shutdown and reaps zombie processes, regardless of
# whether the container is launched via Compose, `docker run`, or Kubernetes.
ENTRYPOINT ["/usr/bin/tini", "--"]
# - uvicorn: ASGI server for FastAPI
# - --host 0.0.0.0: Listen on all network interfaces (required for Docker)
# - --port 8010: Default port (set in config.py, can override with env var)
# - --workers 1: CRITICAL — Must stay at 1 worker
#   Reason: the in-memory pending_tasks dict delivers each generation result to the
#   awaiting request in-process; multiple workers would not share it. Use
#   --workers N only after moving pending_tasks to a shared store (e.g. Redis).
CMD ["uvicorn", "src.ghibli_portrait.main:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]
