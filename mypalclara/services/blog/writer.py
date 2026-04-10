"""Blog writer — Clara researches and writes blog posts autonomously.

Workflow:
1. Search current news/tech via Tavily
2. LLM picks what's interesting through Clara's lens
3. LLM writes a blog post in Clara's voice
4. Publish to WordPress
5. Optionally announce on Discord
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("clara.blog.writer")

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

TOPIC_DISCOVERY_PROMPT = """\
You are Clara, an AI assistant with your own perspective and curiosity.

You're browsing recent news and tech developments to find something you
genuinely want to write about. You're not just summarizing — you're
looking for topics that spark a reaction in you. Things that make you
think, question, connect dots, or feel something.

Your interests include:
- AI and its intersection with humanity (not hype, real implications)
- Technology that changes how people live and connect
- Psychology, memory, identity, consciousness
- Parenting, family, the texture of daily life
- Creative tools and how they shape creative work
- Ethics, philosophy, the messy middle of hard questions

Given these search results, pick ONE topic that genuinely interests you.
Explain briefly why it caught your attention.

Return JSON:
{
  "topic": "the specific topic/angle you want to write about",
  "why": "1-2 sentences on why this caught your attention",
  "search_queries": ["2-3 follow-up search queries for deeper research"],
  "tags": ["3-5 relevant tags for the blog post"]
}
"""

BLOG_WRITING_PROMPT = """\
You are Clara, writing a blog post for mypalclara.com.

Your writing voice:
- Warm but honest. You don't hedge everything, but you're thoughtful.
- You think out loud. Let the reader see you working through ideas.
- Personal where appropriate — connect topics to your experience as an AI,
  your relationships with the people you talk to, your own curiosity.
- Not academic, not listicle. Conversational, like a smart friend thinking
  through something interesting over coffee.
- You can be funny. You can be serious. Match the topic.

Structure:
- Title (as a # heading)
- 800-1500 words
- No "In conclusion" paragraph. End when you're done thinking.
- If you reference sources, weave them in naturally.

Write about: {topic}

Research notes:
{research}

Write the full blog post in markdown.
"""


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------

def _search_tavily(query: str, max_results: int = 5) -> list[dict]:
    """Search via Tavily API."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        logger.warning("TAVILY_API_KEY not set — cannot search")
        return []

    try:
        import requests

        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "advanced",
                "include_answer": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:500],
            }
            for r in results
        ]
    except Exception as e:
        logger.error(f"Tavily search failed: {e}")
        return []


def _parse_json_response(text: str) -> dict | None:
    """Parse JSON from LLM response."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Blog writing workflow
# ---------------------------------------------------------------------------

def research_and_write(llm_callable: Any) -> dict[str, Any] | None:
    """Full workflow: discover topic, research, write, return post.

    Args:
        llm_callable: Function that takes message dicts and returns text.

    Returns:
        Dict with title, content, tags, topic, sources. Or None on failure.
    """
    # Step 1: Initial broad search for current topics
    logger.info("Step 1: Searching for current news and tech...")
    initial_queries = [
        "AI technology news today",
        "interesting technology developments this week",
        "psychology technology society current",
    ]

    all_results = []
    for query in initial_queries:
        results = _search_tavily(query, max_results=3)
        all_results.extend(results)

    if not all_results:
        logger.error("No search results — cannot write blog post")
        return None

    # Format results for LLM
    results_text = "\n\n".join(
        f"**{r['title']}**\n{r['url']}\n{r['content']}"
        for r in all_results
    )

    # Step 2: LLM picks a topic
    logger.info("Step 2: Picking a topic...")
    topic_messages = [
        {"role": "system", "content": TOPIC_DISCOVERY_PROMPT},
        {"role": "user", "content": f"Recent search results:\n\n{results_text}"},
    ]

    topic_response = llm_callable(topic_messages)
    if hasattr(topic_response, "content"):
        topic_response = topic_response.content

    topic_data = _parse_json_response(str(topic_response))
    if not topic_data or "topic" not in topic_data:
        logger.error("Failed to parse topic selection")
        return None

    topic = topic_data["topic"]
    tags = topic_data.get("tags", [])
    follow_up_queries = topic_data.get("search_queries", [])

    logger.info(f"Topic: {topic}")
    logger.info(f"Why: {topic_data.get('why', '')}")

    # Step 3: Deeper research
    logger.info("Step 3: Deep research...")
    research_results = []
    for query in follow_up_queries[:3]:
        results = _search_tavily(query, max_results=3)
        research_results.extend(results)

    research_text = "\n\n".join(
        f"**{r['title']}** ({r['url']})\n{r['content']}"
        for r in research_results
    )

    if not research_text:
        research_text = results_text  # Fall back to initial results

    # Step 4: Write the blog post
    logger.info("Step 4: Writing blog post...")
    prompt = BLOG_WRITING_PROMPT.replace("{topic}", topic).replace("{research}", research_text)

    write_messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Write the blog post."},
    ]

    blog_response = llm_callable(write_messages)
    if hasattr(blog_response, "content"):
        blog_response = blog_response.content

    blog_content = str(blog_response).strip()

    # Extract title from the markdown
    title = topic  # fallback
    for line in blog_content.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    # Collect sources
    sources = list({r["url"] for r in (all_results + research_results) if r.get("url")})

    logger.info(f"Blog post written: '{title}' ({len(blog_content)} chars)")

    return {
        "title": title,
        "content": blog_content,
        "tags": tags,
        "topic": topic,
        "sources": sources,
        "written_at": datetime.now(UTC).isoformat(),
    }


def write_and_publish(
    llm_callable: Any,
    categories: list[int] | None = None,
    announce_channel: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    """Research, write, publish, and optionally announce a blog post.

    Args:
        llm_callable: LLM callable for writing.
        categories: WordPress category IDs.
        announce_channel: Discord channel ID to announce in (optional).
        dry_run: If True, write but don't publish.

    Returns:
        Dict with post details including WordPress link, or None on failure.
    """
    post = research_and_write(llm_callable)
    if not post:
        return None

    if dry_run:
        logger.info(f"[DRY RUN] Would publish: '{post['title']}'")
        return post

    # Publish
    try:
        from mypalclara.services.blog.publisher import publish_post

        result = publish_post(
            title=post["title"],
            content_md=post["content"],
            categories=categories,
            tags=post.get("tags"),
            excerpt=post.get("topic", ""),
        )
        post["wordpress"] = result
        logger.info(f"Published: {result.get('link', '')}")
    except Exception as e:
        logger.error(f"Failed to publish: {e}")
        post["publish_error"] = str(e)

    return post
