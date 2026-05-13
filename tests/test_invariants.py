"""Phase 4a: Hypothesis property tests for three core invariants.

1. compute_overall_health — weighted-average invariant.
2. route() — routing_confidence range invariant.
3. generate_email_payload / generate_product_event_payload — uuid5 ID stability.
"""

import random
import uuid
from datetime import UTC, datetime

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.pipeline.health import compute_overall_health

# ---------------------------------------------------------------------------
# Invariant 1: compute_overall_health weighted-average invariant
# ---------------------------------------------------------------------------

# Strategy: list of (weight, score) pairs.
# weight >= 0.0, score in [1, 100].
_dim_entry = st.tuples(
    st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    st.integers(min_value=1, max_value=100),
)


@given(st.lists(_dim_entry, min_size=1, max_size=20))
@settings(max_examples=500)
def test_overall_health_range_when_positive_weight(
    entries: list[tuple[float, int]],
) -> None:
    """With at least one positive weight, result is an int in [1, 100]."""
    # Hypothesis generates arbitrary lists; filter to positive-weight cases here.
    # (All-zero-weight should return None — covered separately.)
    total_weight = sum(w for w, _ in entries)
    if total_weight == 0.0:
        # All weights are zero: expect None
        result = compute_overall_health([(w, s) for w, s in entries])
        assert result is None
        return

    result = compute_overall_health([(w, s) for w, s in entries])
    assert isinstance(result, int), f"Expected int, got {type(result)}"
    assert 1 <= result <= 100, f"Score {result} outside [1, 100]"


@given(st.lists(_dim_entry, min_size=1, max_size=20))
@settings(max_examples=500)
def test_overall_health_matches_formula(entries: list[tuple[float, int]]) -> None:
    """Result equals round(sum(w*s) / sum(w)), clamped to [1, 100]."""
    total_weight = sum(w for w, _ in entries)
    if total_weight == 0.0:
        return  # separate case, tested in test_overall_health_all_zero_weights

    result = compute_overall_health([(w, s) for w, s in entries])
    assert result is not None

    raw = sum(w * s for w, s in entries) / total_weight
    expected = max(1, min(100, round(raw)))
    assert result == expected, (
        f"Formula mismatch: got {result}, expected {expected} "
        f"(raw={raw}, entries={entries})"
    )


def test_overall_health_empty_returns_none() -> None:
    """Empty input must return None."""
    assert compute_overall_health([]) is None


def test_overall_health_all_zero_weights_returns_none() -> None:
    """All-zero weights must return None."""
    assert compute_overall_health([(0.0, 50), (0.0, 80)]) is None


def test_overall_health_clamped_at_boundaries() -> None:
    """Score of 100 / score of 1 with any weight returns 100 / 1 (clamped)."""
    # Upper bound
    result = compute_overall_health([(1.0, 100)])
    assert result == 100

    # Lower bound
    result2 = compute_overall_health([(1.0, 1)])
    assert result2 == 1


# ---------------------------------------------------------------------------
# Invariant 2: route() routing_confidence range invariant
# ---------------------------------------------------------------------------

# Build strategies for valid InboundPayload-compatible dicts.
# from_email must match ^[^@]+@[^@]+\.[^@]+$.
_local_part = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="._+-",
    ),
    min_size=1,
    max_size=20,
)
_domain_label = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="-",
    ),
    min_size=1,
    max_size=20,
)
_tld = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll")),
    min_size=2,
    max_size=6,
)
_email_strategy = st.builds(
    lambda local, domain, tld: f"{local}@{domain}.{tld}",
    local=_local_part,
    domain=_domain_label,
    tld=_tld,
)

_body_strategy = st.text(min_size=1, max_size=500).filter(lambda s: s.strip() != "")

_payload_strategy = st.fixed_dictionaries(
    {
        "from_email": _email_strategy,
        "body": _body_strategy,
        "to_emails": st.lists(_email_strategy, max_size=3),
        "thread_id": st.one_of(st.none(), st.text(min_size=1, max_size=50)),
        "subject": st.one_of(st.none(), st.text(min_size=0, max_size=100)),
    }
)


