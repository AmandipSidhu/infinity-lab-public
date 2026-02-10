#!/bin/bash
# stop_all_mcps.sh - Stop all MCP servers
# Part of Infinity 5 Bootstrap v6.1
# Related: UNI-50, MCP_RESEARCH_FINDINGS.md

echo "🛑 Stopping all MCP servers..."
echo ""

# Stop Docker container (QuantConnect)
echo "[1/4] Stopping QuantConnect MCP (Docker)..."
if docker ps -a | grep -q quantconnect-mcp; then
    docker stop quantconnect-mcp 2>/dev/null || true
    docker rm quantconnect-mcp 2>/dev/null || true
    echo "✅ QuantConnect MCP stopped"
else
    echo "ℹ️  QuantConnect MCP not running"
fi
echo ""

# Stop Supergateway instances (Linear + Memory)
echo "[2/4] Stopping Linear MCP (port 8001)..."
if pkill -f "supergateway.*8001" 2>/dev/null; then
    echo "✅ Linear MCP stopped"
else
    echo "ℹ️  Linear MCP not running"
fi
echo ""

echo "[3/4] Stopping Memory MCP (port 8002)..."
if pkill -f "supergateway.*8002" 2>/dev/null; then
    echo "✅ Memory MCP stopped"
else
    echo "ℹ️  Memory MCP not running"
fi
echo ""

# Stop Sequential Thinking (Node.js process)
echo "[4/4] Stopping Sequential Thinking MCP (port 8003)..."
if pkill -f "sequential-thinking" 2>/dev/null; then
    echo "✅ Sequential Thinking MCP stopped"
else
    echo "ℹ️  Sequential Thinking MCP not running"
fi
echo ""

echo "✅ All MCP servers stopped"
echo ""
echo "To restart: ./scripts/start_all_mcps.sh"
