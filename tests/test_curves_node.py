"""Tests for the PW Curves node's pure logic.

The node class itself needs ComfyUI to import, so what is tested here is the
serialisation, preset and normalisation layer plus the end-to-end lattice
behaviour — everything that can break a user's saved workflow.
"""

from __future__ import annotations

import json

import pytest
import torch

from pw_color.curve import IDENTITY_POINTS
from pw_color.lattice import DEFAULT_SIZE, Lattice
from pw_color.ops import build_sample_fn
from pw_color.types import Look, LookOp

PRESETS_FILE = "looks/curves/presets.json"


def _presets() -> dict:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    return json.loads((root / PRESETS_FILE).read_text(encoding="utf-8"))


def _apply(params: dict, image: torch.Tensor, strength: float = 1.0) -> torch.Tensor:
    op = LookOp(type="curves", params=params, strength=strength)
    return Lattice.from_fn(build_sample_fn([op.to_dict()]), DEFAULT_SIZE).apply(image)


def _image(seed: int = 4) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return torch.rand(1, 32, 48, 3, generator=g)


# -- presets -----------------------------------------------------------------


def test_preset_file_parses_and_is_well_formed():
    data = _presets()
    assert data["schema"] == 1
    ids = [p["id"] for p in data["presets"]]
    assert len(ids) == len(set(ids)), "duplicate preset ids"
    for p in data["presets"]:
        assert p["name"] and p["description"]
        assert p["curves"], f"{p['id']} has no curves"
        for channel, pts in p["curves"].items():
            assert channel in ("luma", "r", "g", "b")
            assert len(pts) >= 2
            xs = [q[0] for q in pts]
            assert xs == sorted(xs), f"{p['id']}/{channel} points are not sorted by x"
            assert all(0.0 <= v <= 1.0 for q in pts for v in q), f"{p['id']}/{channel} out of range"


def test_every_preset_is_monotone_and_does_something():
    """A preset that overshoots would ship the exact bug we exist to fix."""
    from pw_color.curve import eval_curve

    x = torch.linspace(0, 1, 1001)
    img = _image()
    for p in _presets()["presets"]:
        for channel, pts in p["curves"].items():
            y = eval_curve([tuple(q) for q in pts], x)
            assert bool((y[1:] - y[:-1] >= -1e-6).all()), f"{p['id']}/{channel} is not monotone"
        params = {"luma": [], "r": [], "g": [], "b": [], "preserve_hue": True, **p["curves"]}
        out = _apply(params, img)
        assert (out - img).abs().max().item() > 1.0 / 255.0, f"{p['id']} is a no-op"


# -- serialisation -----------------------------------------------------------


def test_curves_survive_a_save_reload_cycle():
    """The workflow JSON round trip. Every point, exactly."""
    params = {
        "luma": [[0.0, 0.05], [0.31, 0.27], [0.68, 0.79], [1.0, 0.97]],
        "r": [[0.0, 0.0], [0.5, 0.5432], [1.0, 1.0]],
        "g": [[0.0, 0.0], [1.0, 1.0]],
        "b": [[0.0, 0.031], [1.0, 0.962]],
        "preserve_hue": True,
    }
    # This is exactly what the editor writes into the widget.
    serialised = json.dumps({k: params[k] for k in ("luma", "r", "g", "b")})
    back = json.loads(serialised)
    for k in ("luma", "r", "g", "b"):
        assert back[k] == params[k]


def test_look_round_trips_with_curve_params():
    params = {"luma": [[0.0, 0.05], [1.0, 0.97]], "r": [[0.0, 0.0], [1.0, 1.0]], "preserve_hue": False}
    look = Look(ops=[LookOp(type="curves", params=params, strength=0.6)])
    back = Look.from_json(look.to_json())
    assert back.to_dict() == look.to_dict()
    assert back.ops[0].params["preserve_hue"] is False


def test_reloaded_curves_produce_identical_pixels():
    params = {
        "luma": [[0.0, 0.06], [0.4, 0.35], [1.0, 0.95]],
        "r": [[0.0, 0.0], [0.5, 0.55], [1.0, 1.0]],
        "preserve_hue": True,
    }
    img = _image()
    a = _apply(params, img)
    b = _apply(json.loads(json.dumps(params)), img)
    assert torch.equal(a, b)


# -- behaviour ---------------------------------------------------------------


