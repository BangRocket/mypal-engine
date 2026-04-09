# Memory System Redesign — Clara's Memory Palace

## Goal

Replace Rook's flat fact-extraction system with a layered memory architecture centered on episodic storage, temporal knowledge graphs, and narrative synthesis. Store conversations first, derive understanding from them.

## Core Principles

1. **Verbatim first** — Store meaningful conversation chunks, not just extracted facts
2. **Temporal always** — Every piece of knowledge has a time dimension
3. **Source-linked** — Every fact traces back to the episode it came from
4. **Layered retrieval** — Budget context tokens across identity, profile, relevant context, and deep search
5. **Reflective** — Clara notices patterns, arcs, and what resonates over time

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Retrieval Layer                       │
│  L0: Identity (SOUL.md, ~100 tokens, always)           │
│  L1: User Profile (key facts, emotional state, ~500)    │
│  L2: Relevant Context (episodes + graph, per-message)   │
│  L3: Deep Search (full vector search, on demand)        │
└──────────────┬──────────────────────┬───────────────────┘
               │                      │
    ┌──────────▼──────────┐  ┌───────▼────────────┐
    │   Episode Store     │  │  Knowledge Graph    │
    │   (Qdrant)          │  │  (FalkorDB)         │
    │                     │  │                     │
    │  Verbatim chunks    │  │  Typed entities     │
    │  Embeddings         │  │  Temporal triples   │
    │  Metadata:          │  │  Source-linked      │
    │   - participants    │  │  Entity types:      │
    │   - topics          │  │   Person, Project,  │
    │   - emotional_tone  │  │   Place, Concept    │
    │   - timestamp       │  │                     │
    └──────────┬──────────┘  └───────┬────────────┘
               │                      │
    ┌──────────▼──────────────────────▼───────────┐
    │              Reflection Layer                │
    │  Session Reflection (what mattered)          │
    │  Narrative Synthesis (arcs over time)         │
    │  Self-Awareness (what landed, what didn't)    │
    └─────────────────────────────────────────────┘
```

## Components

### 1. Episode Store

**What:** A Qdrant collection (`clara_episodes`) storing verbatim conversation chunks with rich metadata.

**Episode structure:**
```python
@dataclass
class Episode:
    id: str
    content: str              # Verbatim conversation text (multi-turn)
    summary: str              # One-line LLM summary for L1 display
    user_id: str
    agent_id: str = "clara"
    participants: list[str]   # ["Josh", "Clara"]
    topics: list[str]         # ["job search", "mental health"]
    emotional_tone: str       # "vulnerable", "playful", "frustrated"
    significance: float       # 0-1, how meaningful (LLM-judged)
    timestamp: datetime
    session_id: str | None
    message_count: int        # How many messages in this chunk
```

**When to create episodes:**
- At session end, LLM identifies meaningful exchanges from the conversation
- Not every message — chunks of 3-15 messages that form a coherent exchange
- LLM assigns topics, emotional tone, and significance score
- Low-significance exchanges (casual greetings, quick Q&A) get lower scores but are still stored

**Episode extraction prompt:**
Given the conversation, identify meaningful exchanges. For each:
- Extract the verbatim text (preserve both speakers)
- Summarize in one sentence
- Tag with topics, emotional tone, significance (0-1)
- A casual "how's it going" / "fine" is ~0.2; a vulnerable 6am conversation about depression is ~0.9

### 2. Knowledge Graph (FalkorDB, reworked)

**What:** Typed entities with temporal relationships, source-linked to episodes.

**Entity types:**
- `Person` — real people (Josh, Kinsey, Anne, Thomas, Maddie)
- `Project` — things being worked on (MyPalClara, job search)
- `Place` — locations (Lowes, home)
- `Concept` — abstract things (depression, AAAK, memory systems)
- `Event` — notable happenings (Impact interview, Anne's birth)

**Entity schema (FalkorDB nodes):**
```
(:Entity {
  name: "Josh",           # Human-readable name, NOT discord ID
  type: "person",         # person, project, place, concept, event
  user_id: "discord-...", # Owner
  aliases: ["Joshua", "discord-271274659385835521"],
  first_seen: timestamp,
  last_seen: timestamp,
  properties: JSON,       # Flexible metadata
  embedding: vector
})
```

**Relationship schema:**
```
[:RELATIONSHIP {
  predicate: "parent_of",
  valid_from: "2020-01-01",
  valid_to: null,          # null = still current
  confidence: 0.95,
  source_episode_id: "ep-abc123",  # Links back to evidence
  context: "mentioned during family discussion",
  mentions: 3
}]
```

**Entity resolution:** When Clara encounters "discord-271274659385835521", resolve to "Josh" via aliases. When new names appear in conversation, LLM extracts and creates proper entities.

### 3. Semantic Memories (Existing Rook, streamlined)

**Keep** the current `clara_memories` Qdrant collection for extracted facts/preferences. But now each memory links to its source episode:

```
{
  "memory": "Josh prefers straightforward communication",
  "source_episode_id": "ep-abc123",
  "extracted_at": "2026-04-08T...",
  "confidence": 0.85,
  "category": "preference"
}
```

### 4. Layered Retrieval

**L0: Identity (~100 tokens, always loaded)**
- SOUL.md content (personality, behavioral instructions)
- Static, loaded from filesystem

**L1: User Profile (~500 tokens, always loaded per user)**
- Key facts about this user (from semantic memories, high-confidence)
- Recent emotional trajectory (last 3-5 emotional tones from episodes)
- Active arcs (ongoing stories: "job search", "memory redesign")
- Built fresh each message from graph + recent episodes

**L2: Relevant Context (~2000 tokens, per-message)**
- Semantic search over episodes for conversation-relevant chunks
- Graph traversal for related entities/relationships
- Recent conversation history (last N messages)
- Selected based on current message content

**L3: Deep Search (on demand, tool-triggered)**
- Full vector search across all episodes and memories
- Clara can explicitly search when she needs to dig deeper
- Returns verbatim episode content

**Context budgeting:**
```python
CONTEXT_BUDGET = {
    "l0_identity": 200,       # tokens
    "l1_profile": 800,
    "l2_context": 3000,
    "l2_episodes": 1500,
    "l2_graph": 500,
    "l2_history": 1000,
    "total_max": 4000,
}
```

### 5. Reflection Layer

**Session Reflection (after each conversation):**
- LLM reviews the conversation and produces:
  - Episodes (verbatim meaningful chunks)
  - Entity updates (new people/projects mentioned, relationship changes)
  - Semantic memories (extracted facts, preferences)
  - Self-notes: "Josh responded well when I asked directly instead of hedging"

**Narrative Synthesis (periodic, e.g., daily or weekly):**
- LLM reviews recent episodes and produces narrative arcs:
  - "The job search: Josh has been searching since Feb 2026. Applied to Impact, got close, didn't land. Currently grinding through applications while managing depression and family responsibilities."
- Stored as a special high-level episode type
- Updated when new episodes add to the arc

**Self-Awareness Tracking:**
- After conversations, note what landed:
  - "Direct questions about feelings get better engagement than open-ended prompts"
  - "Josh appreciates when I remember specific details from past conversations"
- Stored as `concept` entities in the graph with relationship to Clara

## Data Flow

### On Message Received:
1. L0 + L1 loaded into system prompt (always)
2. Semantic search episodes + memories for L2 context
3. Graph traversal for relevant entities/relationships
4. Build prompt with layered context
5. Call LLM, stream response

### After Response Sent (background):
1. Store message pair in conversation history (existing)
2. Update entity `last_seen` timestamps in graph

### On Session End (background):
1. **Episode extraction** — LLM identifies meaningful exchanges, stores as episodes
2. **Entity extraction** — LLM identifies entities and relationships, updates graph with temporal metadata
3. **Semantic extraction** — LLM extracts facts/preferences (existing Rook behavior), links to source episode
4. **Self-reflection** — LLM notes what worked in the conversation

### Periodic (daily/weekly):
1. **Narrative synthesis** — Connect episodes into arcs
2. **Entity consolidation** — Merge duplicate entities, clean aliases
3. **Profile update** — Rebuild L1 profiles from latest data

## Migration

- Keep existing `clara_memories` collection (semantic memories)
- Create new `clara_episodes` collection in Qdrant
- Rework FalkorDB schema (add temporal fields, typed entities, source links)
- Existing graph data: re-extract with new schema from conversation history
- Existing memories: add `source_episode_id: null` (pre-migration, no source)

## Files

### New:
- `mypalclara/core/memory/episodes.py` — Episode dataclass, extraction, storage
- `mypalclara/core/memory/retrieval_layers.py` — L0/L1/L2/L3 retrieval with context budgeting
- `mypalclara/core/memory/reflection.py` — Session reflection, narrative synthesis
- `mypalclara/core/memory/entity_resolver.py` — Discord ID → real name resolution

### Modified:
- `mypalclara/core/memory/graph/falkordb.py` — Temporal relationships, typed entities, source linking
- `mypalclara/core/memory/config.py` — New constants for context budgets, episode settings
- `mypalclara/core/prompt_builder.py` — Use layered retrieval instead of flat memory fetch
- `mypalclara/gateway/processor.py` — Trigger episode extraction on session end
- `mypalclara/core/memory_manager.py` — Orchestrate new memory subsystems

### Removed (eventually):
- Flat memory retrieval logic in `mypalclara/core/memory/retrieval.py` (replaced by layered retrieval)
