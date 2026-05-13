"""
Generator tests.
- Unit tests (sentiment clamping, missing key, scoring): no API key required, always run.
- Integration test (test_generate_formation_bio): requires ANTHROPIC_API_KEY, skipped in CI.
"""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from uuid import NAMESPACE_DNS, uuid4, uuid5

import anthropic
import pytest

from src.config.loader import load_config
from src.domain.account import Account, AccountStatus, Vertical
from src.domain.contact import Contact
from src.domain.signal import Channel, Direction, Signal, SourceType

FIXTURE_DIR = Path("fixtures/elicit-shaped")
_WS_ID = uuid5(NAMESPACE_DNS, "elicit")
_NOW = datetime.now(UTC)


def _make_account(slug: str, vertical: Vertical | None = None) -> Account:
    return Account(
        id=uuid5(NAMESPACE_DNS, f"{_WS_ID}:{slug}"),
        workspace_id=_WS_ID,
        slug=slug,
        name=slug.replace("-", " ").title(),
        primary_domain=f"{slug}.com",
        additional_domains=[],
        vertical=vertical,
        crm_record_id=None,
        status=AccountStatus.ACTIVE,
        last_narrative_generated_at=None,
        created_at=_NOW,
        updated_at=_NOW,
        deleted_at=None,
    )


def _load_fixture_signals(account_slug: str) -> tuple[list[Signal], dict]:
    """Load signals from fixture files and build Contact objects from from_email."""
    signals_dir = FIXTURE_DIR / "signals" / account_slug
    signals: list[Signal] = []
    contacts: dict = {}

    for path in sorted(signals_dir.glob("*.json")):
        payload = json.loads(path.read_text())
        from_email = payload["from_email"]
        contact_id = uuid5(NAMESPACE_DNS, f"{_WS_ID}:{from_email}")
        occurred = datetime.fromisoformat(payload["occurred_at"])

        if contact_id not in contacts:
            contacts[contact_id] = Contact(
                id=contact_id,
                workspace_id=_WS_ID,
                account_id=None,
                email=from_email,
                display_name=payload.get("from_name"),
                is_internal=from_email.endswith("@elicit.org"),
                created_at=_NOW,
                updated_at=_NOW,
                deleted_at=None,
            )

        signals.append(
            Signal(
                id=uuid4(),
                workspace_id=_WS_ID,
                account_id=uuid5(NAMESPACE_DNS, f"{_WS_ID}:{account_slug}"),
                source_type=SourceType.JSON_FIXTURE,
                external_id=payload["external_id"],
                thread_id=payload.get("thread_id"),
                direction=Direction(payload["direction"]),
                channel=Channel(payload["channel"]),
                occurred_at=occurred,
                created_at=occurred,
                updated_at=occurred,
                subject=payload.get("subject"),
                body=payload["body"],
                author_contact_id=contact_id,
                recipient_contact_ids=[],
                routing_method=None,
                routing_confidence=None,
                routing_warning=None,
                deleted_at=None,
            )
        )
    return signals, contacts


def _make_mock_db(account_id, config) -> MagicMock:
    mock_db = MagicMock()
    mock_db.table.return_value.insert.return_value.execute.return_value.data = [
        {
            "id": str(uuid4()),
            "workspace_id": str(_WS_ID),
            "account_id": str(account_id),
            "narrative": "placeholder",
            "engagement": 90,
            "engagement_rationale": "test",
            "sentiment": 72,
            "signal_window_start": _NOW.isoformat(),
            "signal_window_end": _NOW.isoformat(),
            "signals_considered": [],
            "model": config.narrative_generation.model,
            "prompt_version": "test",
            "generated_at": _NOW.isoformat(),
            "superseded_at": None,
        }
    ]
    mock_db.table.return_value.update.return_value.eq.return_value.is_.return_value.execute.return_value.data = []  # noqa: E501
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []
    return mock_db


def _make_mock_anthropic_response(json_payload: dict) -> Mock:
    """Return a mock Anthropic response object with the given payload as content."""
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
# Unit tests — no API key required
# ---------------------------------------------------------------------------


