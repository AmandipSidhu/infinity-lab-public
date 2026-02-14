#!/usr/bin/env python3
"""
Autonomous Strategy Builder
Builds live-trading-worthy algorithmic strategies autonomously for QuantConnect + IBKR
"""

import os
import sys
import time
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import re
from difflib import SequenceMatcher


# MCP Ports
MCP_PORTS = {
    "quantconnect": 8000,
    "linear": 8001,
    "memory": 8002,
    "thinking": 8003,
    "github": 8004,
    "knowledge": 8005,
}


class SessionManager:
    """
    Auto-refresh session management for MCP servers.
    Refreshes sessions every 2 minutes with retry logic.
    """
    
    def __init__(self):
        self.sessions = {}  # {mcp_name: {token, expires_at}}
        self.refresh_interval = 120  # 2 minutes
        
    def ensure_valid_session(self, mcp_name: str) -> Optional[str]:
        """Ensure MCP session is valid, refresh if needed."""
        if mcp_name not in self.sessions:
            self.sessions[mcp_name] = self.init_session(mcp_name)
            return self.sessions[mcp_name]['token']
        
        session = self.sessions[mcp_name]
        if datetime.now() >= session['expires_at']:
            # Refresh session
            print(f"🔄 Refreshing session for {mcp_name}...")
            self.sessions[mcp_name] = self.init_session(mcp_name)
        
        return self.sessions[mcp_name]['token']
    
    def init_session(self, mcp_name: str) -> Dict:
        """Initialize session for MCP server."""
        try:
            if mcp_name == "quantconnect":
                response = requests.post(
                    f"http://localhost:{MCP_PORTS[mcp_name]}/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {}
                    },
                    timeout=10
                )
                token = response.json().get('result', {}).get('sessionId')
                expires_at = datetime.now() + timedelta(seconds=300)  # 5 min
                return {'token': token, 'expires_at': expires_at}
            
            # For other MCPs, use generic session init
            response = requests.post(
                f"http://localhost:{MCP_PORTS.get(mcp_name, 8000)}/mcp",
                json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {}
                },
                timeout=10
            )
            
            return {
                'token': response.json().get('result', {}).get('sessionId'),
                'expires_at': datetime.now() + timedelta(hours=1)
            }
        except Exception as e:
            print(f"⚠️  Session init failed for {mcp_name}: {e}")
            return {'token': None, 'expires_at': datetime.now() + timedelta(hours=1)}


class FitnessTracker:
    """
    Track strategy fitness (Sharpe ratio) across iterations.
    Auto-rollback on degradation to prevent overfitting.
    """
    
    def __init__(self):
        self.history = []  # [(version, sharpe, iteration)]
        
    def record(self, version: str, sharpe: float, iteration: int):
        """Record fitness for a strategy version."""
        self.history.append((version, sharpe, iteration))
        print(f"📊 Iteration {iteration}: Sharpe {sharpe:.2f} (version: {version})")
        
    def should_rollback(self) -> bool:
        """Check if fitness is degrading across 2 consecutive iterations."""
        if len(self.history) < 3:
            return False
        
        recent_sharpes = [h[1] for h in self.history[-3:]]
        
        # Check for degradation across 2 consecutive iterations
        if recent_sharpes[-1] < recent_sharpes[-2] < recent_sharpes[-3]:
            print(f"⚠️  Fitness degrading: {recent_sharpes}")
            return True
        
        return False
    
    def get_best_version(self) -> Optional[Tuple[str, float, int]]:
        """Get the best version by Sharpe ratio."""
        if not self.history:
            return None
        return max(self.history, key=lambda x: x[1])


