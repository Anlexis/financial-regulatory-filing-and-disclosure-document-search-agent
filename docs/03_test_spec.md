# Test Specification — FIN-C2-090 FinancialRegulatoryFilingSearchAgent

## Test Strategy
- Coverage target: 80%+
- Test types: Unit (per-node and per-helper) / Proof-of-Boundary / Integration (real ASGI entry)
- Framework: pytest; per-module `emit_trace_event` patching where a node is driven in isolation
  (no sys.modules stubs — the framework imports the real shared package at load time)

| File | Scope |
|---|---|
| `tests/unit/test_agent.py` | Per-node behaviour, driven through `execute()` |
| `tests/unit/test_security_contract.py` | Injection screen, render safety, finite bounds, context-field bounds, output-gate union, containment |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | `@final` gate methods not overridden |
| `tests/integration/test_invoke_contract.py` | End to end through `POST /invoke` with Bearer auth |
| `tests/proof_of_boundary/test_import_isolation.py` | PB-4 |
| `tests/proof_of_boundary/test_state_safety.py` | PB-2 / PB-5 |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | PB-6 |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | PB-7 (conditional: skipped unless `hitl.enabled`) |

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict (no Pydantic/dataclass) | Type check pass | PASS |
| TC-02 | S-1 gate: empty user_input returns ERROR (PreProcessNode) | AgentStatus.ERROR returned | PASS |
| TC-03 | No JWT/Credential in State (gate-credential-scan) | CI gate: 0 violations | PASS |
| TC-04 | InvocationContext via config["configurable"] only | Not in State | PASS |
| TC-05 | S-4: no duplicate lifecycle events in execute() bodies | node_start/node_complete absent from execute() | PASS |
| TC-06 | S-2: _security_gate_input() not overridden on FunctionNode subclasses | @final enforced by framework | PASS |
| TC-07 | S-3: _security_gate_output() not overridden on FunctionNode subclasses | @final enforced by framework | PASS |
| TC-08 | required_trust_level declared on all nodes | Explicit ClassVar on every node | PASS |
| TC-09 | S-1 PreProcessNode VERIFIED_EXTERNAL gate | Insufficient trust denied | PASS |
| TC-10 | S-3 PostProcessNode credential scan (module-level helper) | API key pattern → OUTPUT BLOCKED | PASS |
| TC-11 | S-4: emit_trace_event in every node execute() | Domain event emitted per node | PASS |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → EventEmitter | emit_trace_event fires on every node success path | No silent failures | PASS |
| PB-2 | State serialization | Post-invoke State is JSON-serializable primitives only | No Pydantic/dataclass | PASS |
| PB-3 | Level 2 → External API | APIFetchNode stub returns raw_documents (production: real EDINET/FSA/BOJ calls) | Documents returned | PASS |
| PB-4 | Import isolation | No agenticstar (Level 0) imports under src/ | AST scan: 0 violations | PASS |
| PB-5 | Checkpoint safety | No JWT/Pydantic in serialized State | Inspection pass | PASS |
| PB-6 | Backbone invoke order | FinancialRegulatoryFilingSearchAgent().invoke(VERIFIED_EXTERNAL) node_history == [Initialize, PreProcess, RegulatorySearchGraphNode, PostProcess, Finalize] | Order verified | PASS |
| PB-7 | HITL interrupt propagation | Conditional — runs only when `config/config.yaml` declares `hitl.enabled: true` | SKIP (this template does not call interrupt()) | SKIP |

## Business Logic Tests

