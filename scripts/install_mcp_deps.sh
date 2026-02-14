#!/bin/bash
# install_mcp_deps.sh - Install all MCP server dependencies
# Part of Infinity 5 Bootstrap v6.2
# Related: UNI-52, UNI-54, MCP_RESEARCH_FINDINGS.md, ARCHITECTURE_v4.0.md

set -e

echo "🔧 Installing MCP dependencies for Infinity 5 v4.1..."
echo ""

# 1. Supergateway (stdio -> HTTP wrapper)
echo "[1/6] Installing Supergateway (stdio transport wrapper)..."
npm install -g supergateway
echo "✅ Supergateway installed"
echo ""

# 2. Linear MCP
echo "[2/6] Installing Linear MCP (@tacticlaunch/mcp-linear)..."
npm install -g @tacticlaunch/mcp-linear
echo "✅ Linear MCP installed"
echo ""

# 3. Memory MCP
echo "[3/6] Installing Memory MCP (official)..."
npm install -g @modelcontextprotocol/server-memory
echo "✅ Memory MCP installed"
echo ""

# 4. Sequential Thinking MCP (clone and build)
echo "[4/6] Installing Sequential Thinking MCP (streamable-http native)..."
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
echo "[5/6] Pulling QuantConnect MCP Docker image..."
docker pull quantconnect/mcp-server:latest
echo "✅ QuantConnect MCP image pulled"
echo ""

# 6. Python dependencies for Knowledge RAG (Day 1 Critical)
echo "[6/6] Installing Python dependencies for Knowledge RAG..."
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

# v4.1: Alpaca removed (Canada restriction per UNI-54)
# QC MCP provides get_history for data validation

# Create directories
mkdir -p "$HOME/mcp-logs"
mkdir -p "$HOME/mcp-data"
mkdir -p "$HOME/.chromadb"

echo "✅ All MCP dependencies installed!"
echo ""
echo "📊 Day 1 Components (v4.1 - 6 MCPs):"
echo "  - Ports 8000-8004: Core MCPs (QC, Linear, Memory, Thinking, GitHub)"
echo "  - Port 8005: Knowledge RAG (ChromaDB + BM25)"
echo "  - Port 8006: Removed (Alpaca - Canada restriction per UNI-54)"
echo ""
echo "Next steps:"
echo "  1. Set up ~/.env.mcp with API keys"
echo "  2. Run: python scripts/ingest_knowledge_db.py"
echo "  3. Run: python scripts/validate_rag.py"
echo "  4. Run: bash scripts/start_all_mcps.sh"
