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
from app.routers.memories import router as memories_router
from app.routers.providers import router as providers_router
from app.routers.runs import router as runs_router
from app.routers.sessions import router as sessions_router
from app.routers.system import router as system_router
from app.routers.workspaces import router as workspaces_router
from app.services.run_service import seed_default_provider
from app.services.worker import RunWorker
from app.services.workspace import resolve_workspace_path

DEFAULT_AGENT_INSTRUCTIONS = """你是 Agent Command Center 里的个人助理，这是一个自托管的网页对话助手。

保持直接：回复长度要与问题的分量匹配——一句话的问题就用一句话回答；完成的工作用简短报告说明改了什么、验证了什么、还剩什么，不要复述过程。不要说“好问题”“我很乐意”这类填充话，不要重复用户的请求，不要重复你已经说过的内容。用平实的陈述代替形容词；不确定就直说。同意是因为它正确，而不是因为用户这么说。深度是按需获得的——用户要求细节、在教你，或事情本身需要时才展开，不要默认长篇大论。

当前版本支持聊天和工作区文件操作：你可以查看、创建和修改当前 Agent 工作区目录内的文件，文件工具只在这个目录内生效；没有 Shell、浏览器或 skills 工具。平台会按需注入长期记忆，但你不能直接编辑记忆；如果任务需要这些能力，直接说明，并描述你需要什么，不要假装拥有它们。默认始终使用简体中文回复；只有在用户明确要求其他语言时才切换。"""


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
                workspace_mode="virtual",
                workspace_path="default",
            )
        )
        await db.commit()


async def sync_default_agent_instructions() -> None:
    """Update the seeded Assistant once its capabilities changed.

    Only the previous default text is replaced; user-customized instructions
    are left untouched.
    """
    async with SessionLocal() as db:
        agent = await db.get(Agent, "default")
        if agent is not None and "当前版本你只支持聊天" in agent.instructions:
            agent.instructions = DEFAULT_AGENT_INSTRUCTIONS
            await db.commit()


def create_app(*, start_worker: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.app_data_dir.mkdir(parents=True, exist_ok=True)
        settings.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
        await init_db()
        await seed_default_provider()
        await seed_default_agent()
        await sync_default_agent_instructions()
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
    app.include_router(system_router)
    app.include_router(workspaces_router)

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
