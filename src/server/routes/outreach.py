import logging
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sendgrid
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sendgrid.helpers.mail import Bcc, From, Mail, To

import src.analytics as analytics
from src.config.loader import load_config
from src.db.accounts import get_account_by_slug
from src.db.client import get_client
from src.db.contacts import get_contact_by_id
from src.db.narratives import get_current_narrative
from src.db.outreach_drafts import get_active_draft, get_draft_by_id, mark_draft_sent, save_draft
from src.db.signals import get_signals_for_account, insert_signal
from src.db.workspaces import get_workspace_by_id
from src.domain.outreach_draft import DraftIntent, DraftStatus, GeneratedBy, OutreachDraft
from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType
from src.pipeline.outreach import build_signal_panel, load_all_templates, recommend_template

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_workspace_id_for_user(client_db, user_id: str) -> UUID:
    row = client_db.table("users").select("workspace_id").eq("id", user_id).limit(1).execute()
    if not row.data:
        raise HTTPException(status_code=403, detail="User has no workspace")
    return UUID(row.data[0]["workspace_id"])


class ContextRequest(BaseModel):
    contact_id: str | None = None


def _get_current_user(request: Request, client_db):
    """Validate Supabase JWT from Authorization header. Returns (user_id, user_email)."""
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing auth token")
    try:
        response = client_db.auth.get_user(token)
        user = response.user
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user.id, user.email
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def _send_email(
    api_key: str,
    from_email: str,
    from_name: str | None,
    to_email: str,
    bcc_email: str,
    subject: str,
    body: str,
) -> None:
    sg = sendgrid.SendGridAPIClient(api_key=api_key)
    message = Mail(
        from_email=From(from_email, from_name),
        to_emails=To(to_email),
        subject=subject,
        plain_text_content=body,
    )
    message.bcc = [Bcc(bcc_email)]
    response = sg.send(message)
    if response.status_code >= 400:
        raise RuntimeError(f"SendGrid error {response.status_code}: {response.body}")


