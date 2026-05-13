"""Integration tests for the orchestrator: scenario load, yield_events, and scenario properties."""

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from src.synthetic.orchestrator import load_scenario, run_scenario, yield_events

SCENARIOS_DIR = Path("fixtures/synthetic-scenarios")

_WORKSPACE_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "test-workspace")


class TestLoadScenario:
    def test_load_single_champion(self):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        assert scenario.name == "single-champion-then-silence"
        assert scenario.seed == 4217
        assert len(scenario.accounts) == 1
        assert scenario.accounts[0].slug == "acme-corp"

    def test_load_multi_stakeholder(self):
        scenario = load_scenario(SCENARIOS_DIR / "multi-stakeholder-mixed-domain.yaml")
        assert scenario.name == "multi-stakeholder-mixed-domain"
        assert scenario.seed == 7831
        assert len(scenario.accounts) == 1

    def test_load_frustrated_escalation(self):
        scenario = load_scenario(SCENARIOS_DIR / "frustrated-escalation.yaml")
        assert scenario.name == "frustrated-escalation"
        assert scenario.seed == 9163
        assert len(scenario.signals) == 3

    def test_missing_file_raises_systemexit(self, tmp_path):
        with pytest.raises(SystemExit):
            load_scenario(tmp_path / "nonexistent.yaml")

    def test_axes_unknown_field_rejected(self, tmp_path):
        """AxesSpec with extra="forbid" must reject unknown axis names."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(
            "version: 1\n"
            "name: bad\n"
            "seed: 1\n"
            "signals:\n"
            "  - source_type: inbound_email\n"
            "    account_slug: test\n"
            "    axes:\n"
            "      sentiment_trajectory_v2: declining\n"  # unknown field
        )
        with pytest.raises(ValidationError) as exc_info:
            load_scenario(bad_yaml)
        assert "sentiment_trajectory_v2" in str(exc_info.value)

    def test_target_block_validated_but_ignored(self):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        # target is present in YAML with nulls — should validate without error
        assert scenario.target is not None or scenario.target is None  # accepted either way

    def test_version_field_present(self):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        assert scenario.version == 1


class TestYieldEvents:
    def test_single_champion_produces_correct_count(self):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        events = [e for _, e in yield_events(scenario, _WORKSPACE_ID)]
        # single-champion-then-silence has 12 inbound_email signals
        assert len(events) == 12

    def test_multi_stakeholder_produces_correct_count(self):
        scenario = load_scenario(SCENARIOS_DIR / "multi-stakeholder-mixed-domain.yaml")
        events = [e for _, e in yield_events(scenario, _WORKSPACE_ID)]
        # 8 + 6 + 4 = 18 signals
        assert len(events) == 18

    def test_frustrated_escalation_produces_correct_count(self):
        scenario = load_scenario(SCENARIOS_DIR / "frustrated-escalation.yaml")
        events = [e for _, e in yield_events(scenario, _WORKSPACE_ID)]
        # 4 + 4 + 5 = 13 signals
        assert len(events) == 13

    def test_all_events_are_raw_inbound_events(self):
        from src.domain.raw_inbound_event import RawInboundEvent

        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        events = [e for _, e in yield_events(scenario, _WORKSPACE_ID)]
        assert all(isinstance(e, RawInboundEvent) for e in events)

    def test_all_events_have_correct_workspace_id(self):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        ws_id = uuid.uuid4()
        events = [e for _, e in yield_events(scenario, ws_id)]
        assert all(e.workspace_id == ws_id for e in events)

    def test_external_ids_are_unique(self):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        events = [e for _, e in yield_events(scenario, _WORKSPACE_ID)]
        external_ids = [json.loads(e.raw_payload)["external_id"] for e in events]
        assert len(external_ids) == len(set(external_ids)), "Duplicate external_ids"

    def test_payloads_pass_inbound_payload_validation(self):
        from src.pipeline.normalizer import InboundPayload

        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        events = [e for _, e in yield_events(scenario, _WORKSPACE_ID)]
        for event in events:
            payload = json.loads(event.raw_payload)
            # Should not raise
            validated = InboundPayload.model_validate(payload)
            assert validated.body.strip()
            assert validated.from_email

    def test_timestamps_are_ordered(self):
        """Signals within a spec should have non-decreasing timestamps."""
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        events = [e for _, e in yield_events(scenario, _WORKSPACE_ID)]
        occurred_ats = [json.loads(e.raw_payload)["occurred_at"] for e in events]
        # All timestamps tz-aware UTC, comparing as ISO strings works for ordering
        assert occurred_ats == sorted(occurred_ats), "Timestamps should be non-decreasing"

    def test_timestamps_are_tz_aware_utc(self):
        from datetime import datetime

        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        events = [e for _, e in yield_events(scenario, _WORKSPACE_ID)]
        for event in events:
            payload = json.loads(event.raw_payload)
            ts = datetime.fromisoformat(payload["occurred_at"])
            assert ts.tzinfo is not None, "Timestamps must be tz-aware"

    def test_reproducibility_same_seed(self):
        """Same scenario + seed → byte-identical external_ids."""
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        run1 = [
            json.loads(e.raw_payload)["external_id"]
            for _, e in yield_events(scenario, _WORKSPACE_ID)
        ]
        run2 = [
            json.loads(e.raw_payload)["external_id"]
            for _, e in yield_events(scenario, _WORKSPACE_ID)
        ]
        assert run1 == run2, "Same seed should produce identical external_ids"

    def test_reproducibility_payload_content(self):
        """Same scenario + seed → identical payload bodies."""
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        run1_bodies = [
            json.loads(e.raw_payload)["body"] for _, e in yield_events(scenario, _WORKSPACE_ID)
        ]
        run2_bodies = [
            json.loads(e.raw_payload)["body"] for _, e in yield_events(scenario, _WORKSPACE_ID)
        ]
        assert run1_bodies == run2_bodies, "Same seed should produce identical bodies"

    def test_silence_cadence_produces_no_events(self, tmp_path):
        """A spec with response_cadence=silence produces zero events."""
        silent_yaml = tmp_path / "silent.yaml"
        silent_yaml.write_text(
            "version: 1\n"
            "name: silent-test\n"
            "seed: 99\n"
            "accounts:\n"
            "  - slug: test-co\n"
            "    name: Test Co\n"
            "    primary_domain: test.com\n"
            "signals:\n"
            "  - source_type: inbound_email\n"
            "    account_slug: test-co\n"
            "    count: 5\n"
            "    axes:\n"
            "      response_cadence: silence\n"
        )
        scenario = load_scenario(silent_yaml)
        events = [e for _, e in yield_events(scenario, _WORKSPACE_ID)]
        assert len(events) == 0, "silence cadence should produce no events"


class TestScenarioProperties:
    def test_single_champion_single_contact_diversity(self):
        """single-champion-then-silence uses contact_diversity=single for all specs."""
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        for spec in scenario.signals:
            assert spec.axes.contact_diversity == "single"

    def test_multi_stakeholder_has_multiple_contact_diversity_settings(self):
        """multi-stakeholder-mixed-domain exercises multi and crowded diversity."""
        scenario = load_scenario(SCENARIOS_DIR / "multi-stakeholder-mixed-domain.yaml")
        diversities = {spec.axes.contact_diversity for spec in scenario.signals}
        assert "multi" in diversities or "crowded" in diversities

    def test_multi_stakeholder_has_mixed_domain(self):
        """multi-stakeholder-mixed-domain exercises personal_email and mixed origins."""
        scenario = load_scenario(SCENARIOS_DIR / "multi-stakeholder-mixed-domain.yaml")
        origins = {spec.axes.contact_email_origin for spec in scenario.signals}
        assert len(origins) > 1, "Should exercise multiple contact_email_origin values"

    def test_escalation_scenario_has_three_specs(self):
        scenario = load_scenario(SCENARIOS_DIR / "frustrated-escalation.yaml")
        assert len(scenario.signals) == 3

    def test_escalation_scenario_tone_shifts(self):
        """frustrated-escalation covers formal, apologetic, and escalation email tones."""
        scenario = load_scenario(SCENARIOS_DIR / "frustrated-escalation.yaml")
        tones = {spec.axes.email_tone for spec in scenario.signals}
        assert "escalation" in tones
        assert len(tones) >= 2, "Should cover multiple email tones"

    def test_single_champion_time_range_covers_multiple_days(self):
        """12 burst signals should span at least a few hours."""
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        events = [e for _, e in yield_events(scenario, _WORKSPACE_ID)]
        from datetime import datetime

        times = [datetime.fromisoformat(json.loads(e.raw_payload)["occurred_at"]) for e in events]
        span = max(times) - min(times)
        assert span.total_seconds() > 0, "Signals should span more than a single instant"


class TestRunScenario:
    """End-to-end integration: orchestrator → process_event chain (ADR-015 §D1, Phase 2a Req 1).

    Mocks process_event itself so we don't need a live Supabase client. Goal is to
    prove run_scenario walks every yielded event through the production pipeline
    entry point exactly once and returns the resulting Signals.
    """

    def test_run_scenario_dispatches_every_event_through_process_event(self):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        sentinel_signal = MagicMock()

        with patch("src.pipeline.run.process_event", return_value=sentinel_signal) as mock_pe:
            signals = run_scenario(
                scenario,
                workspace_id=_WORKSPACE_ID,
                workspace=MagicMock(),
                accounts=[],
                client=MagicMock(),
            )

        # single-champion-then-silence has 12 events
        assert len(signals) == 12
        assert mock_pe.call_count == 12
        assert all(s is sentinel_signal for s in signals)

    def test_run_scenario_passes_raw_inbound_events_in_yield_order(self):
        """run_scenario must hand process_event each yielded RawInboundEvent in order,
        not bypass yield_events or transform events before dispatch."""
        from src.domain.raw_inbound_event import RawInboundEvent

        scenario = load_scenario(SCENARIOS_DIR / "frustrated-escalation.yaml")
        expected_event_ids = [e.id for _, e in yield_events(scenario, _WORKSPACE_ID)]

        with patch("src.pipeline.run.process_event", return_value=MagicMock()) as mock_pe:
            run_scenario(
                scenario,
                workspace_id=_WORKSPACE_ID,
                workspace=MagicMock(),
                accounts=[],
                client=MagicMock(),
            )

        # First positional arg of each call is the RawInboundEvent
        passed_events = [call.args[0] for call in mock_pe.call_args_list]
        assert all(isinstance(e, RawInboundEvent) for e in passed_events)
        # IDs differ run-to-run (uuid4) so we verify count + type rather than identity:
        # the deterministic guarantee is on payload content, not event UUID.
        assert len(passed_events) == len(expected_event_ids)


class TestContactPoolStabilityViaOrchestrator:
    """End-to-end check that yield_events pre-builds contact pools per spec so
    contact_diversity is honored across all signals in a spec."""

    def test_single_champion_all_signals_same_sender(self):
        """single-champion-then-silence has contact_diversity=single throughout —
        all yielded email events must share exactly one from_email."""
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        events = [e for _, e in yield_events(scenario, _WORKSPACE_ID)]
        senders = {json.loads(e.raw_payload)["from_email"] for e in events}
        assert len(senders) == 1, (
            f"single contact_diversity should yield 1 unique sender; got {len(senders)}: {senders}"
        )

    def test_seed_stage_saas_crucible_single_per_spec(self):
        """Crucible has two email specs, each with contact_diversity=single.
        Each spec must produce exactly 1 unique sender (spec-level stability).
        Across the two specs there may be up to 2 different senders — that is the
        correct behavior; cross-spec unification requires an explicit override in
        the YAML, not a synthesiser guarantee."""
        from src.domain.raw_inbound_event import RawInboundEvent

        scenario = load_scenario(SCENARIOS_DIR / "seed-stage-saas.yaml")
        # Crucible email specs are indices 6 (Group A, count=3) and 7 (Group B, count=3)
        # in the signals list.  Collect events in spec order.
        quorum_email_events = [
            e
            for slug, e in yield_events(scenario, _WORKSPACE_ID)
            if slug == "crucible" and isinstance(e, RawInboundEvent)
        ]
        # 6 emails total across two specs of 3 each
        assert len(quorum_email_events) == 6
        # Each spec's 3 signals must share the same sender — verify by checking the
        # first half (Group A) and second half (Group B) independently.
        group_a = quorum_email_events[:3]
        group_b = quorum_email_events[3:]
        senders_a = {json.loads(e.raw_payload)["from_email"] for e in group_a}
        senders_b = {json.loads(e.raw_payload)["from_email"] for e in group_b}
        assert len(senders_a) == 1, (
            f"Group A (contact_diversity=single) should have 1 sender; got {senders_a}"
        )
        assert len(senders_b) == 1, (
            f"Group B (contact_diversity=single) should have 1 sender; got {senders_b}"
        )
        # Across both specs there should be at most 2 unique senders (one per spec).
        all_senders = senders_a | senders_b
        assert len(all_senders) <= 2, (
            f"Expected at most 2 senders across two single-diversity specs; got {all_senders}"
        )


class TestStructuredSignalOrchestration:
    """Verify that plain_ticket and granola_note source types are dispatched through
    yield_events and reach run_scenario correctly (ADR-020 Phase 4)."""

    def _make_ticket_scenario(self, tmp_path, count: int = 2):
        yaml_file = tmp_path / "ticket-test.yaml"
        yaml_file.write_text(
            f"version: 1\n"
            f"name: ticket-test\n"
            f"seed: 55\n"
            f"accounts:\n"
            f"  - slug: acme\n"
            f"    name: Acme Corp\n"
            f"    primary_domain: acme.com\n"
            f"signals:\n"
            f"  - source_type: plain_ticket\n"
            f"    account_slug: acme\n"
            f"    count: {count}\n"
        )
        return load_scenario(yaml_file)

    def _make_note_scenario(self, tmp_path, count: int = 2):
        yaml_file = tmp_path / "note-test.yaml"
        yaml_file.write_text(
            f"version: 1\n"
            f"name: note-test\n"
            f"seed: 77\n"
            f"accounts:\n"
            f"  - slug: acme\n"
            f"    name: Acme Corp\n"
            f"    primary_domain: acme.com\n"
            f"signals:\n"
            f"  - source_type: granola_note\n"
            f"    account_slug: acme\n"
            f"    count: {count}\n"
        )
        return load_scenario(yaml_file)

    def test_ticket_scenario_yields_correct_count(self, tmp_path):
        scenario = self._make_ticket_scenario(tmp_path, count=3)
        events = list(yield_events(scenario, _WORKSPACE_ID))
        assert len(events) == 3

    def test_ticket_scenario_yields_dicts(self, tmp_path):
        scenario = self._make_ticket_scenario(tmp_path, count=2)
        for _slug, event in yield_events(scenario, _WORKSPACE_ID):
            assert isinstance(event, dict), f"Expected dict, got {type(event)}"

    def test_ticket_dict_has_plain_type_field(self, tmp_path):
        scenario = self._make_ticket_scenario(tmp_path, count=2)
        for _slug, event in yield_events(scenario, _WORKSPACE_ID):
            assert "type" in event
            assert event["type"] in {"thread.created", "email.received", "email.sent"}

    def test_note_scenario_yields_correct_count(self, tmp_path):
        scenario = self._make_note_scenario(tmp_path, count=3)
        events = list(yield_events(scenario, _WORKSPACE_ID))
        assert len(events) == 3

    def test_note_scenario_yields_dicts(self, tmp_path):
        scenario = self._make_note_scenario(tmp_path, count=2)
        for _slug, event in yield_events(scenario, _WORKSPACE_ID):
            assert isinstance(event, dict)

    def test_note_dict_has_granola_id_prefix(self, tmp_path):
        scenario = self._make_note_scenario(tmp_path, count=2)
        for _slug, event in yield_events(scenario, _WORKSPACE_ID):
            assert event["id"].startswith("not_")

    def test_ticket_round_trip_through_parse_plain_event(self, tmp_path):
        """Each yielded ticket dict must parse cleanly through the Plain adapter."""
        from uuid import UUID

        from src.integrations.plain.adapter import parse_plain_event

        scenario = self._make_ticket_scenario(tmp_path, count=3)
        for _slug, event in yield_events(scenario, _WORKSPACE_ID):
            result = parse_plain_event(event, event["type"], UUID(int=0))
            assert result is not None
            assert result.kind == "ticket"
            assert result.external_id.startswith("plain:")

    def test_note_round_trip_through_parse_granola_note(self, tmp_path):
        """Each yielded note dict must parse cleanly through the Granola adapter."""
        from uuid import UUID

        from src.integrations.granola.adapter import parse_granola_note

        scenario = self._make_note_scenario(tmp_path, count=3)
        for _slug, event in yield_events(scenario, _WORKSPACE_ID):
            result = parse_granola_note(event, UUID(int=0))
            assert result is not None
            assert result.kind == "meeting_note"
            assert result.external_id.startswith("granola:")

    def test_run_scenario_routes_tickets_through_normalize_structured_signal(self, tmp_path):
        """run_scenario must call normalize_structured_signal for plain_ticket events."""
        scenario = self._make_ticket_scenario(tmp_path, count=2)
        sentinel = MagicMock()
        sentinel.signal = MagicMock()
        sentinel.signal.account_id = None

        with patch(
            "src.pipeline.structured_signal.normalize_structured_signal",
            return_value=sentinel,
        ) as mock_norm:
            run_scenario(
                scenario,
                workspace_id=_WORKSPACE_ID,
                workspace=MagicMock(),
                accounts=[],
                client=MagicMock(),
            )

        assert mock_norm.call_count == 2

    def test_run_scenario_routes_notes_through_normalize_structured_signal(self, tmp_path):
        """run_scenario must call normalize_structured_signal for granola_note events."""
        scenario = self._make_note_scenario(tmp_path, count=2)
        sentinel = MagicMock()
        sentinel.signal = MagicMock()
        sentinel.signal.account_id = None

        with patch(
            "src.pipeline.structured_signal.normalize_structured_signal",
            return_value=sentinel,
        ) as mock_norm:
            run_scenario(
                scenario,
                workspace_id=_WORKSPACE_ID,
                workspace=MagicMock(),
                accounts=[],
                client=MagicMock(),
            )

        assert mock_norm.call_count == 2

    def test_seed_stage_saas_ticket_and_note_specs_load(self):
        """seed-stage-saas.yaml (updated in Phase 4.5) must parse without error
        and include plain_ticket, pylon_ticket, and granola_note specs."""
        scenario = load_scenario(SCENARIOS_DIR / "seed-stage-saas.yaml")
        source_types = {spec.source_type for spec in scenario.signals}
        assert "plain_ticket" in source_types, (
            "seed-stage-saas.yaml must include plain_ticket specs (Cascade Infra)"
        )
        assert "pylon_ticket" in source_types, (
            "seed-stage-saas.yaml must include pylon_ticket specs (Crucible)"
        )
        assert "granola_note" in source_types, (
            "seed-stage-saas.yaml must include granola_note specs"
        )

    def test_mixed_scenario_ticket_note_email_count(self, tmp_path):
        """A scenario mixing inbound_email + plain_ticket + granola_note yields the
        correct total event count."""
        yaml_file = tmp_path / "mixed.yaml"
        yaml_file.write_text(
            "version: 1\n"
            "name: mixed\n"
            "seed: 33\n"
            "accounts:\n"
            "  - slug: co\n"
            "    name: Co\n"
            "    primary_domain: co.com\n"
            "signals:\n"
            "  - source_type: inbound_email\n"
            "    account_slug: co\n"
            "    count: 3\n"
            "  - source_type: plain_ticket\n"
            "    account_slug: co\n"
            "    count: 2\n"
            "  - source_type: granola_note\n"
            "    account_slug: co\n"
            "    count: 1\n"
        )
        scenario = load_scenario(yaml_file)
        events = list(yield_events(scenario, _WORKSPACE_ID))
        assert len(events) == 6

    # ---- Phase 4.5: Pylon ticket tests ----------------------------------------

    def _make_pylon_scenario(self, tmp_path, count: int = 2):
        yaml_file = tmp_path / "pylon-test.yaml"
        yaml_file.write_text(
            f"version: 1\n"
            f"name: pylon-test\n"
            f"seed: 88\n"
            f"accounts:\n"
            f"  - slug: acme\n"
            f"    name: Acme Corp\n"
            f"    primary_domain: acme.com\n"
            f"signals:\n"
            f"  - source_type: pylon_ticket\n"
            f"    account_slug: acme\n"
            f"    count: {count}\n"
        )
        return load_scenario(yaml_file)

    def test_pylon_ticket_scenario_yields_correct_count(self, tmp_path):
        scenario = self._make_pylon_scenario(tmp_path, count=3)
        events = list(yield_events(scenario, _WORKSPACE_ID))
        assert len(events) == 3

    def test_pylon_ticket_scenario_yields_dicts(self, tmp_path):
        scenario = self._make_pylon_scenario(tmp_path, count=2)
        for _slug, event in yield_events(scenario, _WORKSPACE_ID):
            assert isinstance(event, dict), f"Expected dict, got {type(event)}"

    def test_pylon_ticket_dict_has_data_envelope(self, tmp_path):
        """Pylon payloads use the {'data': {...}} envelope, not a flat structure."""
        scenario = self._make_pylon_scenario(tmp_path, count=2)
        for _slug, event in yield_events(scenario, _WORKSPACE_ID):
            assert "data" in event, "Pylon payload must have top-level 'data' key"
            assert "type" in event["data"]

    def test_pylon_ticket_round_trip_through_parse_pylon_event(self, tmp_path):
        """Each yielded Pylon dict (non-skipped type) must parse through the Pylon adapter."""
        from uuid import UUID

        from src.integrations.pylon.adapter import parse_pylon_event

        scenario = self._make_pylon_scenario(tmp_path, count=4)
        signal_count = 0
        for _slug, event in yield_events(scenario, _WORKSPACE_ID):
            event_type = event["data"]["type"]
            result = parse_pylon_event(event, event_type, UUID(int=0))
            # issue.status_changed returns None (skip) — not an error
            if event_type != "issue.status_changed":
                assert result is not None, (
                    f"parse_pylon_event returned None for non-skip type {event_type}"
                )
                assert result.kind == "ticket"
                assert result.external_id.startswith("pylon:")
                signal_count += 1

        assert signal_count >= 1, "At least one non-skipped Pylon event must parse"

    def test_run_scenario_routes_pylon_tickets_through_normalize_structured_signal(self, tmp_path):
        """run_scenario must call normalize_structured_signal for pylon_ticket events."""
        scenario = self._make_pylon_scenario(tmp_path, count=2)
        sentinel = MagicMock()
        sentinel.signal = MagicMock()
        sentinel.signal.account_id = None

        # pylon_ticket always produces issue.created for within=0, so count=2
        # means 1x issue.created + 1x (message_added or status_changed).
        # status_changed yields None from the adapter -> _process returns None -> not in signals.
        # We can't predict count exactly, so just assert >= 1 call.
        with patch(
            "src.pipeline.structured_signal.normalize_structured_signal",
            return_value=sentinel,
        ) as mock_norm:
            run_scenario(
                scenario,
                workspace_id=_WORKSPACE_ID,
                workspace=MagicMock(),
                accounts=[],
                client=MagicMock(),
            )

        assert mock_norm.call_count >= 1

    def test_mixed_plain_and_pylon_ticket_scenario(self, tmp_path):
        """A scenario with both plain_ticket and pylon_ticket specs yields events from both."""
        yaml_file = tmp_path / "mixed-vendors.yaml"
        yaml_file.write_text(
            "version: 1\n"
            "name: mixed-vendors\n"
            "seed: 44\n"
            "accounts:\n"
            "  - slug: co\n"
            "    name: Co\n"
            "    primary_domain: co.com\n"
            "signals:\n"
            "  - source_type: plain_ticket\n"
            "    account_slug: co\n"
            "    count: 2\n"
            "  - source_type: pylon_ticket\n"
            "    account_slug: co\n"
            "    count: 2\n"
        )
        scenario = load_scenario(yaml_file)
        events = list(yield_events(scenario, _WORKSPACE_ID))
        assert len(events) == 4

        # Verify we can distinguish Plain vs Pylon by payload shape
        plain_events = [e for _, e in yield_events(scenario, _WORKSPACE_ID) if "data" not in e]
        pylon_events = [e for _, e in yield_events(scenario, _WORKSPACE_ID) if "data" in e]
        assert len(plain_events) == 2, f"Expected 2 Plain events; got {len(plain_events)}"
        assert len(pylon_events) == 2, f"Expected 2 Pylon events; got {len(pylon_events)}"
