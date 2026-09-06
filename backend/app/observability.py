"""PII-safe tracing and bounded-cardinality operational metrics."""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

from arq.connections import ArqRedis
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from app.core.config import Settings

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
request_id: ContextVar[str] = ContextVar("request_id", default="")

REGISTRY = CollectorRegistry()
HTTP_REQUESTS = Counter(
    "novacart_http_requests_total",
    "HTTP requests",
    ["method", "route", "status_class"],
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "novacart_http_request_duration_seconds", "HTTP latency", ["method", "route"], registry=REGISTRY
)
AGENT_OUTCOMES = Counter(
    "novacart_agent_outcomes_total", "Agent outcomes", ["outcome", "locale"], registry=REGISTRY
)
INTENTS = Counter(
    "novacart_agent_intents_total", "Intent routing", ["intent", "locale"], registry=REGISTRY
)
TOOLS = Counter(
    "novacart_tool_executions_total", "Tool executions", ["tool", "outcome"], registry=REGISTRY
)
RAG_LATENCY = Histogram(
    "novacart_rag_duration_seconds", "Retrieval latency", ["outcome", "locale"], registry=REGISTRY
)
CITATIONS = Counter(
    "novacart_citation_validation_total",
    "Citation validation",
    ["outcome", "locale"],
    registry=REGISTRY,
)
PROVIDERS = Counter(
    "novacart_provider_requests_total",
    "Provider requests",
    ["provider", "operation", "outcome"],
    registry=REGISTRY,
)
PROVIDER_LATENCY = Histogram(
    "novacart_provider_request_duration_seconds",
    "Provider latency",
    ["provider", "operation"],
    registry=REGISTRY,
)
CONFIRMATIONS = Counter(
    "novacart_confirmations_total", "Confirmation funnel", ["action", "outcome"], registry=REGISTRY
)
LIFECYCLE = Counter(
    "novacart_lifecycle_events_total",
    "Operational lifecycle",
    ["domain", "event"],
    registry=REGISTRY,
)
WORKER_JOBS = Counter(
    "novacart_worker_jobs_total", "Worker jobs", ["job", "outcome"], registry=REGISTRY
)
QUEUE_DEPTH = Gauge(
    "novacart_worker_queue_depth", "Approximate worker queue depth", ["queue"], registry=REGISTRY
)
OPENAI_TOKENS = Counter(
    "novacart_openai_tokens_total",
    "Actual OpenAI token usage",
    ["model", "direction", "operation"],
    registry=REGISTRY,
)
OPENAI_COST = Counter(
    "novacart_openai_estimated_cost_usd_total",
    "Estimated OpenAI cost, not invoice data",
    ["model", "operation"],
    registry=REGISTRY,
)
RATE_LIMITS = Counter(
    "novacart_control_rejections_total",
    "Rate, concurrency, or budget rejections",
    ["control", "scope"],
    registry=REGISTRY,
)

_UUID = re.compile(r"(?i)[0-9a-f]{8}-[0-9a-f-]{27,}")
_INTEGER = re.compile(r"/\d+(?=/|$)")
_WEBHOOK_KEY = re.compile(r"^(/api/v1/webhooks/[^/]+)/[^/]+$")
ALLOWED_LOCALES = {"en", "fr", "other"}


def safe_route(path: str) -> str:
    """Normalize identifiers before they can become metric labels or span names."""
    normalized = _INTEGER.sub("/{id}", _UUID.sub("{id}", path))
    normalized = _WEBHOOK_KEY.sub(r"\1/{endpoint}", normalized)
    return normalized[:160]


def bounded(value: str, allowed: set[str], fallback: str = "other") -> str:
    return value if value in allowed else fallback


def configure_observability(settings: Settings) -> None:
    if not settings.otel_enabled or isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": settings.service_name, "deployment.environment": settings.app_env}
        )
    )
    endpoint = str(settings.otel_exporter_endpoint).rstrip("/") + "/v1/traces"
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)


def tracer() -> trace.Tracer:
    return trace.get_tracer("novacart", "1.0")


@contextmanager
def span(name: str, **safe_attributes: str | int | float | bool) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(name, attributes=safe_attributes) as current:
        yield current


@asynccontextmanager
async def timed_span(
    name: str, **safe_attributes: str | int | float | bool
) -> AsyncIterator[trace.Span]:
    with span(name, **safe_attributes) as current:
        yield current


def trace_id() -> str:
    value = trace.get_current_span().get_span_context().trace_id
    return f"{value:032x}" if value else ""


def metrics_payload() -> bytes:
    return generate_latest(REGISTRY)


async def observe_queue_depth(queue: ArqRedis) -> None:
    """Sample ARQ's bounded default queue without exposing job arguments or IDs."""
    try:
        QUEUE_DEPTH.labels("default").set(len(await queue.queued_jobs()))
    except Exception:
        # Telemetry must not make enqueueing or critical worker processing fail.
        return


def record_openai_usage(response: object, settings: Settings, operation: str) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    raw_input = getattr(usage, "input_tokens", 0)
    raw_output = getattr(usage, "output_tokens", 0)
    # Test doubles and older SDK responses may not expose numeric usage.
    if not isinstance(raw_input, int) or not isinstance(raw_output, int):
        return
    input_tokens = max(0, raw_input)
    output_tokens = max(0, raw_output)
    model = settings.agent_model if settings.agent_model in {"gpt-5-mini", "gpt-5"} else "other"
    operation = bounded(operation, {"triage", "answer", "summary", "lead"})
    OPENAI_TOKENS.labels(model, "input", operation).inc(input_tokens)
    OPENAI_TOKENS.labels(model, "output", operation).inc(output_tokens)
    estimate = input_tokens * settings.openai_input_cost_per_million_usd / 1_000_000
    estimate += output_tokens * settings.openai_output_cost_per_million_usd / 1_000_000
    OPENAI_COST.labels(model, operation).inc(estimate)


@dataclass(frozen=True)
class Timer:
    started: float

    @classmethod
    def start(cls) -> Timer:
        return cls(time.perf_counter())

    def seconds(self) -> float:
        return max(0.0, time.perf_counter() - self.started)


def new_request_id(candidate: str | None) -> str:
    if candidate and re.fullmatch(r"[A-Za-z0-9._-]{8,64}", candidate):
        return candidate
    return uuid4().hex


def job_carrier() -> dict[str, str]:
    """Serialize only W3C trace context and the safe generated correlation ID."""
    carrier: dict[str, str] = {}
    inject(carrier)
    carrier["x-correlation-id"] = correlation_id.get()
    return carrier


@contextmanager
def continued_job_context(carrier: dict[str, str] | None) -> Iterator[None]:
    if not carrier:
        yield
        return
    token = correlation_id.set(new_request_id(carrier.get("x-correlation-id")))
    with tracer().start_as_current_span("queue.receive", context=extract(carrier)):
        try:
            yield
        finally:
            correlation_id.reset(token)
