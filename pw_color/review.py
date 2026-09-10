"""The tray: images collected at a node across runs, until someone rates them.

PW Review used to hold a run while a person looked, one run at a time. That
fits a single run that makes a big batch, and it fits nothing else: queue four
one-image runs and you review one image at a time while the rest of the queue
waits behind you. So the node collects instead. Each run drops its frames here
and finishes at once; the tray fills while generation carries on; the person
rates when they are ready; a release hands the keepers to one more run, which
sends them downstream.

State that used to live inside a waiting execution lives here instead, which is
why this module is mostly bookkeeping: frames, the encoded images the panel
draws, ratings that survive new arrivals, the release waiting to be delivered,
and the live auto_pass switch.

Everything is guarded by one lock. Frames arrive on the executor's thread and
ratings, releases and switches arrive on the server's, so both touch this at
once as a matter of course.
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
    "Tray",
    "kept_order",
    "add",
    "get",
    "set_ratings",
    "request_release",
    "release_pending",
    "take_release",
    "clear",
    "set_auto_pass",
    "auto_pass",
    "epoch",
]

_log = logging.getLogger("PW_Color")

#: The rating scale: one to five stars, or zero for unrated.
STARS = 5
#: Long edge of a filmstrip thumbnail.
THUMB_EDGE = 160
#: Long edge of the focus view, big enough to judge a frame on a node panel.
VIEW_EDGE = 768


@dataclass
class Tray:
    """What one node has collected. ``ratings`` has one entry per frame."""

    node_id: str
    images: list["torch.Tensor"] = field(default_factory=list)
    thumbs: list[bytes] = field(default_factory=list)
    views: list[bytes] = field(default_factory=list)
    ratings: list[int] = field(default_factory=list)
    #: Frames chosen by the last release, best first, waiting for the run that
    #: delivers them. Indices into the tray as it stood when release was pressed.
    release: list[int] | None = None
    #: How many frames the tray held when release was pressed. Frames that
    #: arrive after that were not part of the review and stay for the next one.
    release_upto: int = 0

    @property
    def count(self) -> int:
        return len(self.images)


_trays: dict[str, Tray] = {}
_live_auto_pass: dict[str, bool] = {}
#: Bumped whenever frames leave the tray, because that renumbers the ones
#: left. The panel caches frames by index and needs to know when to forget.
_epochs: dict[str, int] = {}
_lock = threading.Lock()


def _bump(node_id: str) -> None:
    """Call with the lock held."""
    _epochs[node_id] = _epochs.get(node_id, 0) + 1


def epoch(node_id: str) -> int:
    """How many times frames have left this tray. See `_epochs`."""
    with _lock:
        return _epochs.get(str(node_id), 0)


def _reset() -> None:
    """Forget everything. For tests."""
    with _lock:
        _trays.clear()
        _live_auto_pass.clear()
        _epochs.clear()


def kept_order(ratings: Sequence[int]) -> list[int]:
    """Indices of the rated frames, best first, ties in the order they arrived.

    Ties keeping their arrival order is a promise to the user rather than an
    accident: two four-star frames leave in the order they were generated,
    which is the order they are shown in. `sorted` is stable, and that is what
    makes it true, so the filter and the sort are two steps rather than one
    clever key that would hide the dependency.
    """
    rated = [i for i, r in enumerate(ratings) if r > 0]
    return sorted(rated, key=lambda i: -ratings[i])


def _clean(ratings: Sequence[int], count: int) -> list[int]:
    """What a browser sent, padded to the tray and clamped to the scale."""
    clean = [0] * count
    for i in range(min(len(ratings), count)):
        try:
            clean[i] = max(0, min(STARS, int(ratings[i])))
        except (TypeError, ValueError):
            clean[i] = 0
    return clean


def add(node_id: str, images: "torch.Tensor") -> Tray:
    """Put a run's frames in the tray and encode what the panel needs.

    Frames are moved to the CPU and kept whole, because the release sends the
    real frames downstream rather than the previews. Encoding happens outside
    the lock: it is the slow part, and the panel should not stall on it.
    """
    node_id = str(node_id)
    frames = [images[i].detach().to("cpu") for i in range(int(images.shape[0]))]
    thumbs = [encode_jpeg(f, THUMB_EDGE) for f in frames]
    views = [encode_jpeg(f, VIEW_EDGE) for f in frames]
    with _lock:
        tray = _trays.setdefault(node_id, Tray(node_id))
        tray.images.extend(frames)
        tray.thumbs.extend(thumbs)
        tray.views.extend(views)
        tray.ratings.extend([0] * len(frames))
        return tray


def get(node_id: str) -> Tray | None:
    """The node's tray, or None when it holds nothing."""
    with _lock:
        tray = _trays.get(str(node_id))
        return tray if tray is not None and tray.count else None


def set_ratings(node_id: str, ratings: Sequence[int]) -> bool:
    """Record the ratings so far. False if the tray is empty.

    Kept on the server rather than in the panel because frames keep arriving:
    every arrival re-syncs the panel, and ratings held only in the browser
    would be wiped by the next one.
    """
    with _lock:
        tray = _trays.get(str(node_id))
        if tray is None or not tray.count:
            return False
        tray.ratings = _clean(ratings, tray.count)
        return True


def request_release(node_id: str, ratings: Sequence[int]) -> int:
    """Choose the keepers and mark them for delivery. Returns how many.

    Nothing rated means the whole tray was rejected: it is emptied and no
    release is left waiting, because no run would have anything to deliver.
    """
    with _lock:
        tray = _trays.get(str(node_id))
        if tray is None or not tray.count:
            return 0
        tray.ratings = _clean(ratings, tray.count)
        keep = kept_order(tray.ratings)
        if not keep:
            _trays.pop(str(node_id), None)
            _bump(str(node_id))
            return 0
        tray.release = keep
        tray.release_upto = tray.count
        return len(keep)


def release_pending(node_id: str) -> bool:
    with _lock:
        tray = _trays.get(str(node_id))
        return tray is not None and tray.release is not None


def take_release(node_id: str) -> list["torch.Tensor"] | None:
    """Hand over the keepers, best first, and drop the reviewed frames.

    None when no release is waiting. Frames that arrived after release was
    pressed were not part of that review, so they stay, unrated, for the next.
    """
    with _lock:
        tray = _trays.get(str(node_id))
        if tray is None or tray.release is None:
            return None
        kept = [tray.images[i] for i in tray.release]
        upto = tray.release_upto
        del tray.images[:upto], tray.thumbs[:upto], tray.views[:upto], tray.ratings[:upto]
        tray.release, tray.release_upto = None, 0
        _bump(str(node_id))
        if not tray.count:
            _trays.pop(str(node_id), None)
        return kept


def clear(node_id: str) -> None:
    """Empty the tray, and forget any release that was waiting."""
    with _lock:
        _trays.pop(str(node_id), None)
        _bump(str(node_id))


def set_auto_pass(node_id: str, value: bool) -> None:
    """What the switch on the node says right now."""
    with _lock:
        _live_auto_pass[str(node_id)] = bool(value)


def auto_pass(node_id: str, default: bool) -> bool:
    """Whether this run should go straight through.

    The live switch wins over the value queued with the run. ComfyUI copies
    every widget into a run when it is queued, so with Run set to four, all
    four carried whatever auto_pass said at that moment, and flipping it did
    nothing to them - the switch said pass while the node held, and the other
    way round. The panel reports the switch here as it changes. `default` is
    the queued value, used until the panel has said anything.
    """
    with _lock:
        return _live_auto_pass.get(str(node_id), bool(default))