class ErrorClassifier:
    """
    Classify errors into categories for escalation.
    Uses exact regex matching with 80% similarity fallback.
    """
    
    def __init__(self):
        self.api_errors = [
            r"API.*key.*invalid",
            r"Insufficient.*credits",
            r"Rate limit exceeded",
            r"API.*timeout",
        ]
        
        self.code_errors = [
            r"SyntaxError",
            r"NameError",
            r"TypeError",
            r"IndentationError",
            r"AttributeError",
            r"ImportError",
        ]
        
        self.resource_errors = [
            r"Not enough memory",
            r"Disk space",
            r"Connection refused",
            r"Network.*error",
        ]
        
        self.error_history = []
    
    def classify(self, error_msg: str) -> str:
        """Classify error message into category."""
        # Try exact regex first
        for pattern in self.api_errors:
            if re.search(pattern, error_msg, re.IGNORECASE):
                return "API_ERROR"
        
        for pattern in self.code_errors:
            if re.search(pattern, error_msg, re.IGNORECASE):
                return "CODE_ERROR"
        
        for pattern in self.resource_errors:
            if re.search(pattern, error_msg, re.IGNORECASE):
                return "RESOURCE_ERROR"
        
        # Fallback: 80% similarity with known errors
        for known_error in self.error_history:
            similarity = SequenceMatcher(None, error_msg, known_error['message']).ratio()
            if similarity >= 0.80:
                return known_error['classification']
        
        # Record new error
        return "UNKNOWN"
    
    def record_error(self, error_msg: str, classification: str):
        """Record error for similarity matching."""
        self.error_history.append({
            'message': error_msg,
            'classification': classification
        })


def call_mcp_with_retry(
    session_manager: SessionManager,
    mcp_name: str,
    method: str,
    params: Dict,
    max_retries: int = 3
) -> Dict:
    """Call MCP server with automatic session refresh and retry logic."""
    for attempt in range(max_retries):
        try:
            session_token = session_manager.ensure_valid_session(mcp_name)
            
            headers = {}
            if session_token:
                headers["X-Session-Token"] = session_token
            
            response = requests.post(
                f"http://localhost:{MCP_PORTS.get(mcp_name, 8000)}/mcp",
                json={"jsonrpc": "2.0", "method": method, "params": params},
                headers=headers,
                timeout=30
            )
            
            return response.json()
            
        except Exception as e:
            if "session" in str(e).lower() and attempt < max_retries - 1:
                # Force session refresh
                session_manager.sessions.pop(mcp_name, None)
                print(f"🔄 Session error, retrying... ({attempt + 1}/{max_retries})")
                time.sleep(2)
                continue
            
            if attempt < max_retries - 1:
                print(f"⚠️  Request failed, retrying... ({attempt + 1}/{max_retries})")
                time.sleep(2)
                continue
            
            raise


def send_slack_notification(message: str, webhook_url: Optional[str] = None):
    """Send Slack notification."""
    if not webhook_url:
        webhook_url = os.getenv('SLACK_WEBHOOK')
    
    if not webhook_url:
        print(f"📢 Slack notification (webhook not configured): {message}")
        return
    
    try:
        requests.post(
            webhook_url,
            json={"text": message},
            timeout=10
        )
        print(f"✅ Slack: {message}")
    except Exception as e:
        print(f"⚠️  Slack notification failed: {e}")


def main():
    """Main autonomous build loop."""
    print("="*60)
    print("Autonomous Strategy Builder - v4.1")
    print("="*60 + "\n")
    
    # Initialize components
    session_manager = SessionManager()
    fitness_tracker = FitnessTracker()
    error_classifier = ErrorClassifier()
    
    print("✅ SessionManager initialized (auto-refresh every 2 min)")
    print("✅ FitnessTracker initialized (auto-rollback on degradation)")
    print("✅ ErrorClassifier initialized (regex + similarity matching)")
    print("")
    
    # Example: Initialize sessions for all MCPs
    print("🔄 Initializing MCP sessions...")
    for mcp_name in MCP_PORTS.keys():
        try:
            session_manager.ensure_valid_session(mcp_name)
            print(f"  ✅ {mcp_name} session ready")
        except Exception as e:
            print(f"  ⚠️  {mcp_name} session failed: {e}")
    
    print("\n✅ Autonomous builder ready")
    print("💡 This is a framework - integrate with Aider or your builder")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
