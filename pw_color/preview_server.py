"""The HTTP layer over the preview caches.

Five routes, all read-only, all serving what `preview_cache` already holds. The
caching and encoding live there; this file is only about how the browser asks,
and it imports two readers to do it. It deliberately does *not* re-export the
cache's writers: doing that made every node import its image cache from the
module named "server", which is how a split becomes nominal — the file moved
but the dependency arrow did not.

On who can read these caches.

Entries are keyed by graph-local node id and nothing else, so any client that
can reach the ComfyUI server can GET /pw_color/input/5. That is worth stating
rather than leaving to be discovered.

It is deliberate, and it matches the platform: ComfyUI's own /view serves any
file under output/ and /history returns every prompt that has run, to any
client the deployment lets through. Authentication is the deployment's job, and
the cache holds only downscaled proxies of images the same client can already
fetch through those endpoints. Scoping by session would be a stronger guarantee
than the surrounding server offers and would break the case these caches exist
for — the browser reading a preview for a node it did not run.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from .paths import LOOK_PRESETS
from .preview_cache import get_input, get_output

__all__ = ["register_routes"]

_log = logging.getLogger("PW_Color")

#: Set once *this process* has attached the handlers.
#:
#: Module-global, so it guards a second call within one interpreter — which
#: is the case that happens: a Manager reload or a second entrypoint. It
#: cannot know whether some other code registered the same paths, and does
#: not claim to.
_routes_registered = False

_NO_STORE = {"Cache-Control": "no-store_input"}


Handler = Callable[[Any], Awaitable[Any]]


def _bytes_handler(web: Any, fetch: Callable[[str], dict | None], key: str, missing: str, content_type: str) -> Handler:
    """Serve one cached blob, or 404 with a reason.

    Three of the five routes are exactly this and differ only in which cache
    they read, which key holds the bytes, and the content type.
    """

    async def handler(request: Any) -> Any:
        entry = fetch(request.match_info["node_id"])
        if entry is None:
            return web.json_response({"error": missing}, status=404)
        return web.Response(body=entry[key], content_type=content_type, headers=_NO_STORE)

    return handler


def _histogram_handler(web: Any) -> Handler:
    """The input histogram, computed from the full-resolution image.

    Not from the proxy: a histogram of a downscale is not the histogram of the
    image, because resampling fills in the gaps that make a posterised source
    obvious.
    """

    async def handler(request: Any) -> Any:
        entry = get_input(request.match_info["node_id"])
        if entry is None:
            return web.json_response({"error": "no cached input"}, status=404)
        return web.json_response(
            {"histogram": entry["histogram"], "width": entry["width"], "height": entry["height"]}
        )

    return handler


def _presets_handler(web: Any) -> Handler:
    """Look presets, so the node can bake each one onto the user's own image.

    Served rather than bundled into the JS: presets are data, and a user
    dropping a file into looks/ should not need a rebuild to see it.
    """

    async def handler(_request: Any) -> Any:
        try:
            return web.json_response(json.loads(LOOK_PRESETS.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _log.exception("PW Color: could not read %s", LOOK_PRESETS)
            return web.json_response({"presets": []})

    return handler


def _routing_table(web: Any) -> list[tuple[str, Handler]]:
    """Every route this pack serves, in one readable list.

    ``web`` is passed in rather than imported at module scope because aiohttp
    is a ComfyUI dependency, absent when the pack is imported for tests.
    """
    return [
        ("/pw_color/input/{node_id}", _bytes_handler(web, get_input, "proxy", "no cached input", "image/jpeg")),
        ("/pw_color/histogram/{node_id}", _histogram_handler(web)),
        ("/pw_color/output/{node_id}", _bytes_handler(web, get_output, "proxy", "no cached output", "image/jpeg")),
        ("/pw_color/output_crop/{node_id}", _bytes_handler(web, get_output, "crop", "no cached output", "image/png")),
        ("/pw_color/presets", _presets_handler(web)),
    ]


def register_routes() -> bool:
    """Attach our routes to ComfyUI's aiohttp app. Returns False if unavailable.

    Never raises. The preview routes are a convenience; a headless run, a bare
    import or a future rename of ``PromptServer.instance`` must degrade to "no
    histogram in the editor", not to "the pack failed to load". ComfyUI catches
    exceptions out of ``comfy_entrypoint`` and skips the *entire* extension, so
    an unguarded failure here costs every node in the pack.

    Idempotent. ComfyUI can import an extension more than once — a reload from
    the Manager, a second entrypoint — and aiohttp happily accepts a duplicate
    route, so a second call used to leave two handlers registered for every
    path with no error and no way to tell.
    """
    global _routes_registered
    if _routes_registered:
        _log.debug("PW Color: preview routes already registered")
        return True

    try:
        from aiohttp import web
        from server import PromptServer

        routes = PromptServer.instance.routes
    except Exception:  # pragma: no cover - outside a running ComfyUI server
        _log.debug("PW Color: preview routes unavailable", exc_info=True)
        return False

    for path, handler in _routing_table(web):
        routes.get(path)(handler)

    _routes_registered = True
    return True
