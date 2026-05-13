import json
import re
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from uuid import UUID, uuid4

from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
from src.domain.signal import SourceType

# --- HTML stripping ---


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        # Normalize whitespace runs that arise when adjacent text nodes each carry trailing space
        return re.sub(r" +", " ", " ".join(self._parts)).strip()


def strip_html(html: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


# --- Header parsing ---


def parse_message_id(raw_headers: str) -> str:
    for line in raw_headers.splitlines():
        if line.lower().startswith("message-id:"):
            value = line.split(":", 1)[1].strip().strip("<>")
            if value:
                return value
    return str(uuid4())


def parse_thread_id(raw_headers: str) -> str | None:
    for header_name in ("in-reply-to", "references"):
        for line in raw_headers.splitlines():
            if line.lower().startswith(f"{header_name}:"):
                value = line.split(":", 1)[1].strip()
                if value:
                    # References can be a space-separated list; use the first one
                    return value.split()[0].strip("<>")
    return None


def parse_in_reply_to(raw_headers: str) -> str | None:
    for line in raw_headers.splitlines():
        if line.lower().startswith("in-reply-to:"):
            value = line.split(":", 1)[1].strip().strip("<>")
            return value or None
    return None


def parse_references(raw_headers: str) -> str | None:
    for line in raw_headers.splitlines():
        if line.lower().startswith("references:"):
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


# --- Address routing ---


def extract_workspace_slug(envelope_json: str, inbound_domain: str) -> tuple[str, str | None]:
    """
    Returns (workspace_slug, account_slug_or_None) from the SendGrid envelope JSON string.
    Plus-addressing: workspace+account@domain → (workspace, account).
    Raises ValueError if no matching inbound-domain address is found.
    """
    envelope = json.loads(envelope_json)
    for raw_addr in envelope.get("to", []):
        _, email = parseaddr(raw_addr)
        local, _, domain = email.partition("@")
        if domain != inbound_domain:
            continue
        if "+" in local:
            workspace_slug, _, account_slug = local.partition("+")
            return workspace_slug, account_slug or None
        return local, None
    raise ValueError(f"No address matching {inbound_domain!r} in envelope.to: {envelope_json!r}")


# --- Payload builder ---


def build_raw_payload(form: dict, inbound_domain: str) -> str:
    """
    Converts SendGrid multipart form fields into the InboundPayload-compatible JSON
    that normalizer.py reads from RawInboundEvent.raw_payload.
    """
    raw_headers = form.get("headers", "")
    timestamp_raw = form.get("timestamp", "")

    # Occurred-at: SendGrid sends Unix epoch int as a string
    try:
        occurred_at = datetime.fromtimestamp(int(timestamp_raw), tz=UTC).isoformat()
    except (ValueError, TypeError):
        occurred_at = datetime.now(UTC).isoformat()

    # Body: prefer plaintext, fall back to stripped HTML
    body = form.get("text", "").strip()
    if not body:
        body = strip_html(form.get("html", ""))

    # From: parse display name + email
    from_raw = form.get("from", "")
    from_name, from_email = parseaddr(from_raw)
    from_email = from_email.lower()

    # To emails: for contact upsert (inbound address included — is_internal filters it later)
    to_raw = form.get("to", "")
    to_emails = [addr.lower() for _, addr in getaddresses([to_raw]) if addr]

    payload = {
        "external_id": parse_message_id(raw_headers),
        "thread_id": parse_thread_id(raw_headers),
        "in_reply_to": parse_in_reply_to(raw_headers),
        "references": parse_references(raw_headers),
        "from_email": from_email,
        "from_name": from_name or None,
        "to_emails": to_emails,
        "subject": form.get("subject") or None,
        "body": body,
        "occurred_at": occurred_at,
        "source_type": "inbound_email",
        "direction": "inbound",
        "channel": "email",
    }
    return json.dumps(payload)


def build_raw_inbound_event(
    raw_payload: str,
    workspace_id: UUID,
    received_at: datetime,
) -> RawInboundEvent:
    return RawInboundEvent(
        id=uuid4(),
        workspace_id=workspace_id,
        received_at=received_at,
        source_type=SourceType.INBOUND_EMAIL,
        raw_payload=raw_payload,
        parse_status=ParseStatus.PENDING,
        signal_id=None,
        error_detail=None,
        processed_at=None,
    )
