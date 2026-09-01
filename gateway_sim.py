"""
Mock payment gateway. Each synthetic transaction is generated with a hidden
ground-truth recoverability profile (data_gen.py). When the executor "tries"
an action against a transaction, this module decides success/failure by
checking that ground truth - never by asking the LLM, and never randomly
independent of the case's actual story. That's what makes the batch's
"$ recovered" number mean something instead of being cosmetic.

do_nothing and escalate_to_human never succeed on their own here (they hand
off to a human who isn't simulated) - they're logged as attempts but don't
move money in this simulation.
"""

from dataclasses import dataclass
from typing import Optional
from schemas import ActionType

MONEY_MOVING_ACTIONS = {"silent_gateway_retry", "send_whatsapp_payment_link", "send_email_payment_link"}


@dataclass
class GroundTruth:
    transaction_id: str
    is_actually_recoverable: bool
    recovers_at_attempt: Optional[int]  # 1-indexed attempt number that would succeed, if recoverable
    amount_minor: int


_registry: dict[str, GroundTruth] = {}


def register(gt: GroundTruth):
    _registry[gt.transaction_id] = gt


def reset():
    _registry.clear()


def attempt(transaction_id: str, action: ActionType, attempt_number: int) -> tuple[bool, str, int]:
    """
    Returns (succeeded, gateway_response_code, amount_recovered_minor).
    """
    gt = _registry.get(transaction_id)
    if gt is None:
        return False, "unknown_transaction", 0

    if action not in MONEY_MOVING_ACTIONS:
        return False, "no_op", 0

    if not gt.is_actually_recoverable:
        return False, "still_declined", 0

    if gt.recovers_at_attempt is not None and attempt_number >= gt.recovers_at_attempt:
        return True, "approved", gt.amount_minor

    return False, "still_declined", 0
