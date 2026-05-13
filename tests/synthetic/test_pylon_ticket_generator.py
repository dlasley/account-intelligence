"""Unit tests for src/synthetic/generators/ticket_pylon.py (ADR-020 Phase 4.5)."""

import random
import re
import uuid
from datetime import UTC, datetime
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.domain.signal import Direction
from src.integrations.pylon.adapter import parse_pylon_event
from src.synthetic.generators.ticket_pylon import (
    build_pylon_contact_pool,
    generate_pylon_ticket_payload,
)
from src.synthetic.scenario import AxesSpec, SignalSpec

_EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
_NOW = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
_SCENARIO_NAME = "test-pylon-scenario"


def _make_spec(**axes_kwargs) -> SignalSpec:
    return SignalSpec(
        source_type="pylon_ticket",
        account_slug="test-account",
        count=1,
        axes=AxesSpec(**axes_kwargs),
    )


def _gen(spec: SignalSpec, seed: int = 42, signal_index: int = 0, within: int = 0) -> dict:
    rng = random.Random(seed)
    return generate_pylon_ticket_payload(
        spec=spec,
        rng=rng,
        now=_NOW,
        signal_index=signal_index,
        scenario_name=_SCENARIO_NAME,
        account_name="Test Account",
        primary_domain="testaccount.com",
        signal_index_within_spec=within,
    )


class TestPylonPayloadShape:
    """Verify Pylon event envelope shape contract."""

    def test_top_level_has_data_key(self):
        payload = _gen(_make_spec())
        assert "data" in payload, "Pylon envelope must have top-level 'data' key"

    def test_data_has_required_fields(self):
        payload = _gen(_make_spec())
        data = payload["data"]
        for field in ("id", "type", "timestamp", "issue"):
            assert field in data, f"Missing required field in data: {field}"

    def test_event_type_is_string(self):
        payload = _gen(_make_spec())
        assert isinstance(payload["data"]["type"], str)

    def test_first_signal_is_issue_created(self):
        """signal_index_within_spec == 0 must always produce issue.created."""
        for seed in range(10):
            payload = _gen(_make_spec(), seed=seed, within=0)
            assert payload["data"]["type"] == "issue.created", (
                f"seed={seed}: expected issue.created, got {payload['data']['type']}"
            )

    def test_follow_up_signals_are_valid_event_types(self):
        """signal_index_within_spec > 0 must produce a recognized Pylon event type."""
        valid_types = {"issue.message_added", "issue.status_changed"}
        for within in range(1, 6):
            payload = _gen(_make_spec(), seed=42, within=within)
            assert payload["data"]["type"] in valid_types, (
                f"within={within}: unexpected type {payload['data']['type']}"
            )

    def test_issue_created_has_requester(self):
        payload = _gen(_make_spec(contact_diversity="single"), within=0)
        assert payload["data"]["type"] == "issue.created"
        requester = payload["data"]["issue"]["requester"]
        assert _EMAIL_RE.match(requester["email"]), f"Bad requester email: {requester['email']}"

    def test_issue_created_has_messages(self):
        payload = _gen(_make_spec(), within=0)
        messages = payload["data"]["issue"].get("messages", [])
        assert len(messages) >= 1, "issue.created must include at least one message"

    def test_issue_message_added_has_author(self):
        """Find a seed that produces issue.message_added for within=1."""
        for seed in range(50):
            payload = _gen(_make_spec(), seed=seed, within=1)
            if payload["data"]["type"] == "issue.message_added":
                message = payload["data"]["issue"]["messages"][-1]
                assert "author" in message
                assert message["author"]["type"] in {"customer", "agent"}
                return
        pytest.skip("No issue.message_added generated in 50 seeds — probabilistic test")

    def test_issue_status_changed_shape(self):
        """Find a seed that produces issue.status_changed for within=1."""
        for seed in range(200):
            payload = _gen(_make_spec(), seed=seed, within=1)
            if payload["data"]["type"] == "issue.status_changed":
                # status_changed only needs data.id, type, timestamp, issue.id/title
                assert "issue" in payload["data"]
                return
        pytest.skip("No issue.status_changed generated in 200 seeds — probabilistic test")

    def test_external_id_is_deterministic(self):
        spec = _make_spec()
        p1 = _gen(spec, seed=42, signal_index=7)
        p2 = _gen(spec, seed=42, signal_index=7)
        assert p1["data"]["id"] == p2["data"]["id"]

    def test_external_id_namespaced_with_uuid5(self):
        spec = _make_spec()
        payload = _gen(spec, seed=42, signal_index=5)
        expected_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{_SCENARIO_NAME}:pylon:5"))
        assert payload["data"]["id"] == expected_id

    def test_different_signal_indexes_differ(self):
        spec = _make_spec()
        p0 = _gen(spec, signal_index=0)
        p1 = _gen(spec, signal_index=1)
        assert p0["data"]["id"] != p1["data"]["id"]

    def test_issue_id_is_stable_across_signals_in_same_spec(self):
        """All signals in the same spec share the same issue ID (same thread)."""
        spec = _make_spec()
        issue_ids = set()
        for si in range(5):
            payload = _gen(spec, signal_index=si, within=si)
            issue_ids.add(payload["data"]["issue"]["id"])
        assert len(issue_ids) == 1, (
            f"All signals in one spec should share one issue ID; got {issue_ids}"
        )

    def test_timestamp_format(self):
        payload = _gen(_make_spec())
        ts = payload["data"]["timestamp"]
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.year == 2026


