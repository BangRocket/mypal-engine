"""Discord interactive views for Clara commands.

Provides reusable UI components like confirmation dialogs and help menus.
"""

from __future__ import annotations

from typing import Any, Callable

import discord

from config.logging import get_logger

from .embeds import (
    EMBED_COLOR_PRIMARY,
    create_error_embed,
    create_help_embed,
    create_success_embed,
)

logger = get_logger("clara_core.discord.views")


class ConfirmView(discord.ui.View):
    """Confirmation dialog for destructive or important actions.

    Usage:
        view = ConfirmView("delete server")
        await ctx.respond("Are you sure?", view=view)
        await view.wait()
        if view.confirmed:
            # Proceed with action
    """

    def __init__(
        self,
        action_name: str,
        timeout: float = 60.0,
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
    ):
        """Initialize the confirmation view.

        Args:
            action_name: Name of the action being confirmed (for logging)
            timeout: Seconds before the view times out
            confirm_label: Label for the confirm button
            cancel_label: Label for the cancel button
        """
        super().__init__(timeout=timeout)
        self.action_name = action_name
        self.confirmed: bool | None = None
        self.interaction: discord.Interaction | None = None

        # Update button labels
        self.confirm_button.label = confirm_label
        self.cancel_button.label = cancel_label

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Handle confirm button click."""
        self.confirmed = True
        self.interaction = interaction
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Handle cancel button click."""
        self.confirmed = False
        self.interaction = interaction
        self.stop()

    async def on_timeout(self):
        """Handle view timeout."""
        self.confirmed = None
        self.stop()


class HelpSelectView(discord.ui.View):
    """Interactive help menu with topic selection."""

    TOPICS = {
        "mcp": {
            "title": "MCP Server Management",
            "commands": {
                "/mcp list": "List all installed MCP servers",
                "/mcp status [server]": "Get server status details",
                "/mcp tools [server]": "List tools from a server",
                "/mcp install <source>": "Install a new server (admin)",
                "/mcp uninstall <name>": "Remove a server (admin)",
                "/mcp enable <name>": "Enable a server",
                "/mcp disable <name>": "Disable a server",
                "/mcp restart <name>": "Restart a server",
            },
        },
        "model": {
            "title": "Model & Tier Settings",
            "commands": {
                "/model status": "Show current model and tier",
                "/model tier <tier>": "Set default tier (admin)",
                "/model auto <on/off>": "Toggle auto-tier selection (admin)",
            },
        },
        "ors": {
            "title": "Organic Response System",
            "commands": {
                "/ors status": "Show ORS configuration",
                "/ors enable": "Enable proactive messages (admin)",
                "/ors disable": "Disable proactive messages (admin)",
                "/ors channel <channel>": "Set ORS target channel (admin)",
                "/ors quiet <start> <end>": "Set quiet hours (admin)",
            },
        },
        "sandbox": {
            "title": "Code Execution Sandbox",
            "commands": {
                "/sandbox status": "Show sandbox availability",
                "/sandbox mode <mode>": "Set sandbox mode (admin)",
            },
        },
        "memory": {
            "title": "Memory System",
            "commands": {
                "/memory status": "Show memory statistics",
                "/memory search <query>": "Search your memories",
                "/memory clear": "Clear your memories",
            },
        },
        "email": {
            "title": "Email Monitoring",
            "commands": {
                "/email status": "Show email monitoring status",
                "/email channel <channel>": "Set alert channel (admin)",
                "/email presets": "List available presets",
            },
        },
    }

    def __init__(self, timeout: float = 180.0):
        """Initialize the help select view."""
        super().__init__(timeout=timeout)

    @discord.ui.select(
        placeholder="Select a help topic...",
        options=[
            discord.SelectOption(label="MCP Servers", value="mcp", emoji="\U0001f50c"),
            discord.SelectOption(label="Model Settings", value="model", emoji="\U0001f916"),
            discord.SelectOption(label="Proactive (ORS)", value="ors", emoji="\U0001f4ac"),
            discord.SelectOption(label="Sandbox", value="sandbox", emoji="\U0001f4e6"),
            discord.SelectOption(label="Memory", value="memory", emoji="\U0001f9e0"),
            discord.SelectOption(label="Email", value="email", emoji="\U0001f4e7"),
        ],
    )
    async def select_topic(self, select: discord.ui.Select, interaction: discord.Interaction):
        """Handle topic selection."""
        topic = select.values[0]
        topic_info = self.TOPICS.get(topic, {})

        embed = create_help_embed(
            topic=topic_info.get("title", topic),
            commands_info=topic_info.get("commands"),
        )

        await interaction.response.edit_message(embed=embed, view=self)


