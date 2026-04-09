#!/usr/bin/env python3
"""Chat Cleaner — web tool for reviewing and cleaning Discord chat exports.

Usage:
    python tools/chat-cleaner/server.py path/to/export.json [--port 8899]

Opens a browser with the chat displayed. Each message is color-coded:
  Green  = keep
  Yellow = maybe (review needed)
  Red    = remove

Decisions are saved to a sidecar file: <export>_decisions.json
"""

import argparse
import json
import os
import re
import sys
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Auto-flagging rules
# ---------------------------------------------------------------------------

def auto_flag(messages: list[dict]) -> dict[str, dict]:
    """Auto-flag messages based on noise patterns.

    Returns dict mapping message ID -> {status, reason, user_id, user_name}.
    """
    decisions = {}

    for m in messages:
        mid = m["id"]
        content = m.get("content", "").strip()
        author = m.get("author", {}).get("name", "")
        author_id = m.get("author", {}).get("id", "")
        mtype = m.get("type", "Default")

        entry = {
            "status": "keep",
            "reason": "",
            "user_id": author_id,
            "user_name": author,
        }

        # Red: tool status lines (Clara's -# prefix)
        if content.startswith("-# ") and author != "stairmaster401":
            entry["status"] = "remove"
            entry["reason"] = "Tool status line"

        # Red: slash command empty responses (type 20)
        elif mtype in ("20", 20) and not content:
            entry["status"] = "remove"
            entry["reason"] = "Empty slash command response"

        # Red: pinned message notifications
        elif mtype == "ChannelPinnedMessage":
            entry["status"] = "remove"
            entry["reason"] = "Pin notification"

        # Red: completely empty
        elif not content:
            entry["status"] = "remove"
            entry["reason"] = "Empty message"

        # Red: long error/log pastes from user
        elif (
            author == "stairmaster401"
            and len(content) > 300
            and any(
                x in content
                for x in [
                    "Traceback (most recent",
                    "FAILED",
                    "File \"/",
                    "Could not find platform",
                ]
            )
        ):
            entry["status"] = "remove"
            entry["reason"] = "Error/log paste"

        # Yellow: messages that are mostly code blocks (might be testing)
        elif content.count("```") >= 2 and len(content) > 500:
            code_content = re.findall(r"```[\s\S]*?```", content)
            code_len = sum(len(c) for c in code_content)
            if code_len > len(content) * 0.8:
                entry["status"] = "maybe"
                entry["reason"] = "Mostly code block"

        # Yellow: very short bot responses that might be errors
        elif author != "stairmaster401" and content.startswith("⚠️"):
            entry["status"] = "maybe"
            entry["reason"] = "Warning message"

        # Yellow: messages with only an attachment/embed and no text
        elif not content and (m.get("attachments") or m.get("embeds")):
            entry["status"] = "maybe"
            entry["reason"] = "Attachment/embed only"

        decisions[mid] = entry

    return decisions


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------

class CleanerHandler(SimpleHTTPRequestHandler):
    chat_data = None
    decisions = None
    decisions_path = None
    html_path = None

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            with open(self.html_path, "rb") as f:
                self.wfile.write(f.read())

        elif parsed.path == "/api/chat":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.chat_data).encode())

        elif parsed.path == "/api/decisions":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.decisions).encode())

        elif parsed.path == "/api/stats":
            stats = {"total": len(self.chat_data.get("messages", []))}
            for status in ("keep", "maybe", "remove"):
                stats[status] = sum(
                    1 for d in self.decisions.values() if d["status"] == status
                )
            # Per-user stats
            user_stats = {}
            for d in self.decisions.values():
                uid = d.get("user_name", "unknown")
                if uid not in user_stats:
                    user_stats[uid] = {"keep": 0, "maybe": 0, "remove": 0, "total": 0}
                user_stats[uid][d["status"]] += 1
                user_stats[uid]["total"] += 1
            stats["users"] = user_stats
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(stats).encode())

        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/decision":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            mid = body.get("id")
            status = body.get("status")
            reason = body.get("reason", "")

            if mid and status in ("keep", "maybe", "remove"):
                if mid in self.decisions:
                    self.decisions[mid]["status"] = status
                    self.decisions[mid]["reason"] = reason
                else:
                    self.decisions[mid] = {
                        "status": status,
                        "reason": reason,
                        "user_id": "",
                        "user_name": "",
                    }
                self._save_decisions()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        elif parsed.path == "/api/bulk":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            updates = body.get("updates", [])

            for update in updates:
                mid = update.get("id")
                status = update.get("status")
                reason = update.get("reason", "")
                if mid and status in ("keep", "maybe", "remove"):
                    if mid in self.decisions:
                        self.decisions[mid]["status"] = status
                        self.decisions[mid]["reason"] = reason

            self._save_decisions()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "updated": len(updates)}).encode())

        else:
            self.send_error(404)

    def _save_decisions(self):
        with open(self.decisions_path, "w") as f:
            json.dump(self.decisions, f, indent=2)

    def log_message(self, format, *args):
        pass  # Suppress request logging


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Chat Cleaner web tool")
    parser.add_argument("chat_file", help="Path to Discord chat export JSON")
    parser.add_argument("--port", type=int, default=8899, help="Port (default: 8899)")
    parser.add_argument("--bind", type=str, default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    chat_path = Path(args.chat_file)
    if not chat_path.exists():
        print(f"File not found: {chat_path}")
        sys.exit(1)

    decisions_path = chat_path.with_name(chat_path.stem + "_decisions.json")
    html_path = Path(__file__).parent / "index.html"

    print(f"Loading {chat_path}...")
    with open(chat_path) as f:
        chat_data = json.load(f)

    total = len(chat_data.get("messages", []))
    print(f"Loaded {total} messages")

    # Load existing decisions or auto-flag
    if decisions_path.exists():
        print(f"Loading existing decisions from {decisions_path}")
        with open(decisions_path) as f:
            decisions = json.load(f)
        # Flag any new messages not in decisions
        new_flags = auto_flag(chat_data["messages"])
        added = 0
        for mid, entry in new_flags.items():
            if mid not in decisions:
                decisions[mid] = entry
                added += 1
        if added:
            print(f"  Added {added} new message flags")
    else:
        print("Auto-flagging messages...")
        decisions = auto_flag(chat_data["messages"])
        with open(decisions_path, "w") as f:
            json.dump(decisions, f, indent=2)

    # Stats
    counts = {"keep": 0, "maybe": 0, "remove": 0}
    for d in decisions.values():
        counts[d["status"]] += 1
    print(f"  Keep: {counts['keep']}  Maybe: {counts['maybe']}  Remove: {counts['remove']}")

    # Set up handler
    CleanerHandler.chat_data = chat_data
    CleanerHandler.decisions = decisions
    CleanerHandler.decisions_path = str(decisions_path)
    CleanerHandler.html_path = str(html_path)

    server = HTTPServer((args.bind, args.port), CleanerHandler)
    url = f"http://{args.bind}:{args.port}"
    print(f"\nChat Cleaner running at {url}")
    print("Press Ctrl+C to stop\n")

    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
