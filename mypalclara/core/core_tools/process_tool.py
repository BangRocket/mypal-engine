"""Process management tool for Clara.

Provides tools to start, monitor, and manage background processes.
Useful for running long-running commands, development servers, builds, etc.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from mypalclara.tools._base import ToolContext, ToolDef

MODULE_NAME = "process"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """
## Process Management

You can start and manage background processes. This is useful for:
- Running development servers
- Long-running builds or tests
- Any command that needs to run in the background

**Tools:**
- `process_start` - Start a background process
- `process_status` - Get status of a process
- `process_output` - Get recent output from a process
- `process_stop` - Stop a running process
- `process_list` - List all tracked processes

**Important:**
- Processes are tracked by name for easy reference
- Output is buffered (last 1000 lines kept)
- Processes timeout after 1 hour by default
- Use `force=true` with process_stop for stubborn processes
""".strip()


@dataclass
class TrackedProcess:
    """A tracked background process."""

    pid: int
    command: str
    started_at: float
    process: asyncio.subprocess.Process
    output_buffer: list[str] = field(default_factory=list)
    cwd: str | None = None
    timeout: int = 3600


# Global process tracker
_processes: dict[str, TrackedProcess] = {}
_output_readers: dict[str, asyncio.Task] = {}


async def _read_output(name: str, proc: asyncio.subprocess.Process, timeout: int) -> None:
    """Read process output and handle timeout."""
    try:
        async with asyncio.timeout(timeout):
            while True:
                if proc.stdout is None:
                    break
                line = await proc.stdout.readline()
                if not line:
                    break
                if name in _processes:
                    _processes[name].output_buffer.append(line.decode(errors="replace"))
                    # Keep last 1000 lines
                    if len(_processes[name].output_buffer) > 1000:
                        _processes[name].output_buffer.pop(0)
    except asyncio.TimeoutError:
        proc.terminate()
        if name in _processes:
            _processes[name].output_buffer.append(f"\n[Process timed out after {timeout}s]")
    except Exception as e:
        if name in _processes:
            _processes[name].output_buffer.append(f"\n[Error reading output: {e}]")
    finally:
        await proc.wait()


async def process_start(args: dict[str, Any], ctx: ToolContext) -> str:
    """Start a background process."""
    command = args.get("command")
    if not command:
        return "Error: command is required"

    name = args.get("name", f"proc_{int(time.time())}")
    timeout = args.get("timeout", 3600)  # Default 1 hour
    cwd = args.get("cwd")

    if name in _processes:
        tp = _processes[name]
        if tp.process.returncode is None:
            return f"Error: Process '{name}' is already running (PID {tp.pid}). Use a different name or stop it first."

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )

        _processes[name] = TrackedProcess(
            pid=proc.pid,
            command=command,
            started_at=time.time(),
            process=proc,
            cwd=cwd,
            timeout=timeout,
        )

        # Start output reader
        task = asyncio.create_task(_read_output(name, proc, timeout))
        _output_readers[name] = task

        return f"Started process '{name}' with PID {proc.pid}\nCommand: {command}"
    except Exception as e:
        return f"Error starting process: {e}"


async def process_status(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get status of a process."""
    name = args.get("name")
    if not name:
        return "Error: name is required"

    if name not in _processes:
        available = list(_processes.keys()) if _processes else "none"
        return f"Process '{name}' not found. Available: {available}"

    tp = _processes[name]
    running = tp.process.returncode is None
    elapsed = int(time.time() - tp.started_at)

    status = "running" if running else f"exited (code {tp.process.returncode})"

    return f"""Process: {name}
PID: {tp.pid}
Command: {tp.command}
Working Dir: {tp.cwd or '(default)'}
Status: {status}
Elapsed: {elapsed}s
Output lines: {len(tp.output_buffer)}
Timeout: {tp.timeout}s"""


