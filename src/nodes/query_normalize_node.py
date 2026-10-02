"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants -- never plain strings [A1]
#  - Never import from mediator/, api/, or other agents
#
# FIN-C2-090 -- QueryNormalizeNode
# Inner domain graph node 1: parse the free-text regulatory search query
# into a structured representation (normalized_query, search_keywords,
# document_sources).
#
# Input:  state["validated_input"] -- sanitized query string from PreProcessNode
#         state["search_options"]  -- resolved retrieval parameters (bridged in)
# Output: state["normalized_query"]  -- expanded/normalized query string
#         state["search_keywords"]   -- extracted keyword list for hybrid search
#         state["document_sources"]  -- which registries to search
#
# When the caller named registries explicitly they win outright; the keyword
# heuristic only chooses when the caller did not.
#
# required_trust_level = ANONYMOUS: inner domain graph node (review finding 5).
# S-4: emits "query_normalize_complete" trace event.

import logging
import re
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.security import KNOWN_SOURCES

logger = logging.getLogger(__name__)

# Known regulatory document sources
_ALL_SOURCES = list(KNOWN_SOURCES)

# Cap on the extracted keyword list — a structural bound on caller text, not a
# tuning knob: without it a long query drives an unbounded per-document scan.
_MAX_KEYWORDS = 20

# Source detection keywords
_SOURCE_PATTERNS: List[tuple[str, "re.Pattern[str]"]] = [
    ("EDINET", re.compile(r"\b(?:EDINET|有価証券報告書|securities report|annual report|disclosure)\b", re.IGNORECASE)),
    ("FSA", re.compile(r"\b(?:FSA|金融庁|circular|監督指針|ガイドライン|guidance)\b", re.IGNORECASE)),
    ("BOJ", re.compile(r"\b(?:BOJ|日本銀行|Bank of Japan|monetary policy|statistics)\b", re.IGNORECASE)),
]

# Financial terminology expansion map (stub -- production uses an embedding model)
_TERM_EXPANSIONS: Dict[str, List[str]] = {
    "NISA": ["NISA", "少額投資非課税", "tax-exempt investment"],
    "AI": ["AI", "artificial intelligence", "machine learning", "人工知能"],
    "climate": ["climate", "ESG", "carbon", "transition risk", "気候変動"],
    "risk": ["risk", "リスク", "risk factor", "リスク要因"],
}


def _detect_sources(query: str) -> List[str]:
    """Identify which regulatory sources are relevant to the query.

    Returns a deduplicated list of source codes. Falls back to all sources
    if no specific source is detected.
    """
    sources = []
    for source_code, pattern in _SOURCE_PATTERNS:
        if pattern.search(query):
            sources.append(source_code)
    return sources if sources else list(_ALL_SOURCES)


def _extract_keywords(query: str) -> List[str]:
    """Extract search keywords from the normalized query.

    Splits on whitespace, removes stop words, deduplicates.
    Stub implementation -- production uses NLP tokenization.
    """
    stop_words = {"the", "a", "an", "of", "for", "in", "on", "at", "to", "and", "or", "is", "are"}
    tokens = re.findall(r"[A-Za-z0-9぀-鿿]+", query)
    seen = set()
    keywords = []
    for tok in tokens:
        lower = tok.lower()
        if lower not in stop_words and lower not in seen:
            seen.add(lower)
            keywords.append(tok)
    return keywords[:_MAX_KEYWORDS]


def _normalize_query(query: str) -> str:
    """Expand financial terms and normalize whitespace."""
    result = query
    for term, expansions in _TERM_EXPANSIONS.items():
        if re.search(rf"\b{re.escape(term)}\b", query, re.IGNORECASE):
            expansion_str = " ".join(expansions)
            result = re.sub(
                rf"\b{re.escape(term)}\b", f"{term} ({expansion_str})", result, flags=re.IGNORECASE, count=1
            )
    return re.sub(r"\s+", " ", result).strip()


class QueryNormalizeNode(FunctionNode):
    """Parse and normalize a regulatory document search query.

    Extracts structured search parameters:
    - normalized_query: term-expanded query string for semantic search
    - search_keywords: keyword list for hybrid keyword matching
    - document_sources: target registries (EDINET / FSA / BOJ)

    required_trust_level = ANONYMOUS (inner domain node, review finding 5).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # Inner Cat-2 graph is entered via subgraph.invoke(user_input=...), where
        # RegulatorySearchGraphNode.extract_input() forwards the outer
        # validated_input as the inner graph's user_input. So inside the inner
        # graph the sanitized query arrives as user_input; prefer validated_input
        # (direct unit invocation) and fall back to user_input (inner-graph run).
        validated_input: str = state.get("validated_input") or state.get("user_input", "") or ""
        options: Dict[str, Any] = state.get("search_options") or {}

        if not validated_input.strip():
            logger.error("QueryNormalizeNode: validated_input is missing")
            emit_trace_event("query_normalize_rejected", {"reason": "missing_query"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "QueryNormalizeNode: validated_input is missing -- " "PreProcessNode [S-1] gate may not have run"
                ],
            }

        normalized_query = _normalize_query(validated_input)
        search_keywords = _extract_keywords(validated_input)

        # A caller-named registry set is authoritative; the heuristic only picks
        # when the caller expressed no preference.
        if options.get("sources_from_caller"):
            document_sources = list(options.get("sources") or _ALL_SOURCES)
        else:
            document_sources = _detect_sources(validated_input)

        logger.info(
            "QueryNormalizeNode: normalized query -- keywords=%d sources=%s",
            len(search_keywords),
            document_sources,
        )

        # S-4 audit trail
        emit_trace_event(
            "query_normalize_complete",
            {
                "keyword_count": len(search_keywords),
                "document_sources": document_sources,
                "sources_from_caller": bool(options.get("sources_from_caller")),
                "query_length": len(normalized_query),
            },
            state,
        )

        return {
            "normalized_query": normalized_query,
            "search_keywords": search_keywords,
            "document_sources": document_sources,
            "status": AgentStatus.SUCCESS.value,
        }
