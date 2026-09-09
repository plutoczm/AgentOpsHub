# Synthetic SQL standards

Training fixture only; no production schema or database connection.

## Read safety

Validate the SQL AST, enforce read-only access, a row limit and a timeout.
Use EXPLAIN or a dry-run where the target system supports it.

## Example

```sql
SELECT order_id, total_amount
FROM demo_orders
WHERE status = 'complete'
LIMIT 10;
```

The example is inert document text and must not be executed during ingestion.
