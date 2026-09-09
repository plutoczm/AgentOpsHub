# RAG generation boundary: planned

Phase 5 ingestion lives in app/knowledge and app/services/knowledge.py.
Phase 6 PostgreSQL FTS lives in app/retrieval; app/evaluation measures source retrieval.
This directory has no answer generation, embeddings, Dense, Qdrant business retrieval,
Hybrid/RRF or reranker. No knowledge_search tool is registered.
