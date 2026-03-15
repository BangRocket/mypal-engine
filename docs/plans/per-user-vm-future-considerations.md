# Per-User VM — Future Considerations

Items intentionally omitted from v1. Kept here so they stay on the radar.

## Per-User Heartbeat Loops

One global heartbeat for now. Future: each user's VM could run its own heartbeat with personalized checks (user-specific HEARTBEAT.md already lives in the VM).

## User-to-User Sharing

No "share this file with Bob" in v1. Everything is either private or public to all group channels. Granular sharing (specific users, specific channels) is a future concern.

## VM Resource Quotas Per User

All VMs get the same Incus profile in v1. Future: admin-configurable per-user limits (CPU, memory, disk) for multi-tenant fairness.

## VM Backup / Export

No snapshot-to-S3 or VM migration in v1. Incus handles local persistence. Disaster recovery and cross-host migration are separate efforts.

## Web UI for Privacy Management

Users manage visibility through conversation with Clara ("make this public") in v1. Future: dashboard showing what's public vs private, bulk toggle controls.

## Tiered Visibility

Just `private` and `public` in v1. Future: "visible to channel X but not channel Y", per-group visibility, role-based access.

## Migration of Existing Memories

Existing Rook memories have no `visibility` field. Treated as `private` by default if field is missing. No backfill needed in v1. Future: bulk classification tool or LLM-assisted review.

## Per-User Agents / Background Tasks

v1 VMs are passive (Clara accesses them on demand). Future: user-defined cron jobs, monitoring scripts, or always-on agents running inside the VM.
