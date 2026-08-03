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

__all__ = ["MatchStats", "channel_stats", "match_mean_std", "MATCH_SPACES"]

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
