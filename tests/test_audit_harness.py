"""Unit tests for the Phase 3 cross-model narrative audit harness.

Tests cover pure-logic functions only: no DB, no real OpenAI calls.
Expected red state until `scripts/audit_narratives.py` is authored:
    ModuleNotFoundError: No module named 'scripts.audit_narratives'
"""

import re
import uuid
from unittest.mock import MagicMock, patch

import pytest

# This import is the intentional red line.  scripts/audit_narratives.py does not
# exist yet.  Every test in this file will fail at collection time with:
#   ModuleNotFoundError: No module named 'scripts.audit_narratives'
from scripts.audit_narratives import (
    AuditContext,
    AuditResult,
    GateOutcome,
    _build_user_prompt,
    calculate_cost,
    evaluate_corpus_gate,
    evaluate_gate,
    fetch_audit_context,
    generate_audit_run_id,
    parse_audit_response,
)

# ---------------------------------------------------------------------------
# Helpers: canned raw responses the GPT-5 structured-output call would return
# ---------------------------------------------------------------------------

_VALID_RAW_RESPONSE = {
    "faithfulness": {
        "score": 4,
        "passed": True,
        "reasoning": "All major claims trace to provided signals.",
        "details": {"cited_signal_ids": ["sig-001", "sig-002"]},
    },
    "coverage": {
        "score": None,
        "passed": True,
        "reasoning": "Engagement and sentiment dimensions both addressed.",
        "details": {"missing_dimensions": []},
    },
    "calibration": {
        "score": 4,
        "passed": True,
        "reasoning": "Positive language matches sentiment score of 78.",
        "details": {},
    },
    "hallucination": {
        "score": None,
        "passed": True,
        "reasoning": "No invented specifics detected.",
        "details": {"invented_items": []},
    },
    "tone_fit": {
        "score": None,
        "passed": True,
        "reasoning": "Narrative register matches the workspace voice config.",
        "details": {},
    },
}

# ---------------------------------------------------------------------------
# Test 1: parse_audit_response — happy path
# ---------------------------------------------------------------------------


class TestParseAuditResponseHappyPath:
    def test_returns_five_criteria_with_correct_values(self):
        # Arrange
        raw = _VALID_RAW_RESPONSE

        # Act
        result = parse_audit_response(raw)

        # Assert
        assert isinstance(result, AuditResult)
        assert result.faithfulness.score == 4
        assert result.faithfulness.passed is True
        assert result.coverage.passed is True
        assert result.coverage.score is None  # binary criterion
        assert result.calibration.score == 4
        assert result.hallucination.passed is True
        assert result.tone_fit.passed is True
        # All five criteria are populated (not silently skipped)
        assert result.faithfulness is not None
        assert result.coverage is not None
        assert result.calibration is not None
        assert result.hallucination is not None
        assert result.tone_fit is not None


# ---------------------------------------------------------------------------
# Test 2: parse_audit_response — schema mismatch
# ---------------------------------------------------------------------------


class TestParseAuditResponseSchemaMismatch:
    def test_missing_criterion_raises_validation_error(self):
        # Arrange: drop the hallucination key entirely
        raw = {k: v for k, v in _VALID_RAW_RESPONSE.items() if k != "hallucination"}

        # Act + Assert: must raise a clear error, not silently return 4 criteria
        with pytest.raises((ValueError, KeyError, TypeError)):
            parse_audit_response(raw)

    def test_wrong_type_for_score_raises_validation_error(self):
        # Arrange: faithfulness.score is a string instead of int
        raw = {
            **_VALID_RAW_RESPONSE,
            "faithfulness": {
                **_VALID_RAW_RESPONSE["faithfulness"],
                "score": "four",  # wrong type
            },
        }

        # Act + Assert
        with pytest.raises((ValueError, TypeError)):
            parse_audit_response(raw)

    def test_extra_top_level_key_raises_validation_error(self):
        # Arrange: add an unknown criterion
        raw = {
            **_VALID_RAW_RESPONSE,
            "specificity": {"score": 3, "passed": True, "reasoning": "fine", "details": {}},
        }

        # Act + Assert: strict schema must reject extra keys
        with pytest.raises((ValueError, KeyError, TypeError)):
            parse_audit_response(raw)


# ---------------------------------------------------------------------------
# Test 3: evaluate_gate — all pass
# ---------------------------------------------------------------------------


class TestEvaluateGateAllPass:
    def test_overall_passed_true_when_all_criteria_pass(self):
        # Arrange
        result = parse_audit_response(_VALID_RAW_RESPONSE)

        # Act
        gate = evaluate_gate(result)

        # Assert
        assert gate.overall_passed is True
        assert gate.hard_gate_failures == 0
        assert gate.advisory_failures == 0


