import re
import sys
from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.address_parser import extract_proposed_address
from app.agent.answers import AnswerModel
from app.agent.state import (
    AgentState,
    CitationRef,
    IntentLabel,
    IntentScore,
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
from app.services.address_actions import AddressActionError, AddressActionService
from app.services.handoff import explicit_human_request
from app.services.refunds import (
    RefundError,
    RefundOutcome,
    RefundService,
    extract_refund_order_number,
    parse_refund_intent,
)
from app.services.sales_leads import SalesLeadError, SalesLeadService, genuine_sales_intent

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
        message = self.context.request_message or _latest_user(state)
        lowered = message.casefold()
        deterministic_reason = (
            "explicit_human_request"
            if explicit_human_request(message)
            else "security_sensitive_account"
            if re.search(
                r"\b(account hacked|account takeover|stolen card|payment card|fraud|"
                r"compte pirat[ée]|carte vol[ée]e|fraude)\b",
                lowered,
            )
            else None
        )
        if deterministic_reason:
            await self.emit("escalation_required", {"reason": deterministic_reason})
            return {
                "intents": [IntentScore(label=IntentLabel.HUMAN_HELP, confidence=1.0)],
                "confidence": 1.0,
                "risk_level": RiskLevel.ESCALATION,
                "step_count": state.step_count + 1,
                "status": "escalation_required",
                "escalation_reason": deterministic_reason,
            }
        try:
            raw = await self.triage_model.classify(
                self.context.request_message or _latest_user(state)
            )
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
        message = self.context.request_message or _latest_user(state)
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
        if IntentLabel.ACCOUNT_CHANGE in labels:
            if order_ref is None:
                return {
                    "status": "clarification_required",
                    "escalation_reason": "missing_or_ambiguous_order_number",
                    "step_count": state.step_count + 1,
                }
            try:
                address = extract_proposed_address(message)
            except AddressActionError:
                return {
                    "status": "clarification_required",
                    "escalation_reason": "missing_or_ambiguous_address",
                    "step_count": state.step_count + 1,
                }
            if not all(
                (
                    self.context.customer_id,
                    self.context.session_id,
                    self.context.conversation_id,
                    self.context.run_id,
                )
            ):
                return {"status": "failed", "escalation_reason": "trusted_context_missing"}
            customer_id = cast(UUID, self.context.customer_id)
            session_id = cast(UUID, self.context.session_id)
            conversation_id = cast(UUID, self.context.conversation_id)
            run_id = cast(UUID, self.context.run_id)
            try:
                proposal = await AddressActionService(
                    self.context.session, self.settings, self.context.commerce
                ).propose(
                    organization_id=self.context.organization_id,
                    customer_id=customer_id,
                    customer_ref=self.context.customer_ref,
                    session_id=session_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    order_number=order_ref,
                    proposed_address=address,
                )
            except AddressActionError as exc:
                return {
                    "status": "failed",
                    "escalation_reason": exc.code,
                    "step_count": state.step_count + 1,
                }
            self.context.address_proposal = proposal
            await self.emit("confirmation_required", {"status": "confirmation_required"})
            return {
                "status": "confirmation_required",
                "pending_action_id": str(proposal.action_id),
                "pending_action_hash": proposal.action_hash,
                "step_count": state.step_count + 1,
            }
        if IntentLabel.REFUND in labels:
            order_ref = extract_refund_order_number(message, order_ref)
            if not all(
                (
                    self.context.customer_id,
                    self.context.session_id,
                    self.context.conversation_id,
                    self.context.run_id,
                )
            ):
                return {
                    "selected_tools": [
                        SelectedTool(
                            name="create_refund_request",
                            arguments={"request_summary": message[:500]},
                        )
                    ],
                    "step_count": state.step_count + 1,
                }
            try:
                refund_intent = parse_refund_intent(message, order_ref)
            except RefundError as exc:
                return {
                    "status": "clarification_required",
                    "escalation_reason": exc.code,
                    "step_count": state.step_count + 1,
                }
            try:
                refund_proposal = await RefundService(
                    self.context.session, self.settings, self.context.commerce
                ).propose(
                    organization_id=self.context.organization_id,
                    customer_id=cast(UUID, self.context.customer_id),
                    customer_ref=self.context.customer_ref,
                    session_id=cast(UUID, self.context.session_id),
                    conversation_id=cast(UUID, self.context.conversation_id),
                    run_id=cast(UUID, self.context.run_id),
                    intent=refund_intent,
                    locale=locale,
                )
            except RefundError as exc:
                return {
                    "status": "failed",
                    "escalation_reason": exc.code,
                    "step_count": state.step_count + 1,
                }
            self.context.refund_proposal = refund_proposal
            citations = [CitationRef.model_validate(item) for item in refund_proposal.citations]
            result = SanitizedResult(
                tool="refund_eligibility",
                status="completed",
                data={
                    "outcome": refund_proposal.outcome.value,
                    "reason_codes": list(refund_proposal.reason_codes),
                    "order_number": refund_proposal.order_number,
                    "policy_version": refund_proposal.policy_version,
                    "ruleset_version": refund_proposal.ruleset_version,
                },
            )
            if refund_proposal.outcome == RefundOutcome.ELIGIBLE:
                await self.emit("confirmation_required", {"status": "confirmation_required"})
                return {
                    "status": "confirmation_required",
                    "pending_action_id": str(refund_proposal.action_id),
                    "pending_action_hash": refund_proposal.action_hash,
                    "sanitized_results": [result],
                    "citations": citations,
                    "step_count": state.step_count + 1,
                }
            if refund_proposal.outcome == RefundOutcome.MANUAL_REVIEW_REQUIRED:
                await self.emit("escalation_required", {"reason": "refund_manual_review"})
                return {
                    "sanitized_results": [result],
                    "citations": citations,
                    "status": "escalation_required",
                    "escalation_reason": "refund_manual_review",
                    "step_count": state.step_count + 1,
                }
            return {
                "sanitized_results": [result],
                "citations": citations,
                "step_count": state.step_count + 1,
            }
        if IntentLabel.SALES_LEAD in labels:
            if not genuine_sales_intent(message):
                labels.remove(IntentLabel.SALES_LEAD)
            elif not all(
                (
                    self.context.customer_id,
                    self.context.session_id,
                    self.context.conversation_id,
                    self.context.run_id,
                    self.context.verified_name,
                    self.context.verified_email,
                )
            ):
                return {"status": "failed", "escalation_reason": "trusted_context_missing"}
            elif self.context.lead_extractor is None:
                return {"status": "failed", "escalation_reason": "lead_extractor_unavailable"}
            else:
                try:
                    extracted = await self.context.lead_extractor.extract(message)
                    sales_proposal = await SalesLeadService(
                        self.context.session, self.settings, self.context.crm
                    ).propose(
                        organization_id=self.context.organization_id,
                        customer_id=cast(UUID, self.context.customer_id),
                        session_id=cast(UUID, self.context.session_id),
                        conversation_id=cast(UUID, self.context.conversation_id),
                        run_id=cast(UUID, self.context.run_id),
                        verified_name=cast(str, self.context.verified_name),
                        verified_email=cast(str, self.context.verified_email),
                        fields=extracted,
                    )
                except SalesLeadError as exc:
                    return {
                        "status": "clarification_required",
                        "escalation_reason": exc.code,
                        "step_count": state.step_count + 1,
                    }
                except Exception:
                    return {
                        "status": "failed",
                        "escalation_reason": "lead_extraction_failed",
                        "step_count": state.step_count + 1,
                    }
                self.context.lead_proposal = sales_proposal
                await self.emit(
                    "confirmation_required",
                    {"status": "confirmation_required", "action_type": "sales_lead"},
                )
                combined: dict[str, object] = {}
                if selected:
                    tool_update = await self._execute_tools(
                        state.model_copy(update={"selected_tools": selected})
                    )
                    if tool_update.get("status") != "escalation_required":
                        composed = await self._compose(state.model_copy(update=tool_update))
                        combined = {
                            key: composed[key]
                            for key in ("messages", "citations", "sanitized_results")
                            if key in composed
                        }
                return {
                    **combined,
                    "status": "confirmation_required",
                    "pending_action_id": str(sales_proposal.action_id),
                    "pending_action_hash": sales_proposal.action_hash,
                    "step_count": state.step_count + 1,
                }
        if labels & {IntentLabel.HUMAN_HELP, IntentLabel.UNSUPPORTED}:
            return {
                "selected_tools": selected,
                "status": "escalation_required",
                "escalation_reason": "human_or_unsupported",
                "step_count": state.step_count + 1,
            }
        return {"selected_tools": selected, "step_count": state.step_count + 1}

    def _after_plan(self, state: AgentState) -> str:
        if state.status in {"confirmation_required", "escalation_required"}:
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
        if state.status == "failed":
            reason = state.escalation_reason or "action_failed"
            await self.emit("action_failed", {"status": "action_failed", "reason": reason})
            return {"status": "failed", "step_count": state.step_count + 1}
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
            address_missing = state.escalation_reason == "missing_or_ambiguous_address"
            refund_missing = (state.escalation_reason or "").startswith("missing_")
            sales_missing = state.escalation_reason in {
                "missing_lead_information",
                "missing_or_invalid_contact_method",
                "invalid_budget_range",
                "invalid_timeline",
            }
            if sales_missing:
                parts.append(
                    "Veuillez fournir l’entreprise, le produit/service concerné, un bref besoin "
                    "professionnel et le moyen de contact préféré (e-mail, téléphone ou "
                    "appel vidéo). "
                    "Le budget et le calendrier sont facultatifs."
                    if locale == "fr"
                    else "Please provide the company, product/service interest, a short business "
                    "need, and preferred contact method (email, phone, or video call). Budget and "
                    "timeline are optional."
                )
            elif refund_missing:
                missing = (
                    (state.escalation_reason or "missing_information")
                    .removeprefix("missing_")
                    .replace("_", ", ")
                )
                parts.append(
                    f"Veuillez préciser : {missing}. Utilisez les libellés motif, "
                    "article/SKU, quantité, montant et devise."
                    if locale == "fr"
                    else f"Please provide: {missing}. Use the labels reason, item/SKU, "
                    "quantity, amount, and currency."
                )
            elif locale == "fr":
                parts.append(
                    "Veuillez fournir le destinataire, l’adresse (ligne 1), la ville, "
                    "la région/province, le code postal et le code pays ISO à deux lettres."
                    if address_missing
                    else "Veuillez fournir un seul numéro de commande public."
                )
            else:
                parts.append(
                    "Please provide recipient, address line 1, city, state/region, "
                    "postal code, and a two-letter ISO country code."
                    if address_missing
                    else "Please provide one public order number."
                )
        refund = next(
            (item for item in state.sanitized_results if item.tool == "refund_eligibility"), None
        )
        if refund is not None:
            for ref in state.citations:
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
                if valid:
                    validated_refs.append(ref)
            if not validated_refs:
                return await self._safe_failure(state, locale, "refund_citation_invalid")
            data = refund.data
            outcome = str(data.get("outcome"))
            raw_reasons = data.get("reason_codes", [])
            reason_values = raw_reasons if isinstance(raw_reasons, list) else []
            reasons = ", ".join(str(value) for value in reason_values)
            citation = validated_refs[0]
            marker = f" [{citation.receipt_id}]" if citation else ""
            if locale == "fr":
                wording = {
                    "ineligible": "La demande de remboursement n’est pas admissible",
                    "manual_review_required": "Une vérification manuelle est nécessaire",
                }.get(outcome, "La demande est admissible")
                parts.append(
                    f"## Admissibilité au remboursement\n\n{wording} ({reasons}). "
                    f"Politique {data.get('policy_version')}.{marker}"
                )
            else:
                wording = {
                    "ineligible": "The refund request is ineligible",
                    "manual_review_required": "Manual review is required",
                }.get(outcome, "The request is eligible")
                parts.append(
                    f"## Refund eligibility\n\n{wording} ({reasons}). "
                    f"Policy {data.get('policy_version')}.{marker}"
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
