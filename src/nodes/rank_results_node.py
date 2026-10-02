"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants -- never plain strings [A1]
#  - Never import from mediator/, api/, or other agents
#
# FIN-C2-090 -- RankResultsNode
# Inner domain graph node 4: re-rank retrieved_chunks by relevance to the
# normalized_query, applying source-priority boosting.
#
# Input:  state["retrieved_chunks"]  -- chunks from HybridRetrieveNode
#         state["normalized_query"]  -- query string (for cross-encoder stub)
#         state["document_sources"]  -- source priority list
#         state["search_options"]    -- resolved retrieval parameters (min_score)
# Output: state["ranked_results"]    -- re-ranked list of chunks (highest first)
#
# Applies a source-priority multiplier and drops anything below the caller's
# relevance floor. A deployment replaces the multiplier with a cross-encoder.
#
# required_trust_level = ANONYMOUS: inner domain graph node (review finding 5).
# S-4: emits "rank_results_complete" trace event.

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

# Source priority multipliers (stub; production uses cross-encoder re-ranking)
_SOURCE_PRIORITY: Dict[str, float] = {
    "EDINET": 1.2,
    "FSA": 1.15,
    "BOJ": 1.0,
}


def _rerank_score(chunk: Dict[str, Any]) -> float:
    """Compute final rank score with source-priority multiplier.

    Production replaces this stub with a cross-encoder or LLM-based re-ranker.
    """
    base_score: float = chunk.get("score", 0.0)
    source: str = chunk.get("source", "")
    multiplier: float = _SOURCE_PRIORITY.get(source, 1.0)
    return round(base_score * multiplier, 4)


class RankResultsNode(FunctionNode):
    """Re-rank retrieved document chunks by relevance.

    Applies a source-priority multiplier to refine the initial hybrid
    retrieval ranking. Returns chunks sorted by final rank score descending.

    Input state keys:
        retrieved_chunks: list of scored chunk dicts from HybridRetrieveNode
        normalized_query: query string (used by cross-encoder in production)
        document_sources: source priority list

    Output state keys (partial dict):
        ranked_results: re-ranked chunk list, sorted by final score descending

    required_trust_level = ANONYMOUS (inner domain node, review finding 5).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        retrieved_chunks: List[Dict[str, Any]] = state.get("retrieved_chunks") or []
        options: Dict[str, Any] = state.get("search_options") or {}
        min_score = float(options.get("min_score", 0.0))

        if not retrieved_chunks:
            logger.warning("RankResultsNode: retrieved_chunks is empty; nothing to rank")
            return {
                "ranked_results": [],
                "status": AgentStatus.SUCCESS.value,
            }

        ranked = []
        for chunk in retrieved_chunks:
            final_score = _rerank_score(chunk)
            ranked_chunk = dict(chunk)
            ranked_chunk["rank_score"] = final_score
            ranked.append(ranked_chunk)

        ranked.sort(key=lambda c: c["rank_score"], reverse=True)

        # The relevance floor is applied after re-ranking, so it is enforced on
        # the score the caller is shown rather than on an intermediate one.
        dropped = 0
        if min_score > 0.0:
            kept = [c for c in ranked if c["rank_score"] >= min_score]
            dropped = len(ranked) - len(kept)
            ranked = kept

        logger.info(
            "RankResultsNode: ranked %d results; top score=%.4f",
            len(ranked),
            ranked[0]["rank_score"] if ranked else 0.0,
        )

        # S-4 audit trail
        emit_trace_event(
            "rank_results_complete",
            {
                "ranked_count": len(ranked),
                "top_rank_score": ranked[0]["rank_score"] if ranked else 0.0,
                "top_source": ranked[0].get("source", "") if ranked else "",
                "min_score": min_score,
                "below_floor_dropped": dropped,
            },
            state,
        )

        return {
            "ranked_results": ranked,
            "status": AgentStatus.SUCCESS.value,
        }
