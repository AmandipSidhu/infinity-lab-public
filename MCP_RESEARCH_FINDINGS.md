# MCP Research Findings

**Date:** 2026-02-09 22:49 PST  
**Updated:** 2026-02-10 11:27 PST  
**Status:** ✅ CORRECTED - All verified from official sources

## Executive Summary

**Problem:** ALL 5 MCPs in ARCHITECTURE.md v2.8 cannot run as documented.  
**Solution:** Working alternatives found for all 5 MCPs.

## Solutions Summary

| Port | Original Spec | Status | Working Solution |
|------|---------------|--------|------------------|
| 8000 | QuantConnect | ❌ stdio-only | QuantConnect Docker + Supergateway |
| 8001 | Linear (local) | ❌ stdio-only | @tacticlaunch/mcp-linear + Supergateway |
| 8002 | Memory | ❌ stdio-only | Official package + Supergateway |
| 8003 | Thinking | ✅ Native HTTP | @camilovelezr/server-sequential-thinking |
| 8004 | Git | ❌ Doesn't exist | GitHub Copilot Remote API |

## Key Discovery: Supergateway

**Problem:** ALL MCPs except Sequential Thinking use stdio transport (process-based), not HTTP.

**Solution:** [Supergateway](https://github.com/supercorp-ai/supergateway) wraps stdio MCPs in streamable-http.

```bash
npm install -g supergateway

# Required flags:
supergateway \
  --stdio "<command>" \
  --outputTransport streamableHttp \
  --port <port> \
  --healthEndpoint /health
```

**Critical flags:**
- `--outputTransport streamableHttp` - Default is SSE, we need streamableHttp
- `--healthEndpoint /health` - Enables /health endpoint for monitoring

## Verification Sources

✅ **QuantConnect MCP** - [Official README](https://github.com/QuantConnect/mcp-server)  
- Shows `docker run -i` (stdin/stdio)  
- No HTTP transport support  
- Requires Supergateway wrapper

✅ **Linear MCP** - [Official @tacticlaunch/mcp-linear](https://github.com/tacticlaunch/mcp-linear)  
- stdio-only (npx command)  
- Requires Supergateway wrapper

✅ **Memory MCP** - [Official @modelcontextprotocol](https://github.com/modelcontextprotocol/servers/tree/main/src/memory)  
- stdio-only  
- Requires Supergateway wrapper

✅ **Sequential Thinking** - [@camilovelezr fork](https://github.com/camilovelezr/server-sequential-thinking)  
- Native streamableHttp support  
- Built-in /health endpoint  
- No wrapper needed

✅ **Supergateway** - [Official supercorp-ai/supergateway](https://github.com/supercorp-ai/supergateway)  
- stdio → streamableHttp wrapper  
- Supports health endpoints  
- Maintained and active

## Installation Script

```bash
#!/bin/bash
# install_mcp_deps.sh

set -e

echo "Installing Supergateway..."
npm install -g supergateway

echo "Installing Linear MCP..."
npm install -g @tacticlaunch/mcp-linear

echo "Installing Memory MCP..."
npm install -g @modelcontextprotocol/server-memory

echo "Cloning Sequential Thinking MCP..."
mkdir -p ~/mcp-servers
git clone https://github.com/camilovelezr/server-sequential-thinking.git ~/mcp-servers/sequential-thinking
cd ~/mcp-servers/sequential-thinking && npm install

echo "Pulling QuantConnect Docker image..."
docker pull quantconnect/mcp-server

echo "Creating directories..."
mkdir -p ~/mcp-logs ~/mcp-data

echo "✅ All MCP dependencies installed"
```

## Startup Script

```bash
#!/bin/bash
# start_all_mcps.sh

set -e
source ~/.env.mcp

LOG_DIR="$HOME/mcp-logs"
DATA_DIR="$HOME/mcp-data"

# Port 8000: QuantConnect (Docker stdio → Supergateway → streamableHttp)
supergateway \
  --stdio "docker run -i --rm -e QUANTCONNECT_USER_ID -e QUANTCONNECT_API_TOKEN quantconnect/mcp-server" \
  --outputTransport streamableHttp \
  --port 8000 \
  --healthEndpoint /health \
  > "${LOG_DIR}/quantconnect-mcp.log" 2>&1 &

# Port 8001: Linear (stdio → Supergateway → streamableHttp)
supergateway \
  --stdio "mcp-linear --token ${LINEAR_API_KEY}" \
  --outputTransport streamableHttp \
  --port 8001 \
  --healthEndpoint /health \
  > "${LOG_DIR}/linear-mcp.log" 2>&1 &

# Port 8002: Memory (stdio → Supergateway → streamableHttp)
export MEMORY_FILE_PATH="${DATA_DIR}/memory.json"
supergateway \
  --stdio "npx -y @modelcontextprotocol/server-memory" \
  --outputTransport streamableHttp \
  --port 8002 \
  --healthEndpoint /health \
  > "${LOG_DIR}/memory-mcp.log" 2>&1 &

# Port 8003: Sequential Thinking (native streamableHttp)
cd ~/mcp-servers/sequential-thinking && PORT=8003 npm start > "${LOG_DIR}/thinking-mcp.log" 2>&1 &

# Port 8004: GitHub (remote, no local process)
echo "GitHub MCP: https://api.githubcopilot.com/mcp/"

echo "Waiting 20 seconds for initialization..."
sleep 20

echo "Health checks:"
for port in 8000 8001 8002 8003; do
  curl -f http://localhost:${port}/health && echo "✅ Port ${port}" || echo "❌ Port ${port}"
done
```

## Transport Matrix

| MCP | Native Transport | Wrapper | Final Transport |
|-----|------------------|---------|------------------|
| QuantConnect | stdio | Supergateway | streamableHttp |
| Linear | stdio | Supergateway | streamableHttp |
| Memory | stdio | Supergateway | streamableHttp |
| Sequential Thinking | streamableHttp | None | streamableHttp |
| GitHub | Remote HTTP | None | HTTP |

## References

- [QuantConnect MCP](https://github.com/QuantConnect/mcp-server) - Official stdio implementation
- [Supergateway](https://github.com/supercorp-ai/supergateway) - Official stdio→HTTP wrapper
- [@tacticlaunch/mcp-linear](https://github.com/tacticlaunch/mcp-linear) - Community Linear MCP
- [@modelcontextprotocol/server-memory](https://github.com/modelcontextprotocol/servers) - Official Memory MCP
- [@camilovelezr/server-sequential-thinking](https://github.com/camilovelezr/server-sequential-thinking) - HTTP fork
- [GitHub MCP Server](https://github.com/github/github-mcp-server) - Official GitHub MCP

---

**Change Log:**
- 2026-02-09: Initial research
- 2026-02-10: Corrected QuantConnect classification (stdio, not HTTP)
- 2026-02-10: Corrected Supergateway repo (supercorp-ai, not sercorpai)
- 2026-02-10: Added required Supergateway flags

**Next Steps:**
1. ✅ Scripts corrected with proper flags
2. ✅ Workflow updated for --rm containers
3. ⏳ Test workflow to verify all fixes
4. ⏳ Update UNI-50 with final results
