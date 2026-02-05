"""Task scheduler for the Clara Gateway.

Supports:
- One-shot tasks: Run once at a specific time
- Interval tasks: Run every N seconds/minutes/hours
- Cron tasks: Run on cron schedule expressions

Configuration file format (scheduler.yaml):
```yaml
tasks:
  - name: cleanup-sessions
    type: interval
    interval: 3600  # Every hour
    command: python -m scripts.cleanup_sessions

  - name: daily-summary
    type: cron
    cron: "0 9 * * *"  # 9 AM daily
    command: python -m scripts.daily_summary

  - name: startup-check
    type: one_shot
    delay: 30  # 30 seconds after gateway starts
    command: curl http://localhost:8000/health
```
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine

import yaml

from config.logging import get_logger
from mypalclara.gateway.events import Event, EventType, emit

logger = get_logger("gateway.scheduler")


class TaskType(str, Enum):
    """Types of scheduled tasks."""

    ONE_SHOT = "one_shot"  # Run once
    INTERVAL = "interval"  # Run every N seconds
    CRON = "cron"  # Run on cron schedule


@dataclass
class TaskResult:
    """Result of task execution."""

    task_name: str
    success: bool
    output: str = ""
    error: str | None = None
    duration_ms: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ScheduledTask:
    """A scheduled task configuration."""

    name: str
    type: TaskType

    # For shell commands
    command: str | None = None
    timeout: float = 300.0
    working_dir: str | None = None

    # For Python handlers
    handler: Callable[[], Coroutine[Any, Any, None]] | None = None

    # Scheduling parameters
    interval: float | None = None  # Seconds for interval tasks
    cron: str | None = None  # Cron expression
    delay: float | None = None  # Initial delay for one-shot
    run_at: datetime | None = None  # Specific time for one-shot

    # State
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None
    run_count: int = 0

    # Metadata
    description: str = ""

    def __repr__(self) -> str:
        return f"ScheduledTask({self.name}, type={self.type.value})"


class CronParser:
    """Simple cron expression parser.

    Supports: minute hour day_of_month month day_of_week
    Examples:
        "* * * * *"       - Every minute
        "0 * * * *"       - Every hour
        "0 9 * * *"       - 9 AM daily
        "0 9 * * 1-5"     - 9 AM weekdays
        "*/15 * * * *"    - Every 15 minutes
        "0 0 1 * *"       - First of every month
    """

    @staticmethod
    def parse(expression: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
        """Parse a cron expression.

        Args:
            expression: Cron expression (5 fields)

        Returns:
            Tuple of (minutes, hours, days, months, weekdays) as sets
        """
        parts = expression.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {expression}")

        return (
            CronParser._parse_field(parts[0], 0, 59),  # minutes
            CronParser._parse_field(parts[1], 0, 23),  # hours
            CronParser._parse_field(parts[2], 1, 31),  # days
            CronParser._parse_field(parts[3], 1, 12),  # months
            CronParser._parse_field(parts[4], 0, 6),  # weekdays (0=Sunday)
        )

    @staticmethod
    def _parse_field(field: str, min_val: int, max_val: int) -> set[int]:
        """Parse a single cron field.

        Args:
            field: Field string (*, */N, N, N-M, N,M,...)
            min_val: Minimum allowed value
            max_val: Maximum allowed value

        Returns:
            Set of matching values
        """
        result: set[int] = set()

        for part in field.split(","):
            if part == "*":
                result.update(range(min_val, max_val + 1))
            elif part.startswith("*/"):
                step = int(part[2:])
                result.update(range(min_val, max_val + 1, step))
            elif "-" in part:
                start, end = part.split("-")
                result.update(range(int(start), int(end) + 1))
            else:
                result.add(int(part))

        return result

    @staticmethod
    def next_run(expression: str, after: datetime | None = None) -> datetime:
        """Calculate the next run time for a cron expression.

        Args:
            expression: Cron expression
            after: Start searching after this time (defaults to now)

        Returns:
            Next datetime that matches the expression
        """
        minutes, hours, days, months, weekdays = CronParser.parse(expression)

        if after is None:
            after = datetime.now()

        # Start from the next minute
        candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

        # Search up to 1 year ahead
        max_iterations = 525600  # Minutes in a year
        for _ in range(max_iterations):
            if (
                candidate.minute in minutes
                and candidate.hour in hours
                and candidate.day in days
                and candidate.month in months
                and candidate.weekday() in weekdays  # Python weekday: 0=Monday
            ):
                return candidate
            candidate += timedelta(minutes=1)

        raise ValueError(f"Could not find next run time for: {expression}")


class Scheduler:
    """Task scheduler for the gateway.

    Manages scheduled tasks with support for one-shot, interval, and cron-based
    scheduling.
    """

    def __init__(self, config_dir: str | Path | None = None) -> None:
        """Initialize the scheduler.

        Args:
            config_dir: Directory containing scheduler.yaml
        """
        self._tasks: dict[str, ScheduledTask] = {}
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._results: list[TaskResult] = []
        self._results_limit = 100
        self._config_dir = Path(config_dir) if config_dir else Path(".")
        self._scheduler_task: asyncio.Task[None] | None = None
        self._running = False

    def add_task(self, task: ScheduledTask) -> None:
        """Add a scheduled task.

        Args:
            task: Task to add
        """
        if task.name in self._tasks:
            logger.warning(f"Overwriting existing task: {task.name}")

        # Calculate initial next_run
        task.next_run = self._calculate_next_run(task)
        self._tasks[task.name] = task

        logger.info(f"Added task: {task.name} ({task.type.value}), next run: {task.next_run}")

    def remove_task(self, name: str) -> bool:
        """Remove a scheduled task.

        Args:
            name: Task name

        Returns:
            True if task was found and removed
        """
        if name not in self._tasks:
            return False

        # Cancel if running
        if name in self._running_tasks:
            self._running_tasks[name].cancel()
            del self._running_tasks[name]

        del self._tasks[name]
        logger.info(f"Removed task: {name}")
        return True

    def enable_task(self, name: str) -> bool:
        """Enable a task.

        Args:
            name: Task name

        Returns:
            True if task was found
        """
        if name not in self._tasks:
            return False
        task = self._tasks[name]
        task.enabled = True
        task.next_run = self._calculate_next_run(task)
        return True

    def disable_task(self, name: str) -> bool:
        """Disable a task.

        Args:
            name: Task name

        Returns:
            True if task was found
        """
        if name not in self._tasks:
            return False
        self._tasks[name].enabled = False
        self._tasks[name].next_run = None
        return True

    def _calculate_next_run(self, task: ScheduledTask) -> datetime | None:
        """Calculate the next run time for a task.

        Args:
            task: The task

        Returns:
            Next run datetime or None
        """
        if not task.enabled:
            return None

        now = datetime.now()

        if task.type == TaskType.ONE_SHOT:
            if task.run_count > 0:
                return None  # Already ran
            if task.run_at:
                return task.run_at
            if task.delay:
                return now + timedelta(seconds=task.delay)
            return now  # Run immediately

        elif task.type == TaskType.INTERVAL:
            if task.interval is None:
                return None
            if task.last_run:
                return task.last_run + timedelta(seconds=task.interval)
            # First run: use delay if specified, otherwise run immediately
            if task.delay:
                return now + timedelta(seconds=task.delay)
            return now

        elif task.type == TaskType.CRON:
            if task.cron is None:
                return None
            try:
                return CronParser.next_run(task.cron, now)
            except Exception as e:
                logger.error(f"Invalid cron for task {task.name}: {e}")
                return None

        return None

    async def start(self) -> None:
        """Start the scheduler loop."""
        if self._running:
            return

        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler loop."""
        self._running = False

        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None

        # Cancel all running tasks
        for task in self._running_tasks.values():
            task.cancel()
        self._running_tasks.clear()

        logger.info("Scheduler stopped")

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                now = datetime.now()

                # Check each task
                for task in list(self._tasks.values()):
                    if not task.enabled or task.next_run is None:
                        continue

                    if now >= task.next_run:
                        # Time to run
                        if task.name not in self._running_tasks:
                            self._running_tasks[task.name] = asyncio.create_task(self._run_task(task))

                # Sleep briefly before next check (100ms for responsive scheduling)
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Scheduler loop error: {e}")
                await asyncio.sleep(5)

    async def _run_task(self, task: ScheduledTask) -> None:
        """Run a scheduled task.

        Args:
            task: Task to run
        """
        start = datetime.now()

        try:
            # Emit event
            await emit(
                Event(
                    type=EventType.SCHEDULED_TASK_RUN,
                    data={"task_name": task.name, "task_type": task.type.value},
                )
            )

            if task.handler:
                # Python handler
                await task.handler()
                result = TaskResult(
                    task_name=task.name,
                    success=True,
                    output="Handler completed",
                )
            elif task.command:
                # Shell command
                result = await self._run_shell_task(task)
            else:
                result = TaskResult(
                    task_name=task.name,
                    success=False,
                    error="No handler or command specified",
                )

        except asyncio.CancelledError:
            result = TaskResult(
                task_name=task.name,
                success=False,
                error="Task cancelled",
            )
        except Exception as e:
            logger.exception(f"Task {task.name} failed: {e}")
            result = TaskResult(
                task_name=task.name,
                success=False,
                error=str(e),
            )

            # Emit error event
            await emit(
                Event(
                    type=EventType.SCHEDULED_TASK_ERROR,
                    data={"task_name": task.name, "error": str(e)},
                )
            )

        # Calculate duration
        duration = datetime.now() - start
        result.duration_ms = int(duration.total_seconds() * 1000)

        # Store result
        self._results.append(result)
        if len(self._results) > self._results_limit:
            self._results.pop(0)

        # Update task state
        task.last_run = start
        task.run_count += 1
        task.next_run = self._calculate_next_run(task)

        # Clean up running task
        if task.name in self._running_tasks:
            del self._running_tasks[task.name]

        # Log result
        if result.success:
            logger.info(f"Task {task.name} completed in {result.duration_ms}ms")
        else:
            logger.warning(f"Task {task.name} failed: {result.error}")

    async def _run_shell_task(self, task: ScheduledTask) -> TaskResult:
        """Run a shell command task.

        Args:
            task: Task with shell command

        Returns:
            Execution result
        """
        if not task.command:
            return TaskResult(
                task_name=task.name,
                success=False,
                error="No command specified",
            )

        cwd = task.working_dir or str(self._config_dir)

        try:
            proc = await asyncio.create_subprocess_shell(
                task.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=task.timeout,
            )

            output = stdout.decode("utf-8", errors="replace")
            error_output = stderr.decode("utf-8", errors="replace")

            if proc.returncode == 0:
                return TaskResult(
                    task_name=task.name,
                    success=True,
                    output=output,
                )
            else:
                return TaskResult(
                    task_name=task.name,
                    success=False,
                    output=output,
                    error=f"Exit code {proc.returncode}: {error_output}",
                )

        except asyncio.TimeoutError:
            proc.kill()
            return TaskResult(
                task_name=task.name,
                success=False,
                error=f"Timeout after {task.timeout}s",
            )

    def load_from_file(self, path: str | Path | None = None) -> int:
        """Load tasks from a YAML configuration file.

        Args:
            path: Path to scheduler.yaml

        Returns:
            Number of tasks loaded
        """
        if path is None:
            path = self._config_dir / "scheduler.yaml"
        else:
            path = Path(path)

        if not path.exists():
            logger.debug(f"No scheduler config at {path}")
            return 0

        try:
            with open(path) as f:
                config = yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load scheduler config: {e}")
            return 0

        if not config or "tasks" not in config:
            return 0

        count = 0
        for task_config in config["tasks"]:
            try:
                task = self._parse_task_config(task_config)
                self.add_task(task)
                count += 1
            except Exception as e:
                logger.error(f"Failed to parse task config: {e}")

        logger.info(f"Loaded {count} tasks from {path}")
        return count

    def _parse_task_config(self, config: dict[str, Any]) -> ScheduledTask:
        """Parse a task configuration dict.

        Args:
            config: Task configuration from YAML

        Returns:
            ScheduledTask instance

        Raises:
            ValueError: If configuration is invalid
        """
        name = config.get("name")
        if not name:
            raise ValueError("Task must have a name")

        type_str = config.get("type", "interval")
        task_type = TaskType(type_str)

        task = ScheduledTask(
            name=name,
            type=task_type,
            command=config.get("command"),
            timeout=float(config.get("timeout", 300)),
            working_dir=config.get("working_dir"),
            interval=config.get("interval"),
            cron=config.get("cron"),
            delay=config.get("delay"),
            enabled=config.get("enabled", True),
            description=config.get("description", ""),
        )

        # Parse run_at for one-shot tasks
        if run_at := config.get("run_at"):
            if isinstance(run_at, str):
                task.run_at = datetime.fromisoformat(run_at)
            elif isinstance(run_at, datetime):
                task.run_at = run_at

        return task

    async def run_task_now(self, name: str) -> TaskResult | None:
        """Run a task immediately.

        Args:
            name: Task name

        Returns:
            Task result or None if not found
        """
        task = self._tasks.get(name)
        if not task:
            return None

        # Wait if already running
        if name in self._running_tasks:
            logger.warning(f"Task {name} is already running")
            return None

        self._running_tasks[name] = asyncio.create_task(self._run_task(task))
        await self._running_tasks[name]

        # Return most recent result
        for result in reversed(self._results):
            if result.task_name == name:
                return result
        return None

    def get_tasks(self) -> list[ScheduledTask]:
        """Get all scheduled tasks."""
        return list(self._tasks.values())

    def get_task(self, name: str) -> ScheduledTask | None:
        """Get a specific task by name."""
        return self._tasks.get(name)

    def get_results(self, limit: int = 50) -> list[TaskResult]:
        """Get recent task execution results.

        Args:
            limit: Maximum results to return

        Returns:
            List of results (newest first)
        """
        return list(reversed(self._results[-limit:]))

    def get_stats(self) -> dict[str, Any]:
        """Get scheduler statistics."""
        total = len(self._results)
        successful = sum(1 for r in self._results if r.success)

        return {
            "total_tasks": len(self._tasks),
            "enabled_tasks": sum(1 for t in self._tasks.values() if t.enabled),
            "running_tasks": len(self._running_tasks),
            "executions_total": total,
            "executions_successful": successful,
            "executions_failed": total - successful,
            "scheduler_running": self._running,
        }


