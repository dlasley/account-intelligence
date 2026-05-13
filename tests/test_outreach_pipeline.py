"""Unit tests for recommend_template in src.pipeline.outreach."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.pipeline.outreach import recommend_template


def _make_account(*, overall_health_score: int | None = None):
    from src.domain.account import Account, AccountStatus

    now = datetime.now(UTC)
    return Account(
        id=uuid4(),
        workspace_id=uuid4(),
        slug="test-account",
        name="Test Account",
        primary_domain="test.com",
        additional_domains=[],
        vertical=None,
        crm_record_id=None,
        status=AccountStatus.ACTIVE,
        last_narrative_generated_at=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        overall_health_score=overall_health_score,
    )


def _make_narrative(*, text: str, account_id=None, workspace_id=None):
    from src.domain.narrative import Narrative

    now = datetime.now(UTC)
    return Narrative(
        id=uuid4(),
        workspace_id=workspace_id or uuid4(),
        account_id=account_id or uuid4(),
        narrative=text,
        engagement=50,
        engagement_rationale="Test.",
        sentiment=60,
        signal_window_start=now,
        signal_window_end=now,
        signals_considered=(),
        model="claude-opus-4-7",
        prompt_version="abc12345",
        generated_at=now,
        superseded_at=None,
    )


def _make_signal(*, days_ago: int):
    from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType

    now = datetime.now(UTC)
    occurred_at = now - timedelta(days=days_ago)
    return Signal(
        id=uuid4(),
        workspace_id=uuid4(),
        account_id=uuid4(),
        source_type=SourceType.INBOUND_EMAIL,
        external_id="test-ext-id",
        thread_id=None,
        direction=Direction.INBOUND,
        channel=Channel.EMAIL,
        occurred_at=occurred_at,
        created_at=now,
        updated_at=now,
        subject="Test subject",
        body="Test body.",
        author_contact_id=None,
        recipient_contact_ids=[],
        routing_method=RoutingMethod.AUTO_DISCOVERY,
        routing_confidence=1.0,
        routing_warning=None,
        deleted_at=None,
    )


def test_recommend_low_health():
    """Health < 40 → renewal.risk regardless of narrative or signals."""
    account = _make_account(overall_health_score=25)
    template_id, rationale = recommend_template(account, None, [])
    assert template_id == "renewal.risk"
    assert "25" in rationale


def test_recommend_risk_keywords():
    """Narrative contains risk keyword and health < 70 → renewal.risk."""
    account = _make_account(overall_health_score=55)
    narrative = _make_narrative(
        text="The client mentioned they might churn if things don't improve."
    )
    template_id, _ = recommend_template(account, narrative, [])
    assert template_id == "renewal.risk"


def test_recommend_expansion():
    """High health → expansion.usecase."""
    account = _make_account(overall_health_score=80)
    template_id, rationale = recommend_template(account, None, [])
    assert template_id == "expansion.usecase"
    assert "expansion" in rationale.lower() or "strong" in rationale.lower()


def test_recommend_no_recent_signals():
    """Most recent signal > 30 days ago → check_in.reengagement."""
    account = _make_account(overall_health_score=60)
    signals = [_make_signal(days_ago=60)]
    template_id, rationale = recommend_template(account, None, signals)
    assert template_id == "check_in.reengagement"
    assert "60" in rationale


def test_recommend_default():
    """No conditions match → check_in.casual."""
    account = _make_account(overall_health_score=60)
    template_id, _ = recommend_template(account, None, [])
    assert template_id == "check_in.casual"


def test_recommend_none_health_skips_score_rules():
    """overall_health_score=None → rules 1-3 skipped; falls through to signal/default rules."""
    account = _make_account(overall_health_score=None)
    template_id, _ = recommend_template(account, None, [])
    assert template_id in ("check_in.casual", "check_in.reengagement")
