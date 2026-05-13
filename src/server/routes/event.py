import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

import src.analytics as analytics
from src.config.loader import load_config
from src.db.api_keys import verify_api_key
from src.db.client import get_client
from src.db.workspaces import get_workspace_by_id
from src.pipeline.product_event import ProductEvent, normalize_product_event, validate_event_name
from src.pipeline.scheduler import schedule_regen
from src.server.rate_limit import check_rate_limit

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_EVENTS_PER_BATCH = 500
MAX_BODY_BYTES = 256_000
FUTURE_TOLERANCE = timedelta(minutes=5)


class NativeEvent(BaseModel):
    contact_email: str | None = None
    event: str
    properties: dict = {}
    event_id: str | None = None
    occurred_at: str | None = None


class NativeBatch(BaseModel):
    events: list[NativeEvent]


def _is_segment_payload(body: dict) -> bool:
    return body.get("type") == "track" and isinstance(body.get("context"), dict)


def _segment_to_native(body: dict) -> NativeEvent:
    """Map a Segment track() body to NativeEvent."""
    traits = (body.get("context") or {}).get("traits") or {}
    return NativeEvent(
        contact_email=traits.get("email"),
        event=body.get("event", ""),
        properties=body.get("properties", {}) or {},
        event_id=body.get("messageId"),
        occurred_at=body.get("timestamp"),
    )


def _parse_occurred_at(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@router.options("/event")
async def event_options() -> JSONResponse:
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Max-Age": "86400",
        },
    )


@router.post("/event")
async def event(request: Request) -> JSONResponse:
    cors_headers = {"Access-Control-Allow-Origin": "*"}

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header")
    raw_key = auth.removeprefix("Bearer ").strip()

    client = get_client()
    try:
        key_info = verify_api_key(client, raw_key, required_scope="ingest")
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(401, "Invalid API key") from exc

    config = load_config()
    if not check_rate_limit(key_info.key_prefix, config.api.ingest_rate_limit_per_minute):
        analytics.track(
            "API Key Rate Limited",
            key_info.workspace_id,
            {"key_prefix": key_info.key_prefix},
        )
        return JSONResponse(
            {"error": "rate_limited"},
            status_code=429,
            headers={**cors_headers, "Retry-After": "60"},
        )

    raw_bytes = await request.body()
    if len(raw_bytes) > MAX_BODY_BYTES:
        raise HTTPException(413, "Payload too large")

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(400, "Invalid JSON body") from exc

    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a JSON object")

    events: list[NativeEvent] = []
    if _is_segment_payload(body):
        events = [_segment_to_native(body)]
    elif "events" in body:
        try:
            batch = NativeBatch.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors()) from exc
        events = batch.events
    elif "batch" in body and isinstance(body["batch"], list):
        events = [_segment_to_native(e) for e in body["batch"]]
    elif "event" in body:
        try:
            events = [NativeEvent.model_validate(body)]
        except ValidationError as exc:
            raise HTTPException(422, exc.errors()) from exc
    else:
        raise HTTPException(400, "Unrecognized payload shape")

    if len(events) > MAX_EVENTS_PER_BATCH:
        raise HTTPException(413, f"Batch exceeds {MAX_EVENTS_PER_BATCH} events")

    workspace = get_workspace_by_id(client, key_info.workspace_id)
    if workspace is None:
        raise HTTPException(500, "Workspace not found for key")

    accepted = 0
    rejected = 0
    errors: list[dict[str, Any]] = []
    signal_ids: list[str] = []
    duplicate_ids: list[str] = []
    affected_signals: list = []

    for idx, ev in enumerate(events):
        try:
            occurred_at = _parse_occurred_at(ev.occurred_at)
        except ValueError as exc:
            rejected += 1
            errors.append({"index": idx, "error": f"invalid occurred_at: {exc}"})
            continue

        if occurred_at and occurred_at - datetime.now(UTC) > FUTURE_TOLERANCE:
            rejected += 1
            errors.append({"index": idx, "error": "occurred_at too far in future"})
            continue

        try:
            validated_event_name = validate_event_name(ev.event)
        except ValueError as exc:
            rejected += 1
            errors.append({"index": idx, "error": str(exc)})
            continue

        product_event = ProductEvent(
            contact_email=ev.contact_email,
            event_name=validated_event_name,
            event_properties=ev.properties,
            event_id=ev.event_id,
            occurred_at=occurred_at,
        )

        try:
            result = normalize_product_event(
                product_event,
                workspace.id,
                workspace.name,
                key_info.id,
                client,
            )
        except Exception:
            logger.exception("normalize_product_event failed for index=%s", idx)
            rejected += 1
            errors.append({"index": idx, "error": "internal"})
            continue

        accepted += 1
        if result.duplicate:
            duplicate_ids.append(str(result.signal.id))
        else:
            signal_ids.append(str(result.signal.id))
            if result.signal.account_id:
                affected_signals.append(result.signal)

    seen_account_ids: set[UUID] = set()
    for signal in affected_signals:
        if signal.account_id in seen_account_ids:
            continue
        seen_account_ids.add(signal.account_id)
        try:
            schedule_regen(signal, workspace.id, client)
        except Exception:
            logger.exception("schedule_regen failed for account=%s", signal.account_id)

    analytics.track(
        "Product Events Ingested",
        workspace.id,
        {
            "accepted": accepted,
            "rejected": rejected,
            "duplicate_count": len(duplicate_ids),
            "batch_size": len(events),
        },
    )

    return JSONResponse(
        {
            "accepted": accepted,
            "rejected": rejected,
            "signal_ids": signal_ids,
            "duplicate_ids": duplicate_ids,
            "errors": errors,
        },
        headers=cors_headers,
    )
