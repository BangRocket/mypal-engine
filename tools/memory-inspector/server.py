#!/usr/bin/env python3
"""Memory Inspector — web tool for querying Clara's memory system.

Input a word or sentence and see exactly what context would be sent
to the LLM: episodes, semantic memories, graph relations, user profile.

Usage:
    python tools/memory-inspector/server.py [--port 8898] [--user discord-271274659385835521]
"""

import argparse
import json
import sys
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()


class InspectorHandler(SimpleHTTPRequestHandler):
    palace = None
    memory_manager = None
    default_user_id = None
    html_path = None

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            with open(self.html_path, "rb") as f:
                self.wfile.write(f.read())
        elif self.path == "/api/status":
            status = {
                "palace": self.palace is not None,
                "episode_store": self.memory_manager.episode_store is not None if self.memory_manager else False,
                "graph": (
                    hasattr(self.palace, "graph") and self.palace.graph is not None
                ) if self.palace else False,
                "default_user": self.default_user_id,
            }
            self._json_response(status)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/query":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            query = body.get("query", "")
            user_id = body.get("user_id", self.default_user_id)
            result = self._run_query(query, user_id)
            self._json_response(result)
        else:
            self.send_error(404)

    def _json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _run_query(self, query: str, user_id: str) -> dict:
        """Run the query through all memory layers and return raw results."""
        result = {
            "query": query,
            "user_id": user_id,
            "timing": {},
            "semantic_memories": [],
            "episodes": [],
            "recent_episodes": [],
            "graph_relations": [],
            "active_arcs": [],
            "layered_context": "",
        }

        if not self.palace:
            result["error"] = "Palace not initialized"
            return result

        # Semantic memory search
        t0 = time.time()
        try:
            raw = self.palace.search(query, user_id=user_id, agent_id="clara", limit=15)
            memories = raw.get("results", [])
            result["semantic_memories"] = [
                {
                    "id": m.get("id", ""),
                    "memory": m.get("memory", m.get("data", "")),
                    "score": m.get("score", 0),
                    "metadata": {
                        k: v for k, v in m.get("metadata", {}).items()
                        if k not in ("data", "hash")
                    },
                }
                for m in memories
            ]
        except Exception as e:
            result["semantic_memories"] = [{"error": str(e)}]
        result["timing"]["semantic"] = round(time.time() - t0, 3)

        # Episode search
        t0 = time.time()
        mm = self.memory_manager
        if mm and mm.episode_store:
            try:
                episodes = mm.episode_store.search(query, user_id, limit=5, min_significance=0.0)
                result["episodes"] = [
                    {
                        "id": getattr(ep, "id", ""),
                        "summary": getattr(ep, "summary", ""),
                        "content": getattr(ep, "content", "")[:500],
                        "topics": getattr(ep, "topics", []),
                        "emotional_tone": getattr(ep, "emotional_tone", ""),
                        "significance": getattr(ep, "significance", 0),
                        "timestamp": str(getattr(ep, "timestamp", "")),
                    }
                    for ep in episodes
                ]
            except Exception as e:
                result["episodes"] = [{"error": str(e)}]

            # Recent episodes
            try:
                recent = mm.episode_store.get_recent(user_id, limit=5)
                result["recent_episodes"] = [
                    {
                        "id": getattr(ep, "id", ""),
                        "summary": getattr(ep, "summary", ""),
                        "emotional_tone": getattr(ep, "emotional_tone", ""),
                        "significance": getattr(ep, "significance", 0),
                        "timestamp": str(getattr(ep, "timestamp", "")),
                    }
                    for ep in recent
                ]
            except Exception as e:
                result["recent_episodes"] = [{"error": str(e)}]

            # Active arcs
            try:
                arcs = mm.episode_store.get_active_arcs(user_id)
                result["active_arcs"] = [
                    {
                        "id": getattr(a, "id", ""),
                        "title": getattr(a, "title", ""),
                        "summary": getattr(a, "summary", ""),
                        "status": getattr(a, "status", ""),
                        "emotional_trajectory": getattr(a, "emotional_trajectory", ""),
                    }
                    for a in arcs
                ]
            except Exception as e:
                result["active_arcs"] = [{"error": str(e)}]

        result["timing"]["episodes"] = round(time.time() - t0, 3)

        # Graph relations
        t0 = time.time()
        if hasattr(self.palace, "graph") and self.palace.graph is not None:
            try:
                relations = self.palace.graph.search(query, {"user_id": user_id}, limit=20)
                result["graph_relations"] = relations
            except Exception as e:
                result["graph_relations"] = [{"error": str(e)}]
        result["timing"]["graph"] = round(time.time() - t0, 3)

        # Full layered context (what the LLM actually sees)
        t0 = time.time()
        try:
            from mypalclara.core.memory.retrieval_layers import LayeredRetrieval

            retrieval = LayeredRetrieval()
            result["layered_context"] = retrieval.build_context(
                user_id=user_id,
                semantic_memories=result.get("semantic_memories", []),
                recent_episodes=result.get("recent_episodes", []),
                active_arcs=result.get("active_arcs", []),
                graph_context=result.get("graph_relations", []),
                relevant_episodes=result.get("episodes", []),
                relevant_memories=result.get("semantic_memories", []),
                relevant_relations=result.get("graph_relations", []),
            )
        except Exception as e:
            result["layered_context"] = f"Error: {e}"
        result["timing"]["layered"] = round(time.time() - t0, 3)

        result["timing"]["total"] = round(
            sum(v for v in result["timing"].values()), 3
        )

        return result

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="Memory Inspector")
    parser.add_argument("--port", type=int, default=8898)
    parser.add_argument("--user", type=str, default="discord-271274659385835521")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    from mypalclara.core.memory.config import PALACE

    if PALACE is None:
        print("ERROR: Palace not initialized")
        sys.exit(1)

    from mypalclara.core import make_llm
    from mypalclara.core.memory_manager import MemoryManager

    llm = make_llm()
    mm = MemoryManager.initialize(llm_callable=llm)

    print("Palace: OK")
    print(f"Episode store: {'OK' if mm.episode_store else 'unavailable'}")
    print(f"Graph: {'OK' if hasattr(PALACE, 'graph') and PALACE.graph else 'unavailable'}")

    InspectorHandler.palace = PALACE
    InspectorHandler.memory_manager = mm
    InspectorHandler.default_user_id = args.user
    InspectorHandler.html_path = str(Path(__file__).parent / "index.html")

    server = HTTPServer(("127.0.0.1", args.port), InspectorHandler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"\nMemory Inspector at {url}")
    print("Press Ctrl+C to stop\n")

    if not args.no_browser:
        import webbrowser
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