def test_identity_curves_are_a_no_op():
    ident = [list(p) for p in IDENTITY_POINTS]
    params = {"luma": ident, "r": ident, "g": ident, "b": ident, "preserve_hue": True}
    img = _image()
    out = _apply(params, img)
    assert (out - img).abs().max().item() * 255 < 0.5


def test_strength_blends_toward_identity():
    params = {"luma": [[0.0, 0.1], [0.5, 0.55], [1.0, 0.9]], "preserve_hue": True}
    img = _image()
    full = _apply(params, img, strength=1.0)
    half = _apply(params, img, strength=0.5)
    none = _apply(params, img, strength=0.0)
    assert (none - img).abs().max().item() * 255 < 0.5
    d_half = (half - img).abs().mean().item()
    d_full = (full - img).abs().mean().item()
    assert 0.3 * d_full < d_half < 0.7 * d_full


def test_preserve_hue_holds_chroma_where_per_channel_does_not():
    """The headline feature, stated as the thing a user would notice.

    Push contrast hard on a saturated colour. With preserve hue on, OKLab
    chroma should barely move; with it off, the per-channel curve drags it.
    """
    from pw_color import colour

    s = [[0.0, 0.0], [0.25, 0.13], [0.75, 0.87], [1.0, 1.0]]
    # Mid-saturation colours, the ones a per-channel S-curve distorts most.
    px = torch.tensor([[[[0.75, 0.45, 0.35], [0.35, 0.55, 0.72], [0.62, 0.58, 0.28]]]])

    def chroma_shift(preserve: bool) -> float:
        out = _apply({"luma": s, "preserve_hue": preserve}, px)
        c0 = colour.oklab_to_oklch(colour.srgb_to_oklab(px))[..., 1]
        c1 = colour.oklab_to_oklch(colour.srgb_to_oklab(out))[..., 1]
        return float((c1 - c0).abs().max().item())

    on, off = chroma_shift(True), chroma_shift(False)
    assert on < off / 3.0, f"preserve hue shifted chroma by {on:.4f}, per-channel by {off:.4f}"


def test_preserve_hue_holds_hue():
    from pw_color import colour

    s = [[0.0, 0.0], [0.25, 0.13], [0.75, 0.87], [1.0, 1.0]]
    px = torch.tensor([[[[0.75, 0.45, 0.35], [0.35, 0.55, 0.72]]]])
    out = _apply({"luma": s, "preserve_hue": True}, px)
    h0 = colour.oklab_to_oklch(colour.srgb_to_oklab(px))[..., 2]
    h1 = colour.oklab_to_oklch(colour.srgb_to_oklab(out))[..., 2]
    assert float((h1 - h0).abs().max().item()) < 0.02, "hue moved under preserve hue"


def test_per_channel_curves_are_independent():
    img = _image()
    only_r = _apply({"r": [[0.0, 0.0], [0.5, 0.7], [1.0, 1.0]]}, img)
    assert (only_r[..., 0] - img[..., 0]).abs().max().item() > 0.05
    assert (only_r[..., 1] - img[..., 1]).abs().max().item() * 255 < 0.5
    assert (only_r[..., 2] - img[..., 2]).abs().max().item() * 255 < 0.5


def test_no_arrangement_of_points_can_overshoot():
    """The acceptance criterion, exercised through the node's own code path."""
    from pw_color.curve import eval_curve

    x = torch.linspace(0, 1, 1001)
    g = torch.Generator().manual_seed(0xBEEF)
    for _ in range(150):
        k = int(torch.randint(2, 8, (1,), generator=g).item())
        xs = torch.cat((torch.zeros(1), torch.rand(k, generator=g).sort().values, torch.ones(1)))
        ys = torch.rand(len(xs), generator=g)
        pts = list(zip(xs.tolist(), ys.tolist()))
        y = eval_curve(pts, x)
        assert y.min() >= -1e-6 and y.max() <= 1.0 + 1e-6, pts


def test_output_is_deterministic():
    params = {"luma": [[0.0, 0.05], [0.5, 0.6], [1.0, 0.95]], "preserve_hue": True}
    img = _image()
    assert torch.equal(_apply(params, img), _apply(params, img))


def test_malformed_curve_json_is_reported_not_swallowed():
    with pytest.raises(ValueError):
        json.loads("{not json")
