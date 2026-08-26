"""
Conditional routing functions. Each takes the state after a node has run
and returns the name of the next node (or END).
"""

from langgraph.graph import END
from schemas import RecoveryState


def route_after_diagnosis(state: RecoveryState) -> str:
    if state.status == "exhausted":  # diagnosed as unrecoverable
        return END
    return "strategist"


def route_after_safety(state: RecoveryState) -> str:
    if state.status in ("exhausted", "escalated", "blocked"):
        return END
    return "executor"


def route_after_verify(state: RecoveryState) -> str:
    if state.status == "strategizing":  # bounded retry loop
        return "strategist"
    return END  # recovered or exhausted
