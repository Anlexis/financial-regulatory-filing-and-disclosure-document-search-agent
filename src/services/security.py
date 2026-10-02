"""AgentCore Platform v1.0"""

# Caller-facing input screening and bounds for FIN-C2-090.
#
# Pure, stateless helpers (NOT framework gate methods). Everything a caller can
# influence -- the free-text search query and every field of the structured
# search context -- passes through this module before it reaches the pipeline.
#
#   sanitize_query()             strips markup and caps length
#   screen_injection()           refuses prompt-injection payloads; run it on the
#                                RAW text AND on the sanitized text, because the
#                                markup strip both deletes control tokens and
#                                re-assembles directives that were split by tags
#   validate_context_identifier() locks every caller value that renders into the
#                                report to a short inert identifier
#   finite_in_range()            parses a caller number fail-CLOSED, so a
#                                non-finite value raises instead of comparing
#                                False against the bound it is meant to set
#   detect_output_credentials()  the UNION of this template's own patterns and
#                                the framework detector -- see the note below
#
# No helper here ever echoes a rejected value: they name the field.

from __future__ import annotations

import math
import re
from typing import Any

from framework.security.credential_detector import detect_credentials

# --------------------------------------------------------------------------
# Query bounds
# --------------------------------------------------------------------------

MAX_QUERY_LENGTH = 1000

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Everything below 0x20 plus DEL. Whitespace is collapsed separately; these are
# the characters that carry no meaning in a search query but do carry meaning in
# a terminal or a rendered document (escape sequences, bell, NUL).
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE_RUN_RE = re.compile(r"\s+")

# --------------------------------------------------------------------------
# Structured search-context bounds
# --------------------------------------------------------------------------

# The context channel carries retrieval parameters and one caller reference that
# is rendered back into the report. Deliberately inert: a short alphanumeric run
# with `_` and `-`, nothing that can carry markup, whitespace, a directive or a
# credential-shaped blob.
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
IDENTIFIER_MAX_LENGTH = 32

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

MAX_CONTEXT_KEYS = 8
MAX_CONTEXT_VALUE_CHARS = 64
MAX_SOURCES = 3

# The registries this template can search. A closed set: an unknown source is
# refused rather than silently ignored, so a caller never believes a registry
# was consulted when it was not.
KNOWN_SOURCES = ("EDINET", "FSA", "BOJ")

# Retrieval bounds. Every one of these is caller-settable, so every one is
# parsed through finite_in_range().
TOP_K_MIN, TOP_K_MAX = 1, 20
SUMMARY_TOP_N_MIN, SUMMARY_TOP_N_MAX = 1, 10
MIN_SCORE_MIN, MIN_SCORE_MAX = 0.0, 1.0

# --------------------------------------------------------------------------
# Injection screens
# --------------------------------------------------------------------------

# Chat-template control tokens are their own attack class. They are not
# directive PHRASES, so a phrase-based screen misses them entirely, and the
# markup strip silently deletes `<|...|>` (it looks like a tag) while forwarding
# whatever followed it as ordinary text. The framework's own input policy blocks
# `<|im_start|>` and `[INST]` but scores `<<SYS>>` as no finding, so this
# template cannot delegate the class.
_CONTROL_TOKEN_RE = re.compile(
    r"<\|[^\n|]{0,64}\|>"  # <|im_start|>, <|endoftext|>, <|system|>
    r"|\[/?INST\]"  # [INST] / [/INST]
    r"|<</?SYS>>",  # <<SYS>> / <</SYS>>
    re.IGNORECASE,
)

# Directive forms. Anchored at a statement boundary and requiring an explicit
# instruction object, so ordinary regulatory-search wording is untouched:
# "disclosure of prior period adjustments", "filings that override earlier
# guidance" and "show me the FSA circular on AI model risk" must all pass.
_DIRECTIVE_RE = re.compile(
    r"(?:\A|[.!?;\n]\s*)(?:please\s+)?"
    r"(?:ignore|disregard|forget|override|bypass)\s+"
    r"(?:all\s+|any\s+|the\s+|your\s+)*"
    r"(?:previous|prior|earlier|above|preceding|system|these|those)\s+"
    r"(?:instructions?|rules?|prompts?|directives?|constraints?)",
    re.IGNORECASE,
)
_ROLE_HIJACK_RE = re.compile(
    r"(?:\A|[.!?;\n]\s*)you\s+are\s+(?:now|no\s+longer)\b",
    re.IGNORECASE,
)
_PROMPT_EXFIL_RE = re.compile(
    r"(?:reveal|print|show|repeat|output|display|dump)\s+(?:me\s+)?"
    r"(?:your\s+(?:system\s+)?(?:prompt|instructions?)|the\s+system\s+prompt)",
    re.IGNORECASE,
)
# Statement-shaped SQL, not a bare verb. "Insert into the trust holdings note"
# is ordinary filing prose; `INSERT INTO filings VALUES` is not.
_SQL_INJECTION_RE = re.compile(
    r"'\s*or\s+1\s*=\s*1"
    r"|\bunion\s+all?\s*select\b"
    r"|\bunion\s+select\b"
    r"|\bdrop\s+table\s+\w"
    r"|;\s*drop\b"
    r"|\binsert\s+into\s+\w+\s*(?:\(|values\b)"
    r"|\bdelete\s+from\s+\w",
    re.IGNORECASE,
)
_SCRIPT_TAG_RE = re.compile(r"</?\s*script\b", re.IGNORECASE)

