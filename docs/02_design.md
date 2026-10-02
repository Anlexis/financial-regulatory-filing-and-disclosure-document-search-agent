# Template Design Specification — FIN-C2-090

## Position in AgentCore Architecture

| Aspect | Value |
|---|---|
| Agent class | FinancialRegulatoryFilingSearchAgent |
| L1 Base (framework base class) | AgentBaseGraph — direct framework inheritance |
| Composition | Cat 2 two-layer nested (outer backbone + inner BaseGraph) |

- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution)

## Architecture Overview

### Node Configuration

| Node | Responsibility | Input State Keys | Output State Keys | Inherits |
|------|---------------|-----------------|------------------|---------|
| initialize | Set schema_version, session_id, trust_level | — | session_id, schema_version, trust_level | InitializeNode (default) |
| pre_process | S-1/S-2 caller contract: validate query, screen injection, bound every search parameter | user_input, input_context, runtime_config | validated_input, enriched_context, search_options | PreProcessNode (VERIFIED_EXTERNAL) |
| main | Delegate to inner DomainWorkflowGraph (search pipeline) | validated_input, search_options | result, search_summary, ranked_results | RegulatorySearchGraphNode (GraphNode) |
| post_process | S-3 output gate; credential scan and containment | result, search_summary | result, search_summary, formatted_output, status | PostProcessNode (ANONYMOUS) |
| finalize | Build response_metadata, total_time_ms | — | output, response_metadata | FinalizeNode (default) |

### Data Flow

```
START
  → initialize       (framework default — sets session/schema/trust)
  → pre_process      (S-1/S-2: validate query + search context; VERIFIED_EXTERNAL gate)
  → main             (RegulatorySearchGraphNode delegates to inner DomainWorkflowGraph)
      ┌── INNER GRAPH (DomainWorkflowGraph) ───────────────────────────────┐
      │   START                                                             │
      │     → query_normalize   (QueryNormalizeNode: expand & structure)    │
      │     → api_fetch         (APIFetchNode: EDINET / FSA / BOJ fetch)    │
      │     → hybrid_retrieve   (HybridRetrieveNode: keyword scoring)       │
      │     → rank_results      (RankResultsNode: re-rank + relevance floor)│
      │     → summary_generate  (SummaryGenerateNode: render the report)    │
      │   END                                                               │
      └────────────────────────────────────────────────────────────────────┘
  → {route}          (ERROR → pre_process retry; SUCCESS → post_process)
  → post_process     (S-3: credential scan on every output-bearing field)
  → finalize         (framework default — builds response_metadata)
END
```

### Runtime parameter flow

`config/agent.yaml` is the static manifest and carries no runtime block. Every runtime value lives
in `config/config.yaml` and reaches the pipeline along one path:

```
config/config.yaml
  → AgentRegistry (or src/api/server.py when standalone) constructs Graph(config=...)
  → FinancialRegulatoryFilingSearchAgent._extra_initial_state()  →  state["runtime_config"]
  → PreProcessNode resolves defaults + caller overrides          →  state["search_options"]
  → RegulatorySearchGraphNode.extract_input() stashes them       →  src/graph/context_bridge.py
  → DomainWorkflowGraph._extra_initial_state() seeds them        →  inner state["search_options"]
  → retrieval / ranking / rendering nodes read them
```

Two properties of the framework make the last two hops necessary, and both are silent when missed:

- `BaseNode.__call__` invokes `execute(state)` with the state alone. A node that takes its
  configuration from an `execute(state, config=None)` argument always sees `None`, so every value
  declared for it is dead while the pipeline still answers.
- `GraphNode.execute()` invokes the subgraph as `subgraph.invoke(user_input, session_id, ctx)` and
  forwards nothing else — not `input_context`, not the outer state. Without the ContextVar bridge in
  `src/graph/context_bridge.py`, every caller-supplied search parameter is discarded at that
  boundary.

### Caller contract

`POST /invoke` takes the free-text question plus an optional `input_context`. Bounds and the
rejection behaviour are in `README.md`; they are enforced in two places by the same helpers
(`src/services/search_options.py`), so the HTTP path and a direct `agent.invoke()` share one block
set:

- the adapter (`src/api/server.py`), which turns a bad value into HTTP 400 naming the field, and
- `PreProcessNode`, which owns the contract for callers that never pass through the adapter.

Numeric fields go through `finite_in_range()`: `NaN` and `Infinity` parse via `float()` and then
compare False against every bound, so an unchecked non-finite value silently disables the limit it
configures.

