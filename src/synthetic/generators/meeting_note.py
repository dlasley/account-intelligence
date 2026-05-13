# ruff: noqa: E501
"""Meeting-note signal generator (ADR-020 Phase 4).

Produces a Granola-shaped note dict that, when passed through
``parse_granola_note``, yields a valid ``StructuredSignalInput``.

Contract rules:
- Returns a dict matching Granola's ``GET /v1/notes/{id}`` response shape.
- Accepts a seeded ``random.Random`` — no module-level random calls.
- Accepts ``now: datetime`` — no ``datetime.now()`` calls.
- ``external_id`` is deterministic: uuid5(NAMESPACE_DNS,
  "{scenario_name}:note:{signal_index}").

Variability axes honoured:
  message_length        — short (brief summary) / paragraph (standard) / multi (long
                          summary + transcript) / chain (alias for multi)
  email_tone            — drives summary and transcript tone
  sentiment_trajectory  — tone drift across the spec's signal sequence
  concern_topic         — selects meeting-topic template family

Meeting kinds derive from concern_topic:
  none / feature_gap / success_expansion → sync / kickoff / QBR
  outage / escalation-adjacent tones     → incident debrief / escalation call
  renewal_pending / competitive          → renewal review / eval call
  pricing                                → commercial review
  utilization_decline                    → health check / re-engagement call

The meeting note owner is a customer-side participant (on the account's
primary_domain). This makes the synthetic data route correctly through the
production adapter's auto_discovery path. In real Granola the owner is the
CSM (the recorder); per ADR-020 open question 3, transcript-based participant
extraction is deferred — synthesis takes the customer-hosted shortcut to
guarantee correct routing without a richer participants schema.
"""

import random
import uuid
from datetime import datetime

from src.synthetic.generators.email import (
    _FIRST_NAMES,
    _LAST_NAMES,
)
from src.synthetic.scenario import AxesSpec, SignalSpec

# ---------------------------------------------------------------------------
# Meeting title templates per concern_topic
# ---------------------------------------------------------------------------

_MEETING_TITLE_TEMPLATES: dict[str, list[str]] = {
    "outage":             ["{account_name} outage debrief", "{account_name} incident review — {topic}", "Post-incident sync: {account_name}"],
    "feature_gap":        ["{account_name} product roadmap sync", "{account_name} / {topic} feature review", "{account_name} — {topic} deep-dive"],
    "pricing":            ["{account_name} commercial review", "{account_name} pricing discussion", "{account_name} / renewal commercial"],
    "utilization_decline": ["{account_name} health check", "{account_name} adoption review", "{account_name} re-engagement call"],
    "competitive":        ["{account_name} vendor evaluation call", "{account_name} renewal / eval sync", "{account_name} — competitive review"],
    "success_expansion":  ["{account_name} expansion planning", "{account_name} / QBR", "{account_name} — growth sync"],
    "renewal_pending":    ["{account_name} renewal review", "{account_name} QBR", "{account_name} — renewal + roadmap"],
    "none":               ["{account_name} weekly sync", "{account_name} / {date_str}", "{account_name} — check-in", "{account_name} kick-off"],
}

# ---------------------------------------------------------------------------
# Summary template corpus: concern_topic → tone (flat/positive/negative)
# "positive"  = success_expansion, none, renewal (happy path)
# "neutral"   = feature_gap, pricing, utilization_decline
# "negative"  = outage, competitive, escalation
# ---------------------------------------------------------------------------

_POSITIVE_SUMMARIES = [
    "Solid sync with {account_name}. {contact_name} confirmed the team is seeing strong adoption across both use cases we discussed. Key highlights: {topic} rollout is on track, two new team members are onboarding next month, and they're already thinking about expanding to the European team. No blockers. Next steps: share updated pricing proposal by Friday.",
    "Great quarterly review with {account_name}. Usage is up significantly quarter-over-quarter. {contact_name} mentioned exec sponsorship is still strong — the VP of Engineering referenced the platform in an all-hands last week. We agreed to schedule a technical deep-dive on {topic} for the next session.",
    "Productive expansion planning call with {account_name}. {contact_name} walked us through their 90-day roadmap and {topic} sits right at the centre of their Q3 initiative. Team is healthy, adoption is strong, and they're enthusiastic about the new features on the roadmap. Action item: send the multi-team onboarding guide.",
    "Kick-off meeting with {account_name} went well. {contact_name} and two colleagues attended — good energy, clear on their goals. We covered the implementation timeline for {topic} and confirmed the integration path. They have internal buy-in and are ready to move fast.",
]

