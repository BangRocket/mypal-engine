"""IMAP email provider using imap-tools library."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from imap_tools import AND, MailBox, MailboxLoginError

from config.logging import get_logger
from email_service.credentials import decrypt_credential
from email_service.providers.base import EmailMessage, EmailProvider

if TYPE_CHECKING:
    from db.models import EmailAccount

logger = get_logger("email.imap")

# Default IMAP settings
DEFAULT_IMAP_PORT = 993


class IMAPProvider(EmailProvider):
    """IMAP email provider using imap-tools for robust message handling."""

    def __init__(self, account: EmailAccount):
        """Initialize IMAP provider.

        Args:
            account: EmailAccount with IMAP credentials (encrypted password)
        """
        super().__init__(account)
        self._server: str = ""
        self._username: str = ""
        self._password: str = ""

    async def connect(self) -> bool:
        """Validate credentials and store for later use."""
        try:
            self._server = self.account.imap_server or ""
            self._username = self.account.imap_username or self.account.email_address
            self._password = decrypt_credential(self.account.imap_password or "")

            if not self._password:
                logger.error(f"No password configured for {self.account.email_address}")
                return False

            if not self._server:
                logger.error(f"No IMAP server configured for {self.account.email_address}")
                return False

            self._connected = True
            logger.debug(f"IMAP credentials ready for {self._server} as {self._username}")
            return True

        except Exception as e:
            logger.error(f"IMAP credential setup failed: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Clear stored credentials."""
        self._password = ""
        self._connected = False

    def _get_mailbox(self) -> MailBox:
        """Create a new MailBox connection."""
        port = self.account.imap_port or DEFAULT_IMAP_PORT
        return MailBox(self._server, port=port)

    async def test_connection(self) -> tuple[bool, str | None]:
        """Test IMAP connection with current credentials."""
        try:
            if not await self.connect():
                return False, "Failed to setup credentials"

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._test_connection_sync)
            return result

        except Exception as e:
            return False, str(e)

    def _test_connection_sync(self) -> tuple[bool, str | None]:
        """Synchronous connection test."""
        try:
            with self._get_mailbox().login(self._username, self._password) as mailbox:
                # Just selecting INBOX is enough to verify
                mailbox.folder.set("INBOX")
                return True, None
        except MailboxLoginError as e:
            return False, f"Login failed: {e}"
        except Exception as e:
            return False, str(e)

    async def get_new_messages(
        self,
        since_uid: str | None = None,
        since_timestamp: datetime | None = None,
        limit: int = 50,
    ) -> list[EmailMessage]:
        """Fetch new messages from IMAP server."""
        if not self._connected:
            if not await self.connect():
                return []

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: self._fetch_new_messages_sync(since_uid, since_timestamp, limit),
            )
        except Exception as e:
            logger.error(f"Error fetching IMAP messages: {e}")
            return []

    def _fetch_new_messages_sync(
        self,
        since_uid: str | None,
        since_timestamp: datetime | None,
        limit: int,
    ) -> list[EmailMessage]:
        """Synchronous new message fetch."""
        messages = []

        try:
            with self._get_mailbox().login(self._username, self._password) as mailbox:
                mailbox.folder.set("INBOX")

                # Build criteria
                if since_uid:
                    # Fetch by UID range
                    criteria = AND(uid=f"{int(since_uid) + 1}:*")
                elif since_timestamp:
                    criteria = AND(date_gte=since_timestamp.date())
                else:
                    # Default to unseen
                    criteria = AND(seen=False)

                for msg in mailbox.fetch(criteria, limit=limit, reverse=True):
                    messages.append(self._convert_message(msg, include_body=False))

        except MailboxLoginError as e:
            logger.error(f"IMAP login failed: {e}")
        except Exception as e:
            logger.error(f"IMAP fetch error: {e}")

        return messages

    async def list_folders(self) -> list[str]:
        """List all available IMAP folders."""
        if not self._connected:
            if not await self.connect():
                return []

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._list_folders_sync)
        except Exception as e:
            logger.error(f"Error listing IMAP folders: {e}")
            return []

    def _list_folders_sync(self) -> list[str]:
        """Synchronous folder listing."""
        folders = []
        try:
            with self._get_mailbox().login(self._username, self._password) as mailbox:
                for folder in mailbox.folder.list():
                    folders.append(folder.name)
        except Exception as e:
            logger.error(f"Error listing folders: {e}")
        return sorted(folders)

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
        """Search emails with filters."""
        if not self._connected:
            if not await self.connect():
                return []

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: self._search_emails_sync(
                    query,
                    from_addr,
                    subject,
                    after,
                    before,
                    unread_only,
                    include_body,
                    limit,
                    folder,
                ),
            )
        except Exception as e:
            logger.error(f"Error searching IMAP messages: {e}")
            return []

    def _search_emails_sync(
        self,
        query: str | None,
        from_addr: str | None,
        subject: str | None,
        after: datetime | None,
        before: datetime | None,
        unread_only: bool,
        include_body: bool,
        limit: int,
        folder: str,
    ) -> list[EmailMessage]:
        """Synchronous email search."""
        messages = []

        # Log all search params
        logger.info(
            f"IMAP: Search params - from_addr={from_addr!r}, subject={subject!r}, "
            f"query={query!r}, unread_only={unread_only}, limit={limit}, folder={folder}"
        )

        try:
            with self._get_mailbox().login(self._username, self._password) as mailbox:
                logger.info(f"IMAP: Selecting folder '{folder}'")
                mailbox.folder.set(folder)

                # Get folder stats
                status = mailbox.folder.status(folder)
                logger.info(f"IMAP: Folder '{folder}' has {status.get('MESSAGES', '?')} messages")

                # Build search criteria as kwargs for AND()
                criteria_kwargs = {}

                if unread_only:
                    criteria_kwargs["seen"] = False
                if from_addr:
                    criteria_kwargs["from_"] = from_addr
                if subject:
                    criteria_kwargs["subject"] = subject
                if after:
                    criteria_kwargs["date_gte"] = after.date()
                if before:
                    criteria_kwargs["date_lt"] = before.date()
                if query:
                    criteria_kwargs["text"] = query

                # Build criteria - use ALL if no filters
                logger.info(f"IMAP: criteria_kwargs = {criteria_kwargs}")
                if criteria_kwargs:
                    criteria = AND(**criteria_kwargs)
                else:
                    criteria = AND(all=True)

                logger.info(f"IMAP: Final search criteria: {criteria}")

                # Fetch messages
                count = 0
                for msg in mailbox.fetch(criteria, limit=limit, reverse=True, mark_seen=False):
                    messages.append(self._convert_message(msg, include_body=include_body))
                    count += 1

                logger.info(f"IMAP: Successfully fetched {count} messages")

        except MailboxLoginError as e:
            logger.error(f"IMAP login failed: {e}")
        except Exception as e:
            logger.error(f"IMAP search error: {e}", exc_info=True)

        return messages

    async def get_email_by_id(
        self,
        uid: str,
        include_body: bool = True,
        folder: str = "INBOX",
    ) -> EmailMessage | None:
        """Get a specific email by its UID."""
        if not self._connected:
            if not await self.connect():
                return None

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: self._get_email_by_id_sync(uid, include_body, folder),
            )
        except Exception as e:
            logger.error(f"Error getting email by ID: {e}")
            return None

    def _get_email_by_id_sync(self, uid: str, include_body: bool, folder: str) -> EmailMessage | None:
        """Synchronous get email by UID."""
        try:
            with self._get_mailbox().login(self._username, self._password) as mailbox:
                mailbox.folder.set(folder)

                # Fetch by UID
                for msg in mailbox.fetch(AND(uid=uid), mark_seen=False):
                    return self._convert_message(msg, include_body=include_body)

                logger.warning(f"IMAP: Message with UID {uid} not found in {folder}")
                return None

        except Exception as e:
            logger.error(f"Error fetching email {uid}: {e}")
            return None

    def _convert_message(self, msg, include_body: bool = False) -> EmailMessage:
        """Convert imap-tools message to EmailMessage."""
        # Get received date, falling back to now if not available
        received_at = msg.date
        if received_at:
            # Remove timezone info for consistency
            if received_at.tzinfo:
                received_at = received_at.replace(tzinfo=None)
        else:
            received_at = datetime.now(timezone.utc).replace(tzinfo=None)

        # Build snippet from text body
        snippet = ""
        if msg.text:
            snippet = " ".join(msg.text.split())[:200]

        # Check for attachments
        has_attachments = len(msg.attachments) > 0

        # Get full body if requested
        full_body = None
        body_html = None
        if include_body:
            full_body = msg.text
            body_html = msg.html

        return EmailMessage(
            uid=msg.uid,
            from_addr=msg.from_,
            subject=msg.subject or "(No Subject)",
            snippet=snippet,
            received_at=received_at,
            has_attachments=has_attachments,
            is_read="\\Seen" in msg.flags,
            full_body=full_body,
            body_html=body_html,
        )