### State Definition

| Field | Type | Set By | Purpose |
|-------|------|--------|---------|
| runtime_config | `dict` | outer graph | Parsed `config/config.yaml` |
| validated_input | `str` | pre_process | Validated, screened query string |
| enriched_context | `dict` | pre_process | Request metadata (source, request_ref) |
| search_options | `dict` | pre_process | Resolved, bounded retrieval parameters |
| normalized_query | `str` | QueryNormalizeNode | Expanded search query |
| search_keywords | `list[str]` | QueryNormalizeNode | Extracted keywords (capped at 20) |
| document_sources | `list[str]` | QueryNormalizeNode | Registries to search: EDINET, FSA, BOJ |
| raw_documents | `list[dict]` | APIFetchNode | Documents fetched from the registries |
| fetch_metadata | `dict` | APIFetchNode | Fetch stats (source counts, date window) |
| retrieved_chunks | `list[dict]` | HybridRetrieveNode | Scored chunks, capped at `top_k` |
| ranked_results | `list[dict]` | RankResultsNode | Re-ranked chunks above the relevance floor |
| search_summary | `str` | SummaryGenerateNode | Rendered report |
| result | `str` | SummaryGenerateNode | Primary output string (read by PostProcessNode) |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, caller trust level)
- [x] S-1: `required_trust_level` on every node; PreProcessNode is the external gate
- [x] S-2: framework `@final _security_gate_input()` plus this template's own screen in
      PreProcessNode — see "Security Architecture" for why both
- [x] S-3: framework `@final _security_gate_output()` plus this template's own gate in
      PostProcessNode, taking the union of both pattern sets
- [x] S-4: `emit_trace_event()` — at least one domain event inside each `execute()`

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass → framework `@final` gate always runs automatically
> - `GraphNode` → deliberate no-op (the subgraph boundary carries structured dicts)
>
> Neither `_extra_security_gate_input()` nor `_extra_security_gate_output()` is used in this
> template: the framework auto-wraps those hooks and can pass `None` state to the next node. The
> domain checks are module-level functions called inline from `execute()`.

### Composition Pattern

- **Pattern**: Cat 2 Nested (AgentBaseGraph outer + BaseGraph inner via GraphNode)
- **Outer graph**: `src/graph/graph.py` — `FinancialRegulatoryFilingSearchAgent(AgentBaseGraph)`
- **Inner graph**: `src/graph/domain_workflow_graph.py` — `DomainWorkflowGraph(BaseGraph)`
- **GraphNode**: `RegulatorySearchGraphNode(GraphNode)` in `main` slot
- **Parameter bridge**: `src/graph/context_bridge.py` (ContextVar; per thread/task)
- **Error propagation strategy**: propagate (SubgraphError on inner failure)

## Domain Node Design

### QueryNormalizeNode
- Parses the free-text query into structured fields; expands financial terminology
- Chooses target registries from query intent, unless the caller named them explicitly
- Produces `normalized_query`, `search_keywords` (capped at 20), `document_sources`
- S-4: emits `query_normalize_complete` / `query_normalize_rejected`
- trust_level: ANONYMOUS

### APIFetchNode
- Fetches document metadata from EDINET, FSA and BOJ; applies the caller's filing-date window
- Ships with a sample registry; `_fetch_documents()` is the replacement point for real API calls
- A query with keywords returns only documents those keywords hit — a keyword search that matched
  nothing must not report the whole corpus as hits
- Produces `raw_documents` (capped at 200), `fetch_metadata`
- S-4: emits `api_fetch_complete` (no credentials in payload)
- S-5: no hardcoded credentials — a deployment reads them from the secrets provider
- trust_level: ANONYMOUS

### HybridRetrieveNode
- Scores documents by keyword overlap and keeps the `top_k` highest
- Produces `retrieved_chunks`
- S-4: emits `hybrid_retrieve_complete`
- trust_level: ANONYMOUS

### RankResultsNode
- Applies a source-priority multiplier and drops results below `min_score`
- The floor is applied after re-ranking, so it is enforced on the score the caller is shown
- Produces `ranked_results`
- S-4: emits `rank_results_complete`
- trust_level: ANONYMOUS

### SummaryGenerateNode
- Renders the top-`summary_top_n` ranked results as a report
- **The render boundary.** Every caller-controlled string that appears in the report passes through
  `render_safe()` (control characters removed, whitespace runs collapsed, length capped) or the
  inert-identifier alphabet. Registry-supplied titles and snippets are bounded too
