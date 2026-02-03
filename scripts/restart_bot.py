#!/usr/bin/env python3
"""
Restart script for MyPalClara Discord bot.

This script safely restarts the Clara Discord bot by:
1. Asking for user confirmation
2. Optionally waiting for a specified delay
3. Gracefully stopping the running instance
4. Starting the bot as a daemon

Usage:
    poetry run python scripts/restart_bot.py [options]

Options:
    -y, --yes           Skip confirmation prompt
    -d, --delay SECS    Delay before restart in seconds (default: 0)
    --pidfile FILE      PID file path (default: /tmp/clara-discord.pid)
    --logfile FILE      Log file for the new daemon (default: none)
    --force             Force restart even if bot doesn't appear to be running
    --no-start          Only stop the bot, don't restart
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_PIDFILE = "/tmp/clara-discord.pid"
PROJECT_ROOT = Path(__file__).parent.parent


def get_pid_from_file(pidfile: str) -> int | None:
    """Read PID from file, returning None if not found or invalid."""
    try:
        with open(pidfile, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_bot_status(pidfile: str) -> tuple[bool, int | None]:
    """
    Check if the bot is running.

    Returns:
        Tuple of (is_running, pid)
    """
    pid = get_pid_from_file(pidfile)
    if pid is None:
        return False, None
    return is_process_running(pid), pid


def stop_bot(pidfile: str, timeout: int = 10) -> bool:
    """
    Stop the running bot gracefully.

    Args:
        pidfile: Path to the PID file
        timeout: Maximum seconds to wait for graceful shutdown

    Returns:
        True if bot was stopped (or wasn't running), False on failure
    """
    pid = get_pid_from_file(pidfile)
    if pid is None:
        print("No PID file found - bot may not be running")
        return True

    if not is_process_running(pid):
        print(f"Process {pid} not running (stale PID file)")
        # Clean up stale PID file
        try:
            os.remove(pidfile)
        except OSError:
            pass
        return True

    print(f"Stopping Clara (PID: {pid})...")

    # Send SIGTERM for graceful shutdown
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        print(f"Failed to send SIGTERM: {e}")
        return False

    # Wait for graceful shutdown
    print("Waiting for graceful shutdown", end="", flush=True)
    for _ in range(timeout):
        time.sleep(1)
        print(".", end="", flush=True)
        if not is_process_running(pid):
            print(" stopped!")
            break
    else:
        # Process still running, force kill
        print(" timeout!")
        print(f"Sending SIGKILL to {pid}...")
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(1)
        except OSError:
            pass

    # Clean up PID file
    if os.path.exists(pidfile):
        try:
            os.remove(pidfile)
        except OSError:
            pass

    return not is_process_running(pid)


def start_bot_daemon(pidfile: str, logfile: str | None = None) -> bool:
    """
    Start the bot as a daemon using poetry.

    Args:
        pidfile: Path to the PID file
        logfile: Optional path to log file

    Returns:
        True if bot was started successfully
    """
    print("Starting Clara as daemon...")

    cmd = [
        "poetry", "run", "python", "discord_bot.py",
        "--daemon",
        "--pidfile", pidfile,
    ]
    if logfile:
        cmd.extend(["--logfile", logfile])

    try:
        # Run from project root
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            print(f"Failed to start daemon: {result.stderr}")
            return False

        # Wait a moment for daemon to initialize
        time.sleep(2)

        # Verify it's running
        is_running, pid = get_bot_status(pidfile)
        if is_running:
            print(f"Clara started successfully (PID: {pid})")
            return True
        else:
            print("Daemon process exited unexpectedly")
            return False

    except subprocess.TimeoutExpired:
        print("Timed out waiting for daemon to start")
        return False
    except Exception as e:
        print(f"Error starting daemon: {e}")
        return False


def countdown(seconds: int):
    """Display a countdown timer."""
    if seconds <= 0:
        return

    print(f"Restarting in {seconds} seconds...", end="", flush=True)
    for remaining in range(seconds, 0, -1):
        print(f"\rRestarting in {remaining} seconds... ", end="", flush=True)
        time.sleep(1)
    print("\rRestarting now!                    ")


def confirm_restart() -> bool:
    """Ask for user confirmation."""
    while True:
        response = input("Are you sure you want to restart Clara? [y/N]: ").strip().lower()
        if response in ("y", "yes"):
            return True
        if response in ("n", "no", ""):
            return False
        print("Please enter 'y' or 'n'")


def main():
    parser = argparse.ArgumentParser(
        description="Restart the Clara Discord bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Interactive restart with confirmation
    poetry run python scripts/restart_bot.py

    # Restart with 30 second delay, no confirmation
    poetry run python scripts/restart_bot.py -y -d 30

    # Just stop the bot (no restart)
    poetry run python scripts/restart_bot.py --no-start

    # Restart with log file
    poetry run python scripts/restart_bot.py -y --logfile /var/log/clara.log
""",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    parser.add_argument(
        "-d", "--delay",
        type=int,
        default=0,
        metavar="SECS",
        help="Delay before restart in seconds (default: 0)",
    )
    parser.add_argument(
        "--pidfile",
        default=DEFAULT_PIDFILE,
        help=f"PID file path (default: {DEFAULT_PIDFILE})",
    )
    parser.add_argument(
        "--logfile",
        default=None,
        help="Log file for the restarted daemon",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force restart even if bot doesn't appear running",
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Only stop the bot, don't restart",
    )

    args = parser.parse_args()

    # Check current status
    is_running, pid = get_bot_status(args.pidfile)

    if is_running:
        print(f"Clara is currently running (PID: {pid})")
    else:
        print("Clara does not appear to be running")
        if not args.force and not args.no_start:
            start_anyway = input("Start Clara anyway? [Y/n]: ").strip().lower()
            if start_anyway in ("n", "no"):
                print("Aborted.")
                return 1
            # Just start without the stop/restart flow
            if start_bot_daemon(args.pidfile, args.logfile):
                return 0
            return 1

    # Confirm restart
    if not args.yes:
        if not confirm_restart():
            print("Restart cancelled.")
            return 0

    # Countdown if delay specified
    if args.delay > 0:
        countdown(args.delay)

    # Stop the bot
    if not stop_bot(args.pidfile):
        print("Failed to stop Clara!")
        return 1

    # If --no-start, we're done
    if args.no_start:
        print("Clara stopped. Not restarting (--no-start specified).")
        return 0

    # Brief pause before restart
    time.sleep(1)

    # Start the bot
    if not start_bot_daemon(args.pidfile, args.logfile):
        print("Failed to start Clara!")
        return 1

    print("Restart complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
