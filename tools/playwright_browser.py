"""Playwright browser automation tools.

Provides tools for web browsing, screenshots, page interaction, and persistent sessions.

**Stateless Tools:** browse_page, screenshot_page, extract_page_data
**Session Tools:** browser_create_session, browser_navigate, browser_click, browser_type,
                   browser_screenshot_session, browser_extract_session, browser_save_session,
                   browser_close_session, browser_list_sessions

Requires: playwright package and browser binaries installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._base import ToolContext, ToolDef

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

MODULE_NAME = "playwright_browser"
MODULE_VERSION = "2.0.0"

logger = logging.getLogger(__name__)

# Session storage configuration
BROWSER_SESSIONS_DIR = Path(os.getenv("BROWSER_SESSIONS_DIR", "./data/browser_sessions"))
SESSION_IDLE_TIMEOUT = int(os.getenv("BROWSER_SESSION_IDLE_MINUTES", "30")) * 60  # seconds

SYSTEM_PROMPT = """
## Browser Automation (Playwright)
You can browse web pages, take screenshots, extract content, and maintain persistent sessions.

### Stateless Tools (quick one-off requests):
- `browse_page` - Navigate to a URL and extract text content
- `screenshot_page` - Take a screenshot of a webpage (returns file path)
- `extract_page_data` - Extract structured data using CSS selectors

### Session Tools (persistent, maintains login state):
- `browser_create_session` - Create or restore a named session (e.g., "amazon", "gmail")
- `browser_navigate` - Navigate to a URL within a session
- `browser_click` - Click an element by CSS selector
- `browser_type` - Type text into an input field
- `browser_screenshot_session` - Screenshot the current session page
- `browser_extract_session` - Extract content/data from session page
- `browser_save_session` - Explicitly save cookies/state to disk
- `browser_close_session` - Close a session (optionally keep saved state)
- `browser_list_sessions` - List active sessions

### When to Use Sessions:
- Sites requiring authentication (Amazon, Gmail, etc.)
- Multi-step workflows (search → click → fill form → submit)
- Anything that needs cookies to persist between calls
- Bot detection workarounds (consistent browser fingerprint)

### Session Workflow Example:
1. `browser_create_session("amazon")` - Create/restore session
2. `browser_navigate("amazon", "https://amazon.com")` - Go to site
3. `browser_click("amazon", "#nav-link-accountList")` - Click sign-in
4. `browser_type("amazon", "#ap_email", "user@email.com")` - Enter email
5. ... (complete login, possibly manual 2FA) ...
6. `browser_save_session("amazon")` - Save logged-in state
7. (Later) `browser_create_session("amazon")` - Restored! Still logged in

**Note:** Session data is stored per-user. Sessions auto-close after 30 minutes of inactivity.
""".strip()


# =============================================================================
# Session Management
# =============================================================================


@dataclass
class BrowserSession:
    """A persistent browser session with state tracking."""

    name: str
    user_id: str
    context: "BrowserContext"
    page: "Page"
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    storage_path: Path | None = None

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.time()

    def is_stale(self) -> bool:
        """Check if session has been idle too long."""
        return (time.time() - self.last_activity) > SESSION_IDLE_TIMEOUT


# Global state
_browser = None
_playwright = None
_sessions: dict[str, BrowserSession] = {}  # key: "{user_id}:{session_name}"


def _session_key(user_id: str, session_name: str) -> str:
    """Generate a unique key for user+session combo."""
    return f"{user_id}:{session_name}"


def _get_storage_path(user_id: str, session_name: str) -> Path:
    """Get the storage path for a session's persistent data."""
    # Sanitize session name for filesystem
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_name)
    user_dir = BROWSER_SESSIONS_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / f"{safe_name}.json"


def _get_session(user_id: str, session_name: str) -> BrowserSession | None:
    """Get an existing session if it exists and isn't stale."""
    key = _session_key(user_id, session_name)
    session = _sessions.get(key)
    if session and session.is_stale():
        logger.info(f"[playwright] Session '{session_name}' is stale, will be cleaned up")
        return None
    return session


