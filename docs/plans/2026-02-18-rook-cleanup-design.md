# Rook Memory System Cleanup

## Context

Clara's memory system ("Rook") in `clara_core/memory/` evolved from a vendored copy of mem0. The vendored code was replaced with a native implementation (~3,500 lines across 28 files), but the architecture still carries mem0's abstractions — factory patterns for single implementations, unused API surface, stale naming, and dead providers.

This is a "trim the fat" cleanup: remove dead code, collapse unnecessary abstractions, clean up naming. No behavioral changes, no architecture redesign.

## Current State

**What ROOK does in production** (4 methods):
- `ROOK.add()` — 2-pass LLM pipeline: extract facts, compare against existing, execute ADD/UPDATE/DELETE/NONE
- `ROOK.search()` — semantic vector search
- `ROOK.get_all()` — list memories with filters
- `ROOK.delete_all()` — bulk cleanup (scripts only)

**Also used internally:**
- `ROOK.delete()` — called by `memory_ingestion.py` for post-ingest deduplication
- `ROOK.history()` — audit trail writes happen inside `add()`, method exists for future reads

**~6,200 lines across 28 files** for a 4-method public API + 2 internal-use methods.

## Design

### 1. Dead Code Removal

**Delete files:**
- `clara_core/memory/llm/openai.py` — dead LLM provider, config always uses "unified"
- `clara_core/memory/llm/anthropic.py` — dead LLM provider

**Remove unused public methods from `ClaraMemory`:**
- `get(memory_id)` — zero callers. Retrieval uses `search()` and `get_all()`.
- `update(memory_id, data)` — zero callers. Updates happen inside `add()` via private `_update_memory()`.
- `feedback(memory_id, feedback)` — caller in `memory_dynamics_manager.py:206` but the method is a no-op (logs and returns). Remove method AND call site together.
- `reset()` — zero callers.

**Keep:**
- `delete(memory_id)` — has a real caller in `memory_ingestion.py:76`
- `history(memory_id)` — audit trail, Joshua wants to build on this
- All private methods (`_create_memory`, `_update_memory`, `_delete_memory`) — called internally by `add()`
- `delete_all()` — used by cleanup scripts

### 2. Collapse LLM Factory

`LlmFactory` supports 3 providers but config.py hardcodes `"provider": "unified"` every time. The "unified" provider delegates to `clara_core/llm/` (Clara's main LLM system). The other two providers (openai, anthropic) are being deleted.

**Action:** Remove `LlmFactory` class. Have `ClaraMemory` instantiate `UnifiedLLM` directly from config. Delete `clara_core/memory/llm/factory.py`.

Keep `clara_core/memory/llm/unified.py` (the actual adapter) and `clara_core/memory/llm/base.py` (config classes used by unified).

### 3. Collapse Embeddings Factory

`EmbedderFactory` has exactly one entry: `"openai"`. It's not a factory.

**Action:** Remove `EmbedderFactory` class. Instantiate `OpenAIEmbedding` directly, with the `CachedEmbedding` wrapper logic preserved inline. Move `MockEmbeddings` to test utilities. Delete `clara_core/memory/embeddings/factory.py`.

### 4. Clean Up Naming

**Remove MEM0 code aliases:**
- `MEM0 = ROOK` singleton alias in `config.py`
- `MEM0_PROVIDER`, `MEM0_MODEL`, `MEM0_DATABASE_URL` Python variable aliases
- `Memory = ClaraMemory` alias
- `config/mem0.py` deprecated shim (zero imports found)

**Keep `_get_env("ROOK_X", "MEM0_X")` fallback pattern** one more cycle:
- `.env.docker.example` still uses `MEM0_*` variable names
- Existing deployments may have these set
- Add deprecation warning: `logger.warning("MEM0_PROVIDER is deprecated, use ROOK_PROVIDER")`
- Update `.env.docker.example` to use `ROOK_*` vars

**Rename loggers:** `"mem0"` logger names throughout the codebase become `"rook"` or `"clara.memory"`.

**Clean stale comments:** Remove references to "vendored mem0" in pyproject.toml, config.py, etc.

### 5. Trim `__init__.py` Exports

Current: 30+ symbols exported including factories, utilities, backward compat aliases.

**Keep:** `ROOK`, `ClaraMemory`, `ClaraMemoryItem`, `ClaraMemoryConfig`, `ClaraMemoryValidationError`, `MemoryType`, config values that are actually imported externally (`ENABLE_GRAPH_MEMORY`, `ROOK_PROVIDER`, `ROOK_MODEL`, `ROOK_DATABASE_URL`), `VectorStoreFactory` (used internally by ClaraMemory).

**Remove from exports:** `MEM0*` aliases, `LlmFactory`, `EmbedderFactory`, `Memory`, `MemoryManager` re-export, utility functions (`parse_messages`, `remove_code_blocks`, `extract_json` — these are internal to the memory module).

**Verify before removing:** `MemoryManager` re-export — check if anything imports it through this path.

### 6. Investigate Before Removing

**`_clear_rook_env_vars` / `_restore_env_vars`** (config.py lines 74-96):
- Comment says: "The vendored mem0 auto-detects these env vars and overrides our config!"
- Since vendored mem0 is gone, this may be dead
- BUT: verify `ClaraMemory.from_config()` doesn't still sniff env vars before removing
- If it does, keep the dance. If not, remove it.

## What Stays Untouched

- The `add()` pipeline (2-pass LLM extraction, vector search, action execution)
- Vector store abstraction (`VectorStoreFactory`, Qdrant, pgvector) — real multi-backend need
- `dual_write.py` migration infrastructure
- Graph memory (broken but present — separate problem)
- History/audit storage (`storage.py`, both Postgres and SQLite backends)
- All upstream consumers: MemoryRetriever, MemoryWriter, PromptBuilder, etc.
- ROOK singleton pattern and initialization
- `core/prompts.py`, `core/utils.py`, `core/base.py`

## Estimated Impact

- ~800-1,000 lines removed (dead code + collapsed factories)
- ~200 lines modified (direct instantiation replacing factory calls, naming cleanup)
- Zero behavioral changes — same memories stored, same retrieval, same prompts

## Verification

- `poetry run ruff check .` — clean
- `poetry run pytest` — same pass/fail as before
- Manual: `from clara_core.memory import ROOK; print(ROOK)` — singleton still works
- Manual: verify `ROOK.add()`, `ROOK.search()`, `ROOK.get_all()` still function
- Check that `memory_ingestion.py` can still call `ROOK.delete()`
