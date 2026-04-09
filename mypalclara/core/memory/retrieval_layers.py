"""Layered memory retrieval — context-budgeted memory loading.

Inspired by MemPalace's layered approach:
  L0: Identity       (~200 tokens)  — Always loaded. SOUL.md persona.
  L1: User Profile   (~800 tokens)  — Always loaded. Key facts, emotional state, active arcs.
  L2: Relevant Context (~3000 tokens) — Per-message. Episodes, graph relations, history.
  L3: Deep Search    (on demand)    — Full semantic search, triggered by Clara's tools.

Context budgeting ensures we never blow the context window on memory alone.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger("clara.memory.retrieval")

if TYPE_CHECKING:
    from mypalclara.core.memory.episodes import EpisodeStore

# ---------------------------------------------------------------------------
# Context budget (tokens, approximate)
# ---------------------------------------------------------------------------

BUDGET = {
    "l0_identity": int(os.getenv("MEMORY_BUDGET_L0", "200")),
    "l1_profile": int(os.getenv("MEMORY_BUDGET_L1", "800")),
    "l2_episodes": int(os.getenv("MEMORY_BUDGET_L2_EPISODES", "1500")),
    "l2_graph": int(os.getenv("MEMORY_BUDGET_L2_GRAPH", "500")),
    "l2_memories": int(os.getenv("MEMORY_BUDGET_L2_MEMORIES", "1000")),
}


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token)."""
    return len(text) // 4


def _truncate_to_budget(text: str, budget_tokens: int) -> str:
    """Truncate text to fit within a token budget."""
    max_chars = budget_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...(truncated)"


# ---------------------------------------------------------------------------
# L0: Identity
# ---------------------------------------------------------------------------

class L0Identity:
    """Always-loaded persona and behavioral instructions.

    Reads SOUL.md and IDENTITY.md from the workspace directory.
    ~200 tokens, loaded once per session.
    """

    def __init__(self, workspace_dir: str | Path | None = None):
        if workspace_dir is None:
            workspace_dir = Path(__file__).parent.parent.parent / "workspace"
        self._workspace = Path(workspace_dir)
        self._cache: str | None = None

    def render(self) -> str:
        """Return identity text."""
        if self._cache is not None:
            return self._cache

        parts = []
        for filename in ("SOUL.md", "IDENTITY.md"):
            path = self._workspace / filename
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                parts.append(f"## {filename}\n{content}")

        self._cache = "\n\n".join(parts) if parts else ""
        return self._cache

    def invalidate(self) -> None:
        """Clear cached identity (call after workspace changes)."""
        self._cache = None


# ---------------------------------------------------------------------------
# L1: User Profile
# ---------------------------------------------------------------------------

class L1UserProfile:
    """Always-loaded user profile — key facts, emotional state, active arcs.

    Built fresh per message from:
    - Semantic memories (high-confidence facts about this user)
    - Recent episode emotional tones (last 3-5 episodes)
    - Active narrative arcs

    ~800 tokens.
    """

    def render(
        self,
        user_id: str,
        semantic_memories: list[dict[str, Any]] | None = None,
        recent_episodes: list[dict[str, Any]] | None = None,
        active_arcs: list[dict[str, Any]] | None = None,
        graph_context: list[dict[str, Any]] | None = None,
    ) -> str:
        """Build user profile text.

        Args:
            user_id: The user to build profile for.
            semantic_memories: Key facts/preferences from Palace.
            recent_episodes: Last few episodes (for emotional trajectory).
            active_arcs: Currently active narrative arcs.
            graph_context: Key relationships from knowledge graph.

        Returns:
            Formatted profile text, truncated to budget.
        """
        sections = []

        # Key facts
        if semantic_memories:
            facts = []
            for mem in semantic_memories[:10]:
                text = mem.get("memory", mem.get("data", ""))
                if text:
                    facts.append(f"- {text}")
            if facts:
                sections.append("**Key facts:**\n" + "\n".join(facts))

        # Emotional trajectory
        if recent_episodes:
            tones = []
            for ep in recent_episodes[:5]:
                tone = ep.get("emotional_tone", "neutral")
                summary = ep.get("summary", "")
                ts = ep.get("timestamp", "")
                if isinstance(ts, datetime):
                    ts = ts.strftime("%b %d")
                tones.append(f"- {ts}: {tone} — {summary}")
            if tones:
                sections.append("**Recent emotional trajectory:**\n" + "\n".join(tones))

        # Active arcs
        if active_arcs:
            arcs = []
            for arc in active_arcs[:5]:
                title = arc.get("title", "")
                status = arc.get("status", "active")
                summary = arc.get("summary", "")
                arcs.append(f"- **{title}** ({status}): {summary}")
            if arcs:
                sections.append("**Active arcs:**\n" + "\n".join(arcs))

        # Key relationships
        if graph_context:
            rels = []
            for rel in graph_context[:10]:
                src = rel.get("source", "")
                pred = rel.get("relationship", "")
                dest = rel.get("destination", "")
                rels.append(f"- {src} → {pred} → {dest}")
            if rels:
                sections.append("**Key relationships:**\n" + "\n".join(rels))

        text = "\n\n".join(sections) if sections else ""
        return _truncate_to_budget(text, BUDGET["l1_profile"])


