import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.models import (
    DocumentStatus,
    DocumentVersion,
    IngestionStatus,
    KnowledgeDocument,
    PendingAction,
    RecordStatus,
    RefundDecisionRecord,
)
from app.knowledge.retrieval import retrieve_passages, validate_citation
from app.providers.errors import ProviderError
from app.providers.models import Money, Order, ProviderContext, ProviderErrorCode
from app.providers.order_numbers import InvalidOrderNumber, normalize_order_number
from app.providers.ports import CommerceProviderV1
from app.services.audit import AuditService

RULESET_VERSION = "refund-v1"
POLICY_FINGERPRINTS = {
    "b549c0f0ae0625e573850422076e1c8600a9832b3fe033a478126c2aaba0117a",
    "20618d2c1ece16009b718e99f36d7d28c9b2cbb138209a349daf929dd7854bf7",
}
CENT = Decimal("0.01")
AUTO_REFUND_REVIEW_THRESHOLD = Decimal("500.00")


class RefundOutcome(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class RefundError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RefundIntent:
    order_number: str
    reason: str
    sku: str
    quantity: int
    amount: Decimal
    currency: str
    damaged: bool = False
    evidence_supplied: bool = False


@dataclass(frozen=True)
class PolicyBinding:
    document_id: UUID
    version_id: UUID
    version: str
    fingerprint: str
    citation_ids: tuple[str, ...]
    citations: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class EligibilityDecision:
    outcome: RefundOutcome
    reason_codes: tuple[str, ...]
    ruleset_version: str
    policy_version: str | None
    facts_hash: str


@dataclass(frozen=True)
class RefundProposal:
    action_id: UUID | None
    decision_id: UUID
    outcome: RefundOutcome
    reason_codes: tuple[str, ...]
    order_number: str
    policy_version: str | None
    ruleset_version: str
    citations: tuple[dict[str, object], ...]
    action_hash: str | None = None
    confirmation_token: str | None = None
    expires_at: datetime | None = None
    sku: str | None = None
    quantity: int | None = None
    amount: Decimal | None = None
    currency: str | None = None


@dataclass(frozen=True)
class RefundExecution:
    status: str
    action_id: UUID
    order_number: str
    request_ref: str | None = None
    reason_code: str | None = None


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT))


def _facts(order: Order, intent: RefundIntent, policy: PolicyBinding | None) -> bytes:
    safe = {
        "order_ref": order.external_ref,
        "order_version": order.version,
        "status": order.status,
        "fulfillment": order.fulfillment_status,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "refund_count": len(order.refund_requests),
        "sku": intent.sku,
        "quantity": intent.quantity,
        "amount": _money(intent.amount),
        "currency": intent.currency,
        "damaged": intent.damaged,
        "evidence": intent.evidence_supplied,
        "policy": policy.fingerprint if policy else None,
    }
    return json.dumps(safe, sort_keys=True, separators=(",", ":")).encode()


