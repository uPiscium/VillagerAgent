"""Production FastAPI wiring for the Minecraft EAC preflight endpoint."""
from __future__ import annotations

try:
    from env.eac_preflight import register_eac_preflight_route
except ImportError:
    from eac_preflight import register_eac_preflight_route


def install_minecraft_server_eac_route(app, *, native_bot, Vec3, timeout_decorator):
    """Bind the server's native read-only providers to the shared route handler."""
    return register_eac_preflight_route(
        app,
        bot_provider=lambda: native_bot,
        vec3_provider=lambda: Vec3,
        timeout_decorator=timeout_decorator,
    )
