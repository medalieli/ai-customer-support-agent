import asyncio
import base64
import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.domain.models import (
    AuditEvent,
    Conversation,
    Customer,
    DocumentStatus,
    DocumentVersion,
    IngestionStatus,
    KnowledgeDocument,
    PendingAction,
    RefundDecisionRecord,
)
from app.infrastructure.database import create_database_engine
from app.providers.errors import ProviderError
from app.providers.models import (
    Address,
    LineItem,
    Money,
    Order,
    ProviderContext,
    ProviderErrorCode,
    RefundRequest,
    Tracking,
)
from app.seed import ORGANIZATIONS, seed
from app.services.refunds import (
    POLICY_FINGERPRINTS,
    PolicyBinding,
    RefundError,
    RefundIntent,
    RefundOutcome,
    RefundProposal,
    RefundService,
)

pytestmark = pytest.mark.skipif(
    os.getenv("NOVACART_RUN_DB_TESTS") != "1", reason="integration database not requested"
)
NOW = datetime.now(timezone.utc)
POLICY_DOCUMENT_ID = UUID("90000000-0000-0000-0000-000000000901")
POLICY_VERSION_ID = UUID("90000000-0000-0000-0000-000000000902")


def make_order() -> Order:
    money = Money(amount=Decimal("69.00"), currency="USD")
    return Order(
        external_ref="refund-integration-order",
        order_number="NC-REFUND-1",
        version="1",
        status="open",
        fulfillment_status="delivered",
        placed_at=NOW - timedelta(days=8),
        delivered_at=NOW - timedelta(days=3),
        line_items=[
            LineItem(
                sku="MSE-PRO",
                name="Synthetic mouse",
                quantity=2,
                unit_price=money,
                fulfillment_status="fulfilled",
            )
        ],
        subtotal=Money(amount=Decimal("138.00"), currency="USD"),
        shipping=Money(amount=Decimal("8.00"), currency="USD"),
        tax=Money(amount=Decimal("11.04"), currency="USD"),
        total=Money(amount=Decimal("157.04"), currency="USD"),
        shipping_address=Address(
            recipient="Synthetic",
            line1="1 Test",
            city="Boston",
            region="MA",
            postal_code="02110",
            country_code="US",
        ),
    )


class Commerce:
    def __init__(self, *, timeout_after_write: bool = False) -> None:
        self.order = make_order()
        self.writes = 0
        self.timeout_after_write = timeout_after_write

    async def resolve_order(self, context: ProviderContext, order_number: str) -> Order:
        if context.customer_ref != "seed-amira-en" or order_number != self.order.order_number:
            raise ProviderError(ProviderErrorCode.NOT_FOUND)
        return self.order.model_copy(deep=True)

    async def get_order(self, context: ProviderContext, order_ref: str) -> Order:
        if context.customer_ref != "seed-amira-en" or order_ref != self.order.external_ref:
            raise ProviderError(ProviderErrorCode.NOT_FOUND)
        return self.order.model_copy(deep=True)

    async def get_tracking(self, context: ProviderContext, order_ref: str) -> Tracking:
        raise ProviderError(ProviderErrorCode.NOT_FOUND)

    async def update_address(
        self, context: ProviderContext, order_ref: str, address: Address, version: str
    ) -> Order:
        raise ProviderError(ProviderErrorCode.UNSUPPORTED)

    async def create_refund_request(
        self, context: ProviderContext, order_ref: str, amount: Money, reason: str, version: str
    ) -> RefundRequest:
        if version != self.order.version:
            raise ProviderError(ProviderErrorCode.CONFLICT)
        self.writes += 1
        created = RefundRequest(
            external_ref="refund-request-1",
            status="requested",
            requested_at=NOW,
            amount=amount,
            reason=reason,
        )
        self.order.refund_requests.append(created)
        self.order.version = "2"
        if self.timeout_after_write:
            raise ProviderError(ProviderErrorCode.TIMEOUT)
        return created


def binding() -> PolicyBinding:
    return PolicyBinding(
        POLICY_DOCUMENT_ID,
        POLICY_VERSION_ID,
        "1.0-test",
        next(iter(POLICY_FINGERPRINTS)),
        ("receipt",),
        ({"receipt_id": "receipt"},),
    )


