# Workspace File Tools Design

**Date:** 2026-03-03
**Status:** Approved

## Overview

Give Clara runtime access to her workspace files via 4 tools: list, read, write, create. SOUL.md and IDENTITY.md are read-only (owner-controlled). New files must be .md. No deletion, no renaming, no subdirectories.

## Tools

| Tool | Params | Guards |
|------|--------|--------|
| `workspace_list` | none | none |
| `workspace_read` | `filename` | none |
| `workspace_write` | `filename`, `content`, `mode` (overwrite/append) | SOUL.md, IDENTITY.md blocked |
| `workspace_create` | `filename`, `content` | must be .md, no reserved names, no existing files |

## Guards

```python
READONLY_FILES = {"SOUL.md", "IDENTITY.md"}
```

- Path traversal prevention: strip `..`, `/`, ensure result stays in workspace dir
- Write to readonly file → error string
- Create with existing name → error, use write instead
- Create without .md extension → error

## File

`mypalclara/core/core_tools/workspace_tool.py` — follows tool module contract (MODULE_NAME, MODULE_VERSION, TOOLS, SYSTEM_PROMPT).

## Intentional Omissions

- No delete (dangerous, add later if needed)
- No rename
- No binary files
- No subdirectories
