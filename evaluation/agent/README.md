# Phase 7B live-agent evaluation contract

`phase7b-live-agent-v1.json` is a separate 17-case synthetic dataset for a later authorized
live-model baseline. It covers SupportOps/DataCopilot positive tasks, paraphrase and ordinary
wording, no evidence and unsupported facts, trusted tenant/namespace boundaries, malicious
retrieved instructions, tool selection, write denial, invalid arguments, unknown tools, and
duplicate calls. It does not alter the Phase 6 retrieval data or Phase 7A knowledge-agent data.

## Phase 7B0 harness validation

Run local configuration inspection:

```text
python scripts/dev.py eval-agent-live --preflight
```

Run the deterministic harness against a private ephemeral PostgreSQL database and an offline
`httpx2.MockTransport`:

```text
python scripts/dev.py eval-agent-live --offline
```

This exercises the existing `AgentRuntime -> LLMGateway -> OpenAICompatibleProvider` path. It
reports `HARNESS_VALIDATION`; it is not evidence of model quality. Its ignored artifact is
`.artifacts/phase7b-live-agent.json`.

## Later live modes

The default command only runs preflight. Live smoke and measured runs require the exact process
environment opt-in `AGENTOPSHUB_EVAL_LIVE_LLM=true`, a named route, an enabled provider profile,
an exact resolved model with native tool calling, an API key when required, and dated cloud
pricing with a configured source. Preflight performs no network request and never prints a key
or endpoint.

```text
python scripts/dev.py eval-agent-live --smoke --route agent-eval
python scripts/dev.py eval-agent-live --measured --route agent-eval
```

Smoke is 4 representative cases with 1 repetition; measured is all 17 cases with 2
repetitions. The defaults are 8 model turns per case, 16 proposed tool calls, a 60-second
agent deadline, 2 attempts per candidate, and 512 output tokens per provider call. The
theoretical provider-call ceiling is cases × repetitions × turns × route candidates × retries;
maximum generated output is that ceiling × output tokens per call. Input tokens do not have a
known upper bound. Sequential smoke/measured wall-time ceilings are 4 and 34 agent deadlines
respectively (240 seconds and 2,040 seconds at the default 60-second deadline). The live runner
enforces a USD 10 observed configured-price stop threshold. A
run-local Gateway decorator checks the governor before every model turn and adds each successful
response's existing `CostEstimate`. Once the cumulative observed estimate reaches or exceeds USD
10, the current response may finish its Agent turn, but no later provider generation is delegated.
An estimate is not a provider billing hard cap: the response that crosses the threshold may have
arrived from a request whose dynamically sized input tokens were not known in advance. If a
response has no `CostEstimate` or its currency differs from the run budget, cost observability is
lost and future generations are blocked; `None` is never treated as zero.
Run summaries keep `cost_observable` separate from `cost_complete`: a bounded retry that later
succeeds can continue with its returned estimate, while the summary still marks cost completeness
false when the attempt history contains a failure.

Terminal configuration, model/provider, protocol, runtime, deadline, and Agent-budget errors
abort the live batch after the current case is summarized. Successful bounded retries do not
abort. Deterministic quality failures and safely denied tool actions remain case observations and
do not by themselves abort. Tenant/namespace violations, unauthorized writes, and trusted-context
overrides abort before another case starts. Aborted runs preserve a sanitized partial artifact
with run status, abort reason, case counts, safe traces, and observed-cost accounting.
Artifact schema 1.1.0 adds the optional live batch-control summary; offline runs remain
`HARNESS_VALIDATION` with a complete run status.

## Grading and trace rules

All graders are deterministic. Task success is case-specific: required tool selection,
expected outcome class, expected source and answer linkage where applicable, safe refusal or
policy denial, and no forbidden execution or scope violation must all match. Metrics separate
tool attempts from successful executions and retrieval from final grounded answer behavior.
No LLM-as-judge score is used.

Run traces are immutable, ordered, per-run, and bounded by model-turn, route/retry, and tool
call limits. Safe events include model/provider/deployment, finish reason, attempt metadata,
tool name/effect/result status, durations, usage/cost estimates, context fingerprint, and
retriever identity. Raw prompts, messages, tool arguments/results, evidence, tenant UUIDs,
credentials, authorization headers, and DB URLs are prohibited. Unknown usage/cost stays null;
configured-price estimates are not invoices, and failed unpriced attempts make cost
completeness false.

The Phase 7B0 harness run does not issue a real model call or claim live accuracy. Phase 7B1
requires separate user authorization after provider/model/pricing and call/token bounds have
been reviewed. The proposed DeepSeek non-thinking adapter boundary and official sources are
recorded in [decision 0004](../../docs/decisions/0004-deepseek-v4-flash-nonthinking.md).
