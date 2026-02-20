"""Browser automation tool using Playwright.

Provides tools for web automation, screenshots, PDF generation,
and interaction with web pages.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from mypalclara.tools._base import ToolContext, ToolDef

MODULE_NAME = "browser"
MODULE_VERSION = "1.0.0"

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
## Browser Automation

You can control a headless browser for web automation, screenshots, and more.

**Tools:**
- `browser_start` - Start a new browser session
- `browser_navigate` - Navigate to a URL
- `browser_screenshot` - Take a screenshot
- `browser_click` - Click on an element
- `browser_type` - Type text into a field
- `browser_extract` - Extract text content from the page
- `browser_pdf` - Generate a PDF of the page
- `browser_evaluate` - Run JavaScript in the page
- `browser_stop` - Close a browser session
- `browser_list` - List active sessions

**Usage Notes:**
- Sessions are named for easy reference (default: "default")
- Selectors use CSS syntax (e.g., "#submit-btn", ".form-input")
- Screenshots return base64-encoded PNGs
- PDFs return base64-encoded PDF data
""".strip()

# Global browser state
_playwright = None
_browser = None
_contexts: dict[str, Any] = {}
_pages: dict[str, Any] = {}


async def _ensure_browser():
    """Ensure browser is running."""
    global _playwright, _browser

    if _browser is not None:
        return _browser

    try:
        from playwright.async_api import async_playwright

        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=True)
        logger.info("[browser] Started Chromium browser")
        return _browser
    except Exception as e:
        logger.error(f"[browser] Failed to start browser: {e}")
        raise


async def browser_start(args: dict[str, Any], ctx: ToolContext) -> str:
    """Start a new browser session."""
    session_name = args.get("name", "default")

    if session_name in _contexts:
        return f"Browser session '{session_name}' already exists. Use browser_stop to close it first."

    try:
        browser = await _ensure_browser()
        context = await browser.new_context(
            viewport={
                "width": args.get("width", 1280),
                "height": args.get("height", 720),
            },
            user_agent=args.get("user_agent"),
        )
        page = await context.new_page()

        _contexts[session_name] = context
        _pages[session_name] = page

        return f"Browser session '{session_name}' started (1280x720 viewport)."
    except Exception as e:
        return f"Error starting browser: {e}"


async def browser_navigate(args: dict[str, Any], ctx: ToolContext) -> str:
    """Navigate to a URL."""
    session_name = args.get("session", "default")
    url = args.get("url")

    if not url:
        return "Error: url is required"

    # Auto-start session if needed
    if session_name not in _pages:
        result = await browser_start({"name": session_name}, ctx)
        if "Error" in result:
            return result

    page = _pages[session_name]
    wait_until = args.get("wait_until", "domcontentloaded")

    try:
        response = await page.goto(url, wait_until=wait_until, timeout=30000)
        status = response.status if response else "unknown"
        title = await page.title()

        return f"Navigated to {url}\nStatus: {status}\nTitle: {title}"
    except Exception as e:
        return f"Navigation failed: {e}"


async def browser_screenshot(args: dict[str, Any], ctx: ToolContext) -> str:
    """Take a screenshot of the current page."""
    session_name = args.get("session", "default")
    full_page = args.get("full_page", False)

    if session_name not in _pages:
        return f"Browser session '{session_name}' not found. Start a session first."

    page = _pages[session_name]

    try:
        screenshot = await page.screenshot(full_page=full_page, type="png")
        b64 = base64.b64encode(screenshot).decode()

        # Return as data URL for easy embedding
        return f"Screenshot captured ({len(screenshot)} bytes).\nData URL: data:image/png;base64,{b64[:100]}..."
    except Exception as e:
        return f"Screenshot failed: {e}"


async def browser_click(args: dict[str, Any], ctx: ToolContext) -> str:
    """Click on an element."""
    session_name = args.get("session", "default")
    selector = args.get("selector")

    if not selector:
        return "Error: selector is required"

    if session_name not in _pages:
        return f"Browser session '{session_name}' not found."

    page = _pages[session_name]

    try:
        await page.click(selector, timeout=10000)
        return f"Clicked on '{selector}'"
    except Exception as e:
        return f"Click failed: {e}"


