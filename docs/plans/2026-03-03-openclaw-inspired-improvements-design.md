# OpenClaw-Inspired Improvements — Design Document

**Date:** 2026-03-03
**Status:** Approved
**Approach:** Layered Integration (features implemented independently, dependency-ordered)

## Overview

Eight features inspired by OpenClaw's architecture, adapted to MyPalClara's existing patterns. Each feature is independently testable and shippable. Implementation order respects the dependency graph between features.

## Motivation

OpenClaw demonstrates several patterns that address real gaps in MyPalClara:

1. **No tool loop protection** — An LLM stuck in a tool loop burns tokens indefinitely
2. **Blind tool result truncation** — 50K char hard cut loses structure and context
3. **Zero provider failover** — A single API error takes down the entire system
4. **Monolithic prompt builder** — No budget management, no modes, no section composition
5. **No user-customizable persona** — Personality hardcoded in config, not user-editable
6. **Drop-oldest context trimming** — Loses important context without summarization
7. **Single-agent architecture** — No way to decompose complex tasks into parallel sub-work
8. **No high-level tool orientation** — LLM sees raw schemas without a human-readable map

## Implementation Order

```
1. Tool Loop Detection          (independent)
2. Tool Result Size Capping     (independent)
3. Provider Failover            (independent)
4. Compositional Prompt Builder (foundation for 5, 6, 8)
5. Workspace Files              (depends on 4)
   Tool Summaries in Prompts    (depends on 4, parallel with 5)
6. Context Compaction           (depends on 4, uses 3)
7. Subagent Orchestration       (depends on 1, 2, 4)
```

---

## Feature 1: Tool Loop Detection

**New file:** `mypalclara/core/tool_guard.py`
**Modified:** `mypalclara/gateway/llm_orchestrator.py`

### Design

`ToolLoopGuard` maintains a sliding window of the last 30 tool calls (SHA-256 hash of `tool_name + json(args)`). Four detection mechanisms:

| Mechanism | Trigger | Action |
|-----------|---------|--------|
| Generic Repeat | 10+ identical calls | WARN (inject warning), 30+ = STOP |
| Poll No Progress | 5+ calls with identical result hash | STOP |
| Ping-Pong | 2+ full A-B-A-B cycles | STOP |
| Circuit Breaker | 30+ identical no-progress calls (global) | STOP |

### API

```python
class ToolLoopGuard:
    def check(self, tool_name: str, args: dict) -> LoopCheckResult
    def record_result(self, tool_name: str, args: dict, result: str) -> None
    def reset(self) -> None

class LoopCheckResult:
    action: LoopAction  # ALLOW | WARN | STOP
    reason: str | None
    pattern: str | None  # "generic_repeat" | "poll_no_progress" | "ping_pong" | "circuit_breaker"
```

### Integration

In `LLMOrchestrator.generate_with_tools()`, before each tool execution:

```python
check = self._loop_guard.check(tool_name, args)
if check.action == LoopAction.STOP:
    # Inject system message explaining the loop, force response without tools
    ...
elif check.action == LoopAction.WARN:
    # Inject warning message, continue
    ...
```

After each tool result: `self._loop_guard.record_result(tool_name, args, result_str)`

---

## Feature 2: Tool Result Size Capping

**New file:** `mypalclara/core/tool_result_guard.py`
**Modified:** `mypalclara/gateway/llm_orchestrator.py`

### Design

Validates and caps all tool results before they enter the message history. Replaces the existing hard truncation at 50K chars.

**Truncation strategies by content type:**

| Content Type | Strategy |
|-------------|----------|
| JSON | Preserve structure, truncate array elements from middle (keep first 3 + last 2) |
| Text | 70/20 split: 70% head + 20% tail with marker in between |
| Error | Never truncate |

**Truncation marker:** `\n...[truncated: kept {head_chars}+{tail_chars} of {total_chars} chars]...\n`

### API

```python
class ToolResultGuard:
    def __init__(self, max_chars: int = 50_000):
        ...

    def cap(self, tool_name: str, tool_call_id: str, result: str) -> CappedResult:
        """Cap result size with intelligent truncation."""
        ...

class CappedResult:
    content: str
    was_truncated: bool
    original_size: int
    strategy: str  # "json" | "text_70_20" | "none"
```

### Additional Features

