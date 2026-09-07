"""What each node was given, and what the spatial ones produced.

Solves the limitation every existing colour pack lists: "preview only works
after the graph has run once". The decoded input tensor is cached per node id
as a small JPEG proxy plus a histogram, so a node can fetch its own input on
demand instead of waiting for an execution to hand it one.

Cache policy: bounded by entry count *and* total bytes, evicted
least-recently-used. A colour node's input is a full-resolution decoded image
and a graph can hold many, so an unbounded dict here would be a slow memory
leak that only shows up in long sessions — the kind of bug users blame on
ComfyUI rather than on us.

Split from `preview_server`, which now holds only the HTTP layer. Caching
images and serving them over aiohttp are different jobs with different reasons
to change, and they were sharing a file.
"""

from __future__ import annotations

import io as _io
import logging
import threading
from collections import OrderedDict
from typing import Callable

import torch

from .colour import luma_bt709, srgb_to_linear

__all__ = [
    "store",
    "store_output",
    "store_input_for_node",
    "store_output_for_node",
    "get",
    "get_output",
    "inputs",
    "outputs",
    "PROXY_LONG_EDGE",
    "CROP_EDGE",
    "MAX_ENTRIES",
    "MAX_BYTES",
]

_log = logging.getLogger("PW_Color")

#: Long side of the cached proxy. Big enough to judge a grade on a node panel,
#: small enough that caching a dozen costs a few megabytes.
PROXY_LONG_EDGE = 512
#: Edge length of the 1:1 crop kept for spatial nodes.
#:
#: Grain and halation are the reason this exists. Grain size is absolute — the
#: whole point of PW Grain is that 1.4px grain is 1.4px at any resolution — so a
#: downscaled preview shows it finer than it will render, which is worse than
#: no preview at all. A native-resolution crop shows it at true size.
CROP_EDGE = 512
MAX_ENTRIES = 24
#: Bytes in a megabyte, so the cache ceiling reads as one.
MEGABYTE = 1024 * 1024
MAX_BYTES = 48 * MEGABYTE