@router.post("/outreach/{account_slug}/context")
async def get_outreach_context(
    account_slug: str,
    body: ContextRequest,
    request: Request,
) -> JSONResponse:
    client_db = get_client()

    # 1. Validate JWT
    user_id, _user_email = _get_current_user(request, client_db)

    # 2. Resolve workspace
    workspace_id = _get_workspace_id_for_user(client_db, user_id)

    # 3. Load account
    account = get_account_by_slug(client_db, workspace_id, account_slug)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # 4. Load contact if provided
    contact = None
    if body.contact_id:
        contact = get_contact_by_id(client_db, UUID(body.contact_id), workspace_id)

    # 5. Load config
    config = load_config()

    # 6. Load signals (for panel + recommendation)
    signals = get_signals_for_account(client_db, account.workspace_id, account.id)

    # 7. Load narrative (for recommendation)
    narrative = get_current_narrative(client_db, account.workspace_id, account.id)

    # 8. Compute recommendation (pure function, no LLM)
    rec_id, rec_rationale = recommend_template(account, narrative, signals)

    # 9. Load all templates (all intents), render with account/contact name
    templates = load_all_templates(config.outreach_generation.templates_path, account, contact)

    # 10. Get or create active draft
    active_draft = get_active_draft(client_db, account.id)
    if active_draft is None:
        rec_template = next((t for t in templates if t.id == rec_id), templates[0])
        now = datetime.now(UTC)
        draft_seed = OutreachDraft(
            id=uuid4(),
            workspace_id=account.workspace_id,
            account_id=account.id,
            contact_id=contact.id if contact else None,
            intent=DraftIntent(rec_template.intent),
            user_context=None,
            subject=rec_template.subject,
            body=rec_template.body,
            generated_by=GeneratedBy.TEMPLATE,
            template_id=rec_template.id,
            status=DraftStatus.DRAFT,
            sent_at=None,
            sent_by_user_id=None,
            model=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        active_draft = save_draft(client_db, draft_seed)

    # 11. Build signals panel
    panel = build_signal_panel(signals, limit=config.outreach_generation.max_signals_in_context)

    analytics.track(
        "Outreach Context Loaded",
        workspace_id,
        {
            "account_id": str(account.id),
            "has_contact": body.contact_id is not None,
            "recommended_template_id": rec_id,
            "signal_count": len(signals),
        },
    )

    return JSONResponse(
        {
            "draft_id": str(active_draft.id),
            "workspace_id": str(workspace_id),
            "subject": active_draft.subject,
            "body": active_draft.body,
            "recommended_template_id": rec_id,
            "recommendation_rationale": rec_rationale,
            "templates": [
                {
                    "id": t.id,
                    "intent": t.intent,
                    "name": t.name,
                    "subject": t.subject,
                    "body": t.body,
                }
                for t in templates
            ],
            "signals": panel,
        }
    )


@router.post("/outreach/send/{draft_id}")
async def send_draft(draft_id: str, request: Request) -> JSONResponse:
    client_db = get_client()

    # 1. Validate JWT
    user_id, user_email = _get_current_user(request, client_db)
    if not user_email:
        raise HTTPException(status_code=401, detail="User email missing from token")

    # 2. Load draft
    draft = get_draft_by_id(client_db, UUID(draft_id))
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    # 3. Verify workspace ownership
    user_workspace_id = _get_workspace_id_for_user(client_db, user_id)
    if draft.workspace_id != user_workspace_id:
        raise HTTPException(status_code=403, detail="Draft does not belong to your workspace")

    # 4. Validate draft status
    if draft.status != DraftStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Draft already sent")

    # 5. Validate contact_id is set
    if not draft.contact_id:
        raise HTTPException(status_code=400, detail="Draft has no recipient contact")

    # 6. Load contact
    contact = get_contact_by_id(client_db, draft.contact_id, draft.workspace_id)
    if not contact:
        raise HTTPException(status_code=400, detail="Contact not found")

    # 7. Load workspace (to get sender email)
    workspace = get_workspace_by_id(client_db, draft.workspace_id)
    if not workspace or not workspace.outbound_sender_email:
        raise HTTPException(status_code=500, detail="Workspace outbound sender not configured")

    # 8. Send via SendGrid
    api_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="SENDGRID_API_KEY not configured")

    try:
        _send_email(
            api_key=api_key,
            from_email=workspace.outbound_sender_email,
            from_name=workspace.outbound_sender_name,
            to_email=contact.email,
            bcc_email=user_email,
            subject=draft.subject,
            body=draft.body,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502, detail="Email delivery failed — please retry."
        ) from exc

    # 9. Mark draft sent
    now = datetime.now(UTC)
    try:
        mark_draft_sent(client_db, draft.id, now, UUID(user_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Draft already sent") from exc

    # 10. Insert outbound Signal
    signal = Signal(
        id=uuid4(),
        workspace_id=draft.workspace_id,
        account_id=draft.account_id,
        source_type=SourceType.OUTBOUND_EMAIL,
        external_id=str(draft.id),
        thread_id=None,
        direction=Direction.OUTBOUND,
        channel=Channel.EMAIL,
        occurred_at=now,
        created_at=now,
        updated_at=now,
        subject=draft.subject,
        body=draft.body,
        author_contact_id=None,
        recipient_contact_ids=[draft.contact_id] if draft.contact_id else [],
        routing_method=RoutingMethod.MANUAL,
        routing_confidence=1.0,
        routing_warning=None,
        deleted_at=None,
    )
    try:
        insert_signal(client_db, signal)
    except Exception:
        logger.exception("failed to insert outbound signal for draft %s", draft.id)

    analytics.track(
        "Outreach Sent",
        draft.workspace_id,
        {
            "account_id": str(draft.account_id),
            "draft_id": str(draft.id),
            "intent": str(draft.intent),
            "generated_by": str(draft.generated_by),
            "subject_length": len(draft.subject or ""),
            "body_length": len(draft.body or ""),
        },
    )

    return JSONResponse({"status": "sent"})
