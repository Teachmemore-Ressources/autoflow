# Autoflow — shared/security_headers — Apache 2.0
"""
Shared Security Headers Middleware
===================================
A single BaseHTTPMiddleware that injects the recommended HTTP security headers
on every response from any Autoflow FastAPI service.

Usage
-----
In each service's main.py::

    from security_headers import SecurityHeadersMiddleware

    if settings.security_headers_enabled:
        app.add_middleware(SecurityHeadersMiddleware)

Headers applied
---------------
- X-Content-Type-Options      : prevents MIME-type sniffing
- X-Frame-Options             : blocks clickjacking (DENY)
- X-XSS-Protection            : legacy XSS filter hint for older browsers
- Referrer-Policy             : strict-origin-when-cross-origin
- Permissions-Policy          : disables unused browser features
- Content-Security-Policy     : default-src 'self'; frame-ancestors 'none'
- Strict-Transport-Security   : HSTS (only injected on HTTPS requests)
- Cache-Control: no-store     : on /auth/* routes only (prevents caching tokens)

Excluded from Cache-Control
---------------------------
Routes /health, /metrics, /docs, /redoc and /openapi.json are public operational
endpoints that must remain cacheable by proxies and monitoring tools.
"""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_log = logging.getLogger("autoflow.security")


def parse_cors_origins(
    cors_raw: str,
    env: str,
    log: logging.Logger | None = None,
) -> list[str]:
    """
    Parse and validate a CORS origins configuration string.

    Parameters
    ----------
    cors_raw:
        Raw value of the CORS_ORIGINS environment variable (already stripped).
    env:
        Current runtime environment (``"development"`` or ``"production"``).
    log:
        Logger to use for warnings. Defaults to the module-level logger.

    Returns
    -------
    list[str]
        Parsed list of allowed origins (may be empty).

    Raises
    ------
    RuntimeError
        If ``cors_raw == "*"`` and ``env == "production"``.
        This is a deliberate startup crash-guard to prevent wildcard CORS
        in production deployments.
    """
    logger = log or _log

    if cors_raw == "*":
        logger.warning(
            "CORS_ORIGINS=* detected — allowing all origins is dangerous in "
            "production. Restrict it to your frontend domain(s) via "
            "CORS_ORIGINS in .env."
        )
        if env.lower() == "production":
            raise RuntimeError(
                "CORS_ORIGINS=* is not allowed when ENV=production. "
                "Set CORS_ORIGINS to your actual frontend origin(s) in .env."
            )

    if not cors_raw:
        logger.warning(
            "CORS not configured — no origin will be allowed. "
            "Set CORS_ORIGINS=https://your-frontend.example.com in .env "
            "if your frontend and API are on different origins."
        )

    return [o.strip() for o in cors_raw.split(",") if o.strip()]

# Routes that must NOT receive Cache-Control: no-store
# (public / monitoring / docs — not sensitive)
_NO_CACHE_SKIP_PREFIXES: tuple[str, ...] = (
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
)

# Routes that DO receive Cache-Control: no-store
# (authentication and any route that may return tokens / credentials)
_AUTH_PREFIXES: tuple[str, ...] = ("/auth",)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Injects HTTP security headers on every response.

    HSTS is only set when the request arrived over HTTPS (i.e. when
    Traefik forwards X-Forwarded-Proto: https — FastAPI sets
    request.url.scheme accordingly thanks to --proxy-headers).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        path = request.url.path

        # ── Standard hardening headers (always) ──────────────────────────────
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'"
        )

        # ── HSTS — only over HTTPS ────────────────────────────────────────────
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        # ── Cache-Control: no-store — auth routes only ────────────────────────
        is_auth_route = any(path.startswith(p) for p in _AUTH_PREFIXES)
        is_public_route = any(path.startswith(p) for p in _NO_CACHE_SKIP_PREFIXES)

        if is_auth_route and not is_public_route:
            response.headers["Cache-Control"] = "no-store"

        return response
