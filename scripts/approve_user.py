#!/usr/bin/env python3
"""CLI tool for managing user approvals.

Usage:
    poetry run python scripts/approve_user.py list [--pending]
    poetry run python scripts/approve_user.py approve <user_id_or_email>
    poetry run python scripts/approve_user.py set-admin <user_id_or_email>
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import SessionLocal, init_db
from db.models import CanonicalUser


def find_user(db, identifier: str) -> CanonicalUser | None:
    """Find user by ID or email."""
    user = db.query(CanonicalUser).filter(CanonicalUser.id == identifier).first()
    if not user:
        user = db.query(CanonicalUser).filter(CanonicalUser.primary_email == identifier).first()
    return user


def cmd_list(pending_only: bool = False):
    init_db()
    db = SessionLocal()
    try:
        query = db.query(CanonicalUser)
        if pending_only:
            query = query.filter(CanonicalUser.status == "pending")
        users = query.order_by(CanonicalUser.created_at.desc()).all()

        if not users:
            print("No users found.")
            return

        for u in users:
            user_status = getattr(u, "status", "active")
            admin = getattr(u, "is_admin", False)
            admin_tag = " [ADMIN]" if admin else ""
            print(
                f"  {u.id}  {user_status:10s}  "
                f"{u.display_name or '(no name)':20s}  "
                f"{u.primary_email or '(no email)'}{admin_tag}"
            )
    finally:
        db.close()


def cmd_approve(identifier: str):
    init_db()
    db = SessionLocal()
    try:
        user = find_user(db, identifier)
        if not user:
            print(f"User not found: {identifier}")
            sys.exit(1)
        user.status = "active"
        db.commit()
        print(f"Approved: {user.display_name} ({user.id})")
    finally:
        db.close()


def cmd_set_admin(identifier: str):
    init_db()
    db = SessionLocal()
    try:
        user = find_user(db, identifier)
        if not user:
            print(f"User not found: {identifier}")
            sys.exit(1)
        user.is_admin = True
        user.status = "active"
        db.commit()
        print(f"Set admin: {user.display_name} ({user.id})")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    if command == "list":
        pending_only = "--pending" in sys.argv
        cmd_list(pending_only)
    elif command == "approve" and len(sys.argv) >= 3:
        cmd_approve(sys.argv[2])
    elif command == "set-admin" and len(sys.argv) >= 3:
        cmd_set_admin(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)
