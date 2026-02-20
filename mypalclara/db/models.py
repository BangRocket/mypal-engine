from __future__ import annotations

import uuid
from datetime import datetime, timezone


def utcnow():
    """Return current UTC time (naive, for SQLite compatibility)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from db.base import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=gen_uuid)
    owner_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)

    sessions = relationship("Session", back_populates="project")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    user_id = Column(String, nullable=False)
    context_id = Column(String, nullable=False, default="default")  # Platform context identifier
    title = Column(String, nullable=True)  # Thread title for UI
    archived = Column(String, default="false", nullable=False)  # "true" or "false"
    started_at = Column(DateTime, default=utcnow, nullable=False)
    last_activity_at = Column(DateTime, default=utcnow, nullable=False)
    previous_session_id = Column(String, nullable=True)
    context_snapshot = Column(Text, nullable=True)
    session_summary = Column(Text, nullable=True)  # LLM-generated summary

    # Index for efficient session lookups by user_id + context_id + project_id
    __table_args__ = (Index("ix_session_user_context_project", "user_id", "context_id", "project_id"),)

    project = relationship("Project", back_populates="sessions")
    messages = relationship("Message", back_populates="session")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    user_id = Column(String, nullable=False)
    role = Column(String, nullable=False)  # 'user' | 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    session = relationship("Session", back_populates="messages")

    __table_args__ = (Index("ix_message_session_created", "session_id", "created_at"),)


class ChannelSummary(Base):
    """Rolling summary of Discord channel conversations."""

    __tablename__ = "channel_summaries"

    id = Column(String, primary_key=True, default=gen_uuid)
    channel_id = Column(String, nullable=False, unique=True)  # discord-channel-{id}
    summary = Column(Text, default="")
    summary_cutoff_at = Column(DateTime, nullable=True)  # newest summarized msg ts
    last_updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ChannelConfig(Base):
    """Per-channel configuration for Clara's behavior."""

    __tablename__ = "channel_configs"

    id = Column(String, primary_key=True, default=gen_uuid)
    channel_id = Column(String, nullable=False, unique=True, index=True)
    guild_id = Column(String, nullable=False, index=True)  # Discord server ID
    mode = Column(String, default="mention", nullable=False)  # active, mention, off
    configured_by = Column(String, nullable=True)  # User ID who set this
    configured_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class LogEntry(Base):
    """Persistent log entries stored in the database."""

    __tablename__ = "log_entries"

    id = Column(String, primary_key=True, default=gen_uuid)
    timestamp = Column(DateTime, default=utcnow, nullable=False, index=True)
    level = Column(String(10), nullable=False, index=True)  # INFO, WARNING, ERROR, CRITICAL
    logger_name = Column(String(100), nullable=False, index=True)  # e.g., "api", "discord"
    message = Column(Text, nullable=False)
    module = Column(String(100), nullable=True)
    function = Column(String(100), nullable=True)
    line_number = Column(Integer, nullable=True)
    exception = Column(Text, nullable=True)  # Traceback if error
    extra_data = Column(Text, nullable=True)  # JSON for additional context
    user_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)


class GoogleOAuthToken(Base):
    """OAuth 2.0 tokens for Google Workspace integration (per-user)."""

    __tablename__ = "google_oauth_tokens"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, unique=True, index=True)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    token_type = Column(String, default="Bearer")
    expires_at = Column(DateTime, nullable=True)
    scopes = Column(Text, nullable=True)  # JSON array of granted scopes
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# =============================================================================
# Proactive Conversation Models
# =============================================================================


class ProactiveMessage(Base):
    """History of proactive messages sent by Clara."""

    __tablename__ = "proactive_messages"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    channel_id = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    priority = Column(String, nullable=False)  # low, normal, high, critical
    reason = Column(Text, nullable=True)  # Why Clara decided to reach out
    sent_at = Column(DateTime, default=utcnow, nullable=False)
    response_received = Column(String, default="false")  # "true" or "false"
    response_at = Column(DateTime, nullable=True)


