"""File storage tool for Clara.

Provides managed storage for Clara to save and retrieve files.
Supports both local filesystem and S3-compatible storage (Wasabi, AWS, etc.).
Files can come from Discord attachments or sandbox environments.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools._base import ToolContext, ToolDef

MODULE_NAME = "files"
MODULE_VERSION = "1.0.0"

logger = logging.getLogger(__name__)

# Configuration
LOCAL_FILES_DIR = Path(os.getenv("CLARA_FILES_DIR", "./clara_files"))
MAX_FILE_SIZE = int(os.getenv("CLARA_MAX_FILE_SIZE", str(50 * 1024 * 1024)))  # 50MB default

# S3 Configuration
S3_ENABLED = os.getenv("S3_ENABLED", "false").lower() == "true"
S3_BUCKET = os.getenv("S3_BUCKET", "clara-files")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "https://s3.wasabisys.com")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_REGION = os.getenv("S3_REGION", "us-east-1")


SYSTEM_PROMPT = """
## File Storage

You can save, read, and manage files that persist across sessions.
Storage backend is configured via environment variables (local or S3).

**Tools:**
- `save_to_local` - Save content to persistent storage
- `list_local_files` - List saved files
- `read_local_file` - Read a saved file's content
- `delete_local_file` - Delete a saved file
- `send_local_file` - Send a saved file to Discord chat

**Sandbox Integration (when sandbox available):**
- `download_from_sandbox` - Copy sandbox file to persistent storage
- `upload_to_sandbox` - Upload persistent file to sandbox

**S3 Storage (when S3_ENABLED=true):**
- `s3_save` - Save content directly to S3
- `s3_list` - List files in S3 bucket
- `s3_read` - Read file from S3
- `s3_delete` - Delete file from S3

