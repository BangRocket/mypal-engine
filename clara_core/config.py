"""Centralized configuration for Clara platform.

Loads environment variables and provides a unified configuration interface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from dotenv import load_dotenv


@dataclass
class ClaraConfig:
    """Configuration for Clara platform."""

    # Database
    database_url: str = ""
    mem0_database_url: str = ""

    # LLM Provider
    llm_provider: str = "openrouter"

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4"
    openrouter_site: str = "http://localhost:3000"
    openrouter_title: str = "MyPalClara"

    # NanoGPT
    nanogpt_api_key: str = ""
    nanogpt_model: str = "moonshotai/Kimi-K2-Instruct-0905"

    # Custom OpenAI
    custom_openai_api_key: str = ""
    custom_openai_base_url: str = "https://api.openai.com/v1"
    custom_openai_model: str = "gpt-4o"

    # OpenAI (for embeddings)
    openai_api_key: str = ""

    # Mem0 Provider (independent from chat LLM)
    mem0_provider: str = "openrouter"
    mem0_model: str = "openai/gpt-4o-mini"
    mem0_api_key: str = ""
    mem0_base_url: str = ""

    # Tool calling
    tool_api_key: str = ""
    tool_base_url: str = ""
    tool_model: str = ""
    tool_format: str = "openai"

    # User config
    user_id: str = "demo-user"
    default_project: str = "Default Project"
    skip_profile_load: bool = True

    # Graph memory
    enable_graph_memory: bool = False
    graph_store_provider: str = "neo4j"
    neo4j_url: str = ""
    neo4j_username: str = ""
    neo4j_password: str = ""

    # Discord
    discord_bot_token: str = ""
    discord_client_id: str = ""
    discord_allowed_channels: str = ""
    discord_allowed_roles: str = ""
    discord_max_messages: int = 25
    discord_summary_age_minutes: int = 30
    discord_channel_history_limit: int = 50
    discord_monitor_port: int = 8001
    discord_monitor_enabled: bool = True

    # Docker sandbox
    docker_sandbox_image: str = "python:3.12-slim"
    docker_sandbox_timeout: int = 900
    docker_sandbox_memory: str = "512m"
    docker_sandbox_cpu: float = 1.0

    # Web search
    tavily_api_key: str = ""

    # Local file storage
    clara_files_dir: str = "./clara_files"
    clara_max_file_size: int = 50 * 1024 * 1024  # 50MB

    # Email
    clara_email_address: str = ""
    clara_email_password: str = ""
    clara_email_notify_user: str = ""
    clara_email_notify: bool = False

    # Paths
    base_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent)

    # Singleton instance
    _instance: ClassVar["ClaraConfig | None"] = None
    _initialized: ClassVar[bool] = False

    @classmethod
    def get_instance(cls) -> "ClaraConfig":
        """Get the singleton configuration instance."""
        if cls._instance is None:
            cls._instance = cls._load_from_env()
        return cls._instance

    @classmethod
    def _load_from_env(cls) -> "ClaraConfig":
        """Load configuration from environment variables."""
        load_dotenv()

        return cls(
            # Database
            database_url=os.getenv("DATABASE_URL", ""),
            mem0_database_url=os.getenv("MEM0_DATABASE_URL", ""),
            # LLM Provider
            llm_provider=os.getenv("LLM_PROVIDER", "openrouter").lower(),
            # OpenRouter
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            openrouter_model=os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4"),
            openrouter_site=os.getenv("OPENROUTER_SITE", "http://localhost:3000"),
            openrouter_title=os.getenv("OPENROUTER_TITLE", "MyPalClara"),
            # NanoGPT
            nanogpt_api_key=os.getenv("NANOGPT_API_KEY", ""),
            nanogpt_model=os.getenv(
                "NANOGPT_MODEL", "moonshotai/Kimi-K2-Instruct-0905"
            ),
            # Custom OpenAI
            custom_openai_api_key=os.getenv("CUSTOM_OPENAI_API_KEY", ""),
            custom_openai_base_url=os.getenv(
                "CUSTOM_OPENAI_BASE_URL", "https://api.openai.com/v1"
            ),
            custom_openai_model=os.getenv("CUSTOM_OPENAI_MODEL", "gpt-4o"),
            # OpenAI
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            # Mem0 Provider
            mem0_provider=os.getenv("MEM0_PROVIDER", "openrouter").lower(),
            mem0_model=os.getenv("MEM0_MODEL", "openai/gpt-4o-mini"),
            mem0_api_key=os.getenv("MEM0_API_KEY", ""),
            mem0_base_url=os.getenv("MEM0_BASE_URL", ""),
            # Tool calling
            tool_api_key=os.getenv("TOOL_API_KEY", ""),
            tool_base_url=os.getenv("TOOL_BASE_URL", ""),
            tool_model=os.getenv("TOOL_MODEL", ""),
            tool_format=os.getenv("TOOL_FORMAT", "openai").lower(),
            # User config
            user_id=os.getenv("USER_ID", "demo-user"),
            default_project=os.getenv("DEFAULT_PROJECT", "Default Project"),
            skip_profile_load=os.getenv("SKIP_PROFILE_LOAD", "true").lower() == "true",
            # Graph memory
            enable_graph_memory=os.getenv("ENABLE_GRAPH_MEMORY", "false").lower()
            == "true",
            graph_store_provider=os.getenv("GRAPH_STORE_PROVIDER", "neo4j"),
            neo4j_url=os.getenv("NEO4J_URL", ""),
            neo4j_username=os.getenv("NEO4J_USERNAME", ""),
            neo4j_password=os.getenv("NEO4J_PASSWORD", ""),
            # Discord
            discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", ""),
            discord_client_id=os.getenv("DISCORD_CLIENT_ID", ""),
            discord_allowed_channels=os.getenv("DISCORD_ALLOWED_CHANNELS", ""),
            discord_allowed_roles=os.getenv("DISCORD_ALLOWED_ROLES", ""),
            discord_max_messages=int(os.getenv("DISCORD_MAX_MESSAGES", "25")),
            discord_summary_age_minutes=int(
                os.getenv("DISCORD_SUMMARY_AGE_MINUTES", "30")
            ),
            discord_channel_history_limit=int(
                os.getenv("DISCORD_CHANNEL_HISTORY_LIMIT", "50")
            ),
            discord_monitor_port=int(os.getenv("DISCORD_MONITOR_PORT", "8001")),
            discord_monitor_enabled=os.getenv("DISCORD_MONITOR_ENABLED", "true").lower()
            == "true",
            # Docker sandbox
            docker_sandbox_image=os.getenv(
                "DOCKER_SANDBOX_IMAGE", "python:3.12-slim"
            ),
            docker_sandbox_timeout=int(os.getenv("DOCKER_SANDBOX_TIMEOUT", "900")),
            docker_sandbox_memory=os.getenv("DOCKER_SANDBOX_MEMORY", "512m"),
            docker_sandbox_cpu=float(os.getenv("DOCKER_SANDBOX_CPU", "1.0")),
            # Web search
            tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
            # Local file storage
            clara_files_dir=os.getenv("CLARA_FILES_DIR", "./clara_files"),
            clara_max_file_size=int(
                os.getenv("CLARA_MAX_FILE_SIZE", str(50 * 1024 * 1024))
            ),
            # Email
            clara_email_address=os.getenv("CLARA_EMAIL_ADDRESS", ""),
            clara_email_password=os.getenv("CLARA_EMAIL_PASSWORD", ""),
            clara_email_notify_user=os.getenv("CLARA_EMAIL_NOTIFY_USER", ""),
            clara_email_notify=os.getenv("CLARA_EMAIL_NOTIFY", "false").lower()
            == "true",
        )


def get_config() -> ClaraConfig:
    """Get the current configuration."""
    return ClaraConfig.get_instance()


def init_platform() -> None:
    """Initialize the Clara platform.

    Call this once at application startup to:
    1. Load configuration
    2. Initialize database
    3. Initialize MemoryManager singleton
    4. Initialize ToolRegistry singleton
    5. Optionally load initial profile
    """
    from clara_core.memory import MemoryManager, load_initial_profile
    from clara_core.llm import make_llm
    from clara_core.tools import ToolRegistry
    from db.connection import init_db

    config = get_config()

    # Mark as initialized
    if ClaraConfig._initialized:
        print("[clara_core] Platform already initialized, skipping")
        return

    print("[clara_core] Initializing platform...")

    # 1. Initialize database
    init_db()

    # 2. Initialize LLM and MemoryManager
    llm = make_llm()
    MemoryManager.initialize(llm_callable=llm)

    # 3. Initialize ToolRegistry
    ToolRegistry.initialize()

    # 4. Load initial profile if enabled
    if not config.skip_profile_load:
        load_initial_profile(config.user_id)

    ClaraConfig._initialized = True
    print("[clara_core] Platform initialized successfully")
