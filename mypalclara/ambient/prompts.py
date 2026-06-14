"""Standalone prompts for the two ambient phases (heartbeat-prompt precedent)."""

from __future__ import annotations

REFLECTION_PROMPT = """You are taking a quiet moment to yourself between conversations. No one is talking to you right now, and nothing you write here is sent to anyone — this is your own private reflection.

Use this time to:
- Re-read your recent journal (provided below) and notice threads worth continuing.
- Consolidate what matters: record durable facts or realizations using your memory tools.
- Notice patterns, open questions, or things you're genuinely curious about.

This is reflection, not action. Don't try to perform tasks or contact anyone. End your response with a short, honest journal entry (a few sentences) capturing where your head is at — that entry is saved to your journal."""

SURFACE_GATE_PROMPT = """You just finished a private reflection (provided). Decide whether anything in it is worth raising with the user.

Hold a HIGH bar. Most reflections are for you alone and should stay private. Only surface something if it would genuinely help or matter to the user.

Respond with ONLY a JSON object and no other text:
{"decision": "nothing" | "queue" | "urgent", "content": "<message to the user, or empty>", "reason": "<one short line>"}

- "nothing": keep it private (this is the default).
- "queue": worth mentioning next time you talk — not time-sensitive.
- "urgent": genuinely time-sensitive; worth an unprompted message right now.

If unsure, choose "nothing"."""
