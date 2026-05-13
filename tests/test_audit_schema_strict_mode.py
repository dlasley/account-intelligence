"""OpenAI structured-output strict-mode schema contract test.

The audit harness uses `response_format: {type: "json_schema", strict: True, ...}`
when calling GPT-5-mini. OpenAI's strict mode enforces three rules on every
nested object schema:

1. `additionalProperties` MUST be present and MUST be `False`.
2. `properties` MUST be present (can be empty `{}`).
3. `required` MUST be present and MUST list every key in `properties`
   (no missing, no extras).

Violations cause OpenAI to reject the request with HTTP 400 at the
structured-output validator (before any LLM call, before billing). But
the existing audit-harness tests (`tests/test_audit_harness.py`,
`tests/synthetic/test_audit_integration.py`) mock the OpenAI client, so
they never exercise this validator — schema violations ship to main and
surface only on first CI fire.

This test walks the schema offline and asserts strict-mode compliance
without making any API call. It exists specifically to prevent recurrence
of the calibration/tone_fit `additionalProperties: True` bug class
(audit run 2026-05-06, fixed in commit e9e3705).

Reference: https://platform.openai.com/docs/guides/structured-outputs#supported-schemas
"""

import pytest

from scripts.audit_narratives import _AUDIT_JSON_SCHEMA


def _validate_strict_mode(schema: dict, path: str = "$") -> None:
    """Recursively assert that schema satisfies OpenAI strict-mode structured-output rules.

    Raises AssertionError with a path-qualified message on the first violation.
    Path uses jq-style notation ($.faithfulness.details, etc.) for easy navigation
    back to the source.
    """
    if not isinstance(schema, dict):
        return

    schema_type = schema.get("type")

    if schema_type == "object":
        # Rule 1: additionalProperties must be False (not True, not absent)
        additional_properties = schema.get("additionalProperties")
        assert additional_properties is False, (
            f"Strict mode violation at {path}: object schema must have "
            f"`additionalProperties: False`, got {additional_properties!r}. "
            f"OpenAI rejects this with HTTP 400."
        )

        # Rule 2: properties must be present (can be empty dict)
        properties = schema.get("properties")
        assert properties is not None, (
            f"Strict mode violation at {path}: object schema must have "
            f"`properties` key (can be empty dict for closed empty objects)."
        )
        assert isinstance(properties, dict), (
            f"Strict mode violation at {path}: `properties` must be a dict, "
            f"got {type(properties).__name__}."
        )

        # Rule 3: required must list every key in properties (and nothing else)
        required = schema.get("required", [])
        property_keys = set(properties.keys())
        required_keys = set(required)
        missing_required = property_keys - required_keys
        extra_required = required_keys - property_keys
        assert not missing_required, (
            f"Strict mode violation at {path}: `required` is missing keys "
            f"that exist in `properties`: {sorted(missing_required)}. "
            f"OpenAI rejects this with HTTP 400."
        )
        assert not extra_required, (
            f"Strict mode violation at {path}: `required` lists keys not in "
            f"`properties`: {sorted(extra_required)}. "
            f"OpenAI rejects this with HTTP 400."
        )

        # Recurse into each property's sub-schema
        for prop_name, prop_schema in properties.items():
            _validate_strict_mode(prop_schema, path=f"{path}.{prop_name}")

    elif schema_type == "array":
        items = schema.get("items")
        if items is not None:
            _validate_strict_mode(items, path=f"{path}[]")

    # For union types (`["integer", "null"]`) and primitive types ("string",
    # "boolean", etc.), no recursion needed — strict mode only constrains
    # objects and arrays.


class TestAuditSchemaStrictModeCompliance:
    """The audit script's response_format JSON schema must satisfy OpenAI strict mode."""

    def test_audit_schema_passes_strict_mode_validation(self) -> None:
        """Walk _AUDIT_JSON_SCHEMA recursively and assert strict-mode rules at every level.

        This is the regression test for commit e9e3705 (calibration + tone_fit
        `details` sub-schemas had `additionalProperties: True` and missing
        `properties`/`required` — caused HTTP 400 from OpenAI on first audit run).
        """
        _validate_strict_mode(_AUDIT_JSON_SCHEMA)


class TestStrictModeValidatorItself:
    """Self-tests for the validator — make sure it actually catches the violations
    it claims to catch. Otherwise the regression test above is silent."""

    def test_validator_catches_additional_properties_true(self) -> None:
        bad_schema = {
            "type": "object",
            "properties": {"foo": {"type": "string"}},
            "required": ["foo"],
            "additionalProperties": True,
        }
        with pytest.raises(AssertionError, match="additionalProperties"):
            _validate_strict_mode(bad_schema)

    def test_validator_catches_missing_properties(self) -> None:
        bad_schema = {
            "type": "object",
            "additionalProperties": False,
        }
        with pytest.raises(AssertionError, match="properties"):
            _validate_strict_mode(bad_schema)

    def test_validator_catches_missing_required(self) -> None:
        bad_schema = {
            "type": "object",
            "properties": {"foo": {"type": "string"}, "bar": {"type": "string"}},
            "required": ["foo"],  # missing 'bar'
            "additionalProperties": False,
        }
        with pytest.raises(AssertionError, match="missing keys"):
            _validate_strict_mode(bad_schema)

    def test_validator_catches_extra_required(self) -> None:
        bad_schema = {
            "type": "object",
            "properties": {"foo": {"type": "string"}},
            "required": ["foo", "phantom"],  # 'phantom' not in properties
            "additionalProperties": False,
        }
        with pytest.raises(AssertionError, match="not in"):
            _validate_strict_mode(bad_schema)

    def test_validator_catches_nested_violation(self) -> None:
        bad_schema = {
            "type": "object",
            "properties": {
                "outer": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": True,  # violation in nested object
                },
            },
            "required": ["outer"],
            "additionalProperties": False,
        }
        with pytest.raises(AssertionError, match=r"\$\.outer"):
            _validate_strict_mode(bad_schema)

    def test_validator_accepts_empty_closed_object(self) -> None:
        """The fix pattern from commit e9e3705 — empty object with all three keys."""
        good_schema = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
        _validate_strict_mode(good_schema)  # must not raise

    def test_validator_recurses_into_array_items(self) -> None:
        bad_schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,  # violation inside array items
                    },
                },
            },
            "required": ["items"],
            "additionalProperties": False,
        }
        with pytest.raises(AssertionError, match=r"\$\.items\[\]"):
            _validate_strict_mode(bad_schema)

    def test_validator_handles_union_types(self) -> None:
        """Union types like ['integer', 'null'] are common and not constrained by strict mode."""
        good_schema = {
            "type": "object",
            "properties": {"score": {"type": ["integer", "null"]}},
            "required": ["score"],
            "additionalProperties": False,
        }
        _validate_strict_mode(good_schema)  # must not raise
