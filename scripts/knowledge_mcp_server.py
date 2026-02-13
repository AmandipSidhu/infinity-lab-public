#!/usr/bin/env python3
"""
Knowledge RAG MCP Server (Port 8005)
Provides WorldQuant alphas, QC docs, trading patterns, risk formulas
"""

from fastmcp import FastMCP
import chromadb
from chromadb.config import Settings
from rank_bm25 import BM25Okapi
import numpy as np
from pathlib import Path

mcp = FastMCP("Knowledge RAG")

class HybridKnowledgeRAG:
    def __init__(self, persist_dir="~/.chromadb"):
        persist_path = Path(persist_dir).expanduser()
        persist_path.mkdir(parents=True, exist_ok=True)
        
        self.chroma_client = chromadb.PersistentClient(
            path=str(persist_path),
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.chroma_client.get_or_create_collection("trading_knowledge")
        self.bm25 = None
        self.documents = []
        
    def ingest_documents(self, docs):
        """Ingest documents into both semantic and keyword indexes."""
        # Semantic embeddings (ChromaDB)
        self.collection.add(
            documents=[d['text'] for d in docs],
            metadatas=[d['metadata'] for d in docs],
            ids=[d['id'] for d in docs]
        )
        
        # Keyword index (BM25)
        self.documents = docs
        tokenized_docs = [doc['text'].split() for doc in docs]
        self.bm25 = BM25Okapi(tokenized_docs)
    
    def search(self, query, top_k=5, category=None):
        """Hybrid search: semantic (70%) + keyword (30%)."""
        if self.collection.count() == 0:
            return []
        
        # Semantic search
        where_filter = {"category": category} if category else None
        semantic_results = self.collection.query(
            query_texts=[query],
            n_results=min(top_k, self.collection.count()),
            where=where_filter
        )
        
        if not semantic_results['ids'] or not semantic_results['ids'][0]:
            return []
        
        # Keyword search
        tokenized_query = query.split()
        bm25_scores = self.bm25.get_scores(tokenized_query)
        bm25_top_indices = np.argsort(bm25_scores)[-top_k:][::-1]
        
        # Combine results (weighted average)
        combined_results = {}
        for idx, doc_id in enumerate(semantic_results['ids'][0]):
            doc_idx = next((i for i, d in enumerate(self.documents) if d['id'] == doc_id), None)
            if doc_idx is not None:
                combined_results[doc_id] = {
                    'semantic_score': 1.0 - semantic_results['distances'][0][idx],  # Convert distance to similarity
                    'bm25_score': 0,
                    'document': self.documents[doc_idx]
                }
        
        for idx in bm25_top_indices:
            doc_id = self.documents[idx]['id']
            if doc_id in combined_results:
                combined_results[doc_id]['bm25_score'] = bm25_scores[idx]
            else:
                combined_results[doc_id] = {
                    'semantic_score': 0,
                    'bm25_score': bm25_scores[idx],
                    'document': self.documents[idx]
                }
        
        # Weighted combination (70% semantic, 30% keyword)
        for doc_id in combined_results:
            combined_results[doc_id]['final_score'] = (
                0.7 * combined_results[doc_id]['semantic_score'] +
                0.3 * combined_results[doc_id]['bm25_score']
            )
        
        # Sort by final score
        ranked = sorted(combined_results.items(), 
                       key=lambda x: x[1]['final_score'], 
                       reverse=True)[:top_k]
        
        return [item[1]['document'] for item in ranked]

# Initialize RAG
rag = HybridKnowledgeRAG()

@mcp.tool()
def search_trading_knowledge(query: str, category: str = "all") -> list:
    """
    Search WorldQuant alphas, QC docs, patterns, risk formulas.
    
    Args:
        query: Search query (e.g., "RSI divergence strategy")
        category: Filter by category ("all", "worldquant_alpha", "quantconnect_api", 
                 "trading_pattern", "risk_management", "market_regime")
    
    Returns:
        List of relevant documents with text and metadata
    """
    category_filter = None if category == "all" else category
    results = rag.search(query, top_k=5, category=category_filter)
    
    return [
        {
            "id": r['id'],
            "text": r['text'],
            "metadata": r['metadata']
        }
        for r in results
    ]

@mcp.tool()
def get_worldquant_alpha(alpha_number: int) -> dict:
    """
    Get specific WorldQuant alpha by number (1-101).
    
    Args:
        alpha_number: Alpha number (1-101)
    
    Returns:
        Alpha formula, metadata, and performance characteristics
    """
    results = rag.collection.query(
        query_texts=[f"Alpha #{alpha_number}"],
        where={"category": "worldquant_alpha"},
        n_results=1
    )
    
    if not results['ids'] or not results['ids'][0]:
        return {"error": f"Alpha #{alpha_number} not found"}
    
    doc_id = results['ids'][0][0]
    doc = next((d for d in rag.documents if d['id'] == doc_id), None)
    
    if doc:
        return {
            "id": doc['id'],
            "text": doc['text'],
            "metadata": doc['metadata']
        }
    
    return {"error": f"Alpha #{alpha_number} not found in documents"}

@mcp.tool()
def list_categories() -> list:
    """
    List all knowledge categories available in the RAG.
    
    Returns:
        List of category names
    """
    return [
        "worldquant_alpha",
        "quantconnect_api",
        "trading_pattern",
        "risk_management",
        "market_regime"
    ]

@mcp.tool()
def get_knowledge_stats() -> dict:
    """
    Get statistics about the knowledge base.
    
    Returns:
        Document counts by category
    """
    total = rag.collection.count()
    
    categories = {}
    for doc in rag.documents:
        cat = doc['metadata'].get('category', 'unknown')
        categories[cat] = categories.get(cat, 0) + 1
    
    return {
        "total_documents": total,
        "categories": categories
    }

if __name__ == "__main__":
    print("Starting Knowledge RAG MCP server on port 8005...")
    mcp.run(transport="stdio", port=8005)
