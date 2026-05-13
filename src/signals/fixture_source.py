import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
from src.domain.signal import SourceType
from src.signals.source import SignalSource


class JsonFixtureSource(SignalSource):
    def __init__(self, scenario_path: Path) -> None:
        self.scenario_path = scenario_path

    def _collect_files(self) -> list[Path]:
        files: list[Path] = []
        signals_dir = self.scenario_path / "signals"
        if signals_dir.exists():
            for account_dir in sorted(signals_dir.iterdir()):
                if account_dir.is_dir():
                    files.extend(sorted(account_dir.glob("*.json")))
        routing_dir = self.scenario_path / "routing-tests"
        if routing_dir.exists():
            files.extend(sorted(routing_dir.glob("*.json")))
        # candidates/ is intentionally excluded: those are account-level metadata fixtures
        # (status=candidate), not signal fixtures. Phase 2's account loader will handle them.
        return files

    async def fetch(self, workspace_id: UUID, since: datetime) -> list[RawInboundEvent]:
        # `since` is intentionally not applied in Phase 1: all fixture events are returned
        # unconditionally. Phase 2's SupabaseSource will filter by received_at > since.
        events: list[RawInboundEvent] = []
        now = datetime.now(UTC)
        for path in self._collect_files():
            payload = json.loads(path.read_text())
            events.append(
                RawInboundEvent(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    received_at=now,
                    source_type=SourceType.JSON_FIXTURE,
                    raw_payload=json.dumps(payload),
                    parse_status=ParseStatus.PENDING,
                    signal_id=None,
                    error_detail=None,
                    processed_at=None,
                )
            )
        return events
