"""Tests for the PW Look grade ops.

The node itself needs ComfyUI to import, so what is covered here is every
per-pixel op, the glow pass, compositing, the presets file, and the properties
that make the controls behave the way the labels promise.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import torch

from pw_color import colour
from pw_color.blend import BLEND_MODES, composite
from pw_color.glow import apply_glow, gaussian_blur
from pw_color.lattice import DEFAULT_SIZE, FINAL_SIZE, Lattice
from pw_color.look import HSL_BANDS, op_colour, op_gradient_map, op_hsl, op_tone, ramp_from_palette
from pw_color.ops import build_sample_fn

ROOT = Path(__file__).resolve().parents[1]


def _ramp(n: int = 512) -> torch.Tensor:
    v = torch.linspace(0.0, 1.0, n)
    return torch.stack((v, v, v), dim=-1)


def _colours() -> torch.Tensor:
    return torch.tensor(
        [[0.75, 0.45, 0.35], [0.35, 0.55, 0.72], [0.62, 0.58, 0.28], [0.5, 0.5, 0.5], [0.2, 0.6, 0.3]]
    )


def _L(rgb: torch.Tensor) -> torch.Tensor:
    return colour.srgb_to_oklab(rgb)[..., 0]


def _chroma(rgb: torch.Tensor) -> torch.Tensor:
    lab = colour.srgb_to_oklab(rgb)
    return torch.sqrt(lab[..., 1] ** 2 + lab[..., 2] ** 2)


def _hue(rgb: torch.Tensor) -> torch.Tensor:
    lab = colour.srgb_to_oklab(rgb)
    return torch.atan2(lab[..., 2], lab[..., 1])


# -- tone --------------------------------------------------------------------


def test_tone_identity():
    r = _ramp()
    assert torch.allclose(op_tone(r, {}), r, atol=1e-6)


def test_exposure_is_monotone_and_brightens():
    r = _ramp()
    up = op_tone(r, {"exposure": 1.0})
    assert (up[1:, 0] - up[:-1, 0] >= -1e-6).all()
    assert (up[10:-10, 0] > r[10:-10, 0]).all()


def test_contrast_pivots_around_middle_grey():
    """A 0.5 pivot drags midtone skin darker as contrast rises; 0.18 does not."""
    pivot_srgb = colour.linear_to_srgb(torch.tensor([0.18, 0.18, 0.18]))
    out = op_tone(pivot_srgb.unsqueeze(0), {"contrast": 0.5})
    assert torch.allclose(out[0], pivot_srgb, atol=2e-3)


def test_each_tonal_band_moves_its_own_region_most():
    """blacks/shadows/highlights/whites must be separable, or the four sliders
    are really one slider with extra steps."""
    r = _ramp(256)
    base_l = _L(r)
    probes = {"blacks": 0.05, "shadows": 0.33, "highlights": 0.67, "whites": 0.97}
    for name, centre in probes.items():
        delta = (_L(op_tone(r, {name: 1.0})) - base_l).abs()
        peak = int(delta.argmax())
        peak_l = float(base_l[peak])
        assert abs(peak_l - centre) < 0.22, f"{name} peaks at L={peak_l:.2f}, expected near {centre}"


def test_tone_bands_hold_chroma():
    """The signature behaviour: tone moves lightness, not colour."""
    c = _colours()
    before = _chroma(c)
    for name in ("blacks", "shadows", "highlights", "whites"):
        after = _chroma(op_tone(c, {name: 0.8}))
        assert float((after - before).abs().max()) < 0.02, name


def test_tone_bands_hold_hue():
    """Neutrals are excluded: their hue is atan2 of two zeros, so it is
    numerically defined and visually meaningless. Every colour with real chroma
    must come through untouched."""
    c = _colours()
    keep = _chroma(c) > 1e-3
    before = _hue(c)[keep]
    after = _hue(op_tone(c, {"shadows": 0.8, "highlights": -0.8}))[keep]
    assert int(keep.sum()) >= 4, "test lost its coloured probes"
    assert float((after - before).abs().max()) < 1e-4


def test_negative_and_positive_tone_are_opposite_in_sign():
    r = _ramp(128)
    up = _L(op_tone(r, {"shadows": 0.6})) - _L(r)
    down = _L(op_tone(r, {"shadows": -0.6})) - _L(r)
    assert float(up.max()) > 0 and float(down.min()) < 0
    assert torch.allclose(up, -down, atol=1e-5)


# -- colour ------------------------------------------------------------------


def test_colour_identity():
    c = _colours()
    assert torch.allclose(op_colour(c, {}), c, atol=1e-6)
    assert torch.allclose(op_colour(c, {"saturation": 1.0}), c, atol=1e-6)


def test_warmth_moves_the_blue_yellow_axis_only():
    c = _colours()
    before = colour.srgb_to_oklab(c)
    after = colour.srgb_to_oklab(op_colour(c, {"warmth": 0.8}))
    assert float((after[:, 2] - before[:, 2]).min()) > 0  # b axis up
    assert float((after[:, 1] - before[:, 1]).abs().max()) < 1e-4  # a axis held


def test_tint_moves_the_green_magenta_axis_only():
    c = _colours()
    before = colour.srgb_to_oklab(c)
    after = colour.srgb_to_oklab(op_colour(c, {"tint": 0.8}))
    assert float((after[:, 1] - before[:, 1]).min()) > 0
    assert float((after[:, 2] - before[:, 2]).abs().max()) < 1e-4


def test_warmth_leaves_black_alone():
    """Scaled by lightness, so deep shadows do not pick up a flat cast."""
    black = torch.tensor([[0.0, 0.0, 0.0]])
    assert float((op_colour(black, {"warmth": 1.0}) - black).abs().max()) < 1e-4


def test_saturation_scales_all_chroma_equally():
    c = _colours()
    before, after = _chroma(c), _chroma(op_colour(c, {"saturation": 1.5}))
    ratio = after / before.clamp(min=1e-6)
    live = before > 1e-3
    assert float((ratio[live] - 1.5).abs().max()) < 0.02


def test_vibrance_lifts_muted_colour_more_than_saturated_colour():
    """The whole reason vibrance exists as a separate control."""
    muted = torch.tensor([[0.52, 0.48, 0.46]])
    vivid = torch.tensor([[0.90, 0.12, 0.08]])
    for probe, name in ((muted, "muted"), (vivid, "vivid")):
        assert float(_chroma(probe)) > 0, name
    muted_gain = float(_chroma(op_colour(muted, {"vibrance": 1.0})) / _chroma(muted))
    vivid_gain = float(_chroma(op_colour(vivid, {"vibrance": 1.0})) / _chroma(vivid))
    assert muted_gain > vivid_gain * 1.5, (muted_gain, vivid_gain)


def test_colour_ops_hold_lightness():
    c = _colours()
    before = _L(c)
    after = _L(op_colour(c, {"warmth": 0.5, "vibrance": 0.5, "saturation": 1.3}))
    assert float((after - before).abs().max()) < 1e-4


def test_neutral_grey_stays_neutral_under_saturation():
    grey = torch.tensor([[0.5, 0.5, 0.5]])
    out = op_colour(grey, {"saturation": 2.0, "vibrance": 1.0})
    assert float((out - grey).abs().max()) < 1e-4


# -- HSL mixer ---------------------------------------------------------------


def test_hsl_identity_when_no_band_is_touched():
    c = _colours()
    assert torch.allclose(op_hsl(c, {}), c, atol=1e-6)
    assert torch.allclose(op_hsl(c, {"bands": {"red": {"hue": 0, "sat": 0, "lum": 0}}}), c, atol=1e-6)


def test_hsl_band_hits_its_own_hue_hardest():
    """Each band must actually own its part of the wheel."""
    for name, centre in HSL_BANDS:
        # A colour sitting exactly on the band centre, at usable chroma.
        probe = colour.oklab_to_srgb(torch.tensor([[0.6, 0.12 * math.cos(centre), 0.12 * math.sin(centre)]])).clamp(0, 1)
        moved = _chroma(op_hsl(probe, {"bands": {name: {"sat": 1.0}}})) / _chroma(probe)
        assert float(moved) > 1.4, f"{name} band barely moved its own hue ({float(moved):.2f})"


def test_hsl_does_not_disturb_the_opposite_band():
    centre = dict(HSL_BANDS)["blue"]
    probe = colour.oklab_to_srgb(torch.tensor([[0.6, 0.12 * math.cos(centre), 0.12 * math.sin(centre)]])).clamp(0, 1)
    out = op_hsl(probe, {"bands": {"orange": {"sat": 1.0, "lum": 0.5}}})
    assert float((out - probe).abs().max()) < 0.02


def test_hsl_leaves_near_neutrals_alone():
    """The chroma gate. Without it, a near-grey sky gets tugged by whichever
    band its numerically-defined-but-meaningless hue happens to land in.

    Budget is 4 code values under the most extreme setting the UI allows —
    every one of the eight bands slammed to maximum on all three axes at once.
    A pure neutral must not move at all.
    """
    bands = {name: {"hue": 1.0, "sat": 1.0, "lum": 1.0} for name, _ in HSL_BANDS}

    pure_grey = torch.tensor([[0.30, 0.30, 0.30]])
    assert float((op_hsl(pure_grey, {"bands": bands}) - pure_grey).abs().max()) < 1e-6

    near_grey = torch.tensor([[0.50, 0.50, 0.505]])
    codes = float((op_hsl(near_grey, {"bands": bands}) - near_grey).abs().max()) * 255
    assert codes < 4.0, f"near-neutral moved {codes:.2f} codes"


def test_hsl_hue_shift_moves_hue_and_holds_chroma():
    centre = dict(HSL_BANDS)["green"]
    probe = colour.oklab_to_srgb(torch.tensor([[0.6, 0.11 * math.cos(centre), 0.11 * math.sin(centre)]])).clamp(0, 1)
    out = op_hsl(probe, {"bands": {"green": {"hue": 1.0}}})
    assert float((_hue(out) - _hue(probe)).abs()) > 0.15
    assert float((_chroma(out) / _chroma(probe) - 1.0).abs()) < 0.05


def test_hsl_wraps_around_the_hue_circle():
    """Red is at +29 degrees and magenta at -32. Without wrapping they look
    ~360 degrees apart and the red band stops affecting reds that lean pink."""
    from pw_color.look import _hue_distance

    d = _hue_distance(torch.tensor([3.0]), -3.0)
    assert abs(float(d)) < math.pi


# -- gradient map ------------------------------------------------------------


def _stops():
    return [[0.0, [0.05, 0.04, 0.12]], [0.5, [0.55, 0.42, 0.38]], [1.0, [0.98, 0.92, 0.78]]]


def test_gradient_map_zero_amount_is_a_no_op():
    c = _colours()
    assert torch.allclose(op_gradient_map(c, {"amount": 0.0, "stops": _stops()}), c, atol=1e-6)


def test_gradient_map_needs_two_stops():
    c = _colours()
    assert torch.allclose(op_gradient_map(c, {"amount": 1.0, "stops": [[0.0, [1, 0, 0]]]}), c, atol=1e-6)


def test_gradient_map_maps_endpoints():
    black = torch.tensor([[0.0, 0.0, 0.0]])
    white = torch.tensor([[1.0, 1.0, 1.0]])
    p = {"amount": 1.0, "blend": "normal", "stops": _stops()}
    assert torch.allclose(op_gradient_map(black, p)[0], torch.tensor(_stops()[0][1]), atol=1e-3)
    assert torch.allclose(op_gradient_map(white, p)[0], torch.tensor(_stops()[2][1]), atol=1e-3)


def test_gradient_map_colour_mode_keeps_image_lightness():
    """What makes a gradient map a grading tool rather than a poster filter."""
    c = _colours()
    before = _L(c)
    after = _L(op_gradient_map(c, {"amount": 1.0, "blend": "colour", "stops": _stops()}))
    assert float((after - before).abs().max()) < 1e-3


def test_gradient_map_amount_interpolates():
    c = _colours()
    p = {"blend": "normal", "stops": _stops()}
    half = op_gradient_map(c, {**p, "amount": 0.5})
    full = op_gradient_map(c, {**p, "amount": 1.0})
    assert torch.allclose(half, torch.lerp(c, full, 0.5), atol=1e-5)


def test_gradient_map_rejects_unknown_blend():
    with pytest.raises(ValueError, match="gradient map blend"):
        op_gradient_map(_colours(), {"amount": 1.0, "blend": "divide", "stops": _stops()})


def test_ramp_from_palette_orders_dark_to_light():
    stops = ramp_from_palette(["#E0A44C", "#1B1A20", "#7F77DD"])
    ls = [colour.srgb_to_oklab(torch.tensor(rgb))[0].item() for _, rgb in stops]
    assert ls == sorted(ls)
    assert stops[0][0] == 0.0 and stops[-1][0] == 1.0


def test_ramp_from_single_colour_is_flat():
    stops = ramp_from_palette(["#7F77DD"])
    assert len(stops) == 2 and stops[0][1] == stops[1][1]


# -- glow (spatial) ----------------------------------------------------------


def _bright_spot(h: int = 96, w: int = 96) -> torch.Tensor:
    img = torch.full((1, h, w, 3), 0.12)
    img[:, h // 2 - 4 : h // 2 + 4, w // 2 - 4 : w // 2 + 4] = 1.0
    return img


def test_glow_zero_is_a_no_op():
    img = _bright_spot()
    assert torch.equal(apply_glow(img, 0.0), img)


def test_glow_spreads_light_beyond_the_source():
    """Light must land outside the bright area — that is the whole effect.

    Measured just outside the square rather than across the frame: a small
    source spread over a 20px radius has very little energy left far away, so a
    distant probe tests the inverse-square falloff, not the feature.
    """
    img = _bright_spot()
    out = apply_glow(img, 0.6, radius=20.0)
    just_outside = out[0, 48, 56] - img[0, 48, 56]
    assert float(just_outside.max()) > 0.02, float(just_outside.max())
    # And it must fall off with distance rather than flooding the frame.
    far = float((out[0, 48, 20] - img[0, 48, 20]).max())
    assert far < float(just_outside.max())


def test_glow_leaves_a_dark_frame_alone():
    dark = torch.full((1, 48, 48, 3), 0.1)
    out = apply_glow(dark, 0.8, threshold=0.65)
    assert float((out - dark).abs().max()) < 1e-3


def test_glow_is_warm():
    img = _bright_spot()
    out = apply_glow(img, 0.6, radius=20.0, warmth=0.6)
    d = (out - img)[0, 48, 20]
    assert float(d[0]) > float(d[2]), "glow should lean amber, not blue"


def test_glow_stays_in_range():
    img = _bright_spot()
    out = apply_glow(img, 1.0, radius=40.0)
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


def test_glow_alpha_passes_through():
    img = torch.cat((_bright_spot(), torch.rand(1, 96, 96, 1)), dim=-1)
    out = apply_glow(img, 0.5)
    assert torch.equal(out[..., 3:], img[..., 3:])


def test_blur_preserves_mean():
    g = torch.Generator().manual_seed(2)
    img = torch.rand(1, 64, 64, 3, generator=g)
    out = gaussian_blur(img, 3.0)
    assert abs(float(out.mean() - img.mean())) < 1e-3


def test_blur_does_not_darken_the_frame_edge():
    """Reflect padding, not zero padding — zero padding reads as a vignette."""
    flat = torch.full((1, 48, 48, 3), 0.6)
    out = gaussian_blur(flat, 4.0)
    assert float((out - flat).abs().max()) < 1e-4


# -- compositing -------------------------------------------------------------


@pytest.mark.parametrize("mode", BLEND_MODES)
def test_composite_opacity_zero_is_the_base(mode: str):
    g = torch.Generator().manual_seed(1)
    base, layer = torch.rand(8, 3, generator=g), torch.rand(8, 3, generator=g)
    assert torch.allclose(composite(base, layer, mode, 0.0), base, atol=1e-6)


@pytest.mark.parametrize("mode", BLEND_MODES)
def test_composite_stays_in_range(mode: str):
    g = torch.Generator().manual_seed(1)
    base, layer = torch.rand(64, 3, generator=g), torch.rand(64, 3, generator=g)
    out = composite(base, layer, mode)
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


def test_composite_normal_is_the_layer():
    g = torch.Generator().manual_seed(1)
    base, layer = torch.rand(8, 3, generator=g), torch.rand(8, 3, generator=g)
    assert torch.allclose(composite(base, layer, "normal"), layer, atol=1e-6)


def test_composite_rejects_unknown_mode():
    with pytest.raises(ValueError, match="blend mode"):
        composite(torch.zeros(4, 3), torch.zeros(4, 3), "divide")


# -- presets -----------------------------------------------------------------


def _preset_data():
    return json.loads((ROOT / "looks" / "presets.json").read_text(encoding="utf-8"))


def test_preset_file_is_well_formed():
    data = _preset_data()
    assert data["schema"] == 1
    ids = [p["id"] for p in data["presets"]]
    assert len(ids) == len(set(ids)), "duplicate preset ids"
    assert ids[0] == "none", "'none' must be first so it is the default"
    for p in data["presets"]:
        assert p["name"] and p["description"]
        assert isinstance(p["params"], dict)


def test_every_preset_produces_a_visible_change():
    g = torch.Generator().manual_seed(6)
    img = torch.rand(1, 32, 48, 3, generator=g) * 0.7 + 0.15
    for p in _preset_data()["presets"]:
        if p["id"] == "none":
            continue
        params = p["params"]
        ops = [
            {"type": "tone", "params": {k: params.get(k, 0.0) for k in ("exposure", "contrast", "highlights", "shadows", "whites", "blacks")}},
            {"type": "colour", "params": {"warmth": params.get("warmth", 0.0), "tint": params.get("tint", 0.0),
                                          "vibrance": params.get("vibrance", 0.0), "saturation": params.get("saturation", 1.0)}},
            {"type": "hsl", "params": {"bands": params.get("hsl", {})}},
        ]
        out = Lattice.from_fn(build_sample_fn(ops), DEFAULT_SIZE).apply(img)
        assert float((out - img).abs().max()) > 2.0 / 255.0, f"{p['id']} is a no-op"


def test_every_preset_stays_in_range():
    g = torch.Generator().manual_seed(6)
    img = torch.rand(1, 32, 48, 3, generator=g)
    for p in _preset_data()["presets"]:
        params = p["params"]
        ops = [
            {"type": "tone", "params": {k: params.get(k, 0.0) for k in ("exposure", "contrast", "highlights", "shadows", "whites", "blacks")}},
            {"type": "colour", "params": {"warmth": params.get("warmth", 0.0), "tint": params.get("tint", 0.0),
                                          "vibrance": params.get("vibrance", 0.0), "saturation": params.get("saturation", 1.0)}},
        ]
        out = Lattice.from_fn(build_sample_fn(ops), DEFAULT_SIZE).apply(img)
        assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0, p["id"]


# -- lattice integration -----------------------------------------------------


def _bake_cost(ops: list, size: int) -> float:
    fn = build_sample_fn(ops)
    lat = Lattice.from_fn(fn, size)
    g = torch.Generator().manual_seed(11)
    pts = torch.rand(20000, 3, generator=g)
    return float((lat.apply_points(pts).clamp(0, 1) - fn(pts).to(torch.float32).clamp(0, 1)).abs().max()) * 255


def test_ops_that_stay_in_gamut_bake_cheaply():
    """Pulling saturation, or a gradient map, costs nothing to bake."""
    assert _bake_cost([{"type": "colour", "params": {"saturation": 0.9}}], DEFAULT_SIZE) < 0.5
    assert (
        _bake_cost(
            [{"type": "gradient_map", "params": {"amount": 0.5, "blend": "colour", "stops": _stops()}}],
            DEFAULT_SIZE,
        )
        < 1.0
    )


def test_full_grade_is_why_pw_look_defaults_to_65_cubed():
    """The measurement behind the node's `quality` default.

    A realistic grade stacks several chroma-moving ops, and chroma pushed past
    the sRGB boundary is the one thing a lattice cannot represent (see
    ARCHITECTURE.md). At 33³ that lands around 14 code values in saturated
    areas, which is visible as banding; at 65³ it is around 5, for about 30 ms
    more bake. Hence PW Look defaults to high quality where PW Curves does not.

    Characterisation, not a target. If these move a lot, revisit the default.
    """
    ops = [
        {"type": "tone", "params": {"exposure": 0.2, "contrast": 0.2, "shadows": 0.3, "highlights": -0.25}},
        {"type": "colour", "params": {"warmth": 0.25, "vibrance": 0.3, "saturation": 1.05}},
        {"type": "hsl", "params": {"bands": {"orange": {"sat": 0.2}, "blue": {"sat": 0.25}}}},
    ]
    fast = _bake_cost(ops, DEFAULT_SIZE)
    high = _bake_cost(ops, FINAL_SIZE)
    assert 8.0 < fast < 22.0, f"33³ cost moved: {fast:.2f} codes"
    assert high < fast / 2.0, f"65³ ({high:.2f}) no longer clearly beats 33³ ({fast:.2f})"
    assert high < 8.0, f"65³ cost moved: {high:.2f} codes"


def test_look_ops_are_deterministic():
    c = _colours()
    p = {"exposure": 0.3, "shadows": 0.2}
    assert torch.equal(op_tone(c, p), op_tone(c, p))
