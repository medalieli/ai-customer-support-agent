"""Deterministic model substitutes allowed only by explicit test configuration."""

import re
from typing import Literal

from app.agent.answers import GroundedAnswer
from app.agent.state import IntentLabel, IntentScore
from app.agent.triage import TriageOutput
from app.core.config import Settings
from app.services.handoff import SummaryDraft
from app.services.sales_leads import LeadFields


class DeterministicTriageModel:
    def __init__(self, settings: Settings) -> None:
        if settings.app_env != "test":
            raise RuntimeError("deterministic agent models are test-only")

    async def classify(self, message: str) -> TriageOutput:
        text = message.casefold()
        address = any(word in text for word in ("address", "adress", "addres", "adresse"))
        address_question = address and bool(
            re.search(r"\b(can|could|may|possible|allowed|policy|puis-je|peut-on)\b", text)
        )
        if re.fullmatch(r"\s*(?:hi|hello|hey|thanks|thank you|bonjour|salut|merci)[!.?\s]*", text):
            label = IntentLabel.SMALL_TALK
        elif any(
            word in text
            for word in (
                "weather",
                "bitcoin",
                "recipe",
                "météo",
                "recette",
                "python",
                "amazon",
                "system prompt",
                "internal instructions",
                "capital of",
            )
        ):
            label = IntentLabel.UNSUPPORTED
        elif any(
            word in text
            for word in (
                "human",
                "representative",
                "someone",
                "somebody",
                "real person",
                "humain",
                "conseiller",
                "manager",
                "agent du support",
                "support agent",
            )
        ):
            label = IntentLabel.HUMAN_HELP
        elif address_question:
            label = IntentLabel.KNOWLEDGE
        elif address:
            label = IntentLabel.ACCOUNT_CHANGE
        elif any(word in text for word in ("policy", "politique", "warranty", "garantie")):
            label = IntentLabel.KNOWLEDGE
        elif any(word in text for word in ("refund", "rembours", "money back", "return the")):
            label = IntentLabel.REFUND
        elif any(word in text for word in ("sales", "enterprise", "demo", "devis", "commercial")):
            label = IntentLabel.SALES_LEAD
        elif re.search(r"\bnc-\d+\b", text) or any(
            word in text for word in ("order", "track", "commande", "suivi")
        ):
            label = IntentLabel.ORDER_STATUS
        elif any(word in text for word in ("weather", "bitcoin", "recipe", "météo", "recette")):
            label = IntentLabel.UNSUPPORTED
        else:
            label = IntentLabel.KNOWLEDGE
        return TriageOutput(intents=[IntentScore(label=label, confidence=0.99)])


class DeterministicAnswerModel:
    def __init__(self, settings: Settings) -> None:
        if settings.app_env != "test":
            raise RuntimeError("deterministic agent models are test-only")

    async def answer(
        self, question: str, language: Literal["en", "fr"], evidence: list[dict[str, str]]
    ) -> GroundedAnswer:
        if not evidence:
            return GroundedAnswer(
                supported=False,
                answer=(
                    "Informations insuffisantes."
                    if language == "fr"
                    else "Insufficient information."
                ),
            )
        passage = evidence[0].get("text") or evidence[0].get("snippet")
        if not passage:
            passage = (
                "Les informations de politique sont disponibles."
                if language == "fr"
                else "Policy information is available."
            )
        sentences = re.split(r"(?<=[.!?])\s+", passage.strip())
        concise = " ".join(sentences[:2])[:500]
        prefix = (
            "Voici la règle applicable : " if language == "fr" else "Here’s the relevant policy: "
        )
        return GroundedAnswer(
            supported=True,
            answer=prefix + concise,
            citation_receipt_ids=[evidence[0]["receipt_id"]],
        )


class DeterministicHandoffSummaryModel:
    def __init__(self, settings: Settings) -> None:
        if settings.app_env != "test":
            raise RuntimeError("deterministic agent models are test-only")

    async def summarize(
        self, visible_messages: list[str], allowed_order_refs: list[str], reason_code: str
    ) -> SummaryDraft:
        return SummaryDraft(
            issue_category=reason_code,
            customer_summary=visible_messages[-1][:1200],
            relevant_order_refs=allowed_order_refs[:10],
        )


class DeterministicLeadExtractor:
    def __init__(self, settings: Settings) -> None:
        if settings.app_env != "test":
            raise RuntimeError("deterministic agent models are test-only")

    async def extract(self, message: str) -> LeadFields:
        del message
        return LeadFields(
            company="E2E Company",
            interest="NovaCart enterprise",
            business_need="Deterministic browser acceptance test",
            budget_range="unspecified",
            timeline="1_3_months",
            preferred_contact_method="email",
        )
