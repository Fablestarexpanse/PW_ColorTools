"""Palette extraction: k-means in OKLab.

OKLab rather than RGB because k-means minimises Euclidean distance, and
Euclidean distance in sRGB does not mean "looks different". Cluster in RGB and
you get five near-identical dark browns out of a night scene while the one
saturated accent gets absorbed; cluster in OKLab and the distances match what
the eye is doing.

Determinism is a hard requirement — the acceptance criterion is that the same
image gives a byte-identical palette across runs. Everything that could
introduce variance is pinned:

* clustering runs on the **CPU** regardless of where the image is, because
  reductions on CUDA are not order-deterministic
* k-means++ seeding uses an explicit seeded generator
* a fixed iteration cap with an explicit convergence epsilon, so the answer does
  not depend on how quickly it happened to settle
* empty clusters are reseeded from the farthest point, not a random one
* sorts are stable with an explicit tie-break
"""

from __future__ import annotations

import torch

from . import colour
from .types import Palette, Swatch, content_hash

__all__ = ["SORT_MODES", "extract_palette", "kmeans_oklab", "CLUSTER_LONG_EDGE"]

#: Long edge the image is reduced to before clustering. 200px is ~40k samples,
#: which is far more than k-means needs for k<=12 and keeps extraction instant
#: even at 4K. Larger does not change the answer; it only costs time.
CLUSTER_LONG_EDGE = 200

SORT_MODES = ("coverage", "lightness", "hue")

#: Below/above these OKLab L values a pixel counts as near-black / near-white.
NEAR_BLACK_L = 0.16
NEAR_WHITE_L = 0.94


def _downsample(image: torch.Tensor, long_edge: int = CLUSTER_LONG_EDGE) -> torch.Tensor:
    """Reduce ``[1,H,W,3]`` to at most ``long_edge`` on its long side.

    Area averaging, not nearest: nearest would sample the image on a grid and
    could miss a small accent entirely, which is exactly the colour a user most
    wants in their palette.
    """
    h, w = image.shape[1], image.shape[2]
    scale = min(1.0, long_edge / max(h, w))
    if scale >= 1.0:
        return image
    nh, nw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    x = image.permute(0, 3, 1, 2)
    x = torch.nn.functional.adaptive_avg_pool2d(x, (nh, nw))
    return x.permute(0, 2, 3, 1)


def _kmeanspp_init(pts: torch.Tensor, weights: torch.Tensor, k: int, generator: torch.Generator) -> torch.Tensor:
    """k-means++ seeding. Deterministic given the generator.

    Plain random seeding is what makes naive palette extractors return a
    different answer every run; k-means++ also converges in far fewer iterations
    because it starts spread out.
    """
    n = pts.shape[0]
    first = int(torch.multinomial(weights, 1, generator=generator).item())
    centres = [pts[first]]
    d2 = ((pts - centres[0]) ** 2).sum(dim=1)

    for _ in range(1, k):
        probs = d2 * weights
        total = probs.sum()
        if total <= 1e-12:
            # Every remaining point coincides with a centre — an image with
            # fewer distinct colours than k. Duplicate rather than fail; the
            # duplicate collapses to zero coverage and gets dropped later.
            centres.append(centres[-1])
            continue
        idx = int(torch.multinomial(probs / total, 1, generator=generator).item())
        centres.append(pts[idx])
        d2 = torch.minimum(d2, ((pts - pts[idx]) ** 2).sum(dim=1))

    return torch.stack(centres)


