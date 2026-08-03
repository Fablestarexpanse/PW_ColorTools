"""Colour space conversions for PW Color.

Everything here operates on torch tensors with channels in the *last* dimension
(``[..., 3]``) and is shape-agnostic, so the same code serves a ``[B,H,W,3]``
image, a ``[N,3]`` list of lattice sample points and a ``[3]`` single colour.

Why float32 linear internally: the incoming ComfyUI ``IMAGE`` is a
display-referred sRGB-encoded tensor in ``[0,1]``. Doing exposure, blending and
blur in that encoding produces the classic "muddy magenta" edges, because the
transfer curve is not linear in light. We decode to linear for anything that
sums light, and to OKLab for anything perceptual (curve luma, clustering,
reference matching). See `oklab` docs at https://bottosson.github.io/posts/oklab/

Deliberately *not* here: any working-space / ACES / OCIO machinery. v1 assumes
the input primaries are sRGB. Every function takes its input space in the name,
so a future `to_working()` / `from_working()` pair can be slotted in front of
these without touching call sites.
"""

from __future__ import annotations

import torch

__all__ = [
    "srgb_to_linear",
    "linear_to_srgb",
    "linear_to_oklab",
    "oklab_to_linear",
    "srgb_to_oklab",
    "oklab_to_srgb",
    "oklab_to_oklch",
    "oklch_to_oklab",
    "luma_bt709",
    "hex_to_srgb",
    "srgb_to_hex",
]

# ---------------------------------------------------------------------------
# sRGB transfer function
# ---------------------------------------------------------------------------

_SRGB_LINEAR_CUTOFF = 0.0031308
_SRGB_ENCODED_CUTOFF = 0.04045


def srgb_to_linear(x: torch.Tensor) -> torch.Tensor:
    """Decode sRGB-encoded values to linear light.

    Uses the piecewise IEC 61966-2-1 definition rather than a 2.2 power
    approximation: the linear toe matters for the near-black region, which is
    exactly where lifted-black looks and grain live.

    Values outside ``[0,1]`` are handled by odd (sign-preserving) extension so
    that intermediate over/undershoot survives a round trip instead of clipping.
    """
    s = torch.sign(x)
    a = x.abs()
    low = a / 12.92
    high = ((a + 0.055) / 1.055).pow(2.4)
    return s * torch.where(a <= _SRGB_ENCODED_CUTOFF, low, high)


def linear_to_srgb(x: torch.Tensor) -> torch.Tensor:
    """Encode linear light back to sRGB. Inverse of :func:`srgb_to_linear`."""
    s = torch.sign(x)
    a = x.abs()
    low = a * 12.92
    high = 1.055 * a.clamp(min=1e-12).pow(1.0 / 2.4) - 0.055
    return s * torch.where(a <= _SRGB_LINEAR_CUTOFF, low, high)


# ---------------------------------------------------------------------------
# OKLab
# ---------------------------------------------------------------------------

# Bottosson's linear-sRGB -> LMS matrix, applied as row-vector * M^T.
_LRGB_TO_LMS = (
    (0.4122214708, 0.5363325363, 0.0514459929),
    (0.2119034982, 0.6806995451, 0.1073969566),
    (0.0883024619, 0.2817188376, 0.6299787005),
)
_LMS_TO_LAB = (
    (0.2104542553, 0.7936177850, -0.0040720468),
    (1.9779984951, -2.4285922050, 0.4505937099),
    (0.0259040371, 0.7827717662, -0.8086757660),
)
_LAB_TO_LMS = (
    (1.0, 0.3963377774, 0.2158037573),
    (1.0, -0.1055613458, -0.0638541728),
    (1.0, -0.0894841775, -1.2914855480),
)
_LMS_TO_LRGB = (
    (4.0767416621, -3.3077115913, 0.2309699292),
    (-1.2684380046, 2.6097574011, -0.3413193965),
    (-0.0041960863, -0.7034186147, 1.7076147010),
)


def _mat3(x: torch.Tensor, m: tuple) -> torch.Tensor:
    """Apply a 3x3 matrix to a ``[..., 3]`` tensor, channels last."""
    mt = torch.tensor(m, dtype=x.dtype, device=x.device).transpose(0, 1)
    return x @ mt


def _cbrt(x: torch.Tensor) -> torch.Tensor:
    """Signed cube root. Required because linear values can go negative after a
    matrix into LMS for saturated out-of-gamut colours; ``pow(1/3)`` would NaN.
    """
    return torch.sign(x) * x.abs().clamp(min=0.0).pow(1.0 / 3.0)


def linear_to_oklab(x: torch.Tensor) -> torch.Tensor:
    """Linear sRGB -> OKLab. Returns ``[..., 3]`` of ``(L, a, b)``."""
    lms = _mat3(x, _LRGB_TO_LMS)
    return _mat3(_cbrt(lms), _LMS_TO_LAB)


def oklab_to_linear(x: torch.Tensor) -> torch.Tensor:
    """OKLab -> linear sRGB. Inverse of :func:`linear_to_oklab`."""
    lms_ = _mat3(x, _LAB_TO_LMS)
    return _mat3(lms_ * lms_ * lms_, _LMS_TO_LRGB)


def srgb_to_oklab(x: torch.Tensor) -> torch.Tensor:
    return linear_to_oklab(srgb_to_linear(x))


def oklab_to_srgb(x: torch.Tensor) -> torch.Tensor:
    return linear_to_srgb(oklab_to_linear(x))


def oklab_to_oklch(x: torch.Tensor) -> torch.Tensor:
    """OKLab -> OKLCh (lightness, chroma, hue in radians).

    Hue is the axis we hold fixed for ``preserve hue``; keeping it as an
    explicit polar coordinate makes that operation a one-liner instead of a
    vector projection nobody can read six months later.
    """
    l, a, b = x[..., 0], x[..., 1], x[..., 2]
    c = torch.sqrt(a * a + b * b)
    h = torch.atan2(b, a)
    return torch.stack((l, c, h), dim=-1)


def oklch_to_oklab(x: torch.Tensor) -> torch.Tensor:
    l, c, h = x[..., 0], x[..., 1], x[..., 2]
    return torch.stack((l, c * torch.cos(h), c * torch.sin(h)), dim=-1)


def luma_bt709(linear: torch.Tensor) -> torch.Tensor:
    """Rec.709 relative luminance of *linear* values. Returns ``[...]``.

    Used for grain tonal weighting, where we want physical light response
    rather than perceptual lightness — grain sits in the emulsion, not the eye.
    """
    w = torch.tensor((0.2126, 0.7152, 0.0722), dtype=linear.dtype, device=linear.device)
    return (linear * w).sum(dim=-1)


# ---------------------------------------------------------------------------
# Hex helpers (palette I/O)
# ---------------------------------------------------------------------------


def hex_to_srgb(value: str) -> tuple[float, float, float]:
    """``"#7F77DD"`` -> sRGB-encoded floats in ``[0,1]``."""
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        raise ValueError(f"not a hex colour: {value!r}")
    return tuple(int(v[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def srgb_to_hex(rgb) -> str:
    """sRGB-encoded floats -> ``"#RRGGBB"``, rounded half-up and clamped."""
    out = []
    for c in rgb:
        v = float(c)
        v = 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)
        out.append(int(v * 255.0 + 0.5))
    return "#{:02X}{:02X}{:02X}".format(*out)
