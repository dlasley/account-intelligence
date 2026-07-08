"""
Tests for src/analytics.py — wrapper contract and per-event integration.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from uuid import NAMESPACE_DNS, uuid4, uuid5

# ---------------------------------------------------------------------------
# Helpers shared by integration tests
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path("fixtures/quantas-labs-shaped")
_WS_ID = uuid5(NAMESPACE_DNS, "quantas-labs")
_NOW = datetime.now(UTC)


def _make_account(slug: str):
    from src.domain.account import Account, AccountStatus

    return Account(
        id=uuid5(NAMESPACE_DNS, f"{_WS_ID}:{slug}"),
        workspace_id=_WS_ID,
        slug=slug,
        name=slug.replace("-", " ").title(),
        primary_domain=f"{slug}.com",
        additional_domains=[],
        vertical=None,
        crm_record_id=None,
        status=AccountStatus.ACTIVE,
        last_narrative_generated_at=None,
        created_at=_NOW,
        updated_at=_NOW,
        deleted_at=None,
    )


def _make_mock_anthropic_response(json_payload: dict) -> Mock:
    import anthropic

    content_block = Mock(spec=anthropic.types.TextBlock)
    content_block.text = json.dumps(json_payload)
    usage = Mock()
    usage.input_tokens = 100
    usage.output_tokens = 50
    usage.cache_read_input_tokens = 0
    response = Mock()
    response.content = [content_block]
    response.usage = usage
    return response


# ---------------------------------------------------------------------------
# Wrapper contract tests
# ---------------------------------------------------------------------------


def test_track_disabled_does_not_call_posthog(monkeypatch):
    """POSTHOG_ENABLED=false → track() returns without calling PostHog."""
    monkeypatch.setenv("POSTHOG_ENABLED", "false")

    # Reset module-level singleton so env var is evaluated fresh
    import src.analytics as analytics_mod

    analytics_mod._client = None

    with patch("posthog.Posthog") as mock_cls:
        from src.analytics import track

        track("Test Event", _WS_ID, {})
        mock_cls.assert_not_called()


def test_track_enabled_calls_posthog(monkeypatch):
    """POSTHOG_ENABLED=true → track() calls posthog.capture with correct event."""
    monkeypatch.setenv("POSTHOG_ENABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
    monkeypatch.setenv("APP_ENV", "production")

    import src.analytics as analytics_mod

    analytics_mod._client = None

    mock_client = MagicMock()
    with patch("posthog.Posthog", return_value=mock_client):
        from src.analytics import track

        track("Signal Ingested", _WS_ID, {"source_type": "email"})

    mock_client.capture.assert_called_once()
    _, kwargs = mock_client.capture.call_args
    assert kwargs["event"] == "Signal Ingested"
    assert kwargs["distinct_id"] == f"workspace:{_WS_ID}"


def test_track_always_includes_workspace_id(monkeypatch):
    """track() always merges workspace_id into the properties dict sent to PostHog."""
    monkeypatch.setenv("POSTHOG_ENABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
    monkeypatch.setenv("APP_ENV", "production")

    import src.analytics as analytics_mod

    analytics_mod._client = None

    mock_client = MagicMock()
    with patch("posthog.Posthog", return_value=mock_client):
        from src.analytics import track

        track("Signal Ingested", _WS_ID, {"source_type": "email"})

    _, kwargs = mock_client.capture.call_args
    assert "workspace_id" in kwargs["properties"]
    assert kwargs["properties"]["workspace_id"] == str(_WS_ID)


def test_track_distinct_id_is_workspace_prefixed(monkeypatch):
    """distinct_id is always f'workspace:{workspace_id}' (plus optional [dev] prefix)."""
    monkeypatch.setenv("POSTHOG_ENABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
    monkeypatch.setenv("APP_ENV", "production")

    import src.analytics as analytics_mod

    analytics_mod._client = None

    mock_client = MagicMock()
    with patch("posthog.Posthog", return_value=mock_client):
        from src.analytics import track

        track("Narrative Generated", _WS_ID, {})

    _, kwargs = mock_client.capture.call_args
    assert kwargs["distinct_id"] == f"workspace:{_WS_ID}"


def test_track_dev_prefix_in_non_production(monkeypatch):
    """distinct_id is prefixed with '[dev]' when APP_ENV != 'production'."""
    monkeypatch.setenv("POSTHOG_ENABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
    monkeypatch.setenv("APP_ENV", "staging")

    import src.analytics as analytics_mod

    analytics_mod._client = None

    mock_client = MagicMock()
    with patch("posthog.Posthog", return_value=mock_client):
        from src.analytics import track

        track("Signal Ingested", _WS_ID, {})

    _, kwargs = mock_client.capture.call_args
    assert kwargs["distinct_id"].startswith("[dev]workspace:")


def test_track_fire_and_log_on_posthog_exception(monkeypatch, caplog):
    """PostHog raises → track() does not raise; exception is logged at WARNING."""
    monkeypatch.setenv("POSTHOG_ENABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
    monkeypatch.setenv("APP_ENV", "production")

    import src.analytics as analytics_mod

    analytics_mod._client = None

    mock_client = MagicMock()
    mock_client.capture.side_effect = Exception("network error")
    with patch("posthog.Posthog", return_value=mock_client):
        from src.analytics import track

        with caplog.at_level(logging.WARNING, logger="src.analytics"):
            track("Signal Ingested", _WS_ID, {})  # must not raise

    assert any("analytics.track failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Per-event integration tests — patch src.analytics.track at call site
# ---------------------------------------------------------------------------


def test_signal_ingested_fires_after_normalize(monkeypatch):
    """Signal Ingested event fires after insert_signal succeeds in normalize()."""
    monkeypatch.setenv("POSTHOG_ENABLED", "true")

    from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
    from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType

    payload = json.dumps(
        {
            "from_email": "user@acme.com",
            "body": "Hello there",
            "occurred_at": "2026-01-01T10:00:00Z",
            "external_id": "ext-001",
            "source_type": "json_fixture",
            "direction": "inbound",
            "channel": "email",
        }
    )
    event = RawInboundEvent(
        id=uuid4(),
        workspace_id=_WS_ID,
        received_at=_NOW,
        source_type=SourceType.JSON_FIXTURE,
        raw_payload=payload,
        parse_status=ParseStatus.PENDING,
        signal_id=None,
        error_detail=None,
        processed_at=None,
    )

    mock_signal = Signal(
        id=uuid4(),
        workspace_id=_WS_ID,
        account_id=None,
        source_type=SourceType.JSON_FIXTURE,
        external_id="ext-001",
        thread_id=None,
        direction=Direction.INBOUND,
        channel=Channel.EMAIL,
        occurred_at=_NOW,
        created_at=_NOW,
        updated_at=_NOW,
        subject=None,
        body="Hello there",
        author_contact_id=None,
        recipient_contact_ids=[],
        routing_method=RoutingMethod.PLUS_ADDRESSING,
        routing_confidence=1.0,
        routing_warning=None,
        deleted_at=None,
    )

    with (
        patch("src.pipeline.normalizer.get_account_by_email_domain", return_value=None),
        patch("src.pipeline.normalizer.upsert_contact", side_effect=lambda _c, contact: contact),
        patch("src.pipeline.normalizer.insert_signal", return_value=(mock_signal, False)),
        patch("src.pipeline.normalizer.insert_audit_event"),
        patch("src.analytics.track") as mock_track,
    ):
        from src.pipeline.normalizer import normalize

        normalize(event, _WS_ID, [], MagicMock())

    mock_track.assert_any_call(
        "Signal Ingested",
        _WS_ID,
        {
            "account_id": str(mock_signal.account_id),
            "routing_method": (
                str(mock_signal.routing_method) if mock_signal.routing_method else None
            ),
            "source_type": str(mock_signal.source_type),
            "direction": str(mock_signal.direction),
        },
    )


def test_signal_unmatched_fires_when_routed_to_unmatched_account(monkeypatch):
    """Signal Unmatched fires only when actually routed to the _unmatched catch-all,
    not when the signal is truly orphan (no _unmatched account in the workspace).
    Per code-review fix to align with the brief's trigger ("Signal routed to unmatched")."""
    monkeypatch.setenv("POSTHOG_ENABLED", "true")

    from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
    from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType
    from src.domain.workspace import Workspace

    payload = json.dumps(
        {
            "from_email": "unknown@nowhere.com",
            "body": "test body",
            "occurred_at": "2026-01-01T10:00:00Z",
            "external_id": "ext-unmatched",
            "source_type": "json_fixture",
            "direction": "inbound",
            "channel": "email",
        }
    )
    event = RawInboundEvent(
        id=uuid4(),
        workspace_id=_WS_ID,
        received_at=_NOW,
        source_type=SourceType.JSON_FIXTURE,
        raw_payload=payload,
        parse_status=ParseStatus.PENDING,
        signal_id=None,
        error_detail=None,
        processed_at=None,
    )

    mock_signal = Signal(
        id=uuid4(),
        workspace_id=_WS_ID,
        account_id=None,
        source_type=SourceType.JSON_FIXTURE,
        external_id="ext-unmatched",
        thread_id=None,
        direction=Direction.INBOUND,
        channel=Channel.EMAIL,
        occurred_at=_NOW,
        created_at=_NOW,
        updated_at=_NOW,
        subject=None,
        body="test body",
        author_contact_id=None,
        recipient_contact_ids=[],
        routing_method=None,
        routing_confidence=None,
        routing_warning=None,
        deleted_at=None,
    )

    workspace = Workspace(
        id=_WS_ID,
        organization_id=uuid4(),
        slug="test-ws",
        name="Test",
        internal_domains=(),
        crm_url_template=None,
        crm_portal_id=None,
        outbound_sender_email=None,
        outbound_sender_name=None,
        created_at=_NOW,
        updated_at=_NOW,
        deleted_at=None,
    )

    from src.pipeline.router import RoutingResult

    unmatched_routing = RoutingResult(
        account_id=None,
        routing_method=RoutingMethod.UNMATCHED,
        routing_confidence=0.0,
        routing_warning=None,
        new_candidate=None,
    )

    # Build an _unmatched pseudo-account so the catch-all path is exercised
    from src.domain.account import Account, AccountStatus

    unmatched_account = Account(
        id=uuid4(),
        workspace_id=_WS_ID,
        slug="_unmatched",
        name="Unmatched",
        primary_domain=None,
        additional_domains=[],
        vertical=None,
        crm_record_id=None,
        status=AccountStatus.ACTIVE,
        last_narrative_generated_at=None,
        created_at=_NOW,
        updated_at=_NOW,
        deleted_at=None,
        frequency_multiplier=1.0,
        overall_health_score=None,
    )

    with (
        patch("src.pipeline.run.normalize") as mock_normalize,
        patch("src.pipeline.run.mark_processed"),
        patch("src.pipeline.run.get_signals_by_thread_id", return_value=[]),
        patch("src.pipeline.run.route", return_value=unmatched_routing),
        patch("src.pipeline.run.update_signal_routing"),
        patch("src.pipeline.run.schedule_regen"),
        patch("src.pipeline.run.insert_audit_event"),
        patch("src.analytics.track") as mock_track,
    ):
        mock_normalize.return_value = type(
            "R",
            (),
            {"signal": mock_signal, "author_contact": None, "recipient_contacts": []},
        )()

        from src.pipeline.run import process_event

        # _unmatched IS in the accounts list, so the signal gets routed to it
        process_event(event, workspace, [unmatched_account], MagicMock())

    # Signal Unmatched should fire because the signal was routed to _unmatched
    event_names = [c.args[0] for c in mock_track.call_args_list]
    assert "Signal Unmatched" in event_names


