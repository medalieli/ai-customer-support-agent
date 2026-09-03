import re
import sys
from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.answers import AnswerModel
from app.agent.state import (
    AgentState,
    IntentLabel,
    RiskLevel,
    SanitizedResult,
    SelectedTool,
    VisibleMessage,
)
from app.agent.tools import ToolContext, ToolGateway
from app.agent.triage import TriageModel, deterministic_risk, validate_triage
from app.core.config import Settings
from app.knowledge.retrieval import Citation, validate_citation
from app.providers.order_numbers import extract_order_number

EventEmitter = Callable[[str, dict[str, object]], Awaitable[None]]


def _latest_user(state: AgentState) -> str:
    return next(item.content for item in reversed(state.messages) if item.role == "user")


def _language(message: str, fallback: str) -> str:
    lowered = message.casefold()
    french = {
        "où",
        "quel",
        "quelle",
        "commande",
        "livraison",
        "retour",
        "politique",
        "puis-je",
        "remboursement",
    }
    if any(token in lowered for token in french):
        return "fr"
    return fallback if fallback in {"en", "fr"} else "en"


def _knowledge_query(message: str) -> str:
    parts = re.split(r"\s+(?:and|et)\s+|[,;]", message, flags=re.IGNORECASE)
    knowledge_parts = [
        part.strip()
        for part in parts
        if part.strip()
        and not (
            extract_order_number(part)
            or re.search(r"\b(?:order|commande|track|suivi)\b", part, re.IGNORECASE)
        )
    ]
    return " ".join(knowledge_parts) or message


def _persistable_results(state: AgentState, receipt_ids: list[str]) -> list[SanitizedResult]:
    values: list[SanitizedResult] = []
    for result in state.sanitized_results:
        if result.tool == "search_knowledge_base" and result.status == "completed":
            values.append(
                SanitizedResult(
                    tool=result.tool,
                    status=result.status,
                    data={"validated_receipt_ids": receipt_ids},
                )
            )
        else:
            values.append(result)
    return values


