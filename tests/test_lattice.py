import torch

from pw_color.lattice import DEFAULT_SIZE, Lattice
from pw_color.ops import build_sample_fn


def _image(seed: int = 7, h: int = 24, w: int = 32) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return torch.rand(1, h, w, 3, generator=g)


def test_identity_lattice_is_a_no_op():
    img = _image()
    assert torch.allclose(Lattice.identity().apply(img), img, atol=1e-6)


def test_flat_round_trip_preserves_indexing():
    lat = Lattice.from_fn(build_sample_fn([{"type": "saturation", "params": {"amount": 1.4}}]))
    assert torch.equal(Lattice.from_flat(lat.to_flat(), lat.size).data, lat.data)


def test_flat_order_is_red_fastest():
    """The .cube contract. If this flips, every exported LUT is scrambled."""
    flat = Lattice.identity(5).to_flat()
    # First five entries walk red with green and blue at 0.
    assert torch.allclose(flat[:5, 0], torch.linspace(0, 1, 5), atol=1e-6)
    assert torch.allclose(flat[:5, 1:], torch.zeros(5, 2), atol=1e-6)
    # Entry 5 is the first green step.
    assert abs(flat[5, 1].item() - 0.25) < 1e-6


def test_lattice_hits_its_own_grid_points_exactly():
    lat = Lattice.from_fn(build_sample_fn([{"type": "exposure", "params": {"stops": 0.5}}]))
    pts = Lattice.identity(lat.size).to_flat()
    assert torch.allclose(lat.apply_points(pts), lat.to_flat(), atol=1e-6)


def test_blend_to_identity_endpoints():
    lat = Lattice.from_fn(build_sample_fn([{"type": "contrast", "params": {"amount": 0.5}}]))
    assert torch.allclose(lat.blend_to_identity(0.0).data, Lattice.identity().data, atol=1e-6)
    assert torch.allclose(lat.blend_to_identity(1.0).data, lat.data, atol=1e-6)


def test_transport_u16_round_trip_within_quantisation():
    lat = Lattice.from_fn(build_sample_fn([{"type": "warmth", "params": {"amount": 0.6}}]))
    back = Lattice.from_transport(lat.to_transport("u16"))
    assert (back.data - lat.data).abs().max().item() <= 1.0 / 65535.0


def test_transport_f32_round_trip_is_exact():
    lat = Lattice.from_fn(build_sample_fn([{"type": "warmth", "params": {"amount": 0.6}}]))
    back = Lattice.from_transport(lat.to_transport("f32"))
    assert torch.equal(back.data, lat.data)


def test_transport_is_idempotent():
    """Quantising twice must not drift — preview quantises, render re-reads."""
    lat = Lattice.from_fn(build_sample_fn([{"type": "saturation", "params": {"amount": 0.3}}]))
    a = Lattice.from_transport(lat.to_transport("u16"))
    b = Lattice.from_transport(a.to_transport("u16"))
    assert torch.equal(a.data, b.data)


def test_cube_round_trip():
    # encoding=None: .cube authoring is the one place full float precision is
    # the point, so it is the one place an unquantised lattice is correct.
    lat = Lattice.from_fn(build_sample_fn([{"type": "saturation", "params": {"amount": 1.6}}]), size=17, encoding=None)
    back = Lattice.from_cube(lat.to_cube())
    assert back.size == 17
    # .cube is a [0,1] format by definition, so the round trip is against the
    # clamped lattice. This is the one place our internal render and an exported
    # LUT legitimately differ — the export cannot carry the unclamped range.
    assert (back.data.to(torch.float64) - lat.data.clamp(0.0, 1.0)).abs().max().item() < 1e-6


def test_cube_rejects_1d():
    import pytest

    with pytest.raises(ValueError, match="1D"):
        Lattice.from_cube("LUT_1D_SIZE 32\n0 0 0\n")


def test_cube_rejects_row_count_mismatch():
    import pytest

    with pytest.raises(ValueError, match="rows"):
        Lattice.from_cube("LUT_3D_SIZE 2\n0 0 0\n1 1 1\n")


def test_apply_preserves_alpha():
    img = torch.cat((_image(), torch.rand(1, 24, 32, 1)), dim=-1)
    out = Lattice.from_fn(build_sample_fn([{"type": "exposure", "params": {"stops": 1.0}}])).apply(img)
    assert torch.equal(out[..., 3], img[..., 3])


def test_out_of_range_input_clamps_to_lattice_edge():
    lat = Lattice.from_fn(build_sample_fn([{"type": "exposure", "params": {"stops": -1.0}}]))
    over = lat.apply_points(torch.tensor([[1.5, 1.5, 1.5]]))
    edge = lat.apply_points(torch.tensor([[1.0, 1.0, 1.0]]))
    assert torch.allclose(over, edge, atol=1e-6)


