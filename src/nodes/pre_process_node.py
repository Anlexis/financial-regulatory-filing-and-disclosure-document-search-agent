"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Read input_context via _without_platform_context(state.get("input_context", {})) -- read-only [C1]
#  - Never import from mediator/, api/, or other agents
#
# FIN-C2-090 -- PreProcessNode
# Outer backbone pre_process slot. This node owns the entire caller contract:
# the free-text query AND the structured search context.
#
# S-1: rejects empty/whitespace-only input, enforces a maximum query length,
# and resolves every caller-supplied retrieval parameter through a
# finite+bounded parser (fail CLOSED, naming the field, never the value).
# S-2: rejects non-string input and screens the query for injection forms --
# on the RAW text and again after the markup strip, because the strip both
# deletes chat-template control tokens and re-joins directives split by tags.
#
# The screen lives here rather than being delegated to the framework's input
# policy: that policy scores `<<SYS>>` as no finding, and where it is absent or
# configured off the payload would reach the answer path and return SUCCESS.
# The template owns its own guarantee; the framework policy is an extra layer,
# not the layer.
#
# required_trust_level = VERIFIED_EXTERNAL: this node is the external-caller
# trust gate for FIN-C2-090. All inner domain nodes declare ANONYMOUS
# (review finding 5 -- the backbone pre_process is the ONLY external-facing gate).
#
# S-4: emits "pre_process_validated" on success and "pre_process_rejected"
# on every rejection path.
# Ships in the SDK wheel only -- import resolves at runtime/CI.

import logging
from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.search_options import resolve_search_options
from src.services.security import (
    MAX_QUERY_LENGTH,
    ContextValidationError,
    sanitize_query,
    screen_injection,
)


# The Marketplace runner seeds input_context with its own conversation history on every
# invocation (shared/bootstrap/marketplace_app.py); the caller neither sends that key nor can
# suppress it, and the build_input_context hook can only overwrite its value, never remove it.
# It is platform plumbing rather than caller data, so it is dropped here, before the caller
# contract runs: the unknown-field guard below stays strict for everything a caller can
# actually send, and no value screen is ever asked to judge a transcript that contains this
# agent's own earlier answers. The value may also be None, which this tolerates.
_PLATFORM_CONTEXT_KEYS = frozenset({"conversation_history"})


def _without_platform_context(raw: Any) -> Any:
    """The caller-supplied half of input_context, platform-injected keys removed."""
    if not isinstance(raw, dict):
        return raw
    return {k: v for k, v in raw.items() if k not in _PLATFORM_CONTEXT_KEYS}


logger = logging.getLogger(__name__)

_MAX_QUERY_LENGTH = MAX_QUERY_LENGTH


# Reason codes whose message names the screen that fired rather than a field the caller
# can correct. These refusals publish nothing; every other reason publishes its reason.
_SILENT_REASONS = frozenset({"injection", "context_injection"})


