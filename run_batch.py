"""
Runs a synthetic batch through the recovery graph end to end and reports
measured revenue recovered - the actual "bar" from the brief.

Usage:
    python run_batch.py             # 150// temp 20 cases, mock LLM (no API key needed)
    N=300 python run_batch.py       # custom batch size
    GEMINI_API_KEY=...   python run_batch.py       # real Gemini Flash API
"""

import os
from collections import defaultdict
import time
import audit
from data_gen import generate_batch
from graph import build_graph
from schemas import RecoveryState, NormalizedFailureEvent, ComplianceProfile


def to_initial_state(event: NormalizedFailureEvent, compliance: ComplianceProfile) -> RecoveryState:
    audit_id = audit.log(event.transaction_id, "ingest", "detected",
                          f"normalized webhook from {event.source}, amount_minor={event.amount_minor}")
    return RecoveryState(
        transaction_id=event.transaction_id,
        customer_id=event.customer_id,
        compliance=compliance,
        audit_log_ids=[audit_id],
        webhook_payload=event,
        status="detected",
    )


def main():
    n = int(os.environ.get("N", 5))
    audit.reset()
    pairs = generate_batch(n=n)
    app = build_graph()

    results: list[RecoveryState] = []
    for event, compliance in pairs:
        initial = to_initial_state(event, compliance)
        final_dict = app.invoke(initial, config={"recursion_limit": 5})
        results.append(RecoveryState.model_validate(final_dict))

    print_report(results)


def print_report(results: list[RecoveryState]):
    total_at_risk = sum(r.webhook_payload.amount_minor for r in results)
    total_recovered = sum(
        r.outcome.amount_recovered_minor for r in results if r.outcome and r.outcome.payment_succeeded
    )
    recovered_count = sum(1 for r in results if r.status == "recovered")
    exhausted_count = sum(1 for r in results if r.status == "exhausted")
    escalated_count = sum(1 for r in results if r.status == "escalated")

    print("=" * 64)
    print(f"BATCH RESULT — {len(results)} cases")
    print("=" * 64)
    print(f"Revenue at risk:      {total_at_risk / 100:,.2f}")
    print(f"Revenue recovered:    {total_recovered / 100:,.2f}")
    print(f"Recovery rate:        {total_recovered / total_at_risk * 100:.1f}%  "
          f"(of revenue, i.e. weighted by amount)")
    print(f"Cases recovered:      {recovered_count} / {len(results)}  "
          f"({recovered_count / len(results) * 100:.1f}% of cases)")
    print(f"Cases exhausted:      {exhausted_count}")
    print(f"Cases escalated:      {escalated_count}")
    print()

    by_cause = defaultdict(lambda: {"count": 0, "recovered": 0, "at_risk_minor": 0, "recovered_minor": 0})
    for r in results:
        cause = r.diagnosis.root_cause_category if r.diagnosis else "undiagnosed"
        bucket = by_cause[cause]
        bucket["count"] += 1
        bucket["at_risk_minor"] += r.webhook_payload.amount_minor
        if r.status == "recovered":
            bucket["recovered"] += 1
            bucket["recovered_minor"] += r.outcome.amount_recovered_minor

    print(f"{'root cause':<20}{'cases':>7}{'recovered':>11}{'$ at risk':>14}{'$ recovered':>14}")
    print("-" * 66)
    for cause, b in sorted(by_cause.items()):
        print(f"{cause:<20}{b['count']:>7}{b['recovered']:>11}"
              f"{b['at_risk_minor']/100:>14,.2f}{b['recovered_minor']/100:>14,.2f}")

    print()
    print(f"Audit trail: {len(audit.all_entries())} entries written to audit_log.jsonl")
    print(f"Example — full trail for {results[0].transaction_id}:")
    for e in audit.entries_for(results[0].transaction_id):
        print(f"  [{e.timestamp.strftime('%H:%M:%S')}] {e.node:<14} {e.status:<14} {e.detail}")


if __name__ == "__main__":
    main()
