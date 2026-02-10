#!/bin/bash
# start_all_mcps.sh - Start all 5 MCP servers
# Part of Infinity 5 Bootstrap v6.1
# Related: UNI-50, MCP_RESEARCH_FINDINGS.md

set -e

# Load environment variables
if [ -f ~/.env.mcp ]; then
    source ~/.env.mcp
else
    echo "❌ Error: ~/.env.mcp not found"
    echo "Create it with:"
    echo "  LINEAR_API_KEY=your_key"
    echo "  QUANTCONNECT_USER_ID=your_id"
    echo "  QUANTCONNECT_API_TOKEN=your_token"
    echo "  GITHUB_TOKEN=your_token"
    exit 1
fi

LOG_DIR="$HOME/mcp-logs"
DATA_DIR="$HOME/mcp-data"

echo "🚀 Starting all MCP servers..."
echo ""

# Port 8000: QuantConnect MCP (Docker)
echo "[1/5] Starting QuantConnect MCP on port 8000..."
docker rm -f quantconnect-mcp 2>/dev/null || true
docker run -d \
  --name quantconnect-mcp \
  -p 8000:8000 \
  -e QUANTCONNECT_USER_ID="${QUANTCONNECT_USER_ID}" \
  -e QUANTCONNECT_API_TOKEN="${QUANTCONNECT_API_TOKEN}" \
  quantconnect/mcp-server:latest \
  python -m quantconnect_mcp.main --transport streamable-http --port 8000

echo "✅ QuantConnect MCP started (Docker container: quantconnect-mcp)"
echo ""

# Port 8001: Linear MCP (Supergateway wrapper)
echo "[2/5] Starting Linear MCP on port 8001..."
supergateway \
  --stdio "mcp-linear --token ${LINEAR_API_KEY}" \
  --port 8001 \
  > "${LOG_DIR}/linear-mcp.log" 2>&1 &

echo "✅ Linear MCP started (Supergateway wrapper, PID: $!)"  
echo ""

# Port 8002: Memory MCP (Supergateway wrapper)
echo "[3/5] Starting Memory MCP on port 8002..."
export MEMORY_FILE_PATH="${DATA_DIR}/memory.json"
supergateway \
  --stdio "npx -y @modelcontextprotocol/server-memory" \
  --port 8002 \
  > "${LOG_DIR}/memory-mcp.log" 2>&1 &

echo "✅ Memory MCP started (Supergateway wrapper, PID: $!)"
echo ""

# Port 8003: Sequential Thinking MCP (native HTTP)
echo "[4/5] Starting Sequential Thinking MCP on port 8003..."
cd "$HOME/mcp-servers/sequential-thinking"
PORT=8003 npm start > "${LOG_DIR}/thinking-mcp.log" 2>&1 &

echo "✅ Sequential Thinking MCP started (native HTTP, PID: $!)"
echo ""

# Port 8004: GitHub MCP (remote - no local process)
echo "[5/5] GitHub MCP uses remote endpoint"
echo "    URL: https://api.githubcopilot.com/mcp/"
echo "    Auth: Bearer ${GITHUB_TOKEN}"
echo "✅ GitHub MCP configured (remote)"
echo ""

echo "⏳ Waiting 10 seconds for servers to initialize..."
sleep 10
echo ""

echo "🔍 Health checks:"
for port in 8000 8001 8002 8003; do
    if curl -s -f http://localhost:${port}/health > /dev/null 2>&1; then
        echo "  ✅ Port ${port}: Healthy"
    else
        echo "  ⚠️  Port ${port}: Not responding (check logs)"
    fi
done
echo "  ℹ️  Port 8004: GitHub MCP (remote, no local health check)"
echo ""

echo "✅ All MCP servers started!"
echo ""
echo "Logs: ${LOG_DIR}/"
echo "Data: ${DATA_DIR}/"
echo ""
echo "To stop: ./scripts/stop_all_mcps.sh"
