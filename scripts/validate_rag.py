#!/usr/bin/env python3
"""
RAG Validation Script
80% precision threshold gate before autonomous builds
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from knowledge_mcp_server import HybridKnowledgeRAG

test_queries = [
    ("RSI divergence strategy", "pattern_rsi_divergence"),
    ("WorldQuant volume momentum", "wq_alpha_011"),
    ("Sharpe ratio calculation", "risk_sharpe"),
    ("How to create backtest in QuantConnect", "qc_api_createbacktest"),
    ("Mean reversion with Bollinger Bands", "pattern_bollinger_reversion"),
    ("Kelly criterion position sizing", "risk_kelly"),
    ("Momentum SMA crossover", "pattern_momentum"),
    ("Alpha #42 formula", "wq_alpha_042"),
    ("VaR calculation", "risk_var"),
    ("QuantConnect RSI indicator", "qc_api_rsi"),
]

def validate_rag():
    """Validate RAG retrieval quality."""
    print("="*60)
    print("RAG Validation - Testing retrieval quality")
    print("="*60 + "\n")
    
    try:
        rag = HybridKnowledgeRAG()
    except Exception as e:
        print(f"❌ Failed to initialize RAG: {e}")
        print("\n💡 Run 'python scripts/ingest_knowledge_db.py' first")
        return 0.0
    
    # Check if knowledge base exists
    total_docs = rag.collection.count()
    if total_docs == 0:
        print("❌ Knowledge base is empty")
        print("\n💡 Run 'python scripts/ingest_knowledge_db.py' first")
        return 0.0
    
    print(f"📚 Knowledge base: {total_docs} documents\n")
    
    correct = 0
    total = len(test_queries)
    
    print("Testing queries...\n")
    
    for query, expected_id in test_queries:
        try:
            results = rag.search(query, top_k=1)
            
            if results and results[0]['id'] == expected_id:
                correct += 1
                print(f"✅ '{query}'")
                print(f"   → Found: {results[0]['id']}\n")
            else:
                actual_id = results[0]['id'] if results else "None"
                print(f"❌ '{query}'")
                print(f"   → Expected: {expected_id}")
                print(f"   → Got: {actual_id}\n")
        except Exception as e:
            print(f"❌ '{query}'")
            print(f"   → Error: {e}\n")
    
    precision = correct / total
    
    print("="*60)
    print(f"RAG Precision: {precision:.1%} ({correct}/{total} correct)")
    print("="*60 + "\n")
    
    if precision < 0.80:
        print(f"❌ FAILED: RAG precision {precision:.1%} below 80% threshold")
        print("\n🔧 Troubleshooting:")
        print("  1. Check if all documents were ingested")
        print("  2. Verify ChromaDB storage at ~/.chromadb")
        print("  3. Re-run ingestion: python scripts/ingest_knowledge_db.py")
        return precision
    
    print("✅ PASSED: RAG validation successful")
    print("\n✅ System ready for autonomous builds")
    return precision

if __name__ == "__main__":
    precision = validate_rag()
    sys.exit(0 if precision >= 0.80 else 1)
