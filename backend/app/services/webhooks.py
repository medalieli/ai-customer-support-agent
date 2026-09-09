import base64
import hashlib
import hmac
import html
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.models import (
    Conversation,
    ConversationOwner,
    ConversationStatus,
    MessageRole,
    OrganizationMembership,
    ProviderConnection,
    ProviderProjection,
    ProviderResourceBinding,
    Status,
    TicketStatus,
    WebhookConversationEffect,
    WebhookEvent,
    WebhookStatus,
)
from app.domain.models import (
    SupportTicket as DbSupportTicket,
)
from app.infrastructure.database import set_tenant_scope
from app.providers.factory import create_commerce_provider, create_crm_provider
from app.providers.models import Order, ProviderContext, SupportTicket, TicketMessage
from app.providers.ports import CommerceProviderV1, CrmProviderV1
from app.repositories.conversations import ConversationRepository
from app.services.audit import AuditService

ALLOWED_TOPICS = {
    "shopify": {
        "orders/create",
        "orders/updated",
        "orders/cancelled",
        "fulfillments/create",
        "fulfillments/update",
        "refunds/create",
        "refunds/update",
    },
    "hubspot": {
        "contact.creation",
        "contact.propertyChange",
        "lead.creation",
        "lead.propertyChange",
        "ticket.creation",
        "ticket.propertyChange",
        "ticket.assignment",
        "ticket.reply",
        "ticket.note",
        "ticket.resolution",
    },
    "mock_commerce": {
        "order.updated",
        "fulfillment.updated",
        "tracking.updated",
        "order.cancelled",
        "refund.updated",
    },
    "mock_crm": {
        "contact.updated",
        "lead.updated",
        "ticket.updated",
        "ticket.assigned",
        "ticket.reply",
        "ticket.note",
        "ticket.resolved",
    },
}


class WebhookRejected(Exception):
    def __init__(self, code: str, status_code: int = 401):
        self.code, self.status_code = code, status_code


def _key(settings: Settings) -> bytes:
    return hashlib.sha256(settings.action_secret.get_secret_value().encode()).digest()


def encrypt(settings: Settings, value: bytes) -> bytes:
    import os

    nonce = os.urandom(12)
    return nonce + AESGCM(_key(settings)).encrypt(nonce, value, b"provider-webhook-v1")


def decrypt(settings: Settings, value: bytes) -> bytes:
    return AESGCM(_key(settings)).decrypt(value[:12], value[12:], b"provider-webhook-v1")


def _within_window(timestamp: str, settings: Settings) -> None:
    try:
        number = int(timestamp)
        if number > 10_000_000_000:
            number //= 1000
        supplied = datetime.fromtimestamp(number, timezone.utc)
    except (ValueError, OSError) as exc:
        raise WebhookRejected("invalid_timestamp") from exc
    if (
        abs((datetime.now(timezone.utc) - supplied).total_seconds())
        > settings.webhook_replay_window_seconds
    ):
        raise WebhookRejected("expired_timestamp")


def verify(
    provider: str,
    secret: bytes,
    raw: bytes,
    headers: dict[str, str],
    method: str,
    uri: str,
    settings: Settings,
) -> bool:
    if provider in {"shopify", "mock_commerce"}:
        timestamp = headers.get("x-shopify-triggered-at") or headers.get("x-mock-timestamp", "")
        _within_window(timestamp, settings)
        supplied = headers.get("x-shopify-hmac-sha256") or headers.get("x-mock-signature", "")
        expected = base64.b64encode(hmac.new(secret, raw, hashlib.sha256).digest()).decode()
    else:
        timestamp = headers.get("x-hubspot-request-timestamp") or headers.get(
            "x-mock-timestamp", ""
        )
        _within_window(timestamp, settings)
        supplied = headers.get("x-hubspot-signature-v3") or headers.get("x-mock-signature", "")
        expected = base64.b64encode(
            hmac.new(
                secret, method.encode() + uri.encode() + raw + timestamp.encode(), hashlib.sha256
            ).digest()
        ).decode()
    return hmac.compare_digest(supplied.encode(), expected.encode())


