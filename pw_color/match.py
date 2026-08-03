"""Statistical colour matching between two images.

Used by ``PW_MatchSource`` to undo VAE round-trip drift, and later by
``PW_Look``'s reference input. Kept out of the node file because the two use it
with different intent — one is a repair, the other is a creative match — and
they should not drift apart.

Tier one, shipped: per-channel mean and standard deviation matching. Simple,
predictable, and enough for the drift a VAE introduces, which is close to an
affine shift per channel.

Tier two, structured for but not built: a least-squares fit of a tone curve
plus a 3x3 matrix. That handles cross-channel contamination, which mean/std
cannot, at the cost of being able to fail in ways a user cannot predict.
"""

from __future__ import annotations

import torch

from . import colour

__all__ = ["MatchStats", "channel_stats", "match_mean_std", "match_least_squares", "MATCH_SPACES", "MATCH_TIERS"]

#: Reference-matching strategies, simplest first.
#:
#: ``mean_std`` matches each channel's first two moments independently. It
#: cannot express cross-channel behaviour — if the reference turns shadows teal
#: while leaving highlights neutral, a per-channel gain cannot reproduce that.
#:
#: ``least_squares`` fits a tone curve plus a full 3x3 matrix, so it *can*.
#: The cost is that it can fail in ways a user cannot predict: fitted on two
#: images with different content it will happily learn the difference in
#: content rather than the difference in grade.
MATCH_TIERS = ("mean_std", "least_squares")

#: Spaces we can match in. ``oklab`` is the default because a mean/std match on
#: sRGB-encoded values is a match on an arbitrary nonlinearity: the same drift
#: gets a different correction depending on how bright the frame happens to be.
MATCH_SPACES = ("oklab", "linear", "srgb")


class MatchStats:
    """Per-channel mean and standard deviation of a masked region."""

    __slots__ = ("mean", "std", "count")

    def __init__(self, mean: torch.Tensor, std: torch.Tensor, count: float) -> None:
        self.mean = mean
        self.std = std
        self.count = count

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"MatchStats(mean={self.mean.tolist()}, std={self.std.tolist()}, count={self.count})"


def _to_space(img: torch.Tensor, space: str) -> torch.Tensor:
    if space == "srgb":
        return img
    if space == "linear":
        return colour.srgb_to_linear(img)
    if space == "oklab":
        return colour.srgb_to_oklab(img)
    raise ValueError(f"unknown match space {space!r}")


def _from_space(img: torch.Tensor, space: str) -> torch.Tensor:
    if space == "srgb":
        return img
    if space == "linear":
        return colour.linear_to_srgb(img)
    if space == "oklab":
        return colour.oklab_to_srgb(img)
    raise ValueError(f"unknown match space {space!r}")


def channel_stats(img: torch.Tensor, weight: torch.Tensor | None = None) -> MatchStats:
    """Weighted per-channel mean and std of ``[B,H,W,C]``.

    ``weight`` is ``[B,H,W]`` in ``[0,1]``. Soft weights are honoured rather
    than thresholded, so a feathered inpaint mask contributes proportionally
    instead of falling off a cliff at 0.5 — which is what produced a visible
    ring in the first place.

    The variance uses the weighted second moment rather than a two-pass
    algorithm. At float32 with values in ``[0,1]`` and a reasonable pixel count
    that is stable enough, and it lets the whole thing stay one reduction.
    """
    flat = img.reshape(-1, img.shape[-1])
    if weight is None:
        n = float(flat.shape[0])
        mean = flat.mean(dim=0)
        var = flat.var(dim=0, unbiased=False)
    else:
        w = weight.reshape(-1, 1).to(flat.dtype)
        n = float(w.sum().item())
        if n < 1e-6:
            # Fully masked out. Zero std makes the correction a no-op below,
            # which is the right failure: change nothing rather than guess.
            z = torch.zeros(flat.shape[-1], dtype=flat.dtype, device=flat.device)
            return MatchStats(z, z, 0.0)
        mean = (flat * w).sum(dim=0) / n
        var = ((flat - mean) ** 2 * w).sum(dim=0) / n
    return MatchStats(mean, var.clamp(min=0.0).sqrt(), n)


