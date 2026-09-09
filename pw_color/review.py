"""A batch, held at a node until someone rates it.

The pack's other nodes are pure functions of their inputs. This one waits for
a person, which makes the state it keeps the whole design: one hold per node,
the encoded images the panel draws, the ratings that come back, and an event
the executing node polls.

Why `threading.Event` and not `asyncio.Event`. ComfyUI runs the prompt
executor on its own loop on its own thread: `main.py` starts `prompt_worker`
as a thread and `PromptExecutor.execute` calls `asyncio.run`. The release
arrives on the aiohttp server's loop, on a different thread, where setting an
asyncio primitive that belongs to another loop is undefined. A threading event
crosses threads by design, and the node polls it — which is also where the
interrupt check belongs, so the poll earns its keep twice.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from .preview_cache import encode_jpeg

if TYPE_CHECKING:  # pragma: no cover - torch is imported by callers, not here
    import torch

__all__ = [
    "STARS",
    "THUMB_EDGE",
    "VIEW_EDGE",
    "Hold",
    "open_hold",
    "get_hold",
    "release",
    "close",
    "kept_order",
]

_log = logging.getLogger("PW_Color")

#: The rating scale: one to five stars, or zero for unrated.
STARS = 5
#: Long edge of a filmstrip thumbnail.
THUMB_EDGE = 160
#: Long edge of the focus view, big enough to judge a frame on a node panel.
VIEW_EDGE = 768


@dataclass
class Hold:
    """One batch, waiting. ``ratings`` has one entry per image, zero unrated."""

    node_id: str
    count: int
    thumbs: list[bytes]
    views: list[bytes]
    released: threading.Event = field(default_factory=threading.Event)
    ratings: list[int] = field(default_factory=list)


_holds: dict[str, Hold] = {}
_lock = threading.Lock()


def kept_order(ratings: Sequence[int]) -> list[int]:
    """Indices of the rated images, best first, ties in the order they arrived.

    Ties keeping their input order is a promise to the user rather than an
    accident: two four-star frames leave in the order they were generated,
    which is the order they were shown in. `sorted` is stable, and that is what
    makes it true — so the filter and the sort are two steps here rather than
    one clever key that would hide the dependency.
    """
    rated = [i for i, r in enumerate(ratings) if r > 0]
    return sorted(rated, key=lambda i: -ratings[i])


def open_hold(node_id: str, images: "torch.Tensor") -> Hold:
    """Hold a batch and encode what the panel needs to draw it.

    Any hold already open for this node is released first. A re-queued run has
    to supersede a stale hold rather than queue up behind one nobody is looking
    at any more — and releasing it, rather than dropping it, is what stops the
    older run waiting for ever.
    """
    node_id = str(node_id)
    count = int(images.shape[0])
    hold = Hold(
        node_id=node_id,
        count=count,
        thumbs=[encode_jpeg(images[i], THUMB_EDGE) for i in range(count)],
        views=[encode_jpeg(images[i], VIEW_EDGE) for i in range(count)],
        ratings=[0] * count,
    )
    with _lock:
        stale = _holds.get(node_id)
        _holds[node_id] = hold
    if stale is not None:
        _log.debug("PW Color: superseding a batch held on node %s", node_id)
        stale.released.set()
    return hold


def get_hold(node_id: str) -> Hold | None:
    with _lock:
        return _holds.get(str(node_id))


def release(node_id: str, ratings: Sequence[int]) -> bool:
    """Record the ratings and wake the node. False if nothing is held.

    What arrives came from a browser, so it is padded to the batch and clamped
    to the scale rather than trusted. A short list would otherwise raise inside
    the executor's thread, where the traceback surfaces far from the cause.
    """
    hold = get_hold(node_id)
    if hold is None:
        return False
    clean = [0] * hold.count
    for i in range(min(len(ratings), hold.count)):
        try:
            clean[i] = max(0, min(STARS, int(ratings[i])))
        except (TypeError, ValueError):
            clean[i] = 0
    hold.ratings = clean
    hold.released.set()
    return True


def close(node_id: str) -> None:
    """Forget a hold. Called from the node's ``finally``, so it never raises."""
    with _lock:
        _holds.pop(str(node_id), None)
