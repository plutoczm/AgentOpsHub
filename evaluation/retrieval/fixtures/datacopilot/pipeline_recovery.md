# Synthetic pipeline recovery

Fictional data-engineering runbook.

## Restart

Resume a failed extract transform load pipeline from its last committed checkpoint.
Use an idempotent write when replaying a batch.
Track the batch identifier to prevent duplicate output.

## Late data

Backfill late arrivals using the event date, not the ingestion date.
Verify row counts and reconcile aggregates after replay.
