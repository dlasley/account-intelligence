import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_DNS, UUID, uuid5

from src.domain.account import Account, AccountStatus
from src.domain.signal import RoutingMethod
from src.domain.workspace import Workspace

PERSONAL_PROVIDER_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "yahoo.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "proton.me",
        "protonmail.com",
        "aol.com",
    }
)

# Forwarded message header patterns, tried in order
_FORWARD_PATTERNS = [
    re.compile(r"From:\s+[^<]*<([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>"),
    re.compile(r"From:\s+([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\s*\nSent:"),
]


@dataclass
class RoutingResult:
    account_id: UUID | None
    routing_method: RoutingMethod
    routing_confidence: float
    routing_warning: str | None
    new_candidate: Account | None


def _domain(email: str) -> str:
    return email.lower().split("@")[-1] if "@" in email else ""


def _matches_account(sender_domain: str, account: Account) -> bool:
    for d in account.all_domains():
        if sender_domain == d or sender_domain.endswith("." + d):
            return True
    return False


def _stage0_outbound_bcc(
    payload: dict,
    workspace: Workspace,
    accounts: list[Account],
    personal_provider_domains: frozenset[str],
    inbound_address: str,
) -> RoutingResult | None:
    from_domain = _domain(payload.get("from_email", ""))
    if from_domain not in workspace.internal_domains:
        return None  # not an outbound signal

    # Filter to_emails to external addresses: exclude internal domains and the workspace's
    # own inbound address (the BCC target) and its plus-addressed variants.
    inbound_local, _, inbound_host = inbound_address.partition("@")

    def _is_workspace_inbound(email: str) -> bool:
        local, _, host = email.lower().strip().partition("@")
        return host == inbound_host and (
            local == inbound_local or local.startswith(inbound_local + "+")
        )

    to_emails = [
        e
        for e in payload.get("to_emails", [])
        if _domain(e)
        and _domain(e) not in workspace.internal_domains
        and not _is_workspace_inbound(e)
    ]
    if not to_emails:
        return None  # internal-only or inbound-only recipients; let existing cascade handle it

    # Try to match an existing account by recipient domain
    external_domains = {_domain(e) for e in to_emails}
    matching = [
        a
        for a in accounts
        if a.slug != "_unmatched" and any(_matches_account(d, a) for d in external_domains)
    ]
    if len(matching) == 1:
        return RoutingResult(
            account_id=matching[0].id,
            routing_method=RoutingMethod.OUTBOUND_BCC,
            routing_confidence=0.9,
            routing_warning=None,
            new_candidate=None,
        )
    if len(matching) > 1:
        best = max(matching, key=lambda a: a.updated_at)
        return RoutingResult(
            account_id=best.id,
            routing_method=RoutingMethod.OUTBOUND_BCC,
            routing_confidence=0.9,
            routing_warning=f"multiple recipient domain matches: {[a.slug for a in matching]}",
            new_candidate=None,
        )

    # No existing account — auto-discover from recipient domain
    to_domain = _domain(to_emails[0])
    if to_domain in personal_provider_domains:
        return None  # can't derive a company from a personal address
    domain_root = to_domain.split(".")[0]
    candidate = Account(
        id=uuid5(NAMESPACE_DNS, f"{workspace.id}:auto:{to_domain}"),
        workspace_id=workspace.id,
        slug=domain_root.lower().replace(".", "-"),
        name=domain_root.title(),
        primary_domain=to_domain,
        additional_domains=[],
        vertical=None,
        crm_record_id=None,
        status=AccountStatus.CANDIDATE,
        last_narrative_generated_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        deleted_at=None,
    )
    return RoutingResult(
        account_id=None,
        routing_method=RoutingMethod.OUTBOUND_BCC,
        routing_confidence=0.3,
        routing_warning=None,
        new_candidate=candidate,
    )


def _stage1_plus_addressing(
    payload: dict, workspace: Workspace, accounts: list[Account]
) -> RoutingResult | None:
    for to_email in payload.get("to_emails", []):
        local, _, _ = to_email.partition("@")
        if "+" not in local:
            continue
        prefix, _, slug = local.partition("+")
        if prefix != workspace.slug:
            continue
        for account in accounts:
            if account.slug == slug:
                return RoutingResult(
                    account_id=account.id,
                    routing_method=RoutingMethod.PLUS_ADDRESSING,
                    routing_confidence=1.0,
                    routing_warning=None,
                    new_candidate=None,
                )
        # Slug present but not recognised — fall through to stage 2
    return None


