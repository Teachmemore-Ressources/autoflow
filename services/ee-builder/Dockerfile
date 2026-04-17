# EE Builder — Dockerfile
# Installs ansible-builder + Docker CLI on a Python 3.12 slim base.
# Requires Docker socket bind-mounted at runtime (/var/run/docker.sock).

FROM python:3.12-slim

# Install Docker CLI + build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg git && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg \
         -o /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) \
          signed-by=/etc/apt/keyrings/docker.asc] \
          https://download.docker.com/linux/debian \
          $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli && \
    rm -rf /var/lib/apt/lists/*

# Python deps
RUN pip install --no-cache-dir \
    ansible-builder \
    fastapi \
    "uvicorn[standard]" \
    prometheus-client

WORKDIR /app
COPY main.py .

EXPOSE 8003
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
