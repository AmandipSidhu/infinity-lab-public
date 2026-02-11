# Infinity 5 Architecture v3.1 (Standalone, No Drift)

**Date:** 2026-02-11 14:07 PST  
**Status:** DAY 1 production-grade reference  
**Principle:** The autonomous builder is the means; the end product is live-trading-worthy strategies (no phased rollouts) [UNI-50].

---

## 0) System purpose (non-negotiable)

**Mission:** Build live-trading-worthy algorithmic strategies autonomously for QuantConnect + IBKR.  
**Constraints:** Must work Day 1; first strategy produced must be live-trading worthy; avoid phased rollouts / “add later” designs [UNI-50].

---

## 1) What this system is

Infinity 5 is a GitHub Actions-driven autonomous strategy builder.

**Trigger:** Create a GitHub issue in this repo with label `autonomous-build`. The workflow runs, starts MCP stack, runs the agent, and opens a PR with code + artifacts.

**Core agent:** Aider (via Python API) orchestrated by our own script so MCP tools are available (mirrajabi action cannot provide MCP access).

---

## 2) High-level data flow

1. User creates issue with strategy request + label `autonomous-build`.
2. GitHub Actions runner:
   - Checks out repo
   - Installs dependencies
   - Starts MCP servers (ports defined below)
   - Runs `scripts/autonomous_build.py`
3. Agent:
   - Discovers MCP tools
   - Creates/updates strategy code
   - Runs QC backtests via QuantConnect MCP
   - Saves artifacts and logs
   - Pushes branch + opens PR
4. Workflow uploads artifacts and comments/updates external trackers.

---

## 3) Model escalation (cost-control + reliability)

Escalation chain used by the autonomous agent:

1. **Gemini 2.0 Flash Thinking** (primary, free/low-cost)
2. **OpenAI GPT-4o (GitHub Models free tier)** (circuit-breaker; different model family to break fixation)
3. **Paid GPT-4o** (higher reliability)
4. **Opus** (final boss; highest cost)

**Rule:** Escalate only after repeated failure on same error class; maintain strict cost limits.

---

## 4) MCP stack (ports, purpose, implementations)

All MCPs are started inside GitHub Actions (ephemeral runner) unless stated remote.

| Port | MCP | Purpose | Implementation |
|------|-----|---------|----------------|
| 8000 | QuantConnect MCP | Create projects, run backtests, fetch results | `quantconnect/mcp-server` wrapped with Supergateway (stdio → streamable-http) |
| 8001 | Linear MCP | External memory / task tracking | `@tacticlaunch/mcp-linear` wrapped with Supergateway |
| 8002 | Memory MCP | Local knowledge graph (short-term, session learnings) | `@modelcontextprotocol/server-memory` wrapped with Supergateway |
| 8003 | Sequential Thinking MCP | Decomposition / structured reasoning | `@camilovelezr/server-sequential-thinking` (session-based) |
| 8004 | GitHub MCP | Repo ops/search/PR tools | Remote (GitHub MCP Direct / Copilot MCP depending on environment) |
| 8005 | Knowledge RAG MCP | Domain retrieval: alphas + QC/Lean docs | Local Python MCP server (FastMCP) + ChromaDB + local embeddings |

### 4.1 QuantConnect cost note

If you already have a **QuantConnect Researcher Tier** account, the API access is included (no extra monthly fee for the API itself) per QuantConnect Tier Features documentation. See: https://www.quantconnect.com/docs/v2/cloud-platform/organizations/tier-features

---

## 5) Knowledge RAG MCP (Port 8005) — Day 1 required

**Purpose:** Prevent hallucinated APIs + provide proven alpha patterns for strategy synthesis.

### 5.1 Datasets ingested (Day 1)

1. **WorldQuant 101 Alphas**
   - Source implementation repo: https://github.com/yli188/WorldQuant_alpha101_code
   - Ingest goal: index each `alpha###` function body + minimal metadata.

2. **QuantConnect/Lean documentation**
   - Source repo: https://github.com/QuantConnect/Lean
   - Ingest goal: index `Documentation/**/*.md` into chunks by header.

### 5.2 Retrieval contracts

Knowledge MCP must provide two tools:

- `knowledge.search(query: str, k: int=8, filters?: {...}) -> [{id, title, snippet, source_url, metadata}]`
- `knowledge.get(id: str) -> {id, content, source_url, metadata}`

**Hard requirement:** returned items must include `source_url` for traceability.

### 5.3 Storage

- Vector DB: ChromaDB persisted in workspace (artifacted for debugging).
- Embeddings: local CPU-friendly model (pin version) to avoid network dependency.

---

## 6) GitHub Actions: workflows and artifacts

### 6.1 Workflows

- `.github/workflows/test-mcp-stack.yml` — validates MCP startup + tool discovery.
- `.github/workflows/autonomous-build.yml` — issue-triggered autonomous strategy build.

### 6.2 Artifacts (must be uploaded every run)

- `artifacts/build-<issue>.log`
- `artifacts/summary.json`
- `artifacts/checkpoints/*`
- Backtest outputs (QC results JSON + key stats + any charts if available)

---

## 7) Reliability (Day 1)

Mandatory:

- Branch isolation: all changes go to `feature/strategy-<issue>`.
- Hard timeouts: workflow timeout + Aider step timeout.
- Cost ceiling: enforce per-build max cost.
- Checkpointing: periodic state save; on failure, preserve resumption data.
- Health checks for MCP ports before agent starts.

---

## 8) Alpaca: position (optional but valuable)

**Alpaca is optional** because QuantConnect already provides the backtesting and live environment, but Alpaca can add value in two ways:

1. **Development-time data validation** outside QC quotas (e.g., quick spot-checks) using Alpaca free plan limits.
2. **Broker/path optionality**: Alpaca brokerage + data feed can be used with QuantConnect (and Lean) for certain deployments.

QuantConnect also documents Alpaca as a data provider and notes pricing is based on your Alpaca subscription (free tier exists, with quota limits): https://www.quantconnect.com/docs/v2/cloud-platform/datasets/alpaca

---

## 9) Key references

- System purpose and Day 1 constraints: https://linear.app/universaltrading/issue/UNI-50/context-seed-complete-mcp-stack-v281-all-6-mcps-day-1
- QuantConnect Tier Features: https://www.quantconnect.com/docs/v2/cloud-platform/organizations/tier-features
- WorldQuant alpha implementation source: https://github.com/yli188/WorldQuant_alpha101_code
- QuantConnect Lean repo: https://github.com/QuantConnect/Lean
- Alpaca data plans: https://alpaca.markets/data
