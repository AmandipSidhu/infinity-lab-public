#!/usr/bin/env python3
"""
Alpaca MCP Server with Rate Limiting (Port 8006)
Token bucket rate limiter: 40 req/min (80% margin from 200 req/min limit)
"""

import time
import os
from collections import deque
from typing import Optional

class TokenBucketRateLimiter:
    """Token bucket rate limiter for Alpaca API calls."""
    
    def __init__(self, max_tokens=40, refill_rate=40/60):
        """
        Args:
            max_tokens: Maximum tokens (40 for safe 200 req/min limit)
            refill_rate: Tokens per second (40/60 = 0.67)
        """
        self.max_tokens = max_tokens
        self.tokens = max_tokens
        self.refill_rate = refill_rate
        self.last_refill = time.time()
        self.request_history = deque(maxlen=100)
    
    def acquire(self, tokens=1):
        """Acquire tokens, blocking if necessary."""
        # Refill tokens based on time passed
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            self.request_history.append(now)
            return True
        else:
            # Calculate wait time
            wait_time = (tokens - self.tokens) / self.refill_rate
            print(f"⏳ Rate limit: waiting {wait_time:.1f}s...")
            time.sleep(wait_time)
            self.tokens = 0
            self.request_history.append(time.time())
            return True
    
    def get_stats(self):
        """Get rate limiter statistics."""
        now = time.time()
        recent = [t for t in self.request_history if now - t < 60]
        return {
            'requests_last_minute': len(recent),
            'tokens_available': self.tokens,
            'rate_limit': self.max_tokens
        }

# Initialize rate limiter
rate_limiter = TokenBucketRateLimiter()

def rate_limited_alpaca_call(func):
    """Decorator to rate limit Alpaca API calls."""
    def wrapper(*args, **kwargs):
        rate_limiter.acquire()
        return func(*args, **kwargs)
    return wrapper

if __name__ == "__main__":
    print("Starting Alpaca MCP server with rate limiting on port 8006...")
    print(f"⚙️  Rate limit: {rate_limiter.max_tokens} requests/minute")
    
    # Check for Alpaca credentials
    if not os.environ.get('ALPACA_API_KEY'):
        print("⚠️  Warning: ALPACA_API_KEY not set")
        print("   Set environment variables:")
        print("   - ALPACA_API_KEY")
        print("   - ALPACA_API_SECRET")
        print("   - ALPACA_BASE_URL (default: https://paper-api.alpaca.markets)")
    
    try:
        # Import alpaca-mcp-server
        from alpaca_mcp_server import AlpacaMCP
        
        # Wrap with rate limiter
        class RateLimitedAlpacaMCP(AlpacaMCP):
            def __call__(self, *args, **kwargs):
                rate_limiter.acquire()
                return super().__call__(*args, **kwargs)
        
        mcp = RateLimitedAlpacaMCP()
        print("✅ Alpaca MCP initialized with rate limiting")
        mcp.run(transport="stdio", port=8006)
        
    except ImportError as e:
        print(f"❌ Failed to import alpaca-mcp-server: {e}")
        print("\n🔧 Install with: pip install alpaca-mcp-server")
        print("   Or run: bash scripts/install_mcp_deps.sh")
        
    except Exception as e:
        print(f"❌ Failed to start Alpaca MCP: {e}")