def test_sentiment_clamped_above_100(caplog):
    """LLM returns sentiment=105 → stored as 100, warning is logged."""
    from src.pipeline.generator import generate_narrative

    config = load_config("elicit")
    account = _make_account("test-account")
    mock_db = _make_mock_db(account.id, config)
    mock_anthropic = Mock()
    mock_anthropic.messages.create.return_value = _make_mock_anthropic_response(
        {
            "narrative": "Test narrative text.",
            "sentiment": 105,
            "notable_events": [],
            "risks": [],
            "opportunities": [],
            "suggested_next_action": None,
        }
    )

    with caplog.at_level(logging.WARNING, logger="src.pipeline.generator"):
        with (
            patch("src.pipeline.generator.supersede_current_narrative"),
            patch("src.pipeline.generator.insert_narrative", side_effect=lambda _c, n: n),
            patch("src.pipeline.generator.update_account_last_generated"),
            patch("src.pipeline.generator.insert_audit_event"),
            patch("src.pipeline.generator.get_dimension_configs", return_value=[]),
            patch("src.pipeline.generator._score_and_snapshot"),
        ):
            result = generate_narrative(
                account=account,
                signals=[],
                contacts={},
                prior_narrative=None,
                config=config,
                workspace_slug="elicit",
                client_db=mock_db,
                client_anthropic=mock_anthropic,
            )

    assert result.narrative.sentiment == 100
    assert any("Sentiment out of range" in r.message for r in caplog.records)


def test_sentiment_clamped_below_one(caplog):
    """LLM returns sentiment=0 → stored as 1, warning is logged."""
    from src.pipeline.generator import generate_narrative

    config = load_config("elicit")
    account = _make_account("test-account")
    mock_db = _make_mock_db(account.id, config)
    mock_anthropic = Mock()
    mock_anthropic.messages.create.return_value = _make_mock_anthropic_response(
        {
            "narrative": "Test narrative text.",
            "sentiment": 0,
            "notable_events": [],
            "risks": [],
            "opportunities": [],
            "suggested_next_action": None,
        }
    )

    with caplog.at_level(logging.WARNING, logger="src.pipeline.generator"):
        with (
            patch("src.pipeline.generator.supersede_current_narrative"),
            patch("src.pipeline.generator.insert_narrative", side_effect=lambda _c, n: n),
            patch("src.pipeline.generator.update_account_last_generated"),
            patch("src.pipeline.generator.insert_audit_event"),
            patch("src.pipeline.generator.get_dimension_configs", return_value=[]),
            patch("src.pipeline.generator._score_and_snapshot"),
        ):
            result = generate_narrative(
                account=account,
                signals=[],
                contacts={},
                prior_narrative=None,
                config=config,
                workspace_slug="elicit",
                client_db=mock_db,
                client_anthropic=mock_anthropic,
            )

    assert result.narrative.sentiment == 1
    assert any("Sentiment out of range" in r.message for r in caplog.records)


