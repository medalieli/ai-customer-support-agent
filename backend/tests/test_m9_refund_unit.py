from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.answers import GroundedAnswer
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, IntentLabel, IntentScore, VisibleMessage
from app.agent.tools import Permission, ToolContext, ToolGateway
from app.agent.triage import TriageOutput
from app.core.config import Settings
from app.domain.models import DocumentStatus, DocumentVersion, IngestionStatus, KnowledgeDocument
from app.knowledge.retrieval import Citation
from app.providers.models import Address, LineItem, Money, Order, RefundRequest
from app.services.refunds import (
    POLICY_FINGERPRINTS,
    PolicyBinding,
    RefundError,
    RefundIntent,
    RefundOutcome,
    RefundProposal,
    RefundService,
    evaluate_refund,
    extract_refund_order_number,
    parse_refund_intent,
)

NOW = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)


def policy(fingerprint: str | None = None) -> PolicyBinding:
    return PolicyBinding(
        uuid4(),
        uuid4(),
        "1.0-b549c0f0",
        fingerprint or next(iter(POLICY_FINGERPRINTS)),
        ("receipt",),
        (),
    )


def order(**updates: object) -> Order:
    value = Order(
        external_ref="ord-recent",
        order_number="NC-1004",
        version="1",
        status="open",
        fulfillment_status="delivered",
        placed_at=NOW - timedelta(days=10),
        delivered_at=NOW - timedelta(days=4),
        line_items=[
            LineItem(
                sku="MSE-PRO",
                name="Mouse",
                quantity=2,
                unit_price=Money(amount=Decimal("69.00"), currency="USD"),
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
    return value.model_copy(update=updates)


def intent(**updates: object) -> RefundIntent:
    base = RefundIntent("NC-1004", "unwanted", "MSE-PRO", 1, Decimal("50.00"), "USD")
    return RefundIntent(**{**base.__dict__, **updates})


def outcome(expected: RefundOutcome, expected_code: str, **updates: object) -> None:
    result = evaluate_refund(order(**updates), intent(), policy(), now=NOW)
    assert result.outcome == expected
    assert expected_code in result.reason_codes
    assert (
        result.facts_hash
        == evaluate_refund(order(**updates), intent(), policy(), now=NOW).facts_hash
    )


def test_eligible_and_decimal_partial_refund() -> None:
    result = evaluate_refund(order(), intent(amount=Decimal("68.99")), policy(), now=NOW)
    assert result.outcome == RefundOutcome.ELIGIBLE
    assert result.ruleset_version == "refund-v1"
    naive = order(delivered_at=(NOW - timedelta(days=4)).replace(tzinfo=None))
    assert evaluate_refund(naive, intent(), policy(), now=NOW).outcome == RefundOutcome.ELIGIBLE


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"delivered_at": NOW - timedelta(days=31)}, "WINDOW_EXPIRED"),
        ({"status": "cancelled"}, "ORDER_CANCELLED"),
        ({"status": "refunded"}, "ALREADY_REFUNDED"),
    ],
)
def test_definitive_order_denials(updates: dict[str, object], code: str) -> None:
    outcome(RefundOutcome.INELIGIBLE, code, **updates)


def test_final_sale_already_refunded_quantity_amount_and_currency() -> None:
    final = order()
    final.line_items[0].final_sale = True
    assert evaluate_refund(final, intent(), policy(), now=NOW).reason_codes == ("FINAL_SALE",)
    refunded = order(
        refund_requests=[
            RefundRequest(
                external_ref="r1",
                status="requested",
                requested_at=NOW,
                amount=Money(amount=Decimal("1"), currency="USD"),
                reason="safe",
            )
        ]
    )
    assert evaluate_refund(refunded, intent(), policy(), now=NOW).reason_codes == (
        "ALREADY_REFUNDED",
    )
    assert evaluate_refund(order(), intent(quantity=3), policy(), now=NOW).reason_codes == (
        "EXCESSIVE_QUANTITY",
    )
    assert evaluate_refund(
        order(), intent(amount=Decimal("138.01")), policy(), now=NOW
    ).reason_codes == ("EXCESSIVE_AMOUNT",)
    assert evaluate_refund(order(), intent(currency="EUR"), policy(), now=NOW).reason_codes == (
        "INVALID_CURRENCY",
    )


