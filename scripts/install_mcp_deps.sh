#!/bin/bash
# install_mcp_deps.sh - Install all MCP server dependencies
# Part of Infinity 5 Bootstrap v6.1
# Related: UNI-50, MCP_RESEARCH_FINDINGS.md, ARCHITECTURE_v4.0.md

set -e

echo "🔧 Installing MCP dependencies for Infinity 5 v4.0..."
echo ""

# 1. Supergateway (stdio -> HTTP wrapper)
echo "[1/7] Installing Supergateway (stdio transport wrapper)..."
npm install -g supergateway
echo "✅ Supergateway installed"
echo ""

# 2. Linear MCP
echo "[2/7] Installing Linear MCP (@tacticlaunch/mcp-linear)..."
npm install -g @tacticlaunch/mcp-linear
echo "✅ Linear MCP installed"
echo ""

# 3. Memory MCP
echo "[3/7] Installing Memory MCP (official)..."
npm install -g @modelcontextprotocol/server-memory
echo "✅ Memory MCP installed"
echo ""

# 4. Sequential Thinking MCP (clone and build)
echo "[4/7] Installing Sequential Thinking MCP (streamable-http native)..."
MCP_DIR="$HOME/mcp-servers"
mkdir -p "$MCP_DIR"

if [ -d "$MCP_DIR/sequential-thinking" ]; then
    echo "⚠️  Sequential Thinking already exists, updating..."
    cd "$MCP_DIR/sequential-thinking"
    git pull
else
    git clone https://github.com/camilovelezr/server-sequential-thinking.git "$MCP_DIR/sequential-thinking"
    cd "$MCP_DIR/sequential-thinking"
fi

npm install
echo "✅ Sequential Thinking MCP installed at $MCP_DIR/sequential-thinking"
echo ""

# 5. QuantConnect MCP (Docker)
echo "[5/7] Pulling QuantConnect MCP Docker image..."
docker pull quantconnect/mcp-server:latest
echo "✅ QuantConnect MCP image pulled"
echo ""

# 6. Python dependencies for Knowledge RAG (NEW - Day 1)
echo "[6/7] Installing Python dependencies for Knowledge RAG + Alpaca..."
pip install --upgrade pip
pip install chromadb>=0.4.0
pip install rank-bm25
pip install fastmcp
pip install PyPDF2
pip install beautifulsoup4
pip install requests
pip install numpy
echo "✅ Knowledge RAG dependencies installed"
echo ""

# 7. Alpaca MCP (NEW - Day 1)
echo "[7/7] Installing Alpaca MCP server..."
pip install alpaca-mcp-server
echo "✅ Alpaca MCP installed"
echo ""

# Create directories
mkdir -p "$HOME/mcp-logs"
mkdir -p "$HOME/mcp-data"
mkdir -p "$HOME/.chromadb"

echo "✅ All MCP dependencies installed!"
echo ""
echo "📊 Day 1 Components:"
echo "  - Ports 8000-8004: Basic MCPs"
echo "  - Port 8005: Knowledge RAG (ChromaDB + BM25)"
echo "  - Port 8006: Alpaca MCP (rate limited)"
echo ""
echo "Next steps:"
echo "  1. Set up ~/.env.mcp with API keys"
echo "  2. Run: python scripts/ingest_knowledge_db.py"
echo "  3. Run: python scripts/validate_rag.py"
echo "  4. Run: bash scripts/start_all_mcps.sh"
