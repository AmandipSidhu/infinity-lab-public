#!/usr/bin/env python3
"""
Knowledge Database Ingestion
Ingests WorldQuant alphas, QuantConnect docs, trading patterns, risk formulas
"""

import requests
from bs4 import BeautifulSoup
import PyPDF2
import json
import re
from pathlib import Path
from knowledge_mcp_server import HybridKnowledgeRAG

class KnowledgeIngester:
    def __init__(self, output_dir="~/.chromadb"):
        self.output_dir = Path(output_dir).expanduser()
        self.documents = []
    
    def ingest_worldquant_alphas(self, pdf_path="/tmp/worldquant_alphas.pdf"):
        """Parse WorldQuant 101 Alphas PDF."""
        print("📥 Ingesting WorldQuant 101 Alphas...")
        
        # Download PDF if not exists
        pdf_file = Path(pdf_path)
        if not pdf_file.exists():
            print("  ⬇️  Downloading WorldQuant alphas PDF...")
            url = "https://arxiv.org/pdf/1601.00991.pdf"
            response = requests.get(url, timeout=30)
            pdf_file.parent.mkdir(parents=True, exist_ok=True)
            pdf_file.write_bytes(response.content)
            print(f"  ✅ Downloaded to {pdf_path}")
        
        # Parse PDF
        try:
            with open(pdf_path, 'rb') as f:
                pdf = PyPDF2.PdfReader(f)
                text = ""
                for page in pdf.pages:
                    text += page.extract_text()
        except Exception as e:
            print(f"  ⚠️  Could not parse PDF: {e}")
            print("  📝 Adding sample alphas instead...")
            self._add_sample_alphas()
            return
        
        # Extract alpha formulas (simplified pattern matching)
        alpha_pattern = r"Alpha\s*[#]?(\d+)[:\s]+(.+?)(?=Alpha\s*[#]?\d+|$)"
        matches = re.finditer(alpha_pattern, text, re.DOTALL | re.IGNORECASE)
        
        count = 0
        for match in matches:
            alpha_num = match.group(1)
            formula = match.group(2).strip()[:500]  # Limit length
            
            if int(alpha_num) <= 101:  # Only first 101 alphas
                self.documents.append({
                    "id": f"wq_alpha_{alpha_num.zfill(3)}",
                    "text": f"Alpha #{alpha_num}: {formula}",
                    "metadata": {
                        "category": "worldquant_alpha",
                        "alpha_number": int(alpha_num),
                        "type": "volume_price"  # Would classify properly in production
                    }
                })
                count += 1
        
        if count == 0:
            print("  ⚠️  No alphas extracted, adding samples...")
            self._add_sample_alphas()
        else:
            print(f"  ✅ Ingested {count} WorldQuant alphas")
    
    def _add_sample_alphas(self):
        """Add sample WorldQuant alphas for testing."""
        samples = [
            {
                "id": "wq_alpha_001",
                "text": "Alpha #1: (-1 * correlation(rank(delta(log(volume), 1)), rank(((close - open) / open)), 6))",
                "metadata": {"category": "worldquant_alpha", "alpha_number": 1, "type": "volume_price"}
            },
            {
                "id": "wq_alpha_011",
                "text": "Alpha #11: ((rank(ts_max((vwap - close), 3)) + rank(ts_min((vwap - close), 3))) * rank(delta(volume, 3)))",
                "metadata": {"category": "worldquant_alpha", "alpha_number": 11, "type": "volume_momentum"}
            },
            {
                "id": "wq_alpha_042",
                "text": "Alpha #42: (rank((vwap - close)) / rank((vwap + close)))",
                "metadata": {"category": "worldquant_alpha", "alpha_number": 42, "type": "price"}
            },
        ]
        self.documents.extend(samples)
        print(f"  ✅ Added {len(samples)} sample alphas")
    
    def ingest_qc_docs(self):
        """Scrape QuantConnect documentation."""
        print("📥 Ingesting QuantConnect documentation...")
        
        # Curated QC API documentation (most critical methods)
        qc_docs = [
            {
                "id": "qc_api_rsi",
                "text": "RSI(symbol, period, resolution) - Relative Strength Index indicator. Returns RSI object with Current, IsReady properties. Example: self.rsi = self.RSI('SPY', 14, Resolution.Daily)",
                "metadata": {"category": "quantconnect_api", "type": "indicator"}
            },
            {
                "id": "qc_api_sma",
                "text": "SMA(symbol, period, resolution) - Simple Moving Average indicator. Example: self.sma = self.SMA('SPY', 50, Resolution.Daily)",
                "metadata": {"category": "quantconnect_api", "type": "indicator"}
            },
            {
                "id": "qc_api_createbacktest",
                "text": "How to create backtest in QuantConnect: 1) Define algorithm class inheriting QCAlgorithm, 2) Implement Initialize() method, 3) Add Universe or specific assets, 4) Implement OnData() for trading logic",
                "metadata": {"category": "quantconnect_api", "type": "tutorial"}
            },
            {
                "id": "qc_api_setholdings",
                "text": "SetHoldings(symbol, percentage) - Set portfolio allocation. Example: self.SetHoldings('SPY', 0.5) allocates 50% to SPY",
                "metadata": {"category": "quantconnect_api", "type": "trading"}
            },
            {
                "id": "qc_api_liquidate",
                "text": "Liquidate(symbol) - Close all positions in symbol. Example: self.Liquidate('SPY')",
                "metadata": {"category": "quantconnect_api", "type": "trading"}
            },
        ]
        
        self.documents.extend(qc_docs)
        print(f"  ✅ Ingested {len(qc_docs)} QC API docs")
    
    def ingest_trading_patterns(self):
        """Add curated trading pattern library."""
        print("📥 Ingesting trading patterns...")
        
        patterns = [
            {
                "id": "pattern_momentum",
                "text": "Momentum strategy: Buy when price > SMA(N), sell when price < SMA(N). Works best in trending markets. Avoid in sideways/choppy conditions. Risk: whipsaws.",
                "metadata": {"category": "trading_pattern", "type": "momentum", "market_regime": "trending"}
            },
            {
                "id": "pattern_mean_reversion",
                "text": "Mean reversion: Buy when RSI < 30 (oversold), sell when RSI > 70 (overbought). Works in range-bound markets. Risk: catching falling knives in trends.",
                "metadata": {"category": "trading_pattern", "type": "mean_reversion", "market_regime": "ranging"}
            },
            {
                "id": "pattern_rsi_divergence",
                "text": "RSI divergence strategy: Bullish divergence when price makes lower low but RSI makes higher low. Bearish divergence when price makes higher high but RSI makes lower high. Strong reversal signal.",
                "metadata": {"category": "trading_pattern", "type": "divergence", "indicator": "RSI"}
            },
            {
                "id": "pattern_bollinger_reversion",
                "text": "Mean reversion with Bollinger Bands: Buy when price touches lower band, sell when price touches upper band. Works best in low-volatility environments.",
                "metadata": {"category": "trading_pattern", "type": "mean_reversion", "indicator": "Bollinger"}
            },
        ]
        
        self.documents.extend(patterns)
        print(f"  ✅ Ingested {len(patterns)} trading patterns")
    
    def ingest_risk_formulas(self):
        """Add risk management formulas."""
        print("📥 Ingesting risk management formulas...")
        
        formulas = [
            {
                "id": "risk_sharpe",
                "text": "Sharpe Ratio = (Portfolio Return - Risk Free Rate) / Portfolio Standard Deviation. Measures risk-adjusted returns. Values >1 good, >2 excellent, >3 exceptional.",
                "metadata": {"category": "risk_management", "type": "performance_metric", "formula": "(Rp - Rf) / σp"}
            },
            {
                "id": "risk_sortino",
                "text": "Sortino Ratio = (Portfolio Return - Risk Free Rate) / Downside Deviation. Like Sharpe but only penalizes downside volatility. Better for asymmetric returns.",
                "metadata": {"category": "risk_management", "type": "performance_metric"}
            },
            {
                "id": "risk_kelly",
                "text": "Kelly criterion position sizing: f* = (bp - q) / b, where b=odds, p=win probability, q=loss probability. Optimal fraction of capital to risk. Use fractional Kelly (0.25-0.5) in practice.",
                "metadata": {"category": "risk_management", "type": "position_sizing"}
            },
            {
                "id": "risk_var",
                "text": "VaR calculation (Value at Risk): Maximum expected loss at given confidence level. 95% VaR = loss exceeded only 5% of time. Use historical, parametric, or Monte Carlo methods.",
                "metadata": {"category": "risk_management", "type": "risk_metric"}
            },
        ]
        
        self.documents.extend(formulas)
        print(f"  ✅ Ingested {len(formulas)} risk formulas")
    
    def save(self):
        """Save to ChromaDB."""
        print("\n💾 Saving to ChromaDB...")
        rag = HybridKnowledgeRAG(persist_dir=self.output_dir)
        rag.ingest_documents(self.documents)
        print(f"✅ Total documents ingested: {len(self.documents)}")
        print(f"📊 Storage location: {self.output_dir}")
        
        # Show category breakdown
        categories = {}
        for doc in self.documents:
            cat = doc['metadata'].get('category', 'unknown')
            categories[cat] = categories.get(cat, 0) + 1
        
        print("\n📈 Category breakdown:")
        for cat, count in sorted(categories.items()):
            print(f"  - {cat}: {count} documents")

if __name__ == "__main__":
    print("="*60)
    print("Knowledge Database Ingestion")
    print("="*60 + "\n")
    
    ingester = KnowledgeIngester()
    ingester.ingest_worldquant_alphas()
    ingester.ingest_qc_docs()
    ingester.ingest_trading_patterns()
    ingester.ingest_risk_formulas()
    ingester.save()
    
    print("\n" + "="*60)
    print("✅ Knowledge base ready")
    print("="*60)
