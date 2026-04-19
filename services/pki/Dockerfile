FROM python:3.12-slim AS builder

LABEL org.opencontainers.image.title="Autoflow PKI Service"
LABEL org.opencontainers.image.description="Certificate Authority management — hardened v2.0"

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r pki && useradd -r -g pki -d /app -s /sbin/nologin pki

COPY --from=builder /install /usr/local
COPY --chown=pki:pki app/ /app/app/

RUN install -d -m 750 -o pki -g pki /data/pki /data/pki/ca /data/pki/certs /data/pki/crl

USER pki
WORKDIR /app

ENV PKI_DATA_DIR=/data/pki \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8004

HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8004/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004", "--workers", "2", "--proxy-headers"]