class AgentGraph:
    def __init__(
        self,
        settings: Settings,
        triage_model: TriageModel,
        answer_model: AnswerModel,
        gateway: ToolGateway,
        context: ToolContext,
        emit: EventEmitter,
        checkpointer: Any,
    ) -> None:
        self.settings = settings
        self.triage_model = triage_model
        self.answer_model = answer_model
        self.gateway = gateway
        self.context = context
        self.emit = emit
        builder = StateGraph(AgentState)
        builder.add_node("triage", self._triage)
        builder.add_node("plan", self._plan)
        builder.add_node("execute_tools", self._execute_tools)
        builder.add_node("interrupt", self._interrupt)
        builder.add_node("compose", self._compose)
        builder.add_edge(START, "triage")
        builder.add_edge("triage", "plan")
        builder.add_conditional_edges(
            "plan",
            self._after_plan,
            {"tools": "execute_tools", "interrupt": "interrupt", "compose": "compose"},
        )
        builder.add_conditional_edges(
            "execute_tools", self._after_tools, {"interrupt": "interrupt", "compose": "compose"}
        )
        builder.add_edge("interrupt", END)
        builder.add_edge("compose", END)
        self.compiled = builder.compile(checkpointer=checkpointer)

    def _bounded(self, state: AgentState) -> bool:
        return state.step_count >= self.settings.agent_max_steps

    async def _triage(self, state: AgentState) -> dict[str, object]:
        if self._bounded(state):
            return {"status": "escalation_required", "escalation_reason": "maximum_steps"}
        try:
            raw = await self.triage_model.classify(_latest_user(state))
            result = validate_triage(raw, self.settings.agent_min_confidence)
        except Exception:
            await self.emit("escalation_required", {"reason": "triage_failed"})
            return {
                "intents": [],
                "confidence": 0.0,
                "risk_level": RiskLevel.ESCALATION,
                "step_count": state.step_count + 1,
                "status": "escalation_required",
                "escalation_reason": "triage_failed",
            }
        confidence = min(item.confidence for item in result.intents)
        risk = RiskLevel(deterministic_risk(result.intents))
        await self.emit(
            "triage_completed",
            {
                "intents": [item.label.value for item in result.intents],
                "confidence": confidence,
                "risk_level": risk.value,
            },
        )
        return {
            "intents": result.intents,
            "confidence": confidence,
            "risk_level": risk,
            "step_count": state.step_count + 1,
        }

    async def _plan(self, state: AgentState) -> dict[str, object]:
        if state.status == "escalation_required" or self._bounded(state):
            return {
                "status": "escalation_required",
                "escalation_reason": state.escalation_reason or "maximum_steps",
            }
        message = _latest_user(state)
        locale = _language(message, self.context.locale)
        labels = {item.label for item in state.intents}
        selected: list[SelectedTool] = []
        if IntentLabel.KNOWLEDGE in labels:
            selected.append(
                SelectedTool(
                    name="search_knowledge_base",
                    arguments={"query": _knowledge_query(message), "locale": locale, "limit": 5},
                )
            )
        order_ref = extract_order_number(message)
        if IntentLabel.ORDER_STATUS in labels:
            if order_ref is None:
                return {
                    "status": "clarification_required",
                    "escalation_reason": "missing_or_ambiguous_order_number",
                    "step_count": state.step_count + 1,
                }
            selected.append(
                SelectedTool(name="get_order_status", arguments={"order_number": order_ref})
            )
        write_map = {
            IntentLabel.ACCOUNT_CHANGE: "propose_shipping_address_change",
            IntentLabel.REFUND: "create_refund_request",
            IntentLabel.SALES_LEAD: "upsert_sales_lead",
        }
        for label, name in write_map.items():
            if label in labels:
                selected.append(
                    SelectedTool(name=name, arguments={"request_summary": message[:500]})
                )
        if labels & {IntentLabel.HUMAN_HELP, IntentLabel.UNSUPPORTED}:
            return {
                "selected_tools": selected,
                "status": "escalation_required",
                "escalation_reason": "human_or_unsupported",
                "step_count": state.step_count + 1,
            }
        return {"selected_tools": selected, "step_count": state.step_count + 1}

    def _after_plan(self, state: AgentState) -> str:
        if state.status == "escalation_required":
            return "interrupt"
        if state.status == "clarification_required":
            return "compose"
        return "tools" if state.selected_tools else "compose"

    async def _execute_tools(self, state: AgentState) -> dict[str, object]:
        results = list(state.sanitized_results)
        citations = list(state.citations)
        seen: set[tuple[str, str]] = set()
        for selected in state.selected_tools:
            signature = (selected.name, repr(sorted(selected.arguments.items())))
            if signature in seen:
                results.append(
                    SanitizedResult(
                        tool=selected.name, status="failed", error_code="repeated_tool_call"
                    )
                )
                return {
                    "sanitized_results": results,
                    "citations": citations,
                    "status": "escalation_required",
                    "escalation_reason": "repeated_tool_call",
                }
            seen.add(signature)
            if state.step_count + len(results) >= self.settings.agent_max_steps:
                return {
                    "sanitized_results": results,
                    "citations": citations,
                    "status": "escalation_required",
                    "escalation_reason": "maximum_steps",
                }
            await self.emit("tool_started", {"tool": selected.name})
            result, refs = await self.gateway.execute(
                selected.name, selected.arguments, self.context
            )
            results.append(result)
            citations.extend(refs)
            await self.emit(
                "tool_completed",
                {"tool": selected.name, "status": result.status, "error_code": result.error_code},
            )
        if any(item.error_code == "confirmation_required" for item in results):
            await self.emit("confirmation_required", {"reason": "guarded_write"})
            return {
                "sanitized_results": results,
                "citations": citations,
                "step_count": state.step_count + len(state.selected_tools),
                "status": "confirmation_required",
            }
        if any(item.status == "failed" for item in results):
            return {
                "sanitized_results": results,
                "citations": citations,
                "step_count": state.step_count + len(state.selected_tools),
                "status": "escalation_required",
                "escalation_reason": "tool_failure",
            }
        return {
            "sanitized_results": results,
            "citations": citations,
            "step_count": state.step_count + len(state.selected_tools),
        }

    def _after_tools(self, state: AgentState) -> str:
        return (
            "interrupt"
            if state.status in {"confirmation_required", "escalation_required"}
            else "compose"
        )

    def _interrupt(self, state: AgentState) -> dict[str, object]:
        reason = state.escalation_reason or "confirmation_required"
        # Context variables propagate through async nodes only on Python 3.11+.
        # Python 3.10 still persists the explicit interrupted status/checkpoint.
        if sys.version_info >= (3, 11):
            interrupt({"reason": reason, "status": state.status})
        return {}

    async def _compose(self, state: AgentState) -> dict[str, object]:
        parts: list[str] = []
        locale = cast(Literal["en", "fr"], _language(_latest_user(state), self.context.locale))
        validated_refs = []
        knowledge = next(
            (
                item
                for item in state.sanitized_results
                if item.tool == "search_knowledge_base" and item.status == "completed"
            ),
            None,
        )
        if knowledge is not None:
            passages = knowledge.data.get("passages", [])
            if not isinstance(passages, list) or not passages or not state.citations:
                return await self._safe_failure(state, locale, "insufficient_evidence")
            try:
                grounded = await self.answer_model.answer(
                    _knowledge_query(_latest_user(state)),
                    locale,
                    [dict(item) for item in passages if isinstance(item, dict)],
                )
                by_id = {item.receipt_id: item for item in state.citations}
                if (
                    not grounded.supported
                    or not grounded.citation_receipt_ids
                    or any(receipt_id not in by_id for receipt_id in grounded.citation_receipt_ids)
                ):
                    raise ValueError("citation_not_supplied")
                for receipt_id in dict.fromkeys(grounded.citation_receipt_ids):
                    ref = by_id[receipt_id]
                    valid = await validate_citation(
                        self.context.session,
                        self.context.organization_id,
                        Citation(
                            record_id=UUID(ref.receipt_id),
                            document_id=UUID(ref.document_id),
                            version_id=UUID(ref.version_id),
                            chunk_id=UUID(ref.chunk_id),
                            source_title=ref.title,
                            language=ref.language,
                            section=ref.section,
                            page=ref.page,
                            snippet=ref.snippet,
                        ),
                    )
                    if not valid:
                        raise ValueError("citation_invalid")
                    validated_refs.append(ref)
                forbidden = (
                    "i updated",
                    "i refunded",
                    "crm record created",
                    "j'ai modifié",
                    "j'ai remboursé",
                )
                if any(term in grounded.answer.casefold() for term in forbidden):
                    raise ValueError("action_claim")
                heading = (
                    "Policy information" if locale == "en" else "Informations sur la politique"
                )
                parts.append(f"## {heading}\n\n{grounded.answer}")
            except Exception:
                return await self._safe_failure(state, locale, "grounding_failed")
        for result in state.sanitized_results:
            if result.tool == "get_order_status" and result.status == "completed":
                data = result.data
                retrieved = data.get("retrieved_at")
                number = data.get("order_number")
                status = data.get("status")
                fulfillment = data.get("fulfillment_status")
                tracking = data.get("tracking_number") or data.get("tracking_url")
                if locale == "fr":
                    order = (
                        "## Statut de la commande\n\nDonnées commerciales en direct "
                        f"récupérées à {retrieved}. Commande {number} : statut {status}, "
                        f"traitement {fulfillment}."
                    )
                    if data.get("carrier"):
                        order += f" Transporteur : {data.get('carrier')}. Suivi : {tracking}."
                    if data.get("estimated_delivery_at"):
                        order += f" Livraison estimée : {data.get('estimated_delivery_at')}."
                else:
                    order = (
                        f"## Order status\n\nLive commerce data retrieved at {retrieved}. "
                        f"Order {number}: status {status}, fulfillment {fulfillment}."
                    )
                    if data.get("carrier"):
                        order += f" Carrier: {data.get('carrier')}. Tracking: {tracking}."
                    if data.get("estimated_delivery_at"):
                        order += f" Estimated delivery: {data.get('estimated_delivery_at')}."
                parts.append(order)
        if state.status == "clarification_required":
            parts.append(
                "Veuillez fournir un seul numéro de commande public."
                if locale == "fr"
                else "Please provide one public order number."
            )
        answer = "\n\n".join(parts) or (
            "Je ne peux pas vérifier suffisamment d’informations pour répondre."
            if locale == "fr"
            else "I could not verify enough information to answer safely."
        )
        await self.emit(
            "response_completed",
            {
                "message": answer,
                "citations": [item.model_dump(mode="json") for item in validated_refs],
            },
        )
        return {
            "messages": [*state.messages, VisibleMessage(role="assistant", content=answer)],
            "citations": validated_refs,
            "sanitized_results": _persistable_results(
                state, [item.receipt_id for item in validated_refs]
            ),
            "status": "completed"
            if state.status != "clarification_required"
            else "clarification_required",
            "step_count": state.step_count + 1,
        }

    async def _safe_failure(self, state: AgentState, locale: str, reason: str) -> dict[str, object]:
        answer = (
            "Je n’ai pas assez de preuves validées pour répondre de façon fiable. "
            "Une assistance humaine est nécessaire."
            if locale == "fr"
            else "I do not have enough validated evidence to answer reliably. "
            "Human help is required."
        )
        await self.emit("escalation_required", {"reason": reason})
        await self.emit("response_completed", {"message": answer, "citations": []})
        return {
            "messages": [*state.messages, VisibleMessage(role="assistant", content=answer)],
            "citations": [],
            "sanitized_results": _persistable_results(state, []),
            "status": "escalation_required",
            "escalation_reason": reason,
            "step_count": state.step_count + 1,
        }
