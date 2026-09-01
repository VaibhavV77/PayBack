from langgraph.graph import StateGraph, START, END
from schemas import RecoveryState
from nodes import diagnoser_node, strategist_node, safety_engine_node, executor_node, verify_node
from edges import route_after_diagnosis, route_after_safety, route_after_verify


def build_graph():
    g = StateGraph(RecoveryState)

    g.add_node("diagnoser", diagnoser_node)
    g.add_node("strategist", strategist_node)
    g.add_node("safety_engine", safety_engine_node)
    g.add_node("executor", executor_node)
    g.add_node("verify", verify_node)

    g.add_edge(START, "diagnoser")
    g.add_conditional_edges("diagnoser", route_after_diagnosis, {"strategist": "strategist", END: END})
    g.add_edge("strategist", "safety_engine")
    g.add_conditional_edges("safety_engine", route_after_safety, {"executor": "executor", END: END})
    g.add_edge("executor", "verify")
    g.add_conditional_edges("verify", route_after_verify, {"strategist": "strategist", END: END})

    return g.compile()
