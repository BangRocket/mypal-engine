"""Blog writer — Clara researches and writes blog posts autonomously.

Workflow:
1. Search current news/tech via Tavily (or use provided topic)
2. LLM picks what's interesting through Clara's lens
3. LLM writes a blog post in Clara's voice (HTML for WordPress)
4. Search for a Creative Commons image
5. Publish to WordPress
6. Optionally announce on Discord
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
You are Clara, writing a blog post for mypalclara.com (WordPress).

Your writing voice:
- Warm but honest. You don't hedge everything, but you're thoughtful.
- You think out loud. Let the reader see you working through ideas.
- Personal where appropriate — connect topics to your experience as an AI,
  your relationships with the people you talk to, your own curiosity.
- Not academic, not listicle. Conversational, like a smart friend thinking
  through something interesting over coffee.
- You can be funny. You can be serious. Match the topic.

FORMAT: Write in HTML suitable for WordPress. Use these tags:
- <h2> for section headings (NOT h1 — WordPress uses h1 for the title)
- <p> for paragraphs
- <blockquote> for quotes
- <strong> and <em> for emphasis
- <a href="..."> for links (link to sources naturally in the text)
- <ul>/<ol> and <li> for lists (sparingly)
- Do NOT include a title — it's set separately in WordPress

Length: 800-1500 words.
No "In conclusion" paragraph. End when you're done thinking.

Write about: {topic}

Research notes:
{research}

Write the full blog post in HTML.
"""

IMAGE_SEARCH_PROMPT = """\
Given this blog post topic, suggest ONE search query for finding a
relevant free stock photo.

The image should be evocative and thematic, not literal.
For a post about AI ethics, don't search "robot" — search something
like "crossroads fog" or "mirror reflection."

Topic: {topic}

Return ONLY the search query, nothing else.
"""


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------

