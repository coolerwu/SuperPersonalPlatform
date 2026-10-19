from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app import APP_VERSION
from app.auth import router as auth_router
from app.config import settings
from app.db import SessionLocal, init_db
from app.models import Agent, Provider
from app.prompts import DEFAULT_AGENT_INSTRUCTIONS
from app.routers.agents import router as agents_router
from app.routers.chat import router as chat_router
from app.routers.memories import router as memories_router
from app.routers.providers import router as providers_router
from app.routers.runs import router as runs_router
from app.routers.sessions import router as sessions_router
from app.routers.skills import router as skills_router
from app.routers.system import router as system_router
from app.routers.tools import router as tools_router
from app.services.run_service import seed_default_provider
from app.services.worker import RunWorker
from app.services.workspace import ensure_agent_workspaces, resolve_workspace_path


async def seed_default_agent() -> None:
    async with SessionLocal() as db:
        existing = await db.scalar(select(Agent).limit(1))
        if existing is not None:
            return
        provider = await db.scalar(select(Provider).limit(1))
        if provider is None:
            return
        resolve_workspace_path("default", create=True)
        db.add(
            Agent(
                id="default",
                name="Assistant",
                instructions=DEFAULT_AGENT_INSTRUCTIONS,
                provider_id=provider.id,
                status="active",
            )
        )
        await db.commit()


LEGACY_DEFAULT_MARKERS = (
    "当前版本你只支持聊天",
    "你是 Agent Command Center 里的个人助理",
    "你是 Hermes Agent，由 Nous Research 构建",
    "你没有 Shell、浏览器或 skills 工具；需要这些能力时直接说明，不要假装拥有。",
)


async def sync_default_agent_instructions() -> None:
    """Update the seeded Assistant once its capabilities changed.

    Only the previous default text is replaced; user-customized instructions
    are left untouched.
    """
    async with SessionLocal() as db:
        agent = await db.get(Agent, "default")
        if agent is not None and any(marker in agent.instructions for marker in LEGACY_DEFAULT_MARKERS):
            agent.instructions = DEFAULT_AGENT_INSTRUCTIONS
            await db.commit()


def create_app(*, start_worker: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.app_data_dir.mkdir(parents=True, exist_ok=True)
        settings.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
        settings.skills_dir.expanduser().mkdir(parents=True, exist_ok=True)
        await init_db()
        await seed_default_provider()
        await seed_default_agent()
        await sync_default_agent_instructions()
        await ensure_agent_workspaces()
        if start_worker:
            worker = RunWorker()
            app.state.worker = worker
            await worker.start()
        yield
        if start_worker:
            worker = getattr(app.state, "worker", None)
            if worker is not None:
                await worker.stop()

    app = FastAPI(title="Agent Command Center", version=APP_VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)
    app.include_router(providers_router)
    app.include_router(agents_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(memories_router)
    app.include_router(runs_router)
    app.include_router(skills_router)
    app.include_router(tools_router)
    app.include_router(system_router)

    @app.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    web_dist = (Path(__file__).resolve().parents[2] / "web" / "dist").resolve()
    if (web_dist / "index.html").is_file():
        assets_dir = web_dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def serve_web(path: str) -> FileResponse:
            if path == "api" or path.startswith("api/"):
                raise HTTPException(status_code=404, detail="not found")
            candidate = (web_dist / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(web_dist):
                return FileResponse(candidate)
            return FileResponse(web_dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
