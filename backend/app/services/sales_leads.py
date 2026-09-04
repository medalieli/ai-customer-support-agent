import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.models import ConsentRecord, PendingAction, RecordStatus
from app.providers.errors import ProviderError
from app.providers.models import ContactUpsert, ProviderContext, ProviderErrorCode, SalesLeadUpsert
from app.providers.ports import CrmProviderV1
from app.services.audit import AuditService

CONTACT_METHODS = {"email", "phone", "video_call"}
BUDGETS = {"under_10k", "10k_50k", "50k_100k", "over_100k", "unspecified"}
TIMELINES = {"immediate", "1_3_months", "3_6_months", "over_6_months", "unspecified"}


class LeadFields(BaseModel):
    """Untrusted model extraction; identity and consent are deliberately absent."""

    model_config = ConfigDict(extra="forbid")
    company: str | None = Field(default=None, max_length=160)
    interest: str | None = Field(default=None, max_length=200)
    business_need: str | None = Field(default=None, max_length=1000)
    budget_range: str | None = Field(default=None, max_length=80)
    timeline: str | None = Field(default=None, max_length=80)
    preferred_contact_method: str | None = Field(default=None, max_length=20)


class LeadExtractor(Protocol):
    async def extract(self, message: str) -> LeadFields: ...


class OpenAILeadExtractor:
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OpenAI API key is required for lead extraction")
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.agent_model_timeout_seconds,
            max_retries=0,
        )
        self.model = settings.agent_model

    async def extract(self, message: str) -> LeadFields:
        response = await self.client.responses.parse(
            model=self.model,
            instructions=(
                "Extract only explicitly stated sales-lead facts. Never infer or invent "
                "company, contact details, budget, timeline, consent, or qualification. "
                "Use null when absent. Allowed budget_range: under_10k, 10k_50k, "
                "50k_100k, over_100k, unspecified. Allowed timeline: immediate, "
                "1_3_months, 3_6_months, over_6_months, unspecified. Allowed contact "
                "method: email, phone, video_call."
            ),
            input=message,
            text_format=LeadFields,
        )
        if response.output_parsed is None:
            raise ValueError("lead extraction missing")
        return response.output_parsed


def genuine_sales_intent(message: str) -> bool:
    text = message.casefold()
    support = re.search(
        r"\b(order status|track(?:ing)?|refund|return|damaged|late order|commande|suivi|"
        r"rembours|retour)\b",
        text,
    )
    sales = re.search(
        r"\b(enterprise|bulk (?:order|purchase)|product demo|book a demo|"
        r"speak (?:to|with) sales|sales team|partnership|partner with|devis|"
        r"commande en gros|d[ée]monstration|parler (?:aux|avec les) ventes|"
        r"partenariat|commercial)\b",
        text,
    )
    return bool(sales and (not support or re.search(r"\b(also|and|et|aussi)\b", text)))


def validate_fields(value: object) -> LeadFields:
    try:
        fields = LeadFields.model_validate(value)
    except ValidationError as exc:
        raise SalesLeadError("invalid_lead_data") from exc
    cleaned = fields.model_dump()
    for key, item in cleaned.items():
        if isinstance(item, str):
            item = " ".join(item.split())
            if not item or any(ord(char) < 32 for char in item):
                cleaned[key] = None
            else:
                cleaned[key] = item
    fields = LeadFields.model_validate(cleaned)
    if fields.budget_range is not None and fields.budget_range not in BUDGETS:
        raise SalesLeadError("invalid_budget_range")
    if fields.timeline is not None and fields.timeline not in TIMELINES:
        raise SalesLeadError("invalid_timeline")
    if fields.preferred_contact_method not in CONTACT_METHODS:
        raise SalesLeadError("missing_or_invalid_contact_method")
    if not fields.company or not fields.interest or not fields.business_need:
        raise SalesLeadError("missing_lead_information")
    return fields


