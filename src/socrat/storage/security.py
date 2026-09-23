"""One-way keyed lookups for passwords and Telegram chat identifiers."""

from __future__ import annotations

import hashlib
import hmac

from socrat.config import Settings


def secret_bytes(settings: Settings) -> bytes:
    secret = settings.app_secret.get_secret_value()
    if not secret:
        raise ValueError("APP_SECRET must be set before using persistent storage")
    return secret.encode("utf-8")


def keyed_lookup(secret: bytes, value: str) -> str:
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
