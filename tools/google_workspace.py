"""Google Workspace tools (Sheets, Drive, Docs).

Provides integration with Google Workspace APIs via per-user OAuth.
Users must connect their Google account before using these tools.

Requires env vars: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI
Also needs: CLARA_API_URL for OAuth redirect (Discord button URL limit is 512 chars)
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from ._base import ToolContext, ToolDef
from .google_oauth import (
    get_valid_token,
    is_configured,
    is_user_connected,
    revoke_token,
)

# API service base URL for OAuth redirects (keeps Discord button URLs short)
CLARA_API_URL = os.getenv("CLARA_API_URL", "")

MODULE_NAME = "google_workspace"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """
## Google Workspace Integration
You can interact with Google Sheets, Drive, and Docs for connected users.

**Connection Tools:**
- `google_connect` - Generate OAuth URL to connect Google account
- `google_status` - Check if user's Google account is connected
- `google_disconnect` - Disconnect Google account

**Google Sheets:**
- `google_sheets_create` - Create a new spreadsheet
- `google_sheets_read` - Read data from a range (A1 notation)
- `google_sheets_write` - Write data to a range
- `google_sheets_append` - Append rows to a sheet
- `google_sheets_list` - List user's spreadsheets

**Google Drive:**
- `google_drive_list` - List files with optional query
- `google_drive_upload` - Upload text content as a file
- `google_drive_download` - Download file content
- `google_drive_create_folder` - Create a folder
- `google_drive_share` - Share a file with someone
- `google_drive_move` - Move a file to a different folder
- `google_drive_copy` - Copy a file
- `google_drive_rename` - Rename a file
- `google_drive_delete` - Delete a file
- `google_drive_get_file` - Get file metadata

**Google Docs:**
- `google_docs_create` - Create a new document
- `google_docs_read` - Read document content
- `google_docs_write` - Append text to a document

Users must first connect their Google account using `google_connect`.
""".strip()

# API base URLs
SHEETS_API_URL = "https://sheets.googleapis.com/v4"
DRIVE_API_URL = "https://www.googleapis.com/drive/v3"
DOCS_API_URL = "https://docs.googleapis.com/v1"


async def _google_request(
    user_id: str,
    method: str,
    url: str,
    params: dict | None = None,
    json_data: dict | None = None,
) -> dict | list | str:
    """Make an authenticated Google API request.

    Args:
        user_id: User making the request
        method: HTTP method
        url: Full API URL
        params: Query parameters
        json_data: JSON body

    Returns:
        API response data
    """
    token = await get_valid_token(user_id)
    if not token:
        raise ValueError(
            "Google account not connected. Use google_connect to connect first."
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=60.0,
        )

        if response.status_code == 204:
            return {"success": True}

        if response.status_code >= 400:
            error_msg = response.text
            try:
                error_data = response.json()
                error_msg = (
                    error_data.get("error", {}).get("message", response.text)
                    if isinstance(error_data.get("error"), dict)
                    else response.text
                )
            except Exception:
                pass
            raise ValueError(f"Google API error ({response.status_code}): {error_msg}")

        return response.json()


# =============================================================================
# Connection / Auth Tools
# =============================================================================


async def google_connect(args: dict[str, Any], ctx: ToolContext) -> str:
    """Generate OAuth URL for connecting Google account."""
    if not is_configured():
        return "Error: Google OAuth is not configured on this server."

    if not CLARA_API_URL:
        return "Error: CLARA_API_URL not configured. Cannot generate OAuth link."

    if is_user_connected(ctx.user_id):
        return "Already connected. Use google_disconnect first to reconnect."

    try:
        # Use short redirect URL (Discord buttons have 512 char URL limit)
        # The API service will redirect to the full Google OAuth URL
        url = f"{CLARA_API_URL.rstrip('/')}/oauth/google/start/{ctx.user_id}"

        # Return structured response for Discord button rendering
        return json.dumps(
            {
                "_discord_button": True,
                "url": url,
                "label": "Connect Google Account",
                "emoji": "🔗",
                "message": "Click the button below to connect your Google account:",
            }
        )
    except Exception as e:
        return f"Error generating authorization URL: {e}"


async def google_status(args: dict[str, Any], ctx: ToolContext) -> str:
    """Check if user's Google account is connected."""
    if not is_configured():
        return json.dumps(
            {
                "configured": False,
                "connected": False,
                "message": "Google OAuth not configured",
            }
        )

    # Include diagnostic info for debugging
    from db.connection import SessionLocal
    from db.models import GoogleOAuthToken

    debug_info = {
        "user_id_checked": ctx.user_id,
        "db_configured": SessionLocal is not None,
    }

    try:
        with SessionLocal() as session:
            token_record = (
                session.query(GoogleOAuthToken)
                .filter(GoogleOAuthToken.user_id == ctx.user_id)
                .first()
            )
            if token_record:
                debug_info["token_found"] = True
                debug_info["token_user_id"] = token_record.user_id
                debug_info["expires_at"] = (
                    token_record.expires_at.isoformat()
                    if token_record.expires_at
                    else None
                )
                debug_info["has_refresh_token"] = bool(token_record.refresh_token)
            else:
                debug_info["token_found"] = False
                # Check how many tokens exist total (for debugging)
                total_tokens = session.query(GoogleOAuthToken).count()
                debug_info["total_tokens_in_db"] = total_tokens
                if total_tokens > 0:
                    # Show what user_ids exist (redacted)
                    all_users = session.query(GoogleOAuthToken.user_id).all()
                    debug_info["existing_user_ids"] = [
                        u[0][:20] + "..." if len(u[0]) > 20 else u[0] for u in all_users
                    ]
    except Exception as e:
        debug_info["db_error"] = str(e)

    connected = is_user_connected(ctx.user_id)
    return json.dumps(
        {
            "configured": True,
            "connected": connected,
            "message": "Connected" if connected else "Not connected",
            "debug": debug_info,
        },
        indent=2,
    )


