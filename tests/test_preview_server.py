"""Tests for the input proxy cache.

The cache is what removes the "preview only works after the graph has run once"
limitation every other pack ships with. It holds full-resolution-derived data
for arbitrarily many nodes, so the bound on it is the part that matters — an
unbounded version is a slow leak that only surfaces in long sessions.
"""

from __future__ import annotations

import torch

from pw_color import preview_server as ps


def _image(h: int = 32, w: int = 48, seed: int = 1) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return torch.rand(1, h, w, 3, generator=g)


def _reset() -> None:
    with ps._lock:
        ps._cache.clear()
    ps._bytes = 0


def test_store_and_get_round_trip():
    _reset()
    img = _image()
    ps.store("node-1", img)
    entry = ps.get("node-1")
    assert entry is not None
    assert entry["width"] == 48 and entry["height"] == 32
    assert entry["proxy"][:2] == b"\xff\xd8"  # JPEG SOI
    assert len(entry["histogram"]["luma"]) == 256


def test_missing_node_returns_none():
    _reset()
    assert ps.get("nope") is None


def test_histogram_counts_every_pixel():
    _reset()
    img = _image(16, 16)
    ps.store("n", img)
    h = ps.get("n")["histogram"]
    for channel in ("r", "g", "b", "luma"):
        assert int(sum(h[channel])) == 16 * 16, channel


def test_histogram_of_flat_black_is_one_spike():
    _reset()
    ps.store("n", torch.zeros(1, 8, 8, 3))
    h = ps.get("n")["histogram"]
    assert h["luma"][0] == 64
    assert sum(h["luma"][1:]) == 0


def test_histogram_of_flat_white_is_at_the_top():
    _reset()
    ps.store("n", torch.ones(1, 8, 8, 3))
    h = ps.get("n")["histogram"]
    assert h["luma"][255] == 64


def test_proxy_is_downscaled_but_dimensions_are_reported_full():
    _reset()
    ps.store("big", _image(1024, 2048))
    entry = ps.get("big")
    # The reported size is the real image; the proxy is what got shrunk.
    assert entry["width"] == 2048 and entry["height"] == 1024
    assert len(entry["proxy"]) < 400_000


def test_cache_is_bounded_by_entry_count():
    _reset()
    img = _image(8, 8)
    for i in range(ps.MAX_ENTRIES + 12):
        ps.store(str(i), img)
    assert len(ps._cache) <= ps.MAX_ENTRIES


def test_eviction_is_least_recently_used():
    _reset()
    img = _image(8, 8)
    for i in range(ps.MAX_ENTRIES):
        ps.store(str(i), img)
    # Touch the oldest so it is no longer the least recently used.
    ps.get("0")
    ps.store("fresh", img)
    assert ps.get("0") is not None
    assert ps.get("1") is None


def test_restoring_the_same_node_does_not_double_count_bytes():
    _reset()
    img = _image()
    ps.store("n", img)
    first = ps._bytes
    for _ in range(5):
        ps.store("n", img)
    assert len(ps._cache) == 1
    assert ps._bytes == first


def test_bad_input_is_ignored_rather_than_raising():
    """A preview concern must never break a render."""
    _reset()
    ps.store("n", None)  # type: ignore[arg-type]
    ps.store("n", torch.rand(32, 32, 3))  # missing batch dim
    assert ps.get("n") is None


def test_register_routes_is_safe_outside_a_server():
    """ComfyUI skips the whole extension if comfy_entrypoint raises."""
    assert ps.register_routes() is False


# -- output cache, for spatial nodes -----------------------------------------


def _reset_output() -> None:
    with ps._lock:
        ps._out_cache.clear()
    ps._out_bytes = 0


def test_output_cache_round_trip():
    _reset_output()
    ps.store_output("n", _image(64, 96))
    entry = ps.get_output("n")
    assert entry is not None
    assert entry["width"] == 96 and entry["height"] == 64
    assert entry["proxy"][:2] == b"\xff\xd8"  # JPEG full frame
    assert entry["crop"][:8] == b"\x89PNG\r\n\x1a\n"  # PNG crop


def test_crop_is_lossless_because_grain_is_high_frequency():
    """JPEG would smooth exactly the detail the crop exists to show, making
    grain look finer and softer than it renders."""
    _reset_output()
    ps.store_output("n", _image(256, 256))
    entry = ps.get_output("n")
    assert entry["crop"][:8] == b"\x89PNG\r\n\x1a\n", "the 1:1 crop must not be lossy"


def test_crop_is_native_resolution_not_downscaled():
    """Grain size is absolute, so a downscaled crop would misreport it."""
    from PIL import Image
    import io as _io

    _reset_output()
    src = _image(1024, 1024)
    ps.store_output("n", src)
    with Image.open(_io.BytesIO(ps.get_output("n")["crop"])) as im:
        assert im.size == (ps.CROP_EDGE, ps.CROP_EDGE)

    # And the pixels must match the centre of the source exactly.
    top = (1024 - ps.CROP_EDGE) // 2
    expected = (src[0, top : top + ps.CROP_EDGE, top : top + ps.CROP_EDGE, :3] * 255 + 0.5).to(torch.uint8)
    with Image.open(_io.BytesIO(ps.get_output("n")["crop"])) as im:
        import numpy as np

        got = torch.from_numpy(np.asarray(im.convert("RGB")))
    assert torch.equal(got, expected)


def test_crop_handles_images_smaller_than_the_crop():
    _reset_output()
    ps.store_output("n", _image(64, 48))
    from PIL import Image
    import io as _io

    with Image.open(_io.BytesIO(ps.get_output("n")["crop"])) as im:
        assert im.size == (48, 48)


def test_output_cache_is_bounded_and_lru():
    _reset_output()
    img = _image(16, 16)
    for i in range(ps.MAX_ENTRIES + 6):
        ps.store_output(str(i), img)
    assert len(ps._out_cache) <= ps.MAX_ENTRIES
    assert ps.get_output("0") is None


def test_output_cache_does_not_double_count_bytes():
    _reset_output()
    img = _image(64, 64)
    ps.store_output("n", img)
    first = ps._out_bytes
    for _ in range(4):
        ps.store_output("n", img)
    assert len(ps._out_cache) == 1 and ps._out_bytes == first


def test_output_and_input_caches_are_independent():
    _reset()
    _reset_output()
    ps.store("n", _image(32, 32))
    assert ps.get_output("n") is None
    ps.store_output("n", _image(32, 32))
    assert ps.get("n") is not None and ps.get_output("n") is not None


def test_store_output_for_node_is_silent_without_hidden_data():
    _reset_output()

    class Fake:
        hidden = None

    assert ps.store_output_for_node(Fake, _image()) is False


def test_bad_output_is_ignored_rather_than_raising():
    _reset_output()
    ps.store_output("n", None)  # type: ignore[arg-type]
    ps.store_output("n", torch.rand(32, 32, 3))
    assert ps.get_output("n") is None
