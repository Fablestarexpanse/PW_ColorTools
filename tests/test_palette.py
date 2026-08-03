"""Tests for PW Palette.

The acceptance criterion — "same image gives byte-identical palette across
runs" — is the first test, and it is checked on the serialised bytes rather
than on approximate colour equality, because that is the promise.
"""

from __future__ import annotations

import pytest
import torch

from pw_color import colour
from pw_color.palette import SORT_MODES, extract_palette, kmeans_oklab
from pw_color.swatch_strip import render_strip
from pw_color.types import Palette


def _blocks(colours: list[str], weights: list[int] | None = None, h: int = 64) -> torch.Tensor:
    """An image made of vertical bands of known colours, in known proportions."""
    weights = weights or [1] * len(colours)
    cols = []
    for hexv, wt in zip(colours, weights):
        rgb = torch.tensor(colour.hex_to_srgb(hexv))
        cols.append(rgb.view(1, 1, 3).expand(h, wt * 8, 3))
    return torch.cat(cols, dim=1).unsqueeze(0)


def _photo(seed: int = 1, h: int = 96, w: int = 128) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    y = torch.linspace(0, 1, h).view(h, 1)
    x = torch.linspace(0, 1, w).view(1, w)
    base = torch.stack(
        (
            0.45 + 0.35 * torch.sin(x * 5.0) * torch.cos(y * 3.0),
            0.40 + 0.28 * torch.cos(x * 2.0 + y * 4.0),
            0.50 + 0.22 * torch.sin(y * 6.0) + 0.10 * torch.cos(x * 4.0),
        ),
        dim=-1,
    )
    return (base + 0.03 * torch.rand(h, w, 3, generator=g)).clamp(0, 1).unsqueeze(0)


# -- acceptance --------------------------------------------------------------


def test_same_image_gives_byte_identical_palette():
    img = _photo()
    a = extract_palette(img, count=5)
    b = extract_palette(img, count=5)
    assert a.to_json() == b.to_json()


def test_determinism_holds_across_every_option_combination():
    img = _photo(2)
    for sort in SORT_MODES:
        for black in (True, False):
            for chroma in (True, False):
                kw = dict(count=6, sort=sort, ignore_near_black=black, weight_by_chroma=chroma)
                assert extract_palette(img, **kw).to_json() == extract_palette(img, **kw).to_json(), kw


def test_determinism_is_independent_of_batch_padding():
    """Only the first frame is used, so a bigger batch must not change anything."""
    img = _photo(3)
    single = extract_palette(img, count=4)
    batched = extract_palette(torch.cat([img, _photo(9)], dim=0), count=4)
    assert single.to_json() == batched.to_json()


# -- correctness -------------------------------------------------------------


def test_finds_the_actual_colours_in_a_flat_image():
    wanted = ["#D96A6A", "#7FBF9E", "#7FA8DD"]
    pal = extract_palette(_blocks(wanted), count=3, ignore_near_black=False, ignore_near_white=False)
    found = sorted(c.hex for c in pal.colors)
    assert found == sorted(wanted), found


def test_coverage_reflects_real_proportions():
    pal = extract_palette(
        _blocks(["#D96A6A", "#7FBF9E"], weights=[3, 1]),
        count=2,
        ignore_near_black=False,
        ignore_near_white=False,
    )
    by_hex = {c.hex: c.coverage for c in pal.colors}
    assert by_hex["#D96A6A"] == pytest.approx(0.75, abs=0.03)
    assert by_hex["#7FBF9E"] == pytest.approx(0.25, abs=0.03)


def test_coverage_sums_to_one():
    pal = extract_palette(_photo(), count=6)
    assert sum(c.coverage for c in pal.colors) == pytest.approx(1.0, abs=1e-3)


def test_oklab_matches_the_reported_hex():
    for c in extract_palette(_photo(), count=5).colors:
        rgb = colour.oklab_to_srgb(torch.tensor(c.oklab)).clamp(0, 1)
        assert colour.srgb_to_hex(rgb.tolist()) == c.hex


def test_count_is_respected():
    for k in (1, 3, 8):
        assert len(extract_palette(_photo(), count=k).colors) == k


def test_asking_for_more_colours_than_exist_does_not_duplicate():
    """k > distinct colours: the extra centroids collapse and are dropped
    rather than returned as duplicate swatches."""
    pal = extract_palette(_blocks(["#D96A6A", "#7FBF9E"]), count=8, ignore_near_black=False, ignore_near_white=False)
    hexes = [c.hex for c in pal.colors]
    assert len(hexes) == len(set(hexes)), hexes
    assert len(hexes) <= 3


# -- near-black / near-white -------------------------------------------------


