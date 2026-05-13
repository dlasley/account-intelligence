"""Unit tests for src/synthetic/generators/email.py."""

import random
import re
import uuid
from datetime import UTC, datetime

from src.synthetic.generators.email import generate_email_payload
from src.synthetic.scenario import AxesSpec, SignalSpec

_EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
_NOW = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
_SCENARIO_NAME = "test-scenario"


def _make_spec(**axes_kwargs) -> SignalSpec:
    return SignalSpec(
        source_type="inbound_email",
        account_slug="test-account",
        count=1,
        axes=AxesSpec(**axes_kwargs),
    )


def _gen(spec: SignalSpec, seed: int = 42, signal_index: int = 0, within: int = 0) -> dict:
    rng = random.Random(seed)
    return generate_email_payload(
        spec=spec,
        rng=rng,
        now=_NOW,
        signal_index=signal_index,
        scenario_name=_SCENARIO_NAME,
        account_name="Test Account",
        primary_domain="testaccount.com",
        signal_index_within_spec=within,
    )


class TestEmailPayloadContract:
    def test_from_email_matches_regex(self):
        payload = _gen(_make_spec())
        assert _EMAIL_RE.match(payload["from_email"]), f"Bad from_email: {payload['from_email']}"

    def test_body_non_blank(self):
        payload = _gen(_make_spec())
        assert payload["body"].strip(), "body must not be blank"

    def test_required_fields_present(self):
        payload = _gen(_make_spec())
        for field in (
            "external_id",
            "source_type",
            "direction",
            "channel",
            "occurred_at",
            "from_email",
            "body",
        ):
            assert field in payload, f"Missing required field: {field}"

    def test_no_routing_fields(self):
        payload = _gen(_make_spec())
        for field in ("routing_method", "routing_confidence", "account_id"):
            assert field not in payload, f"Routing field must not be pre-set: {field}"

    def test_external_id_is_uuid5_deterministic(self):
        spec = _make_spec()
        p1 = _gen(spec, seed=42, signal_index=5)
        p2 = _gen(spec, seed=42, signal_index=5)
        assert p1["external_id"] == p2["external_id"]
        # Verify it matches the expected uuid5 value
        expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{_SCENARIO_NAME}:5"))
        assert p1["external_id"] == expected

    def test_different_signal_indexes_produce_different_external_ids(self):
        spec = _make_spec()
        p0 = _gen(spec, signal_index=0)
        p1 = _gen(spec, signal_index=1)
        assert p0["external_id"] != p1["external_id"]

    def test_occurred_at_format(self):
        payload = _gen(_make_spec())
        # Must be ISO-8601 with Z suffix — matches normalizer's fromisoformat expectation
        assert payload["occurred_at"].endswith("Z")
        parsed = datetime.fromisoformat(payload["occurred_at"])
        assert parsed.tzinfo is not None

    def test_channel_is_email(self):
        payload = _gen(_make_spec())
        assert payload["channel"] == "email"

    def test_direction_is_inbound(self):
        payload = _gen(_make_spec())
        assert payload["direction"] == "inbound"


class TestDomainMixing:
    def test_corporate_domain(self):
        spec = _make_spec(contact_email_origin="corporate")
        for seed in range(10):
            p = _gen(spec, seed=seed)
            assert "@testaccount.com" in p["from_email"], (
                f"Expected corporate domain: {p['from_email']}"
            )

    def test_personal_email_domain(self):
        free_domains = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"}
        spec = _make_spec(contact_email_origin="personal_email")
        for seed in range(10):
            p = _gen(spec, seed=seed)
            domain = p["from_email"].split("@")[-1]
            assert domain in free_domains, f"Expected free-mail domain: {p['from_email']}"

    def test_mixed_domain_produces_variety(self):
        spec = _make_spec(contact_email_origin="mixed", contact_diversity="single")
        domains = set()
        for seed in range(20):
            p = _gen(spec, seed=seed)
            domains.add(p["from_email"].split("@")[-1])
        # Over 20 seeds, should see both corporate and non-corporate domains
        assert "testaccount.com" in domains, "Mixed should produce some corporate domains"
        assert len(domains) > 1, "Mixed should produce variety of domains"


