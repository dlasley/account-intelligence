"""Tests for src/simulator/spec.py — Phase 1 of ADR-021.

Covers:
  1  Happy path — minimal valid YAML with two accounts, two entries each
  2  extra="forbid" top level — unrecognised key raises ValidationError
  3  Duplicate entry id — same id in same file raises ValidationError
  4  Date ordering — end_date <= start_date raises ValidationError
  5  Bad primitive name — raises ValidationError
  6  Missing required params — declining without start_health raises ValidationError
  7  stable happy path — target_band parses without error
  8  cliff happy path — all three required keys parse without error
  9  Id format — non-hex or wrong-length raises ValidationError; valid passes
  10 Round-trip — save_spec(load_spec(path)) produces equal spec
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.simulator.spec import (
    TrajectoryEntry,
    check_collision,
    generate_entry_id,
    load_spec,
    save_spec,
    spec_path_for_workspace,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_VALID_YAML = """\
workspace_slug: test-ws
trajectories:
  account-a:
    - id: aabbccdd
      start_date: '2026-03-01'
      end_date: '2026-03-28'
      primitive: stable
      params:
        target_band: [60, 80]
      seed: 1001
      generated_at: null
    - id: 11223344
      start_date: '2026-04-01'
      end_date: '2026-04-28'
      primitive: declining
      params:
        start_health: 80
        end_health: 55
        slope_shape: linear
      seed: 1002
      generated_at: null
  account-b:
    - id: deadbeef
      start_date: '2026-03-01'
      end_date: '2026-03-28'
      primitive: recovering
      params:
        start_health: 40
        end_health: 65
      seed: 2001
      generated_at: null
    - id: cafebabe
      start_date: '2026-04-01'
      end_date: '2026-04-28'
      primitive: oscillating
      params:
        low: 30
        high: 70
        period_weeks: 4
      seed: 2002
      generated_at: null
"""


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "spec.yaml"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# Test 1 — Happy path
# ---------------------------------------------------------------------------


def test_happy_path_loads_correctly(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
    spec = load_spec(path)

    assert spec.workspace_slug == "test-ws"
    assert set(spec.trajectories.keys()) == {"account-a", "account-b"}
    assert len(spec.trajectories["account-a"]) == 2
    assert len(spec.trajectories["account-b"]) == 2

    entry = spec.trajectories["account-a"][0]
    assert entry.id == "aabbccdd"
    assert entry.primitive == "stable"
    assert entry.generated_at is None


# ---------------------------------------------------------------------------
# Test 2 — extra="forbid" on top-level key
# ---------------------------------------------------------------------------


def test_extra_top_level_key_raises(tmp_path: Path) -> None:
    bad_yaml = MINIMAL_VALID_YAML + "woorkspace_slug: typo\n"
    path = _write_yaml(tmp_path, bad_yaml)
    with pytest.raises(ValidationError):
        load_spec(path)


# ---------------------------------------------------------------------------
# Test 3 — Duplicate entry ids
# ---------------------------------------------------------------------------


def test_duplicate_entry_id_raises(tmp_path: Path) -> None:
    yaml_content = """\
workspace_slug: test-ws
trajectories:
  account-a:
    - id: aabbccdd
      start_date: '2026-03-01'
      end_date: '2026-03-28'
      primitive: stable
      params:
        target_band: [60, 80]
      seed: 1001
      generated_at: null
    - id: aabbccdd
      start_date: '2026-04-01'
      end_date: '2026-04-28'
      primitive: stable
      params:
        target_band: [60, 80]
      seed: 1002
      generated_at: null
