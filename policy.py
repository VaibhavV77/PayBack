### this file is used to set the allowed actions for each root cause type, and the max retries 
from schemas import RecoveryPolicy, RootCauseType
import typing

POLICY_REGISTRY: dict[str, RecoveryPolicy] = {
    "insufficient_funds": RecoveryPolicy(
        root_cause="insufficient_funds",
        allowed_actions=["send_whatsapp_payment_link", "send_email_payment_link", "silent_gateway_retry"],
        max_retries=3,
        requires_human_approval=False,
    ),
    "card_expired": RecoveryPolicy(
        root_cause="card_expired",
        allowed_actions=["send_email_payment_link"],  # no point retrying silently on a dead card
        max_retries=2,
        requires_human_approval=False,
    ),
    "network_timeout": RecoveryPolicy(
        root_cause="network_timeout",
        allowed_actions=["silent_gateway_retry"],
        max_retries=1,  # only silently retry a blip once
        requires_human_approval=False,
    ),
    "fraud_block": RecoveryPolicy(
        root_cause="fraud_block",
        allowed_actions=["escalate_to_human", "do_nothing"],
        max_retries=0,  # never auto-retry a fraud block
        requires_human_approval=True,
    ),
    "abandoned_checkout": RecoveryPolicy(
        root_cause="abandoned_checkout",
        allowed_actions=["send_whatsapp_payment_link", "send_email_payment_link"],
        max_retries=1,  # don't hound window-shoppers
        requires_human_approval=False,
    ),
    "mandate_failed": RecoveryPolicy(
        root_cause="mandate_failed",
        allowed_actions=["send_whatsapp_payment_link", "send_email_payment_link", "escalate_to_human"],
        max_retries=2,
        requires_human_approval=False,
    ),
    "unknown": RecoveryPolicy(
        root_cause="unknown",
        allowed_actions=["escalate_to_human", "do_nothing"],
        max_retries=0,  # never auto-act on a diagnosis we're not sure about
        requires_human_approval=True,
    ),
}

# Which customer-facing channel each action requires, if any. Shared between
# the strategist (to downgrade its own pick) and the safety engine (so its
# allow-list fallback can't re-introduce a channel the customer opted out of).
CHANNEL_FOR_ACTION: dict[str, str] = {
    "send_whatsapp_payment_link": "whatsapp",
    "send_email_payment_link": "email",
    # silent_gateway_retry / escalate_to_human / do_nothing touch no customer channel
}

# Fail loudly at import time if a RootCauseType value has no policy.
_all_root_causes = typing.get_args(RootCauseType)
_missing = [rc for rc in _all_root_causes if rc not in POLICY_REGISTRY]
assert not _missing, f"POLICY_REGISTRY missing entries for: {_missing}"
