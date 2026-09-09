# Synthetic warehouse access control

Fictional data access policy.

## Roles

Role-based access control (RBAC) limits access to approved schemas.
Use a read-only service role for analytical queries.
Tenant identity selects the permitted data scope.

## Review

Review grants before a schema is shared.
An approved SQL statement does not grant permission to read a restricted table.
