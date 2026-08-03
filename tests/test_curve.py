import torch

from pw_color.curve import IDENTITY_POINTS, eval_curve

X = torch.linspace(0.0, 1.0, 2001)


def test_identity_is_identity():
    assert torch.allclose(eval_curve(list(IDENTITY_POINTS), X), X, atol=1e-6)


def test_passes_through_control_points():
    pts = [(0.0, 0.0), (0.25, 0.1), (0.6, 0.8), (1.0, 1.0)]
    for x, y in pts:
        assert abs(eval_curve(pts, torch.tensor([x])).item() - y) < 1e-5


def _is_monotone(y: torch.Tensor) -> bool:
    return bool((y[1:] - y[:-1] >= -1e-6).all())


def test_no_overshoot_classic_s_curve():
    pts = [(0.0, 0.0), (0.25, 0.15), (0.75, 0.85), (1.0, 1.0)]
    y = eval_curve(pts, X)
    assert _is_monotone(y)
    assert y.min() >= -1e-6 and y.max() <= 1.0 + 1e-6


def test_no_overshoot_with_a_cliff():
    """The arrangement that makes Catmull-Rom visibly ring: a near-vertical
    step next to a flat run. This is the case existing curve nodes get wrong."""
    pts = [(0.0, 0.0), (0.48, 0.02), (0.52, 0.98), (1.0, 1.0)]
    y = eval_curve(pts, X)
    assert _is_monotone(y)
    assert y.max() <= 1.0 + 1e-6


def test_no_overshoot_random_monotone_arrangements():
    g = torch.Generator().manual_seed(0xC0FFEE)
    for _ in range(120):
        k = int(torch.randint(2, 9, (1,), generator=g).item())
        xs = torch.rand(k, generator=g).sort().values
        xs = torch.cat((torch.zeros(1), xs, torch.ones(1)))
        ys = torch.rand(len(xs), generator=g).sort().values
        pts = list(zip(xs.tolist(), ys.tolist()))
        assert _is_monotone(eval_curve(pts, X)), pts


def test_flat_segment_stays_flat():
    """A flat run between two points must not bulge — the Fritsch-Carlson
    zero-tangent case."""
    pts = [(0.0, 0.0), (0.3, 0.5), (0.7, 0.5), (1.0, 1.0)]
    y = eval_curve(pts, torch.linspace(0.3, 0.7, 201))
    assert torch.allclose(y, torch.full_like(y, 0.5), atol=1e-6)


def test_duplicate_x_does_not_divide_by_zero():
    pts = [(0.0, 0.0), (0.5, 0.2), (0.5, 0.9), (1.0, 1.0)]
    y = eval_curve(pts, X)
    assert torch.isfinite(y).all()
    assert _is_monotone(y)


def test_unsorted_input_is_sorted():
    a = eval_curve([(1.0, 1.0), (0.0, 0.0), (0.5, 0.7)], X)
    b = eval_curve([(0.0, 0.0), (0.5, 0.7), (1.0, 1.0)], X)
    assert torch.allclose(a, b, atol=1e-7)


def test_output_clamped_to_unit_range():
    pts = [(0.2, 0.0), (0.8, 1.0)]
    y = eval_curve(pts, X)
    assert y.min() >= 0.0 and y.max() <= 1.0
