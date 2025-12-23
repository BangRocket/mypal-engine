#!/usr/bin/env python3
"""
Bootstrap memory extraction and ingestion pipeline.

Extracts atomic memories from user_profile.txt into namespaced JSON files,
then optionally upserts them into mem0.

Usage:
    python -m src.bootstrap_memory --input inputs/user_profile.txt --out generated/
    python -m src.bootstrap_memory --input inputs/user_profile.txt --out generated/ --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from clara_core import make_llm

# Namespaces
NAMESPACES = [
    "profile_bio",
    "interaction_style",
    "project_seed",
    "project_context:creative_portfolio",
    "restricted:sensitive",
]

# Default memory policy
DEFAULT_POLICY = {
    "always_load": ["profile_bio", "interaction_style", "project_seed"],
    "conditional_load": {
        "project_context:creative_portfolio": [
            "game", "story", "plot", "godot", "engine", "puzzle",
            "ARG", "memento", "clara", "project", "creative"
        ],
        "restricted:sensitive": [
            "anxiety", "depression", "adhd", "burnout", "overwhelmed",
            "meds", "therapy", "stress", "mental", "tired", "exhausted"
        ]
    },
    "top_k": {
        "profile_bio": 12,
        "interaction_style": 10,
        "project_seed": 12,
        "project_context:creative_portfolio": 18,
        "restricted:sensitive": 6
    }
}

EXTRACTION_PROMPT = """You are a memory extraction system. Your task is to extract atomic, factual memories from user profile text and categorize them into specific namespaces.

INPUT TEXT:
{profile_text}

NAMESPACES AND RULES:

1. **profile_bio** - Stable biographical facts (likely true for months/years):
   - Name, preferred name, location
   - Family members (spouse, children with names/DOBs)
   - Career identity, broad skills
   - Long-lived constraints (limited time, etc.)
   EXCLUDE: daily moods, one-off tasks, transient plans, sensitive medical/mental-health details

