# Phase 6 plan
Starting SHA: e508330a00039c1e3e63e1deadb0322c5396b817.
Scope: Retrieval Evaluation Foundation + Deterministic Lexical Baseline. Stop before Phase 7; no push.
- [x] Clean preflight, schema/contract inspection, PostgreSQL 17.6 verification.
- [x] Publish A-AG proposal before implementation.
- [x] Implement retrieval contracts, scoped latest-only PostgreSQL FTS and new index migration.
- [x] Freeze 16-document/48-query synthetic benchmark and implement pure metrics/evaluator.
- [x] Add isolated benchmark command and comprehensive unit/PostgreSQL tests.
- [x] Run benchmark twice; record actual strengths, failures and local latency.
- [x] Update ADR/scenario/project docs, run full quality/security/staged checks.
- [x] Prepare reviewed Phase 6 commit and exact final report evidence.

Final evidence: 509 passed, 97.37061769616027% combined coverage; 16 documents/48 chunks/48 queries.
All quality gates and 50-file staged/security review passed. Commit is the final operation;
Git history and final report record the resulting SHA and clean status. Phase 7 remains unstarted.
