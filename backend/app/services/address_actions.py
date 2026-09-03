import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.models import PendingAction, RecordStatus
from app.providers.errors import ProviderError
from app.providers.models import Address, Order, ProviderContext, ProviderErrorCode
from app.providers.ports import CommerceProviderV1
from app.services.audit import AuditService


class AddressActionError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AddressProposal:
    action_id: UUID
    action_hash: str
    confirmation_token: str
    expires_at: datetime
    order_number: str
    masked_current_address: dict[str, str | None]
    proposed_address: Address
    consequences: list[str]


@dataclass(frozen=True)
class AddressExecution:
    status: str
    action_id: UUID
    order_number: str
    order_version: str | None = None
    reason_code: str | None = None


def canonical_address(address: Address) -> bytes:
    return json.dumps(
        address.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()


def validate_address(value: object) -> Address:
    if isinstance(value, dict):
        value = dict(value)
        country = value.get("country_code")
        if isinstance(country, str):
            value["country_code"] = country.strip().upper()
    try:
        address = Address.model_validate(value)
    except ValidationError as exc:
        raise AddressActionError("address_missing_or_invalid") from exc
    fields = (address.recipient, address.line1, address.city, address.postal_code)
    if any(not item.strip() for item in fields):
        raise AddressActionError("address_missing_or_invalid")
    if address.line2 is not None and not address.line2.strip():
        address.line2 = None
    address.country_code = address.country_code.upper()
    if not re.fullmatch(r"[A-Z]{2}", address.country_code):
        raise AddressActionError("invalid_country_code")
    for field in ("recipient", "line1", "city", "region", "postal_code"):
        text = getattr(address, field)
        if len(text.strip()) > 160 or any(ord(char) < 32 for char in text):
            raise AddressActionError("address_missing_or_invalid")
        setattr(address, field, " ".join(text.split()))
    return address


def eligible(order: Order) -> bool:
    return (
        order.status.casefold() == "open" and order.fulfillment_status.casefold() == "unfulfilled"
    )


def mask_address(address: Address) -> dict[str, str | None]:
    return {
        "recipient": (address.recipient[:1] + "***") if address.recipient else "***",
        "line1": "*** " + address.line1.split()[-1] if address.line1 else "***",
        "line2": "***" if address.line2 else None,
        "city": address.city[:1] + "***",
        "region": address.region,
        "postal_code": "***" + address.postal_code[-2:],
        "country_code": address.country_code,
    }


class AddressActionService:
    def __init__(
        self, session: AsyncSession, settings: Settings, commerce: CommerceProviderV1
    ) -> None:
        self.session = session
        self.settings = settings
        self.commerce = commerce
        secret = settings.action_secret.get_secret_value().encode()
        self._secret = hashlib.sha256(secret).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(self._secret))
        self.audit = AuditService(session)

    async def _audit(
        self,
        organization_id: UUID,
        customer_id: UUID,
        action: PendingAction,
        event: str,
        outcome: str,
        reason: str | None = None,
    ) -> None:
        await self.audit.record(
            organization_id,
            "customer",
            event,
            outcome,
            actor_id=customer_id,
            target_type="pending_action",
            target_id=action.id,
            reason_code=reason,
        )

    def _provider_context(
        self,
        organization_id: UUID,
        customer_ref: str,
        actor_id: UUID,
        correlation_id: str,
        idempotency_key: str | None = None,
    ) -> ProviderContext:
        return ProviderContext(
            organization_id=organization_id,
            actor_ref=str(actor_id),
            customer_ref=customer_ref,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )

    def _action_hash(
        self,
        organization_id: UUID,
        customer_id: UUID,
        session_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        order: Order,
        payload_hash: str,
    ) -> str:
        bound = "|".join(
            map(
                str,
                (
                    organization_id,
                    customer_id,
                    session_id,
                    conversation_id,
                    run_id,
                    order.external_ref,
                    order.version,
                    payload_hash,
                ),
            )
        )
        return hmac.new(self._secret, bound.encode(), hashlib.sha256).hexdigest()

    async def propose(
        self,
        *,
        organization_id: UUID,
        customer_id: UUID,
        customer_ref: str,
        session_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        order_number: str,
        proposed_address: object,
    ) -> AddressProposal:
        if self.settings.commerce_provider != "mock":
            raise AddressActionError("unsupported_provider")
        address = validate_address(proposed_address)
        context = self._provider_context(organization_id, customer_ref, customer_id, str(run_id))
        try:
            order = await self.commerce.resolve_order(context, order_number)
        except ProviderError as exc:
            code = (
                "order_not_found"
                if exc.code in {ProviderErrorCode.NOT_FOUND, ProviderErrorCode.NOT_AUTHORIZED}
                else "ambiguous_order_number"
                if exc.code == ProviderErrorCode.CONFLICT
                else exc.code.value
            )
            raise AddressActionError(code) from exc
        if not eligible(order):
            await self.audit.record(
                organization_id,
                "customer",
                "address_change.failure",
                "denied",
                actor_id=customer_id,
                reason_code="ORDER_NOT_MODIFIABLE",
            )
            raise AddressActionError("order_not_modifiable")
        payload = canonical_address(address)
        payload_hash = hashlib.sha256(payload).hexdigest()
        action_hash = self._action_hash(
            organization_id, customer_id, session_id, conversation_id, run_id, order, payload_hash
        )
        nonce = secrets.token_urlsafe(24)
        expires = datetime.now(timezone.utc) + timedelta(
            seconds=self.settings.action_confirmation_ttl_seconds
        )
        action = PendingAction(
            organization_id=organization_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            run_id=run_id,
            session_id=session_id,
            action_type="shipping_address_change",
            order_ref=order.external_ref,
            order_number=order.order_number,
            order_version=order.version,
            encrypted_payload=self._fernet.encrypt(payload),
            payload_hash=payload_hash,
            action_hash=action_hash,
            token_hash=b"0" * 32,
            idempotency_key=f"address-action-{secrets.token_hex(24)}",
            expires_at=expires,
            status=RecordStatus.PENDING,
        )
        self.session.add(action)
        await self.session.flush()
        unsigned = f"{action.id}.{nonce}"
        signature = hmac.new(self._secret, unsigned.encode(), hashlib.sha256).hexdigest()
        token = f"{unsigned}.{signature}"
        action.token_hash = hashlib.sha256(token.encode()).digest()
        await self._audit(
            organization_id, customer_id, action, "address_change.proposed", "success"
        )
        return AddressProposal(
            action_id=action.id,
            action_hash=action_hash,
            confirmation_token=token,
            expires_at=expires,
            order_number=order.order_number,
            masked_current_address=mask_address(order.shipping_address),
            proposed_address=address,
            consequences=[
                "The shipping destination will be replaced for this order.",
                "This does not verify deliverability with a postal service or carrier.",
                "A changed or fulfilled order requires a new preview.",
            ],
        )

    async def decide(
        self,
        *,
        organization_id: UUID,
        customer_id: UUID,
        customer_ref: str,
        session_id: UUID,
        conversation_id: UUID,
        action_id: UUID,
        token: str,
        decision: str,
        correlation_id: str,
    ) -> AddressExecution:
        action = await self.session.scalar(
            select(PendingAction)
            .where(
                PendingAction.organization_id == organization_id,
                PendingAction.id == action_id,
                PendingAction.customer_id == customer_id,
                PendingAction.conversation_id == conversation_id,
                PendingAction.session_id == session_id,
            )
            .with_for_update()
        )
        if action is None or action.action_type != "shipping_address_change":
            raise AddressActionError("invalid_confirmation")
        if action.status != RecordStatus.PENDING:
            raise AddressActionError("replayed_confirmation")
        now = datetime.now(timezone.utc)
        if action.expires_at <= now:
            action.status = RecordStatus.CANCELLED
            action.failure_code = "EXPIRED"
            await self._audit(
                organization_id, customer_id, action, "address_change.expired", "expired"
            )
            return AddressExecution(
                "action_cancelled", action.id, action.order_number, reason_code="expired"
            )
        try:
            token_action, nonce, signature = token.split(".", 2)
            unsigned = f"{token_action}.{nonce}"
            valid = UUID(token_action) == action.id and hmac.compare_digest(
                signature, hmac.new(self._secret, unsigned.encode(), hashlib.sha256).hexdigest()
            )
        except (ValueError, TypeError):
            valid = False
        if not valid or not hmac.compare_digest(
            action.token_hash, hashlib.sha256(token.encode()).digest()
        ):
            await self._audit(
                organization_id,
                customer_id,
                action,
                "address_change.failure",
                "denied",
                "INVALID_CONFIRMATION",
            )
            raise AddressActionError("invalid_confirmation")
        normalized = decision.strip().casefold()
        if normalized in {"deny", "cancel", "no", "non", "annuler", "refuser"}:
            action.status = RecordStatus.CANCELLED
            action.completed_at = now
            await self._audit(
                organization_id, customer_id, action, "address_change.denied", "success"
            )
            return AddressExecution("action_cancelled", action.id, action.order_number)
        if normalized not in {"approve", "confirm", "yes", "oui", "confirmer", "approuver"}:
            raise AddressActionError("explicit_confirmation_required")
        try:
            payload = self._fernet.decrypt(action.encrypted_payload)
        except InvalidToken as exc:
            await self._audit(
                organization_id,
                customer_id,
                action,
                "address_change.failure",
                "denied",
                "PAYLOAD_TAMPERED",
            )
            raise AddressActionError("tampered_payload") from exc
        if hashlib.sha256(payload).hexdigest() != action.payload_hash:
            await self._audit(
                organization_id,
                customer_id,
                action,
                "address_change.failure",
                "denied",
                "PAYLOAD_TAMPERED",
            )
            raise AddressActionError("tampered_payload")
        address = validate_address(json.loads(payload))
        context = self._provider_context(
            organization_id, customer_ref, customer_id, correlation_id, action.idempotency_key
        )
        try:
            current = await self.commerce.get_order(context, action.order_ref)
        except ProviderError as exc:
            raise AddressActionError("provider_unavailable") from exc
        expected_hash = self._action_hash(
            organization_id,
            customer_id,
            session_id,
            conversation_id,
            action.run_id,
            current,
            action.payload_hash,
        )
        if current.version != action.order_version or not hmac.compare_digest(
            expected_hash, action.action_hash
        ):
            action.status = RecordStatus.CANCELLED
            action.failure_code = "VERSION_CONFLICT"
            await self._audit(
                organization_id,
                customer_id,
                action,
                "address_change.conflict",
                "conflict",
                "VERSION_CHANGED",
            )
            return AddressExecution(
                "action_failed",
                action.id,
                action.order_number,
                reason_code="new_confirmation_required",
            )
        if not eligible(current):
            action.status = RecordStatus.CANCELLED
            action.failure_code = "ORDER_NOT_MODIFIABLE"
            await self._audit(
                organization_id,
                customer_id,
                action,
                "address_change.conflict",
                "conflict",
                "ORDER_NOT_MODIFIABLE",
            )
            return AddressExecution(
                "action_failed", action.id, action.order_number, reason_code="order_not_modifiable"
            )
        action.approved_at = now
        await self._audit(
            organization_id, customer_id, action, "address_change.approved", "success"
        )
        try:
            updated = await self.commerce.update_address(
                context, action.order_ref, address, action.order_version
            )
        except ProviderError as exc:
            if exc.code == ProviderErrorCode.TIMEOUT:
                try:
                    reconciled = await self.commerce.get_order(context, action.order_ref)
                except ProviderError:
                    reconciled = None
                if reconciled and canonical_address(
                    reconciled.shipping_address
                ) == canonical_address(address):
                    updated = reconciled
                else:
                    action.failure_code = "OUTCOME_UNKNOWN"
                    await self._audit(
                        organization_id,
                        customer_id,
                        action,
                        "address_change.failure",
                        "uncertain",
                        "OUTCOME_UNKNOWN",
                    )
                    return AddressExecution(
                        "action_failed",
                        action.id,
                        action.order_number,
                        reason_code="outcome_unknown",
                    )
            elif exc.code == ProviderErrorCode.CONFLICT:
                action.status = RecordStatus.CANCELLED
                action.failure_code = "VERSION_CONFLICT"
                await self._audit(
                    organization_id,
                    customer_id,
                    action,
                    "address_change.conflict",
                    "conflict",
                    "VERSION_CHANGED",
                )
                return AddressExecution(
                    "action_failed",
                    action.id,
                    action.order_number,
                    reason_code="new_confirmation_required",
                )
            else:
                action.failure_code = exc.code.value.upper()
                await self._audit(
                    organization_id,
                    customer_id,
                    action,
                    "address_change.failure",
                    "failed",
                    action.failure_code,
                )
                return AddressExecution(
                    "action_failed", action.id, action.order_number, reason_code="provider_failure"
                )
        action.status = RecordStatus.COMPLETED
        action.completed_at = datetime.now(timezone.utc)
        action.result_version = updated.version
        await self._audit(
            organization_id, customer_id, action, "address_change.executed", "success"
        )
        return AddressExecution("action_completed", action.id, action.order_number, updated.version)
