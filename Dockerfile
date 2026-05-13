# Build stage
FROM python:3.10-slim as builder

WORKDIR /tmp

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
RUN pip install --no-cache-dir uv

# Copy project files
COPY pyproject.toml pyproject.toml

# Create virtual environment and install dependencies
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv pip install -r pyproject.toml --no-cache


# Runtime stage
FROM python:3.10-slim

WORKDIR /app

# Install runtime dependencies (required for MediaPipe, OpenCV, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy application code
COPY . .

# Create temporary directory for static files
RUN mkdir -p src/ghibli_portrait/static/tmp

# Expose port
EXPOSE 8010

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8010/v1/health', timeout=5)" || exit 1

# Run the application
CMD ["uvicorn", "src.ghibli_portrait.main:app", "--host", "0.0.0.0", "--port", "8010"]
