"""End-to-end integration tests for the Phase 3 audit harness.

Exercises the full audit-one-narrative path with:
  - mocked OpenAI structured-output response
  - mocked Supabase client (writes captured into a list)

Expected red state until `scripts/audit_narratives.py` is authored:
    ModuleNotFoundError: No module named 'scripts.audit_narratives'

ADR-016 references:
  - D4  narrative_audits table (5 criterion rows per narrative)
  - D11 audit_source values + audit_run_id conventions
  - D12 narrative_audit_runs aggregate table (1 row per narrative)
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

# This import is the intentional red line.
from scripts.audit_narratives import AuditContext, audit_one_narrative
from src.domain.narrative import Narrative
from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType

# ---------------------------------------------------------------------------
# Deterministic stub builders
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 3, 0, 0, 0, tzinfo=UTC)
_WORKSPACE_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "audit-test-workspace")
_ACCOUNT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "audit-test-account")
_NARRATIVE_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "audit-test-narrative")


def _make_narrative() -> Narrative:
    sig_ids = tuple(
        uuid.uuid5(uuid.NAMESPACE_DNS, f"audit-sig-{i}") for i in range(3)
    )
    return Narrative(
        id=_NARRATIVE_ID,
        workspace_id=_WORKSPACE_ID,
        account_id=_ACCOUNT_ID,
        narrative=(
            "Acme Corp has been consistently engaged over the past month. "
            "Primary champion Sarah Chen submitted 3 feature requests and "
            "attended the monthly sync. Sentiment is positive (score: 78)."
        ),
        engagement=72,
        engagement_rationale="3 inbound emails and 1 product event in the window.",
        sentiment=78,
        signal_window_start=datetime(2026, 4, 1, tzinfo=UTC),
        signal_window_end=datetime(2026, 5, 1, tzinfo=UTC),
        signals_considered=sig_ids,
        model="claude-sonnet-4-5",
        prompt_version="v1",
        generated_at=_NOW,
        superseded_at=None,
    )


def _make_signals() -> list[Signal]:
    signals = []
    for i in range(3):
        signals.append(
            Signal(
                id=uuid.uuid5(uuid.NAMESPACE_DNS, f"audit-sig-{i}"),
                workspace_id=_WORKSPACE_ID,
                account_id=_ACCOUNT_ID,
                source_type=SourceType.INBOUND_EMAIL,
                external_id=f"ext-{i}",
                thread_id=None,
                direction=Direction.INBOUND,
                channel=Channel.EMAIL,
                occurred_at=_NOW,
                created_at=_NOW,
                updated_at=_NOW,
                subject=f"Test subject {i}",
                body=f"Body text for signal {i}.",
                author_contact_id=None,
                recipient_contact_ids=[],
                routing_method=RoutingMethod.HEADER_DOMAIN,
                routing_confidence=0.95,
                routing_warning=None,
                deleted_at=None,
            )
        )
    return signals


def _make_audit_context(signals: list | None = None) -> AuditContext:
    """Build a minimal AuditContext for tests — all required fields populated."""
    return AuditContext(
        signals=signals if signals is not None else _make_signals(),
        contacts=[
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "audit-contact-0")),
                "email": "sarah.chen@acmecorp.com",
                "display_name": "Sarah Chen",
                "is_internal": False,
            }
        ],
        account_meta={
            "name": "Acme Corp",
            "vertical": "software",
            "status": "active",
            "primary_domain": "acmecorp.com",
            "additional_domains": [],
        },
        dimension_configs=[
            {"dimension_type": "engagement", "weight": 0.5},
            {"dimension_type": "sentiment", "weight": 0.5},
        ],
        product_usage_config={},
        workspace_slug="test-workspace",
    )


# ---------------------------------------------------------------------------
# Canned GPT-5 structured-output response (all-pass)
# ---------------------------------------------------------------------------

_MOCK_OPENAI_RESPONSE_DICT = {
    "faithfulness": {
        "score": 4,
        "passed": True,
        "reasoning": "All claims trace to provided signals.",
        "details": {"cited_signal_ids": ["audit-sig-0", "audit-sig-1"]},
    },
    "coverage": {
        "score": None,
        "passed": True,
        "reasoning": "Engagement and sentiment addressed.",
        "details": {"missing_dimensions": []},
    },
    "calibration": {
        "score": 5,
        "passed": True,
        "reasoning": "Positive language matches score of 78.",
        "details": {},
    },
    "hallucination": {
        "score": None,
        "passed": True,
        "reasoning": "No invented specifics.",
        "details": {"invented_items": []},
    },
    "tone_fit": {
        "score": None,
        "passed": True,
        "reasoning": "Register matches workspace voice config.",
        "details": {},
    },
}

# Canned response with one hard-gate failure (faithfulness score = 1)
_MOCK_OPENAI_RESPONSE_FAITHFULNESS_FAIL = {
    **_MOCK_OPENAI_RESPONSE_DICT,
    "faithfulness": {
        "score": 1,
        "passed": False,
        "reasoning": "Narrative substantially fabricated.",
        "details": {"cited_signal_ids": []},
    },
}

_AUDIT_RUN_ID = "ci_a1b2c3d4_1746316800"
_AUDIT_SOURCE = "ci"
_AUDITOR_MODEL = "gpt-5-mini-2025-08-07"


def _build_mock_openai_client(response_dict: dict) -> MagicMock:
    """Build a mock openai.OpenAI client whose structured-output call returns
    response_dict as the parsed content.  Assumption: the script calls
    client.chat.completions.create(..., response_format={...}) and reads
    .choices[0].message.parsed or .choices[0].message.content.
    Coder may need to adjust the attribute chain if the actual SDK call differs.
    """
    mock_choice = MagicMock()
    mock_choice.message.parsed = response_dict
    # Also provide .content as a JSON string fallback in case the script uses that path
    import json
    mock_choice.message.content = json.dumps(response_dict)

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 6400
    mock_usage.completion_tokens = 2000

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage

    mock_completions = MagicMock()
    mock_completions.create = MagicMock(return_value=mock_response)

    mock_chat = MagicMock()
    mock_chat.completions = mock_completions

    mock_client = MagicMock()
    mock_client.chat = mock_chat

    return mock_client


# ---------------------------------------------------------------------------
# Test: 5 criterion rows + 1 aggregate row written with consistent metadata
# ---------------------------------------------------------------------------


class TestAuditWritesFiveCriterionRowsPlusOneAggregate:
    """audit_one_narrative writes exactly 5 narrative_audits rows + 1 narrative_audit_runs
    row, all sharing the same audit_run_id and audit_source (ADR-016 D4 + D12).
    """

    def test_audit_writes_correct_row_counts_and_shared_metadata(self):
        # Arrange
        narrative = _make_narrative()
        signals = _make_signals()

        criterion_rows: list[dict] = []
        aggregate_rows: list[dict] = []

        def _capture_insert(table: str, row: dict):
            if table == "narrative_audits":
                criterion_rows.append(row)
            elif table == "narrative_audit_runs":
                aggregate_rows.append(row)
            return MagicMock()  # simulated Supabase response

        mock_openai_client = _build_mock_openai_client(_MOCK_OPENAI_RESPONSE_DICT)
        mock_supabase_client = MagicMock()
        mock_supabase_client.table.return_value.insert.return_value.execute.side_effect = (
            lambda: MagicMock()
        )

        # Patch at module level so audit_one_narrative picks up the mocks.
        # Assumption: the script calls a thin DB helper (e.g., _insert_row(table, row))
        # that we can intercept, OR uses supabase_client.table(name).insert(row).execute().
        # Using the latter here; coder must not inline the supabase client construction
        # in a way that prevents patching.
        with (
            patch(
                "scripts.audit_narratives.openai.OpenAI",
                return_value=mock_openai_client,
            ),
            patch(
                "scripts.audit_narratives._insert_audit_row",
                side_effect=_capture_insert,
            ),
        ):
            audit_one_narrative(
                narrative=narrative,
                context=_make_audit_context(signals),
                audit_run_id=_AUDIT_RUN_ID,
                audit_source=_AUDIT_SOURCE,
                auditor_model=_AUDITOR_MODEL,
                workspace_id=_WORKSPACE_ID,
                dry_run=False,
            )

        # Assert row counts
        assert len(criterion_rows) == 5, (
            f"Expected 5 criterion rows, got {len(criterion_rows)}"
        )
        assert len(aggregate_rows) == 1, (
            f"Expected 1 aggregate row, got {len(aggregate_rows)}"
        )

        # Assert shared audit_run_id across all 6 rows
        all_rows = criterion_rows + aggregate_rows
        run_ids = {row["audit_run_id"] for row in all_rows}
        assert run_ids == {_AUDIT_RUN_ID}, (
            f"Expected all rows to share audit_run_id={_AUDIT_RUN_ID!r}, got: {run_ids}"
        )

        # Assert shared audit_source across all 6 rows
        sources = {row["audit_source"] for row in all_rows}
        assert sources == {_AUDIT_SOURCE}, (
            f"Expected all rows to share audit_source={_AUDIT_SOURCE!r}, got: {sources}"
        )

        # Assert 5 criterion rows cover all 5 required criteria
        criterion_names = {row["criterion"] for row in criterion_rows}
        assert criterion_names == {
            "faithfulness", "coverage", "calibration", "hallucination", "tone_fit"
        }, f"Unexpected criterion names: {criterion_names}"

    def test_aggregate_cost_equals_sum_of_criterion_costs(self):
        """narrative_audit_runs.cost_usd must equal sum of the 5 criterion row cost_usd
        values (ADR-016 D12).  Within floating-point rounding tolerance.
        """
        narrative = _make_narrative()
        signals = _make_signals()

        criterion_rows: list[dict] = []
        aggregate_rows: list[dict] = []

        def _capture_insert(table: str, row: dict):
            if table == "narrative_audits":
                criterion_rows.append(row)
            elif table == "narrative_audit_runs":
                aggregate_rows.append(row)
            return MagicMock()

        mock_openai_client = _build_mock_openai_client(_MOCK_OPENAI_RESPONSE_DICT)

        with (
            patch(
                "scripts.audit_narratives.openai.OpenAI",
                return_value=mock_openai_client,
            ),
            patch(
                "scripts.audit_narratives._insert_audit_row",
                side_effect=_capture_insert,
            ),
        ):
            audit_one_narrative(
                narrative=narrative,
                context=_make_audit_context(signals),
                audit_run_id=_AUDIT_RUN_ID,
                audit_source=_AUDIT_SOURCE,
                auditor_model=_AUDITOR_MODEL,
                workspace_id=_WORKSPACE_ID,
                dry_run=False,
            )

        assert aggregate_rows, "No aggregate row written"
        criterion_cost_sum = sum(row["cost_usd"] for row in criterion_rows)
        aggregate_cost = aggregate_rows[0]["cost_usd"]

        assert aggregate_cost == pytest.approx(criterion_cost_sum, rel=1e-4), (
            f"Aggregate cost_usd {aggregate_cost} != sum of criterion cost_usd {criterion_cost_sum}"
        )

    def test_aggregate_overall_passed_reflects_hard_gate_results(self):
        """overall_passed in narrative_audit_runs must be False when any hard-gate
        criterion row has passed=False (ADR-016 D6 + D12).
        """
        narrative = _make_narrative()
        signals = _make_signals()

        aggregate_rows: list[dict] = []

        def _capture_insert(table: str, row: dict):
            if table == "narrative_audit_runs":
                aggregate_rows.append(row)
            return MagicMock()

        # Use a response with faithfulness hard-gate failure
        mock_openai_client = _build_mock_openai_client(
            _MOCK_OPENAI_RESPONSE_FAITHFULNESS_FAIL
        )

        with (
            patch(
                "scripts.audit_narratives.openai.OpenAI",
                return_value=mock_openai_client,
            ),
            patch(
                "scripts.audit_narratives._insert_audit_row",
                side_effect=_capture_insert,
            ),
        ):
            audit_one_narrative(
                narrative=narrative,
                context=_make_audit_context(signals),
                audit_run_id=_AUDIT_RUN_ID,
                audit_source=_AUDIT_SOURCE,
                auditor_model=_AUDITOR_MODEL,
                workspace_id=_WORKSPACE_ID,
                dry_run=False,
            )

        assert aggregate_rows, "No aggregate row written"
        agg = aggregate_rows[0]
        assert agg["overall_passed"] is False
        assert agg["hard_gate_failures"] == 1
        assert agg["advisory_failures"] == 0


# ---------------------------------------------------------------------------
# Test: atomicity — no partial writes when aggregate insert fails
# ---------------------------------------------------------------------------


class TestAuditAtomicityOnAggregateFailure:
    """If writing the aggregate row fails, no criterion rows should persist.
    ADR-016 D12: '5 criterion rows and the 1 aggregate row are submitted as a
    single [...] transaction [...]. If the aggregate write fails, the runner
    logs the error and continues.'

    Note: ADR-016 D12 allows two interpretations:
      (a) true DB transaction (rollback on failure) — ideal
      (b) cleanup on failure (delete the 5 rows if aggregate insert raises)

    This test enforces interpretation (a) or (b): either way, the caller must
    not observe 5 persisted criterion rows with no aggregate row after a failure.

    If the coder cannot achieve real DB-level atomicity via Supabase REST,
    they must implement compensating deletes and this test will enforce it.
    """

    def test_no_criterion_rows_persist_when_aggregate_insert_fails(self):
        # Arrange
        narrative = _make_narrative()
        signals = _make_signals()

        persisted_criterion_rows: list[dict] = []

        def _capture_insert(table: str, row: dict):
            if table == "narrative_audits":
                persisted_criterion_rows.append(row)
                return MagicMock()
            elif table == "narrative_audit_runs":
                # Simulate the aggregate insert failing
                raise RuntimeError("Supabase: insert into narrative_audit_runs failed")

        def _capture_delete(table: str, row_id):
            # Track any compensating deletes that roll back criterion rows
            persisted_criterion_rows.clear()

        mock_openai_client = _build_mock_openai_client(_MOCK_OPENAI_RESPONSE_DICT)

        with (
            patch(
                "scripts.audit_narratives.openai.OpenAI",
                return_value=mock_openai_client,
            ),
            patch(
                "scripts.audit_narratives._insert_audit_row",
                side_effect=_capture_insert,
            ),
            patch(
                "scripts.audit_narratives._delete_audit_rows_by_run_id",
                side_effect=lambda table, run_id, narrative_id: persisted_criterion_rows.clear(),
            ),
        ):
            # The function should not propagate the aggregate failure as an unhandled exception
            # (ADR-016 D12: 'the runner logs the error and continues')
            try:
                audit_one_narrative(
                    narrative=narrative,
                    context=_make_audit_context(signals),
                    audit_run_id=_AUDIT_RUN_ID,
                    audit_source=_AUDIT_SOURCE,
                    auditor_model=_AUDITOR_MODEL,
                    workspace_id=_WORKSPACE_ID,
                    dry_run=False,
                )
            except RuntimeError:
                pass  # acceptable if the script re-raises after cleanup

        # Assert: no partial write state — criterion rows must not outlive a failed aggregate
        assert len(persisted_criterion_rows) == 0, (
            f"Found {len(persisted_criterion_rows)} orphaned criterion rows after aggregate "
            f"insert failure.  The harness must clean up criterion rows atomically."
        )
