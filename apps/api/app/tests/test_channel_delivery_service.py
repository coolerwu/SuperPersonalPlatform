import pytest
from sqlalchemy import delete

from app.db import SessionLocal, engine, init_db
from app.models import ChannelDelivery
from app.services import channel_delivery


async def _cleanup(*delivery_ids: int | None) -> None:
    ids = [delivery_id for delivery_id in delivery_ids if delivery_id is not None]
    if not ids:
        return
    async with SessionLocal() as db:
        await db.execute(delete(ChannelDelivery).where(ChannelDelivery.id.in_(ids)))
        await db.commit()


async def _create(session_id: str = "s1") -> int:
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_pending(
            db,
            channel_id="ch1",
            external_id="wx-1",
            session_id=session_id,
        )
        await db.commit()
    return delivery_id


@pytest.mark.asyncio
async def test_pending_to_delivered():
    await init_db()
    delivery_id = None
    try:
        async with SessionLocal() as db:
            delivery_id = await channel_delivery.create_pending(
                db,
                channel_id="ch1",
                external_id="wx-1",
                session_id="11111111-1111-1111-1111-111111111111",
            )
            await db.commit()
        row = await channel_delivery.get(delivery_id)
        assert row is not None and row.status == "pending"
        assert await channel_delivery.complete(delivery_id, "hello") is True
        # complete() fills the content but keeps the row deliverable; the sender
        # records the attempt and final sent state.
        row = await channel_delivery.get(delivery_id)
        assert row is not None and row.status == "pending" and row.content == "hello"
        assert await channel_delivery.mark_attempting(delivery_id) is True
        row = await channel_delivery.get(delivery_id)
        assert row is not None and row.status == "attempting" and row.attempts == 1
        assert await channel_delivery.mark_sent(delivery_id) is True
        row = await channel_delivery.get(delivery_id)
        assert row is not None and row.status == "sent" and row.content == "hello"
    finally:
        await _cleanup(delivery_id)


@pytest.mark.asyncio
async def test_create_pending_rollback_leaves_no_row():
    await init_db()
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_pending(
            db, channel_id="ch1", external_id="wx-rollback", session_id="s1"
        )
        await db.rollback()
    assert await channel_delivery.get(delivery_id) is None


@pytest.mark.asyncio
async def test_mark_attempting_claims_only_live_rows():
    await init_db()
    delivery_id = None
    try:
        delivery_id = await _create()
        assert await channel_delivery.complete(delivery_id, "hello") is True
        assert await channel_delivery.mark_attempting(delivery_id) is True
        row = await channel_delivery.get(delivery_id)
        assert row is not None and row.status == "attempting" and row.attempts == 1
        assert await channel_delivery.mark_sent(delivery_id) is True
        assert await channel_delivery.mark_attempting(delivery_id) is False
        row = await channel_delivery.get(delivery_id)
        assert row is not None and row.status == "sent" and row.attempts == 1
    finally:
        await _cleanup(delivery_id)


@pytest.mark.asyncio
async def test_mark_pending_fails_at_max_attempts_and_skips_terminal_rows():
    await init_db()
    failed_id = None
    sent_id = None
    try:
        failed_id = await _create()
        await channel_delivery.complete(failed_id, "first")
        await channel_delivery.mark_attempting(failed_id)
        assert (
            await channel_delivery.mark_pending(
                failed_id, "boom", max_attempts=1
            )
            is True
        )
        row = await channel_delivery.get(failed_id)
        assert row is not None and row.status == "failed" and row.last_error == "boom"
        assert (
            await channel_delivery.mark_pending(
                failed_id, "again", max_attempts=1
            )
            is False
        )
        assert await channel_delivery.mark_sent(failed_id) is False
        row = await channel_delivery.get(failed_id)
        assert row is not None and row.status == "failed"

        sent_id = await _create()
        await channel_delivery.complete(sent_id, "second")
        await channel_delivery.mark_attempting(sent_id)
        await channel_delivery.mark_sent(sent_id)
        assert (
            await channel_delivery.mark_pending(
                sent_id, "late", max_attempts=9
            )
            is False
        )
        row = await channel_delivery.get(sent_id)
        assert row is not None and row.status == "sent" and row.last_error is None
    finally:
        await _cleanup(failed_id, sent_id)


