# RAG module boundary

Phase 5 ingestion lives in app/knowledge and app/services/knowledge.py.
Phase 6 PostgreSQL FTS lives in app/retrieval; app/evaluation measures source retrieval.
Phase 7A registers `knowledge_search` under `app.tools.builtin`; bounded context assembly
lives in `app.knowledge.context` and the Agent uses the existing runtime. This directory
does not add a second tool executor or retrieval backend. Live-model answer quality,
embeddings, Dense, Qdrant business retrieval, Hybrid/RRF and reranking remain unimplemented
or unmeasured.
