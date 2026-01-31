"""Tests for gateway hooks system."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from gateway.events import Event, EventEmitter, EventType
from gateway.hooks import (
    Hook,
    HookManager,
    HookType,
    reset_hook_manager,
)


@pytest.fixture
def emitter():
    """Create a fresh event emitter."""
    return EventEmitter()


@pytest.fixture
def manager(emitter):
    """Create a fresh hook manager."""
    return HookManager(emitter=emitter)


@pytest.fixture(autouse=True)
def reset_global():
    """Reset global manager between tests."""
    yield
    reset_hook_manager()


class TestHook:
    """Tests for Hook dataclass."""

    def test_shell_hook(self):
        hook = Hook(
            name="test-hook",
            event=EventType.GATEWAY_STARTUP,
            type=HookType.SHELL,
            command="echo hello",
        )
        assert hook.name == "test-hook"
        assert hook.type == HookType.SHELL
        assert hook.enabled is True

    def test_python_hook(self):
        async def handler(event: Event):
            pass

        hook = Hook(
            name="py-hook",
            event=EventType.MESSAGE_RECEIVED,
            type=HookType.PYTHON,
            handler=handler,
        )
        assert hook.type == HookType.PYTHON
        assert hook.handler is handler


class TestHookManager:
    """Tests for HookManager."""

    def test_register_hook(self, manager):
        hook = Hook(
            name="test",
            event=EventType.GATEWAY_STARTUP,
            command="echo test",
        )
        manager.register(hook)

        assert manager.get_hook("test") is hook
        assert len(manager.get_hooks()) == 1

    def test_unregister_hook(self, manager):
        hook = Hook(name="test", event=EventType.GATEWAY_STARTUP, command="echo")
        manager.register(hook)

        result = manager.unregister("test")
        assert result is True
        assert manager.get_hook("test") is None

        result = manager.unregister("nonexistent")
        assert result is False

    def test_enable_disable(self, manager):
        hook = Hook(name="test", event=EventType.GATEWAY_STARTUP, command="echo")
        manager.register(hook)

        manager.disable("test")
        assert manager.get_hook("test").enabled is False

        manager.enable("test")
        assert manager.get_hook("test").enabled is True

    @pytest.mark.asyncio
    async def test_shell_hook_execution(self, manager, emitter):
        hook = Hook(
            name="echo-hook",
            event=EventType.GATEWAY_STARTUP,
            command="echo hello world",
            timeout=5.0,
        )
        manager.register(hook)

        # Emit the event
        await emitter.emit(Event(type=EventType.GATEWAY_STARTUP))

        # Give it time to execute
        await asyncio.sleep(0.2)

        # Check results
        results = manager.get_results()
        assert len(results) >= 1
        assert results[0].hook_name == "echo-hook"
        assert results[0].success is True
        assert "hello world" in results[0].output

    @pytest.mark.asyncio
    async def test_python_hook_execution(self, manager, emitter):
        received = []

        async def handler(event: Event):
            received.append(event.type)

        hook = Hook(
            name="py-hook",
            event=EventType.MESSAGE_RECEIVED,
            type=HookType.PYTHON,
            handler=handler,
        )
        manager.register(hook)

        await emitter.emit(Event(type=EventType.MESSAGE_RECEIVED))
        await asyncio.sleep(0.1)

        assert EventType.MESSAGE_RECEIVED in received

    @pytest.mark.asyncio
    async def test_disabled_hook_not_executed(self, manager, emitter):
        hook = Hook(
            name="disabled",
            event=EventType.GATEWAY_STARTUP,
            command="echo should not run",
        )
        manager.register(hook)
        manager.disable("disabled")

        await emitter.emit(Event(type=EventType.GATEWAY_STARTUP))
        await asyncio.sleep(0.2)

        # No results because hook was disabled
        results = manager.get_results()
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_environment_variables(self, manager, emitter):
        # Use printenv to capture environment
        hook = Hook(
            name="env-test",
            event=EventType.SESSION_START,
            command="echo USER:$CLARA_USER_ID CHANNEL:$CLARA_CHANNEL_ID",
            timeout=5.0,
        )
        manager.register(hook)

        await emitter.emit(
            Event(
                type=EventType.SESSION_START,
                user_id="test-user-123",
                channel_id="test-channel-456",
            )
        )
        await asyncio.sleep(0.2)

        results = manager.get_results()
        assert len(results) >= 1
        assert "USER:test-user-123" in results[0].output
        assert "CHANNEL:test-channel-456" in results[0].output

    @pytest.mark.asyncio
    async def test_hook_timeout(self, manager, emitter):
        hook = Hook(
            name="slow-hook",
            event=EventType.GATEWAY_STARTUP,
            command="sleep 10",
            timeout=0.1,  # Very short timeout
        )
        manager.register(hook)

        await emitter.emit(Event(type=EventType.GATEWAY_STARTUP))
        await asyncio.sleep(0.3)

        results = manager.get_results()
        assert len(results) >= 1
        assert results[0].success is False
        assert "Timeout" in results[0].error

    def test_load_from_file(self, manager):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
hooks:
  - name: test-hook
    event: gateway:startup
    command: echo loaded
    timeout: 10
    description: Test hook from file
""")
            f.flush()

            count = manager.load_from_file(f.name)
            assert count == 1

            hook = manager.get_hook("test-hook")
            assert hook is not None
            assert hook.command == "echo loaded"
            assert hook.description == "Test hook from file"

    def test_stats(self, manager):
        manager.register(Hook(name="h1", event=EventType.GATEWAY_STARTUP, command="echo"))
        manager.register(Hook(name="h2", event=EventType.GATEWAY_STARTUP, command="echo"))
        manager.register(Hook(name="h3", event=EventType.MESSAGE_RECEIVED, command="echo"))

        stats = manager.get_stats()
        assert stats["total_hooks"] == 3
        assert stats["enabled_hooks"] == 3
        assert stats["hooks_by_event"][EventType.GATEWAY_STARTUP.value] == 2
