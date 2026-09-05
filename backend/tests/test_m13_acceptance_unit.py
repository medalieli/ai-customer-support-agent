from typing import Literal
from uuid import UUID

import pytest
from fastapi import Response

from app.agent.answers import OpenAIAnswerModel
from app.agent.deterministic import (
    DeterministicAnswerModel,
    DeterministicHandoffSummaryModel,
    DeterministicLeadExtractor,
    DeterministicTriageModel,
)
from app.agent.triage import OpenAITriageModel
from app.api.v1.auth import current_identity, set_session_cookie
from app.api.v1.tickets import _audit_category
from app.core.config import Settings
from app.domain.models import Role
from app.services.auth import Principal


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("tool.completed", "tool"),
        ("address_change.approved", "confirmation"),
        ("refund.submitted", "confirmation"),
        ("sales_lead.consented", "crm"),
        ("crm.write", "crm"),
        ("handoff.created", "escalation"),
        ("ticket.resolved", "ticket"),
        ("staff.replied", "staff"),
        ("webhook.processed", "webhook"),
        ("agent.resumed", "ai"),
        ("ai.resumed", "ai"),
        ("agent.triage_completed", "triage"),
        ("agent.tool_completed", "tool"),
        ("conversation.create", "system"),
    ],
)
def test_audit_categories_are_safe_fixed_labels(action: str, expected: str) -> None:
    assert _audit_category(action) == expected


def test_deterministic_agent_is_refused_outside_test() -> None:
    with pytest.raises(ValueError, match="allowed only in test"):
        Settings(app_env="development", agent_provider="deterministic")


def test_browser_session_cookie_is_http_only_and_same_site() -> None:
    response = Response()
    set_session_cookie(response, "synthetic-session", Settings(app_env="test"))
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [None, Role.SUPPORT])
async def test_identity_response_safely_projects_optional_role(role: Role | None) -> None:
    identifier = UUID("10000000-0000-0000-0000-000000000001")
    result = await current_identity(
        Principal(
            kind="customer" if role is None else "staff",
            organization_id=identifier,
            subject_id=identifier,
            role=role,
            session_id=identifier,
        )
    )
    assert result.role == (role.value if role else None)


def test_openai_model_clients_are_configured_without_live_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = object()
    monkeypatch.setattr("app.agent.answers.AsyncOpenAI", lambda **kwargs: marker)
    monkeypatch.setattr("app.agent.triage.AsyncOpenAI", lambda **kwargs: marker)
    settings = Settings(app_env="test", openai_api_key="synthetic-openai-key")
    assert OpenAIAnswerModel(settings).client is marker
    assert OpenAITriageModel(settings).client is marker


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["human", "address", "refund", "sales", "order", "policy"])
async def test_explicit_test_models(message: str) -> None:
    settings = Settings(app_env="test")
    assert (await DeterministicTriageModel(settings).classify(message)).intents
    locales: tuple[Literal["en", "fr"], ...] = ("en", "fr")
    for locale in locales:
        assert not (await DeterministicAnswerModel(settings).answer(message, locale, [])).supported
        answer = await DeterministicAnswerModel(settings).answer(
            message, locale, [{"receipt_id": "receipt", "snippet": "Synthetic policy"}]
        )
        assert answer.citation_receipt_ids == ["receipt"]
    assert (
        await DeterministicHandoffSummaryModel(settings).summarize([message], [], "human")
    ).customer_summary == message
    assert (
        await DeterministicLeadExtractor(settings).extract(message)
    ).preferred_contact_method == "email"


@pytest.mark.parametrize(
    "model",
    [
        DeterministicAnswerModel,
        DeterministicTriageModel,
        DeterministicHandoffSummaryModel,
        DeterministicLeadExtractor,
    ],
)
def test_model_constructors_reject_demo(model: type) -> None:
    with pytest.raises(RuntimeError, match="test-only"):
        model(Settings(app_env="development"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "environment, enabled, confirmation",
    [
        ("production", True, True),
        ("test", True, True),
        ("development", False, True),
        ("development", True, False),
        ("development", True, True),
    ],
)
async def test_demo_reset_guards(
    environment: str, enabled: bool, confirmation: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import demo_reset

    settings = Settings(app_env="test").model_copy(
        update={
            "app_env": environment,
            "demo_auth_enabled": enabled,
        }
    )
    monkeypatch.setattr(demo_reset, "get_settings", lambda: settings)
    monkeypatch.setenv(
        "NOVACART_CONFIRM_DEMO_RESET", "RESET_SYNTHETIC_NOVACART" if confirmation else ""
    )
    with pytest.raises(RuntimeError):
        await demo_reset.main()