def match_mean_std(
    processed: torch.Tensor,
    original: torch.Tensor,
    mask: torch.Tensor | None = None,
    strength: float = 1.0,
    space: str = "oklab",
    max_gain: float = 4.0,
) -> torch.Tensor:
    """Correct ``processed`` so its statistics match ``original``.

    ``mask`` marks the region that was *regenerated* (ComfyUI's inpaint
    convention: 1 where the model painted). Statistics are gathered from the
    **unmasked** region — the pixels both images share — and the resulting
    correction is applied to the whole frame. That is the entire trick: the
    untouched surround tells us exactly how far the VAE drifted, and the same
    correction then lands on the inpainted area, so the seam disappears.

    ``max_gain`` bounds the std ratio. A flat region — clear sky, a solid
    background — has near-zero std, and an unbounded ratio there turns sensor
    noise into a chroma explosion. Clamping is not elegant but the alternative
    is a node that occasionally destroys an image.
    """
    if processed.shape[-1] < 3 or original.shape[-1] < 3:
        raise ValueError("match_mean_std needs RGB images")

    proc_rgb = processed[..., :3]
    orig_rgb = original[..., :3]
    if proc_rgb.shape != orig_rgb.shape:
        raise ValueError(
            f"original {tuple(orig_rgb.shape)} and processed {tuple(proc_rgb.shape)} must be the same size — "
            "resize before matching, so it is your choice of filter and not ours"
        )

    weight = None
    if mask is not None:
        # Sample from where the model did NOT paint.
        weight = (1.0 - mask.clamp(0.0, 1.0)).to(proc_rgb.dtype)
        if weight.shape[-2:] != proc_rgb.shape[1:3]:
            raise ValueError(f"mask {tuple(weight.shape)} does not match image {tuple(proc_rgb.shape[:3])}")

    p = _to_space(proc_rgb, space)
    o = _to_space(orig_rgb, space)

    ps = channel_stats(p, weight)
    os_ = channel_stats(o, weight)

    # Zero std on either side means there is nothing to learn from — leave the
    # scale alone and correct the offset only.
    gain = torch.where(
        (ps.std > 1e-5) & (os_.std > 1e-5),
        (os_.std / ps.std.clamp(min=1e-5)).clamp(1.0 / max_gain, max_gain),
        torch.ones_like(ps.std),
    )
    corrected = (p - ps.mean) * gain + os_.mean
    out = _from_space(corrected, space)

    s = float(strength)
    if s < 1.0:
        out = torch.lerp(proc_rgb, out, s)
    out = out.clamp(0.0, 1.0)

    if processed.shape[-1] == 4:
        return torch.cat((out, processed[..., 3:]), dim=-1)
    return out


def _sorted_quantiles(values: torch.Tensor, weights: torch.Tensor, n: int) -> torch.Tensor:
    """``n`` evenly spaced weighted quantiles of a 1-D tensor."""
    order = torch.argsort(values)
    v, w = values[order], weights[order]
    cum = torch.cumsum(w, dim=0)
    total = cum[-1].clamp(min=1e-9)
    targets = torch.linspace(0.0, 1.0, n, dtype=values.dtype, device=values.device) * total
    idx = torch.searchsorted(cum.contiguous(), targets.contiguous()).clamp(0, v.numel() - 1)
    return v[idx]


