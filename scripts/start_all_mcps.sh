#!/bin/bash
# start_all_mcps.sh - Start all 7 MCP servers (v4.0 Day 1)
# Part of Infinity 5 Bootstrap v6.1
# Related: UNI-50, ARCHITECTURE_v4.0.md

set -e

# Verify dependencies installed
echo "🔍 Verifying dependencies..."
command -v supergateway >/dev/null 2>&1 || { echo "❌ supergateway not found. Run install_mcp_deps.sh first"; exit 1; }
command -v mcp-linear >/dev/null 2>&1 || { echo "❌ mcp-linear not found. Run install_mcp_deps.sh first"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "❌ Docker not found. Install Docker first"; exit 1; }
[ -d "$HOME/mcp-servers/sequential-thinking" ] || { echo "❌ Sequential Thinking not found. Run install_mcp_deps.sh first"; exit 1; }
[ -d "$HOME/.chromadb" ] || { echo "⚠️  Warning: ChromaDB not found at ~/.chromadb. Run: python scripts/ingest_knowledge_db.py"; }
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
    echo "  ALPACA_API_KEY=your_key (optional for testing)"
    echo "  ALPACA_API_SECRET=your_secret (optional for testing)"
    exit 1
fi

LOG_DIR="$HOME/mcp-logs"
DATA_DIR="$HOME/mcp-data"
mkdir -p "$LOG_DIR" "$DATA_DIR"

echo "🚀 Starting all 7 MCP servers (v4.0 Day 1)..."
echo ""

# Port 8000: QuantConnect MCP (Docker stdio → Supergateway → streamableHttp)
echo "[1/7] Starting QuantConnect MCP on port 8000..."
supergateway \
  --stdio "docker run -i --rm -e QUANTCONNECT_USER_ID=${QUANTCONNECT_USER_ID} -e QUANTCONNECT_API_TOKEN=${QUANTCONNECT_API_TOKEN} quantconnect/mcp-server" \
  --outputTransport streamableHttp \
  --healthEndpoint /health \
  --port 8000 \
  > "${LOG_DIR}/quantconnect-mcp.log" 2>&1 &
QC_PID=$!

echo "✅ QuantConnect MCP started (Supergateway wrapper, PID: $QC_PID)"
echo ""

# Port 8001: Linear MCP (stdio → Supergateway → streamableHttp)
echo "[2/7] Starting Linear MCP on port 8001..."
supergateway \
  --stdio "mcp-linear --token ${LINEAR_API_KEY}" \
  --outputTransport streamableHttp \
  --healthEndpoint /health \
  --port 8001 \
  > "${LOG_DIR}/linear-mcp.log" 2>&1 &
LINEAR_PID=$!

echo "✅ Linear MCP started (Supergateway wrapper, PID: $LINEAR_PID)"  
echo ""

# Port 8002: Memory MCP (stdio → Supergateway → streamableHttp)
echo "[3/7] Starting Memory MCP on port 8002..."
export MEMORY_FILE_PATH="${DATA_DIR}/memory.json"
supergateway \
  --stdio "npx -y @modelcontextprotocol/server-memory" \
  --outputTransport streamableHttp \
  --healthEndpoint /health \
  --port 8002 \
  > "${LOG_DIR}/memory-mcp.log" 2>&1 &
MEMORY_PID=$!

echo "✅ Memory MCP started (Supergateway wrapper, PID: $MEMORY_PID)"
echo ""

# Port 8003: Sequential Thinking MCP (native streamableHttp)
echo "[4/7] Starting Sequential Thinking MCP on port 8003..."
(cd "$HOME/mcp-servers/sequential-thinking" && PORT=8003 npm start > "${LOG_DIR}/thinking-mcp.log" 2>&1) &
THINKING_PID=$!

echo "✅ Sequential Thinking MCP started (native streamableHttp, PID: $THINKING_PID)"
echo ""

# Port 8004: GitHub MCP (remote - no local process)
echo "[5/7] GitHub MCP uses remote endpoint"
echo "    URL: https://api.githubcopilot.com/mcp/"
echo "    Auth: Bearer ${GITHUB_TOKEN:0:8}..."
echo "✅ GitHub MCP configured (remote)"
echo ""

# Port 8005: Knowledge RAG MCP (NEW - Day 1 Critical)
echo "[6/7] Starting Knowledge RAG MCP on port 8005..."
python3 scripts/knowledge_mcp_server.py > "${LOG_DIR}/knowledge-rag-mcp.log" 2>&1 &
KNOWLEDGE_PID=$!

echo "✅ Knowledge RAG MCP started (FastMCP, PID: $KNOWLEDGE_PID)"
echo ""

# Port 8006: Alpaca MCP with Rate Limiting (NEW - Day 1 Critical)
echo "[7/7] Starting Alpaca MCP on port 8006..."
if [ -n "$ALPACA_API_KEY" ]; then
    export ALPACA_API_KEY
    export ALPACA_API_SECRET
    export ALPACA_BASE_URL="${ALPACA_BASE_URL:-https://paper-api.alpaca.markets}"
    python3 scripts/alpaca_rate_limited.py > "${LOG_DIR}/alpaca-mcp.log" 2>&1 &
    ALPACA_PID=$!
    echo "✅ Alpaca MCP started (rate limited, PID: $ALPACA_PID)"
else
    echo "⚠️  Alpaca MCP skipped (ALPACA_API_KEY not set)"
    echo "    Set ALPACA_API_KEY in ~/.env.mcp to enable"
    ALPACA_PID="N/A"
fi
echo ""

echo "⏳ Waiting 20 seconds for servers to initialize..."
sleep 20
echo ""

echo "🔍 Health checks:"
bash scripts/health_check.sh
echo ""

echo "✅ All MCP servers started!"
echo ""
echo "Process IDs:"
echo "  - QuantConnect MCP (8000): $QC_PID"
echo "  - Linear MCP (8001): $LINEAR_PID"
echo "  - Memory MCP (8002): $MEMORY_PID"
echo "  - Sequential Thinking (8003): $THINKING_PID"
echo "  - GitHub MCP (8004): Remote"
echo "  - Knowledge RAG (8005): $KNOWLEDGE_PID"
echo "  - Alpaca MCP (8006): $ALPACA_PID"
echo ""
echo "Logs: ${LOG_DIR}/"
echo "Data: ${DATA_DIR}/"
echo ""
echo "To stop: ./scripts/stop_all_mcps.sh"
