from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = {
    "api_key",
    "api_keys",
    "api_key_list",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private_key",
    "refresh_token",
    "secret",
    "ssh_private_key",
    "token",
    "access_token",
}
_SENSITIVE_FLAGS = {
    "--api-key",
    "--api_key",
    "--authorization",
    "--credential",
    "--password",
    "--secret",
    "--token",
}
_NON_SECRET_PLACEHOLDERS = {"", "dummy", "none", "null", "ollama", "test"}


def collect_secret_values(value: Any) -> tuple[str, ...]:
    secrets: set[str] = set()
    _collect_secret_values(value, secrets)
    return tuple(sorted(secrets, key=len, reverse=True))


def sanitize_artifact_value(value: Any, *, secret_values: Iterable[str] = ()) -> Any:
    secrets = tuple(secret_values) or collect_secret_values(value)
    if isinstance(value, dict):
        sanitized = {}
        for key, child in value.items():
            key_text = str(key)
            if _is_credential_source_key(key_text):
                sanitized[key] = (
                    redact_text(child, secret_values=secrets)
                    if isinstance(child, str)
                    else sanitize_artifact_value(child, secret_values=secrets)
                )
            elif _is_sensitive_key(key_text):
                sanitized[key] = REDACTED
            else:
                sanitized[key] = sanitize_artifact_value(child, secret_values=secrets)
        return sanitized
    if isinstance(value, list):
        return [sanitize_artifact_value(item, secret_values=secrets) for item in value]
    if isinstance(value, tuple):
        return [sanitize_artifact_value(item, secret_values=secrets) for item in value]
    if isinstance(value, set | frozenset):
        return [sanitize_artifact_value(item, secret_values=secrets) for item in value]
    if isinstance(value, str):
        return redact_text(value, secret_values=secrets)
    return value


def sanitize_command(command: list[str], *, secret_values: Iterable[str] = ()) -> list[str]:
    secrets = tuple(secret_values)
    sanitized: list[str] = []
    redact_next = False
    for argument in command:
        if redact_next:
            sanitized.append(REDACTED)
            redact_next = False
            continue
        flag, separator, _ = argument.partition("=")
        if flag.lower() in _SENSITIVE_FLAGS:
            sanitized.append(f"{flag}={REDACTED}" if separator else flag)
            redact_next = not separator
            continue
        sanitized.append(redact_text(argument, secret_values=secrets))
    return sanitized


def redact_command_text(command: str, *, secret_values: Iterable[str] = ()) -> str:
    redacted = redact_text(command, secret_values=secret_values)
    for flag in sorted(_SENSITIVE_FLAGS, key=len, reverse=True):
        pattern = rf"({re.escape(flag)}(?:=|\s+))([^\s]+)"
        redacted = re.sub(pattern, rf"\1{REDACTED}", redacted, flags=re.IGNORECASE)
    return redacted


def redact_text(value: str, *, secret_values: Iterable[str] = ()) -> str:
    redacted = value
    for secret in secret_values:
        if _should_redact_literal(secret):
            redacted = redacted.replace(secret, REDACTED)
    return redacted


def _collect_secret_values(value: Any, secrets: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text) and not _is_credential_source_key(key_text):
                _collect_scalar_secrets(child, secrets)
            else:
                _collect_secret_values(child, secrets)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for child in value:
            _collect_secret_values(child, secrets)


def _collect_scalar_secrets(value: Any, secrets: set[str]) -> None:
    if isinstance(value, str):
        secrets.add(value)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for child in value:
            _collect_scalar_secrets(child, secrets)
    elif isinstance(value, dict):
        for child in value.values():
            _collect_scalar_secrets(child, secrets)


def _is_credential_source_key(key: str) -> bool:
    lowered = key.lower()
    return lowered.endswith("_env") or lowered in {"credential_source", "credential_sources"}


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return (
        lowered in _SENSITIVE_KEYS
        or lowered.endswith("_api_key")
        or lowered.endswith("_password")
        or lowered.endswith("_secret")
        or lowered.endswith("_token")
    )


def _should_redact_literal(secret: str) -> bool:
    stripped = secret.strip()
    return bool(stripped) and stripped.lower() not in _NON_SECRET_PLACEHOLDERS
