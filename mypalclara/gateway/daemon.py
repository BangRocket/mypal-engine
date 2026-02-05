"""Daemon management for Clara Gateway.

Provides Unix-style daemonization with PID file management.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

DEFAULT_GATEWAY_PIDFILE = "/tmp/clara-gateway.pid"
DEFAULT_ADAPTER_PIDFILE_PATTERN = "/tmp/clara-adapter-{name}.pid"


def daemonize(pidfile: str, logfile: str | None = None) -> None:
    """Fork the process into a background daemon (Unix only).

    Uses the classic double-fork pattern to fully detach from the terminal.

    Args:
        pidfile: Path to write the daemon's PID
        logfile: Path to redirect stdout/stderr (None = /dev/null)
    """
    # Convert relative paths to absolute before changing directory
    pidfile = os.path.abspath(pidfile)
    if logfile:
        logfile = os.path.abspath(logfile)

    # First fork - parent exits, child continues
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as e:
        sys.stderr.write(f"First fork failed: {e}\n")
        sys.exit(1)

    # Decouple from parent environment
    os.chdir("/")
    os.setsid()
    os.umask(0)

    # Second fork - prevents daemon from acquiring terminal
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as e:
        sys.stderr.write(f"Second fork failed: {e}\n")
        sys.exit(1)

    # Redirect standard file descriptors
    sys.stdout.flush()
    sys.stderr.flush()

    if logfile:
        # Ensure log directory exists
        log_path = Path(logfile)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Redirect to log file
        log_fd = open(logfile, "a+")
        os.dup2(log_fd.fileno(), sys.stdout.fileno())
        os.dup2(log_fd.fileno(), sys.stderr.fileno())
    else:
        # Redirect to /dev/null
        devnull = open("/dev/null", "r+")
        os.dup2(devnull.fileno(), sys.stdin.fileno())
        os.dup2(devnull.fileno(), sys.stdout.fileno())
        os.dup2(devnull.fileno(), sys.stderr.fileno())

    # Write PID file
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))


def stop_daemon(pidfile: str, timeout: float = 5.0) -> bool:
    """Stop a running daemon using its PID file.

    Args:
        pidfile: Path to the PID file
        timeout: Seconds to wait before sending SIGKILL

    Returns:
        True if daemon was stopped, False otherwise
    """
    try:
        with open(pidfile, "r") as f:
            pid = int(f.read().strip())
    except FileNotFoundError:
        print(f"PID file not found: {pidfile}")
        return False
    except ValueError:
        print(f"Invalid PID in {pidfile}")
        _cleanup_pidfile(pidfile)
        return False

    try:
        # Send SIGTERM for graceful shutdown
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to process {pid}")

        # Wait for process to terminate
        start = time.time()
        while time.time() - start < timeout:
            try:
                os.kill(pid, 0)  # Check if process exists
                time.sleep(0.1)
            except OSError:
                # Process terminated
                _cleanup_pidfile(pidfile)
                print("Daemon stopped")
                return True

        # Process still running, send SIGKILL
        print(f"Process {pid} still running after {timeout}s, sending SIGKILL...")
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)

        _cleanup_pidfile(pidfile)
        print("Daemon killed")
        return True

    except OSError as e:
        print(f"Failed to stop daemon: {e}")
        _cleanup_pidfile(pidfile)
        return False


def get_daemon_status(pidfile: str) -> tuple[bool, int | None]:
    """Check if a daemon is running.

    Args:
        pidfile: Path to the PID file

    Returns:
        Tuple of (is_running, pid or None)
    """
    try:
        with open(pidfile, "r") as f:
            pid = int(f.read().strip())
    except FileNotFoundError:
        return False, None
    except ValueError:
        return False, None

    try:
        os.kill(pid, 0)  # Check if process exists
        return True, pid
    except OSError:
        # Stale PID file
        return False, pid


def check_daemon_running(pidfile: str) -> bool:
    """Check if daemon is running, clean up stale PID file if needed.

    Args:
        pidfile: Path to the PID file

    Returns:
        True if daemon is running
    """
    running, pid = get_daemon_status(pidfile)
    if not running and pid is not None:
        # Stale PID file
        _cleanup_pidfile(pidfile)
    return running


def _cleanup_pidfile(pidfile: str) -> None:
    """Remove PID file if it exists."""
    try:
        if os.path.exists(pidfile):
            os.remove(pidfile)
    except OSError:
        pass


def get_adapter_pidfile(adapter_name: str) -> str:
    """Get the PID file path for an adapter.

    Args:
        adapter_name: Name of the adapter (e.g., 'discord', 'teams')

    Returns:
        Path to the adapter's PID file
    """
    return DEFAULT_ADAPTER_PIDFILE_PATTERN.format(name=adapter_name)
