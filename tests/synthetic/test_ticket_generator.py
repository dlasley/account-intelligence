"""Unit tests for src/synthetic/generators/ticket_plain.py (ADR-020 Phase 4 / Phase 4.5)."""

import random
import re
import uuid
from datetime import UTC, datetime
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.integrations.plain.adapter import parse_plain_event
from src.synthetic.generators.ticket_plain import (
    build_ticket_contact_pool,
    generate_ticket_payload,
)
from src.synthetic.scenario import AxesSpec, SignalSpec

_EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
_NOW = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
_SCENARIO_NAME = "test-ticket-scenario"


def _make_spec(**axes_kwargs) -> SignalSpec:
    return SignalSpec(
        source_type="plain_ticket",
        account_slug="test-account",
        count=1,
        axes=AxesSpec(**axes_kwargs),
    )


def _gen(spec: SignalSpec, seed: int = 42, signal_index: int = 0, within: int = 0) -> dict:
    rng = random.Random(seed)
    return generate_ticket_payload(
        spec=spec,
        rng=rng,
        now=_NOW,
        signal_index=signal_index,
        scenario_name=_SCENARIO_NAME,
        account_name="Test Account",
        primary_domain="testaccount.com",
        signal_index_within_spec=within,
    )


class TestTicketPayloadShape:
    """Verify Plain event shape contract."""

    def test_required_top_level_fields(self):
        payload = _gen(_make_spec())
        for field in ("id", "type", "timestamp", "workspaceId", "payload"):
            assert field in payload, f"Missing required field: {field}"

    def test_event_type_is_string(self):
        payload = _gen(_make_spec())
        assert isinstance(payload["type"], str)
        assert payload["type"] in {"thread.created", "email.received", "email.sent"}

    def test_first_signal_is_thread_created(self):
        """signal_index_within_spec == 0 must always produce thread.created."""
        for seed in range(10):
            payload = _gen(_make_spec(), seed=seed, within=0)
            assert payload["type"] == "thread.created", f"seed={seed}: expected thread.created"

    def test_follow_up_signals_are_email_events(self):
        """signal_index_within_spec > 0 must produce email.received or email.sent."""
        for within in range(1, 6):
            payload = _gen(_make_spec(), seed=42, within=within)
            assert payload["type"] in {"email.received", "email.sent"}

    def test_thread_created_has_customer_in_payload(self):
        payload = _gen(_make_spec(contact_diversity="single"), within=0)
        assert payload["type"] == "thread.created"
        customer = payload["payload"]["customer"]
        email_field = customer["email"]["email"]
        assert _EMAIL_RE.match(email_field), f"Bad customer email: {email_field}"

    def test_email_received_has_from_field(self):
        """Find a seed that produces email.received for within=1."""
        for seed in range(50):
            payload = _gen(_make_spec(), seed=seed, within=1)
            if payload["type"] == "email.received":
                from_email = payload["payload"]["email"]["from_"]["email"]
                assert _EMAIL_RE.match(from_email)
                return
        pytest.skip("No email.received generated in 50 seeds — probabilistic test")

    def test_email_sent_has_to_field(self):
        """Find a seed that produces email.sent for within=2."""
        for seed in range(200):
            payload = _gen(_make_spec(), seed=seed, within=2)
            if payload["type"] == "email.sent":
                to_list = payload["payload"]["email"]["to"]
                assert len(to_list) >= 1
                assert _EMAIL_RE.match(to_list[0]["email"])
                return
        pytest.skip("No email.sent generated in 200 seeds — probabilistic test")

    def test_external_id_is_deterministic(self):
        spec = _make_spec()
        p1 = _gen(spec, seed=42, signal_index=7)
        p2 = _gen(spec, seed=42, signal_index=7)
        assert p1["id"] == p2["id"]

    def test_external_id_namespaced_with_uuid5(self):
        spec = _make_spec()
        payload = _gen(spec, seed=42, signal_index=5)
        expected_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{_SCENARIO_NAME}:ticket:5"))
        assert payload["id"] == expected_id

    def test_different_signal_indexes_differ(self):
        spec = _make_spec()
        p0 = _gen(spec, signal_index=0)
        p1 = _gen(spec, signal_index=1)
        assert p0["id"] != p1["id"]

    def test_timestamp_format(self):
        payload = _gen(_make_spec())
        ts = payload["timestamp"]
        # Must be parseable ISO 8601
        from datetime import datetime as dt
        parsed = dt.fromisoformat(ts.replace("Z", "+00:00"))
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
        # IDs will differ because the rng is seeded differently
        assert p1 != p2


