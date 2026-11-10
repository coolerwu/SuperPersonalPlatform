"""Agent tools for managing the current Agent's cron jobs."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Channel, ChannelConversation, CronJob, Run, Session
from app.services import cron_service
from app.services.cron_service import (
    CronBusy,
    CronConflict,
    CronInactiveAgent,
    CronNotFound,
    CronValidationError,
    session_title,
)
from app.services.skill_tools import SKILL_TOOL_REGISTRY, SkillToolContext, SkillToolRegistry

RUN_STATUS_LABELS = {
    "queued": "排队中",
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}


def _format_time(value: datetime | None, timezone_name: str) -> str:
    if value is None:
        return "—"
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        return value.isoformat()
    return value.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def _format_job(job: CronJob, last_status: str | None, push_label: str = "") -> str:
    state = "启用" if job.enabled else "暂停"
    next_at = _format_time(job.next_run_at, job.timezone) if job.enabled else "—"
    last_at = _format_time(job.last_run_at, job.timezone) if job.last_run_at else "—"
    status = RUN_STATUS_LABELS.get(last_status or "", last_status or "—")
    session = f"{session_title(job.name)}（{job.session_id}）" if job.session_id else "下次触发时重建"
    return (
        f"- id: {job.id} | 名称: {job.name} | {state} | "
        f"{job.schedule}（{job.timezone}） | 下次: {next_at} | "
        f"最近: {last_at} {status} | 会话: {session} | "
        f"推送: {push_label or '不推送'}"
    )


async def _push_label(db, job: CronJob) -> str:
    if not job.push_channel_id or not job.push_conversation_id:
        return "不推送"
    channel = await db.get(Channel, job.push_channel_id)
    conversation = await db.get(ChannelConversation, job.push_conversation_id)
    channel_name = (channel.name if channel else "") or "已删除渠道"
    conversation_name = (
        (conversation.display_name if conversation else "") or "已删除联系人"
    )
    return f"{channel_name} / {conversation_name}"


async def _list_text(context: SkillToolContext) -> str:
    if not context.agent_id:
        return "Error: 当前 Agent 上下文不可用"
    async with SessionLocal() as db:
        jobs = await cron_service.list_jobs(db, context.agent_id)
        run_ids = [job.last_run_id for job in jobs if job.last_run_id]
        status_by_id: dict[str, str] = {}
        if run_ids:
            rows = (
                await db.execute(select(Run.id, Run.status).where(Run.id.in_(run_ids)))
            ).all()
            status_by_id = {str(row[0]): str(row[1]) for row in rows}
        push_labels = {job.id: await _push_label(db, job) for job in jobs}
    if not jobs:
        return "当前 Agent 还没有定时任务。"
    lines = [f"## 定时任务（{len(jobs)} 个）"]
    lines.extend(
        _format_job(
            job,
            status_by_id.get(job.last_run_id or ""),
            push_labels.get(job.id, ""),
        )
        for job in jobs
    )
    return "\n".join(lines)


async def _create_text(
    context: SkillToolContext,
    name: str,
    prompt: str,
    schedule: str,
    timezone_name: str,
    enabled: bool,
    push_channel_id: str,
    push_conversation_id: str,
) -> str:
    if not context.agent_id:
        return "Error: 当前 Agent 上下文不可用"
    async with SessionLocal() as db:
        try:
            job = await cron_service.create_job(
                db,
                agent_id=context.agent_id,
                name=name,
                prompt=prompt,
                schedule=schedule,
                timezone=timezone_name,
                enabled=enabled,
                push_channel_id=push_channel_id,
                push_conversation_id=push_conversation_id,
            )
            await db.commit()
            await db.refresh(job)
            push_label = await _push_label(db, job)
        except CronNotFound:
            return "Error: 当前 Agent 不存在或已归档"
        except CronValidationError as exc:
            return f"Error: {exc}"
        except CronConflict as exc:
            return f"Error: {exc}"
    state = "已启用" if job.enabled else "已暂停"
    next_at = _format_time(job.next_run_at, job.timezone) if job.enabled else "—"
    if push_label == "不推送":
        delivery = "结果不会主动推送，用户可在该会话查看和追问。"
    else:
        delivery = f"结果会推送到 {push_label}，同时保存在专属会话里。"
    return (
        f"已创建定时任务「{job.name}」（id: {job.id}），{state}。\n"
        f"cron: {job.schedule}（{job.timezone}）；下次触发: {next_at}。\n"
        f"专属会话：{session_title(job.name)}（session_id: {job.session_id}）。\n"
        f"推送：{push_label}。\n"
        f"任务在独立会话中运行，prompt 需要自包含；{delivery}"
    )


async def _update_text(
    context: SkillToolContext,
    job_id: str,
    name: str | None,
    prompt: str | None,
    schedule: str | None,
    timezone_name: str | None,
    enabled: bool | None,
    push_channel_id: str | None,
    push_conversation_id: str | None,
) -> str:
    if not context.agent_id:
        return "Error: 当前 Agent 上下文不可用"
    # An empty timezone means "leave unchanged" for updates; creation treats
    # it as the configured default.
    timezone_name = (timezone_name or "").strip() or None
    async with SessionLocal() as db:
        try:
            job = await cron_service.update_job(
                db,
                job_id,
                agent_id=context.agent_id,
                name=name,
                prompt=prompt,
                schedule=schedule,
                timezone=timezone_name,
                enabled=enabled,
                push_channel_id=push_channel_id,
                push_conversation_id=push_conversation_id,
            )
            await db.commit()
            await db.refresh(job)
            push_label = await _push_label(db, job)
        except CronNotFound:
            return "Error: 定时任务不存在，或不属于当前 Agent"
        except CronValidationError as exc:
            return f"Error: {exc}"
        except CronConflict as exc:
            return f"Error: {exc}"
    state = "启用" if job.enabled else "暂停"
    next_at = _format_time(job.next_run_at, job.timezone) if job.enabled else "—"
    return (
        f"已更新定时任务「{job.name}」（id: {job.id}），当前{state}；"
        f"下次触发: {next_at}；推送: {push_label}。"
    )


async def _targets_text(context: SkillToolContext) -> str:
    if not context.agent_id:
        return "Error: 当前 Agent 上下文不可用"
    async with SessionLocal() as db:
        channels = (
            await db.scalars(
                select(Channel)
                .where(Channel.agent_id == context.agent_id, Channel.type == "wechat")
                .order_by(Channel.created_at)
            )
        ).all()
        if not channels:
            return "当前 Agent 没有微信渠道；定时任务仍可创建，但不会推送。"
        lines = ["## 可推送的微信目标"]
        for channel in channels:
            label = channel.name or channel.account_id or channel.id
            lines.append(f"渠道：{label}（id: {channel.id}）· 状态: {channel.status}")
            rows = (
                await db.execute(
                    select(ChannelConversation, Session.title)
                    .outerjoin(Session, Session.id == ChannelConversation.session_id)
                    .where(ChannelConversation.channel_id == channel.id)
                    .order_by(ChannelConversation.updated_at.desc())
                )
            ).all()
            if not rows:
                lines.append("  - 还没有联系人会话（需要对方先给 bot 发一条消息）")
                continue
            for conversation, title in rows:
                name = conversation.display_name or "未命名联系人"
                lines.append(
                    f"  - {name}（conversation_id: {conversation.id}）"
                    f" · 会话: {title or '—'}"
                )
    return "\n".join(lines)


async def _delete_text(context: SkillToolContext, job_id: str) -> str:
    if not context.agent_id:
        return "Error: 当前 Agent 上下文不可用"
    async with SessionLocal() as db:
        try:
            job = await cron_service.delete_job(
                db, job_id, agent_id=context.agent_id
            )
            await db.commit()
        except CronNotFound:
            return "Error: 定时任务不存在，或不属于当前 Agent"
    return f"已删除定时任务「{job.name}」（id: {job.id}）；专属会话与历史保留。"


async def _run_text(context: SkillToolContext, job_id: str) -> str:
    if not context.agent_id:
        return "Error: 当前 Agent 上下文不可用"
    try:
        run = await cron_service.run_job_now(job_id, agent_id=context.agent_id)
    except LookupError:
        return "Error: 定时任务不存在，或不属于当前 Agent"
    except CronInactiveAgent:
        return "Error: Agent 已归档或暂停，无法运行"
    except CronBusy as exc:
        return f"Error: 任务会话里已有 {exc.status} 的 Run，请等它结束后再运行"
    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
    title = session_title(job.name) if job is not None else "定时任务会话"
    return f"已创建一次立即运行（run_id: {run.id}）；结果会出现在「{title}」会话里。"


def _make_list_tool(context: SkillToolContext) -> BaseTool:
    async def cron_list() -> str:
        """列出当前 Agent 的定时任务。"""
        return await _list_text(context)

    return StructuredTool.from_function(
        name="cron_list",
        description=(
            "列出当前 Agent 的定时任务：id、名称、启用状态、cron 表达式、时区、"
            "下次触发、最近运行状态和专属会话。修改或删除前先用它确认任务 id。"
        ),
        coroutine=cron_list,
    )


def _make_create_tool(context: SkillToolContext) -> BaseTool:
    async def cron_create(
        name: str,
        prompt: str,
        schedule: str,
        timezone: str = "",
        enabled: bool = True,
        push_channel_id: str = "",
        push_conversation_id: str = "",
    ) -> str:
        """创建定时任务并生成专属会话。"""
        return await _create_text(
            context,
            name,
            prompt,
            schedule,
            timezone,
            enabled,
            push_channel_id,
            push_conversation_id,
        )

    return StructuredTool.from_function(
        name="cron_create",
        description=(
            "为当前 Agent 创建定时任务。schedule 是 5 字段 cron（分 时 日 月 周），"
            "timezone 默认 Asia/Shanghai，enabled 默认 true。会同时创建专属会话；"
            "prompt 必须自包含，因为任务会话不继承当前对话。需要推送到微信时，先调用 "
            "cron_targets 获取渠道和联系人 id，再同时传 push_channel_id 和 "
            "push_conversation_id；不传或都传空字符串表示不推送。成功后返回任务 id、"
            "下次触发时间、会话标题和推送目标。"
        ),
        coroutine=cron_create,
    )


def _make_update_tool(context: SkillToolContext) -> BaseTool:
    async def cron_update(
        job_id: str,
        name: str | None = None,
        prompt: str | None = None,
        schedule: str | None = None,
        timezone: str | None = None,
        enabled: bool | None = None,
        push_channel_id: str | None = None,
        push_conversation_id: str | None = None,
    ) -> str:
        """修改当前 Agent 的定时任务。"""
        return await _update_text(
            context,
            job_id,
            name,
            prompt,
            schedule,
            timezone,
            enabled,
            push_channel_id,
            push_conversation_id,
        )

    return StructuredTool.from_function(
        name="cron_update",
        description=(
            "修改当前 Agent 的定时任务，只传需要修改的字段。job_id 来自 cron_list。"
            "修改表达式、时区、名称或重新启用后会重新计算下一次触发；暂停后不再触发。"
            "修改推送目标时同时传 push_channel_id 和 push_conversation_id；两个都传空字符串"
            "表示清除推送，两个都不传表示保持原目标。目标 id 用 cron_targets 获取。"
        ),
        coroutine=cron_update,
    )


def _make_targets_tool(context: SkillToolContext) -> BaseTool:
    async def cron_targets() -> str:
        """列出当前 Agent 可推送的微信渠道和联系人。"""
        return await _targets_text(context)

    return StructuredTool.from_function(
        name="cron_targets",
        description=(
            "列出当前 Agent 的微信渠道和已有 1:1 联系人会话，返回渠道 id、状态、"
            "联系人 conversation_id 和绑定的网页会话标题。创建或修改定时任务的推送目标前"
            "先用它确认目标；没有联系人会话时，需要对方先给 bot 发一条消息。"
        ),
        coroutine=cron_targets,
    )


def _make_delete_tool(context: SkillToolContext) -> BaseTool:
    async def cron_delete(job_id: str) -> str:
        """删除当前 Agent 的定时任务，保留会话。"""
        return await _delete_text(context, job_id)

    return StructuredTool.from_function(
        name="cron_delete",
        description=(
            "删除当前 Agent 的定时任务配置；专属会话、消息和 Run 历史会保留。"
            "job_id 来自 cron_list；删除前先向用户确认。"
        ),
        coroutine=cron_delete,
    )


def _make_run_tool(context: SkillToolContext) -> BaseTool:
    async def cron_run(job_id: str) -> str:
        """立即运行一次定时任务，不改变原计划。"""
        return await _run_text(context, job_id)

    return StructuredTool.from_function(
        name="cron_run",
        description=(
            "立即运行一次定时任务，不改变原定计划。job_id 来自 cron_list；"
            "任务会话已有 queued/running 的 Run 时会返回错误而不是排队。"
        ),
        coroutine=cron_run,
    )


def register_cron_tools(registry: SkillToolRegistry = SKILL_TOOL_REGISTRY) -> None:
    specs = (
        ("cron_list", _make_list_tool, "列出当前 Agent 的定时任务"),
        ("cron_create", _make_create_tool, "创建定时任务与专属会话"),
        ("cron_update", _make_update_tool, "修改定时任务"),
        ("cron_delete", _make_delete_tool, "删除定时任务并保留会话"),
        ("cron_run", _make_run_tool, "立即运行一次定时任务"),
        ("cron_targets", _make_targets_tool, "列出可推送的微信渠道和联系人"),
    )
    for name, factory, description in specs:
        if registry.has(name):
            continue
        registry.register_factory(name, factory, description=description)


# Intentionally not auto-registered: the tool layer still uses the retired
# Run/Session flow and is scheduled for removal. Nothing may register these
# globally until Task 8 replaces the cron tool contract.
