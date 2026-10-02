"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants -- never plain strings [A1]
#  - Never import from mediator/, api/, or other agents
#
# FIN-C2-090 -- APIFetchNode
# Inner domain graph node 2: fetch document metadata from EDINET REST API,
# FSA circular index, and BOJ publications registry.
#
# Input:  state["search_keywords"]  -- keyword list from QueryNormalizeNode
#         state["document_sources"] -- target source list (EDINET/FSA/BOJ)
#         state["search_options"]   -- resolved retrieval parameters, including
#                                      the caller's filed_from / filed_to window
# Output: state["raw_documents"]    -- list of document metadata dicts
#         state["fetch_metadata"]   -- fetch stats (source counts, latency)
#
# Data source: sample registry below. A deployment replaces _fetch_documents()
# with real EDINET / FSA / BOJ calls; credentials come from the secrets provider
# and MUST NOT appear in State (checkpoint DB leakage).
#
# required_trust_level = ANONYMOUS: inner domain graph node (review finding 5).
# S-4: emits "api_fetch_complete" trace event (no credentials in payload).
# S-5: no hardcoded credentials -- production uses secrets provider.

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.security import KNOWN_SOURCES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sample registry data (stands in for the real EDINET/FSA/BOJ API calls)
# ---------------------------------------------------------------------------
_SAMPLE_EDINET_DOCUMENTS = [
    {
        "source": "EDINET",
        "doc_id": "E00001-S100ZZZ1",
        "company": "Pharma Corp A",
        "type": "securities_report",
        "title": "Annual Securities Report FY2023 -- Pharma Corp A",
        "content_snippet": "AI drug discovery risk factors include regulatory approval uncertainty, "
        "competitive pipeline risks, and clinical trial failure rates.",
        "filing_date": "2024-06-28",
    },
    {
        "source": "EDINET",
        "doc_id": "E00002-S100ZZZ2",
        "company": "Biotech Inc B",
        "type": "securities_report",
        "title": "Annual Securities Report FY2023 -- Biotech Inc B",
        "content_snippet": "Our AI-powered drug discovery platform leverages machine learning "
        "for target identification; key risk: regulatory pathway uncertainty.",
        "filing_date": "2024-07-01",
    },
    {
        "source": "EDINET",
        "doc_id": "E00003-S100ZZZ3",
        "company": "Regional Bank C",
        "type": "securities_report",
        "title": "Annual Securities Report FY2022 -- Regional Bank C",
        "content_snippet": "Climate transition risk in the loan book is assessed under the "
        "ESG disclosure framework; carbon-intensive exposures are reported.",
        "filing_date": "2023-06-29",
    },
]

_SAMPLE_FSA_DOCUMENTS = [
    {
        "source": "FSA",
        "doc_id": "FSA-2024-001",
        "title": "Supervisory Guidelines for AI Model Risk Management at Banks",
        "type": "circular",
        "content_snippet": "Financial institutions using AI for credit scoring must implement "
        "model validation frameworks per FSA AI governance guidelines.",
        "publish_date": "2024-03-15",
    },
    {
        "source": "FSA",
        "doc_id": "FSA-2023-014",
        "title": "Guidance on Climate-Related Financial Disclosure for Listed Companies",
        "type": "circular",
        "content_snippet": "Listed companies should disclose climate transition risk and "
        "carbon reduction targets in line with the ESG guidance.",
        "publish_date": "2023-11-30",
    },
]

_SAMPLE_BOJ_DOCUMENTS = [
    {
        "source": "BOJ",
        "doc_id": "BOJ-WP-2024-01",
        "title": "NISA 2.0 and Retail Investor Participation in Japanese Equity Markets",
        "type": "working_paper",
        "content_snippet": "NISA 2.0 accounts have reached 20 million; retail investor disclosure "
        "access remains a key democratization challenge.",
        "publish_date": "2024-05-10",
    },
]

_SOURCE_DOCUMENT_MAP: Dict[str, List[Dict[str, Any]]] = {
    "EDINET": _SAMPLE_EDINET_DOCUMENTS,
    "FSA": _SAMPLE_FSA_DOCUMENTS,
    "BOJ": _SAMPLE_BOJ_DOCUMENTS,
}

