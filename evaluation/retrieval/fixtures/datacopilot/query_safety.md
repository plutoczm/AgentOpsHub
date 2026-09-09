# Synthetic query safety

Fictional warehouse execution policy.

## Validation

Parse the SQL abstract syntax tree (AST) before execution.
Reject multiple statements and disallowed write operations.
Read-only SQL needs a row limit and a timeout.

## Planning

Use EXPLAIN to inspect estimated query cost.
A dry-run does not authorize a write query.
Cancel a runaway query when the timeout expires.