- **Tool name normalization:** Validate against registry, fallback to `"unknown_tool"` with warning
- **Pending state tracking:** Track tool calls awaiting results, warn on unexpected result IDs

### Integration

In `LLMOrchestrator`, replace existing truncation with:

```python
capped = self._result_guard.cap(tool_name, call_id, raw_result)
if capped.was_truncated:
    logger.info(f"Tool result truncated: {capped.strategy}, {capped.original_size} -> {len(capped.content)}")
```

---

## Feature 3: Model Fallback with Cooldown Classification

**New file:** `mypalclara/core/llm/failover.py`
**Modified:** `mypalclara/core/llm/providers/registry.py`, `mypalclara/core/llm/config.py`

### Failure Classification

| Type | Detection | Response |
|------|-----------|----------|
| Auth/Billing | HTTP 401, 403, payment errors | Cooldown entire provider (10 min), skip to next |
| Rate Limit | HTTP 429, retry-after | Backoff (1s-2s-4s-8s + jitter), try sibling model |
| Context Overflow | Context length exceeded | Rethrow immediately |
| Transient | HTTP 500, 502, 503, timeout, connection error | Retry same model, max 3 attempts with backoff |
| Unknown | Anything else | Treat as transient, log for investigation |

### Fallback Chain

```python
@dataclass
class LLMConfig:
    # ... existing fields ...
    fallback_configs: list[LLMConfig] = field(default_factory=list)
```

Chain: primary -> retry with backoff -> fallback 1 -> fallback 2 -> raise

### Cooldown Management

```python
class CooldownManager:
    """Track provider/model cooldowns with expiry timestamps."""
    def is_cooled_down(self, provider: str, model: str | None = None) -> bool
    def set_cooldown(self, provider: str, model: str | None, duration_s: float, reason: FailoverReason) -> None
    def clear(self, provider: str, model: str | None = None) -> None
```

- Auth failures: 10-minute cooldown on entire provider
- Rate limits: 30-second cooldown on specific model
- Probe throttling: test primary at 30s intervals after cooldown expires

### ResilientProvider Wrapper

```python
class ResilientProvider(LLMProvider):
    """Wraps a provider chain with retry, backoff, and failover."""
    def __init__(self, primary: LLMProvider, fallbacks: list[LLMProvider], cooldowns: CooldownManager):
        ...

    async def complete(self, messages, **kwargs) -> str:
        # Try primary, classify failures, failover as needed
        ...
```

### Integration

`ProviderRegistry.get_provider()` wraps returned provider in `ResilientProvider`. Transparent to all callers. `compat.py` functions work unchanged.

### Configuration

```bash
LLM_FALLBACK_1_PROVIDER=openrouter
LLM_FALLBACK_1_MODEL=anthropic/claude-sonnet-4
LLM_FALLBACK_2_PROVIDER=openai
LLM_FALLBACK_2_MODEL=gpt-4o
```

---

## Feature 4: Compositional Prompt Builder

**Modified:** `mypalclara/core/prompt_builder.py`

### Three Prompt Modes

| Mode | Sections | Use Case |
|------|----------|----------|
| `"full"` | All sections | Primary agent |
| `"minimal"` | Identity + tools + workspace + runtime | Sub-agents |
| `"none"` | Single identity line | Lightweight calls (tier classification, memory extraction) |

### Section Builder Pattern

Each section is a method returning `list[str]`. Empty list = section skipped.

```python
class PromptBuilder:
    def build_prompt(self, ..., mode: PromptMode = "full") -> list[Message]:
        sections = [
            self._build_identity(),          # 1. WORM persona (always)
            self._build_tooling(),           # 2. Tool summaries
            self._build_tool_call_style(),   # 3. Narration guidelines
            self._build_safety(),            # 4. Injection defense
            self._build_skills(),            # 5. Loaded skills (conditional)
            self._build_memory_recall(),     # 6. Memory instructions (conditional)
            self._build_workspace(),         # 7. Working directory
            self._build_authorized_users(),  # 8. Owner identity
            self._build_datetime(),          # 9. Timezone-aware timestamp
            self._build_workspace_files(),   # 10. Context files (Section 5)
            self._build_messaging(),         # 11. Platform instructions (conditional)
            self._build_runtime(),           # 12. Metadata line
        ]
        # Filter by mode, join, apply budgets
        ...
```

### Per-Section Budgets

