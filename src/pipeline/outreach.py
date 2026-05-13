from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.config.loader import config_root
from src.domain.account import Account
from src.domain.contact import Contact
from src.domain.narrative import Narrative
from src.domain.signal import Signal
from src.pipeline.generator import _body_excerpt

_RISK_KEYWORDS: frozenset[str] = frozenset(
    [
        "churn",
        "cancel",
        "at risk",
        "renewal risk",
        "concerned",
        "unhappy",
        "offboarding",
        "contraction",
    ]
)


@dataclass(frozen=True)
class OutreachTemplate:
    id: str
    name: str
    intent: str
    subject: str
    body: str


@dataclass(frozen=True)
class OutreachContext:
    recommended_template_id: str
    recommendation_rationale: str
    templates: list[OutreachTemplate]
    signals: list[dict]


def _templates_root(templates_path: str) -> Path:
    root = config_root()
    resolved = (root / templates_path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"templates_path escapes config root: {templates_path!r}")
    return resolved


def _parse_template_file(path: Path) -> dict:
    """Parse YAML frontmatter + body. No external YAML dependency — simple key: value only."""
    text = path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"Template {path} missing frontmatter")
    parts = text.split("---", maxsplit=2)
    # parts[0]='' parts[1]=frontmatter parts[2]=body
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()

    body_text = parts[2].strip()
    lines = body_text.splitlines()
    subject = ""
    body_lines: list[str] = []
    for i, line in enumerate(lines):
        if line.startswith("Subject: "):
            subject = line.removeprefix("Subject: ").strip()
            body_lines = lines[i + 1 :]
            break
    else:
        body_lines = lines

    return {
        "id": meta.get("id", path.stem),
        "name": meta.get("name", path.stem),
        "intent": meta.get("intent", ""),
        "subject": subject,
        "body": "\n".join(body_lines).strip(),
    }


def _render(raw: dict, account: Account, contact: Contact | None) -> OutreachTemplate:
    """Fill [Account Name] and [Contact Name] slots; leave other [placeholders] for user."""
    contact_name = (contact.display_name or contact.email) if contact else "[Contact Name]"
    slots = {"[Account Name]": account.name, "[Contact Name]": contact_name}

    def _fill(text: str) -> str:
        for k, v in slots.items():
            text = text.replace(k, v)
        return text

    return OutreachTemplate(
        id=raw["id"],
        name=raw["name"],
        intent=raw["intent"],
        subject=_fill(raw["subject"]),
        body=_fill(raw["body"]),
    )


def load_all_templates(
    templates_path: str, account: Account, contact: Contact | None
) -> list[OutreachTemplate]:
    """Load and render all templates (all intents)."""
    root = _templates_root(templates_path)
    results: list[OutreachTemplate] = []
    for path in sorted(root.glob("*.md")):
        try:
            raw = _parse_template_file(path)
        except (ValueError, IndexError):
            continue
        results.append(_render(raw, account, contact))
    return results


def recommend_template(
    account: Account,
    narrative: Narrative | None,
    signals: list[Signal],
) -> tuple[str, str]:
    """Rule-based recommendation. Returns (template_id, rationale). First match wins."""
    score = account.overall_health_score

    # Rule 1: low health score
    if score is not None and score < 40:
        return "renewal.risk", f"Account health score is {score} — renewal outreach recommended."

    # Rule 2: risk language in narrative, moderate health
    if narrative and score is not None and score < 70:
        text = narrative.narrative.lower()
        if any(kw in text for kw in _RISK_KEYWORDS):
            return (
                "renewal.risk",
                "Narrative indicates account risk — renewal outreach recommended.",
            )

    # Rule 3: high health → suggest expansion
    if score is not None and score >= 70:
        return "expansion.usecase", "Account health is strong — expansion opportunity."

    # Rule 4: no recent signals
    if signals:
        most_recent = max(s.occurred_at for s in signals)
        days_since = (datetime.now(UTC) - most_recent).days
        if days_since > 30:
            return (
                "check_in.reengagement",
                f"No recent signals in {days_since} days — re-engagement suggested.",
            )

    return "check_in.casual", "No specific signal detected — default check-in suggested."


def build_signal_panel(signals: list[Signal], limit: int = 5) -> list[dict]:
    """Return the N most recent signals formatted for the signals panel."""
    recent = sorted(signals, key=lambda s: s.occurred_at, reverse=True)[:limit]
    return [
        {
            "occurred_at": s.occurred_at.isoformat(),
            "direction": str(s.direction),
            "subject": s.subject,
            "body_excerpt": _body_excerpt(s.body) if s.body else None,
        }
        for s in recent
    ]
