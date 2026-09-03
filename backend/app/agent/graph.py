import re
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

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

EventEmitter = Callable[[str, dict[str, object]], Awaitable[None]]


def _latest_user(state: AgentState) -> str:
    return next(item.content for item in reversed(state.messages) if item.role == "user")


def _order_ref(message: str) -> str | None:
    match = re.search(r"\b(?:(?:NC|OO)-\d{4,10}|ord-[a-z0-9-]+)\b", message, flags=re.IGNORECASE)
    if match is None:
        return None
    value = match.group(0)
    return value.lower() if value.casefold().startswith("ord-") else value.upper()


class AgentGraph:
    def __init__(
        self,
        settings: Settings,
        triage_model: TriageModel,
        gateway: ToolGateway,
        context: ToolContext,
        emit: EventEmitter,
        checkpointer: Any,
    ) -> None:
        self.settings = settings
        self.triage_model = triage_model
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
        labels = {item.label for item in state.intents}
        selected: list[SelectedTool] = []
        if IntentLabel.KNOWLEDGE in labels:
            selected.append(
                SelectedTool(
                    name="search_knowledge_base",
                    arguments={"query": message, "locale": "en", "limit": 5},
                )
            )
        order_ref = _order_ref(message)
        if IntentLabel.ORDER_STATUS in labels:
            if order_ref is None:
                return {
                    "status": "escalation_required",
                    "escalation_reason": "missing_order_reference",
                    "step_count": state.step_count + 1,
                }
            selected.extend(
                [
                    SelectedTool(name="get_order", arguments={"order_ref": order_ref}),
                    SelectedTool(name="get_tracking", arguments={"order_ref": order_ref}),
                ]
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
        for result in state.sanitized_results:
            if result.tool == "search_knowledge_base" and result.status == "completed":
                passages = result.data.get("passages", [])
                if isinstance(passages, list) and passages:
                    parts.append(str(passages[0]))
            elif result.tool == "get_order" and result.status == "completed":
                order_ref = result.data.get("order_ref")
                status = result.data.get("status")
                fulfillment = result.data.get("fulfillment_status")
                parts.append(f"Order {order_ref} is {status} ({fulfillment}).")
            elif result.tool == "get_tracking" and result.status == "completed":
                tracking_status = result.data.get("status")
                estimate = result.data.get("estimated_delivery_at") or "not available"
                parts.append(f"Tracking status: {tracking_status}; estimated delivery: {estimate}.")
        answer = "\n\n".join(parts) or "I could not verify enough information to answer safely."
        await self.emit(
            "response_completed",
            {
                "message": answer,
                "citations": [item.model_dump(mode="json") for item in state.citations],
            },
        )
        return {
            "messages": [*state.messages, VisibleMessage(role="assistant", content=answer)],
            "status": "completed",
            "step_count": state.step_count + 1,
        }
