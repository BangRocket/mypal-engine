"""
Logging configuration with console and PostgreSQL database handlers.

Usage:
    from logging_config import get_logger
    logger = get_logger("api")
    logger.info("Server started", extra={"user_id": "123"})
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as DBSession

# ANSI color codes for console output
COLORS = {
    "DEBUG": "\033[36m",  # Cyan
    "INFO": "\033[32m",  # Green
    "WARNING": "\033[33m",  # Yellow
    "ERROR": "\033[31m",  # Red
    "CRITICAL": "\033[35m",  # Magenta
    "RESET": "\033[0m",
}

# Module-specific colors for tags — matched by prefix (first match wins)
TAG_COLORS = {
    "gateway": "\033[94m",  # Bright blue
    "adapters": "\033[93m",  # Bright yellow
    "adapter_manager": "\033[93m",  # Bright yellow (same family)
    "rook": "\033[95m",  # Bright magenta
    "memory": "\033[38;5;219m",  # Pink
    "thread": "\033[38;5;81m",  # Sky blue
    "db": "\033[92m",  # Bright green
    "llm": "\033[91m",  # Bright red
    "email": "\033[97m",  # Bright white
    "tools": "\033[36m",  # Cyan
    "sandbox": "\033[38;5;208m",  # Orange
    "emotional": "\033[38;5;213m",  # Hot pink
    "intentions": "\033[38;5;141m",  # Light purple
    "topic": "\033[38;5;179m",  # Gold
    "contradiction": "\033[38;5;167m",  # Salmon
    "ors": "\033[38;5;114m",  # Sage green
    "prompt_builder": "\033[38;5;153m",  # Periwinkle
    "websockets": "\033[90m",  # Dark gray
}


def utcnow():
    """Return current UTC time (naive, for SQLite compatibility)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ColoredConsoleFormatter(logging.Formatter):
    """Formatter that adds colors and matches existing tag-based style."""

    @staticmethod
    def _get_tag_color(name: str) -> str:
        """Get color for a logger name using prefix matching."""
        for prefix, color in TAG_COLORS.items():
            if name == prefix or name.startswith(prefix + ".") or name.startswith(prefix + "_"):
                return color
        return "\033[37m"  # Default: white

    # Display-friendly names for third-party loggers
    TAG_RENAMES = {
        "uvicorn": "gateway.api",
        "uvicorn.error": "gateway.api",
    }

    def format(self, record: logging.LogRecord) -> str:
        level_color = COLORS.get(record.levelname, "")
        reset = COLORS["RESET"]

        tag = self.TAG_RENAMES.get(record.name, record.name)
        tag_color = self._get_tag_color(tag)

        timestamp = datetime.now().strftime("%H:%M:%S")
        level_str = f"{level_color}{record.levelname:8}{reset}"
        tag_str = f"{tag_color}[{tag}]{reset}"

        extra_parts = []
        if hasattr(record, "user_id") and record.user_id:
            extra_parts.append(f"user={record.user_id}")
        if hasattr(record, "session_id") and record.session_id:
            extra_parts.append(f"session={record.session_id[:8]}")
        if hasattr(record, "channel_id") and record.channel_id:
            extra_parts.append(f"channel={record.channel_id}")

        extra_str = f" ({', '.join(extra_parts)})" if extra_parts else ""
        msg = f"{timestamp} {level_str} {tag_str} {record.getMessage()}{extra_str}"

        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)

        return msg


class DatabaseHandler(logging.Handler):
    """Async logging handler that writes to PostgreSQL via background thread."""

    def __init__(self, level: int = logging.INFO):
        super().__init__(level)
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=1000)
        self._db_session_factory = None
        self._shutdown = False
        self._thread: threading.Thread | None = None

    def set_session_factory(self, session_factory):
        """Set the SQLAlchemy session factory and start the background thread."""
        self._db_session_factory = session_factory
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        """Background worker that writes logs to the database."""
        from mypalclara.db.models import LogEntry

        batch: list[dict[str, Any]] = []
        batch_size = 10
        flush_interval = 2.0

        while not self._shutdown:
            try:
                try:
                    record_dict = self._queue.get(timeout=flush_interval)
                    batch.append(record_dict)
                except Empty:
                    pass

                while len(batch) < batch_size:
                    try:
                        record_dict = self._queue.get_nowait()
                        batch.append(record_dict)
                    except Empty:
                        break

                if batch and self._db_session_factory:
                    self._flush_batch(batch, LogEntry)
                    batch = []

            except Exception as e:
                print(f"[logging] Database handler error: {e}", file=sys.stderr)
                batch = []

    def _flush_batch(self, batch: list[dict[str, Any]], LogEntry):
        """Write a batch of logs to the database."""
        session: DBSession | None = None
        try:
            session = self._db_session_factory()
            for record_dict in batch:
                entry = LogEntry(**record_dict)
                session.add(entry)
            session.commit()
        except Exception as e:
            if session:
                session.rollback()
            print(f"[logging] Failed to write logs: {e}", file=sys.stderr)
        finally:
            if session:
                session.close()

    def emit(self, record: logging.LogRecord):
        """Queue a log record for async database insertion."""
        if self._shutdown or self._db_session_factory is None:
            return

        try:
            extra_data = {}
            for key in ["request_id", "duration_ms", "status_code", "method", "path", "channel_id", "guild_id"]:
                if hasattr(record, key):
                    extra_data[key] = getattr(record, key)

            record_dict = {
                "timestamp": utcnow(),
                "level": record.levelname,
                "logger_name": record.name,
                "message": record.getMessage(),
                "module": record.module,
                "function": record.funcName,
                "line_number": record.lineno,
                "exception": ("".join(traceback.format_exception(*record.exc_info)) if record.exc_info else None),
                "extra_data": json.dumps(extra_data) if extra_data else None,
                "user_id": getattr(record, "user_id", None),
                "session_id": getattr(record, "session_id", None),
            }

            try:
                self._queue.put_nowait(record_dict)
            except Exception:
                pass  # Drop log if queue is full

        except Exception:
            self.handleError(record)

    def shutdown(self):
        """Gracefully shutdown the handler."""
        self._shutdown = True
        if self._thread:
            self._thread.join(timeout=5.0)


