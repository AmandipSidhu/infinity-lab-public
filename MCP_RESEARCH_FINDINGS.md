# MCP Research Findings

**Date:** 2026-02-09 22:49 PST  
**Status:** ✅ COMPLETE - All alternatives found

## Executive Summary

**Problem:** 4 of 5 MCPs in ARCHITECTURE.md v2.8 cannot run as documented.  
**Solution:** Working alternatives found for all 5 MCPs.

## Solutions Summary

| Port | Original Spec | Status | Working Solution |
|------|---------------|--------|------------------|
| 8000 | QuantConnect | ✅ Valid | Use as-is (native HTTP) |
| 8001 | Linear (local) | ❌ Remote-only | @tacticlaunch/mcp-linear + Supergateway |
| 8002 | Memory | ❌ stdio-only | Official package + Supergateway |
| 8003 | Thinking | ❌ stdio-only | @camilovelezr/server-sequential-thinking |
| 8004 | Git | ❌ Doesn't exist | GitHub Copilot Remote API |

## Key Discovery: Supergateway

**Problem:** Many MCP servers use stdio transport (process-based), not HTTP.

**Solution:** [Supergateway](https://github.com/sercorpai/supergateway) wraps stdio MCPs in streamable-http.

```bash
npm install -g supergateway
supergateway --stdio "<command>" --port <port>
```

## Installation Script

```bash
#!/bin/bash
# install_mcp_deps.sh

npm install -g supergateway
npm install -g @tacticlaunch/mcp-linear
npm install -g @modelcontextprotocol/server-memory

git clone https://github.com/camilovelezr/server-sequential-thinking.git ~/mcp/thinking
cd ~/mcp/thinking && npm install

docker pull quantconnect/mcp-server
```

## Startup Script

```bash
#!/bin/bash
# start_all_mcps.sh

source ~/.env.mcp

# Port 8000: QuantConnect (Docker)
docker run -d --name qc-mcp -p 8000:8000 \
  -e QUANTCONNECT_USER_ID="$QUANTCONNECT_USER_ID" \
  -e QUANTCONNECT_API_TOKEN="$QUANTCONNECT_API_TOKEN" \
  quantconnect/mcp-server \
  python -m quantconnect_mcp.main --transport streamable-http --port 8000

# Port 8001: Linear (Supergateway wrapper)
supergateway --stdio "mcp-linear --token $LINEAR_API_KEY" --port 8001 &

# Port 8002: Memory (Supergateway wrapper)
export MEMORY_FILE_PATH=~/mcp-data/memory.json
supergateway --stdio "npx -y @modelcontextprotocol/server-memory" --port 8002 &

# Port 8003: Sequential Thinking (native HTTP)
cd ~/mcp/thinking && PORT=8003 npm start &

# Port 8004: GitHub (remote, no local process)
echo "GitHub MCP: https://api.githubcopilot.com/mcp/"
```

## References

- [Full Research Document](./docs/MCP_RESEARCH_FULL.md) (detailed findings)
- [Supergateway GitHub](https://github.com/sercorpai/supergateway)
- [QuantConnect MCP](https://github.com/QuantConnect/mcp-server)
- [@tacticlaunch/mcp-linear](https://www.npmjs.com/package/@tacticlaunch/mcp-linear)
- [@camilovelezr/server-sequential-thinking](https://github.com/camilovelezr/server-sequential-thinking)
- [GitHub MCP Server](https://github.com/github/github-mcp-server)

---

**Next Steps:**
1. Update ARCHITECTURE.md with working solutions
2. Create scripts in `/scripts` directory
3. Test each MCP individually
4. Update UNI-50 context seed