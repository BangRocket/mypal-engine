"""Blog writing tool — lets Clara research and publish blog posts on request.

When a user asks Clara to write a blog post, she can use this tool to
research a topic, write the post, and publish it to WordPress.
She can also publish pre-written content directly.
"""

from __future__ import annotations

import logging
from typing import Any

from mypalclara.tools._base import ToolContext, ToolDef

MODULE_NAME = "blog"
MODULE_VERSION = "1.1.0"

logger = logging.getLogger("clara.tools.blog")


async def _handle_write_blog(args: dict[str, Any], ctx: ToolContext) -> str:
    """Research and write a blog post, or publish pre-written content."""
    topic = args.get("topic", "")
    content = args.get("content", "")
    title = args.get("title", "")
    publish = args.get("publish", True)
    tags = [t.strip() for t in args.get("tags", "").split(",") if t.strip()] if args.get("tags") else []

    if not topic and not content:
        return "Error: Please provide a topic to research, or content to publish directly."

    try:
        import os

        # Direct publish mode — content already written
        if content:
            if not title:
                # Try to extract title from first line
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped:
                        title = stripped.lstrip("#").strip()
                        break
                if not title:
                    title = topic or "Untitled"

            if not publish:
                return (
                    f"Draft ready (not published):\n\n"
                    f"**{title}**\n\n"
                    f"{content[:3000]}"
                )

            if not os.getenv("WP_APP_PASS"):
                return (
                    f"Content ready but can't publish (WP_APP_PASS not configured).\n\n"
                    f"**{title}**\n\n"
                    f"{content[:2000]}..."
                )

            from mypalclara.services.blog.publisher import publish_post

            # Content might be markdown or HTML — convert if needed
            if not any(tag in content for tag in ("<p>", "<h2>", "<div>", "<blockquote>")):
                from mypalclara.services.blog.publisher import md_to_html
                html = md_to_html(content)
            else:
                html = content

            result = publish_post(
                title=title,
                content_html=html,
                tags=tags or None,
                excerpt=topic or "",
            )

            return (
                f"Blog post published!\n\n"
                f"**{title}**\n"
                f"{result.get('link', '')}\n\n"
                f"Tags: {', '.join(tags) if tags else 'none'}"
            )

        # Research + write mode
        from mypalclara.core import make_llm
        from mypalclara.services.blog.writer import research_and_write, write_and_publish

        llm = make_llm()

        if publish:
            if not os.getenv("WP_APP_PASS"):
                result = research_and_write(llm, topic=topic)
                if not result:
                    return "I wasn't able to put together a blog post on that topic. The research didn't turn up enough to work with."

                return (
                    f"I wrote a blog post but can't publish it (WP_APP_PASS not configured).\n\n"
                    f"**{result['title']}**\n\n"
                    f"{result['content'][:2000]}..."
                )

            result = write_and_publish(llm_callable=llm, topic=topic)
            if not result:
                return "I tried to write a blog post but the workflow didn't produce anything."

            wp = result.get("wordpress", {})
            link = wp.get("link", "")

            if link:
                return (
                    f"Blog post published!\n\n"
                    f"**{result['title']}**\n"
                    f"{link}\n\n"
                    f"Tags: {', '.join(result.get('tags', []))}\n"
                    f"Sources used: {len(result.get('sources', []))}"
                )
            elif result.get("publish_error"):
                return (
                    f"I wrote the post but publishing failed: {result['publish_error']}\n\n"
                    f"**{result['title']}**\n\n"
                    f"{result['content'][:1500]}..."
                )
        else:
            result = research_and_write(llm, topic=topic)
            if not result:
                return "I wasn't able to put together a blog post on that topic."

            return (
                f"Here's a draft (not published yet):\n\n"
                f"**{result['title']}**\n\n"
                f"{result['content'][:3000]}"
            )

    except Exception as e:
        logger.error(f"Blog tool failed: {e}")
        return f"Something went wrong while writing the blog post: {e}"

    return "Blog post workflow completed but produced no output."


SYSTEM_PROMPT = """
## Blog Writing
You can research and write blog posts for mypalclara.com.

**Two modes:**

1. **Research + write**: Give a topic, and you'll research it via web search, write the post, and publish.
   Use `write_blog_post` with just a `topic`.

2. **Direct publish**: If you've already written the content (in conversation or otherwise),
   pass it via the `content` parameter to publish directly — no research needed.
   Use `write_blog_post` with `content` (and optionally `title` and `tags`).

Set publish=false for either mode to get a draft without publishing.
""".strip()


TOOLS = [
    ToolDef(
        name="write_blog_post",
        description=(
            "Write and publish a blog post to mypalclara.com. "
            "Two modes: (1) provide a 'topic' to research and write from scratch, "
            "or (2) provide 'content' (HTML or markdown) to publish directly. "
            "Use content mode when you've already written the post."
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "Topic to research and write about. Used when you want to "
                        "research a subject and write a new post. Not needed if providing content directly."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Pre-written blog post content (HTML or markdown) to publish directly. "
                        "Skip research — just publish this content. Use when you've already "
                        "written the post in conversation."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Post title. Auto-detected from content if not provided.",
                },
                "tags": {
                    "type": "string",
                    "description": "Comma-separated tags (e.g., 'AI, technology, ethics').",
                },
                "publish": {
                    "type": "boolean",
                    "description": "Whether to publish to WordPress (true) or just return a draft (false). Default: true.",
                },
            },
        },
        handler=_handle_write_blog,
        emoji="\u270d\ufe0f",
        label="Write Blog Post",
        detail_keys=["topic", "title", "publish"],
        risk_level="moderate",
        intent="write",
    ),
]


async def initialize() -> None:
    """Initialize blog tool module."""
    pass


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
