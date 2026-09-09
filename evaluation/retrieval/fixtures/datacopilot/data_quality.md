# Synthetic data quality

Fictional warehouse checks.

## Validation

Check primary key uniqueness and reject null values in required fields.
Duplicate records in a batch should be quarantined before publication.

## Reconciliation

Compare source and destination row counts after loading.
A data quality alert must identify the affected partition and the failed rule.