class UserInteractionPattern(Base):
    """Learned interaction patterns per user for proactive timing."""

    __tablename__ = "user_interaction_patterns"

    user_id = Column(String, primary_key=True)
    last_interaction_at = Column(DateTime, nullable=True)
    last_interaction_channel = Column(String, nullable=True)
    last_interaction_summary = Column(Text, nullable=True)  # LLM-extracted summary of last convo
    last_interaction_energy = Column(String, nullable=True)  # stressed, focused, casual, tired, excited, frustrated
    typical_active_hours = Column(Text, nullable=True)  # JSON: {"weekday": [9,17], "weekend": [10,22]}
    timezone = Column(String, nullable=True)  # IANA timezone (e.g., "America/New_York")
    timezone_source = Column(String, nullable=True)  # "calendar", "explicit", "inferred"
    avg_response_time_seconds = Column(Integer, nullable=True)
    explicit_signals = Column(Text, nullable=True)  # JSON: {"do_not_disturb": false, "busy_until": null}
    proactive_success_rate = Column(Integer, nullable=True)  # Percentage (0-100)
    # ORS enhancements
    proactive_response_rate = Column(Integer, nullable=True)  # % of proactive messages acknowledged (0-100)
    preferred_proactive_times = Column(Text, nullable=True)  # JSON: When user responds best to proactive
    preferred_proactive_types = Column(Text, nullable=True)  # JSON: What kinds of reach-outs work
    topic_receptiveness = Column(Text, nullable=True)  # JSON: Which topics land vs. get ignored
    explicit_boundaries = Column(Text, nullable=True)  # JSON: Things user said not to do
    open_threads = Column(Text, nullable=True)  # JSON: Unresolved topics from conversations
    contact_cadence_days = Column(Float, nullable=True)  # Avg days between interactions (inferred)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ProactiveNote(Base):
    """Internal notes/observations for ORS - accumulated understanding over time."""

    __tablename__ = "proactive_notes"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    note = Column(Text, nullable=False)  # The observation/thought
    note_type = Column(String, nullable=True)  # observation, question, follow_up, connection
    source_context = Column(Text, nullable=True)  # JSON: What triggered this note
    source_model = Column(String, nullable=True)  # Model that created this note (opus-4, sonnet-4, etc)
    source_confidence = Column(String, nullable=True)  # Self-assessed confidence (high, medium, low)
    grounding_message_ids = Column(Text, nullable=True)  # JSON: Message IDs that triggered this note
    connections = Column(Text, nullable=True)  # JSON: List of related note IDs
    relevance_score = Column(Integer, default=100)  # 0-100, decays over time
    surface_conditions = Column(Text, nullable=True)  # JSON: When should this come up
    surface_at = Column(DateTime, nullable=True)  # Specific time to potentially bring this up
    expires_at = Column(DateTime, nullable=True)  # Time-sensitive notes expire
    surfaced = Column(String, default="false")  # "true" or "false"
    surfaced_at = Column(DateTime, nullable=True)
    archived = Column(String, default="false")  # "true" or "false" - stale or surfaced notes
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ProactiveAssessment(Base):
    """Situation assessments for ORS continuity - records of THINK state processing."""

    __tablename__ = "proactive_assessments"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    context_snapshot = Column(Text, nullable=True)  # JSON: Full context at time of assessment
    assessment = Column(Text, nullable=True)  # LLM's read on the situation
    decision = Column(String, nullable=False)  # WAIT, THINK, or SPEAK
    reasoning = Column(Text, nullable=True)  # Why this decision was made
    note_created = Column(String, nullable=True)  # Note ID if THINK created a note
    message_sent = Column(Text, nullable=True)  # Message text if SPEAK
    next_check_at = Column(DateTime, nullable=True)  # When to check again
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)


# =============================================================================
# Email Monitoring Models
# =============================================================================


