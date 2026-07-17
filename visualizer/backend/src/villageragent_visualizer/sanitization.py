from __future__ import annotations

from villageragent_visualizer.dto import JSONValue


_SENSITIVE_KEYS = {
    "api_key",
    "api_keys",
    "api_key_list",
    "apikey",
    "auth",
    "authorization",
    "base_url",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private",
    "private_key",
    "refresh_token",
    "secret",
    "ssh_private_key",
    "token",
    "access_token",
}
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "credential",
    "password",
    "passwd",
    "private",
    "secret",
    "token",
)


def sanitize_public_value(value: JSONValue) -> JSONValue:
    if isinstance(value, dict):
        return {
            key: sanitize_public_value(child)
            for key, child in value.items()
            if _is_public_key(key)
        }
    if isinstance(value, list):
        return [sanitize_public_value(child) for child in value]
    return value


def _is_public_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        not key.startswith("_")
        and normalized not in _SENSITIVE_KEYS
        and not any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)
    )
