"""Monotonic cubic interpolation for the curve editor.

Fritsch-Carlson (1980), not Catmull-Rom. Catmull-Rom is the usual choice and it
is wrong for tone curves: with unevenly spaced control points it overshoots,
which shows up as a dark halo just above a lifted shadow point and, worse, as a
*reversal* — a range of inputs where a brighter input maps to a darker output.
Every existing ComfyUI curve node has this. Fritsch-Carlson limits the tangents
so the interpolant is monotone wherever the control points are, which makes
overshoot and reversal impossible by construction rather than by clamping after
the fact.

Reference: Fritsch & Carlson, "Monotone Piecewise Cubic Interpolation",
SIAM J. Numer. Anal. 17(2), 1980.

``web/src/core/curve.ts`` mirrors this file exactly. If you change the maths
here, change it there, and the parity test will tell you if you didn't.
"""

from __future__ import annotations

import torch

__all__ = ["monotone_tangents", "eval_curve", "IDENTITY_POINTS"]

#: A two-point identity curve. The minimum a curve is ever allowed to be.
IDENTITY_POINTS: tuple[tuple[float, float], ...] = ((0.0, 0.0), (1.0, 1.0))


def _sorted_unique(points: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
    """Sort by x and drop duplicate x, keeping the last.

    Duplicate x values would divide by zero in the slope. Dragging a point past
    its neighbour is a normal thing for a user to do mid-gesture, so this has to
    be handled here rather than prevented in the UI.
    """
    pts = sorted((float(x), float(y)) for x, y in points)
    xs: list[float] = []
    ys: list[float] = []
    for x, y in pts:
        if xs and abs(x - xs[-1]) < 1e-7:
            ys[-1] = y
        else:
            xs.append(x)
            ys.append(y)
    if len(xs) < 2:
        return [0.0, 1.0], [ys[0] if ys else 0.0, ys[0] if ys else 1.0]
    return xs, ys


def monotone_tangents(xs: list[float], ys: list[float]) -> list[float]:
    """Fritsch-Carlson tangents ``m[i]`` at each knot."""
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    delta = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]

    m = [0.0] * n
    m[0] = delta[0]
    m[n - 1] = delta[n - 2]
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0.0:
            # Local extremum: flat tangent, which is what stops the overshoot.
            m[i] = 0.0
        else:
            # Weighted harmonic mean (Fritsch-Butland form): favours the
            # shorter interval, so a tight pair of points doesn't get whipped
            # around by a distant one.
            w1 = 2.0 * h[i] + h[i - 1]
            w2 = h[i] + 2.0 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])

    # Fritsch-Carlson circle condition. Belt and braces over the harmonic mean:
    # it also handles the endpoint tangents, which the mean never touches.
    for i in range(n - 1):
        if delta[i] == 0.0:
            m[i] = 0.0
            m[i + 1] = 0.0
            continue
        a = m[i] / delta[i]
        b = m[i + 1] / delta[i]
        s = a * a + b * b
        if s > 9.0:
            t = 3.0 / (s**0.5)
            m[i] = t * a * delta[i]
            m[i + 1] = t * b * delta[i]
    return m


def eval_curve(points: list[tuple[float, float]], x: torch.Tensor) -> torch.Tensor:
    """Evaluate the monotone cubic through ``points`` at ``x``.

    Outside the control point range the curve is extended linearly along the
    end tangent, then the whole thing is clamped to ``[0,1]`` — a tone curve
    that leaves the display range is not doing anything a user asked for.
    """
    xs, ys = _sorted_unique(list(points))
    m = monotone_tangents(xs, ys)
    n = len(xs)

    # Preserve the caller's dtype. Lattice bakes run in float64 so that the
    # result matches the browser's float64 arithmetic bit for bit after
    # quantisation; forcing float32 here would reintroduce the drift.
    dev = x.device
    dt = x.dtype if x.dtype.is_floating_point else torch.float32
    xt = torch.tensor(xs, dtype=dt, device=dev)
    yt = torch.tensor(ys, dtype=dt, device=dev)
    mt = torch.tensor(m, dtype=dt, device=dev)

    xf = x.to(dt)
    # Segment index: the last knot whose x is <= our x.
    idx = (torch.bucketize(xf.contiguous(), xt, right=True) - 1).clamp(0, n - 2)

    x0, x1 = xt[idx], xt[idx + 1]
    y0, y1 = yt[idx], yt[idx + 1]
    m0, m1 = mt[idx], mt[idx + 1]
    h = x1 - x0
    t = (xf - x0) / h

    t2 = t * t
    t3 = t2 * t
    h00 = 2.0 * t3 - 3.0 * t2 + 1.0
    h10 = t3 - 2.0 * t2 + t
    h01 = -2.0 * t3 + 3.0 * t2
    h11 = t3 - t2
    y = h00 * y0 + h10 * h * m0 + h01 * y1 + h11 * h * m1

    # Linear extension beyond the endpoints, using the end tangents.
    below = xf < xt[0]
    above = xf > xt[n - 1]
    y = torch.where(below, yt[0] + (xf - xt[0]) * mt[0], y)
    y = torch.where(above, yt[n - 1] + (xf - xt[n - 1]) * mt[n - 1], y)
    return y.clamp(0.0, 1.0)
