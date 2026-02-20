"""Credential encryption for email account passwords.

Uses Fernet symmetric encryption with a key from environment variable.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from config.logging import get_logger

logger = get_logger("email.credentials")

# Encryption key from environment (generate with: Fernet.generate_key().decode())
EMAIL_ENCRYPTION_KEY = os.getenv("EMAIL_ENCRYPTION_KEY")

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Get or create Fernet instance."""
    global _fernet
    if _fernet is None:
        if not EMAIL_ENCRYPTION_KEY:
            raise ValueError(
                "EMAIL_ENCRYPTION_KEY environment variable is required for IMAP credentials. "
                'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        _fernet = Fernet(EMAIL_ENCRYPTION_KEY.encode())
    return _fernet


def encrypt_credential(plaintext: str) -> str:
    """Encrypt a credential for storage.

    Args:
        plaintext: The credential to encrypt (e.g., IMAP password)

    Returns:
        Base64-encoded encrypted string safe for database storage
    """
    if not plaintext:
        return ""
    fernet = _get_fernet()
    encrypted = fernet.encrypt(plaintext.encode())
    return encrypted.decode()


def decrypt_credential(ciphertext: str) -> str:
    """Decrypt a credential for use.

    Args:
        ciphertext: The encrypted credential from database

    Returns:
        Decrypted plaintext credential

    Raises:
        ValueError: If decryption fails (wrong key or corrupted data)
    """
    if not ciphertext:
        return ""
    fernet = _get_fernet()
    try:
        decrypted = fernet.decrypt(ciphertext.encode())
        return decrypted.decode()
    except InvalidToken as e:
        logger.error("Failed to decrypt credential - key may have changed")
        raise ValueError("Failed to decrypt credential") from e


def is_encryption_configured() -> bool:
    """Check if encryption key is configured."""
    return bool(EMAIL_ENCRYPTION_KEY)
