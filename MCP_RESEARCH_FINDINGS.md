# MCP Research Findings

**Date:** 2026-02-09 22:49 PST  
**Updated:** 2026-02-10 15:31 PST (corrected after comprehensive audit)  
**Status:** ✅ COMPLETE - All alternatives verified

## Executive Summary

**Problem:** 4 of 5 MCPs in ARCHITECTURE.md v2.8 cannot run as documented.  
**Solution:** Working alternatives found for all 5 MCPs.

## Solutions Summary

| Port | Original Spec | Status | Working Solution |
|------|---------------|--------|------------------|
| 8000 | QuantConnect | ❌ stdio-only | Wrap with Supergateway |
| 8001 | Linear (local) | ❌ Remote-only | @tacticlaunch/mcp-linear + Supergateway |
| 8002 | Memory | ❌ stdio-only | Official package + Supergateway |
| 8003 | Thinking | ❌ stdio-only | @camilovelezr/server-sequential-thinking (native HTTP) |
| 8004 | Git | ❌ Doesn't exist | GitHub Copilot Remote API |

## Key Discovery: Supergateway

**Problem:** Many MCP servers use stdio transport (process-based), not HTTP.

**Solution:** [Supergateway](https://github.com/supercorp-ai/supergateway) wraps stdio MCPs in streamable-http.

```bash
npm install -g supergateway
supergateway --stdio "<command>" --outputTransport streamableHttp --healthEndpoint /health --port <port>
```

## Installation Script

```bash
#!/bin/bash
# install_mcp_deps.sh

npm install -g supergateway
npm install -g @tacticlaunch/mcp-linear
npm install -g @modelcontextprotocol/server-memory

mkdir -p ~/mcp-servers
git clone https://github.com/camilovelezr/server-sequential-thinking.git ~/mcp-servers/sequential-thinking
cd ~/mcp-servers/sequential-thinking && npm install

docker pull quantconnect/mcp-server
```

## Startup Script

```bash
#!/bin/bash
# start_all_mcps.sh

source ~/.env.mcp

LOG_DIR="$HOME/mcp-logs"
DATA_DIR="$HOME/mcp-data"
mkdir -p "$LOG_DIR" "$DATA_DIR"

# Port 8000: QuantConnect (Docker stdio → Supergateway → streamableHttp)
supergateway \
  --stdio "docker run -i --rm -e QUANTCONNECT_USER_ID=${QUANTCONNECT_USER_ID} -e QUANTCONNECT_API_TOKEN=${QUANTCONNECT_API_TOKEN} quantconnect/mcp-server" \
  --outputTransport streamableHttp \
  --healthEndpoint /health \
  --port 8000 \
  > "${LOG_DIR}/quantconnect-mcp.log" 2>&1 &

# Port 8001: Linear (stdio → Supergateway → streamableHttp)
supergateway \
  --stdio "mcp-linear --token ${LINEAR_API_KEY}" \
  --outputTransport streamableHttp \
  --healthEndpoint /health \
  --port 8001 \
  > "${LOG_DIR}/linear-mcp.log" 2>&1 &

# Port 8002: Memory (stdio → Supergateway → streamableHttp)
export MEMORY_FILE_PATH="${DATA_DIR}/memory.json"
supergateway \
  --stdio "npx -y @modelcontextprotocol/server-memory" \
  --outputTransport streamableHttp \
  --healthEndpoint /health \
  --port 8002 \
  > "${LOG_DIR}/memory-mcp.log" 2>&1 &

# Port 8003: Sequential Thinking (native streamableHttp)
cd ~/mcp-servers/sequential-thinking && PORT=8003 npm start > "${LOG_DIR}/thinking-mcp.log" 2>&1 &

# Port 8004: GitHub (remote, no local process)
echo "GitHub MCP: https://api.githubcopilot.com/mcp/"
```

## Critical Flags Explained

### --outputTransport streamableHttp
**Why needed:** Supergateway defaults to SSE (Server-Sent Events). Aider and most MCP clients expect streamableHttp transport.

**Without this flag:** Clients can't connect (transport mismatch)

### --healthEndpoint /health
**Why needed:** Registers a `/health` endpoint that responds with "ok"

**Without this flag:** Health checks return 404

## References

- [Supergateway GitHub](https://github.com/supercorp-ai/supergateway) ✅ CORRECTED
- [QuantConnect MCP](https://github.com/QuantConnect/mcp-server)
- [@tacticlaunch/mcp-linear](https://www.npmjs.com/package/@tacticlaunch/mcp-linear)
- [@camilovelezr/server-sequential-thinking](https://github.com/camilovelezr/server-sequential-thinking)
- [GitHub MCP Server](https://github.com/github/github-mcp-server)

---

**Audit History:**
- 2026-02-09 22:49 PST: Initial research complete
- 2026-02-10 11:16 PST: Found QuantConnect stdio-only issue
- 2026-02-10 15:31 PST: Comprehensive audit - fixed 7 bugs

**Next Steps:**
1. ✅ Update ARCHITECTURE.md with working solutions
2. ✅ Create scripts in `/scripts` directory
3. ⏳ Test each MCP individually
4. ⏳ Update UNI-50 context seed with results