class DiscordLogHandler(logging.Handler):
    """Async logging handler that mirrors logs to a Discord channel.

    Each log line becomes a Discord message. Handles rate limiting by batching
    messages when needed. Supports embeds for structured data.
    """

    # Discord markdown level indicators
    LEVEL_EMOJI = {
        "DEBUG": "🔍",
        "INFO": "ℹ️",
        "WARNING": "⚠️",
        "ERROR": "❌",
        "CRITICAL": "🔴",
    }

    # Tag colors for embeds (Discord decimal color values)
    TAG_EMBED_COLORS = {
        "rook": 0xAA55FF,  # Purple
        "thread": 0x00FFFF,  # Cyan
        "discord": 0xFFFF00,  # Yellow
        "db": 0x00FF00,  # Green
        "llm": 0xFF5555,  # Red
        "email": 0xFFFFFF,  # White
        "tools": 0x00FFFF,  # Cyan
        "sandbox": 0xFF55FF,  # Magenta
        "organic": 0xFFAA00,  # Orange
        "emotional": 0xFF69B4,  # Pink
        "topic": 0x9370DB,  # Medium Purple
    }

    def __init__(self, level: int = logging.INFO):
        super().__init__(level)
        self._queue: Queue[dict] = Queue(maxsize=500)
        self._bot = None
        self._channel_id: int | None = None
        self._shutdown = False
        self._task = None
        self._loop = None

    def set_bot(self, bot, channel_id: int, loop):
        """Set the Discord bot client and start the background task."""
        self._bot = bot
        self._channel_id = channel_id
        self._loop = loop
        self._task = loop.create_task(self._worker())

    async def _worker(self):
        """Background worker that sends logs to Discord."""
        import asyncio

        while not self._shutdown:
            try:
                # Batch messages to respect rate limits (~5/5s per channel)
                items: list[dict] = []
                batch_size = 5
                flush_interval = 1.0

                # Wait for first message or timeout
                await asyncio.sleep(flush_interval)

                # Collect available items (up to batch size)
                while len(items) < batch_size:
                    try:
                        item = self._queue.get_nowait()
                        items.append(item)
                    except Empty:
                        break

                if items and self._bot and self._channel_id:
                    await self._send_items(items)

            except Exception as e:
                print(f"[logging] Discord handler error: {e}", file=sys.stderr)

    async def _send_items(self, items: list[dict]):
        """Send items (messages or embeds) to Discord channel."""
        try:
            channel = self._bot.get_channel(self._channel_id)
            if not channel:
                return

            for item in items:
                try:
                    if item.get("embed"):
                        await channel.send(embed=item["embed"])
                    else:
                        msg = item.get("message", "")
                        # Discord message limit is 2000 chars
                        if len(msg) > 1990:
                            msg = msg[:1990] + "..."
                        await channel.send(msg)
                except Exception as e:
                    print(f"[logging] Failed to send to Discord: {e}", file=sys.stderr)

        except Exception as e:
            print(f"[logging] Discord send error: {e}", file=sys.stderr)

    def _strip_ansi(self, text: str) -> str:
        """Remove ANSI color codes from text."""
        import re

        ansi_escape = re.compile(r"\x1b\[[0-9;]*m")
        return ansi_escape.sub("", text)

    def _format_discord_message(self, record: logging.LogRecord) -> str:
        """Format a log record for Discord with markdown."""
        emoji = self.LEVEL_EMOJI.get(record.levelname, "")
        tag = record.name
        timestamp = datetime.now().strftime("%H:%M:%S")
        message = record.getMessage()

        # Use Discord code block for the message
        if record.levelname in ("ERROR", "CRITICAL"):
            # Errors get highlighted
            return f"{emoji} `{timestamp}` **[{tag}]** ```diff\n- {message}```"
        elif record.levelname == "WARNING":
            return f"{emoji} `{timestamp}` **[{tag}]** ```fix\n{message}```"
        else:
            return f"`{timestamp}` **[{tag}]** {message}"

    def emit(self, record: logging.LogRecord):
        """Queue a log record for Discord."""
        if self._shutdown or self._bot is None:
            return

        try:
            # Format the message for Discord
            msg = self._format_discord_message(record)
            msg = self._strip_ansi(msg)

            try:
                self._queue.put_nowait({"message": msg})
            except Exception:
                pass  # Drop log if queue is full

        except Exception:
            self.handleError(record)

    def shutdown(self):
        """Gracefully shutdown the handler."""
        self._shutdown = True
        if self._task:
            self._task.cancel()

    async def send_direct(self, message: str):
        """Send a message directly to the log channel (for startup/shutdown)."""
        if self._bot and self._channel_id:
            try:
                channel = self._bot.get_channel(self._channel_id)
                if channel:
                    await channel.send(f"**{message}**")
            except Exception as e:
                print(f"[logging] Failed to send direct message: {e}", file=sys.stderr)

    async def send_embed(
        self,
        title: str,
        description: str | None = None,
        color: int | None = None,
        fields: list[dict] | None = None,
        footer: str | None = None,
        tag: str | None = None,
    ):
        """Send a rich embed to the log channel.

        Args:
            title: Embed title
            description: Embed description (supports Discord markdown)
            color: Embed color (decimal int). If None, uses tag color or default.
            fields: List of {"name": str, "value": str, "inline": bool} dicts
            footer: Footer text
            tag: Tag name for auto-color (e.g., "rook", "emotional")
        """
        if not self._bot or not self._channel_id:
            return

        try:
            import discord

            # Determine color
            if color is None:
                color = self.TAG_EMBED_COLORS.get(tag, 0x5865F2)  # Discord blurple default

            embed = discord.Embed(
                title=title,
                description=description,
                color=discord.Color(color),
                timestamp=datetime.now(timezone.utc),
            )

            if fields:
                for field in fields:
                    embed.add_field(
                        name=field.get("name", ""),
                        value=field.get("value", ""),
                        inline=field.get("inline", True),
                    )

            if footer:
                embed.set_footer(text=footer)

            # Queue the embed
            try:
                self._queue.put_nowait({"embed": embed})
            except Exception:
                pass  # Drop if queue full

        except Exception as e:
            print(f"[logging] Failed to create embed: {e}", file=sys.stderr)

    def queue_embed(
        self,
        title: str,
        description: str | None = None,
        color: int | None = None,
        fields: list[dict] | None = None,
        footer: str | None = None,
        tag: str | None = None,
    ):
        """Queue an embed for sending (non-async version for use in sync code).

        Uses the event loop to schedule the embed send.
        """
        if self._loop and not self._shutdown:
            self._loop.call_soon_threadsafe(
                lambda: self._loop.create_task(self.send_embed(title, description, color, fields, footer, tag))
            )