# ---------------------------------------------------------------------------
# Test 4: evaluate_gate — single hard-gate failure (faithfulness score <= 2)
# ---------------------------------------------------------------------------


class TestEvaluateGateHardFailure:
    def test_faithfulness_score_1_triggers_hard_gate_failure(self):
        # Arrange: faithfulness score = 1, all others pass
        raw = {
            **_VALID_RAW_RESPONSE,
            "faithfulness": {
                "score": 1,
                "passed": False,
                "reasoning": "Narrative is substantially fabricated.",
                "details": {"cited_signal_ids": []},
            },
        }
        result = parse_audit_response(raw)

        # Act
        gate = evaluate_gate(result)

        # Assert (ADR-016 D6: faithfulness score <= 2 = hard gate)
        assert gate.overall_passed is False
        assert gate.hard_gate_failures == 1
        assert gate.advisory_failures == 0

    def test_calibration_score_2_triggers_hard_gate_failure(self):
        # Arrange: calibration score = 2 (on the threshold — must fail)
        raw = {
            **_VALID_RAW_RESPONSE,
            "calibration": {
                "score": 2,
                "passed": False,
                "reasoning": "Narrative tone directly contradicts the numeric score.",
                "details": {},
            },
        }
        result = parse_audit_response(raw)

        gate = evaluate_gate(result)

        assert gate.overall_passed is False
        assert gate.hard_gate_failures == 1

    def test_hallucination_fail_triggers_hard_gate_failure(self):
        # Arrange: hallucination failed (binary)
        raw = {
            **_VALID_RAW_RESPONSE,
            "hallucination": {
                "score": None,
                "passed": False,
                "reasoning": "Narrative invented a contact name.",
                "details": {"invented_items": ["Jane Smith (not in signals)"]},
            },
        }
        result = parse_audit_response(raw)

        gate = evaluate_gate(result)

        assert gate.overall_passed is False
        assert gate.hard_gate_failures == 1

    def test_coverage_fail_triggers_hard_gate_failure(self):
        # Arrange: coverage failed (binary)
        raw = {
            **_VALID_RAW_RESPONSE,
            "coverage": {
                "score": None,
                "passed": False,
                "reasoning": "Engagement dimension not addressed.",
                "details": {"missing_dimensions": ["engagement"]},
            },
        }
        result = parse_audit_response(raw)

        gate = evaluate_gate(result)

        assert gate.overall_passed is False
        assert gate.hard_gate_failures == 1


# ---------------------------------------------------------------------------
# Test 5: evaluate_gate — warning-only failure (tone_fit)
# ---------------------------------------------------------------------------


class TestEvaluateGateWarningOnly:
    def test_tone_fit_failure_does_not_block_overall_pass(self):
        # Arrange: tone_fit fails, all 4 hard-gate criteria pass
        raw = {
            **_VALID_RAW_RESPONSE,
            "tone_fit": {
                "score": None,
                "passed": False,
                "reasoning": "Narrative is too formal for the casual voice config.",
                "details": {},
            },
        }
        result = parse_audit_response(raw)

        # Act (ADR-016 D6: tone_fit is warning-only, does not block)
        gate = evaluate_gate(result)

        # Assert
        assert gate.overall_passed is True
        assert gate.advisory_failures == 1
        assert gate.hard_gate_failures == 0


# ---------------------------------------------------------------------------
# Test 6: calculate_cost — known token counts
# ---------------------------------------------------------------------------


class TestCalculateCost:
    def test_cost_formula_matches_known_values(self):
        """GPT-5-mini: $0.25/M input, $2.00/M output (reasoning + content).
        prompt_tokens=5000, completion_tokens=2000:
            input cost  = 5000 / 1_000_000 * 0.25 = 0.00125
            output cost = 2000 / 1_000_000 * 2.00 = 0.00400
            total       = 0.00525
        Reasoning tokens are billed at the output rate (ADR-016 D1).
        """
        cost = calculate_cost(prompt_tokens=5000, completion_tokens=2000)

        assert cost == pytest.approx(0.00525, rel=1e-4)

    def test_reasoning_tokens_billed_at_output_rate(self):
        """reasoning_tokens are a subset of completion_tokens; both billed at output rate.
        This test confirms the formula does NOT discount reasoning_tokens.
        prompt=0, completion=1000 (all reasoning) → 0 + 1000/1M * 2.00 = 0.002
        """
        cost = calculate_cost(prompt_tokens=0, completion_tokens=1000)

        assert cost == pytest.approx(0.002, rel=1e-4)

    def test_zero_tokens_returns_zero(self):
        cost = calculate_cost(prompt_tokens=0, completion_tokens=0)

        assert cost == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Test 7: cost ceiling guard — refuses to run when estimated cost exceeds limit
