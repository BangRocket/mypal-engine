"""Google Calendar tools.

Provides integration with Google Calendar API via per-user OAuth.
Users must connect their Google account before using these tools.

Requires env vars: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from ._base import ToolContext, ToolDef
from .google_oauth import get_valid_token, is_configured

MODULE_NAME = "google_calendar"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """
## Google Calendar Integration
You can access and manage Google Calendar events for connected users.

**Calendar Tools:**
- `google_calendar_list_events` - List upcoming events (with optional filters)
- `google_calendar_get_event` - Get details of a specific event
- `google_calendar_create_event` - Create a new calendar event
- `google_calendar_update_event` - Modify an existing event
- `google_calendar_delete_event` - Delete an event
- `google_calendar_list_calendars` - List available calendars

**Date/Time Format:** Use ISO 8601 format (e.g., "2024-01-15T10:00:00-05:00")

Users must first connect their Google account using `google_connect`.
""".strip()

# API base URL
CALENDAR_API_URL = "https://www.googleapis.com/calendar/v3"


async def _calendar_request(
    user_id: str,
    method: str,
    endpoint: str,
    params: dict | None = None,
    json_data: dict | None = None,
) -> dict | list | str:
    """Make an authenticated Google Calendar API request.

    Args:
        user_id: User making the request
        method: HTTP method
        endpoint: API endpoint (appended to CALENDAR_API_URL)
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

    url = f"{CALENDAR_API_URL}/{endpoint}"

    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=30.0,
        )

        if response.status_code == 204:
            return {"success": True}

        if response.status_code >= 400:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get("error", {}).get("message", response.text)
            raise ValueError(
                f"Calendar API error ({response.status_code}): {error_msg}"
            )

        return response.json()


# =============================================================================
# Tool Handlers
# =============================================================================


async def calendar_list_events(
    ctx: ToolContext,
    calendar_id: str = "primary",
    time_min: str | None = None,
    time_max: str | None = None,
    max_results: int = 20,
    query: str | None = None,
) -> str:
    """List upcoming calendar events.

    Args:
        ctx: Tool context with user_id
        calendar_id: Calendar ID (default: "primary")
        time_min: Start of time range (ISO 8601), defaults to now
        time_max: End of time range (ISO 8601)
        max_results: Maximum number of events (default: 20)
        query: Free-text search query

    Returns:
        JSON string with events list
    """
    if not is_configured():
        return "Error: Google OAuth not configured."

    # Default time_min to now if not specified
    if not time_min:
        time_min = datetime.now(timezone.utc).isoformat()

    params = {
        "maxResults": min(max_results, 100),
        "orderBy": "startTime",
        "singleEvents": True,  # Expand recurring events
        "timeMin": time_min,
    }

    if time_max:
        params["timeMax"] = time_max

    if query:
        params["q"] = query

    try:
        result = await _calendar_request(
            ctx.user_id, "GET", f"calendars/{calendar_id}/events", params=params
        )

        events = result.get("items", [])
        if not events:
            return "No upcoming events found."

        # Format events for readability
        formatted = []
        for event in events:
            start = event.get("start", {})
            end = event.get("end", {})
            start_time = start.get("dateTime", start.get("date", "Unknown"))
            end_time = end.get("dateTime", end.get("date", ""))

            event_info = {
                "id": event.get("id"),
                "summary": event.get("summary", "(No title)"),
                "start": start_time,
                "end": end_time,
                "location": event.get("location"),
                "description": event.get("description", "")[:200]
                if event.get("description")
                else None,
                "attendees": [a.get("email") for a in event.get("attendees", [])],
            }
            # Remove None values
            event_info = {k: v for k, v in event_info.items() if v is not None}
            formatted.append(event_info)

        return json.dumps({"events": formatted, "count": len(formatted)}, indent=2)

    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error listing events: {e}"


