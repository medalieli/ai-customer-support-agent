"""Tenant-scoped aggregate operational analytics; never returns conversation content."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select

from app.api.dependencies import CurrentPrincipal, DatabaseSession
from app.domain.models import (
    AgentRun,
    AuditEvent,
    Conversation,
    PendingAction,
    RefundDecisionRecord,
    Role,
    SupportTicket,
    ToolRun,
    WebhookEvent,
    WebhookStatus,
)
from app.services.auth import AuthorizationError

router = APIRouter(prefix="/staff/analytics", tags=["staff-analytics"])


class AnalyticsResponse(BaseModel):
    range: str
    generated_at: datetime
    conversation_volume: int
    containment_rate: float
    escalation_rate: float
    tool_success_rate: float
    average_response_latency_ms: float
    p95_response_latency_ms: float
    citation_success_rate: float
    confirmations: dict[str, int]
    aggregates: dict[str, int]
    provider_health: dict[str, int]
    webhooks: dict[str, int]


RANGES = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


@router.get("", response_model=AnalyticsResponse)
async def analytics(
    principal: CurrentPrincipal,
    session: DatabaseSession,
    range: Literal["24h", "7d", "30d", "90d"] = Query(default="7d"),
) -> AnalyticsResponse:
    if principal.kind != "staff" or principal.role not in {Role.SUPPORT, Role.ADMIN}:
        raise AuthorizationError
    since = datetime.now(timezone.utc) - RANGES[range]
    organization_id = principal.organization_id

    conversation_volume = int(
        await session.scalar(
            select(func.count(Conversation.id)).where(
                Conversation.organization_id == organization_id, Conversation.created_at >= since
            )
        )
        or 0
    )
    run_row = (
        await session.execute(
            select(
                func.count(AgentRun.id),
                func.count(
                    case(
                        (
                            AgentRun.status.in_(
                                ["completed", "action_completed", "response_completed"]
                            ),
                            1,
                        )
                    )
                ),
                func.avg(func.extract("epoch", AgentRun.updated_at - AgentRun.created_at) * 1000),
                func.percentile_cont(0.95).within_group(
                    func.extract("epoch", AgentRun.updated_at - AgentRun.created_at) * 1000
                ),
            ).where(AgentRun.organization_id == organization_id, AgentRun.created_at >= since)
        )
    ).one()
    run_total, contained, average_latency, p95_latency = run_row
    escalations = int(
        await session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.occurred_at >= since,
                AuditEvent.action.in_(
                    ["agent.escalation_required", "agent.handoff_requested", "handoff.created"]
                ),
            )
        )
        or 0
    )
    tool_total = int(
        await session.scalar(
            select(func.count(ToolRun.id)).where(
                ToolRun.organization_id == organization_id, ToolRun.created_at >= since
            )
        )
        or 0
    )
    tool_success = int(
        await session.scalar(
            select(func.count(ToolRun.id)).where(
                ToolRun.organization_id == organization_id,
                ToolRun.created_at >= since,
                ToolRun.status == "COMPLETED",
            )
        )
        or 0
    )
    citation_failures = int(
        await session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.occurred_at >= since,
                AuditEvent.action == "agent.citation_rejected",
            )
        )
        or 0
    )
    citation_checks = int(
        await session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.occurred_at >= since,
                AuditEvent.action.in_(["agent.citation_validated", "agent.citation_rejected"]),
            )
        )
        or 0
    )

    pending_rows = (
        await session.execute(
            select(PendingAction.status, func.count(PendingAction.id))
            .where(
                PendingAction.organization_id == organization_id, PendingAction.created_at >= since
            )
            .group_by(PendingAction.status)
        )
    ).all()
    confirmations = {"approved": 0, "denied": 0, "expired": 0, "conflict": 0}
    for status, count in pending_rows:
        key = {"COMPLETED": "approved", "CANCELLED": "denied"}.get(
            str(getattr(status, "value", status)).upper()
        )
        if key:
            confirmations[key] += int(count)
    expired = int(
        await session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.occurred_at >= since,
                AuditEvent.reason_code == "expired",
            )
        )
        or 0
    )
    confirmations["expired"] = expired
    confirmations["conflict"] = int(
        await session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.occurred_at >= since,
                AuditEvent.reason_code.ilike("%conflict%"),
            )
        )
        or 0
    )

    refunds = int(
        await session.scalar(
            select(func.count(RefundDecisionRecord.id)).where(
                RefundDecisionRecord.organization_id == organization_id,
                RefundDecisionRecord.evaluated_at >= since,
            )
        )
        or 0
    )
    tickets = int(
        await session.scalar(
            select(func.count(SupportTicket.id)).where(
                SupportTicket.organization_id == organization_id, SupportTicket.created_at >= since
            )
        )
        or 0
    )
    leads = int(
        await session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.occurred_at >= since,
                AuditEvent.action.like("sales_lead.%"),
            )
        )
        or 0
    )
    provider_failures = int(
        await session.scalar(
            select(func.count(ToolRun.id)).where(
                ToolRun.organization_id == organization_id,
                ToolRun.created_at >= since,
                ToolRun.status == "FAILED",
            )
        )
        or 0
    )
    webhook_rows = (
        await session.execute(
            select(WebhookEvent.status, func.count(WebhookEvent.id))
            .where(
                WebhookEvent.organization_id == organization_id, WebhookEvent.received_at >= since
            )
            .group_by(WebhookEvent.status)
        )
    ).all()
    webhook_counts = {"retries": 0, "dead_letter": 0}
    for status, count in webhook_rows:
        if status == WebhookStatus.DEAD_LETTER:
            webhook_counts["dead_letter"] += int(count)
    webhook_counts["retries"] = int(
        await session.scalar(
            select(func.sum(func.greatest(WebhookEvent.attempts - 1, 0))).where(
                WebhookEvent.organization_id == organization_id,
                WebhookEvent.received_at >= since,
            )
        )
        or 0
    )
    return AnalyticsResponse(
        range=range,
        generated_at=datetime.now(timezone.utc),
        conversation_volume=conversation_volume,
        containment_rate=_rate(int(contained or 0), int(run_total or 0)),
        escalation_rate=_rate(escalations, int(run_total or 0)),
        tool_success_rate=_rate(tool_success, tool_total),
        average_response_latency_ms=round(float(average_latency or 0), 2),
        p95_response_latency_ms=round(float(p95_latency or 0), 2),
        citation_success_rate=_rate(citation_checks - citation_failures, citation_checks),
        confirmations=confirmations,
        aggregates={"refunds": refunds, "leads": leads, "tickets": tickets},
        provider_health={
            "successes": max(0, tool_total - provider_failures),
            "failures": provider_failures,
        },
        webhooks=webhook_counts,
    )