**Usage Notes:**
- Files are stored per-user with optional channel separation
- Binary files are supported
- Max file size is configurable (default 50MB)
""".strip()


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class FileInfo:
    """Information about a stored file."""

    name: str
    path: Path
    size: int
    created_at: datetime
    user_id: str  # Who saved/uploaded the file


@dataclass
class FileResult:
    """Result of a file operation."""

    success: bool
    message: str
    file_info: FileInfo | None = None


# =============================================================================
# Local File Manager
# =============================================================================


class LocalFileManager:
    """Manages local file storage for users."""

    def __init__(self, base_dir: Path = LOCAL_FILES_DIR):
        self.base_dir = base_dir
        self._ensure_base_dir()

    def _ensure_base_dir(self):
        """Ensure the base storage directory exists."""
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_id(self, id_str: str) -> str:
        """Sanitize an ID for filesystem use."""
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in id_str)

    def _storage_dir(self, user_id: str, channel_id: str | None = None) -> Path:
        """Get or create storage directory for user/channel."""
        safe_user = self._sanitize_id(user_id)
        if channel_id:
            safe_channel = self._sanitize_id(channel_id)
            storage_dir = self.base_dir / safe_user / safe_channel
        else:
            storage_dir = self.base_dir / safe_user
        storage_dir.mkdir(parents=True, exist_ok=True)
        return storage_dir

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for safe storage."""
        # Remove path separators and dangerous characters
        safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in filename)
        # Prevent hidden files and path traversal
        safe = safe.lstrip(".")
        return safe or "unnamed_file"

    def save_file(self, user_id: str, filename: str, content: str | bytes, channel_id: str | None = None) -> FileResult:
        """Save content to a local file."""
        try:
            safe_name = self._sanitize_filename(filename)
            storage_dir = self._storage_dir(user_id, channel_id)
            file_path = storage_dir / safe_name

            # Check size
            if isinstance(content, str):
                content_bytes = content.encode("utf-8")
            else:
                content_bytes = content

            if len(content_bytes) > MAX_FILE_SIZE:
                return FileResult(
                    success=False,
                    message=f"File too large ({len(content_bytes)} bytes, max {MAX_FILE_SIZE})",
                )

            # Write file
            if isinstance(content, str):
                file_path.write_text(content, encoding="utf-8")
            else:
                file_path.write_bytes(content)

            file_info = FileInfo(
                name=safe_name,
                path=file_path,
                size=len(content_bytes),
                created_at=datetime.now(UTC),
                user_id=user_id,
            )

            return FileResult(
                success=True,
                message=f"Saved to local storage: {safe_name}",
                file_info=file_info,
            )

        except Exception as e:
            return FileResult(success=False, message=f"Error saving file: {e}")

    def list_files(self, user_id: str, channel_id: str | None = None) -> list[FileInfo]:
        """List all files for a user/channel."""
        storage_dir = self._storage_dir(user_id, channel_id)
        files = []

        for file_path in storage_dir.iterdir():
            if file_path.is_file():
                stat = file_path.stat()
                files.append(
                    FileInfo(
                        name=file_path.name,
                        path=file_path,
                        size=stat.st_size,
                        created_at=datetime.fromtimestamp(stat.st_mtime, UTC),
                        user_id=user_id,
                    )
                )

        # Sort by modification time, newest first
        files.sort(key=lambda f: f.created_at, reverse=True)
        return files

    def read_file(self, user_id: str, filename: str, channel_id: str | None = None) -> FileResult:
        """Read a file's content."""
        try:
            safe_name = self._sanitize_filename(filename)
            storage_dir = self._storage_dir(user_id, channel_id)
            file_path = storage_dir / safe_name

            if not file_path.exists():
                return FileResult(success=False, message=f"File not found: {safe_name}")

            # Try to read as text, fall back to binary
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = f"[Binary file: {file_path.stat().st_size} bytes]"

            stat = file_path.stat()
            file_info = FileInfo(
                name=safe_name,
                path=file_path,
                size=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_mtime, UTC),
                user_id=user_id,
            )

            return FileResult(success=True, message=content, file_info=file_info)

        except Exception as e:
            return FileResult(success=False, message=f"Error reading file: {e}")

    def read_file_bytes(self, user_id: str, filename: str, channel_id: str | None = None) -> tuple[bytes | None, str]:
        """Read a file's binary content.

        Returns:
            tuple: (bytes content or None, error message if failed)
        """
        try:
            safe_name = self._sanitize_filename(filename)
            storage_dir = self._storage_dir(user_id, channel_id)
            file_path = storage_dir / safe_name

            if not file_path.exists():
                return None, f"File not found: {safe_name}"

            return file_path.read_bytes(), ""

        except Exception as e:
            return None, f"Error reading file: {e}"

    def delete_file(self, user_id: str, filename: str, channel_id: str | None = None) -> FileResult:
        """Delete a file."""
        try:
            safe_name = self._sanitize_filename(filename)
            storage_dir = self._storage_dir(user_id, channel_id)
            file_path = storage_dir / safe_name

            if not file_path.exists():
                return FileResult(success=False, message=f"File not found: {safe_name}")

            file_path.unlink()
            return FileResult(success=True, message=f"Deleted: {safe_name}")

        except Exception as e:
            return FileResult(success=False, message=f"Error deleting file: {e}")

    def get_file_path(self, user_id: str, filename: str, channel_id: str | None = None) -> Path | None:
        """Get the full path to a file, or None if it doesn't exist."""
        safe_name = self._sanitize_filename(filename)
        storage_dir = self._storage_dir(user_id, channel_id)
        file_path = storage_dir / safe_name

        if file_path.exists():
            return file_path
        return None

    def save_from_bytes(self, user_id: str, filename: str, data: bytes, channel_id: str | None = None) -> FileResult:
        """Save binary data to a file."""
        return self.save_file(user_id, filename, data, channel_id)


# =============================================================================
# S3 File Manager
# =============================================================================