def test_manual_review_branches_and_policy_mismatch() -> None:
    assert evaluate_refund(
        order(fulfillment_status="partially_fulfilled"), intent(), policy(), now=NOW
    ).reason_codes == ("PARTIAL_FULFILLMENT",)
    assert evaluate_refund(order(), intent(damaged=True), policy(), now=NOW).reason_codes == (
        "MISSING_EVIDENCE",
    )
    assert evaluate_refund(
        order(), intent(damaged=True, evidence_supplied=True), policy(), now=NOW
    ).reason_codes == ("DAMAGE_REVIEW",)
    assert evaluate_refund(order(delivered_at=None), intent(), policy(), now=NOW).reason_codes == (
        "MISSING_DELIVERY_DATE",
    )
    assert (
        evaluate_refund(order(), intent(), None, now=NOW).outcome
        == RefundOutcome.MANUAL_REVIEW_REQUIRED
    )
    assert evaluate_refund(order(), intent(), policy("0" * 64), now=NOW).reason_codes == (
        "POLICY_MISMATCH",
    )
    costly = order()
    costly.line_items[0].unit_price.amount = Decimal("600.00")
    assert evaluate_refund(
        costly, intent(amount=Decimal("550.00")), policy(), now=NOW
    ).reason_codes == ("HIGH_VALUE",)


def test_item_and_fulfillment_validation() -> None:
    assert evaluate_refund(order(), intent(sku="OTHER"), policy(), now=NOW).reason_codes == (
        "INVALID_ITEM_OR_QUANTITY",
    )
    item = order()
    item.line_items[0].fulfillment_status = "unfulfilled"
    assert evaluate_refund(item, intent(), policy(), now=NOW).reason_codes == (
        "ITEM_NOT_FULFILLED",
    )
    assert evaluate_refund(order(status="pending"), intent(), policy(), now=NOW).reason_codes == (
        "NOT_PAID",
    )


def test_parser_en_fr_and_missing_fields() -> None:
    assert extract_refund_order_number("Refund order NC-1005; item: MON-27", None) == "NC-1005"
    assert extract_refund_order_number("refund this", "NC-1004") == "NC-1004"
    parsed = parse_refund_intent(
        "Refund NC-1004; reason: unwanted; item: MSE-PRO; quantity: 1; amount: USD 50.00",
        "NC-1004",
    )
    assert parsed.amount == Decimal("50.00")
    french = parse_refund_intent(
        "Remboursement NC-1004; motif: endommagé avec photos; article: MSE-PRO; "
        "quantité: 1; montant: 50,00 USD",
        "NC-1004",
    )
    assert french.damaged and french.evidence_supplied
    with pytest.raises(RefundError, match="missing_reason"):
        parse_refund_intent("Refund NC-1004", "NC-1004")
    with pytest.raises(RefundError, match="order_number"):
        parse_refund_intent("Refund", None)
    with pytest.raises(RefundError, match="missing_currency"):
        parse_refund_intent(
            "Refund NC-1004; reason: unwanted; item: MSE-PRO; quantity: 1; amount: 50.00",
            "NC-1004",
        )


@pytest.mark.asyncio
async def test_active_policy_binding_requires_valid_current_citations() -> None:
    document = KnowledgeDocument(
        id=uuid4(),
        organization_id=uuid4(),
        slug="returns-refunds",
        title="Return and Refund Policy",
        document_type="returns_refunds",
        status=DocumentStatus.APPROVED,
    )
    version = DocumentVersion(
        id=uuid4(),
        organization_id=document.organization_id,
        document_id=document.id,
        version="1.0-test",
        locale="en",
        checksum=next(iter(POLICY_FINGERPRINTS)),
        status=IngestionStatus.READY,
        source_filename="returns.md",
        media_type="text/markdown",
        raw_content=b"synthetic",
    )
    receipt = uuid4()
    citation = Citation(
        record_id=receipt,
        document_id=document.id,
        version_id=version.id,
        chunk_id=uuid4(),
        source_title=document.title,
        language="en",
        section="Returns",
        page=None,
        snippet="Returns are allowed within 30 days.",
    )
    session = Mock()
    query_result = Mock()
    query_result.first.return_value = (document, version)
    session.execute = AsyncMock(return_value=query_result)
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        reranker_provider="deterministic",
        _env_file=None,
    )
    service = RefundService(session, settings, Mock())
    passage = SimpleNamespace(citation=citation, text=citation.snippet)
    with (
        patch("app.services.refunds.retrieve_passages", AsyncMock(return_value=[passage])),
        patch("app.services.refunds.validate_citation", AsyncMock(return_value=True)),
    ):
        active = await service.policy(document.organization_id, "en")
    assert active and active.version_id == version.id
    assert active.citation_ids == (str(receipt),)

    query_result.first.return_value = None
    assert await service.policy(document.organization_id, "en") is None

    query_result.first.return_value = (document, version)
    wrong = SimpleNamespace(
        citation=citation.__class__(**{**citation.__dict__, "version_id": uuid4()}),
        text="wrong",
    )
    with patch("app.services.refunds.retrieve_passages", AsyncMock(return_value=[wrong])):
        assert await service.policy(document.organization_id, "en") is None