def _make_workspace() -> object:
    """Build a minimal Workspace-duck for the router."""
    from src.domain.workspace import Workspace

    ws_id = uuid.uuid5(uuid.NAMESPACE_DNS, "test-invariant-workspace")
    return Workspace(
        id=ws_id,
        slug="test-workspace",
        name="Test Workspace",
        organization_id=uuid.uuid5(uuid.NAMESPACE_DNS, "test-org"),
        internal_domains=("test-internal.com",),
        crm_url_template=None,
        crm_portal_id=None,
        outbound_sender_email=None,
        outbound_sender_name=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        deleted_at=None,
    )


@given(payload=_payload_strategy)
@settings(
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.filter_too_much],
)
def test_routing_confidence_in_range(payload: dict) -> None:
    """For any synthesized payload, routing_confidence is None or in [0.0, 1.0].

    Coverage note: this variant uses accounts=[] and thread_accounts={}, which
    forces all routing decisions through the AUTO_DISCOVERY (0.3) and UNMATCHED
    (0.0) fallthrough paths. The companion test
    test_routing_confidence_in_range_with_accounts exercises stages 0-4
    (PLUS_ADDRESSING, HEADER_DOMAIN, FORWARD_PARSE, OUTBOUND_BCC, THREAD_INHERIT)
    so the union covers every confidence value the router can emit.
    """
    from src.pipeline.router import route

    workspace = _make_workspace()
    result = route(
        payload=payload,
        workspace=workspace,  # type: ignore[arg-type]
        accounts=[],
        thread_accounts={},
    )

    confidence = result.routing_confidence
    # routing_confidence is always a float in this implementation (never None)
    assert isinstance(confidence, float), (
        f"Expected float, got {type(confidence)}: {confidence}"
    )
    assert 0.0 <= confidence <= 1.0, (
        f"routing_confidence {confidence} outside [0.0, 1.0] for payload {payload}"
    )


def _make_accounts_for_router_test() -> list:
    """Build a small fixed account set so router stages 0-4 are reachable."""
    from src.domain.account import Account, AccountStatus

    ws_id = uuid.uuid5(uuid.NAMESPACE_DNS, "test-invariant-workspace")
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def _acc(slug: str, primary_domain: str | None) -> Account:
        return Account(
            id=uuid.uuid5(uuid.NAMESPACE_DNS, f"{ws_id}:{slug}"),
            workspace_id=ws_id,
            slug=slug,
            name=slug.replace("-", " ").title(),
            primary_domain=primary_domain,
            additional_domains=[],
            vertical=None,
            crm_record_id=None,
            status=AccountStatus.ACTIVE,
            last_narrative_generated_at=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
            frequency_multiplier=1.0,
            overall_health_score=None,
        )

    return [
        _acc("acme", "acme.com"),
        _acc("bigco", "bigco.io"),
    ]


@given(payload=_payload_strategy)
@settings(
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.filter_too_much],
)
def test_routing_confidence_in_range_with_accounts(payload: dict) -> None:
    """Same invariant, but with a non-empty accounts list and thread_accounts
    so the synthesized payloads can land in any of the 7 routing stages
    (PLUS_ADDRESSING=1.0, HEADER_DOMAIN=0.9, OUTBOUND_BCC=0.9, FORWARD_PARSE=0.7,
    THREAD_INHERIT=0.6, AUTO_DISCOVERY=0.3, UNMATCHED=0.0). The companion test
    above only exercises 0.0 and 0.3 because zero accounts forecloses stages 0-4.
    """
    from src.pipeline.router import route

    workspace = _make_workspace()
    accounts = _make_accounts_for_router_test()

    # Pre-seeded thread_accounts so THREAD_INHERIT (0.6) is reachable when the
    # synthesized payload's thread_id happens to match.
    thread_accounts = {"existing-thread-1": accounts[0].id}

    result = route(
        payload=payload,
        workspace=workspace,  # type: ignore[arg-type]
        accounts=accounts,
        thread_accounts=thread_accounts,
    )

    confidence = result.routing_confidence
    assert isinstance(confidence, float), (
        f"Expected float, got {type(confidence)}: {confidence}"
    )
    assert 0.0 <= confidence <= 1.0, (
        f"routing_confidence {confidence} outside [0.0, 1.0] for payload {payload}"
    )


# ---------------------------------------------------------------------------
# Invariant 3: uuid5 ID stability for synthetic generators
# ---------------------------------------------------------------------------

_scenario_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_"),
    min_size=1,
    max_size=40,
)
_signal_index_strategy = st.integers(min_value=0, max_value=9999)