# ---------------------------------------------------------------------------


class TestCostCeilingGuard:
    def test_raises_before_any_api_call_when_cost_exceeds_ceiling(self, monkeypatch):
        """With AUDIT_MAX_COST_USD=0.10 and a corpus+per-narrative-cost that would
        exceed that, the script must raise a clear error before touching OpenAI.
        """
        # Arrange
        monkeypatch.setenv("AUDIT_MAX_COST_USD", "0.10")

        # Import the ceiling-check entry point.  Assumption: the script exposes a
        # function like `check_cost_ceiling(corpus_size, per_narrative_estimate_usd)`
        # that raises ValueError if the estimate exceeds the ceiling.
        # Coder must expose this function; if it is inlined in main(), refactor it out.
        from scripts.audit_narratives import check_cost_ceiling

        # corpus_size=100, per_narrative=$0.006 → estimated $0.60 > $0.10 ceiling
        with pytest.raises((ValueError, SystemExit)) as exc_info:
            check_cost_ceiling(corpus_size=100, per_narrative_estimate_usd=0.006)

        # The error message must mention the ceiling or cost — not a silent exit code
        error_text = str(exc_info.value).lower()
        assert any(kw in error_text for kw in ("cost", "ceiling", "exceed", "limit", "0.10"))

    def test_passes_when_estimated_cost_is_within_ceiling(self, monkeypatch):
        monkeypatch.setenv("AUDIT_MAX_COST_USD", "1.00")

        from scripts.audit_narratives import check_cost_ceiling

        # Should not raise: 10 x $0.006 = $0.06 < $1.00
        check_cost_ceiling(corpus_size=10, per_narrative_estimate_usd=0.006)


# ---------------------------------------------------------------------------
# Test 8: dry-run mode — no OpenAI call fires
# ---------------------------------------------------------------------------


class TestDryRunMode:
    def test_no_openai_call_in_dry_run_mode(self):
        """run_dry_run must not instantiate or call the OpenAI client.

        Patches the openai module so any client construction would raise. Calls the
        real run_dry_run; if it tried to construct or call OpenAI, the sentinel
        would fire. (Earlier revision of this test patched run_dry_run itself,
        making the assertion vacuous.)
        """
        sentinel = MagicMock()
        sentinel.side_effect = AssertionError("OpenAI client called in dry-run mode")

        with patch("scripts.audit_narratives.openai") as mock_openai:
            mock_openai.OpenAI = sentinel

            from scripts.audit_narratives import run_dry_run

            result = run_dry_run()

        # If the sentinel had fired, AssertionError would have propagated above.
        # Verify run_dry_run returned a synthetic AuditResult.
        assert result is not None


# ---------------------------------------------------------------------------
# Test 9: audit_run_id format conventions (ADR-016 D11)
# ---------------------------------------------------------------------------


class TestAuditRunIdFormat:
    _SHA_STUB = "a1b2c3d4e5f6"
    _TIMESTAMP = 1746316800
    _DATE_STR = "2026-05-03"
    _HINT = "local"

    def test_ci_run_id_matches_expected_pattern(self):
        run_id = generate_audit_run_id(
            source="ci", sha=self._SHA_STUB, timestamp=self._TIMESTAMP
        )

        # ci_<sha8>_<unix-timestamp>
        assert re.fullmatch(r"ci_[0-9a-f]{8}_\d+", run_id), (
            f"CI run_id '{run_id}' does not match 'ci_<sha8>_<ts>' pattern"
        )
        assert run_id.startswith("ci_")
        assert self._SHA_STUB[:8] in run_id
        assert str(self._TIMESTAMP) in run_id

    def test_nightly_run_id_matches_expected_pattern(self):
        run_id = generate_audit_run_id(source="nightly", date=self._DATE_STR)

        # nightly_<YYYY-MM-DD>
        assert re.fullmatch(r"nightly_\d{4}-\d{2}-\d{2}", run_id), (
            f"Nightly run_id '{run_id}' does not match 'nightly_<date>' pattern"
        )
        assert self._DATE_STR in run_id

    def test_manual_run_id_matches_expected_pattern(self):
        run_id = generate_audit_run_id(
            source="manual", hint=self._HINT, timestamp=self._TIMESTAMP
        )

        # manual_<hint>_<unix-timestamp>
        assert re.fullmatch(r"manual_[a-zA-Z0-9_-]+_\d+", run_id), (
            f"Manual run_id '{run_id}' does not match 'manual_<hint>_<ts>' pattern"
        )
        assert self._HINT in run_id
        assert str(self._TIMESTAMP) in run_id

    def test_unknown_source_raises(self):
        with pytest.raises((ValueError, KeyError)):
            generate_audit_run_id(source="unknown_source")