def test_signal_unmatched_does_not_fire_when_truly_orphan(monkeypatch):
    """Per code-review fix: when routing.account_id is None AND no _unmatched account
    exists in the workspace, the signal stays orphan and Signal Unmatched does NOT fire.
    This matches the brief's trigger semantic ('routed to unmatched', not 'no account at all')."""
    monkeypatch.setenv("POSTHOG_ENABLED", "true")

    from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
    from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType
    from src.domain.workspace import Workspace

    payload = json.dumps(
        {
            "from_email": "unknown@nowhere.com",
            "body": "test body",
            "occurred_at": "2026-01-01T10:00:00Z",
            "external_id": "ext-orphan",
            "source_type": "json_fixture",
            "direction": "inbound",
            "channel": "email",
        }
    )
    event = RawInboundEvent(
        id=uuid4(),
        workspace_id=_WS_ID,
        received_at=_NOW,
        source_type=SourceType.JSON_FIXTURE,
        raw_payload=payload,
        parse_status=ParseStatus.PENDING,
        signal_id=None,
        error_detail=None,
        processed_at=None,
    )
    mock_signal = Signal(
        id=uuid4(),
        workspace_id=_WS_ID,
        account_id=None,
        source_type=SourceType.JSON_FIXTURE,
        external_id="ext-orphan",
        thread_id=None,
        direction=Direction.INBOUND,
        channel=Channel.EMAIL,
        occurred_at=_NOW,
        created_at=_NOW,
        updated_at=_NOW,
        subject=None,
        body="test body",
        author_contact_id=None,
        recipient_contact_ids=[],
        routing_method=None,
        routing_confidence=None,
        routing_warning=None,
        deleted_at=None,
    )
    workspace = Workspace(
        id=_WS_ID,
        organization_id=uuid4(),
        slug="test-ws",
        name="Test",
        internal_domains=(),
        crm_url_template=None,
        crm_portal_id=None,
        outbound_sender_email=None,
        outbound_sender_name=None,
        created_at=_NOW,
        updated_at=_NOW,
        deleted_at=None,
    )

    from src.pipeline.router import RoutingResult

    orphan_routing = RoutingResult(
        account_id=None,
        routing_method=RoutingMethod.UNMATCHED,
        routing_confidence=0.0,
        routing_warning=None,
        new_candidate=None,
    )

    with (
        patch("src.pipeline.run.normalize") as mock_normalize,
        patch("src.pipeline.run.mark_processed"),
        patch("src.pipeline.run.get_signals_by_thread_id", return_value=[]),
        patch("src.pipeline.run.route", return_value=orphan_routing),
        patch("src.pipeline.run.update_signal_routing"),
        patch("src.pipeline.run.schedule_regen"),
        patch("src.pipeline.run.insert_audit_event"),
        patch("src.analytics.track") as mock_track,
    ):
        mock_normalize.return_value = type(
            "R",
            (),
            {"signal": mock_signal, "author_contact": None, "recipient_contacts": []},
        )()

        from src.pipeline.run import process_event

        # No _unmatched in the accounts list — signal stays truly orphan
        process_event(event, workspace, [], MagicMock())

    # Signal Unmatched should NOT have been called
    event_names = [c.args[0] for c in mock_track.call_args_list]
    assert "Signal Unmatched" not in event_names


