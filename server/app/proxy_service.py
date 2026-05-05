from typing import Protocol

from server.domain.proxy import ProxyRequest, ProxyResponse


class ProxyGateway(Protocol):
    async def forward(self, request: ProxyRequest) -> ProxyResponse:
        raise NotImplementedError


class ProxyService:
    def __init__(self, gateway: ProxyGateway) -> None:
        self._gateway = gateway

    async def forward(self, request: ProxyRequest) -> ProxyResponse:
        return await self._gateway.forward(request)
