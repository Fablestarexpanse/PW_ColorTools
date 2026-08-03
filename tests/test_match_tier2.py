"""Tier-two reference matching: tone curve plus 3x3 matrix.

The property that justifies its existence is the one tier one provably cannot
have: reproducing a *cross-channel* look, where what happens to blue depends on
how bright the pixel is. A per-channel gain and offset cannot express that; a
matrix after a tone curve can.
"""

from __future__ import annotations

import pytest
import torch

from pw_color import colour
from pw_color.match import MATCH_TIERS, match_least_squares, match_mean_std


def _scene(seed: int = 1, h: int = 64, w: int = 96) -> torch.Tensor:
    """A structured image with a full tonal range and several hues."""
    g = torch.Generator().manual_seed(seed)
    y = torch.linspace(0, 1, h).view(h, 1)
    x = torch.linspace(0, 1, w).view(1, w)
    base = torch.stack(
        (
            0.15 + 0.8 * x * (0.6 + 0.4 * torch.cos(y * 3.0)),
            0.20 + 0.7 * (x * 0.6 + y * 0.4),
            0.25 + 0.6 * y * (0.5 + 0.5 * torch.sin(x * 4.0)),
        ),
        dim=-1,
    )
    return (base + 0.02 * torch.rand(h, w, 3, generator=g)).clamp(0, 1).unsqueeze(0)


def _split_tone(img: torch.Tensor, shadow_teal: float = 0.09, highlight_warm: float = 0.07) -> torch.Tensor:
    """A cross-channel grade: teal in the shadows, warm in the highlights.

    Deliberately impossible for per-channel mean/std to reproduce — the sign of
    the blue shift depends on lightness.
    """
    lab = colour.srgb_to_oklab(img)
    l = lab[..., 0]
    shadow = (1.0 - l).clamp(0, 1) ** 2
    highlight = l.clamp(0, 1) ** 2
    b = lab[..., 2] - shadow * shadow_teal + highlight * highlight_warm
    a = lab[..., 1] - shadow * shadow_teal * 0.4
    return colour.oklab_to_srgb(torch.stack((l, a, b), dim=-1)).clamp(0, 1)


def _err(a: torch.Tensor, b: torch.Tensor) -> float:
    """Mean OKLab distance, in units a human reads as 'how different'."""
    return float((colour.srgb_to_oklab(a) - colour.srgb_to_oklab(b)).norm(dim=-1).mean())


# -- the property that justifies tier two ------------------------------------


def test_least_squares_beats_mean_std_on_a_cross_channel_look():
    """The headline claim, measured rather than asserted."""
    src = _scene(1)
    ref = _split_tone(_scene(1))

    before = _err(src, ref)
    tier1 = _err(match_mean_std(src, ref), ref)
    tier2 = _err(match_least_squares(src, ref), ref)

    assert tier1 < before, "tier one should still help"
    assert tier2 < tier1, f"tier two ({tier2:.4f}) should beat tier one ({tier1:.4f})"
    assert tier2 < before * 0.5


def test_least_squares_matches_a_pure_tone_difference():
    src = _scene(2)
    ref = colour.linear_to_srgb((colour.srgb_to_linear(src) * 1.6).clamp(0, 1))
    assert _err(match_least_squares(src, ref), ref) < _err(src, ref) * 0.4


def test_least_squares_matches_a_pure_colour_cast():
    src = _scene(3)
    lab = colour.srgb_to_oklab(src)
    ref = colour.oklab_to_srgb(torch.stack((lab[..., 0], lab[..., 1] + 0.05, lab[..., 2] - 0.06), -1)).clamp(0, 1)
    assert _err(match_least_squares(src, ref), ref) < _err(src, ref) * 0.4


# -- behaviour ---------------------------------------------------------------


def test_matching_an_image_to_itself_changes_nothing():
    src = _scene()
    out = match_least_squares(src, src)
    assert float((out - src).abs().max()) * 255 < 2.0


def test_strength_zero_is_a_no_op():
    src, ref = _scene(1), _split_tone(_scene(2))
    assert torch.allclose(match_least_squares(src, ref, strength=0.0), src, atol=1e-5)


