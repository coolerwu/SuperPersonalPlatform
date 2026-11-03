import base64

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.services.weixin_client import WeixinMediaError
from app.services.weixin_media import (
    MediaRef,
    decrypt_media,
    is_public_media_url,
    parse_aes_key,
    parse_inbound,
    resolve_media_url,
    stored_media_name,
)


def _pad(data: bytes) -> bytes:
    padding = 16 - len(data) % 16
    return data + bytes([padding]) * padding


def test_parse_inbound_collects_text_quote_media_and_unsupported() -> None:
    parsed = parse_inbound(
        [
            {
                "type": 1,
                "text_item": {"text": "这次回复"},
                "ref_msg": {
                    "title": "Alice",
                    "message_item": {
                        "type": 1,
                        "text_item": {"text": "被引用的文字"},
                    },
                },
            },
            {
                "type": 2,
                "image_item": {"media": {"full_url": "https://cdn.example.com/a.png"}},
            },
            {
                "type": 4,
                "file_item": {
                    "file_name": "report.pdf",
                    "media": {"full_url": "https://cdn.example.com/report.pdf"},
                },
            },
            {"type": 3},
        ]
    )
    assert parsed.text == "这次回复"
    assert "[引用 Alice]" in parsed.quote
    assert "> 被引用的文字" in parsed.quote
    assert [media.kind for media in parsed.media] == ["image", "file"]
    assert parsed.unsupported == ["语音"]
    assert parsed.has_content is True


def test_parse_inbound_handles_quoted_media() -> None:
    parsed = parse_inbound(
        [
            {
                "type": 1,
                "text_item": {"text": "这张图呢"},
                "ref_msg": {
                    "title": "Bob",
                    "message_item": {
                        "type": 2,
                        "image_item": {
                            "media": {"full_url": "https://cdn.example.com/b.png"}
                        },
                    },
                },
            }
        ]
    )
    assert parsed.media[0].quoted is True
    assert "引用中包含图片" in parsed.quote


def test_parse_inbound_ignores_malformed_media_items() -> None:
    parsed = parse_inbound([{"type": 2}, {"type": 4}])
    assert parsed.has_content is False
    assert parsed.media == []
    assert parsed.unsupported == []


def test_media_url_prefers_full_url_and_can_be_built() -> None:
    direct = MediaRef(
        kind="image",
        item={"media": {"full_url": "https://cdn.example.com/a.png"}},
        name="",
    )
    assert resolve_media_url(direct) == "https://cdn.example.com/a.png"

    built = MediaRef(
        kind="file",
        item={
            "media": {
                "cdn_base": "https://cdn.example.com/download",
                "encrypt_query_param": "token=abc",
            }
        },
        name="a.pdf",
    )
    assert (
        resolve_media_url(built)
        == "https://cdn.example.com/download?token=abc"
    )


def test_media_url_must_not_target_private_hosts() -> None:
    assert is_public_media_url("https://cdn.example.com/a.png")
    assert is_public_media_url("http://cdn.example.com/a.png")
    assert not is_public_media_url("https://localhost/a.png")
    assert not is_public_media_url("https://127.0.0.1/a.png")
    assert not is_public_media_url("https://10.0.0.1/a.png")
    assert not is_public_media_url("ftp://cdn.example.com/a.png")


def test_aes_key_accepts_hex_and_both_base64_forms() -> None:
    key = bytes(range(16))
    assert parse_aes_key(key.hex()) == key
    assert parse_aes_key(base64.b64encode(key.hex().encode()).decode()) == key
    assert parse_aes_key(base64.b64encode(key).decode()) == key
    with pytest.raises(WeixinMediaError):
        parse_aes_key("not-a-key")


def test_decrypt_media_removes_pkcs7_padding() -> None:
    key = bytes(range(16))
    plain = "你好，微信".encode("utf-8")
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    ciphertext = encryptor.update(_pad(plain)) + encryptor.finalize()
    assert decrypt_media(ciphertext, key) == plain


def test_stored_media_name_is_deterministic_and_safe() -> None:
    first = stored_media_name("msg-1", "../图片.png")
    second = stored_media_name("msg-1", "图片.png")
    assert first == second
    assert first.startswith("wx-")
    assert first.endswith(".png")
    assert "/" not in first
