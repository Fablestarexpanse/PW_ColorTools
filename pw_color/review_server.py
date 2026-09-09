"""The HTTP layer over a held batch.

Separate from `preview_server`, whose first paragraph promises that every
route it serves is read-only. One of the routes here releases a run, and a
module that says one thing while doing another is worse than two modules.

On who can release. Holds are keyed by graph-local node id and nothing else,
so any client that can reach the ComfyUI server can release one — exactly as
any client that can reach it can already queue a prompt, cancel a run or read
`/history`. Authentication is the deployment's job. This is written down
rather than left to be discovered.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from .review import get_hold, release

__all__ = ["register_review_routes"]

_log = logging.getLogger("PW_Color")

#: Set once *this process* has attached the handlers. Same guard, and the same
#: reason, as the preview routes: aiohttp accepts a duplicate route happily and
#: leaves two handlers on one path with no error and no way to tell.
_routes_registered = False

_NO_STORE = {"Cache-Control": "no-store"}

Handler = Callable[[Any], Awaitable[Any]]


def _image_handler(web: Any, attribute: str) -> Handler:
    """Serve one image out of a held batch, or say why not.

    The two image routes differ only in which encoded size they read, so they
    are one function given the attribute to pull.
    """

    async def handler(request: Any) -> Any:
        hold = get_hold(request.match_info["node_id"])
        if hold is None:
            return web.Response(status=404, text="nothing held")
        try:
            index = int(request.match_info["index"])
        except (TypeError, ValueError):
            return web.Response(status=400, text="index must be a number")
        frames = getattr(hold, attribute)
        if not 0 <= index < len(frames):
            return web.Response(status=404, text="no such image in the held batch")
        return web.Response(body=frames[index], content_type="image/jpeg", headers=_NO_STORE)

    return handler


def _state_handler(web: Any) -> Handler:
    """What the panel needs to draw itself, including after a page reload.

    Answers for a node that is not holding rather than 404ing: "nothing is
    held" is the normal answer to this question, not a failure.
    """

    async def handler(request: Any) -> Any:
        hold = get_hold(request.match_info["node_id"])
        if hold is None:
            return web.json_response({"holding": False, "count": 0, "ratings": []}, headers=_NO_STORE)
        return web.json_response(
            {"holding": True, "count": hold.count, "ratings": list(hold.ratings)}, headers=_NO_STORE
        )

    return handler


def _release_handler(web: Any) -> Handler:
    """Take the ratings and let the run continue.

    The body came from a browser, so a bad one is answered rather than raised:
    an exception here would surface as a 500 with a traceback in the server log
    and nothing useful on the node.
    """

    async def handler(request: Any) -> Any:
        try:
            payload = await request.json()
            ratings = [int(r) for r in payload["ratings"]]
        except Exception:
            return web.Response(status=400, text="expected {'ratings': [numbers]}")
        if not release(request.match_info["node_id"], ratings):
            return web.Response(status=404, text="nothing held")
        return web.json_response({"released": True}, headers=_NO_STORE)

    return handler


def _routing_table(web: Any) -> list[tuple[str, str, Handler]]:
    """Every route this module serves, with its method, in one readable list.

    ``web`` is passed in rather than imported at module scope because aiohttp
    is a ComfyUI dependency, absent when the pack is imported for tests.
    """
    return [
        ("/pw_color/review/{node_id}", "GET", _state_handler(web)),
        ("/pw_color/review/{node_id}/thumb/{index}", "GET", _image_handler(web, "thumbs")),
        ("/pw_color/review/{node_id}/image/{index}", "GET", _image_handler(web, "views")),
        ("/pw_color/review/{node_id}/release", "POST", _release_handler(web)),
    ]


def register_review_routes() -> bool:
    """Attach the review routes to ComfyUI's aiohttp app. Never raises.

    Same contract as `preview_server.register_routes`, for the same reason:
    ComfyUI skips the *entire* extension when the entrypoint raises, so a
    missing aiohttp has to cost the review panel and nothing else.
    """
    global _routes_registered
    if _routes_registered:
        _log.debug("PW Color: review routes already registered")
        return True

    try:
        from aiohttp import web
        from server import PromptServer

        routes = PromptServer.instance.routes
    except Exception:  # pragma: no cover - outside a running ComfyUI server
        _log.debug("PW Color: review routes unavailable", exc_info=True)
        return False

    for path, method, handler in _routing_table(web):
        (routes.post if method == "POST" else routes.get)(path)(handler)

    _routes_registered = True
    return True
