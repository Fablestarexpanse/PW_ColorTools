"""Tests for PW Grain.

The three acceptance criteria from the brief are the first three tests:
grain is invisible in pure black and pure white, strongest in the midtones, and
identical across two runs with the same seed.
"""

from __future__ import annotations

import pytest
import torch

from pw_color.grain import (
    GRAIN_BLEND_MODES,
    TonalResponse,
    apply_grain,
    dither,
    plate_field,
    procedural_field,
)


def _flat(value: float, h: int = 64, w: int = 64) -> torch.Tensor:
    return torch.full((1, h, w, 3), value)


def _field(h: int = 64, w: int = 64, size: float = 1.4, seed: int = 7, b: int = 1) -> torch.Tensor:
    return procedural_field(b, h, w, size, seed)


def _grain_energy(before: torch.Tensor, after: torch.Tensor) -> float:
    """Standard deviation of what grain added, in 8-bit code values."""
    return float((after - before).std().item() * 255.0)


# -- acceptance --------------------------------------------------------------


def test_grain_is_invisible_in_pure_black_and_pure_white():
    tonal = TonalResponse()
    field = _field()
    for value in (0.0, 1.0):
        img = _flat(value)
        out = apply_grain(img, field, tonal, amount=0.5)
        assert _grain_energy(img, out) < 0.01, f"grain leaked into flat {value}"
        assert torch.allclose(out, img, atol=1e-5)


def test_grain_is_strongest_in_the_midtones():
    tonal = TonalResponse(shadows=0.20, mids=1.00, highlights=0.10)
    field = _field()
    energies = {v: _grain_energy(_flat(v), apply_grain(_flat(v), field, tonal, amount=0.2)) for v in (0.05, 0.2, 0.5, 0.85, 0.98)}
    assert energies[0.5] > energies[0.2] > energies[0.05], energies
    assert energies[0.5] > energies[0.85] > energies[0.98], energies


def test_same_seed_gives_identical_output():
    img = _flat(0.5)
    tonal = TonalResponse()
    a = apply_grain(img, procedural_field(1, 64, 64, 1.4, 1234), tonal, amount=0.1)
    b = apply_grain(img, procedural_field(1, 64, 64, 1.4, 1234), tonal, amount=0.1)
    assert torch.equal(a, b)


def test_different_seeds_give_different_grain():
    img = _flat(0.5)
    tonal = TonalResponse()
    a = apply_grain(img, procedural_field(1, 64, 64, 1.4, 1), tonal, amount=0.1)
    b = apply_grain(img, procedural_field(1, 64, 64, 1.4, 2), tonal, amount=0.1)
    assert not torch.allclose(a, b, atol=1e-4)


# -- absolute size -----------------------------------------------------------


def _autocorr_at_lag(field: torch.Tensor, lag: int) -> float:
    """Normalised autocorrelation along x — a proxy for grain size in pixels."""
    x = field[0, ..., 0]
    x = x - x.mean()
    a = x[:, :-lag]
    b = x[:, lag:]
    return float(((a * b).mean() / x.var().clamp(min=1e-9)).item())


def test_grain_size_is_absolute_not_relative():
    """The headline guarantee: 1.4px grain is 1.4px at any resolution.

    Measured as spatial autocorrelation, which is what "grain size" physically
    is. If the field were generated at a fixed texture size and stretched, the
    correlation length would scale with the frame and this would fail.
    """
    small = procedural_field(1, 128, 128, 2.0, 5)
    large = procedural_field(1, 512, 512, 2.0, 5)
    for lag in (1, 2, 3, 4):
        a, b = _autocorr_at_lag(small, lag), _autocorr_at_lag(large, lag)
        assert abs(a - b) < 0.06, f"lag {lag}: {a:.3f} vs {b:.3f} — grain scaled with resolution"


def test_larger_size_gives_longer_correlation():
    fine = procedural_field(1, 256, 256, 1.0, 5)
    coarse = procedural_field(1, 256, 256, 4.0, 5)
    assert _autocorr_at_lag(coarse, 3) > _autocorr_at_lag(fine, 3) + 0.2


