# Phase 7A deterministic Agent-context evaluation

Dataset `phase7a-knowledge-agent-v1` evaluates the local KnowledgeRetriever-to-Agent
integration with fixed synthetic sources. It is separate from the frozen Phase 6 retrieval
labels and fixtures.

Run it with:

```text
python scripts/dev.py eval-knowledge-agent
```

The command provisions a unique disposable PostgreSQL database, migrates it, ingests every
manifest fixture through `KnowledgeIngestionService`, builds the real PostgreSQL FTS
retriever and typed tool, and runs each case through `AgentRuntime`, `ToolExecutor`, and a
scripted deterministic Gateway. The runner always tears down its own Compose project and
does not accept a development database URL.

The manifest covers positive SupportOps and DataCopilot evidence, no evidence, tenant and
namespace isolation, latest-revision-only retrieval and stale-revision exclusion, malicious
evidence followed by a denied write, evidence-budget pressure, and a typed retriever failure.
The scripted Gateway checks orchestration and safe boundaries; its responses are not
real-model tool selection, answer quality, or prompt-injection robustness evidence.

The ignored `.artifacts/phase7a-knowledge-agent.json` artifact stores the Git revision and
dirty flag, manifest and fixture hashes, trusted context policy, retriever identity,
case-level safe outcomes, context fingerprints, counts, and durations. It excludes query
text, evidence bodies, database URLs, credentials, and raw model messages. Run details and
limitations are in [measured results](measured-results.md).
