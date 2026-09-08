# Internal LLM gateway — Phase 2

Implemented: typed contracts, profile-configured OpenAI-compatible HTTP transport,
capabilities, bounded retry/fallback/deadlines, local structured validation,
usage/cost normalization and safe in-memory attempt metadata.

Phase 4 AgentRuntime uses the Gateway protocol. No model runtime, public proxy endpoint, RAG, embedding or persistent tracing.
Tests use MockTransport. No real provider/model has been called for acceptance.
See the root README and ARCHITECTURE for configuration and semantics.
