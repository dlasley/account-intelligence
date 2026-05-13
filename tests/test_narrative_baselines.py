"""Phase 4c — narrative snapshot baseline validity tests.

These tests run without a database connection. They assert that every
committed baseline file under ``fixtures/narrative-baselines/`` is
well-formed and was captured from an audit-clean narrative. Drift in
deterministic fields against the live DB is checked separately by
``scripts/check_narrative_baselines.py`` (DB-coupled, manual).

Why split the check?
- Baseline files are committed; their structure should always be valid.
  That can be asserted in pure-Python tests with no infrastructure.
- The deterministic-field equality check (engagement, engagement_rationale,
  overall_health_score) requires the live workspace state and is run
  manually before merge of narrative-touching PRs. We surface it as a
  CLI script rather than a flaky DB-coupled test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_BASELINE_ROOT = Path("fixtures/narrative-baselines")

_REQUIRED_TOP_KEYS = {
    "schema_version",
    "captured_at",
    "account",
    "deterministic",
    "llm_produced",
    "dim_scores",
    "window",
    "provenance",
}

_REQUIRED_ACCOUNT_KEYS = {"slug", "vertical", "status", "overall_health_score"}
_REQUIRED_DETERMINISTIC_KEYS = {"engagement", "engagement_rationale", "overall_health_score"}
_REQUIRED_LLM_KEYS = {"sentiment", "narrative"}
_REQUIRED_PROVENANCE_KEYS = {
    "prompt_version",
    "generated_at",
    "audit_run_id",
    "audit_overall_passed",
    "audited_at",
}


def _baseline_files() -> list[Path]:
    if not _BASELINE_ROOT.exists():
        return []
    return sorted(_BASELINE_ROOT.glob("*/*.json"))


@pytest.fixture(scope="module")
def baselines() -> list[tuple[Path, dict]]:
    files = _baseline_files()
    if not files:
        pytest.skip("no baseline files in fixtures/narrative-baselines/")
    return [(p, json.loads(p.read_text())) for p in files]


class TestBaselineStructure:
    def test_top_level_keys_present(self, baselines):
        for path, data in baselines:
            missing = _REQUIRED_TOP_KEYS - set(data.keys())
            assert not missing, f"{path}: missing top-level keys {missing}"

    def test_schema_version_is_one(self, baselines):
        for path, data in baselines:
            assert data["schema_version"] == 1, f"{path}: unexpected schema_version"

    def test_account_block_well_formed(self, baselines):
        for path, data in baselines:
            acct = data["account"]
            missing = _REQUIRED_ACCOUNT_KEYS - set(acct.keys())
            assert not missing, f"{path}: missing account keys {missing}"
            assert isinstance(acct["slug"], str) and acct["slug"]
            assert isinstance(acct["overall_health_score"], int)

    def test_deterministic_block_well_formed(self, baselines):
        for path, data in baselines:
            det = data["deterministic"]
            missing = _REQUIRED_DETERMINISTIC_KEYS - set(det.keys())
            assert not missing, f"{path}: missing deterministic keys {missing}"
            assert isinstance(det["engagement"], int)
            assert 1 <= det["engagement"] <= 100
            assert isinstance(det["engagement_rationale"], str)
            assert det["engagement_rationale"]

    def test_llm_produced_block_well_formed(self, baselines):
        for path, data in baselines:
            llm = data["llm_produced"]
            missing = _REQUIRED_LLM_KEYS - set(llm.keys())
            assert not missing, f"{path}: missing llm_produced keys {missing}"
            sent = llm["sentiment"]
            assert sent is None or (isinstance(sent, int) and 1 <= sent <= 100)
            assert isinstance(llm["narrative"], str) and llm["narrative"]

    def test_dim_scores_well_formed(self, baselines):
        for path, data in baselines:
            scores = data["dim_scores"]
            assert isinstance(scores, list), f"{path}: dim_scores must be a list"
            for s in scores:
                assert {"dimension_type", "weight", "score"} <= set(s.keys())
                assert isinstance(s["dimension_type"], str) and s["dimension_type"]
                assert isinstance(s["weight"], (int, float))
                assert 0 <= s["weight"] <= 1
                assert isinstance(s["score"], int)
                assert 1 <= s["score"] <= 100

    def test_dim_scores_sorted_by_dimension_type(self, baselines):
        """Stable ordering for stable diffs during code review."""
        for path, data in baselines:
            types = [s["dimension_type"] for s in data["dim_scores"]]
            assert types == sorted(types), (
                f"{path}: dim_scores not sorted by dimension_type "
                f"(got {types}, expected {sorted(types)})"
            )

    def test_provenance_block_well_formed(self, baselines):
        for path, data in baselines:
            prov = data["provenance"]
            missing = _REQUIRED_PROVENANCE_KEYS - set(prov.keys())
            assert not missing, f"{path}: missing provenance keys {missing}"


class TestBaselinesAreAuditClean:
    """Refuse to commit a baseline whose source narrative did not pass audit."""

    def test_every_baseline_audit_passed(self, baselines):
        for path, data in baselines:
            assert data["provenance"]["audit_overall_passed"] is True, (
                f"{path}: baseline was captured from a narrative whose most-recent "
                "audit verdict was not pass — re-run audit until clean before capturing"
            )

    def test_every_baseline_has_audit_run_id(self, baselines):
        for path, data in baselines:
            run_id = data["provenance"]["audit_run_id"]
            assert isinstance(run_id, str) and run_id, (
                f"{path}: baseline has no audit_run_id — capture refused this; how did it land?"
            )


class TestBaselineCorpusCoverage:
    """Sanity check on the baseline set as a whole."""

    def test_at_least_one_baseline_exists(self, baselines):
        assert len(baselines) >= 1

    def test_account_slugs_unique_within_workspace(self, baselines):
        per_workspace: dict[str, list[str]] = {}
        for path, data in baselines:
            workspace = path.parent.name
            slug = data["account"]["slug"]
            per_workspace.setdefault(workspace, []).append(slug)
        for workspace, slugs in per_workspace.items():
            assert len(slugs) == len(set(slugs)), (
                f"workspace {workspace}: duplicate account slugs {slugs}"
            )

    def test_baseline_filename_matches_account_slug(self, baselines):
        for path, data in baselines:
            assert path.stem == data["account"]["slug"], (
                f"{path}: filename does not match account.slug={data['account']['slug']!r}"
            )