def evaluate_refund(
    order: Order,
    intent: RefundIntent,
    policy: PolicyBinding | None,
    *,
    now: datetime | None = None,
) -> EligibilityDecision:
    """Pure, deterministic money-policy decision. No model output is accepted."""
    now = now or datetime.now(timezone.utc)
    facts_hash = hashlib.sha256(_facts(order, intent, policy)).hexdigest()
    if policy is None or policy.fingerprint not in POLICY_FINGERPRINTS:
        return EligibilityDecision(
            RefundOutcome.MANUAL_REVIEW_REQUIRED,
            ("POLICY_MISMATCH",),
            RULESET_VERSION,
            policy.version if policy else None,
            facts_hash,
        )
    item = next(
        (line for line in order.line_items if line.sku.casefold() == intent.sku.casefold()), None
    )
    if item is None or intent.quantity < 1:
        return EligibilityDecision(
            RefundOutcome.INELIGIBLE,
            ("INVALID_ITEM_OR_QUANTITY",),
            RULESET_VERSION,
            policy.version,
            facts_hash,
        )
    if order.status.casefold() == "cancelled":
        return EligibilityDecision(
            RefundOutcome.INELIGIBLE,
            ("ORDER_CANCELLED",),
            RULESET_VERSION,
            policy.version,
            facts_hash,
        )
    if order.status.casefold() == "refunded" or order.refund_requests:
        return EligibilityDecision(
            RefundOutcome.INELIGIBLE,
            ("ALREADY_REFUNDED",),
            RULESET_VERSION,
            policy.version,
            facts_hash,
        )
    if item.final_sale:
        return EligibilityDecision(
            RefundOutcome.INELIGIBLE, ("FINAL_SALE",), RULESET_VERSION, policy.version, facts_hash
        )
    if intent.quantity > item.quantity:
        return EligibilityDecision(
            RefundOutcome.INELIGIBLE,
            ("EXCESSIVE_QUANTITY",),
            RULESET_VERSION,
            policy.version,
            facts_hash,
        )
    if intent.currency != item.unit_price.currency or intent.currency != order.total.currency:
        return EligibilityDecision(
            RefundOutcome.INELIGIBLE,
            ("INVALID_CURRENCY",),
            RULESET_VERSION,
            policy.version,
            facts_hash,
        )
    refundable = (item.unit_price.amount * Decimal(intent.quantity)).quantize(CENT)
    if intent.amount <= Decimal("0") or intent.amount > refundable:
        return EligibilityDecision(
            RefundOutcome.INELIGIBLE,
            ("EXCESSIVE_AMOUNT",),
            RULESET_VERSION,
            policy.version,
            facts_hash,
        )
    if intent.amount > AUTO_REFUND_REVIEW_THRESHOLD:
        return EligibilityDecision(
            RefundOutcome.MANUAL_REVIEW_REQUIRED,
            ("HIGH_VALUE",),
            RULESET_VERSION,
            policy.version,
            facts_hash,
        )
    if order.fulfillment_status.casefold() == "partially_fulfilled":
        return EligibilityDecision(
            RefundOutcome.MANUAL_REVIEW_REQUIRED,
            ("PARTIAL_FULFILLMENT",),
            RULESET_VERSION,
            policy.version,
            facts_hash,
        )
    if intent.damaged:
        code = "DAMAGE_REVIEW" if intent.evidence_supplied else "MISSING_EVIDENCE"
        return EligibilityDecision(
            RefundOutcome.MANUAL_REVIEW_REQUIRED,
            (code,),
            RULESET_VERSION,
            policy.version,
            facts_hash,
        )
    if order.delivered_at is None:
        return EligibilityDecision(
            RefundOutcome.MANUAL_REVIEW_REQUIRED,
            ("MISSING_DELIVERY_DATE",),
            RULESET_VERSION,
            policy.version,
            facts_hash,
        )
    delivered = order.delivered_at
    if delivered.tzinfo is None:
        delivered = delivered.replace(tzinfo=timezone.utc)
    if (now.date() - delivered.astimezone(timezone.utc).date()).days > 30:
        return EligibilityDecision(
            RefundOutcome.INELIGIBLE,
            ("WINDOW_EXPIRED",),
            RULESET_VERSION,
            policy.version,
            facts_hash,
        )
    if order.status.casefold() not in {"open", "paid", "delivered"}:
        return EligibilityDecision(
            RefundOutcome.INELIGIBLE, ("NOT_PAID",), RULESET_VERSION, policy.version, facts_hash
        )
    if item.fulfillment_status.casefold() != "fulfilled":
        return EligibilityDecision(
            RefundOutcome.MANUAL_REVIEW_REQUIRED,
            ("ITEM_NOT_FULFILLED",),
            RULESET_VERSION,
            policy.version,
            facts_hash,
        )
    return EligibilityDecision(
        RefundOutcome.ELIGIBLE, ("WITHIN_WINDOW",), RULESET_VERSION, policy.version, facts_hash
    )


