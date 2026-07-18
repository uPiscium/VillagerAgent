from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def world_view_config(url: str | None, *, allow_remote: bool = False) -> dict:
    if not url:
        return {"enabled": False, "url": None, "remote": False, "reason": "not_configured"}
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return {"enabled": False, "url": None, "remote": False, "reason": "invalid_url"}
    local = parsed.hostname == "localhost"
    try:
        local = local or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        pass
    if not local and not allow_remote:
        return {"enabled": False, "url": None, "remote": True, "reason": "remote_requires_explicit_opt_in"}
    return {"enabled": True, "url": url.rstrip("/"), "remote": not local, "reason": None}