class PaginatedView(discord.ui.View):
    """Paginated view for long lists of items.

    Usage:
        def format_page(items, page, total_pages):
            return discord.Embed(title=f"Page {page}/{total_pages}", description="\\n".join(items))

        view = PaginatedView(all_items, items_per_page=10, formatter=format_page)
        await ctx.respond(embed=view.get_current_embed(), view=view)
    """

    def __init__(
        self,
        items: list[Any],
        items_per_page: int = 10,
        formatter: Callable[[list[Any], int, int], discord.Embed] | None = None,
        timeout: float = 180.0,
    ):
        """Initialize paginated view.

        Args:
            items: All items to paginate
            items_per_page: Items per page
            formatter: Function to format a page's items into an embed
            timeout: View timeout in seconds
        """
        super().__init__(timeout=timeout)
        self.items = items
        self.items_per_page = items_per_page
        self.formatter = formatter or self._default_formatter
        self.current_page = 1
        self.total_pages = max(1, (len(items) + items_per_page - 1) // items_per_page)

        # Disable buttons if only one page
        if self.total_pages <= 1:
            self.prev_button.disabled = True
            self.next_button.disabled = True

    def _default_formatter(self, items: list[Any], page: int, total_pages: int) -> discord.Embed:
        """Default page formatter."""
        return discord.Embed(
            title=f"Page {page}/{total_pages}",
            description="\n".join(str(item) for item in items),
            color=EMBED_COLOR_PRIMARY,
        )

    def get_current_page_items(self) -> list[Any]:
        """Get items for the current page."""
        start = (self.current_page - 1) * self.items_per_page
        end = start + self.items_per_page
        return self.items[start:end]

    def get_current_embed(self) -> discord.Embed:
        """Get embed for the current page."""
        items = self.get_current_page_items()
        return self.formatter(items, self.current_page, self.total_pages)

    def _update_buttons(self):
        """Update button states based on current page."""
        self.prev_button.disabled = self.current_page <= 1
        self.next_button.disabled = self.current_page >= self.total_pages

    @discord.ui.button(label="\u25c0 Previous", style=discord.ButtonStyle.secondary)
    async def prev_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Handle previous page button."""
        if self.current_page > 1:
            self.current_page -= 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

    @discord.ui.button(label="Next \u25b6", style=discord.ButtonStyle.secondary)
    async def next_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Handle next page button."""
        if self.current_page < self.total_pages:
            self.current_page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.get_current_embed(), view=self)