class S3FileManager:
    """Manages S3-compatible file storage for users (Wasabi, AWS, etc.)."""

    def __init__(
        self,
        bucket: str = S3_BUCKET,
        endpoint_url: str = S3_ENDPOINT_URL,
        access_key: str = S3_ACCESS_KEY,
        secret_key: str = S3_SECRET_KEY,
        region: str = S3_REGION,
    ):
        import boto3

        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self._temp_dir = Path(tempfile.gettempdir()) / "clara_s3_cache"
        self._temp_dir.mkdir(parents=True, exist_ok=True)

        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

        # Ensure bucket exists
        self._ensure_bucket()

    def _ensure_bucket(self):
        """Ensure the S3 bucket exists, create if not."""
        try:
            self.s3.head_bucket(Bucket=self.bucket)
            logger.info(f"[s3] Bucket verified: {self.bucket}")
        except Exception as e:
            logger.warning(f"[s3] Bucket check failed: {e}")
            try:
                self.s3.create_bucket(Bucket=self.bucket)
                logger.info(f"[s3] Created bucket: {self.bucket}")
            except Exception as e2:
                logger.error(f"[s3] Could not create bucket {self.bucket}: {e2}")

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for safe storage."""
        safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in filename)
        safe = safe.lstrip(".")
        return safe or "unnamed_file"

    def _sanitize_id(self, id_str: str) -> str:
        """Sanitize an ID for S3 key use."""
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in id_str)

    def _s3_key(self, user_id: str, filename: str, channel_id: str | None = None) -> str:
        """Generate S3 object key for a file."""
        safe_user = self._sanitize_id(user_id)
        safe_name = self._sanitize_filename(filename)
        if channel_id:
            safe_channel = self._sanitize_id(channel_id)
            return f"{safe_user}/{safe_channel}/{safe_name}"
        return f"{safe_user}/{safe_name}"

    def save_file(self, user_id: str, filename: str, content: str | bytes, channel_id: str | None = None) -> FileResult:
        """Save content to S3."""
        try:
            safe_name = self._sanitize_filename(filename)
            key = self._s3_key(user_id, filename, channel_id)
            logger.info(f"[s3] Saving file: {safe_name} -> s3://{self.bucket}/{key}")

            # Convert to bytes
            if isinstance(content, str):
                content_bytes = content.encode("utf-8")
            else:
                content_bytes = content

            if len(content_bytes) > MAX_FILE_SIZE:
                logger.warning(f"[s3] File too large: {len(content_bytes)} bytes")
                return FileResult(
                    success=False,
                    message=f"File too large ({len(content_bytes)} bytes, max {MAX_FILE_SIZE})",
                )

            # Upload to S3
            logger.debug(f"[s3] Uploading {len(content_bytes)} bytes to {self.endpoint_url}")
            response = self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content_bytes,
            )
            logger.info(f"[s3] Upload successful: {safe_name} (ETag: {response.get('ETag', 'N/A')})")

            file_info = FileInfo(
                name=safe_name,
                path=Path(key),  # S3 key as path
                size=len(content_bytes),
                created_at=datetime.now(UTC),
                user_id=user_id,
            )

            return FileResult(
                success=True,
                message=f"Saved to cloud storage: {safe_name}",
                file_info=file_info,
            )

        except Exception as e:
            logger.exception(f"[s3] Failed to save file {filename}: {e}")
            return FileResult(success=False, message=f"Error saving file: {e}")

    def list_files(self, user_id: str, channel_id: str | None = None) -> list[FileInfo]:
        """List all files for a user/channel in S3."""
        safe_user = self._sanitize_id(user_id)
        if channel_id:
            safe_channel = self._sanitize_id(channel_id)
            prefix = f"{safe_user}/{safe_channel}/"
        else:
            prefix = f"{safe_user}/"
        files = []

        try:
            logger.debug(f"[s3] Listing files with prefix: {prefix}")
            response = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)

            for obj in response.get("Contents", []):
                key = obj["Key"]
                filename = key.split("/")[-1]  # Get filename from key

                files.append(
                    FileInfo(
                        name=filename,
                        path=Path(key),
                        size=obj["Size"],
                        created_at=obj["LastModified"].replace(tzinfo=UTC),
                        user_id=user_id,
                    )
                )

            logger.debug(f"[s3] Found {len(files)} files for prefix {prefix}")
            # Sort by modification time, newest first
            files.sort(key=lambda f: f.created_at, reverse=True)

        except Exception as e:
            logger.exception(f"[s3] Error listing files: {e}")

        return files

    def read_file(self, user_id: str, filename: str, channel_id: str | None = None) -> FileResult:
        """Read a file's content from S3."""
        try:
            safe_name = self._sanitize_filename(filename)
            key = self._s3_key(user_id, filename, channel_id)

            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            content_bytes = response["Body"].read()

            # Try to decode as text
            try:
                content = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                content = f"[Binary file: {len(content_bytes)} bytes]"

            file_info = FileInfo(
                name=safe_name,
                path=Path(key),
                size=len(content_bytes),
                created_at=response["LastModified"].replace(tzinfo=UTC),
                user_id=user_id,
            )

            return FileResult(success=True, message=content, file_info=file_info)

        except self.s3.exceptions.NoSuchKey:
            return FileResult(success=False, message=f"File not found: {filename}")
        except Exception as e:
            return FileResult(success=False, message=f"Error reading file: {e}")

    def read_file_bytes(self, user_id: str, filename: str, channel_id: str | None = None) -> tuple[bytes | None, str]:
        """Read a file's binary content from S3."""
        try:
            key = self._s3_key(user_id, filename, channel_id)
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read(), ""
        except self.s3.exceptions.NoSuchKey:
            return None, f"File not found: {filename}"
        except Exception as e:
            return None, f"Error reading file: {e}"

    def delete_file(self, user_id: str, filename: str, channel_id: str | None = None) -> FileResult:
        """Delete a file from S3."""
        try:
            safe_name = self._sanitize_filename(filename)
            key = self._s3_key(user_id, filename, channel_id)

            # Check if exists first
            try:
                self.s3.head_object(Bucket=self.bucket, Key=key)
            except Exception:
                return FileResult(success=False, message=f"File not found: {safe_name}")

            self.s3.delete_object(Bucket=self.bucket, Key=key)
            return FileResult(success=True, message=f"Deleted: {safe_name}")

        except Exception as e:
            return FileResult(success=False, message=f"Error deleting file: {e}")

    def get_file_path(self, user_id: str, filename: str, channel_id: str | None = None) -> Path | None:
        """Download file to temp location and return path.

        For S3, we need to download the file to a temporary location
        so it can be sent via Discord or other file-based operations.
        """
        try:
            safe_name = self._sanitize_filename(filename)
            key = self._s3_key(user_id, filename, channel_id)
            logger.info(f"[s3] get_file_path: {filename} -> s3://{self.bucket}/{key}")

            # Download to temp location
            safe_user = self._sanitize_id(user_id)
            if channel_id:
                safe_channel = self._sanitize_id(channel_id)
                temp_dir = self._temp_dir / safe_user / safe_channel
            else:
                temp_dir = self._temp_dir / safe_user
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_path = temp_dir / safe_name

            logger.debug(f"[s3] Downloading to: {temp_path}")
            self.s3.download_file(self.bucket, key, str(temp_path))
            logger.info(f"[s3] Downloaded: {safe_name}")
            return temp_path

        except Exception as e:
            logger.error(f"[s3] Failed to download {filename}: {e}")
            return None

    def save_from_bytes(self, user_id: str, filename: str, data: bytes, channel_id: str | None = None) -> FileResult:
        """Save binary data to S3."""
        return self.save_file(user_id, filename, data, channel_id)


