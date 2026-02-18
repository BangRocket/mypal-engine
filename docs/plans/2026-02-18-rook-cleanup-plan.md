# Rook Memory System Cleanup — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Trim dead code, collapse unnecessary abstractions, and clean up mem0 naming in `clara_core/memory/`.

**Architecture:** Remove unused ClaraMemory methods and dead LLM providers. Replace LLM and embeddings factories with direct instantiation. Clean up MEM0_* aliases with deprecation warnings. No behavioral changes.

**Tech Stack:** Python, Rook (ClaraMemory), Qdrant/pgvector, OpenAI embeddings, Redis caching.

**Design doc:** `docs/plans/2026-02-18-rook-cleanup-design.md`

---

### Task 1: Delete Dead LLM Providers

**Files:**
- Delete: `clara_core/memory/llm/openai.py`
- Delete: `clara_core/memory/llm/anthropic.py`
- Modify: `clara_core/memory/llm/__init__.py`
- Modify: `clara_core/memory/llm/base.py` (remove `OpenAIConfig`, `AnthropicConfig`)
- Modify: `tests/clara_core/test_memory_llm.py`

**Step 1: Delete the dead provider files**

```bash
rm clara_core/memory/llm/openai.py
rm clara_core/memory/llm/anthropic.py
```

**Step 2: Update `clara_core/memory/llm/base.py`**

Remove `OpenAIConfig` class (lines 60-89) and `AnthropicConfig` class (lines 92-124). These were only used by the deleted providers. Keep `BaseLlmConfig` and `LLMBase`.

Also remove the `import httpx` on line 8 — it was only used by `BaseLlmConfig.http_client`. Remove the `http_client_proxies` parameter and `self.http_client` assignment from `BaseLlmConfig.__init__` since no remaining code uses proxy config.

**Step 3: Update `clara_core/memory/llm/__init__.py`**

Replace contents — remove factory import, keep only what's used:

```python
"""LLM implementations for Clara Memory System."""

from clara_core.memory.llm.base import BaseLlmConfig, LLMBase
from clara_core.memory.llm.unified import UnifiedLLM, UnifiedLLMConfig

__all__ = [
    "LLMBase",
    "BaseLlmConfig",
    "UnifiedLLM",
    "UnifiedLLMConfig",
]
```

**Step 4: Update tests**

In `tests/clara_core/test_memory_llm.py`:
- Remove `TestLlmFactory` class entirely (lines 62-132) — tests dead factory + dead providers
- Remove `test_create_openai_provider` and `test_create_anthropic_provider`
- Remove imports: `LlmFactory` from line 7, `AnthropicConfig, OpenAIConfig` from line 8
- Keep `TestUnifiedLLMConfig`, `TestUnifiedLLM`, `TestBaseLlmConfig`
- In `TestBaseLlmConfig`: remove `test_openai_config` and `test_anthropic_config`

**Step 5: Run tests and lint**

Run: `poetry run ruff check clara_core/memory/llm/ tests/clara_core/test_memory_llm.py`
Run: `poetry run pytest tests/clara_core/test_memory_llm.py -v`
Expected: All remaining tests pass, no lint errors.

**Step 6: Commit**

```bash
git add clara_core/memory/llm/ tests/clara_core/test_memory_llm.py
git commit -m "chore: remove dead LLM providers (openai, anthropic) from memory system"
```

---

### Task 2: Collapse LLM Factory Into Direct Instantiation

**Files:**
- Delete: `clara_core/memory/llm/factory.py`
- Modify: `clara_core/memory/core/memory.py:33,185`
- Modify: `clara_core/memory/graph/kuzu.py:31` (if it imports LlmFactory)
- Modify: `clara_core/memory/graph/neo4j.py:29` (if it imports LlmFactory)
- Modify: `clara_core/memory/__init__.py`

**Step 1: Update `ClaraMemory.__init__` in `memory.py`**

Line 33 — change import:
```python
# OLD
from clara_core.memory.llm.factory import LlmFactory
# NEW
from clara_core.memory.llm.unified import UnifiedLLM, UnifiedLLMConfig
```

Line 185 — change instantiation:
```python
# OLD
self.llm = LlmFactory.create(self.config.llm.provider, self.config.llm.config)
# NEW
llm_conf = self.config.llm.config
if isinstance(llm_conf, dict):
    llm_conf = UnifiedLLMConfig(**llm_conf)
self.llm = UnifiedLLM(llm_conf)
```

**Step 2: Update graph store files**

`clara_core/memory/graph/kuzu.py` and `clara_core/memory/graph/neo4j.py` both import `LlmFactory`. Update to use `UnifiedLLM` + `UnifiedLLMConfig` directly with the same pattern.

**Step 3: Delete factory file**