class EmailAccount(Base):
    """User email account connections for monitoring."""

    __tablename__ = "email_accounts"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    email_address = Column(String, nullable=False)
    provider_type = Column(String, nullable=False)  # "gmail", "imap"

    # Gmail uses existing GoogleOAuthToken via user_id
    # No separate tokens stored here - gmail provider checks GoogleOAuthToken

    # IMAP credentials (encrypted with Fernet)
    imap_server = Column(String, nullable=True)
    imap_port = Column(Integer, nullable=True)
    imap_username = Column(String, nullable=True)
    imap_password = Column(Text, nullable=True)  # Encrypted

    # Polling configuration
    enabled = Column(String, default="true")  # "true" or "false"
    poll_interval_minutes = Column(Integer, default=5)
    last_checked_at = Column(DateTime, nullable=True)
    last_seen_uid = Column(String, nullable=True)  # For incremental fetching
    last_seen_timestamp = Column(DateTime, nullable=True)

    # Status tracking
    status = Column(String, default="active")  # active, error, disabled
    last_error = Column(Text, nullable=True)
    error_count = Column(Integer, default=0)

    # Discord notification settings
    alert_channel_id = Column(String, nullable=True)
    ping_on_alert = Column(String, default="false")  # "true" = @mention
    quiet_hours_start = Column(Integer, nullable=True)  # Hour (0-23)
    quiet_hours_end = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class EmailRule(Base):
    """Per-user importance rules for email filtering."""

    __tablename__ = "email_rules"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    account_id = Column(String, ForeignKey("email_accounts.id"), nullable=True)  # null = all accounts

    name = Column(String, nullable=False)
    enabled = Column(String, default="true")  # "true" or "false"
    priority = Column(Integer, default=0)  # Higher = checked first

    # Rule definition JSON:
    # {
    #   "conditions": {
    #     "sender_contains": ["recruiter", "hr@"],
    #     "sender_domain": ["linkedin.com", "greenhouse.io"],
    #     "subject_contains": ["interview", "offer"],
    #     "subject_regex": "(?i)interview.*schedule",
    #     "body_contains": ["please respond"],
    #     "has_attachments": true
    #   },
    #   "match_mode": "any" | "all"
    # }
    rule_definition = Column(Text, nullable=False)

    # Action configuration
    importance = Column(String, default="normal")  # low, normal, high, urgent
    custom_alert_message = Column(Text, nullable=True)  # Template for alert
    override_ping = Column(String, nullable=True)  # "true"/"false" or null (inherit)

    # Preset tracking
    preset_name = Column(String, nullable=True)  # "job_hunting", "urgent", etc.

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class EmailAlert(Base):
    """History of email alerts sent to Discord (for dedup and tracking)."""

    __tablename__ = "email_alerts"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    account_id = Column(String, ForeignKey("email_accounts.id"), nullable=False)
    rule_id = Column(String, ForeignKey("email_rules.id"), nullable=True)

    # Email info (denormalized for history)
    email_uid = Column(String, nullable=False, index=True)  # For dedup
    email_from = Column(String, nullable=False)
    email_subject = Column(String, nullable=False)
    email_snippet = Column(Text, nullable=True)  # First ~200 chars
    email_received_at = Column(DateTime, nullable=True)

    # Alert info
    channel_id = Column(String, nullable=False)
    message_id = Column(String, nullable=True)  # Discord message ID
    importance = Column(String, nullable=False)
    was_pinged = Column(String, default="false")

    sent_at = Column(DateTime, default=utcnow)


# =============================================================================
# Guild Configuration Models
# =============================================================================


class GuildConfig(Base):
    """Per-guild (Discord server) configuration for Clara settings."""

    __tablename__ = "guild_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, unique=True, nullable=False, index=True)

    # Model settings
    default_tier = Column(String, nullable=True)  # high/mid/low or None (use env default)
    auto_tier_enabled = Column(String, default="false")  # "true" or "false"

    # ORS (Organic Response System) settings
    ors_enabled = Column(String, default="false")  # "true" or "false"
    ors_channel_id = Column(String, nullable=True)  # Channel for proactive messages
    ors_quiet_start = Column(String, nullable=True)  # HH:MM format
    ors_quiet_end = Column(String, nullable=True)  # HH:MM format

    # Sandbox settings
    sandbox_mode = Column(String, default="auto")  # docker, incus, incus-vm, auto

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# =============================================================================
# Memory Dynamics Models (FSRS-6 + Intentions)
# =============================================================================


