"""Gmail API provider using existing Google OAuth tokens."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from config.logging import get_logger
from email_service.providers.base import EmailMessage, EmailProvider

if TYPE_CHECKING:
    from db.models import EmailAccount

logger = get_logger("email.gmail")

# Gmail API base URL
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


class GmailProvider(EmailProvider):
    """Gmail provider using Google OAuth and Gmail API."""

    def __init__(self, account: EmailAccount):
        """Initialize Gmail provider.

        Args:
            account: EmailAccount with provider_type="gmail"
                    (uses GoogleOAuthToken via user_id)
        """
        super().__init__(account)
        self._access_token: str | None = None

    async def connect(self) -> bool:
        """Get valid access token from Google OAuth."""
        try:
            # Import here to avoid circular imports
            from tools.google_oauth import get_valid_token

            token = await get_valid_token(self.account.user_id)
            if token:
                self._access_token = token
                self._connected = True
                logger.debug(f"Gmail connected for user {self.account.user_id}")
                return True

            logger.warning(f"No valid Gmail token for user {self.account.user_id}")
            self._connected = False
            return False

        except Exception as e:
            logger.error(f"Gmail connection failed: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Clear access token (no persistent connection to close)."""
        self._access_token = None
        self._connected = False

    async def test_connection(self) -> tuple[bool, str | None]:
        """Test Gmail connection by fetching profile."""
        try:
            if not await self.connect():
                return False, "No valid Google OAuth token. Please reconnect Google."

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{GMAIL_API_BASE}/users/me/profile",
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    timeout=30.0,
                )

            if response.status_code == 200:
                return True, None
            elif response.status_code == 403:
                return (
                    False,
                    "Gmail access not authorized. Please reconnect Google.",
                )
            else:
                return False, f"Gmail API error: {response.status_code}"

        except Exception as e:
            return False, str(e)

    async def get_new_messages(
        self,
        since_uid: str | None = None,
        since_timestamp: datetime | None = None,
        limit: int = 50,
    ) -> list[EmailMessage]:
        """Fetch new messages from Gmail.

        Args:
            since_uid: Gmail message ID to fetch after (not used - uses historyId)
            since_timestamp: Fetch messages after this time
            limit: Maximum messages to return

        Returns:
            List of new email messages
        """
        if not self._connected:
            if not await self.connect():
                return []

        try:
            # Build query
            query_parts = ["in:inbox"]
            if since_timestamp:
                # Gmail uses Unix timestamp in seconds
                timestamp = int(since_timestamp.timestamp())
                query_parts.append(f"after:{timestamp}")

            query = " ".join(query_parts)

            # List messages
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{GMAIL_API_BASE}/users/me/messages",
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    params={
                        "q": query,
                        "maxResults": limit,
                    },
                    timeout=30.0,
                )

            if response.status_code != 200:
                logger.error(f"Gmail list messages failed: {response.status_code}")
                return []

            data = response.json()
            messages = data.get("messages", [])

            # Filter by since_uid if provided
            if since_uid:
                # Only return messages newer than since_uid
                new_messages = []
                for msg in messages:
                    if msg["id"] == since_uid:
                        break
                    new_messages.append(msg)
                messages = new_messages

            # Fetch full message details
            result = []
            for msg_ref in messages:
                try:
                    email_msg = await self._fetch_message(msg_ref["id"])
                    if email_msg:
                        result.append(email_msg)
                except Exception as e:
                    logger.debug(f"Error fetching message {msg_ref['id']}: {e}")

            return result

        except Exception as e:
            logger.error(f"Gmail get_new_messages failed: {e}")
            return []

    async def _fetch_message(self, message_id: str) -> EmailMessage | None:
        """Fetch full message details by ID."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{GMAIL_API_BASE}/users/me/messages/{message_id}",
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    params={"format": "full"},
                    timeout=30.0,
                )

            if response.status_code != 200:
                return None

            data = response.json()
            return self._parse_message(data)

        except Exception as e:
            logger.debug(f"Error fetching Gmail message {message_id}: {e}")
            return None

    def _parse_message(self, data: dict, include_body: bool = False) -> EmailMessage:
        """Parse Gmail API message response."""
        headers = {h["name"].lower(): h["value"] for h in data.get("payload", {}).get("headers", [])}

        from_addr = headers.get("from", "")
        subject = headers.get("subject", "(No Subject)")
        date_str = headers.get("date", "")

        # Parse date
        received_at = self._parse_date(date_str)

        # Get snippet (Gmail provides this)
        snippet = data.get("snippet", "")

        # Check for attachments
        has_attachments = self._has_attachments(data.get("payload", {}))

        # Check read status (UNREAD label means unread)
        labels = data.get("labelIds", [])
        is_read = "UNREAD" not in labels

        # Extract full body if requested
        full_body = None
        body_html = None
        if include_body:
            full_body, body_html = self._extract_body(data.get("payload", {}))

        return EmailMessage(
            uid=data["id"],
            from_addr=from_addr,
            subject=subject,
            snippet=snippet[:200] if snippet else "",
            received_at=received_at,
            has_attachments=has_attachments,
            is_read=is_read,
            full_body=full_body,
            body_html=body_html,
        )

    def _parse_date(self, date_str: str) -> datetime:
        """Parse email date string."""
        if not date_str:
            return datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            from email.utils import parsedate_to_datetime

            return parsedate_to_datetime(date_str).replace(tzinfo=None)
        except Exception:
            return datetime.now(timezone.utc).replace(tzinfo=None)

    def _has_attachments(self, payload: dict) -> bool:
        """Check if message has attachments."""
        # Check for attachment parts
        parts = payload.get("parts", [])
        for part in parts:
            if part.get("filename"):
                return True
            # Recursively check nested parts
            if self._has_attachments(part):
                return True
        return False

    async def get_unread_count(self) -> int:
        """Get count of unread messages in inbox."""
        if not self._connected:
            if not await self.connect():
                return 0

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{GMAIL_API_BASE}/users/me/labels/INBOX",
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    timeout=30.0,
                )

            if response.status_code == 200:
                data = response.json()
                return data.get("messagesUnread", 0)

        except Exception as e:
            logger.debug(f"Error getting unread count: {e}")

        return 0

    def _extract_body(self, payload: dict) -> tuple[str | None, str | None]:
        """Extract plain text and HTML body from payload.

        Returns:
            Tuple of (plain_text_body, html_body)
        """
        plain_body = None
        html_body = None

        def decode_body(data: str) -> str:
            """Decode base64url encoded body."""
            # Gmail uses URL-safe base64
            padded = data + "=" * (4 - len(data) % 4)
            return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")

        def extract_from_parts(parts: list) -> None:
            nonlocal plain_body, html_body
            for part in parts:
                mime_type = part.get("mimeType", "")
                body_data = part.get("body", {}).get("data")

                if body_data:
                    if mime_type == "text/plain" and not plain_body:
                        plain_body = decode_body(body_data)
                    elif mime_type == "text/html" and not html_body:
                        html_body = decode_body(body_data)

                # Recursively check nested parts
                if "parts" in part:
                    extract_from_parts(part["parts"])

        # Check if body is directly in payload
        body_data = payload.get("body", {}).get("data")
        if body_data:
            mime_type = payload.get("mimeType", "text/plain")
            decoded = decode_body(body_data)
            if "html" in mime_type:
                html_body = decoded
            else:
                plain_body = decoded

        # Check parts for multipart messages
        if "parts" in payload:
            extract_from_parts(payload["parts"])

        return plain_body, html_body

    async def list_folders(self) -> list[str]:
        """List Gmail labels (equivalent to folders)."""
        if not self._connected:
            if not await self.connect():
                return []

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{GMAIL_API_BASE}/users/me/labels",
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    timeout=30.0,
                )

            if response.status_code != 200:
                return ["INBOX"]

            data = response.json()
            labels = [label["name"] for label in data.get("labels", [])]
            return sorted(labels)

        except Exception as e:
            logger.debug(f"Error listing Gmail labels: {e}")
            return ["INBOX"]

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
        """Search emails with filters using Gmail query syntax."""
        if not self._connected:
            if not await self.connect():
                return []

        try:
            # Build Gmail query - use folder/label filter
            folder_filter = f"in:{folder.lower()}" if folder else "in:inbox"
            query_parts = [folder_filter]

            if query:
                query_parts.append(query)
            if from_addr:
                query_parts.append(f"from:{from_addr}")
            if subject:
                query_parts.append(f"subject:{subject}")
            if after:
                query_parts.append(f"after:{int(after.timestamp())}")
            if before:
                query_parts.append(f"before:{int(before.timestamp())}")
            if unread_only:
                query_parts.append("is:unread")

            gmail_query = " ".join(query_parts)
            logger.debug(f"Gmail search query: {gmail_query}")

            # Search messages
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{GMAIL_API_BASE}/users/me/messages",
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    params={
                        "q": gmail_query,
                        "maxResults": limit,
                    },
                    timeout=30.0,
                )

            if response.status_code != 200:
                logger.error(f"Gmail search failed: {response.status_code}")
                return []

            data = response.json()
            messages = data.get("messages", [])

            # Fetch full message details
            result = []
            for msg_ref in messages:
                try:
                    email_msg = await self._fetch_message_with_body(msg_ref["id"], include_body)
                    if email_msg:
                        result.append(email_msg)
                except Exception as e:
                    logger.debug(f"Error fetching message {msg_ref['id']}: {e}")

            return result

        except Exception as e:
            logger.error(f"Gmail search failed: {e}")
            return []

    async def _fetch_message_with_body(self, message_id: str, include_body: bool = False) -> EmailMessage | None:
        """Fetch message with optional full body."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{GMAIL_API_BASE}/users/me/messages/{message_id}",
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    params={"format": "full"},
                    timeout=30.0,
                )

            if response.status_code != 200:
                return None

            data = response.json()
            return self._parse_message(data, include_body=include_body)

        except Exception as e:
            logger.debug(f"Error fetching Gmail message {message_id}: {e}")
            return None

    async def get_email_by_id(
        self,
        uid: str,
        include_body: bool = True,
        folder: str = "INBOX",  # Not used for Gmail (IDs are global)
    ) -> EmailMessage | None:
        """Get a specific email by its Gmail message ID."""
        if not self._connected:
            if not await self.connect():
                return None

        return await self._fetch_message_with_body(uid, include_body)
