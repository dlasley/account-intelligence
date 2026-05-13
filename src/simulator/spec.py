"""Pydantic schema for trajectory YAML files, plus load/save helpers and collision detection.

See ADR-021 §D2 (file naming), §D3 (entry shape), §D11 (YAML schema), §D12 (entry id).
"""

from __future__ import annotations

import re
import secrets
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, NamedTuple

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, field_validator, model_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_PRIMITIVES: frozenset[str] = frozenset(
    ["stable", "declining", "recovering", "oscillating", "cliff"]
)

# Required params per primitive.  Missing any of these at load time is a ValidationError.
_REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "stable": ("target_band",),
    "declining": ("start_health", "end_health"),
    "recovering": ("start_health", "end_health"),
    "oscillating": ("low", "high", "period_weeks"),
    "cliff": ("cliff_date", "pre_band", "post_band"),
}

# ---------------------------------------------------------------------------
# Schema models
# ---------------------------------------------------------------------------


class TrajectoryParams(BaseModel):
    """Primitive-specific params container.

    Different primitives carry different keys, so extra fields are allowed here.
    Required-key validation is handled at the TrajectoryEntry level via a
    model_validator that has access to both `primitive` and `params`.
    """

    model_config = ConfigDict(extra="allow")


class TrajectoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Coerce to str first: YAML parses all-digit hex IDs (e.g. "11223344") as integers.
    id: Annotated[str, BeforeValidator(str)]  # 8-char lowercase hex, stable across runs; see D12
    start_date: date
    end_date: date
    primitive: str
    params: TrajectoryParams
    seed: int
    generated_at: datetime | None = None  # null = pending; ISO-8601 UTC when executed

    @field_validator("id")
    @classmethod
    def id_is_hex(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{8}", v):
            raise ValueError(f"entry id must be exactly 8 lowercase hex chars, got: {v!r}")
        return v

    @field_validator("primitive")
    @classmethod
    def primitive_valid(cls, v: str) -> str:
        if v not in VALID_PRIMITIVES:
            raise ValueError(f"unknown primitive {v!r}; valid: {sorted(VALID_PRIMITIVES)}")
        return v

    @model_validator(mode="after")
    def dates_ordered(self) -> TrajectoryEntry:
        if self.end_date <= self.start_date:
            raise ValueError(
                f"end_date ({self.end_date}) must be strictly after start_date ({self.start_date})"
            )
        return self

    @model_validator(mode="after")
    def params_match_primitive(self) -> TrajectoryEntry:
        """Validate that params carry all required keys for the declared primitive."""
        present = set(self.params.model_extra or {})
        required = _REQUIRED_PARAMS.get(self.primitive, ())
        missing = [k for k in required if k not in present]
        if missing:
            raise ValueError(
                f"primitive {self.primitive!r} requires params: {missing}; "
                f"got keys: {sorted(present)}"
            )
        return self


class TrajectorySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_slug: str
    trajectories: dict[str, list[TrajectoryEntry]]  # account_slug -> entries

    @model_validator(mode="after")
    def no_duplicate_entry_ids(self) -> TrajectorySpec:
        """Duplicate entry ids within a spec file are a ValidationError (see D12)."""
        seen: set[str] = set()
        for account_slug, entries in self.trajectories.items():
            for entry in entries:
                if entry.id in seen:
                    raise ValueError(
                        f"Duplicate entry id {entry.id!r} found for account {account_slug!r}"
                    )
                seen.add(entry.id)
        return self


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def load_spec(path: Path) -> TrajectorySpec:
    """Parse and validate a trajectory YAML file.

    Raises:
        SystemExit: if the file does not exist (user-facing error).
        pydantic.ValidationError: if the YAML fails schema validation.
    """
    if not path.exists():
        raise SystemExit(f"Trajectory spec not found: {path}")
    raw = yaml.safe_load(path.read_text())
    return TrajectorySpec.model_validate(raw)


def save_spec(spec: TrajectorySpec, path: Path) -> None:
    """Serialize spec back to YAML.

    Uses mode="json" so datetimes are serialized as ISO-8601 strings.
    sort_keys=False preserves the field order defined in the Pydantic models.
    """
    data = spec.model_dump(mode="json")
    path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False))


# ---------------------------------------------------------------------------
# Canonical path
# ---------------------------------------------------------------------------


def spec_path_for_workspace(workspace_slug: str) -> Path:
    """Return the canonical path for a workspace's trajectory spec.

    Pattern: fixtures/synthetic-scenarios/trajectory.<workspace_slug>.yaml  (D2)
    Path is relative to repo root (where pytest is invoked and scripts are run).
    """
    return Path("fixtures/synthetic-scenarios") / f"trajectory.{workspace_slug}.yaml"


# ---------------------------------------------------------------------------
# Entry ID generation
# ---------------------------------------------------------------------------


def generate_entry_id() -> str:
    """Generate a stable 8-char lowercase hex entry id.

    Uses secrets.token_hex(4) which produces 4 random bytes → 8 hex chars.
    The caller is responsible for checking uniqueness within the spec if needed.
    """
    return secrets.token_hex(4)


# ---------------------------------------------------------------------------
# Collision detection
# ---------------------------------------------------------------------------


class CollisionVerdict(NamedTuple):
    """Result of check_collision.

    Attributes:
        collides: True when the proposed range overlaps an existing entry.
        recommend_start: the recommended start date when collides is True
            (day after the latest end_date of any overlapping entry); None
            when there is no collision.
    """

    collides: bool
    recommend_start: date | None


def check_collision(
    existing_entries: list[TrajectoryEntry],
    proposed_start: date,
    proposed_end: date,
) -> CollisionVerdict:
    """Detect overlap between a proposed date range and existing entries for one account.

    Two date ranges [A_start, A_end) and [B_start, B_end) overlap iff
    A_start < B_end and B_start < A_end.  We use exclusive end boundaries here
    because the end_date in a trajectory entry represents the last day of the
    period (inclusive), so the overlap condition is:
        proposed_start <= existing_end  AND  existing_start <= proposed_end

    When a collision is found the recommendation is the day after the latest
    overlapping entry's end_date so the caller can continue from there.

    Args:
        existing_entries: all TrajectoryEntry objects for an account.
        proposed_start: proposed start_date of the new entry.
        proposed_end: proposed end_date of the new entry.

    Returns:
        CollisionVerdict(collides=False, recommend_start=None) when clear.
        CollisionVerdict(collides=True, recommend_start=<date>) when overlap found.
    """
    from datetime import timedelta

    overlapping_ends: list[date] = []
    for entry in existing_entries:
        # Inclusive overlap: [A_start, A_end] ∩ [B_start, B_end] is non-empty iff
        # A_start <= B_end AND B_start <= A_end
        if proposed_start <= entry.end_date and entry.start_date <= proposed_end:
            overlapping_ends.append(entry.end_date)

    if not overlapping_ends:
        return CollisionVerdict(collides=False, recommend_start=None)

    latest_end = max(overlapping_ends)
    return CollisionVerdict(collides=True, recommend_start=latest_end + timedelta(days=1))