class RefundTriage:
    async def classify(self, message: str) -> TriageOutput:
        return TriageOutput(intents=[IntentScore(label=IntentLabel.REFUND, confidence=0.99)])


class UnusedAnswer:
    async def answer(
        self, question: str, language: str, evidence: list[dict[str, str]]
    ) -> GroundedAnswer:
        raise AssertionError("refund eligibility is deterministic")


def graph_context() -> ToolContext:
    return ToolContext(
        session=Mock(),
        settings=Settings(app_env="test", embedding_provider="fake", _env_file=None),
        organization_id=uuid4(),
        actor_ref="actor",
        customer_ref="customer",
        correlation_id="correlation-id",
        commerce=Mock(),
        crm=Mock(),
        permissions=frozenset({Permission.PUBLIC_KNOWLEDGE, Permission.CUSTOMER_READ}),
        locale="en",
        customer_id=uuid4(),
        session_id=uuid4(),
        conversation_id=uuid4(),
        run_id=uuid4(),
        request_message=(
            "Refund order NC-1004; reason: unwanted; item: MSE-PRO; quantity: 1; amount: USD 50.00"
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [
        (RefundOutcome.ELIGIBLE, "confirmation_required"),
        (RefundOutcome.INELIGIBLE, "completed"),
        (RefundOutcome.MANUAL_REVIEW_REQUIRED, "completed"),
    ],
)
async def test_langgraph_refund_routes_and_cited_explanation(
    decision: RefundOutcome, expected_status: str
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    async def emit(name: str, payload: dict[str, object]) -> None:
        events.append((name, payload))

    context = graph_context()
    receipt = str(uuid4())
    citation: dict[str, object] = {
        "receipt_id": receipt,
        "title": "Return policy",
        "snippet": "Thirty day return window.",
        "document_id": str(uuid4()),
        "version_id": str(uuid4()),
        "chunk_id": str(uuid4()),
        "language": "en",
        "section": "Returns",
        "page": None,
    }
    proposed = RefundProposal(
        uuid4() if decision == RefundOutcome.ELIGIBLE else None,
        uuid4(),
        decision,
        ("WITHIN_WINDOW" if decision == RefundOutcome.ELIGIBLE else "WINDOW_EXPIRED",),
        "NC-1004",
        "1.0-test",
        "refund-v1",
        (citation,),
        "a" * 64 if decision == RefundOutcome.ELIGIBLE else None,
        "token" * 10 if decision == RefundOutcome.ELIGIBLE else None,
        NOW if decision == RefundOutcome.ELIGIBLE else None,
        "MSE-PRO",
        1,
        Decimal("50.00"),
        "USD",
    )
    graph = AgentGraph(
        context.settings,
        RefundTriage(),
        UnusedAnswer(),
        ToolGateway(),
        context,
        emit,
        InMemorySaver(),
    )
    state = AgentState(
        thread_id="thread",
        run_id=str(context.run_id),
        messages=[VisibleMessage(role="user", content=context.request_message or "refund")],
    )
    with (
        patch("app.agent.graph.RefundService.propose", AsyncMock(return_value=proposed)),
        patch("app.agent.graph.validate_citation", AsyncMock(return_value=True)),
    ):
        compiled = cast(Any, graph.compiled)
        result = await compiled.ainvoke(state, {"configurable": {"thread_id": str(uuid4())}})
    assert result["status"] == expected_status
    if decision == RefundOutcome.ELIGIBLE:
        assert any(name == "confirmation_required" for name, _ in events)
    else:
        response = next(payload for name, payload in events if name == "response_completed")
        assert receipt in str(response)


@pytest.mark.asyncio
async def test_langgraph_refund_clarifies_missing_fields() -> None:
    context = graph_context()
    context.request_message = "Refund order NC-1004"
    events: list[tuple[str, dict[str, object]]] = []

    async def emit(name: str, payload: dict[str, object]) -> None:
        events.append((name, payload))

    graph = AgentGraph(
        context.settings,
        RefundTriage(),
        UnusedAnswer(),
        ToolGateway(),
        context,
        emit,
        InMemorySaver(),
    )
    compiled = cast(Any, graph.compiled)
    result = await compiled.ainvoke(
        AgentState(
            thread_id="thread",
            run_id=str(context.run_id),
            messages=[VisibleMessage(role="user", content="Refund order NC-1004")],
        ),
        {"configurable": {"thread_id": str(uuid4())}},
    )
    assert result["status"] == "clarification_required"
    assert "reason" in str(events)
