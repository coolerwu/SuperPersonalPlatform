from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import APP_VERSION
from app.auth import router as auth_router
from app.config import settings
from app.db import init_db
from app.market_db import init_market_db
from app.routers.agents import router as agents_router
from app.routers.allocation import router as allocation_router
from app.routers.app_settings import router as app_settings_router
from app.routers.backtests import router as backtests_router
from app.routers.channels import router as channels_router
from app.routers.chat import router as chat_router
from app.routers.chan import router as chan_router
from app.routers.cron import router as cron_router
from app.routers.knowledge import router as knowledge_router
from app.routers.memories import router as memories_router
from app.routers.market import router as market_router
from app.routers.providers import router as providers_router
from app.routers.sessions import router as sessions_router
from app.routers.skills import router as skills_router
from app.routers.system import router as system_router
from app.routers.tools import router as tools_router
from app.routers.trade_journal import router as trade_journal_router
from app.services.cron_service import CronRuntime
from app.services.knowledge_sync import KnowledgeSyncRuntime
from app.services.session_service import SessionCleanupRuntime
from app.services.market_data import market_service
from app.services.backtest_worker import BacktestWorker, set_active_worker
from app.services.skills import seed_default_skills
from app.services.weixin_runtime import WeixinRuntime
from app.services.workspace import ensure_agent_workspaces

logger = logging.getLogger(__name__)


def create_app(*, start_worker: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            settings.app_data_dir.mkdir(parents=True, exist_ok=True)
            settings.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
            settings.skills_dir.expanduser().mkdir(parents=True, exist_ok=True)
            settings.knowledge_dir.mkdir(parents=True, exist_ok=True)
            settings.backtests_dir.mkdir(parents=True, exist_ok=True)
            from app.services.claude_env import (
                ensure_claude_dirs,
                install_claude_process_env,
            )
            from app.services.settings_store import get_settings_store

            install_claude_process_env()
            ensure_claude_dirs()
            store = get_settings_store()
            await store.ensure_seeded()
            app.state.settings_store = store
            seed_default_skills()
            await init_db()
            await init_market_db()
            await ensure_agent_workspaces(store)
            weixin = WeixinRuntime(store=store)
            app.state.weixin = weixin
            knowledge = KnowledgeSyncRuntime()
            app.state.knowledge = knowledge
            if start_worker:
                from app.services.claude_runner import ClaudeTurnRunner
                from app.services.recovery import recover_running_sessions
                from app.services.session_catalog import SessionCatalog
                from app.services.session_registry import SessionRegistry

                # Hand the send to a background task: turn teardown must not
                # wait for (potentially slow) WeChat delivery.
                async def _enqueue_delivery(delivery_id: int) -> None:
                    weixin.schedule_delivery(delivery_id)

                runner = ClaudeTurnRunner(
                    store, deliver_callback=_enqueue_delivery
                )
                app.state.runner = runner
                registry = SessionRegistry(
                    runner, concurrency=settings.run_concurrency
                )
                app.state.registry = registry
                weixin.registry = registry
                app.state.session_catalog = SessionCatalog(store)
                # Recover interrupted sessions before WeixinRuntime.start()
                # sweeps pending deliveries: a delivery row created at turn
                # start has no content until the turn completes.
                await recover_running_sessions(registry, store)

                await weixin.start()
                cron = CronRuntime(store=store, registry=registry)
                app.state.cron = cron
                await cron.start()
                backtest_worker = BacktestWorker()
                app.state.backtest_worker = backtest_worker
                set_active_worker(backtest_worker)
                await backtest_worker.start()
                await knowledge.start()
                session_cleanup = SessionCleanupRuntime(
                    store=store, registry=registry
                )
                app.state.session_cleanup = session_cleanup
                await session_cleanup.start()
            yield
        finally:
            # A startup failure must still tear down whatever did start. Each
            # stop is isolated so one failure cannot strand the others, and the
            # market client is always released.
            try:
                if start_worker:
                    # Stop turn producers first. Cron commits the schedule
                    # before dispatching, so a shutdown between commit and
                    # execution can cancel that turn and lose one occurrence;
                    # that is an accepted trade-off. Closing the registry first
                    # would instead make cron ticks hit a closed runtime. The
                    # same producer-first order applies to WeChat intake and
                    # delivery.
                    cron = getattr(app.state, "cron", None)
                    if cron is not None:
                        try:
                            await cron.stop()
                        except Exception:
                            logger.exception("cron shutdown failed")
                    weixin = getattr(app.state, "weixin", None)
                    if weixin is not None:
                        try:
                            await weixin.stop()
                        except Exception:
                            logger.exception("weixin shutdown failed")
                    registry = getattr(app.state, "registry", None)
                    if registry is not None:
                        try:
                            await registry.close()
                        except Exception:
                            logger.exception("registry shutdown failed")
                    runner = getattr(app.state, "runner", None)
                    if runner is not None:
                        try:
                            await runner.close_all()
                        except Exception:
                            logger.exception("runner shutdown failed")
                    session_cleanup = getattr(app.state, "session_cleanup", None)
                    if session_cleanup is not None:
                        try:
                            await session_cleanup.stop()
                        except Exception:
                            logger.exception("session cleanup shutdown failed")
                    knowledge = getattr(app.state, "knowledge", None)
                    if knowledge is not None:
                        try:
                            await knowledge.stop()
                        except Exception:
                            logger.exception("knowledge sync shutdown failed")
                    backtest_worker = getattr(app.state, "backtest_worker", None)
                    if backtest_worker is not None:
                        try:
                            await backtest_worker.stop()
                        except Exception:
                            logger.exception("backtest worker shutdown failed")
                    try:
                        set_active_worker(None)
                    except Exception:
                        logger.exception("backtest worker cleanup failed")
            finally:
                try:
                    await market_service.close()
                except Exception:
                    logger.exception("market close failed")

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
    app.include_router(app_settings_router)
    app.include_router(channels_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(cron_router)
    app.include_router(knowledge_router)
    app.include_router(memories_router)
    app.include_router(skills_router)
    app.include_router(tools_router)
    app.include_router(system_router)
    app.include_router(market_router)
    app.include_router(backtests_router)
    app.include_router(chan_router)
    app.include_router(trade_journal_router)
    app.include_router(allocation_router)

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
