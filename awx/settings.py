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

# Disable Secure cookie flags when not running behind HTTPS.
# AWX production mode sets these to True by default; the browser silently
# drops Secure cookies on plain HTTP, making login always fail.
# Set to True (and configure SSL termination in nginx) for production HTTPS.
CSRF_COOKIE_SECURE    = False
SESSION_COOKIE_SECURE = False

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
# AWX production defaults use HTTPS (port 443). Override for plain HTTP.

# Cookies: disable Secure flag so browsers send them over HTTP.
CSRF_COOKIE_SECURE    = False
SESSION_COOKIE_SECURE = False

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
