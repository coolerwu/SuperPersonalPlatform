"""Channel management and WeChat QR login endpoints (single-user cookie auth).

Channel configuration and contact bindings live in ``data/settings.json``.
Pending QR logins are in-process state on ``WeixinRuntime``; a restart simply
asks the user to scan again.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_db
from app.models import ChannelDelivery, ChannelInbound, CronJob
from app.schemas import (
    ChannelConversationOut,
    ChannelCreate,
    ChannelLoginOut,
    ChannelLoginStatusOut,
    ChannelOut,
    ChannelUpdate,
)
from app.services.settings_store import (
    ChannelSection,
    ConversationSection,
    PlatformSettings,
    SettingsStore,
    get_settings_store,
)
from app.services.weixin_client import ILINK_BASE_URL, WeixinError, qr_svg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"], dependencies=[Depends(require_auth)])

QR_LOGIN_SECONDS = 480


def _runtime(request: Request):
    return getattr(request.app.state, "weixin", None)


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_agent(snapshot: PlatformSettings, agent_id: str):
    # Paused and archived Agents keep their channels manageable so the owner
    # can inspect, edit or delete them; only polling is suspended.
    return next((item for item in snapshot.agents if item.id == agent_id), None)


def _find_channel(snapshot: PlatformSettings, channel_id: str) -> ChannelSection | None:
    channel = next((item for item in snapshot.channels if item.id == channel_id), None)
    if channel is None or channel.type != "wechat":
        return None
    return channel


def _channel_out(snapshot: PlatformSettings, channel: ChannelSection) -> ChannelOut:
    created = channel.created_at or _now_iso()
    return ChannelOut(
        id=channel.id,
        agent_id=channel.agent_id,
        type=channel.type,
        name=channel.name,
        status=channel.status,
        account_id=channel.account_id,
        has_credentials=bool(channel.account_id and channel.bot_token),
        conversation_count=sum(
            1
            for conversation in snapshot.channel_conversations.values()
            if conversation.channel_id == channel.id
        ),
        last_seen_at=channel.last_seen_at,
        last_error=channel.last_error,
        created_at=created,
        updated_at=channel.updated_at or created,
    )


async def _load_channel(store: SettingsStore, channel_id: str) -> ChannelSection:
    snapshot = await store.get()
    channel = _find_channel(snapshot, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")
    return channel


async def _patch_channel(
    store: SettingsStore, channel_id: str, **fields: object
) -> None:
    fields.setdefault("updated_at", _now_iso())
    await store.update_channel_fields(channel_id, **fields)


async def _channel_by_id(store: SettingsStore, channel_id: str) -> ChannelOut:
    snapshot = await store.get()
    channel = _find_channel(snapshot, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")
    return _channel_out(snapshot, channel)


@router.get("", response_model=list[ChannelOut])
async def list_channels(
    request: Request, agent_id: str = Query(..., min_length=1)
) -> list[ChannelOut]:
    snapshot = await _store(request).get()
    if _find_agent(snapshot, agent_id) is None:
        raise HTTPException(status_code=404, detail="agent not found")
    channels = [
        channel for channel in snapshot.channels if channel.agent_id == agent_id
    ]
    channels.sort(key=lambda channel: channel.created_at or channel.id)
    return [_channel_out(snapshot, channel) for channel in channels]


@router.get("/{channel_id}/conversations", response_model=list[ChannelConversationOut])
async def list_channel_conversations(
    channel_id: str, request: Request
) -> list[ChannelConversationOut]:
    snapshot = await _store(request).get()
    if _find_channel(snapshot, channel_id) is None:
        raise HTTPException(status_code=404, detail="channel not found")

    prefix = f"{channel_id}:"
    rows: list[tuple[str, ConversationSection, str]] = []
    for key, conversation in snapshot.channel_conversations.items():
        if conversation.channel_id != channel_id:
            continue
        external_id = key[len(prefix):] if key.startswith(prefix) else key
        rows.append((key, conversation, external_id))
    rows.sort(key=lambda row: row[1].updated_at or "", reverse=True)

    result: list[ChannelConversationOut] = []
    for key, conversation, external_id in rows:
        updated = conversation.updated_at or _now_iso()
        result.append(
            ChannelConversationOut(
                id=key,
                channel_id=channel_id,
                external_id=external_id,
                display_name=conversation.display_name or "未命名联系人",
                session_id=conversation.session_id,
                session_title="",
                created_at=updated,
                updated_at=updated,
            )
        )
    return result


@router.post("", response_model=ChannelOut, status_code=201)
async def create_channel(payload: ChannelCreate, request: Request) -> ChannelOut:
    store = _store(request)
    snapshot = await store.get()
    if _find_agent(snapshot, payload.agent_id) is None:
        raise HTTPException(status_code=404, detail="agent not found")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    now = _now_iso()
    channel = ChannelSection(
        id=uuid.uuid4().hex,
        agent_id=payload.agent_id,
        type="wechat",
        name=name,
        status="pairing",
        created_at=now,
        updated_at=now,
    )
    await store.update_channel(channel)
    return _channel_out((await store.get()), channel)


@router.put("/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: str,
    payload: ChannelUpdate,
    request: Request,
) -> ChannelOut:
    store = _store(request)
    channel = await _load_channel(store, channel_id)
    data = payload.model_dump(exclude_unset=True)

    fields: dict[str, object] = {}
    if data.get("name"):
        name = str(data["name"]).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        fields["name"] = name
    next_status = None
    if "status" in data and data["status"] is not None:
        next_status = str(data["status"])
        if next_status == "active" and not (channel.account_id and channel.bot_token):
            raise HTTPException(
                status_code=400, detail="channel has not completed WeChat login"
            )
        fields["status"] = next_status

    if fields:
        await _patch_channel(store, channel_id, **fields)

    runtime = _runtime(request)
    if runtime is not None and next_status is not None:
        if next_status == "active":
            await runtime.start_channel(channel_id)
            await runtime.sweep_deliveries(channel_id)
        else:
            await runtime.stop_channel(channel_id)
    return await _channel_by_id(store, channel_id)


@router.delete("/{channel_id}")
async def delete_channel(
    channel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    store = _store(request)
    channel = await _load_channel(store, channel_id)
    runtime = _runtime(request)
    if runtime is not None:
        await runtime.stop_channel(channel.id)
        runtime.clear_login(channel.id)

    await db.execute(
        update(CronJob)
        .where(CronJob.push_channel_id == channel.id)
        .values(push_channel_id=None, push_conversation_id=None)
    )
    await db.execute(
        delete(ChannelDelivery).where(ChannelDelivery.channel_id == channel.id)
    )
    await db.execute(
        delete(ChannelInbound).where(ChannelInbound.channel_id == channel.id)
    )
    await db.commit()

    def remove_channel(data: dict) -> None:
        channels = data.get("channels")
        if isinstance(channels, list):
            data["channels"] = [
                item
                for item in channels
                if not (isinstance(item, dict) and item.get("id") == channel.id)
            ]
        conversations = data.get("channel_conversations")
        if isinstance(conversations, dict):
            for key, value in list(conversations.items()):
                if isinstance(value, dict) and value.get("channel_id") == channel.id:
                    conversations.pop(key, None)

    await store.mutate_raw(remove_channel)
    return {"deleted": True}


@router.post("/{channel_id}/wechat/login", response_model=ChannelLoginOut)
async def start_wechat_login(channel_id: str, request: Request) -> ChannelLoginOut:
    store = _store(request)
    channel = await _load_channel(store, channel_id)
    if channel.status == "active":
        raise HTTPException(
            status_code=409, detail="channel is already connected; use relogin"
        )
    runtime = _runtime(request)
    if runtime is None:
        raise HTTPException(status_code=503, detail="WeChat runtime is not available")
    client = runtime.new_client()
    try:
        data = await client.fetch_qr()
    except WeixinError as exc:
        logger.warning("weixin QR request failed channel=%s: %s", channel_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("weixin QR request failed channel=%s", channel_id)
        raise HTTPException(
            status_code=502, detail="failed to request WeChat QR code"
        ) from exc
    finally:
        await client.aclose()

    qrcode = str(data.get("qrcode") or "").strip()
    scan_url = str(data.get("qrcode_img_content") or "").strip() or qrcode
    if not qrcode or not scan_url:
        raise HTTPException(status_code=502, detail="WeChat QR response was incomplete")

    base_url = ILINK_BASE_URL
    runtime.set_login(channel.id, qrcode=qrcode, scan_url=scan_url, base_url=base_url)
    await _patch_channel(
        store, channel.id, status="pairing", last_error=None
    )
    return ChannelLoginOut(
        qrcode=qrcode,
        scan_url=scan_url,
        qr_svg=qr_svg(scan_url),
        expires_in=QR_LOGIN_SECONDS,
    )


@router.get("/{channel_id}/wechat/login/status", response_model=ChannelLoginStatusOut)
async def wechat_login_status(
    channel_id: str,
    request: Request,
    qrcode: str = Query(..., min_length=1),
) -> ChannelLoginStatusOut:
    store = _store(request)
    channel = await _load_channel(store, channel_id)
    runtime = _runtime(request)
    if runtime is None:
        raise HTTPException(status_code=503, detail="WeChat runtime is not available")

    login = runtime.get_login(channel.id)
    if login is not None and login.get("qrcode") and login["qrcode"] != qrcode:
        raise HTTPException(
            status_code=409, detail="QR code does not match the active login"
        )
    base_url = (login or {}).get("base_url") or channel.base_url or ILINK_BASE_URL

    client = runtime.new_client()
    try:
        data = await client.qr_status(qrcode, base_url=base_url)
    except WeixinError as exc:
        logger.warning("weixin QR status failed channel=%s: %s", channel_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("weixin QR status failed channel=%s", channel_id)
        raise HTTPException(
            status_code=502, detail="failed to query WeChat QR status"
        ) from exc
    finally:
        await client.aclose()

    status = str(data.get("status") or "wait")
    if status == "scaned_but_redirect" and data.get("redirect_host"):
        redirect_host = str(data["redirect_host"]).strip()
        if redirect_host:
            base_url = f"https://{redirect_host}"
            runtime.set_login(
                channel.id,
                qrcode=qrcode,
                scan_url=(login or {}).get("scan_url") or qrcode,
                base_url=base_url,
            )
        return ChannelLoginStatusOut(status=status)

    if status != "confirmed":
        return ChannelLoginStatusOut(status=status)

    account_id = str(data.get("ilink_bot_id") or "").strip()
    bot_token = str(data.get("bot_token") or "").strip()
    if not account_id or not bot_token:
        raise HTTPException(status_code=502, detail="WeChat login response was incomplete")
    await _patch_channel(
        store,
        channel.id,
        account_id=account_id,
        bot_token=bot_token,
        base_url=str(data.get("baseurl") or base_url).rstrip("/"),
        ilink_user_id=str(data.get("ilink_user_id") or ""),
        status="active",
        last_error=None,
        sync_buf="",
    )
    runtime.clear_login(channel.id)
    await runtime.start_channel(channel.id)
    await runtime.sweep_deliveries(channel.id)
    return ChannelLoginStatusOut(status="confirmed", account_id=account_id)


@router.post("/{channel_id}/wechat/relogin", response_model=ChannelOut)
async def relogin_wechat(channel_id: str, request: Request) -> ChannelOut:
    store = _store(request)
    channel = await _load_channel(store, channel_id)
    runtime = _runtime(request)
    if runtime is not None:
        await runtime.stop_channel(channel.id)
        runtime.clear_login(channel.id)
    await _patch_channel(
        store,
        channel.id,
        status="pairing",
        bot_token="",
        account_id="",
        ilink_user_id="",
        sync_buf="",
        last_error=None,
    )
    return await _channel_by_id(store, channel.id)
