# Synthetic refund policy

Training fixture only; this is not a real merchant policy.

## Eligibility

Verify tenant and customer identity. Retrieve the order.
An unopened item within 14 days is eligible for policy review.

## Execution

The model may propose a refund. Deterministic policy and write authorization
must approve it. Use an idempotency key and a transaction before recording completion.