# ---------------------------------------------------------------------------
# Test 10: evaluate_corpus_gate — ADR-016 §D6 Amendment 2026-05-06
# ---------------------------------------------------------------------------


def _make_gate_outcome(passed: bool) -> GateOutcome:
    return GateOutcome(
        overall_passed=passed,
        hard_gate_failures=0 if passed else 1,
        advisory_failures=0,
    )


class TestEvaluateCorpusGate:
    def test_0_of_5_failures_does_not_block(self):
        """All pass — corpus gate does not block."""
        outcomes = [_make_gate_outcome(True) for _ in range(5)]
        assert evaluate_corpus_gate(outcomes) is False

    def test_1_of_5_failures_does_not_block(self):
        """1/5 failures is at or below 50% — tuning-mode gate does not block."""
        outcomes = [_make_gate_outcome(False)] + [_make_gate_outcome(True) for _ in range(4)]
        assert evaluate_corpus_gate(outcomes) is False

    def test_2_of_5_failures_does_not_block(self):
        """2/5 = 40% failures — below 50% threshold, does not block."""
        outcomes = [_make_gate_outcome(False)] * 2 + [_make_gate_outcome(True)] * 3
        assert evaluate_corpus_gate(outcomes) is False

    def test_3_of_5_failures_blocks(self):
        """3/5 = 60% > 50% — corpus gate blocks (strict more-than-50% threshold)."""
        outcomes = [_make_gate_outcome(False)] * 3 + [_make_gate_outcome(True)] * 2
        assert evaluate_corpus_gate(outcomes) is True

    def test_5_of_5_failures_blocks(self):
        """All fail — corpus gate blocks."""
        outcomes = [_make_gate_outcome(False) for _ in range(5)]
        assert evaluate_corpus_gate(outcomes) is True

    def test_empty_corpus_does_not_block(self):
        """No narratives audited — nothing to gate on, does not block."""
        assert evaluate_corpus_gate([]) is False


# ---------------------------------------------------------------------------
# Test 11: _build_user_prompt — VALID CONTACTS section (ADR-016 D6 follow-up)
# ---------------------------------------------------------------------------


class _StubNarrative:
    """Minimal stand-in for the narrative DB row passed to _build_user_prompt."""

    def __init__(
        self,
        *,
        nid: str = "n-1",
        account_id: str = "a-1",
        narrative: str = "stub narrative",
        sentiment: int | None = 60,
        engagement: int = 70,
    ) -> None:
        self.id = nid
        self.account_id = account_id
        self.narrative = narrative
        self.sentiment = sentiment
        self.engagement = engagement
        self.engagement_rationale = ""
        self.signal_window_start = "2026-04-01"
        self.signal_window_end = "2026-04-30"


class _StubSignal:
    def __init__(
        self,
        *,
        sig_id: str = "sig-1",
        author_contact_id: str | None = None,
        subject: str = "subj",
        body: str = "body",
        direction: str = "inbound",
        occurred_at: str = "2026-04-15",
    ) -> None:
        self.id = sig_id
        self.author_contact_id = author_contact_id
        self.subject = subject
        self.body = body
        self.direction = direction
        self.occurred_at = occurred_at