_SCREENS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("control_token", _CONTROL_TOKEN_RE),
    ("instruction_override", _DIRECTIVE_RE),
    ("role_hijack", _ROLE_HIJACK_RE),
    ("prompt_exfiltration", _PROMPT_EXFIL_RE),
    ("sql_injection", _SQL_INJECTION_RE),
    ("script_tag", _SCRIPT_TAG_RE),
)

# --------------------------------------------------------------------------
# Output credential patterns
# --------------------------------------------------------------------------

# This template's own patterns. They are kept IN ADDITION to the framework
# detector, never instead of it:
#   * the framework describes credential FORMATS (sk_live_, sk-, eyJ, AKIA,
#     Bearer, db URIs) and matches none of the `password=...` assignment shape;
#   * this list misses every framework format it does not name.
# Whichever set is narrower is a bypass, so the gate takes the union.
_LOCAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_key", re.compile(r"\b(?:sk|pk|ak)-[A-Za-z0-9]{16,}", re.IGNORECASE)),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/]{20,}", re.IGNORECASE)),
    (
        "credential_assignment",
        re.compile(
            r"\b(?:password|passwd|secret|api_key|token|access_key|private_key)\s*[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
)


class ContextValidationError(ValueError):
    """A caller-supplied value failed its bounds check.

    The message names the offending FIELD and the expected shape. It never
    contains the rejected value -- an error log is an output surface too.
    """


# --------------------------------------------------------------------------
# Query helpers
# --------------------------------------------------------------------------


def sanitize_query(query: str, max_length: int = MAX_QUERY_LENGTH) -> str:
    """Strip markup tags and cap length.

    Sanitizing is not refusal: this only removes markup, and removing markup can
    make an attack HARDER to see (a stripped `<|im_start|>` leaves plain
    directive text behind). Always call :func:`screen_injection` on both the raw
    and the sanitized string.
    """
    return _HTML_TAG_RE.sub("", query)[:max_length]


def screen_injection(*texts: str) -> str | None:
    """Return the class of the first screened form found, else None.

    Pass every representation of the caller's text -- raw AND sanitized --
    because the two hide different attacks from each other.
    """
    for text in texts:
        if not text:
            continue
        for label, pattern in _SCREENS:
            if pattern.search(text):
                return label
    return None


def render_safe(text: str, max_length: int = 200) -> str:
    """Return *text* reduced to a single inert line safe to render in the report.

    The search query is echoed back in the result header, so a caller controls
    part of a document other people read. Control characters are removed and
    every whitespace run collapses to one space, which is what makes it
    structurally impossible for a caller to manufacture what looks like an
    additional numbered result entry. The collapse is stated here as the
    invariant rather than left as a side effect of query normalisation.
    """
    cleaned = _CONTROL_CHAR_RE.sub(" ", text)
    cleaned = _WHITESPACE_RUN_RE.sub(" ", cleaned).strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip() + "..."
    return cleaned


# --------------------------------------------------------------------------
# Context-field helpers
# --------------------------------------------------------------------------


def mask_field_name(name: Any) -> str:
    """Render an unrecognised context key safely for an error message.

    A rejected key is caller-controlled text that would otherwise be echoed into
    an error log verbatim. Recognisable keys are short and inert; anything else
    is reported by shape, never by content.
    """
    text = name if isinstance(name, str) else type(name).__name__
    if IDENTIFIER_RE.match(text) and not detect_credentials(text):
        return text
    return f"<unrecognised field, {len(text)} chars>"


def validate_context_identifier(field: str, value: Any) -> str:
    """Return *value* as an inert identifier, or raise ContextValidationError."""
    if not isinstance(value, str):
        raise ContextValidationError(
            f"input_context['{field}'] must be a string identifier matching "
            f"[A-Za-z0-9][A-Za-z0-9_-]{{0,{IDENTIFIER_MAX_LENGTH - 1}}}"
        )
    if len(value) > MAX_CONTEXT_VALUE_CHARS:
        raise ContextValidationError(f"input_context['{field}'] exceeds {MAX_CONTEXT_VALUE_CHARS} characters")
    candidate = value.strip()
    if not candidate:
        return ""
    if not IDENTIFIER_RE.match(candidate):
        raise ContextValidationError(
            f"input_context['{field}'] must match " f"[A-Za-z0-9][A-Za-z0-9_-]{{0,{IDENTIFIER_MAX_LENGTH - 1}}}"
        )
    return candidate


def validate_iso_date(field: str, value: Any) -> str:
    """Return *value* as a YYYY-MM-DD date string, or raise.

    The comparison downstream is lexicographic over the fixed-width ISO form,
    which is why the shape is enforced here rather than parsed loosely.
    """
    if not isinstance(value, str):
        raise ContextValidationError(f"input_context['{field}'] must be a YYYY-MM-DD date string")
    candidate = value.strip()
    if not candidate:
        return ""
    if len(candidate) > MAX_CONTEXT_VALUE_CHARS or not ISO_DATE_RE.match(candidate):
        raise ContextValidationError(f"input_context['{field}'] must be a YYYY-MM-DD date string")
    month = int(candidate[5:7])
    day = int(candidate[8:10])
    if not (1 <= month <= 12) or not (1 <= day <= 31):
        raise ContextValidationError(f"input_context['{field}'] must be a valid calendar date (YYYY-MM-DD)")
    return candidate


def validate_sources(field: str, value: Any) -> list[str]:
    """Return the requested registries as a deduplicated closed-set list."""
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ContextValidationError(
            f"input_context['{field}'] must be a list of registry codes ({', '.join(KNOWN_SOURCES)})"
        )
    if len(value) > MAX_SOURCES:
        raise ContextValidationError(f"input_context['{field}'] accepts at most {MAX_SOURCES} registry codes")
    chosen: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ContextValidationError(
                f"input_context['{field}'] entries must be registry codes ({', '.join(KNOWN_SOURCES)})"
            )
        code = item.strip().upper()
        if code not in KNOWN_SOURCES:
            raise ContextValidationError(f"input_context['{field}'] entries must be one of {', '.join(KNOWN_SOURCES)}")
        if code not in chosen:
            chosen.append(code)
    return chosen


def finite_in_range(field: str, value: Any, *, minimum: float, maximum: float) -> float:
    """Parse a number fail-CLOSED: finite, in range, not a bool.

    ``float("nan")`` and ``float("inf")`` parse without error and then compare
    False against every bound, so an unchecked non-finite value silently
    disables the very limit it configures. JSON delivers bare ``NaN`` /
    ``Infinity`` too, so this is reachable from data, not only from code.
    """
    if isinstance(value, bool):
        raise ContextValidationError(f"{field} must be a number, not a boolean")
    if not isinstance(value, (int, float, str)):
        raise ContextValidationError(f"{field} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ContextValidationError(f"{field} must be a number") from None
    if not math.isfinite(parsed):
        raise ContextValidationError(f"{field} must be a finite number")
    if not (minimum <= parsed <= maximum):
        raise ContextValidationError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def bounded_int(field: str, value: Any, *, minimum: int, maximum: int) -> int:
    """Parse a whole number fail-CLOSED through :func:`finite_in_range`."""
    parsed = finite_in_range(field, value, minimum=float(minimum), maximum=float(maximum))
    if parsed != int(parsed):
        raise ContextValidationError(f"{field} must be a whole number")
    return int(parsed)


# --------------------------------------------------------------------------
# Output gate
# --------------------------------------------------------------------------


def detect_output_credentials(content: str) -> str | None:
    """Return the class of the first credential found in *content*, else None.

    The union of this template's patterns and the framework's own detector.
    The framework scans every value of every node result and RAISES on a match;
    a template gate narrower than that detector therefore never gets to contain
    anything -- the raise is caught by the node wrapper, which discards the
    gate's cleared fields and returns a bare error partial, leaving the
    un-gated inner answer in state for the output projection to fall back to.
    Taking the union is what keeps the gate ahead of the framework.
    """
    if not isinstance(content, str) or not content:
        return None
    for label, pattern in _LOCAL_PATTERNS:
        if pattern.search(content):
            return label
    findings = detect_credentials(content)
    if findings:
        return str(findings[0]["type"])
    return None
