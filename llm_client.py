"""
Thin wrapper around the LLM boundary for the two reasoning nodes
(diagnoser, strategist). Gemini only -- no other provider.

- GEMINI_API_KEY set   -> Gemini (gemini-flash-lite-latest by default, free
                          tier on Google AI Studio, plenty for a batch this size)
- not set              -> deterministic rule-based mock, so the whole
                          pipeline runs offline with zero API calls

Structured output is bound via LangChain's with_structured_output(), which
constrains the schema at the API level (Gemini's native schema binding)
instead of prompting for JSON and parsing raw text -- no json.loads, no
markdown-fence stripping, no risk of the model wrapping its answer in prose.

Few-shot exemplars and system prompts live in prompts.py, not here.
"""

import json
import os
from schemas import DiagnoserOutput, StrategistOutput, NormalizedFailureEvent
from policy import CHANNEL_FOR_ACTION
from prompts import DIAGNOSER_SYSTEM_PROMPT, STRATEGIST_SYSTEM_PROMPT

USE_LIVE_LLM = bool(os.environ.get("GEMINI_API_KEY"))
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")


def _mock_diagnose(event: NormalizedFailureEvent) -> DiagnoserOutput:
    """Deterministic rule-based fallback, keyed off the same few-shot table."""
    code = event.raw_error_code.lower()
    msg = event.raw_error_message.lower()
    rules = [
        # order matters — more specific keywords checked before broader ones
        # (e.g. "mandate_expired" must hit the mandate rule, not "expired")
        (("insufficient", "nsf"), "insufficient_funds", True, 0.9),
        (("mandate",), "mandate_failed", True, 0.87),
        (("session_expired", "abandoned", "inactivity", "cart_abandoned"), "abandoned_checkout", True, 0.85),
        (("expired",), "card_expired", True, 0.92),
        (("timeout", "timed out"), "network_timeout", True, 0.88),
        (("fraud", "risk engine"), "fraud_block", False, 0.9),
    ]
    for keywords, category, recoverable, conf in rules:
        if any(k in code or k in msg for k in keywords):
            return DiagnoserOutput(
                root_cause_category=category, is_recoverable=recoverable,
                confidence_score=conf, reasoning=f"Matched keyword rule for '{category}' (mock mode)."
            )
    if "do_not_honor" in code or "do not honor" in msg:
        # mirrors the ambiguous few-shot case in prompts.py
        return DiagnoserOutput(
            root_cause_category="unknown", is_recoverable=True,
            confidence_score=0.4, reasoning="do_not_honor is ambiguous between fraud and soft decline (mock mode)."
        )
    return DiagnoserOutput(
        root_cause_category="unknown", is_recoverable=False,
        confidence_score=0.3, reasoning="No rule matched this error code (mock mode)."
    )


def _mock_strategize(diagnosis: DiagnoserOutput, attempt_number: int,
                      contact_channels: list[str], is_quiet_hours: bool) -> StrategistOutput:
    ladder = {
        "insufficient_funds": ["send_email_payment_link", "send_whatsapp_payment_link", "escalate_to_human"],
        "card_expired": ["send_email_payment_link", "send_email_payment_link"],
        "network_timeout": ["silent_gateway_retry"],
        "abandoned_checkout": ["send_whatsapp_payment_link", "send_whatsapp_payment_link"],
        "mandate_failed": ["send_whatsapp_payment_link", "escalate_to_human"],
        "fraud_block": ["escalate_to_human"],
        "unknown": ["escalate_to_human"],
    }[diagnosis.root_cause_category]
    idx = min(attempt_number - 1, len(ladder) - 1)
    action = ladder[idx]
    reasoning = f"Escalation ladder step {idx + 1} for {diagnosis.root_cause_category} (mock mode)."

    # respect opted-out channels: downgrade rather than send on a channel the
    # customer isn't reachable on
    required_channel = CHANNEL_FOR_ACTION.get(action)
    if required_channel and required_channel not in contact_channels:
        if "email" in contact_channels and action != "send_email_payment_link":
            action = "send_email_payment_link"
            reasoning += f" Downgraded from ladder choice — '{required_channel}' not in allowed channels."
        else:
            action = "escalate_to_human"
            reasoning += f" No allowed channel could carry this action — escalating to a human instead."

    delay = {"network_timeout": 2, "abandoned_checkout": 30}.get(diagnosis.root_cause_category, 60 * 6)
    if is_quiet_hours and required_channel:
        delay = max(delay, 60 * 8)  # push customer-facing sends past quiet hours
        reasoning += " Delayed due to quiet hours."

    return StrategistOutput(recommended_action=action, delay_minutes=delay, reasoning=reasoning)


def _bound_model(output_model):
    """
    Builds a LangChain ChatGoogleGenerativeAI model bound to output_model via
    with_structured_output(). This binds the schema at the API level
    (Gemini's native schema binding), so the parsed pydantic instance comes
    back directly -- no manual JSON parsing on our end.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    llm = ChatGoogleGenerativeAI(model=GEMINI_MODEL, temperature=0)
    return llm.with_structured_output(output_model)


def _call_llm(system_prompt: str, user_content: str, output_model):
    bound = _bound_model(output_model)
    return bound.invoke([("system", system_prompt), ("human", user_content)])


def diagnose(event: NormalizedFailureEvent) -> DiagnoserOutput:
    if not USE_LIVE_LLM:
        return _mock_diagnose(event)
    user_content = json.dumps({
        "raw_error_code": event.raw_error_code,
        "raw_error_message": event.raw_error_message,
        "source": event.source,
        "amount_minor": event.amount_minor,
        "currency": event.currency,
    })
    return _call_llm(DIAGNOSER_SYSTEM_PROMPT, user_content, DiagnoserOutput)


def strategize(diagnosis: DiagnoserOutput, attempt_number: int,
               contact_channels: list[str], is_quiet_hours: bool) -> StrategistOutput:
    if not USE_LIVE_LLM:
        return _mock_strategize(diagnosis, attempt_number, contact_channels, is_quiet_hours)
    user_content = json.dumps({
        "root_cause_category": diagnosis.root_cause_category,
        "confidence_score": diagnosis.confidence_score,
        "attempt_number": attempt_number,
        "contact_channels_allowed": contact_channels,
        "is_quiet_hours": is_quiet_hours,
    })
    return _call_llm(STRATEGIST_SYSTEM_PROMPT, user_content, StrategistOutput)
