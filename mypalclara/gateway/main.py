"""Main entry point for the Clara Gateway.

Usage:
    poetry run python -m gateway
    poetry run python -m gateway --host 0.0.0.0 --port 18789

Environment variables:
    CLARA_GATEWAY_HOST - Bind address (default: 127.0.0.1)
    CLARA_GATEWAY_PORT - Port to listen on (default: 18789)
    CLARA_GATEWAY_SECRET - Shared secret for authentication (optional)
    CLARA_HOOKS_DIR - Directory containing hooks.yaml (default: ./hooks)
    CLARA_SCHEDULER_DIR - Directory containing scheduler.yaml (default: .)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from config.logging import get_logger, init_logging
from mypalclara.gateway.events import Event, EventType, emit, get_event_emitter
from mypalclara.gateway.hooks import get_hook_manager
from mypalclara.gateway.processor import MessageProcessor
from mypalclara.gateway.scheduler import get_scheduler
from mypalclara.gateway.server import GatewayServer

init_logging()
logger = get_logger("gateway")


async def main(host: str, port: int, hooks_dir: str, scheduler_dir: str) -> None:
    """Run the gateway server.

    Args:
        host: Bind address
        port: Port to listen on
        hooks_dir: Directory containing hooks.yaml
        scheduler_dir: Directory containing scheduler.yaml
    """
    # Initialize hooks system
    hook_manager = get_hook_manager()
    hook_manager._hooks_dir = Path(hooks_dir)
    hooks_loaded = hook_manager.load_from_file()
    logger.info(f"Hooks system ready ({hooks_loaded} hooks loaded)")

    # Initialize scheduler
    scheduler = get_scheduler()
    scheduler._config_dir = Path(scheduler_dir)
    tasks_loaded = scheduler.load_from_file()
    logger.info(f"Scheduler ready ({tasks_loaded} tasks loaded)")

    # Create server and processor
    server = GatewayServer(host=host, port=port)
    processor = MessageProcessor()

    # Wire them together
    server.set_processor(processor)

    # Initialize processor
    await processor.initialize()

    # Start scheduler
    await scheduler.start()

    # Start server
    await server.start()

    # Emit startup event
    await emit(
        Event(
            type=EventType.GATEWAY_STARTUP,
            data={
                "host": host,
                "port": port,
                "hooks_loaded": hooks_loaded,
                "tasks_loaded": tasks_loaded,
            },
        )
    )

    # Set up signal handlers
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Received shutdown signal")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    logger.info("Gateway ready and accepting connections")
    logger.info(f"Connect adapters to: ws://{host}:{port}")

    # Wait for shutdown
    await stop_event.wait()

    # Emit shutdown event (before stopping services)
    await emit(
        Event(
            type=EventType.GATEWAY_SHUTDOWN,
            data={"reason": "signal"},
        )
    )

    # Cleanup
    logger.info("Shutting down gateway...")
    await processor.shutdown()
    await scheduler.stop()
    await server.stop()
    logger.info("Gateway stopped")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Clara Gateway Server")

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

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    try:
        asyncio.run(main(args.host, args.port, args.hooks_dir, args.scheduler_dir))
    except KeyboardInterrupt:
        pass
    # Use os._exit() to skip Python's async generator finalization phase
    # which causes noisy errors from MCP stdio_client cleanup
    os._exit(0)
