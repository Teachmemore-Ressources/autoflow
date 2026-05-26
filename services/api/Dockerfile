FROM python:3.12-slim

# Security: run as non-root
RUN groupadd -r autoflow && useradd -r -g autoflow autoflow

WORKDIR /app

# Install dependencies before copying source (layer cache).
# Build context is ./services — requirements.txt lives in the api/ sub-directory.
COPY api/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared modules (security_headers, etc.) first so service code can import them.
COPY shared/ .

# Copy service source files.
COPY api/ .

RUN chown -R autoflow:autoflow /app

USER autoflow

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