def test_dark_image_does_not_return_five_blacks():
    """The reason ignore_near_black exists."""
    img = _blocks(["#000000", "#050505", "#0A0A0A", "#0D0D0D", "#8B3A3A"], weights=[8, 8, 8, 8, 1])
    filtered = extract_palette(img, count=5, ignore_near_black=True)
    assert any(c.hex == "#8B3A3A" for c in filtered.colors), [c.hex for c in filtered.colors]

    unfiltered = extract_palette(img, count=5, ignore_near_black=False)
    dark = sum(1 for c in unfiltered.colors if c.oklab[0] < 0.2)
    assert dark >= 3, "test image is not actually dark-dominated"


def test_bright_image_does_not_return_five_whites():
    img = _blocks(["#FFFFFF", "#FDFDFD", "#FAFAFA", "#F7F7F7", "#3A6B8B"], weights=[8, 8, 8, 8, 1])
    pal = extract_palette(img, count=5, ignore_near_white=True)
    assert any(c.hex == "#3A6B8B" for c in pal.colors), [c.hex for c in pal.colors]


def test_all_black_image_still_returns_a_palette():
    """Everything filtered out must fall back, not fail. An all-black image
    has a palette and it is one black."""
    pal = extract_palette(torch.zeros(1, 32, 32, 3), count=3)
    assert len(pal.colors) >= 1
    assert pal.colors[0].hex == "#000000"


def test_all_white_image_still_returns_a_palette():
    pal = extract_palette(torch.ones(1, 32, 32, 3), count=3)
    assert len(pal.colors) >= 1
    assert pal.colors[0].hex == "#FFFFFF"


# -- chroma weighting --------------------------------------------------------

def _chroma(sw) -> float:
    return (sw.oklab[1] ** 2 + sw.oklab[2] ** 2) ** 0.5


def _accent_rank(pal) -> int:
    """Where the most saturated swatch sits in the returned order."""
    idx = max(range(len(pal.colors)), key=lambda i: _chroma(pal.colors[i]))
    return idx


def test_weight_by_chroma_lifts_a_small_accent_up_the_order():
    """The stated purpose: a 2% accent red should not be buried by coverage sort.

    Note k-means++ already *finds* the accent without this toggle — seeding by
    squared distance naturally favours outliers, which is a good property we get
    for free. What the toggle changes is prominence, and therefore position.
    """
    img = _blocks(["#6E6A66", "#726E6A", "#767270", "#E03A2F"], weights=[16, 16, 16, 1])
    kw = dict(count=4, ignore_near_black=False, ignore_near_white=False, sort="coverage")
    plain = extract_palette(img, weight_by_chroma=False, **kw)
    boosted = extract_palette(img, weight_by_chroma=True, **kw)

    assert _accent_rank(boosted) < _accent_rank(plain), (
        [c.hex for c in plain.colors],
        [c.hex for c in boosted.colors],
    )


def test_accent_is_found_even_without_chroma_weighting():
    """k-means++ seeding earns this. Worth pinning so a switch to plain random
    seeding would be caught rather than quietly degrading palettes."""
    img = _blocks(["#6E6A66", "#726E6A", "#767270", "#E03A2F"], weights=[16, 16, 16, 1])
    pal = extract_palette(img, count=3, weight_by_chroma=False, ignore_near_black=False, ignore_near_white=False)
    assert any(_chroma(c) > 0.1 for c in pal.colors), [c.hex for c in pal.colors]


def test_weight_by_chroma_does_not_falsify_coverage():
    """Coverage stays a true pixel fraction whatever the weighting."""
    img = _blocks(["#6E6A66", "#E03A2F"], weights=[9, 1])
    pal = extract_palette(img, count=2, weight_by_chroma=True, ignore_near_black=False, ignore_near_white=False)
    accent = next(c for c in pal.colors if (c.oklab[1] ** 2 + c.oklab[2] ** 2) ** 0.5 > 0.1)
    assert accent.coverage == pytest.approx(0.1, abs=0.03), accent.coverage


# -- sorting -----------------------------------------------------------------


def test_sort_by_lightness():
    pal = extract_palette(_photo(), count=6, sort="lightness")
    ls = [c.oklab[0] for c in pal.colors]
    assert ls == sorted(ls, reverse=True)


def test_sort_by_hue():
    import math

    pal = extract_palette(_photo(), count=6, sort="hue")
    hues = [math.atan2(c.oklab[2], c.oklab[1]) for c in pal.colors]
    assert hues == sorted(hues)


def test_sort_by_coverage():
    pal = extract_palette(_photo(), count=6, sort="coverage")
    cov = [c.coverage for c in pal.colors]
    assert cov == sorted(cov, reverse=True)


def test_sort_mode_is_recorded_and_validated():
    assert extract_palette(_photo(), count=3, sort="hue").sort == "hue"
    with pytest.raises(ValueError, match="sort mode"):
        extract_palette(_photo(), count=3, sort="brightness")