@pytest.mark.asyncio
async def test_complete_does_not_overwrite_terminal_rows():
    await init_db()
    sent_id = None
    failed_id = None
    try:
        sent_id = await _create()
        await channel_delivery.complete(sent_id, "first")
        await channel_delivery.mark_attempting(sent_id)
        await channel_delivery.mark_sent(sent_id)
        assert await channel_delivery.complete(sent_id, "second") is False
        row = await channel_delivery.get(sent_id)
        assert row is not None and row.status == "sent" and row.content == "first"

        failed_id = await _create()
        await channel_delivery.complete(failed_id, "first")
        await channel_delivery.mark_attempting(failed_id)
        await channel_delivery.mark_pending(failed_id, "boom", max_attempts=1)
        assert await channel_delivery.complete(failed_id, "second") is False
        row = await channel_delivery.get(failed_id)
        assert row is not None and row.status == "failed" and row.content == "first"
    finally:
        await _cleanup(sent_id, failed_id)


@pytest.mark.asyncio
async def test_pending_list_orders_by_id():
    await init_db()
    first = None
    second = None
    try:
        first = await _create()
        second = await _create()
        await channel_delivery.complete(first, "first")
        await channel_delivery.complete(second, "second")
        rows = await channel_delivery.list_pending()
        ids = [row.id for row in rows]
        assert ids.index(first) < ids.index(second)
    finally:
        await _cleanup(first, second)


@pytest.mark.asyncio
async def test_list_pending_skips_empty_content_and_terminal_rows():
    await init_db()
    empty_id = None
    pending_id = None
    attempting_id = None
    sent_id = None
    failed_id = None
    try:
        empty_id = await _create()
        pending_id = await _create()
        await channel_delivery.complete(pending_id, "pending content")
        attempting_id = await _create()
        await channel_delivery.complete(attempting_id, "attempting content")
        await channel_delivery.mark_attempting(attempting_id)
        sent_id = await _create()
        await channel_delivery.complete(sent_id, "sent content")
        await channel_delivery.mark_attempting(sent_id)
        await channel_delivery.mark_sent(sent_id)
        failed_id = await _create()
        await channel_delivery.complete(failed_id, "failed content")
        await channel_delivery.mark_attempting(failed_id)
        await channel_delivery.mark_pending(failed_id, "boom", max_attempts=1)

        rows = await channel_delivery.list_pending()
        ids = [row.id for row in rows]
        assert pending_id in ids
        assert attempting_id in ids
        assert empty_id not in ids
        assert sent_id not in ids
        assert failed_id not in ids
        assert ids.index(pending_id) < ids.index(attempting_id)
    finally:
        await _cleanup(empty_id, pending_id, attempting_id, sent_id, failed_id)


@pytest.mark.asyncio
async def test_mark_pending_retries_until_limit_and_truncates_error():
    await init_db()
    delivery_id = None
    try:
        delivery_id = await _create()
        await channel_delivery.complete(delivery_id, "hello")
        error = RuntimeError("x" * 2100)

        assert await channel_delivery.mark_attempting(delivery_id) is True
        assert (
            await channel_delivery.mark_pending(
                delivery_id, error, max_attempts=2
            )
            is True
        )
        row = await channel_delivery.get(delivery_id)
        assert row is not None and row.status == "pending"
        assert row.last_error == "x" * 2000

        assert await channel_delivery.mark_attempting(delivery_id) is True
        assert (
            await channel_delivery.mark_pending(
                delivery_id, error, max_attempts=2
            )
            is True
        )
        row = await channel_delivery.get(delivery_id)
        assert row is not None and row.status == "failed"
        assert row.last_error == "x" * 2000
    finally:
        await _cleanup(delivery_id)


