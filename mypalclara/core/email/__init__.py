"""Email monitoring and tools for Clara."""

from clara_core.email.monitor import (
    EMAIL_TOOLS,
    execute_email_tool,
    start_email_monitor,
    stop_email_monitor,
)

__all__ = [
    "EMAIL_TOOLS",
    "execute_email_tool",
    "start_email_monitor",
    "stop_email_monitor",
]
