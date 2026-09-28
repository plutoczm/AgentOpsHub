# Decision 0004: DeepSeek V4.1 Flash in non-thinking mode for Phase 7B1

**Status:** Selected as the single Phase 7B1-A2 compatibility target. Live requests remain unauthorized.

## Provider contract

Use the OpenAI-compatible Chat Completions adapter at `https://api.deepseek.com` with API model
ID `deepseek-flash`, which DeepSeek documents as DeepSeek-V4.1-Flash. DeepSeek's official
Models & Pricing documentation lists Chat Completions, Tool Calls, the base URL, model version,
and pricing. The official change log dates the V4.1-Flash release to 2026-09-10 and directs API
clients to use `deepseek-flash`.

Sources checked on 2026-09-28:

- [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- [DeepSeek 2026-09-10 change log](https://api-docs.deepseek.com/updates/)
- [DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)

The official Thinking Mode guide states that thinking is enabled by default and documents the
OpenAI-format control `thinking: {"type": "disabled"}`. It also says tool-enabled thinking
loops must round-trip `reasoning_content` on later requests. AgentOpsHub's normalized `Message`
contract does not carry that field, so its DeepSeek profile accepts only a typed
`DeepSeekOptions(thinking_mode="disabled")`. Missing options fail closed before transport;
the serializer writes the explicit `thinking` control. No generic vendor-parameter dictionary
or `reasoning_content` field is added to Agent state, trace, or artifact.

The Chat Completions schema exposes `temperature`. DeepSeek documents that it has no effect in
thinking mode; Phase 7B1 uses temperature 0 only after the preflight confirms non-thinking mode.
This adapter sends the configured temperature with the explicit disabled-mode control.

## Pricing and local limits

The 2026-09-28 check of DeepSeek's official pricing table lists DeepSeek Flash peak cache-miss
input at USD 0.30 per million tokens and peak output at USD 1.20 per million tokens. The local
configuration uses those conservative rates, currency USD, effective date 2026-09-10, and the
official pricing page as the source. DeepSeek also lists lower cache-hit and off-peak rates;
the single-rate `ModelPricing` contract cannot represent those distinctions. This is a
conservative estimator, not a billing record.

With one route candidate, two attempts, 8 model turns, and 512 output tokens per attempt:

- Smoke: 4 cases × 1 repetition × 8 turns × 1 candidate × 2 attempts = 64 attempts and 32,768 output tokens. Output-side bound: USD 0.0393216.
- Measured: 17 cases × 2 repetitions × 8 turns × 1 candidate × 2 attempts = 544 attempts and 278,528 output tokens. Output-side bound: USD 0.3342336.

Input-token usage has no static upper bound, so a full monetary upper bound remains unknown.
The USD 10 observed-estimated-cost threshold is a local stop condition, not a provider billing
cap. The actual local key remains absent; no DeepSeek provider API request or API spend has
occurred.
