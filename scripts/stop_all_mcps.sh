#!/bin/bash
# stop_all_mcps.sh - Stop all 5 MCP servers
# Part of Infinity 5 Bootstrap v6.1
# Related: UNI-50, ARCHITECTURE v2.9 CORRECTED

echo "🛑 Stopping all MCP servers..."
echo ""

# Stop all Supergateway processes (wraps QC, Linear, Memory)
echo "Stopping Supergateway processes..."
if pgrep -f "supergateway" > /dev/null; then
    pkill -f "supergateway"
    echo "✅ Supergateway processes stopped"
else
    echo "ℹ️  No Supergateway processes running"
fi

# Stop Sequential Thinking MCP (native Node process)
echo "Stopping Sequential Thinking MCP..."
if pgrep -f "sequential-thinking" > /dev/null; then
    pkill -f "sequential-thinking"
    echo "✅ Sequential Thinking stopped"
else
    echo "ℹ️  Sequential Thinking not running"
fi

# Clean up any orphaned Docker containers from QuantConnect
echo "Cleaning up Docker containers..."
if docker ps -a | grep "quantconnect/mcp-server" > /dev/null; then
    docker ps -a | grep "quantconnect/mcp-server" | awk '{print $1}' | xargs docker rm -f
    echo "✅ Docker containers cleaned"
else
    echo "ℹ️  No Docker containers to clean"
fi

# Verify all processes stopped
echo ""
echo "🔍 Verifying shutdown..."

if pgrep -f "supergateway" > /dev/null; then
    echo "⚠️  Warning: Supergateway still running"
    pgrep -af "supergateway"
else
    echo "✅ No Supergateway processes"
fi

if pgrep -f "sequential-thinking" > /dev/null; then
    echo "⚠️  Warning: Sequential Thinking still running"
    pgrep -af "sequential-thinking"
else
    echo "✅ No Sequential Thinking processes"
fi

if docker ps | grep "quantconnect/mcp-server" > /dev/null; then
    echo "⚠️  Warning: Docker containers still running"
    docker ps | grep "quantconnect/mcp-server"
else
    echo "✅ No Docker containers"
fi

echo ""
echo "✅ All MCP servers stopped"