async def accept(
    session: AsyncSession,
    settings: Settings,
    provider: str,
    endpoint_key: str,
    raw: bytes,
    headers: dict[str, str],
    method: str,
    uri: str,
) -> tuple[WebhookEvent, str]:
    if len(raw) > settings.webhook_max_body_bytes:
        raise WebhookRejected("body_too_large", 413)
    if "application/json" not in headers.get("content-type", "").lower():
        raise WebhookRejected("invalid_content_type", 415)
    connection = await session.scalar(
        select(ProviderConnection).where(
            ProviderConnection.provider == provider,
            ProviderConnection.endpoint_key == endpoint_key,
            ProviderConnection.active.is_(True),
        )
    )
    if connection is None:
        raise WebhookRejected("unknown_connection", 404)
    organization_id = connection.organization_id
    connection_id = connection.id
    await set_tenant_scope(session, organization_id)
    secrets = [connection.encrypted_current_secret]
    if connection.encrypted_previous_secret:
        secrets.append(connection.encrypted_previous_secret)
    if not any(
        verify(provider, decrypt(settings, item), raw, headers, method, uri, settings)
        for item in secrets
    ):
        raise WebhookRejected("invalid_signature")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WebhookRejected("malformed_json", 400) from exc
    if not isinstance(payload, dict | list):
        raise WebhookRejected("malformed_json", 400)
    topic = (
        headers.get("x-shopify-topic")
        or headers.get("x-mock-topic")
        or (
            payload[0].get("subscriptionType")
            if provider == "hubspot" and isinstance(payload, list) and payload
            else None
        )
    )
    event_id = (
        headers.get("x-shopify-webhook-id")
        or headers.get("x-mock-event-id")
        or (
            str(payload[0].get("eventId"))
            if provider == "hubspot" and isinstance(payload, list) and payload
            else None
        )
    )
    if not topic or topic not in ALLOWED_TOPICS.get(provider, set()):
        raise WebhookRejected("unsupported_topic", 422)
    if not event_id or len(event_id) > 200:
        raise WebhookRejected("invalid_event_id", 422)
    fingerprint = hashlib.sha256(raw).hexdigest()
    item: dict[str, Any]
    if isinstance(payload, list):
        item = payload[0] if payload and isinstance(payload[0], dict) else {}
    else:
        item = payload
    occurred_value = item.get("occurred_at") or item.get("occurredAt") or item.get("updated_at")
    try:
        occurred = datetime.fromisoformat(str(occurred_value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        occurred = datetime.now(timezone.utc)
    event = WebhookEvent(
        organization_id=organization_id,
        provider=provider,
        connection_id=connection_id,
        external_event_id=event_id,
        topic=topic,
        payload_hash=fingerprint,
        encrypted_payload=encrypt(settings, raw),
        occurred_at=occurred,
        status=WebhookStatus.RECEIVED,
        attempts=0,
    )
    session.add(event)
    try:
        await session.commit()
        await AuditService(session).record(
            organization_id,
            "provider",
            "webhook.accepted",
            "success",
            target_type="webhook",
            target_id=event.id,
            metadata={"provider": provider, "topic": topic},
        )
        await session.commit()
        return event, "accepted"
    except IntegrityError:
        await session.rollback()
        await set_tenant_scope(session, organization_id)
        existing = await session.scalar(
            select(WebhookEvent).where(
                WebhookEvent.connection_id == connection_id,
                WebhookEvent.external_event_id == event_id,
            )
        )
        if existing and existing.payload_hash == fingerprint:
            await AuditService(session).record(
                organization_id,
                "provider",
                "webhook.duplicated",
                "ignored",
                target_type="webhook",
                target_id=existing.id,
                metadata={"provider": provider, "topic": topic},
            )
            await session.commit()
            return existing, "duplicate"
        if existing:
            existing.status, existing.safe_error = (
                WebhookStatus.CONFLICT,
                "payload_fingerprint_conflict",
            )
            await AuditService(session).record(
                organization_id,
                "provider",
                "webhook.conflicting",
                "rejected",
                target_type="webhook",
                target_id=existing.id,
                metadata={"provider": provider, "topic": topic},
            )
            await session.commit()
            return existing, "conflict"
        raise


def safe_projection(payload: Any, topic: str) -> tuple[str, str, str, dict[str, Any]]:
    item = payload[0] if isinstance(payload, list) else payload
    ref = str(
        item.get("id")
        or item.get("objectId")
        or item.get("order_ref")
        or item.get("ticket_ref")
        or "unknown"
    )[:160]
    occurred = str(
        item.get("updated_at") or item.get("occurred_at") or item.get("occurredAt") or ""
    )
    version = str(item.get("version") or occurred or "0")[:80]
    resource = (
        "ticket"
        if "ticket" in topic
        else "contact"
        if "contact" in topic
        else "lead"
        if "lead" in topic
        else "order"
    )
    allowed = {
        k: item[k]
        for k in (
            "status",
            "fulfillment_status",
            "tracking_status",
            "assigned_staff_ref",
            "visibility",
        )
        if k in item
    }
    return resource, ref, version, allowed


def _resource_identity(payload: Any, topic: str) -> tuple[str, str]:
    item = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(item, dict):
        return "unknown", ""
    resource_type = "ticket" if "ticket" in topic else "order"
    candidates = (
        ("ticket_ref", "ticketId", "objectId", "id")
        if resource_type == "ticket"
        else ("order_ref", "admin_graphql_api_id", "order_id", "id")
    )
    return resource_type, str(next((item[key] for key in candidates if item.get(key)), ""))[:160]


def _order_data(order: Order) -> dict[str, Any]:
    tracking_status = (
        order.tracking.events[-1].status if order.tracking and order.tracking.events else None
    )
    return {
        "status": order.status,
        "fulfillment_status": order.fulfillment_status,
        "tracking_status": tracking_status,
        "refund_statuses": [item.status for item in order.refund_requests],
    }


def _ticket_data(ticket: SupportTicket) -> dict[str, Any]:
    return {
        "status": ticket.status,
        "assigned_staff_ref": ticket.assigned_staff_ref,
    }


def _clean_public_text(value: str) -> str:
    return html.escape(" ".join(value.replace("\x00", "").split())[:4000])


async def _append_effects(
    session: AsyncSession,
    event: WebhookEvent,
    bindings: list[ProviderResourceBinding],
    safe_data: dict[str, Any],
    version: str,
    public_reply: TicketMessage | None,
) -> None:
    repo = ConversationRepository(session)
    logical = hashlib.sha256(
        json.dumps(
            [event.topic, version, safe_data, public_reply.external_ref if public_reply else None],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    for binding in bindings:
        prior = await session.scalar(
            select(WebhookConversationEffect).where(
                WebhookConversationEffect.binding_id == binding.id,
                WebhookConversationEffect.logical_key == logical,
            )
        )
        if prior:
            continue
        conversation = await session.scalar(
            select(Conversation)
            .where(
                Conversation.organization_id == event.organization_id,
                Conversation.id == binding.conversation_id,
            )
            .with_for_update()
        )
        if conversation is None:
            continue
        payload = {
            "type": "provider_sync",
            "resource": binding.resource_type,
            "topic": event.topic,
            "state": safe_data,
        }
        visible = True
        if public_reply:
            payload = {"type": "crm_public_reply", "body": _clean_public_text(public_reply.body)}
        message = await repo.add_message(
            conversation,
            MessageRole.SYSTEM_EVENT,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            conversation.locale,
            visible_to_customer=visible,
        )
        session.add(
            WebhookConversationEffect(
                organization_id=event.organization_id,
                webhook_event_id=event.id,
                binding_id=binding.id,
                conversation_id=conversation.id,
                logical_key=logical,
                message_id=message.id,
            )
        )


async def _sync_ticket_state(
    session: AsyncSession,
    event: WebhookEvent,
    ticket: SupportTicket,
    bindings: list[ProviderResourceBinding],
) -> None:
    local = await session.scalar(
        select(DbSupportTicket)
        .where(
            DbSupportTicket.organization_id == event.organization_id,
            DbSupportTicket.provider_ref == ticket.external_ref,
        )
        .with_for_update()
    )
    if local is None:
        return
    conversation = await session.scalar(
        select(Conversation)
        .where(
            Conversation.organization_id == event.organization_id,
            Conversation.id == local.conversation_id,
        )
        .with_for_update()
    )
    if conversation is None:
        return
    if ticket.status in {"resolved", "closed"}:
        local.status = TicketStatus(ticket.status)
        local.resolved_at = ticket.updated_at
        conversation.status = ConversationStatus(ticket.status)
        conversation.ownership_state = ticket.status
    if ticket.assigned_staff_ref and ticket.status not in {"resolved", "closed"}:
        try:
            staff_id = UUID(ticket.assigned_staff_ref)
        except ValueError:
            return
        membership = await session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == event.organization_id,
                OrganizationMembership.staff_user_id == staff_id,
                OrganizationMembership.status == Status.ACTIVE,
            )
        )
        if membership:
            local.assigned_staff_id, local.status = staff_id, TicketStatus.IN_PROGRESS
            conversation.assigned_staff_id, conversation.owner = staff_id, ConversationOwner.STAFF
            conversation.ownership_state = "staff_active"


async def process(
    session: AsyncSession,
    settings: Settings,
    event_id: UUID,
    *,
    fail: str | None = None,
    commerce: CommerceProviderV1 | None = None,
    crm: CrmProviderV1 | None = None,
) -> str:
    event = await session.get(WebhookEvent, event_id, with_for_update=True)
    if event is None:
        return "missing"
    await set_tenant_scope(session, event.organization_id)
    if event.status in {WebhookStatus.PROCESSED, WebhookStatus.STALE, WebhookStatus.CONFLICT}:
        return event.status.value
    event.status = WebhookStatus.PROCESSING
    event.attempts += 1
    event.processing_started_at = datetime.now(timezone.utc)
    await session.commit()
    try:
        if fail:
            raise RuntimeError(fail)
        payload = json.loads(decrypt(settings, event.encrypted_payload))
        resource, ref = _resource_identity(payload, event.topic)
        bindings = list(
            await session.scalars(
                select(ProviderResourceBinding)
                .where(
                    ProviderResourceBinding.organization_id == event.organization_id,
                    ProviderResourceBinding.connection_id == event.connection_id,
                    ProviderResourceBinding.resource_type == resource,
                    ProviderResourceBinding.external_ref == ref,
                )
                .with_for_update()
            )
        )
        if not bindings:
            event.status, event.safe_error, event.processed_at = (
                WebhookStatus.PROCESSED,
                "unassociated",
                datetime.now(timezone.utc),
            )
            await AuditService(session).record(
                event.organization_id,
                "worker",
                "webhook.unassociated",
                event.status.value,
                target_type="webhook",
                target_id=event.id,
                metadata={"provider": event.provider, "topic": event.topic},
            )
            await session.commit()
            return "unassociated"
        ownership = {(item.customer_id, item.provider_customer_ref) for item in bindings}
        if len(ownership) != 1:
            event.status, event.safe_error, event.processed_at = (
                WebhookStatus.PROCESSED,
                "binding_conflict",
                datetime.now(timezone.utc),
            )
            await AuditService(session).record(
                event.organization_id,
                "worker",
                "webhook.quarantined",
                event.status.value,
                target_type="webhook",
                target_id=event.id,
                metadata={"provider": event.provider, "topic": event.topic},
            )
            await session.commit()
            return "quarantined"
        context = ProviderContext(
            organization_id=event.organization_id,
            actor_ref="webhook-worker",
            customer_ref=bindings[0].provider_customer_ref,
            correlation_id=str(event.id),
        )
        public_reply = None
        if resource == "order":
            authoritative = await (commerce or create_commerce_provider(settings)).get_order(
                context, ref
            )
            version, data, authoritative_time = (
                authoritative.version,
                _order_data(authoritative),
                event.occurred_at or event.received_at,
            )
        else:
            authoritative_ticket = await (crm or create_crm_provider(settings)).get_ticket(
                context, ref
            )
            version, data, authoritative_time = (
                authoritative_ticket.version,
                _ticket_data(authoritative_ticket),
                authoritative_ticket.updated_at,
            )
            await _sync_ticket_state(session, event, authoritative_ticket, bindings)
            if "reply" in event.topic:
                messages = await (crm or create_crm_provider(settings)).list_ticket_messages(
                    context, ref
                )
                public = [item for item in messages if item.visibility in {"public", "customer"}]
                public_reply = public[-1] if public else None
        current = await session.scalar(
            select(ProviderProjection)
            .where(
                ProviderProjection.organization_id == event.organization_id,
                ProviderProjection.provider == event.provider,
                ProviderProjection.resource_type == resource,
                ProviderProjection.external_ref == ref,
            )
            .with_for_update()
        )
        occurred = authoritative_time
        if current and current.occurred_at >= occurred:
            event.status = WebhookStatus.STALE
            action = "webhook.stale"
        else:
            if current:
                current.version, current.occurred_at, current.safe_data = version, occurred, data
            else:
                session.add(
                    ProviderProjection(
                        organization_id=event.organization_id,
                        provider=event.provider,
                        resource_type=resource,
                        external_ref=ref,
                        version=version,
                        occurred_at=occurred,
                        safe_data=data,
                    )
                )
            event.status, event.processed_at = WebhookStatus.PROCESSED, datetime.now(timezone.utc)
            action = "webhook.processed"
            if "note" not in event.topic:
                await _append_effects(session, event, bindings, data, version, public_reply)
        event.safe_error = None
        await AuditService(session).record(
            event.organization_id,
            "worker",
            action,
            event.status.value,
            target_type="webhook",
            target_id=event.id,
            metadata={"provider": event.provider, "topic": event.topic},
        )
        await session.commit()
        return event.status.value
    except Exception:
        await session.rollback()
        event = await session.get(WebhookEvent, event_id, with_for_update=True)
        assert event
        if event.attempts >= settings.webhook_max_attempts:
            event.status, event.safe_error = WebhookStatus.DEAD_LETTER, "processing_failed"
            action = "webhook.dead_lettered"
        else:
            event.status, event.safe_error = WebhookStatus.FAILED, "temporary_processing_failure"
            event.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                seconds=min(settings.webhook_retry_base_seconds * 2 ** (event.attempts - 1), 3600)
            )
            action = "webhook.retried"
        await AuditService(session).record(
            event.organization_id,
            "worker",
            action,
            event.status.value,
            target_type="webhook",
            target_id=event.id,
            metadata={"provider": event.provider, "topic": event.topic},
        )
        await session.commit()
        return event.status.value
