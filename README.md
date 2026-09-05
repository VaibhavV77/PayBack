# PayBack — Autonomous Revenue Recovery Agent

Built for the **Razorpay Buildathon — Revenue Recovery** problem statement.

## The Problem

When a payment fails, most systems just log it and move on. Someone has to
notice the failure, figure out *why* it happened, decide whether it's worth
retrying, pick the right way to reach the customer (or realize a human needs
to step in instead), and track all of that — usually manually, at scale,
across thousands of failed transactions a day. Revenue quietly leaks through
that gap.

## What PayBack Does

PayBack is an autonomous agent that closes that gap. For every failed
payment, it:

1. **Diagnoses** the root cause of the failure (insufficient funds, expired
   card, a transient network/gateway blip, suspected fraud, an abandoned
   checkout, or a failed recurring-payment mandate).
2. **Strategizes** the right recovery action for that root cause and attempt
   number — a silent gateway retry, a payment link over WhatsApp or email,
   or escalating to a human — respecting what channel the customer has
   actually consented to be contacted on.
3. **Enforces policy** through a dedicated safety layer: hard retry caps per
   root cause, compliance-aware channel restrictions, and mandatory human
   approval for anything touching fraud.
4. **Executes** the chosen action and **verifies** the outcome, looping back
   to re-strategize on failure (within policy limits) or closing the case
   as recovered, exhausted, or escalated.
5. **Logs everything.** Every node writes to an append-only audit trail, so
   any case's full decision history — what was tried, why, and what a
   policy overrode — can be replayed after the fact.

It's built to be *measured*, not just demoed: a synthetic batch generator
seeds every case with a hidden ground-truth recoverability profile, so the
agent's actual performance can be checked against a real, known ceiling
instead of an arbitrary number.

## Architecture

A 5-node graph, built with **LangGraph**:

```
START → diagnoser → strategist → safety_engine ──┬──→ executor → verify ──┐
                         ↑                        │                       │
                         └────────────────────────┴───────────────────────┘
                              (loops back to strategist on a failed,
                               within-policy retry; otherwise → END)
```

| Node | Responsibility |
|---|---|
| **diagnoser** | Classifies root cause + recoverability from the raw gateway error. LLM-backed (or deterministic keyword rules offline). |
| **strategist** | Picks one recovery action given the diagnosis, attempt number, allowed contact channels, and quiet-hours constraints. |
| **safety_engine** | The sole enforcement point for business policy — retry caps, the allowed-action list per root cause, human-approval gates, and compliance-channel filtering. Nothing else in the graph makes policy decisions. |
| **executor** | Attempts the chosen action against a simulated payment gateway. |
| **verify** | Checks the outcome and decides: recovered, retry (loop back), or exhausted. |

Policy data lives in exactly one place (`policy.py`) — every other module
reads it through `get_policy()`, so it can be swapped for a database-backed
lookup later without touching graph logic.

## Tech Stack

- **LangGraph** — orchestration
- **Pydantic** — every data contract (events, diagnoses, strategies,
  outcomes, audit entries) is a typed, validated schema, defined once in
  `schemas.py`
- **Gemini 3.1 Flash-Lite** via `langchain-google-genai`, with
  `with_structured_output()` binding the response schema at the API level
  (no manual JSON parsing, no markdown-fence stripping)
- **tenacity** — exponential-backoff retry around the live API boundary
- A fully deterministic **mock LLM mode** (keyword-rule diagnosis + a fixed
  escalation ladder) so the entire pipeline runs offline with zero API
  calls and zero cost, for development and CI-style testing

## Results

These are the results obtained by a running a test batch of size N=50

```code-runner-output
================================================================
BATCH RESULT � 50 cases
================================================================
Revenue at risk:      256,950.00
Revenue recovered:    105,488.00
Recovery rate:        41.1%  (of revenue, i.e. weighted by amount)
Cases recovered:      12 / 50  (24.0% of cases)
Cases exhausted:      8
Cases escalated:      30

root cause            cases  recovered     $ at risk   $ recovered
------------------------------------------------------------------
abandoned_checkout        8          0      6,492.00          0.00
card_expired              9          3     35,491.00     11,497.00
fraud_block               8          0     39,992.00          0.00
insufficient_funds       11          3     67,489.00     40,497.00
mandate_failed            8          1     53,992.00      4,999.00
network_timeout           6          5     53,494.00     48,495.00

Audit trail: 371 entries written to audit_log.jsonl
Example � full trail for txn_19ed4bf210:
  [17:19:40] ingest         detected       normalized webhook from razorpay, amount_minor=49900
  [17:19:40] diagnoser      diagnosing     Diagnoser started
  [17:19:48] diagnoser      diagnosing     root_cause=mandate_failed recoverable=True confidence=1.00
  [17:19:54] strategist     strategizing   attempt=1 action=send_email_payment_link delay=15m
  [17:19:54] safety_engine  executing      approved action='send_email_payment_link'
  [17:19:54] executor       executing      attempt=1 action=send_email_payment_link result=still_declined succeeded=False
  [17:19:54] verify         strategizing   attempt 1 failed, retrying (cap=2)
  [17:20:10] strategist     strategizing   attempt=2 action=escalate_to_human delay=0m
  [17:20:10] safety_engine  escalated      strategist requested human escalation for mandate_failed (attempt 2)
```
## Running It

```bash
pip install -r requirements.txt

# Offline, deterministic, zero API calls:
python run_batch.py

# Custom batch size:
N=150 python run_batch.py

# Live Gemini:
GEMINI_API_KEY=your_key python run_batch.py
```

`run_batch.py` generates a synthetic batch, runs every case through the
graph, and prints a report: revenue at risk vs. recovered, recovery rate,
and a breakdown by root cause — plus a full audit trail sample for one case.

## Measuring It Against a Real Ceiling

Because every synthetic case carries a hidden ground-truth recoverability
profile, we don't just report "$X recovered" — we can compute the actual
**achievable ceiling** under the current retry policy (i.e. the best any
agent could do given the retry caps) and measure capture rate against it.

That measurement caught real problems during development:

- A policy/behavior gap meant the agent's own deliberate "hand this to a
  human" decisions were being silently overridden back into automated
  retries for two root causes — masking genuine escalations as either
  wasted retries or (worse) miscounted as "exhausted."
- A subtle wording collision in the strategist's prompt — "escalate the
  channel" (switch from email to WhatsApp) sitting next to an action
  literally named `escalate_to_human` — was causing the live model to give
  up early instead of trying the next contact channel.
- A missing diagnostic example meant a whole class of genuinely recoverable
  network-timeout failures was being misclassified as `unknown` and
  escalated with zero recovery attempt.

Fixing these took the agent from capturing **~26% of achievable revenue to
~66%** on the same synthetic batch — without touching the underlying model,
just the policy and prompt logic around it.

## Known Limitations / Next Steps

- `StrategistOutput.delay_minutes` is computed and logged but not yet
  enforced by a real scheduler — a "wait 30 minutes before nudging" decision
  currently just proceeds immediately in the simulation. Real deferred
  execution (cart-abandonment-style delays) would need a durable job queue
  and checkpointed state, not just an in-memory graph run.
- The strategist doesn't yet know how many attempts it has left when
  deciding whether to escalate vs. keep trying — giving it that context is
  the next planned improvement, since in this environment giving up early
  is pure downside whenever an automated channel is still available.
- The audit trail is intentionally simple (in-memory + append-only JSONL)
  for easy inspection during a batch run; a production version would need
  durable, queryable storage.

## Problem Statement

Razorpay Buildathon — Revenue Recovery.
