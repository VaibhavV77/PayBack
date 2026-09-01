"""
LangGraph node functions. Each takes RecoveryState and returns a partial
update dict. Every node writes to the audit trail on entry/exit and
appends the returned audit_id onto audit_log_ids, so state itself is a
queryable pointer into the full trail for that case.
"""

from schemas import RecoveryState
from policy import get_policy, CHANNEL_FOR_ACTION
from llm_client import diagnose, strategize
import gateway_sim
import audit


def diagnoser_node(state: RecoveryState) -> dict:
    ids = list(state.audit_log_ids)
    ids.append(audit.log(state.transaction_id, "diagnoser", "diagnosing", "Diagnoser started"))
    diagnosis = diagnose(state.webhook_payload)
    ids.append(audit.log(
        state.transaction_id, "diagnoser", "diagnosing",
        f"root_cause={diagnosis.root_cause_category} recoverable={diagnosis.is_recoverable} "
        f"confidence={diagnosis.confidence_score:.2f}"
    ))
    # is_recoverable is informational, not a routing gate -- whether a case
    # gets auto-retried, escalated to a human, or written off is a POLICY
    # decision made in safety_engine_node. A "not recoverable" fraud_block
    # case still needs to reach the safety engine so it can be routed to
    # human approval instead of silently dropped here.
    return {"diagnosis": diagnosis, "status": "strategizing", "audit_log_ids": ids}


def strategist_node(state: RecoveryState) -> dict:
    attempt_number = state.retry_count + 1
    compliance = state.compliance
    contact_channels = compliance.allowed_channels if compliance else ["email"]
    if compliance:
        contact_channels = [c for c in contact_channels if c not in compliance.opted_out_channels]
    is_quiet_hours = compliance.is_quiet_hours if compliance else False

    strategy = strategize(state.diagnosis, attempt_number, contact_channels, is_quiet_hours)
    ids = list(state.audit_log_ids)
    ids.append(audit.log(
        state.transaction_id, "strategist", "strategizing",
        f"attempt={attempt_number} action={strategy.recommended_action} delay={strategy.delay_minutes}m"
    ))
    return {"strategy": strategy, "audit_log_ids": ids}


def safety_engine_node(state: RecoveryState) -> dict:
    root_cause = state.diagnosis.root_cause_category
    policy = get_policy(root_cause)
    action = state.strategy.recommended_action
    ids = list(state.audit_log_ids)

    # 1. human approval gate -- checked FIRST and unconditionally. Must not
    #    be gated behind the retry cap: fraud_block has max_retries=0, so if
    #    the cap were checked first it would route straight to "exhausted"
    #    and the case would never reach a human. Compliance gates always win.
    if policy.requires_human_approval:
        ids.append(audit.log(state.transaction_id, "safety_engine", "escalated",
                              f"{root_cause} requires human approval -- routing to escalation, no auto-execution"))
        return {"status": "escalated", "execution_approved": False,
                "action_history": state.action_history + ["escalate_to_human"],
                "audit_log_ids": ids}

    # 2. retry cap
    if state.retry_count >= policy.max_retries:
        ids.append(audit.log(state.transaction_id, "safety_engine", "exhausted",
                              f"retry_count={state.retry_count} >= max_retries={policy.max_retries}"))
        return {"status": "exhausted", "execution_approved": False, "audit_log_ids": ids}

    # 3. action allow-list -- if the strategist picked something not
    #    permitted for this root cause, fall back to an allowed action
    #    instead of executing an unapproved one. The fallback candidates
    #    are filtered by compliance FIRST: picking policy.allowed_actions[0]
    #    blindly can re-introduce a channel the customer opted out of (this
    #    happened in testing -- a whatsapp opt-out got silently overridden
    #    back to whatsapp because the policy fallback didn't know about it).
    if action not in policy.allowed_actions:
        allowed_channels = state.compliance.allowed_channels if state.compliance else None
        candidates = policy.allowed_actions
        if allowed_channels is not None:
            candidates = [
                a for a in policy.allowed_actions
                if CHANNEL_FOR_ACTION.get(a) is None or CHANNEL_FOR_ACTION[a] in allowed_channels
            ]
        if not candidates:
            ids.append(audit.log(state.transaction_id, "safety_engine", "escalated",
                                  f"no policy-allowed action for {root_cause} respects compliance "
                                  f"channels {allowed_channels} -- escalating to human"))
            return {"status": "escalated", "execution_approved": False,
                    "action_history": state.action_history + ["escalate_to_human"],
                    "audit_log_ids": ids}
        fallback = candidates[0]
        ids.append(audit.log(state.transaction_id, "safety_engine", "strategizing",
                              f"action '{action}' not in policy allow-list for {root_cause}, "
                              f"overriding to '{fallback}' (compliance-filtered)"))
        action = fallback

    ids.append(audit.log(state.transaction_id, "safety_engine", "executing", f"approved action='{action}'"))
    return {"status": "executing", "execution_approved": True,
            "strategy": state.strategy.model_copy(update={"recommended_action": action}),
            "audit_log_ids": ids}


def executor_node(state: RecoveryState) -> dict:
    attempt_number = state.retry_count + 1
    action = state.strategy.recommended_action
    succeeded, code, amount_recovered = gateway_sim.attempt(state.transaction_id, action, attempt_number)

    from schemas import RecoveryOutcome
    outcome = RecoveryOutcome(
        transaction_id=state.transaction_id,
        action_taken=action,
        attempt_number=attempt_number,
        payment_succeeded=succeeded,
        amount_recovered_minor=amount_recovered,
        gateway_response_code=code,
    )
    ids = list(state.audit_log_ids)
    ids.append(audit.log(state.transaction_id, "executor", "executing",
                          f"attempt={attempt_number} action={action} result={code} succeeded={succeeded}"))
    return {
        "outcome": outcome,
        "execution_status": code,
        "action_history": state.action_history + [action],
        "audit_log_ids": ids,
    }


def verify_node(state: RecoveryState) -> dict:
    policy = get_policy(state.diagnosis.root_cause_category)
    ids = list(state.audit_log_ids)

    if state.outcome.payment_succeeded:
        ids.append(audit.log(state.transaction_id, "verify", "recovered",
                              f"payment recovered, amount_minor={state.outcome.amount_recovered_minor}"))
        return {"status": "recovered", "audit_log_ids": ids}

    new_retry_count = state.retry_count + 1
    if new_retry_count < policy.max_retries:
        ids.append(audit.log(state.transaction_id, "verify", "strategizing",
                              f"attempt {new_retry_count} failed, retrying (cap={policy.max_retries})"))
        return {"status": "strategizing", "retry_count": new_retry_count, "audit_log_ids": ids}

    ids.append(audit.log(state.transaction_id, "verify", "exhausted",
                          f"attempt {new_retry_count} failed, retry cap reached"))
    return {"status": "exhausted", "retry_count": new_retry_count, "audit_log_ids": ids}