@pytest.mark.asyncio
async def test_discard_removes_only_live_rows():
    await init_db()
    pending_id = None
    attempting_id = None
    sent_id = None
    failed_id = None
    try:
        pending_id = await _create()
        attempting_id = await _create()
        await channel_delivery.complete(attempting_id, "content")
        await channel_delivery.mark_attempting(attempting_id)
        sent_id = await _create()
        await channel_delivery.complete(sent_id, "content")
        await channel_delivery.mark_attempting(sent_id)
        await channel_delivery.mark_sent(sent_id)
        failed_id = await _create()
        await channel_delivery.complete(failed_id, "content")
        await channel_delivery.mark_attempting(failed_id)
        await channel_delivery.mark_pending(failed_id, "boom", max_attempts=1)

        assert await channel_delivery.discard(pending_id) is True
        assert await channel_delivery.get(pending_id) is None
        assert await channel_delivery.discard(attempting_id) is True
        assert await channel_delivery.get(attempting_id) is None

        # Terminal rows are delivery history, not pending obligations.
        assert await channel_delivery.discard(sent_id) is False
        row = await channel_delivery.get(sent_id)
        assert row is not None and row.status == "sent"
        assert await channel_delivery.discard(failed_id) is False
        row = await channel_delivery.get(failed_id)
        assert row is not None and row.status == "failed"
    finally:
        await _cleanup(pending_id, attempting_id, sent_id, failed_id)


@pytest.mark.asyncio
async def test_find_unfilled_for_session_skips_filled_rows():
    await init_db()
    filled_id = None
    empty_id = None
    try:
        filled_id = await _create(session_id="s-unfilled")
        await channel_delivery.complete(filled_id, "already owed")
        empty_id = await _create(session_id="s-unfilled")

        row = await channel_delivery.find_unfilled_for_session("s-unfilled")
        assert row is not None and row.id == empty_id and row.content == ""
        assert (
            await channel_delivery.find_unfilled_for_session("s-missing") is None
        )
    finally:
        await _cleanup(filled_id, empty_id)


@pytest.mark.asyncio
async def test_find_unfilled_ignores_queued_rows():
    await init_db()
    queued_id = pending_id = None
    try:
        async with SessionLocal() as db:
            queued_id = await channel_delivery.create_queued(
                db, channel_id="ch1", external_id="wx-q", session_id="queued-session"
            )
            pending_id = await channel_delivery.create_pending(
                db, channel_id="ch1", external_id="wx-p", session_id="queued-session"
            )
            await db.commit()
        found = await channel_delivery.find_unfilled_for_session("queued-session")
        assert found is not None
        assert found.id == pending_id
        assert await channel_delivery.get(queued_id) is not None
    finally:
        await _cleanup(queued_id, pending_id)


@pytest.mark.asyncio
async def test_mark_started_promotes_only_a_queued_row():
    await init_db()
    delivery_id = None
    try:
        async with SessionLocal() as db:
            delivery_id = await channel_delivery.create_queued(
                db, channel_id="ch1", external_id="wx-s", session_id="s1"
            )
            await db.commit()
        async with SessionLocal() as db:
            assert await channel_delivery.mark_started_in(db, delivery_id) is True
            await db.commit()
        row = await channel_delivery.get(delivery_id)
        assert row is not None and row.status == "pending"
        async with SessionLocal() as db:
            assert await channel_delivery.mark_started_in(db, delivery_id) is False
            await db.rollback()
    finally:
        await _cleanup(delivery_id)


@pytest.mark.asyncio
async def test_fail_queued_for_session_answers_only_queued_rows():
    await init_db()
    queued_id = pending_id = None
    try:
        async with SessionLocal() as db:
            queued_id = await channel_delivery.create_queued(
                db, channel_id="ch1", external_id="wx-q", session_id="s1"
            )
            pending_id = await channel_delivery.create_pending(
                db, channel_id="ch1", external_id="wx-p", session_id="s1"
            )
            await db.commit()
        assert await channel_delivery.fail_queued_for_session(
            "s1", "lost", "queued turn lost"
        ) == 1
        row = await channel_delivery.get(queued_id)
        assert row is not None and row.status == "pending" and row.content == "lost"
        other = await channel_delivery.get(pending_id)
        assert other is not None and other.status == "pending" and other.content == ""
    finally:
        await _cleanup(queued_id, pending_id)