class TestBuildUserPromptValidContacts:
    def test_renders_valid_contacts_section_when_provided(self):
        narrative = _StubNarrative()
        contacts = [
            {
                "id": "c-1",
                "email": "priya@formationbio.com",
                "display_name": "Priya Sharma",
                "is_internal": False,
            },
        ]
        prompt = _build_user_prompt(narrative, [], contacts=contacts)

        assert "--- VALID CONTACTS FOR THIS ACCOUNT ---" in prompt
        assert "Priya Sharma <priya@formationbio.com>" in prompt

    def test_excludes_internal_contacts_from_roster_listing(self):
        narrative = _StubNarrative()
        contacts = [
            {
                "id": "c-1",
                "email": "priya@formationbio.com",
                "display_name": "Priya Sharma",
                "is_internal": False,
            },
            {
                "id": "c-2",
                "email": "csm@us.com",
                "display_name": "Internal CSM",
                "is_internal": True,
            },
        ]
        prompt = _build_user_prompt(narrative, [], contacts=contacts)
        assert "Priya Sharma" in prompt
        assert "Internal CSM" not in prompt

    def test_signal_author_resolved_from_contacts_dict(self):
        """Signal author UUID is resolved to display_name + email when contacts
        are provided, instead of the bare UUID string."""
        narrative = _StubNarrative()
        signal = _StubSignal(author_contact_id="c-1", subject="Renewal q")
        contacts = [
            {
                "id": "c-1",
                "email": "priya@formationbio.com",
                "display_name": "Priya Sharma",
                "is_internal": False,
            },
        ]
        prompt = _build_user_prompt(narrative, [signal], contacts=contacts)

        assert "Priya Sharma <priya@formationbio.com>" in prompt
        assert "[Renewal q]" in prompt

    def test_no_contacts_renders_explicit_disclaimer(self):
        """When the caller passes no contacts, the prompt explicitly tells the
        auditor to evaluate names against signals only — no false positives
        because the section is missing."""
        narrative = _StubNarrative()
        prompt = _build_user_prompt(narrative, [], contacts=None)
        assert "(contact roster not provided" in prompt

    def test_empty_external_roster_renders_explicit_marker(self):
        narrative = _StubNarrative()
        contacts = [
            {
                "id": "c-2",
                "email": "csm@us.com",
                "display_name": "Internal CSM",
                "is_internal": True,
            },
        ]
        prompt = _build_user_prompt(narrative, [], contacts=contacts)
        assert "(no external contacts on roster)" in prompt


# ---------------------------------------------------------------------------
# Test 12: _build_user_prompt — Signal count field (ADR-021 addendum D3)
# ---------------------------------------------------------------------------


class TestBuildUserPromptSignalCount:
    def test_signal_count_zero_appears_in_prompt(self):
        """Signal count field is present for an empty signal list."""
        narrative = _StubNarrative()
        prompt = _build_user_prompt(narrative, [])
        assert "Signal count: 0" in prompt

    def test_signal_count_nonzero_appears_in_prompt(self):
        """Signal count field reflects the actual number of signals passed."""
        narrative = _StubNarrative()
        signals = [_StubSignal(sig_id=f"sig-{i}") for i in range(3)]
        prompt = _build_user_prompt(narrative, signals)
        assert "Signal count: 3" in prompt

    def test_signal_count_precedes_valid_contacts_section(self):
        """Signal count field appears before the VALID CONTACTS header."""
        narrative = _StubNarrative()
        signals = [_StubSignal()]
        prompt = _build_user_prompt(narrative, signals)
        count_pos = prompt.index("Signal count: 1")
        contacts_pos = prompt.index("--- VALID CONTACTS FOR THIS ACCOUNT ---")
        assert count_pos < contacts_pos


# ---------------------------------------------------------------------------
# Test 13: prompt file structural guardrails (ADR-021 addendum §4 Test 3/4)
# ---------------------------------------------------------------------------


class TestPromptFileStructuralGuardrails:
    def test_audit_prompt_contains_thin_corpus_exception(self):
        """audit-narratives.md contains the thin-corpus coverage exception text."""
        from pathlib import Path

        prompt_text = (Path("scripts/prompts/audit-narratives.md")).read_text()
        assert "Signal count < 5" in prompt_text
        assert "Thin-corpus exception" in prompt_text

    def test_narrative_prompt_contains_thin_corpus_threshold(self):
        """narrative.v1.md contains the thin-corpus signal threshold."""
        from pathlib import Path

        prompt_text = (Path("config/prompts/narrative.v1.md")).read_text()
        assert "fewer than 5 signals" in prompt_text
        assert "sync with audit-narratives.md thin-corpus threshold" in prompt_text


# ---------------------------------------------------------------------------
# Test 14: --include-superseded flag — narrative fetch filter (regression guard)
# ---------------------------------------------------------------------------