def _stage2_header_domain(
    payload: dict, workspace: Workspace, accounts: list[Account]
) -> RoutingResult | None:
    all_emails = [payload.get("from_email", ""), *payload.get("to_emails", [])]
    external_domains = {
        _domain(e)
        for e in all_emails
        if e and _domain(e) not in workspace.internal_domains and _domain(e)
    }

    matching: list[Account] = []
    for account in accounts:
        if account.slug == "_unmatched":
            continue
        if any(_matches_account(d, account) for d in external_domains):
            matching.append(account)

    if not matching:
        return None
    if len(matching) == 1:
        return RoutingResult(
            account_id=matching[0].id,
            routing_method=RoutingMethod.HEADER_DOMAIN,
            routing_confidence=0.9,
            routing_warning=None,
            new_candidate=None,
        )
    # Multiple matches — pick most recently updated, record warning
    best = max(matching, key=lambda a: a.updated_at)
    warning = f"multiple domain matches: {[a.slug for a in matching]}"
    return RoutingResult(
        account_id=best.id,
        routing_method=RoutingMethod.HEADER_DOMAIN,
        routing_confidence=0.9,
        routing_warning=warning,
        new_candidate=None,
    )


def _stage3_forward_parse(
    payload: dict, workspace: Workspace, accounts: list[Account]
) -> RoutingResult | None:
    from_email = payload.get("from_email", "")
    if not from_email or _domain(from_email) not in workspace.internal_domains:
        return None

    body = payload.get("body", "")
    for pattern in _FORWARD_PATTERNS:
        m = pattern.search(body)
        if m:
            extracted = m.group(1).strip()
            result = _stage2_header_domain(dict(payload, from_email=extracted), workspace, accounts)
            if result:
                return RoutingResult(
                    account_id=result.account_id,
                    routing_method=RoutingMethod.FORWARD_PARSE,
                    routing_confidence=0.7,
                    routing_warning=result.routing_warning,
                    new_candidate=None,
                )
    return None


def _stage4_thread_inherit(
    payload: dict, thread_accounts: dict[str, list[UUID]]
) -> RoutingResult | None:
    thread_id = payload.get("thread_id")
    if not thread_id or thread_id not in thread_accounts:
        return None

    # Deduplicate while preserving created_at DESC order from DB query
    seen: set[UUID] = set()
    distinct: list[UUID] = []
    for aid in thread_accounts[thread_id]:
        if aid not in seen:
            seen.add(aid)
            distinct.append(aid)

    if not distinct:
        return None
    if len(distinct) == 1:
        return RoutingResult(
            account_id=distinct[0],
            routing_method=RoutingMethod.THREAD_INHERIT,
            routing_confidence=0.6,
            routing_warning=None,
            new_candidate=None,
        )
    # Split-brain — pick most-recently-ingested (first in list)
    picked = distinct[0]
    warning = f"thread split across accounts {distinct}; picked most recent"
    return RoutingResult(
        account_id=picked,
        routing_method=RoutingMethod.THREAD_INHERIT_SPLIT,
        routing_confidence=0.6,
        routing_warning=warning,
        new_candidate=None,
    )


def _stage5_auto_discovery(
    payload: dict,
    workspace: Workspace,
    personal_provider_domains: frozenset[str],
) -> RoutingResult | None:
    from_email = payload.get("from_email", "")
    if not from_email:
        return None
    from_domain = _domain(from_email)
    if from_domain in workspace.internal_domains or from_domain in personal_provider_domains:
        return None

    # Simple v1 slug/name derivation from domain root
    domain_root = from_domain.split(".")[0]
    candidate = Account(
        id=uuid5(NAMESPACE_DNS, f"{workspace.id}:auto:{from_domain}"),
        workspace_id=workspace.id,
        slug=domain_root.lower().replace(".", "-"),
        name=domain_root.title(),
        primary_domain=from_domain,
        additional_domains=[],
        vertical=None,
        crm_record_id=None,
        status=AccountStatus.CANDIDATE,
        last_narrative_generated_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        deleted_at=None,
    )
    return RoutingResult(
        account_id=None,
        routing_method=RoutingMethod.AUTO_DISCOVERY,
        routing_confidence=0.3,
        routing_warning=None,
        new_candidate=candidate,
    )


def route(
    payload: dict,
    workspace: Workspace,
    accounts: list[Account],
    thread_accounts: dict[str, list[UUID]],
    personal_provider_domains: frozenset[str] | None = None,
    inbound_address: str | None = None,
) -> RoutingResult:
    if personal_provider_domains is None:
        personal_provider_domains = PERSONAL_PROVIDER_DOMAINS
    if inbound_address is None:
        inbound_address = workspace.inbound_address

    result = _stage0_outbound_bcc(
        payload, workspace, accounts, personal_provider_domains, inbound_address
    )
    if result:
        return result

    result = _stage1_plus_addressing(payload, workspace, accounts)
    if result:
        return result

    result = _stage2_header_domain(payload, workspace, accounts)
    if result:
        return result

    result = _stage3_forward_parse(payload, workspace, accounts)
    if result:
        return result

    result = _stage4_thread_inherit(payload, thread_accounts)
    if result:
        return result

    result = _stage5_auto_discovery(payload, workspace, personal_provider_domains)
    if result:
        return result

    return RoutingResult(
        account_id=None,
        routing_method=RoutingMethod.UNMATCHED,
        routing_confidence=0.0,
        routing_warning=None,
        new_candidate=None,
    )
