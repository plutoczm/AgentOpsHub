# Evaluation package boundary — planned

Phase 0 reserves this directory only. There is no executable evaluation package,
benchmark dataset, retrieval experiment or result yet.

The future independent distribution will use `evaluation/pyproject.toml`,
`evaluation/src/agentopshub_eval/`, `evaluation/tests/`, and a versioned dataset
manifest. It will call retrieval/agent interfaces, not import persistence internals.

Required metrics: Recall@K, MRR, Hit Rate@K, latency, token usage and answer relevance.
The first real experiment will compare dense, hybrid and hybrid + reranking with
fixed corpus/query splits and retained raw outputs. See ARCHITECTURE.md for metric
definitions and reproducibility requirements. Synthetic unit fixtures are never
presented as real-world benchmark evidence.
