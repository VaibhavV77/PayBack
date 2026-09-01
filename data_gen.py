"""
Generates a synthetic batch of failed-payment events with a deliberate
story arc (mirrors the seed-data approach used for the diagnoser few-shot
set): a realistic mix of recoverable / unrecoverable cases across every
root cause, each with a hidden ground-truth profile registered into
gateway_sim so the batch's outcome is measurable, not fabricated.
"""

import random
import uuid
from schemas import NormalizedFailureEvent, ComplianceProfile
from gateway_sim import GroundTruth, register as register_gt

# (raw_error_code, raw_error_message, source) samples per intended category -
# grounded in real gateway decline-code taxonomies (Stripe/Razorpay docs),
# not invented strings.
SAMPLES = {
    "insufficient_funds": [
        ("insufficient_funds", "Your card has insufficient funds.", "stripe"),
        ("insufficient_funds", "The customer's account does not have enough balance.", "razorpay"),
    ],
    "card_expired": [
        ("expired_card", "Your card expired.", "stripe"),
        ("card_expired", "The card on file has expired.", "chargebee"),
    ],
    "network_timeout": [
        ("gateway_timeout", "The request to the acquirer timed out.", "razorpay"),
        ("processing_error", "An error occurred while processing your card.", "stripe"),
    ],
    "fraud_block": [
        ("do_not_honor", "Card issuer declined without a specific reason.", "stripe"),
        ("fraud_suspected", "Transaction flagged by risk engine.", "razorpay"),
    ],
    "abandoned_checkout": [
        ("session_expired", "Checkout session expired after 30 min of inactivity.", "shopify"),
        ("cart_abandoned", "Customer left checkout before completing payment.", "internal"),
    ],
    "mandate_failed": [
        ("mandate_not_confirmed", "UPI autopay mandate was not confirmed by customer.", "razorpay"),
        ("mandate_expired", "Recurring payment mandate has expired.", "chargebee"),
    ],
}

# per-category ground truth profile: (p_recoverable, possible recovers_at_attempt values)
PROFILES = {
    "insufficient_funds": (0.7, [1, 2, 3]),
    "card_expired": (0.5, [1, 2]),
    "network_timeout": (0.9, [1]),
    "fraud_block": (0.05, [1]),          # almost never actually recoverable
    "abandoned_checkout": (0.4, [1, 2]),
    "mandate_failed": (0.55, [1, 2]),
}


def generate_batch(n: int = 150, seed: int = 42) -> list[tuple[NormalizedFailureEvent, ComplianceProfile]]:
    """
    Returns (event, compliance) pairs. Compliance is generated as a separate
    object per case -- mirroring the real system, where it'd be a fresh DB
    lookup at strategize-time rather than something frozen into the webhook.
    """
    rng = random.Random(seed)
    gateway_sim_reset()
    results = []
    categories = list(SAMPLES.keys())
    for i in range(n):
        category = rng.choice(categories)
        code, msg, source = rng.choice(SAMPLES[category])
        p_recoverable, possible_attempts = PROFILES[category]
        amount_minor = rng.choice([49900, 99900, 149900, 299900, 499900, 1999900])  # e.g. paise/cents
        txn_id = f"txn_{uuid.uuid4().hex[:10]}"

        is_recoverable = rng.random() < p_recoverable
        recovers_at = rng.choice(possible_attempts) if is_recoverable else None

        event = NormalizedFailureEvent(
            event_id=f"evt_{uuid.uuid4().hex[:10]}",
            source=source,
            transaction_id=txn_id,
            customer_id=f"cust_{uuid.uuid4().hex[:8]}",
            amount_minor=amount_minor,
            currency=rng.choice(["INR", "USD"]),
            raw_error_code=code,
            raw_error_message=msg,
        )

        allowed = rng.sample(["email", "whatsapp", "sms"], k=rng.randint(1, 2))
        compliance = ComplianceProfile(
            allowed_channels=allowed,
            opted_out_channels=[c for c in ["email", "whatsapp", "sms"] if c not in allowed],
            is_quiet_hours=rng.random() < 0.15,
        )
        results.append((event, compliance))

        register_gt(GroundTruth(
            transaction_id=txn_id,
            is_actually_recoverable=is_recoverable,
            recovers_at_attempt=recovers_at,
            amount_minor=amount_minor,
        ))
    return results


def gateway_sim_reset():
    from gateway_sim import reset
    reset()