"""
    path = _write_yaml(tmp_path, yaml_content)
    with pytest.raises(ValidationError, match="Duplicate entry id"):
        load_spec(path)


# ---------------------------------------------------------------------------
# Test 4 — Date ordering: end_date <= start_date
# ---------------------------------------------------------------------------


def test_end_date_before_start_raises() -> None:
    with pytest.raises(ValidationError, match="end_date"):
        TrajectoryEntry(
            id="aabbccdd",
            start_date=date(2026, 3, 28),
            end_date=date(2026, 3, 1),  # before start
            primitive="stable",
            params={"target_band": [60, 80]},
            seed=1,
        )


def test_end_date_equal_start_raises() -> None:
    with pytest.raises(ValidationError, match="end_date"):
        TrajectoryEntry(
            id="aabbccdd",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 1),  # equal
            primitive="stable",
            params={"target_band": [60, 80]},
            seed=1,
        )


# ---------------------------------------------------------------------------
# Test 5 — Bad primitive name
# ---------------------------------------------------------------------------


def test_bad_primitive_raises() -> None:
    with pytest.raises(ValidationError, match="unknown primitive"):
        TrajectoryEntry(
            id="aabbccdd",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 28),
            primitive="swooshing",
            params={"target_band": [60, 80]},
            seed=1,
        )


# ---------------------------------------------------------------------------
# Test 6 — Missing required params: declining without start_health
# ---------------------------------------------------------------------------


def test_declining_missing_start_health_raises() -> None:
    with pytest.raises(ValidationError, match="start_health"):
        TrajectoryEntry(
            id="aabbccdd",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 28),
            primitive="declining",
            params={"end_health": 40},  # start_health missing
            seed=1,
        )


def test_oscillating_missing_period_weeks_raises() -> None:
    with pytest.raises(ValidationError, match="period_weeks"):
        TrajectoryEntry(
            id="aabbccdd",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 28),
            primitive="oscillating",
            params={"low": 30, "high": 70},  # period_weeks missing
            seed=1,
        )


# ---------------------------------------------------------------------------
# Test 7 — stable happy path
# ---------------------------------------------------------------------------


def test_stable_happy_path() -> None:
    entry = TrajectoryEntry(
        id="aabbccdd",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 28),
        primitive="stable",
        params={"target_band": [60, 80]},
        seed=1,
    )
    assert entry.primitive == "stable"
    assert (entry.params.model_extra or {})["target_band"] == [60, 80]


# ---------------------------------------------------------------------------
# Test 8 — cliff happy path
# ---------------------------------------------------------------------------


def test_cliff_happy_path() -> None:
    entry = TrajectoryEntry(
        id="aabbccdd",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 4, 28),
        primitive="cliff",
        params={
            "cliff_date": date(2026, 3, 24),
            "pre_band": [70, 82],
            "post_band": [18, 30],
        },
        seed=1,
    )
    assert entry.primitive == "cliff"
    extra = entry.params.model_extra or {}
    assert "cliff_date" in extra
    assert "pre_band" in extra
    assert "post_band" in extra


# ---------------------------------------------------------------------------
# Test 9 — Id format validation
# ---------------------------------------------------------------------------


def test_id_too_short_raises() -> None:
    with pytest.raises(ValidationError, match="8 lowercase hex"):
        TrajectoryEntry(
            id="xyz",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 28),
            primitive="stable",
            params={"target_band": [60, 80]},
            seed=1,
        )


def test_id_uppercase_raises() -> None:
    with pytest.raises(ValidationError, match="8 lowercase hex"):
        TrajectoryEntry(
            id="AABBCCDD",  # uppercase
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 28),
            primitive="stable",
            params={"target_band": [60, 80]},
            seed=1,
        )


def test_id_valid_hex_passes() -> None:
    entry = TrajectoryEntry(
        id="a1b2c3d4",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 28),
        primitive="stable",
        params={"target_band": [60, 80]},
        seed=1,
    )
    assert entry.id == "a1b2c3d4"


# ---------------------------------------------------------------------------
# Test 10 — Round-trip
# ---------------------------------------------------------------------------


def test_round_trip(tmp_path: Path) -> None:
    original_path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
    spec_a = load_spec(original_path)

    saved_path = tmp_path / "saved.yaml"
    save_spec(spec_a, saved_path)

    spec_b = load_spec(saved_path)

    assert spec_a.workspace_slug == spec_b.workspace_slug
    assert set(spec_a.trajectories.keys()) == set(spec_b.trajectories.keys())
    for slug in spec_a.trajectories:
        entries_a = spec_a.trajectories[slug]
        entries_b = spec_b.trajectories[slug]
        assert len(entries_a) == len(entries_b)
        for ea, eb in zip(entries_a, entries_b, strict=True):
            assert ea.id == eb.id
            assert ea.start_date == eb.start_date
            assert ea.end_date == eb.end_date
            assert ea.primitive == eb.primitive
            assert ea.seed == eb.seed
            assert ea.generated_at == eb.generated_at


def test_round_trip_with_generated_at(tmp_path: Path) -> None:
    """generated_at timestamps survive the round-trip."""
    yaml_content = """\
