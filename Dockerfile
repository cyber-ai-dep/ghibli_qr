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
# - libgomp1: Required by NumPy/MediaPipe for parallel processing
# - libgl1, libglib2.0-0, libgles2, libegl1: Required by MediaPipe
#   (GL context for face detection — missing libGLESv2.so.2 throws
#   "cannot open shared object file" at first MediaPipe call)
# - libzbar0: Required by pyzbar (fast-path QR decoder; without it
#   pyzbar silently fails and every QR check falls back to YOLO ~1-2s)
# These are stripped from build stage and added fresh for security
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    libgles2 \
    libegl1 \
    libzbar0 \
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

# Expose the API port
# Default port is 8010 (configurable via docker run -p)
EXPOSE 8010

# Configure health check for orchestration systems (Kubernetes, Docker Compose, Swarm)
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
# - uvicorn: ASGI server for FastAPI
# - --host 0.0.0.0: Listen on all network interfaces (required for Docker)
# - --port 8010: Default port (set in config.py, can override with env var)
# - --workers 1: CRITICAL — Must stay at 1 worker
#   Reason: the in-memory pending_tasks dict delivers each generation result to the
#   awaiting request in-process; multiple workers would not share it. Use
#   --workers N only after moving pending_tasks to a shared store (e.g. Redis).
CMD ["uvicorn", "src.ghibli_portrait.main:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]
