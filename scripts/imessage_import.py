"""
iMessage Import Tool for MyPalClara

Imports text message conversations from macOS iMessage database into mem0
for building relationship context with specific contacts.

Usage:
    python imessage_import.py --contacts "+15551234567,+15559876543"
    python imessage_import.py --contacts "john@example.com" --limit 100
    python imessage_import.py --list-contacts
    python imessage_import.py --contacts "+15551234567" --dry-run

Requires Full Disk Access for Terminal in System Preferences > Security & Privacy > Privacy
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional
from collections import defaultdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not required if env vars are set directly

# Default iMessage database path on macOS
DEFAULT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"


def get_imessage_data(db_path: Optional[str] = None):
    """Fetch messages from iMessage database."""
    import platform

    if platform.system() != "Darwin" and not db_path:
        print("Error: iMessage import only works on macOS")
        print("On other platforms, provide a path to the chat.db file with --db-path")
        sys.exit(1)

    from imessage_reader import fetch_data

    path = db_path or str(DEFAULT_DB_PATH)

    if not Path(path).exists():
        print(f"Error: iMessage database not found at {path}")
        print("\nTo fix this:")
        print("1. Open System Preferences > Security & Privacy > Privacy > Full Disk Access")
        print("2. Add your terminal application (Terminal, iTerm2, etc.)")
        print("3. Restart your terminal")
        sys.exit(1)

    try:
        fd = fetch_data.FetchData(path)
        return fd.get_messages()
    except Exception as e:
        if "unable to open database" in str(e):
            print(f"Error: Cannot access iMessage database at {path}")
            print("\nThis usually means Full Disk Access is not enabled.")
            print("1. Open System Preferences > Security & Privacy > Privacy > Full Disk Access")
            print("2. Add your terminal application (Terminal, iTerm2, etc.)")
            print("3. Restart your terminal")
            sys.exit(1)
        raise


def list_contacts(db_path: Optional[str] = None) -> dict:
    """List all contacts with message counts."""
    messages = get_imessage_data(db_path)

    contact_counts = defaultdict(lambda: {"sent": 0, "received": 0, "last_date": None})

    for msg in messages:
        # Message tuple: (user_id, message, date, service, account, is_from_me)
        user_id = msg[0]
        date = msg[2]
        is_from_me = msg[5]

        if is_from_me:
            contact_counts[user_id]["sent"] += 1
        else:
            contact_counts[user_id]["received"] += 1

        # Track most recent message date
        if date and (not contact_counts[user_id]["last_date"] or date > contact_counts[user_id]["last_date"]):
            contact_counts[user_id]["last_date"] = date

    return dict(contact_counts)


def filter_messages_by_contacts(messages: list, contacts: list[str]) -> list:
    """Filter messages to only include those from specified contacts."""
    # Normalize contact identifiers (remove spaces, lowercase for emails)
    normalized_contacts = set()
    for c in contacts:
        c = c.strip()
        if "@" in c:
            normalized_contacts.add(c.lower())
        else:
            # Phone number - remove common formatting
            normalized_contacts.add(c.replace(" ", "").replace("-", "").replace("(", "").replace(")", ""))

    filtered = []
    for msg in messages:
        user_id = msg[0]
        if not user_id:
            continue

        # Normalize the message's user_id
        normalized_id = user_id.strip()
        if "@" in normalized_id:
            normalized_id = normalized_id.lower()
        else:
            normalized_id = normalized_id.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

        if normalized_id in normalized_contacts:
            filtered.append(msg)

    return filtered


def group_messages_into_conversations(messages: list, gap_minutes: int = 60) -> list[list]:
    """
    Group messages into conversation chunks based on time gaps.
    A new conversation starts when there's a gap of gap_minutes or more.
    """
    if not messages:
        return []

    # Sort by date
    sorted_msgs = sorted(messages, key=lambda m: m[2] if m[2] else "")

    conversations = []
    current_convo = [sorted_msgs[0]]

    for i in range(1, len(sorted_msgs)):
        prev_date = sorted_msgs[i - 1][2]
        curr_date = sorted_msgs[i][2]

        # If we can't parse dates, keep in same conversation
        if not prev_date or not curr_date:
            current_convo.append(sorted_msgs[i])
            continue

        try:
            # Parse the dates - imessage_reader returns formatted strings
            # Format is typically "YYYY-MM-DD HH:MM:SS"
            if isinstance(prev_date, str) and isinstance(curr_date, str):
                prev_dt = datetime.fromisoformat(prev_date.replace(" ", "T"))
                curr_dt = datetime.fromisoformat(curr_date.replace(" ", "T"))

                diff_minutes = (curr_dt - prev_dt).total_seconds() / 60

                if diff_minutes > gap_minutes:
                    # Start new conversation
                    if current_convo:
                        conversations.append(current_convo)
                    current_convo = [sorted_msgs[i]]
                else:
                    current_convo.append(sorted_msgs[i])
            else:
                current_convo.append(sorted_msgs[i])
        except (ValueError, TypeError):
            current_convo.append(sorted_msgs[i])

    if current_convo:
        conversations.append(current_convo)

    return conversations


def format_conversation_for_mem0(conversation: list, contact_id: str, contact_name: Optional[str] = None) -> list[dict]:
    """
    Format a conversation chunk for mem0 ingestion.
    Returns a list of message dicts in the format mem0 expects.
    """
    display_name = contact_name or contact_id
    messages = []

    for msg in conversation:
        # msg tuple: (user_id, message, date, service, account, is_from_me)
        content = msg[1]
        is_from_me = msg[5]

        if not content or not content.strip():
            continue

        if is_from_me:
            messages.append({
                "role": "user",
                "content": content.strip()
            })
        else:
            # Messages from the contact - prefix with their name for context
            messages.append({
                "role": "assistant",  # Using assistant role for the contact's messages
                "content": f"[{display_name}]: {content.strip()}"
            })

    return messages


def import_to_mem0(
    contacts: list[str],
    contact_names: Optional[dict[str, str]] = None,
    db_path: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    user_id: str = "demo-user",
):
    """
    Import iMessage conversations for specified contacts into mem0.

    Args:
        contacts: List of contact identifiers (phone numbers or emails)
        contact_names: Optional mapping of contact_id -> display name
        db_path: Path to iMessage database (defaults to ~/Library/Messages/chat.db)
        limit: Maximum number of messages to import per contact
        dry_run: If True, only show what would be imported without actually importing
        user_id: User ID for mem0 storage
    """
    from config.mem0 import MEM0

    if MEM0 is None:
        print("Error: mem0 is not initialized. Check your configuration.")
        sys.exit(1)

    contact_names = contact_names or {}

    print(f"Fetching messages from iMessage database...")
    all_messages = get_imessage_data(db_path)
    print(f"Found {len(all_messages)} total messages")

    # Filter to specified contacts
    filtered_messages = filter_messages_by_contacts(all_messages, contacts)
    print(f"Found {len(filtered_messages)} messages from specified contacts")

    if not filtered_messages:
        print("No messages found for the specified contacts.")
        return

    # Group by contact for processing
    messages_by_contact = defaultdict(list)
    for msg in filtered_messages:
        user_id_msg = msg[0]
        messages_by_contact[user_id_msg].append(msg)

    total_memories_added = 0

    for contact_id, contact_messages in messages_by_contact.items():
        contact_name = contact_names.get(contact_id, contact_id)
        print(f"\nProcessing messages with {contact_name} ({len(contact_messages)} messages)")

        # Apply limit if specified
        if limit:
            # Sort by date descending and take most recent
            contact_messages = sorted(contact_messages, key=lambda m: m[2] if m[2] else "", reverse=True)[:limit]
            # Re-sort chronologically for processing
            contact_messages = sorted(contact_messages, key=lambda m: m[2] if m[2] else "")

        # Group into conversations
        conversations = group_messages_into_conversations(contact_messages, gap_minutes=120)
        print(f"  Grouped into {len(conversations)} conversation chunks")

        for i, convo in enumerate(conversations):
            if len(convo) < 2:
                # Skip very short exchanges
                continue

            # Format for mem0
            mem0_messages = format_conversation_for_mem0(convo, contact_id, contact_name)

            if not mem0_messages:
                continue

            # Get conversation date range for context
            first_date = convo[0][2] if convo[0][2] else "unknown"
            last_date = convo[-1][2] if convo[-1][2] else "unknown"

            if dry_run:
                print(f"  [DRY RUN] Would add conversation {i+1}: {len(mem0_messages)} messages ({first_date} to {last_date})")
                for msg in mem0_messages[:3]:
                    preview = msg["content"][:80] + "..." if len(msg["content"]) > 80 else msg["content"]
                    print(f"    {msg['role']}: {preview}")
                if len(mem0_messages) > 3:
                    print(f"    ... and {len(mem0_messages) - 3} more messages")
            else:
                # Add a context-setting message at the start
                context_intro = [{
                    "role": "user",
                    "content": f"Here is a conversation I had with {contact_name} (contact: {contact_id}) from {first_date} to {last_date}. Please remember important details about them and our relationship."
                }]

                # Add to mem0 with contact metadata
                try:
                    result = MEM0.add(
                        context_intro + mem0_messages,
                        user_id=user_id,
                        metadata={
                            "source": "imessage",
                            "contact_id": contact_id,
                            "contact_name": contact_name,
                            "date_range": f"{first_date} to {last_date}",
                        }
                    )
                    memories_added = len(result.get("results", []))
                    total_memories_added += memories_added
                    print(f"  Added conversation {i+1}: {memories_added} memories extracted")
                except Exception as e:
                    print(f"  Error adding conversation {i+1}: {e}")

    if dry_run:
        print(f"\n[DRY RUN] Would process {len(conversations)} conversations")
    else:
        print(f"\nImport complete! Added {total_memories_added} memories total")


def main():
    parser = argparse.ArgumentParser(
        description="Import iMessage conversations into mem0 for MyPalClara",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # List all contacts with message counts
    python imessage_import.py --list-contacts

    # Import messages from specific phone numbers
    python imessage_import.py --contacts "+15551234567,+15559876543"

    # Import with custom names for contacts
    python imessage_import.py --contacts "+15551234567" --names "+15551234567=Mom"

    # Preview what would be imported (dry run)
    python imessage_import.py --contacts "+15551234567" --dry-run

    # Import only the 50 most recent messages per contact
    python imessage_import.py --contacts "+15551234567" --limit 50
        """
    )

    parser.add_argument(
        "--contacts",
        type=str,
        help="Comma-separated list of contact identifiers (phone numbers or emails)"
    )
    parser.add_argument(
        "--names",
        type=str,
        help="Comma-separated contact name mappings (e.g., '+15551234567=Mom,+15559876543=Dad')"
    )
    parser.add_argument(
        "--db-path",
        type=str,
        help="Path to iMessage database (defaults to ~/Library/Messages/chat.db)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of messages to import per contact"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be imported without actually importing"
    )
    parser.add_argument(
        "--list-contacts",
        action="store_true",
        help="List all contacts with message counts"
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=os.getenv("USER_ID", "demo-user"),
        help="User ID for mem0 storage"
    )

    args = parser.parse_args()

    if args.list_contacts:
        print("Fetching contacts from iMessage database...")
        contacts = list_contacts(args.db_path)

        # Sort by total message count
        sorted_contacts = sorted(
            contacts.items(),
            key=lambda x: x[1]["sent"] + x[1]["received"],
            reverse=True
        )

        print(f"\nFound {len(sorted_contacts)} contacts:\n")
        print(f"{'Contact':<40} {'Sent':>8} {'Recv':>8} {'Total':>8} {'Last Message':<20}")
        print("-" * 90)

        for contact_id, stats in sorted_contacts[:50]:  # Show top 50
            total = stats["sent"] + stats["received"]
            last_date = stats["last_date"][:10] if stats["last_date"] else "N/A"
            # Truncate long contact IDs
            display_id = contact_id[:38] + ".." if len(contact_id) > 40 else contact_id
            print(f"{display_id:<40} {stats['sent']:>8} {stats['received']:>8} {total:>8} {last_date:<20}")

        if len(sorted_contacts) > 50:
            print(f"\n... and {len(sorted_contacts) - 50} more contacts")

        return

    if not args.contacts:
        parser.print_help()
        print("\nError: --contacts is required (or use --list-contacts to see available contacts)")
        sys.exit(1)

    # Parse contacts
    contacts = [c.strip() for c in args.contacts.split(",")]

    # Parse contact names if provided
    contact_names = {}
    if args.names:
        for mapping in args.names.split(","):
            if "=" in mapping:
                contact_id, name = mapping.split("=", 1)
                contact_names[contact_id.strip()] = name.strip()

    import_to_mem0(
        contacts=contacts,
        contact_names=contact_names,
        db_path=args.db_path,
        limit=args.limit,
        dry_run=args.dry_run,
        user_id=args.user_id,
    )


if __name__ == "__main__":
    main()