def match_least_squares(
    processed: torch.Tensor,
    reference: torch.Tensor,
    mask: torch.Tensor | None = None,
    strength: float = 1.0,
    matrix_strength: float = 1.0,
    max_gain: float = 4.0,
) -> torch.Tensor:
    """Tier two: a tone curve plus a 3x3 matrix, fitted by least squares.

    Two stages, in this order for a reason:

    1. **Tone.** Match the luminance distribution by histogram-matching a
       monotone set of quantiles. This absorbs the overall contrast difference
       so that the matrix does not have to express it as a scale, which it
       cannot do without also shifting hue.
    2. **Colour.** Solve for the 3x3 that best maps the tone-matched image onto
       the reference in OKLab. A full matrix is what lets this express
       cross-channel behaviour — teal shadows against neutral highlights — that
       per-channel mean/std matching provably cannot.

    Fitted on *statistics*, not on pixel correspondence: the two images do not
    need to be the same scene, and are usually not. The matrix is solved with a
    small ridge term because two images with a narrow palette give a nearly
    singular system, and an unregularised solve there produces a matrix that
    looks fine on the fit and explodes on anything else.

    ``mask`` selects which pixels of ``processed`` the fit learns from — white
    excludes, matching the inpaint convention used by :func:`match_mean_std`.
    The resulting correction is applied to the whole frame regardless. Fitting
    on a region and applying everywhere only improves everything if the region
    was representative, and judging that is the user's call.
    """
    if processed.shape[-1] < 3 or reference.shape[-1] < 3:
        raise ValueError("match_least_squares needs RGB images")

    proc_rgb = processed[..., :3]
    dtype = proc_rgb.dtype
    p = proc_rgb.reshape(-1, 3).to(torch.float32)
    r = reference[..., :3].reshape(-1, 3).to(torch.float32)

    if mask is not None:
        w = (1.0 - mask.clamp(0.0, 1.0)).to(torch.float32).reshape(-1)
        if w.numel() != p.shape[0]:
            raise ValueError(f"mask has {w.numel()} pixels but the image has {p.shape[0]}")
    else:
        w = torch.ones(p.shape[0], dtype=torch.float32)

    # A mask is only meaningful across two images when they are the same scene,
    # and matching shapes is the only signal available for that. When they
    # match, weight both sides — otherwise the fit maps a masked region of one
    # image onto the *whole* of the other, which is worse than not masking.
    # When they differ the reference is a separate photograph and its whole
    # distribution is the thing being matched to.
    rw = w if r.shape[0] == p.shape[0] else torch.ones(r.shape[0], dtype=torch.float32)

    if float(w.sum()) < 8.0 or float(rw.sum()) < 8.0:
        return processed  # nothing to learn from

    # -- 1. tone ---------------------------------------------------------
    n = 64
    p_lin, r_lin = colour.srgb_to_linear(p), colour.srgb_to_linear(r)
    p_lum, r_lum = colour.luma_bt709(p_lin), colour.luma_bt709(r_lin)
    src = _sorted_quantiles(p_lum, w, n)
    dst = _sorted_quantiles(r_lum, rw, n)

    # Force monotonicity: quantiles are sorted, so this only guards against
    # ties producing a flat-then-backward step after interpolation.
    dst = torch.cummax(dst, dim=0).values

    idx = torch.searchsorted(src.contiguous(), p_lum.contiguous().clamp(src[0], src[-1])).clamp(1, n - 1)
    lo, hi = src[idx - 1], src[idx]
    t = ((p_lum - lo) / (hi - lo).clamp(min=1e-9)).clamp(0.0, 1.0)
    mapped_lum = torch.lerp(dst[idx - 1], dst[idx], t)

    gain = (mapped_lum / p_lum.clamp(min=1e-5)).clamp(1.0 / max_gain, max_gain)
    toned = colour.linear_to_srgb(p_lin * gain.unsqueeze(-1))

    # -- 2. colour -------------------------------------------------------
    if matrix_strength > 0.0:
        a = colour.srgb_to_oklab(toned)
        b = colour.srgb_to_oklab(r)
        sw, rwv = w.unsqueeze(-1), rw.unsqueeze(-1)
        mass = w.sum().clamp(min=1e-9)
        rmass = rw.sum().clamp(min=1e-9)
        a_mean = (a * sw).sum(0) / mass
        b_mean = (b * rwv).sum(0) / rmass
        ac, bc = a - a_mean, b - b_mean

        # Match the two *distributions*, via their covariances. Emphatically
        # not a cross-covariance between the two images: that would pair pixel
        # i of one with pixel i of the other, which is only meaningful if they
        # are the same scene. A reference is usually a different photograph
        # entirely, so the fit has to come from the shape of each distribution
        # on its own.
        #
        # Whitening the source and re-colouring it with the reference's
        # Cholesky factor is the standard construction, and it is what makes
        # cross-channel looks reproducible: a split-tone shows up as covariance
        # between OKLab L and b, and a full 3x3 carries that where per-channel
        # statistics cannot.
        caa = (ac * sw).T @ ac / mass
        cbb = (bc * rwv).T @ bc / rmass
        eye = torch.eye(3, dtype=caa.dtype, device=caa.device)
        ridge_a = eye * (float(caa.diagonal().mean()) * 1e-4 + 1e-9)
        ridge_b = eye * (float(cbb.diagonal().mean()) * 1e-4 + 1e-9)
        try:
            ls = torch.linalg.cholesky(caa + ridge_a)
            lt = torch.linalg.cholesky(cbb + ridge_b)
            # Row vectors, so y = ac @ M with M = inv(Ls)^T @ Lt^T. Solving
            # Ls^T X = Lt^T gives exactly that; note this is a left-solve
            # against the transpose, not a right-solve against Ls.
            m = torch.linalg.solve_triangular(ls.transpose(-1, -2), lt.transpose(-1, -2), upper=True, left=True)
        except Exception:
            # A degenerate palette (a flat frame) has no covariance to match.
            # Falling back to the identity leaves the tone match in place,
            # which is strictly better than an exploded matrix.
            m = eye

        # Bound the gain: a near-singular source blown up to a wide reference
        # produces a technically-correct matrix that destroys the image.
        scale = float(torch.linalg.matrix_norm(m, ord=2))
        if scale > max_gain:
            m = m * (max_gain / scale)

        fitted = torch.lerp(a, ac @ m + b_mean, float(matrix_strength))
        toned = colour.oklab_to_srgb(fitted)

    out = toned.reshape(proc_rgb.shape)
    s = float(strength)
    if s < 1.0:
        out = torch.lerp(proc_rgb.to(torch.float32), out, s)
    out = out.clamp(0.0, 1.0).to(dtype)

    if processed.shape[-1] == 4:
        return torch.cat((out, processed[..., 3:]), dim=-1)
    return out