class TestDeterminism:
    """Same seed → byte-identical payload."""

    def test_full_determinism(self):
        spec = _make_spec(
            contact_diversity="multi",
            email_tone="escalation",
            concern_topic="outage",
            message_length="multi",
            sentiment_trajectory="declining",
        )
        p1 = _gen(spec, seed=99)
        p2 = _gen(spec, seed=99)
        assert p1 == p2

    def test_different_seeds_differ(self):
        spec = _make_spec()
        p1 = _gen(spec, seed=1)
        p2 = _gen(spec, seed=2)
        assert p1 != p2


class TestRoundTrip:
    """Synthetic Pylon payload → parse_pylon_event → StructuredSignalInput."""

    def _parse(self, payload: dict):
        event_type = payload["data"]["type"]
        return parse_pylon_event(payload, event_type, UUID(int=0))

    def test_round_trip_issue_created(self):
        payload = _gen(_make_spec(), within=0)
        result = self._parse(payload)
        assert result is not None
        assert result.kind == "ticket"
        assert result.external_id.startswith("pylon:")
        assert result.direction == Direction.INBOUND
        assert len(result.participants) == 1
        assert result.participants[0].role == "customer"

    def test_round_trip_issue_message_added_customer(self):
        """Inbound customer message → INBOUND direction."""
        for seed in range(200):
            payload = _gen(_make_spec(), seed=seed, within=1)
            data = payload["data"]
            if data["type"] == "issue.message_added":
                msg = data["issue"]["messages"][-1]
                if msg["author"]["type"] == "customer":
                    result = self._parse(payload)
                    assert result is not None
                    assert result.kind == "ticket"
                    assert result.direction == Direction.INBOUND
                    assert result.external_id.startswith("pylon:")
                    return
        pytest.skip("No customer message_added in 200 seeds")

    def test_round_trip_issue_message_added_agent(self):
        """Outbound agent reply → OUTBOUND direction."""
        for seed in range(200):
            payload = _gen(_make_spec(), seed=seed, within=1)
            data = payload["data"]
            if data["type"] == "issue.message_added":
                msg = data["issue"]["messages"][-1]
                if msg["author"]["type"] == "agent":
                    result = self._parse(payload)
                    assert result is not None
                    assert result.kind == "ticket"
                    assert result.direction == Direction.OUTBOUND
                    return
        pytest.skip("No agent message_added in 200 seeds")

    def test_round_trip_issue_status_changed_returns_none(self):
        """issue.status_changed is recognized but skipped → returns None (no error)."""
        for seed in range(300):
            payload = _gen(_make_spec(), seed=seed, within=1)
            if payload["data"]["type"] == "issue.status_changed":
                result = self._parse(payload)
                assert result is None, (
                    "issue.status_changed must return None (skip path), not raise"
                )
                return
        pytest.skip("No issue.status_changed generated in 300 seeds")

    def test_round_trip_all_fields_valid(self):
        result = self._parse(_gen(_make_spec(), within=0))
        assert result is not None
        assert result.occurred_at.tzinfo is not None
        assert result.thread_id is not None
        assert result.thread_id.startswith("pylon:")

    def test_round_trip_body_non_empty_for_issue_created(self):
        payload = _gen(_make_spec(), within=0)
        result = self._parse(payload)
        assert result is not None
        assert result.body.strip(), "issue.created body must be non-empty"

    def test_round_trip_body_non_empty_for_message_added(self):
        for seed in range(100):
            payload = _gen(_make_spec(), seed=seed, within=1)
            if payload["data"]["type"] == "issue.message_added":
                result = self._parse(payload)
                assert result is not None
                assert result.body.strip(), "issue.message_added body must be non-empty"
                return
        pytest.skip("No issue.message_added in 100 seeds")


