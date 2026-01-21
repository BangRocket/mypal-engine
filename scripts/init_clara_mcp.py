#!/usr/bin/env python3
"""Initialize Clara MCP Server in the database.

This script adds the clara-mcp-server to the MCP servers database,
enabling Clara to use the native Rust tools.

Usage:
    poetry run python scripts/init_clara_mcp.py
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import SessionLocal
from clara_core.mcp.models import MCPServer


def get_clara_mcp_binary() -> str:
    """Find the clara-mcp-server binary."""
    import shutil

    # Check for system-wide binary first (Docker)
    system_binary = shutil.which("clara-mcp-server")
    if system_binary:
        return system_binary

    # Check for release binary in development
    project_root = Path(__file__).parent.parent
    release_binary = project_root / "clara-mcp-server" / "target" / "release" / "clara-mcp-server"
    debug_binary = project_root / "clara-mcp-server" / "target" / "debug" / "clara-mcp-server"

    if release_binary.exists():
        return str(release_binary)
    elif debug_binary.exists():
        return str(debug_binary)
    else:
        raise FileNotFoundError(
            "clara-mcp-server binary not found. Run: cd clara-mcp-server && cargo build --release"
        )


def init_clara_mcp_server():
    """Initialize the Clara MCP server entry in the database."""
    binary_path = get_clara_mcp_binary()
    print(f"Found clara-mcp-server binary: {binary_path}")

    with SessionLocal() as session:
        # Check if already exists
        existing = session.query(MCPServer).filter(MCPServer.name == "clara-tools").first()

        if existing:
            print(f"Updating existing clara-tools server entry...")
            existing.command = binary_path
            existing.source_type = "local"
            existing.transport = "stdio"
            existing.enabled = True
            existing.status = "stopped"
            existing.display_name = "Clara Native Tools"
        else:
            print("Creating new clara-tools server entry...")
            server = MCPServer(
                name="clara-tools",
                display_name="Clara Native Tools",
                source_type="local",
                source_url=binary_path,
                transport="stdio",
                command=binary_path,
                enabled=True,
                status="stopped",
            )
            session.add(server)

        session.commit()
        print("Clara MCP server initialized successfully!")

        # Verify
        server = session.query(MCPServer).filter(MCPServer.name == "clara-tools").first()
        print(f"  Name: {server.name}")
        print(f"  Command: {server.command}")
        print(f"  Transport: {server.transport}")
        print(f"  Enabled: {server.enabled}")


if __name__ == "__main__":
    init_clara_mcp_server()
