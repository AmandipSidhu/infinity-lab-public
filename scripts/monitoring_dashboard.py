#!/usr/bin/env python3
"""
Enhanced Monitoring Dashboard
Flask app for real-time autonomous build monitoring

Features:
- Real-time build status
- Cost analytics per build
- Success rate metrics
- Strategy performance tracking
- MCP health monitoring
"""

from flask import Flask, render_template, jsonify, request
import os
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


app = Flask(__name__)


class BuildDatabase:
    """SQLite database for build tracking."""
    
    def __init__(self, db_path: str = "~/.acb_builds.db"):
        self.db_path = Path(db_path).expanduser()
        self.init_db()
    
    def init_db(self):
        """Initialize database schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS builds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                strategy_name TEXT,
                status TEXT,
                sharpe_ratio REAL,
                max_drawdown REAL,
                annual_return REAL,
                cost_usd REAL,
                duration_seconds INTEGER,
                error_message TEXT,
                model_used TEXT,
                iterations INTEGER
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mcp_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                mcp_name TEXT NOT NULL,
                port INTEGER,
                status TEXT,
                response_time_ms REAL
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def record_build(self, build_data: Dict):
        """Record a build result."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO builds (
                timestamp, strategy_name, status, sharpe_ratio, max_drawdown,
                annual_return, cost_usd, duration_seconds, error_message,
                model_used, iterations
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            build_data.get('timestamp', datetime.now().isoformat()),
            build_data.get('strategy_name'),
            build_data.get('status'),
            build_data.get('sharpe_ratio'),
            build_data.get('max_drawdown'),
            build_data.get('annual_return'),
            build_data.get('cost_usd'),
            build_data.get('duration_seconds'),
            build_data.get('error_message'),
            build_data.get('model_used'),
            build_data.get('iterations')
        ))
        
        conn.commit()
        conn.close()
    
    def get_recent_builds(self, limit: int = 50) -> List[Dict]:
        """Get recent builds."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM builds
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        
        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        return results
    
    def get_statistics(self) -> Dict:
        """Get aggregate statistics."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Success rate
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
                AVG(CASE WHEN status = 'success' THEN sharpe_ratio END) as avg_sharpe,
                AVG(cost_usd) as avg_cost,
                SUM(cost_usd) as total_cost
            FROM builds
            WHERE timestamp > datetime('now', '-30 days')
        ''')
        
        row = cursor.fetchone()
        
        stats = {
            'total_builds': row[0] or 0,
            'successful_builds': row[1] or 0,
            'success_rate': (row[1] / row[0] * 100) if row[0] else 0,
            'avg_sharpe': row[2] or 0,
            'avg_cost': row[3] or 0,
            'total_cost': row[4] or 0
        }
        
        conn.close()
        return stats


db = BuildDatabase()


@app.route('/')
def index():
    """Main dashboard page."""
    return render_template('dashboard.html')


@app.route('/api/status')
def api_status():
    """Get current system status."""
    # Check MCP health
    mcp_status = check_mcp_health()
    
    # Get recent builds
    recent_builds = db.get_recent_builds(limit=10)
    
    # Get statistics
    stats = db.get_statistics()
    
    return jsonify({
        'mcp_health': mcp_status,
        'recent_builds': recent_builds,
        'statistics': stats,
        'timestamp': datetime.now().isoformat()
    })


@app.route('/api/builds')
def api_builds():
    """Get build history."""
    limit = int(request.args.get('limit', 50))
    builds = db.get_recent_builds(limit=limit)
    return jsonify(builds)


@app.route('/api/statistics')
def api_statistics():
    """Get aggregate statistics."""
    stats = db.get_statistics()
    return jsonify(stats)


def check_mcp_health() -> Dict:
    """Check health of all MCP servers."""
    import requests
    
    mcps = {
        'quantconnect': 8000,
        'linear': 8001,
        'memory': 8002,
        'thinking': 8003,
        'github': 8004,
        'knowledge': 8005
    }
    
    health = {}
    
    for name, port in mcps.items():
        try:
            start = datetime.now()
            response = requests.get(f'http://localhost:{port}/health', timeout=2)
            elapsed = (datetime.now() - start).total_seconds() * 1000
            
            health[name] = {
                'status': 'healthy' if response.status_code == 200 else 'degraded',
                'port': port,
                'response_time_ms': elapsed
            }
        except:
            health[name] = {
                'status': 'down',
                'port': port,
                'response_time_ms': None
            }
    
    return health


# HTML Template (inline for simplicity)
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>ACB Monitoring Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f1419;
            color: #e6edf3;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 { margin-bottom: 30px; color: #58a6ff; }
        .grid { 
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .card {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 20px;
        }
        .card h2 { 
            font-size: 18px;
            margin-bottom: 15px;
            color: #8b949e;
            font-weight: 500;
        }
        .metric {
            font-size: 32px;
            font-weight: 600;
            margin-bottom: 5px;
        }
        .metric.success { color: #3fb950; }
        .metric.warning { color: #d29922; }
        .metric.error { color: #f85149; }
        .label { font-size: 14px; color: #8b949e; }
        .mcp-status {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid #21262d;
        }
        .mcp-status:last-child { border-bottom: none; }
        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 10px;
        }
        .status-dot.healthy { background: #3fb950; }
        .status-dot.down { background: #f85149; }
        .builds-table {
            width: 100%;
            border-collapse: collapse;
        }
        .builds-table th {
            text-align: left;
            padding: 10px;
            color: #8b949e;
            font-weight: 500;
            border-bottom: 1px solid #30363d;
        }
        .builds-table td {
            padding: 10px;
            border-bottom: 1px solid #21262d;
        }
        .badge {
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
        }
        .badge.success { background: #238636; color: #fff; }
        .badge.failed { background: #da3633; color: #fff; }
        .auto-refresh { color: #8b949e; font-size: 12px; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 ACB Monitoring Dashboard</h1>
        
        <div class="grid">
            <div class="card">
                <h2>Success Rate (30d)</h2>
                <div class="metric success" id="success-rate">--</div>
                <div class="label">of builds completed successfully</div>
            </div>
            
            <div class="card">
                <h2>Average Sharpe</h2>
                <div class="metric" id="avg-sharpe">--</div>
                <div class="label">across successful strategies</div>
            </div>
            
            <div class="card">
                <h2>Total Cost (30d)</h2>
                <div class="metric warning" id="total-cost">--</div>
                <div class="label">API costs this month</div>
            </div>
            
            <div class="card">
                <h2>Total Builds</h2>
                <div class="metric" id="total-builds">--</div>
                <div class="label">strategies generated</div>
            </div>
        </div>
        
        <div class="grid">
            <div class="card">
                <h2>MCP Health</h2>
                <div id="mcp-health">Loading...</div>
            </div>
            
            <div class="card">
                <h2>Recent Builds</h2>
                <table class="builds-table">
                    <thead>
                        <tr>
                            <th>Strategy</th>
                            <th>Status</th>
                            <th>Sharpe</th>
                        </tr>
                    </thead>
                    <tbody id="recent-builds">
                        <tr><td colspan="3">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
        
        <div class="auto-refresh">Auto-refreshing every 10 seconds</div>
    </div>
    
    <script>
        function updateDashboard() {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    // Update statistics
                    document.getElementById('success-rate').textContent = 
                        data.statistics.success_rate.toFixed(1) + '%';
                    document.getElementById('avg-sharpe').textContent = 
                        data.statistics.avg_sharpe.toFixed(2);
                    document.getElementById('total-cost').textContent = 
                        '$' + data.statistics.total_cost.toFixed(2);
                    document.getElementById('total-builds').textContent = 
                        data.statistics.total_builds;
                    
                    // Update MCP health
                    let mcpHtml = '';
                    for (const [name, status] of Object.entries(data.mcp_health)) {
                        const dotClass = status.status === 'healthy' ? 'healthy' : 'down';
                        mcpHtml += `
                            <div class="mcp-status">
                                <span><span class="status-dot ${dotClass}"></span>${name}</span>
                                <span>${status.response_time_ms ? status.response_time_ms.toFixed(0) + 'ms' : 'down'}</span>
                            </div>
                        `;
                    }
                    document.getElementById('mcp-health').innerHTML = mcpHtml;
                    
                    // Update recent builds
                    let buildsHtml = '';
                    for (const build of data.recent_builds.slice(0, 5)) {
                        const badgeClass = build.status === 'success' ? 'success' : 'failed';
                        buildsHtml += `
                            <tr>
                                <td>${build.strategy_name || 'Unknown'}</td>
                                <td><span class="badge ${badgeClass}">${build.status}</span></td>
                                <td>${build.sharpe_ratio ? build.sharpe_ratio.toFixed(2) : 'N/A'}</td>
                            </tr>
                        `;
                    }
                    document.getElementById('recent-builds').innerHTML = buildsHtml;
                });
        }
        
        // Update on load
        updateDashboard();
        
        // Auto-refresh every 10 seconds
        setInterval(updateDashboard, 10000);
    </script>
</body>
</html>
'''


# Save template
template_dir = Path(__file__).parent / 'templates'
template_dir.mkdir(exist_ok=True)
with open(template_dir / 'dashboard.html', 'w') as f:
    f.write(DASHBOARD_HTML)


if __name__ == '__main__':
    print("="*60)
    print("ACB Monitoring Dashboard")
    print("="*60)
    print("\n🌐 Starting dashboard on http://localhost:5000")
    print("📊 Real-time build monitoring, cost analytics, MCP health")
    print("\n✅ Dashboard ready - open http://localhost:5000 in your browser\n")
    
    # Production mode - debug should only be enabled in development
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
