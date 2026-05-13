"""Unit tests for src/synthetic/generators/meeting_note.py (ADR-020 Phase 4)."""

import random
import re
from datetime import UTC, datetime
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from src.domain.signal import Direction
from src.integrations.granola.adapter import parse_granola_note
from src.synthetic.generators.meeting_note import generate_meeting_note_payload
from src.synthetic.scenario import AxesSpec, SignalSpec

_EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
_NOW = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
_SCENARIO_NAME = "test-note-scenario"


def _make_spec(**axes_kwargs) -> SignalSpec:
    return SignalSpec(
        source_type="granola_note",
        account_slug="test-account",
        count=1,
        axes=AxesSpec(**axes_kwargs),
    )


def _gen(spec: SignalSpec, seed: int = 42, signal_index: int = 0, within: int = 0) -> dict:
    rng = random.Random(seed)
    return generate_meeting_note_payload(
        spec=spec,
        rng=rng,
        now=_NOW,
        signal_index=signal_index,
        scenario_name=_SCENARIO_NAME,
        account_name="Test Account",
        primary_domain="testaccount.com",
        signal_index_within_spec=within,
    )


class TestNotePayloadShape:
    """Verify Granola note shape contract."""

    def test_required_fields_present(self):
        note = _gen(_make_spec())
        for field in ("id", "title", "createdAt", "owner", "summary"):
            assert field in note, f"Missing required field: {field}"

    def test_owner_has_email_and_name(self):
        note = _gen(_make_spec())
        owner = note["owner"]
        assert "email" in owner
        assert "name" in owner
        assert _EMAIL_RE.match(owner["email"]), f"Bad owner email: {owner['email']}"

    def test_id_prefixed_not(self):
        note = _gen(_make_spec())
        assert note["id"].startswith("not_"), f"Note id must start with 'not_': {note['id']}"

    def test_summary_non_empty(self):
        note = _gen(_make_spec())
        assert note["summary"].strip(), "summary must not be blank"

    def test_title_non_empty(self):
        note = _gen(_make_spec())
        assert note["title"].strip(), "title must not be blank"

    def test_created_at_format(self):
        note = _gen(_make_spec())
        from datetime import datetime as dt
        parsed = dt.fromisoformat(note["createdAt"].replace("Z", "+00:00"))
        assert parsed.year == 2026

    def test_transcript_present_for_paragraph(self):
        spec = _make_spec(message_length="paragraph")
        note = _gen(spec)
        assert note.get("transcript") is not None
        assert len(note["transcript"]) >= 1

    def test_transcript_absent_for_short(self):
        spec = _make_spec(message_length="short")
        note = _gen(spec)
        assert note.get("transcript") is None

    def test_transcript_speaker_fields(self):
        spec = _make_spec(message_length="paragraph")
        note = _gen(spec)
        transcript = note["transcript"]
        for turn in transcript:
            assert "speaker" in turn
            assert "text" in turn
            assert turn["text"].strip()

    def test_multi_length_longer_summary(self):
        spec_short = _make_spec(message_length="short")
        spec_multi = _make_spec(message_length="multi")
        note_short = _gen(spec_short, seed=1)
        note_multi = _gen(spec_multi, seed=1)
        assert len(note_multi["summary"]) > len(note_short["summary"])


class TestDeterminism:
    """Same seed → byte-identical note."""

    def test_full_determinism(self):
        spec = _make_spec(
            message_length="multi",
            email_tone="escalation",
            concern_topic="outage",
            sentiment_trajectory="declining",
        )
        n1 = _gen(spec, seed=99)
        n2 = _gen(spec, seed=99)
        assert n1 == n2

    def test_different_seeds_differ(self):
        spec = _make_spec()
        n1 = _gen(spec, seed=1)
        n2 = _gen(spec, seed=2)
        assert n1 != n2

    def test_different_signal_indexes_differ(self):
        spec = _make_spec()
        n0 = _gen(spec, signal_index=0)
        n1 = _gen(spec, signal_index=1)
        assert n0["id"] != n1["id"]