async def _cleanup_stale_sessions() -> None:
    """Clean up any sessions that have been idle too long."""
    stale_keys = [k for k, s in _sessions.items() if s.is_stale()]
    for key in stale_keys:
        session = _sessions.pop(key, None)
        if session:
            try:
                await session.context.close()
                logger.info(f"[playwright] Cleaned up stale session: {session.name}")
            except Exception as e:
                logger.warning(f"[playwright] Error closing stale session: {e}")


async def _get_browser():
    """Get or create a browser instance."""
    global _browser, _playwright

    if _browser is None:
        try:
            from playwright.async_api import async_playwright

            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            logger.info("[playwright] Browser launched")
        except Exception as e:
            logger.error(f"[playwright] Failed to launch browser: {e}")
            raise

    return _browser


async def _close_browser():
    """Close the browser instance."""
    global _browser, _playwright

    if _browser:
        await _browser.close()
        _browser = None

    if _playwright:
        await _playwright.stop()
        _playwright = None


# --- Tool Handlers ---


async def browse_page(args: dict[str, Any], ctx: ToolContext) -> str:
    """Navigate to a URL and extract text content."""
    url = args.get("url", "")
    if not url:
        return "Error: No URL provided"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    wait_for = args.get("wait_for", "load")
    timeout = min(args.get("timeout", 30), 60) * 1000  # Convert to ms

    try:
        browser = await _get_browser()
        page = await browser.new_page()

        try:
            logger.info(f"[playwright] Browsing: {url}")
            await page.goto(url, wait_until=wait_for, timeout=timeout)

            # Get page title
            title = await page.title()

            # Get main text content
            content = await page.evaluate("""() => {
                // Remove script and style elements
                const scripts = document.querySelectorAll('script, style, noscript');
                scripts.forEach(el => el.remove());

                // Get body text
                return document.body ? document.body.innerText : '';
            }""")

            # Truncate if too long
            max_len = args.get("max_length", 4000)
            if len(content) > max_len:
                content = content[:max_len] + "\n\n[Content truncated...]"

            result = f"**{title}**\n\n{content}"
            return result

        finally:
            await page.close()

    except Exception as e:
        logger.error(f"[playwright] Error browsing {url}: {e}")
        return f"Error browsing page: {e}"


async def screenshot_page(args: dict[str, Any], ctx: ToolContext) -> str:
    """Take a screenshot of a webpage."""
    url = args.get("url", "")
    if not url:
        return "Error: No URL provided"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    full_page = args.get("full_page", False)
    timeout = min(args.get("timeout", 30), 60) * 1000

    try:
        browser = await _get_browser()
        page = await browser.new_page(viewport={"width": 1280, "height": 720})

        try:
            logger.info(f"[playwright] Screenshot: {url}")
            await page.goto(url, wait_until="networkidle", timeout=timeout)

            # Generate filename from URL
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.replace(".", "_")
            filename = f"screenshot_{domain}.png"

            # Save to user's local storage
            from storage.local_files import get_file_manager

            screenshot_bytes = await page.screenshot(full_page=full_page)

            file_manager = get_file_manager()
            result = file_manager.save_file(
                ctx.user_id,
                filename,
                screenshot_bytes,
                ctx.channel_id,
            )

            if result.success:
                # Queue for sending if files_to_send is available
                files_to_send = ctx.extra.get("files_to_send")
                if files_to_send is not None and result.file_info:
                    files_to_send.append(result.file_info.path)
                    return f"Screenshot saved and attached: {filename}"
                return f"Screenshot saved: {filename}. Use send_local_file to share it."
            else:
                return f"Error saving screenshot: {result.message}"

        finally:
            await page.close()

    except Exception as e:
        logger.error(f"[playwright] Error taking screenshot of {url}: {e}")
        return f"Error taking screenshot: {e}"


