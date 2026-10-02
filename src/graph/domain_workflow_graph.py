"""AgentCore Platform v1.0"""

# FIN-C2-090 -- DomainWorkflowGraph (inner BaseGraph)
#
# This is the INNER graph for the Cat 2 two-layer nested architecture.
# It encapsulates the full regulatory filing search domain workflow:
#
#   START
#     -> query_normalize   (QueryNormalizeNode)
#     -> api_fetch         (APIFetchNode)
#     -> hybrid_retrieve   (HybridRetrieveNode)
#     -> rank_results      (RankResultsNode)
#     -> summary_generate  (SummaryGenerateNode)
#   END
#
# Called by RegulatorySearchGraphNode.get_subgraph() in graph.py.
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced:
#   Inherits BaseGraph (fully custom linear topology -- no forced backbone)
#   Implements all 7 BaseGraph abstract methods
#   register_nodes() does NOT call super() (abstract in BaseGraph)
#   _extra_initial_state() seeds the resolved search options (context bridge)
#   Does NOT register initialize / finalize (outer backbone concerns)
#   get_output() designed together with RegulatorySearchGraphNode.merge_output()
#   No L0 platform SDK imports
#   All node required_trust_level = ANONYMOUS (review finding 5)

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from src.nodes.api_fetch_node import APIFetchNode
from src.nodes.hybrid_retrieve_node import HybridRetrieveNode
from src.nodes.query_normalize_node import QueryNormalizeNode
from src.nodes.rank_results_node import RankResultsNode
from src.graph.context_bridge import get_search_options
from src.nodes.summary_generate_node import SummaryGenerateNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for FIN-C2-090.

    Inherits BaseGraph directly for a fully custom linear topology.
    Called by RegulatorySearchGraphNode.get_subgraph() in graph.py.

    Pipeline (linear):
        START
          -> query_normalize   (QueryNormalizeNode)
          -> api_fetch         (APIFetchNode)
          -> hybrid_retrieve   (HybridRetrieveNode)
          -> rank_results      (RankResultsNode)
          -> summary_generate  (SummaryGenerateNode)
        END

    All nodes are FunctionNode subclasses with required_trust_level = ANONYMOUS.
    initialize / finalize are outer backbone concerns -- not registered here.
    """

    # -- Identity -------------------------------------------------------------

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph."""
        return "fin_c2_090_regulatory_search_workflow"

    @property
    def state_schema(self) -> type:
        """TypedDict subclass shared across inner and outer graph."""
        return State

    # -- Config validation ----------------------------------------------------

    def _validate_config(self) -> None:
        """Validate inner graph config before compilation.

        No mandatory config for the search workflow. The retrieval parameters
        arrive already resolved and bounded via _extra_initial_state(); a node
        that receives none falls back to its own constant.
        """
        pass

    # -- Caller parameter hand-off --------------------------------------------

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the search options the outer graph stashed for this invoke.

        GraphNode.execute() calls subgraph.invoke(user_input, session_id, ctx)
        and forwards nothing else, so without this hook every inner node would
        read search_options as {} and every caller-supplied retrieval parameter
        would be silently discarded -- see src/graph/context_bridge.py.
        """
        return {"search_options": get_search_options()}

    # -- Node registration ----------------------------------------------------

    def register_nodes(self) -> None:
        """Register all domain nodes.

        No super() call -- BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize here; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.
        Every key registered here must be reachable from START in add_edges().

        All nodes instantiated with NO constructor arguments (SDK-v1 contract:
        config flows per-call via execute(self, state, config=None)).
        """
        self._nodes["query_normalize"] = QueryNormalizeNode()
        self._nodes["api_fetch"] = APIFetchNode()
        self._nodes["hybrid_retrieve"] = HybridRetrieveNode()
        self._nodes["rank_results"] = RankResultsNode()
        self._nodes["summary_generate"] = SummaryGenerateNode()

    # -- Edge wiring ----------------------------------------------------------

    def add_edges(self) -> None:
        """Wire the linear search pipeline.

        query_normalize -> api_fetch -> hybrid_retrieve -> rank_results
                       -> summary_generate -> END
        """
        self._sg.add_edge(START, "query_normalize")
        self._sg.add_edge("query_normalize", "api_fetch")
        self._sg.add_edge("api_fetch", "hybrid_retrieve")
        self._sg.add_edge("hybrid_retrieve", "rank_results")
        self._sg.add_edge("rank_results", "summary_generate")
        self._sg.add_edge("summary_generate", END)

    # -- Routing --------------------------------------------------------------

    def route(self, state: AgentState) -> str:
        """Conditional routing. Required by BaseGraph ABC.

        This graph uses a linear topology -- route() is never called but must
        be implemented because BaseGraph declares it @abstractmethod.
        Always returns END to prevent any inadvertent re-entry.
        """
        return END

    # -- Output shape ---------------------------------------------------------

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        This dict is received by RegulatorySearchGraphNode.merge_output()
        in graph.py as the sub_result argument. Both methods are designed
        together to guarantee field-name consistency:

            Inner get_output()   emits:  "output", "search_summary",
                                         "ranked_results", "status",
                                         "trace_id", "correlation_id",
                                         "node_history"
            Outer merge_output() reads:  sub_result.get("output"),
                                         sub_result.get("search_summary"),
                                         sub_result.get("ranked_results"),
                                         sub_result.get("status")
        """
        return {
            "output": state.get("result") or state.get("search_summary"),
            "search_summary": state.get("search_summary"),
            "ranked_results": state.get("ranked_results", []),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