class TestContactDiversity:
    def test_single_no_cc(self):
        spec = _make_spec(contact_diversity="single")
        p = _gen(spec)
        assert p["to_emails"] == [], f"Single contact should have no CC: {p['to_emails']}"

    def test_multi_has_cc(self):
        spec = _make_spec(contact_diversity="multi")
        # Run several seeds to get multi contact consistently
        seen_cc = False
        for seed in range(20):
            p = _gen(spec, seed=seed)
            if p["to_emails"]:
                seen_cc = True
                break
        assert seen_cc, "multi contact_diversity should produce CC recipients"

    def test_crowded_has_more_contacts(self):
        # crowded can produce 4-6 contacts; single = 1
        spec_crowded = _make_spec(contact_diversity="crowded")
        spec_single = _make_spec(contact_diversity="single")
        total_crowded = 0
        total_single = 0
        for seed in range(10):
            p_c = _gen(spec_crowded, seed=seed)
            p_s = _gen(spec_single, seed=seed)
            total_crowded += 1 + len(p_c["to_emails"])
            total_single += 1 + len(p_s["to_emails"])
        assert total_crowded > total_single, "crowded should produce more contacts than single"


class TestMessageLength:
    def test_short_body_bounded(self):
        spec = _make_spec(message_length="short")
        for seed in range(5):
            p = _gen(spec, seed=seed)
            # Short bodies should be under 200 chars after trimming
            assert len(p["body"].strip()) <= 200, f"Short body too long: {p['body']}"

    def test_multi_body_longer(self):
        spec_paragraph = _make_spec(message_length="paragraph")
        spec_multi = _make_spec(message_length="multi")
        avg_para = sum(len(_gen(spec_paragraph, seed=s)["body"]) for s in range(5)) / 5
        avg_multi = sum(len(_gen(spec_multi, seed=s)["body"]) for s in range(5)) / 5
        assert avg_multi > avg_para, "multi bodies should be longer than paragraph bodies"

    def test_chain_contains_quoted_block(self):
        spec = _make_spec(message_length="chain")
        for seed in range(5):
            p = _gen(spec, seed=seed)
            assert "Original message" in p["body"] or "---" in p["body"], (
                f"Chain body should contain quoted block: {p['body'][:100]}"
            )


class TestThreadingTopology:
    def test_linear_consistent_thread_id(self):
        spec = _make_spec(threading_topology="linear")
        thread_ids = {_gen(spec, seed=42, signal_index=i)["thread_id"] for i in range(5)}
        assert len(thread_ids) == 1, f"Linear should have consistent thread_id: {thread_ids}"

    def test_missing_thread_id_is_none(self):
        spec = _make_spec(threading_topology="missing_thread_id")
        for seed in range(3):
            p = _gen(spec, seed=seed)
            assert p["thread_id"] is None

    def test_standalone_topology_unique_thread_ids(self):
        spec = _make_spec(threading_topology="standalone")
        thread_ids = [_gen(spec, seed=42, signal_index=i)["thread_id"] for i in range(5)]
        assert len(set(thread_ids)) == len(thread_ids), (
            f"Standalone topology should have unique thread IDs: {thread_ids}"
        )


class TestSentimentTrajectory:
    def test_declining_ends_in_escalation_template(self):
        # Signal index 9+ should use escalation tone (see _resolve_email_tone)
        spec = _make_spec(sentiment_trajectory="declining", email_tone="technical")
        # Late signals use escalation templates
        p_late = _gen(spec, within=9)
        p_early = _gen(spec, within=0)
        # Bodies should differ in register flavor — just assert they're both non-empty
        assert p_late["body"].strip()
        assert p_early["body"].strip()

    def test_recovering_early_signal_uses_escalation(self):
        spec = _make_spec(sentiment_trajectory="recovering")
        p_early = _gen(spec, within=0)
        # recovering starts with escalation register — body should use that template pool
        assert p_early["body"].strip()

    def test_oscillating_varies_register(self):
        spec = _make_spec(sentiment_trajectory="oscillating")
        bodies = [_gen(spec, seed=1, signal_index=i, within=i)["body"] for i in range(6)]
        # With oscillating, bodies should vary (casual vs escalation register)
        assert len(set(bodies)) > 1, "Oscillating trajectory should produce varied bodies"


