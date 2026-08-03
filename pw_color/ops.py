"""Per-pixel operations, evaluated on lattice sample points.

Every function here maps ``[M,3]`` sRGB-encoded values to ``[M,3]`` sRGB-encoded
values and is pure. That signature is the whole point: any composition of them
can be baked into a :class:`~pw_color.lattice.Lattice`, which is what makes
preview and render the same pixels and ``.cube`` export free.

Anything that needs a pixel's *neighbours* — grain, halation, vignette,
chromatic aberration, mask blur — cannot live here. Those are render-only and
belong in their own nodes. Keeping the boundary at the function signature makes
it impossible to accidentally put a spatial op in the LUT path.

``web/src/core/ops.ts`` mirrors this file. Parity is enforced by
``tests/test_parity.py``.
"""

from __future__ import annotations

from typing import Any

import torch

from . import colour
from .curve import eval_curve

__all__ = ["apply_op", "build_sample_fn", "LUT_SAFE_OPS"]

#: Ops that can be baked into a lattice. Everything not in here is render-only.
LUT_SAFE_OPS = ("exposure", "contrast", "saturation", "curves", "warmth")


def op_exposure(rgb: torch.Tensor, stops: float) -> torch.Tensor:
    """Exposure in stops, applied in linear light — the only place it means
    anything. Doing this in sRGB is the single most common bug in hobby grade
    tools and it is why their 'brightness' slider washes out saturation."""
    lin = colour.srgb_to_linear(rgb)
    return colour.linear_to_srgb(lin * (2.0 ** float(stops)))


def op_contrast(rgb: torch.Tensor, amount: float, pivot: float = 0.18) -> torch.Tensor:
    """Contrast about a linear pivot (0.18 = middle grey).

    Pivoting on 0.18 rather than 0.5 keeps midtone skin where the user put it;
    a 0.5 pivot in sRGB drags faces darker as contrast goes up.
    """
    lin = colour.srgb_to_linear(rgb)
    k = 1.0 + float(amount)
    out = (lin.clamp(min=1e-6) / pivot).pow(k) * pivot
    return colour.linear_to_srgb(out)


def op_saturation(rgb: torch.Tensor, amount: float) -> torch.Tensor:
    """Saturation as a scale on OKLab chroma, hue and lightness held.

    In OKLab this stays perceptually even across hues, so pushing saturation
    does not send reds orange or blues purple the way an HSV scale does.
    """
    lab = colour.srgb_to_oklab(rgb)
    lch = colour.oklab_to_oklch(lab)
    lch = torch.stack((lch[..., 0], lch[..., 1] * float(amount), lch[..., 2]), dim=-1)
    return colour.oklab_to_srgb(colour.oklch_to_oklab(lch))


def op_warmth(rgb: torch.Tensor, amount: float) -> torch.Tensor:
    """Warm/cool shift along the OKLab b axis (blue-yellow), scaled by lightness.

    Scaling by L keeps the shift out of the deepest shadows, where a flat
    offset reads as a colour cast rather than as warmth.
    """
    lab = colour.srgb_to_oklab(rgb)
    l = lab[..., 0]
    b = lab[..., 2] + float(amount) * 0.1 * l
    return colour.oklab_to_srgb(torch.stack((l, lab[..., 1], b), dim=-1))


def op_curves(rgb: torch.Tensor, params: dict[str, Any]) -> torch.Tensor:
    """Per-channel and luma curves.

    ``preserve_hue`` is the headline: with it on, the luma curve drives OKLab
    L only, with chroma and hue held. Without it, the same curve is applied to
    R, G and B independently, which is what every other curve node does and is
    why their contrast pushes skin orange — raising R faster than B through the
    steep part of an S-curve *is* a saturation and hue change, just an
    accidental one.

    Order is deliberate: per-channel first (it is a colour decision), then luma
    (it is a tone decision on the result).
    """
    out = rgb
    for i, key in enumerate(("r", "g", "b")):
        pts = params.get(key)
        if pts and len(pts) >= 2 and not _is_identity(pts):
            ch = eval_curve([tuple(p) for p in pts], out[..., i])
            out = torch.cat(
                (out[..., :i], ch.unsqueeze(-1), out[..., i + 1 :]), dim=-1
            )

    luma = params.get("luma")
    if luma and len(luma) >= 2 and not _is_identity(luma):
        pts = [tuple(p) for p in luma]
        if params.get("preserve_hue", True):
            lab = colour.srgb_to_oklab(out)
            l_new = eval_curve(pts, lab[..., 0])
            out = colour.oklab_to_srgb(torch.stack((l_new, lab[..., 1], lab[..., 2]), dim=-1))
        else:
            out = torch.stack([eval_curve(pts, out[..., i]) for i in range(3)], dim=-1)
    return out


def _is_identity(points) -> bool:
    """Cheap short circuit so an untouched channel costs nothing to evaluate."""
    if len(points) != 2:
        return False
    (x0, y0), (x1, y1) = points[0], points[1]
    return abs(x0) < 1e-9 and abs(y0) < 1e-9 and abs(x1 - 1.0) < 1e-9 and abs(y1 - 1.0) < 1e-9


_DISPATCH = {
    "exposure": lambda rgb, p: op_exposure(rgb, p.get("stops", 0.0)),
    "contrast": lambda rgb, p: op_contrast(rgb, p.get("amount", 0.0), p.get("pivot", 0.18)),
    "saturation": lambda rgb, p: op_saturation(rgb, p.get("amount", 1.0)),
    "warmth": lambda rgb, p: op_warmth(rgb, p.get("amount", 0.0)),
    "curves": op_curves,
}


def apply_op(rgb: torch.Tensor, op: dict[str, Any]) -> torch.Tensor:
    """Apply one LOOK op dict to sample points, honouring enabled and strength."""
    if not op.get("enabled", True):
        return rgb
    kind = op.get("type")
    fn = _DISPATCH.get(kind)
    if fn is None:
        # Unknown or render-only op: pass through. The node that owns it applies
        # it elsewhere; silently dropping it here is correct, silently *failing*
        # is not, so LOOK.lut_exportable is what warns the user.
        return rgb
    out = fn(rgb, op.get("params") or {})
    s = float(op.get("strength", 1.0))
    return out if s >= 1.0 else torch.lerp(rgb, out, s)


def build_sample_fn(ops: list[dict[str, Any]]):
    """Fold a list of LOOK ops into a single :data:`~pw_color.lattice.SampleFn`."""

    def fn(pts: torch.Tensor) -> torch.Tensor:
        out = pts
        for op in ops:
            out = apply_op(out, op)
        # Deliberately unclamped. The clamp to [0,1] happens after lattice
        # sampling, in Lattice.apply and in the preview shader — see OUT_MIN in
        # lattice.py for why baking the clamp in is expensive.
        return out

    return fn
