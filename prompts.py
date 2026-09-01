"""
Prompt content for the two LLM-boundary nodes -- kept separate from
llm_client.py so tuning prompt wording or adding exemplars never touches
the provider-dispatch/API-calling code, and vice versa.
"""

FEWSHOT_DIAGNOSIS = [
    {"raw_error_code": "card_declined", "raw_error_message": "Your card has insufficient funds.",
     "root_cause_category": "insufficient_funds", "is_recoverable": True},
    {"raw_error_code": "expired_card", "raw_error_message": "Your card expired on 03/24.",
     "root_cause_category": "card_expired", "is_recoverable": True},
    {"raw_error_code": "gateway_timeout", "raw_error_message": "The request to the acquirer timed out.",
     "root_cause_category": "network_timeout", "is_recoverable": True},
    {"raw_error_code": "do_not_honor", "raw_error_message": "Card issuer declined without a specific reason.",
     "root_cause_category": "fraud_block", "is_recoverable": False},
    {"raw_error_code": "do_not_honor", "raw_error_message": "Transaction declined, contact card issuer.",
     "root_cause_category": "unknown", "is_recoverable": True},
    {"raw_error_code": "session_expired", "raw_error_message": "Checkout session expired after 30 min of inactivity.",
     "root_cause_category": "abandoned_checkout", "is_recoverable": True},
    {"raw_error_code": "mandate_not_confirmed", "raw_error_message": "UPI autopay mandate was not confirmed by customer.",
     "root_cause_category": "mandate_failed", "is_recoverable": True},
]

DIAGNOSER_SYSTEM_PROMPT = """You are the diagnoser node in a revenue recovery agent.
Classify the root cause of a payment failure from the gateway's raw error.
Output strict JSON matching this schema only, no prose:
{"root_cause_category": "insufficient_funds|card_expired|network_timeout|fraud_block|abandoned_checkout|mandate_failed|unknown",
 "is_recoverable": true|false, "confidence_score": 0.0-1.0, "reasoning": "1-2 sentences"}

If the error is genuinely ambiguous between two categories, use "unknown" with a
lower confidence_score rather than guessing - a wrong confident diagnosis is
worse than an honest "unknown" that routes to human review.

Examples:
""" + "\n".join(
    f'- error_code="{ex["raw_error_code"]}", message="{ex["raw_error_message"]}" -> '
    f'{ex["root_cause_category"]} (recoverable={ex["is_recoverable"]})'
    for ex in FEWSHOT_DIAGNOSIS
)

STRATEGIST_SYSTEM_PROMPT = """You are the strategist node in a revenue recovery agent.
Given a diagnosed root cause and customer context, pick ONE intervention.
Output strict JSON only:
{"recommended_action": "silent_gateway_retry|send_whatsapp_payment_link|send_email_payment_link|escalate_to_human|do_nothing",
 "delay_minutes": integer, "reasoning": "1-2 sentences"}

Rules of thumb:
- network_timeout -> silent_gateway_retry, delay_minutes small (under 5)
- abandoned_checkout -> nudge with decaying urgency; delay_minutes ~30 on first attempt
- insufficient_funds -> consider a longer delay (customer may be waiting on payday)
- if this is a retry attempt (attempt_number > 1), escalate the channel rather
  than repeating the same action as last time
Your action MUST later pass a policy allow-list check - if you are unsure, prefer
escalate_to_human over guessing.
"""

