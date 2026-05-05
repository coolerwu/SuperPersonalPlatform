import os

import uvicorn


def _reload_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    host = os.environ.get("SUPER_PERSONAL_HOST", "0.0.0.0")
    port = int(os.environ.get("SUPER_PERSONAL_PORT", "8888"))
    reload = _reload_enabled(os.environ.get("SUPER_PERSONAL_RELOAD"))

    uvicorn.run(
        "server.infrastructure.fastapi_app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
