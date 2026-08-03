"""Tests for PW Match Source.

The acceptance criterion from the brief — "an inpaint round-trip through a VAE
shows no visible seam at strength 1.0" — is modelled here as a synthetic drift
applied to a synthetic image, because a real VAE in the test suite would make it
slow, non-deterministic and dependent on which checkpoint is installed.

The synthetic drift is deliberately the *shape* of real VAE drift: a per-channel
gain and offset, plus a mild nonlinearity, all small. If the correction handles
that, it handles the real thing; the parts it cannot handle (spatially varying
drift) it could not handle with a real VAE either.
"""

from __future__ import annotations

import pytest
import torch

from pw_color import colour
from pw_color.match import channel_stats, match_mean_std


def _image(seed: int = 3, h: int = 96, w: int = 128) -> torch.Tensor:
    """A smooth, structured test image — not noise. Noise has flat statistics
    everywhere, which would let a broken implementation pass."""
    g = torch.Generator().manual_seed(seed)
    y = torch.linspace(0, 1, h).view(h, 1)
    x = torch.linspace(0, 1, w).view(1, w)
    base = torch.stack(
        (
            0.5 + 0.35 * torch.sin(x * 7.0) * torch.cos(y * 4.0),
            0.45 + 0.30 * torch.cos(x * 3.0 + y * 5.0),
            0.55 + 0.25 * torch.sin(y * 6.0 + x * 2.0),
        ),
        dim=-1,
    )
    return (base + 0.02 * torch.rand(h, w, 3, generator=g)).clamp(0, 1).unsqueeze(0)


def _vae_drift(img: torch.Tensor) -> torch.Tensor:
    """The shape of real VAE round-trip drift: per-channel gain and offset in
    linear light, plus a slight S to the response."""
    lin = colour.srgb_to_linear(img)
    gain = torch.tensor([1.045, 0.988, 0.962])
    offset = torch.tensor([0.006, -0.003, 0.011])
    lin = lin * gain + offset
    lin = lin + 0.02 * (lin - 0.18) * (1.0 - lin.clamp(0, 1))
    return colour.linear_to_srgb(lin).clamp(0, 1)


