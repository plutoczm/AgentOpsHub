"""Strict JSON syntax checks before Pydantic schema validation."""

import json
from typing import Never


def reject_nonfinite(value: str) -> Never:
    """Reject nonstandard NaN/Infinity tokens regardless of downstream field types."""
    raise ValueError("Non-finite numbers are not valid JSON")


def validate_json_syntax(value: str | bytes) -> None:
    """Parse standards-compliant JSON without evaluating generated code."""
    json.loads(value, parse_constant=reject_nonfinite)
