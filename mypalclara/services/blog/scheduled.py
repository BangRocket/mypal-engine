"""Scheduled blog post task — wires into Clara's gateway scheduler.

Can be run standalone or registered as a scheduled task.

Standalone:
    poetry run python -m mypalclara.services.blog.scheduled
    poetry run python -m mypalclara.services.blog.scheduled --dry-run

Scheduler registration (in gateway startup):
    from mypalclara.services.blog.scheduled import register_blog_task
    register_blog_task(scheduler)
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("clara.blog.scheduled")

# Default schedule: Tuesday and Friday at 10 AM ET
BLOG_SCHEDULE = os.getenv("BLOG_SCHEDULE", "0 14 * * 2,5")  # UTC (10 AM ET = 14 UTC)
BLOG_CATEGORIES = [int(x) for x in os.getenv("BLOG_CATEGORIES", "").split(",") if x.strip()]
BLOG_ANNOUNCE_CHANNEL = os.getenv("BLOG_ANNOUNCE_CHANNEL")


async def run_blog_task() -> None:
    """Async handler for the scheduled blog task."""
    from mypalclara.core import make_llm
    from mypalclara.services.blog.writer import write_and_publish

    logger.info("Starting scheduled blog post...")

    llm = make_llm()

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: write_and_publish(
            llm_callable=llm,
            categories=BLOG_CATEGORIES or None,
            announce_channel=BLOG_ANNOUNCE_CHANNEL,
        ),
    )

    if result:
        wp = result.get("wordpress", {})
        link = wp.get("link", "not published")
        logger.info(f"Blog post complete: '{result['title']}' → {link}")

        # Announce on Discord if configured
        if BLOG_ANNOUNCE_CHANNEL and wp.get("link"):
            await _announce(result)
    else:
        logger.warning("Blog post workflow returned no result")


async def _announce(post: dict) -> None:
    """Announce the blog post on Discord via proactive message."""
    try:
        from mypalclara.services.proactive.engine import send_proactive_message

        link = post.get("wordpress", {}).get("link", "")
        title = post.get("title", "New post")
        topic = post.get("topic", "")

        message = (
            f"I just published a new blog post: **{title}**\n\n"
            f"{topic}\n\n"
            f"Read it here: {link}"
        )

        await send_proactive_message(
            user_id=None,
            channel_id=BLOG_ANNOUNCE_CHANNEL,
            message=message,
        )
        logger.info(f"Announced blog post in channel {BLOG_ANNOUNCE_CHANNEL}")
    except Exception as e:
        logger.warning(f"Failed to announce blog post: {e}")


def register_blog_task(scheduler) -> None:
    """Register the blog writing task with the gateway scheduler.

    Args:
        scheduler: The gateway Scheduler instance.
    """
    from mypalclara.gateway.scheduler import ScheduledTask

    if not os.getenv("WP_APP_PASS"):
        logger.info("Blog task not registered — WP_APP_PASS not set")
        return

    task = ScheduledTask(
        name="clara-blog-post",
        task_type="cron",
        cron=BLOG_SCHEDULE,
        description="Clara researches and writes a blog post",
        handler=run_blog_task,
    )
    scheduler.add_task(task)
    logger.info(f"Blog task registered (schedule: {BLOG_SCHEDULE})")


# ---------------------------------------------------------------------------
# Standalone execution
# ---------------------------------------------------------------------------

def main():
    """Run blog workflow directly (not via scheduler)."""
    import argparse
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
    from dotenv import load_dotenv

    load_dotenv()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Clara blog writer")
    parser.add_argument("--dry-run", action="store_true", help="Write but don't publish")
    args = parser.parse_args()

    from mypalclara.core import make_llm
    from mypalclara.services.blog.writer import write_and_publish

    llm = make_llm()

    result = write_and_publish(
        llm_callable=llm,
        categories=BLOG_CATEGORIES or None,
        dry_run=args.dry_run,
    )

    if result:
        print(f"\nTitle: {result['title']}")
        print(f"Tags: {', '.join(result.get('tags', []))}")
        print(f"Sources: {len(result.get('sources', []))}")
        if result.get("wordpress"):
            print(f"Link: {result['wordpress'].get('link', '')}")
        if args.dry_run:
            print(f"\n--- Content Preview ---\n{result['content'][:500]}...")
    else:
        print("Failed to write blog post")
        sys.exit(1)


if __name__ == "__main__":
    main()