_NEUTRAL_SUMMARIES = [
    "Check-in with {account_name}. {contact_name} raised a question about {topic} that we need to follow up on — specifically around configuration options and pricing at higher volumes. We reviewed current usage metrics and identified two areas where adoption could be stronger. Action: send a tailored enablement guide by EOW.",
    "Sync with {account_name}. Discussed the current state of {topic} and some gaps the team has been running into. {contact_name} is supportive but acknowledged the team has been slow to adopt. We agreed on a 30-day reengagement plan with defined milestones. Will check back in at the next QBR.",
    "Quarterly review with {account_name}. Coverage: product roadmap for {topic}, current usage health, and upcoming contract renewal. {contact_name} had several questions about upcoming features and pricing. We committed to sharing the roadmap brief and a revised commercial proposal. Mixed tone overall — team is engaged but cautious.",
    "Product roadmap session with {account_name}. {contact_name} shared their feature wishlist for {topic} and we walked through the Q2/Q3 roadmap. Several items on their list are already planned; two are not currently scoped. We'll follow up with a written summary of what's committed vs. under evaluation.",
]

_NEGATIVE_SUMMARIES = [
    "Difficult sync with {account_name}. {contact_name} opened with escalation concerns around {topic} — there have been two incidents in the past month and the team is frustrated. We acknowledged the issues, committed to a root cause summary by Thursday, and proposed a dedicated engineering review. Relationship is strained but recoverable.",
    "Post-incident debrief with {account_name} regarding {topic}. {contact_name} was direct about the business impact: their nightly batch job failed twice and manual recovery took 4+ hours each time. We presented the timeline, root cause, and three corrective actions. They accepted the explanation but made clear that a third incident would trigger a contract review.",
    "Competitive evaluation call with {account_name}. {contact_name} disclosed they've been evaluating an alternative to {topic}. We presented our differentiated capabilities, walked through the roadmap, and addressed the pricing gap. The call was professional but tense. They've agreed to a 30-day hold on the evaluation while we prepare a counter-proposal.",
    "Escalation call with {account_name}. {contact_name} and their VP of Engineering joined. The VP was direct: three months of declining product usage plus an unresolved support ticket has eroded confidence. We committed to an executive sponsor cadence, a dedicated CSE for 60 days, and a formal SLA review. No commitment to stay yet — this is a recovery track.",
]

# ---------------------------------------------------------------------------
# Transcript turn templates
# ---------------------------------------------------------------------------

_CSM_NAMES = ["Sarah Chen", "Marcus Webb", "Priya Nair", "James Torres", "Lena Park"]

