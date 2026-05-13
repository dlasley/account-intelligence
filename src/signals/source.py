from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from src.domain.raw_inbound_event import RawInboundEvent


class SignalSource(ABC):
    @abstractmethod
    async def fetch(self, workspace_id: UUID, since: datetime) -> list[RawInboundEvent]: ...
