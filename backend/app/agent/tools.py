import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.state import CitationRef, RiskLevel, SanitizedResult
from app.core.config import Settings
from app.knowledge.retrieval import retrieve_passages
from app.providers.models import ProviderContext
from app.providers.ports import CommerceProviderV1, CrmProviderV1


class Permission(str, Enum):
    PUBLIC_KNOWLEDGE = "public_knowledge"
    CUSTOMER_READ = "customer_read"
    CUSTOMER_WRITE = "customer_write"
    SENSITIVE_WRITE = "sensitive_write"
    ESCALATE = "escalate"


class KnowledgeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=1000)
    locale: str = Field(default="en", pattern=r"^(en|fr)$")
    limit: int = Field(default=5, ge=1, le=10)


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_ref: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeferredWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_summary: str = Field(min_length=1, max_length=500)


class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict[str, object] = Field(default_factory=dict)
    citations: list[CitationRef] = Field(default_factory=list)


InputT = TypeVar("InputT", bound=BaseModel)
ToolHandler = Callable[[BaseModel, "ToolContext"], Awaitable[ToolOutput]]


@dataclass(frozen=True)
class ToolDefinition(Generic[InputT]):
    name: str
    input_schema: type[InputT]
    output_schema: type[ToolOutput]
    risk: RiskLevel
    required_permission: Permission
    timeout_seconds: float
    idempotency_required: bool
    confirmation_required: bool
    handler: ToolHandler | None


@dataclass
class ToolContext:
    session: AsyncSession
    settings: Settings
    organization_id: UUID
    actor_ref: str
    customer_ref: str
    correlation_id: str
    commerce: CommerceProviderV1
    crm: CrmProviderV1
    permissions: frozenset[Permission]

    def provider_context(self) -> ProviderContext:
        return ProviderContext(
            organization_id=self.organization_id,
            actor_ref=self.actor_ref,
            customer_ref=self.customer_ref,
            correlation_id=self.correlation_id,
        )


async def _knowledge(value: BaseModel, context: ToolContext) -> ToolOutput:
    request = KnowledgeInput.model_validate(value)
    passages = await retrieve_passages(
        context.session,
        context.settings,
        context.organization_id,
        request.query,
        language=request.locale,
        top_k=request.limit,
    )
    citations = [
        CitationRef(
            receipt_id=str(item.citation.record_id),
            title=item.citation.source_title,
            snippet=item.citation.snippet[:500],
        )
        for item in passages
    ]
    return ToolOutput(
        data={"passages": [item.text[:1000] for item in passages]}, citations=citations
    )


async def _order(value: BaseModel, context: ToolContext) -> ToolOutput:
    request = OrderInput.model_validate(value)
    order = await context.commerce.get_order(context.provider_context(), request.order_ref)
    return ToolOutput(
        data={
            "order_ref": order.order_number,
            "status": order.status,
            "fulfillment_status": order.fulfillment_status,
            "placed_at": order.placed_at.isoformat(),
            "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        }
    )


async def _tracking(value: BaseModel, context: ToolContext) -> ToolOutput:
    request = OrderInput.model_validate(value)
    tracking = await context.commerce.get_tracking(context.provider_context(), request.order_ref)
    return ToolOutput(
        data={
            "carrier": tracking.carrier,
            "status": tracking.events[-1].status if tracking.events else "unknown",
            "estimated_delivery_at": (
                tracking.estimated_delivery_at.isoformat()
                if tracking.estimated_delivery_at
                else None
            ),
            "tracking_url": tracking.tracking_url,
        }
    )


def build_registry() -> dict[str, ToolDefinition[Any]]:
    tools: list[ToolDefinition[Any]] = [
        ToolDefinition(
            "search_knowledge_base",
            KnowledgeInput,
            ToolOutput,
            RiskLevel.READ_ONLY,
            Permission.PUBLIC_KNOWLEDGE,
            8,
            False,
            False,
            _knowledge,
        ),
        ToolDefinition(
            "get_order",
            OrderInput,
            ToolOutput,
            RiskLevel.READ_ONLY,
            Permission.CUSTOMER_READ,
            5,
            False,
            False,
            _order,
        ),
        ToolDefinition(
            "get_tracking",
            OrderInput,
            ToolOutput,
            RiskLevel.READ_ONLY,
            Permission.CUSTOMER_READ,
            5,
            False,
            False,
            _tracking,
        ),
        ToolDefinition(
            "get_authenticated_customer",
            EmptyInput,
            ToolOutput,
            RiskLevel.READ_ONLY,
            Permission.CUSTOMER_READ,
            5,
            False,
            False,
            None,
        ),
        ToolDefinition(
            "get_return_status",
            OrderInput,
            ToolOutput,
            RiskLevel.READ_ONLY,
            Permission.CUSTOMER_READ,
            5,
            False,
            False,
            None,
        ),
        ToolDefinition(
            "propose_shipping_address_change",
            DeferredWriteInput,
            ToolOutput,
            RiskLevel.SENSITIVE_WRITE,
            Permission.SENSITIVE_WRITE,
            10,
            True,
            True,
            None,
        ),
        ToolDefinition(
            "execute_confirmed_address_change",
            DeferredWriteInput,
            ToolOutput,
            RiskLevel.SENSITIVE_WRITE,
            Permission.SENSITIVE_WRITE,
            10,
            True,
            True,
            None,
        ),
        ToolDefinition(
            "create_refund_request",
            DeferredWriteInput,
            ToolOutput,
            RiskLevel.SENSITIVE_WRITE,
            Permission.SENSITIVE_WRITE,
            10,
            True,
            True,
            None,
        ),
        ToolDefinition(
            "upsert_sales_lead",
            DeferredWriteInput,
            ToolOutput,
            RiskLevel.WRITE,
            Permission.CUSTOMER_WRITE,
            10,
            True,
            True,
            None,
        ),
        ToolDefinition(
            "create_support_ticket",
            DeferredWriteInput,
            ToolOutput,
            RiskLevel.ESCALATION,
            Permission.ESCALATE,
            10,
            True,
            False,
            None,
        ),
    ]
    return {item.name: item for item in tools}


class ToolGateway:
    def __init__(self, registry: dict[str, ToolDefinition[Any]] | None = None) -> None:
        self.registry = registry or build_registry()

    async def execute(
        self, name: str, arguments: dict[str, object], context: ToolContext
    ) -> tuple[SanitizedResult, list[CitationRef]]:
        definition = self.registry.get(name)
        if definition is None:
            return SanitizedResult(tool=name, status="failed", error_code="unknown_tool"), []
        try:
            parsed = definition.input_schema.model_validate(arguments)
        except ValidationError:
            return SanitizedResult(tool=name, status="failed", error_code="invalid_arguments"), []
        if definition.required_permission not in context.permissions:
            return SanitizedResult(tool=name, status="blocked", error_code="permission_denied"), []
        if definition.risk != RiskLevel.READ_ONLY or definition.confirmation_required:
            return SanitizedResult(
                tool=name, status="blocked", error_code="confirmation_required"
            ), []
        if definition.handler is None:
            return SanitizedResult(tool=name, status="blocked", error_code="not_implemented"), []
        try:
            output = await asyncio.wait_for(
                definition.handler(parsed, context), timeout=definition.timeout_seconds
            )
            checked = definition.output_schema.model_validate(output)
            return SanitizedResult(
                tool=name, status="completed", data=checked.data
            ), checked.citations
        except TimeoutError:
            return SanitizedResult(tool=name, status="failed", error_code="provider_timeout"), []
        except Exception:
            return SanitizedResult(tool=name, status="failed", error_code="tool_unavailable"), []
