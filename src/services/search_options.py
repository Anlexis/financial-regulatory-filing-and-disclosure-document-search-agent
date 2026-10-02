"""AgentCore Platform v1.0"""

# Resolves the retrieval parameters the pipeline actually runs on.
#
# Two inputs, one output:
#   config/config.yaml  -> deployment defaults (runtime_config, seeded into
#                          state by the outer graph)
#   input_context       -> the caller's per-request overrides
#
# Every caller value is re-validated here even though the HTTP adapter already
# validated it, because `agent.invoke(..., input_context=...)` is a supported
# entry point that never passes through the adapter. Both paths share the
# helpers in src/services/security.py, so the two block sets are identical by
# construction rather than by inspection.
#
# Absent caller data degrades to the deployment default; an absent default
# degrades to the constant below. Nothing here fails open: a value that is
# present and wrong raises ContextValidationError naming the field.

from __future__ import annotations

from typing import Any, Dict, List

from src.services.security import (
    KNOWN_SOURCES,
    MAX_CONTEXT_KEYS,
    MIN_SCORE_MAX,
    MIN_SCORE_MIN,
    SUMMARY_TOP_N_MAX,
    SUMMARY_TOP_N_MIN,
    TOP_K_MAX,
    TOP_K_MIN,
    ContextValidationError,
    bounded_int,
    finite_in_range,
    mask_field_name,
    validate_context_identifier,
    validate_iso_date,
    validate_sources,
)

# Last-resort defaults, used only when config/config.yaml declares nothing.
FALLBACK_TOP_K = 5
FALLBACK_SUMMARY_TOP_N = 3
FALLBACK_MIN_SCORE = 0.0

# The caller-settable surface. Anything outside it is refused rather than
# ignored: an ignored key is a key the caller believes had an effect.
CONTEXT_FIELDS: tuple[str, ...] = (
    "sources",
    "top_k",
    "summary_top_n",
    "min_score",
    "filed_from",
    "filed_to",
    "request_ref",
)


def _config_block(runtime_config: Dict[str, Any] | None, key: str) -> Dict[str, Any]:
    block = (runtime_config or {}).get(key)
    return block if isinstance(block, dict) else {}


def resolve_search_options(
    runtime_config: Dict[str, Any] | None,
    input_context: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Return the bounded retrieval parameters for this request.

    Raises ContextValidationError, naming the field, on any caller value that is
    absent-shaped, out of range, non-finite or outside the closed set.
    """
    context: Dict[str, Any] = dict(input_context or {})
    if len(context) > MAX_CONTEXT_KEYS:
        raise ContextValidationError(f"input_context accepts at most {MAX_CONTEXT_KEYS} fields")
    unknown = [k for k in context if k not in CONTEXT_FIELDS]
    if unknown:
        names = ", ".join(mask_field_name(k) for k in sorted(unknown, key=str))
        raise ContextValidationError(f"input_context has unsupported field(s): {names}")

    retrieval = _config_block(runtime_config, "retrieval")
    summary = _config_block(runtime_config, "summary")

    # Deployment defaults go through the same parser as caller values: a bad
    # value in config/config.yaml is a deployment error, not a silent default.
    top_k = bounded_int(
        "retrieval.top_k",
        retrieval.get("top_k", FALLBACK_TOP_K),
        minimum=TOP_K_MIN,
        maximum=TOP_K_MAX,
    )
    summary_top_n = bounded_int(
        "summary.top_n",
        summary.get("top_n", FALLBACK_SUMMARY_TOP_N),
        minimum=SUMMARY_TOP_N_MIN,
        maximum=SUMMARY_TOP_N_MAX,
    )
    min_score = finite_in_range(
        "retrieval.min_score",
        retrieval.get("min_score", FALLBACK_MIN_SCORE),
        minimum=MIN_SCORE_MIN,
        maximum=MIN_SCORE_MAX,
    )

    sources: List[str] = list(KNOWN_SOURCES)
    caller_sources = False
    if "sources" in context:
        chosen = validate_sources("sources", context["sources"])
        if chosen:
            sources = chosen
            caller_sources = True

    if "top_k" in context:
        top_k = bounded_int("input_context['top_k']", context["top_k"], minimum=TOP_K_MIN, maximum=TOP_K_MAX)
    if "summary_top_n" in context:
        summary_top_n = bounded_int(
            "input_context['summary_top_n']",
            context["summary_top_n"],
            minimum=SUMMARY_TOP_N_MIN,
            maximum=SUMMARY_TOP_N_MAX,
        )
    if "min_score" in context:
        min_score = finite_in_range(
            "input_context['min_score']",
            context["min_score"],
            minimum=MIN_SCORE_MIN,
            maximum=MIN_SCORE_MAX,
        )

    filed_from = validate_iso_date("filed_from", context["filed_from"]) if "filed_from" in context else ""
    filed_to = validate_iso_date("filed_to", context["filed_to"]) if "filed_to" in context else ""
    if filed_from and filed_to and filed_from > filed_to:
        raise ContextValidationError("input_context['filed_from'] must not be later than input_context['filed_to']")

    request_ref = validate_context_identifier("request_ref", context["request_ref"]) if "request_ref" in context else ""

    return {
        "sources": sources,
        "sources_from_caller": caller_sources,
        "top_k": top_k,
        "summary_top_n": summary_top_n,
        "min_score": min_score,
        "filed_from": filed_from,
        "filed_to": filed_to,
        "request_ref": request_ref,
    }
