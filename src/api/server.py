"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, AgentGateway calls agent.invoke() directly.

import json
import os
import secrets
from typing import Any, Dict
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from framework.security.credential_detector import detect_credentials_in_value
from shared.secrets import factory as secrets_factory
from src.graph.graph import FinancialRegulatoryFilingSearchAgent
from src.services.runtime_config import load_runtime_config
from src.services.search_options import CONTEXT_FIELDS, resolve_search_options
from src.services.security import (
    MAX_CONTEXT_KEYS,
    MAX_QUERY_LENGTH,
    ContextValidationError,
    mask_field_name,
)

app = FastAPI(title="Agent")

# The runtime configuration is loaded here, not defaulted away. On the platform
# AgentRegistry constructs the graph as Graph(config=...); a standalone adapter
# has to do the same or every value in config/config.yaml is dead.
agent = FinancialRegulatoryFilingSearchAgent(config=load_runtime_config())
agent.compile()
# Replace namespace/agent_name to match the agent's manifest values.
agent.provision_secrets(secrets_factory(namespace="fin", agent_name="FinancialRegulatoryFilingSearchAgent"))

# Adapter-level caps. The request body is bounded before anything downstream
# sees it, so an oversized payload is refused here rather than consuming the
# pipeline. The query is capped again (and lower) by the input gate.
MAX_INPUT_CHARS = 32_000
MAX_CONTEXT_BYTES = 4_096


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""
    input_context: Dict[str, Any] = Field(default_factory=dict)


def _screen_context_credentials(context: Dict[str, Any]) -> None:
    """Refuse a credential-shaped value on the context channel, naming the field.

    Without this the request fails anyway, but opaquely: InitializeNode returns
    input_context verbatim in its own result, the framework's output gate scans
    every value of every result, and the run therefore dies at the FIRST node
    with a traceback the caller cannot act on. The screen calls the same
    framework function that gate calls, so the refusal set matches the block set
    exactly — no local approximation that could drift apart from it.

    detect_credentials_in_value(dict) is defined as the union over .values(), so
    scanning per field is exactly equivalent to scanning the whole mapping; that
    identity is what lets the message name the field without widening or
    narrowing what is blocked. The value itself is never echoed.
    """
    for index, (name, value) in enumerate(context.items(), start=1):
        if detect_credentials_in_value(value):
            label = mask_field_name(name)
            where = f"input_context['{label}']" if label == name else f"input_context field #{index}"
            # 400, not 422: pydantic owns 422 and answers there with a list of
            # error objects, so reusing it makes client handling ambiguous.
            raise HTTPException(
                status_code=400,
                detail=f"{where} looks like a credential and was refused. Remove it and retry.",
            )


def _validated_context(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return the bounded caller context, or raise HTTP 400 naming the field."""
    if not raw:
        return {}
    if len(raw) > MAX_CONTEXT_KEYS:
        raise HTTPException(status_code=400, detail=f"input_context accepts at most {MAX_CONTEXT_KEYS} fields.")
    if len(json.dumps(raw, default=str).encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise HTTPException(status_code=400, detail=f"input_context exceeds {MAX_CONTEXT_BYTES} bytes.")
    unknown = [k for k in raw if k not in CONTEXT_FIELDS]
    if unknown:
        names = ", ".join(mask_field_name(k) for k in sorted(unknown, key=str))
        raise HTTPException(status_code=400, detail=f"input_context has unsupported field(s): {names}")
    _screen_context_credentials(raw)
    try:
        # Resolve against the same deployment defaults the graph will use, so a
        # value that the pipeline would refuse is refused here instead, with a
        # status code and a field name.
        resolve_search_options(agent.config, raw)
    except ContextValidationError as exc:
        # The message names the field and the expected shape, never the value.
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return dict(raw)


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> Any:
    if len(req.input) > MAX_INPUT_CHARS:
        raise HTTPException(status_code=400, detail=f"input exceeds {MAX_INPUT_CHARS} characters.")
    input_context = _validated_context(req.input_context)

    trust = getattr(request.state, "trust_level", TrustLevel.ANONYMOUS)
    # Standalone caller auth: when INVOKE_AUTH_TOKEN is set on the server
    # environment, callers that no upstream middleware vouched for (still
    # ANONYMOUS) must present it as a Bearer token and run at
    # VERIFIED_EXTERNAL. Middleware-established trust is never demoted.
    #
    # This matters more than it looks: the input gate declares
    # VERIFIED_EXTERNAL, so an adapter that always hands the graph ANONYMOUS
    # cannot serve a single request — the trust gate refuses before any node
    # runs, and the deployment check reports the agent as unresponsive with
    # nothing pointing at the cause.
    #
    # This adapter is the entry-point auth boundary — a deployment-level caller
    # credential, not an agent secret, so ctx.secrets does not apply (no
    # InvocationContext exists before auth).
    expected = os.environ.get("INVOKE_AUTH_TOKEN")
    if expected and trust is TrustLevel.ANONYMOUS:
        supplied = request.headers.get("authorization", "")
        # Compare bytes: compare_digest raises TypeError on non-ASCII str input
        # (headers decode as latin-1), which would 500 instead of the generic 401.
        if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            # Generic body on purpose — do not leak whether the token was absent,
            # malformed, or wrong.
            raise HTTPException(status_code=401, detail="Token is invalid or expired.")
        trust = TrustLevel.VERIFIED_EXTERNAL

    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        return agent.invoke(req.input, ctx=ctx, input_context=input_context)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "agent": "FinancialRegulatoryFilingSearchAgent",
        "max_query_chars": MAX_QUERY_LENGTH,
    }