def _bake_cost_codes(ops: list, size: int = DEFAULT_SIZE) -> float:
    """Max error, in 8-bit codes, of the baked lattice against direct evaluation.

    Both sides clamp after evaluation, matching the render path — the lattice
    holds the unclamped function on purpose.
    """
    fn = build_sample_fn(ops)
    lat = Lattice.from_fn(fn, size)
    g = torch.Generator().manual_seed(11)
    pts = torch.rand(20000, 3, generator=g)
    sampled = lat.apply_points(pts).clamp(0, 1)
    direct = fn(pts).to(torch.float32).clamp(0, 1)
    return float((sampled - direct).abs().max().item() * 255.0)


def test_bake_is_free_for_ops_that_stay_in_gamut():
    """A grade that stays inside sRGB costs under half an 8-bit code to bake.

    This is the number that justifies the architecture. Note it is a *quality*
    budget, not a parity one — preview and render sample the same lattice, so
    any bake error is identical on both sides and the user never sees a
    discrepancy between them. What they could see is banding, which is what
    this bounds.

    Highlight-clipping ops are in this list on purpose: they only pass because
    the lattice stores the unclamped function over an extended range and the
    clamp happens after sampling. Baking the clamp in put exposure at 1.9 codes
    and contrast at 1.4. If someone reverts that, these are the tests that go
    red first.
    """
    for label, ops in (
        ("darkening exposure", [{"type": "exposure", "params": {"stops": -0.3}}]),
        ("brightening exposure (clips highlights)", [{"type": "exposure", "params": {"stops": 0.4}}]),
        ("contrast (clips highlights)", [{"type": "contrast", "params": {"amount": 0.3}}]),
        ("saturation pull", [{"type": "saturation", "params": {"amount": 0.85}}]),
        ("per-channel curves", [{"type": "curves", "params": {"r": [[0, 0], [0.5, 0.54], [1, 1]], "b": [[0, 0.03], [1, 0.96]]}}]),
        (
            "per-channel luma curve",
            [{"type": "curves", "params": {"luma": [[0.0, 0.04], [0.3, 0.26], [0.7, 0.78], [1.0, 0.98]], "preserve_hue": False}}],
        ),
    ):
        cost = _bake_cost_codes(ops)
        assert cost < 0.5, f"{label}: bake costs {cost:.3f} codes at 8-bit"


def test_preserve_hue_curve_bakes_within_one_code():
    """The headline curve feature, budgeted separately because it is borderline.

    Driving OKLab L on an already-saturated colour walks it out of the sRGB
    gamut, so ``preserve hue`` is a gamut-clipping op and cannot be quite as
    cheap as its per-channel sibling. At 33³ it costs about three quarters of a
    code; at 65³ a quarter. Both are below the threshold where anyone sees
    banding, but it is worth knowing if that ever changes.
    """
    ops = [{"type": "curves", "params": {"luma": [[0.0, 0.04], [0.3, 0.26], [0.7, 0.78], [1.0, 0.98]], "preserve_hue": True}}]
    assert _bake_cost_codes(ops, 33) < 1.0
    assert _bake_cost_codes(ops, 65) < 0.5


def test_bake_cost_is_dominated_by_gamut_clipping():
    """Characterisation, not a target. Documents the remaining architectural limit.

    With the clamp moved out of the lattice, the only expensive case left is
    chroma pushed past the sRGB boundary. That is a genuine kink — the gamut
    boundary in OKLab has cusps at the six primaries — and no lattice size
    fixes it: 33³ to 65³ roughly thirds the error rather than quartering it.

    Consequence we accept for v1: a heavy saturation push bands slightly in the
    LUT path. Rejected alternative, with measurements, in ARCHITECTURE.md.
    """
    for label, ops in (
        ("saturation push", [{"type": "saturation", "params": {"amount": 1.18}}]),
        ("warmth push", [{"type": "warmth", "params": {"amount": -0.4}}]),
    ):
        assert _bake_cost_codes(ops) > 1.0, f"{label} no longer leaves the gamut — re-tune this test"

    ops = [{"type": "saturation", "params": {"amount": 1.18}}]
    at33, at65 = _bake_cost_codes(ops, 33), _bake_cost_codes(ops, 65)
    assert 3.0 < at33 < 12.0, f"out-of-gamut bake cost at 33³ moved: {at33:.2f} codes"
    assert at33 / 4.0 < at65 < at33, "error no longer scales like a kink — re-tune this test"


def test_digest_is_stable_and_discriminating():
    a = Lattice.from_fn(build_sample_fn([{"type": "saturation", "params": {"amount": 1.2}}]))
    b = Lattice.from_fn(build_sample_fn([{"type": "saturation", "params": {"amount": 1.2}}]))
    c = Lattice.from_fn(build_sample_fn([{"type": "saturation", "params": {"amount": 1.3}}]))
    assert a.digest() == b.digest()
    assert a.digest() != c.digest()
