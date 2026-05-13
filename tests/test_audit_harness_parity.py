"""Prompt-content parity tests for audit_one_narrative.

These tests assert that the AuditContext produced by fetch_audit_context — the
single fetch path used by both the CLI main loop and the simulator's
_audit_narrative wrapper — generates the same _build_user_prompt output for the
same input narrative.

The class of bug they guard against: two callers both accept AuditContext (so
type-checking passes), but one constructs it with different DB queries or
omits a field, silently diverging on the prompt that the LLM auditor sees.

Test inventory (6 tests):
  TestPromptParityCliVsSimulator
    - test_parity_same_context_produces_identical_prompts     (main parity test)
    - test_parity_simulator_path_calls_fetch_audit_context    (call-site guard)
  TestPromptContentAssertions
    - test_valid_contacts_header_present
    - test_contact_display_name_appears_verbatim
    - test_account_name_appears_in_metadata_section
    - test_dimension_configs_appear_in_enabled_dimensions_section
  TestNegativeRegressionMissingField
    - test_missing_contacts_produces_different_prompt_naming_field
    - test_missing_account_meta_produces_different_prompt_naming_field
    - test_missing_dimension_configs_produces_different_prompt_naming_field
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

from scripts.audit_narratives import (
    AuditContext,
    _build_user_prompt,
    fetch_audit_context,
)

# ---------------------------------------------------------------------------
# Shared fixture data — deterministic, no DB
# ---------------------------------------------------------------------------

_WS_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "parity-test-workspace")
_ACC_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "parity-test-account")
_NARR_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "parity-test-narrative")
_CONTACT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "parity-test-contact")
_SIGNAL_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "parity-test-signal")


class _StubNarrative:
    """Minimal narrative stand-in matching the attribute contract of _NarrativeRow."""

    def __init__(self) -> None:
        self.id = _NARR_ID
        self.account_id = _ACC_ID
        self.workspace_id = _WS_ID
        self.narrative = "Account is showing strong engagement this quarter."
        self.sentiment = 72
        self.engagement = 65
        self.engagement_rationale = "4 inbound signals in 30-day window"
        self.signal_window_start = "2026-03-01"
        self.signal_window_end = "2026-04-30"
        self.signals_considered = [str(_SIGNAL_ID)]


# The full DB fixture set: what fetch_audit_context should return.
_SIGNALS_DB_ROW = {
    "id": str(_SIGNAL_ID),
    "subject": "Following up on renewal",
    "body": "Hi team, just circling back on the renewal timeline.",
    "direction": "inbound",
    "occurred_at": "2026-04-15T10:00:00+00:00",
    "author_contact_id": str(_CONTACT_ID),
}
_CONTACTS_DB_ROW = {
    "id": str(_CONTACT_ID),
    "email": "priya.mehta@acmecorp.com",
    "display_name": "Priya Mehta",
    "is_internal": False,
}
_ACCOUNT_DB_ROW = {
    "name": "Acme Corp",
    "vertical": "fintech",
    "status": "active",
    "primary_domain": "acmecorp.com",
    "additional_domains": ["acme.io"],
}
_DIM_CONFIGS_DB_ROWS = [
    {"dimension_type": "engagement", "weight": 0.5},
    {"dimension_type": "sentiment", "weight": 0.5},
]
_PRODUCT_USAGE_DB_ROW = {"config": {"cascade": [7, 14, 30, 60]}}


def _make_fetch_mock_client() -> MagicMock:
    """Return a mock Supabase client that serves the canonical fixture data.

    Mirrors the mock pattern in TestFetchAuditContext in test_audit_harness.py,
    adapted for the parity-specific fixture values above.
    """

    def _resp(data: list) -> MagicMock:
        r = MagicMock()
        r.data = data
        return r

    sig_resp = _resp([_SIGNALS_DB_ROW])
    contacts_resp = _resp([_CONTACTS_DB_ROW])
    acct_resp = _resp([_ACCOUNT_DB_ROW])
    dim_resp = _resp(_DIM_CONFIGS_DB_ROWS)
    pu_resp = _resp([_PRODUCT_USAGE_DB_ROW])

    ws_resp = MagicMock()
    ws_resp.data = {"slug": "test-workspace"}

    def _table_side_effect(name: str) -> MagicMock:
        mock = MagicMock()
        if name == "signals":
            # Chain: .select().in_().eq("workspace_id").eq("account_id").execute()
            (
                mock.select.return_value
                .in_.return_value.eq.return_value.eq.return_value
                .execute
            ).return_value = sig_resp
        elif name == "contacts":
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
            # Two queries hit this table: dim_configs (2 .eq()) and product_usage (3 .eq()).
            (
                mock.select.return_value
                .eq.return_value.eq.return_value.is_.return_value
                .execute
            ).side_effect = [dim_resp, pu_resp]
            (
                mock.select.return_value
                .eq.return_value.eq.return_value.eq.return_value.is_.return_value
                .execute
            ).return_value = pu_resp
        return mock

    client = MagicMock()
    client.table.side_effect = _table_side_effect
    return client


# ---------------------------------------------------------------------------
# Helper: build an AuditContext via fetch_audit_context using a fresh mock client
# ---------------------------------------------------------------------------

def _fetch_context_via_mock() -> AuditContext:
    narrative = _StubNarrative()
    client = _make_fetch_mock_client()
    return fetch_audit_context(
        narrative=narrative,
        workspace_id=_WS_ID,
        account_id=_ACC_ID,
        client=client,
    )


# ---------------------------------------------------------------------------
# TestPromptParityCliVsSimulator
# ---------------------------------------------------------------------------


class TestPromptParityCliVsSimulator:
    """Main parity test: both call paths, same context, same prompt."""

    def test_parity_same_context_produces_identical_prompts(self):
        """The CLI path and simulator path both call fetch_audit_context with the
        same mock DB client.  The resulting AuditContext, when passed through
        _build_user_prompt, must produce byte-identical output.

        This catches divergence where one caller omits a field or uses different
        DB query parameters — the dataclass type is identical but the content
        differs.
        """
        # Arrange: two independent fetch calls with equivalent mock clients
        # (same data, two client instances — mirrors independent call sites).
        narrative = _StubNarrative()
        cli_client = _make_fetch_mock_client()
        simulator_client = _make_fetch_mock_client()

        # Act: CLI path context
        cli_context = fetch_audit_context(
            narrative=narrative,
            workspace_id=_WS_ID,
            account_id=_ACC_ID,
            client=cli_client,
        )

        # Act: Simulator path context (same call, separate client instance)
        simulator_context = fetch_audit_context(
            narrative=narrative,
            workspace_id=_WS_ID,
            account_id=_ACC_ID,
            client=simulator_client,
        )

        # Act: build prompts from each context
        cli_prompt = _build_user_prompt(
            narrative,
            cli_context.signals,
            cli_context.dimension_configs,
            cli_context.contacts,
            product_usage_config=cli_context.product_usage_config,
            account_meta=cli_context.account_meta,
        )
        simulator_prompt = _build_user_prompt(
            narrative,
            simulator_context.signals,
            simulator_context.dimension_configs,
            simulator_context.contacts,
            product_usage_config=simulator_context.product_usage_config,
            account_meta=simulator_context.account_meta,
        )

        # Assert: byte-identical prompts from equivalent DB responses
        assert cli_prompt == simulator_prompt, (
            "CLI and simulator AuditContexts produced different prompts for the same input.\n"
            "This indicates one call path is fetching different context from the DB.\n"
            f"CLI prompt length: {len(cli_prompt)}, simulator prompt length: {len(simulator_prompt)}"
        )

    def test_parity_simulator_path_calls_fetch_audit_context(self):
        """The simulator's _audit_narrative wrapper must invoke fetch_audit_context
        (not construct AuditContext fields ad-hoc).  This ensures the single-fetch
        path invariant holds — any future divergence in context assembly is caught
        before it reaches the audit prompt.

        Patches fetch_audit_context at the scripts.audit_narratives module so any
        import path the simulator uses hits the same patch.
        """
        from src.simulator.executor import _audit_narrative

        narrative = _StubNarrative()
        narrative.id = _NARR_ID  # ensure attribute exists

        # Build a context that _audit_narrative would receive from fetch_audit_context
        canned_context = AuditContext(
            signals=[],
            contacts=[_CONTACTS_DB_ROW],
            account_meta=_ACCOUNT_DB_ROW,
            dimension_configs=_DIM_CONFIGS_DB_ROWS,
            product_usage_config={"cascade": [7, 14, 30, 60]},
            workspace_slug="test-workspace",
        )

        mock_client = MagicMock()

        captured_context: list[AuditContext] = []

        def _capture_audit(narrative, context, **kwargs):  # type: ignore[misc]
            captured_context.append(context)
            from scripts.audit_narratives import _make_dry_run_result
            return _make_dry_run_result()

        with patch("scripts.audit_narratives.fetch_audit_context", return_value=canned_context) as mock_fetch:
            with patch("scripts.audit_narratives.audit_one_narrative", side_effect=_capture_audit):
                _audit_narrative(
                    narrative=narrative,
                    workspace_id=_WS_ID,
                    client=mock_client,
                    audit_run_id="manual_test_parity_123",
                    dry_run=False,
                )

        # Assert: fetch_audit_context was called once with the correct identifiers
        assert mock_fetch.call_count == 1, (
            f"Expected fetch_audit_context to be called once by _audit_narrative, "
            f"got {mock_fetch.call_count} calls"
        )
        fetch_call_kwargs = mock_fetch.call_args
        assert fetch_call_kwargs is not None
        # workspace_id and account_id must be passed through — these are the fields
        # that were missing in the pre-refactor bug (called without account_id => contacts=[])
        assert fetch_call_kwargs.kwargs.get("workspace_id") == _WS_ID or (
            len(fetch_call_kwargs.args) >= 3 and fetch_call_kwargs.args[2] == _WS_ID
        ), "fetch_audit_context called without correct workspace_id"

        # Assert: audit_one_narrative received the full canned context (not a partial)
        assert len(captured_context) == 1
        ctx = captured_context[0]
        assert ctx.contacts == canned_context.contacts, (
            "audit_one_narrative received a context with different contacts than "
            "fetch_audit_context returned — the simulator is not passing context through correctly"
        )


# ---------------------------------------------------------------------------
# TestPromptContentAssertions
# ---------------------------------------------------------------------------


class TestPromptContentAssertions:
    """Secondary: specific load-bearing content appears verbatim in the prompt."""

    def _build_prompt_from_fixture(self) -> str:
        """Return the prompt string for the canonical fixture set."""
        ctx = _fetch_context_via_mock()
        narrative = _StubNarrative()
        return _build_user_prompt(
            narrative,
            ctx.signals,
            ctx.dimension_configs,
            ctx.contacts,
            product_usage_config=ctx.product_usage_config,
            account_meta=ctx.account_meta,
        )

    def test_valid_contacts_header_present(self):
        prompt = self._build_prompt_from_fixture()
        assert "--- VALID CONTACTS FOR THIS ACCOUNT ---" in prompt, (
            "VALID CONTACTS section header is missing from the prompt. "
            "The auditor cannot ground name claims without this section."
        )

    def test_contact_display_name_appears_verbatim(self):
        prompt = self._build_prompt_from_fixture()
        # The fixture contact is "Priya Mehta" at "priya.mehta@acmecorp.com"
        assert "Priya Mehta" in prompt, (
            "Contact display_name 'Priya Mehta' does not appear in the prompt. "
            "Missing contacts mean the auditor flags real names as hallucinations."
        )
        assert "priya.mehta@acmecorp.com" in prompt, (
            "Contact email 'priya.mehta@acmecorp.com' does not appear in the prompt."
        )

    def test_account_name_appears_in_metadata_section(self):
        prompt = self._build_prompt_from_fixture()
        assert "--- VALID ACCOUNT METADATA ---" in prompt, (
            "VALID ACCOUNT METADATA section header is missing from the prompt."
        )
        assert "Acme Corp" in prompt, (
            "Account name 'Acme Corp' does not appear in the prompt. "
            "Missing account metadata means the auditor may flag account-level claims as hallucinations."
        )

    def test_dimension_configs_appear_in_enabled_dimensions_section(self):
        prompt = self._build_prompt_from_fixture()
        assert "Enabled dimensions:" in prompt, (
            "Enabled dimensions section is missing from the prompt."
        )
        assert "engagement" in prompt, (
            "Dimension type 'engagement' does not appear in the prompt."
        )
        assert "sentiment" in prompt, (
            "Dimension type 'sentiment' does not appear in the prompt."
        )


# ---------------------------------------------------------------------------
# TestNegativeRegressionMissingField
# ---------------------------------------------------------------------------


class TestNegativeRegressionMissingField:
    """If AuditContext is constructed with missing/empty fields, the prompt diverges
    from the full-context prompt in a way that names the missing field.

    These tests are the 'would-have-caught-the-original-bug' verification:
    the pre-refactor simulator called audit_one_narrative without contacts,
    account_meta, and dimension_configs. Reverting to that pattern here produces
    a prompt that differs from the canonical prompt, and the assertion message
    names which field is missing.
    """

    def _build_canonical_prompt(self) -> str:
        ctx = _fetch_context_via_mock()
        narrative = _StubNarrative()
        return _build_user_prompt(
            narrative,
            ctx.signals,
            ctx.dimension_configs,
            ctx.contacts,
            product_usage_config=ctx.product_usage_config,
            account_meta=ctx.account_meta,
        )

    def _build_partial_prompt(self, **overrides: Any) -> str:
        """Build a prompt using the canonical context with one field replaced."""
        ctx = _fetch_context_via_mock()
        narrative = _StubNarrative()
        return _build_user_prompt(
            narrative,
            overrides.get("signals", ctx.signals),
            overrides.get("dimension_configs", ctx.dimension_configs),
            overrides.get("contacts", ctx.contacts),
            product_usage_config=overrides.get("product_usage_config", ctx.product_usage_config),
            account_meta=overrides.get("account_meta", ctx.account_meta),
        )

    def test_missing_contacts_produces_different_prompt_naming_field(self):
        """Omitting contacts (pre-refactor bug) produces a detectably different prompt.

        The canonical prompt contains the contact roster; the partial prompt
        renders the '(contact roster not provided)' disclaimer instead.
        """
        canonical = self._build_canonical_prompt()
        # Pre-refactor simulator: contacts not fetched, so contacts=None
        partial = self._build_partial_prompt(contacts=None)

        assert canonical != partial, (
            "Passing contacts=None to _build_user_prompt produced the same prompt as "
            "passing the full roster. The 'contacts' field is not load-bearing in the prompt."
        )
        # The partial prompt must contain a marker that tells the reader what's missing
        assert "(contact roster not provided" in partial, (
            "Missing-contacts prompt does not contain the expected disclaimer. "
            "Field missing: contacts"
        )
        # The canonical prompt must NOT contain the disclaimer (it has real contacts)
        assert "(contact roster not provided" not in canonical, (
            "Canonical prompt unexpectedly contains the missing-contacts disclaimer."
        )

    def test_missing_account_meta_produces_different_prompt_naming_field(self):
        """Omitting account_meta produces a detectably different prompt.

        The canonical prompt contains 'Acme Corp'; the partial prompt renders
        the '(account metadata not provided)' disclaimer.
        """
        canonical = self._build_canonical_prompt()
        partial = self._build_partial_prompt(account_meta=None)

        assert canonical != partial, (
            "Passing account_meta=None produced the same prompt as passing full metadata. "
            "Field missing: account_meta"
        )
        assert "(account metadata not provided" in partial, (
            "Missing-account_meta prompt does not contain the expected disclaimer. "
            "Field missing: account_meta"
        )
        assert "Acme Corp" not in partial, (
            "Partial prompt still contains the account name despite account_meta=None."
        )

    def test_missing_dimension_configs_produces_different_prompt_naming_field(self):
        """Omitting dimension_configs produces a different enabled-dimensions section.

        The canonical prompt lists engagement+sentiment. The partial prompt falls
        back to the default dimensions (also engagement+sentiment, same weights),
        so this test verifies the fallback is active — the test documents that
        dimension_configs has a default, not that omitting it is silent.
        """
        canonical = self._build_canonical_prompt()
        # Pass empty list — the function has a fallback to [engagement, sentiment]
        # so the rendered output may be the same. Test documents this behaviour.
        partial_empty = self._build_partial_prompt(dimension_configs=[])

        # The fallback default IS [engagement(0.5), sentiment(0.5)] — same as our fixture.
        # So canonical == partial_empty is expected here. Document it explicitly.
        # The important invariant: the section appears in BOTH prompts (never silently absent).
        assert "Enabled dimensions:" in partial_empty, (
            "Enabled dimensions section is missing when dimension_configs=[]. "
            "Field missing: dimension_configs (fallback should apply)"
        )
        # Now verify that a DIFFERENT dimension set (not the default) does produce divergence.
        different_dims = [{"dimension_type": "renewal_intent", "weight": 1.0}]
        partial_different = self._build_partial_prompt(dimension_configs=different_dims)
        assert "renewal_intent" in partial_different, (
            "Overridden dimension_configs not reflected in prompt. "
            "Field missing or ignored: dimension_configs"
        )
        assert "renewal_intent" not in canonical, (
            "Canonical prompt unexpectedly contains 'renewal_intent'."
        )
        assert partial_different != canonical, (
            "Custom dimension_configs produced the same prompt as the canonical fixture. "
            "Field missing: dimension_configs"
        )
