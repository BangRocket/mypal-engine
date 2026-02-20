"""Core memory operations for Clara Memory System."""

from mypalclara.core.memory.core.base import MemoryBase
from mypalclara.core.memory.core.memory import ClaraMemory
from mypalclara.core.memory.core.prompts import (
    AGENT_MEMORY_EXTRACTION_PROMPT,
    DEFAULT_UPDATE_MEMORY_PROMPT,
    FACT_RETRIEVAL_PROMPT,
    USER_MEMORY_EXTRACTION_PROMPT,
    get_update_memory_messages,
)
from mypalclara.core.memory.core.utils import (
    extract_json,
    get_fact_retrieval_messages,
    parse_messages,
    remove_code_blocks,
)

__all__ = [
    "MemoryBase",
    "ClaraMemory",
    "FACT_RETRIEVAL_PROMPT",
    "USER_MEMORY_EXTRACTION_PROMPT",
    "AGENT_MEMORY_EXTRACTION_PROMPT",
    "DEFAULT_UPDATE_MEMORY_PROMPT",
    "get_update_memory_messages",
    "extract_json",
    "parse_messages",
    "remove_code_blocks",
    "get_fact_retrieval_messages",
]
