"""ComfyUI-PW-Color — node registration.

V3 only. ComfyUI checks for ``NODE_CLASS_MAPPINGS`` *before* ``comfy_entrypoint``
(nodes.py, 0.29.x), so defining both would silently pin the pack to V1. We
define only the entrypoint.

Minimum supported: ComfyUI 0.27 / frontend 1.40. Older builds lack the V3
``io.Custom`` type registration the LOOK and PALETTE ports depend on.
"""

from __future__ import annotations

import logging

WEB_DIRECTORY = "./web/dist"

_log = logging.getLogger("PW_Color")

# Guarded so the package imports cleanly outside a ComfyUI process — pytest
# collects this file as the rootdir package, and the maths modules underneath
# must be testable without ComfyUI installed.
try:
    from comfy_api.latest import ComfyExtension, io

    _COMFY_AVAILABLE = True
except ImportError:  # pragma: no cover - only hit outside ComfyUI
    _COMFY_AVAILABLE = False


if _COMFY_AVAILABLE:

    class PWColorExtension(ComfyExtension):
        async def get_node_list(self) -> list[type[io.ComfyNode]]:
            from .pw_color.nodes import curves, grain, match_source, palette, parity

            nodes: list[type[io.ComfyNode]] = []
            nodes.extend(curves.NODES)
            nodes.extend(grain.NODES)
            nodes.extend(match_source.NODES)
            nodes.extend(palette.NODES)
            nodes.extend(parity.NODES)
            return nodes

        async def on_load(self) -> None:
            from .pw_color import __version__

            # Belt and braces over register_routes' own guard: ComfyUI skips the
            # whole extension if on_load raises, so nothing optional in here may
            # be allowed to throw.
            ok = False
            try:
                from .pw_color.preview_server import register_routes

                ok = register_routes()
            except Exception:  # pragma: no cover
                _log.warning("PW Color: preview routes unavailable", exc_info=True)
            _log.info("PW Color %s loaded (preview routes: %s)", __version__, "on" if ok else "off")

    async def comfy_entrypoint() -> ComfyExtension:
        return PWColorExtension()


__all__ = ["comfy_entrypoint", "WEB_DIRECTORY"]
