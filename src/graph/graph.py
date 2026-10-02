"""AgentCore Platform v1.0"""

# FIN-C2-090 -- Outer graph (AgentBaseGraph; Cat 2 two-layer nested architecture)
#
# Architecture (Cat 2):
#
#   Outer backbone (fixed -- identical to Cat 1, do NOT override add_edges()):
#     START -> initialize -> pre_process -> main -> {route} -> post_process -> finalize -> END
#                                               |  (RETRY, max 3)
#                                          pre_process
#
#   `main` slot is a GraphNode subclass (RegulatorySearchGraphNode) that delegates
#   the full domain workflow to DomainWorkflowGraph (inner BaseGraph).
#
#   Domain complexity is fully encapsulated inside the inner graph. The outer
#   backbone is never modified.
#
# Directory layout:
#   src/graph/graph.py                 <- outer graph (this file)
#   src/graph/domain_workflow_graph.py <- inner graph (multi-step search topology)
#   src/graph/context_bridge.py        <- resolved-options hand-off (outer -> inner)
#
# Class-name alignment (review finding 2):
#   - class FinancialRegulatoryFilingSearchAgent (this file)
#   - config/agent.yaml: class: "src.graph.graph.FinancialRegulatoryFilingSearchAgent"
#   - src/api/server.py: from src.graph.graph import FinancialRegulatoryFilingSearchAgent
#
# Rules enforced:
#   FinancialRegulatoryFilingSearchAgent inherits AgentBaseGraph (L1 direct)
#   super().register_nodes() called first (fills initialize + finalize)
#   RegulatorySearchGraphNode assigned to self._nodes["main"]
#   merge_output() returns only changed keys
#   add_edges() NOT overridden on the outer graph
#   No Level 0 platform SDK imports

from typing import Any, ClassVar, Dict

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.graph.base_graph import BaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.trust_level import TrustLevel
from framework.schemas.agent_state import AgentState
from src.graph.context_bridge import set_search_options
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State


class RegulatorySearchGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot of FinancialRegulatoryFilingSearchAgent.

    Wraps DomainWorkflowGraph (inner Cat 2 BaseGraph).
    Called by AgentBaseGraph backbone after pre_process and before post_process.

    Contracts:
        get_subgraph()   -- instantiate and return DomainWorkflowGraph
        extract_input()  -- pull validated_input string from outer state, and
                            stash the resolved search options for the inner graph
        merge_output()   -- map sub_result fields into outer state delta (changed keys only)
        error_strategy   -- "propagate": re-raise inner errors as SubgraphError (fail-fast)
    """

    # S-1 declared on the wrapper too: the CI gate only AST-scans FunctionNode
    # subclasses, so a GraphNode main slot passes the pipeline without one and is
    # flagged at review. Same level the nodes in this repo already declare.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> "BaseGraph":
        """Instantiate and return the inner domain workflow graph.

        Imported lazily (inside the method) to avoid circular-import risk
        at module load time.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph()

    def extract_input(self, state: AgentState) -> str:
        """Return the validated query string passed to the inner graph.

        Only validated_input is forwarded. Falling back to the raw user_input
        would hand the inner graph a string the input gate has just refused,
        which is the difference between a rejection and a delay.

        The framework invokes the subgraph as
        `subgraph.invoke(user_input, session_id=..., ctx=...)` and forwards
        nothing else, so the resolved retrieval parameters are stashed here for
        DomainWorkflowGraph._extra_initial_state() to pick up -- see
        src/graph/context_bridge.py. Without that hand-off every caller-supplied
        search parameter would silently do nothing.
        """
        set_search_options(state.get("search_options"))
        return state.get("validated_input") or ""

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map inner graph sub_result back into the outer state delta.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Returns ONLY changed keys -- never the full state.

        Key coupling (designed together with DomainWorkflowGraph.get_output()):
            Inner get_output() emits:  "output", "search_summary",
                                       "ranked_results", "status"
            This merge_output() reads: sub_result.get("output"),
                                       sub_result.get("search_summary"),
                                       sub_result.get("ranked_results"),
                                       sub_result.get("status")
        """
        return {
            "result": sub_result.get("output"),
            "search_summary": sub_result.get("search_summary"),
            "ranked_results": sub_result.get("ranked_results", []),
            "status": sub_result.get("status"),
        }


class FinancialRegulatoryFilingSearchAgent(AgentBaseGraph):
    """Outer graph for FIN-C2-090 (Cat 2).

    Inherits AgentBaseGraph directly (L1 Base). Domain logic is fully
    encapsulated in RegulatorySearchGraphNode (main slot), which delegates
    to DomainWorkflowGraph (inner BaseGraph).

    Backbone (fixed -- identical to Cat 1):
        START -> initialize -> pre_process -> main -> post_process -> finalize -> END

    register_nodes() is the ONLY backbone override:
        super().register_nodes() fills: initialize, finalize (framework defaults)
        pre_process:  PreProcessNode (VERIFIED_EXTERNAL -- S-1 input gate)
        main:         RegulatorySearchGraphNode (delegates to DomainWorkflowGraph)
        post_process: PostProcessNode (ANONYMOUS -- S-3 output gate)

    add_edges() is NOT overridden -- backbone wiring belongs to the framework.

    Construct with the parsed contents of config/config.yaml
    (`FinancialRegulatoryFilingSearchAgent(config=load_runtime_config())`).
    A bare construction leaves self.config empty, and every runtime value
    declared in that file is then silently ignored.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with AgentRegistry."""
        return "FinancialRegulatoryFilingSearchAgent"

    @property
    def state_schema(self) -> type:
        return State

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the deployment configuration into state.

        pre_process resolves the retrieval parameters from these defaults plus
        the caller's overrides, so this hook is the only path by which
        config/config.yaml reaches a node. Nodes must not take the values from
        an execute() `config` argument: BaseNode.__call__ calls execute(state)
        with the state alone, so such an argument is always None.
        """
        return {"runtime_config": dict(self.config)}

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first -- it injects the
        framework's default InitializeNode (sets schema_version, session_id,
        trust_level) and FinalizeNode (builds response_metadata, total_time_ms).
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = RegulatorySearchGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden -- backbone wiring belongs to the framework.


# Module-level alias so callers can import `from src.graph.graph import Graph`.
Graph = FinancialRegulatoryFilingSearchAgent