async def browser_type(args: dict[str, Any], ctx: ToolContext) -> str:
    """Type text into an input field."""
    session_name = args.get("session", "default")
    selector = args.get("selector")
    text = args.get("text")
    clear = args.get("clear", True)

    if not selector:
        return "Error: selector is required"
    if not text:
        return "Error: text is required"

    if session_name not in _pages:
        return f"Browser session '{session_name}' not found."

    page = _pages[session_name]

    try:
        if clear:
            await page.fill(selector, text, timeout=10000)
        else:
            await page.type(selector, text, timeout=10000)
        return f"Typed text into '{selector}'"
    except Exception as e:
        return f"Type failed: {e}"


async def browser_extract(args: dict[str, Any], ctx: ToolContext) -> str:
    """Extract text content from the page."""
    session_name = args.get("session", "default")
    selector = args.get("selector", "body")

    if session_name not in _pages:
        return f"Browser session '{session_name}' not found."

    page = _pages[session_name]

    try:
        content = await page.inner_text(selector, timeout=10000)
        # Truncate if too long
        if len(content) > 10000:
            content = content[:10000] + "\n... (truncated)"
        return content if content else "(no text content)"
    except Exception as e:
        return f"Extraction failed: {e}"


async def browser_pdf(args: dict[str, Any], ctx: ToolContext) -> str:
    """Generate PDF of the current page."""
    session_name = args.get("session", "default")

    if session_name not in _pages:
        return f"Browser session '{session_name}' not found."

    page = _pages[session_name]

    try:
        pdf_bytes = await page.pdf(format="A4", print_background=True)
        b64 = base64.b64encode(pdf_bytes).decode()
        return f"PDF generated ({len(pdf_bytes)} bytes).\nData URL: data:application/pdf;base64,{b64[:100]}..."
    except Exception as e:
        return f"PDF generation failed: {e}"


async def browser_evaluate(args: dict[str, Any], ctx: ToolContext) -> str:
    """Execute JavaScript in the page context."""
    session_name = args.get("session", "default")
    script = args.get("script")

    if not script:
        return "Error: script is required"

    if session_name not in _pages:
        return f"Browser session '{session_name}' not found."

    page = _pages[session_name]

    try:
        result = await page.evaluate(script)
        return str(result) if result is not None else "(undefined)"
    except Exception as e:
        return f"JavaScript execution failed: {e}"


async def browser_stop(args: dict[str, Any], ctx: ToolContext) -> str:
    """Close a browser session."""
    session_name = args.get("session", "default")

    if session_name not in _contexts:
        return f"Browser session '{session_name}' not found."

    try:
        await _contexts[session_name].close()
    except Exception:
        pass

    del _contexts[session_name]
    del _pages[session_name]

    return f"Browser session '{session_name}' closed."


