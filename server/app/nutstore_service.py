from server.infrastructure.config import NutstoreConfig
from server.infrastructure.nutstore_webdav import NutstoreWebDAVClient, WebDAVEntry


class NutstoreService:
    def __init__(self, config: NutstoreConfig) -> None:
        self._client = NutstoreWebDAVClient(config)

    async def list(self, path: str = "") -> tuple[WebDAVEntry, ...]:
        return await self._client.list(path)

    async def read_text(self, path: str, *, max_bytes: int = 200000) -> dict[str, object]:
        content, truncated = await self._client.read_bytes(path, max_bytes=max_bytes)
        return {
            "path": path,
            "content": content.decode("utf-8", errors="replace"),
            "truncated": truncated,
        }

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        create_parent: bool = True,
    ) -> None:
        await self._client.write_bytes(
            path,
            content.encode("utf-8"),
            create_parent=create_parent,
        )

    async def delete(self, path: str) -> None:
        await self._client.delete(path)
