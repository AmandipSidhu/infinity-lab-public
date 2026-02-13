#!/bin/bash
# Health Check Script
# Triple-fallback: HTTP /health -> MCP protocol -> Port listening

set -e

check_mcp_health() {
    local port=$1
    local name=$2
    local retries=3
    
    for i in $(seq 1 $retries); do
        # Method 1: HTTP /health endpoint
        if curl -sf http://localhost:${port}/health > /dev/null 2>&1; then
            echo "✅ ${name} (port ${port}): HTTP health OK"
            return 0
        fi
        
        # Method 2: MCP protocol health
        if curl -sf -X POST http://localhost:${port}/mcp \
            -H "Content-Type: application/json" \
            -d '{"jsonrpc":"2.0","method":"notifications/health","params":{}}' \
            | grep -q "ok" 2>/dev/null; then
            echo "✅ ${name} (port ${port}): MCP health OK"
            return 0
        fi
        
        # Method 3: Port listening check
        if command -v lsof >/dev/null 2>&1; then
            if lsof -i :${port} > /dev/null 2>&1; then
                echo "⚠️  ${name} (port ${port}): Port open (health endpoint unresponsive)"
                return 0
            fi
        elif command -v ss >/dev/null 2>&1; then
            if ss -tuln | grep -q ":${port}"; then
                echo "⚠️  ${name} (port ${port}): Port open (health endpoint unresponsive)"
                return 0
            fi
        fi
        
        # Wait before retry
        if [ $i -lt $retries ]; then
            sleep 2
        fi
    done
    
    echo "❌ ${name} (port ${port}): FAILED after ${retries} attempts"
    return 1
}

echo "========================================"
echo "MCP Health Check - All 7 Ports"
echo "========================================"
echo ""

# Track failures
failed=0

# Check all MCPs
check_mcp_health 8000 "QuantConnect" || failed=$((failed + 1))
check_mcp_health 8001 "Linear" || failed=$((failed + 1))
check_mcp_health 8002 "Memory" || failed=$((failed + 1))
check_mcp_health 8003 "Sequential Thinking" || failed=$((failed + 1))
check_mcp_health 8004 "GitHub" || failed=$((failed + 1))
check_mcp_health 8005 "Knowledge RAG" || failed=$((failed + 1))
check_mcp_health 8006 "Alpaca" || failed=$((failed + 1))

echo ""
echo "========================================"

if [ $failed -eq 0 ]; then
    echo "✅ All 7 MCPs healthy"
    echo "========================================"
    exit 0
else
    echo "❌ ${failed} MCP(s) failed health check"
    echo "========================================"
    echo ""
    echo "🔧 Troubleshooting:"
    echo "  1. Check if MCPs are running: bash scripts/start_all_mcps.sh"
    echo "  2. Check logs in ~/.mcp_logs/"
    echo "  3. Verify ports are not in use: lsof -i :8000-8006"
    exit 1
fi