# =============================================================================
# File Manager Singleton
# =============================================================================


# Type alias for the file manager interface
FileManager = LocalFileManager | S3FileManager

# Global singleton
_file_manager: FileManager | None = None


def get_file_manager() -> FileManager:
    """Get the global file manager instance.

    Returns S3FileManager if S3_ENABLED=true, otherwise LocalFileManager.
    """
    global _file_manager
    if _file_manager is None:
        if S3_ENABLED and S3_ACCESS_KEY and S3_SECRET_KEY:
            logger.info(f"[storage] Using S3 storage: {S3_ENDPOINT_URL} / {S3_BUCKET}")
            _file_manager = S3FileManager()
        else:
            logger.info(f"[storage] Using local storage: {LOCAL_FILES_DIR}")
            _file_manager = LocalFileManager()
    return _file_manager


# =============================================================================
# Helper Functions
# =============================================================================


def format_file_list(files: list[FileInfo]) -> str:
    """Format a list of files for display."""
    if not files:
        return "No files saved."

    lines = ["**Saved Files:**"]
    for f in files:
        size_str = _format_size(f.size)
        age = _format_age(f.created_at)
        lines.append(f"- `{f.name}` ({size_str}, {age})")

    return "\n".join(lines)


def _format_size(size: int) -> str:
    """Format file size for display."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


def _format_age(dt: datetime) -> str:
    """Format file age for display."""
    now = datetime.now(UTC)
    delta = now - dt

    if delta.days > 0:
        return f"{delta.days}d ago"
    elif delta.seconds > 3600:
        return f"{delta.seconds // 3600}h ago"
    elif delta.seconds > 60:
        return f"{delta.seconds // 60}m ago"
    else:
        return "just now"


# =============================================================================
# OpenAI-format Tool Definitions (for backwards compatibility)
# =============================================================================


LOCAL_FILE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save_to_local",
            "description": (
                "Save content to persistent file storage that survives across sessions. "
                "Use this to save important results, generated content, or data that "
                "should be available later. Files are stored per-user. "
                "To share a saved file in Discord chat, use `send_local_file`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": (
                            "Name for the file (e.g., 'results.csv', 'notes.md'). "
                            "Will be saved in the user's personal storage."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to save to the file.",
                    },
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_local_files",
            "description": (
                "List files saved in local storage. " "Shows files you've saved or that were uploaded by the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_local_file",
            "description": (
                "Read content from a locally saved file. "
                "Use this to retrieve previously saved data or uploaded files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Name of the file to read.",
                    },
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_local_file",
            "description": "Delete a file from local storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Name of the file to delete.",
                    },
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_from_sandbox",
            "description": (
                "Download a file from the Docker sandbox to local storage. "
                "Use this to save sandbox results permanently or to share them in chat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sandbox_path": {
                        "type": "string",
                        "description": ("Path to the file in the sandbox " "(e.g., '/home/user/output.csv')"),
                    },
                    "local_filename": {
                        "type": "string",
                        "description": (
                            "Optional: name for the local file. " "If not provided, uses the original filename."
                        ),
                    },
                },
                "required": ["sandbox_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upload_to_sandbox",
            "description": (
                "Upload a file from local storage to the Docker sandbox. "
                "Use this to make locally saved files available for code execution, "
                "data analysis, or processing in the sandbox environment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "local_filename": {
                        "type": "string",
                        "description": "Name of the local file to upload.",
                    },
                    "sandbox_path": {
                        "type": "string",
                        "description": (
                            "Optional: destination path in the sandbox. " "Defaults to /home/user/<filename>"
                        ),
                    },
                },
                "required": ["local_filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_local_file",
            "description": (
                "Send a saved file as a Discord chat attachment. "
                "The file will be uploaded and visible to all users as a downloadable attachment. "
                "Use this when the user asks to share, send, or show a saved file. "
                "This is better than pasting large content directly into chat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Name of the file to send to chat.",
                    },
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_chat_history",
            "description": (
                "Search through the full chat history for messages matching a query. "
                "Use this to find past conversations, recall what was discussed, "
                "or find specific messages. Searches message content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for in message content",
                    },
                    "limit": {
                        "type": "integer",
                        "description": ("Maximum messages to search through (default: 200, max: 1000)"),
                    },
                    "from_user": {
                        "type": "string",
                        "description": ("Optional: only search messages from this username"),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chat_history",
            "description": (
                "Retrieve recent chat history beyond what's in the current context. "
                "Use this to get a summary of past conversations or see what was "
                "discussed earlier. Returns messages in chronological order."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": ("Number of messages to retrieve (default: 50, max: 200)"),
                    },
                    "before_hours": {
                        "type": "number",
                        "description": (
                            "Only get messages older than this many hours ago. "
                            "Useful for looking at 'yesterday' or 'last week'."
                        ),
                    },
                    "user_filter": {
                        "type": "string",
                        "description": ("Optional: only include messages from this username"),
                    },
                },
                "required": [],
            },
        },
    },
]


# =============================================================================
# Tool Handler Functions
# =============================================================================


async def save_to_local(args: dict[str, Any], ctx: ToolContext) -> str:
    """Save content to local storage."""
    filename = args.get("filename")
    content = args.get("content")

    if not filename:
        return "Error: filename is required"
    if content is None:
        return "Error: content is required"

    user_id = ctx.user_id if hasattr(ctx, "user_id") else "default"
    channel_id = ctx.channel_id if hasattr(ctx, "channel_id") else None

    try:
        manager = get_file_manager()
        result = manager.save_file(user_id, filename, content, channel_id)

        if result.success:
            return f"✅ Saved: `{result.file_info.name}` ({_format_size(result.file_info.size)})"
        return f"Error: {result.message}"
    except Exception as e:
        return f"Error saving file: {e}"


async def list_local_files(args: dict[str, Any], ctx: ToolContext) -> str:
    """List files in local storage."""
    user_id = ctx.user_id if hasattr(ctx, "user_id") else "default"
    channel_id = ctx.channel_id if hasattr(ctx, "channel_id") else None

    try:
        manager = get_file_manager()
        files = manager.list_files(user_id, channel_id)

        if not files:
            return "No files saved."

        return format_file_list(files)
    except Exception as e:
        return f"Error listing files: {e}"


async def read_local_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read a file from local storage."""
    filename = args.get("filename")

    if not filename:
        return "Error: filename is required"

    user_id = ctx.user_id if hasattr(ctx, "user_id") else "default"
    channel_id = ctx.channel_id if hasattr(ctx, "channel_id") else None

    try:
        manager = get_file_manager()
        result = manager.read_file(user_id, filename, channel_id)

        if result.success:
            # Truncate very large content
            content = result.message
            if len(content) > 50000:
                content = content[:50000] + "\n\n... (truncated, file too large)"
            return content
        return f"Error: {result.message}"
    except Exception as e:
        return f"Error reading file: {e}"


