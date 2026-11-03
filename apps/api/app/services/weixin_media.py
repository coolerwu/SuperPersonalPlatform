"""Inbound WeChat media: quoted items, CDN download, and AES-128-ECB decrypt.

iLink delivers text, image, voice, file and video items in ``item_list``. A
reply may also carry ``ref_msg.message_item`` for the quoted message. v1 reads
text, images and files; voice and video are acknowledged but not downloaded.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.services.attachments import (
    SavedAttachment,
    detect_image_mime,
    extension_for_mime,
    safe_attachment_name,
    save_attachment_bytes,
)
from app.services.weixin_client import (
    ITEM_FILE,
    ITEM_IMAGE,
    ITEM_TEXT,
    ITEM_VIDEO,
    ITEM_VOICE,
    WeixinMediaError,
)

logger = logging.getLogger(__name__)

_HEX_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_QUOTE_TEXT_LIMIT = 1200


@dataclass(frozen=True)
class MediaRef:
    kind: str
    item: dict[str, Any]
    name: str
    quoted: bool = False


@dataclass(frozen=True)
class ParsedInbound:
    text: str
    quote: str
    media: list[MediaRef]
    unsupported: list[str]

    @property
    def has_content(self) -> bool:
        return bool(self.text.strip() or self.quote.strip() or self.media)


def _text_of(item: dict[str, Any]) -> str:
    if item.get("type") != ITEM_TEXT:
        return ""
    return str((item.get("text_item") or {}).get("text") or "")


def _media_ref(item: dict[str, Any], *, quoted: bool) -> MediaRef | None:
    item_type = item.get("type")
    if item_type == ITEM_IMAGE:
        payload = item.get("image_item") or {}
        if not isinstance(payload, dict) or not payload:
            return None
        media = payload.get("media") or {}
        if not isinstance(media, dict) or not media:
            return None
        return MediaRef(kind="image", item=payload, name="", quoted=quoted)
    if item_type == ITEM_FILE:
        payload = item.get("file_item") or {}
        if not isinstance(payload, dict) or not payload:
            return None
        media = payload.get("media") or {}
        if not isinstance(media, dict) or not media:
            return None
        name = str(payload.get("file_name") or payload.get("name") or "文件")
        return MediaRef(kind="file", item=payload, name=name, quoted=quoted)
    return None


def _unsupported_kind(item_type: Any) -> str:
    if item_type == ITEM_VOICE:
        return "语音"
    if item_type == ITEM_VIDEO:
        return "视频"
    return ""


def parse_inbound(item_list: list[Any] | None) -> ParsedInbound:
    texts: list[str] = []
    quotes: list[str] = []
    media: list[MediaRef] = []
    unsupported: list[str] = []

    for raw in item_list or []:
        if not isinstance(raw, dict):
            continue
        item_type = raw.get("type")
        text = _text_of(raw)
        if text.strip():
            texts.append(text)
        ref = raw.get("ref_msg") or {}
        ref_item = ref.get("message_item") if isinstance(ref, dict) else None
        if isinstance(ref_item, dict):
            ref_text = _text_of(ref_item).strip()
            ref_media = _media_ref(ref_item, quoted=True)
            ref_unsupported = _unsupported_kind(ref_item.get("type"))
            if ref_media is not None:
                media.append(ref_media)
            title = ""
            if isinstance(ref, dict):
                title = str(
                    ref.get("title")
                    or ref.get("from_user_name")
                    or ref.get("sender_name")
                    or ""
                ).strip()
            quote_lines = [f"[引用 {title}]" if title else "[引用]"]
            if ref_text:
                clipped = ref_text[:_QUOTE_TEXT_LIMIT]
                quote_lines.extend(f"> {line}" for line in clipped.splitlines())
            if ref_media is not None:
                quote_lines.append(f"（引用中包含{ref_media.kind == 'image' and '图片' or '文件'}）")
            if ref_unsupported:
                quote_lines.append(f"（引用中包含{ref_unsupported}，暂不支持）")
            quotes.append("\n".join(quote_lines))
        direct_media = _media_ref(raw, quoted=False)
        if direct_media is not None:
            media.append(direct_media)
        elif item_type in (ITEM_IMAGE, ITEM_FILE):
            logger.warning(
                "weixin %s item has no media payload: keys=%s",
                "image" if item_type == ITEM_IMAGE else "file",
                sorted(str(key) for key in raw.keys()),
            )
        kind = _unsupported_kind(item_type)
        if kind:
            unsupported.append(kind)

    return ParsedInbound(
        text="\n".join(texts).strip(),
        quote="\n\n".join(quotes).strip(),
        media=media,
        unsupported=unsupported,
    )


def _media_payload(media_ref: MediaRef) -> dict[str, Any]:
    return media_ref.item.get("media") or {}


def resolve_media_url(media_ref: MediaRef) -> str:
    media = _media_payload(media_ref)
    for key in ("full_url", "fullUrl", "url"):
        value = str(media.get(key) or "").strip()
        if value:
            return value
    param = str(media.get("encrypt_query_param") or "").strip()
    base = str(
        media.get("cdn_base")
        or media.get("cdn_url")
        or media.get("cdn_base_url")
        or ""
    ).strip()
    if param and base:
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}{param}"
    raise WeixinMediaError("weixin media has no download url")


def is_public_media_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
    )


def _decode_key_bytes(raw: bytes) -> bytes:
    if len(raw) == 16:
        return raw
    if len(raw) == 32:
        try:
            return bytes.fromhex(raw.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            pass
    raise WeixinMediaError("weixin media aes key has an unsupported length")


def parse_aes_key(value: Any) -> bytes:
    if isinstance(value, bytes):
        return _decode_key_bytes(value)
    text = str(value or "").strip().strip('"').strip("'")
    if text.lower().startswith("base64:"):
        text = text[7:]
    if _HEX_RE.fullmatch(text):
        return bytes.fromhex(text)
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WeixinMediaError("weixin media aes key is invalid") from exc
    return _decode_key_bytes(decoded)


def decrypt_media(data: bytes, key: bytes) -> bytes:
    if not data or len(data) % 16 != 0:
        raise WeixinMediaError("weixin media ciphertext is invalid")
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    plain = decryptor.update(data) + decryptor.finalize()
    if not plain:
        return plain
    padding = plain[-1]
    if 1 <= padding <= 16 and plain.endswith(bytes([padding]) * padding):
        plain = plain[:-padding]
    return plain


def _safe_media_name(name: str, fallback: str, mime: str | None) -> str:
    value = safe_attachment_name(name or fallback)
    suffix = Path(value).suffix
    if not suffix:
        suffix = extension_for_mime(mime or "")
        if suffix != ".bin":
            value = f"{value}{suffix}"
    if len(value.encode("utf-8")) > 150 and suffix:
        stem = value[: -len(suffix)]
        while len((stem + suffix).encode("utf-8")) > 150:
            stem = stem[:-1]
        value = f"{stem}{suffix}"
    return value or fallback


def stored_media_name(message_id: str, display_name: str) -> str:
    digest = hashlib.sha1(message_id.encode("utf-8")).hexdigest()[:10]
    return f"wx-{digest}-{safe_attachment_name(display_name)}"


async def download_and_save(
    client: Any,
    media_ref: MediaRef,
    *,
    agent_id: str,
    message_id: str,
    max_bytes: int,
) -> SavedAttachment:
    url = resolve_media_url(media_ref)
    if not is_public_media_url(url):
        raise WeixinMediaError("weixin media url is not public")
    raw = await client.download(url, max_bytes=max_bytes)
    media = _media_payload(media_ref)
    aes_key = (
        media_ref.item.get("aeskey")
        or media.get("aes_key")
        or media.get("aeskey")
    )
    if aes_key:
        data = decrypt_media(raw, parse_aes_key(aes_key))
    else:
        data = raw
    mime = detect_image_mime(data)
    fallback = "图片" if media_ref.kind == "image" else "文件"
    display = _safe_media_name(media_ref.name, fallback, mime)
    stored = stored_media_name(f"{message_id}:{url}", display)
    return save_attachment_bytes(
        agent_id,
        stored,
        data,
        reuse_existing=True,
        display_name=display,
    )


def unsupported_text(kinds: list[str]) -> str:
    labels = "、".join(dict.fromkeys(kinds))
    return f"（收到一条{labels}消息，暂不支持；请发文字、图片或文件）"