async def calendar_get_event(
    ctx: ToolContext,
    event_id: str,
    calendar_id: str = "primary",
) -> str:
    """Get details of a specific event.

    Args:
        ctx: Tool context with user_id
        event_id: Event ID
        calendar_id: Calendar ID (default: "primary")

    Returns:
        JSON string with event details
    """
    if not is_configured():
        return "Error: Google OAuth not configured."

    try:
        event = await _calendar_request(
            ctx.user_id, "GET", f"calendars/{calendar_id}/events/{event_id}"
        )

        return json.dumps(event, indent=2)

    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error getting event: {e}"


async def calendar_create_event(
    ctx: ToolContext,
    summary: str,
    start: str,
    end: str,
    calendar_id: str = "primary",
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    reminders: dict | None = None,
) -> str:
    """Create a new calendar event.

    Args:
        ctx: Tool context with user_id
        summary: Event title (required)
        start: Start time in ISO 8601 format (required)
        end: End time in ISO 8601 format (required)
        calendar_id: Calendar ID (default: "primary")
        description: Event description
        location: Event location
        attendees: List of attendee email addresses
        reminders: Custom reminders config

    Returns:
        JSON string with created event details
    """
    if not is_configured():
        return "Error: Google OAuth not configured."

    # Build event body
    event_body: dict[str, Any] = {
        "summary": summary,
    }

    # Handle start/end times (detect all-day vs timed events)
    if "T" in start:
        event_body["start"] = {"dateTime": start}
        event_body["end"] = {"dateTime": end}
    else:
        # All-day event (date only)
        event_body["start"] = {"date": start}
        event_body["end"] = {"date": end}

    if description:
        event_body["description"] = description

    if location:
        event_body["location"] = location

    if attendees:
        event_body["attendees"] = [{"email": email} for email in attendees]

    if reminders:
        event_body["reminders"] = reminders

    try:
        result = await _calendar_request(
            ctx.user_id, "POST", f"calendars/{calendar_id}/events", json_data=event_body
        )

        return json.dumps(
            {
                "success": True,
                "event_id": result.get("id"),
                "summary": result.get("summary"),
                "htmlLink": result.get("htmlLink"),
                "start": result.get("start"),
                "end": result.get("end"),
            },
            indent=2,
        )

    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error creating event: {e}"


async def calendar_update_event(
    ctx: ToolContext,
    event_id: str,
    calendar_id: str = "primary",
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
) -> str:
    """Update an existing calendar event.

    Args:
        ctx: Tool context with user_id
        event_id: Event ID (required)
        calendar_id: Calendar ID (default: "primary")
        summary: New event title
        start: New start time in ISO 8601 format
        end: New end time in ISO 8601 format
        description: New event description
        location: New event location
        attendees: New list of attendee email addresses

    Returns:
        JSON string with updated event details
    """
    if not is_configured():
        return "Error: Google OAuth not configured."

    # First get the existing event
    try:
        existing = await _calendar_request(
            ctx.user_id, "GET", f"calendars/{calendar_id}/events/{event_id}"
        )
    except Exception as e:
        return f"Error fetching event to update: {e}"

    # Apply updates
    if summary is not None:
        existing["summary"] = summary

    if start is not None:
        if "T" in start:
            existing["start"] = {"dateTime": start}
        else:
            existing["start"] = {"date": start}

    if end is not None:
        if "T" in end:
            existing["end"] = {"dateTime": end}
        else:
            existing["end"] = {"date": end}

    if description is not None:
        existing["description"] = description

    if location is not None:
        existing["location"] = location

    if attendees is not None:
        existing["attendees"] = [{"email": email} for email in attendees]

    try:
        result = await _calendar_request(
            ctx.user_id,
            "PUT",
            f"calendars/{calendar_id}/events/{event_id}",
            json_data=existing,
        )

        return json.dumps(
            {
                "success": True,
                "event_id": result.get("id"),
                "summary": result.get("summary"),
                "htmlLink": result.get("htmlLink"),
            },
            indent=2,
        )

    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error updating event: {e}"