class TestRoundTrip:
    """Synthetic ticket payload → parse_plain_event → StructuredSignalInput."""

    def _round_trip(self, spec: SignalSpec, within: int = 0, seed: int = 42):
        payload = _gen(spec, seed=seed, within=within)
        event_type = payload["type"]
        result = parse_plain_event(payload, event_type, UUID(int=0))
        return result

    def test_round_trip_thread_created(self):
        result = self._round_trip(_make_spec(), within=0)
        assert result is not None
        assert result.kind == "ticket"
        assert result.external_id.startswith("plain:")
        assert len(result.participants) == 1
        assert result.participants[0].role == "customer"

    def test_round_trip_email_received(self):
        for seed in range(100):
            payload = _gen(_make_spec(), seed=seed, within=1)
            if payload["type"] == "email.received":
                result = parse_plain_event(payload, "email.received", UUID(int=0))
                assert result is not None
                assert result.kind == "ticket"
                assert result.body is not None  # may be empty string for thread.created
                return
        pytest.skip("No email.received in 100 seeds")

    def test_round_trip_email_sent(self):
        for seed in range(200):
            payload = _gen(_make_spec(), seed=seed, within=2)
            if payload["type"] == "email.sent":
                result = parse_plain_event(payload, "email.sent", UUID(int=0))
                assert result is not None
                assert result.kind == "ticket"
                from src.domain.signal import Direction
                assert result.direction == Direction.OUTBOUND
                return
        pytest.skip("No email.sent in 200 seeds")

    def test_round_trip_all_fields_valid(self):
        result = self._round_trip(_make_spec(), within=0)
        assert result is not None
        assert result.occurred_at.tzinfo is not None
        assert result.external_id.startswith("plain:")

    def test_escalation_tone_body_non_empty_for_email_received(self):
        spec = _make_spec(email_tone="escalation", concern_topic="outage")
        for seed in range(100):
            payload = _gen(spec, seed=seed, within=1)
            if payload["type"] == "email.received":
                result = parse_plain_event(payload, "email.received", UUID(int=0))
                assert result is not None
                assert result.body.strip(), "body must be non-empty for email.received"
                return
        pytest.skip("No email.received in 100 seeds")


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
        for seed in range(20):
            for within in range(1, 3):
                p_esc = _gen(spec_esc, seed=seed, within=within)
                p_cas = _gen(spec_cas, seed=seed, within=within)
                if p_esc["type"] == "email.received":
                    esc_bodies.add(p_esc["payload"]["email"]["textContent"])
                if p_cas["type"] == "email.received":
                    cas_bodies.add(p_cas["payload"]["email"]["textContent"])

        # Escalation and casual should produce different content
        assert esc_bodies != cas_bodies or not esc_bodies, (
            "Escalation and casual bodies are identical — tone selection broken"
        )

    def test_concern_topic_outage_appears_in_subject(self):
        spec = _make_spec(concern_topic="outage", email_tone="escalation")
        subjects: list[str] = []
        for seed in range(20):
            for within in range(2):
                p = _gen(spec, seed=seed, within=within)
                subject = (
                    p["payload"].get("thread", {}).get("title")
                    or p["payload"].get("email", {}).get("subject")
                    or ""
                )
                subjects.append(subject.lower())
        # At least one subject should reference "outage" or "incident" or "urgent"
        assert any(
            ("outage" in s or "incident" in s or "urgent" in s or "urgent" in s)
            for s in subjects
        ), f"No outage-related subject found in: {subjects}"

    def test_corporate_email_origin(self):
        spec = _make_spec(contact_email_origin="corporate")
        payload = _gen(spec, within=0)
        customer = payload["payload"]["customer"]
        email = customer["email"]["email"]
        assert email.endswith("@testaccount.com"), f"Expected corporate domain: {email}"

    def test_personal_email_origin(self):
        spec = _make_spec(contact_email_origin="personal_email")
        for seed in range(20):
            payload = _gen(spec, seed=seed, within=0)
            customer = payload["payload"]["customer"]
            email = customer["email"]["email"]
            if not email.endswith("@testaccount.com"):
                return  # found a personal email
        pytest.fail("All 20 seeds produced corporate email despite personal_email axis")


class TestContactPool:
    def test_single_pool_has_one_contact(self):
        rng = random.Random(1)
        pool = build_ticket_contact_pool(rng, AxesSpec(contact_diversity="single"), "example.com")
        assert len(pool) == 1

    def test_multi_pool_has_two_contacts(self):
        rng = random.Random(1)
        pool = build_ticket_contact_pool(rng, AxesSpec(contact_diversity="multi"), "example.com")
        assert len(pool) == 2

    def test_crowded_pool_has_2_or_3(self):
        for seed in range(5):
            rng = random.Random(seed)
            pool = build_ticket_contact_pool(
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
        assert payload["id"]

    @given(
        seed=st.integers(min_value=0, max_value=10_000),
        within=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=200)
    def test_type_always_valid(self, seed, within):
        spec = _make_spec()
        payload = _gen(spec, seed=seed, within=within)
        assert payload["type"] in {"thread.created", "email.received", "email.sent"}