# Global scheduler singleton
_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    """Get the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler


def reset_scheduler() -> None:
    """Reset the global scheduler (for testing)."""
    global _scheduler
    _scheduler = None


# Convenience decorator for scheduled tasks
def scheduled(
    type: TaskType = TaskType.INTERVAL,
    interval: float | None = None,
    cron: str | None = None,
    delay: float | None = None,
    name: str | None = None,
) -> Callable[[Callable[[], Coroutine[Any, Any, None]]], Callable[[], Coroutine[Any, Any, None]]]:
    """Decorator to register a Python function as a scheduled task.

    Usage:
        @scheduled(type=TaskType.INTERVAL, interval=3600)
        async def hourly_cleanup():
            # Do cleanup
            pass

        @scheduled(type=TaskType.CRON, cron="0 9 * * *")
        async def daily_report():
            # Generate report
            pass

    Args:
        type: Task type
        interval: Seconds between runs (for INTERVAL type)
        cron: Cron expression (for CRON type)
        delay: Initial delay in seconds
        name: Task name (defaults to function name)

    Returns:
        Decorator function
    """

    def decorator(
        func: Callable[[], Coroutine[Any, Any, None]],
    ) -> Callable[[], Coroutine[Any, Any, None]]:
        task_name = name or func.__name__

        task = ScheduledTask(
            name=task_name,
            type=type,
            handler=func,
            interval=interval,
            cron=cron,
            delay=delay,
        )

        get_scheduler().add_task(task)
        return func

    return decorator