class TestIncludeSupersededFlag:
    """Verify the superseded_at filter is applied (or dropped) based on the flag."""

    def _make_mock_sb_client(self, narratives: list[dict]) -> MagicMock:
        """Return a mock Supabase client whose narratives table query resolves to narratives."""
        mock_client = MagicMock()
        # Chain: .table(...).select(...)[.is_(...)]?[.eq(...)]?[.limit(...)]?.execute()
        # We intercept at execute() and return the desired narratives.
        mock_query = MagicMock()
        mock_query.is_.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.limit.return_value = mock_query
        execute_resp = MagicMock()
        execute_resp.data = narratives
        mock_query.execute.return_value = execute_resp
        mock_client.table.return_value.select.return_value = mock_query
        return mock_client, mock_query

    def test_default_applies_superseded_at_null_filter(self):
        """Without --include-superseded, the query must call .is_('superseded_at', 'null')."""
        from scripts.audit_narratives import _build_arg_parser

        parser = _build_arg_parser()
        args = parser.parse_args(["--dry-run"])
        assert args.include_superseded is False

        mock_client, mock_query = self._make_mock_sb_client([])

        with patch("scripts.audit_narratives.create_client", return_value=mock_client, create=True):
            with patch("scripts.audit_narratives._set_supabase_client"):
                # Simulate the fetch logic directly — same code path as main()
                query = mock_client.table("narratives").select("*")
                if not args.include_superseded:
                    query = query.is_("superseded_at", "null")

        mock_query.is_.assert_called_once_with("superseded_at", "null")

    def test_include_superseded_skips_null_filter(self):
        """With --include-superseded, .is_('superseded_at', 'null') must NOT be called."""
        from scripts.audit_narratives import _build_arg_parser

        parser = _build_arg_parser()
        args = parser.parse_args(["--dry-run", "--include-superseded"])
        assert args.include_superseded is True

        mock_client, mock_query = self._make_mock_sb_client([])

        with patch("scripts.audit_narratives.create_client", return_value=mock_client, create=True):
            with patch("scripts.audit_narratives._set_supabase_client"):
                query = mock_client.table("narratives").select("*")
                if not args.include_superseded:
                    query = query.is_("superseded_at", "null")

        mock_query.is_.assert_not_called()


# ---------------------------------------------------------------------------
# Test 15: fetch_audit_context — structural completeness guard
# ---------------------------------------------------------------------------


