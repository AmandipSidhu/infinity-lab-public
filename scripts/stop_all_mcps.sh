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

# Fallback: Kill by port (catches orphaned subshells/wrappers)
echo "Checking for orphaned processes on MCP ports..."
for port in 8000 8001 8002 8003; do
    if command -v lsof > /dev/null 2>&1; then
        if lsof -ti :${port} > /dev/null 2>&1; then
            echo "  Killing process on port ${port}..."
            lsof -ti :${port} | xargs kill -9 2>/dev/null || true
        fi
    elif command -v ss > /dev/null 2>&1; then
        # Fallback for systems without lsof
        PID=$(ss -lptn "sport = :${port}" 2>/dev/null | grep -oP '(?<=pid=)\d+' | head -1)
        if [ -n "$PID" ]; then
            echo "  Killing process $PID on port ${port}..."
            kill -9 "$PID" 2>/dev/null || true
        fi
    fi
done
echo "✅ Port-based cleanup complete"

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

# Verify ports are free
echo "Checking if MCP ports are free..."
for port in 8000 8001 8002 8003; do
    if command -v lsof > /dev/null 2>&1; then
        if lsof -ti :${port} > /dev/null 2>&1; then
            echo "⚠️  Port ${port} still in use"
            lsof -i :${port}
        fi
    fi
done
echo "✅ All ports free"

if docker ps | grep "quantconnect/mcp-server" > /dev/null; then
    echo "⚠️  Warning: Docker containers still running"
    docker ps | grep "quantconnect/mcp-server"
else
    echo "✅ No Docker containers"
fi

echo ""
echo "✅ All MCP servers stopped"