@pytest.fixture
async def refund_db() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation]
]:
    settings = Settings(
        app_env="test",
        demo_auth_enabled=True,
        demo_staff_password=SecretStr("synthetic-demo-password"),
        postgres_host="localhost",
        embedding_provider="fake",
        reranker_provider="deterministic",
        mock_commerce_url=os.getenv("NOVACART_MOCK_COMMERCE_URL", "http://localhost:8080"),
        action_secret=SecretStr("m9-test-action-secret-at-least-32-bytes"),
    )
    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session, "synthetic-demo-password")
        customer = await session.scalar(
            select(Customer).where(
                Customer.organization_id == ORGANIZATIONS[0].id, Customer.demo_key == "amira-en"
            )
        )
        assert customer
        if await session.get(KnowledgeDocument, POLICY_DOCUMENT_ID) is None:
            session.add(
                KnowledgeDocument(
                    id=POLICY_DOCUMENT_ID,
                    organization_id=customer.organization_id,
                    slug="m9-refund-policy",
                    title="Synthetic M9 refund policy",
                    document_type="returns_refunds",
                    status=DocumentStatus.APPROVED,
                )
            )
            await session.flush()
            session.add(
                DocumentVersion(
                    id=POLICY_VERSION_ID,
                    organization_id=customer.organization_id,
                    document_id=POLICY_DOCUMENT_ID,
                    version="1.0-test",
                    locale="en",
                    checksum=next(iter(POLICY_FINGERPRINTS)),
                    status=IngestionStatus.READY,
                    source_filename="m9-refund-policy.md",
                    media_type="text/markdown",
                    raw_content=b"Synthetic policy fixture; no customer data.",
                )
            )
        conversation = Conversation(
            organization_id=customer.organization_id,
            customer_id=customer.id,
            title="M9 integration",
            locale="en",
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
    yield factory, settings, customer, conversation
    await engine.dispose()


async def proposal(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    customer: Customer,
    conversation: Conversation,
    commerce: Commerce,
) -> tuple[RefundProposal, UUID]:
    session = factory()
    service = RefundService(session, settings, commerce)
    cast(Any, service).policy = AsyncMock(return_value=binding())
    result = await service.propose(
        organization_id=customer.organization_id,
        customer_id=customer.id,
        customer_ref=customer.provider_customer_ref,
        session_id=(session_id := uuid4()),
        conversation_id=conversation.id,
        run_id=uuid4(),
        intent=RefundIntent("NC-REFUND-1", "private reason", "MSE-PRO", 1, Decimal("50.00"), "USD"),
        locale="en",
    )
    await session.commit()
    await session.close()
    return result, session_id


@pytest.mark.asyncio
async def test_confirmed_submission_replay_tamper_and_pii_safe_audit(
    refund_db: tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation],
) -> None:
    factory, settings, customer, conversation = refund_db
    commerce = Commerce()
    proposed, session_id = await proposal(factory, settings, customer, conversation, commerce)
    assert proposed.outcome == RefundOutcome.ELIGIBLE and commerce.writes == 0
    assert proposed.action_id and proposed.confirmation_token
    async with factory() as isolated_session:
        isolated = RefundService(isolated_session, settings, commerce)
        with pytest.raises(RefundError, match="invalid_confirmation"):
            await isolated.decide(
                organization_id=ORGANIZATIONS[1].id,
                customer_id=customer.id,
                customer_ref=customer.provider_customer_ref,
                session_id=session_id,
                conversation_id=conversation.id,
                action_id=proposed.action_id,
                token=proposed.confirmation_token,
                decision="confirm",
                correlation_id="cross-tenant",
            )
    assert commerce.writes == 0
    async with factory() as session:
        service = RefundService(session, settings, commerce)
        record = await session.scalar(
            select(RefundDecisionRecord).where(RefundDecisionRecord.id == proposed.decision_id)
        )
        assert record
        assert record.policy_document_id and record.policy_version_id
        assert record.policy_version and record.policy_fingerprint
        active = PolicyBinding(
            record.policy_document_id,
            record.policy_version_id,
            record.policy_version,
            record.policy_fingerprint,
            tuple(record.citation_receipt_ids),
            (),
        )
        cast(Any, service).policy = AsyncMock(return_value=active)
        outcome = await service.decide(
            organization_id=customer.organization_id,
            customer_id=customer.id,
            customer_ref=customer.provider_customer_ref,
            session_id=session_id,
            conversation_id=conversation.id,
            action_id=proposed.action_id,
            token=proposed.confirmation_token,
            decision="confirm",
            correlation_id="integration-confirm",
        )
        await session.commit()
        assert outcome.status == "refund_request_submitted" and commerce.writes == 1
        with pytest.raises(RefundError, match="replayed_confirmation"):
            await service.decide(
                organization_id=customer.organization_id,
                customer_id=customer.id,
                customer_ref=customer.provider_customer_ref,
                session_id=session_id,
                conversation_id=conversation.id,
                action_id=proposed.action_id,
                token=proposed.confirmation_token,
                decision="confirm",
                correlation_id="replay",
            )
        audits = list(
            await session.scalars(
                select(AuditEvent).where(AuditEvent.target_id == proposed.action_id)
            )
        )
        assert {event.action for event in audits} >= {
            "refund.proposed",
            "refund.approved",
            "refund.submitted",
        }
        assert "private reason" not in repr(
            [(event.reason_code, event.metadata_json) for event in audits]
        )


