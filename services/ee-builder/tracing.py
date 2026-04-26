"""
OpenTelemetry distributed tracing setup.

Usage in main.py:
    from tracing import setup_tracing, instrument_app
    setup_tracing("autoflow-<service>")          # before app creation
    ...
    instrument_app(app, "autoflow-<service>")    # after app creation
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def setup_tracing(service_name: str) -> None:
    """
    Initialise le provider OTEL et instrumente httpx + logging.
    No-op si OTEL_EXPORTER_OTLP_ENDPOINT n'est pas défini.
    """
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {
                SERVICE_NAME: service_name,
                "service.version": os.getenv("SERVICE_VERSION", "2.0.0"),
                "deployment.environment": os.getenv("ENVIRONMENT", "production"),
            }
        )
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        # Propagate trace context through outgoing httpx calls
        HTTPXClientInstrumentor().instrument()

        # Inject trace_id / span_id into every log record
        LoggingInstrumentor().instrument(set_logging_format=True)

        logger.info(
            "OpenTelemetry tracing enabled — endpoint=%s service=%s",
            endpoint,
            service_name,
        )
    except ImportError as exc:
        logger.warning("opentelemetry packages unavailable — tracing disabled (%s)", exc)


def instrument_app(app, service_name: str) -> None:
    """
    Instrumente l'application FastAPI.
    Doit être appelé après la création de l'app et après setup_tracing().
    """
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint:
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="health,healthz,metrics",
            server_request_hook=_server_request_hook,
        )
    except ImportError:
        pass


def _server_request_hook(span, scope: dict) -> None:
    """Enrichit chaque span HTTP avec des attributs utiles."""
    if span and span.is_recording():
        headers = dict(scope.get("headers", []))
        user_agent = headers.get(b"user-agent", b"").decode("utf-8", errors="ignore")
        if user_agent:
            span.set_attribute("http.user_agent", user_agent)
