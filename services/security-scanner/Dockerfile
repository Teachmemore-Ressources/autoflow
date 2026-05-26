# ── Stage 1: extract Trivy binary from official image ────────────────────────
# This avoids any HTTP download during build and uses the official Aqua image.
# Pin the tag via build arg to keep builds reproducible.
ARG TRIVY_VERSION=0.63.0
FROM aquasec/trivy:${TRIVY_VERSION} AS trivy-bin

# ── Stage 2: final image ──────────────────────────────────────────────────────
FROM python:3.12-slim

# Security: run as non-root (matches project convention)
RUN groupadd -r autoflow && useradd -r -g autoflow autoflow

WORKDIR /app

# Copy Trivy binary from stage 1
COPY --from=trivy-bin /usr/local/bin/trivy /usr/local/bin/trivy

# Pre-create Trivy DB cache directory with correct ownership.
# The named Docker volume is mounted here at runtime.
RUN mkdir -p /home/autoflow/.cache/trivy \
    && chown -R autoflow:autoflow /home/autoflow/.cache

# ── Python dependencies ───────────────────────────────────────────────────────
# Build context is ./services — requirements.txt lives in the security-scanner/ sub-directory.
COPY security-scanner/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared modules (security_headers, etc.) first so service code can import them.
COPY shared/ .

# Copy service source files.
COPY security-scanner/ .

RUN chown -R autoflow:autoflow /app

USER autoflow

EXPOSE 8002

# start_period is 60s to allow Trivy DB download on first boot before
# health checks start evaluating.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--proxy-headers", "--forwarded-allow-ips=*"]
