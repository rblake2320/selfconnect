"""
test_event_bus.py — Unit tests for the in-process pub/sub event bus.
"""
import asyncio
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision_server.services import event_bus


@pytest.fixture(autouse=True)
def reset_bus():
    """Clear all subscribers before each test."""
    event_bus._subscribers.clear()
    yield
    event_bus._subscribers.clear()


class TestSubscribePublish:
    @pytest.mark.asyncio
    async def test_subscribe_and_receive(self):
        received = []

        async def callback(msg):
            received.append(msg)

        await event_bus.subscribe("log", callback)
        await event_bus.publish("log", {"t": "12:00:00.000", "msg": "test"})

        assert len(received) == 1
        import json
        data = json.loads(received[0])
        assert data["channel"] == "log"
        assert data["data"]["msg"] == "test"

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        received_a = []
        received_b = []

        async def cb_a(msg): received_a.append(msg)
        async def cb_b(msg): received_b.append(msg)

        await event_bus.subscribe("health", cb_a)
        await event_bus.subscribe("health", cb_b)
        await event_bus.publish("health", {"sdk": "ok"})

        assert len(received_a) == 1
        assert len(received_b) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        received = []

        async def callback(msg):
            received.append(msg)

        await event_bus.subscribe("detections", callback)
        await event_bus.unsubscribe("detections", callback)
        await event_bus.publish("detections", [])

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_is_safe(self):
        async def callback(msg): pass
        # Should not raise
        await event_bus.unsubscribe("log", callback)

    @pytest.mark.asyncio
    async def test_channel_isolation(self):
        received_log = []
        received_queue = []

        async def cb_log(msg): received_log.append(msg)
        async def cb_queue(msg): received_queue.append(msg)

        await event_bus.subscribe("log", cb_log)
        await event_bus.subscribe("queue", cb_queue)

        await event_bus.publish("log", {"msg": "log event"})

        assert len(received_log) == 1
        assert len(received_queue) == 0


class TestSubscribeAll:
    @pytest.mark.asyncio
    async def test_subscribe_all_channels(self):
        received = []

        async def callback(msg):
            received.append(msg)

        await event_bus.subscribe_all(callback)

        await event_bus.publish("log", {"msg": "a"})
        await event_bus.publish("health", {"sdk": "ok"})
        await event_bus.publish("detections", [])

        assert len(received) == 3

    @pytest.mark.asyncio
    async def test_unsubscribe_all(self):
        received = []

        async def callback(msg):
            received.append(msg)

        await event_bus.subscribe_all(callback)
        await event_bus.unsubscribe_all(callback)

        await event_bus.publish("log", {"msg": "a"})
        assert len(received) == 0


class TestDeadSubscriberCleanup:
    @pytest.mark.asyncio
    async def test_dead_subscriber_removed(self):
        call_count = [0]

        async def bad_callback(msg):
            call_count[0] += 1
            raise RuntimeError("simulated dead subscriber")

        await event_bus.subscribe("vl", bad_callback)
        # First publish: callback raises, gets cleaned up
        await event_bus.publish("vl", {"text": "test"})
        # Second publish: subscriber was removed, no call
        await event_bus.publish("vl", {"text": "test2"})

        assert call_count[0] == 1  # only called once before removal


class TestLogEntry:
    def test_log_entry_format(self):
        entry = event_bus.log_entry("capture", "ok", "Frame captured 800x600")
        assert entry["kind"] == "capture"
        assert entry["status"] == "ok"
        assert entry["msg"] == "Frame captured 800x600"
        assert ":" in entry["t"]  # has timestamp format HH:MM:SS

    def test_log_entry_fail(self):
        entry = event_bus.log_entry("vl", "fail", "Ollama timeout")
        assert entry["status"] == "fail"
