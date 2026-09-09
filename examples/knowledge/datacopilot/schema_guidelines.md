# Synthetic schema guidelines

Training fixture only; fictional table definitions.

## Constraints

Document primary keys, nullable fields and join cardinalities.
A model proposes SQL; a deterministic validator checks it before execution.

## Example

```sql
CREATE TABLE demo_orders (
    order_id BIGINT PRIMARY KEY,
    total_amount DECIMAL(12, 2) NOT NULL
);
```

This DDL is a knowledge example, not authorization to create a table.
