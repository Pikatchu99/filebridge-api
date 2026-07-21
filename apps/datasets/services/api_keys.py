"""Generate and verify DatasetApiKey secrets.

Raw keys are shown to the user exactly once, at creation time, and never stored —
only a SHA-256 hash is persisted, so a leaked database dump doesn't hand out usable keys.
"""

import hashlib
import secrets

_KEY_PREFIX = "fbk_"


def generate_api_key() -> tuple[str, str]:
    """Return (raw_key, key_hash). Persist only key_hash; show raw_key to the caller once."""
    raw_key = f"{_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return raw_key, hash_api_key(raw_key)


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