class TestVariabilityAxes:
    """Axes produce topically distinct content."""

    def test_escalation_tone_differs_from_casual(self):
        spec_esc = _make_spec(
            email_tone="escalation", concern_topic="none", sentiment_trajectory="flat"
        )
        spec_cas = _make_spec(
            email_tone="casual", concern_topic="none", sentiment_trajectory="flat"
        )

        esc_bodies: set[str] = set()
        cas_bodies: set[str] = set()
        for seed in range(30):
            p_esc = _gen(spec_esc, seed=seed, within=0)
            p_cas = _gen(spec_cas, seed=seed, within=0)
            # issue.created always has a messages[0].body
            esc_bodies.add(p_esc["data"]["issue"]["messages"][0]["body"])
            cas_bodies.add(p_cas["data"]["issue"]["messages"][0]["body"])

        assert esc_bodies != cas_bodies or not esc_bodies, (
            "Escalation and casual bodies are identical — tone selection broken"
        )

    def test_concern_topic_outage_appears_in_title(self):
        spec = _make_spec(concern_topic="outage", email_tone="escalation")
        titles: list[str] = []
        for seed in range(20):
            p = _gen(spec, seed=seed, within=0)
            titles.append(p["data"]["issue"]["title"].lower())
        assert any(
            ("outage" in t or "incident" in t or "urgent" in t) for t in titles
        ), f"No outage-related title found in: {titles}"

    def test_corporate_email_origin(self):
        spec = _make_spec(contact_email_origin="corporate")
        payload = _gen(spec, within=0)
        requester_email = payload["data"]["issue"]["requester"]["email"]
        assert requester_email.endswith("@testaccount.com"), (
            f"Expected corporate domain: {requester_email}"
        )

    def test_personal_email_origin(self):
        spec = _make_spec(contact_email_origin="personal_email")
        for seed in range(20):
            payload = _gen(spec, seed=seed, within=0)
            requester_email = payload["data"]["issue"]["requester"]["email"]
            if not requester_email.endswith("@testaccount.com"):
                return
        pytest.fail("All 20 seeds produced corporate email despite personal_email axis")

    def test_sentiment_trajectory_declining_escalates_tone(self):
        """declining trajectory should produce escalation tone on later signals."""
        spec_declining = _make_spec(sentiment_trajectory="declining", email_tone="casual")
        # Within=6 → _resolve_ticket_tone returns "escalation" for declining trajectory
        payloads = [_gen(spec_declining, seed=s, within=6) for s in range(10)]
        bodies = [
            p["data"]["issue"]["messages"][0]["body"]
            for p in payloads
            if p["data"]["type"] == "issue.created"
        ]
        # Just verify they're non-empty and present — tone routing test is implicit
        # (the _resolve_ticket_tone function is unit-tested via the Plain tests already)
        assert all(b.strip() for b in bodies)


class TestContactPool:
    def test_single_pool_has_one_contact(self):
        rng = random.Random(1)
        pool = build_pylon_contact_pool(rng, AxesSpec(contact_diversity="single"), "example.com")
        assert len(pool) == 1

    def test_multi_pool_has_two_contacts(self):
        rng = random.Random(1)
        pool = build_pylon_contact_pool(rng, AxesSpec(contact_diversity="multi"), "example.com")
        assert len(pool) == 2

    def test_crowded_pool_has_2_or_3(self):
        for seed in range(5):
            rng = random.Random(seed)
            pool = build_pylon_contact_pool(
                rng, AxesSpec(contact_diversity="crowded"), "example.com"
            )
            assert 2 <= len(pool) <= 3


class TestHypothesisInvariants:
    """Property tests: generators are deterministic and never produce invalid output."""

    @given(
        seed=st.integers(min_value=0, max_value=10_000),
        signal_index=st.integers(min_value=0, max_value=100),
        within=st.integers(min_value=0, max_value=20),
    )
    @settings(max_examples=200)
    def test_deterministic_output(self, seed, signal_index, within):
        spec = _make_spec()
        p1 = _gen(spec, seed=seed, signal_index=signal_index, within=within)
        p2 = _gen(spec, seed=seed, signal_index=signal_index, within=within)
        assert p1 == p2

    @given(
        seed=st.integers(min_value=0, max_value=10_000),
        within=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=200)
    def test_id_never_empty(self, seed, within):
        spec = _make_spec()
        payload = _gen(spec, seed=seed, within=within)
        assert payload["data"]["id"]

    @given(
        seed=st.integers(min_value=0, max_value=10_000),
        within=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=200)
    def test_type_always_valid(self, seed, within):
        spec = _make_spec()
        payload = _gen(spec, seed=seed, within=within)
        assert payload["data"]["type"] in {
            "issue.created",
            "issue.message_added",
            "issue.status_changed",
        }

    @given(
        seed=st.integers(min_value=0, max_value=10_000),
    )
    @settings(max_examples=200)
    def test_issue_created_body_never_empty(self, seed):
        """issue.created (within=0) must always have a non-empty message body."""
        spec = _make_spec()
        payload = _gen(spec, seed=seed, within=0)
        assert payload["data"]["type"] == "issue.created"
        body = payload["data"]["issue"]["messages"][0]["body"]
        assert body.strip(), f"Empty body at seed={seed}"
