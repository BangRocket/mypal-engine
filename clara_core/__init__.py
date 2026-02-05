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

from pathlib import Path

# Read version from VERSION file
_VERSION_FILE = Path(__file__).parent.parent / "VERSION"
__version__ = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "0.0.0"


def get_version() -> str:
    """Get the current Clara platform version."""
    return __version__

from clara_core.config import get_config, init_platform
from clara_core.llm import (
    DEFAULT_TIER,
    ModelTier,
    anthropic_to_openai_response,
    generate_tool_description,
    get_current_tier,
    get_model_for_tier,
    get_tier_info,
    make_llm,
    make_llm_streaming,
    make_llm_with_tools,
    make_llm_with_tools_anthropic,
    make_llm_with_tools_langchain,
    make_llm_with_xml_tools,
    make_llm_with_xml_tools_streaming,
)
from clara_core.memory_manager import MemoryManager, load_initial_profile
from clara_core.platform import PlatformAdapter, PlatformContext, PlatformMessage
from clara_core.tools import ToolRegistry

__all__ = [
    # Version
    "__version__",
    "get_version",
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
    "make_llm_with_tools_anthropic",
    "make_llm_with_tools_langchain",
    "make_llm_with_xml_tools",
    "make_llm_with_xml_tools_streaming",
    "anthropic_to_openai_response",
    "generate_tool_description",
    # Model tiers
    "ModelTier",
    "get_model_for_tier",
    "get_current_tier",
    "get_tier_info",
    "DEFAULT_TIER",
    # Profile loading
    "load_initial_profile",
]
