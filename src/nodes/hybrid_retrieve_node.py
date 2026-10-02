"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants -- never plain strings [A1]
#  - Never import from mediator/, api/, or other agents
#
# FIN-C2-090 -- HybridRetrieveNode
# Inner domain graph node 3: perform hybrid retrieval over raw_documents.
# Combines keyword matching (BM25-style) and semantic similarity scoring
# to identify the most relevant document chunks.
#
# Input:  state["raw_documents"]   -- list of document metadata from APIFetchNode
#         state["normalized_query"] -- expanded query string
#         state["search_keywords"]  -- keyword list for BM25 matching
#         state["search_options"]   -- resolved retrieval parameters (top_k)
# Output: state["retrieved_chunks"] -- top-k relevant document chunks
#
# Scoring uses keyword overlap; a deployment replaces it with a vector index.
#
# top_k comes from state["search_options"], which the pre_process gate resolved
# from config/config.yaml plus the caller's overrides. It is deliberately NOT
# read from an execute() `config` argument: BaseNode.__call__ invokes
# execute(state) with the state alone, so such an argument is always None and
# every value declared for it is silently dead.
#
# required_trust_level = ANONYMOUS: inner domain graph node (review finding 5).
# S-4: emits "hybrid_retrieve_complete" trace event.

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

_DEFAULT_TOP_K = 5


def _keyword_score(document: Dict[str, Any], keywords: List[str]) -> float:
    """Score a document by keyword overlap with its title and content snippet.

    Returns a float in [0.0, 1.0] (fraction of keywords present).
    Production replaces this with BM25 + embedding cosine similarity.
    """
    if not keywords:
        return 0.5  # neutral score when no keywords

    text = " ".join(
        [
            (document.get("title") or ""),
            (document.get("content_snippet") or ""),
        ]
    ).lower()

    matches = sum(1 for kw in keywords if kw.lower() in text)
    return matches / len(keywords)


def _build_chunk(document: Dict[str, Any], score: float) -> Dict[str, Any]:
    """Build a standardized retrieved chunk from a document entry."""
    return {
        "doc_id": document.get("doc_id", ""),
        "source": document.get("source", ""),
        "title": document.get("title", ""),
        "content_snippet": document.get("content_snippet", ""),
        "score": round(score, 4),
        "filing_date": document.get("filing_date") or document.get("publish_date", ""),
        "company": document.get("company", ""),
        "doc_type": document.get("type", ""),
    }


class HybridRetrieveNode(FunctionNode):
    """Hybrid retrieval over raw regulatory documents.

    Scores documents by keyword overlap (stub; production uses BM25 +
    embedding similarity) and returns the top-k most relevant chunks.

    Input state keys:
        raw_documents:    list of document metadata dicts from APIFetchNode
        normalized_query: expanded query string
        search_keywords:  keyword list for scoring

    Output state keys (partial dict):
        retrieved_chunks: list of scored document chunk dicts, sorted by score desc

    required_trust_level = ANONYMOUS (inner domain node, review finding 5).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raw_documents: List[Dict[str, Any]] = state.get("raw_documents") or []
        search_keywords: List[str] = state.get("search_keywords") or []

        options: Dict[str, Any] = state.get("search_options") or {}
        top_k: int = int(options.get("top_k", _DEFAULT_TOP_K))

        if not raw_documents:
            logger.warning("HybridRetrieveNode: raw_documents is empty; no chunks to retrieve")
            return {
                "retrieved_chunks": [],
                "status": AgentStatus.SUCCESS.value,
            }

        scored = []
        for doc in raw_documents:
            score = _keyword_score(doc, search_keywords)
            scored.append(_build_chunk(doc, score))

        # Sort by score descending, take top_k
        scored.sort(key=lambda c: c["score"], reverse=True)
        retrieved_chunks = scored[:top_k]

        logger.info(
            "HybridRetrieveNode: retrieved %d chunks (top_k=%d, from %d docs)",
            len(retrieved_chunks),
            top_k,
            len(raw_documents),
        )

        # S-4 audit trail
        emit_trace_event(
            "hybrid_retrieve_complete",
            {
                "total_documents": len(raw_documents),
                "retrieved_count": len(retrieved_chunks),
                "top_k": top_k,
                "top_score": retrieved_chunks[0]["score"] if retrieved_chunks else 0.0,
                "top_k_from_caller": "top_k" in options,
            },
            state,
        )

        return {
            "retrieved_chunks": retrieved_chunks,
            "status": AgentStatus.SUCCESS.value,
        }