class ConfirmDangerView(ConfirmView):
    """Extended confirmation for particularly dangerous actions.

    Requires typing a confirmation phrase.
    """

    def __init__(
        self,
        action_name: str,
        confirm_phrase: str = "CONFIRM",
        timeout: float = 120.0,
    ):
        """Initialize dangerous action confirmation.

        Args:
            action_name: Name of the action
            confirm_phrase: Phrase user must type to confirm
            timeout: View timeout
        """
        super().__init__(action_name, timeout)
        self.confirm_phrase = confirm_phrase
        self.phrase_matched = False

        # Remove the confirm button - will use modal instead
        self.remove_item(self.confirm_button)

    @discord.ui.button(label="Confirm (Type to verify)", style=discord.ButtonStyle.danger)
    async def type_confirm_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Open modal for phrase confirmation."""
        modal = ConfirmPhraseModal(self.confirm_phrase, self)
        await interaction.response.send_modal(modal)


class ConfirmPhraseModal(discord.ui.Modal):
    """Modal for typing confirmation phrase."""

    def __init__(self, confirm_phrase: str, parent_view: ConfirmDangerView):
        super().__init__(title="Confirm Action")
        self.confirm_phrase = confirm_phrase
        self.parent_view = parent_view

        self.phrase_input = discord.ui.InputText(
            label=f"Type '{confirm_phrase}' to confirm",
            placeholder=confirm_phrase,
            style=discord.InputTextStyle.short,
            required=True,
        )
        self.add_item(self.phrase_input)

    async def callback(self, interaction: discord.Interaction):
        """Handle modal submission."""
        if self.phrase_input.value == self.confirm_phrase:
            self.parent_view.confirmed = True
            self.parent_view.phrase_matched = True
            self.parent_view.interaction = interaction
            self.parent_view.stop()
            await interaction.response.send_message(
                embed=create_success_embed("Confirmed", "Action proceeding..."),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=create_error_embed("Phrase mismatch", f"Expected: `{self.confirm_phrase}`"),
                ephemeral=True,
            )


class GatewayButtonView(discord.ui.View):
    """Dynamic button view for gateway-generated buttons.

    Handles buttons created by Clara's LLM responses with dismiss/confirm actions.
    Buttons are dynamically created from configuration data.
    """

    # Map style names to Discord button styles
    STYLE_MAP = {
        "primary": discord.ButtonStyle.primary,
        "secondary": discord.ButtonStyle.secondary,
        "success": discord.ButtonStyle.success,
        "danger": discord.ButtonStyle.danger,
    }

    def __init__(
        self,
        buttons: list[dict[str, Any]],
        timeout: float = 180.0,
    ):
        """Initialize the gateway button view.

        Args:
            buttons: List of button configurations with label, style, action, disabled
            timeout: View timeout in seconds
        """
        super().__init__(timeout=timeout)
        self._setup_buttons(buttons)

    def _setup_buttons(self, buttons: list[dict[str, Any]]) -> None:
        """Create and add buttons from configuration.

        Args:
            buttons: Button configuration list
        """
        for i, btn_config in enumerate(buttons[:5]):  # Max 5 buttons
            label = btn_config.get("label", f"Button {i + 1}")
            style_name = btn_config.get("style", "secondary")
            action = btn_config.get("action", "dismiss")
            disabled = btn_config.get("disabled", False)

            style = self.STYLE_MAP.get(style_name, discord.ButtonStyle.secondary)

            button = GatewayButton(
                label=label,
                style=style,
                action=action,
                disabled=disabled,
                custom_id=f"gateway_btn_{i}",
            )
            self.add_item(button)

    async def on_timeout(self) -> None:
        """Remove buttons when view times out."""
        # Buttons become non-interactive after timeout
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        self.stop()


class GatewayButton(discord.ui.Button):
    """Individual button for GatewayButtonView."""

    def __init__(
        self,
        label: str,
        style: discord.ButtonStyle,
        action: str,
        disabled: bool = False,
        custom_id: str | None = None,
    ):
        """Initialize a gateway button.

        Args:
            label: Button display text
            style: Discord button style
            action: Action type ('dismiss' or 'confirm')
            disabled: Whether button is disabled
            custom_id: Custom ID for the button
        """
        super().__init__(
            label=label,
            style=style,
            disabled=disabled,
            custom_id=custom_id,
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle button click.

        Args:
            interaction: Discord interaction
        """
        view = self.view
        if not isinstance(view, GatewayButtonView):
            return

        try:
            if self.action == "dismiss":
                # Remove all buttons from the message
                await interaction.response.edit_message(view=None)
                view.stop()

            elif self.action == "confirm":
                # Update message to show confirmation
                original_content = interaction.message.content if interaction.message else ""
                confirmed_content = f"{original_content}\n\n✅ *Confirmed by {interaction.user.display_name}*"

                await interaction.response.edit_message(
                    content=confirmed_content,
                    view=None,
                )
                view.stop()

            else:
                # Unknown action - just acknowledge
                await interaction.response.defer()

        except Exception as e:
            logger.warning(f"Button callback error: {e}")
            try:
                await interaction.response.send_message(
                    "Button action failed.",
                    ephemeral=True,
                )
            except Exception:
                pass