async def delete_local_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Delete a file from local storage."""
    filename = args.get("filename")

    if not filename:
        return "Error: filename is required"

    user_id = ctx.user_id if hasattr(ctx, "user_id") else "default"
    channel_id = ctx.channel_id if hasattr(ctx, "channel_id") else None

    try:
        manager = get_file_manager()
        result = manager.delete_file(user_id, filename, channel_id)

        if result.success:
            return f"🗑️ Deleted: `{filename}`"
        return f"Error: {result.message}"
    except Exception as e:
        return f"Error deleting file: {e}"


async def send_local_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Send a saved file to Discord chat.

    Note: This tool prepares the file for sending. The actual Discord
    attachment is handled by the message handler based on the return value.
    """
    filename = args.get("filename")

    if not filename:
        return "Error: filename is required"

    user_id = ctx.user_id if hasattr(ctx, "user_id") else "default"
    channel_id = ctx.channel_id if hasattr(ctx, "channel_id") else None

    try:
        manager = get_file_manager()
        file_path = manager.get_file_path(user_id, filename, channel_id)

        if file_path and file_path.exists():
            # Return special marker that the message handler can use
            return f"__SEND_FILE__:{file_path}"
        return f"Error: File not found: {filename}"
    except Exception as e:
        return f"Error preparing file: {e}"