def kmeans_oklab(
    pts: torch.Tensor,
    weights: torch.Tensor,
    k: int,
    seed: int = 0,
    iters: int = 64,
    eps: float = 1e-6,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Weighted Lloyd's algorithm. Returns ``(centres [k,3], assignment [n])``.

    Runs to a fixed cap with an explicit convergence test, so the result does
    not depend on timing or on how lucky the seeding was.
    """
    if pts.shape[0] == 0:
        raise ValueError("no samples to cluster")
    k = min(k, pts.shape[0])

    g = torch.Generator(device="cpu").manual_seed(int(seed))
    centres = _kmeanspp_init(pts, weights, k, g)

    assign = torch.zeros(pts.shape[0], dtype=torch.int64)
    for _ in range(iters):
        # argmin breaks ties toward the lowest index, which is deterministic.
        dist = torch.cdist(pts, centres)
        assign = dist.argmin(dim=1)

        new = torch.zeros_like(centres)
        mass = torch.zeros(k, dtype=pts.dtype)
        new.index_add_(0, assign, pts * weights.unsqueeze(1))
        mass.index_add_(0, assign, weights)

        empty = mass <= 1e-9
        if bool(empty.any()):
            # Reseed an empty cluster from the point farthest from any centre.
            far = dist.min(dim=1).values.argsort(descending=True)
            for j, cluster in enumerate(torch.nonzero(empty).flatten().tolist()):
                new[cluster] = pts[far[min(j, far.numel() - 1)]]
                mass[cluster] = 1.0
        new = new / mass.unsqueeze(1)

        shift = (new - centres).abs().max().item()
        centres = new
        if shift < eps:
            break

    assign = torch.cdist(pts, centres).argmin(dim=1)
    return centres, assign


def extract_palette(
    image: torch.Tensor,
    count: int = 5,
    mask: torch.Tensor | None = None,
    seed: int = 0,
    ignore_near_black: bool = True,
    ignore_near_white: bool = True,
    weight_by_chroma: bool = False,
    sort: str = "coverage",
) -> Palette:
    """Extract an ordered palette from ``[B,H,W,3]``. Only the first frame is used.

    ``ignore_near_black`` / ``ignore_near_white`` exist because without them a
    night scene returns five blacks and a snow scene returns five whites — the
    clusters go where the pixels are, and the pixels are all at one end.

    ``weight_by_chroma`` boosts saturated pixels when clustering and when
    ordering, so a 2% accent red gets its own centroid instead of being absorbed
    into the nearest neutral. It deliberately does **not** change the reported
    ``coverage``, which stays a true pixel fraction — a "coverage" that silently
    meant something else depending on a toggle would be worse than useless.
    """
    if sort not in SORT_MODES:
        raise ValueError(f"unknown sort mode {sort!r}, expected one of {SORT_MODES}")
    if count < 1:
        raise ValueError("palette needs at least one colour")

    # CPU throughout: CUDA reductions are not order-deterministic, and the
    # acceptance criterion is a byte-identical palette across runs.
    img = image[:1, ..., :3].detach().to("cpu", torch.float32)
    src_hash = content_hash(
        {
            "pixels": _cheap_image_hash(img),
            "count": int(count),
            "seed": int(seed),
            "black": bool(ignore_near_black),
            "white": bool(ignore_near_white),
            "chroma": bool(weight_by_chroma),
        }
    )

    if mask is not None:
        m = mask[:1].detach().to("cpu", torch.float32)
        if m.ndim == 2:
            m = m.unsqueeze(0)
        if m.shape[-2:] != img.shape[1:3]:
            raise ValueError(f"mask {tuple(m.shape[-2:])} does not match image {tuple(img.shape[1:3])}")
        # Downsample image and mask together so they stay aligned.
        small = _downsample(torch.cat((img, m.unsqueeze(-1)), dim=-1))
        pixels = small[0, ..., :3].reshape(-1, 3)
        keep = small[0, ..., 3].reshape(-1)
    else:
        small = _downsample(img)
        pixels = small[0].reshape(-1, 3)
        keep = torch.ones(pixels.shape[0])

    lab = colour.srgb_to_oklab(pixels.clamp(0.0, 1.0))
    lightness = lab[:, 0]
    chroma = torch.sqrt(lab[:, 1] ** 2 + lab[:, 2] ** 2)

    weights = keep.clamp(0.0, 1.0)
    if ignore_near_black:
        weights = weights * (lightness > NEAR_BLACK_L).float()
    if ignore_near_white:
        weights = weights * (lightness < NEAR_WHITE_L).float()

    if weights.sum() < 1e-6:
        # Everything was excluded. Fall back to the unfiltered pixels rather
        # than returning nothing: an all-black image still has a palette, and
        # it is one black.
        weights = keep.clamp(0.0, 1.0)
    if weights.sum() < 1e-6:
        weights = torch.ones_like(keep)

    cluster_weights = weights * (1.0 + 4.0 * chroma) if weight_by_chroma else weights

    live = cluster_weights > 1e-9
    lab_live = lab[live]
    w_live = cluster_weights[live]
    pixel_w = weights[live]

    centres, assign = kmeans_oklab(lab_live, w_live, count, seed=seed)

    total_pixels = float(pixel_w.sum().item())
    swatches: list[tuple[Swatch, float]] = []
    for i in range(centres.shape[0]):
        member = assign == i
        pixels_in = float(pixel_w[member].sum().item())
        if pixels_in <= 0.0:
            continue  # collapsed duplicate from k > distinct colours
        cov = pixels_in / max(total_pixels, 1e-9)
        lab_c = centres[i]
        rgb = colour.oklab_to_srgb(lab_c).clamp(0.0, 1.0)
        sw = Swatch(
            hex=colour.srgb_to_hex(rgb.tolist()),
            oklab=(round(float(lab_c[0]), 6), round(float(lab_c[1]), 6), round(float(lab_c[2]), 6)),
            coverage=round(cov, 6),
        )
        prominence = float(w_live[member].sum().item())
        swatches.append((sw, prominence))

    swatches = _sort(swatches, sort)
    return Palette(colors=[s for s, _ in swatches], source_hash=src_hash, sort=sort)


def _sort(items: list[tuple[Swatch, float]], mode: str) -> list[tuple[Swatch, float]]:
    """Stable sort with an explicit tie-break on hex, so ties never reorder."""
    if mode == "coverage":
        key = lambda p: (-p[1], p[0].hex)  # noqa: E731 - prominence, then hex
    elif mode == "lightness":
        key = lambda p: (-p[0].oklab[0], p[0].hex)  # noqa: E731
    else:  # hue
        import math

        key = lambda p: (math.atan2(p[0].oklab[2], p[0].oklab[1]), p[0].hex)  # noqa: E731
    return sorted(items, key=key)


def _cheap_image_hash(img: torch.Tensor) -> str:
    """A hash of the image content, cheap enough to run every execution.

    Hashing 24M floats on every run would be silly, so this hashes a
    deterministic subsample plus the shape and the exact sum. Two genuinely
    different images colliding here would mean a stale-palette warning is
    missed, which is a cosmetic failure, not a correctness one.
    """
    import hashlib

    flat = img.reshape(-1)
    stride = max(1, flat.numel() // 4096)
    sample = flat[::stride][:4096]
    h = hashlib.sha256()
    h.update(str(tuple(img.shape)).encode())
    h.update(f"{float(flat.sum().item()):.6f}".encode())
    h.update(sample.numpy().tobytes())
    return h.hexdigest()[:16]