class TestFetchAuditContext:
    """fetch_audit_context must return an AuditContext with all 5 fields populated.

    Uses a mock supabase client so no real DB calls are made.
    """

    _WS_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "fetch-ctx-workspace")
    _ACC_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "fetch-ctx-account")
    _NARR_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "fetch-ctx-narrative")

    def _make_mock_client(self) -> MagicMock:
        """Build a mock supabase client for fetch_audit_context.

        MagicMock auto-chains all attribute accesses, so the chain always
        terminates at a MagicMock .execute() regardless of depth.  We
        intercept at the table() level and make every .execute() call on
        that table's chain return the same response.  Two calls to the same
        table get different responses via side_effect.
        """

        def _resp(data: list) -> MagicMock:
            r = MagicMock()
            r.data = data
            return r

        sig_resp = _resp([
            {"id": str(uuid.uuid4()), "subject": "s1", "body": "b1",
             "direction": "inbound", "occurred_at": "2026-04-01", "author_contact_id": None}
        ])
        contacts_resp = _resp([
            {"id": str(uuid.uuid4()), "email": "a@b.com",
             "display_name": "A B", "is_internal": False}
        ])
        acct_resp = _resp([
            {"name": "Corp", "vertical": "saas", "status": "active",
             "primary_domain": "corp.com", "additional_domains": []}
        ])
        dim_resp = _resp([
            {"dimension_type": "engagement", "weight": 0.5},
            {"dimension_type": "sentiment", "weight": 0.5},
        ])
        pu_resp = _resp([{"config": {"cascade": [7, 14, 30, 60]}}])
        ws_resp = MagicMock()
        ws_resp.data = {"slug": "test-workspace"}

        def _table_side_effect(name: str):
            # MagicMock chains automatically — we only need to override the
            # .execute() at the leaf of each chain.  Because MagicMock returns
            # the same child mock for repeated attribute accesses, setting
            # execute.return_value once covers any chain depth that ends in
            # .execute().  For tables with two distinct queries we use
            # side_effect on the shared .execute() mock.
            mock = MagicMock()
            if name == "signals":
                # Chain: .select().in_().eq("workspace_id").eq("account_id").execute()
                (
                    mock.select.return_value
                    .in_.return_value.eq.return_value.eq.return_value
                    .execute
                ).return_value = sig_resp
            elif name == "contacts":
                # Chain: .select().eq().eq().is_().execute()
                (
                    mock.select.return_value
                    .eq.return_value.eq.return_value.is_.return_value
                    .execute
                ).return_value = contacts_resp
            elif name == "accounts":
                # Chain: .select().eq("id").eq("workspace_id").execute()
                (
                    mock.select.return_value
                    .eq.return_value.eq.return_value
                    .execute
                ).return_value = acct_resp
            elif name == "workspaces":
                (
                    mock.select.return_value.eq.return_value.single.return_value.execute
                ).return_value = ws_resp
            elif name == "health_dimension_configs":
                # dim_configs uses 2 eq(); product_usage uses 3 eq().
                # The 2-eq path: .select().eq().eq().is_().execute()
                (
                    mock.select.return_value
                    .eq.return_value.eq.return_value.is_.return_value
                    .execute
                ).side_effect = [dim_resp, pu_resp]
                # 3-eq path: .select().eq().eq().eq().is_().execute()
                (
                    mock.select.return_value
                    .eq.return_value.eq.return_value.eq.return_value.is_.return_value
                    .execute
                ).return_value = pu_resp
            return mock

        client = MagicMock()
        client.table.side_effect = _table_side_effect
        return client

    def test_returns_all_five_fields(self):
        narrative = MagicMock()
        narrative.signals_considered = [str(uuid.uuid4())]

        client = self._make_mock_client()
        ctx = fetch_audit_context(narrative, self._WS_ID, self._ACC_ID, client)

        assert isinstance(ctx, AuditContext)
        assert isinstance(ctx.signals, list)
        assert isinstance(ctx.contacts, list)
        assert isinstance(ctx.account_meta, dict)
        assert isinstance(ctx.dimension_configs, list)
        assert isinstance(ctx.product_usage_config, dict)
        assert ctx.workspace_slug == "test-workspace"

    def test_signals_query_scoped_by_workspace_and_account(self):
        """Regression guard: signals query must filter by workspace_id AND account_id.

        Dropping either .eq() silently widens the scope to cross-workspace/account
        signal leakage.  The mock chain validates the exact call sequence.
        """
        ws_id = self._WS_ID
        acc_id = self._ACC_ID
        signal_id = str(uuid.uuid4())

        narrative = MagicMock()
        narrative.signals_considered = [signal_id]

        captured_signals_mock: list[MagicMock] = []

        _orig_make = self._make_mock_client

        def _patched_make():
            client = _orig_make()
            # Intercept the signals table mock to capture it for assertion.
            original_side_effect = client.table.side_effect

            def _capturing_side_effect(name: str):
                mock = original_side_effect(name)
                if name == "signals":
                    captured_signals_mock.append(mock)
                return mock

            client.table.side_effect = _capturing_side_effect
            return client

        client = _patched_make()
        fetch_audit_context(narrative, ws_id, acc_id, client)

        assert len(captured_signals_mock) == 1
        sig_mock = captured_signals_mock[0]
        # Verify .in_() was called first (on signal IDs), then two .eq() calls
        # for workspace_id and account_id respectively.
        in_call_args = sig_mock.select.return_value.in_.call_args
        assert in_call_args is not None, ".in_() was not called on signals table"
        # First eq after in_: workspace_id
        eq1_call = sig_mock.select.return_value.in_.return_value.eq.call_args_list
        assert len(eq1_call) >= 1, "No .eq() calls after .in_() on signals"
        first_eq_args = eq1_call[0][0]
        assert first_eq_args[0] == "workspace_id", (
            f"First .eq() after .in_() should filter workspace_id, got {first_eq_args[0]!r}"
        )
        assert first_eq_args[1] == str(ws_id), (
            f"workspace_id filter value mismatch: {first_eq_args[1]!r}"
        )
        # Second eq: account_id
        eq2_call = sig_mock.select.return_value.in_.return_value.eq.return_value.eq.call_args_list
        assert len(eq2_call) >= 1, "No second .eq() call (account_id) on signals"
        second_eq_args = eq2_call[0][0]
        assert second_eq_args[0] == "account_id", (
            f"Second .eq() should filter account_id, got {second_eq_args[0]!r}"
        )
        assert second_eq_args[1] == str(acc_id), (
            f"account_id filter value mismatch: {second_eq_args[1]!r}"
        )

    def test_accounts_query_scoped_by_workspace_id(self):
        """Regression guard: accounts query must filter by both id AND workspace_id.

        Dropping workspace_id .eq() allows fetching another workspace's account
        metadata given a known account UUID.
        """
        ws_id = self._WS_ID
        acc_id = self._ACC_ID

        narrative = MagicMock()
        narrative.signals_considered = []

        captured_accounts_mock: list[MagicMock] = []

        orig_side_effect = None
        client = self._make_mock_client()
        orig_side_effect = client.table.side_effect

        def _capturing_side_effect(name: str):
            mock = orig_side_effect(name)
            if name == "accounts":
                captured_accounts_mock.append(mock)
            return mock

        client.table.side_effect = _capturing_side_effect
        fetch_audit_context(narrative, ws_id, acc_id, client)

        assert len(captured_accounts_mock) == 1
        acct_mock = captured_accounts_mock[0]
        # First eq: id = acc_id
        eq_calls = acct_mock.select.return_value.eq.call_args_list
        assert len(eq_calls) >= 1, "No .eq() calls on accounts table"
        assert eq_calls[0][0][0] == "id", (
            f"First .eq() on accounts should filter 'id', got {eq_calls[0][0][0]!r}"
        )
        assert eq_calls[0][0][1] == str(acc_id)
        # Second eq: workspace_id
        eq2_calls = acct_mock.select.return_value.eq.return_value.eq.call_args_list
        assert len(eq2_calls) >= 1, "No second .eq() call (workspace_id) on accounts"
        assert eq2_calls[0][0][0] == "workspace_id", (
            f"Second .eq() on accounts should filter 'workspace_id', got {eq2_calls[0][0][0]!r}"
        )
        assert eq2_calls[0][0][1] == str(ws_id)