```bash
rm clara_core/memory/llm/factory.py
```

**Step 4: Update `clara_core/memory/__init__.py`**

Remove `LlmFactory` from imports and `__all__`.

**Step 5: Run tests and lint**

Run: `poetry run ruff check clara_core/memory/`
Run: `poetry run pytest tests/clara_core/test_memory_llm.py -v`
Run: `poetry run python -c "from clara_core.memory import ROOK; print(type(ROOK))"`
Expected: All pass, ROOK initializes.

**Step 6: Commit**

```bash
git add clara_core/memory/
git commit -m "refactor: replace LlmFactory with direct UnifiedLLM instantiation"
```

---

### Task 3: Collapse Embeddings Factory Into Direct Instantiation

**Files:**
- Delete: `clara_core/memory/embeddings/factory.py`
- Modify: `clara_core/memory/core/memory.py:32,173-177`
- Modify: `clara_core/memory/embeddings/__init__.py`
- Modify: `clara_core/memory/__init__.py`
- Modify: `clara_core/memory/graph/kuzu.py` (if it imports EmbedderFactory)
- Modify: `clara_core/memory/graph/neo4j.py` (if it imports EmbedderFactory)
- Modify: `scripts/benchmark_memory.py:81,105`
- Create: `tests/conftest.py` or add `MockEmbeddings` to existing test utilities

**Step 1: Move `MockEmbeddings` to test utilities**

Create or update a test fixture. `MockEmbeddings` is currently in `factory.py`:

```python
class MockEmbeddings:
    """Mock embeddings for testing."""
    def embed(self, text, memory_action=None):
        return [0.0] * 1536
```

Add to `tests/conftest.py` or a test helper module.

**Step 2: Update `ClaraMemory.__init__` in `memory.py`**

Line 32 — change import:
```python
# OLD
from clara_core.memory.embeddings.factory import EmbedderFactory
# NEW
import os
from clara_core.memory.embeddings.openai import OpenAIEmbedding
from clara_core.memory.embeddings.base import BaseEmbedderConfig
from clara_core.memory.embeddings.cached import CachedEmbedding
```

Lines 173-177 — change instantiation:
```python
# OLD
self.embedding_model = EmbedderFactory.create(
    self.config.embedder.provider,
    self.config.embedder.config,
    self.config.vector_store.config,
)
# NEW
embedder_conf = self.config.embedder.config
if isinstance(embedder_conf, dict):
    embedder_conf = BaseEmbedderConfig(**embedder_conf)
self.embedding_model = OpenAIEmbedding(embedder_conf)

# Wrap with cache if configured
enable_cache = os.getenv("MEMORY_EMBEDDING_CACHE", "true").lower() == "true"
if enable_cache and os.getenv("REDIS_URL"):
    self.embedding_model = CachedEmbedding(self.embedding_model, enabled=True)
```

**Step 3: Update graph store files**

Same pattern — replace `EmbedderFactory.create(...)` with direct `OpenAIEmbedding` + optional `CachedEmbedding` wrapper.

**Step 4: Update `scripts/benchmark_memory.py`**

Lines 81 and 105 import `EmbedderFactory`. Update to direct `OpenAIEmbedding` instantiation.

**Step 5: Delete factory and update `__init__` files**

```bash
rm clara_core/memory/embeddings/factory.py
```

Update `clara_core/memory/embeddings/__init__.py` — remove `EmbedderFactory`.
Update `clara_core/memory/__init__.py` — remove `EmbedderFactory` from imports and `__all__`.

**Step 6: Run tests and lint**

Run: `poetry run ruff check clara_core/memory/ scripts/benchmark_memory.py`
Run: `poetry run python -c "from clara_core.memory import ROOK; print(type(ROOK))"`
Expected: ROOK initializes, no lint errors.

**Step 7: Commit**

```bash
git add clara_core/memory/ scripts/benchmark_memory.py tests/
git commit -m "refactor: replace EmbedderFactory with direct OpenAIEmbedding instantiation"
```

---

### Task 4: Remove Unused ClaraMemory Methods

**Files:**
- Modify: `clara_core/memory/core/memory.py`
- Modify: `clara_core/memory_dynamics_manager.py:204-208`

**Step 1: Remove `feedback()` call site**

In `clara_core/memory_dynamics_manager.py` around line 204-208, remove the `ROOK.feedback(...)` call and its surrounding try/except:

```python
# DELETE this block:
if ROOK is not None:
    try:
        ROOK.feedback(memory_id, feedback="NEGATIVE")
    except Exception as e:
        memory_logger.debug(f"Could not send mem0 feedback: {e}")
```

**Step 2: Remove methods from `ClaraMemory`**

In `clara_core/memory/core/memory.py`, remove:

