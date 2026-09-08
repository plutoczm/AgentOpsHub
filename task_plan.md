# Phase 4 plan

Starting SHA: 00fa41009917a6d0eea5d526a38b4612e4f284ce. Phase 4 only; no push.

- [x] Recover accepted Conda/task-process workflow and verify clean preflight.
- [x] Inspect existing boundaries and official wheel API; publish design proposal.
- [x] Lock and sync dependency; inspect installed API.
- [x] Implement bounded internal StateGraph runtime.
- [x] Offline and real PostgreSQL security tests.
- [x] Documentation, quality gates and security review.
- [x] Prepare reviewed Phase 4 commit and final report evidence.

Preflight correction: Windows CreateProcess does not search the child env PATH for the executable. Use accepted wrapper with explicit executable arguments, or set PATH inside the temporary Python launcher before resolution. No global changes.

Final validation: 57 focused Agent tests; 339 complete real-PostgreSQL run; 98.3204% combined coverage. Staged membership/byte review and safety checks passed. Commit is the final operation; Git history and final response record resulting SHA and clean status. No Phase 5 work.
