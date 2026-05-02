# ──────────────────────────────────────────────────────────────────────────────
# Autoflow – AWX production settings
# Mounted into the container at /etc/tower/settings.py
# AWX loads this file automatically in production mode.
# All secrets are injected at runtime via environment variables.
# ──────────────────────────────────────────────────────────────────────────────

import os

# ── Database ──────────────────────────────────────────────────────────────────
DATABASES = {
    'default': {
        'ATOMIC_REQUESTS': True,
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'awx'),
        'USER': os.environ.get('POSTGRES_USER', 'awx'),
        'PASSWORD': os.environ['POSTGRES_PASSWORD'],
        'HOST': os.environ.get('DATABASE_HOST', 'postgres'),
        'PORT': os.environ.get('DATABASE_PORT', '5432'),
    }
}

# Listener connections (pg_notify / long-lived).
LISTENER_DATABASES = {
    'default': {
        'OPTIONS': {
            'keepalives': 1,
            'keepalives_idle': 5,
            'keepalives_interval': 5,
            'keepalives_count': 5,
        },
    }
}

# ── Security ──────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ['AWX_SECRET_KEY']
ALLOWED_HOSTS = ['*']

# Traefik termine TLS et injecte X-Forwarded-Proto: https.
# nginx passe ce header via uwsgi_param HTTP_X_FORWARDED_PROTO.
# Django lit ce header grâce à SECURE_PROXY_SSL_HEADER et active les cookies Secure.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST    = True
CSRF_COOKIE_SECURE      = True
SESSION_COOKIE_SECURE   = True

# ── Redis ─────────────────────────────────────────────────────────────────────
_host     = os.environ.get('REDIS_HOST', 'redis')
_port     = os.environ.get('REDIS_PORT', '6379')
_password = os.environ.get('REDIS_PASSWORD', '')

# Build authenticated URL when a password is set.
if _password:
    _redis_base = f'redis://:{_password}@{_host}:{_port}'
else:
    _redis_base = f'redis://{_host}:{_port}'

BROKER_URL = _redis_base + '/0'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [BROKER_URL],
            'capacity': 10000,
            'group_expiry': 157784760,  # 5 years
        },
    }
}

CACHES = {
    'default': {
        'BACKEND': 'awx.main.cache.AWXRedisCache',
        'LOCATION': _redis_base + '/1',
    }
}

# ── Receptor ──────────────────────────────────────────────────────────────────
# Receptor socket is on a shared volume between the receptor and awx_task containers.
RECEPTOR_SOCKET_PATH = '/var/run/receptor/receptor.sock'

# ── HTTP mode overrides ────────────────────────────────────────────────────────
# AWX production defaults use HTTPS (port 443).
# TLS est terminé par Traefik — AWX reçoit du HTTP en interne, d'où les overrides ci-dessous.
# Les cookies Secure sont activés plus haut via SECURE_PROXY_SSL_HEADER.

# WebSocket relay (awx_task → awx_web): use HTTP port 80.
# Default is https/443 which breaks in docker-compose without TLS termination.
BROADCAST_WEBSOCKET_PROTOCOL    = 'http'
BROADCAST_WEBSOCKET_PORT        = 80
BROADCAST_WEBSOCKET_VERIFY_CERT = False

# Base URL used for email notifications and internal links.
TOWER_URL_BASE = 'http://awxweb'

# ── Branding / Login-Logout redirects ─────────────────────────────────────────
# Après logout, renvoyer vers / (notre page custom) au lieu de /api/
LOGOUT_REDIRECT_URL = '/'
LOGIN_URL = '/'

# ── Execution Environments — container runtime ────────────────────────────────
# In Docker Compose, jobs are executed via Docker (socket bind-mounted into
# awx_task). Podman is not available in this environment.
# Without this setting AWX 24+ defaults to podman and fails immediately.
CONTAINER_RUNTIME = 'docker'

# AWX defaults to --network slirp4netns:enable_ipv6=true (Podman rootless).
# Docker does not understand slirp4netns — use bridge instead.
# --add-host: /etc/hosts entries point to 127.0.0.1 which is meaningless inside
# a container; host-gateway resolves to the Docker bridge IP (172.17.0.1) so
# HTTPS requests reach Traefik running on the host.
DEFAULT_CONTAINER_RUN_OPTIONS = [
    '--network', 'bridge',
    '--add-host', f"git.{os.environ.get('DOMAIN', 'localhost')}:host-gateway",
    # Trust the internal PKI CA mounted via AWX_ISOLATION_SHOW_PATHS
    '--env', 'GIT_SSL_CAINFO=/etc/autoflow/ca.crt',
    '--env', 'SSL_CERT_FILE=/etc/autoflow/ca.crt',
]

# Working directory for per-job isolated environments.
AWX_ISOLATION_BASE_PATH = os.environ.get('AWX_ISOLATION_BASE_PATH', '/tmp')

# Extra host paths bind-mounted inside every EE container.
# IMPORTANT: these paths must exist on the HOST (not just inside a container),
# because the EE container is created by the host Docker daemon via socket.
# Named volumes (like awx_projects) cannot be listed here — use only host paths.
# ansible-runner copies project files to the private_data_dir (under /tmp)
# before launching the EE, so /var/lib/awx/projects is not needed here.
AWX_ISOLATION_SHOW_PATHS = [
    '/etc/resolv.conf:/etc/resolv.conf:ro',
    # Internal PKI CA cert — mounted so git/curl inside EE containers trust it
    f"{os.environ.get('TRAEFIK_CERTS_DIR', '/home/vagrant/autoflow/traefik/certs')}/ca.{os.environ.get('DOMAIN', 'localhost')}.crt:/etc/autoflow/ca.crt:ro",
]
