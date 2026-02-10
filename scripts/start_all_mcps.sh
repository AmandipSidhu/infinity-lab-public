#!/bin/bash
# start_all_mcps.sh - Start all 5 MCP servers
# Part of Infinity 5 Bootstrap v6.1
# Related: UNI-50, ARCHITECTURE v2.9 CORRECTED

set -e

# Verify dependencies installed
echo "🔍 Verifying dependencies..."
command -v supergateway >/dev/null 2>&1 || { echo "❌ supergateway not found. Run install_mcp_deps.sh first"; exit 1; }
command -v mcp-linear >/dev/null 2>&1 || { echo "❌ mcp-linear not found. Run install_mcp_deps.sh first"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "❌ Docker not found. Install Docker first"; exit 1; }
[ -d "$HOME/mcp-servers/sequential-thinking" ] || { echo "❌ Sequential Thinking not found. Run install_mcp_deps.sh first"; exit 1; }
echo "✅ All dependencies found"
echo ""

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
mkdir -p "$LOG_DIR" "$DATA_DIR"

echo "🚀 Starting all MCP servers..."
echo ""

# Port 8000: QuantConnect MCP (Docker stdio → Supergateway → Streamable HTTP)
echo "[1/5] Starting QuantConnect MCP on port 8000..."
supergateway \
  --stdio "docker run -i --rm -e QUANTCONNECT_USER_ID=${QUANTCONNECT_USER_ID} -e QUANTCONNECT_API_TOKEN=${QUANTCONNECT_API_TOKEN} quantconnect/mcp-server" \
  --outputTransport streamableHttp \
  --stateful \
  --port 8000 \
  --healthEndpoint /health \
  > "${LOG_DIR}/quantconnect-mcp.log" 2>&1 &
QC_PID=$!

echo "✅ QuantConnect MCP started (Supergateway wrapper, PID: $QC_PID)"
echo ""

# Port 8001: Linear MCP (stdio → Supergateway → Streamable HTTP)
echo "[2/5] Starting Linear MCP on port 8001..."
supergateway \
  --stdio "mcp-linear --token ${LINEAR_API_KEY}" \
  --outputTransport streamableHttp \
  --stateful \
  --port 8001 \
  --healthEndpoint /health \
  > "${LOG_DIR}/linear-mcp.log" 2>&1 &
LINEAR_PID=$!

echo "✅ Linear MCP started (Supergateway wrapper, PID: $LINEAR_PID)"  
echo ""

# Port 8002: Memory MCP (stdio → Supergateway → Streamable HTTP)
echo "[3/5] Starting Memory MCP on port 8002..."
export MEMORY_FILE_PATH="${DATA_DIR}/memory.json"
supergateway \
  --stdio "npx -y @modelcontextprotocol/server-memory" \
  --outputTransport streamableHttp \
  --stateful \
  --port 8002 \
  --healthEndpoint /health \
  > "${LOG_DIR}/memory-mcp.log" 2>&1 &
MEMORY_PID=$!

echo "✅ Memory MCP started (Supergateway wrapper, PID: $MEMORY_PID)"
echo ""

# Port 8003: Sequential Thinking MCP (native Streamable HTTP)
echo "[4/5] Starting Sequential Thinking MCP on port 8003..."
(cd "$HOME/mcp-servers/sequential-thinking" && PORT=8003 npm start > "${LOG_DIR}/thinking-mcp.log" 2>&1) &
THINKING_PID=$!

echo "✅ Sequential Thinking MCP started (native HTTP, PID: $THINKING_PID)"
echo ""

# Port 8004: GitHub MCP (remote - no local process)
echo "[5/5] GitHub MCP uses remote endpoint"
echo "    URL: https://api.githubcopilot.com/mcp/"
echo "    Auth: Bearer ${GITHUB_TOKEN:0:8}..."
echo "✅ GitHub MCP configured (remote)"
echo ""

echo "⏳ Waiting 20 seconds for servers to initialize..."
sleep 20
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
echo "Process IDs:"
echo "  - QuantConnect MCP (8000): $QC_PID"
echo "  - Linear MCP (8001): $LINEAR_PID"
echo "  - Memory MCP (8002): $MEMORY_PID"
echo "  - Sequential Thinking (8003): $THINKING_PID"
echo ""
echo "Logs: ${LOG_DIR}/"
echo "Data: ${DATA_DIR}/"
echo ""
echo "To stop: ./scripts/stop_all_mcps.sh"