def test_field_has_unit_variance_at_every_size():
    """Otherwise coarser grain is quieter grain and `amount` stops being stable."""
    for size in (0.5, 1.0, 2.0, 4.0, 8.0):
        f = procedural_field(1, 256, 256, size, 3)
        assert abs(float(f.std().item()) - 1.0) < 0.05, size


def test_amount_is_stable_across_grain_size():
    img = _flat(0.5, 256, 256)
    tonal = TonalResponse()
    energies = [
        _grain_energy(img, apply_grain(img, procedural_field(1, 256, 256, s, 3), tonal, amount=0.1))
        for s in (1.0, 2.0, 4.0)
    ]
    assert max(energies) / min(energies) < 1.25, energies


def test_mismatched_field_size_is_an_explicit_error():
    """Silently resizing here would break the absolute-size guarantee, so the
    only safe response is to refuse."""
    with pytest.raises(ValueError, match="absolute"):
        apply_grain(_flat(0.5, 64, 64), _field(256, 256), TonalResponse())


# -- batch and seeding -------------------------------------------------------


def test_batch_is_identically_grained_by_default():
    f = procedural_field(3, 32, 32, 1.4, 9, vary_per_frame=False)
    assert torch.equal(f[0], f[1]) and torch.equal(f[1], f[2])


def test_vary_per_frame_decorrelates_the_batch():
    f = procedural_field(3, 32, 32, 1.4, 9, vary_per_frame=True)
    assert not torch.allclose(f[0], f[1], atol=1e-4)
    assert not torch.allclose(f[1], f[2], atol=1e-4)


def test_vary_per_frame_is_still_deterministic():
    a = procedural_field(3, 32, 32, 1.4, 9, vary_per_frame=True)
    b = procedural_field(3, 32, 32, 1.4, 9, vary_per_frame=True)
    assert torch.equal(a, b)


# -- plates ------------------------------------------------------------------


def _plate(seed: int = 11, h: int = 96, w: int = 96, offset: float = 0.7) -> torch.Tensor:
    """A plate with a deliberately wrong exposure, to prove mean-centring."""
    g = torch.Generator().manual_seed(seed)
    return (torch.rand(1, h, w, 3, generator=g) * 0.2 + offset).clamp(0, 1)


def test_plate_exposure_does_not_shift_the_image():
    """A raw plate carries its own exposure; a mean-centred one carries none.

    This is the test that would catch someone 'simplifying' the mean subtraction
    away — the image would get brighter and it would look like a grain amount
    problem rather than a plate problem.
    """
    img = _flat(0.5)
    tonal = TonalResponse()
    for offset in (0.1, 0.5, 0.9):
        field = plate_field(_plate(offset=offset), 1, 64, 64, seed=3)
        out = apply_grain(img, field, tonal, amount=0.1)
        shift = float((out.mean() - img.mean()).item() * 255.0)
        assert abs(shift) < 0.6, f"plate at exposure {offset} shifted the image by {shift:.2f} codes"


def test_plate_field_is_mean_centred_and_unit_variance():
    f = plate_field(_plate(), 1, 64, 64, seed=3)
    assert abs(float(f.mean().item())) < 0.05
    assert abs(float(f.std().item()) - 1.0) < 0.05


def test_plate_crop_offset_varies_with_seed():
    a = plate_field(_plate(), 1, 48, 48, seed=1)
    b = plate_field(_plate(), 1, 48, 48, seed=2)
    assert not torch.allclose(a, b, atol=1e-4), "same crop for different seeds"


def test_plate_smaller_than_frame_tiles_without_a_hard_seam():
    """Mirror tiling: the derivative across a tile boundary must not spike."""
    f = plate_field(_plate(h=32, w=32), 1, 128, 128, seed=0)
    dx = (f[0, :, 1:, 0] - f[0, :, :-1, 0]).abs()
    assert float(dx.max().item()) < float(dx.mean().item()) * 25.0


