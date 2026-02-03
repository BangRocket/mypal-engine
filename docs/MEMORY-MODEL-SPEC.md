# Clara Memory Model Specification

A next-generation memory system for AI assistants, written from scratch in Rust. Combines proven patterns from MyPalClara, mem0 Platform features, and **cognitive science foundations from Vestige**.

**Design Philosophy:** "Memory that fades like yours does" — human-like memory with natural decay, spreading activation, and surprise-based encoding rather than perfect recall.

---

## Part 0: Cognitive Science Foundations

Based on 130+ years of memory research, implemented in [Vestige](https://github.com/samvallad33/vestige).

### 0.1 FSRS-6 Spaced Repetition

The [Free Spaced Repetition Scheduler](https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm) models memory through three variables:

| Variable | Symbol | Description |
|----------|--------|-------------|
| **Stability** | S | Time interval where retrievability = 90% |
| **Difficulty** | D | How hard to remember (1-10 scale) |
| **Retrievability** | R | Probability of successful recall (0-1) |

**Forgetting Curve (FSRS-6):**
```
R(t, S) = (1 + factor × t/S)^(-w₂₀)

where factor = 0.9^(-1/w₂₀) - 1
```

This power-law decay replaces our naive exponential decay. The `w₂₀` parameter (0.1-0.8) personalizes the curve per user.

**Stability After Access:**
```
S' = S × e^(w₁₇ × (G - 3 + w₁₈)) × S^(-w₁₉)

where G = grade (1=again, 2=hard, 3=good, 4=easy)
```

**Key Insight:** Memory stability increases more when:
- Material is simpler (lower difficulty)
- Current stability is lower (more room to grow)
- Retrievability is lower at time of review (desirable difficulty)

### 0.2 Spreading Activation

Based on [Collins & Loftus (1975)](https://en.wikipedia.org/wiki/Spreading_activation) and [ACT-R cognitive architecture](http://act-r.psy.cmu.edu/wordpress/wp-content/uploads/2012/12/66SATh.JRA.JVL.1983.pdf):

**Core Algorithm:**
1. Initialize source node(s) with activation = 1.0
2. Propagate activation to neighbors: `A_j += w_ij × A_i × decay`
3. Apply firing threshold: only nodes with `A > threshold` propagate
4. Repeat until convergence or max iterations

**ACT-R Base-Level Activation:**
```
B_i = ln(Σ t_j^(-d)) + β_i

where:
- t_j = time since jth access
- d = decay parameter (~0.5)
- β_i = base-level constant for item i
```

**Associative Activation:**
```
A_i = B_i + Σ W_j × S_ji + noise

where:
- W_j = attention weight on source j
- S_ji = associative strength from j to i
- noise ~ Logistic(0, s) for probabilistic retrieval
```

**Application to Memory Retrieval:**
- Query activates matching nodes
- Activation spreads through graph connections
- Memories with highest total activation are retrieved
- Explains semantic priming and contextual recall

### 0.3 Prediction Error Gating

Based on [dopaminergic reward prediction error research](https://www.nature.com/articles/s41562-019-0597-3):

**Principle:** Surprising information is encoded more strongly than expected information.

```
Prediction Error = |Actual - Expected|

if PE > threshold:
    encoding_strength *= (1 + PE × surprise_boost)
```

**Memory Operation Selection:**
| Condition | Action | Rationale |
|-----------|--------|-----------|
| PE ≈ 0 (expected) | Skip or weak update | Already known |
| PE > 0 (novel) | CREATE new memory | New information |
| Contradicts existing | UPDATE or SUPERSEDE | Correction needed |

**Implementation:**
```rust
enum MemoryDecision {
    Skip,           // PE ≈ 0, already known
    Create,         // Novel information
    Update(MemoryId), // Elaborates existing
    Supersede(MemoryId), // Contradicts existing
}

fn predict_error_gate(
    new_content: &str,
    existing: &[Memory],
    embedding: &[f32],
) -> MemoryDecision {
    let similarities = existing.iter()
        .map(|m| cosine_similarity(embedding, &m.embedding))
        .collect();

    let max_sim = similarities.max();

    if max_sim > 0.95 {
        MemoryDecision::Skip  // Nearly identical
    } else if max_sim > 0.8 {
        if contradicts(new_content, &existing[max_idx]) {
            MemoryDecision::Supersede(existing[max_idx].id)
        } else {
            MemoryDecision::Update(existing[max_idx].id)
        }
    } else {
        MemoryDecision::Create
    }
}
```

### 0.3.1 Contradiction Detection (Multi-Layer)

Embedding similarity alone misses subtle contradictions. Use layered detection:

| Layer | Method | Catches | Latency |
|-------|--------|---------|---------|
| **1. Embedding** | Cosine similarity < 0.3 on related topics | "likes coffee" vs "hates coffee" | ~1ms |
| **2. Keyword** | Negation patterns, antonyms | "is available" vs "is not available" | ~5ms |
| **3. Temporal** | Date/time conflicts | "free Tuesday" vs "meeting Tuesday 3pm" | ~10ms |
| **4. LLM** | Semantic contradiction check | Subtle logical conflicts | ~200ms |

```rust
struct ContradictionResult {
    contradicts: bool,
    confidence: f32,
    explanation: Option<String>,
    detection_layer: ContradictionLayer,
}

async fn detect_contradiction(
    new_content: &str,
    existing: &Memory,
    config: &ContradictionConfig,
) -> ContradictionResult {
    // Layer 1: Fast embedding check (opposite sentiment on similar topic)
    if let Some(result) = embedding_contradiction_check(new_content, existing) {
        return result;
    }

    // Layer 2: Keyword/pattern check
    if let Some(result) = keyword_contradiction_check(new_content, &existing.content) {
        return result;
    }

    // Layer 3: Temporal conflict check (if both contain dates/times)
    if let Some(result) = temporal_contradiction_check(new_content, &existing.content) {
        return result;
    }

    // Layer 4: LLM check (expensive, only if configured and uncertain)
    if config.enable_llm_check && needs_deeper_analysis(new_content, existing) {
        return llm_contradiction_check(new_content, &existing.content).await;
    }

    ContradictionResult::no_contradiction()
}

/// Patterns that suggest contradiction without LLM
const NEGATION_PATTERNS: &[(&str, &str)] = &[
    ("likes", "doesn't like"),
    ("loves", "hates"),
    ("is", "isn't"),
    ("can", "can't"),
    ("will", "won't"),
    ("always", "never"),
    ("available", "busy"),
    ("free", "occupied"),
];
```

**When to use LLM layer:**
- High-stakes memories (is_key = true)
- User corrections ("Actually, that's wrong...")
- Conflicting sources (same topic, different channels)

### 0.4 Synaptic Tagging & Capture

Based on [Frey & Morris (1997)](https://www.nature.com/articles/s42003-021-01778-y) synaptic consolidation research:

**Principle:** Memories go through consolidation phases:
1. **Immediate** (seconds): Temporary synaptic change
2. **Early** (minutes-hours): Tagged for potential consolidation
3. **Late** (hours-days): Protein synthesis, permanent storage

**Tagging Mechanism:**
```rust
struct MemoryTag {
    memory_id: MemoryId,
    tagged_at: DateTime<Utc>,
    tag_strength: f32,      // Decays over time
    consolidation_boost: f32, // From nearby novel events
}
```

**Behavioral Tagging:** Novel experiences boost consolidation of temporally-adjacent memories:
```rust
fn apply_behavioral_tagging(
    memories: &mut [Memory],
    novel_event: &Event,
    time_window: Duration,  // ~1 hour
) {
    for memory in memories {
        let time_diff = (novel_event.time - memory.created_at).abs();
        if time_diff < time_window {
            let boost = 1.0 - (time_diff.as_secs_f32() / time_window.as_secs_f32());
            memory.consolidation_score *= 1.0 + boost * TAGGING_FACTOR;
        }
    }
}
```

### 0.5 Dual-Strength Memory Model

Vestige tracks two independent strength dimensions:

| Dimension | Description | Affects |
|-----------|-------------|---------|
| **Retrieval Strength** | How likely to be recalled | Search ranking |
| **Storage Strength** | How well consolidated | Decay resistance |

```rust
struct MemoryStrength {
    /// Retrieval strength: probability of recall given cue
    /// Increases with practice, decreases with time
    retrieval: f32,

    /// Storage strength: resistance to forgetting
    /// Increases with spaced repetition, consolidation
    storage: f32,
}
```

**Paradox:** High storage strength can reduce retrieval strength gains (already consolidated memories benefit less from review).

### 0.6 Automatic Strength Feedback

The `promote()` / `demote()` operations should be **automatic**, not requiring explicit user action:

| Signal | Action | Grade | Example |
|--------|--------|-------|---------|
| **Memory used successfully** | promote | Good (3) | Response helped user |
| **Memory retrieved but unused** | - | - | Retrieved but not in final response |
| **User correction** | demote + supersede | Again (1) | "Actually, I don't like coffee" |
| **User confirmation** | promote | Easy (4) | "Yes, exactly!" |
| **Contradiction from trusted source** | demote | Hard (2) | New info from user conflicts |
| **Explicit feedback** | promote/demote | Based on rating | Thumbs up/down UI |

```rust
/// Signals that trigger automatic strength updates
enum StrengthSignal {
    /// Memory was retrieved and included in response
    UsedInResponse { memory_id: MemoryId, response_quality: Option<f32> },

    /// User explicitly corrected this memory
    UserCorrection { memory_id: MemoryId, correction: String },

    /// User confirmed/validated this memory
    UserConfirmation { memory_id: MemoryId },

    /// New information contradicts this memory
    Contradiction { memory_id: MemoryId, new_info: String, confidence: f32 },

    /// Explicit rating (if UI supports)
    ExplicitRating { memory_id: MemoryId, rating: i8 },  // -1, 0, +1
}

impl MemoryStore {
    /// Process strength signal and update memory accordingly
    async fn process_signal(&self, signal: StrengthSignal) -> Result<()> {
        match signal {
            StrengthSignal::UsedInResponse { memory_id, response_quality } => {
                // Memory helped generate response - promote
                let grade = response_quality
                    .map(|q| if q > 0.8 { Grade::Easy } else { Grade::Good })
                    .unwrap_or(Grade::Good);
                self.promote(memory_id, grade).await
            }
            StrengthSignal::UserCorrection { memory_id, correction } => {
                // User said we were wrong - demote and possibly supersede
                self.demote(memory_id).await?;
                self.smart_ingest(correction, scope).await  // Creates superseding memory
            }
            // ... etc
        }
    }
}
```

**Integration point:** The LLM orchestrator should track which memories were retrieved and used, then emit `UsedInResponse` signals after each turn.

---

## Part 1: Core Architecture (Proven Patterns)

These patterns are validated through MyPalClara production usage and should be first-class features in the Rust implementation.

### 1.1 Unified Entity Scoping

All memory operations are scoped by a 4-tuple:

| Scope | Purpose | Lifetime |
|-------|---------|----------|
| **app_id** | Multi-tenant isolation | Permanent |
| **user_id** | Individual user context | Permanent |
| **agent_id** | AI persona separation | Permanent |
| **run_id** | Session/conversation | Ephemeral |

```rust
struct Scope {
    app_id: Option<AppId>,    // Tenant isolation
    user_id: UserId,          // Required
    agent_id: AgentId,        // Defaults to "default"
    run_id: Option<RunId>,    // Session-scoped memories
}
```

**Key Insight**: Searches resolve one entity space at a time. Cannot AND user_id + agent_id in single query—use separate queries and merge results.

### 1.2 Memory Classification Hierarchy

Memories are classified into tiers that determine retrieval priority:

```
┌─────────────────────────────────────────────┐
│  KEY MEMORIES (always included, max 15)     │  ← Highest priority
├─────────────────────────────────────────────┤
│  CATEGORICAL MEMORIES (by category match)   │
├─────────────────────────────────────────────┤
│  SEMANTIC MEMORIES (by embedding similarity)│
├─────────────────────────────────────────────┤
│  GRAPH RELATIONS (entity connections)       │  ← Augments, doesn't reorder
└─────────────────────────────────────────────┘
```

| Tier | Description | Retrieval |
|------|-------------|-----------|
| **Key** | Critical facts flagged `is_key: true` | Always included first |
| **Categorical** | Matched by category filter | Before semantic search |
| **Semantic** | Embedding similarity to query | Standard retrieval |
| **Graph** | Entity relationships | Supplements results |

### 1.3 Category System (Graph-Integrated)

Categories are first-class citizens, integrated with the graph store for relationship awareness.

**Default Categories** (replaceable per-app):
```rust
const DEFAULT_CATEGORIES: &[&str] = &[
    "personal_details", "family", "professional",
    "preferences", "goals", "health", "projects",
    "relationships", "milestones", "misc"
];
```

**Category Features:**
| Feature | Description |
|---------|-------------|
| **Auto-classification** | LLM assigns categories on ingest |
| **Hierarchical** | Parent/child relationships (e.g., `work/projects/clara`) |
| **Graph-linked** | Categories become graph nodes, memories link to them |
| **App-scoped** | Each app can define custom category taxonomy |

**Graph Integration:**
```
[Memory: "Josh works at Acme Corp"]
    ├── category: professional
    ├── entity: Josh (person)
    ├── entity: Acme Corp (organization)
    └── relation: Josh --works_at--> Acme Corp
```

Categories in the graph enable queries like "all professional memories about organizations Josh is connected to."

### 1.4 Graph Memory (Enhanced)

**Entity Types:**
| Type | Examples |
|------|----------|
| `person` | Names, contacts, relationships |
| `organization` | Companies, teams, groups |
| `location` | Cities, addresses, places |
| `project` | Work items, goals, tasks |
| `concept` | Topics, themes, interests |
| `event` | Dates, milestones, appointments |
| `category` | Memory classification nodes |

**Relationship Types:**
| Relation | Example |
|----------|---------|
| `knows` | person → person |
| `works_at` | person → organization |
| `lives_in` | person → location |
| `working_on` | person → project |
| `interested_in` | person → concept |
| `member_of` | category → category (hierarchy) |
| `tagged_with` | memory → category |

**Graph Threshold Configuration:**
```rust
struct GraphConfig {
    /// Minimum embedding similarity to match existing nodes (0.0-1.0)
    /// Higher = more distinct nodes, Lower = more aggressive merging
    threshold: f32,  // Default: 0.7

    /// Enable/disable graph extraction per request
    enabled: bool,   // Default: true
}
```

| Scenario | Threshold | Effect |
|----------|-----------|--------|
| UUIDs/structured IDs | 0.95-0.99 | Maximum distinction |
| General purpose | 0.70-0.80 | Balanced (default) |
| Natural language | 0.60-0.70 | Aggressive merging ("Bob" = "Robert") |

**Graph + Vector Integration:**
- Graph relations **augment** vector search results
- They do **not** reorder semantic similarity rankings
- Returned alongside vector hits for additional context

### 1.5 Session Management (run_id)

Sessions provide ephemeral context that auto-expires:

| Feature | Description |
|---------|-------------|
| **Working Memory** | Short-term facts for current conversation |
| **Session Summary** | LLM-generated summary on timeout/end |
| **Context Snapshot** | Last N messages preserved for next session |
| **Auto-cleanup** | Memories with `run_id` expire with session |
| **Session Linking** | Chain sessions via `previous_run_id` |

```rust
struct Session {
    run_id: RunId,
    previous_run_id: Option<RunId>,
    started_at: DateTime<Utc>,
    last_activity: DateTime<Utc>,
    summary: Option<String>,
    context_snapshot: Vec<Message>,
}
```

**Triggers:**
- Summary generated every N messages (default: 10)
- Summary generated on idle timeout (default: 30 min)
- Context snapshot saved on session end

### 1.6 Temporal Context Signals

Track emotional and topical patterns over time:

**Emotional Context:**
| Field | Description |
|-------|-------------|
| `sentiment` | Per-message score (-1.0 to 1.0) |
| `emotional_arc` | Trajectory: stable, improving, declining, volatile |
| `energy_level` | Dominant emotion of conversation |
| `lookback_window` | Retrieve last 3-7 days of emotional context |

**Topic Recurrence:**
| Field | Description |
|-------|-------------|
| `topic` | Extracted topic name |
| `topic_type` | Entity (person, place) vs Theme (concern, goal) |
| `emotional_weight` | Light, moderate, heavy |
| `mention_count` | Frequency across conversations |
| `sentiment_trend` | Improving, declining, stable |

These become **memory metadata** and **graph nodes**, enabling queries like "topics Josh has been stressed about lately."

---

## Part 2: Advanced Retrieval System

### 2.1 Spreading Activation Retrieval

Core retrieval uses spreading activation through the memory graph:

```rust
struct ActivationConfig {
    /// Initial activation for query-matched nodes
    initial_activation: f32,  // Default: 1.0

    /// Decay per hop in graph
    decay_factor: f32,  // Default: 0.8

    /// Minimum activation to propagate
    firing_threshold: f32,  // Default: 0.1

    /// Maximum propagation depth
    max_depth: u32,  // Default: 3

    /// Include noise for probabilistic retrieval (ACT-R style)
    noise_scale: f32,  // Default: 0.1, 0 = deterministic
}

async fn spreading_activation_search(
    query_embedding: &[f32],
    graph: &MemoryGraph,
    config: &ActivationConfig,
) -> Vec<(MemoryId, f32)> {
    // 1. Find seed nodes via vector similarity
    let seeds = vector_search(query_embedding, TOP_K);

    // 2. Initialize activation
    let mut activation: HashMap<NodeId, f32> = HashMap::new();
    for (id, similarity) in seeds {
        activation.insert(id, similarity * config.initial_activation);
    }

    // 3. Spread activation through graph
    for depth in 0..config.max_depth {
        let mut next_activation = activation.clone();

        for (node_id, act) in &activation {
            if *act < config.firing_threshold {
                continue;
            }

            for (neighbor, edge_weight) in graph.neighbors(node_id) {
                let spread = act * edge_weight * config.decay_factor;
                *next_activation.entry(neighbor).or_insert(0.0) += spread;
            }
        }

        activation = next_activation;
    }

    // 4. Add noise for probabilistic retrieval
    if config.noise_scale > 0.0 {
        for act in activation.values_mut() {
            *act += logistic_noise(config.noise_scale);
        }
    }

    // 5. Apply FSRS retrievability weighting
    for (id, act) in activation.iter_mut() {
        let memory = get_memory(id);
        let retrievability = fsrs_retrievability(
            days_since(memory.accessed_at),
            memory.stability,
        );
        *act *= retrievability * memory.retrieval_strength;
    }

    // 6. Sort by activation and return
    activation.into_iter()
        .sorted_by(|a, b| b.1.partial_cmp(&a.1).unwrap())
        .collect()
}
```

### 2.2 Retrieval Strategy Composition

Four composable strategies with different latency/quality tradeoffs:

| Strategy | Latency | Purpose |
|----------|---------|---------|
| **Vector Search** | ~5ms | Embedding similarity (baseline) |
| **Spreading Activation** | +20-50ms | Graph-aware contextual recall |
| **Keyword Search** | +10ms | Exact term matches |
| **Reranking** | +150-200ms | Reorder by semantic relevance |

**Hybrid Configurations:**
| Mode | Strategies | Use Case |
|------|------------|----------|
| Quick | Vector only | Lowest latency |
| Standard | Vector + Spreading + Keyword | Typical applications |
| Precise | All + Reranking | Production, safety-critical |
| Cognitive | Spreading + FSRS weighting | Human-like recall |

### 2.2 Filter DSL (v2)

Composable filters with logical operators:

```rust
enum Filter {
    // Logical
    And(Vec<Filter>),
    Or(Vec<Filter>),
    Not(Box<Filter>),

    // Comparison
    Eq(Field, Value),
    Ne(Field, Value),
    Gt(Field, Value),
    Gte(Field, Value),
    Lt(Field, Value),
    Lte(Field, Value),

    // Collection
    In(Field, Vec<Value>),
    Contains(Field, String),      // Substring match
    IContains(Field, String),     // Case-insensitive

    // Special
    IsNull(Field),
    IsNotNull(Field),
    Exists(Field),                // For sparse metadata
}
```

**Filterable Fields:**
| Category | Fields |
|----------|--------|
| Scope | `user_id`, `agent_id`, `app_id`, `run_id` |
| Time | `created_at`, `updated_at`, `accessed_at` |
| Classification | `categories`, `is_key`, `memory_type` |
| Content | `keywords`, `metadata.*` |
| Identity | `id`, `ids` (batch) |

**Example:**
```rust
Filter::And(vec![
    Filter::In("categories".into(), vec!["work", "project"]),
    Filter::Gte("created_at".into(), "2024-01-01".into()),
    Filter::Not(Box::new(
        Filter::In("categories".into(), vec!["archived"])
    )),
])
```

### 2.3 Priority-Based Retrieval

Retrieval order with caps per tier:

```rust
struct RetrievalConfig {
    max_key_memories: usize,        // Default: 15
    max_categorical: usize,         // Default: 20
    max_semantic: usize,            // Default: 35
    max_graph_relations: usize,     // Default: 20
    max_query_chars: usize,         // Default: 6000
    dedup_threshold: f32,           // Default: 0.95
}
```

**Deduplication:** Memories with >95% embedding similarity are merged, keeping the most recent.

---

## Part 3: Multimodal Support

### 3.1 Supported Modalities

| Modality | Formats | Ingestion Method |
|----------|---------|------------------|
| **Text** | Plain text, Markdown | Direct |
| **Documents** | PDF, DOCX, TXT, MDX | URL or Base64 |
| **Images** | JPG, PNG, WebP, GIF | URL or Base64 |
| **Audio** | MP3, WAV, M4A | URL (transcription) |

### 3.2 Extraction Pipeline

All modalities are converted to text representations:

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│ Raw Content │ -> │  Extractor   │ -> │ Text + Meta │
└─────────────┘    └──────────────┘    └─────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
    [Image OCR]    [PDF Parser]    [Audio STT]
```

**Image Example:**
```
Input: Photo of pizza
Extracted: "Likes large pizza with toppings including cherry tomatoes,
           black olives, spinach, yellow bell peppers, ham, mushrooms"
Metadata: { modality: "image", original_url: "...", extracted_at: "..." }
```

### 3.3 Cross-Modal Retrieval

Retrieval operates on extracted text, but original modality is preserved:

```rust
struct Memory {
    content: String,              // Extracted text
    modality: Modality,           // Original type
    original_content: Option<ContentRef>,  // URL or hash
    // ...
}
```

---

## Part 4: Event System & Webhooks

### 4.1 Memory Events

| Event | Trigger | Payload |
|-------|---------|---------|
| `memory.created` | After add() | Memory + extraction details |
| `memory.updated` | After update() | Memory + diff |
| `memory.deleted` | After delete() | Memory ID + scope |
| `memory.accessed` | After search/get | Query + result count |
| `memory.decayed` | Auto-archival | Memory ID + decay score |

### 4.2 Webhook Configuration

```rust
struct WebhookConfig {
    url: String,
    events: Vec<EventType>,
    secret: Option<String>,      // HMAC signing
    retry_policy: RetryPolicy,
    timeout_ms: u32,
}
```

### 4.3 Internal Event Hooks

For in-process consumers (UI notifications, analytics):

```rust
trait MemoryEventHandler: Send + Sync {
    async fn on_memory_created(&self, memory: &Memory);
    async fn on_memory_retrieved(&self, query: &str, results: &[Memory]);
    async fn on_memory_extracted(&self, source: &str, memories: &[Memory]);
}
```

---

## Part 5: Memory Lifecycle

### 5.1 Versioning

Track memory mutations over time:

```rust
struct MemoryVersion {
    version: u32,
    content: String,
    metadata: Metadata,
    changed_at: DateTime<Utc>,
    changed_by: Option<String>,  // User or system
}
```

| Operation | Behavior |
|-----------|----------|
| Update | Increment version, store previous |
| Delete | Soft-delete, retain history |
| Restore | Create new version from historical |
| Point-in-time | Query state at specific timestamp |

### 5.2 FSRS-6 Memory Dynamics

Replace naive decay with cognitive science-based FSRS-6:

```rust
struct MemoryDynamics {
    /// Stability: interval where retrievability = 90%
    stability: f32,

    /// Difficulty: how hard to remember (1.0-10.0)
    difficulty: f32,

    /// Retrieval strength (dual-strength model)
    retrieval_strength: f32,

    /// Storage strength (dual-strength model)
    storage_strength: f32,

    /// Last review/access time
    last_accessed: DateTime<Utc>,

    /// Review count
    access_count: u32,

    /// FSRS-6 parameters (can be personalized per user)
    fsrs_params: Option<FsrsParams>,
}

struct FsrsParams {
    w: [f32; 21],  // FSRS-6 parameters
}

impl Default for FsrsParams {
    fn default() -> Self {
        Self {
            w: [0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194,
                0.001, 1.8722, 0.1666, 0.796, 1.4835, 0.0614, 0.2629,
                1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542]
        }
    }
}
```

**Retrievability Calculation (FSRS-6):**
```rust
fn retrievability(t: f32, stability: f32, w20: f32) -> f32 {
    let factor = 0.9_f32.powf(-1.0 / w20) - 1.0;
    (1.0 + factor * t / stability).powf(-w20)
}
```

**Stability Update After Access:**
```rust
fn update_stability(
    current_stability: f32,
    difficulty: f32,
    retrievability: f32,
    grade: Grade,  // Again, Hard, Good, Easy
    params: &FsrsParams,
) -> f32 {
    // FSRS-6 stability increase formula
    let s_inc = params.w[8] *
        (11.0 - difficulty).exp() *
        current_stability.powf(-params.w[9]) *
        ((1.0 - retrievability).exp() * params.w[10] - 1.0);

    current_stability * (1.0 + s_inc)
}
```

**Archival Policy:**
```rust
struct ArchivalConfig {
    /// Archive when retrievability drops below this
    archive_threshold: f32,  // Default: 0.1 (10% recall probability)

    /// Key memories never archived
    protect_key_memories: bool,

    /// Minimum age before archival eligible
    min_age_days: u32,  // Default: 30
}
```

### 5.3 Provenance Tracking

Track where memories come from:

```rust
struct Provenance {
    source_message_id: Option<String>,
    source_channel: Option<String>,
    source_modality: Modality,
    extraction_model: String,
    extraction_confidence: f32,
    corroborated_by: Vec<MemoryId>,  // Other memories confirming this
}
```

### 5.4 Memory Relationships

Track how memories relate to each other:

| Relation | Description | Example |
|----------|-------------|---------|
| `contradicts` | Conflicting information | "likes coffee" vs "hates coffee" |
| `supersedes` | Newer replaces older | Updated preference |
| `elaborates` | Adds detail | "likes coffee" → "prefers dark roast" |
| `related_to` | Topically connected | Same project/person |

---

## Part 6: Storage Architecture

### 6.1 Embedded-First Design

Default to embedded databases, scale to external when needed:

| Component | Embedded | External |
|-----------|----------|----------|
| Vector store | SQLite + sqlite-vss | PostgreSQL + pgvector |
| Graph store | Kuzu | Neo4j |
| Full-text | Tantivy | Elasticsearch |
| Cache | redb | Redis |

### 6.2 Storage Schema

**Memories Table:**
```sql
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    embedding BLOB NOT NULL,  -- f32 array

    -- Scoping
    app_id TEXT,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT 'default',
    run_id TEXT,

    -- Classification
    categories TEXT[],  -- JSON array
    is_key BOOLEAN DEFAULT FALSE,
    memory_type TEXT,

    -- Metadata
    metadata JSONB,
    keywords TEXT[],

    -- Provenance
    source_message_id TEXT,
    source_channel TEXT,
    modality TEXT DEFAULT 'text',
    extraction_model TEXT,
    confidence REAL,

    -- FSRS-6 Memory Dynamics (Cognitive Science)
    stability REAL DEFAULT 1.0,        -- Time for R=90%
    difficulty REAL DEFAULT 5.0,       -- 1-10 scale
    retrieval_strength REAL DEFAULT 1.0, -- Dual-strength: recall probability
    storage_strength REAL DEFAULT 0.5,   -- Dual-strength: consolidation

    -- Lifecycle
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    accessed_at TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    deleted_at TIMESTAMP,  -- Soft delete

    -- Synaptic Tagging
    tagged_at TIMESTAMP,              -- When tagged for consolidation
    consolidation_score REAL DEFAULT 0.0, -- Behavioral tagging boost

    -- Indexes
    INDEX idx_scope (app_id, user_id, agent_id),
    INDEX idx_run (run_id),
    INDEX idx_categories USING GIN (categories),
    INDEX idx_created (created_at),
    INDEX idx_stability (stability),
    INDEX idx_retrieval (retrieval_strength)
);
```

### 6.3 Export/Import

| Format | Use Case |
|--------|----------|
| JSON Lines | Streaming backup/restore |
| Parquet | Analytics, data science |
| SQLite dump | Full database backup |

---

## Part 7: API Design

### 7.1 Core Operations

```rust
impl MemoryStore {
    // === CRUD ===
    async fn add(&self, input: MemoryInput, opts: AddOptions) -> Result<MemoryId>;
    async fn get(&self, id: MemoryId) -> Result<Option<Memory>>;
    async fn update(&self, id: MemoryId, input: MemoryUpdate) -> Result<()>;
    async fn delete(&self, id: MemoryId) -> Result<()>;

    // === Smart Ingestion (Prediction Error Gating) ===
    /// Intelligently decides: Skip, Create, Update, or Supersede
    async fn smart_ingest(&self, input: MemoryInput, scope: Scope) -> Result<IngestResult>;

    // === Search ===
    async fn search(&self, query: SearchQuery) -> Result<SearchResults>;

    // === Memory Strength (FSRS-6) ===
    /// Mark memory as successfully recalled (increases stability)
    async fn promote(&self, id: MemoryId, grade: Grade) -> Result<()>;
    /// Mark memory as incorrect/outdated (decreases stability)
    async fn demote(&self, id: MemoryId) -> Result<()>;

    // === Batch ===
    async fn add_batch(&self, inputs: Vec<MemoryInput>) -> Result<Vec<MemoryId>>;
    async fn delete_all(&self, scope: Scope, filter: Option<Filter>) -> Result<u64>;

    // === Scoped Queries ===
    async fn get_all(&self, scope: Scope, filter: Option<Filter>) -> Result<Vec<Memory>>;
    async fn count(&self, scope: Scope, filter: Option<Filter>) -> Result<u64>;

    // === Graph ===
    async fn get_relations(&self, entity: &str, depth: u32) -> Result<Vec<Relation>>;
    async fn traverse(&self, query: GraphQuery) -> Result<GraphResults>;

    // === Intentions (Future Triggers) ===
    async fn set_intention(&self, intention: Intention) -> Result<IntentionId>;
    async fn check_intentions(&self, context: &str) -> Result<Vec<Intention>>;

    // === Maintenance ===
    async fn consolidate(&self) -> Result<ConsolidationReport>;  // Synaptic tagging
    async fn run_decay(&self) -> Result<DecayReport>;            // FSRS-6 decay
    async fn export(&self, scope: Scope, format: ExportFormat) -> Result<ExportHandle>;
    async fn stats(&self, scope: Scope) -> Result<MemoryStats>;
}

/// Result of smart_ingest with prediction error gating
enum IngestResult {
    Skipped { reason: String },
    Created { id: MemoryId },
    Updated { id: MemoryId, diff: String },
    Superseded { old_id: MemoryId, new_id: MemoryId },
}

/// FSRS-6 grade for memory strength updates
enum Grade {
    Again = 1,  // Complete failure to recall
    Hard = 2,   // Recalled with difficulty
    Good = 3,   // Recalled correctly
    Easy = 4,   // Recalled effortlessly
}

/// Future trigger / reminder
struct Intention {
    id: IntentionId,
    content: String,
    trigger_conditions: Vec<TriggerCondition>,
    created_at: DateTime<Utc>,
    expires_at: Option<DateTime<Utc>>,
    fired: bool,
    fire_once: bool,  // vs recurring
}

enum TriggerCondition {
    /// Fires when keyword/phrase appears in conversation
    KeywordMention(String),

    /// Fires when topic is discussed (semantic match)
    TopicDiscussed { topic: String, threshold: f32 },

    /// Fires after duration since creation
    TimeElapsed(Duration),

    /// Fires when specific user is mentioned
    UserMentioned(UserId),

    /// Fires on specific date/time
    ScheduledTime(DateTime<Utc>),

    /// Fires when entering specific channel/context
    ContextEntered(String),

    /// Compound: all conditions must match
    All(Vec<TriggerCondition>),

    /// Compound: any condition matches
    Any(Vec<TriggerCondition>),
}

/// When to check intentions (not on every message - that's expensive)
enum IntentionCheckStrategy {
    /// Check on every message (thorough but slow)
    EveryMessage,

    /// Check on session start only
    SessionStart,

    /// Check periodically (every N messages or M seconds)
    Periodic { messages: u32, seconds: u32 },

    /// Smart: lightweight keyword scan on every message,
    /// full semantic check only when keywords hint at match
    Tiered {
        /// Fast keyword bloom filter check
        keyword_scan: bool,
        /// Full semantic check interval
        full_check_interval: u32,
    },
}

impl Default for IntentionCheckStrategy {
    fn default() -> Self {
        // Default: tiered approach for efficiency
        Self::Tiered {
            keyword_scan: true,
            full_check_interval: 10,  // Full check every 10 messages
        }
    }
}
```

**Intention Checking Flow:**
```rust
impl MemoryStore {
    /// Check if any intentions should fire given current context
    /// Called by LLM orchestrator based on configured strategy
    async fn check_intentions(
        &self,
        context: &IntentionContext,
        strategy: &IntentionCheckStrategy,
    ) -> Result<Vec<FiredIntention>> {
        match strategy {
            IntentionCheckStrategy::Tiered { keyword_scan, full_check_interval } => {
                // Fast path: keyword bloom filter
                if *keyword_scan {
                    let keyword_hits = self.keyword_scan_intentions(&context.message).await?;
                    if !keyword_hits.is_empty() {
                        // Keywords matched - do full semantic check on those
                        return self.semantic_check_intentions(&keyword_hits, context).await;
                    }
                }

                // Periodic full check
                if context.message_count % full_check_interval == 0 {
                    return self.full_intention_check(context).await;
                }

                Ok(vec![])
            }
            // ... other strategies
        }
    }
}

struct IntentionContext {
    message: String,
    user_id: UserId,
    channel_id: Option<String>,
    message_count: u32,  // Messages since session start
    session_start: DateTime<Utc>,
}
```

### 7.2 Add Options

```rust
struct AddOptions {
    /// Extract and store graph relations
    enable_graph: bool,  // Default: true

    /// Categories to assign (or auto-classify if empty)
    categories: Vec<String>,

    /// Mark as key memory
    is_key: bool,

    /// Custom metadata
    metadata: HashMap<String, Value>,

    /// Source tracking
    provenance: Option<Provenance>,
}
```

### 7.3 Search Query

```rust
struct SearchQuery {
    /// Natural language query
    query: String,

    /// Scope constraints
    scope: Scope,

    /// Filter constraints
    filter: Option<Filter>,

    /// Retrieval strategy
    strategy: RetrievalStrategy,

    /// Result limits
    limit: usize,
    offset: usize,

    /// Include graph relations
    include_relations: bool,

    /// Include version history
    include_history: bool,
}

enum RetrievalStrategy {
    Quick,      // Keyword only
    Standard,   // Keyword + rerank
    Precise,    // Rerank + filter
    Custom { keyword: bool, rerank: bool, filter: bool },
}
```

---

## Part 8: Implementation Phases

> **Note on dependencies:** Spreading activation needs the graph. We introduce a minimal graph
> structure in Phase 2 (just nodes + edges), then build out full graph features in Phase 5.

### Phase 1: Core Engine
- [ ] Memory struct and serialization (serde)
- [ ] SQLite storage with sqlite-vss
- [ ] Basic CRUD operations
- [ ] Embedding generation (FastEmbed local, ~130MB)
- [ ] Vector similarity search
- [ ] Entity scoping (user, agent, app, run)

### Phase 2: Minimal Graph + FSRS-6 Foundations
**Graph (minimal for spreading activation):**
- [ ] Node/edge tables in SQLite (no Kuzu yet)
- [ ] Manual edge creation API
- [ ] Basic neighbor traversal

**FSRS-6 Math:**
- [ ] FSRS-6 parameter storage
- [ ] Retrievability calculation (forgetting curve)
- [ ] Stability updates on access
- [ ] Difficulty estimation
- [ ] Dual-strength model (retrieval + storage)
- [ ] promote/demote operations

**Spreading Activation (basic):**
- [ ] Activation propagation algorithm
- [ ] Configurable decay and threshold
- [ ] Integration with vector search

### Phase 3: Smart Ingestion
- [ ] Prediction error calculation
- [ ] Memory decision logic (Skip/Create/Update/Supersede)
- [ ] Multi-layer contradiction detection (embedding → keyword → temporal → LLM)
- [ ] Duplicate detection with similarity threshold
- [ ] smart_ingest API
- [ ] Automatic strength signals (UsedInResponse, UserCorrection, etc.)

### Phase 4: Classification & Categories
- [ ] Category system with defaults
- [ ] Auto-classification via LLM
- [ ] Key memory tier
- [ ] Filter DSL implementation

### Phase 5: Full Graph Memory
**Upgrade to Kuzu:**
- [ ] Kuzu integration (replace SQLite graph tables)
- [ ] Migration from minimal graph

**Entity Extraction:**
- [ ] LLM-based entity extraction pipeline
- [ ] Entity type classification (person, org, location, etc.)
- [ ] Relationship extraction and typing

**Graph Features:**
- [ ] Graph threshold configuration (entity merging)
- [ ] Hierarchical categories as graph nodes
- [ ] Category-memory edges
- [ ] ACT-R base-level activation
- [ ] Noise for probabilistic retrieval

### Phase 6: Consolidation & Tagging
- [ ] Synaptic tagging mechanism
- [ ] Behavioral tagging (novel event boost)
- [ ] Consolidation scoring
- [ ] consolidate() maintenance operation
- [ ] Multi-timescale memory phases (immediate → early → late)

### Phase 7: Advanced Retrieval
- [ ] Keyword search (Tantivy full-text index)
- [ ] Reranking pipeline
- [ ] FSRS-weighted retrieval
- [ ] Hybrid strategy configuration (Quick/Standard/Precise/Cognitive)
- [ ] Deduplication

### Phase 8: Intentions & Events
- [ ] Intention storage and trigger conditions
- [ ] Tiered intention checking (keyword bloom → semantic)
- [ ] Memory versioning (audit trail)
- [ ] Event system (created/updated/deleted/accessed)
- [ ] Webhook delivery with retry

### Phase 9: Multimodal
- [ ] Document extraction (PDF, DOCX via external libs)
- [ ] Image extraction (OCR, vision LLM)
- [ ] Audio transcription (Whisper or API)
- [ ] Cross-modal retrieval (search by any modality)

### Phase 10: Production Hardening
- [ ] PostgreSQL + pgvector backend option
- [ ] Neo4j backend option (alternative to Kuzu)
- [ ] Connection pooling (deadpool)
- [ ] Metrics/telemetry (OpenTelemetry)
- [ ] Export/import utilities (JSON Lines, Parquet)
- [ ] Python bindings (PyO3)
- [ ] MCP server interface (for Claude Code integration)

---

## Appendix A: Configuration

```toml
# clara-memory.toml

[storage]
backend = "sqlite"  # or "postgres"
path = "./data/memory.db"

[storage.postgres]
url = "postgres://..."
pool_size = 10

[vector]
dimensions = 1536
distance = "cosine"  # or "euclidean", "dot"

[graph]
backend = "kuzu"  # or "neo4j"
threshold = 0.7
enabled = true

[graph.neo4j]
url = "bolt://..."
username = "neo4j"
password = "..."

[retrieval]
max_key_memories = 15
max_categorical = 20
max_semantic = 35
max_graph_relations = 20
dedup_threshold = 0.95

[decay]
enabled = true
rate = 0.01
access_boost = 0.1
archive_threshold = 0.2
protect_key = true

[categories]
defaults = ["personal", "professional", "preferences", "goals", "misc"]
auto_classify = true

[llm]
provider = "openai"  # or "anthropic", "ollama"
model = "gpt-4o-mini"
embedding_model = "text-embedding-3-small"
```

---

## Appendix B: Reference Links

### Cognitive Science Foundations
- [Vestige - Cognitive Memory MCP Server](https://github.com/samvallad33/vestige)
- [FSRS-6 Algorithm](https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm)
- [FSRS Technical Explanation](https://expertium.github.io/Algorithm.html)
- [Spreading Activation - Wikipedia](https://en.wikipedia.org/wiki/Spreading_activation)
- [ACT-R Spreading Activation Theory](http://act-r.psy.cmu.edu/wordpress/wp-content/uploads/2012/12/66SATh.JRA.JVL.1983.pdf)
- [Prediction Errors & Memory (Nature)](https://www.nature.com/articles/s41562-019-0597-3)
- [Synaptic Tagging & Capture](https://www.nature.com/articles/s42003-021-01778-y)

### mem0 Platform
- [mem0 Open Source](https://github.com/mem0ai/mem0)
- [mem0 Platform vs OSS](https://docs.mem0.ai/platform/platform-vs-oss)
- [mem0 v2 Memory Filters](https://docs.mem0.ai/platform/features/v2-memory-filters)
- [mem0 Entity-Scoped Memory](https://docs.mem0.ai/platform/features/entity-scoped-memory)
- [mem0 Multimodal Support](https://docs.mem0.ai/platform/features/multimodal-support)
- [mem0 Custom Categories](https://docs.mem0.ai/platform/features/custom-categories)
- [mem0 Graph Memory](https://docs.mem0.ai/platform/features/graph-memory)
- [mem0 Graph Threshold](https://docs.mem0.ai/platform/features/graph-threshold)
- [mem0 Advanced Retrieval](https://docs.mem0.ai/platform/features/advanced-retrieval)
- [mem0 Advanced Operations](https://docs.mem0.ai/platform/advanced-memory-operations)

### Rust Ecosystem
- [sqlite-vss](https://github.com/asg017/sqlite-vss) - Vector similarity for SQLite
- [Kuzu](https://kuzudb.com/) - Embedded graph database
- [Tantivy](https://github.com/quickwit-oss/tantivy) - Full-text search
- [FastEmbed](https://github.com/Anush008/fastembed-rs) - Local embeddings (~130MB)
- [PyO3](https://pyo3.rs/) - Python bindings for Rust