async def google_disconnect(args: dict[str, Any], ctx: ToolContext) -> str:
    """Disconnect user's Google account."""
    if not is_user_connected(ctx.user_id):
        return "Your Google account is not connected."

    try:
        await revoke_token(ctx.user_id)
        return "Google account disconnected successfully."
    except Exception as e:
        return f"Error disconnecting: {e}"


# =============================================================================
# Google Sheets Tools
# =============================================================================


async def sheets_create(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new Google Spreadsheet."""
    title = args.get("title", "Untitled Spreadsheet")

    try:
        data = await _google_request(
            ctx.user_id,
            "POST",
            f"{SHEETS_API_URL}/spreadsheets",
            json_data={"properties": {"title": title}},
        )
        return json.dumps(
            {
                "spreadsheet_id": data.get("spreadsheetId"),
                "title": data.get("properties", {}).get("title"),
                "url": data.get("spreadsheetUrl"),
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def sheets_read(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read data from a spreadsheet range."""
    spreadsheet_id = args.get("spreadsheet_id")
    range_notation = args.get("range", "Sheet1")

    if not spreadsheet_id:
        return "Error: spreadsheet_id is required"

    try:
        data = await _google_request(
            ctx.user_id,
            "GET",
            f"{SHEETS_API_URL}/spreadsheets/{spreadsheet_id}/values/{range_notation}",
        )
        values = data.get("values", [])
        return json.dumps({"range": data.get("range"), "values": values}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def sheets_write(args: dict[str, Any], ctx: ToolContext) -> str:
    """Write data to a spreadsheet range."""
    spreadsheet_id = args.get("spreadsheet_id")
    range_notation = args.get("range", "Sheet1!A1")
    values = args.get("values", [])

    if not spreadsheet_id:
        return "Error: spreadsheet_id is required"
    if not values:
        return "Error: values array is required"

    try:
        data = await _google_request(
            ctx.user_id,
            "PUT",
            f"{SHEETS_API_URL}/spreadsheets/{spreadsheet_id}/values/{range_notation}",
            params={"valueInputOption": "USER_ENTERED"},
            json_data={"values": values},
        )
        return json.dumps(
            {
                "updated_range": data.get("updatedRange"),
                "updated_rows": data.get("updatedRows"),
                "updated_columns": data.get("updatedColumns"),
                "updated_cells": data.get("updatedCells"),
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def sheets_append(args: dict[str, Any], ctx: ToolContext) -> str:
    """Append rows to a spreadsheet."""
    spreadsheet_id = args.get("spreadsheet_id")
    range_notation = args.get("range", "Sheet1")
    values = args.get("values", [])

    if not spreadsheet_id:
        return "Error: spreadsheet_id is required"
    if not values:
        return "Error: values array is required"

    try:
        data = await _google_request(
            ctx.user_id,
            "POST",
            f"{SHEETS_API_URL}/spreadsheets/{spreadsheet_id}/values/{range_notation}:append",
            params={
                "valueInputOption": "USER_ENTERED",
                "insertDataOption": "INSERT_ROWS",
            },
            json_data={"values": values},
        )
        updates = data.get("updates", {})
        return json.dumps(
            {
                "updated_range": updates.get("updatedRange"),
                "updated_rows": updates.get("updatedRows"),
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def sheets_list(args: dict[str, Any], ctx: ToolContext) -> str:
    """List user's spreadsheets."""
    max_results = min(args.get("max_results", 20), 100)

    try:
        data = await _google_request(
            ctx.user_id,
            "GET",
            f"{DRIVE_API_URL}/files",
            params={
                "q": "mimeType='application/vnd.google-apps.spreadsheet'",
                "pageSize": max_results,
                "fields": "files(id,name,modifiedTime,webViewLink)",
            },
        )
        files = data.get("files", [])
        spreadsheets = [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "modified": f.get("modifiedTime"),
                "url": f.get("webViewLink"),
            }
            for f in files
        ]
        return json.dumps(
            {"count": len(spreadsheets), "spreadsheets": spreadsheets}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Google Drive Tools
# =============================================================================


async def drive_list(args: dict[str, Any], ctx: ToolContext) -> str:
    """List files in Google Drive."""
    query = args.get("query", "")
    max_results = min(args.get("max_results", 20), 100)
    folder_id = args.get("folder_id")

    try:
        q_parts = []
        if query:
            q_parts.append(f"name contains '{query}'")
        if folder_id:
            q_parts.append(f"'{folder_id}' in parents")
        q_parts.append("trashed=false")

        data = await _google_request(
            ctx.user_id,
            "GET",
            f"{DRIVE_API_URL}/files",
            params={
                "q": " and ".join(q_parts) if q_parts else None,
                "pageSize": max_results,
                "fields": "files(id,name,mimeType,size,modifiedTime,webViewLink)",
            },
        )
        files = data.get("files", [])
        result = [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "type": f.get("mimeType"),
                "size": f.get("size"),
                "modified": f.get("modifiedTime"),
                "url": f.get("webViewLink"),
            }
            for f in files
        ]
        return json.dumps({"count": len(result), "files": result}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def drive_upload(args: dict[str, Any], ctx: ToolContext) -> str:
    """Upload text content as a file to Google Drive."""
    name = args.get("name", "untitled.txt")
    content = args.get("content", "")
    folder_id = args.get("folder_id")
    mime_type = args.get("mime_type", "text/plain")

    if not content:
        return "Error: content is required"

    try:
        # Create file metadata
        metadata: dict[str, Any] = {"name": name}
        if folder_id:
            metadata["parents"] = [folder_id]

        # Use multipart upload for simplicity with text content
        token = await get_valid_token(ctx.user_id)
        if not token:
            return "Error: Google account not connected"

        # Simple upload for small files
        async with httpx.AsyncClient() as client:
            # First create metadata
            response = await client.post(
                f"{DRIVE_API_URL}/files",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                params={"uploadType": "multipart"},
                json=metadata,
                timeout=60.0,
            )

            if response.status_code >= 400:
                return f"Error creating file: {response.text}"

            file_data = response.json()
            file_id = file_data.get("id")

            # Then upload content
            response = await client.patch(
                f"https://www.googleapis.com/upload/drive/v3/files/{file_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": mime_type,
                },
                params={"uploadType": "media"},
                content=content.encode(),
                timeout=60.0,
            )

            if response.status_code >= 400:
                return f"Error uploading content: {response.text}"

            return json.dumps(
                {"file_id": file_id, "name": name, "uploaded": True}, indent=2
            )
    except Exception as e:
        return f"Error: {e}"


async def drive_download(args: dict[str, Any], ctx: ToolContext) -> str:
    """Download file content from Google Drive."""
    file_id = args.get("file_id")

    if not file_id:
        return "Error: file_id is required"

    try:
        token = await get_valid_token(ctx.user_id)
        if not token:
            return "Error: Google account not connected"

        # Get file metadata first
        metadata = await _google_request(
            ctx.user_id,
            "GET",
            f"{DRIVE_API_URL}/files/{file_id}",
            params={"fields": "name,mimeType,size"},
        )

        mime_type = metadata.get("mimeType", "")

        # For Google Docs types, export as text
        async with httpx.AsyncClient() as client:
            if "google-apps" in mime_type:
                # Export Google Docs format
                export_mime = "text/plain"
                if "spreadsheet" in mime_type:
                    export_mime = "text/csv"
                elif "document" in mime_type:
                    export_mime = "text/plain"

                response = await client.get(
                    f"{DRIVE_API_URL}/files/{file_id}/export",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"mimeType": export_mime},
                    timeout=60.0,
                )
            else:
                # Download regular file
                response = await client.get(
                    f"{DRIVE_API_URL}/files/{file_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"alt": "media"},
                    timeout=60.0,
                )

            if response.status_code >= 400:
                return f"Error downloading: {response.text}"

            content = response.text
            # Truncate if too long
            if len(content) > 50000:
                content = content[:50000] + "\n... (truncated)"

            return json.dumps(
                {
                    "name": metadata.get("name"),
                    "mime_type": mime_type,
                    "content": content,
                },
                indent=2,
            )
    except Exception as e:
        return f"Error: {e}"


async def drive_create_folder(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a folder in Google Drive."""
    name = args.get("name", "New Folder")
    parent_id = args.get("parent_id")

    try:
        metadata: dict[str, Any] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            metadata["parents"] = [parent_id]

        data = await _google_request(
            ctx.user_id,
            "POST",
            f"{DRIVE_API_URL}/files",
            json_data=metadata,
        )
        return json.dumps(
            {"folder_id": data.get("id"), "name": data.get("name")}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


async def drive_share(args: dict[str, Any], ctx: ToolContext) -> str:
    """Share a file or folder with someone."""
    file_id = args.get("file_id")
    email = args.get("email")
    role = args.get("role", "reader")  # reader, writer, commenter

    if not file_id:
        return "Error: file_id is required"
    if not email:
        return "Error: email is required"

    try:
        data = await _google_request(
            ctx.user_id,
            "POST",
            f"{DRIVE_API_URL}/files/{file_id}/permissions",
            json_data={"type": "user", "role": role, "emailAddress": email},
        )
        return json.dumps(
            {"shared": True, "permission_id": data.get("id"), "role": role}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


async def drive_move(args: dict[str, Any], ctx: ToolContext) -> str:
    """Move a file to a different folder."""
    file_id = args.get("file_id")
    folder_id = args.get("folder_id")

    if not file_id:
        return "Error: file_id is required"
    if not folder_id:
        return "Error: folder_id (destination folder) is required"

    try:
        # First get current parents to remove them
        metadata = await _google_request(
            ctx.user_id,
            "GET",
            f"{DRIVE_API_URL}/files/{file_id}",
            params={"fields": "name,parents"},
        )
        current_parents = metadata.get("parents", [])

        # Move file: add new parent, remove old parents
        data = await _google_request(
            ctx.user_id,
            "PATCH",
            f"{DRIVE_API_URL}/files/{file_id}",
            params={
                "addParents": folder_id,
                "removeParents": ",".join(current_parents) if current_parents else None,
                "fields": "id,name,parents",
            },
        )
        return json.dumps(
            {
                "file_id": data.get("id"),
                "name": data.get("name"),
                "new_parent": folder_id,
                "moved": True,
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def drive_copy(args: dict[str, Any], ctx: ToolContext) -> str:
    """Copy a file."""
    file_id = args.get("file_id")
    new_name = args.get("name")
    folder_id = args.get("folder_id")

    if not file_id:
        return "Error: file_id is required"

    try:
        body: dict[str, Any] = {}
        if new_name:
            body["name"] = new_name
        if folder_id:
            body["parents"] = [folder_id]

        data = await _google_request(
            ctx.user_id,
            "POST",
            f"{DRIVE_API_URL}/files/{file_id}/copy",
            json_data=body if body else None,
        )
        return json.dumps(
            {
                "new_file_id": data.get("id"),
                "name": data.get("name"),
                "copied": True,
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def drive_rename(args: dict[str, Any], ctx: ToolContext) -> str:
    """Rename a file."""
    file_id = args.get("file_id")
    new_name = args.get("name")

    if not file_id:
        return "Error: file_id is required"
    if not new_name:
        return "Error: name (new name) is required"

    try:
        data = await _google_request(
            ctx.user_id,
            "PATCH",
            f"{DRIVE_API_URL}/files/{file_id}",
            json_data={"name": new_name},
        )
        return json.dumps(
            {
                "file_id": data.get("id"),
                "name": data.get("name"),
                "renamed": True,
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def drive_delete(args: dict[str, Any], ctx: ToolContext) -> str:
    """Delete a file (move to trash)."""
    file_id = args.get("file_id")
    permanent = args.get("permanent", False)

    if not file_id:
        return "Error: file_id is required"

    try:
        if permanent:
            # Permanently delete (no trash)
            await _google_request(
                ctx.user_id,
                "DELETE",
                f"{DRIVE_API_URL}/files/{file_id}",
            )
            return json.dumps({"file_id": file_id, "deleted": True, "permanent": True}, indent=2)
        else:
            # Move to trash
            data = await _google_request(
                ctx.user_id,
                "PATCH",
                f"{DRIVE_API_URL}/files/{file_id}",
                json_data={"trashed": True},
            )
            return json.dumps(
                {"file_id": data.get("id"), "deleted": True, "trashed": True},
                indent=2,
            )
    except Exception as e:
        return f"Error: {e}"


async def drive_get_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get file metadata."""
    file_id = args.get("file_id")

    if not file_id:
        return "Error: file_id is required"

    try:
        data = await _google_request(
            ctx.user_id,
            "GET",
            f"{DRIVE_API_URL}/files/{file_id}",
            params={
                "fields": "id,name,mimeType,size,createdTime,modifiedTime,parents,webViewLink,sharingUser,owners,shared"
            },
        )
        return json.dumps(
            {
                "id": data.get("id"),
                "name": data.get("name"),
                "mime_type": data.get("mimeType"),
                "size": data.get("size"),
                "created": data.get("createdTime"),
                "modified": data.get("modifiedTime"),
                "parents": data.get("parents"),
                "url": data.get("webViewLink"),
                "shared": data.get("shared"),
                "owners": [o.get("emailAddress") for o in data.get("owners", [])],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Google Docs Tools
# =============================================================================


async def docs_create(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new Google Doc."""
    title = args.get("title", "Untitled Document")

    try:
        data = await _google_request(
            ctx.user_id,
            "POST",
            f"{DOCS_API_URL}/documents",
            json_data={"title": title},
        )
        doc_id = data.get("documentId")
        return json.dumps(
            {
                "document_id": doc_id,
                "title": data.get("title"),
                "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def docs_read(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read content from a Google Doc."""
    document_id = args.get("document_id")

    if not document_id:
        return "Error: document_id is required"

    try:
        data = await _google_request(
            ctx.user_id,
            "GET",
            f"{DOCS_API_URL}/documents/{document_id}",
        )

        # Extract text content from document body
        content_parts = []
        body = data.get("body", {})
        for element in body.get("content", []):
            if "paragraph" in element:
                for elem in element["paragraph"].get("elements", []):
                    if "textRun" in elem:
                        content_parts.append(elem["textRun"].get("content", ""))

        content = "".join(content_parts)

        return json.dumps(
            {
                "document_id": document_id,
                "title": data.get("title"),
                "content": content,
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def docs_write(args: dict[str, Any], ctx: ToolContext) -> str:
    """Append text to a Google Doc."""
    document_id = args.get("document_id")
    text = args.get("text", "")

    if not document_id:
        return "Error: document_id is required"
    if not text:
        return "Error: text is required"

    try:
        # Get current document to find end index
        doc = await _google_request(
            ctx.user_id,
            "GET",
            f"{DOCS_API_URL}/documents/{document_id}",
        )

        # Find end of body content
        body = doc.get("body", {})
        end_index = body.get("content", [{}])[-1].get("endIndex", 1)

        # Insert text at end (before the newline at endIndex)
        insert_index = max(1, end_index - 1)

        await _google_request(
            ctx.user_id,
            "POST",
            f"{DOCS_API_URL}/documents/{document_id}:batchUpdate",
            json_data={
                "requests": [
                    {
                        "insertText": {
                            "location": {"index": insert_index},
                            "text": text,
                        }
                    }
                ]
            },
        )

        return json.dumps({"document_id": document_id, "text_appended": True}, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Tool Definitions
# =============================================================================


def _is_available() -> bool:
    """Check if Google Workspace tools are available."""
    return is_configured()


TOOLS = [
    # Connection tools
    ToolDef(
        name="google_connect",
        description="Generate OAuth URL to connect your Google account.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=google_connect,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_status",
        description="Check if your Google account is connected.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=google_status,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_disconnect",
        description="Disconnect your Google account and revoke access.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=google_disconnect,
        requires=["google_oauth"],
    ),
    # Sheets tools
    ToolDef(
        name="google_sheets_create",
        description="Create a new Google Spreadsheet.",
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title for the new spreadsheet",
                }
            },
            "required": [],
        },
        handler=sheets_create,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_sheets_read",
        description="Read data from a spreadsheet range (A1 notation).",
        parameters={
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "Spreadsheet ID from the URL",
                },
                "range": {
                    "type": "string",
                    "description": "Range in A1 notation (e.g., 'Sheet1!A1:D10')",
                },
            },
            "required": ["spreadsheet_id"],
        },
        handler=sheets_read,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_sheets_write",
        description="Write data to a spreadsheet range. Values should be a 2D array.",
        parameters={
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "Spreadsheet ID from the URL",
                },
                "range": {
                    "type": "string",
                    "description": "Range in A1 notation (e.g., 'Sheet1!A1')",
                },
                "values": {
                    "type": "array",
                    "items": {"type": "array"},
                    "description": "2D array of values to write",
                },
            },
            "required": ["spreadsheet_id", "values"],
        },
        handler=sheets_write,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_sheets_append",
        description="Append rows to a spreadsheet. Values should be a 2D array.",
        parameters={
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "Spreadsheet ID from the URL",
                },
                "range": {
                    "type": "string",
                    "description": "Sheet name or range (e.g., 'Sheet1')",
                },
                "values": {
                    "type": "array",
                    "items": {"type": "array"},
                    "description": "2D array of rows to append",
                },
            },
            "required": ["spreadsheet_id", "values"],
        },
        handler=sheets_append,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_sheets_list",
        description="List your Google Spreadsheets.",
        parameters={
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default 20)",
                }
            },
            "required": [],
        },
        handler=sheets_list,
        requires=["google_oauth"],
    ),
    # Drive tools
    ToolDef(
        name="google_drive_list",
        description="List files in your Google Drive.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for file names",
                },
                "folder_id": {
                    "type": "string",
                    "description": "Folder ID to list contents of",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default 20)",
                },
            },
            "required": [],
        },
        handler=drive_list,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_drive_upload",
        description="Upload text content as a file to Google Drive.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "File name (e.g., 'notes.txt')",
                },
                "content": {"type": "string", "description": "Text content to upload"},
                "folder_id": {
                    "type": "string",
                    "description": "Folder ID to upload to (optional)",
                },
                "mime_type": {
                    "type": "string",
                    "description": "MIME type (default: text/plain)",
                },
            },
            "required": ["name", "content"],
        },
        handler=drive_upload,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_drive_download",
        description="Download file content from Google Drive.",
        parameters={
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "File ID to download"}
            },
            "required": ["file_id"],
        },
        handler=drive_download,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_drive_create_folder",
        description="Create a folder in Google Drive.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Folder name"},
                "parent_id": {
                    "type": "string",
                    "description": "Parent folder ID (optional)",
                },
            },
            "required": ["name"],
        },
        handler=drive_create_folder,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_drive_share",
        description="Share a file or folder with someone.",
        parameters={
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "File or folder ID"},
                "email": {"type": "string", "description": "Email to share with"},
                "role": {
                    "type": "string",
                    "enum": ["reader", "writer", "commenter"],
                    "description": "Permission role (default: reader)",
                },
            },
            "required": ["file_id", "email"],
        },
        handler=drive_share,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_drive_move",
        description="Move a file to a different folder.",
        parameters={
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "File ID to move"},
                "folder_id": {"type": "string", "description": "Destination folder ID"},
            },
            "required": ["file_id", "folder_id"],
        },
        handler=drive_move,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_drive_copy",
        description="Copy a file. Optionally give it a new name or place in a folder.",
        parameters={
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "File ID to copy"},
                "name": {"type": "string", "description": "New name for the copy (optional)"},
                "folder_id": {"type": "string", "description": "Folder to place copy in (optional)"},
            },
            "required": ["file_id"],
        },
        handler=drive_copy,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_drive_rename",
        description="Rename a file or folder.",
        parameters={
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "File ID to rename"},
                "name": {"type": "string", "description": "New name"},
            },
            "required": ["file_id", "name"],
        },
        handler=drive_rename,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_drive_delete",
        description="Delete a file (moves to trash by default, use permanent=true to delete forever).",
        parameters={
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "File ID to delete"},
                "permanent": {
                    "type": "boolean",
                    "description": "Permanently delete instead of moving to trash (default: false)",
                },
            },
            "required": ["file_id"],
        },
        handler=drive_delete,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_drive_get_file",
        description="Get detailed metadata about a file (size, dates, sharing status, etc.).",
        parameters={
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "File ID to get info for"},
            },
            "required": ["file_id"],
        },
        handler=drive_get_file,
        requires=["google_oauth"],
    ),
    # Docs tools
    ToolDef(
        name="google_docs_create",
        description="Create a new Google Doc.",
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title for the new document",
                }
            },
            "required": [],
        },
        handler=docs_create,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_docs_read",
        description="Read content from a Google Doc.",
        parameters={
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "Document ID from the URL",
                }
            },
            "required": ["document_id"],
        },
        handler=docs_read,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_docs_write",
        description="Append text to a Google Doc.",
        parameters={
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "Document ID from the URL",
                },
                "text": {"type": "string", "description": "Text to append"},
            },
            "required": ["document_id", "text"],
        },
        handler=docs_write,
        requires=["google_oauth"],
    ),
]