- `get()` method (lines 579-611) — 33 lines
- `update()` method (lines 770-782) — 13 lines
- `feedback()` method (lines 848-860) — 13 lines
- `reset()` method (lines 1002-1027) — 26 lines

Keep: `delete()`, `delete_all()`, `history()`, and all private methods.

**Step 3: Clean up `delete_all` reference to `reset()`**

Line 821 in `delete_all()`: change error message from `"Use reset() to delete all memories."` to `"At least one filter (user_id, agent_id, run_id) is required."` since `reset()` no longer exists.

**Step 4: Run tests and lint**

Run: `poetry run ruff check clara_core/memory/core/memory.py clara_core/memory_dynamics_manager.py`
Run: `poetry run pytest tests/ -x -q`
Expected: Same pass/fail as before (332 passed, 5 pre-existing failures).

**Step 5: Commit**

```bash
git add clara_core/memory/core/memory.py clara_core/memory_dynamics_manager.py
git commit -m "chore: remove unused ClaraMemory methods (get, update, feedback, reset)"
```

---

### Task 5: Clean Up MEM0 Naming and Aliases

**Files:**
- Modify: `clara_core/memory/config.py`
- Delete: `config/mem0.py`
- Modify: `config/rook.py`
- Modify: `clara_core/memory/__init__.py`
- Modify: `pyproject.toml` (stale comment)

**Step 1: Add deprecation warnings to `_get_env`**

In `clara_core/memory/config.py`, update `_get_env` to warn when MEM0 fallback is used:

```python
def _get_env(rook_key: str, mem0_key: str, default: str | None = None) -> str | None:
    """Get env var with ROOK_* preferred, MEM0_* as fallback."""
    rook_val = os.getenv(rook_key)
    if rook_val:
        return rook_val
    mem0_val = os.getenv(mem0_key)
    if mem0_val:
        logger.warning(f"{mem0_key} is deprecated, use {rook_key} instead")
        return mem0_val
    return default
```

**Step 2: Remove MEM0 Python aliases from `config.py`**

Remove these lines:
```python
# Lines ~40-43 — remove MEM0_* variable aliases
MEM0_PROVIDER = ROOK_PROVIDER
MEM0_MODEL = ROOK_MODEL
MEM0_API_KEY = ROOK_API_KEY
MEM0_BASE_URL = ROOK_BASE_URL

# Line ~106 — remove alias
MEM0_DATABASE_URL = ROOK_DATABASE_URL

# Line ~218 — remove alias
MEM0_COLLECTION_NAME = ROOK_COLLECTION_NAME

# Lines ~351-353 — remove aliases
MEM0 = ROOK
Memory = ClaraMemory
```

**Step 3: Clean stale comments**

In `config.py`:
- Line 72: Remove `"IMPORTANT: The vendored mem0 auto-detects..."` comment and the `_saved_env_vars` / `_env_vars_to_clear` / `_clear_rook_env_vars` / `_restore_env_vars` code.
  - **BUT FIRST:** Check if `ClaraMemory.__init__` or `from_config` sniffs env vars. Look for `os.getenv("OPENAI_API_KEY")` etc inside `memory.py`. **If yes, keep the clearing dance. If no, remove it.**
  - Based on my reading: `OpenAIEmbedding.__init__` (line 30) does `os.getenv("OPENAI_API_KEY")` as fallback. But we pass the key explicitly via config. Since the key is passed in config, the env sniffing is only a fallback. The clearing dance is **not needed** — we always pass explicit config. Remove it, and also remove the `_clear_rook_env_vars()` / `_restore_env_vars()` calls in `_init_rook()`.

In `pyproject.toml`:
- Update comment on line ~20 from "using vendored mem0 with fixes" to "Dependencies for Rook (Clara's memory system)"

**Step 4: Delete `config/mem0.py`**

```bash
rm config/mem0.py
```

**Step 5: Update `config/rook.py`**

Remove any MEM0 re-exports if present.

**Step 6: Update `clara_core/memory/__init__.py`**

Remove all MEM0 imports and aliases:
```python
# Remove these imports:
MEM0, MEM0_DATABASE_URL, MEM0_MODEL, MEM0_PROVIDER

# Remove from __all__:
"MEM0", "MEM0_PROVIDER", "MEM0_MODEL", "MEM0_DATABASE_URL"

# Remove:
Memory = ClaraMemory
"Memory" from __all__

# Remove MemoryManager re-export (unused via this path):
try:
    from clara_core.memory_manager import MemoryManager
except ImportError:
    MemoryManager = None
```

**Step 7: Rename `"mem0"` loggers**

