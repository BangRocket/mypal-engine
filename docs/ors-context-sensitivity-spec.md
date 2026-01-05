# Spec: Context-Sensitive Note Retrieval for ORS

## Problem Statement

ORS creates notes based on individual conversation moments, but when those notes are retrieved later, they're not validated against recent conversation history. This leads to:

1. **Hallucination amplification**: If the LLM hallucinates a task/detail, ORS creates a note about it. Later, ORS sees the note and treats it as ground truth, potentially creating follow-up notes about "failing" to complete the fictional task.

2. **Stale context surfacing**: A note about "check if they looked at the code" stays active even if that topic was resolved 3 conversations ago.

3. **Model quality dependency**: Weaker models (Sonnet) hallucinate more, and ORS dutifully records and acts on those hallucinations, creating feedback loops.

## Current Architecture

```
gather_full_context()
    └── get_notes_context() 
        └── Returns pending_notes filtered only by:
            - user_id match
            - surfaced == false
            - archived == false
            - ordered by relevance_score DESC

assess_situation()
    └── Receives pending_notes as raw context
    └── LLM sees notes without conversation grounding

decide_action()
    └── May create THINK notes based on stale/hallucinated context
    └── May SPEAK based on notes that no longer make sense
```

## Proposed Solution

Add a **context validation layer** between note retrieval and LLM assessment. This layer:

### 1. Recent Conversation Injection

Before `assess_situation()` runs, fetch the last N messages (or last conversation since idle timeout) for the user. This gives the LLM ground truth about what was *actually* discussed recently.

```python
# New field in ORSContext
recent_messages: list[dict] = field(default_factory=list)  # Last ~10 messages
```

### 2. Note Relevance Pre-filtering

Before passing notes to the LLM, run a lightweight check:

```python
async def validate_note_relevance(
    note: dict, 
    recent_messages: list[dict],
    llm_call: Callable  # Could use a cheaper/faster model
) -> tuple[bool, str]:
    """
    Check if a note is still relevant given recent conversation.
    
    Returns: (is_relevant, reason)
    """
```

Filter criteria:
- **Recency match**: Does the note reference something mentioned in recent conversation?
- **Resolution detection**: Did recent conversation resolve/supersede this topic?
- **Contradiction check**: Does recent conversation contradict the note?

### 3. Staleness Scoring

Add a `context_match_score` to notes during retrieval:

```python
@dataclass
class ValidatedNote:
    original_note: dict
    context_match_score: float  # 0.0 = no recent context, 1.0 = strongly matches recent convo
    validation_reason: str
```

Notes with low context match scores get:
- Deprioritized in the prompt
- Flagged as "possibly stale" to the LLM
- Faster relevance decay

### 4. Source Tracking for Notes

When creating notes, record more context about origin:

```python
# Enhanced source_context
{
    "from": "conversation_extraction" | "think_decision" | "manual",
    "model": "opus-4" | "sonnet-4",  # Which model created this note
    "confidence": "high" | "medium" | "low",  # Self-assessed
    "grounding_messages": ["msg_id_1", "msg_id_2"]  # Specific messages that triggered this
}
```

Notes from weaker models or with low confidence get additional scrutiny during validation.

### 5. Updated Assessment Prompt

Modify `SITUATION_ASSESSMENT_PROMPT` to include:

```
**Recent conversation (last {n} messages):**
{recent_messages}

**Notes you've been collecting:**
{validated_notes}

When reviewing notes, check them against the recent conversation above.
If a note references something not present in recent conversation, 
consider whether it's still relevant or has gone stale.
```

## Implementation Approach

### Phase 1: Conversation Injection (Low effort, high impact)
- Add recent message retrieval to `gather_full_context()`
- Update prompts to include recent messages
- Let the LLM do the validation naturally

### Phase 2: Pre-filtering (Medium effort)
- Add lightweight validation pass before notes reach assessment
- Could use Haiku for fast/cheap validation
- Filter out obviously stale notes

### Phase 3: Source Tracking (Higher effort)
- Enhance note creation to capture model/confidence
- Weight notes by source quality during retrieval
- Build decay curves based on source reliability

## Success Criteria

1. Notes created from hallucinations don't spawn follow-up notes
2. Resolved topics don't resurface as "open threads"
3. ORS decisions are grounded in actual recent conversation
4. Feedback loops are broken within 1-2 cycles instead of persisting

## Open Questions

1. **How many recent messages?** 10? 20? Last conversation since idle?
2. **Validation model**: Same model as ORS, or cheaper (Haiku)?
3. **Aggressive vs conservative filtering**: Err toward keeping notes or discarding?
4. **User visibility**: Should users be able to see why notes were filtered?

## Files to Modify

- `organic_response_system.py`
  - `gather_full_context()` - add recent messages
  - `get_notes_context()` - add validation layer
  - `assess_situation()` - updated prompt
  - `create_note()` - enhanced source tracking
  
- `db/models.py` (possibly)
  - Add `source_model`, `confidence` fields to `ProactiveNote`