@pytest.mark.asyncio
async def test_init_db_rebuilds_stale_channel_deliveries_table():
    async with engine.begin() as conn:
        await conn.exec_driver_sql("DROP TABLE IF EXISTS channel_deliveries")
        await conn.exec_driver_sql(
            "CREATE TABLE channel_deliveries ("
            "run_id VARCHAR(32) PRIMARY KEY, "
            "channel_id VARCHAR(32), "
            "conversation_id VARCHAR(32), "
            "content TEXT, "
            "status VARCHAR(20), "
            "attempts INTEGER, "
            "last_error TEXT, "
            "created_at DATETIME, "
            "updated_at DATETIME, "
            "delivered_at DATETIME)"
        )
        await conn.exec_driver_sql(
            "INSERT INTO channel_deliveries (run_id, channel_id, content, status) "
            "VALUES ('old-run', 'ch-old', 'legacy', 'pending')"
        )

    await init_db()

    async with engine.begin() as conn:
        result = await conn.exec_driver_sql("PRAGMA table_info(channel_deliveries)")
        columns = {row[1] for row in result.fetchall()}
    assert "id" in columns
    assert "session_id" in columns
    assert "run_id" not in columns

    delivery_id = None
    try:
        delivery_id = await _create(session_id="s-after-rebuild")
        row = await channel_delivery.get(delivery_id)
        assert row is not None and row.id == delivery_id
    finally:
        await _cleanup(delivery_id)


@pytest.mark.asyncio
async def test_fail_unfilled_for_session_fills_only_empty_live_rows():
    await init_db()
    empty_id = filled_id = None
    try:
        empty_id = await _create("session-empty")
        filled_id = await _create("session-empty")
        await channel_delivery.complete(filled_id, "already owed")

        changed = await channel_delivery.fail_unfilled_for_session(
            "session-empty", "服务重启时这条消息尚未开始处理", "lost"
        )

        assert changed == 1
        empty = await channel_delivery.get(empty_id)
        assert empty is not None and empty.status == "pending"
        assert empty.content == "服务重启时这条消息尚未开始处理"
        assert empty.last_error == "lost"
        filled = await channel_delivery.get(filled_id)
        assert filled is not None and filled.content == "already owed"
    finally:
        await _cleanup(empty_id, filled_id)


@pytest.mark.asyncio
async def test_mark_attempting_error_uses_claim_count_and_stops_at_limit():
    await init_db()
    delivery_id = None
    try:
        delivery_id = await _create()
        await channel_delivery.complete(delivery_id, "hello")

        # max_attempts counts send attempts: the claim already incremented
        # attempts, so failures 1 and 2 stay retryable and failure 3 is final.
        for expected_attempts, expected_status in (
            (1, "attempting"),
            (2, "attempting"),
            (3, "failed"),
        ):
            assert await channel_delivery.mark_attempting(delivery_id) is True
            assert (
                await channel_delivery.mark_attempting_error(
                    delivery_id, f"failure {expected_attempts}", max_attempts=3
                )
                is True
            )
            row = await channel_delivery.get(delivery_id)
            assert row is not None
            assert row.status == expected_status
            assert row.attempts == expected_attempts
            assert f"failure {expected_attempts}" in (row.last_error or "")
            if expected_status == "failed":
                break

        assert await channel_delivery.mark_sent(delivery_id) is False
    finally:
        await _cleanup(delivery_id)


@pytest.mark.asyncio
async def test_has_for_session():
    await init_db()
    delivery_id = None
    try:
        delivery_id = await _create("session-has")
        assert await channel_delivery.has_for_session("session-has") is True
        assert await channel_delivery.has_for_session("session-missing") is False
    finally:
        await _cleanup(delivery_id)