Search for `get_logger("mem0")` throughout the codebase. These are in:
- `clara_core/memory_manager.py:24` → `get_logger("rook")`
- `clara_core/memory_writer.py:17` → `get_logger("rook")`
- `clara_core/memory_retriever.py:25` → `get_logger("rook")`
- `clara_core/memory_ingestion.py` (if present)

Also update `config/logging.py` if it has a `"mem0"` logger configuration.

**Step 8: Run tests and lint**

Run: `poetry run ruff check .`
Run: `poetry run pytest tests/ -x -q`
Run: `poetry run python -c "from clara_core.memory import ROOK; print(type(ROOK))"`
Expected: All pass. No regressions.

**Step 9: Commit**

```bash
git add clara_core/memory/ config/ pyproject.toml clara_core/memory_manager.py clara_core/memory_writer.py clara_core/memory_retriever.py clara_core/memory_ingestion.py
git commit -m "chore: clean up MEM0 naming, add deprecation warnings, rename loggers to rook"
```

---

### Task 6: Trim `__init__.py` Exports

**Files:**
- Modify: `clara_core/memory/__init__.py`

**Step 1: Rewrite `__init__.py` to only export what's needed**

```python
"""Clara Memory System (Rook) - Native memory management for Clara.

This module provides Clara's memory system, called "Rook" internally.

Usage:
    from clara_core.memory import ROOK

    if ROOK:
        ROOK.add(messages, user_id="user-123", agent_id="clara")
        results = ROOK.search("preferences", user_id="user-123")
"""

from clara_core.memory.config import (
    ENABLE_GRAPH_MEMORY,
    GRAPH_STORE_PROVIDER,
    ROOK,
    ROOK_DATABASE_URL,
    ROOK_MODEL,
    ROOK_PROVIDER,
    config,
)
from clara_core.memory.core.memory import (
    ClaraMemory,
    ClaraMemoryConfig,
    ClaraMemoryItem,
    ClaraMemoryValidationError,
    MemoryType,
)

__all__ = [
    "ClaraMemory",
    "ClaraMemoryConfig",
    "ClaraMemoryItem",
    "ClaraMemoryValidationError",
    "MemoryType",
    "ROOK",
    "config",
    "ROOK_PROVIDER",
    "ROOK_MODEL",
    "ROOK_DATABASE_URL",
    "ENABLE_GRAPH_MEMORY",
    "GRAPH_STORE_PROVIDER",
]
```

**Step 2: Verify no broken imports**

Run: `poetry run ruff check .`
Run: `poetry run python -c "from clara_core.memory import ROOK, ClaraMemory; print('OK')"`
Run: `poetry run python -c "from clara_core.memory import ENABLE_GRAPH_MEMORY; print('OK')"`

**Step 3: Run full test suite**

Run: `poetry run pytest tests/ -x -q`
Expected: Same pass/fail as baseline.

**Step 4: Commit**

```bash
git add clara_core/memory/__init__.py
git commit -m "chore: trim memory module exports to public API only"
```

---

### Task 7: Final Verification and Cleanup

**Step 1: Full lint check**

Run: `poetry run ruff check . && poetry run ruff format --check .`
Expected: Clean.

**Step 2: Full test suite**

Run: `poetry run pytest tests/ -q`
Expected: 332 passed, 5 pre-existing failures (same as before).

**Step 3: Import smoke tests**

```bash
poetry run python -c "
from clara_core.memory import ROOK, ClaraMemory, ROOK_PROVIDER
from clara_core.memory_manager import MemoryManager
from clara_core.memory.llm import UnifiedLLM, UnifiedLLMConfig
from clara_core.memory.embeddings import CachedEmbedding
print('All imports OK')
print(f'ROOK type: {type(ROOK)}')
print(f'Provider: {ROOK_PROVIDER}')
"
```

**Step 4: Verify `memory_ingestion.py` can still call `ROOK.delete()`**

```bash
poetry run python -c "
from clara_core.memory import ClaraMemory
assert hasattr(ClaraMemory, 'delete'), 'delete() method missing!'
assert hasattr(ClaraMemory, 'history'), 'history() method missing!'
assert not hasattr(ClaraMemory, 'feedback'), 'feedback() should be removed!'
print('Method audit OK')
"
```

**Step 5: Commit any final fixes**

If any issues found, fix and commit with appropriate message.

---

## Summary

| Task | What | Lines Impact |
|------|------|-------------|
| 1 | Delete dead LLM providers | -300 |
| 2 | Collapse LLM factory | -100 |
| 3 | Collapse embeddings factory | -90 |
| 4 | Remove unused ClaraMemory methods | -85 |
| 5 | Clean up MEM0 naming | -50, ~100 modified |
| 6 | Trim exports | -40 |
| 7 | Final verification | 0 |
| **Total** | | **~-665 lines** |
