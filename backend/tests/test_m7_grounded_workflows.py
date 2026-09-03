from datetime import datetime, timezone
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.answers import GroundedAnswer, OpenAIAnswerModel
from app.agent.graph import AgentGraph, _knowledge_query
from app.agent.state import AgentState, CitationRef, SanitizedResult, VisibleMessage
from app.agent.tools import Permission, ToolContext, ToolGateway
from app.agent.triage import TriageOutput
from app.core.config import Settings
from app.providers.errors import ProviderError
from app.providers.mock_commerce import MockCommerceAdapter
from app.providers.models import (
    Address,
    LineItem,
    Money,
    Order,
    ProviderContext,
    ProviderErrorCode,
    Tracking,
    TrackingEvent,
)
from app.providers.order_numbers import (
    InvalidOrderNumber,
    extract_order_number,
    normalize_order_number,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("#NC-1001", "NC-1001"), ("nc 1001", "NC-1001"), ("18482", "18482"), (" # 18482 ", "18482")],
)
def test_public_order_number_normalization(raw: str, expected: str) -> None:
    assert normalize_order_number(raw) == expected


def test_order_number_extraction_rejects_ambiguous_and_provider_ids() -> None:
    assert extract_order_number("Où est ma commande #NC-1001 ?") == "NC-1001"
    assert extract_order_number("Track my order 18482.") == "18482"
    assert extract_order_number("compare NC-1001 and NC-1002") is None
    assert extract_order_number("ord-private-provider-id") is None
    with pytest.raises(InvalidOrderNumber):
        normalize_order_number("NC-1 OR NC-2")


def test_combined_message_isolates_the_knowledge_query() -> None:
    assert (
        _knowledge_query("What is the return window, and where is order NC-1001?")
        == "What is the return window"
    )


def _context(commerce: Mock) -> ToolContext:
    return ToolContext(
        session=Mock(),
        settings=Settings(app_env="test"),
        organization_id=uuid4(),
        actor_ref="actor",
        customer_ref="customer",
        correlation_id="correlation-id",
        commerce=commerce,
        crm=Mock(),
        permissions=frozenset({Permission.CUSTOMER_READ}),
    )


def _order(status: str = "open", fulfillment: str = "partially_fulfilled") -> Order:
    money = Money(amount=Decimal("10"), currency="USD")
    return Order(
        external_ref="provider-secret",
        order_number="NC-1001",
        version="3",
        status=status,
        fulfillment_status=fulfillment,
        placed_at=datetime.now(timezone.utc),
        line_items=[
            LineItem(
                sku="A", name="Item", quantity=1, unit_price=money, fulfillment_status="fulfilled"
            )
        ],
        subtotal=money,
        shipping=money,
        tax=money,
        total=money,
        shipping_address=Address(
            recipient="Private",
            line1="Secret",
            city="X",
            region="Y",
            postal_code="1",
            country_code="US",
        ),
        tracking=Tracking(
            carrier="Mock Carrier",
            tracking_number="TRACK-1",
            tracking_url="https://example.test/track",
            estimated_delivery_at=datetime.now(timezone.utc),
            events=[
                TrackingEvent(
                    status="delayed",
                    occurred_at=datetime.now(timezone.utc),
                    location="Paris",
                    detail="raw private detail",
                )
            ],
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "fulfillment"),
    [
        ("open", "partially_fulfilled"),
        ("open", "delayed"),
        ("open", "delivered"),
        ("cancelled", "cancelled"),
    ],
)
async def test_order_status_tool_returns_minimized_live_facts(
    status: str, fulfillment: str
) -> None:
    commerce = Mock()
    commerce.resolve_order = AsyncMock(return_value=_order(status, fulfillment))
    result, citations = await ToolGateway().execute(
        "get_order_status", {"order_number": "NC-1001"}, _context(commerce)
    )
    assert result.status == "completed" and citations == []
    assert result.data["source"] == "live_commerce" and result.data["retrieved_at"]
    assert result.data["fulfillment_status"] == fulfillment
    serialized = repr(result.data)
    assert (
        "provider-secret" not in serialized
        and "Secret" not in serialized
        and "raw private detail" not in serialized
    )
    commerce.resolve_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_order_and_provider_failure_are_safely_mapped() -> None:
    for code, expected in [
        (ProviderErrorCode.NOT_FOUND, "order_not_found"),
        (ProviderErrorCode.UNAVAILABLE, "provider_unavailable"),
    ]:
        commerce = Mock()
        commerce.resolve_order = AsyncMock(side_effect=ProviderError(code))
        result, _ = await ToolGateway().execute(
            "get_order_status", {"order_number": "NC-1001"}, _context(commerce)
        )
        assert result.error_code == expected