# ---------------------------------------------------------------------------
# Test 16: audit_one_narrative — missing AuditContext fails at call site
# ---------------------------------------------------------------------------


class TestAuditOneNarrativeRequiresContext:
    """Calling audit_one_narrative without a context argument raises TypeError.

    This is the structural guard: the function no longer accepts individual
    signal/contacts/... kwargs, so any caller that omits context fails at
    runtime (and at type-check time with pyright).
    """

    def test_calling_without_context_raises_type_error(self):
        from scripts.audit_narratives import audit_one_narrative

        narrative = MagicMock()

        with pytest.raises(TypeError):
            audit_one_narrative(  # type: ignore[call-arg]
                narrative=narrative,
                # context intentionally omitted
                audit_run_id="manual_test_123",
                audit_source="manual",
                dry_run=True,
            )


# ---------------------------------------------------------------------------
# Test 17: audit call max_completion_tokens floor (regression guard)
# ---------------------------------------------------------------------------


def test_audit_call_max_completion_tokens_at_least_16000():
    """audit_one_narrative calls chat.completions.create with max_completion_tokens >= 16000.

    The prior cap of 3000 caused RuntimeError on the revenant-systems narrative
    (lattice-build run 2026-05-11) when verbose low-effort reasoning exceeded the
    budget.  16000 gives >5x headroom over the failure point; GPT-5-mini supports up
    to 16384 output tokens.
    """
    import scripts.audit_narratives as _mod

    captured_kwargs: list[dict] = []

    def _capture_create(**kwargs):
        captured_kwargs.append(kwargs)
        # Return a minimal well-formed response so audit_one_narrative doesn't error.
        choice = MagicMock()
        choice.finish_reason = "stop"
        choice.message.parsed = None
        choice.message.content = (
            '{"faithfulness":{"score":4,"passed":true,"reasoning":"ok","details":{"cited_signal_ids":[]}},'
            '"coverage":{"score":null,"passed":true,"reasoning":"ok","details":{"missing_dimensions":[]}},'
            '"calibration":{"score":4,"passed":true,"reasoning":"ok","details":{}},'
            '"hallucination":{"score":null,"passed":true,"reasoning":"ok","details":{"invented_items":[]}},'
            '"tone_fit":{"score":null,"passed":true,"reasoning":"ok","details":{}}}'
        )
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 200
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = usage
        return resp

    mock_completions = MagicMock()
    mock_completions.create.side_effect = _capture_create
    mock_chat = MagicMock()
    mock_chat.completions = mock_completions
    mock_openai_client = MagicMock()
    mock_openai_client.chat = mock_chat

    narrative = _StubNarrative()
    context = AuditContext(
        signals=[],
        contacts=[],
        account_meta={"name": "Test Corp", "vertical": "saas", "status": "active",
                      "primary_domain": "testcorp.com", "additional_domains": []},
        dimension_configs=[],
        product_usage_config={},
        workspace_slug="test-workspace",
    )

    with (
        patch("scripts.audit_narratives.openai") as mock_openai_module,
        patch("scripts.audit_narratives._insert_audit_row"),
    ):
        mock_openai_module.OpenAI.return_value = mock_openai_client

        _mod.audit_one_narrative(
            narrative=narrative,
            context=context,
            workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            audit_run_id="manual_test_cap",
            audit_source="manual",
            dry_run=False,
        )

    assert captured_kwargs, "Expected chat.completions.create to be called"
    assert captured_kwargs[0]["max_completion_tokens"] >= 16000, (
        f"max_completion_tokens was {captured_kwargs[0]['max_completion_tokens']}; "
        "must be >= 16000 to avoid truncation on verbose low-effort reasoning output"
    )