_TRANSCRIPT_TURNS: dict[str, list[tuple[str, str]]] = {
    # (csm_line, customer_line)
    "positive": [
        ("Great to reconnect. How has the rollout been going on your end?", "Really well, honestly. The team picked it up faster than we expected. {topic} has been the main focus and it's been smooth."),
        ("Thanks for making time today. I wanted to walk through what we've seen on our side — usage is up about 40% quarter-over-quarter.", "That tracks with what we're seeing internally. We've been pushing adoption pretty hard and it's been paying off."),
        ("Before we get into the agenda — any quick wins to share since our last call?", "Yeah, we shipped two new integrations using {topic} last week. The team is really happy with how it works."),
    ],
    "neutral": [
        ("We wanted to check in on the adoption numbers. They've been a bit lower than we expected.", "Yeah, I know. We had some internal restructuring and it slowed things down. We're back on track now but {topic} got deprioritised for a few weeks."),
        ("How is the team finding {topic} day to day?", "It's mixed. The power users love it. But the broader team hasn't really gotten into it yet — mostly a change management issue on our side."),
        ("I wanted to talk through the upcoming renewal and some of the features you've been asking about.", "Sure. The main thing is we still don't have {topic} the way we need it. That's the biggest question mark for us going into renewal."),
    ],
    "negative": [
        ("I wanted to start by acknowledging what happened with the {topic} incident and take responsibility for the impact on your team.", "I appreciate that. But two incidents in a month is concerning. Our CEO is asking questions and I don't have great answers."),
        ("Can you walk me through what the business impact looked like from your side?", "Four hours of manual recovery work, two engineers pulled off sprint work, and we had to delay a customer delivery. That's the cost."),
        ("We've been running an evaluation in parallel and I wanted to be transparent about that with you.", "I understand. We want to be your partner here but we need to see a different track record on {topic}. Right now we're not confident."),
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_meeting_tone(rng: random.Random, axes: AxesSpec, signal_index_within_spec: int) -> str:
    """Map axes to a tone category: positive / neutral / negative."""
    traj = axes.sentiment_trajectory
    base_tone = axes.email_tone
    i = signal_index_within_spec

    if traj == "declining":
        return "positive" if i < 2 else ("neutral" if i < 5 else "negative")
    elif traj == "recovering":
        return "negative" if i < 2 else ("neutral" if i < 5 else "positive")
    elif traj == "oscillating":
        return "positive" if i % 2 == 0 else "negative"
    elif traj == "sudden_escalation":
        return "negative" if i >= 4 else "neutral"

    # flat: derive from base email_tone
    if base_tone in ("escalation",):
        return "negative"
    elif base_tone in ("apologetic", "technical", "formal"):
        return "neutral"
    else:  # casual
        return "positive"


def _tone_from_concern(concern_topic: str) -> str:
    if concern_topic in ("outage", "competitive"):
        return "negative"
    elif concern_topic in ("success_expansion",):
        return "positive"
    else:
        return "neutral"


def _build_meeting_body(
    rng: random.Random,
    axes: AxesSpec,
    csm_name: str,
    contact_name: str,
    account_name: str,
    topic: str,
    tone: str,
    include_transcript: bool,
) -> str:
    """Build the meeting note body (AI summary ± transcript)."""
    concern_topic = getattr(axes, "concern_topic", "none")
    # Prefer concern-based tone when trajectory is flat
    if axes.sentiment_trajectory == "flat":
        tone = _tone_from_concern(concern_topic)

    if tone == "positive":
        summaries = _POSITIVE_SUMMARIES
    elif tone == "negative":
        summaries = _NEGATIVE_SUMMARIES
    else:
        summaries = _NEUTRAL_SUMMARIES

    summary_template = rng.choice(summaries)
    summary = summary_template.format(
        account_name=account_name,
        contact_name=contact_name,
        topic=topic,
    )

    if axes.message_length == "short":
        # Truncate to ~two sentences
        sentences = summary.split(". ")
        summary = ". ".join(sentences[:2]).rstrip(". ") + "."

    elif axes.message_length in ("multi", "chain"):
        # Append a second summary paragraph
        second_template = rng.choice([t for t in summaries if t != summary_template])
        second = second_template.format(
            account_name=account_name,
            contact_name=contact_name,
            topic=topic,
        )
        summary = f"{summary}\n\n{second}"

    if not include_transcript:
        return summary

    # Build transcript: 2-4 alternating turns
    turns_pool = _TRANSCRIPT_TURNS.get(tone, _TRANSCRIPT_TURNS["neutral"])
    turn_pair = rng.choice(turns_pool)
    csm_line, customer_line = turn_pair
    csm_line = csm_line.format(topic=topic)
    customer_line = customer_line.format(topic=topic)

    transcript_text = (
        f"{csm_name}: {csm_line}\n"
        f"{contact_name}: {customer_line}"
    )

    return summary + "\n\n---\n\n" + transcript_text


def _build_granola_transcript(
    rng: random.Random,
    csm_name: str,
    contact_name: str,
    topic: str,
    tone: str,
) -> list[dict]:
    """Build a Granola-shaped transcript list."""
    turns_pool = _TRANSCRIPT_TURNS.get(tone, _TRANSCRIPT_TURNS["neutral"])
    turn_pair = rng.choice(turns_pool)
    csm_line, customer_line = turn_pair
    csm_line = csm_line.format(topic=topic)
    customer_line = customer_line.format(topic=topic)

    return [
        {"speaker": {"name": csm_name, "source": "microphone"}, "text": csm_line},
        {"speaker": {"name": contact_name, "source": "speaker"}, "text": customer_line},
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_meeting_note_payload(
    spec: SignalSpec,
    rng: random.Random,
    now: datetime,
    signal_index: int,
    scenario_name: str,
    account_name: str,
    primary_domain: str,
    signal_index_within_spec: int = 0,
) -> dict:
    """Generate a single Granola-shaped note dict.

    The returned dict matches the shape of ``GET /v1/notes/{id}`` and can be
    passed directly to ``parse_granola_note`` to produce a
    ``StructuredSignalInput``.

    Args:
        spec:                    SignalSpec driving this signal's axes.
        rng:                     Seeded Random instance.
        now:                     Timestamp for this signal — no datetime.now().
        signal_index:            Zero-based index across the full scenario; used for uuid5.
        scenario_name:           Used to derive the deterministic note id.
        account_name:            Human-readable account name for title / summary substitution.
        primary_domain:          Customer domain (drives participant email).
        signal_index_within_spec: Position within this spec; drives tone drift.

    Returns:
        dict — Granola note shape ready for ``parse_granola_note``.
    """
    axes = spec.axes

    # --- Deterministic IDs ---
    note_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{scenario_name}:note:{signal_index}")
    note_id = f"not_{note_uuid.hex}"

    # --- Timestamps ---
    created_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")

    # --- Owner (customer-side, for routing) ---
    # In real Granola usage the note owner is the CSM (the person whose Mac was
    # recording). For synthesis we model the customer-hosted variant: the customer's
    # rep initiated the call (e.g. customer demoing, customer-led onboarding session).
    # This makes owner.email land on the customer's primary_domain, which is what the
    # production adapter routes on (parse_granola_note → auto_discovery via owner
    # email domain match). ADR-020 open question 3 carries the limitation forward.
    first = rng.choice(_FIRST_NAMES)
    last = rng.choice(_LAST_NAMES)
    contact_name = f"{first} {last}"
    contact_email = f"{first.lower()}.{last.lower()}@{primary_domain}"

    # CSM still picked for transcript content, but is not the owner.
    csm_name = rng.choice(_CSM_NAMES)
    # --- Topic ---
    ticket_topics = [
        "product adoption", "platform stability", "integration scope",
        "contract terms", "feature roadmap", "Q3 goals", "renewal terms",
        "onboarding progress", "expansion planning", "engineering handoff",
    ]
    topic = rng.choice(ticket_topics)

    # --- Title ---
    concern_topic = getattr(axes, "concern_topic", "none")
    title_templates = _MEETING_TITLE_TEMPLATES.get(concern_topic, _MEETING_TITLE_TEMPLATES["none"])
    title = rng.choice(title_templates).format(
        account_name=account_name,
        topic=topic,
        date_str=date_str,
    )

    # --- Tone ---
    tone = _resolve_meeting_tone(rng, axes, signal_index_within_spec)

    # --- Include transcript based on message_length ---
    include_transcript = axes.message_length in ("multi", "chain", "paragraph")

    # --- Summary / body ---
    summary = _build_meeting_body(
        rng, axes, csm_name, contact_name, account_name, topic, tone, include_transcript=False
    )

    # --- Transcript (optional; always present for paragraph/multi/chain for richer signals) ---
    transcript: list[dict] | None = None
    if include_transcript:
        transcript = _build_granola_transcript(rng, csm_name, contact_name, topic, tone)

    # Granola note shape (matches parse_granola_note's expected keys)
    return {
        "id": note_id,
        "title": title,
        "createdAt": created_at,
        "owner": {
            "name": contact_name,
            "email": contact_email,
        },
        "summary": summary,
        "transcript": transcript,
    }
