import asyncio

import pytest

from app.services.turn_stream import TurnStream


@pytest.mark.asyncio
async def test_subscriber_receives_buffered_and_live_events():
    stream = TurnStream()
    stream.publish({"type": "assistant_delta", "delta": "a"})
    queue = await stream.subscribe()
    assert await asyncio.wait_for(queue.get(), 1) == (
        0,
        {"type": "assistant_delta", "delta": "a"},
    )
    stream.publish({"type": "assistant_delta", "delta": "b"})
    assert await asyncio.wait_for(queue.get(), 1) == (
        1,
        {"type": "assistant_delta", "delta": "b"},
    )
    stream.close()
    assert await asyncio.wait_for(queue.get(), 1) == (2, {"type": "turn_end"})


@pytest.mark.asyncio
async def test_publish_after_close_is_ignored():
    stream = TurnStream()
    stream.close()
    stream.publish({"type": "assistant_delta", "delta": "x"})
    queue = await stream.subscribe()
    assert await asyncio.wait_for(queue.get(), 1) == (0, {"type": "turn_end"})


@pytest.mark.asyncio
async def test_close_is_idempotent_and_delivers_turn_end_once_per_subscriber():
    stream = TurnStream()
    first = await stream.subscribe()
    second = await stream.subscribe()
    stream.publish({"type": "assistant_delta", "delta": "a"})
    stream.close()
    stream.close()
    for queue in (first, second):
        assert await asyncio.wait_for(queue.get(), 1) == (
            0,
            {"type": "assistant_delta", "delta": "a"},
        )
        assert await asyncio.wait_for(queue.get(), 1) == (1, {"type": "turn_end"})
        assert queue.empty()


@pytest.mark.asyncio
async def test_close_does_not_duplicate_published_turn_end():
    stream = TurnStream()
    queue = await stream.subscribe()
    rich_end = {"type": "turn_end", "status": "completed"}
    stream.publish(rich_end)
    stream.close()
    assert await asyncio.wait_for(queue.get(), 1) == (0, rich_end)
    assert queue.empty()


@pytest.mark.asyncio
async def test_subscriber_after_close_receives_only_published_turn_end():
    stream = TurnStream()
    rich_end = {"type": "turn_end", "status": "completed"}
    stream.publish({"type": "assistant_delta", "delta": "a"})
    stream.publish(rich_end)
    stream.close()
    queue = await stream.subscribe()
    assert await asyncio.wait_for(queue.get(), 1) == (
        0,
        {"type": "assistant_delta", "delta": "a"},
    )
    assert await asyncio.wait_for(queue.get(), 1) == (1, rich_end)
    assert queue.empty()


@pytest.mark.asyncio
async def test_subscribe_after_skips_already_seen_events():
    stream = TurnStream()
    for delta in ("a", "b", "c"):
        stream.publish({"type": "assistant_delta", "delta": delta})
    stream.publish({"type": "turn_end", "status": "completed"})
    queue = await stream.subscribe(after=2)
    assert await asyncio.wait_for(queue.get(), 1) == (
        2,
        {"type": "assistant_delta", "delta": "c"},
    )
    assert await asyncio.wait_for(queue.get(), 1) == (
        3,
        {"type": "turn_end", "status": "completed"},
    )


@pytest.mark.asyncio
async def test_subscribe_after_beyond_buffer_waits_for_new_events():
    stream = TurnStream()
    stream.publish({"type": "assistant_delta", "delta": "a"})
    queue = await stream.subscribe(after=5)
    assert queue.empty()
    stream.publish({"type": "assistant_delta", "delta": "b"})
    assert await asyncio.wait_for(queue.get(), 1) == (
        1,
        {"type": "assistant_delta", "delta": "b"},
    )
    stream.close()
    assert await asyncio.wait_for(queue.get(), 1) == (2, {"type": "turn_end"})


@pytest.mark.asyncio
async def test_negative_after_replays_from_the_start():
    stream = TurnStream()
    stream.publish({"type": "assistant_delta", "delta": "a"})
    queue = await stream.subscribe(after=-3)
    assert await asyncio.wait_for(queue.get(), 1) == (
        0,
        {"type": "assistant_delta", "delta": "a"},
    )


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery_and_is_idempotent():
    stream = TurnStream()
    queue = await stream.subscribe()
    stream.unsubscribe(queue)
    stream.unsubscribe(queue)
    assert stream._subscribers == set()
    stream.publish({"type": "assistant_delta", "delta": "late"})
    assert queue.empty()


@pytest.mark.asyncio
async def test_unsubscribe_after_close_is_safe():
    stream = TurnStream()
    queue = await stream.subscribe()
    stream.close()
    assert await asyncio.wait_for(queue.get(), 1) == (0, {"type": "turn_end"})
    stream.unsubscribe(queue)
    assert stream._subscribers == set()
