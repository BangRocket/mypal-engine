#!/usr/bin/env python3
"""
Clear all mem0 databases (vector store + graph store).

Usage:
    python clear_dbs.py              # Clear with confirmation
    python clear_dbs.py --yes        # Skip confirmation
    python clear_dbs.py --user josh  # Clear specific user
"""

import argparse
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

USER_ID = os.getenv("USER_ID", "demo-user")


def clear_databases(user_id: str, skip_confirm: bool = False):
    """Clear all mem0 data for a user."""
    from config.mem0 import MEM0, QDRANT_DATA_DIR

    if MEM0 is None:
        print("Error: mem0 is not initialized")
        return False

    if not skip_confirm:
        print(f"This will delete ALL memories for user '{user_id}':")
        print(f"  - Vector store (Qdrant): {QDRANT_DATA_DIR}")
        print(f"  - Graph store (Neo4j/Kuzu): configured in .env")
        response = input("\nAre you sure? [y/N]: ").strip().lower()
        if response != "y":
            print("Aborted.")
            return False

    print(f"\nClearing memories for user '{user_id}'...")

    # Clear via mem0 API (handles both vector and graph)
    try:
        MEM0.delete_all(user_id=user_id)
        print("  - mem0.delete_all() completed")
    except Exception as e:
        print(f"  - Error during delete_all: {e}")

    # Clear local qdrant data files
    if QDRANT_DATA_DIR.exists():
        for item in QDRANT_DATA_DIR.iterdir():
            if item.name == ".lock":
                continue  # Keep lock file
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                print(f"  - Removed {item}")
            except Exception as e:
                print(f"  - Error removing {item}: {e}")

    # Remove profile loaded flag
    profile_flag = Path(__file__).parent / ".profile_loaded"
    if profile_flag.exists():
        profile_flag.unlink()
        print(f"  - Removed {profile_flag}")

    # Verify
    print("\nVerifying...")
    result = MEM0.get_all(user_id=user_id)
    memories = len(result.get("results", []))
    relations = len(result.get("relations", []))
    print(f"  - Remaining memories: {memories}")
    print(f"  - Remaining relations: {relations}")

    if memories == 0 and relations == 0:
        print("\nAll databases cleared successfully!")
        return True
    else:
        print("\nWarning: Some data may remain")
        return False


def main():
    parser = argparse.ArgumentParser(description="Clear all mem0 databases")
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt"
    )
    parser.add_argument(
        "--user", "-u",
        type=str,
        default=USER_ID,
        help=f"User ID to clear (default: {USER_ID})"
    )
    args = parser.parse_args()

    clear_databases(args.user, skip_confirm=args.yes)


if __name__ == "__main__":
    main()