def test_strength_is_monotone():
    src, ref = _scene(1), _split_tone(_scene(1))
    errs = [_err(match_least_squares(src, ref, strength=s), ref) for s in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert errs == sorted(errs, reverse=True), errs


def test_matrix_strength_zero_leaves_only_the_tone_match():
    """Disabling the matrix must still apply the tone curve, or the control is
    an all-or-nothing switch pretending to be a blend.

    The reference here differs in *both* tone and colour: a split-tone alone
    preserves lightness exactly, so there would be no tone difference for the
    curve to find and the test would measure nothing.
    """
    src = _scene(1)
    brighter = colour.linear_to_srgb((colour.srgb_to_linear(_scene(1)) * 1.5).clamp(0, 1))
    ref = _split_tone(brighter)

    tone_only = match_least_squares(src, ref, matrix_strength=0.0)
    full = match_least_squares(src, ref, matrix_strength=1.0)
    assert _err(tone_only, ref) < _err(src, ref), "the tone curve alone should help"
    assert _err(full, ref) < _err(tone_only, ref), "the matrix should add to it"


def test_matrix_is_fitted_on_distributions_not_pixel_pairs():
    """A reference is usually a different photograph. Pairing pixel i with
    pixel i would only work when the two images are the same scene, so the fit
    must come from each distribution's own shape.

    Shuffling the reference's pixels destroys any correspondence while leaving
    its distribution identical, so the result must not change.
    """
    src = _scene(1)
    ref = _split_tone(_scene(1))
    flat = ref.reshape(-1, 3)
    perm = torch.randperm(flat.shape[0], generator=torch.Generator().manual_seed(0))
    shuffled = flat[perm].reshape(ref.shape)

    a = match_least_squares(src, ref)
    b = match_least_squares(src, shuffled)
    assert float((a - b).abs().max()) * 255 < 1.0, "the fit depends on pixel order"


def test_output_stays_in_range_and_finite():
    for seed in range(4):
        out = match_least_squares(_scene(seed), _split_tone(_scene(seed + 10)))
        assert torch.isfinite(out).all()
        assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


def test_survives_degenerate_inputs():
    """A narrow palette gives a nearly singular system; the ridge term is what
    stops the solve producing a matrix that explodes."""
    flat = torch.full((1, 16, 16, 3), 0.5)
    for src, ref in (
        (flat, _scene()[:, :16, :16]),
        (_scene()[:, :16, :16], flat),
        (flat, flat),
        (torch.zeros(1, 8, 8, 3), torch.ones(1, 8, 8, 3)),
    ):
        out = match_least_squares(src, ref)
        assert torch.isfinite(out).all()
        assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


def test_works_with_differently_sized_reference():
    """Fitted on statistics, not on pixel correspondence, so the reference does
    not have to be the same size or the same scene."""
    out = match_least_squares(_scene(1, 64, 96), _split_tone(_scene(2, 32, 48)))
    assert torch.isfinite(out).all()


def test_mask_restricts_which_pixels_the_fit_learns_from():
    """The mask selects the sample, and the correction still applies to the
    whole frame.

    So the region that was sampled must improve. The rest need not: fitting on
    a region and applying everywhere is only an improvement everywhere if the
    region was representative, which is the user's call to make and not
    something this function can decide for them.
    """
    src, ref = _scene(1), _split_tone(_scene(1))
    mask = torch.zeros(1, 64, 96)
    mask[:, :, 48:] = 1.0  # exclude the right half, so the left half is sampled
    out = match_least_squares(src, ref, mask=mask)
    assert torch.isfinite(out).all()

    left = slice(None), slice(None), slice(0, 48)
    assert _err(out[left], ref[left]) < _err(src[left], ref[left])


def test_mask_and_no_mask_differ():
    src, ref = _scene(1), _split_tone(_scene(1))
    mask = torch.zeros(1, 64, 96)
    mask[:, :, 48:] = 1.0
    assert not torch.allclose(match_least_squares(src, ref, mask=mask), match_least_squares(src, ref), atol=1e-4)


def test_mask_size_mismatch_is_explicit():
    with pytest.raises(ValueError, match="pixels"):
        match_least_squares(_scene(), _scene(2), mask=torch.ones(1, 8, 8))


def test_alpha_passes_through():
    src = torch.cat((_scene(), torch.rand(1, 64, 96, 1)), dim=-1)
    out = match_least_squares(src, _scene(2))
    assert torch.equal(out[..., 3:], src[..., 3:])


def test_is_deterministic():
    src, ref = _scene(1), _split_tone(_scene(2))
    assert torch.equal(match_least_squares(src, ref), match_least_squares(src, ref))


def test_rejects_non_rgb():
    with pytest.raises(ValueError, match="RGB"):
        match_least_squares(torch.rand(1, 8, 8, 1), torch.rand(1, 8, 8, 3))


def test_tier_names_are_stable():
    """These strings are a node's combo options and land in saved workflows."""
    assert MATCH_TIERS == ("mean_std", "least_squares")
