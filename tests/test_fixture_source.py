import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
from src.domain.signal import SourceType
from src.signals.fixture_source import JsonFixtureSource

SCENARIO = Path("fixtures/quantas-labs-shaped")

if not SCENARIO.exists():
    pytest.skip(
        "quantas-labs pilot data moved to .private/; not present in tracked tree",
        allow_module_level=True,
    )
ROUTING_TEST_FILES = [
    "auto_discovery.json",
    "forward_parse.json",
    "plus_addressed.json",
    "plus_addressed_unknown.json",
    "thread_inherit.json",
    "thread_split.json",
    "unmatched_gmail_1.json",
    "unmatched_gmail_2.json",
]

_SINCE = datetime.min.replace(tzinfo=UTC)


def test_quantas_labs_fixture_loads():
    source = JsonFixtureSource(SCENARIO)
    events = asyncio.run(source.fetch(workspace_id=uuid4(), since=_SINCE))
    assert len(events) > 0
    assert all(isinstance(e, RawInboundEvent) for e in events)
    assert all(e.source_type == SourceType.JSON_FIXTURE for e in events)
    assert all(e.parse_status == ParseStatus.PENDING for e in events)


def test_routing_test_fixtures_present():
    routing_dir = SCENARIO / "routing-tests"
    for filename in ROUTING_TEST_FILES:
        path = routing_dir / filename
        assert path.exists(), f"Missing routing test fixture: {filename}"
        data = json.loads(path.read_text())
        assert "external_id" in data, f"Missing external_id in {filename}"


def test_external_ids_unique():
    source = JsonFixtureSource(SCENARIO)
    events = asyncio.run(source.fetch(workspace_id=uuid4(), since=_SINCE))
    external_ids = [json.loads(e.raw_payload)["external_id"] for e in events]
    assert len(external_ids) == len(set(external_ids)), "Duplicate external_ids found in fixtures"


def test_all_events_have_workspace_id():
    workspace_id = uuid4()
    source = JsonFixtureSource(SCENARIO)
    events = asyncio.run(source.fetch(workspace_id=workspace_id, since=_SINCE))
    assert all(e.workspace_id == workspace_id for e in events)


def test_signal_count_by_account():
    source = JsonFixtureSource(SCENARIO)
    events = asyncio.run(source.fetch(workspace_id=uuid4(), since=_SINCE))
    # 19 formation-bio + 17 jnj + 12 shionogi + 10 harvard + 5 cdc + 8 routing-tests = 71
    assert len(events) == 71