def parse_refund_intent(message: str, order_number: str | None) -> RefundIntent:
    missing: list[str] = []
    if not order_number:
        missing.append("order_number")
    reason_match = re.search(r"(?:reason|because|motif|raison)\s*[:=]?\s*([^;,]+)", message, re.I)
    sku_match = re.search(
        r"(?:sku|item|article|produit)\s*[:=]?\s*([A-Z0-9-]{2,40})", message, re.I
    )
    qty_match = re.search(r"(?:qty|quantity|quantit[eé])\s*[:=]?\s*(\d+)", message, re.I)
    amount_match = re.search(
        r"(?:amount|montant)\s*[:=]?\s*(?:([A-Z]{3})\s*)?([0-9]+(?:[.,][0-9]{1,2})?)\s*([A-Z]{3})?",
        message,
        re.I,
    )
    for name, match in (
        ("reason", reason_match),
        ("item", sku_match),
        ("quantity", qty_match),
        ("requested_amount", amount_match),
    ):
        if match is None:
            missing.append(name)
    if missing:
        raise RefundError("missing_" + "_".join(missing))
    if not order_number or not reason_match or not sku_match or not qty_match or not amount_match:
        raise RefundError("missing_information")
    currency = (amount_match.group(1) or amount_match.group(3) or "").upper()
    if not currency:
        raise RefundError("missing_currency")
    try:
        amount = Decimal(amount_match.group(2).replace(",", ".")).quantize(CENT)
    except InvalidOperation as exc:
        raise RefundError("invalid_amount") from exc
    reason = " ".join(reason_match.group(1).split())[:240]
    lowered = message.casefold()
    damaged = any(word in lowered for word in ("damaged", "broken", "endommag", "cassé", "casse"))
    evidence = any(word in lowered for word in ("photo", "evidence", "preuve", "image"))
    return RefundIntent(
        order_number,
        reason,
        sku_match.group(1).upper(),
        int(qty_match.group(1)),
        amount,
        currency,
        damaged,
        evidence,
    )


def extract_refund_order_number(message: str, fallback: str | None) -> str | None:
    """Prefer an explicitly labelled order so SKU-like tokens cannot create ambiguity."""
    match = re.search(
        r"(?:order|commande)\s*(?:number|num[eé]ro|n[o°])?\s*[:#-]?\s*"
        r"(NC[- ]?\d{3,12}|\d{4,18})\b",
        message,
        re.IGNORECASE,
    )
    if match is None:
        return fallback
    try:
        return normalize_order_number(match.group(1))
    except InvalidOrderNumber:
        return None


