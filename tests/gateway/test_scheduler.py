"""Tests for gateway scheduler."""

import asyncio
import tempfile
from datetime import datetime, timedelta

import pytest

from gateway.scheduler import (
    CronParser,
    ScheduledTask,
    Scheduler,
    TaskType,
    reset_scheduler,
)


@pytest.fixture
def scheduler():
    """Create a fresh scheduler."""
    return Scheduler()


@pytest.fixture(autouse=True)
def reset_global():
    """Reset global scheduler between tests."""
    yield
    reset_scheduler()


class TestCronParser:
    """Tests for CronParser."""

    def test_every_minute(self):
        minutes, hours, days, months, weekdays = CronParser.parse("* * * * *")
        assert minutes == set(range(60))
        assert hours == set(range(24))

    def test_specific_values(self):
        minutes, hours, days, months, weekdays = CronParser.parse("30 9 * * *")
        assert minutes == {30}
        assert hours == {9}

    def test_ranges(self):
        minutes, hours, days, months, weekdays = CronParser.parse("0 9-17 * * *")
        assert hours == set(range(9, 18))

    def test_steps(self):
        minutes, hours, days, months, weekdays = CronParser.parse("*/15 * * * *")
        assert minutes == {0, 15, 30, 45}

    def test_lists(self):
        minutes, hours, days, months, weekdays = CronParser.parse("0,30 * * * *")
        assert minutes == {0, 30}

    def test_next_run_basic(self):
        # Every minute - should be within next 60 seconds
        after = datetime.now().replace(second=0, microsecond=0)
        next_run = CronParser.next_run("* * * * *", after)
        assert next_run > after
        assert next_run <= after + timedelta(minutes=1)

    def test_next_run_specific_hour(self):
        after = datetime(2024, 1, 15, 8, 30, 0)
        next_run = CronParser.next_run("0 10 * * *", after)  # 10 AM
        assert next_run.hour == 10
        assert next_run.minute == 0

    def test_invalid_expression(self):
        with pytest.raises(ValueError):
            CronParser.parse("* * *")  # Too few fields


class TestScheduledTask:
    """Tests for ScheduledTask dataclass."""

    def test_interval_task(self):
        task = ScheduledTask(
            name="test-task",
            type=TaskType.INTERVAL,
            interval=60,
            command="echo test",
        )
        assert task.type == TaskType.INTERVAL
        assert task.interval == 60
        assert task.enabled is True
        assert task.run_count == 0

    def test_cron_task(self):
        task = ScheduledTask(
            name="cron-task",
            type=TaskType.CRON,
            cron="0 9 * * *",
            command="echo daily",
        )
        assert task.type == TaskType.CRON
        assert task.cron == "0 9 * * *"

    def test_one_shot_task(self):
        task = ScheduledTask(
            name="one-shot",
            type=TaskType.ONE_SHOT,
            delay=30,
            command="echo once",
        )
        assert task.type == TaskType.ONE_SHOT
        assert task.delay == 30


