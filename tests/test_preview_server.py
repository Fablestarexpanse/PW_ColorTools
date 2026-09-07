"""Tests for the input proxy cache.

The cache is what removes the "preview only works after the graph has run once"
limitation every other pack ships with. It holds full-resolution-derived data
for arbitrarily many nodes, so the bound on it is the part that matters — an
unbounded version is a slow leak that only surfaces in long sessions.
"""

from __future__ import annotations

import pytest
import torch

from pw_color import preview_cache as pc
from pw_color import preview_server as ps


@pytest.fixture(autouse=True)
def empty_caches():
    """Both caches start empty for every test.

    They used to be cleared by calling one of two `_reset` helpers by hand at
    the top of each test, which meant a test that forgot inherited whatever the
    previous one left behind — and the bounds tests are precisely the ones that
    fill the cache up.
    """
    pc.inputs.clear()
    pc.outputs.clear()
    yield
    pc.inputs.clear()
    pc.outputs.clear()


def _image(h: int = 32, w: int = 48, seed: int = 1) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return torch.rand(1, h, w, 3, generator=g)



def test_store_and_get_round_trip():
    img = _image()
    pc.store_input("node-1", img)
    entry = pc.get_input("node-1")
    assert entry is not None
    assert entry["width"] == 48 and entry["height"] == 32
    assert entry["proxy"][:2] == b"\xff\xd8"  # JPEG SOI
    assert len(entry["histogram"]["luma"]) == 256


def test_missing_node_returns_none():
    assert pc.get_input("nope") is None


def test_histogram_counts_every_pixel():
    img = _image(16, 16)
    pc.store_input("n", img)
    h = pc.get_input("n")["histogram"]
    for channel in ("r", "g", "b", "luma"):
        assert int(sum(h[channel])) == 16 * 16, channel


def test_histogram_of_flat_black_is_one_spike():
    pc.store_input("n", torch.zeros(1, 8, 8, 3))
    h = pc.get_input("n")["histogram"]
    assert h["luma"][0] == 64
    assert sum(h["luma"][1:]) == 0


def test_histogram_of_flat_white_is_at_the_top():
    pc.store_input("n", torch.ones(1, 8, 8, 3))
    h = pc.get_input("n")["histogram"]
    assert h["luma"][255] == 64


def test_proxy_is_downscaled_but_dimensions_are_reported_full():
    pc.store_input("big", _image(1024, 2048))
    entry = pc.get_input("big")
    # The reported size is the real image; the proxy is what got shrunk.
    assert entry["width"] == 2048 and entry["height"] == 1024
    assert len(entry["proxy"]) < 400_000


def test_cache_is_bounded_by_entry_count():
    img = _image(8, 8)
    for i in range(pc.MAX_ENTRIES + 12):
        pc.store_input(str(i), img)
    assert len(pc.inputs) <= pc.MAX_ENTRIES


def test_eviction_is_least_recently_used():
    img = _image(8, 8)
    for i in range(pc.MAX_ENTRIES):
        pc.store_input(str(i), img)
    # Touch the oldest so it is no longer the least recently used.
    pc.get_input("0")
    pc.store_input("fresh", img)
    assert pc.get_input("0") is not None
    assert pc.get_input("1") is None


def test_restoring_the_same_node_does_not_double_count_bytes():
    img = _image()
    pc.store_input("n", img)
    first = pc.inputs.nbytes
    for _ in range(5):
        pc.store_input("n", img)
    assert len(pc.inputs) == 1
    assert pc.inputs.nbytes == first


def test_bad_input_is_ignored_rather_than_raising():
    """A preview concern must never break a render."""
    pc.store_input("n", None)  # type: ignore[arg-type]
    pc.store_input("n", torch.rand(32, 32, 3))  # missing batch dim
    assert pc.get_input("n") is None


def test_register_routes_is_safe_outside_a_server():
    """ComfyUI skips the whole extension if comfy_entrypoint raises."""
    assert ps.register_routes() is False


# -- output cache, for spatial nodes -----------------------------------------



def test_output_cache_round_trip():
    pc.store_output("n", _image(64, 96))
    entry = pc.get_output("n")
    assert entry is not None
    assert entry["width"] == 96 and entry["height"] == 64
    assert entry["proxy"][:2] == b"\xff\xd8"  # JPEG full frame
    assert entry["crop"][:8] == b"\x89PNG\r\n\x1a\n"  # PNG crop


def test_crop_is_lossless_because_grain_is_high_frequency():
    """JPEG would smooth exactly the detail the crop exists to show, making
    grain look finer and softer than it renders."""
    pc.store_output("n", _image(256, 256))
    entry = pc.get_output("n")
    assert entry["crop"][:8] == b"\x89PNG\r\n\x1a\n", "the 1:1 crop must not be lossy"


def test_crop_is_native_resolution_not_downscaled():
    """Grain size is absolute, so a downscaled crop would misreport it."""
    from PIL import Image
    import io as _io

    src = _image(1024, 1024)
    pc.store_output("n", src)
    with Image.open(_io.BytesIO(pc.get_output("n")["crop"])) as im:
        assert im.size == (pc.CROP_EDGE, pc.CROP_EDGE)

    # And the pixels must match the centre of the source exactly.
    top = (1024 - pc.CROP_EDGE) // 2
    expected = (src[0, top : top + pc.CROP_EDGE, top : top + pc.CROP_EDGE, :3] * 255 + 0.5).to(torch.uint8)
    with Image.open(_io.BytesIO(pc.get_output("n")["crop"])) as im:
        import numpy as np

        got = torch.from_numpy(np.asarray(im.convert("RGB")))
    assert torch.equal(got, expected)


