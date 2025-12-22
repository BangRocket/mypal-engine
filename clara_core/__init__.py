"""Clara Core - Shared infrastructure for the Clara platform.

This package provides the common components used by all Clara platform services:
- API server
- Discord bot
- Email monitor
- Future platforms (Slack, Telegram, etc.)

Usage:
    from clara_core import init_platform, MemoryManager, ToolRegistry

    # Initialize shared infrastructure (call once at startup)
    init_platform()

    # Access singletons
    mm = MemoryManager.get_instance()
    tools = ToolRegistry.get_instance()
"""

from clara_core.config import get_config, init_platform
from clara_core.llm import (
    make_llm,
    make_llm_streaming,
    make_llm_with_tools,
)
from clara_core.memory import MemoryManager, load_initial_profile
from clara_core.platform import PlatformAdapter, PlatformContext, PlatformMessage
from clara_core.tools import ToolRegistry

__all__ = [
    # Initialization
    "init_platform",
    "get_config",
    # Core classes
    "MemoryManager",
    "ToolRegistry",
    # Platform abstractions
    "PlatformAdapter",
    "PlatformContext",
    "PlatformMessage",
    # LLM functions
    "make_llm",
    "make_llm_streaming",
    "make_llm_with_tools",
    # Profile loading
    "load_initial_profile",
]
