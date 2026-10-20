from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Agent, Message, Provider, Run, RunEvent, Session, utcnow
from app.services import memory_service
from app.services.executor import DeepAgentExecutor
from app.services.run_service import (
    append_event,
    cancel_run_record,
    complete_run,
    fail_run,
    requeue_run,
    requeue_running_runs,
)

logger = logging.getLogger(__name__)


class RunWorker:
    def __init__(self, *, weixin_runtime: Any = None) -> None:
        self.weixin_runtime = weixin_runtime
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._background: set[asyncio.Task] = set()
        self._memory_locks: dict[str, asyncio.Lock] = {}
        self._shutting_down = False

    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._run_loop(), name="run-worker")

    async def stop(self) -> None:
        self._shutting_down = True
        self._stop.set()
        self._wake.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
            self._tasks.clear()
        for task in list(self._background):
            task.cancel()
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
            self._background.clear()

    def wake(self) -> None:
        self._wake.set()

    async def _run_loop(self) -> None:
        await requeue_running_runs()
        limit = max(1, settings.run_concurrency)
        while not self._stop.is_set():
            while len(self._tasks) < limit:
                try:
                    run_id = await self._claim_next()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("failed to claim next run")
                    await asyncio.sleep(1)
                    break
                if run_id is None:
                    break
                self._tasks[run_id] = asyncio.create_task(
                    self.execute_run(run_id), name=f"run-{run_id}"
                )

            if not self._tasks:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                continue

            await asyncio.wait(
                self._tasks.values(),
                timeout=1.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for run_id, task in list(self._tasks.items()):
                if not task.done():
                    continue
                self._tasks.pop(run_id, None)
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("run worker task failed for %s", run_id)

    async def _claim_next(self) -> str | None:
        async with SessionLocal() as db:
            running_sessions = select(Run.session_id).where(Run.status == "running")
            while True:
                run = await db.scalar(
                    select(Run)
                    .where(Run.status == "queued", Run.session_id.notin_(running_sessions))
                    .order_by(Run.created_at)
                    .limit(1)
                )
                if run is None:
                    return None
                if run.cancel_requested:
                    run.status = "cancelled"
                    run.finished_at = utcnow()
                    await db.commit()
                    continue
                run.status = "running"
                run.started_at = utcnow()
                await db.commit()
                return run.id

    async def execute_run(self, run_id: str) -> None:
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            if run is None:
                return
            if run.cancel_requested:
                await cancel_run_record(run_id)
                return
            agent = await db.get(Agent, run.agent_id)
            session = await db.get(Session, run.session_id)
            provider = await db.get(Provider, agent.provider_id) if agent is not None else None
            if agent is None or session is None or provider is None:
                await fail_run(run_id, "ConfigurationError", "agent, session, or provider not found")
                await self._deliver_channel_reply(run_id)
                return
            try:
                memory_context, memory_ids = await memory_service.retrieve_for_prompt(
                    agent_id=agent.id, query=run.input
                )
            except Exception:
                logger.exception("memory retrieval failed for run %s", run_id)
                memory_context, memory_ids = "", []
            executor = DeepAgentExecutor(
                provider=provider,
                checkpoint_dir=settings.checkpoint_dir,
                agent_name=agent.name,
                agent_id=agent.id,
                memory_context=memory_context,
                workspace_path=agent.id,
                enabled_skills=list(agent.enabled_skills or []),
                run_id=run_id,
            )
            instructions = agent.instructions
            auto_memory = agent.auto_memory
            agent_id = agent.id
            session_id = session.id
            content = run.input

        if memory_ids:
            try:
                await memory_service.record_run_memories(run_id, memory_ids)
            except Exception:
                logger.exception("failed to record memory usage for run %s", run_id)

        try:
            async with asyncio.timeout(settings.run_timeout_seconds):
                async with aclosing(
                    executor.run(
                        instructions=instructions,
                        session_id=session_id,
                        content=content,
                    )
                ) as events:
                    async for event in events:
                        if event.type == "run_completed":
                            payload = event.payload
                            output = str(payload.get("output") or "")
                            await complete_run(
                                run_id,
                                output=output,
                                input_tokens=int(payload.get("input_tokens") or 0),
                                output_tokens=int(payload.get("output_tokens") or 0),
                                total_tokens=int(payload.get("total_tokens") or 0),
                            )
                            if auto_memory:
                                self._schedule_memory_extraction(
                                    provider=provider,
                                    agent_id=agent_id,
                                    run_id=run_id,
                                    content=content,
                                    output=output,
                                )
                            await self._deliver_channel_reply(run_id)
                            return
                        if event.type == "run_failed":
                            error = event.payload.get("error") or {}
                            await fail_run(
                                run_id,
                                str(error.get("type") or "RunError"),
                                str(error.get("message") or "run failed"),
                            )
                            await self._deliver_channel_reply(run_id)
                            return
                        await append_event(run_id, event.type, event.payload)
        except asyncio.CancelledError:
            if self._shutting_down:
                await requeue_run(run_id)
            else:
                await cancel_run_record(run_id)
            raise
        except TimeoutError:
            await fail_run(run_id, "RunTimeoutError", "run exceeded the execution timeout")
            await self._deliver_channel_reply(run_id)
        except Exception as exc:
            logger.exception("run %s failed", run_id)
            await fail_run(run_id, type(exc).__name__, str(exc))
            await self._deliver_channel_reply(run_id)

    async def _deliver_channel_reply(self, run_id: str) -> None:
        """Send the final answer immediately; never fail the Run on send errors."""
        if self.weixin_runtime is None:
            return
        try:
            await self.weixin_runtime.deliver_run(run_id)
        except Exception:
            logger.exception("channel delivery failed for run %s", run_id)

    def _schedule_memory_extraction(
        self,
        *,
        provider: Provider,
        agent_id: str,
        run_id: str,
        content: str,
        output: str,
    ) -> None:
        # Memory extraction is best-effort post-processing; it must not hold the
        # run's concurrency slot while waiting on the model.
        task = asyncio.create_task(
            self._extract_memories(
                provider=provider,
                agent_id=agent_id,
                run_id=run_id,
                content=content,
                output=output,
            ),
            name=f"memory-{run_id}",
        )
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _memory_lock(self, agent_id: str) -> asyncio.Lock:
        # Runs from different sessions of the same Agent can finish together.
        # Serialize their extraction so the second one sees the first one's adds
        # as merge candidates instead of writing a near-duplicate memory.
        lock = self._memory_locks.get(agent_id)
        if lock is None:
            lock = asyncio.Lock()
            self._memory_locks[agent_id] = lock
        return lock

    async def _extract_memories(
        self,
        *,
        provider: Provider,
        agent_id: str,
        run_id: str,
        content: str,
        output: str,
    ) -> None:
        try:
            if await memory_service.has_explicit_memory_event(run_id):
                return
            async with SessionLocal() as db:
                source_message_id = await db.scalar(
                    select(Message.id)
                    .where(Message.run_id == run_id, Message.role == "user")
                    .limit(1)
                )
            async with self._memory_lock(agent_id):
                changes = await memory_service.extract_agent_memories(
                    provider=provider,
                    agent_id=agent_id,
                    user_content=content,
                    assistant_output=output,
                    run_id=run_id,
                    source_message_id=source_message_id,
                )
            for change in changes:
                await append_event(
                    run_id,
                    "memory_saved",
                    {
                        "source": "auto",
                        "action": change.action,
                        "scope": "agent",
                        "memory_id": change.memory_id,
                        "content": change.content,
                    },
                )
        except Exception:
            logger.exception("auto memory extraction failed for run %s", run_id)

    async def cancel(self, run_id: str) -> bool:
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            if run is None:
                raise LookupError("run not found")
            if run.status in ("completed", "failed", "cancelled"):
                return False
            run.cancel_requested = True
            if run.status == "queued":
                run.status = "cancelled"
                run.finished_at = utcnow()
                db.add(RunEvent(run_id=run.id, type="run_cancelled", payload={"message": "run cancelled"}))
                await db.commit()
                return True
            await db.commit()
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
        return True
