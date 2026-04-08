# Decouple Memory Logic Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Consolidate all scattered memory/Rook logic into `mypalclara/core/memory/` before a major rewrite.

**Architecture:** Move 9 files from `core/` into `core/memory/`, move constants from `memory_manager.py` to `memory/config.py`, update all imports. No behavior changes — pure file reorganization.

**Tech Stack:** Python, poetry, ruff

---

### Task 1: Move constants and utility from `memory_manager.py` to `memory/config.py`

Must happen FIRST to break circular dependencies (moved modules import these constants).

**Files:**
- Modify: `mypalclara/core/memory/config.py` — add constants
- Modify: `mypalclara/core/memory_manager.py` — remove constants, import from new location

**Constants to move:**
- `CONTEXT_MESSAGE_COUNT`, `CHANNEL_CONTEXT_COUNT`, `SUMMARY_INTERVAL`
- `MAX_SEARCH_QUERY_CHARS`, `MAX_KEY_MEMORIES`, `MAX_MEMORIES_PER_TYPE`, `MAX_GRAPH_RELATIONS`
- `FSRS_SEMANTIC_WEIGHT`, `FSRS_DYNAMICS_WEIGHT`
- `SMART_INGEST_SKIP_THRESHOLD`, `SMART_INGEST_UPDATE_THRESHOLD`, `SMART_INGEST_SUPERSEDE_THRESHOLD`
- `MEMORY_CONTEXT_SLICE`, `THREAD_SUMMARY_MAX_MESSAGES`
- `MEMORY_ACCESS_LOG_RETENTION_DAYS`, `PRUNE_CHECK_FREQUENCY`
- `_format_message_timestamp()` function

**Importers to update (change `from mypalclara.core.memory_manager import X` to `from mypalclara.core.memory.config import X`):**
- `mypalclara/core/memory_retriever.py` — MAX_GRAPH_RELATIONS, MAX_KEY_MEMORIES, MAX_MEMORIES_PER_TYPE, MAX_SEARCH_QUERY_CHARS
- `mypalclara/core/memory_writer.py` — MEMORY_CONTEXT_SLICE
- `mypalclara/core/memory_ingestion.py` — SMART_INGEST_*
- `mypalclara/core/memory_dynamics_manager.py` — FSRS_*, MEMORY_ACCESS_LOG_RETENTION_DAYS, PRUNE_CHECK_FREQUENCY
- `mypalclara/core/session_manager.py` — CONTEXT_MESSAGE_COUNT, SUMMARY_INTERVAL, THREAD_SUMMARY_MAX_MESSAGES, _format_message_timestamp
- `mypalclara/core/prompt_builder.py` — _format_message_timestamp

**Verify:** `poetry run ruff check mypalclara/core/memory/config.py mypalclara/core/memory_manager.py`

---

### Task 2: Move Tier 1 files (6 pure memory modules)

Move files and update their internal imports to reference each other at new paths.

| Old Path | New Path |
|----------|----------|
| `core/memory_retriever.py` | `core/memory/retrieval.py` |
| `core/memory_writer.py` | `core/memory/writer.py` |
| `core/memory_ingestion.py` | `core/memory/ingestion.py` |
| `core/memory_dynamics_manager.py` | `core/memory/dynamics/manager.py` |
| `core/emotional_context.py` | `core/memory/context/emotional.py` |
| `core/topic_recurrence.py` | `core/memory/context/topics.py` |

**Create directories:**
- `mypalclara/core/memory/context/` (with `__init__.py`)

**Update internal cross-imports within moved files:**
- `memory_writer.py` imports from `memory_ingestion` and `memory_dynamics_manager`
- `memory_ingestion.py` imports from `memory_dynamics_manager`
- `memory_retriever.py` imports constants (already moved in Task 1)

---

### Task 3: Move Tier 3 files (3 borderline modules)

| Old Path | New Path |
|----------|----------|
| `core/session_manager.py` | `core/memory/session.py` |
| `core/intention_manager.py` | `core/memory/intentions.py` |
| `core/personality_evolution.py` | `core/memory/personality.py` |

---

### Task 4: Update all external imports

**Files that import from moved modules (update paths):**
- `mypalclara/core/memory_manager.py` — imports all 9 classes
- `mypalclara/core/prompt_builder.py` — imports topic_recurrence
- `mypalclara/gateway/session.py` — imports emotional_context, topic_recurrence
- `mypalclara/gateway/processor.py` — imports personality_evolution
- `mypalclara/services/proactive/engine.py` — imports emotional_context
- `tests/clara_core/test_privacy_filtered_fetch.py` — imports MemoryRetriever

---

### Task 5: Update `memory/__init__.py` exports and delete `config/rook.py`

- Update `mypalclara/core/memory/__init__.py` to re-export key classes
- Delete `mypalclara/config/rook.py` (just re-exports)
- Update `mypalclara/config/__init__.py` if needed

---

### Task 6: Verify

- `poetry run ruff check mypalclara/`
- `poetry run pytest tests/clara_core/ -v`
- Import smoke test