class MemoryDynamics(Base):
    """FSRS-6 scheduling data for mem0 memories.

    Tracks the spaced repetition state for each memory to enable
    intelligent retrieval weighting based on the forgetting curve.

    Attributes:
        memory_id: The mem0 memory ID this tracks
        user_id: Owner of the memory
        stability: Days until retrievability drops to 90% (FSRS S parameter)
        difficulty: Inherent difficulty 1-10 (FSRS D parameter)
        retrieval_strength: Current recall ability (Bjork dual-strength R_r)
        storage_strength: Consolidated long-term strength (Bjork dual-strength R_s)
        last_accessed_at: When the memory was last used/retrieved
        access_count: Total number of times memory was accessed
    """

    __tablename__ = "memory_dynamics"

    memory_id = Column(String, primary_key=True)  # mem0 memory ID
    user_id = Column(String, nullable=False, index=True)

    # FSRS-6 core fields
    stability = Column(Float, default=1.0)  # Days until R=90%
    difficulty = Column(Float, default=5.0)  # 1-10 scale
    retrieval_strength = Column(Float, default=1.0)  # Dual-strength: recall
    storage_strength = Column(Float, default=0.5)  # Dual-strength: consolidation

    # Key memory flag (high importance, always retrieved)
    is_key = Column(Boolean, default=False)
    importance_weight = Column(Float, default=1.0)

    # Classification category (personal, professional, preferences, goals, emotional, temporal)
    category = Column(String(50), nullable=True, default=None)

    # Tags (JSON array stored as string, e.g. '["work","important"]')
    tags = Column(Text, nullable=True, default=None)

    # Access tracking
    last_accessed_at = Column(DateTime, nullable=True)
    access_count = Column(Integer, default=0)

    # Lifecycle
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Composite index for efficient user+recency queries
    __table_args__ = (Index("ix_memory_dynamics_user_accessed", "user_id", "last_accessed_at"),)


class MemoryAccessLog(Base):
    """History of memory accesses for FSRS calculations.

    Records each time a memory is retrieved and used, along with
    an inferred grade based on usage signals. This enables the
    FSRS algorithm to update stability and difficulty over time.

    Attributes:
        memory_id: The memory that was accessed
        user_id: User who accessed it
        grade: FSRS grade (1=Again, 2=Hard, 3=Good, 4=Easy)
        signal_type: What triggered this access
        retrievability_at_access: R value when accessed (for FSRS)
    """

    __tablename__ = "memory_access_log"

    id = Column(String, primary_key=True, default=gen_uuid)
    memory_id = Column(
        String,
        ForeignKey("memory_dynamics.memory_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(String, nullable=False, index=True)

    # FSRS grade: 1=Again, 2=Hard, 3=Good, 4=Easy
    grade = Column(Integer, nullable=False)

    # Context about the access
    signal_type = Column(String)  # "used_in_response", "user_correction", etc.
    retrievability_at_access = Column(Float)  # R value when accessed
    context = Column(Text, nullable=True)  # Optional JSON context

    accessed_at = Column(DateTime, default=utcnow, index=True)

    # Composite index for analytics queries
    __table_args__ = (Index("ix_memory_access_user_time", "user_id", "accessed_at"),)


class Intention(Base):
    """Future triggers/reminders for proactive memory surfacing.

    Intentions are conditional reminders that Clara stores to surface
    information at the right time. They can be triggered by:
    - Keywords in user messages
    - Semantic similarity to topics
    - Specific times
    - Contextual conditions

    Attributes:
        user_id: User this intention is for
        agent_id: Bot persona (default "clara")
        content: What to remind about
        trigger_conditions: JSON defining when to fire
        fired: Whether this intention has been triggered
        fire_once: If true, delete after firing
    """

    __tablename__ = "intentions"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    agent_id = Column(String, default="mypalclara")

    # Content
    content = Column(Text, nullable=False)  # What to remind about
    source_memory_id = Column(String, nullable=True)  # Optional link to source memory

    # Trigger conditions (JSON)
    # Examples:
    # {"type": "keyword", "keywords": ["meeting", "standup"]}
    # {"type": "time", "at": "2024-01-15T09:00:00Z"}
    # {"type": "topic", "topic": "project deadline", "threshold": 0.7}
    # {"type": "context", "conditions": {"channel_name": "work", "time_of_day": "morning"}}
    trigger_conditions = Column(Text, nullable=False)  # JSON string

    # Priority for ordering when multiple intentions fire
    priority = Column(Integer, default=0)  # Higher = more important

    # State
    fired = Column(Boolean, default=False)
    fire_once = Column(Boolean, default=True)  # Delete after firing if true

    # Lifecycle
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=True)  # Optional expiration
    fired_at = Column(DateTime, nullable=True)

    # Composite index for efficient unfired intention queries
    __table_args__ = (
        Index("ix_intention_user_unfired", "user_id", "fired"),
        Index("ix_intention_expires", "expires_at"),
    )


class MemorySupersession(Base):
    """Tracks when memories are superseded by newer information.

    When prediction error gating detects a contradiction, the old
    memory is marked as superseded and linked to the new one.

    Attributes:
        old_memory_id: The superseded memory
        new_memory_id: The memory that replaced it
        reason: Why the supersession occurred
        confidence: How confident we are in the supersession
    """

    __tablename__ = "memory_supersessions"

    id = Column(String, primary_key=True, default=gen_uuid)
    old_memory_id = Column(String, nullable=False, index=True)
    new_memory_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)

    reason = Column(String, nullable=True)  # "contradiction", "update", "correction"
    confidence = Column(Float, default=1.0)  # 0-1 confidence in supersession
    details = Column(Text, nullable=True)  # JSON with additional context

    created_at = Column(DateTime, default=utcnow)