- Default per-section: 10K chars
- Workspace files: 20K per file, 150K total
- Total system prompt: configurable, default 200K chars
- Over-budget sections trimmed with 70/20 strategy

### Invariants Preserved

- WORM persona remains outermost layer of section 1
- `<untrusted_*>` sandboxing unchanged
- Typed `Message` objects returned
- Gateway processor index-1/index-2 injection still works

---

## Feature 5: Workspace Files

**New files:** `mypalclara/core/workspace_loader.py`, `mypalclara/workspace/*.md` (templates)
**Modified:** `mypalclara/core/prompt_builder.py`, `mypalclara/config/bot.py`

### File Set

| File | Purpose | Replaces |
|------|---------|----------|
| `SOUL.md` | Personality, tone, behavior | `config/personality.md` |
| `IDENTITY.md` | Structured: name, emoji, vibe, avatar | `BOT_NAME`/`PERSONALITY` in `config/bot.py` |
| `USER.md` | User profile: name, timezone, prefs | Scattered Rook user memories |
| `AGENTS.md` | Core behavioral instructions | Universal instructions in `personality.md` |
| `TOOLS.md` | User-specific tool config notes | New capability |
| `MEMORY.md` | Long-term curated memory (human-editable) | New capability |

### WorkspaceLoader API

```python
class WorkspaceLoader:
    def load(self, workspace_dir: Path, mode: str = "full") -> list[WorkspaceFile]:
        """Load workspace files with budget management."""
        ...

class WorkspaceFile:
    filename: str
    content: str
    was_truncated: bool
    structured_fields: dict | None  # Parsed from IDENTITY.md
```

### Budget Management

- Per-file: 20K chars
- Total: 150K chars
- 70/20 truncation with marker: `[...truncated, see {filename} for full content...]`
- Minimal mode loads: SOUL.md, IDENTITY.md, USER.md, AGENTS.md only

### Workspace Resolution

- Single-user: `~/.clara/workspace/`
- Multi-user: `data/workspaces/{user_id}/`
- Default templates seeded from `mypalclara/workspace/` on first access
- `IDENTITY.md` structured fields override `config/bot.py` when present

### Migration

- `config/personality.md` becomes default `SOUL.md` template
- `config/bot.py` constants become fallbacks when `IDENTITY.md` absent
- No breaking changes

---

## Feature 6: Context Compaction (Progressive Summarization)

**New file:** `mypalclara/core/context_compactor.py`
**Modified:** `mypalclara/gateway/llm_orchestrator.py`

### Compaction Pipeline

1. **Token estimation** — `count_tokens()` across all messages
2. **Budget check** — Skip if history < 60% of context window
3. **Chunk selection** — Keep recent 40% untouched, older 60% is compaction candidate
4. **Multi-stage summarization:**
   - Split candidates into N chunks (adaptive chunk size)
   - Summarize each with low-tier model (cheap/fast)
   - Merge summaries preserving: active tasks, last user request, decisions + rationale, open questions, file paths/URLs/identifiers
5. **Injection** — Replace compacted messages with `SystemMessage("## Conversation Summary\n{summary}")`

### API

```python
class ContextCompactor:
    def __init__(self, llm_config: LLMConfig, max_context_ratio: float = 0.6):
        ...

    async def compact_if_needed(self, messages: list[Message], budget_tokens: int) -> CompactionResult:
        ...

class CompactionResult:
    messages: list[Message]  # The new message list
    was_compacted: bool
    tokens_saved: int
    summary_tokens: int
```

### Security

- Tool result content stripped before summarization LLM call
- `<untrusted_*>` tags preserved in summaries
- Oversized messages (>30% context window) excluded, noted as `[Large message (~{n}K tokens) omitted]`

### Safety

- 20% buffer on token estimates (tiktoken vs actual tokenizer variance)
- Existing drop-oldest trimming preserved as fallback if summarization fails

### Integration

`LLMOrchestrator` calls `compactor.compact_if_needed()` before each LLM call.

---

## Feature 7: Subagent Orchestration (LLM-Driven)

**New files:** `mypalclara/core/subagent/registry.py`, `mypalclara/core/subagent/tools.py`, `mypalclara/core/subagent/runner.py`
**Modified:** `mypalclara/gateway/llm_orchestrator.py`, plugin registration

### Tools Exposed to LLM

