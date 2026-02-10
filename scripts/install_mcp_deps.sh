#!/bin/bash
# install_mcp_deps.sh - Install all MCP server dependencies
# Part of Infinity 5 Bootstrap v6.1
# Related: UNI-50, MCP_RESEARCH_FINDINGS.md

set -e

echo "🔧 Installing MCP dependencies for Infinity 5..."
echo ""

# 1. Supergateway (stdio -> HTTP wrapper)
echo "[1/5] Installing Supergateway (stdio transport wrapper)..."
npm install -g supergateway
echo "✅ Supergateway installed"
echo ""

# 2. Linear MCP
echo "[2/5] Installing Linear MCP (@tacticlaunch/mcp-linear)..."
npm install -g @tacticlaunch/mcp-linear
echo "✅ Linear MCP installed"
echo ""

# 3. Memory MCP
echo "[3/5] Installing Memory MCP (official)..."
npm install -g @modelcontextprotocol/server-memory
echo "✅ Memory MCP installed"
echo ""

# 4. Sequential Thinking MCP (clone and build)
echo "[4/5] Installing Sequential Thinking MCP (streamable-http native)..."
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
echo "[5/5] Pulling QuantConnect MCP Docker image..."
docker pull quantconnect/mcp-server:latest
echo "✅ QuantConnect MCP image pulled"
echo ""

# Create directories
mkdir -p "$HOME/mcp-logs"
mkdir -p "$HOME/mcp-data"

echo "✅ All MCP dependencies installed!"
echo ""
echo "Next: Set up ~/.env.mcp then run ./scripts/start_all_mcps.sh"