@pytest.mark.asyncio
async def test_denial_expiry_cross_tenant_tampering_and_timeout_reconciliation(
    refund_db: tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation],
) -> None:
    factory, settings, customer, conversation = refund_db
    for mode in ("deny", "expired", "timeout", "tamper"):
        commerce = Commerce(timeout_after_write=mode == "timeout")
        proposed, session_id = await proposal(factory, settings, customer, conversation, commerce)
        assert proposed.action_id and proposed.confirmation_token
        async with factory() as session:
            record = await session.get(RefundDecisionRecord, proposed.decision_id)
            assert record
            assert record.policy_document_id and record.policy_version_id
            assert record.policy_version and record.policy_fingerprint
            active = PolicyBinding(
                record.policy_document_id,
                record.policy_version_id,
                record.policy_version,
                record.policy_fingerprint,
                tuple(record.citation_receipt_ids),
                (),
            )
            service = RefundService(session, settings, commerce)
            cast(Any, service).policy = AsyncMock(return_value=active)
            if mode == "expired":
                action = await session.get(PendingAction, proposed.action_id)
                assert action
                action.expires_at = NOW - timedelta(seconds=1)
            token = (
                proposed.confirmation_token + "x"
                if mode == "tamper"
                else proposed.confirmation_token
            )
            if mode == "tamper":
                with pytest.raises(RefundError, match="invalid_confirmation"):
                    await service.decide(
                        organization_id=customer.organization_id,
                        customer_id=customer.id,
                        customer_ref=customer.provider_customer_ref,
                        session_id=session_id,
                        conversation_id=conversation.id,
                        action_id=proposed.action_id,
                        token=token,
                        decision="confirm",
                        correlation_id="negative",
                    )
            else:
                result = await service.decide(
                    organization_id=customer.organization_id,
                    customer_id=customer.id,
                    customer_ref=customer.provider_customer_ref,
                    session_id=session_id,
                    conversation_id=conversation.id,
                    action_id=proposed.action_id,
                    token=token,
                    decision="deny" if mode == "deny" else "confirm",
                    correlation_id="negative",
                )
                assert result.status == (
                    "refund_request_submitted" if mode == "timeout" else "action_cancelled"
                )
            await session.commit()
            assert commerce.writes == (1 if mode == "timeout" else 0)


@pytest.mark.asyncio
async def test_concurrent_confirmation_has_exactly_one_write(
    refund_db: tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation],
) -> None:
    factory, settings, customer, conversation = refund_db
    commerce = Commerce()
    proposed, session_id = await proposal(factory, settings, customer, conversation, commerce)
    assert proposed.action_id and proposed.confirmation_token
    action_id = proposed.action_id
    confirmation_token = proposed.confirmation_token

    async def confirm() -> str:
        async with factory() as session:
            record = await session.get(RefundDecisionRecord, proposed.decision_id)
            assert record
            assert record.policy_document_id and record.policy_version_id
            assert record.policy_version and record.policy_fingerprint
            active = PolicyBinding(
                record.policy_document_id,
                record.policy_version_id,
                record.policy_version,
                record.policy_fingerprint,
                tuple(record.citation_receipt_ids),
                (),
            )
            service = RefundService(session, settings, commerce)
            cast(Any, service).policy = AsyncMock(return_value=active)
            try:
                result = await service.decide(
                    organization_id=customer.organization_id,
                    customer_id=customer.id,
                    customer_ref=customer.provider_customer_ref,
                    session_id=session_id,
                    conversation_id=conversation.id,
                    action_id=action_id,
                    token=confirmation_token,
                    decision="confirm",
                    correlation_id=str(uuid4()),
                )
                await session.commit()
                return result.status
            except RefundError as exc:
                await session.rollback()
                return exc.code

    results = await asyncio.gather(confirm(), confirm())
    assert sorted(results) == ["refund_request_submitted", "replayed_confirmation"]
    assert commerce.writes == 1