def test_crop_handles_images_smaller_than_the_crop():
    pc.store_output("n", _image(64, 48))
    from PIL import Image
    import io as _io

    with Image.open(_io.BytesIO(pc.get_output("n")["crop"])) as im:
        assert im.size == (48, 48)


def test_output_cache_is_bounded_and_lru():
    img = _image(16, 16)
    for i in range(pc.MAX_ENTRIES + 6):
        pc.store_output(str(i), img)
    assert len(pc.outputs) <= pc.MAX_ENTRIES
    assert pc.get_output("0") is None


def test_output_cache_does_not_double_count_bytes():
    img = _image(64, 64)
    pc.store_output("n", img)
    first = pc.outputs.nbytes
    for _ in range(4):
        pc.store_output("n", img)
    assert len(pc.outputs) == 1 and pc.outputs.nbytes == first


def test_output_and_input_caches_are_independent():
    pc.store_input("n", _image(32, 32))
    assert pc.get_output("n") is None
    pc.store_output("n", _image(32, 32))
    assert pc.get_input("n") is not None and pc.get_output("n") is not None


def test_store_output_for_node_is_silent_without_hidden_data():

    class Fake:
        hidden = None

    assert pc.store_output_for_node(Fake, _image()) is False


def test_bad_output_is_ignored_rather_than_raising():
    pc.store_output("n", None)  # type: ignore[arg-type]
    pc.store_output("n", torch.rand(32, 32, 3))
    assert pc.get_output("n") is None


def test_register_routes_is_idempotent(monkeypatch):
    """ComfyUI can import an extension twice — a Manager reload, a second
    entrypoint — and aiohttp accepts duplicate routes without complaint, so a
    second registration would silently leave two handlers on every path."""
    registered: list[str] = []

    class _Routes:
        def get(self, path):
            registered.append(path)
            return lambda fn: fn

    class _Server:
        instance = type("I", (), {"routes": _Routes()})()

    import sys
    import types

    monkeypatch.setitem(sys.modules, "server", types.SimpleNamespace(PromptServer=_Server))
    monkeypatch.setattr(ps, "_routes_registered", False)

    assert ps.register_routes() is True
    first = len(registered)
    assert first, "expected routes to be registered"

    assert ps.register_routes() is True
    assert len(registered) == first, "a second call registered the routes again"


# -- the routes themselves ---------------------------------------------------


class _FakeWeb:
    """Just enough of aiohttp.web to call a handler and read what it returned.

    The real thing is a ComfyUI dependency and pulling it in would make these
    tests skip on a bare checkout; the handlers only use these two constructors.
    """

    class Response:
        def __init__(self, body=None, content_type=None, headers=None):
            self.body = body
            self.content_type = content_type
            self.headers = headers or {}
            self.status = 200

    class _Json:
        def __init__(self, data, status):
            self.data = data
            self.status = status

    @staticmethod
    def json_response(data, status=200):
        return _FakeWeb._Json(data, status)


class _Request:
    def __init__(self, node_id: str):
        self.match_info = {"node_id": node_id}


def _routes() -> dict:
    return dict(ps._routing_table(_FakeWeb))


def _call(path: str, node_id: str = "n"):
    import asyncio

    return asyncio.run(_routes()[path](_Request(node_id)))


def test_every_route_the_frontend_calls_is_registered():
    """The browser hardcodes these paths; a rename here is a silent 404 there."""
    assert set(_routes()) == {
        "/pw_color/input/{node_id}",
        "/pw_color/histogram/{node_id}",
        "/pw_color/output/{node_id}",
        "/pw_color/output_crop/{node_id}",
        "/pw_color/presets",
    }


def test_input_route_serves_the_cached_jpeg():
    pc.store_input("n", _image())
    res = _call("/pw_color/input/{node_id}")
    assert res.content_type == "image/jpeg"
    assert res.body[:2] == b"\xff\xd8"
    assert res.headers["Cache-Control"] == "no-store_input", "a stale preview is worse than none"


def test_output_crop_route_serves_png_not_jpeg():
    """JPEG smooths high-frequency detail, which is precisely what grain is."""
    pc.store_output("n", _image())
    res = _call("/pw_color/output_crop/{node_id}")
    assert res.content_type == "image/png"
    assert res.body[:8] == b"\x89PNG\r\n\x1a\n"


def test_histogram_route_returns_the_bins_and_the_true_size():
    pc.store_input("n", _image(h=32, w=48))
    res = _call("/pw_color/histogram/{node_id}")
    assert res.status == 200
    assert res.data["width"] == 48 and res.data["height"] == 32
    assert len(res.data["histogram"]["luma"]) == 256


def test_asking_for_a_node_with_nothing_cached_is_a_404_not_a_crash():
    """Normal, not exceptional: the browser asks before the node has run."""
    for path in ("/pw_color/input/{node_id}", "/pw_color/output/{node_id}", "/pw_color/output_crop/{node_id}"):
        res = _call(path, "never-seen")
        assert res.status == 404, path
        assert "error" in res.data


def test_presets_route_serves_the_shipped_file():
    res = _call("/pw_color/presets")
    assert res.status == 200
    ids = [p["id"] for p in res.data["presets"]]
    assert "none" in ids and len(ids) > 1


def test_presets_route_degrades_to_empty_rather_than_failing(monkeypatch, tmp_path):
    """A malformed presets file must cost the preset strip, not the editor."""
    bad = tmp_path / "presets.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(ps, "LOOK_PRESETS", bad)
    res = _call("/pw_color/presets")
    assert res.status == 200
    assert res.data == {"presets": []}