async def extract_page_data(args: dict[str, Any], ctx: ToolContext) -> str:
    """Extract structured data from a page using CSS selectors."""
    url = args.get("url", "")
    if not url:
        return "Error: No URL provided"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    selectors = args.get("selectors", {})
    if not selectors:
        return "Error: No selectors provided"

    timeout = min(args.get("timeout", 30), 60) * 1000

    try:
        browser = await _get_browser()
        page = await browser.new_page()

        try:
            logger.info(f"[playwright] Extracting data from: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)

            results = {}
            for name, selector in selectors.items():
                try:
                    # Handle different selector types
                    if selector.endswith("[]"):
                        # Multiple elements
                        selector = selector[:-2]
                        elements = await page.query_selector_all(selector)
                        texts = []
                        for el in elements[:20]:  # Limit to 20 elements
                            text = await el.inner_text()
                            texts.append(text.strip())
                        results[name] = texts
                    else:
                        # Single element
                        element = await page.query_selector(selector)
                        if element:
                            text = await element.inner_text()
                            results[name] = text.strip()
                        else:
                            results[name] = None
                except Exception as e:
                    results[name] = f"Error: {e}"

            # Format results
            import json
            return f"Extracted data:\n```json\n{json.dumps(results, indent=2)}\n```"

        finally:
            await page.close()

    except Exception as e:
        logger.error(f"[playwright] Error extracting data from {url}: {e}")
        return f"Error extracting data: {e}"


# =============================================================================
# Session Tool Handlers
# =============================================================================


async def browser_create_session(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create or restore a named browser session."""
    session_name = args.get("session_name", "").strip()
    if not session_name:
        return "Error: session_name is required"

    # Validate session name
    if len(session_name) > 50:
        return "Error: session_name must be 50 characters or less"

    # Clean up stale sessions first
    await _cleanup_stale_sessions()

    # Check for existing session
    existing = _get_session(ctx.user_id, session_name)
    if existing:
        existing.touch()
        url = existing.page.url if existing.page else "about:blank"
        return f"Session '{session_name}' is already active. Current URL: {url}"

    try:
        browser = await _get_browser()
        storage_path = _get_storage_path(ctx.user_id, session_name)

        # Create context with persistent storage if available
        context_options = {
            "viewport": {"width": 1280, "height": 720},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        restored = False
        if storage_path.exists():
            try:
                context = await browser.new_context(
                    storage_state=str(storage_path),
                    **context_options,
                )
                restored = True
                logger.info(f"[playwright] Restored session '{session_name}' from storage")
            except Exception as e:
                logger.warning(f"[playwright] Failed to restore session state: {e}")
                context = await browser.new_context(**context_options)
        else:
            context = await browser.new_context(**context_options)

        # Create initial page
        page = await context.new_page()

        # Store session
        key = _session_key(ctx.user_id, session_name)
        _sessions[key] = BrowserSession(
            name=session_name,
            user_id=ctx.user_id,
            context=context,
            page=page,
            storage_path=storage_path,
        )

        status = "restored from saved state" if restored else "created fresh"
        return f"Session '{session_name}' {status}. Use browser_navigate to go to a URL."

    except Exception as e:
        logger.error(f"[playwright] Error creating session: {e}")
        return f"Error creating session: {e}"


async def browser_navigate(args: dict[str, Any], ctx: ToolContext) -> str:
    """Navigate to a URL within a session."""
    session_name = args.get("session_name", "").strip()
    url = args.get("url", "").strip()

    if not session_name:
        return "Error: session_name is required"
    if not url:
        return "Error: url is required"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    session = _get_session(ctx.user_id, session_name)
    if not session:
        return f"Error: Session '{session_name}' not found. Use browser_create_session first."

    wait_for = args.get("wait_for", "load")
    timeout = min(args.get("timeout", 30), 60) * 1000

    try:
        session.touch()
        logger.info(f"[playwright] Session '{session_name}' navigating to: {url}")
        await session.page.goto(url, wait_until=wait_for, timeout=timeout)

        title = await session.page.title()
        return f"Navigated to: {url}\nPage title: {title}"

    except Exception as e:
        logger.error(f"[playwright] Session navigate error: {e}")
        return f"Error navigating: {e}"


async def browser_click(args: dict[str, Any], ctx: ToolContext) -> str:
    """Click an element in a session by CSS selector."""
    session_name = args.get("session_name", "").strip()
    selector = args.get("selector", "").strip()

    if not session_name:
        return "Error: session_name is required"
    if not selector:
        return "Error: selector is required"

    session = _get_session(ctx.user_id, session_name)
    if not session:
        return f"Error: Session '{session_name}' not found. Use browser_create_session first."

    timeout = min(args.get("timeout", 10), 30) * 1000

    try:
        session.touch()
        logger.info(f"[playwright] Session '{session_name}' clicking: {selector}")

        # Wait for element to be visible and clickable
        await session.page.wait_for_selector(selector, state="visible", timeout=timeout)
        await session.page.click(selector, timeout=timeout)

        # Wait a moment for any navigation or dynamic updates
        await asyncio.sleep(0.5)

        # Get new page state
        title = await session.page.title()
        url = session.page.url

        return f"Clicked: {selector}\nCurrent page: {title}\nURL: {url}"

    except Exception as e:
        logger.error(f"[playwright] Session click error: {e}")
        return f"Error clicking '{selector}': {e}"


async def browser_type(args: dict[str, Any], ctx: ToolContext) -> str:
    """Type text into an input field in a session."""
    session_name = args.get("session_name", "").strip()
    selector = args.get("selector", "").strip()
    text = args.get("text", "")

    if not session_name:
        return "Error: session_name is required"
    if not selector:
        return "Error: selector is required"
    if text is None:
        return "Error: text is required"

    session = _get_session(ctx.user_id, session_name)
    if not session:
        return f"Error: Session '{session_name}' not found. Use browser_create_session first."

    clear_first = args.get("clear_first", True)
    press_enter = args.get("press_enter", False)
    timeout = min(args.get("timeout", 10), 30) * 1000

    try:
        session.touch()
        logger.info(f"[playwright] Session '{session_name}' typing into: {selector}")

        # Wait for element
        await session.page.wait_for_selector(selector, state="visible", timeout=timeout)

        if clear_first:
            await session.page.fill(selector, text)
        else:
            await session.page.type(selector, text)

        if press_enter:
            await session.page.press(selector, "Enter")
            await asyncio.sleep(0.5)

        return f"Typed into: {selector}" + (" (pressed Enter)" if press_enter else "")

    except Exception as e:
        logger.error(f"[playwright] Session type error: {e}")
        return f"Error typing into '{selector}': {e}"


async def browser_screenshot_session(args: dict[str, Any], ctx: ToolContext) -> str:
    """Take a screenshot of the current session page."""
    session_name = args.get("session_name", "").strip()

    if not session_name:
        return "Error: session_name is required"

    session = _get_session(ctx.user_id, session_name)
    if not session:
        return f"Error: Session '{session_name}' not found. Use browser_create_session first."

    full_page = args.get("full_page", False)

    try:
        session.touch()

        # Generate filename
        from urllib.parse import urlparse

        url = session.page.url
        parsed = urlparse(url)
        domain = parsed.netloc.replace(".", "_") if parsed.netloc else "session"
        filename = f"screenshot_{session_name}_{domain}.png"

        # Save to user's local storage
        from storage.local_files import get_file_manager

        screenshot_bytes = await session.page.screenshot(full_page=full_page)

        file_manager = get_file_manager()
        result = file_manager.save_file(
            ctx.user_id,
            filename,
            screenshot_bytes,
            ctx.channel_id,
        )

        if result.success:
            files_to_send = ctx.extra.get("files_to_send")
            if files_to_send is not None and result.file_info:
                files_to_send.append(result.file_info.path)
                return f"Screenshot saved and attached: {filename}"
            return f"Screenshot saved: {filename}. Use send_local_file to share it."
        else:
            return f"Error saving screenshot: {result.message}"

    except Exception as e:
        logger.error(f"[playwright] Session screenshot error: {e}")
        return f"Error taking screenshot: {e}"


async def browser_extract_session(args: dict[str, Any], ctx: ToolContext) -> str:
    """Extract content from the current session page."""
    session_name = args.get("session_name", "").strip()

    if not session_name:
        return "Error: session_name is required"

    session = _get_session(ctx.user_id, session_name)
    if not session:
        return f"Error: Session '{session_name}' not found. Use browser_create_session first."

    selectors = args.get("selectors")
    max_length = args.get("max_length", 4000)

    try:
        session.touch()

        if selectors:
            # Extract specific selectors
            results = {}
            for name, selector in selectors.items():
                try:
                    if selector.endswith("[]"):
                        selector = selector[:-2]
                        elements = await session.page.query_selector_all(selector)
                        texts = []
                        for el in elements[:20]:
                            text = await el.inner_text()
                            texts.append(text.strip())
                        results[name] = texts
                    else:
                        element = await session.page.query_selector(selector)
                        if element:
                            text = await element.inner_text()
                            results[name] = text.strip()
                        else:
                            results[name] = None
                except Exception as e:
                    results[name] = f"Error: {e}"

            return f"Extracted data:\n```json\n{json.dumps(results, indent=2)}\n```"

        else:
            # Extract full page text
            title = await session.page.title()
            content = await session.page.evaluate(
                """() => {
                const scripts = document.querySelectorAll('script, style, noscript');
                scripts.forEach(el => el.remove());
                return document.body ? document.body.innerText : '';
            }"""
            )

            if len(content) > max_length:
                content = content[:max_length] + "\n\n[Content truncated...]"

            return f"**{title}**\n\n{content}"

    except Exception as e:
        logger.error(f"[playwright] Session extract error: {e}")
        return f"Error extracting content: {e}"


async def browser_save_session(args: dict[str, Any], ctx: ToolContext) -> str:
    """Save session cookies and storage state to disk."""
    session_name = args.get("session_name", "").strip()

    if not session_name:
        return "Error: session_name is required"

    session = _get_session(ctx.user_id, session_name)
    if not session:
        return f"Error: Session '{session_name}' not found. Use browser_create_session first."

    try:
        session.touch()
        storage_path = _get_storage_path(ctx.user_id, session_name)

        await session.context.storage_state(path=str(storage_path))
        session.storage_path = storage_path

        logger.info(f"[playwright] Saved session '{session_name}' to {storage_path}")
        return f"Session '{session_name}' saved. Cookies and storage will persist for future use."

    except Exception as e:
        logger.error(f"[playwright] Session save error: {e}")
        return f"Error saving session: {e}"


async def browser_close_session(args: dict[str, Any], ctx: ToolContext) -> str:
    """Close a browser session."""
    session_name = args.get("session_name", "").strip()

    if not session_name:
        return "Error: session_name is required"

    key = _session_key(ctx.user_id, session_name)
    session = _sessions.pop(key, None)

    if not session:
        return f"Session '{session_name}' not found or already closed."

    keep_storage = args.get("keep_storage", True)

    try:
        # Optionally save before closing
        if keep_storage:
            try:
                storage_path = _get_storage_path(ctx.user_id, session_name)
                await session.context.storage_state(path=str(storage_path))
                logger.info(f"[playwright] Saved session state before closing: {session_name}")
            except Exception as e:
                logger.warning(f"[playwright] Failed to save before close: {e}")

        await session.context.close()
        logger.info(f"[playwright] Closed session: {session_name}")

        msg = f"Session '{session_name}' closed."
        if keep_storage:
            msg += " Cookies saved for next time."
        else:
            # Delete storage file if not keeping
            storage_path = _get_storage_path(ctx.user_id, session_name)
            if storage_path.exists():
                storage_path.unlink()
                msg += " Storage deleted."

        return msg

    except Exception as e:
        logger.error(f"[playwright] Session close error: {e}")
        return f"Error closing session: {e}"


async def browser_list_sessions(args: dict[str, Any], ctx: ToolContext) -> str:
    """List active browser sessions for the user."""
    # Clean up stale sessions first
    await _cleanup_stale_sessions()

    user_sessions = []
    for key, session in _sessions.items():
        if session.user_id == ctx.user_id:
            try:
                url = session.page.url
                idle_minutes = int((time.time() - session.last_activity) / 60)
                user_sessions.append(
                    {
                        "name": session.name,
                        "url": url,
                        "idle_minutes": idle_minutes,
                        "has_saved_state": session.storage_path and session.storage_path.exists(),
                    }
                )
            except Exception:
                user_sessions.append(
                    {
                        "name": session.name,
                        "url": "unknown",
                        "idle_minutes": 0,
                        "has_saved_state": False,
                    }
                )

    # Also list saved sessions that aren't currently active
    user_storage_dir = BROWSER_SESSIONS_DIR / ctx.user_id
    saved_sessions = []
    if user_storage_dir.exists():
        for storage_file in user_storage_dir.glob("*.json"):
            session_name = storage_file.stem
            if not any(s["name"] == session_name for s in user_sessions):
                saved_sessions.append(session_name)

    if not user_sessions and not saved_sessions:
        return "No active or saved browser sessions."

    result_parts = []

    if user_sessions:
        result_parts.append("**Active Sessions:**")
        for s in user_sessions:
            saved_indicator = " 💾" if s["has_saved_state"] else ""
            result_parts.append(
                f"• {s['name']}{saved_indicator} - {s['url'][:50]}{'...' if len(s['url']) > 50 else ''} "
                f"(idle {s['idle_minutes']}m)"
            )

    if saved_sessions:
        result_parts.append("\n**Saved Sessions (not active):**")
        for name in saved_sessions:
            result_parts.append(f"• {name} 💾 - use browser_create_session to restore")

    return "\n".join(result_parts)


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="browse_page",
        description=(
            "Navigate to a URL and extract its text content. "
            "Uses a real browser so JavaScript-rendered content works. "
            "Good for reading articles, checking websites, or getting page info."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to browse (https:// prefix optional)",
                },
                "wait_for": {
                    "type": "string",
                    "enum": ["load", "domcontentloaded", "networkidle"],
                    "description": "When to consider page loaded (default: load)",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Max characters of content to return (default: 4000)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30, max: 60)",
                },
            },
            "required": ["url"],
        },
        handler=browse_page,
        requires=["playwright"],
    ),
    ToolDef(
        name="screenshot_page",
        description=(
            "Take a screenshot of a webpage. "
            "Returns the screenshot as a file attachment. "
            "Useful for showing what a page looks like."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to screenshot",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture full scrollable page (default: false, viewport only)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30, max: 60)",
                },
            },
            "required": ["url"],
        },
        handler=screenshot_page,
        requires=["playwright", "files"],
    ),
    ToolDef(
        name="extract_page_data",
        description=(
            "Extract structured data from a page using CSS selectors. "
            "Useful for scraping specific elements like prices, titles, or lists. "
            "Add [] suffix to selector to get multiple elements."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to extract from",
                },
                "selectors": {
                    "type": "object",
                    "description": (
                        "Map of name -> CSS selector. Add [] suffix for multiple elements. "
                        "Example: {\"title\": \"h1\", \"links\": \"a.nav-link[]\"}"
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30)",
                },
            },
            "required": ["url", "selectors"],
        },
        handler=extract_page_data,
        requires=["playwright"],
    ),
    # --- Session Tools ---
    ToolDef(
        name="browser_create_session",
        description=(
            "Create or restore a named browser session. Sessions maintain cookies and "
            "login state across tool calls. Use for sites requiring authentication or "
            "multi-step workflows. If a saved session exists, it will be restored."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_name": {
                    "type": "string",
                    "description": "Name for this session (e.g., 'amazon', 'gmail'). Used to identify and restore sessions.",
                },
            },
            "required": ["session_name"],
        },
        handler=browser_create_session,
        requires=["playwright"],
    ),
    ToolDef(
        name="browser_navigate",
        description=(
            "Navigate to a URL within an existing browser session. "
            "The session maintains cookies, so authenticated pages work."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_name": {
                    "type": "string",
                    "description": "Name of the session to use",
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to",
                },
                "wait_for": {
                    "type": "string",
                    "enum": ["load", "domcontentloaded", "networkidle"],
                    "description": "When to consider page loaded (default: load)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30, max: 60)",
                },
            },
            "required": ["session_name", "url"],
        },
        handler=browser_navigate,
        requires=["playwright"],
    ),
    ToolDef(
        name="browser_click",
        description=(
            "Click an element in a browser session by CSS selector. "
            "Waits for the element to be visible before clicking."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_name": {
                    "type": "string",
                    "description": "Name of the session to use",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the element to click (e.g., '#submit-btn', '.nav-link')",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 10, max: 30)",
                },
            },
            "required": ["session_name", "selector"],
        },
        handler=browser_click,
        requires=["playwright"],
    ),
    ToolDef(
        name="browser_type",
        description=(
            "Type text into an input field in a browser session. "
            "Can clear existing content first and optionally press Enter."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_name": {
                    "type": "string",
                    "description": "Name of the session to use",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the input field",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type into the field",
                },
                "clear_first": {
                    "type": "boolean",
                    "description": "Clear existing content before typing (default: true)",
                },
                "press_enter": {
                    "type": "boolean",
                    "description": "Press Enter after typing (default: false)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 10, max: 30)",
                },
            },
            "required": ["session_name", "selector", "text"],
        },
        handler=browser_type,
        requires=["playwright"],
    ),
    ToolDef(
        name="browser_screenshot_session",
        description=(
            "Take a screenshot of the current page in a browser session. "
            "Returns the screenshot as a file."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_name": {
                    "type": "string",
                    "description": "Name of the session to screenshot",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture full scrollable page (default: false)",
                },
            },
            "required": ["session_name"],
        },
        handler=browser_screenshot_session,
        requires=["playwright", "files"],
    ),
    ToolDef(
        name="browser_extract_session",
        description=(
            "Extract content from the current page in a browser session. "
            "Can extract full page text or specific elements via selectors."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_name": {
                    "type": "string",
                    "description": "Name of the session to extract from",
                },
                "selectors": {
                    "type": "object",
                    "description": (
                        "Optional: Map of name -> CSS selector. If omitted, extracts full page text. "
                        "Add [] suffix for multiple elements."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "max_length": {
                    "type": "integer",
                    "description": "Max characters for full page text (default: 4000)",
                },
            },
            "required": ["session_name"],
        },
        handler=browser_extract_session,
        requires=["playwright"],
    ),
    ToolDef(
        name="browser_save_session",
        description=(
            "Save a session's cookies and storage state to disk. "
            "The session can be restored later with browser_create_session. "
            "Use after logging in to persist the authenticated state."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_name": {
                    "type": "string",
                    "description": "Name of the session to save",
                },
            },
            "required": ["session_name"],
        },
        handler=browser_save_session,
        requires=["playwright"],
    ),
    ToolDef(
        name="browser_close_session",
        description=(
            "Close a browser session. By default, saves cookies before closing "
            "so the session can be restored later."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_name": {
                    "type": "string",
                    "description": "Name of the session to close",
                },
                "keep_storage": {
                    "type": "boolean",
                    "description": "Save cookies before closing (default: true). Set false to delete saved state.",
                },
            },
            "required": ["session_name"],
        },
        handler=browser_close_session,
        requires=["playwright"],
    ),
    ToolDef(
        name="browser_list_sessions",
        description=(
            "List active browser sessions and saved sessions. "
            "Shows which sessions are running and which have saved state on disk."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=browser_list_sessions,
        requires=["playwright"],
    ),
]


# --- Lifecycle Hooks ---

_available = False


async def initialize() -> None:
    """Check if Playwright is available."""
    global _available

    try:
        from playwright.async_api import async_playwright
        _available = True
        logger.info("[playwright] Module loaded - browser automation available")
    except ImportError:
        _available = False
        logger.warning("[playwright] playwright package not installed - tools disabled")


async def cleanup() -> None:
    """Cleanup all sessions and browser on module unload."""
    global _sessions

    # Close all active sessions
    for key, session in list(_sessions.items()):
        try:
            await session.context.close()
            logger.info(f"[playwright] Closed session on cleanup: {session.name}")
        except Exception as e:
            logger.warning(f"[playwright] Error closing session {session.name}: {e}")

    _sessions.clear()

    await _close_browser()
    logger.info("[playwright] Browser closed")
