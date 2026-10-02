"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants -- never plain strings [A1]
#  - Never import from mediator/, api/, or other agents
#
# FIN-C2-090 -- SummaryGenerateNode
# Inner domain graph node 5: generate a narrative summary from the top-N
# ranked results.
#
# Input:  state["ranked_results"]   -- re-ranked chunks from RankResultsNode
#         state["normalized_query"] -- query string (for summary heading)
#         state["search_options"]   -- resolved retrieval parameters (top_n,
#                                      request_ref, date window)
# Output: state["search_summary"]   -- narrative text summary
#         state["result"]           -- primary output string read by PostProcessNode
#
# THE RENDER BOUNDARY. Everything the caller controls that appears in the
# report -- the query and the request reference -- passes through render_safe()
# or the inert-identifier alphabet first. This is what makes it structurally
# impossible for a caller to forge an extra numbered result entry by embedding
# newlines in the query: a result entry begins at the start of a line, and no
# caller-supplied line break survives to the render.
#
# The rounding/precision grid that financial REPORT templates enforce does not
# apply here: this agent renders no monetary aggregate. Its output invariant is
# the one above plus the credential gate in post_process. See docs/02.
#
# S-2: summary never includes raw credentials or API keys.
# required_trust_level = ANONYMOUS: inner domain graph node (review finding 5).
# S-4: emits "summary_generate_complete" trace event.

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.security import render_safe

logger = logging.getLogger(__name__)

_DEFAULT_TOP_N = 3

# Caps on what a single rendered entry may contribute. Registry titles and
# snippets are third-party data, so they are bounded here as well: without a cap
# one long snippet sets the size of the whole report.
_MAX_RENDERED_TITLE = 200
_MAX_RENDERED_SNIPPET = 600
_MAX_RENDERED_QUERY = 200


def _entry_field(value: Any, limit: int) -> str:
    """Render one registry-supplied field as a single bounded line."""
    return render_safe(str(value or ""), max_length=limit)


def _build_summary(
    query: str,
    results: List[Dict[str, Any]],
    top_n: int,
    request_ref: str = "",
    window: str = "",
) -> str:
    """Build a structured text summary from the top-N ranked results.

    A deployment replaces this with a generation call that synthesises the
    retrieved snippets into a prose answer. The rendering rules above hold
    either way: the caller's text is bounded and single-line before it is
    placed into the document.
    """
    safe_query = render_safe(query, max_length=_MAX_RENDERED_QUERY)
    header: List[str] = ["Regulatory Filing Search Results"]
    if request_ref:
        header.append(f"Request: {request_ref}")
    header.append(f"Query: {safe_query}")
    if window:
        header.append(f"Filed between: {window}")

    if not results:
        return "\n".join(
            header
            + [
                "",
                "No regulatory documents matched this query.",
                "Refine the search terms, widen the date window, or add registries.",
            ]
        )

    top_results = results[:top_n]

    lines = header + [
        "",
        f"Found {len(results)} relevant documents. Top {len(top_results)} results:",
        "",
    ]

    for i, chunk in enumerate(top_results, start=1):
        source = _entry_field(chunk.get("source"), 16)
        title = _entry_field(chunk.get("title"), _MAX_RENDERED_TITLE)
        snippet = _entry_field(chunk.get("content_snippet"), _MAX_RENDERED_SNIPPET)
        date = _entry_field(chunk.get("filing_date"), 32)
        company = _entry_field(chunk.get("company"), 120)
        raw_score = chunk.get("rank_score")
        if raw_score is None:
            raw_score = chunk.get("score", 0.0)
        score = float(raw_score)

        lines.append(f"{i}. [{source}] {title}")
        if company:
            lines.append(f"   Company: {company}")
        if date:
            lines.append(f"   Date: {date}")
        lines.append(f"   Relevance: {score:.2f}")
        lines.append(f"   Excerpt: {snippet}")
        lines.append("")

    return "\n".join(lines).strip()


class SummaryGenerateNode(FunctionNode):
    """Generate a narrative summary from the top-N re-ranked regulatory results.

    Produces search_summary (narrative text) and result (the primary output
    field read by PostProcessNode).

    Input state keys:
        ranked_results:   list of ranked chunk dicts from RankResultsNode
        normalized_query: query string for the summary heading
        search_options:   resolved retrieval parameters (summary_top_n,
                          request_ref, filed_from/filed_to)

    Output state keys (partial dict):
        search_summary: formatted text summary of top-N results
        result:         same as search_summary (primary output field)

    required_trust_level = ANONYMOUS (inner domain node, review finding 5).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ranked_results: List[Dict[str, Any]] = state.get("ranked_results") or []
        normalized_query: str = state.get("normalized_query") or state.get("validated_input", "") or ""
        options: Dict[str, Any] = state.get("search_options") or {}

        top_n: int = int(options.get("summary_top_n", _DEFAULT_TOP_N))
        request_ref: str = str(options.get("request_ref") or "")
        filed_from = str(options.get("filed_from") or "")
        filed_to = str(options.get("filed_to") or "")
        window = f"{filed_from or 'earliest'} .. {filed_to or 'latest'}" if (filed_from or filed_to) else ""

        summary = _build_summary(normalized_query, ranked_results, top_n, request_ref, window)

        if not summary:
            logger.warning("SummaryGenerateNode: summary is empty")
            emit_trace_event("summary_generate_failed", {"result_count": len(ranked_results)}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["SummaryGenerateNode: failed to generate summary"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + ("SummaryGenerateNode: failed to generate summary"),
            }

        logger.info(
            "SummaryGenerateNode: summary generated -- %d chars, %d results",
            len(summary),
            len(ranked_results),
        )

        # S-4 audit trail
        emit_trace_event(
            "summary_generate_complete",
            {
                "result_count": len(ranked_results),
                "summary_length": len(summary),
                "top_n": top_n,
                "request_ref": request_ref,
            },
            state,
        )

        return {
            "search_summary": summary,
            "result": summary,
            "status": AgentStatus.SUCCESS.value,
        }
