"""Platform abstraction layer for Clara.

Defines common interfaces for different communication platforms (Discord, API, Slack, etc.)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class PlatformMessage:
    """Unified message format across all platforms."""

    # Unified user ID (platform-prefixed, e.g., "discord-123", "api-demo-user")
    user_id: str

    # Platform identifier
    platform: str  # "api", "discord", "slack", "telegram"

    # Original platform-specific user ID
    platform_user_id: str

    # Message content
    content: str

    # Channel/thread context (optional)
    channel_id: str | None = None
    thread_id: str | None = None

    # User display info
    user_name: str | None = None
    user_display_name: str | None = None

    # Attachments (platform-specific format)
    attachments: list[dict] = field(default_factory=list)

    # Timestamp
    timestamp: datetime = field(default_factory=datetime.now)

    # Platform-specific metadata
    metadata: dict = field(default_factory=dict)


@dataclass
class PlatformContext:
    """Context for a platform conversation."""

    # Platform identifier
    platform: str

    # Platform-specific channel object (e.g., discord.TextChannel)
    channel: Any = None

    # Platform-specific user object (e.g., discord.User)
    user: Any = None

    # Platform-specific message object
    message: Any = None

    # Additional context data
    guild_id: str | None = None
    guild_name: str | None = None
    channel_name: str | None = None

    # Participants in the conversation (for multi-user contexts)
    participants: list[dict] = field(default_factory=list)


class PlatformAdapter(ABC):
    """Base class for platform adapters.

    Each platform (Discord, Slack, API) should implement this interface
    to integrate with the Clara core.
    """

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Return the platform identifier (e.g., 'discord', 'api')."""
        ...

    def format_user_id(self, platform_user_id: str) -> str:
        """Format a platform-specific user ID to unified format.

        Override this if you need custom formatting.
        Default: {platform_name}-{platform_user_id}
        """
        return f"{self.platform_name}-{platform_user_id}"

    def parse_user_id(self, unified_user_id: str) -> tuple[str, str]:
        """Parse a unified user ID into (platform, platform_user_id).

        Returns (platform, original_id) tuple.
        """
        if "-" in unified_user_id:
            parts = unified_user_id.split("-", 1)
            return parts[0], parts[1]
        return "unknown", unified_user_id

    @abstractmethod
    async def send_message(
        self,
        context: PlatformContext,
        content: str,
        files: list[Any] | None = None,
    ) -> Any:
        """Send a message through this platform.

        Args:
            context: The platform context (channel, user, etc.)
            content: The message content to send
            files: Optional list of files to attach

        Returns:
            Platform-specific message object
        """
        ...

    @abstractmethod
    async def send_typing_indicator(self, context: PlatformContext) -> None:
        """Show a typing indicator on this platform."""
        ...

    async def on_message(self, message: PlatformMessage) -> str | None:
        """Handle an incoming message.

        Override this to implement custom message handling.
        Default implementation returns None (no response).

        Args:
            message: The incoming platform message

        Returns:
            Response content or None if no response
        """
        return None


class APIAdapter(PlatformAdapter):
    """Platform adapter for the REST API.

    This is a minimal implementation since the API handles messages
    synchronously through HTTP requests.
    """

    @property
    def platform_name(self) -> str:
        return "api"

    async def send_message(
        self,
        context: PlatformContext,
        content: str,
        files: list[Any] | None = None,
    ) -> dict:
        """API doesn't actively send messages - returns response dict."""
        return {"content": content, "files": files or []}

    async def send_typing_indicator(self, context: PlatformContext) -> None:
        """API doesn't have typing indicators."""
        pass