class MemoryHistory(Base):
    """History of memory changes for Rook (Clara's memory system).

    Tracks ADD, UPDATE, and DELETE events for memories stored in the
    vector store. Used for auditing and debugging memory operations.

    Attributes:
        memory_id: The vector store memory ID this event relates to
        old_memory: Previous memory content (null for ADD events)
        new_memory: New memory content (null for DELETE events)
        event: Event type (ADD, UPDATE, DELETE)
        is_deleted: Whether the memory was deleted
        actor_id: ID of the actor who triggered this change
        role: Role associated with the memory (user, assistant)
    """

    __tablename__ = "memory_history"

    id = Column(String, primary_key=True, default=gen_uuid)
    memory_id = Column(String, nullable=False, index=True)
    old_memory = Column(Text, nullable=True)
    new_memory = Column(Text, nullable=True)
    event = Column(String, nullable=False)  # ADD, UPDATE, DELETE
    is_deleted = Column(Boolean, default=False)
    actor_id = Column(String, nullable=True)
    role = Column(String, nullable=True)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_memory_history_memory_id_created", "memory_id", "created_at"),)


# =============================================================================
# Web Interface Identity Models
# =============================================================================


class CanonicalUser(Base):
    """Unified user identity across platforms.

    Maps to one or more PlatformLinks, providing a single identity
    for the web interface and cross-platform memory queries.
    """

    __tablename__ = "canonical_users"

    id = Column(String, primary_key=True, default=gen_uuid)
    display_name = Column(String, nullable=False)
    primary_email = Column(String, nullable=True, unique=True)
    avatar_url = Column(String, nullable=True)
    status = Column(String, default="active", server_default="active", nullable=False)  # pending, active, suspended
    is_admin = Column(Boolean, default=False, server_default="0", nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    platform_links = relationship("PlatformLink", back_populates="canonical_user")
    oauth_tokens = relationship("OAuthToken", back_populates="canonical_user")
    web_sessions = relationship("WebSession", back_populates="canonical_user")


class PlatformLink(Base):
    """Maps a platform user to a canonical user.

    The prefixed_user_id matches the existing user_id format used
    throughout the system (e.g., 'discord-123', 'teams-abc').
    """

    __tablename__ = "platform_links"

    id = Column(String, primary_key=True, default=gen_uuid)
    canonical_user_id = Column(String, ForeignKey("canonical_users.id"), nullable=False)
    platform = Column(String, nullable=False)  # discord, teams, slack, google, etc.
    platform_user_id = Column(String, nullable=False)  # Raw platform ID
    prefixed_user_id = Column(String, nullable=False, unique=True)  # e.g., discord-123
    display_name = Column(String, nullable=True)
    linked_at = Column(DateTime, default=utcnow)
    linked_via = Column(String, nullable=True)  # oauth, manual, backfill

    __table_args__ = (Index("ix_platform_link_platform_user", "platform", "platform_user_id", unique=True),)

    canonical_user = relationship("CanonicalUser", back_populates="platform_links")


class OAuthToken(Base):
    """OAuth2 tokens for web interface authentication."""

    __tablename__ = "oauth_tokens"

    id = Column(String, primary_key=True, default=gen_uuid)
    canonical_user_id = Column(String, ForeignKey("canonical_users.id"), nullable=False)
    provider = Column(String, nullable=False)  # discord, google, teams
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    scopes = Column(Text, nullable=True)  # JSON array
    provider_user_id = Column(String, nullable=True)
    provider_data = Column(Text, nullable=True)  # JSON
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_oauth_token_user_provider", "canonical_user_id", "provider", unique=True),)

    canonical_user = relationship("CanonicalUser", back_populates="oauth_tokens")


class WebSession(Base):
    """Active web sessions for the web interface."""

    __tablename__ = "web_sessions"

    id = Column(String, primary_key=True, default=gen_uuid)
    canonical_user_id = Column(String, ForeignKey("canonical_users.id"), nullable=False)
    session_token_hash = Column(String, nullable=False, unique=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, default=utcnow)
    revoked = Column(Boolean, default=False)

    canonical_user = relationship("CanonicalUser", back_populates="web_sessions")


# =============================================================================
# Tool Audit Log Model
# =============================================================================


class ToolAuditLog(Base):
    """Audit log for tool executions.

    Records every tool call for compliance, debugging, and analytics.
    """

    __tablename__ = "tool_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=utcnow, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    tool_name = Column(String, nullable=False, index=True)
    platform = Column(String, nullable=False)
    parameters = Column(Text, nullable=True)  # JSON string of parameters
    result_status = Column(String, nullable=False)  # success, error, denied
    error_message = Column(Text, nullable=True)
    execution_time_ms = Column(Integer, nullable=True)
    risk_level = Column(String, nullable=True)  # safe, moderate, dangerous
    intent = Column(String, nullable=True)  # read, write, execute, network
    channel_id = Column(String, nullable=True, index=True)

    # Composite index for efficient queries
    __table_args__ = (
        Index("ix_tool_audit_user_time", "user_id", "timestamp"),
        Index("ix_tool_audit_tool_time", "tool_name", "timestamp"),
    )


# =============================================================================
# Personality Evolution Models
# =============================================================================


class PersonalityTrait(Base):
    """An evolved personality trait that Clara manages herself.

    Traits are scoped by agent_id for multi-bot support and soft-deleted
    via the active flag so history is preserved.
    """

    __tablename__ = "personality_traits"

    id = Column(String, primary_key=True, default=gen_uuid)
    agent_id = Column(String, nullable=False, default="mypalclara")
    category = Column(String(50), nullable=False)  # interests, communication_style, values, etc.
    trait_key = Column(String(100), nullable=False)  # short identifier within category
    content = Column(Text, nullable=False)  # the trait description
    source = Column(String(20), nullable=False, default="self")  # self, user, seed
    reason = Column(Text, nullable=True)  # why added/updated
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_personality_trait_agent_category", "agent_id", "category"),
        Index("ix_personality_trait_agent_active", "agent_id", "active"),
    )


