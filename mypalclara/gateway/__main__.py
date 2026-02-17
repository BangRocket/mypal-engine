"""Entry point for Clara Gateway with daemon and adapter management.

Usage:
    # Start gateway in foreground
    python -m gateway

    # Start gateway as daemon with all enabled adapters
    python -m gateway start

    # Start gateway with specific adapter only
    python -m gateway start --adapter discord

    # Stop gateway daemon
    python -m gateway stop

    # Check status
    python -m gateway status

    # Restart gateway
    python -m gateway restart

    # Manage individual adapters
    python -m gateway adapter discord start
    python -m gateway adapter discord stop
    python -m gateway adapter discord status

    # Tail logs
    python -m gateway logs
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="clara-gateway",
        description="Clara Gateway - Central message processing hub for platform adapters",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                      Run in foreground (development)
  %(prog)s start                Start daemon with all enabled adapters
  %(prog)s start -f             Start in foreground with adapters
  %(prog)s start --adapter discord  Start with specific adapter
  %(prog)s stop                 Stop daemon
  %(prog)s status               Show running status
  %(prog)s restart              Restart daemon
  %(prog)s adapter discord start  Start individual adapter
  %(prog)s logs                 Tail gateway logs
""",
    )

    # Global options
    parser.add_argument(
        "--host",
        default=os.getenv("CLARA_GATEWAY_HOST", "127.0.0.1"),
        help="Bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CLARA_GATEWAY_PORT", "18789")),
        help="Port to listen on (default: 18789)",
    )
    parser.add_argument(
        "--hooks-dir",
        default=os.getenv("CLARA_HOOKS_DIR", "./hooks"),
        help="Directory containing hooks.yaml (default: ./hooks)",
    )
    parser.add_argument(
        "--scheduler-dir",
        default=os.getenv("CLARA_SCHEDULER_DIR", "."),
        help="Directory containing scheduler.yaml (default: .)",
    )
    parser.add_argument(
        "--adapters-config",
        default=os.getenv("CLARA_ADAPTERS_CONFIG"),
        help="Path to adapters.yaml (default: gateway/adapters.yaml)",
    )
    parser.add_argument(
        "--pidfile",
        default=os.getenv("CLARA_GATEWAY_PIDFILE", "/tmp/clara-gateway.pid"),
        help="PID file path (default: /tmp/clara-gateway.pid)",
    )
    parser.add_argument(
        "--logfile",
        default=os.getenv("CLARA_GATEWAY_LOGFILE"),
        help="Log file path when daemonized (default: none)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Start command
    start_parser = subparsers.add_parser("start", help="Start gateway daemon with adapters")
    start_parser.add_argument(
        "-f",
        "--foreground",
        action="store_true",
        help="Run in foreground instead of daemonizing",
    )
    start_parser.add_argument(
        "--adapter",
        action="append",
        dest="adapters",
        help="Start specific adapter(s) only (can be repeated)",
    )
    start_parser.add_argument(
        "--no-adapters",
        action="store_true",
        help="Start gateway without spawning any adapters",
    )

    # Stop command
    subparsers.add_parser("stop", help="Stop gateway daemon")

    # Status command
    subparsers.add_parser("status", help="Show gateway and adapter status")

    # Restart command
    restart_parser = subparsers.add_parser("restart", help="Restart gateway daemon")
    restart_parser.add_argument(
        "-f",
        "--foreground",
        action="store_true",
        help="Run in foreground after restart",
    )

    # Adapter subcommand
    adapter_parser = subparsers.add_parser("adapter", help="Manage individual adapters")
    adapter_parser.add_argument("name", help="Adapter name (e.g., discord, teams)")
    adapter_parser.add_argument(
        "action",
        choices=["start", "stop", "restart", "status"],
        help="Action to perform",
    )

    # Logs command
    logs_parser = subparsers.add_parser("logs", help="Tail gateway logs")
    logs_parser.add_argument(
        "-n",
        "--lines",
        type=int,
        default=50,
        help="Number of lines to show (default: 50)",
    )
    logs_parser.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow log output",
    )

    return parser


def cmd_start(args: argparse.Namespace) -> None:
    """Handle start command."""
    from mypalclara.gateway.daemon import (
        DEFAULT_GATEWAY_PIDFILE,
        check_daemon_running,
        daemonize,
    )

    pidfile = args.pidfile

    # Check if already running
    if check_daemon_running(pidfile):
        with open(pidfile) as f:
            pid = f.read().strip()
        print(f"Gateway already running (PID: {pid})")
        sys.exit(1)

    # [] = start no adapters, None = start all enabled, ["foo"] = start specific
    adapters = [] if args.no_adapters else args.adapters

    if args.foreground:
        print(f"Starting gateway on {args.host}:{args.port}")
        if adapters:
            print(f"Starting adapters: {', '.join(adapters)}")
        elif not args.no_adapters:
            print("Starting all enabled adapters")
        else:
            print("No adapters will be started")
        _run_gateway(args, adapters)
    else:
        print(f"Starting gateway daemon (PID file: {pidfile})...")
        if args.logfile:
            print(f"Logging to: {args.logfile}")
        daemonize(pidfile, args.logfile)
        _run_gateway(args, adapters)