| TC-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | EDINET source detection | "EDINET pharma AI drug discovery" | document_sources contains EDINET | PASS |
| BL-02 | FSA source detection | "FSA circular AI model risk banks" | document_sources contains FSA | PASS |
| BL-03 | Generic query fallback | "climate transition risk" | All sources searched (>= 2 sources) | PASS |
| BL-04 | Keyword extraction | "EDINET pharma AI drug discovery risk" | >= 1 keyword extracted | PASS |
| BL-05 | EDINET priority over BOJ | Same base score, EDINET vs BOJ | EDINET ranked first | PASS |
| BL-06 | Summary includes source reference | ranked_results with EDINET source | "EDINET" in result | PASS |
| BL-07 | Empty ranked_results guidance message | No results from ranking | Non-empty guidance text, SUCCESS status | PASS |
| BL-08 | S-3 gate blocks JWT pattern | result field with JWT-like string | OUTPUT BLOCKED stub returned, ERROR status | PASS |
| BL-09 | Overlong query rejected | Query > 1000 chars | AgentStatus.ERROR, error_log populated | PASS |
| BL-10 | End-to-end search pipeline | EDINET pharma query via full invoke() | result["output"] non-empty, SUCCESS status | PASS |
| BL-11 | Entry point serves a request | `POST /invoke` with a valid Bearer token | HTTP 200, SUCCESS, full 5-node backbone | PASS |
| BL-12 | Registry selection changes the answer | `sources: ["BOJ"]` vs `["FSA"]` | Different documents; neither leaks the other's registry | PASS |
| BL-13 | Summary width changes the answer | `summary_top_n` 1 vs 5 | 1 vs 2+ rendered entries | PASS |
| BL-14 | Date window filters the result set | `filed_from: 2024-01-01` vs `filed_to: 2023-12-31` | Disjoint result sets | PASS |
| BL-15 | Relevance floor drops weak matches | `min_score` 0.0 vs 0.9 on the same query | 3 vs 1 rendered entries | PASS |
| BL-16 | Deployment config reaches the inner graph | `Graph(config={"summary": {"top_n": 1 / 5}})` | Rendered entry count follows the config | PASS |
| BL-17 | Request reference echoed | `request_ref: audit-2024_07` | Appears in the report header | PASS |
| BL-18 | Keyword query with no match reports no hits | Query whose keywords hit nothing | "No regulatory documents matched", not the whole corpus | PASS |

## Security Tests

| TC-ID | Gate | Test | Expected Result | Result |
|-------|------|------|----------------|--------|
| SEC-01 | S-1 | PreProcessNode rejects empty query | ERROR, no downstream processing | PASS |
| SEC-02 | S-1 | PreProcessNode rejects >1000 char query | ERROR | PASS |
| SEC-03 | S-3 | PostProcessNode blocks API key pattern in result | ERROR, OUTPUT BLOCKED stub | PASS |
| SEC-04 | S-3 | PostProcessNode blocks JWT pattern in result | ERROR, OUTPUT BLOCKED stub | PASS |
| SEC-05 | S-4 | All nodes emit trace event on success path | emit_trace_event called per execute() | PASS |
| SEC-06 | S-5 | No credentials in State (state.py fields) | gate-credential-scan: 0 violations | PASS |
| SEC-07 | review finding 5 | All inner domain nodes ANONYMOUS | TrustLevel.ANONYMOUS on QueryNormalize/APIFetch/HybridRetrieve/RankResults/SummaryGenerate | PASS |
| SEC-08 | review finding 2 | Class name match: graph.py == agent.yaml == server.py | FinancialRegulatoryFilingSearchAgent in all three | PASS |
| SEC-09 | S-1 | `POST /invoke` without / with a wrong Bearer token | HTTP 401, generic body | PASS |
| SEC-10 | S-1 | Non-finite, boolean, non-numeric and out-of-range values on every numeric context field | HTTP 400 naming the field; the value is never echoed | PASS |
| SEC-11 | S-1 | Malformed `sources` / dates / `request_ref`; unsupported field name; too many fields; oversized body | HTTP 400; an unrecognised field name is masked, not echoed | PASS |
| SEC-12 | S-2 | Chat-template control tokens `<\|im_start\|>`, `[INST]`, `<<SYS>>` | Refused; nothing published. `<<SYS>>` is scored as no finding by the framework policy, so the template's own screen owns it | PASS |
| SEC-13 | S-2 | Directive split by markup (`ig<b>nore all previous instructions`) | Refused after the markup strip re-assembles it | PASS |
| SEC-14 | S-2 | Ordinary regulatory wording containing screened words | Answered normally (fail-closed direction) | PASS |
| SEC-15 | S-2 | Credential-shaped value on the context channel | HTTP 400 naming the field, never the value; ordinary values on the same field still pass | PASS |
| SEC-16 | S-3 | Credential at the subgraph boundary, all six detected shapes | ERROR; every output-bearing field cleared; non-empty blocked notice; no traceback or path in the envelope | PASS |
| SEC-17 | S-3 | Gate detector is the union of local and framework patterns | Framework-only shapes and the local-only assignment shape both blocked | PASS |
| SEC-18 | Output | Newline / control characters in the query | Cannot manufacture a result entry; no control character reaches the report | PASS |

## Test Execution Summary
- Execution date: 2026-09-08
- Framework: pytest==9.0.3; suite run against the real `agenticstar-agentcore` wheel
  (CI installs the version pinned by `AGENTCORE_WHEEL_SPEC`)
- Assertion contract: result["output"] (not "formatted_output"); result["status"] == AgentStatus.SUCCESS; invoke(user_input=, ctx=, input_context=)
- Security assertions are BEHAVIOURAL — refused, and nothing published — never a gate's wording.
  Which layer refuses a given payload depends on the framework build; what must hold on every build
  is that no search result is returned.