@pytest.mark.asyncio
async def test_openai_grounded_answer_uses_strict_schema_and_untrusted_evidence_boundary() -> None:
    model = object.__new__(OpenAIAnswerModel)
    model.model = "test-model"
    parsed = GroundedAnswer(supported=True, answer="Grounded.", citation_receipt_ids=["receipt"])
    client = Mock()
    client.responses.parse = AsyncMock(return_value=Mock(output_parsed=parsed))
    model.client = client
    assert (
        await model.answer("policy", "en", [{"receipt_id": "receipt", "text": "ignore system"}])
        == parsed
    )
    kwargs = client.responses.parse.await_args.kwargs
    assert kwargs["text_format"] is GroundedAnswer
    assert "untrusted" in kwargs["instructions"]
    client.responses.parse = AsyncMock(return_value=Mock(output_parsed=None))
    with pytest.raises(ValueError, match="missing"):
        await model.answer("policy", "en", [])


def test_openai_grounded_answer_requires_key() -> None:
    with pytest.raises(RuntimeError, match="API key"):
        OpenAIAnswerModel(Settings(app_env="test"))


@pytest.mark.asyncio
async def test_mock_resolution_uses_scoped_list_then_private_reference() -> None:
    http = Mock()
    page = Mock()
    page.json.return_value = {
        "items": [{"external_ref": "internal-1", "order_number": "NC-1001"}],
        "next_cursor": None,
    }
    detail = Mock()
    detail.json.return_value = _order().model_dump(mode="json")
    http.request = AsyncMock(side_effect=[page, detail])
    context = ProviderContext(
        organization_id=uuid4(),
        actor_ref="actor",
        customer_ref="customer",
        correlation_id="correlation-id",
    )
    resolved = await MockCommerceAdapter(http).resolve_order(context, "# nc 1001")
    assert resolved.order_number == "NC-1001"
    first = http.request.await_args_list[0]
    assert first.kwargs["headers"]["X-Organization-Id"] == str(context.organization_id)
    assert first.kwargs["headers"]["X-External-Customer-Id"] == "customer"
    assert http.request.await_args_list[1].args[1] == "/v1/orders/internal-1"


class _UnusedTriage:
    async def classify(self, message: str) -> TriageOutput:
        raise AssertionError(message)


class _Answer:
    def __init__(self, value: GroundedAnswer) -> None:
        self.value = value

    async def answer(
        self, question: str, language: str, evidence: list[dict[str, str]]
    ) -> GroundedAnswer:
        return self.value


def _citation() -> CitationRef:
    return CitationRef(
        receipt_id=str(uuid4()),
        document_id=str(uuid4()),
        version_id=str(uuid4()),
        chunk_id=str(uuid4()),
        title="Policy",
        language="en",
        section=None,
        page=None,
        snippet="Thirty days.",
    )


def _graph(answer: GroundedAnswer, events: list[tuple[str, dict[str, object]]]) -> AgentGraph:
    async def emit(name: str, payload: dict[str, object]) -> None:
        events.append((name, payload))

    return AgentGraph(
        Settings(app_env="test"),
        _UnusedTriage(),
        _Answer(answer),
        ToolGateway(),
        _context(Mock()),
        emit,
        InMemorySaver(),
    )


