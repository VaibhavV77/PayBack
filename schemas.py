"""
Data contracts only. This module owns every Pydantic model shared across
the graph and MUST NOT own business/policy configuration -- that lives in
policy.py. Keeping the split this way is what stops the two files from
drifting out of sync with each other (which is what happened before: a
second, contradictory POLICY_REGISTRY had been pasted in here).
"""

from datetime import datetime
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

# =====================================================================
# 1. SHARED TYPE ALIASES
# =====================================================================

RootCauseType = Literal[
    "insufficient_funds",
    "card_expired",
    "network_timeout",
    "fraud_block",
    "abandoned_checkout",
    "mandate_failed",
    "unknown",
]

ActionType = Literal[
    "silent_gateway_retry",        # Invisible to user, retry via API
    "send_whatsapp_payment_link",  # High-urgency intervention
    "send_email_payment_link",     # Low-urgency intervention
    "escalate_to_human",           # Give up and alert customer success team
    "do_nothing",                  # Leave it alone (e.g., fraud block)
]

# Lifecycle status of a case. Includes both terminal states (recovered,
# exhausted, escalated, blocked) and in-flight states, because this same
# type is used both for RecoveryState.status (case-level) and for
# AuditEntry.status (node-level, e.g. "diagnosing" logged on entry).
# "blocked" exists for a compliance-hard-stop path (see edges.py
# route_after_safety) that isn't wired into safety_engine_node yet --
# kept here so adding that path later doesn't require another schema edit.
CaseStatus = Literal[
    "detected",
    "diagnosing",
    "strategizing",
    "awaiting_approval",
    "executing",
    "recovered",
    "exhausted",
    "escalated",
    "blocked",
]

# =====================================================================
# 2. INGESTION & COMPLIANCE
# =====================================================================

class ComplianceProfile(BaseModel):
    """User-level rules fetched from DB before strategizing."""
    allowed_channels: List[str] = Field(
        default_factory=lambda: ["email"],
        description="Channels the customer can legally/contractually be contacted on, e.g. ['email', 'whatsapp']",
    )
    opted_out_channels: List[str] = Field(default_factory=list, description="e.g., ['whatsapp']")
    timezone: str = Field(default="UTC")
    is_quiet_hours: bool = Field(
        default=False,
        description="Calculated before execution based on local time (e.g., 8 PM - 8 AM)",
    )


class NormalizedFailureEvent(BaseModel):
    """Standardizes the incoming webhook payload."""
    event_id: str
    source: Literal["stripe", "razorpay", "shopify", "chargebee", "internal"]
    transaction_id: str
    customer_id: str

    # Money stored as minor units (cents/paise) to prevent float drift.
    amount_minor: int = Field(description="The transaction amount in the smallest currency unit (e.g., cents, paise)")
    currency: str = Field(max_length=3, description="ISO currency code (e.g., USD, INR)")

    raw_error_code: str
    raw_error_message: str
    occurred_at: datetime = Field(default_factory=datetime.utcnow)

# =====================================================================
# 3. LLM BOUNDARY (Strict JSON Outputs)
# =====================================================================

class DiagnoserOutput(BaseModel):
    root_cause_category: RootCauseType
    is_recoverable: bool
    confidence_score: float = Field(ge=0.0, le=1.0)
    reasoning: str


class StrategistOutput(BaseModel):
    recommended_action: ActionType
    delay_minutes: int = Field(
        default=0,
        description="Used to comply with quiet-hours or strategic delays (e.g., 30 mins for cart abandonment).",
    )
    reasoning: str

# =====================================================================
# 4. POLICY DATA CONTRACT
# =====================================================================
# NOTE: this is the *shape* of a policy only. The actual POLICY_REGISTRY
# (the data) lives in policy.py and nowhere else -- see the module
# docstring there for why that split matters.

class RecoveryPolicy(BaseModel):
    root_cause: RootCauseType
    allowed_actions: List[ActionType]
    max_retries: int
    requires_human_approval: bool = False

# =====================================================================
# 5. AUDIT TRAIL
# =====================================================================

class AuditEntry(BaseModel):
    audit_id: str
    transaction_id: str
    node: str
    status: CaseStatus
    detail: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

# =====================================================================
# 6. EXECUTION BOUNDARY & GRAPH STATE
# =====================================================================

class RecoveryOutcome(BaseModel):
    """Proves the ROI of the agent -- written when an attempt is verified."""
    transaction_id: str
    action_taken: ActionType
    payment_succeeded: bool
    amount_recovered_minor: int
    gateway_response_code: str
    attempt_number: int
    verified_at: datetime = Field(default_factory=datetime.utcnow)


class RecoveryState(BaseModel):
    """The LangGraph state passed between nodes."""
    transaction_id: str
    customer_id: str
    webhook_payload: NormalizedFailureEvent
    compliance: Optional[ComplianceProfile] = None

    status: CaseStatus = "detected"
    retry_count: int = 0

    diagnosis: Optional[DiagnoserOutput] = None
    strategy: Optional[StrategistOutput] = None
    outcome: Optional[RecoveryOutcome] = None

    execution_approved: bool = False
    execution_status: Optional[str] = None  # last gateway_response_code seen
    action_history: List[ActionType] = Field(default_factory=list)

    audit_log_ids: List[str] = Field(default_factory=list)
