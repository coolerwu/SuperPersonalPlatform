import json

import httpx

from server.domain.errors import UpstreamLogsError
from server.domain.logs import LogsPayload


class HttpLogsGateway:
    def __init__(self, logs_url: str, timeout_seconds: float = 8.0) -> None:
        self._logs_url = logs_url
        self._timeout_seconds = timeout_seconds

    async def fetch_logs(self) -> LogsPayload:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(self._logs_url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamLogsError(str(exc)) from exc

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return LogsPayload.from_json(response.json())

        text = response.text
        try:
            return LogsPayload.from_json(json.loads(text))
        except json.JSONDecodeError:
            return LogsPayload.from_text(text)