def test_invalid_count_is_rejected():
    with pytest.raises(ValueError, match="at least one"):
        extract_palette(_photo(), count=0)


# -- mask --------------------------------------------------------------------


def test_mask_restricts_extraction_to_a_region():
    img = _blocks(["#D96A6A", "#7FBF9E"], weights=[1, 1])
    w = img.shape[2]
    mask = torch.zeros(1, img.shape[1], w)
    mask[:, :, : w // 2] = 1.0  # left half only
    pal = extract_palette(img, count=1, mask=mask, ignore_near_black=False, ignore_near_white=False)
    assert pal.colors[0].hex == "#D96A6A"


def test_mask_size_mismatch_is_an_explicit_error():
    with pytest.raises(ValueError, match="does not match"):
        extract_palette(_photo(h=64, w=64), count=3, mask=torch.ones(1, 32, 32))


# -- source hash -------------------------------------------------------------


def test_source_hash_distinguishes_images_and_settings():
    a = extract_palette(_photo(1), count=4)
    b = extract_palette(_photo(2), count=4)
    c = extract_palette(_photo(1), count=5)
    assert a.source_hash != b.source_hash
    assert a.source_hash != c.source_hash
    assert a.source_hash == extract_palette(_photo(1), count=4).source_hash


# -- serialisation and outputs -----------------------------------------------


def test_palette_round_trips_through_json():
    pal = extract_palette(_photo(), count=5)
    assert Palette.from_json(pal.to_json()).to_dict() == pal.to_dict()


def test_hex_string_matches_the_swatch_order():
    pal = extract_palette(_photo(), count=4, sort="hue")
    assert pal.hex_string() == ", ".join(c.hex for c in pal.colors)


def test_ase_export_is_well_formed():
    pal = extract_palette(_photo(), count=4)
    data = pal.to_ase_bytes()
    assert data[:4] == b"ASEF"
    assert int.from_bytes(data[8:12], "big") == 4


# -- swatch strip ------------------------------------------------------------


def test_strip_renders_at_the_requested_size():
    strip = render_strip(extract_palette(_photo(), count=5), width=512, height=160)
    assert strip.shape == (1, 160, 512, 3)
    assert strip.dtype == torch.float32
    assert 0.0 <= strip.min().item() and strip.max().item() <= 1.0


def test_strip_contains_the_palette_colours():
    pal = extract_palette(_blocks(["#D96A6A", "#7FBF9E", "#7FA8DD"]), count=3, ignore_near_black=False, ignore_near_white=False)
    strip = render_strip(pal, width=600, height=200)
    px = (strip[0] * 255).round().to(torch.int32)
    present = {tuple(v.tolist()) for v in px.reshape(-1, 3).unique(dim=0)}
    for sw in pal.colors:
        rgb = tuple(int(v * 255 + 0.5) for v in colour.hex_to_srgb(sw.hex))
        assert rgb in present, f"{sw.hex} missing from the strip"


def test_strip_without_labels_is_all_swatch():
    a = render_strip(extract_palette(_photo(), count=3), show_labels=False)
    b = render_strip(extract_palette(_photo(), count=3), show_labels=True)
    assert not torch.equal(a, b)


def test_strip_of_an_empty_palette_does_not_crash():
    strip = render_strip(Palette(), width=200, height=80)
    assert strip.shape == (1, 80, 200, 3)


def test_strip_is_deterministic():
    pal = extract_palette(_photo(), count=5)
    assert torch.equal(render_strip(pal), render_strip(pal))


# -- clustering internals ----------------------------------------------------


def test_kmeans_separates_well_separated_clusters():
    g = torch.Generator().manual_seed(0)
    a = torch.randn(200, 3, generator=g) * 0.01 + torch.tensor([0.2, 0.0, 0.0])
    b = torch.randn(200, 3, generator=g) * 0.01 + torch.tensor([0.8, 0.1, -0.1])
    pts = torch.cat((a, b))
    centres, assign = kmeans_oklab(pts, torch.ones(400), k=2, seed=0)
    assert len(set(assign[:200].tolist())) == 1
    assert len(set(assign[200:].tolist())) == 1
    assert assign[0] != assign[-1]


def test_kmeans_is_deterministic():
    g = torch.Generator().manual_seed(4)
    pts = torch.rand(500, 3, generator=g)
    w = torch.ones(500)
    c1, a1 = kmeans_oklab(pts, w, k=5, seed=7)
    c2, a2 = kmeans_oklab(pts, w, k=5, seed=7)
    assert torch.equal(c1, c2) and torch.equal(a1, a2)


def test_kmeans_rejects_empty_input():
    with pytest.raises(ValueError, match="no samples"):
        kmeans_oklab(torch.zeros(0, 3), torch.zeros(0), k=2)