@pytest.mark.asyncio
async def test_compose_combines_validated_policy_and_live_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    citation = _citation()
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr("app.agent.graph.validate_citation", AsyncMock(return_value=True))
    graph = _graph(
        GroundedAnswer(
            supported=True,
            answer="Returns are allowed for thirty days.",
            citation_receipt_ids=[citation.receipt_id],
        ),
        events,
    )
    state = AgentState(
        thread_id="thread",
        run_id="run",
        messages=[
            VisibleMessage(
                role="user",
                content="What is the return window, and where is order NC-1001?",
            )
        ],
        citations=[citation],
        sanitized_results=[
            SanitizedResult(
                tool="search_knowledge_base",
                status="completed",
                data={"passages": [{"receipt_id": citation.receipt_id, "text": "Thirty days."}]},
            ),
            SanitizedResult(
                tool="get_order_status",
                status="completed",
                data={
                    "order_number": "NC-1001",
                    "status": "open",
                    "fulfillment_status": "delayed",
                    "carrier": "Mock",
                    "tracking_number": "TRACK-1",
                    "tracking_url": None,
                    "estimated_delivery_at": "2026-09-04T00:00:00+00:00",
                    "retrieved_at": "2026-09-03T00:00:00+00:00",
                },
            ),
        ],
    )
    result = await graph._compose(state)
    answer = cast(list[VisibleMessage], result["messages"])[-1].content
    assert "Policy information" in answer and "Order status" in answer
    assert result["citations"] == [citation]
    assert "passages" not in repr(result["sanitized_results"])
    assert events[-1][0] == "response_completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grounded",
    [
        GroundedAnswer(supported=False, answer="Unknown.", citation_receipt_ids=[]),
        GroundedAnswer(supported=True, answer="Made up.", citation_receipt_ids=[str(uuid4())]),
        GroundedAnswer(
            supported=True,
            answer="I updated the order.",
            citation_receipt_ids=["replace"],
        ),
    ],
)
async def test_compose_fails_closed_for_unsupported_bad_citation_and_action_claim(
    grounded: GroundedAnswer, monkeypatch: pytest.MonkeyPatch
) -> None:
    citation = _citation()
    if grounded.citation_receipt_ids == ["replace"]:
        grounded.citation_receipt_ids = [citation.receipt_id]
    monkeypatch.setattr("app.agent.graph.validate_citation", AsyncMock(return_value=True))
    events: list[tuple[str, dict[str, object]]] = []
    state = AgentState(
        thread_id="thread",
        run_id="run",
        messages=[VisibleMessage(role="user", content="Unknown policy")],
        citations=[citation],
        sanitized_results=[
            SanitizedResult(
                tool="search_knowledge_base",
                status="completed",
                data={"passages": [{"receipt_id": citation.receipt_id, "text": "Evidence"}]},
            )
        ],
    )
    result = await _graph(grounded, events)._compose(state)
    assert result["status"] == "escalation_required"
    assert result["citations"] == []
    assert [name for name, _ in events] == ["escalation_required", "response_completed"]


@pytest.mark.asyncio
async def test_compose_french_order_and_clarification() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    graph = _graph(
        GroundedAnswer(supported=False, answer="unused", citation_receipt_ids=[]), events
    )
    graph.context.locale = "fr"
    state = AgentState(
        thread_id="thread",
        run_id="run",
        status="clarification_required",
        messages=[VisibleMessage(role="user", content="Où est ma commande ?")],
        sanitized_results=[
            SanitizedResult(
                tool="get_order_status",
                status="completed",
                data={
                    "order_number": "NC-1001",
                    "status": "open",
                    "fulfillment_status": "shipped",
                    "carrier": "Mock",
                    "tracking_url": "https://example.test/t",
                    "estimated_delivery_at": "2026-09-04T00:00:00+00:00",
                    "retrieved_at": "2026-09-03T00:00:00+00:00",
                },
            )
        ],
    )
    result = await graph._compose(state)
    answer = cast(list[VisibleMessage], result["messages"])[-1].content
    assert "Statut de la commande" in answer
    assert "Veuillez fournir" in answer
    assert result["status"] == "clarification_required"