class PreProcessNode(FunctionNode):
    """S-1/S-2 input validation gate for FIN-C2-090.

    Validates the incoming regulatory search query:
    - S-2: reject non-string input (type guard)
    - S-1: reject empty or whitespace-only input
    - S-1: enforce maximum query length (_MAX_QUERY_LENGTH chars)
    - S-2: reject injection forms, including chat-template control tokens
    - S-1: resolve and bound every caller-supplied retrieval parameter

    required_trust_level = VERIFIED_EXTERNAL: this is the external-caller
    gate. Inner domain nodes declare ANONYMOUS (review finding 5).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context: Dict[str, Any] = _without_platform_context(state.get("input_context", {})) or {}

        # S-2 type guard: reject non-string input before any string operation
        if not isinstance(user_input, str):
            logger.warning(
                "PreProcessNode [S-2]: user_input is not a string (type=%s)",
                type(user_input).__name__,
            )
            return self._reject(
                state,
                reason="non_string_input",
                message="PreProcessNode [S-2]: user_input must be a string",
                detail={"input_type": type(user_input).__name__},
            )

        if not user_input or not user_input.strip():
            logger.warning("PreProcessNode [S-1]: user_input is empty")
            return self._reject(
                state,
                reason="empty_input",
                message="PreProcessNode [S-1]: user_input is empty or missing",
                detail={},
            )

        validated = user_input.strip()

        if len(validated) > _MAX_QUERY_LENGTH:
            logger.warning(
                "PreProcessNode [S-1]: query too long (%d chars, max %d)",
                len(validated),
                _MAX_QUERY_LENGTH,
            )
            return self._reject(
                state,
                reason="length_exceeded",
                message=(
                    f"PreProcessNode [S-1]: query exceeds maximum length ({len(validated)} > {_MAX_QUERY_LENGTH})"
                ),
                detail={"query_length": len(validated)},
            )

        # S-2 injection screen -- raw AND markup-stripped, BEFORE validated_input
        # is written. Screening only one of the two forms misses a whole class:
        # the strip removes `<|im_start|>` (raw catches it) and re-assembles
        # "ig<b>nore all previous instructions" (stripped catches it).
        stripped = sanitize_query(validated)
        injection = screen_injection(validated, stripped)
        if injection:
            logger.warning("PreProcessNode [S-2]: injection marker detected (%s)", injection)
            return self._reject(
                state,
                reason="injection",
                message=(f"PreProcessNode [S-2]: input rejected -- injection marker detected ({injection})"),
                detail={"marker": injection},
            )

        # The context channel carries only inert identifiers, numbers and dates,
        # so the same screen applied to its string values costs nothing and
        # closes the drift risk if the contract ever widens.
        context_injection = screen_injection(*[v for v in input_context.values() if isinstance(v, str)])
        if context_injection:
            logger.warning("PreProcessNode [S-2]: injection marker in input_context (%s)", context_injection)
            return self._reject(
                state,
                reason="context_injection",
                message=(
                    f"PreProcessNode [S-2]: input_context rejected -- injection marker detected ({context_injection})"
                ),
                detail={"marker": context_injection},
            )

        # S-1 bounds on every caller-settable retrieval parameter. Resolved here
        # so a direct invoke() -- which never passes through the HTTP adapter --
        # gets the same block set. The message names the field, never the value.
        try:
            search_options = resolve_search_options(state.get("runtime_config"), input_context)
        except ContextValidationError as exc:
            logger.warning("PreProcessNode [S-1]: search context rejected")
            return self._reject(
                state,
                reason="invalid_search_context",
                message=f"PreProcessNode [S-1]: {exc}",
                detail={},
            )

        logger.info("PreProcessNode [S-1]: query validated -- %d chars", len(validated))

        # S-4 audit trail
        emit_trace_event(
            "pre_process_validated",
            {
                "query_length": len(validated),
                "sources": search_options["sources"],
                "top_k": search_options["top_k"],
                "context_fields": sorted(str(k) for k in input_context if k in _SAFE_AUDIT_KEYS),
            },
            state,
        )

        return {
            "validated_input": validated,
            "enriched_context": {
                "source": "FinancialRegulatoryFilingSearchAgent",
                "request_ref": search_options["request_ref"],
            },
            "search_options": search_options,
            "status": AgentStatus.SUCCESS.value,
        }

    def _reject(self, state: Dict[str, Any], *, reason: str, message: str, detail: Dict[str, Any]) -> Dict[str, Any]:
        """Return the rejection delta and audit it.

        `message` names the field and the expected shape; no rejected value is
        ever carried into error_log or into the audit payload.
        """
        emit_trace_event("pre_process_rejected", {"reason": reason, **detail}, state)
        return {
            "status": AgentStatus.ERROR.value,
            "error_log": [message],
            # The runner surfaces `formatted_output or result` as `output`. A reason left only in
            # error_log reaches no one: the terminal result carries just `status`, and get_output()
            # does not copy error_log out of the graph -- the caller sees a blank spinner.
            # A SCREENED refusal stays silent. Its message names the marker that caught the
            # payload, and handing that back lets an attacker probe the screen one try at a
            # time. A VALIDATION refusal says what to fix -- without it the caller cannot tell
            # a rejected request from a hung one.
            **({} if reason in _SILENT_REASONS else {"formatted_output": "Request could not be completed. " + message}),
        }


# Context keys that may be named in an audit payload. A key outside this closed
# set is caller-controlled text and is simply not reported.
_SAFE_AUDIT_KEYS = frozenset(
    {"sources", "top_k", "summary_top_n", "min_score", "filed_from", "filed_to", "request_ref"}
)