# --- Sandbox Integration Tools ---


async def download_from_sandbox(args: dict[str, Any], ctx: ToolContext) -> str:
    """Download a file from the sandbox to local storage."""
    sandbox_path = args.get("sandbox_path")
    local_filename = args.get("local_filename")

    if not sandbox_path:
        return "Error: sandbox_path is required"

    user_id = ctx.user_id if hasattr(ctx, "user_id") else "default"
    channel_id = ctx.channel_id if hasattr(ctx, "channel_id") else None

    try:
        # Try to get sandbox manager
        from sandbox.manager import get_sandbox_manager

        sandbox = await get_sandbox_manager()

        if sandbox is None:
            return "Error: No sandbox available. Enable Docker or remote sandbox."

        # Download from sandbox
        content = await sandbox.download_file(sandbox_path, user_id)
        if content is None:
            return f"Error: Could not download file from sandbox: {sandbox_path}"

        # Determine local filename
        if not local_filename:
            local_filename = sandbox_path.split("/")[-1]

        # Save to local storage
        manager = get_file_manager()
        result = manager.save_file(user_id, local_filename, content, channel_id)

        if result.success:
            return f"✅ Downloaded from sandbox: `{local_filename}` ({_format_size(result.file_info.size)})"
        return f"Error: {result.message}"

    except ImportError:
        return "Error: Sandbox module not available"
    except Exception as e:
        return f"Error downloading from sandbox: {e}"


async def upload_to_sandbox(args: dict[str, Any], ctx: ToolContext) -> str:
    """Upload a file from local storage to the sandbox."""
    local_filename = args.get("local_filename")
    sandbox_path = args.get("sandbox_path")

    if not local_filename:
        return "Error: local_filename is required"

    user_id = ctx.user_id if hasattr(ctx, "user_id") else "default"
    channel_id = ctx.channel_id if hasattr(ctx, "channel_id") else None

    try:
        # Read local file
        manager = get_file_manager()
        content_bytes, error = manager.read_file_bytes(user_id, local_filename, channel_id)

        if error:
            return f"Error: {error}"

        # Determine sandbox path
        if not sandbox_path:
            sandbox_path = f"/home/user/{local_filename}"

        # Try to get sandbox manager
        from sandbox.manager import get_sandbox_manager

        sandbox = await get_sandbox_manager()

        if sandbox is None:
            return "Error: No sandbox available. Enable Docker or remote sandbox."

        # Upload to sandbox
        success = await sandbox.upload_file(sandbox_path, content_bytes, user_id)
        if success:
            return f"✅ Uploaded to sandbox: `{sandbox_path}`"
        return "Error: Could not upload file to sandbox"

    except ImportError:
        return "Error: Sandbox module not available"
    except Exception as e:
        return f"Error uploading to sandbox: {e}"


