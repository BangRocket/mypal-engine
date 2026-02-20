"""Configuration modules for Clara.

Submodules:
- config.bot: Bot personality and name configuration
- config.logging: Logging setup with console and database handlers
- config.rook: Rook memory system configuration (formerly mem0)
- config.mem0: Backward compatibility alias for config.rook
"""

# Lazy imports to avoid circular dependencies
# Import directly from submodules as needed:
#   from mypalclara.config.bot import PERSONALITY, BOT_NAME
#   from mypalclara.config.logging import init_logging, get_logger
#   from mypalclara.config.rook import ROOK
#   from mypalclara.core.memory import ROOK  # Preferred