# Structural cap on the fetch result set, independent of any caller parameter.
_MAX_DOCUMENTS = 200


def _document_date(document: Dict[str, Any]) -> str:
    """Return the document's ISO date, whichever field carries it."""
    return str(document.get("filing_date") or document.get("publish_date") or "")


def _in_window(document: Dict[str, Any], filed_from: str, filed_to: str) -> bool:
    """Whether the document falls inside the caller's date window.

    Both bounds are fixed-width ISO dates validated at the caller boundary, so
    a lexicographic comparison is exact. A document with no date is kept only
    when the caller asked for no window.
    """
    if not filed_from and not filed_to:
        return True
    date = _document_date(document)
    if not date:
        return False
    if filed_from and date < filed_from:
        return False
    if filed_to and date > filed_to:
        return False
    return True


def _fetch_documents(
    sources: List[str],
    keywords: List[str],
    filed_from: str = "",
    filed_to: str = "",
) -> List[Dict[str, Any]]:
    """Return the registry documents matching the request.

    A deployment replaces this with real HTTP calls to the EDINET / FSA / BOJ
    APIs. Credentials are read from the secrets provider inside the replacement,
    never stored in State.

    Matching rule: a query that carries keywords returns only the documents
    those keywords hit. A query with no usable keywords places no keyword
    constraint, so the requested registries are returned in full. Returning the
    whole corpus for a keyword query that matched nothing would report every
    document as a search hit.
    """
    keyword_set = {k.lower() for k in keywords if k}
    docs: List[Dict[str, Any]] = []
    for source in sources:
        for doc in _SOURCE_DOCUMENT_MAP.get(source, []):
            if not _in_window(doc, filed_from, filed_to):
                continue
            if keyword_set:
                haystack = " ".join([str(doc.get("title") or ""), str(doc.get("content_snippet") or "")]).lower()
                if not any(kw in haystack for kw in keyword_set):
                    continue
            docs.append(doc)
            if len(docs) >= _MAX_DOCUMENTS:
                return docs
    return docs


class APIFetchNode(FunctionNode):
    """Fetch regulatory document metadata from EDINET, FSA, and BOJ.

    Production wires real API calls via the secrets provider. The bundled
    sample registry stands in for them so the pipeline is runnable as shipped.

    Input state keys:
        search_keywords:  keyword list from QueryNormalizeNode
        document_sources: target source list (EDINET / FSA / BOJ)
        search_options:   resolved retrieval parameters (date window)

    Output state keys (partial dict):
        raw_documents:  list of document metadata dicts
        fetch_metadata: dict with source counts and total fetched

    required_trust_level = ANONYMOUS (inner domain node, review finding 5).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        search_keywords: List[str] = state.get("search_keywords") or []
        document_sources: List[str] = state.get("document_sources") or list(KNOWN_SOURCES)
        options: Dict[str, Any] = state.get("search_options") or {}

        if not document_sources:
            logger.warning("APIFetchNode: document_sources is empty; using all sources")
            document_sources = list(KNOWN_SOURCES)

        filed_from = str(options.get("filed_from") or "")
        filed_to = str(options.get("filed_to") or "")

        raw_documents = _fetch_documents(document_sources, search_keywords, filed_from, filed_to)

        source_counts: Dict[str, int] = {}
        for doc in raw_documents:
            src = str(doc.get("source", "unknown"))
            source_counts[src] = source_counts.get(src, 0) + 1

        fetch_metadata: Dict[str, Any] = {
            "total_fetched": len(raw_documents),
            "source_counts": source_counts,
            "sources_queried": document_sources,
            "filed_from": filed_from,
            "filed_to": filed_to,
        }

        logger.info(
            "APIFetchNode: fetched %d documents from %s",
            len(raw_documents),
            document_sources,
        )

        # S-4 audit trail (no credentials in payload)
        emit_trace_event(
            "api_fetch_complete",
            {
                "total_fetched": len(raw_documents),
                "source_counts": source_counts,
                "sources_queried": document_sources,
                "date_window_applied": bool(filed_from or filed_to),
            },
            state,
        )

        return {
            "raw_documents": raw_documents,
            "fetch_metadata": fetch_metadata,
            "status": AgentStatus.SUCCESS.value,
        }
