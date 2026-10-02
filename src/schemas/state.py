"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.

from typing import Any, Dict, List, Optional

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state for FIN-C2-090 FinancialRegulatoryFilingSearchAgent.

    Search pipeline over EDINET, FSA circulars, and BOJ publications.

    Field lifecycle:
        outer graph         -> runtime_config
        pre_process         -> validated_input, enriched_context, search_options
        QueryNormalizeNode  -> normalized_query, search_keywords, document_sources
        APIFetchNode        -> raw_documents, fetch_metadata
        HybridRetrieveNode  -> retrieved_chunks
        RankResultsNode     -> ranked_results
        SummaryGenerateNode -> search_summary, result

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.
    """

    # -- Deployment configuration (seeded by the outer graph) ------------------
    runtime_config: Optional[Dict[str, Any]]

    # -- Pre-process output (S-1 validated) -----------------------------------
    validated_input: Optional[str]
    enriched_context: Optional[Dict[str, Any]]

    # -- Resolved retrieval parameters (config defaults + caller overrides) ----
    search_options: Optional[Dict[str, Any]]

    # -- QueryNormalizeNode output ---------------------------------------------
    normalized_query: Optional[str]
    search_keywords: Optional[List[str]]
    document_sources: Optional[List[str]]

    # -- APIFetchNode output ---------------------------------------------------
    raw_documents: Optional[List[Dict[str, Any]]]
    fetch_metadata: Optional[Dict[str, Any]]

    # -- HybridRetrieveNode output ---------------------------------------------
    retrieved_chunks: Optional[List[Dict[str, Any]]]

    # -- RankResultsNode output ------------------------------------------------
    ranked_results: Optional[List[Dict[str, Any]]]

    # -- SummaryGenerateNode output --------------------------------------------
    search_summary: Optional[str]
    result: Optional[str]