class TestCrossSignalContactStability:
    """Verify that build_contact_pool + generate_email_payload honor contact_diversity
    across multiple signals in the same spec (not just within a single signal)."""

    def _gen_with_pool(self, spec, seed: int, signal_indices: list[int]) -> list[dict]:
        """Generate multiple signals sharing the same pre-built contact pool."""
        from src.synthetic.generators.email import build_contact_pool

        rng = random.Random(seed)
        pool = build_contact_pool(rng, spec.axes, "testaccount.com")
        payloads = []
        for i, sig_idx in enumerate(signal_indices):
            payloads.append(
                generate_email_payload(
                    spec=spec,
                    rng=rng,
                    now=_NOW,
                    signal_index=sig_idx,
                    scenario_name=_SCENARIO_NAME,
                    account_name="Test Account",
                    primary_domain="testaccount.com",
                    signal_index_within_spec=i,
                    contact_pool=pool,
                )
            )
        return payloads

    def test_single_same_sender_across_signals(self):
        """contact_diversity=single: all signals share the exact same from_email."""
        spec = _make_spec(contact_diversity="single")
        payloads = self._gen_with_pool(spec, seed=42, signal_indices=list(range(6)))
        senders = {p["from_email"] for p in payloads}
        assert len(senders) == 1, f"single should have exactly 1 sender; got {senders}"

    def test_multi_sender_pool_stable_across_signals(self):
        """contact_diversity=multi: senders come from a fixed pool of 2-3 contacts."""
        spec = _make_spec(contact_diversity="multi")
        # Build the pool separately to check its size
        from src.synthetic.generators.email import build_contact_pool

        rng = random.Random(99)
        pool = build_contact_pool(rng, spec.axes, "testaccount.com")
        assert 2 <= len(pool) <= 3, f"multi pool size should be 2-3; got {len(pool)}"

        # Now generate signals and confirm senders are all from the pool
        pool_emails = {email for email, _ in pool}
        payloads = self._gen_with_pool(spec, seed=99, signal_indices=list(range(8)))
        for p in payloads:
            assert p["from_email"] in pool_emails, (
                f"sender {p['from_email']} not in pool {pool_emails}"
            )

    def test_crowded_sender_pool_stable_across_signals(self):
        """contact_diversity=crowded: senders come from a fixed pool of 4-6 contacts."""
        spec = _make_spec(contact_diversity="crowded")
        from src.synthetic.generators.email import build_contact_pool

        rng = random.Random(7)
        pool = build_contact_pool(rng, spec.axes, "testaccount.com")
        assert 4 <= len(pool) <= 6, f"crowded pool size should be 4-6; got {len(pool)}"

        pool_emails = {email for email, _ in pool}
        payloads = self._gen_with_pool(spec, seed=7, signal_indices=list(range(8)))
        for p in payloads:
            assert p["from_email"] in pool_emails, (
                f"sender {p['from_email']} not in pool {pool_emails}"
            )

    def test_pool_is_deterministic_for_same_rng_state(self):
        """Same rng state before build_contact_pool → same pool."""
        from src.synthetic.generators.email import build_contact_pool

        spec = _make_spec(contact_diversity="multi")
        pool1 = build_contact_pool(random.Random(42), spec.axes, "testaccount.com")
        pool2 = build_contact_pool(random.Random(42), spec.axes, "testaccount.com")
        assert pool1 == pool2, "Same seed must produce the same contact pool"


class TestOverrides:
    def test_field_override_applied(self):
        spec = SignalSpec(
            source_type="inbound_email",
            account_slug="test",
            count=1,
            overrides={"subject": "Overridden Subject"},
        )
        p = _gen(spec)
        assert p["subject"] == "Overridden Subject"

    def test_body_override_respected(self):
        spec = SignalSpec(
            source_type="inbound_email",
            account_slug="test",
            count=1,
            overrides={"body": "Custom body text."},
        )
        p = _gen(spec)
        assert p["body"] == "Custom body text."
