"""Input proxy cache and HTTP routes.

Solves the limitation every existing colour pack lists: "preview only works
after the graph has run once". We cache the decoded input tensor per node id as
a small JPEG proxy plus a histogram, and serve them on request, so a node can
fetch its own input on demand instead of waiting for an execution to hand it one.

Cache policy: bounded by entry count and total bytes, evicted least-recently-used.
A colour node's input is a full-resolution decoded image and there can be a lot
of them in a graph, so an unbounded dict here would be a slow memory leak that
only shows up in long sessions — which is exactly the kind of bug users blame on
ComfyUI rather than on us.
"""

from __future__ import annotations

import io as _io
import logging
import threading
from collections import OrderedDict

import torch

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
MAX_BYTES = 48 * 1024 * 1024

_lock = threading.Lock()
_cache: "OrderedDict[str, dict]" = OrderedDict()
_out_cache: "OrderedDict[str, dict]" = OrderedDict()
_bytes = 0
_out_bytes = 0


def _histogram(image: torch.Tensor, bins: int = 256) -> dict[str, list[float]]:
    """Per-channel and luma histogram of an sRGB-encoded ``[B,H,W,3]`` tensor.

    Computed here rather than in the browser because the browser only ever has
    the downscaled proxy, and a histogram of a proxy is not the histogram of the
    image — resampling fills in the gaps that make a posterised source obvious.
    """
    from .colour import luma_bt709, srgb_to_linear

    img = image[0, ..., :3].reshape(-1, 3).float().clamp(0, 1)
    out: dict[str, list[float]] = {}
    for i, key in enumerate(("r", "g", "b")):
        idx = (img[:, i] * (bins - 1)).round().to(torch.int64)
        out[key] = torch.bincount(idx, minlength=bins).float().tolist()
    lum = luma_bt709(srgb_to_linear(img)).clamp(0, 1)
    out["luma"] = torch.bincount((lum * (bins - 1)).round().to(torch.int64), minlength=bins).float().tolist()
    return out


def _encode_proxy(image: torch.Tensor) -> bytes:
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


def store(node_id: str, image: torch.Tensor) -> None:
    """Cache a node's input. Safe to call from the execution thread."""
    global _bytes
    if image is None or image.ndim != 4:
        return
    try:
        proxy = _encode_proxy(image)
        entry = {
            "proxy": proxy,
            "histogram": _histogram(image),
            "width": int(image.shape[2]),
            "height": int(image.shape[1]),
        }
    except Exception:  # pragma: no cover - never let a preview break a render
        _log.exception("PW Color: failed to cache input proxy for node %s", node_id)
        return

    with _lock:
        old = _cache.pop(str(node_id), None)
        if old is not None:
            _bytes -= len(old["proxy"])
        _cache[str(node_id)] = entry
        _bytes += len(proxy)
        while _cache and (len(_cache) > MAX_ENTRIES or _bytes > MAX_BYTES):
            _, dropped = _cache.popitem(last=False)
            _bytes -= len(dropped["proxy"])


def _encode_crop(image: torch.Tensor) -> bytes:
    """A native-resolution centre crop, as PNG.

    PNG, not JPEG, and this matters: JPEG smooths high-frequency detail, which
    is precisely what grain *is*. A lossy crop would show the user a softer,
    finer grain than the one being rendered.
    """
    from PIL import Image

    img = image[0, ..., :3].float().clamp(0, 1)
    h, w = img.shape[0], img.shape[1]
    edge = min(CROP_EDGE, h, w)
    top, left = (h - edge) // 2, (w - edge) // 2
    arr = (img[top : top + edge, left : left + edge] * 255.0 + 0.5).clamp(0, 255).to(torch.uint8).cpu().numpy()
    buf = _io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def store_output(node_id: str, image: torch.Tensor) -> None:
    """Cache what a node *produced*, for nodes whose effect cannot be previewed
    any other way.

    Colour nodes bake to a lattice and the browser can reproduce them exactly.
    Grain, halation and vignette are spatial, so there is nothing to bake — the
    only honest preview is the real output, which means caching it.
    """
    global _out_bytes
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

    size = len(entry["proxy"]) + len(entry["crop"])
    with _lock:
        old = _out_cache.pop(str(node_id), None)
        if old is not None:
            _out_bytes -= len(old["proxy"]) + len(old["crop"])
        _out_cache[str(node_id)] = entry
        _out_bytes += size
        while _out_cache and (len(_out_cache) > MAX_ENTRIES or _out_bytes > MAX_BYTES):
            _, dropped = _out_cache.popitem(last=False)
            _out_bytes -= len(dropped["proxy"]) + len(dropped["crop"])