async def browser_list(args: dict[str, Any], ctx: ToolContext) -> str:
    """List active browser sessions."""
    if not _contexts:
        return "No active browser sessions."

    lines = ["Active browser sessions:"]
    for name in _pages:
        page = _pages[name]
        url = page.url if page else "(no page)"
        lines.append(f"  - {name}: {url}")

    return "\n".join(lines)


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="browser_start",
        description="Start a new browser session with optional viewport size and user agent.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Session name (default: 'default')",
                },
                "width": {
                    "type": "integer",
                    "description": "Viewport width (default: 1280)",
                },
                "height": {
                    "type": "integer",
                    "description": "Viewport height (default: 720)",
                },
                "user_agent": {
                    "type": "string",
                    "description": "Custom user agent string",
                },
            },
        },
        handler=browser_start,
        emoji="🌐",
        label="Start Browser",
        detail_keys=["name"],
        risk_level="moderate",
        intent="execute",
    ),
    ToolDef(
        name="browser_navigate",
        description="Navigate to a URL in the browser. Auto-starts a session if needed.",
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to navigate to",
                },
                "session": {
                    "type": "string",
                    "description": "Session name (default: 'default')",
                },
                "wait_until": {
                    "type": "string",
                    "enum": ["load", "domcontentloaded", "networkidle"],
                    "description": "When to consider navigation complete",
                },
            },
            "required": ["url"],
        },
        handler=browser_navigate,
        emoji="🔗",
        label="Navigate",
        detail_keys=["url"],
        risk_level="moderate",
        intent="network",
    ),
    ToolDef(
        name="browser_screenshot",
        description="Take a screenshot of the current page. Returns base64-encoded PNG.",
        parameters={
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": "Session name (default: 'default')",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture full scrollable page (default: false)",
                },
            },
        },
        handler=browser_screenshot,
        emoji="📸",
        label="Screenshot",
        detail_keys=["session"],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="browser_click",
        description="Click on an element in the page using a CSS selector.",
        parameters={
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector for element to click",
                },
                "session": {
                    "type": "string",
                    "description": "Session name (default: 'default')",
                },
            },
            "required": ["selector"],
        },
        handler=browser_click,
        emoji="👆",
        label="Click",
        detail_keys=["selector"],
        risk_level="moderate",
        intent="execute",
    ),
    ToolDef(
        name="browser_type",
        description="Type text into an input field. By default clears the field first.",
        parameters={
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector for input field",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type",
                },
                "session": {
                    "type": "string",
                    "description": "Session name (default: 'default')",
                },
                "clear": {
                    "type": "boolean",
                    "description": "Clear field before typing (default: true)",
                },
            },
            "required": ["selector", "text"],
        },
        handler=browser_type,
        emoji="⌨️",
        label="Type",
        detail_keys=["selector"],
        risk_level="moderate",
        intent="execute",
    ),
    ToolDef(
        name="browser_extract",
        description="Extract text content from the page or a specific element.",
        parameters={
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector (default: body)",
                },
                "session": {
                    "type": "string",
                    "description": "Session name (default: 'default')",
                },
            },
        },
        handler=browser_extract,
        emoji="📝",
        label="Extract",
        detail_keys=["selector"],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="browser_pdf",
        description="Generate a PDF of the current page. Returns base64-encoded PDF.",
        parameters={
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": "Session name (default: 'default')",
                },
            },
        },
        handler=browser_pdf,
        emoji="📄",
        label="PDF",
        detail_keys=["session"],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="browser_evaluate",
        description="Execute JavaScript code in the page context. Use for complex interactions or data extraction.",
        parameters={
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "JavaScript code to execute",
                },
                "session": {
                    "type": "string",
                    "description": "Session name (default: 'default')",
                },
            },
            "required": ["script"],
        },
        handler=browser_evaluate,
        emoji="⚡",
        label="JS Eval",
        detail_keys=[],
        risk_level="dangerous",
        intent="execute",
    ),
    ToolDef(
        name="browser_stop",
        description="Close a browser session and release resources.",
        parameters={
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": "Session name to close (default: 'default')",
                },
            },
        },
        handler=browser_stop,
        emoji="🛑",
        label="Stop Browser",
        detail_keys=["session"],
        risk_level="safe",
        intent="execute",
    ),
    ToolDef(
        name="browser_list",
        description="List all active browser sessions with their current URLs.",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=browser_list,
        emoji="📋",
        label="List Sessions",
        detail_keys=[],
        risk_level="safe",
        intent="read",
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize browser tool module."""
    # Browser is lazily initialized on first use
    pass


async def cleanup() -> None:
    """Cleanup on module unload - close all sessions and browser."""
    global _playwright, _browser

    # Close all contexts
    for name in list(_contexts.keys()):
        try:
            await _contexts[name].close()
        except Exception:
            pass

    _contexts.clear()
    _pages.clear()

    # Close browser
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None

    # Stop playwright
    if _playwright:
        try:
            await _playwright.stop()
        except Exception:
            pass
        _playwright = None

    logger.info("[browser] Cleaned up browser resources")