async def calendar_delete_event(
    ctx: ToolContext,
    event_id: str,
    calendar_id: str = "primary",
) -> str:
    """Delete a calendar event.

    Args:
        ctx: Tool context with user_id
        event_id: Event ID (required)
        calendar_id: Calendar ID (default: "primary")

    Returns:
        Success message
    """
    if not is_configured():
        return "Error: Google OAuth not configured."

    try:
        await _calendar_request(
            ctx.user_id, "DELETE", f"calendars/{calendar_id}/events/{event_id}"
        )

        return json.dumps({"success": True, "message": f"Event {event_id} deleted."})

    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error deleting event: {e}"


async def calendar_list_calendars(ctx: ToolContext) -> str:
    """List available calendars for the user.

    Args:
        ctx: Tool context with user_id

    Returns:
        JSON string with calendars list
    """
    if not is_configured():
        return "Error: Google OAuth not configured."

    try:
        result = await _calendar_request(ctx.user_id, "GET", "users/me/calendarList")

        calendars = result.get("items", [])
        formatted = []
        for cal in calendars:
            formatted.append(
                {
                    "id": cal.get("id"),
                    "summary": cal.get("summary"),
                    "primary": cal.get("primary", False),
                    "accessRole": cal.get("accessRole"),
                }
            )

        return json.dumps({"calendars": formatted, "count": len(formatted)}, indent=2)

    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error listing calendars: {e}"


# =============================================================================
# Tool Definitions
# =============================================================================


def _is_available() -> bool:
    """Check if Google Calendar tools are available."""
    return is_configured()


TOOLS = [
    ToolDef(
        name="google_calendar_list_events",
        description="List upcoming calendar events with optional filters.",
        parameters={
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default: 'primary')",
                },
                "time_min": {
                    "type": "string",
                    "description": "Start of time range in ISO 8601 format (default: now)",
                },
                "time_max": {
                    "type": "string",
                    "description": "End of time range in ISO 8601 format",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of events (default: 20, max: 100)",
                },
                "query": {
                    "type": "string",
                    "description": "Free-text search query",
                },
            },
            "required": [],
        },
        handler=calendar_list_events,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_calendar_get_event",
        description="Get details of a specific calendar event.",
        parameters={
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Event ID",
                },
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default: 'primary')",
                },
            },
            "required": ["event_id"],
        },
        handler=calendar_get_event,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_calendar_create_event",
        description="Create a new calendar event.",
        parameters={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Event title",
                },
                "start": {
                    "type": "string",
                    "description": "Start time in ISO 8601 format (e.g., '2024-01-15T10:00:00-05:00' or '2024-01-15' for all-day)",
                },
                "end": {
                    "type": "string",
                    "description": "End time in ISO 8601 format",
                },
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default: 'primary')",
                },
                "description": {
                    "type": "string",
                    "description": "Event description",
                },
                "location": {
                    "type": "string",
                    "description": "Event location",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of attendee email addresses",
                },
            },
            "required": ["summary", "start", "end"],
        },
        handler=calendar_create_event,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_calendar_update_event",
        description="Update an existing calendar event.",
        parameters={
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Event ID to update",
                },
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default: 'primary')",
                },
                "summary": {
                    "type": "string",
                    "description": "New event title",
                },
                "start": {
                    "type": "string",
                    "description": "New start time in ISO 8601 format",
                },
                "end": {
                    "type": "string",
                    "description": "New end time in ISO 8601 format",
                },
                "description": {
                    "type": "string",
                    "description": "New event description",
                },
                "location": {
                    "type": "string",
                    "description": "New event location",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New list of attendee email addresses",
                },
            },
            "required": ["event_id"],
        },
        handler=calendar_update_event,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_calendar_delete_event",
        description="Delete a calendar event.",
        parameters={
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Event ID to delete",
                },
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default: 'primary')",
                },
            },
            "required": ["event_id"],
        },
        handler=calendar_delete_event,
        requires=["google_oauth"],
    ),
    ToolDef(
        name="google_calendar_list_calendars",
        description="List all calendars available to the user.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=calendar_list_calendars,
        requires=["google_oauth"],
    ),
]