def get_output(node_id: str) -> dict | None:
    with _lock:
        entry = _out_cache.get(str(node_id))
        if entry is not None:
            _out_cache.move_to_end(str(node_id))
        return entry


def store_output_for_node(node_cls, image: torch.Tensor) -> bool:
    """:func:`store_output`, keyed by the executing node. Never raises."""
    hidden = getattr(node_cls, "hidden", None)
    node_id = getattr(hidden, "unique_id", None) if hidden is not None else None
    if node_id is None:
        return False
    try:
        store_output(str(node_id), image)
        return True
    except Exception:
        _log.warning("PW Color: could not cache the output for node %s", node_id, exc_info=True)
        return False


def store_for_node(node_cls, image: torch.Tensor) -> bool:
    """Cache ``image`` under the executing node's id. Never raises.

    Every node did this inline behind a bare ``except Exception: pass``, which
    is how a broken cache went unnoticed for the whole project: the preview
    stayed empty and nothing anywhere said why. One helper, one place to get it
    right, and a warning when it does not work.

    ``cls.hidden`` is populated on a per-execution clone of the node class
    (``PREPARE_CLASS_CLONE``), so it is ``None`` outside a real run — that case
    is silent, because it is normal in tests.
    """
    hidden = getattr(node_cls, "hidden", None)
    if hidden is None:
        _log.debug("PW Color: no hidden data on %s, skipping input cache", getattr(node_cls, "__name__", node_cls))
        return False
    node_id = getattr(hidden, "unique_id", None)
    if node_id is None:
        _log.warning(
            "PW Color: %s has no unique_id; the node's preview will stay empty. "
            "Is io.Hidden.unique_id declared in its schema?",
            getattr(node_cls, "__name__", node_cls),
        )
        return False
    try:
        store(str(node_id), image)
        return True
    except Exception:
        _log.warning("PW Color: could not cache the input for node %s", node_id, exc_info=True)
        return False


def get(node_id: str) -> dict | None:
    with _lock:
        entry = _cache.get(str(node_id))
        if entry is not None:
            _cache.move_to_end(str(node_id))
        return entry


def register_routes() -> bool:
    """Attach our routes to ComfyUI's aiohttp app. Returns False if unavailable.

    Never raises. The preview routes are a convenience; a headless run, a bare
    import or a future rename of ``PromptServer.instance`` must degrade to "no
    histogram in the editor", not to "the pack failed to load". ComfyUI catches
    exceptions out of ``comfy_entrypoint`` and skips the *entire* extension, so
    an unguarded failure here costs every node in the pack.
    """
    try:
        from aiohttp import web
        from server import PromptServer

        routes = PromptServer.instance.routes
    except Exception:  # pragma: no cover - outside a running ComfyUI server
        _log.debug("PW Color: preview routes unavailable", exc_info=True)
        return False

    @routes.get("/pw_color/input/{node_id}")
    async def _input_proxy(request):
        entry = get(request.match_info["node_id"])
        if entry is None:
            return web.json_response({"error": "no cached input"}, status=404)
        return web.Response(
            body=entry["proxy"],
            content_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @routes.get("/pw_color/histogram/{node_id}")
    async def _input_histogram(request):
        entry = get(request.match_info["node_id"])
        if entry is None:
            return web.json_response({"error": "no cached input"}, status=404)
        return web.json_response(
            {"histogram": entry["histogram"], "width": entry["width"], "height": entry["height"]}
        )

    @routes.get("/pw_color/output/{node_id}")
    async def _output_proxy(request):
        entry = get_output(request.match_info["node_id"])
        if entry is None:
            return web.json_response({"error": "no cached output"}, status=404)
        return web.Response(body=entry["proxy"], content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @routes.get("/pw_color/output_crop/{node_id}")
    async def _output_crop(request):
        entry = get_output(request.match_info["node_id"])
        if entry is None:
            return web.json_response({"error": "no cached output"}, status=404)
        return web.Response(body=entry["crop"], content_type="image/png", headers={"Cache-Control": "no-store"})

    @routes.get("/pw_color/presets")
    async def _presets(_request):
        """Look presets, so the node can bake each one onto the user's own image.

        Served rather than bundled into the JS: presets are data, and a user
        dropping a file into looks/ should not need a rebuild to see it.
        """
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "looks" / "presets.json"
        try:
            import json

            return web.json_response(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _log.exception("PW Color: could not read %s", path)
            return web.json_response({"presets": []})

    return True