# Global state
_db_handler: DatabaseHandler | None = None
_discord_handler: DiscordLogHandler | None = None
_initialized = False


def _get_console_level() -> int:
    """Get console log level from environment variable."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, level_name, logging.INFO)


def init_logging(session_factory=None, console_level: int | None = None):
    """Initialize the logging system with console and optional database handlers."""
    global _db_handler, _initialized

    if _initialized:
        return

    if console_level is None:
        console_level = _get_console_level()

    # Clear any existing handlers on root logger (from basicConfig or other sources)
    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(ColoredConsoleFormatter())

    # Database handler
    _db_handler = DatabaseHandler(level=logging.INFO)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(_db_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    if session_factory:
        _db_handler.set_session_factory(session_factory)

    _initialized = True


def set_db_session_factory(session_factory):
    """Set the database session factory after init."""
    global _db_handler
    if _db_handler:
        _db_handler.set_session_factory(session_factory)


def init_discord_logging(bot, channel_id: int, loop) -> DiscordLogHandler | None:
    """Initialize Discord log handler after bot is ready.

    Args:
        bot: Discord bot client
        channel_id: Discord channel ID to send logs to
        loop: asyncio event loop

    Returns:
        The DiscordLogHandler instance, or None if channel_id is invalid
    """
    global _discord_handler

    if not channel_id:
        return None

    if _discord_handler is None:
        _discord_handler = DiscordLogHandler(level=_get_console_level())
        # Use same formatter as console but without colors
        _discord_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s [%(name)s] %(message)s", datefmt="%H:%M:%S")
        )
        logging.getLogger().addHandler(_discord_handler)

    _discord_handler.set_bot(bot, channel_id, loop)
    return _discord_handler


def get_discord_handler() -> DiscordLogHandler | None:
    """Get the Discord log handler instance."""
    return _discord_handler


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name."""
    if not _initialized:
        init_logging()
    return logging.getLogger(name)


def shutdown_logging():
    """Gracefully shutdown logging."""
    global _db_handler, _discord_handler
    if _db_handler:
        _db_handler.shutdown()
    if _discord_handler:
        _discord_handler.shutdown()