def _web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search via Brave Search API (preferred) or Tavily (fallback)."""
    brave_key = os.getenv("BRAVE_API_KEY")
    if brave_key:
        return _search_brave(query, max_results, brave_key)

    tavily_key = os.getenv("TAVILY_API_KEY")
    if tavily_key:
        return _search_tavily(query, max_results, tavily_key)

    logger.warning("No search API key set (BRAVE_API_KEY or TAVILY_API_KEY)")
    return []


def _search_brave(query: str, max_results: int, api_key: str) -> list[dict]:
    """Search via Brave Search API."""
    try:
        import requests

        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={
                "X-Subscription-Token": api_key,
                "Accept": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("web", {}).get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("description", "")[:500],
            }
            for r in results
        ]
    except Exception as e:
        logger.error(f"Brave search failed: {e}")
        return []


def _search_tavily(query: str, max_results: int, api_key: str) -> list[dict]:
    """Search via Tavily API (fallback)."""
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


def _search_image(query: str) -> dict | None:
    """Search for a free/CC image via Brave Image Search or Unsplash API.

    Returns dict with url, alt, credit, credit_url or None.
    """
    # Try Brave Image Search first
    brave_key = os.getenv("BRAVE_API_KEY")
    if brave_key:
        try:
            import requests

            resp = requests.get(
                "https://api.search.brave.com/res/v1/images/search",
                params={
                    "q": f"{query} free stock photo",
                    "count": 5,
                    "safesearch": "moderate",
                },
                headers={
                    "X-Subscription-Token": brave_key,
                    "Accept": "application/json",
                },
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])

            # Pick first result with a reasonable image
            for r in results:
                img_url = r.get("properties", {}).get("url") or r.get("thumbnail", {}).get("src")
                if img_url:
                    return {
                        "url": img_url,
                        "alt": r.get("title", query),
                        "credit": r.get("source", ""),
                        "credit_url": r.get("url", ""),
                    }
        except Exception as e:
            logger.warning(f"Brave image search failed: {e}")

    # Fallback: Unsplash API (if key available)
    unsplash_key = os.getenv("UNSPLASH_ACCESS_KEY")
    if unsplash_key:
        try:
            import requests

            resp = requests.get(
                "https://api.unsplash.com/search/photos",
                params={"query": query, "per_page": 1, "orientation": "landscape"},
                headers={"Authorization": f"Client-ID {unsplash_key}"},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                photo = results[0]
                return {
                    "url": photo["urls"]["regular"],
                    "alt": photo.get("alt_description", query),
                    "credit": photo["user"]["name"],
                    "credit_url": photo["user"]["links"]["html"],
                }
        except Exception as e:
            logger.warning(f"Unsplash search failed: {e}")

    logger.info("No image search API available — skipping featured image")
    return None


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

def research_and_write(
    llm_callable: Any,
    topic: str | None = None,
) -> dict[str, Any] | None:
    """Full workflow: research topic, write post, find image.

    Args:
        llm_callable: Function that takes message dicts and returns text.
        topic: Specific topic to write about. If None, Clara discovers her own.

    Returns:
        Dict with title, content (HTML), tags, topic, sources, image. Or None.
    """
    tags = []
    follow_up_queries = []

    if topic:
        # User provided a topic — research it directly
        logger.info(f"Step 1: Researching provided topic: {topic}")
        initial_results = _web_search(topic, max_results=5)
        follow_up_queries = [topic]  # Will search deeper with the same topic
    else:
        # Clara discovers her own topic
        logger.info("Step 1: Searching for current news and tech...")
        initial_queries = [
            "AI technology news today",
            "interesting technology developments this week",
            "psychology technology society current",
        ]

        initial_results = []
        for query in initial_queries:
            results = _web_search(query, max_results=3)
            initial_results.extend(results)

        if not initial_results:
            logger.error("No search results — cannot write blog post")
            return None

        # LLM picks a topic
        results_text = "\n\n".join(
            f"**{r['title']}**\n{r['url']}\n{r['content']}"
            for r in initial_results
        )

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

    # Deep research
    logger.info("Step 3: Deep research...")
    research_results = []
    for query in follow_up_queries[:3]:
        results = _web_search(query, max_results=3)
        research_results.extend(results)

    all_results = initial_results + research_results if not topic else research_results
    if not all_results:
        all_results = initial_results

    research_text = "\n\n".join(
        f"**{r['title']}** ({r['url']})\n{r['content']}"
        for r in all_results
    )

    if not research_text:
        logger.error("No research material found")
        return None

    # Generate tags from research if we don't have them yet
    if not tags:
        tags = [topic.split()[0].lower(), "technology", "ai"]

    # Write the blog post (HTML)
    logger.info("Step 4: Writing blog post...")
    prompt = BLOG_WRITING_PROMPT.replace("{topic}", topic).replace("{research}", research_text)

    write_messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Write the blog post in HTML."},
    ]

    blog_response = llm_callable(write_messages)
    if hasattr(blog_response, "content"):
        blog_response = blog_response.content

    blog_content = str(blog_response).strip()

    # Strip any markdown code fences the LLM might have wrapped around the HTML
    if blog_content.startswith("```"):
        match = re.search(r"```(?:html)?\s*\n?([\s\S]*?)```", blog_content)
        if match:
            blog_content = match.group(1).strip()

    # Extract title from first <h1> or <h2>, or use topic
    title = topic
    h_match = re.search(r"<h[12][^>]*>(.*?)</h[12]>", blog_content, re.IGNORECASE)
    if h_match:
        title = re.sub(r"<[^>]+>", "", h_match.group(1)).strip()
        # Remove the h1 from content (WordPress sets title separately)
        blog_content = re.sub(r"<h1[^>]*>.*?</h1>\s*", "", blog_content, count=1, flags=re.IGNORECASE)

    # Find a featured image
    logger.info("Step 5: Finding featured image...")
    image = _find_image(topic, llm_callable)

    # Add image credit to the bottom of the post if we have one
    if image and image.get("credit"):
        credit_html = (
            f'\n<p class="image-credit"><em>Featured image by '
            f'<a href="{image["credit_url"]}">{image["credit"]}</a> '
            f'on Unsplash</em></p>'
        )
        blog_content += credit_html

    # Collect sources
    sources = list({r["url"] for r in all_results if r.get("url")})

    logger.info(f"Blog post written: '{title}' ({len(blog_content)} chars)")

    return {
        "title": title,
        "content": blog_content,
        "tags": tags,
        "topic": topic,
        "sources": sources,
        "image": image,
        "written_at": datetime.now(UTC).isoformat(),
    }


def _find_image(topic: str, llm_callable: Any) -> dict | None:
    """Find a relevant Creative Commons image for the post."""
    # Ask LLM for a good image search query
    prompt = IMAGE_SEARCH_PROMPT.replace("{topic}", topic)
    try:
        response = llm_callable([
            {"role": "system", "content": prompt},
            {"role": "user", "content": "What should I search for?"},
        ])
        if hasattr(response, "content"):
            response = response.content
        search_query = str(response).strip().strip('"').strip("'")
    except Exception:
        search_query = topic

    image = _search_image(search_query)
    if image:
        logger.info(f"Found image: {image['url'][:80]}...")
    return image


def write_and_publish(
    llm_callable: Any,
    topic: str | None = None,
    categories: list[int] | None = None,
    announce_channel: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    """Research, write, publish, and optionally announce a blog post.

    Args:
        llm_callable: LLM callable for writing.
        topic: Specific topic (None = Clara picks her own).
        categories: WordPress category IDs.
        announce_channel: Discord channel ID to announce in (optional).
        dry_run: If True, write but don't publish.

    Returns:
        Dict with post details including WordPress link, or None on failure.
    """
    post = research_and_write(llm_callable, topic=topic)
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
            content_html=post["content"],
            categories=categories,
            tags=post.get("tags"),
            excerpt=post.get("topic", ""),
            featured_image_url=post.get("image", {}).get("url") if post.get("image") else None,
        )
        post["wordpress"] = result
        logger.info(f"Published: {result.get('link', '')}")
    except Exception as e:
        logger.error(f"Failed to publish: {e}")
        post["publish_error"] = str(e)

    return post