def test_plate_batch_maps_to_output_batch_index():
    g = torch.Generator().manual_seed(2)
    plates = torch.rand(3, 64, 64, 3, generator=g)
    f = plate_field(plates, 3, 32, 32, seed=5)
    assert f.shape[0] == 3
    assert not torch.allclose(f[0], f[1], atol=1e-4)


def test_plate_is_cropped_not_resized():
    """Resizing would scale grain with the image and break absolute size."""
    plate = _plate(h=256, w=256)
    small = plate_field(plate, 1, 64, 64, seed=4)
    large = plate_field(plate, 1, 200, 200, seed=4)
    for lag in (1, 2):
        assert abs(_autocorr_at_lag(small, lag) - _autocorr_at_lag(large, lag)) < 0.12


def test_plate_rejects_a_bad_shape():
    with pytest.raises(ValueError, match="grain plate"):
        plate_field(torch.rand(64, 64, 3), 1, 32, 32, seed=0)


# -- blending and channels ---------------------------------------------------


@pytest.mark.parametrize("mode", GRAIN_BLEND_MODES)
def test_every_blend_mode_is_in_range_and_does_something(mode: str):
    img = _flat(0.5)
    out = apply_grain(img, _field(), TonalResponse(), amount=0.15, blend=mode)
    assert out.min() >= 0.0 and out.max() <= 1.0
    assert _grain_energy(img, out) > 0.1, mode


@pytest.mark.parametrize("mode", GRAIN_BLEND_MODES)
def test_every_blend_mode_is_neutral_at_zero_grain(mode: str):
    """No mode may shift exposure on its own.

    This is what caught screen being fed a 0.5-centred layer: screen's neutral
    is black, not grey, so a grey layer lifted mid grey to 0.75.
    """
    for value in (0.0, 0.25, 0.5, 0.75, 1.0):
        img = _flat(value)
        out = apply_grain(img, torch.zeros(1, 64, 64, 3), TonalResponse(), amount=0.2, blend=mode)
        assert torch.allclose(out, img, atol=1e-5), f"{mode} at {value}"


@pytest.mark.parametrize("mode", GRAIN_BLEND_MODES)
def test_no_blend_mode_shifts_average_exposure_much(mode: str):
    """Grain is texture, not an exposure control.

    Screen is the loose one by construction — it only lightens — so it gets more
    latitude, but it still must not read as an exposure change.
    """
    img = _flat(0.5)
    out = apply_grain(img, _field(), TonalResponse(), amount=0.1, blend=mode)
    shift = abs(float((out.mean() - img.mean()).item()) * 255.0)
    assert shift < (6.0 if mode == "screen" else 1.0), f"{mode} shifted exposure by {shift:.2f} codes"


def test_unknown_blend_mode_is_rejected():
    with pytest.raises(ValueError, match="blend mode"):
        apply_grain(_flat(0.5), _field(), TonalResponse(), blend="divide")


def test_opacity_zero_is_a_no_op():
    img = _flat(0.5)
    out = apply_grain(img, _field(), TonalResponse(), amount=0.2, opacity=0.0)
    assert torch.allclose(out, img, atol=1e-6)


def test_blue_channel_defaults_hotter():
    img = _flat(0.5)
    out = apply_grain(img, _field(), TonalResponse(), amount=0.1)
    per_channel = [(out[..., i] - img[..., i]).std().item() for i in range(3)]
    assert per_channel[2] > per_channel[0] * 1.05, per_channel


def test_channel_amount_zero_leaves_that_channel_clean():
    img = _flat(0.5)
    out = apply_grain(img, _field(), TonalResponse(), amount=0.2, channel_amounts=(1.0, 0.0, 1.0))
    assert (out[..., 1] - img[..., 1]).abs().max().item() < 1e-6


def test_alpha_passes_through():
    img = torch.cat((_flat(0.5), torch.rand(1, 64, 64, 1)), dim=-1)
    out = apply_grain(img, _field(), TonalResponse(), amount=0.1)
    assert torch.equal(out[..., 3:], img[..., 3:])