2. **interaction_style** - Communication preferences:
   - Tone preferences (casual, candid, adult)
   - Boundaries (not overly flirty, don't mimic user)
   - Formatting preferences (likes lists for complex topics)
   EXCLUDE: bio facts, project architecture

3. **project_seed** - Assistant operating principles:
   - Continuity across conversations
   - Selective memory over raw logs
   - Local-first, debuggable preferences
   - Actionability, breaking tasks into chunks
   EXCLUDE: exact library versions, implementation details

4. **project_context:creative_portfolio** - Creative projects and preferences:
   - Stable canon of user projects (games, ARG ideas)
   - Design constraints and preferences that recur
   - Tool preferences (Godot, raylib, etc.)
   - Aesthetic preferences (90s, retro, pastel)
   EXCLUDE: unrelated life bio facts, assistant style rules

5. **restricted:sensitive** - Mental health and sensitive content:
   - ADHD, anxiety, depression mentions
   - Medication, therapy, burnout themes
   - Deep emotional struggles
   NOTE: This content should NOT be loaded by default

OUTPUT FORMAT:
Return a JSON object with namespace keys, each containing an array of memory items:

```json
{{
  "profile_bio": [
    {{"key": "name.preferred", "value": "Atomic factual statement.", "confidence": 0.95}}
  ],
  "interaction_style": [...],
  "project_seed": [...],
  "project_context:creative_portfolio": [...],
  "restricted:sensitive": [...]
}}
```

RULES:
- Each "key" must be dot-separated, stable, and deterministic (e.g., "family.spouse.name")
- Each "value" must be atomic (1-2 sentences max, no paragraphs)
- Avoid duplicates; each fact should appear in only ONE namespace
- confidence should be 0.85-0.95 for clear facts, lower for inferences
- Extract ALL relevant facts - be thorough but atomic

Return ONLY the JSON object, no other text."""


def extract_memories_with_llm(profile_text: str) -> dict:
    """Use LLM to extract atomic memories from profile text."""
    llm = make_llm()

    prompt = EXTRACTION_PROMPT.format(profile_text=profile_text)

    messages = [
        {"role": "system", "content": "You are a precise memory extraction system. Output only valid JSON."},
        {"role": "user", "content": prompt}
    ]

    print("[bootstrap] Extracting memories with LLM...")
    response = llm(messages)

    # Parse JSON from response
    try:
        # Try to find JSON in response
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(response)
    except json.JSONDecodeError as e:
        print(f"[bootstrap] Error parsing LLM response: {e}")
        print(f"[bootstrap] Response was: {response[:500]}...")
        raise


def normalize_key(key: str) -> str:
    """Normalize a key to be stable and deterministic."""
    # Lowercase, replace spaces with dots, remove special chars
    key = key.lower().strip()
    key = re.sub(r'[^a-z0-9._]', '.', key)
    key = re.sub(r'\.+', '.', key)  # Collapse multiple dots
    key = key.strip('.')
    return key


def validate_memories(memories: dict) -> dict:
    """Validate and clean extracted memories."""
    validated = {}

    for namespace in NAMESPACES:
        items = memories.get(namespace, [])
        validated[namespace] = []

        seen_keys = set()
        for item in items:
            if not isinstance(item, dict):
                continue

            key = normalize_key(item.get("key", ""))
            value = str(item.get("value", "")).strip()
            confidence = float(item.get("confidence", 0.85))

            if not key or not value:
                continue

            # Skip duplicates
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # Truncate overly long values
            if len(value) > 300:
                value = value[:297] + "..."

            # Clamp confidence
            confidence = max(0.0, min(1.0, confidence))

            validated[namespace].append({
                "key": key,
                "value": value,
                "confidence": confidence
            })

    return validated


def write_json_files(memories: dict, output_dir: Path):
    """Write memory namespaces to JSON files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Map namespace to filename
    filename_map = {
        "profile_bio": "profile_bio.json",
        "interaction_style": "interaction_style.json",
        "project_seed": "project_seed.json",
        "project_context:creative_portfolio": "project_context_creative_portfolio.json",
        "restricted:sensitive": "restricted_sensitive.json",
    }

    for namespace, items in memories.items():
        filename = filename_map.get(namespace)
        if not filename:
            continue

        filepath = output_dir / filename
        with open(filepath, 'w') as f:
            json.dump(items, f, indent=2)
        print(f"[bootstrap] Wrote {len(items)} items to {filepath}")

    # Write memory policy
    policy_path = output_dir / "memory_policy.json"
    with open(policy_path, 'w') as f:
        json.dump(DEFAULT_POLICY, f, indent=2)
    print(f"[bootstrap] Wrote memory policy to {policy_path}")


def load_existing_memories(output_dir: Path) -> dict:
    """Load existing generated memories for comparison."""
    existing = {}

    filename_map = {
        "profile_bio": "profile_bio.json",
        "interaction_style": "interaction_style.json",
        "project_seed": "project_seed.json",
        "project_context:creative_portfolio": "project_context_creative_portfolio.json",
        "restricted:sensitive": "restricted_sensitive.json",
    }

    for namespace, filename in filename_map.items():
        filepath = output_dir / filename
        if filepath.exists():
            with open(filepath) as f:
                existing[namespace] = json.load(f)

    return existing


def group_memories_for_graph(memories: dict) -> list[dict]:
    """
    Group related memories into relationship-rich conversations for better graph extraction.
    Returns a list of conversation groups with metadata.
    """
    groups = []

    # Group 1: Core identity and family relationships
    family_items = []
    bio_items = memories.get("profile_bio", [])
    for item in bio_items:
        if any(k in item["key"] for k in ["name", "family", "location"]):
            family_items.append(item)

    if family_items:
        # Build a relationship-rich statement
        facts = [item["value"] for item in family_items]
        content = "Here's information about me and my family: " + ". ".join(facts)
        groups.append({
            "namespace": "profile_bio",
            "category": "identity_and_family",
            "content": content,
            "items": family_items,
        })

    # Group 2: Career and skills
    career_items = [item for item in bio_items if any(k in item["key"] for k in ["career", "skill"])]
    if career_items:
        facts = [item["value"] for item in career_items]
        content = "About my career and skills: " + ". ".join(facts)
        groups.append({
            "namespace": "profile_bio",
            "category": "career",
            "content": content,
            "items": career_items,
        })

    # Group 3: Interaction preferences (as a cohesive set)
    style_items = memories.get("interaction_style", [])
    if style_items:
        facts = [item["value"] for item in style_items]
        content = "My preferences for how Clara should interact with me: " + ". ".join(facts)
        groups.append({
            "namespace": "interaction_style",
            "category": "preferences",
            "content": content,
            "items": style_items,
        })

    # Group 4: Project seed principles
    seed_items = memories.get("project_seed", [])
    if seed_items:
        facts = [item["value"] for item in seed_items]
        content = "Core principles for how the assistant should operate: " + ". ".join(facts)
        groups.append({
            "namespace": "project_seed",
            "category": "principles",
            "content": content,
            "items": seed_items,
        })

    # Group 5: Creative projects - group by project
    creative_items = memories.get("project_context:creative_portfolio", [])
    project_groups = {}
    other_creative = []

    for item in creative_items:
        key = item["key"]
        if key.startswith("project."):
            # Extract project name (e.g., "project.midnight_hunters.concept" -> "midnight_hunters")
            parts = key.split(".")
            if len(parts) >= 2:
                proj_name = parts[1]
                if proj_name not in project_groups:
                    project_groups[proj_name] = []
                project_groups[proj_name].append(item)
        else:
            other_creative.append(item)

    # Add each project as a group
    for proj_name, items in project_groups.items():
        facts = [item["value"] for item in items]
        display_name = proj_name.replace("_", " ").title()
        content = f"About my game project '{display_name}': " + ". ".join(facts)
        groups.append({
            "namespace": "project_context:creative_portfolio",
            "category": f"project_{proj_name}",
            "content": content,
            "items": items,
        })

    # Other creative preferences
    if other_creative:
        facts = [item["value"] for item in other_creative]
        content = "My creative preferences and interests: " + ". ".join(facts)
        groups.append({
            "namespace": "project_context:creative_portfolio",
            "category": "preferences",
            "content": content,
            "items": other_creative,
        })

    # Group 6: Sensitive/mental health - flag but include
    sensitive_items = memories.get("restricted:sensitive", [])
    if sensitive_items:
        facts = [item["value"] for item in sensitive_items]
        content = "Personal context about my mental health and challenges: " + ". ".join(facts)
        groups.append({
            "namespace": "restricted:sensitive",
            "category": "mental_health",
            "content": content,
            "items": sensitive_items,
            "sensitive": True,  # Flag it
        })

    return groups


def link_user_to_person(user_id: str):
    """Link the mem0 user_id node to person nodes in Neo4j graph."""
    try:
        from neo4j import GraphDatabase
        NEO4J_URL = os.getenv("NEO4J_URL")
        NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
        NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

        if not NEO4J_URL or not NEO4J_PASSWORD:
            return

        driver = GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
        user_node_name = f"user_id:_{user_id}"

        with driver.session() as session:
            # Find person nodes that should be linked (joshua, josh)
            result = session.run('''
                MATCH (u:__User__ {name: $user_name})
                MATCH (p:person)
                WHERE p.name IN ["joshua", "josh"]
                MERGE (u)-[r:is_person]->(p)
                RETURN p.name as linked
            ''', user_name=user_node_name)

            linked = [record["linked"] for record in result]
            if linked:
                print(f"[bootstrap] Linked {user_node_name} to person nodes: {linked}")

        driver.close()
    except Exception as e:
        print(f"[bootstrap] Warning: Could not link user to person in graph: {e}")


def apply_to_mem0(memories: dict, user_id: str, dry_run: bool = False):
    """Upsert memories to mem0 with relationship-rich grouping for graph extraction."""
    from config.mem0 import MEM0

    if MEM0 is None:
        print("[bootstrap] Error: mem0 is not initialized")
        return

    # Group memories for better graph extraction
    groups = group_memories_for_graph(memories)

    total_added = 0
    total_relations = 0

    for group in groups:
        namespace = group["namespace"]
        category = group["category"]
        content = group["content"]
        is_sensitive = group.get("sensitive", False)

        metadata = {
            "namespace": namespace,
            "category": category,
            "source": "user_profile.txt",
            "bootstrap": True,
            "sensitive": is_sensitive,
        }

        # Add confidence from items
        items = group.get("items", [])
        if items:
            avg_confidence = sum(i["confidence"] for i in items) / len(items)
            metadata["confidence"] = round(avg_confidence, 2)

        if dry_run:
            print(f"  [DRY RUN] Would add group: {namespace}/{category} ({len(items)} items)")
            continue

        # Add as a conversation for rich graph extraction
        try:
            messages = [
                {"role": "user", "content": content},
                {"role": "assistant", "content": f"I've noted this information about {category.replace('_', ' ')}."}
            ]

            result = MEM0.add(
                messages,
                user_id=user_id,
                metadata=metadata,
            )

            added = len(result.get("results", []))
            relations = result.get("relations", {})
            added_entities = relations.get("added_entities", [])
            relation_count = sum(len(e) for e in added_entities if isinstance(e, list))

            total_added += added
            total_relations += relation_count

            sensitive_tag = " [SENSITIVE]" if is_sensitive else ""
            print(f"  Added {namespace}/{category}: {added} memories, {relation_count} relations{sensitive_tag}")

        except Exception as e:
            print(f"  Error adding {namespace}/{category}: {e}")

    if not dry_run:
        print(f"\n[bootstrap] Total: {total_added} memories, {total_relations} graph relations")

        # Link user_id node to person nodes in graph
        link_user_to_person(user_id)


def consolidate_memories(memories: dict) -> dict:
    """Consolidate memories: merge near-duplicates, enforce caps."""
    consolidated = {}

    for namespace, items in memories.items():
        # Get cap for this namespace
        cap = DEFAULT_POLICY["top_k"].get(namespace, 20)

        # Simple deduplication by checking value similarity
        seen_values = []
        unique_items = []

        for item in items:
            value_lower = item["value"].lower()

            # Check if similar value exists
            is_duplicate = False
            for seen in seen_values:
                # Simple similarity: check if one contains most of the other
                if len(value_lower) > 20 and len(seen) > 20:
                    shorter = min(value_lower, seen, key=len)
                    longer = max(value_lower, seen, key=len)
                    if shorter in longer or longer in shorter:
                        is_duplicate = True
                        break

            if not is_duplicate:
                seen_values.append(value_lower)
                unique_items.append(item)

        # Enforce cap (keep highest confidence items)
        if len(unique_items) > cap:
            unique_items.sort(key=lambda x: x["confidence"], reverse=True)
            unique_items = unique_items[:cap]

        consolidated[namespace] = unique_items

    return consolidated


def main():
    parser = argparse.ArgumentParser(
        description="Extract and bootstrap memories from user profile"
    )
    parser.add_argument(
        "--input", "-i",
        type=str,
        default="inputs/user_profile.txt",
        help="Input profile text file"
    )
    parser.add_argument(
        "--out", "-o",
        type=str,
        default="generated/",
        help="Output directory for JSON files"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply memories to mem0 (without this flag, only generates JSON)"
    )
    parser.add_argument(
        "--user", "-u",
        type=str,
        default=os.getenv("USER_ID", "demo-user"),
        help="User ID for mem0"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration even if JSON files exist"
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.out)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    # Read input
    print(f"[bootstrap] Reading {input_path}...")
    profile_text = input_path.read_text()

    # Check for existing generated files
    existing = load_existing_memories(output_dir)
    if existing and not args.force:
        print("[bootstrap] Found existing generated files. Use --force to regenerate.")
        memories = existing
    else:
        # Extract with LLM
        raw_memories = extract_memories_with_llm(profile_text)

        # Validate and clean
        memories = validate_memories(raw_memories)

        # Consolidate (dedupe, enforce caps)
        memories = consolidate_memories(memories)

        # Write JSON files
        write_json_files(memories, output_dir)

    # Summary
    print("\n[bootstrap] Memory extraction summary:")
    for namespace, items in memories.items():
        print(f"  {namespace}: {len(items)} items")

    # Apply to mem0 if requested
    if args.apply:
        print(f"\n[bootstrap] Applying to mem0 for user '{args.user}'...")
        apply_to_mem0(memories, args.user)
    else:
        print("\n[bootstrap] Dry run complete. Use --apply to upsert to mem0.")


if __name__ == "__main__":
    main()