class PersonalityTraitHistory(Base):
    """Audit log for personality trait changes."""

    __tablename__ = "personality_trait_history"

    id = Column(String, primary_key=True, default=gen_uuid)
    trait_id = Column(String, ForeignKey("personality_traits.id"), nullable=False, index=True)
    agent_id = Column(String, nullable=False)
    event = Column(String(20), nullable=False)  # add, update, remove, restore
    old_content = Column(Text, nullable=True)
    new_content = Column(Text, nullable=True)
    old_category = Column(String(50), nullable=True)
    new_category = Column(String(50), nullable=True)
    reason = Column(Text, nullable=True)
    source = Column(String(20), nullable=False, default="self")  # self, user, system
    trigger_context = Column(Text, nullable=True)  # JSON conversation snippet
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("ix_personality_history_agent_created", "agent_id", "created_at"),)


# =============================================================================
# MCP (Model Context Protocol) Models
# =============================================================================

# Import MCP database models for multi-user support and metrics tracking
from db.mcp_models import (
    MCPOAuthToken,
    MCPRateLimit,
    MCPServer,
    MCPToolCall,
    MCPUsageMetrics,
)

__all__ = [
    # Core models
    "Base",
    "Project",
    "Session",
    "Message",
    "ChannelSummary",
    "ChannelConfig",
    "LogEntry",
    "GoogleOAuthToken",
    # Proactive models
    "ProactiveMessage",
    "UserInteractionPattern",
    "ProactiveNote",
    "ProactiveAssessment",
    # Email monitoring
    "EmailAccount",
    "EmailRule",
    "EmailAlert",
    # Guild config
    "GuildConfig",
    # Memory dynamics (FSRS-6 + Intentions)
    "MemoryDynamics",
    "MemoryAccessLog",
    "Intention",
    "MemorySupersession",
    # Memory history (Rook)
    "MemoryHistory",
    # Web interface identity
    "CanonicalUser",
    "PlatformLink",
    "OAuthToken",
    "WebSession",
    # Tool audit log
    "ToolAuditLog",
    # Personality evolution
    "PersonalityTrait",
    "PersonalityTraitHistory",
    # MCP models
    "MCPServer",
    "MCPOAuthToken",
    "MCPToolCall",
    "MCPUsageMetrics",
    "MCPRateLimit",
]
