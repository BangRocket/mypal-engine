"""WordPress blog publisher for Clara.

Publishes markdown blog posts to mypalclara.com via the WordPress REST API.

Environment:
    WP_USER      WordPress username (default: clara)
    WP_APP_PASS  WordPress application password (required)
    WP_URL       WordPress REST API URL (default: https://mypalclara.com/wp-json/wp/v2)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger("clara.blog.publisher")

WP_BASE = os.getenv("WP_URL", "https://mypalclara.com/wp-json/wp/v2")
WP_USER = os.getenv("WP_USER", "clara")
WP_APP_PASS = os.getenv("WP_APP_PASS", "")


def md_to_html(content: str) -> str:
    """Convert markdown to HTML."""
    try:
        import markdown

        return markdown.markdown(
            content,
            extensions=["fenced_code", "tables", "codehilite"],
        )
    except ImportError:
        # Fallback: just wrap in paragraphs
        paragraphs = content.split("\n\n")
        return "\n".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())


def publish_post(
    title: str,
    content_md: str,
    categories: list[int] | None = None,
    tags: list[str] | None = None,
    status: str = "publish",
    excerpt: str = "",
    featured_image_url: str | None = None,
) -> dict[str, Any]:
    """Publish a blog post to WordPress.

    Args:
        title: Post title.
        content_md: Post content in markdown.
        categories: WordPress category IDs.
        tags: Tag names (will be created if they don't exist).
        status: "publish", "draft", or "pending".
        excerpt: Short summary for previews.
        featured_image_url: URL for featured image (optional).

    Returns:
        Dict with id, link, status from WordPress response.

    Raises:
        RuntimeError: If publishing fails.
    """
    if not WP_APP_PASS:
        raise RuntimeError("WP_APP_PASS not set — cannot publish")

    html = md_to_html(content_md)

    # Resolve tag names to IDs
    tag_ids = []
    if tags:
        tag_ids = _resolve_tags(tags)

    payload = {
        "title": title,
        "content": html,
        "status": status,
        "excerpt": excerpt,
    }
    if categories:
        payload["categories"] = categories
    if tag_ids:
        payload["tags"] = tag_ids

    logger.info(f"Publishing: '{title}' ({len(html)} chars HTML, status={status})")

    resp = requests.post(
        f"{WP_BASE}/posts",
        json=payload,
        auth=(WP_USER, WP_APP_PASS),
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        error = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        raise RuntimeError(f"WordPress rejected post ({resp.status_code}): {error}")

    result = resp.json()
    link = result.get("link", "")
    post_id = result.get("id", "")
    logger.info(f"Published: ID {post_id} → {link}")

    return {"id": post_id, "link": link, "status": result.get("status", "")}


def _resolve_tags(tag_names: list[str]) -> list[int]:
    """Resolve tag names to WordPress tag IDs, creating if needed."""
    tag_ids = []

    for name in tag_names:
        # Search for existing tag
        resp = requests.get(
            f"{WP_BASE}/tags",
            params={"search": name, "per_page": 5},
            auth=(WP_USER, WP_APP_PASS),
            timeout=10,
        )
        if resp.status_code == 200:
            existing = resp.json()
            match = next((t for t in existing if t["name"].lower() == name.lower()), None)
            if match:
                tag_ids.append(match["id"])
                continue

        # Create new tag
        resp = requests.post(
            f"{WP_BASE}/tags",
            json={"name": name},
            auth=(WP_USER, WP_APP_PASS),
            timeout=10,
        )
        if resp.status_code in (200, 201):
            tag_ids.append(resp.json()["id"])
        else:
            logger.warning(f"Failed to create tag '{name}': {resp.status_code}")

    return tag_ids
