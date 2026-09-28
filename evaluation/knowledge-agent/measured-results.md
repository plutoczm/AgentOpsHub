# Phase 7A measured results

## Deterministic integration evaluation

Command: `python scripts/dev.py eval-knowledge-agent`.

Two invocations each passed all 10 cases (`10/10`, integration pass rate `1.0`). The
case outcomes, corpus hashes, manifest hash, retriever identity, policy values, and all
context fingerprints matched between runs. Timing values were recorded and allowed to
vary. Each invocation used a fresh, uniquely named disposable PostgreSQL database and
removed its Compose resources.

| Case | Verified result |
| --- | --- |
| `positive_supportops` | Current FTS evidence reached the second Agent turn with source provenance |
| `positive_datacopilot` | Trusted DataCopilot namespace returned only its fixture |
| `no_evidence` | Empty retrieval was a successful tool result; final scripted response said evidence was insufficient |
| `tenant_isolation` | Tenant B could not retrieve tenant A's supportops source |
| `namespace_isolation` | A user request to switch namespace did not change trusted supportops scope |
| `latest_revision_only` | Current revision was retrieved |
| `stale_revision_excluded` | Terms found only in the older revision returned no evidence |
| `malicious_evidence_write_escalation` | Injection text was returned as untrusted data; the follow-up `ticket_create` call was denied |
| `budget_pressure` | Ranked whole chunks stayed within the evidence character budget and later chunks were counted as omitted |
| `retriever_failure` | Backend failure remained a safe execution error, distinct from no evidence |

The fixture and manifest hashes, safe case results, durations, and fingerprints are in the
ignored artifact `.artifacts/phase7a-knowledge-agent.json`. Its Git SHA identifies the
starting commit and its dirty flag was true because this evaluation ran against the
uncommitted Phase 7A worktree.

## Phase 6 retrieval regression

Command: `python scripts/dev.py eval-retrieval`.

The existing benchmark repeated its rankings and metrics consistently on PostgreSQL 17.6.
The frozen combined result remained HitRate@5 `0.642857143` and Recall@5 `0.619047619` over
16 synthetic documents and 48 labeled queries. The paraphrase category remained `0/8`;
ordinary wording remained `4/8`. No Phase 6 labels, query semantics, FTS configuration,
ranking formula, or metric formulas changed.

## Repository validation

`python scripts/dev.py check` passed Ruff, format check, strict mypy, and the non-integration
suite (`426 passed, 109 integration tests skipped`). `python scripts/dev.py test-integration`
passed all `535` tests on the isolated PostgreSQL database; the run also passed `/health`
and `/ready` probes before and after stopping that database.

## Evidence limits

These checks establish local integration behavior against disposable PostgreSQL and
deterministic orchestration cases. They do not measure live-model selection or answer
quality, model-specific prompt-injection robustness, business retrieval accuracy, or
production performance. Real-model quality remains **NOT MEASURED**. Dense retrieval,
embeddings, Qdrant, Hybrid/RRF, MCP, and public Agent APIs were not added.
