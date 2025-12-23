"""Playwright browser automation tools.

Provides tools for web browsing, screenshots, and page interaction.
Tools: browse_page, screenshot_page, extract_page_data

Requires: playwright package and browser binaries installed.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any

from ._base import ToolContext, ToolDef

MODULE_NAME = "playwright_browser"
MODULE_VERSION = "1.0.0"

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
## Browser Automation (Playwright)
You can browse web pages, take screenshots, and extract content.

**Tools:**
- `browse_page` - Navigate to a URL and extract text content
- `screenshot_page` - Take a screenshot of a webpage (returns file path)
- `extract_page_data` - Extract structured data using CSS selectors

**When to Use:**
- User asks to check a website or get info from a URL
- Need to take a screenshot of a page
- Scraping data from a structured page
- Checking if a website is up or what it shows

**Note:** Pages are rendered with a real browser, so JavaScript content works.
""".strip()

# Browser instance (reused across calls)
_browser = None
_playwright = None


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
    """Cleanup browser on module unload."""
    await _close_browser()
    logger.info("[playwright] Browser closed")
