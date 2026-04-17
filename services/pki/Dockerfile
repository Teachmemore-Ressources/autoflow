FROM python:3.12-slim

LABEL org.opencontainers.image.title="Autoflow PKI Service"
LABEL org.opencontainers.image.description="Certificate Authority management with REST API and web UI"

# ── System deps ───────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── App user ──────────────────────────────────────────────────────────────────
RUN groupadd -r pki && useradd -r -g pki -d /app pki

# ── Python deps ───────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── App code ──────────────────────────────────────────────────────────────────
COPY app/ ./app/

# ── Data directory (will be a volume) ────────────────────────────────────────
RUN mkdir -p /data/pki && chown -R pki:pki /data /app

USER pki

EXPOSE 8004

HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8004/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004", "--workers", "1"]