async def process_output(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get output from a process."""
    name = args.get("name")
    if not name:
        return "Error: name is required"

    lines = args.get("lines", 50)

    if name not in _processes:
        available = list(_processes.keys()) if _processes else "none"
        return f"Process '{name}' not found. Available: {available}"

    output = _processes[name].output_buffer[-lines:]
    if not output:
        return "(no output yet)"

    return "".join(output)


async def process_stop(args: dict[str, Any], ctx: ToolContext) -> str:
    """Stop a running process."""
    name = args.get("name")
    if not name:
        return "Error: name is required"

    force = args.get("force", False)

    if name not in _processes:
        available = list(_processes.keys()) if _processes else "none"
        return f"Process '{name}' not found. Available: {available}"

    tp = _processes[name]
    if tp.process.returncode is not None:
        # Already finished, clean up
        del _processes[name]
        if name in _output_readers:
            _output_readers[name].cancel()
            del _output_readers[name]
        return f"Process '{name}' already finished (exit code {tp.process.returncode})."

    if force:
        tp.process.kill()
        action = "killed"
    else:
        tp.process.terminate()
        action = "terminated"

    try:
        await asyncio.wait_for(tp.process.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        if not force:
            tp.process.kill()
            await tp.process.wait()
            action = "force killed (did not respond to terminate)"

    del _processes[name]
    if name in _output_readers:
        _output_readers[name].cancel()
        del _output_readers[name]

    return f"Process '{name}' {action}."


async def process_list(args: dict[str, Any], ctx: ToolContext) -> str:
    """List all tracked processes."""
    if not _processes:
        return "No active processes."

    lines = ["Active processes:"]
    for name, tp in _processes.items():
        running = "running" if tp.process.returncode is None else f"stopped ({tp.process.returncode})"
        elapsed = int(time.time() - tp.started_at)
        cmd_short = tp.command[:50] + ("..." if len(tp.command) > 50 else "")
        lines.append(f"  - {name}: PID {tp.pid}, {running}, {elapsed}s elapsed")
        lines.append(f"    Command: {cmd_short}")

    return "\n".join(lines)


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="process_start",
        description=(
            "Start a background process and track it. Use this for long-running commands "
            "like development servers, builds, or any command that runs in the background."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "name": {
                    "type": "string",
                    "description": "Name to identify this process (auto-generated if not provided)",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory for the command",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 3600 = 1 hour)",
                },
            },
            "required": ["command"],
        },
        handler=process_start,
        emoji="▶️",
        label="Start Process",
        detail_keys=["command"],
        risk_level="dangerous",
        intent="execute",
    ),
    ToolDef(
        name="process_status",
        description="Get status of a tracked process including running state, elapsed time, and output line count.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Process name",
                },
            },
            "required": ["name"],
        },
        handler=process_status,
        emoji="📊",
        label="Process Status",
        detail_keys=["name"],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="process_output",
        description="Get recent output lines from a tracked process.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Process name",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of lines to return (default 50)",
                },
            },
            "required": ["name"],
        },
        handler=process_output,
        emoji="📜",
        label="Process Output",
        detail_keys=["name"],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="process_stop",
        description="Stop a running process. Use force=true if the process doesn't respond to terminate.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Process name",
                },
                "force": {
                    "type": "boolean",
                    "description": "Force kill (SIGKILL) instead of terminate (SIGTERM)",
                },
            },
            "required": ["name"],
        },
        handler=process_stop,
        emoji="⏹️",
        label="Stop Process",
        detail_keys=["name"],
        risk_level="moderate",
        intent="execute",
    ),
    ToolDef(
        name="process_list",
        description="List all tracked processes with their status and elapsed time.",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=process_list,
        emoji="📋",
        label="List Processes",
        detail_keys=[],
        risk_level="safe",
        intent="read",
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize process tool module."""
    pass


async def cleanup() -> None:
    """Cleanup on module unload - stop all processes."""
    for name in list(_processes.keys()):
        tp = _processes[name]
        if tp.process.returncode is None:
            tp.process.terminate()
            try:
                await asyncio.wait_for(tp.process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                tp.process.kill()

    _processes.clear()

    for task in _output_readers.values():
        task.cancel()
    _output_readers.clear()
