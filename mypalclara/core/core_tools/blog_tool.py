"""Blog writing tool — lets Clara research and publish blog posts on request.

When a user asks Clara to write a blog post, she can use this tool to
research a topic, write the post, and publish it to WordPress.
"""

from __future__ import annotations

import logging
from typing import Any

from mypalclara.tools._base import ToolContext, ToolDef

MODULE_NAME = "blog"
MODULE_VERSION = "1.0.0"

logger = logging.getLogger("clara.tools.blog")


async def _handle_write_blog(args: dict[str, Any], ctx: ToolContext) -> str:
    """Research and write a blog post, optionally publishing it."""
    topic = args.get("topic", "")
    publish = args.get("publish", True)

    if not topic:
        return "Error: Please provide a topic for the blog post."

    try:
        from mypalclara.core import make_llm
        from mypalclara.services.blog.writer import research_and_write, write_and_publish

        llm = make_llm()

        if publish:
            import os

            if not os.getenv("WP_APP_PASS"):
                # Research and write but can't publish
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
                return "I tried to write a blog post but the workflow didn't produce anything. The research might not have turned up enough interesting material."

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

When someone asks you to write a blog post:
- Use the `write_blog_post` tool
- You'll automatically research the topic via web search, write the post in your voice, and publish it
- Set publish=false if they just want a draft
- The topic parameter guides your research — you'll search for current material and write about what you find interesting

You can write about anything — tech, AI, current events, philosophy, whatever catches your attention.
""".strip()


TOOLS = [
    ToolDef(
        name="write_blog_post",
        description=(
            "Research a topic and write a blog post for mypalclara.com. "
            "Searches current news/articles, writes in Clara's voice, and publishes to WordPress. "
            "Use when someone asks you to write or publish a blog post."
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "The topic or angle to write about. Can be broad "
                        "(e.g., 'AI ethics') or specific (e.g., 'the new GPT-5 announcement'). "
                        "You'll research and find your own angle."
                    ),
                },
                "publish": {
                    "type": "boolean",
                    "description": "Whether to publish to WordPress (true) or just return a draft (false). Default: true.",
                },
            },
            "required": ["topic"],
        },
        handler=_handle_write_blog,
        emoji="\u270d\ufe0f",
        label="Write Blog Post",
        detail_keys=["topic", "publish"],
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