def test_narrative_generated_fires_after_db_write(monkeypatch):
    """Narrative Generated fires after insert_narrative succeeds."""
    monkeypatch.setenv("POSTHOG_ENABLED", "true")

    from src.config.loader import load_config

    config = load_config("quantas-labs")
    account = _make_account("test-account")
    mock_db = MagicMock()
    mock_anthropic = Mock()
    mock_anthropic.messages.create.return_value = _make_mock_anthropic_response(
        {
            "narrative": "Test narrative text.",
            "sentiment": 72,
            "notable_events": [],
            "risks": [],
            "opportunities": [],
            "suggested_next_action": None,
        }
    )

    with (
        patch("src.pipeline.generator.supersede_current_narrative"),
        patch("src.pipeline.generator.insert_narrative", side_effect=lambda _c, n: n),
        patch("src.pipeline.generator.update_account_last_generated"),
        patch("src.pipeline.generator.insert_audit_event"),
        patch("src.pipeline.generator.get_dimension_configs", return_value=[]),
        patch("src.pipeline.generator._score_and_snapshot"),
        patch("src.analytics.track") as mock_track,
    ):
        from src.pipeline.generator import generate_narrative

        generate_narrative(
            account=account,
            signals=[],
            contacts={},
            prior_narrative=None,
            config=config,
            workspace_slug="quantas-labs",
            client_db=mock_db,
            client_anthropic=mock_anthropic,
        )

    event_names = [c.args[0] for c in mock_track.call_args_list]
    assert "Narrative Generated" in event_names
    narrative_call = next(
        c for c in mock_track.call_args_list if c.args[0] == "Narrative Generated"
    )
    props = narrative_call.args[2]
    assert "model" in props
    assert "sentiment" in props
    assert "engagement" in props
    assert "signal_count" in props
    assert "cached_tokens" in props