def test_sentiment_string_becomes_none():
    """LLM returns sentiment as a string → treated as wrong type, stored as None."""
    from src.pipeline.generator import generate_narrative

    config = load_config("elicit")
    account = _make_account("test-account")
    mock_db = _make_mock_db(account.id, config)
    mock_anthropic = Mock()
    mock_anthropic.messages.create.return_value = _make_mock_anthropic_response(
        {
            "narrative": "Test narrative text.",
            "sentiment": "72",
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
    ):
        result = generate_narrative(
            account=account,
            signals=[],
            contacts={},
            prior_narrative=None,
            config=config,
            workspace_slug="elicit",
            client_db=mock_db,
            client_anthropic=mock_anthropic,
        )

    assert result.narrative.sentiment is None


def test_sentiment_missing_from_response():
    """LLM omits sentiment key → stored as None."""
    from src.pipeline.generator import generate_narrative

    config = load_config("elicit")
    account = _make_account("test-account")
    mock_db = _make_mock_db(account.id, config)
    mock_anthropic = Mock()
    mock_anthropic.messages.create.return_value = _make_mock_anthropic_response(
        {
            "narrative": "Test narrative text.",
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
    ):
        result = generate_narrative(
            account=account,
            signals=[],
            contacts={},
            prior_narrative=None,
            config=config,
            workspace_slug="elicit",
            client_db=mock_db,
            client_anthropic=mock_anthropic,
        )

    assert result.narrative.sentiment is None


def test_score_and_snapshot_called_once(caplog):
    """generate_narrative calls _score_and_snapshot exactly once after insert."""
    from src.pipeline.generator import generate_narrative

    config = load_config("elicit")
    account = _make_account("test-account")
    mock_db = _make_mock_db(account.id, config)
    mock_anthropic = Mock()
    mock_anthropic.messages.create.return_value = _make_mock_anthropic_response(
        {
            "narrative": "Test narrative text.",
            "sentiment": 75,
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
        patch("src.pipeline.generator._score_and_snapshot") as mock_score,
    ):
        generate_narrative(
            account=account,
            signals=[],
            contacts={},
            prior_narrative=None,
            config=config,
            workspace_slug="elicit",
            client_db=mock_db,
            client_anthropic=mock_anthropic,
        )

    assert mock_score.call_count == 1


def test_score_and_snapshot_composite_no_sentiment_warns(caplog):
    """_score_and_snapshot with composite score_from and no sentiment logs warning, no DB write."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from src.domain.dimension_config import DimensionConfig
    from src.domain.narrative import Narrative
    from src.pipeline.generator import _score_and_snapshot

    dim_cfg = DimensionConfig(
        id=uuid4(),
        workspace_id=uuid4(),
        dimension_type="email",
        name="Email Health",
        weight=0.7,
        enabled=True,
        config={"email_score_source": "composite"},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        deleted_at=None,
    )
    now = datetime.now(UTC)
    acc_id = uuid4()
    account = _make_account("test")

    narrative = Narrative(
        id=uuid4(),
        workspace_id=dim_cfg.workspace_id,
        account_id=acc_id,
        narrative="Test.",
        engagement=70,
        engagement_rationale="2 signals in 30 days.",
        sentiment=None,
        signal_window_start=now,
        signal_window_end=now,
        signals_considered=(),
        model="claude-opus-4-7",
        prompt_version="abc12345",
        generated_at=now,
        superseded_at=None,
    )

    mock_db = MagicMock()

    with (
        patch("src.pipeline.generator.supersede_dimension_score") as mock_sup,
        patch("src.pipeline.generator.insert_dimension_score") as mock_ins,
        caplog.at_level(logging.WARNING, logger="src.pipeline.generator"),
    ):
        _score_and_snapshot(narrative, account, [], [dim_cfg], mock_db, now)

    mock_sup.assert_not_called()
    mock_ins.assert_not_called()
    assert any("sentiment not available" in r.message for r in caplog.records)


def test_score_and_snapshot_writes_sentiment_dimension(caplog):
    """_score_and_snapshot writes email and sentiment dimension scores when sentiment is set."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from src.domain.dimension_config import DimensionConfig
    from src.domain.dimension_score import DimensionScore
    from src.domain.narrative import Narrative
    from src.pipeline.generator import _score_and_snapshot

    now = datetime.now(UTC)
    ws_id = uuid4()
    acc_id = uuid4()

    email_cfg = DimensionConfig(
        id=uuid4(),
        workspace_id=ws_id,
        dimension_type="email",
        name="Email Health",
        weight=0.5,
        enabled=True,
        config={"email_score_source": "engagement"},
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )
    sentiment_cfg = DimensionConfig(
        id=uuid4(),
        workspace_id=ws_id,
        dimension_type="sentiment",
        name="Sentiment",
        weight=0.3,
        enabled=True,
        config={},
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )

    account = _make_account("test")
    account = Account(
        id=acc_id,
        workspace_id=ws_id,
        slug=account.slug,
        name=account.name,
        primary_domain=account.primary_domain,
        additional_domains=account.additional_domains,
        vertical=account.vertical,
        crm_record_id=account.crm_record_id,
        status=account.status,
        last_narrative_generated_at=account.last_narrative_generated_at,
        created_at=account.created_at,
        updated_at=account.updated_at,
        deleted_at=account.deleted_at,
    )

    narrative = Narrative(
        id=uuid4(),
        workspace_id=ws_id,
        account_id=acc_id,
        narrative="Test.",
        engagement=70,
        engagement_rationale="2 signals in 30 days.",
        sentiment=75,
        signal_window_start=now,
        signal_window_end=now,
        signals_considered=(),
        model="claude-opus-4-7",
        prompt_version="abc12345",
        generated_at=now,
        superseded_at=None,
    )

    mock_db = MagicMock()

    with (
        patch("src.pipeline.generator.supersede_dimension_score"),
        patch("src.pipeline.generator.insert_dimension_score") as mock_ins,
        patch("src.pipeline.generator.get_current_scores", return_value=[]),
        patch("src.pipeline.generator.supersede_health_snapshot"),
        patch("src.pipeline.generator.insert_health_snapshot"),
        patch("src.pipeline.generator.update_account_overall_health"),
    ):
        _score_and_snapshot(narrative, account, [], [email_cfg, sentiment_cfg], mock_db, now)

    assert mock_ins.call_count == 2
    calls = mock_ins.call_args_list
    inserted_scores: list[DimensionScore] = [c.args[1] for c in calls]
    dimension_ids = {s.dimension_id for s in inserted_scores}
    assert sentiment_cfg.id in dimension_ids
    sentiment_score_obj = next(s for s in inserted_scores if s.dimension_id == sentiment_cfg.id)
    assert sentiment_score_obj.score == 75


def test_score_and_snapshot_skips_sentiment_when_none(caplog):
    """_score_and_snapshot skips the sentiment dimension write when narrative.sentiment is None."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from src.domain.dimension_config import DimensionConfig
    from src.domain.narrative import Narrative
    from src.pipeline.generator import _score_and_snapshot

    now = datetime.now(UTC)
    ws_id = uuid4()
    acc_id = uuid4()

    email_cfg = DimensionConfig(
        id=uuid4(),
        workspace_id=ws_id,
        dimension_type="email",
        name="Email Health",
        weight=0.5,
        enabled=True,
        config={"email_score_source": "engagement"},
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )
    sentiment_cfg = DimensionConfig(
        id=uuid4(),
        workspace_id=ws_id,
        dimension_type="sentiment",
        name="Sentiment",
        weight=0.3,
        enabled=True,
        config={},
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )

    account = _make_account("test")
    account = Account(
        id=acc_id,
        workspace_id=ws_id,
        slug=account.slug,
        name=account.name,
        primary_domain=account.primary_domain,
        additional_domains=account.additional_domains,
        vertical=account.vertical,
        crm_record_id=account.crm_record_id,
        status=account.status,
        last_narrative_generated_at=account.last_narrative_generated_at,
        created_at=account.created_at,
        updated_at=account.updated_at,
        deleted_at=account.deleted_at,
    )

    narrative = Narrative(
        id=uuid4(),
        workspace_id=ws_id,
        account_id=acc_id,
        narrative="Test.",
        engagement=70,
        engagement_rationale="2 signals in 30 days.",
        sentiment=None,
        signal_window_start=now,
        signal_window_end=now,
        signals_considered=(),
        model="claude-opus-4-7",
        prompt_version="abc12345",
        generated_at=now,
        superseded_at=None,
    )

    mock_db = MagicMock()

    with (
        patch("src.pipeline.generator.supersede_dimension_score"),
        patch("src.pipeline.generator.insert_dimension_score") as mock_ins,
        patch("src.pipeline.generator.get_current_scores", return_value=[]),
        patch("src.pipeline.generator.supersede_health_snapshot"),
        patch("src.pipeline.generator.insert_health_snapshot"),
        patch("src.pipeline.generator.update_account_overall_health"),
        caplog.at_level(logging.WARNING, logger="src.pipeline.generator"),
    ):
        _score_and_snapshot(narrative, account, [], [email_cfg, sentiment_cfg], mock_db, now)

    assert mock_ins.call_count == 1
    assert any("narrative.sentiment is None" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Rendering tests — verify prompt placeholder wiring without API key
# ---------------------------------------------------------------------------


def test_render_valid_contact_list_with_external_contacts():
    """_render_valid_contact_list returns one line per external contact."""
    from src.pipeline.generator import _render_valid_contact_list

    cid1 = uuid4()
    cid2 = uuid4()
    contacts = {
        cid1: Contact(
            id=cid1,
            workspace_id=_WS_ID,
            account_id=None,
            email="alice@customer.com",
            display_name="Alice Example",
            is_internal=False,
            created_at=_NOW,
            updated_at=_NOW,
            deleted_at=None,
        ),
        cid2: Contact(
            id=cid2,
            workspace_id=_WS_ID,
            account_id=None,
            email="bob@internal.com",
            display_name="Bob Internal",
            is_internal=True,
            created_at=_NOW,
            updated_at=_NOW,
            deleted_at=None,
        ),
    }

    result = _render_valid_contact_list(contacts)

    assert "Alice Example <alice@customer.com>" in result
    assert "Bob Internal" not in result  # internal contacts excluded


def test_render_valid_contact_list_all_internal():
    """_render_valid_contact_list returns fallback text when all contacts are internal."""
    from src.pipeline.generator import _render_valid_contact_list

    cid = uuid4()
    contacts = {
        cid: Contact(
            id=cid,
            workspace_id=_WS_ID,
            account_id=None,
            email="worker@internal.com",
            display_name="Internal Worker",
            is_internal=True,
            created_at=_NOW,
            updated_at=_NOW,
            deleted_at=None,
        ),
    }

    result = _render_valid_contact_list(contacts)

    assert result == "No contacts identified."


def test_valid_contact_list_placeholder_populated_in_prompt():
    """generate_narrative fills {{valid_contact_list}} in the rendered user prompt."""
    from src.pipeline.generator import generate_narrative

    config = load_config("elicit")
    account = _make_account("test-account")
    cid = uuid4()
    external_contact = Contact(
        id=cid,
        workspace_id=_WS_ID,
        account_id=account.id,
        email="contact@customer.com",
        display_name="Test Contact",
        is_internal=False,
        created_at=_NOW,
        updated_at=_NOW,
        deleted_at=None,
    )
    mock_db = _make_mock_db(account.id, config)
    captured_prompts: list[dict] = []

    def _capture_create(**kwargs):
        captured_prompts.append(kwargs)
        return _make_mock_anthropic_response(
            {
                "narrative": "Test narrative text.",
                "sentiment": 70,
                "notable_events": [],
                "risks": [],
                "opportunities": [],
                "suggested_next_action": None,
            }
        )

    mock_anthropic = Mock()
    mock_anthropic.messages.create.side_effect = _capture_create

    with (
        patch("src.pipeline.generator.supersede_current_narrative"),
        patch("src.pipeline.generator.insert_narrative", side_effect=lambda _c, n: n),
        patch("src.pipeline.generator.update_account_last_generated"),
        patch("src.pipeline.generator.insert_audit_event"),
        patch("src.pipeline.generator.get_dimension_configs", return_value=[]),
        patch("src.pipeline.generator._score_and_snapshot"),
    ):
        generate_narrative(
            account=account,
            signals=[],
            contacts={cid: external_contact},
            prior_narrative=None,
            config=config,
            workspace_slug="elicit",
            client_db=mock_db,
            client_anthropic=mock_anthropic,
        )

    assert captured_prompts, "Expected messages.create to be called"
    user_prompt = captured_prompts[0]["messages"][0]["content"]
    assert "Test Contact <contact@customer.com>" in user_prompt
    assert "{{valid_contact_list}}" not in user_prompt  # placeholder must be replaced


def test_signal_count_placeholder_populated_in_prompt():
    """generate_narrative fills {{signal_count}} in the rendered user prompt."""
    from src.pipeline.generator import generate_narrative

    config = load_config("elicit")
    account = _make_account("test-account")
    mock_db = _make_mock_db(account.id, config)
    captured_prompts: list[dict] = []

    def _capture_create(**kwargs):
        captured_prompts.append(kwargs)
        return _make_mock_anthropic_response(
            {
                "narrative": "Test narrative text.",
                "sentiment": 70,
                "notable_events": [],
                "risks": [],
                "opportunities": [],
                "suggested_next_action": None,
            }
        )

    mock_anthropic = Mock()
    mock_anthropic.messages.create.side_effect = _capture_create

    with (
        patch("src.pipeline.generator.supersede_current_narrative"),
        patch("src.pipeline.generator.insert_narrative", side_effect=lambda _c, n: n),
        patch("src.pipeline.generator.update_account_last_generated"),
        patch("src.pipeline.generator.insert_audit_event"),
        patch("src.pipeline.generator.get_dimension_configs", return_value=[]),
        patch("src.pipeline.generator._score_and_snapshot"),
    ):
        generate_narrative(
            account=account,
            signals=[],
            contacts={},
            prior_narrative=None,
            config=config,
            workspace_slug="elicit",
            client_db=mock_db,
            client_anthropic=mock_anthropic,
        )

    assert captured_prompts, "Expected messages.create to be called"
    user_prompt = captured_prompts[0]["messages"][0]["content"]
    assert "{{signal_count}}" not in user_prompt  # placeholder must be replaced
    assert "0 in window" in user_prompt  # substituted value present


def test_generate_narrative_max_tokens_at_least_4096():
    """generate_narrative calls messages.create with max_tokens >= 4096.

    The prior 2048 cap caused mid-sentence truncation on the first live simulator
    run; 4096 gives ~3x headroom within Opus 4.6's 8192 output limit.
    """
    from src.pipeline.generator import generate_narrative

    config = load_config("elicit")
    account = _make_account("test-account")
    mock_db = _make_mock_db(account.id, config)
    captured_kwargs: list[dict] = []

    def _capture_create(**kwargs):
        captured_kwargs.append(kwargs)
        return _make_mock_anthropic_response(
            {
                "narrative": "Test narrative text.",
                "sentiment": 70,
                "notable_events": [],
                "risks": [],
                "opportunities": [],
                "suggested_next_action": None,
            }
        )

    mock_anthropic = Mock()
    mock_anthropic.messages.create.side_effect = _capture_create

    with (
        patch("src.pipeline.generator.supersede_current_narrative"),
        patch("src.pipeline.generator.insert_narrative", side_effect=lambda _c, n: n),
        patch("src.pipeline.generator.update_account_last_generated"),
        patch("src.pipeline.generator.insert_audit_event"),
        patch("src.pipeline.generator.get_dimension_configs", return_value=[]),
        patch("src.pipeline.generator._score_and_snapshot"),
    ):
        generate_narrative(
            account=account,
            signals=[],
            contacts={},
            prior_narrative=None,
            config=config,
            workspace_slug="elicit",
            client_db=mock_db,
            client_anthropic=mock_anthropic,
        )

    assert captured_kwargs, "Expected messages.create to be called"
    assert captured_kwargs[0]["max_tokens"] >= 4096, (
        f"max_tokens was {captured_kwargs[0]['max_tokens']}; "
        "must be >= 4096 to avoid mid-sentence truncation on longer narratives"
    )


# ---------------------------------------------------------------------------
# _strip_fences tests
# ---------------------------------------------------------------------------


def test_strip_fences_unchanged_behavior_for_plain_fence():
    """Existing behavior: entire response is a single ```json fence → strip markers."""
    from src.pipeline.generator import _strip_fences

    payload = '{"narrative": "hello"}'
    fenced = f"```json\n{payload}\n```"
    assert _strip_fences(fenced) == payload


def test_strip_fences_handles_prose_preamble_then_fenced_json():
    """New case: prose preamble before fenced JSON → extract fence interior."""
    from src.pipeline.generator import _strip_fences

    payload = '{"narrative": "hello"}'
    text = f"## Self-check\nAll dimensions covered.\n```json\n{payload}\n```"
    assert _strip_fences(text) == payload


def test_strip_fences_handles_prose_then_raw_json():
    """New case: prose preamble before raw JSON (no fence) → trim to first '{'."""
    from src.pipeline.generator import _strip_fences

    payload = '{"narrative": "hello"}'
    text = f"## Self-check\nAll dimensions covered.\n{payload}"
    assert _strip_fences(text) == payload


def test_generate_narrative_retries_once_on_json_decode_error(caplog):
    """On JSONDecodeError, generate_narrative retries once; second valid response is used."""
    from src.pipeline.generator import generate_narrative

    config = load_config("elicit")
    account = _make_account("test-account")
    mock_db = _make_mock_db(account.id, config)

    valid_payload = {
        "narrative": "Recovered narrative text.",
        "sentiment": 65,
        "notable_events": [],
        "risks": [],
        "opportunities": [],
        "suggested_next_action": None,
    }

    # First call returns invalid JSON; second returns valid JSON.
    invalid_response = Mock()
    invalid_block = Mock(spec=anthropic.types.TextBlock)
    invalid_block.text = "not valid json {"
    invalid_response.content = [invalid_block]

    mock_anthropic = Mock()
    mock_anthropic.messages.create.side_effect = [
        invalid_response,
        _make_mock_anthropic_response(valid_payload),
    ]

    with (
        patch("src.pipeline.generator.supersede_current_narrative"),
        patch("src.pipeline.generator.insert_narrative", side_effect=lambda _c, n: n),
        patch("src.pipeline.generator.update_account_last_generated"),
        patch("src.pipeline.generator.insert_audit_event"),
        patch("src.pipeline.generator.get_dimension_configs", return_value=[]),
        patch("src.pipeline.generator._score_and_snapshot"),
        caplog.at_level(logging.WARNING, logger="src.pipeline.generator"),
    ):
        result = generate_narrative(
            account=account,
            signals=[],
            contacts={},
            prior_narrative=None,
            config=config,
            workspace_slug="elicit",
            client_db=mock_db,
            client_anthropic=mock_anthropic,
        )

    assert mock_anthropic.messages.create.call_count == 2, "Expected exactly one retry"
    assert result.narrative.narrative == "Recovered narrative text."
    assert any("attempt 1/2" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Integration test — requires ANTHROPIC_API_KEY
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="requires ANTHROPIC_API_KEY",
)
@pytest.mark.skipif(
    not FIXTURE_DIR.exists(),
    reason="elicit pilot data moved to .private/; not present in tracked tree",
)
def test_generate_formation_bio():
    """Full generation against real Claude API using fixture signals (no Supabase)."""
    import anthropic as anthropic_sdk

    from src.pipeline.generator import generate_narrative

    account = _make_account("formation-bio", Vertical.LIFE_SCIENCES)
    signals, contacts = _load_fixture_signals("formation-bio")
    config = load_config("elicit")
    mock_db = _make_mock_db(account.id, config)

    client_ai = anthropic_sdk.Anthropic()

    with (
        patch("src.pipeline.generator.supersede_current_narrative"),
        patch("src.pipeline.generator.insert_narrative", side_effect=lambda _c, n: n),
        patch("src.pipeline.generator.update_account_last_generated"),
        patch("src.pipeline.generator.insert_audit_event"),
        patch("src.pipeline.generator.get_dimension_configs", return_value=[]),
        patch("src.pipeline.generator._score_and_snapshot"),
    ):
        result = generate_narrative(
            account=account,
            signals=signals,
            contacts=contacts,
            prior_narrative=None,
            config=config,
            workspace_slug="elicit",
            client_db=mock_db,
            client_anthropic=client_ai,
        )

    assert result.narrative.narrative
    assert len(result.narrative.narrative) > 50
    assert isinstance(result.narrative.engagement, int)
    assert 1 <= result.narrative.engagement <= 100
    assert result.narrative.engagement_rationale
    assert result.narrative.sentiment is None or isinstance(result.narrative.sentiment, int)
    if result.narrative.sentiment is not None:
        assert 1 <= result.narrative.sentiment <= 100
    assert len(result.narrative.signals_considered) > 0
    assert result.narrative.model == config.narrative_generation.model
    assert result.input_tokens > 0
    assert result.output_tokens > 0
