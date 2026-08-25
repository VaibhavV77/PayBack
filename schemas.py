from datetime import datetime
from typing import Literal, Dict, List, Optional
from pydantic import BaseModel, Field

# =====================================================================
# 1. SHARED TYPE ALIASES (Fixing Gap #5)
# =====================================================================
# Extracting these prevents typos in the registry and keeps the LLM bounded.

RootCauseType = Literal[
    "insufficient_funds", 
    "card_expired", 
    "network_timeout", 
    "fraud_block", 
    "abandoned_checkout",
    "mandate_failed",
    "unknown"
]

ActionType = Literal[
    "silent_gateway_retry",        # Invisible to user, retry via API
    "send_whatsapp_payment_link",  # High-urgency intervention
    "send_email_payment_link",     # Low-urgency intervention
    "escalate_to_human",           # Give up and alert customer success team
    "do_nothing"                   # Leave it alone (e.g., fraud block)
]

CaseStatusType = Literal[
    "detected", 
    "diagnosing", 
    "strategizing", 
    "awaiting_approval", 
    "executing", 
    "recovered", 
    "exhausted", 
    "escalated"
]

# =====================================================================
# 2. INGESTION & COMPLIANCE (Fixing Gaps #2 and #7)
# =====================================================================

class ComplianceProfile(BaseModel):
    """
    User-level rules fetched from DB before strategizing.
    Enforces 'compliant escalation' and quiet hours.
    """
    opted_out_channels: List[str] = Field(default_factory=list, description="e.g., ['whatsapp']")
    timezone: str = Field(default="UTC")
    is_quiet_hours: bool = Field(default=False, description="Calculated before execution based on local time (e.g., 8 PM - 8 AM)")

class NormalizedFailureEvent(BaseModel):
    """
    Standardizes the incoming webhook payload.
    """
    event_id: str
    source: Literal["stripe", "razorpay", "shopify", "chargebee", "internal"]
    transaction_id: str
    customer_id: str
    
    # GAP #2 FIXED: Storing money as minor units (cents/paise) to prevent float drift
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
        description="Used to comply with quiet-hours or strategic delays (e.g., 30 mins for cart abandonment)."
    )
    reasoning: str

# =====================================================================
# 4. EXECUTION BOUNDARY & POLICIES (Fixing Gap #3)
# =====================================================================

class RecoveryPolicy(BaseModel):
    root_cause: RootCauseType
    allowed_actions: List[ActionType]
    max_retries: int
    requires_human_approval: bool = False

# GAP #3 FIXED: Added 'mandate_failed' and 'unknown' fallback to registry
POLICY_REGISTRY: Dict[RootCauseType, RecoveryPolicy] = {
    "insufficient_funds": RecoveryPolicy(
        root_cause="insufficient_funds",
        allowed_actions=["send_whatsapp_payment_link", "send_email_payment_link"],
        max_retries=3
    ),
    "card_expired": RecoveryPolicy(
        root_cause="card_expired",
        allowed_actions=["send_email_payment_link"], 
        max_retries=2
    ),
    "network_timeout": RecoveryPolicy(
        root_cause="network_timeout",
        allowed_actions=["silent_gateway_retry"],
        max_retries=1
    ),
    "fraud_block": RecoveryPolicy(
        root_cause="fraud_block",
        allowed_actions=["escalate_to_human", "do_nothing"],
        max_retries=0, 
        requires_human_approval=True
    ),
    "abandoned_checkout": RecoveryPolicy(
        root_cause="abandoned_checkout",
        allowed_actions=["send_whatsapp_payment_link"],
        max_retries=1
    ),
    "mandate_failed": RecoveryPolicy(
        root_cause="mandate_failed",
        allowed_actions=["silent_gateway_retry", "send_email_payment_link"],
        max_retries=2  # Mandate retry sequencer rule
    ),
    "unknown": RecoveryPolicy(
        root_cause="unknown",
        allowed_actions=["escalate_to_human"],
        max_retries=0,
        requires_human_approval=True
    )
}

# =====================================================================
# 5. GRAPH MEMORY & ROI TRACKING (Fixing Gaps #1, #4, and #6)
# =====================================================================

class RecoveryOutcome(BaseModel):
    """
    GAP #1 FIXED: This proves the ROI of the agent. 
    Written to the database when a successful payment webhook comes back.
    """
    transaction_id: str
    action_taken: ActionType
    payment_succeeded: bool
    amount_recovered_minor: int
    verified_at: datetime = Field(default_factory=datetime.utcnow)
    attempt_number: int

class RecoveryState(BaseModel):
    """
    The LangGraph state passed between nodes.
    """
    transaction_id: str
    customer_id: str
    webhook_payload: NormalizedFailureEvent
    compliance: Optional[ComplianceProfile] = None
    
    # GAP #6 FIXED: Track case lifecycle
    status: CaseStatusType = "detected"
    
    # GAP #4 FIXED: Wire up the retry counter for the Safety Engine
    retry_count: int = 0
    
    diagnosis: Optional[DiagnoserOutput] = None
    strategy: Optional[StrategistOutput] = None
    
    execution_approved: bool = False
    audit_log_id: Optional[str] = None