class SalesLeadError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SalesLeadProposal:
    action_id: UUID
    action_hash: str
    confirmation_token: str
    expires_at: datetime
    preview: dict[str, str | None]


@dataclass(frozen=True)
class SalesLeadExecution:
    status: str
    action_id: UUID
    contact_ref: str | None = None
    lead_ref: str | None = None
    note_ref: str | None = None
    contact_operation: str | None = None
    lead_operation: str | None = None
    reason_code: str | None = None


class SalesLeadService:
    def __init__(self, session: AsyncSession, settings: Settings, crm: CrmProviderV1) -> None:
        self.session, self.settings, self.crm = session, settings, crm
        self._secret = hashlib.sha256(settings.action_secret.get_secret_value().encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(self._secret))
        self.audit = AuditService(session)

    async def _audit(
        self,
        org: UUID,
        customer: UUID,
        action: PendingAction,
        event: str,
        outcome: str,
        reason: str | None = None,
    ) -> None:
        await self.audit.record(
            org,
            "customer",
            event,
            outcome,
            actor_id=customer,
            target_type="pending_action",
            target_id=action.id,
            reason_code=reason,
        )

    def _hash(
        self,
        org: UUID,
        customer: UUID,
        session: UUID,
        conversation: UUID,
        run: UUID,
        payload_hash: str,
    ) -> str:
        bound = "|".join(map(str, (org, customer, session, conversation, run, payload_hash)))
        return hmac.new(self._secret, bound.encode(), hashlib.sha256).hexdigest()

    async def propose(
        self,
        *,
        organization_id: UUID,
        customer_id: UUID,
        session_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        verified_name: str,
        verified_email: str,
        fields: object,
    ) -> SalesLeadProposal:
        if self.settings.crm_provider != "mock":
            raise SalesLeadError("unsupported_provider")
        parsed = validate_fields(fields)
        email = verified_email.strip().lower()
        if len(email) > 254 or not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", email
        ):
            raise SalesLeadError("invalid_verified_email")
        name = " ".join(verified_name.split())
        if not name or len(name) > 160:
            raise SalesLeadError("invalid_verified_name")
        payload_obj = {"verified_name": name, "verified_email": email, **parsed.model_dump()}
        payload = json.dumps(payload_obj, sort_keys=True, separators=(",", ":")).encode()
        payload_hash = hashlib.sha256(payload).hexdigest()
        action_hash = self._hash(
            organization_id, customer_id, session_id, conversation_id, run_id, payload_hash
        )
        expires = datetime.now(timezone.utc) + timedelta(
            seconds=self.settings.action_confirmation_ttl_seconds
        )
        action = PendingAction(
            organization_id=organization_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            run_id=run_id,
            session_id=session_id,
            action_type="sales_lead",
            order_ref="crm",
            order_number="CRM",
            order_version="v1",
            encrypted_payload=self._fernet.encrypt(payload),
            payload_hash=payload_hash,
            action_hash=action_hash,
            token_hash=b"0" * 32,
            idempotency_key="sales-lead-" + secrets.token_hex(24),
            expires_at=expires,
            status=RecordStatus.PENDING,
        )
        self.session.add(action)
        await self.session.flush()
        nonce = secrets.token_urlsafe(24)
        unsigned = f"{action.id}.{nonce}"
        token = (
            f"{unsigned}.{hmac.new(self._secret, unsigned.encode(), hashlib.sha256).hexdigest()}"
        )
        action.token_hash = hashlib.sha256(token.encode()).digest()
        await self._audit(organization_id, customer_id, action, "sales_lead.proposed", "success")
        return SalesLeadProposal(action.id, action_hash, token, expires, payload_obj)

    async def decide(
        self,
        *,
        organization_id: UUID,
        customer_id: UUID,
        session_id: UUID,
        conversation_id: UUID,
        action_id: UUID,
        token: str,
        decision: str,
        correlation_id: str,
    ) -> SalesLeadExecution:
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
        if action is None or action.action_type != "sales_lead":
            raise SalesLeadError("invalid_confirmation")
        if action.status != RecordStatus.PENDING:
            raise SalesLeadError("replayed_confirmation")
        now = datetime.now(timezone.utc)
        if action.expires_at <= now:
            action.status, action.failure_code = RecordStatus.CANCELLED, "EXPIRED"
            await self._audit(
                organization_id, customer_id, action, "sales_lead.failure", "expired", "EXPIRED"
            )
            return SalesLeadExecution("action_cancelled", action.id, reason_code="expired")
        valid = False
        try:
            token_action, nonce, signature = token.split(".", 2)
            unsigned = f"{token_action}.{nonce}"
            valid = UUID(token_action) == action.id and hmac.compare_digest(
                signature, hmac.new(self._secret, unsigned.encode(), hashlib.sha256).hexdigest()
            )
        except (ValueError, TypeError):
            pass
        if not valid or not hmac.compare_digest(
            action.token_hash, hashlib.sha256(token.encode()).digest()
        ):
            await self._audit(
                organization_id,
                customer_id,
                action,
                "sales_lead.failure",
                "denied",
                "INVALID_CONFIRMATION",
            )
            raise SalesLeadError("invalid_confirmation")
        normalized = decision.strip().casefold()
        if normalized in {"deny", "cancel", "no", "non", "annuler", "refuser"}:
            action.status, action.completed_at = RecordStatus.CANCELLED, now
            await self._audit(organization_id, customer_id, action, "sales_lead.denied", "success")
            return SalesLeadExecution("action_cancelled", action.id)
        if normalized not in {"approve", "confirm", "yes", "oui", "confirmer", "approuver"}:
            raise SalesLeadError("explicit_confirmation_required")
        try:
            raw = self._fernet.decrypt(action.encrypted_payload)
        except InvalidToken as exc:
            await self._audit(
                organization_id,
                customer_id,
                action,
                "sales_lead.failure",
                "denied",
                "PAYLOAD_TAMPERED",
            )
            raise SalesLeadError("tampered_payload") from exc
        if hashlib.sha256(raw).hexdigest() != action.payload_hash or not hmac.compare_digest(
            action.action_hash,
            self._hash(
                organization_id,
                customer_id,
                session_id,
                conversation_id,
                action.run_id,
                action.payload_hash,
            ),
        ):
            raise SalesLeadError("tampered_payload")
        data = json.loads(raw)
        fields = validate_fields({key: data.get(key) for key in LeadFields.model_fields})
        action.approved_at = now
        consent = await self.session.scalar(
            select(ConsentRecord).where(
                ConsentRecord.organization_id == organization_id,
                ConsentRecord.pending_action_id == action.id,
            )
        )
        if consent is None:
            self.session.add(
                ConsentRecord(
                    organization_id=organization_id,
                    customer_id=customer_id,
                    conversation_id=conversation_id,
                    pending_action_id=action.id,
                    purpose="crm_sales_lead_v1",
                    consented_at=now,
                )
            )
        await self._audit(organization_id, customer_id, action, "sales_lead.consented", "success")
        base = ProviderContext(
            organization_id=organization_id,
            actor_ref=str(customer_id),
            correlation_id=correlation_id,
        )
        existing_contact = None
        existing_lead = None
        try:
            existing_contact = await self.crm.find_contact(base, data["verified_email"])
            names = data["verified_name"].split(" ", 1)
            contact_input = ContactUpsert(
                email=data["verified_email"],
                first_name=existing_contact.first_name if existing_contact else names[0],
                last_name=(
                    existing_contact.last_name
                    if existing_contact
                    else names[1]
                    if len(names) > 1
                    else "-"
                ),
                lifecycle_stage=existing_contact.lifecycle_stage if existing_contact else "lead",
                locale=existing_contact.locale if existing_contact else None,
                company=fields.company,
            )
            contact = await self.crm.upsert_contact(
                base.model_copy(update={"idempotency_key": action.idempotency_key + "-contact"}),
                contact_input,
            )
            existing_lead = await self.crm.find_lead(base, contact.external_ref)
            lead = await self.crm.upsert_lead(
                base.model_copy(update={"idempotency_key": action.idempotency_key + "-lead"}),
                SalesLeadUpsert(
                    contact_ref=contact.external_ref,
                    interest=fields.interest or "",
                    business_need=fields.business_need or "",
                    budget_range=fields.budget_range,
                    timeline=fields.timeline,
                    preferred_contact_method=fields.preferred_contact_method or "email",
                    version=existing_lead.version if existing_lead else None,
                ),
            )
            note_body = f"Sales inquiry: {fields.interest}. Business need: {fields.business_need}."
            note = await self.crm.create_conversation_note(
                base.model_copy(update={"idempotency_key": action.idempotency_key + "-note"}),
                contact.external_ref,
                note_body,
            )
        except ProviderError as exc:
            if exc.code == ProviderErrorCode.TIMEOUT:
                try:
                    reconciled_contact = await self.crm.find_contact(base, data["verified_email"])
                    if reconciled_contact is not None:
                        reconciled_lead = await self.crm.find_lead(
                            base, reconciled_contact.external_ref
                        )
                        if reconciled_lead is None:
                            reconciled_lead = await self.crm.upsert_lead(
                                base.model_copy(
                                    update={"idempotency_key": action.idempotency_key + "-lead"}
                                ),
                                SalesLeadUpsert(
                                    contact_ref=reconciled_contact.external_ref,
                                    interest=fields.interest or "",
                                    business_need=fields.business_need or "",
                                    budget_range=fields.budget_range,
                                    timeline=fields.timeline,
                                    preferred_contact_method=(
                                        fields.preferred_contact_method or "email"
                                    ),
                                ),
                            )
                        note = await self.crm.create_conversation_note(
                            base.model_copy(
                                update={"idempotency_key": action.idempotency_key + "-note"}
                            ),
                            reconciled_contact.external_ref,
                            f"Sales inquiry: {fields.interest}. Business need: "
                            f"{fields.business_need}.",
                        )
                        action.status = RecordStatus.COMPLETED
                        action.completed_at = datetime.now(timezone.utc)
                        action.result_version = reconciled_lead.version
                        await self._audit(
                            organization_id,
                            customer_id,
                            action,
                            "sales_lead.updated" if existing_lead else "sales_lead.created",
                            "success",
                        )
                        return SalesLeadExecution(
                            "action_completed",
                            action.id,
                            reconciled_contact.external_ref,
                            reconciled_lead.external_ref,
                            note.external_ref,
                            "updated" if existing_contact else "created",
                            "updated" if existing_lead else "created",
                        )
                except ProviderError:
                    pass
            action.failure_code = exc.code.value.upper()
            if exc.code == ProviderErrorCode.CONFLICT:
                action.status = RecordStatus.CANCELLED
            event = (
                "sales_lead.conflict"
                if exc.code == ProviderErrorCode.CONFLICT
                else "sales_lead.failure"
            )
            await self._audit(
                organization_id, customer_id, action, event, "failed", action.failure_code
            )
            return SalesLeadExecution("action_failed", action.id, reason_code=exc.code.value)
        action.status, action.completed_at, action.result_version = (
            RecordStatus.COMPLETED,
            datetime.now(timezone.utc),
            lead.version,
        )
        await self._audit(
            organization_id,
            customer_id,
            action,
            "sales_lead.updated" if existing_lead else "sales_lead.created",
            "success",
        )
        return SalesLeadExecution(
            "action_completed",
            action.id,
            contact.external_ref,
            lead.external_ref,
            note.external_ref,
            "updated" if existing_contact else "created",
            "updated" if existing_lead else "created",
        )
