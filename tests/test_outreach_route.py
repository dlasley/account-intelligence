"""
FastAPI handler tests for POST /outreach/{account_slug}/context and POST /outreach/send/{draft_id}.

DB calls and SendGrid are fully patched — no external connections required.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.server.app import create_app


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
    monkeypatch.setenv("SENDGRID_API_KEY", "fake-sg-key")
    monkeypatch.setenv("INBOUND_DOMAIN", "signal.example.com")
    return TestClient(create_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ACCOUNT_ID = uuid.uuid4()
_WS_ID = uuid.uuid4()
_CONTACT_ID = uuid.uuid4()
_DRAFT_ID = uuid.uuid4()
_USER_ID = str(uuid.uuid4())
_USER_EMAIL = "csm@quantaslabs.com"

FAKE_TOKEN = "valid-jwt-token"


def _fake_db_client(monkeypatch, *, user_id=_USER_ID, user_email=_USER_EMAIL):
    """Returns a mock Supabase client with auth.get_user pre-configured."""
    mock_user = MagicMock()
    mock_user.id = user_id
    mock_user.email = user_email

    mock_auth_response = MagicMock()
    mock_auth_response.user = mock_user

    mock_client = MagicMock()
    mock_client.auth.get_user.return_value = mock_auth_response

    ws_rows = [{"workspace_id": str(_WS_ID)}]
    (
        mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value
    ).data = ws_rows
    return mock_client


def _make_fake_account(*, overall_health_score=None):
    from src.domain.account import Account, AccountStatus

    now = datetime.now(UTC)
    return Account(
        id=_ACCOUNT_ID,
        workspace_id=_WS_ID,
        slug="formation-bio",
        name="Formation Bio",
        primary_domain="formationbio.com",
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


def _make_fake_draft(*, status="draft"):
    from src.domain.outreach_draft import DraftIntent, DraftStatus, GeneratedBy, OutreachDraft

    now = datetime.now(UTC)
    return OutreachDraft(
        id=_DRAFT_ID,
        workspace_id=_WS_ID,
        account_id=_ACCOUNT_ID,
        contact_id=_CONTACT_ID,
        intent=DraftIntent.CHECK_IN,
        user_context=None,
        subject="Checking in — Formation Bio",
        body="Hi [Contact Name],\n\n[Reference something.]",
        generated_by=GeneratedBy.TEMPLATE,
        template_id="check_in.casual",
        status=DraftStatus(status),
        sent_at=None,
        sent_by_user_id=None,
        model=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def _make_fake_contact(*, contact_id=_CONTACT_ID):
    from src.domain.contact import Contact

    now = datetime.now(UTC)
    return Contact(
        id=contact_id,
        workspace_id=_WS_ID,
        account_id=_ACCOUNT_ID,
        email="priya@formationbio.com",
        display_name="Priya Sharma",
        is_internal=False,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def _make_fake_workspace():
    from src.domain.workspace import Workspace

    now = datetime.now(UTC)
    return Workspace(
        id=_WS_ID,
        organization_id=uuid.uuid4(),
        slug="quantas-labs",
        name="Quantas Labs",
        internal_domains=("quantaslabs.com",),
        crm_url_template=None,
        crm_portal_id=None,
        outbound_sender_email="cs@quantaslabs.com",
        outbound_sender_name="Quantas Labs CS",
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def _make_fake_template(*, template_id="check_in.casual", intent="check_in"):
    from src.pipeline.outreach import OutreachTemplate

    return OutreachTemplate(
        id=template_id,
        name="Casual Check-in",
        intent=intent,
        subject="Checking in — Formation Bio",
        body="Hi [Contact Name],\n\n[Reference something.]",
    )


# ---------------------------------------------------------------------------
# POST /outreach/{account_slug}/context — no JWT → 401
# ---------------------------------------------------------------------------


def test_get_context_no_auth(monkeypatch):
    client = _make_client(monkeypatch)
    with patch("src.server.routes.outreach.get_client"):
        resp = client.post("/outreach/formation-bio/context", json={})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /outreach/{account_slug}/context — unknown account → 404
# ---------------------------------------------------------------------------


def test_get_context_unknown_account(monkeypatch):
    client = _make_client(monkeypatch)
    mock_db = _fake_db_client(monkeypatch)
    fake_config = MagicMock()
    fake_config.outreach_generation.max_signals_in_context = 5
    fake_config.outreach_generation.templates_path = "config/templates/outreach"

    with (
        patch("src.server.routes.outreach.get_client", return_value=mock_db),
        patch("src.server.routes.outreach.get_account_by_slug", return_value=None),
        patch("src.server.routes.outreach.load_config", return_value=fake_config),
    ):
        resp = client.post(
            "/outreach/unknown-account/context",
            json={},
            headers={"Authorization": f"Bearer {FAKE_TOKEN}"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /outreach/{account_slug}/context — valid request → 200 with shape
# ---------------------------------------------------------------------------


def test_get_context_returns_200(monkeypatch):
    client = _make_client(monkeypatch)
    mock_db = _fake_db_client(monkeypatch)

    fake_account = _make_fake_account(overall_health_score=60)
    fake_draft = _make_fake_draft()
    fake_template = _make_fake_template()
    fake_config = MagicMock()
    fake_config.outreach_generation.max_signals_in_context = 5
    fake_config.outreach_generation.templates_path = "config/templates/outreach"

    with (
        patch("src.server.routes.outreach.get_client", return_value=mock_db),
        patch("src.server.routes.outreach.get_account_by_slug", return_value=fake_account),
        patch("src.server.routes.outreach.get_contact_by_id", return_value=None),
        patch("src.server.routes.outreach.load_config", return_value=fake_config),
        patch("src.server.routes.outreach.get_signals_for_account", return_value=[]),
        patch("src.server.routes.outreach.get_current_narrative", return_value=None),
        patch(
            "src.server.routes.outreach.recommend_template",
            return_value=("check_in.casual", "No specific signal detected."),
        ),
        patch("src.server.routes.outreach.load_all_templates", return_value=[fake_template]),
        patch("src.server.routes.outreach.get_active_draft", return_value=fake_draft),
        patch("src.server.routes.outreach.build_signal_panel", return_value=[]),
    ):
        resp = client.post(
            "/outreach/formation-bio/context",
            json={},
            headers={"Authorization": f"Bearer {FAKE_TOKEN}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "draft_id" in data
    assert "subject" in data
    assert "body" in data
    assert "recommended_template_id" in data
    assert "recommendation_rationale" in data
    assert isinstance(data["templates"], list)
    assert isinstance(data["signals"], list)
    assert data["contact_id"] == str(_CONTACT_ID)


# ---------------------------------------------------------------------------
# POST /outreach/{account_slug}/context — active_draft is None → save_draft called
# ---------------------------------------------------------------------------


def test_get_context_creates_draft_when_none_exists(monkeypatch):
    """When no active draft exists, save_draft is called with correct intent and template_id."""
    from src.domain.outreach_draft import DraftIntent, OutreachDraft

    client = _make_client(monkeypatch)
    mock_db = _fake_db_client(monkeypatch)

    fake_account = _make_fake_account(overall_health_score=30)  # → renewal.risk
    fake_template = _make_fake_template(template_id="renewal.risk", intent="renewal")
    fake_saved_draft = _make_fake_draft()
    fake_config = MagicMock()
    fake_config.outreach_generation.max_signals_in_context = 5
    fake_config.outreach_generation.templates_path = "config/templates/outreach"

    with (
        patch("src.server.routes.outreach.get_client", return_value=mock_db),
        patch("src.server.routes.outreach.get_account_by_slug", return_value=fake_account),
        patch("src.server.routes.outreach.get_contact_by_id", return_value=None),
        patch("src.server.routes.outreach.load_config", return_value=fake_config),
        patch("src.server.routes.outreach.get_signals_for_account", return_value=[]),
        patch("src.server.routes.outreach.get_current_narrative", return_value=None),
        patch(
            "src.server.routes.outreach.recommend_template",
            return_value=("renewal.risk", "Health score is 30."),
        ),
        patch("src.server.routes.outreach.load_all_templates", return_value=[fake_template]),
        patch("src.server.routes.outreach.get_active_draft", return_value=None),
        patch("src.server.routes.outreach.save_draft", return_value=fake_saved_draft) as mock_save,
        patch("src.server.routes.outreach.build_signal_panel", return_value=[]),
    ):
        resp = client.post(
            "/outreach/formation-bio/context",
            json={},
            headers={"Authorization": f"Bearer {FAKE_TOKEN}"},
        )

    assert resp.status_code == 200
    mock_save.assert_called_once()
    saved: OutreachDraft = mock_save.call_args[0][1]
    assert saved.intent == DraftIntent.RENEWAL
    assert saved.template_id == "renewal.risk"
    assert saved.generated_by == "template"


# ---------------------------------------------------------------------------
# POST /outreach/send/{draft_id} — no JWT → 401
# ---------------------------------------------------------------------------


def test_send_no_auth_returns_401(monkeypatch):
    client = _make_client(monkeypatch)
    with patch("src.server.routes.outreach.get_client"):
        resp = client.post(f"/outreach/send/{_DRAFT_ID}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /outreach/send/{draft_id} — NULL contact_id → 400
# ---------------------------------------------------------------------------


def test_send_null_contact_returns_400(monkeypatch):
    client = _make_client(monkeypatch)
    mock_db = _fake_db_client(monkeypatch)

    from src.domain.outreach_draft import DraftIntent, DraftStatus, GeneratedBy, OutreachDraft

    now = datetime.now(UTC)
    draft_no_contact = OutreachDraft(
        id=_DRAFT_ID,
        workspace_id=_WS_ID,
        account_id=_ACCOUNT_ID,
        contact_id=None,
        intent=DraftIntent.CHECK_IN,
        user_context=None,
        subject="Quick check-in",
        body="Hi there.",
        generated_by=GeneratedBy.TEMPLATE,
        status=DraftStatus.DRAFT,
        sent_at=None,
        sent_by_user_id=None,
        model=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )

    with (
        patch("src.server.routes.outreach.get_client", return_value=mock_db),
        patch("src.server.routes.outreach.get_draft_by_id", return_value=draft_no_contact),
    ):
        resp = client.post(
            f"/outreach/send/{_DRAFT_ID}",
            headers={"Authorization": f"Bearer {FAKE_TOKEN}"},
        )

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /outreach/send/{draft_id} — already-sent draft → 409
# ---------------------------------------------------------------------------


def test_send_already_sent_returns_409(monkeypatch):
    client = _make_client(monkeypatch)
    mock_db = _fake_db_client(monkeypatch)

    sent_draft = _make_fake_draft(status="sent")

    with (
        patch("src.server.routes.outreach.get_client", return_value=mock_db),
        patch("src.server.routes.outreach.get_draft_by_id", return_value=sent_draft),
    ):
        resp = client.post(
            f"/outreach/send/{_DRAFT_ID}",
            headers={"Authorization": f"Bearer {FAKE_TOKEN}"},
        )

    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /outreach/send/{draft_id} — valid → 200 {"status": "sent"}
# ---------------------------------------------------------------------------


def test_send_valid_returns_200(monkeypatch):
    client = _make_client(monkeypatch)
    mock_db = _fake_db_client(monkeypatch)

    fake_draft = _make_fake_draft()
    fake_contact = _make_fake_contact()
    fake_workspace = _make_fake_workspace()
    fake_sent_draft = _make_fake_draft(status="sent")

    with (
        patch("src.server.routes.outreach.get_client", return_value=mock_db),
        patch("src.server.routes.outreach.get_draft_by_id", return_value=fake_draft),
        patch("src.server.routes.outreach.get_contact_by_id", return_value=fake_contact),
        patch("src.server.routes.outreach.get_workspace_by_id", return_value=fake_workspace),
        patch("src.server.routes.outreach._send_email"),
        patch("src.server.routes.outreach.mark_draft_sent", return_value=fake_sent_draft),
        patch("src.server.routes.outreach.insert_signal"),
    ):
        resp = client.post(
            f"/outreach/send/{_DRAFT_ID}",
            headers={"Authorization": f"Bearer {FAKE_TOKEN}"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "sent"}
