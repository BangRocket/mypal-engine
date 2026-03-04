"""Integration tests for per-user VM + privacy scoped memory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mypalclara.gateway.processor import _determine_privacy_scope


class TestPrivacyScopeIntegration:
    def test_dm_gets_full_scope(self):
        assert _determine_privacy_scope("dm") == "full"

    def test_server_gets_public_only(self):
        assert _determine_privacy_scope("server") == "public_only"


class TestMemoryVisibilityToolIntegration:
    @pytest.mark.asyncio
    async def test_set_and_list_visibility(self):
        from mypalclara.core.core_tools.memory_visibility_tool import (
            _handle_list_public,
            _handle_set_visibility,
        )
        from mypalclara.tools._base import ToolContext

        ctx = ToolContext(user_id="discord-123")

        mock_mem = MagicMock()
        mock_mem.search.return_value = {"results": [MagicMock(memory="Likes Python", id="m1")]}

        with patch(
            "mypalclara.core.core_tools.memory_visibility_tool._get_memory",
            return_value=mock_mem,
        ):
            # Set visibility
            result = await _handle_set_visibility({"memory_id": "m1", "visibility": "public"}, ctx)
            assert "public" in result

            # Verify update_memory_visibility was called correctly
            mock_mem.update_memory_visibility.assert_called_once_with("m1", "public")

            # List public
            result = await _handle_list_public({}, ctx)
            assert "Likes Python" in result


class TestWorkspaceScopingIntegration:
    @pytest.mark.asyncio
    async def test_workspace_write_blocks_soul_md(self, tmp_path, monkeypatch):
        """SOUL.md is always read-only even when workspace is scoped to VM."""
        import mypalclara.core.core_tools.workspace_tool as mod
        from mypalclara.tools._base import ToolContext

        # Create a shared workspace with SOUL.md
        shared_ws = tmp_path / "shared"
        shared_ws.mkdir()
        (shared_ws / "SOUL.md").write_text("Global soul")
        monkeypatch.setattr(mod, "WORKSPACE_DIR", shared_ws)

        ctx = ToolContext(user_id="discord-123")

        # Try to write to SOUL.md — should be blocked
        result = await mod._handle_workspace_write({"filename": "SOUL.md", "content": "hacked"}, ctx)
        assert "read-only" in result.lower() or "cannot" in result.lower()

    @pytest.mark.asyncio
    async def test_workspace_read_soul_from_shared(self, tmp_path, monkeypatch):
        """SOUL.md is always read from the shared workspace, not the VM workspace."""
        import mypalclara.core.core_tools.workspace_tool as mod
        from mypalclara.tools._base import ToolContext

        # Set up shared workspace with SOUL.md
        shared_ws = tmp_path / "shared"
        shared_ws.mkdir()
        (shared_ws / "SOUL.md").write_text("Shared soul content")
        monkeypatch.setattr(mod, "WORKSPACE_DIR", shared_ws)

        # Set up mock VM manager with a SOUL.md in the VM (should be ignored)
        mock_vm = MagicMock()

        async def mock_read(uid, path):
            return "VM soul - should not be read"

        mock_vm.read_file = mock_read
        monkeypatch.setattr(mod, "_vm_manager", mock_vm)
        monkeypatch.setattr(mod, "_vm_users", {"discord-456"})

        try:
            ctx = ToolContext(user_id="discord-456")

            # Read SOUL.md — should come from the shared workspace
            result = await mod._handle_workspace_read({"filename": "SOUL.md"}, ctx)
            assert "Shared soul content" in result
        finally:
            monkeypatch.setattr(mod, "_vm_manager", None)
            monkeypatch.setattr(mod, "_vm_users", set())

    @pytest.mark.asyncio
    async def test_vm_user_writes_to_own_workspace(self, tmp_path, monkeypatch):
        """A user with a VM workspace writes through the VM manager, not shared filesystem."""
        import mypalclara.core.core_tools.workspace_tool as mod
        from mypalclara.tools._base import ToolContext

        # Set up shared workspace
        shared_ws = tmp_path / "shared"
        shared_ws.mkdir()
        monkeypatch.setattr(mod, "WORKSPACE_DIR", shared_ws)

        # Set up mock VM manager
        vm_files = {"NOTES.md": "old notes"}
        mock_vm = MagicMock()

        async def mock_exec(uid, cmd):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "test" in cmd_str and "-f" in cmd_str:
                path = cmd[-1] if isinstance(cmd, list) else ""
                filename = path.rsplit("/", 1)[-1]
                if filename in vm_files:
                    return ""
                raise RuntimeError("not found")
            return ""

        async def mock_read(uid, path):
            filename = path.rsplit("/", 1)[-1]
            if filename in vm_files:
                return vm_files[filename]
            raise RuntimeError("not found")

        async def mock_write(uid, path, content):
            filename = path.rsplit("/", 1)[-1]
            vm_files[filename] = content

        mock_vm.exec_in_vm = mock_exec
        mock_vm.read_file = mock_read
        mock_vm.write_file = mock_write
        monkeypatch.setattr(mod, "_vm_manager", mock_vm)
        monkeypatch.setattr(mod, "_vm_users", {"discord-789"})

        try:
            ctx = ToolContext(user_id="discord-789")

            # Write to NOTES.md — should go through VM manager
            result = await mod._handle_workspace_write({"filename": "NOTES.md", "content": "new notes"}, ctx)
            assert "Updated" in result or "VM" in result

            # Verify it was written through the VM, not to shared filesystem
            assert vm_files["NOTES.md"] == "new notes"
            assert not (shared_ws / "NOTES.md").exists()
        finally:
            monkeypatch.setattr(mod, "_vm_manager", None)
            monkeypatch.setattr(mod, "_vm_users", set())