workspace_slug: test-ws
trajectories:
  account-a:
    - id: aabbccdd
      start_date: '2026-03-01'
      end_date: '2026-03-28'
      primitive: stable
      params:
        target_band: [60, 80]
      seed: 1001
      generated_at: '2026-05-09T14:23:00+00:00'
"""
    path = _write_yaml(tmp_path, yaml_content)
    spec_a = load_spec(path)
    entry_a = spec_a.trajectories["account-a"][0]
    assert entry_a.generated_at is not None
    assert entry_a.generated_at == datetime(2026, 5, 9, 14, 23, 0, tzinfo=UTC)

    saved_path = tmp_path / "saved.yaml"
    save_spec(spec_a, saved_path)
    spec_b = load_spec(saved_path)
    entry_b = spec_b.trajectories["account-a"][0]
    assert entry_b.generated_at == entry_a.generated_at


# ---------------------------------------------------------------------------
# generate_entry_id
# ---------------------------------------------------------------------------


def test_generate_entry_id_format() -> None:
    eid = generate_entry_id()
    assert len(eid) == 8
    assert all(c in "0123456789abcdef" for c in eid)


def test_generate_entry_id_unique() -> None:
    ids = {generate_entry_id() for _ in range(100)}
    # With 4 random bytes, collision probability over 100 samples is negligible.
    assert len(ids) == 100


# ---------------------------------------------------------------------------
# spec_path_for_workspace
# ---------------------------------------------------------------------------


def test_spec_path_for_workspace() -> None:
    p = spec_path_for_workspace("lattice-build")
    assert p == Path("fixtures/synthetic-scenarios/trajectory.lattice-build.yaml")


# ---------------------------------------------------------------------------
# check_collision
# ---------------------------------------------------------------------------


def _make_entry(start: date, end: date, eid: str = "aabbccdd") -> TrajectoryEntry:
    return TrajectoryEntry(
        id=eid,
        start_date=start,
        end_date=end,
        primitive="stable",
        params={"target_band": [60, 80]},
        seed=1,
    )


def test_collision_no_overlap() -> None:
    existing = [_make_entry(date(2026, 3, 1), date(2026, 3, 28))]
    verdict = check_collision(existing, date(2026, 4, 1), date(2026, 4, 28))
    assert verdict.collides is False
    assert verdict.recommend_start is None


def test_collision_exact_overlap() -> None:
    existing = [_make_entry(date(2026, 3, 1), date(2026, 3, 28))]
    verdict = check_collision(existing, date(2026, 3, 1), date(2026, 3, 28))
    assert verdict.collides is True
    from datetime import timedelta

    assert verdict.recommend_start == date(2026, 3, 28) + timedelta(days=1)


def test_collision_partial_overlap() -> None:
    existing = [_make_entry(date(2026, 3, 1), date(2026, 3, 28))]
    # proposed starts before existing ends
    verdict = check_collision(existing, date(2026, 3, 15), date(2026, 4, 15))
    assert verdict.collides is True
    from datetime import timedelta

    assert verdict.recommend_start == date(2026, 3, 28) + timedelta(days=1)


def test_collision_adjacent_no_overlap() -> None:
    """Entry ending on day N and proposed starting on day N+1 do not overlap."""
    existing = [_make_entry(date(2026, 3, 1), date(2026, 3, 28))]
    verdict = check_collision(existing, date(2026, 3, 29), date(2026, 4, 28))
    assert verdict.collides is False
    assert verdict.recommend_start is None


def test_collision_recommend_from_latest_end() -> None:
    """When multiple entries overlap, recommend_start is from the latest end_date."""
    from datetime import timedelta

    existing = [
        _make_entry(date(2026, 3, 1), date(2026, 3, 14), "aabbccdd"),
        _make_entry(date(2026, 3, 15), date(2026, 4, 10), "11223344"),
    ]
    verdict = check_collision(existing, date(2026, 3, 1), date(2026, 4, 28))
    assert verdict.collides is True
    assert verdict.recommend_start == date(2026, 4, 10) + timedelta(days=1)


def test_collision_empty_existing() -> None:
    verdict = check_collision([], date(2026, 3, 1), date(2026, 3, 28))
    assert verdict.collides is False
    assert verdict.recommend_start is None