class TestRoundTrip:
    """Synthetic note → parse_granola_note → StructuredSignalInput."""

    def _round_trip(self, spec: SignalSpec, seed: int = 42):
        note = _gen(spec, seed=seed)
        result = parse_granola_note(note, UUID(int=0))
        return result

    def test_round_trip_basic(self):
        result = self._round_trip(_make_spec())
        assert result is not None
        assert result.kind == "meeting_note"
        assert result.external_id.startswith("granola:")
        assert result.direction == Direction.INTERNAL

    def test_round_trip_participants_non_empty(self):
        result = self._round_trip(_make_spec())
        assert result is not None
        assert len(result.participants) >= 1

    def test_round_trip_external_id_format(self):
        note = _gen(_make_spec())
        result = parse_granola_note(note, UUID(int=0))
        assert result is not None
        expected_id = f"granola:{note['id']}"
        assert result.external_id == expected_id

    def test_round_trip_occurred_at_tz_aware(self):
        result = self._round_trip(_make_spec())
        assert result is not None
        assert result.occurred_at.tzinfo is not None

    def test_round_trip_subject_equals_title(self):
        note = _gen(_make_spec())
        result = parse_granola_note(note, UUID(int=0))
        assert result is not None
        assert result.subject == note["title"]

    def test_round_trip_body_contains_summary(self):
        note = _gen(_make_spec(message_length="paragraph"))
        result = parse_granola_note(note, UUID(int=0))
        assert result is not None
        # Body starts with the summary (transcript appended after)
        assert result.body.startswith(note["summary"][:30])

    def test_round_trip_meeting_note_direction_internal(self):
        result = self._round_trip(_make_spec())
        assert result is not None
        assert result.direction == Direction.INTERNAL


class TestVariabilityAxes:
    """Axes produce topically distinct content."""

    def test_outage_concern_topic_in_title(self):
        spec = _make_spec(concern_topic="outage")
        titles: list[str] = []
        for seed in range(20):
            note = _gen(spec, seed=seed)
            titles.append(note["title"].lower())
        assert any(
            ("outage" in t or "incident" in t or "debrief" in t)
            for t in titles
        ), f"No outage-related title found: {titles}"

    def test_success_expansion_title(self):
        spec = _make_spec(concern_topic="success_expansion")
        titles = [_gen(spec, seed=seed)["title"].lower() for seed in range(20)]
        assert any(
            ("expansion" in t or "qbr" in t or "growth" in t or "planning" in t)
            for t in titles
        ), f"No expansion-related title: {titles}"

    def test_renewal_pending_title(self):
        spec = _make_spec(concern_topic="renewal_pending")
        titles = [_gen(spec, seed=seed)["title"].lower() for seed in range(20)]
        assert any(("renewal" in t or "qbr" in t) for t in titles), f"No renewal title: {titles}"

    def test_declining_trajectory_ends_negative(self):
        """Later signals (within=8+) should surface negative/escalation language."""
        spec = _make_spec(sentiment_trajectory="declining", message_length="paragraph")
        negative_words = {"escalat", "incident", "frustrat", "difficult", "concern", "strained"}
        late_summaries = [_gen(spec, seed=seed, within=8)["summary"].lower() for seed in range(10)]
        assert any(
            any(w in s for w in negative_words) for s in late_summaries
        ), "No negative language found in late declining summaries"


class TestHypothesisInvariants:
    """Property tests: generators are deterministic and produce valid output."""

    @given(
        seed=st.integers(min_value=0, max_value=10_000),
        signal_index=st.integers(min_value=0, max_value=100),
        within=st.integers(min_value=0, max_value=20),
    )
    @settings(max_examples=200)
    def test_deterministic_output(self, seed, signal_index, within):
        spec = _make_spec()
        n1 = _gen(spec, seed=seed, signal_index=signal_index, within=within)
        n2 = _gen(spec, seed=seed, signal_index=signal_index, within=within)
        assert n1 == n2

    @given(
        seed=st.integers(min_value=0, max_value=10_000),
        within=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=200)
    def test_summary_never_empty(self, seed, within):
        spec = _make_spec()
        note = _gen(spec, seed=seed, within=within)
        assert note["summary"].strip()

    @given(
        seed=st.integers(min_value=0, max_value=10_000),
        within=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=200)
    def test_id_always_starts_with_not(self, seed, within):
        spec = _make_spec()
        note = _gen(spec, seed=seed, within=within)
        assert note["id"].startswith("not_")

    @given(
        seed=st.integers(min_value=0, max_value=10_000),
        within=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=200)
    def test_round_trip_never_raises(self, seed, within):
        """parse_granola_note must accept every generated note without ValueError."""
        spec = _make_spec()
        note = _gen(spec, seed=seed, within=within)
        result = parse_granola_note(note, UUID(int=0))
        assert result is not None  # non-empty summary guaranteed by generator