class RefundService:
    def __init__(
        self, session: AsyncSession, settings: Settings, commerce: CommerceProviderV1
    ) -> None:
        self.session = session
        self.settings = settings
        self.commerce = commerce
        self.audit = AuditService(session)
        secret = settings.action_secret.get_secret_value().encode()
        self.secret = secret
        self.fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))

    def context(
        self,
        organization_id: UUID,
        customer_ref: str,
        customer_id: UUID,
        correlation_id: str,
        key: str | None = None,
    ) -> ProviderContext:
        return ProviderContext(
            organization_id=organization_id,
            actor_ref=str(customer_id),
            customer_ref=customer_ref,
            correlation_id=correlation_id,
            idempotency_key=key,
        )

    async def policy(self, organization_id: UUID, locale: str) -> PolicyBinding | None:
        now = datetime.now(timezone.utc)
        row = (
            await self.session.execute(
                select(KnowledgeDocument, DocumentVersion)
                .join(DocumentVersion, DocumentVersion.document_id == KnowledgeDocument.id)
                .where(
                    KnowledgeDocument.organization_id == organization_id,
                    KnowledgeDocument.document_type == "returns_refunds",
                    KnowledgeDocument.status == DocumentStatus.APPROVED,
                    DocumentVersion.locale == locale,
                    DocumentVersion.status == IngestionStatus.READY,
                    or_(
                        DocumentVersion.effective_from.is_(None),
                        DocumentVersion.effective_from <= now,
                    ),
                    or_(
                        DocumentVersion.effective_to.is_(None),
                        DocumentVersion.effective_to > now,
                    ),
                )
                .order_by(
                    DocumentVersion.approved_at.desc().nullslast(),
                    DocumentVersion.created_at.desc(),
                )
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        document, version = row
        passages = await retrieve_passages(
            self.session,
            self.settings,
            organization_id,
            "refund return policy eligibility final sale delivery window",
            language=locale,
            top_k=5,
        )
        valid: list[dict[str, object]] = []
        for item in passages:
            citation = item.citation
            if citation.document_id != document.id or citation.version_id != version.id:
                continue
            if await validate_citation(self.session, organization_id, citation):
                valid.append(
                    {
                        "receipt_id": str(citation.record_id),
                        "title": citation.source_title,
                        "snippet": citation.snippet,
                        "document_id": str(citation.document_id),
                        "version_id": str(citation.version_id),
                        "chunk_id": str(citation.chunk_id),
                        "language": citation.language,
                        "section": citation.section,
                        "page": citation.page,
                    }
                )
        if not valid:
            return None
        return PolicyBinding(
            document.id,
            version.id,
            version.version,
            version.checksum,
            tuple(str(x["receipt_id"]) for x in valid),
            tuple(valid),
        )

    def action_hash(
        self,
        organization_id: UUID,
        customer_id: UUID,
        session_id: UUID,
        conversation_id: UUID,
        order: Order,
        policy: PolicyBinding,
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
                    order.external_ref,
                    order.version,
                    policy.version_id,
                    policy.version,
                    policy.fingerprint,
                    RULESET_VERSION,
                    payload_hash,
                ),
            )
        )
        return hmac.new(self.secret, bound.encode(), hashlib.sha256).hexdigest()

    async def propose(
        self,
        *,
        organization_id: UUID,
        customer_id: UUID,
        customer_ref: str,
        session_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        intent: RefundIntent,
        locale: str,
    ) -> RefundProposal:
        try:
            order = await self.commerce.resolve_order(
                self.context(organization_id, customer_ref, customer_id, str(run_id)),
                intent.order_number,
            )
        except ProviderError as exc:
            raise RefundError(
                "order_not_found"
                if exc.code in {ProviderErrorCode.NOT_FOUND, ProviderErrorCode.NOT_AUTHORIZED}
                else exc.code.value
            ) from exc
        policy = await self.policy(organization_id, locale)
        decision = evaluate_refund(order, intent, policy)
        record = RefundDecisionRecord(
            organization_id=organization_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            order_ref=order.external_ref,
            order_number=order.order_number,
            order_version=order.version,
            policy_document_id=policy.document_id if policy else None,
            policy_version_id=policy.version_id if policy else None,
            policy_version=policy.version if policy else None,
            policy_fingerprint=policy.fingerprint if policy else None,
            ruleset_version=RULESET_VERSION,
            outcome=decision.outcome.value,
            reason_codes=list(decision.reason_codes),
            facts_hash=decision.facts_hash,
            citation_receipt_ids=list(policy.citation_ids if policy else ()),
            state="manual_review"
            if decision.outcome == RefundOutcome.MANUAL_REVIEW_REQUIRED
            else "evaluated",
            evaluated_at=datetime.now(timezone.utc),
        )
        self.session.add(record)
        await self.session.flush()
        await self.audit.record(
            organization_id,
            "customer",
            "refund.evaluated",
            "success",
            actor_id=customer_id,
            target_type="refund_decision",
            target_id=record.id,
            reason_code=decision.reason_codes[0],
        )
        if (
            decision.outcome != RefundOutcome.ELIGIBLE
            or self.settings.commerce_provider == "shopify"
        ):
            if (
                self.settings.commerce_provider == "shopify"
                and decision.outcome == RefundOutcome.ELIGIBLE
            ):
                record.outcome = RefundOutcome.MANUAL_REVIEW_REQUIRED.value
                record.state = "human_approval_required"
                decision = EligibilityDecision(
                    RefundOutcome.MANUAL_REVIEW_REQUIRED,
                    ("HUMAN_APPROVAL_REQUIRED",),
                    RULESET_VERSION,
                    decision.policy_version,
                    decision.facts_hash,
                )
                record.reason_codes = list(decision.reason_codes)
            audit_action = (
                "refund.manual_review_routed"
                if decision.outcome == RefundOutcome.MANUAL_REVIEW_REQUIRED
                else "refund.denied"
            )
            await self.audit.record(
                organization_id,
                "customer",
                audit_action,
                "success",
                actor_id=customer_id,
                target_type="refund_decision",
                target_id=record.id,
                reason_code=decision.reason_codes[0],
            )
            return RefundProposal(
                None,
                record.id,
                decision.outcome,
                decision.reason_codes,
                order.order_number,
                decision.policy_version,
                RULESET_VERSION,
                policy.citations if policy else (),
            )
        if policy is None:  # an eligible outcome always has a matched policy
            raise RefundError("policy_mismatch")
        payload = json.dumps(
            {
                "decision_id": str(record.id),
                "sku": intent.sku,
                "quantity": intent.quantity,
                "amount": _money(intent.amount),
                "currency": intent.currency,
                "reason": intent.reason,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        payload_hash = hashlib.sha256(payload).hexdigest()
        bound_action_hash = self.action_hash(
            organization_id, customer_id, session_id, conversation_id, order, policy, payload_hash
        )
        expires = datetime.now(timezone.utc) + timedelta(
            seconds=self.settings.action_confirmation_ttl_seconds
        )
        nonce = secrets.token_urlsafe(24)
        action = PendingAction(
            organization_id=organization_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            run_id=run_id,
            session_id=session_id,
            action_type="refund_request",
            order_ref=order.external_ref,
            order_number=order.order_number,
            order_version=order.version,
            encrypted_payload=self.fernet.encrypt(payload),
            payload_hash=payload_hash,
            action_hash=bound_action_hash,
            token_hash=b"0" * 32,
            idempotency_key="refund-action-" + secrets.token_hex(24),
            expires_at=expires,
            status=RecordStatus.PENDING,
        )
        self.session.add(action)
        await self.session.flush()
        unsigned = f"{action.id}.{nonce}"
        token = f"{unsigned}.{hmac.new(self.secret, unsigned.encode(), hashlib.sha256).hexdigest()}"
        action.token_hash = hashlib.sha256(token.encode()).digest()
        await self.audit.record(
            organization_id,
            "customer",
            "refund.proposed",
            "success",
            actor_id=customer_id,
            target_type="pending_action",
            target_id=action.id,
        )
        return RefundProposal(
            action.id,
            record.id,
            decision.outcome,
            decision.reason_codes,
            order.order_number,
            decision.policy_version,
            RULESET_VERSION,
            policy.citations,
            bound_action_hash,
            token,
            expires,
            intent.sku,
            intent.quantity,
            intent.amount,
            intent.currency,
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
    ) -> RefundExecution:
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
        if action is None or action.action_type != "refund_request":
            raise RefundError("invalid_confirmation")
        if action.status != RecordStatus.PENDING:
            raise RefundError("replayed_confirmation")
        now = datetime.now(timezone.utc)
        if action.expires_at <= now:
            action.status = RecordStatus.CANCELLED
            action.failure_code = "EXPIRED"
            await self.audit.record(
                organization_id,
                "customer",
                "refund.expired",
                "expired",
                actor_id=customer_id,
                target_type="pending_action",
                target_id=action.id,
            )
            return RefundExecution(
                "action_cancelled", action.id, action.order_number, reason_code="expired"
            )
        try:
            token_action, nonce, signature = token.split(".", 2)
            unsigned = f"{token_action}.{nonce}"
            valid = UUID(token_action) == action.id and hmac.compare_digest(
                signature, hmac.new(self.secret, unsigned.encode(), hashlib.sha256).hexdigest()
            )
        except (ValueError, TypeError):
            valid = False
        if not valid or not hmac.compare_digest(
            action.token_hash, hashlib.sha256(token.encode()).digest()
        ):
            await self.audit.record(
                organization_id,
                "customer",
                "refund.failure",
                "denied",
                actor_id=customer_id,
                target_type="pending_action",
                target_id=action.id,
                reason_code="INVALID_CONFIRMATION",
            )
            raise RefundError("invalid_confirmation")
        normalized = decision.strip().casefold()
        if normalized in {"deny", "cancel", "no", "non", "annuler", "refuser"}:
            action.status = RecordStatus.CANCELLED
            action.completed_at = now
            await self.audit.record(
                organization_id,
                "customer",
                "refund.denied",
                "success",
                actor_id=customer_id,
                target_type="pending_action",
                target_id=action.id,
            )
            return RefundExecution("action_cancelled", action.id, action.order_number)
        if normalized not in {"approve", "confirm", "yes", "oui", "confirmer", "approuver"}:
            raise RefundError("explicit_confirmation_required")
        try:
            payload = self.fernet.decrypt(action.encrypted_payload)
        except InvalidToken as exc:
            await self.audit.record(
                organization_id,
                "customer",
                "refund.failure",
                "denied",
                actor_id=customer_id,
                target_type="pending_action",
                target_id=action.id,
                reason_code="PAYLOAD_TAMPERED",
            )
            raise RefundError("tampered_payload") from exc
        if hashlib.sha256(payload).hexdigest() != action.payload_hash:
            await self.audit.record(
                organization_id,
                "customer",
                "refund.failure",
                "denied",
                actor_id=customer_id,
                target_type="pending_action",
                target_id=action.id,
                reason_code="PAYLOAD_TAMPERED",
            )
            raise RefundError("tampered_payload")
        values: dict[str, Any] = json.loads(payload)
        record = await self.session.scalar(
            select(RefundDecisionRecord)
            .where(
                RefundDecisionRecord.organization_id == organization_id,
                RefundDecisionRecord.id == UUID(values["decision_id"]),
            )
            .with_for_update()
        )
        if record is None or record.outcome != RefundOutcome.ELIGIBLE.value:
            raise RefundError("stale_decision")
        context = self.context(
            organization_id, customer_ref, customer_id, correlation_id, action.idempotency_key
        )
        try:
            current = await self.commerce.get_order(context, action.order_ref)
        except ProviderError as exc:
            raise RefundError("provider_unavailable") from exc
        try:
            policy = await self.policy(organization_id, "en")
            if policy is None or policy.version_id != record.policy_version_id:
                policy = await self.policy(organization_id, "fr")
        except Exception:
            policy = None
        intent = RefundIntent(
            action.order_number,
            str(values["reason"]),
            str(values["sku"]),
            int(values["quantity"]),
            Decimal(str(values["amount"])),
            str(values["currency"]),
        )
        fresh = evaluate_refund(current, intent, policy)
        expected = (
            self.action_hash(
                organization_id,
                customer_id,
                session_id,
                conversation_id,
                current,
                policy,
                action.payload_hash,
            )
            if policy
            else ""
        )
        if (
            current.version != action.order_version
            or fresh.outcome != RefundOutcome.ELIGIBLE
            or fresh.policy_version != record.policy_version
            or not hmac.compare_digest(expected, action.action_hash)
        ):
            action.status = RecordStatus.CANCELLED
            action.failure_code = "STALE_OR_CONFLICT"
            await self.audit.record(
                organization_id,
                "customer",
                "refund.conflict",
                "conflict",
                actor_id=customer_id,
                target_type="pending_action",
                target_id=action.id,
                reason_code="STATE_CHANGED",
            )
            return RefundExecution(
                "action_failed",
                action.id,
                action.order_number,
                reason_code="new_confirmation_required",
            )
        action.approved_at = now
        await self.audit.record(
            organization_id,
            "customer",
            "refund.approved",
            "success",
            actor_id=customer_id,
            target_type="pending_action",
            target_id=action.id,
        )
        try:
            created = await self.commerce.create_refund_request(
                context,
                action.order_ref,
                Money(amount=intent.amount, currency=intent.currency),
                intent.reason,
                action.order_version,
            )
        except ProviderError as exc:
            if exc.code == ProviderErrorCode.TIMEOUT:
                try:
                    reconciled = await self.commerce.get_order(context, action.order_ref)
                except ProviderError:
                    reconciled = None
                found = next(
                    (
                        x
                        for x in (reconciled.refund_requests if reconciled else [])
                        if x.amount == Money(amount=intent.amount, currency=intent.currency)
                    ),
                    None,
                )
                if found is None:
                    action.failure_code = "OUTCOME_UNKNOWN"
                    record.state = "manual_review"
                    await self.audit.record(
                        organization_id,
                        "customer",
                        "refund.failure",
                        "uncertain",
                        actor_id=customer_id,
                        target_type="pending_action",
                        target_id=action.id,
                        reason_code="OUTCOME_UNKNOWN",
                    )
                    return RefundExecution(
                        "action_failed",
                        action.id,
                        action.order_number,
                        reason_code="outcome_unknown",
                    )
                created = found
            elif exc.code == ProviderErrorCode.CONFLICT:
                action.status = RecordStatus.CANCELLED
                action.failure_code = "VERSION_CONFLICT"
                await self.audit.record(
                    organization_id,
                    "customer",
                    "refund.conflict",
                    "conflict",
                    actor_id=customer_id,
                    target_type="pending_action",
                    target_id=action.id,
                    reason_code="VERSION_CHANGED",
                )
                return RefundExecution(
                    "action_failed",
                    action.id,
                    action.order_number,
                    reason_code="new_confirmation_required",
                )
            else:
                action.failure_code = exc.code.value.upper()
                await self.audit.record(
                    organization_id,
                    "customer",
                    "refund.failure",
                    "failed",
                    actor_id=customer_id,
                    target_type="pending_action",
                    target_id=action.id,
                    reason_code=action.failure_code,
                )
                return RefundExecution(
                    "action_failed", action.id, action.order_number, reason_code="provider_failure"
                )
        action.status = RecordStatus.COMPLETED
        action.completed_at = datetime.now(timezone.utc)
        action.result_version = created.external_ref
        record.state = "submitted"
        await self.audit.record(
            organization_id,
            "customer",
            "refund.submitted",
            "success",
            actor_id=customer_id,
            target_type="pending_action",
            target_id=action.id,
        )
        return RefundExecution(
            "refund_request_submitted", action.id, action.order_number, created.external_ref
        )
