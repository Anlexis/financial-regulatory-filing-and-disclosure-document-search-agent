# FIN-C2-090 — FinancialRegulatoryFilingSearchAgent

> **Category**: Cat 2 (domain pipeline — retrieval, ranking and reporting over regulatory filings)
> **Industry**: FIN

## Overview

Searches Japanese regulatory disclosure sources — EDINET securities reports, FSA circulars and BOJ
publications — and returns a ranked, sourced answer to a compliance or investment-research question.
A free-text question is normalised into keywords and target registries, matching documents are
fetched and scored, the results are re-ranked and filtered against a relevance floor, and the top
matches are rendered as a short report with document identifiers, dates and excerpts.

The caller can steer every stage of that pipeline per request: which registries to search, how many
documents to retrieve, the relevance floor, a filing-date window, and how many results to render.
The values are bounded and validated at the entry point, and defaults come from `config/config.yaml`.

The bundled registry data is a small sample so the pipeline runs as shipped. Replace
`_fetch_documents()` in `src/nodes/api_fetch_node.py` with calls to the real registry APIs, taking
credentials from the secrets provider.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails during
graph compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Calling the agent

`POST /invoke` takes the question plus an optional structured search context:

```json
{
  "input": "EDINET pharma AI drug discovery risk factors securities report",
  "session_id": "req-001",
  "input_context": {
    "sources": ["EDINET", "FSA"],
    "top_k": 8,
    "summary_top_n": 3,
    "min_score": 0.2,
    "filed_from": "2024-01-01",
    "filed_to": "2024-12-31",
    "request_ref": "audit-2024_07"
  }
}
```

| Field | Meaning | Bounds |
|---|---|---|
| `sources` | Registries to search | up to 3 of `EDINET`, `FSA`, `BOJ` |
| `top_k` | Documents kept after retrieval | 1–20 |
| `summary_top_n` | Results rendered in the report | 1–10 |
| `min_score` | Relevance floor applied after re-ranking | 0.0–1.0 |
| `filed_from` / `filed_to` | Filing-date window | `YYYY-MM-DD` |
| `request_ref` | Caller reference echoed into the report header | `[A-Za-z0-9][A-Za-z0-9_-]{0,31}` |

Every field is optional; anything omitted falls back to `config/config.yaml`. A value that is
out of range, non-finite or outside the accepted alphabet is refused with HTTP 400 naming the
field — the rejected value is never echoed back.

When `INVOKE_AUTH_TOKEN` is set on the server environment, callers that no upstream middleware
vouched for must present it as a Bearer token.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent manifest (agent.yaml) and runtime parameters (config.yaml)
docs/         design and operational documentation
```

See `docs/` for the design and the test specification.

## Customising

1. Adjust `config/config.yaml` for your own retrieval defaults.
2. Replace `_fetch_documents()` in `src/nodes/api_fetch_node.py` with your registry integration,
   and the sample documents with your own corpus.
3. Replace the keyword scorer in `src/nodes/hybrid_retrieve_node.py` with a vector index, and the
   source-priority multiplier in `src/nodes/rank_results_node.py` with a re-ranking model.
4. Review the screens and bounds in `src/services/security.py` against your own policy.
5. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