def test_account_health_score_changed_fires(monkeypatch):
    """Account Health Score Changed fires when update_account_overall_health is called."""
    monkeypatch.setenv("POSTHOG_ENABLED", "true")

    account_id = uuid4()
    workspace_id = uuid4()
    mock_client = MagicMock()
    mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
        {"id": str(account_id), "workspace_id": str(workspace_id)}
    ]

    with patch("src.analytics.track") as mock_track:
        from src.db.accounts import update_account_overall_health

        update_account_overall_health(mock_client, account_id, 75)

    event_names = [c.args[0] for c in mock_track.call_args_list]
    assert "Account Health Score Changed" in event_names
    score_call = next(
        c for c in mock_track.call_args_list if c.args[0] == "Account Health Score Changed"
    )
    props = score_call.args[2]
    assert props["new_score"] == 75
    assert "account_id" in props
    assert "triggered_by" in props


# ---------------------------------------------------------------------------
# Fire-and-log contract regression — analytics error must not break pipeline
# ---------------------------------------------------------------------------


def test_normalize_does_not_raise_when_posthog_client_fails(monkeypatch):
    """PostHog client raises inside track() → normalize() still completes (fire-and-log)."""
    monkeypatch.setenv("POSTHOG_ENABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")

    # Reset singleton so a fresh client is created
    import src.analytics as analytics_mod

    analytics_mod._client = None

    from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
    from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType

    payload = json.dumps(
        {
            "from_email": "user@acme.com",
            "body": "Hello there",
            "occurred_at": "2026-01-01T10:00:00Z",
            "external_id": "ext-002",
            "source_type": "json_fixture",
            "direction": "inbound",
            "channel": "email",
        }
    )
    event = RawInboundEvent(
        id=uuid4(),
        workspace_id=_WS_ID,
        received_at=_NOW,
        source_type=SourceType.JSON_FIXTURE,
        raw_payload=payload,
        parse_status=ParseStatus.PENDING,
        signal_id=None,
        error_detail=None,
        processed_at=None,
    )
    mock_signal = Signal(
        id=uuid4(),
        workspace_id=_WS_ID,
        account_id=None,
        source_type=SourceType.JSON_FIXTURE,
        external_id="ext-002",
        thread_id=None,
        direction=Direction.INBOUND,
        channel=Channel.EMAIL,
        occurred_at=_NOW,
        created_at=_NOW,
        updated_at=_NOW,
        subject=None,
        body="Hello there",
        author_contact_id=None,
        recipient_contact_ids=[],
        routing_method=RoutingMethod.PLUS_ADDRESSING,
        routing_confidence=1.0,
        routing_warning=None,
        deleted_at=None,
    )

    mock_posthog_client = MagicMock()
    mock_posthog_client.capture.side_effect = Exception("network error")

    with (
        patch("posthog.Posthog", return_value=mock_posthog_client),
        patch("src.pipeline.normalizer.get_account_by_email_domain", return_value=None),
        patch("src.pipeline.normalizer.upsert_contact", side_effect=lambda _c, contact: contact),
        patch("src.pipeline.normalizer.insert_signal", return_value=(mock_signal, False)),
        patch("src.pipeline.normalizer.insert_audit_event"),
    ):
        from src.pipeline.normalizer import normalize

        # Must not raise even though posthog.capture raises — fire-and-log in analytics.track
        result = normalize(event, _WS_ID, [], MagicMock())

    assert result.signal.external_id == "ext-002"


