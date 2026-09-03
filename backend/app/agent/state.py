from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IntentLabel(str, Enum):
    KNOWLEDGE = "knowledge_question"
    ORDER_STATUS = "order_status"
    ACCOUNT_CHANGE = "account_address_change"
    REFUND = "refund_request"
    SALES_LEAD = "sales_lead"
    HUMAN_HELP = "human_help"
    UNSUPPORTED = "unsupported_uncertain"


class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    WRITE = "write"
    SENSITIVE_WRITE = "sensitive_write"
    ESCALATION = "escalation"


class VisibleMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class IntentScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: IntentLabel
    confidence: float = Field(ge=0, le=1)


class SelectedTool(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    arguments: dict[str, object] = Field(default_factory=dict)


class SanitizedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    status: Literal["completed", "blocked", "failed"]
    data: dict[str, object] = Field(default_factory=dict)
    error_code: str | None = None


class CitationRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt_id: str
    title: str
    snippet: str


class AgentState(BaseModel):
    """Persistable operational state. It intentionally has no reasoning/scratchpad fields."""

    model_config = ConfigDict(extra="forbid")
    thread_id: str
    run_id: str
    messages: list[VisibleMessage] = Field(default_factory=list, max_length=40)
    intents: list[IntentScore] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    selected_tools: list[SelectedTool] = Field(default_factory=list)
    sanitized_results: list[SanitizedResult] = Field(default_factory=list)
    citations: list[CitationRef] = Field(default_factory=list)
    step_count: int = Field(default=0, ge=0)
    status: Literal[
        "running", "completed", "confirmation_required", "escalation_required", "failed"
    ] = "running"
    escalation_reason: str | None = None
