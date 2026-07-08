import json
from datetime import datetime

import pytest

from src.signals.shared_inbox import (
    build_raw_payload,
    extract_workspace_slug,
    parse_message_id,
    parse_thread_id,
    strip_html,
)

# --- strip_html ---


def test_strip_html_removes_tags():
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_plain_text_passthrough():
    assert strip_html("No tags here") == "No tags here"


def test_strip_html_empty():
    assert strip_html("") == ""


# --- parse_message_id ---

SAMPLE_HEADERS = """\
Received: from mail.example.com ...
Message-ID: <abc123@mail.example.com>
In-Reply-To: <parent456@mail.example.com>
References: <parent456@mail.example.com> <grandparent789@mail.example.com>
Subject: Test
"""


def test_parse_message_id_found():
    assert parse_message_id(SAMPLE_HEADERS) == "abc123@mail.example.com"


def test_parse_message_id_missing_returns_uuid():
    mid = parse_message_id("Subject: No message id here\n")
    assert len(mid) == 36  # uuid4 string length


# --- parse_thread_id ---


def test_parse_thread_id_uses_in_reply_to():
    assert parse_thread_id(SAMPLE_HEADERS) == "parent456@mail.example.com"


def test_parse_thread_id_references_fallback():
    headers = "References: <ref1@x.com> <ref2@x.com>\nSubject: hi\n"
    assert parse_thread_id(headers) == "ref1@x.com"


def test_parse_thread_id_none_when_absent():
    assert parse_thread_id("Subject: hi\n") is None


# --- extract_workspace_slug ---


def _envelope(to: list[str]) -> str:
    return json.dumps({"from": "sender@customer.com", "to": to})


DOMAIN = "inbound.example.com"


def test_extract_workspace_slug_simple():
    ws, acct = extract_workspace_slug(_envelope([f"quantas-labs@{DOMAIN}"]), DOMAIN)
    assert ws == "quantas-labs"
    assert acct is None


def test_extract_workspace_slug_plus_addressing():
    ws, acct = extract_workspace_slug(_envelope([f"quantas-labs+formation-bio@{DOMAIN}"]), DOMAIN)
    assert ws == "quantas-labs"
    assert acct == "formation-bio"


def test_extract_workspace_slug_ignores_other_domains():
    ws, _acct = extract_workspace_slug(
        _envelope(["other@otherdomain.com", f"quantas-labs@{DOMAIN}"]), DOMAIN
    )
    assert ws == "quantas-labs"


def test_extract_workspace_slug_raises_if_no_match():
    with pytest.raises(ValueError):
        extract_workspace_slug(_envelope(["other@otherdomain.com"]), DOMAIN)


def test_extract_workspace_slug_malformed_json_raises():
    with pytest.raises(ValueError):  # json.JSONDecodeError is a subclass of ValueError
        extract_workspace_slug("{not-json}", DOMAIN)


def test_extract_workspace_slug_empty_to_raises():
    with pytest.raises(ValueError):
        extract_workspace_slug(json.dumps({"to": []}), DOMAIN)


# --- build_raw_payload ---

SAMPLE_FORM = {
    "from": "Priya Patel <priya@formationbio.com>",
    "to": f"quantas-labs@{DOMAIN}",
    "subject": "Quick question about the API",
    "text": "Hi, can you clarify the rate limits?",
    "html": "<p>Hi, can you clarify the rate limits?</p>",
    "timestamp": "1714000000",
    "headers": SAMPLE_HEADERS,
    "envelope": _envelope([f"quantas-labs@{DOMAIN}"]),
}


def test_build_raw_payload_fields():
    raw = json.loads(build_raw_payload(SAMPLE_FORM, DOMAIN))
    assert raw["from_email"] == "priya@formationbio.com"
    assert raw["from_name"] == "Priya Patel"
    assert raw["body"] == "Hi, can you clarify the rate limits?"
    assert raw["subject"] == "Quick question about the API"
    assert raw["source_type"] == "inbound_email"
    assert raw["direction"] == "inbound"
    assert raw["channel"] == "email"
    assert raw["external_id"] == "abc123@mail.example.com"
    assert raw["thread_id"] == "parent456@mail.example.com"


def test_build_raw_payload_prefers_text_over_html():
    raw = json.loads(build_raw_payload(SAMPLE_FORM, DOMAIN))
    assert "clarify the rate limits" in raw["body"]
    # Must not contain HTML tags
    assert "<p>" not in raw["body"]


def test_build_raw_payload_falls_back_to_stripped_html():
    form = {**SAMPLE_FORM, "text": ""}
    raw = json.loads(build_raw_payload(form, DOMAIN))
    assert "clarify the rate limits" in raw["body"]
    assert "<p>" not in raw["body"]


def test_build_raw_payload_timestamp_conversion():
    raw = json.loads(build_raw_payload(SAMPLE_FORM, DOMAIN))
    # Should be a valid ISO timestamp with UTC
    dt = datetime.fromisoformat(raw["occurred_at"])
    assert dt.tzinfo is not None