def test_generate_narrative_does_not_raise_when_analytics_fails(monkeypatch):
    """analytics.track raises → generate_narrative() still returns result (fire-and-log)."""
    monkeypatch.setenv("POSTHOG_ENABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")

    import src.analytics as analytics_mod

    analytics_mod._client = None

    from src.config.loader import load_config

    config = load_config("quantas-labs")
    account = _make_account("test-account")
    mock_db = MagicMock()
    mock_anthropic = Mock()
    mock_anthropic.messages.create.return_value = _make_mock_anthropic_response(
        {
            "narrative": "Test narrative text.",
            "sentiment": 72,
            "notable_events": [],
            "risks": [],
            "opportunities": [],
            "suggested_next_action": None,
        }
    )

    mock_posthog_client = MagicMock()
    mock_posthog_client.capture.side_effect = Exception("network error")

    with (
        patch("posthog.Posthog", return_value=mock_posthog_client),
        patch("src.pipeline.generator.supersede_current_narrative"),
        patch("src.pipeline.generator.insert_narrative", side_effect=lambda _c, n: n),
        patch("src.pipeline.generator.update_account_last_generated"),
        patch("src.pipeline.generator.insert_audit_event"),
        patch("src.pipeline.generator.get_dimension_configs", return_value=[]),
        patch("src.pipeline.generator._score_and_snapshot"),
    ):
        from src.pipeline.generator import generate_narrative

        # Must not raise — generator.py wraps analytics.track in try/except
        result = generate_narrative(
            account=account,
            signals=[],
            contacts={},
            prior_narrative=None,
            config=config,
            workspace_slug="quantas-labs",
            client_db=mock_db,
            client_anthropic=mock_anthropic,
        )

    assert result.narrative.narrative == "Test narrative text."
