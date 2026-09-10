"""The HTTP layer over the review tray.

Separate from `preview_server`, whose first paragraph promises that every
route it serves is read-only. Several of these change state, and a module that
says one thing while doing another is worse than two modules.

On who can use them. Trays are keyed by graph-local node id and nothing else,
so any client that can reach the ComfyUI server can rate, release or clear
one - exactly as any client that can reach it can already queue a prompt,
cancel a run or read `/history`. Authentication is the deployment's job. This
is written down rather than left to be discovered.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from . import review

__all__ = ["register_review_routes"]

_log = logging.getLogger("PW_Color")

#: Set once *this process* has attached the handlers. aiohttp accepts a
#: duplicate route happily and leaves two handlers on one path with no error.
_routes_registered = False

_NO_STORE = {"Cache-Control": "no-store"}

Handler = Callable[[Any], Awaitable[Any]]


def _state(node_id: str) -> dict:
    tray = review.get(node_id)
    return {
        "count": tray.count if tray else 0,
        "ratings": list(tray.ratings) if tray else [],
        "pending": len(tray.release) if tray and tray.release else 0,
        "epoch": review.epoch(node_id),
        "reruns": tray.reruns if tray else [],
        "rerunnable": [p is not None for p in tray.prompts] if tray else [],
    }


def _state_handler(web: Any) -> Handler:
    """What the panel draws, including after a page reload."""

    async def handler(request: Any) -> Any:
        return web.json_response(_state(request.match_info["node_id"]), headers=_NO_STORE)

    return handler


def _image_handler(web: Any, attribute: str) -> Handler:
    """One frame out of the tray, at thumbnail or focus size."""

    async def handler(request: Any) -> Any:
        tray = review.get(request.match_info["node_id"])
        if tray is None:
            return web.Response(status=404, text="the tray is empty")
        try:
            index = int(request.match_info["index"])
        except (TypeError, ValueError):
            return web.Response(status=400, text="index must be a number")
        frames = getattr(tray, attribute)
        if not 0 <= index < len(frames):
            return web.Response(status=404, text="no such frame in the tray")
        return web.Response(body=frames[index], content_type="image/jpeg", headers=_NO_STORE)

    return handler


async def _ratings_from(request: Any) -> list[int] | None:
    try:
        payload = await request.json()
        return [int(r) for r in payload["ratings"]]
    except Exception:
        return None


def _ratings_handler(web: Any) -> Handler:
    """Save the ratings so far, so the next arrival does not wipe them."""

    async def handler(request: Any) -> Any:
        ratings = await _ratings_from(request)
        if ratings is None:
            return web.Response(status=400, text="expected {'ratings': [numbers]}")
        if not review.set_ratings(request.match_info["node_id"], ratings):
            return web.Response(status=404, text="the tray is empty")
        return web.json_response({"saved": True}, headers=_NO_STORE)

    return handler


def _release_handler(web: Any) -> Handler:
    """Choose the keepers. The browser then queues the run that delivers them."""

    async def handler(request: Any) -> Any:
        ratings = await _ratings_from(request)
        if ratings is None:
            return web.Response(status=400, text="expected {'ratings': [numbers]}")
        node_id = request.match_info["node_id"]
        if review.get(node_id) is None:
            return web.Response(status=404, text="the tray is empty")
        try:
            everything = (await request.json()).get("everything", False) is True
        except Exception:
            everything = False
        kept = review.request_release(node_id, ratings, everything=everything)
        return web.json_response({"kept": kept}, headers=_NO_STORE)

    return handler


def _rerun_handler(web: Any) -> Handler:
    """The prompt that made one frame, with new seeds, for the browser to queue.

    The browser queues it rather than this server, so the run belongs to the
    person's own session: its progress shows in their UI like any other.
    """

    async def handler(request: Any) -> Any:
        node_id = request.match_info["node_id"]
        try:
            index = int(request.match_info["index"])
        except (TypeError, ValueError):
            return web.Response(status=400, text="index must be a number")
        job = review.rerun_job(node_id, index, node_id)
        if job is None:
            return web.Response(status=404, text="that frame is gone, or its run left no prompt to re-run")
        return web.json_response(job, headers=_NO_STORE)

    return handler


def _clear_handler(web: Any) -> Handler:
    async def handler(request: Any) -> Any:
        review.clear(request.match_info["node_id"])
        return web.json_response({"cleared": True}, headers=_NO_STORE)

    return handler


def _mode_handler(web: Any) -> Handler:
    """The auto_pass switch, live, so it reaches runs already queued."""

    async def handler(request: Any) -> Any:
        try:
            payload = await request.json()
            value = payload["auto_pass"]
            if not isinstance(value, bool):
                raise TypeError
        except Exception:
            return web.Response(status=400, text="expected {'auto_pass': true|false}")
        review.set_auto_pass(request.match_info["node_id"], value)
        return web.json_response({"auto_pass": value}, headers=_NO_STORE)

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
        ("/pw_color/review/{node_id}/ratings", "POST", _ratings_handler(web)),
        ("/pw_color/review/{node_id}/release", "POST", _release_handler(web)),
        ("/pw_color/review/{node_id}/rerun/{index}", "POST", _rerun_handler(web)),
        ("/pw_color/review/{node_id}/clear", "POST", _clear_handler(web)),
        ("/pw_color/review/{node_id}/mode", "POST", _mode_handler(web)),
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
