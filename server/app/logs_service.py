from typing import Protocol

from server.domain.logs import LogsPayload


class LogsGateway(Protocol):
    async def fetch_logs(self) -> LogsPayload:
        raise NotImplementedError


class LogsService:
    def __init__(self, gateway: LogsGateway) -> None:
        self._gateway = gateway

    async def get_logs(self) -> LogsPayload:
        return await self._gateway.fetch_logs()
