from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.core.config import Settings
from app.services.handoff import (
    OpenAIHandoffSummaryModel,
    SummaryDraft,
    explicit_human_request,
    priority_for,
)


@pytest.mark.parametrize(
    "text",
    [
        "I want a human",
        "Let me speak to a real person",
        "Je veux un conseiller humain",
        "service client, s'il vous plaît",
    ],
)
def test_explicit_human_detection(text: str) -> None:
    assert explicit_human_request(text)
    assert not explicit_human_request("Where is my order?")


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("security_sensitive_account", "urgent"),
        ("payment_fraud", "urgent"),
        ("refund_manual_review", "high"),
        ("provider_failure", "high"),
        ("ambiguous_request", "low"),
        ("triage_failed", "low"),
        ("explicit_human_request", "normal"),
    ],
)
def test_deterministic_priority(reason: str, expected: str) -> None:
    assert priority_for(reason) == expected


@pytest.mark.asyncio
async def test_openai_summary_strict_schema_and_missing_key() -> None:
    with pytest.raises(RuntimeError):
        OpenAIHandoffSummaryModel(
            Settings(app_env="test", embedding_provider="fake", openai_api_key=None, _env_file=None)
        )
    parsed = SummaryDraft(
        issue_category="human_request",
        customer_summary="Customer requested human support.",
        relevant_order_refs=[],
    )
    response = Mock(output_parsed=parsed)
    client = Mock()
    client.responses.parse = AsyncMock(return_value=response)
    settings = Settings(
        app_env="test", embedding_provider="fake", openai_api_key="synthetic", _env_file=None
    )
    with patch("app.services.handoff.AsyncOpenAI", return_value=client):
        assert (
            await OpenAIHandoffSummaryModel(settings).summarize(
                ["Customer requested human support."], [], "explicit_human_request"
            )
            == parsed
        )
    response.output_parsed = None
    with patch("app.services.handoff.AsyncOpenAI", return_value=client):
        with pytest.raises(ValueError):
            await OpenAIHandoffSummaryModel(settings).summarize(["x"], [], "unsupported")