- Produces `search_summary` and `result`
- S-4: emits `summary_generate_complete`
- trust_level: ANONYMOUS

## Security Architecture

| Gate | Implementation | Location |
|------|---------------|----------|
| S-1 Trust | `required_trust_level = VERIFIED_EXTERNAL` on the entry node; the adapter grants it on a valid Bearer token | PreProcessNode, `src/api/server.py` |
| S-1 Bounds | Empty/over-length query refused; every caller number through `finite_in_range()`; every caller identifier through an inert alphabet | PreProcessNode, `src/services/security.py` |
| S-2 Input scan | Framework `@final _security_gate_input()` **plus** this template's screen | FunctionNode base, PreProcessNode |
| S-3 Output scan | Union of local patterns and the framework's `detect_credentials()`, with containment | PostProcessNode, `src/services/security.py` |
| S-4 Audit trail | `emit_trace_event(event, payload, state)` in every node | All nodes |
| S-5 Credentials | No credentials in State; a deployment reads them from the secrets provider | Architecture |

### Why the template screens input itself

The framework's input policy is a layer, not the layer. It scores `<<SYS>>` as no finding while
blocking `<|im_start|>` and `[INST]`, and where the policy is absent or configured off the payload
reaches the answer path and returns SUCCESS. `screen_injection()` therefore owns the chat-template
control-token class outright, and runs on the raw text **and** on the markup-stripped text: the
strip deletes `<|...|>` (caught raw) and re-assembles `ig<b>nore all previous instructions` (caught
after). Sanitizing is never treated as refusal.

The screens are anchored so ordinary regulatory prose is untouched — "filings that override earlier
guidance", "insert into trust holdings note" and "documents that disregard the previous accounting
treatment" all pass. The fail-closed direction is the one that blocks real work.

### Why the output gate takes the union

The framework scans every value of every node result and **raises** on a credential match. The node
wrapper catches that and returns a bare error partial with no `result` key, so the merged state
keeps the previous — un-gated — value, and `AgentBaseGraph.get_output()` falls back to exactly that
field. A template gate narrower than the framework detector therefore never gets to contain
anything. `detect_output_credentials()` is the union of both sets: the framework's format patterns
(`sk_live_`, `sk-`, `eyJ`, `AKIA`, `Bearer`, database URIs) and this template's assignment pattern
(`password=…`), which the framework does not carry. Delegating wholesale in either direction
narrows the gate.

On a violation the node returns ERROR and **clears every output-bearing field**
(`result`, `search_summary`, `formatted_output`), replacing `result` with a non-empty blocked
notice. Raising instead would be caught by the wrapper and leave the un-gated answer in state; a
falsy replacement would re-open the `formatted_output or result` fallback.

The subgraph boundary is why this matters in practice: `GraphNode`'s own output gate is a
deliberate no-op and `merge_output()` writes `state["result"]` straight from the inner graph's
`get_output()`, so `post_process` is the only gate on that hand-off.

### Output invariant

This agent renders no monetary aggregate, so the rounding/precision grid that financial *report*
templates enforce does not apply and no such gate is implemented. Its output invariants are:

1. no credential-shaped string reaches the caller, in any output-bearing field;
2. no caller-supplied text can manufacture document structure — a result entry begins at the start
   of a line, and no caller line break or control character survives the render;
3. rejected values are never echoed, in the response body or in `error_log`.

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: framework/ and shared/ only (no agents/base/ required)
- [x] No `from agenticstar import ...` anywhere in src/

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | AgentBaseGraph | Fixed multi-step pipeline; no autonomous loop needed |
| Inner graph parent | BaseGraph | AgentBaseGraph | BaseGraph | Fully custom linear topology; no backbone slots needed |
| Domain node trust | ANONYMOUS | VERIFIED_EXTERNAL | ANONYMOUS | Inner graph nodes; the external gate lives on pre_process only |
| Caller parameter channel | `input_context` | extra request fields | `input_context` | The framework carries it into state; the fields stay inert identifiers and numbers, which makes the channel structurally immune to the credential hazard that kills a run at the first node |
| Output credential gate | local patterns | framework detector | union of both | Either alone is narrower than the other in at least one shape, and a narrower gate is a bypass |
| Registry access | real registry HTTP | sample corpus + replacement point | sample corpus | Credentials belong to a deployment; the shipped pipeline must still run and be testable |