def _mask(h: int = 96, w: int = 128, feather: bool = False) -> torch.Tensor:
    """A centred rectangle: 1 where the model painted."""
    m = torch.zeros(1, h, w)
    m[:, h // 4 : 3 * h // 4, w // 4 : 3 * w // 4] = 1.0
    if feather:
        k = torch.ones(1, 1, 9, 9) / 81.0
        m = torch.nn.functional.conv2d(m.unsqueeze(1), k, padding=4).squeeze(1)
    return m


def _seam_step(img: torch.Tensor, mask: torch.Tensor) -> float:
    """Mean absolute difference across the mask boundary, in 8-bit codes.

    Measured as the gap between the strip just inside the mask and the strip
    just outside it. That is precisely what a viewer perceives as a seam: not
    absolute error, but a *step* at the edge.
    """
    m = mask[0]
    inner = (m > 0.5)
    # One-pixel bands either side of the boundary.
    pad = torch.nn.functional.max_pool2d(inner.float().unsqueeze(0).unsqueeze(0), 3, 1, 1)[0, 0] > 0.5
    outer_band = pad & ~inner
    eroded = -torch.nn.functional.max_pool2d(-inner.float().unsqueeze(0).unsqueeze(0), 3, 1, 1)[0, 0] > 0.5
    inner_band = inner & ~eroded
    a = img[0][inner_band].mean(dim=0)
    b = img[0][outer_band].mean(dim=0)
    return float((a - b).abs().max().item() * 255.0)


# -- acceptance --------------------------------------------------------------


def test_inpaint_round_trip_has_no_visible_seam():
    """The Phase 1 acceptance criterion.

    Build the composite an inpaint workflow actually produces: original pixels
    outside the mask, VAE-drifted pixels inside it. Uncorrected there is a step
    at the boundary; after matching there must not be.

    Measured against a floor, because a test image with content in it has a
    genuine gradient across the mask boundary even with no drift at all. The
    number that matters is the *excess* step over that floor — that is the part
    a viewer reads as a seam rather than as picture.
    """
    original = _image()
    mask = _mask()
    decoded = _vae_drift(original)
    m = mask.unsqueeze(-1)

    floor = _seam_step(original, mask)
    before = _seam_step(original * (1 - m) + decoded * m, mask) - floor
    assert before > 2.0, f"test setup produced no seam to fix ({before:.2f} codes over floor)"

    corrected = match_mean_std(decoded, original, mask=mask, strength=1.0)
    after = _seam_step(original * (1 - m) + corrected * m, mask) - floor

    assert after < 0.5, f"seam is {after:.2f} codes over floor after correction (was {before:.2f})"


def test_oklab_is_the_best_default_space():
    """Pins the default. oklab beat linear and srgb on seam removal when this
    was measured; if that ever inverts, the default should change with it."""
    original = _image()
    mask = _mask()
    decoded = _vae_drift(original)
    m = mask.unsqueeze(-1)
    floor = _seam_step(original, mask)

    def excess(space: str) -> float:
        c = match_mean_std(decoded, original, mask=mask, space=space)
        return _seam_step(original * (1 - m) + c * m, mask) - floor

    assert excess("oklab") <= min(excess("linear"), excess("srgb"))


def test_correction_is_applied_to_the_masked_region_too():
    """The whole point: the fix lands where it could not be measured."""
    original = _image()
    mask = _mask()
    decoded = _vae_drift(original)
    corrected = match_mean_std(decoded, original, mask=mask, strength=1.0)

    inside = mask[0] > 0.5
    err_before = (decoded[0][inside] - original[0][inside]).abs().mean().item()
    err_after = (corrected[0][inside] - original[0][inside]).abs().mean().item()
    assert err_after < err_before / 2.0, f"masked region: {err_before * 255:.2f} -> {err_after * 255:.2f} codes"


# -- behaviour ---------------------------------------------------------------


def test_identity_when_images_already_match():
    img = _image()
    out = match_mean_std(img, img, mask=_mask())
    assert (out - img).abs().max().item() * 255 < 0.5


def test_strength_zero_is_a_no_op():
    original = _image()
    decoded = _vae_drift(original)
    out = match_mean_std(decoded, original, mask=_mask(), strength=0.0)
    assert torch.allclose(out, decoded, atol=1e-6)


def test_strength_is_monotone():
    original = _image()
    decoded = _vae_drift(original)
    errs = [
        (match_mean_std(decoded, original, mask=_mask(), strength=s) - original).abs().mean().item()
        for s in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    assert errs == sorted(errs, reverse=True), errs


def test_works_without_a_mask():
    original = _image()
    decoded = _vae_drift(original)
    out = match_mean_std(decoded, original, mask=None)
    assert (out - original).abs().mean().item() < (decoded - original).abs().mean().item() / 2.0


def test_feathered_mask_is_honoured_softly():
    """A feathered mask must weight, not threshold — the hard-threshold version
    of this produced a visible ring at the feather edge."""
    original = _image()
    decoded = _vae_drift(original)
    soft = match_mean_std(decoded, original, mask=_mask(feather=True))
    hard = match_mean_std(decoded, original, mask=_mask(feather=False))
    assert not torch.allclose(soft, hard, atol=1e-5)
    assert (soft - original).abs().mean().item() < (decoded - original).abs().mean().item() / 2.0


def test_fully_masked_frame_changes_nothing():
    """No sample region at all. Must decline to guess rather than blow up."""
    original = _image()
    decoded = _vae_drift(original)
    out = match_mean_std(decoded, original, mask=torch.ones(1, 96, 128))
    assert torch.isfinite(out).all()
    assert (out - decoded).abs().max().item() * 255 < 1.0


def test_flat_region_does_not_explode():
    """A clear-sky sample region has near-zero std; an unbounded gain ratio
    there turns noise into a chroma explosion."""
    g = torch.Generator().manual_seed(5)
    original = torch.full((1, 64, 64, 3), 0.5) + 0.0005 * torch.rand(1, 64, 64, 3, generator=g)
    decoded = original * 0.999 + 0.02
    out = match_mean_std(decoded, original, mask=_mask(64, 64), max_gain=4.0)
    assert torch.isfinite(out).all()
    assert (out - out.mean()).abs().max().item() < 0.2, "flat region amplified into noise"


def test_batch_of_processed_against_single_original():
    original = _image()
    decoded = torch.cat([_vae_drift(original), _vae_drift(original) * 0.98], dim=0)
    out = match_mean_std(decoded, original.expand(2, -1, -1, -1), mask=_mask().expand(2, -1, -1))
    assert out.shape == decoded.shape


def test_alpha_passes_through():
    original = _image()
    decoded = _vae_drift(original)
    a = torch.rand(1, 96, 128, 1)
    out = match_mean_std(torch.cat((decoded, a), -1), original, mask=_mask())
    assert torch.equal(out[..., 3:], a)


def test_size_mismatch_is_an_explicit_error():
    with pytest.raises(ValueError, match="same size"):
        match_mean_std(_image(h=64), _image(h=96))


def test_all_spaces_reduce_the_error():
    original = _image()
    decoded = _vae_drift(original)
    base = (decoded - original).abs().mean().item()
    for space in ("oklab", "linear", "srgb"):
        out = match_mean_std(decoded, original, mask=_mask(), space=space)
        assert (out - original).abs().mean().item() < base / 2.0, space


def test_unknown_space_is_rejected():
    with pytest.raises(ValueError, match="match space"):
        match_mean_std(_image(), _image(), space="lab")


def test_channel_stats_weighting():
    img = torch.zeros(1, 4, 4, 3)
    img[:, :2] = 1.0
    w = torch.zeros(1, 4, 4)
    w[:, :2] = 1.0
    s = channel_stats(img, w)
    assert torch.allclose(s.mean, torch.ones(3), atol=1e-6)
    assert torch.allclose(s.std, torch.zeros(3), atol=1e-6)
    assert s.count == 8.0


def test_is_deterministic():
    original = _image()
    decoded = _vae_drift(original)
    a = match_mean_std(decoded, original, mask=_mask())
    b = match_mean_std(decoded, original, mask=_mask())
    assert torch.equal(a, b)
