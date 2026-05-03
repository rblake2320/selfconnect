"""
test_action_queue.py — Unit tests for the action queue state machine.
Tests enqueue, state transitions, cancel, and command parsing.
SDK calls are mocked — no real Win32 API required.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision_server.models.schemas import ActionRequest
from vision_server.services import action_queue


@pytest.fixture(autouse=True)
def reset_queue():
    """Clear queue state before each test."""
    action_queue._queue.clear()
    action_queue._history.clear()
    action_queue._running = False
    action_queue._paused = False
    action_queue._task = None
    yield
    action_queue._queue.clear()
    action_queue._history.clear()
    action_queue._running = False
    action_queue._paused = False


@pytest.fixture(autouse=True)
def mock_event_bus():
    """Mock event_bus.publish so tests don't need a live bus."""
    with patch("vision_server.services.action_queue.asyncio") as _:
        pass
    with patch("vision_server.services.event_bus.publish", new_callable=AsyncMock) as mock_pub:
        yield mock_pub


class TestEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_returns_item(self, mock_event_bus):
        req = ActionRequest(kind="click", target="OK", value="320,240")
        item = await action_queue.enqueue(req)
        assert item["kind"] == "click"
        assert item["state"] == "pending"
        assert len(item["id"]) > 0

    @pytest.mark.asyncio
    async def test_enqueue_adds_to_queue(self, mock_event_bus):
        req = ActionRequest(kind="type", value="hello")
        await action_queue.enqueue(req)
        assert len(action_queue._queue) == 1

    @pytest.mark.asyncio
    async def test_multiple_enqueues(self, mock_event_bus):
        for i in range(3):
            await action_queue.enqueue(ActionRequest(kind="wait", value="0.1"))
        assert len(action_queue._queue) == 3

    @pytest.mark.asyncio
    async def test_enqueue_publishes_queue_event(self, mock_event_bus):
        req = ActionRequest(kind="click", value="0,0")
        await action_queue.enqueue(req)
        # publish called twice: once for queue, once for log
        assert mock_event_bus.call_count >= 2


class TestGetQueue:
    @pytest.mark.asyncio
    async def test_empty_queue(self):
        assert action_queue.get_queue() == []

    @pytest.mark.asyncio
    async def test_queue_includes_pending(self, mock_event_bus):
        await action_queue.enqueue(ActionRequest(kind="wait", value="0.1"))
        q = action_queue.get_queue()
        assert len(q) == 1
        assert q[0]["state"] == "pending"


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_removes_item(self, mock_event_bus):
        item = await action_queue.enqueue(ActionRequest(kind="wait", value="0.1"))
        result = action_queue.cancel(item["id"])
        assert result is True
        assert len(action_queue._queue) == 0

    def test_cancel_nonexistent(self):
        result = action_queue.cancel("nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_only_removes_target(self, mock_event_bus):
        item1 = await action_queue.enqueue(ActionRequest(kind="wait", value="1"))
        item2 = await action_queue.enqueue(ActionRequest(kind="wait", value="2"))
        action_queue.cancel(item1["id"])
        assert len(action_queue._queue) == 1
        assert action_queue._queue[0]["id"] == item2["id"]


class TestPause:
    def test_pause_sets_flag(self):
        action_queue.pause()
        assert action_queue._paused is True


class TestRunAlreadyRunning:
    @pytest.mark.asyncio
    async def test_run_while_running_is_noop(self):
        action_queue._running = True
        # Should return without creating a new task
        await action_queue.run()
        assert action_queue._task is None  # no new task created
        action_queue._running = False


class TestWaitExecution:
    @pytest.mark.asyncio
    async def test_wait_action_executes(self, mock_event_bus):
        """Wait action uses asyncio.sleep — test it runs to completion."""
        req = ActionRequest(kind="wait", value="0.01")
        item = await action_queue.enqueue(req)

        # Execute the item directly (bypassing SDK mocks)
        with patch("vision_server.services.macro_recorder.record_step"):
            await action_queue._execute_item(item)
        # No exception = success


class TestCommandParsing:
    @pytest.mark.asyncio
    async def test_click_command(self, mock_event_bus):
        item = await action_queue.enqueue_command("click Submit")
        assert item["kind"] == "click"
        assert item["target"] == "Submit"

    @pytest.mark.asyncio
    async def test_type_command(self, mock_event_bus):
        item = await action_queue.enqueue_command("type hello world")
        assert item["kind"] == "type"
        assert item["value"] == "hello world"

    @pytest.mark.asyncio
    async def test_unknown_command_defaults_to_type(self, mock_event_bus):
        item = await action_queue.enqueue_command("do something unknown")
        assert item["kind"] == "type"


class TestGetAll:
    @pytest.mark.asyncio
    async def test_get_all_includes_history(self, mock_event_bus):
        await action_queue.enqueue(ActionRequest(kind="wait", value="0.01"))
        item = action_queue._queue[0]
        item["state"] = "done"
        action_queue._queue.popleft()
        action_queue._history.append(item)

        all_items = action_queue._get_all()
        assert any(i["state"] == "done" for i in all_items)

    @pytest.mark.asyncio
    async def test_history_capped_at_50(self, mock_event_bus):
        # Fill history with 60 items
        for i in range(60):
            action_queue._history.append({"id": str(i), "state": "done", "kind": "wait"})

        all_items = action_queue._get_all()
        history_items = [i for i in all_items if i["state"] == "done"]
        assert len(history_items) <= 50