class TestScheduler:
    """Tests for Scheduler."""

    def test_add_task(self, scheduler):
        task = ScheduledTask(
            name="test",
            type=TaskType.INTERVAL,
            interval=60,
            command="echo",
        )
        scheduler.add_task(task)

        assert scheduler.get_task("test") is task
        assert len(scheduler.get_tasks()) == 1
        assert task.next_run is not None

    def test_remove_task(self, scheduler):
        task = ScheduledTask(name="test", type=TaskType.INTERVAL, interval=60, command="echo")
        scheduler.add_task(task)

        result = scheduler.remove_task("test")
        assert result is True
        assert scheduler.get_task("test") is None

        result = scheduler.remove_task("nonexistent")
        assert result is False

    def test_enable_disable_task(self, scheduler):
        task = ScheduledTask(name="test", type=TaskType.INTERVAL, interval=60, command="echo")
        scheduler.add_task(task)

        scheduler.disable_task("test")
        assert scheduler.get_task("test").enabled is False
        assert scheduler.get_task("test").next_run is None

        scheduler.enable_task("test")
        assert scheduler.get_task("test").enabled is True
        assert scheduler.get_task("test").next_run is not None

    @pytest.mark.asyncio
    async def test_interval_task_execution(self, scheduler):
        task = ScheduledTask(
            name="quick-task",
            type=TaskType.INTERVAL,
            interval=0.1,  # Very short interval for testing
            command="echo executed",
            timeout=5.0,
        )
        scheduler.add_task(task)

        await scheduler.start()

        # Wait for at least one execution
        await asyncio.sleep(0.5)

        await scheduler.stop()

        results = scheduler.get_results()
        assert len(results) >= 1
        assert results[0].task_name == "quick-task"
        assert results[0].success is True
        assert "executed" in results[0].output

    @pytest.mark.asyncio
    async def test_one_shot_task(self, scheduler):
        task = ScheduledTask(
            name="one-shot",
            type=TaskType.ONE_SHOT,
            delay=0.05,  # Very short delay
            command="echo once",
            timeout=5.0,
        )
        scheduler.add_task(task)

        await scheduler.start()
        await asyncio.sleep(1.0)  # Increased wait time
        await scheduler.stop()

        results = scheduler.get_results()
        assert len(results) == 1  # Should only run once

        # Check that next_run is None after execution
        t = scheduler.get_task("one-shot")
        assert t.next_run is None
        assert t.run_count == 1

    @pytest.mark.asyncio
    async def test_python_handler_task(self, scheduler):
        executed = []

        async def handler():
            executed.append(datetime.now())

        task = ScheduledTask(
            name="py-task",
            type=TaskType.INTERVAL,
            interval=0.15,  # Slightly longer interval
            handler=handler,
        )
        scheduler.add_task(task)

        await scheduler.start()
        await asyncio.sleep(1.0)  # Increased wait time
        await scheduler.stop()

        assert len(executed) >= 2

    @pytest.mark.asyncio
    async def test_run_task_now(self, scheduler):
        task = ScheduledTask(
            name="manual-task",
            type=TaskType.INTERVAL,
            interval=3600,  # Long interval
            command="echo manual",
            timeout=5.0,
        )
        scheduler.add_task(task)

        # Don't start scheduler, just run manually
        result = await scheduler.run_task_now("manual-task")

        assert result is not None
        assert result.success is True
        assert "manual" in result.output

    @pytest.mark.asyncio
    async def test_task_timeout(self, scheduler):
        task = ScheduledTask(
            name="slow-task",
            type=TaskType.ONE_SHOT,
            delay=0,
            command="sleep 10",
            timeout=0.1,  # Very short timeout
        )
        scheduler.add_task(task)

        await scheduler.start()
        await asyncio.sleep(0.5)
        await scheduler.stop()

        results = scheduler.get_results()
        assert len(results) >= 1
        assert results[0].success is False
        assert "Timeout" in results[0].error

    def test_load_from_file(self, scheduler):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
tasks:
  - name: test-task
    type: interval
    interval: 3600
    command: echo loaded
    description: Test task from file
""")
            f.flush()

            count = scheduler.load_from_file(f.name)
            assert count == 1

            task = scheduler.get_task("test-task")
            assert task is not None
            assert task.interval == 3600
            assert task.description == "Test task from file"

    def test_stats(self, scheduler):
        scheduler.add_task(
            ScheduledTask(name="t1", type=TaskType.INTERVAL, interval=60, command="echo")
        )
        scheduler.add_task(
            ScheduledTask(name="t2", type=TaskType.CRON, cron="0 9 * * *", command="echo")
        )
        scheduler.disable_task("t2")

        stats = scheduler.get_stats()
        assert stats["total_tasks"] == 2
        assert stats["enabled_tasks"] == 1
        assert stats["scheduler_running"] is False

    @pytest.mark.asyncio
    async def test_scheduler_start_stop(self, scheduler):
        stats = scheduler.get_stats()
        assert stats["scheduler_running"] is False

        await scheduler.start()
        stats = scheduler.get_stats()
        assert stats["scheduler_running"] is True

        await scheduler.stop()
        stats = scheduler.get_stats()
        assert stats["scheduler_running"] is False
