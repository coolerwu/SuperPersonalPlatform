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
from app.routers.agents import router as agents_router
from app.routers.chat import router as chat_router
from app.routers.providers import router as providers_router
from app.routers.runs import router as runs_router
from app.routers.sessions import router as sessions_router
from app.routers.system import router as system_router
from app.services.run_service import seed_default_provider
from app.services.worker import RunWorker


async def seed_default_agent() -> None:
    async with SessionLocal() as db:
        existing = await db.scalar(select(Agent).limit(1))
        if existing is not None:
            return
        provider = await db.scalar(select(Provider).limit(1))
        if provider is None:
            return
        db.add(
            Agent(
                id="default",
                name="Assistant",
                instructions="You are a helpful personal assistant. Answer clearly and concisely.",
                provider_id=provider.id,
                status="active",
            )
        )
        await db.commit()


def create_app(*, start_worker: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.app_data_dir.mkdir(parents=True, exist_ok=True)
        settings.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        await init_db()
        await seed_default_provider()
        await seed_default_agent()
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
    app.include_router(runs_router)
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
