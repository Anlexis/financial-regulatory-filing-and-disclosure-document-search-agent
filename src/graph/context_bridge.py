"""AgentCore Platform v1.0"""

# Carries the resolved search options across the outer -> inner graph boundary.
#
# Why this exists: GraphNode.execute() (framework) invokes the inner graph as
# `subgraph.invoke(user_input, session_id=..., ctx=...)` and forwards NOTHING
# else -- not input_context, not the outer state. So an inner node reading
# state["search_options"] would always see {} through the full nested graph, and
# every caller-supplied retrieval parameter would silently do nothing. The two
# sanctioned subclass hooks bridge it:
#
#   RegulatorySearchGraphNode.extract_input(state)  [runs BEFORE subgraph.invoke]
#       -> set_search_options(state["search_options"])
#   DomainWorkflowGraph._extra_initial_state()      [runs INSIDE subgraph.invoke]
#       -> returns {"search_options": get_search_options()}
#
# A ContextVar keeps the hand-off correct per thread/task, so concurrent
# invocations in one process cannot see each other's parameters.

from contextvars import ContextVar
from typing import Any, Dict, Optional

_SEARCH_OPTIONS: ContextVar[Optional[Dict[str, Any]]] = ContextVar("fin_c2_090_search_options", default=None)


def set_search_options(options: Optional[Dict[str, Any]]) -> None:
    """Stash the resolved search options for the imminent inner-graph invoke."""
    _SEARCH_OPTIONS.set(dict(options) if options else {})


def get_search_options() -> Dict[str, Any]:
    """Read (without consuming) the stashed options; {} when none was set."""
    return _SEARCH_OPTIONS.get() or {}