# --- S3 Storage Tools ---


def _get_s3_manager() -> S3FileManager | None:
    """Get an S3 file manager instance if available."""
    if S3_ENABLED and S3_ACCESS_KEY and S3_SECRET_KEY:
        return S3FileManager()
    return None


async def s3_save(args: dict[str, Any], ctx: ToolContext) -> str:
    """Save content directly to S3 storage."""
    filename = args.get("filename")
    content = args.get("content")

    if not filename:
        return "Error: filename is required"
    if content is None:
        return "Error: content is required"

    s3 = _get_s3_manager()
    if s3 is None:
        return "Error: S3 storage not configured. Set S3_ENABLED=true and provide credentials."

    user_id = ctx.user_id if hasattr(ctx, "user_id") else "default"
    channel_id = ctx.channel_id if hasattr(ctx, "channel_id") else None

    try:
        result = s3.save_file(user_id, filename, content, channel_id)

        if result.success:
            return f"☁️ Saved to S3: `{result.file_info.name}` ({_format_size(result.file_info.size)})"
        return f"Error: {result.message}"
    except Exception as e:
        return f"Error saving to S3: {e}"


async def s3_list(args: dict[str, Any], ctx: ToolContext) -> str:
    """List files in S3 storage."""
    s3 = _get_s3_manager()
    if s3 is None:
        return "Error: S3 storage not configured. Set S3_ENABLED=true and provide credentials."

    user_id = ctx.user_id if hasattr(ctx, "user_id") else "default"
    channel_id = ctx.channel_id if hasattr(ctx, "channel_id") else None

    try:
        files = s3.list_files(user_id, channel_id)

        if not files:
            return "No files in S3 storage."

        lines = ["**S3 Files:**"]
        for f in files:
            size_str = _format_size(f.size)
            lines.append(f"- `{f.name}` ({size_str})")

        return "\n".join(lines)
    except Exception as e:
        return f"Error listing S3 files: {e}"


