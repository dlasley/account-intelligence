import json
import logging
from uuid import UUID

import src.analytics as analytics
from src.db.accounts import upsert_account
from src.db.audit import insert_audit_event
from src.db.raw_inbound_events import mark_failed, mark_processed
from src.db.signals import get_signals_by_thread_id, update_signal_routing
from src.domain.account import Account
from src.domain.events import ActorType, AuditAction
from src.domain.raw_inbound_event import RawInboundEvent
from src.domain.signal import RoutingMethod, Signal
from src.domain.workspace import Workspace
from src.pipeline.normalizer import normalize
from src.pipeline.router import route
from src.pipeline.scheduler import UNMATCHED_ACCOUNT_SLUG, schedule_regen
from supabase import Client

logger = logging.getLogger(__name__)


def process_event(
    event: RawInboundEvent,
    workspace: Workspace,
    accounts: list[Account],  # mutated in-place: auto-discovered candidates are appended so
    # subsequent events in the same batch can match the new domain. NOT thread-safe —
    # a Phase 3 parallelism refactor must replace this with a thread-safe accumulator.
    client: Client,
) -> Signal:
    workspace_id = workspace.id

    # 1. Normalise — parse payload, upsert contacts, insert signal
    try:
        result = normalize(event, workspace_id, list(workspace.internal_domains), client)
    except Exception as exc:
        mark_failed(client, event.id, str(exc))
        raise

    signal = result.signal
    mark_processed(client, event.id, signal.id)

    # 2. Query thread history for thread-inherit stages
    thread_accounts: dict[str, list[UUID]] = {}
    if signal.thread_id:
        thread_signals = get_signals_by_thread_id(client, workspace_id, signal.thread_id)
        seen: set[UUID] = set()
        ordered_ids: list[UUID] = []
        for ts in thread_signals:
            if ts.account_id and ts.account_id not in seen:
                seen.add(ts.account_id)
                ordered_ids.append(ts.account_id)
        thread_accounts = {signal.thread_id: ordered_ids}

    # 3. Route
    payload = json.loads(event.raw_payload)
    routing = route(
        payload, workspace, accounts, thread_accounts, inbound_address=workspace.inbound_address
    )

    # 4. Persist any newly auto-discovered candidate account
    if routing.new_candidate:
        candidate = upsert_account(client, routing.new_candidate)
        accounts.append(candidate)
        routing = routing.__class__(
            account_id=candidate.id,
            routing_method=routing.routing_method,
            routing_confidence=routing.routing_confidence,
            routing_warning=routing.routing_warning,
            new_candidate=None,
        )

    # 5. Resolve account_id — fall back to _unmatched pseudo-account
    account_id = routing.account_id
    if account_id is None:
        unmatched = next((a for a in accounts if a.slug == UNMATCHED_ACCOUNT_SLUG), None)
        if unmatched:
            account_id = unmatched.id
            # Fires only when actually routed to the _unmatched catch-all per the
            # tracking-plan brief ("Signal routed to unmatched"). If no _unmatched
            # exists in the workspace, signal stays orphan and no event fires.
            analytics.track(
                "Signal Unmatched",
                workspace_id,
                {"routing_method": "unmatched"},
            )

    # 6. Persist routing decision on the signal row
    update_signal_routing(
        client,
        signal_id=signal.id,
        account_id=account_id,
        routing_method=routing.routing_method,
        routing_confidence=routing.routing_confidence,
        routing_warning=routing.routing_warning,
    )
    signal.account_id = account_id
    signal.routing_method = routing.routing_method
    signal.routing_confidence = routing.routing_confidence
    signal.routing_warning = routing.routing_warning

    # 7. Enqueue narrative regen job
    matched_slug = next((a.slug for a in accounts if a.id == account_id), None)
    schedule_regen(signal, workspace_id, client, account_slug=matched_slug)

    # 8. Audit log for routing
    audit_meta: dict = {
        "routing_method": routing.routing_method,
        "routing_confidence": routing.routing_confidence,
    }
    if routing.routing_warning:
        audit_meta["routing_warning"] = routing.routing_warning

    if routing.routing_method == RoutingMethod.THREAD_INHERIT_SPLIT:
        insert_audit_event(
            client,
            workspace_id=workspace_id,
            actor_type=ActorType.WORKER,
            actor_id="worker",
            action=AuditAction.ROUTING_THREAD_SPLIT,
            resource_type="signal",
            resource_id=signal.id,
            metadata=audit_meta,
        )

    logger.debug(
        "processed %s → %s (%.2f)",
        signal.external_id,
        routing.routing_method,
        routing.routing_confidence,
    )
    return signal