@pytest.mark.asyncio
async def test_ineligible_manual_and_shopify_routes_create_no_pending_write(
    refund_db: tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation],
) -> None:
    factory, settings, customer, conversation = refund_db
    cases = [
        ("final", RefundOutcome.INELIGIBLE),
        ("partial", RefundOutcome.MANUAL_REVIEW_REQUIRED),
        ("shopify", RefundOutcome.MANUAL_REVIEW_REQUIRED),
    ]
    for mode, expected in cases:
        commerce = Commerce()
        if mode == "final":
            commerce.order.line_items[0].final_sale = True
        if mode == "partial":
            commerce.order.fulfillment_status = "partially_fulfilled"
        selected_settings = (
            settings.model_copy(update={"commerce_provider": "shopify"})
            if mode == "shopify"
            else settings
        )
        async with factory() as session:
            service = RefundService(session, selected_settings, commerce)
            cast(Any, service).policy = AsyncMock(return_value=binding())
            result = await service.propose(
                organization_id=customer.organization_id,
                customer_id=customer.id,
                customer_ref=customer.provider_customer_ref,
                session_id=uuid4(),
                conversation_id=conversation.id,
                run_id=uuid4(),
                intent=RefundIntent(
                    "NC-REFUND-1",
                    "private reason",
                    "MSE-PRO",
                    1,
                    Decimal("50.00"),
                    "USD",
                ),
                locale="en",
            )
            await session.commit()
            assert result.outcome == expected
            assert result.action_id is None
            assert commerce.writes == 0
            if mode == "shopify":
                assert result.reason_codes == ("HUMAN_APPROVAL_REQUIRED",)


@pytest.mark.asyncio
async def test_stale_order_or_policy_requires_new_confirmation(
    refund_db: tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation],
) -> None:
    factory, settings, customer, conversation = refund_db
    commerce = Commerce()
    proposed, session_id = await proposal(factory, settings, customer, conversation, commerce)
    assert proposed.action_id and proposed.confirmation_token
    commerce.order.version = "changed"
    async with factory() as session:
        record = await session.get(RefundDecisionRecord, proposed.decision_id)
        assert record and record.policy_document_id and record.policy_version_id
        assert record.policy_version and record.policy_fingerprint
        active = PolicyBinding(
            record.policy_document_id,
            record.policy_version_id,
            record.policy_version,
            record.policy_fingerprint,
            tuple(record.citation_receipt_ids),
            (),
        )
        service = RefundService(session, settings, commerce)
        cast(Any, service).policy = AsyncMock(return_value=active)
        result = await service.decide(
            organization_id=customer.organization_id,
            customer_id=customer.id,
            customer_ref=customer.provider_customer_ref,
            session_id=session_id,
            conversation_id=conversation.id,
            action_id=proposed.action_id,
            token=proposed.confirmation_token,
            decision="confirm",
            correlation_id="stale-order",
        )
        await session.commit()
        assert result.reason_code == "new_confirmation_required"
        assert commerce.writes == 0


@pytest.mark.asyncio
async def test_encrypted_payload_tampering_fails_without_write(
    refund_db: tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation],
) -> None:
    factory, settings, customer, conversation = refund_db
    commerce = Commerce()
    proposed, session_id = await proposal(factory, settings, customer, conversation, commerce)
    assert proposed.action_id and proposed.confirmation_token
    async with factory() as session:
        action = await session.get(PendingAction, proposed.action_id)
        assert action
        ciphertext = bytearray(base64.urlsafe_b64decode(action.encrypted_payload))
        ciphertext[-1] ^= 1
        action.encrypted_payload = base64.urlsafe_b64encode(ciphertext)
        await session.commit()
    async with factory() as session:
        service = RefundService(session, settings, commerce)
        with pytest.raises(RefundError, match="tampered_payload"):
            await service.decide(
                organization_id=customer.organization_id,
                customer_id=customer.id,
                customer_ref=customer.provider_customer_ref,
                session_id=session_id,
                conversation_id=conversation.id,
                action_id=proposed.action_id,
                token=proposed.confirmation_token,
                decision="confirm",
                correlation_id="tampered-payload",
            )
        assert commerce.writes == 0