| Tool | Args | Description |
|------|------|-------------|
| `subagent_spawn` | `task: str, model_tier: str?, tools: list[str]?` | Create sub-agent with task |
| `subagent_list` | (none) | Show active/recent sub-agents |
| `subagent_kill` | `id: str \| "all"` | Terminate sub-agent(s) |
| `subagent_steer` | `id: str, instruction: str` | Send corrective instruction |

### SubagentRunRecord

```python
@dataclass
class SubagentRunRecord:
    id: str                    # uuid
    session_key: str           # agent:{parent_id}:sub:{uuid}
    task: str
    status: SubagentStatus     # RUNNING | COMPLETED | FAILED | KILLED
    model_tier: str
    token_usage: int
    start_time: float
    result_summary: str | None
    tool_subset: list[str] | None
```

### Execution Model

- Each sub-agent gets own `LLMOrchestrator` with own tool loop
- Uses `"minimal"` prompt mode
- Inherits parent's `ToolExecutor` (same tools, same circuit breakers)
- Runs as asyncio task in gateway event loop
- **1 level deep only** — sub-agents cannot spawn sub-agents
- Hard timeout: 10 minutes (configurable)

### Steer Mechanism

- Instruction injected as `SystemMessage` into sub-agent's history before next LLM call
- Rate limited: min 2s between steers to same sub-agent
- Idempotency key prevents duplicate injections

### Security

- Sub-agents inherit parent's `PolicyEngine` restrictions
- Tool subset can be narrowed at spawn, never expanded
- WORM persona applied via minimal prompt mode

### Integration

- Tools registered via `PluginRegistry`
- Sub-agent events forwarded to parent's event stream
- `ToolLoopGuard` (Feature 1) and `ToolResultGuard` (Feature 2) apply per sub-agent independently

---

## Feature 8: Human-Readable Tool Summaries in Prompts

**New file:** `mypalclara/core/tool_summaries.py`
**Modified:** `mypalclara/core/prompt_builder.py`
**Replaces:** `build_capability_inventory()` in `core/security/worm_persona.py`

### Design

Concise tool listing injected into system prompt section 2, supplementing native API tool schemas.

```python
def build_tool_summary_section(tools: list[ToolSchema], max_chars: int = 5000) -> list[str]:
    """Build human-readable tool summary for system prompt."""
    ...
```

### Output Format

```
## Available Tools
Tool names are case-sensitive. Call tools exactly as listed.

Core Tools:
- memory_search: Search user/project memories by semantic query
- web_search: Search the web (Tavily)
- code_execute: Run code in sandboxed container

MCP Tools:
- mcp__github__list_issues: List GitHub issues

Subagent Tools:
- subagent_spawn: Create a sub-agent for a task
```

### Grouping and Ordering

- Tools grouped by name prefix (core, MCP by server, subagent)
- Core tools ordered by frequency (read/write/search/execute first)
- Plugin tools alphabetical
- Per-tool description: first sentence, capped at 80 chars

### Budget

- Total section capped at `max_chars` (default 5K)
- Over budget: drop least-common groups, append `"...and {n} more tools available"`

---

## Files Summary

| Feature | New Files | Modified Files |
|---------|-----------|----------------|
| 1. Tool Loop Detection | `core/tool_guard.py` | `gateway/llm_orchestrator.py` |
| 2. Tool Result Capping | `core/tool_result_guard.py` | `gateway/llm_orchestrator.py` |
| 3. Provider Failover | `core/llm/failover.py` | `core/llm/providers/registry.py`, `core/llm/config.py` |
| 4. Prompt Builder | — (refactor) | `core/prompt_builder.py` |
| 5. Workspace Files | `core/workspace_loader.py`, `workspace/*.md` | `core/prompt_builder.py`, `config/bot.py` |
| 6. Context Compaction | `core/context_compactor.py` | `gateway/llm_orchestrator.py` |
| 7. Subagent Orchestration | `core/subagent/registry.py`, `core/subagent/tools.py`, `core/subagent/runner.py` | `gateway/llm_orchestrator.py`, plugin registration |
| 8. Tool Summaries | `core/tool_summaries.py` | `core/prompt_builder.py` |

## Testing Strategy

Each feature gets unit tests. Integration test for full pipeline after all features land. Sub-agent orchestration gets an additional integration test exercising spawn/steer/kill lifecycle.