async def s3_read(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read a file from S3 storage."""
    filename = args.get("filename")

    if not filename:
        return "Error: filename is required"

    s3 = _get_s3_manager()
    if s3 is None:
        return "Error: S3 storage not configured. Set S3_ENABLED=true and provide credentials."

    user_id = ctx.user_id if hasattr(ctx, "user_id") else "default"
    channel_id = ctx.channel_id if hasattr(ctx, "channel_id") else None

    try:
        result = s3.read_file(user_id, filename, channel_id)

        if result.success:
            content = result.message
            if len(content) > 50000:
                content = content[:50000] + "\n\n... (truncated, file too large)"
            return content
        return f"Error: {result.message}"
    except Exception as e:
        return f"Error reading from S3: {e}"


async def s3_delete(args: dict[str, Any], ctx: ToolContext) -> str:
    """Delete a file from S3 storage."""
    filename = args.get("filename")

    if not filename:
        return "Error: filename is required"

    s3 = _get_s3_manager()
    if s3 is None:
        return "Error: S3 storage not configured. Set S3_ENABLED=true and provide credentials."

    user_id = ctx.user_id if hasattr(ctx, "user_id") else "default"
    channel_id = ctx.channel_id if hasattr(ctx, "channel_id") else None

    try:
        result = s3.delete_file(user_id, filename, channel_id)

        if result.success:
            return f"☁️🗑️ Deleted from S3: `{filename}`"
        return f"Error: {result.message}"
    except Exception as e:
        return f"Error deleting from S3: {e}"


# =============================================================================
# ToolDef Definitions (for core_tools registration)
# =============================================================================


TOOLS = [
    # Local/default storage tools
    ToolDef(
        name="save_to_local",
        description=(
            "Save content to persistent file storage that survives across sessions. "
            "Use this to save important results, generated content, or data that "
            "should be available later. Files are stored per-user."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name for the file (e.g., 'results.csv', 'notes.md')",
                },
                "content": {
                    "type": "string",
                    "description": "The content to save to the file",
                },
            },
            "required": ["filename", "content"],
        },
        handler=save_to_local,
        emoji="💾",
        label="Save File",
        detail_keys=["filename"],
        risk_level="safe",
        intent="write",
    ),
    ToolDef(
        name="list_local_files",
        description=(
            "List files saved in persistent storage. " "Shows files you've saved or that were uploaded by the user."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=list_local_files,
        emoji="📁",
        label="List Files",
        detail_keys=[],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="read_local_file",
        description=(
            "Read content from a saved file. " "Use this to retrieve previously saved data or uploaded files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to read",
                },
            },
            "required": ["filename"],
        },
        handler=read_local_file,
        emoji="📖",
        label="Read File",
        detail_keys=["filename"],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="delete_local_file",
        description="Delete a file from persistent storage.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to delete",
                },
            },
            "required": ["filename"],
        },
        handler=delete_local_file,
        emoji="🗑️",
        label="Delete File",
        detail_keys=["filename"],
        risk_level="moderate",
        intent="write",
    ),
    ToolDef(
        name="send_local_file",
        description=(
            "Send a saved file to the Discord chat. " "Use this when the user asks to see or download a saved file."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to send to chat",
                },
            },
            "required": ["filename"],
        },
        handler=send_local_file,
        emoji="📤",
        label="Send File",
        detail_keys=["filename"],
        risk_level="safe",
        intent="read",
    ),
    # Sandbox integration tools
    ToolDef(
        name="download_from_sandbox",
        description=(
            "Download a file from the Docker/Incus sandbox to persistent storage. "
            "Use this to save sandbox results permanently or to share them in chat."
        ),
        parameters={
            "type": "object",
            "properties": {
                "sandbox_path": {
                    "type": "string",
                    "description": "Path to the file in the sandbox (e.g., '/home/user/output.csv')",
                },
                "local_filename": {
                    "type": "string",
                    "description": "Optional: name for the local file. Uses original filename if not provided.",
                },
            },
            "required": ["sandbox_path"],
        },
        handler=download_from_sandbox,
        emoji="⬇️",
        label="Download from Sandbox",
        detail_keys=["sandbox_path"],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="upload_to_sandbox",
        description=(
            "Upload a file from persistent storage to the Docker/Incus sandbox. "
            "Use this to make saved files available for code execution or data analysis."
        ),
        parameters={
            "type": "object",
            "properties": {
                "local_filename": {
                    "type": "string",
                    "description": "Name of the local file to upload",
                },
                "sandbox_path": {
                    "type": "string",
                    "description": "Optional: destination path in sandbox. Defaults to /home/user/<filename>",
                },
            },
            "required": ["local_filename"],
        },
        handler=upload_to_sandbox,
        emoji="⬆️",
        label="Upload to Sandbox",
        detail_keys=["local_filename"],
        risk_level="safe",
        intent="write",
    ),
    # S3 storage tools
    ToolDef(
        name="s3_save",
        description=(
            "Save content directly to S3-compatible cloud storage (Wasabi, AWS, etc.). "
            "Requires S3_ENABLED=true and proper credentials configured."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name for the file in S3",
                },
                "content": {
                    "type": "string",
                    "description": "The content to save",
                },
            },
            "required": ["filename", "content"],
        },
        handler=s3_save,
        emoji="☁️",
        label="S3 Save",
        detail_keys=["filename"],
        risk_level="safe",
        intent="write",
    ),
    ToolDef(
        name="s3_list",
        description=(
            "List files in S3-compatible cloud storage. " "Requires S3_ENABLED=true and proper credentials configured."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=s3_list,
        emoji="☁️",
        label="S3 List",
        detail_keys=[],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="s3_read",
        description=(
            "Read a file from S3-compatible cloud storage. "
            "Requires S3_ENABLED=true and proper credentials configured."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to read from S3",
                },
            },
            "required": ["filename"],
        },
        handler=s3_read,
        emoji="☁️",
        label="S3 Read",
        detail_keys=["filename"],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="s3_delete",
        description=(
            "Delete a file from S3-compatible cloud storage. "
            "Requires S3_ENABLED=true and proper credentials configured."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to delete from S3",
                },
            },
            "required": ["filename"],
        },
        handler=s3_delete,
        emoji="☁️",
        label="S3 Delete",
        detail_keys=["filename"],
        risk_level="moderate",
        intent="write",
    ),
]


# =============================================================================
# Lifecycle Hooks
# =============================================================================


async def initialize() -> None:
    """Initialize files tool module."""
    # Test that file manager is accessible
    try:
        manager = get_file_manager()
        storage_type = "S3" if S3_ENABLED else "local"
        logger.info(f"[files_tool] Initialized with {storage_type} storage backend")
    except Exception as e:
        logger.warning(f"[files_tool] Could not initialize file manager: {e}")


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
