"""Abstract base class for email providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.models import EmailAccount


@dataclass
class EmailMessage:
    """Represents an email message from any provider."""

    uid: str  # Unique identifier (provider-specific format)
    from_addr: str  # Sender email address
    subject: str  # Email subject
    snippet: str  # First ~200 chars of body
    received_at: datetime  # When the email was received
    has_attachments: bool = False
    to_addrs: list[str] | None = None
    cc_addrs: list[str] | None = None
    # Full body content (optional, populated by get_email_by_id or search)
    full_body: str | None = None  # Plain text body
    body_html: str | None = None  # HTML body if available
    is_read: bool | None = None  # Read/unread status


class EmailProvider(ABC):
    """Abstract base class for email providers (Gmail, IMAP, etc.)."""

    def __init__(self, account: EmailAccount):
        """Initialize provider with account configuration.

        Args:
            account: EmailAccount database model with credentials
        """
        self.account = account
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if provider is currently connected."""
        return self._connected

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to email server.

        Returns:
            True if connection successful, False otherwise
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to email server."""
        pass

    @abstractmethod
    async def get_new_messages(
        self,
        since_uid: str | None = None,
        since_timestamp: datetime | None = None,
        limit: int = 50,
    ) -> list[EmailMessage]:
        """Fetch new messages since last check.

        Args:
            since_uid: Fetch messages after this UID
            since_timestamp: Fetch messages after this time
            limit: Maximum messages to return

        Returns:
            List of new email messages
        """
        pass

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str | None]:
        """Test connection with current credentials.

        Returns:
            Tuple of (success, error_message)
        """
        pass

    @abstractmethod
    async def search_emails(
        self,
        query: str | None = None,
        from_addr: str | None = None,
        subject: str | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
        unread_only: bool = False,
        include_body: bool = False,
        limit: int = 20,
        folder: str = "INBOX",
    ) -> list[EmailMessage]:
        """Search emails with filters.

        Args:
            query: Free text search query
            from_addr: Filter by sender address/name
            subject: Filter by subject contains
            after: Messages after this date
            before: Messages before this date
            unread_only: Only return unread messages
            include_body: Include full body content (slower)
            limit: Maximum messages to return
            folder: Folder to search in (IMAP) or label (Gmail)

        Returns:
            List of matching email messages
        """
        pass

    @abstractmethod
    async def get_email_by_id(
        self,
        uid: str,
        include_body: bool = True,
        folder: str = "INBOX",
    ) -> EmailMessage | None:
        """Get a specific email by its UID.

        Args:
            uid: Unique identifier of the email
            include_body: Include full body content
            folder: Folder containing the email (IMAP only)

        Returns:
            EmailMessage if found, None otherwise
        """
        pass

    async def list_folders(self) -> list[str]:
        """List available folders/labels.

        Returns:
            List of folder names (IMAP) or labels (Gmail)
        """
        return ["INBOX"]  # Default implementation

    async def __aenter__(self) -> EmailProvider:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.disconnect()
