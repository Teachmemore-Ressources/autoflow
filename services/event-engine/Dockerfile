FROM python:3.12-slim

# Security: run as non-root (matches project convention)
RUN groupadd -r autoflow && useradd -r -g autoflow autoflow

WORKDIR /app

# Install dependencies before copying source (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R autoflow:autoflow /app

USER autoflow

EXPOSE 8001

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
