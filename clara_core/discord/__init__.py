"""Clara Discord integration package.

This package provides slash commands and interactive components for Discord.

Modules:
- commands: ClaraCommands cog with all slash commands
- embeds: Helper functions for creating Discord embeds
- views: Interactive views (buttons, select menus)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discord.ext import commands as discord_commands


def setup(bot: "discord_commands.Bot") -> None:
    """Register the ClaraCommands cog with the bot.

    Args:
        bot: The Discord bot instance
    """
    from .commands import ClaraCommands

    bot.add_cog(ClaraCommands(bot))


__all__ = ["setup"]
