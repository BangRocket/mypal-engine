"""Backfill CanonicalUser + PlatformLink for all existing user_ids.

Scans Sessions, Messages, MemoryDynamics, and Intentions for unique
prefixed user_ids (e.g., 'discord-123') and creates CanonicalUser +
PlatformLink records for each.

Usage:
    poetry run python scripts/backfill_users.py
    poetry run python scripts/backfill_users.py --dry-run
"""

from __future__ import annotations

import sys
import uuid
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

from db.connection import SessionLocal, init_db
from db.models import (
    CanonicalUser,
    Intention,
    MemoryDynamics,
    Message,
    PlatformLink,
    Session,
    utcnow,
)


def extract_platform(prefixed_id: str) -> tuple[str, str]:
    """Extract platform and raw ID from a prefixed user ID.

    e.g., 'discord-123' -> ('discord', '123')
          'teams-abc-def' -> ('teams', 'abc-def')
    """
    parts = prefixed_id.split("-", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "unknown", prefixed_id


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("[DRY RUN] No changes will be made.\n")

    init_db(run_migrations=False)
    db = SessionLocal()

    try:
        # Collect all unique user_ids from various tables
        user_ids: set[str] = set()

        print("Scanning Sessions...")
        for (uid,) in db.query(Session.user_id).distinct():
            user_ids.add(uid)

        print("Scanning Messages...")
        for (uid,) in db.query(Message.user_id).distinct():
            user_ids.add(uid)

        print("Scanning MemoryDynamics...")
        for (uid,) in db.query(MemoryDynamics.user_id).distinct():
            user_ids.add(uid)

        print("Scanning Intentions...")
        for (uid,) in db.query(Intention.user_id).distinct():
            user_ids.add(uid)

        print(f"\nFound {len(user_ids)} unique user_ids.")

        # Filter out those that already have PlatformLinks
        existing = {link.prefixed_user_id for link in db.query(PlatformLink.prefixed_user_id).all()}
        new_ids = user_ids - existing
        print(f"Already linked: {len(existing)}")
        print(f"New to backfill: {len(new_ids)}\n")

        if not new_ids:
            print("Nothing to backfill.")
            return

        # Group by platform for reporting
        by_platform: dict[str, list[str]] = defaultdict(list)
        for uid in sorted(new_ids):
            platform, _ = extract_platform(uid)
            by_platform[platform].append(uid)

        for platform, ids in sorted(by_platform.items()):
            print(f"  {platform}: {len(ids)} users")

        if dry_run:
            print("\n[DRY RUN] Would create the above CanonicalUsers + PlatformLinks.")
            return

        # Create CanonicalUser + PlatformLink for each
        created = 0
        for uid in sorted(new_ids):
            platform, platform_id = extract_platform(uid)

            canonical = CanonicalUser(
                id=str(uuid.uuid4()),
                display_name=f"{platform}:{platform_id}",
                created_at=utcnow(),
            )
            db.add(canonical)
            db.flush()

            link = PlatformLink(
                id=str(uuid.uuid4()),
                canonical_user_id=canonical.id,
                platform=platform,
                platform_user_id=platform_id,
                prefixed_user_id=uid,
                display_name=f"{platform}:{platform_id}",
                linked_via="backfill",
            )
            db.add(link)
            created += 1

        db.commit()
        print(f"\nCreated {created} CanonicalUsers + PlatformLinks.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
