"""Email Monitoring Service for Clara.

Background service that monitors user email accounts and sends
Discord alerts for important messages based on configurable rules.
"""

from mypalclara.services.email.credentials import (
    decrypt_credential,
    encrypt_credential,
    is_encryption_configured,
)
from mypalclara.services.email.providers.base import EmailMessage, EmailProvider
from mypalclara.services.email.rules_engine import RuleMatch, evaluate_email

__all__ = [
    "EmailMessage",
    "EmailProvider",
    "RuleMatch",
    "decrypt_credential",
    "encrypt_credential",
    "evaluate_email",
    "is_encryption_configured",
]

# Startup warning for missing encryption key
if not is_encryption_configured():
    print(
        "[email] WARNING: EMAIL_ENCRYPTION_KEY not set. "
        "IMAP email accounts cannot be connected. "
        'Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    )