def cmd_stop(args: argparse.Namespace) -> None:
    """Handle stop command."""
    from mypalclara.gateway.daemon import stop_daemon

    if stop_daemon(args.pidfile):
        sys.exit(0)
    else:
        sys.exit(1)


def cmd_status(args: argparse.Namespace) -> None:
    """Handle status command."""
    from mypalclara.gateway.daemon import get_adapter_pidfile, get_daemon_status

    # Check gateway status
    running, pid = get_daemon_status(args.pidfile)

    print("Gateway Status")
    print("-" * 40)
    if running:
        print(f"  Gateway: running (PID: {pid})")
    else:
        print("  Gateway: stopped")

    # Check adapter statuses from PID files
    print("\nAdapter Status")
    print("-" * 40)

    # Load adapter config to get names
    config_path = args.adapters_config or (Path(__file__).parent / "adapters.yaml")
    if config_path.exists() if isinstance(config_path, Path) else Path(config_path).exists():
        import yaml

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        adapter_names = list(config.get("adapters", {}).keys())
    else:
        adapter_names = ["discord", "cli", "teams"]  # Default list

    for name in adapter_names:
        pidfile = get_adapter_pidfile(name)
        adapter_running, adapter_pid = get_daemon_status(pidfile)
        if adapter_running:
            print(f"  {name}: running (PID: {adapter_pid})")
        else:
            print(f"  {name}: stopped")


def cmd_restart(args: argparse.Namespace) -> None:
    """Handle restart command."""
    from mypalclara.gateway.daemon import check_daemon_running, stop_daemon

    # Stop if running
    if check_daemon_running(args.pidfile):
        print("Stopping gateway...")
        stop_daemon(args.pidfile)
        # Wait a moment for cleanup
        import time

        time.sleep(1)

    # Start
    cmd_start(args)


def cmd_adapter(args: argparse.Namespace) -> None:
    """Handle adapter subcommand."""
    from mypalclara.gateway.daemon import get_adapter_pidfile, get_daemon_status

    name = args.name
    action = args.action
    pidfile = get_adapter_pidfile(name)

    if action == "status":
        running, pid = get_daemon_status(pidfile)
        if running:
            print(f"Adapter {name}: running (PID: {pid})")
        else:
            print(f"Adapter {name}: stopped")

    elif action == "start":
        # For individual adapter start, we need the gateway to be running
        running, _ = get_daemon_status(args.pidfile)
        if not running:
            print("Error: Gateway must be running to start adapters")
            print("Run 'clara-gateway start' first")
            sys.exit(1)

        # TODO: Implement IPC to tell gateway to start adapter
        # For now, we can start the adapter directly
        print(f"Starting adapter {name}...")
        _start_adapter_directly(name, args)

    elif action == "stop":
        from mypalclara.gateway.daemon import stop_daemon as stop_adapter_daemon

        if stop_adapter_daemon(pidfile):
            print(f"Adapter {name} stopped")
        else:
            print(f"Adapter {name} was not running")

    elif action == "restart":
        from mypalclara.gateway.daemon import stop_daemon as stop_adapter_daemon

        stop_adapter_daemon(pidfile)
        import time

        time.sleep(1)
        _start_adapter_directly(name, args)


def cmd_logs(args: argparse.Namespace) -> None:
    """Handle logs command."""
    import subprocess

    logfile = args.logfile
    if not logfile:
        print("No log file configured. Use --logfile when starting the gateway.")
        sys.exit(1)

    if not os.path.exists(logfile):
        print(f"Log file not found: {logfile}")
        sys.exit(1)

    cmd = ["tail"]
    if args.follow:
        cmd.append("-f")
    cmd.extend(["-n", str(args.lines), logfile])

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass


def _start_adapter_directly(name: str, args: argparse.Namespace) -> None:
    """Start an adapter as a subprocess directly.

    This is used when starting an individual adapter without the full
    adapter manager. For production use, prefer running adapters through
    the gateway daemon.
    """
    import subprocess

    # Load adapter config
    config_path = args.adapters_config or (Path(__file__).parent / "adapters.yaml")
    if not Path(config_path).exists():
        print(f"Adapter config not found: {config_path}")
        sys.exit(1)

    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    adapters = config.get("adapters", {})
    if name not in adapters:
        print(f"Unknown adapter: {name}")
        print(f"Available adapters: {', '.join(adapters.keys())}")
        sys.exit(1)

    adapter_config = adapters[name]
    module = adapter_config.get("module", f"adapters.{name}")

    # Build environment
    env = os.environ.copy()
    for key, value in adapter_config.get("env", {}).items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            env[key] = os.environ.get(env_var, "")
        else:
            env[key] = str(value)

    cmd = [sys.executable, "-m", module]
    print(f"Starting: {' '.join(cmd)}")

    # Run in background
    process = subprocess.Popen(
        cmd,
        env=env,
        start_new_session=True,
    )

    # Write PID file
    from mypalclara.gateway.daemon import get_adapter_pidfile

    pidfile = get_adapter_pidfile(name)
    with open(pidfile, "w") as f:
        f.write(str(process.pid))

    print(f"Adapter {name} started (PID: {process.pid})")


def _run_gateway(args: argparse.Namespace, adapter_names: list[str] | None) -> None:
    """Run the gateway (and optionally adapters) in the current process."""
    try:
        asyncio.run(_async_run_gateway(args, adapter_names))
    except KeyboardInterrupt:
        pass
    finally:
        # Clean up PID file
        if os.path.exists(args.pidfile):
            try:
                os.remove(args.pidfile)
            except OSError:
                pass
        # Use os._exit() to skip Python's async generator finalization phase
        # which causes noisy errors from MCP stdio_client cleanup
        os._exit(0)


async def _async_run_gateway(args: argparse.Namespace, adapter_names: list[str] | None) -> None:
    """Async main function for running gateway with adapters."""
    from config.logging import get_logger, init_logging
    from mypalclara.gateway.adapter_manager import get_adapter_manager
    from mypalclara.gateway.events import Event, EventType, emit
    from mypalclara.gateway.hooks import get_hook_manager
    from mypalclara.gateway.processor import MessageProcessor
    from mypalclara.gateway.scheduler import get_scheduler
    from mypalclara.gateway.server import GatewayServer

    init_logging()
    logger = get_logger("gateway")

    # Initialize hooks system
    hook_manager = get_hook_manager()
    hook_manager._hooks_dir = Path(args.hooks_dir)
    hooks_loaded = hook_manager.load_from_file()
    logger.info(f"Hooks system ready ({hooks_loaded} hooks loaded)")

    # Initialize scheduler
    scheduler = get_scheduler()
    scheduler._config_dir = Path(args.scheduler_dir)
    tasks_loaded = scheduler.load_from_file()
    logger.info(f"Scheduler ready ({tasks_loaded} tasks loaded)")

    # Create server and processor
    server = GatewayServer(host=args.host, port=args.port)
    processor = MessageProcessor()

    # Wire them together
    server.set_processor(processor)

    # Initialize processor
    await processor.initialize()

    # Start scheduler
    await scheduler.start()

    # Start server
    await server.start()

    # Initialize and start adapter manager
    # None = start all enabled, [] = start none, ["foo"] = start specific
    adapter_manager = None
    if adapter_names is None or adapter_names:
        config_path = args.adapters_config or (Path(__file__).parent / "adapters.yaml")
        adapter_manager = get_adapter_manager(config_path)
        await adapter_manager.start(adapter_names)

    # Emit startup event
    await emit(
        Event(
            type=EventType.GATEWAY_STARTUP,
            data={
                "host": args.host,
                "port": args.port,
                "hooks_loaded": hooks_loaded,
                "tasks_loaded": tasks_loaded,
                "adapters": adapter_names,
            },
        )
    )

    # Set up signal handlers
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Received shutdown signal")
        stop_event.set()

    import signal

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    logger.info("Gateway ready and accepting connections")
    logger.info(f"Connect adapters to: ws://{args.host}:{args.port}")

    # Wait for shutdown
    await stop_event.wait()

    # Emit shutdown event
    await emit(
        Event(
            type=EventType.GATEWAY_SHUTDOWN,
            data={"reason": "signal"},
        )
    )

    # Cleanup
    logger.info("Shutting down gateway...")

    if adapter_manager:
        await adapter_manager.stop()

    await processor.shutdown()
    await scheduler.stop()
    await server.stop()
    logger.info("Gateway stopped")


def main() -> None:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    # If no command, run in foreground with adapters (same as 'start -f')
    if args.command is None:
        print(f"Starting gateway on {args.host}:{args.port}")
        print("Starting all enabled adapters")
        # None = start all enabled adapters
        _run_gateway(args, None)
        return

    # Dispatch to command handlers
    if args.command == "start":
        cmd_start(args)
    elif args.command == "stop":
        cmd_stop(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "restart":
        cmd_restart(args)
    elif args.command == "adapter":
        cmd_adapter(args)
    elif args.command == "logs":
        cmd_logs(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
