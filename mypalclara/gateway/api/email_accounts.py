"""Email account listing endpoint (internal, gateway-secret auth).

Read-only listing scoped to an explicit user_id (matching the adapter's existing
query). Secrets (imap_password) are never serialized.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from mypalclara.db.models import EmailAccount
from mypalclara.gateway.api.auth import get_db, require_gateway_secret

router = APIRouter()

# Safe, non-secret fields to expose (imap_password is intentionally excluded).
_PUBLIC_FIELDS = (
    "id",
    "email_address",
    "provider_type",
    "enabled",
    "status",
    "poll_interval_minutes",
    "alert_channel_id",
    "ping_on_alert",
    "error_count",
    "last_error",
    "last_checked_at",
)


def _serialize(account: EmailAccount) -> dict:
    out: dict = {}
    for field in _PUBLIC_FIELDS:
        value = getattr(account, field, None)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        out[field] = value
    return out


@router.get("/email-accounts")
async def list_email_accounts(
    user_id: str,
    _: bool = Depends(require_gateway_secret),
    db: DBSession = Depends(get_db),
) -> list[dict]:
    accounts = db.query(EmailAccount).filter(EmailAccount.user_id == user_id).all()
    return [_serialize(a) for a in accounts]
