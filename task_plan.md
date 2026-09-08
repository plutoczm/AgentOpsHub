# Phase 1 plan

Starting commit: 63b165081cd08142f71ad4c138fadaa869b7e302 (accepted Phase 0).
Scope: Conda environment migration and PostgreSQL persistence only. No Phase 2.

- [x] Inspect clean repository, architecture, tools, dependencies and test boundaries.
- [x] Create dedicated Conda runtime and verify uv dry-run target; publish A-H proposal.
- [x] Migrate dependencies, scripts and hooks; pass Phase 0 gate and HTTP smoke test.
- [x] Implement database lifecycle, Tenant/Ticket, repositories and migration.
- [x] Add isolated PostgreSQL tests, readiness and CI coverage.
- [x] Run quality gates, migration round trip and actual HTTP/failure checks.
- [x] Update documentation with measured evidence and prepare final staged review.

The single Phase 1 commit is the final operation; Git history records its authoritative result.

Decisions: explicit uv target equals current interpreter prefix, inexact sync to preserve
Conda bootstrap packages; current interpreter executes tools. No base environment changes.
Session.begin owns transactions; repositories flush only; tenant predicates are mandatory.
Independent Compose test PostgreSQL plus run-owned databases keep destructive tests isolated.