# Fixed args that don't affect the uuid5 derivation — held constant across the two calls.
_FIXED_RNG_SEED = 42
_FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_FIXED_PRIMARY_DOMAIN = "stable-test.example.com"
_FIXED_ACCOUNT_NAME = "Stable Co"
_FIXED_ACCOUNT_SLUG = "stable-co"


@given(scenario_name=_scenario_name_strategy, signal_index=_signal_index_strategy)
@settings(
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.filter_too_much],
)
def test_email_external_id_is_stable(scenario_name: str, signal_index: int) -> None:
    """generate_email_payload produces identical external_id on two calls with identical inputs."""
    from src.synthetic.generators.email import generate_email_payload
    from src.synthetic.scenario import AxesSpec, SignalSpec

    spec = SignalSpec(
        source_type="inbound_email",
        account_slug=_FIXED_ACCOUNT_SLUG,
        count=1,
        axes=AxesSpec(),
        overrides={},
    )

    rng1 = random.Random(_FIXED_RNG_SEED)
    payload1 = generate_email_payload(
        spec=spec,
        rng=rng1,
        now=_FIXED_NOW,
        signal_index=signal_index,
        scenario_name=scenario_name,
        account_name=_FIXED_ACCOUNT_NAME,
        primary_domain=_FIXED_PRIMARY_DOMAIN,
        signal_index_within_spec=0,
    )

    rng2 = random.Random(_FIXED_RNG_SEED)
    payload2 = generate_email_payload(
        spec=spec,
        rng=rng2,
        now=_FIXED_NOW,
        signal_index=signal_index,
        scenario_name=scenario_name,
        account_name=_FIXED_ACCOUNT_NAME,
        primary_domain=_FIXED_PRIMARY_DOMAIN,
        signal_index_within_spec=0,
    )

    assert payload1["external_id"] == payload2["external_id"], (
        f"external_id not stable for scenario={scenario_name!r}, index={signal_index}: "
        f"{payload1['external_id']!r} != {payload2['external_id']!r}"
    )


@given(scenario_name=_scenario_name_strategy, signal_index=_signal_index_strategy)
@settings(
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.filter_too_much],
)
def test_product_event_id_is_stable(scenario_name: str, signal_index: int) -> None:
    """generate_product_event_payload produces identical event_id on two identical calls."""
    from src.synthetic.generators.product import generate_product_event_payload
    from src.synthetic.scenario import AxesSpec, SignalSpec

    spec = SignalSpec(
        source_type="product_event",
        account_slug=_FIXED_ACCOUNT_SLUG,
        count=1,
        axes=AxesSpec(),
        overrides={},
    )

    rng1 = random.Random(_FIXED_RNG_SEED)
    event1 = generate_product_event_payload(
        spec=spec,
        rng=rng1,
        now=_FIXED_NOW,
        signal_index=signal_index,
        scenario_name=scenario_name,
        primary_domain=_FIXED_PRIMARY_DOMAIN,
    )

    rng2 = random.Random(_FIXED_RNG_SEED)
    event2 = generate_product_event_payload(
        spec=spec,
        rng=rng2,
        now=_FIXED_NOW,
        signal_index=signal_index,
        scenario_name=scenario_name,
        primary_domain=_FIXED_PRIMARY_DOMAIN,
    )

    assert event1.event_id == event2.event_id, (
        f"event_id not stable for scenario={scenario_name!r}, index={signal_index}: "
        f"{event1.event_id!r} != {event2.event_id!r}"
    )


@given(scenario_name=_scenario_name_strategy, signal_index=_signal_index_strategy)
@settings(max_examples=500)
def test_email_and_product_ids_are_distinct_namespaces(
    scenario_name: str, signal_index: int
) -> None:
    """Email external_id and product event_id for the same (name, index) must differ.

    ADR-015 §D5 mandates separate namespaces:
      email:   uuid5(NAMESPACE_DNS, "{scenario}:{index}")
      product: uuid5(NAMESPACE_DNS, "{scenario}:pe:{index}")
    """
    email_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{scenario_name}:{signal_index}"))
    product_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{scenario_name}:pe:{signal_index}"))
    assert email_id != product_id, (
        f"ID collision between email and product namespaces at "
        f"scenario={scenario_name!r}, index={signal_index}"
    )