# ---------------------------------------------------------------------------
# L2: Relevant Context
# ---------------------------------------------------------------------------

class L2RelevantContext:
    """Per-message context — episodes, graph relations, and memories relevant
    to the current conversation.

    Loaded via semantic search on the current message.
    ~3000 tokens total across sub-sections.
    """

    def render(
        self,
        relevant_episodes: list[dict[str, Any]] | None = None,
        relevant_memories: list[dict[str, Any]] | None = None,
        relevant_relations: list[dict[str, Any]] | None = None,
    ) -> str:
        """Build relevant context text.

        Args:
            relevant_episodes: Episodes matching current conversation topic.
            relevant_memories: Semantic memories matching current message.
            relevant_relations: Graph relations relevant to current topic.

        Returns:
            Formatted context text, budget-constrained.
        """
        sections = []

        # Relevant episodes (verbatim conversation chunks)
        if relevant_episodes:
            ep_parts = []
            budget_remaining = BUDGET["l2_episodes"]
            for ep in relevant_episodes:
                summary = ep.get("summary", "")
                content = ep.get("content", "")
                tone = ep.get("emotional_tone", "")
                ts = ep.get("timestamp", "")
                if isinstance(ts, datetime):
                    ts = ts.strftime("%b %d, %I:%M %p")

                # Use summary if content would blow budget, full content if it fits
                entry_text = f"**[{ts}, {tone}]** {summary}"
                if content and _estimate_tokens(content) < budget_remaining:
                    entry_text = f"**[{ts}, {tone}]**\n{content}"

                entry_tokens = _estimate_tokens(entry_text)
                if entry_tokens > budget_remaining:
                    break
                budget_remaining -= entry_tokens
                ep_parts.append(entry_text)

            if ep_parts:
                sections.append(
                    "**Relevant past conversations:**\n" + "\n\n".join(ep_parts)
                )

        # Relevant semantic memories
        if relevant_memories:
            mem_parts = []
            budget_remaining = BUDGET["l2_memories"]
            for mem in relevant_memories:
                text = mem.get("memory", mem.get("data", ""))
                if not text:
                    continue
                entry = f"- {text}"
                entry_tokens = _estimate_tokens(entry)
                if entry_tokens > budget_remaining:
                    break
                budget_remaining -= entry_tokens
                mem_parts.append(entry)

            if mem_parts:
                sections.append("**Relevant memories:**\n" + "\n".join(mem_parts))

        # Relevant graph relations
        if relevant_relations:
            rel_parts = []
            for rel in relevant_relations[:15]:
                src = rel.get("source", "")
                pred = rel.get("relationship", "").replace("_", " ")
                dest = rel.get("destination", "")
                rel_parts.append(f"- {src} → {pred} → {dest}")

            if rel_parts:
                text = "\n".join(rel_parts)
                sections.append(
                    "**Relevant relationships:**\n"
                    + _truncate_to_budget(text, BUDGET["l2_graph"])
                )

        return "\n\n".join(sections) if sections else ""


# ---------------------------------------------------------------------------
# Assembled Retrieval
# ---------------------------------------------------------------------------

class LayeredRetrieval:
    """Orchestrates all retrieval layers into a single context block.

    Usage:
        retrieval = LayeredRetrieval()
        context = retrieval.build_context(
            user_id="discord-123",
            current_message="How's the job search going?",
            episode_store=episode_store,
            ...
        )
    """

    def __init__(self, workspace_dir: str | Path | None = None):
        self.l0 = L0Identity(workspace_dir)
        self.l1 = L1UserProfile()
        self.l2 = L2RelevantContext()

    def build_context(
        self,
        user_id: str,
        # L1 data
        semantic_memories: list[dict[str, Any]] | None = None,
        recent_episodes: list[dict[str, Any]] | None = None,
        active_arcs: list[dict[str, Any]] | None = None,
        graph_context: list[dict[str, Any]] | None = None,
        # L2 data
        relevant_episodes: list[dict[str, Any]] | None = None,
        relevant_memories: list[dict[str, Any]] | None = None,
        relevant_relations: list[dict[str, Any]] | None = None,
    ) -> str:
        """Build the full layered context for a message.

        Returns a single string with all layers concatenated, each
        section labeled and budget-constrained.
        """
        parts = []

        # L0: Identity (always)
        l0_text = self.l0.render()
        if l0_text:
            parts.append(l0_text)

        # L1: User Profile (always, per-user)
        l1_text = self.l1.render(
            user_id=user_id,
            semantic_memories=semantic_memories,
            recent_episodes=recent_episodes,
            active_arcs=active_arcs,
            graph_context=graph_context,
        )
        if l1_text:
            parts.append(f"## About this user\n{l1_text}")

        # L2: Relevant Context (per-message)
        l2_text = self.l2.render(
            relevant_episodes=relevant_episodes,
            relevant_memories=relevant_memories,
            relevant_relations=relevant_relations,
        )
        if l2_text:
            parts.append(f"## Context for this conversation\n{l2_text}")

        return "\n\n".join(parts)