class _ByteCache:
    """An LRU of encoded previews, bounded by entry count and by total bytes.

    There were two of these written out by hand — one for inputs, one for
    outputs — with the eviction loop and the byte accounting typed twice. The
    copies had already diverged: the input cache counted only its JPEG proxy
    while the output cache counted proxy plus crop, so the two "48 MB" limits
    meant different amounts of memory.

    Bounded by bytes as well as entries because entries are not the same size:
    a 1:1 PNG crop of grain is an order of magnitude larger than a JPEG proxy,
    and twenty-four of those is not a few megabytes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, dict]" = OrderedDict()
        self._bytes = 0

    @staticmethod
    def _size(entry: dict) -> int:
        return sum(len(v) for v in entry.values() if isinstance(v, (bytes, bytearray)))

    def put(self, key: str, entry: dict) -> None:
        with self._lock:
            old = self._entries.pop(str(key), None)
            if old is not None:
                self._bytes -= self._size(old)
            self._entries[str(key)] = entry
            self._bytes += self._size(entry)
            while self._entries and (len(self._entries) > MAX_ENTRIES or self._bytes > MAX_BYTES):
                _, dropped = self._entries.popitem(last=False)
                self._bytes -= self._size(dropped)

    def get(self, key: str) -> dict | None:
        with self._lock:
            entry = self._entries.get(str(key))
            if entry is not None:
                self._entries.move_to_end(str(key))
            return entry

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def nbytes(self) -> int:
        return self._bytes


#: What each node was given, for the histogram and the before-side of a compare.
inputs = _ByteCache()
#: What a spatial node produced, for nodes whose effect cannot be baked.
outputs = _ByteCache()


def _histogram(image: torch.Tensor, bins: int = 256) -> dict[str, list[float]]:
    """Per-channel and luma histogram of an sRGB-encoded ``[B,H,W,3]`` tensor.

    Computed here rather than in the browser because the browser only ever has
    the downscaled proxy, and a histogram of a proxy is not the histogram of the
    image — resampling fills in the gaps that make a posterised source obvious.
    """
    img = image[0, ..., :3].reshape(-1, 3).float().clamp(0, 1)
    out: dict[str, list[float]] = {}
    for i, key in enumerate(("r", "g", "b")):
        idx = (img[:, i] * (bins - 1)).round().to(torch.int64)
        out[key] = torch.bincount(idx, minlength=bins).float().tolist()
    lum = luma_bt709(srgb_to_linear(img)).clamp(0, 1)
    out["luma"] = torch.bincount((lum * (bins - 1)).round().to(torch.int64), minlength=bins).float().tolist()
    return out


def _encode_proxy(image: torch.Tensor) -> bytes:
    # Pillow and numpy are ComfyUI runtime dependencies rather than ours, and
    # only the rendering paths need them. Deferred so importing the pack stays
    # cheap and a colour-only use never touches them.
    from PIL import Image

    img = image[0, ..., :3].float().clamp(0, 1)
    h, w = img.shape[0], img.shape[1]
    scale = min(1.0, PROXY_LONG_EDGE / max(h, w))
    arr = (img * 255.0 + 0.5).clamp(0, 255).to(torch.uint8).cpu().numpy()
    pil = Image.fromarray(arr, "RGB")
    if scale < 1.0:
        pil = pil.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = _io.BytesIO()
    pil.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def store(node_id: str, image: torch.Tensor | None) -> None:
    """Cache a node's input. Safe to call from the execution thread.

    ``None`` is accepted rather than rejected: several nodes have an optional
    IMAGE input, and "there was no image" is a normal thing for them to report.
    """
    if image is None or image.ndim != 4:
        return
    try:
        entry = {
            "proxy": _encode_proxy(image),
            "histogram": _histogram(image),
            "width": int(image.shape[2]),
            "height": int(image.shape[1]),
        }
    except Exception:  # pragma: no cover - never let a preview break a render
        _log.exception("PW Color: failed to cache input proxy for node %s", node_id)
        return
    inputs.put(str(node_id), entry)


def _encode_crop(image: torch.Tensor) -> bytes:
    """A native-resolution centre crop, as PNG.

    PNG, not JPEG, and this matters: JPEG smooths high-frequency detail, which
    is precisely what grain *is*. A lossy crop would show the user a softer,
    finer grain than the one being rendered.
    """
    # Deferred: see the note on the first such import in this module.
    from PIL import Image

    img = image[0, ..., :3].float().clamp(0, 1)
    h, w = img.shape[0], img.shape[1]
    edge = min(CROP_EDGE, h, w)
    top, left = (h - edge) // 2, (w - edge) // 2
    arr = (img[top : top + edge, left : left + edge] * 255.0 + 0.5).clamp(0, 255).to(torch.uint8).cpu().numpy()
    buf = _io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def store_output(node_id: str, image: torch.Tensor | None) -> None:
    """Cache what a node *produced*, for nodes whose effect cannot be previewed
    any other way.

    Colour nodes bake to a lattice and the browser can reproduce them exactly.
    Grain, halation and vignette are spatial, so there is nothing to bake — the
    only honest preview is the real output, which means caching it.
    """
    if image is None or image.ndim != 4:
        return
    try:
        entry = {
            "proxy": _encode_proxy(image),
            "crop": _encode_crop(image),
            "width": int(image.shape[2]),
            "height": int(image.shape[1]),
        }
    except Exception:  # pragma: no cover - never let a preview break a render
        _log.exception("PW Color: failed to cache output for node %s", node_id)
        return
    outputs.put(str(node_id), entry)


def get_output(node_id: str) -> dict | None:
    return outputs.get(str(node_id))


def _executing_node_id(node_cls: type, *, quiet: bool) -> str | None:
    """The id of the node currently executing, or ``None`` with a reason logged.

    ``cls.hidden`` is populated on a per-execution clone of the node class
    (``PREPARE_CLASS_CLONE``), so it is ``None`` outside a real run — that case
    is normal in tests and says nothing.

    A node that *has* hidden data but no ``unique_id`` is a different story:
    that is a schema missing ``io.Hidden.unique_id``, and the symptom is a
    preview panel that stays empty forever with no other clue.
    """
    hidden = getattr(node_cls, "hidden", None)
    if hidden is None:
        _log.debug("PW Color: no hidden data on %s, skipping preview cache", getattr(node_cls, "__name__", node_cls))
        return None
    node_id = getattr(hidden, "unique_id", None)
    if node_id is None and not quiet:
        _log.warning(
            "PW Color: %s has no unique_id; the node's preview will stay empty. "
            "Is io.Hidden.unique_id declared in its schema?",
            getattr(node_cls, "__name__", node_cls),
        )
    return None if node_id is None else str(node_id)


def _store_for_node(
    cache_call: Callable[[str, torch.Tensor | None], None],
    node_cls: type,
    image: torch.Tensor | None,
    what: str,
    *,
    quiet: bool,
) -> bool:
    """Run ``cache_call`` under the executing node's id. Never raises.

    Every node used to do this inline behind a bare ``except Exception: pass``,
    which is how a broken cache went unnoticed for the whole project: the
    preview stayed empty and nothing anywhere said why. One helper, one place
    to get it right, and a warning when it does not work.
    """
    node_id = _executing_node_id(node_cls, quiet=quiet)
    if node_id is None:
        return False
    try:
        cache_call(node_id, image)
        return True
    except Exception:
        _log.warning("PW Color: could not cache the %s for node %s", what, node_id, exc_info=True)
        return False


def store_input_for_node(node_cls: type, image: torch.Tensor | None) -> bool:
    """Cache what this node was *given*, keyed by the executing node."""
    return _store_for_node(store, node_cls, image, "input", quiet=False)


def store_output_for_node(node_cls: type, image: torch.Tensor | None) -> bool:
    """Cache what this node *produced*, keyed by the executing node."""
    return _store_for_node(store_output, node_cls, image, "output", quiet=True)


def get(node_id: str) -> dict | None:
    return inputs.get(str(node_id))