def test_tonal_weights_are_a_partition_of_unity():
    """Equal sliders must give genuinely uniform grain, so they read as a
    balance rather than as three interacting gains."""
    tonal = TonalResponse(1.0, 1.0, 1.0)
    ramp = torch.linspace(0.15, 0.85, 64).view(1, 1, 64, 1).expand(1, 8, 64, 3).contiguous()
    w = tonal.weight(ramp)
    assert float((w - 1.0).abs().max().item()) < 1e-4


# -- dither ------------------------------------------------------------------


def test_dither_is_sub_lsb():
    img = _flat(0.5)
    out = dither(img, seed=1)
    delta = (out - img).abs().max().item() * 255.0
    assert 0.0 < delta <= 1.05, f"dither peak {delta:.3f} code values"


def test_dither_is_deterministic():
    img = _flat(0.5)
    assert torch.equal(dither(img, seed=1), dither(img, seed=1))


def test_dither_zero_strength_is_a_no_op():
    img = _flat(0.5)
    assert torch.equal(dither(img, seed=1, strength=0.0), img)


def test_dither_removes_banding_from_a_soft_gradient():
    """The reason it is always on.

    A gradient so gentle that it crosses fewer 8-bit levels than it has pixels
    quantises into visible bands. Dither trades those bands for noise, which
    shows up as *more distinct levels* in the quantised output.
    """
    ramp = torch.linspace(0.40, 0.44, 256).view(1, 1, 256, 1).expand(1, 64, 256, 3).contiguous()
    q = lambda x: (x.clamp(0, 1) * 255 + 0.5).floor()

    plain = q(ramp)
    dithered = q(dither(ramp, seed=2))

    # Count transitions along the gradient: banding is few, wide steps.
    plain_edges = int((plain[0, 0, 1:, 0] != plain[0, 0, :-1, 0]).sum().item())
    dithered_edges = int((dithered[0, 0, 1:, 0] != dithered[0, 0, :-1, 0]).sum().item())
    assert dithered_edges > plain_edges * 4, (plain_edges, dithered_edges)

    # And it must not have moved the picture.
    assert abs(float((dither(ramp, seed=2) - ramp).mean().item())) < 1e-4


def test_dither_alpha_passes_through():
    img = torch.cat((_flat(0.5), torch.rand(1, 64, 64, 1)), dim=-1)
    out = dither(img, seed=1)
    assert torch.equal(out[..., 3:], img[..., 3:])


# -- cross-language parity ---------------------------------------------------


def test_tonal_response_matches_the_browser():
    """The grain node draws its response curve in TS and applies it in torch.

    That is a second place the same maths lives in two languages, so it gets the
    same treatment as the lattice. If this fails, the curve a user is reading is
    not the curve being applied to their pixels.
    """
    import json
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH")

    harness = Path(__file__).resolve().parents[1] / "web" / "tools" / "tonal.ts"
    ts = torch.linspace(0.0, 1.0, 257)
    shadows, mids, highlights = 0.2, 1.0, 0.1

    proc = subprocess.run(
        [node, "--experimental-strip-types", "--no-warnings", str(harness)],
        input=json.dumps({"t": ts.tolist(), "shadows": shadows, "mids": mids, "highlights": highlights}),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(f"tonal harness failed:\n{proc.stderr}")
    js = torch.tensor(json.loads(proc.stdout)["weights"], dtype=torch.float32)

    # Drive the Python side at the same perceptual positions. TonalResponse
    # takes an image and derives t internally, so feed it the sRGB values whose
    # linear luminance lands on exactly those t.
    lum = ts.clamp(0, 1).pow(2.2)
    from pw_color.colour import linear_to_srgb

    px = linear_to_srgb(lum).view(1, 1, -1, 1).expand(1, 1, ts.numel(), 3).contiguous()
    py = TonalResponse(shadows, mids, highlights).weight(px).reshape(-1)

    err = float((py - js).abs().max().item())
    assert err < 1e-5, f"tonal response differs by {err:.3e} between TS and torch"
