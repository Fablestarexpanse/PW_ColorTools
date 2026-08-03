"""Tests for PW Look I/O, PW Optics and PW Scopes."""

from __future__ import annotations

import pytest
import torch

from pw_color import look_io as lio
from pw_color import optics
from pw_color.lattice import Lattice
from pw_color.scopes import SCOPE_MODES, render_scope
from pw_color.types import Look, LookOp


@pytest.fixture(autouse=True)
def _tmp_look_dir(tmp_path, monkeypatch):
    """Never write into the real output folder from a test."""
    d = tmp_path / "looks"
    d.mkdir()
    monkeypatch.setattr(lio, "look_dir", lambda: d)
    return d


def _mixed_look() -> Look:
    """A look with both bakeable and render-only ops — the interesting case."""
    return Look(
        name="Mixed",
        ops=[
            LookOp(type="tone", params={"contrast": 0.2, "shadows": 0.3}),
            LookOp(type="colour", params={"warmth": 0.25, "saturation": 1.1}),
            LookOp(type="grain", params={"amount": 0.05}, lut_safe=False),
            LookOp(type="glow", params={"amount": 0.2}, lut_safe=False),
            LookOp(type="curves", params={"luma": [[0, 0.05], [1, 0.97]]}),
        ],
    )


def _image(seed: int = 3, h: int = 64, w: int = 96) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return torch.rand(1, h, w, 3, generator=g)


# -- look I/O ----------------------------------------------------------------


def test_look_save_load_round_trip(_tmp_look_dir):
    look = _mixed_look()
    path = lio.save_look(look, "my grade")
    assert path.name == "my grade.look"
    assert lio.load_look("my grade.look").to_dict() == look.to_dict()


def test_saved_look_is_byte_stable(_tmp_look_dir):
    """An unchanged look must produce an identical file, so .look diffs."""
    look = _mixed_look()
    a = lio.save_look(look, "x").read_bytes()
    b = lio.save_look(look, "x").read_bytes()
    assert a == b


def test_list_saved_is_newest_first(_tmp_look_dir):
    import os, time

    lio.save_look(_mixed_look(), "older")
    lio.save_look(_mixed_look(), "newer")
    os.utime(_tmp_look_dir / "newer.look", (time.time() + 10, time.time() + 10))
    assert lio.list_saved()[0] == "newer.look"


def test_list_saved_ignores_other_files(_tmp_look_dir):
    (_tmp_look_dir / "notes.txt").write_text("x")
    lio.save_look(_mixed_look(), "real")
    assert lio.list_saved() == ["real.look"]


def test_loading_missing_or_wrong_type_is_explicit(_tmp_look_dir):
    with pytest.raises(ValueError, match="not found"):
        lio.load_look("nope.look")
    (_tmp_look_dir / "thing.cube").write_text("x")
    with pytest.raises(ValueError, match="not a .look"):
        lio.load_look("thing.cube")


@pytest.mark.parametrize("raw", ["../../etc/passwd", "..\\..\\windows\\x", "/abs/path", "C:\\evil"])
def test_look_path_traversal_is_stripped(raw: str, _tmp_look_dir):
    path = lio.save_look(_mixed_look(), raw)
    assert path.parent == _tmp_look_dir
    assert "/" not in path.name and "\\" not in path.name and ".." not in path.name


# -- cube export honesty -----------------------------------------------------


def test_export_report_separates_bakeable_from_render_only():
    complete, included, dropped = lio.export_report(_mixed_look())
    assert not complete
    assert set(included) == {"tone", "colour", "curves"}
    assert set(dropped) == {"grain", "glow"}


def test_export_report_is_complete_for_a_pure_colour_look():
    look = Look(ops=[LookOp(type="tone", params={"contrast": 0.2})])
    complete, included, dropped = lio.export_report(look)
    assert complete and included == ["tone"] and dropped == []


def test_disabled_render_only_op_does_not_spoil_the_export():
    look = _mixed_look()
    for op in look.ops:
        if not op.lut_safe:
            op.enabled = False
    complete, _, dropped = lio.export_report(look)
    assert complete and dropped == []


def test_cube_excludes_render_only_ops():
    """The load-bearing claim: a .cube carries the colour ops and nothing else.

    Baked from the mixed look, it must equal the cube baked from the same look
    with the render-only ops removed entirely.
    """
    mixed = _mixed_look()
    colour_only = Look(name=mixed.name, ops=[op for op in mixed.ops if op.lut_safe])
    # Compare the lattice data, not the header: the header carries the name.
    def rows(text: str) -> list[str]:
        return [ln for ln in text.splitlines() if ln and ln[0].isdigit()]

    assert rows(lio.bake_cube(mixed, size=17)) == rows(lio.bake_cube(colour_only, size=17))


def test_cube_is_valid_and_reloadable():
    text = lio.bake_cube(_mixed_look(), size=17, title="Mixed")
    assert 'TITLE "Mixed"' in text
    assert "LUT_3D_SIZE 17" in text
    back = Lattice.from_cube(text)
    assert back.size == 17


def test_cube_of_an_empty_look_is_identity():
    text = lio.bake_cube(Look(), size=9)
    lat = Lattice.from_cube(text)
    img = _image(h=8, w=8)
    assert float((lat.apply(img) - img).abs().max()) < 1e-5


def test_cube_size_is_respected():
    for size in (17, 33):
        assert f"LUT_3D_SIZE {size}" in lio.bake_cube(_mixed_look(), size=size)


# -- optics: halation --------------------------------------------------------


def _bright_window(h: int = 96, w: int = 96) -> torch.Tensor:
    img = torch.full((1, h, w, 3), 0.10)
    img[:, 20:40, 20:40] = 1.0
    return img


def test_halation_zero_is_a_no_op():
    img = _bright_window()
    assert torch.equal(optics.apply_halation(img, 0.0), img)


def test_halation_is_red():
    """It is red because the red-sensitive layer is re-exposed from behind.
    A neutral version of this is just bloom, which PW Look already has."""
    img = _bright_window()
    out = optics.apply_halation(img, 0.8, radius=20.0)
    d = (out - img)[0, 30, 45]  # just outside the bright square
    assert float(d[0]) > float(d[1]) > float(d[2]), d.tolist()
    assert float(d[0]) > 0.02


def test_halation_leaves_a_dark_frame_alone():
    dark = torch.full((1, 48, 48, 3), 0.12)
    out = optics.apply_halation(dark, 1.0, threshold=0.70)
    assert float((out - dark).abs().max()) < 1e-3


def test_halation_falls_off_with_distance():
    img = _bright_window()
    out = optics.apply_halation(img, 0.8, radius=20.0)
    near = float((out - img)[0, 30, 45].max())
    far = float((out - img)[0, 30, 90].max())
    assert near > far


def test_halation_stays_in_range():
    out = optics.apply_halation(_bright_window(), 1.0, radius=40.0)
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


# -- optics: vignette --------------------------------------------------------


def test_vignette_zero_is_a_no_op():
    img = torch.full((1, 64, 64, 3), 0.6)
    assert torch.equal(optics.apply_vignette(img, 0.0), img)


def test_vignette_darkens_corners_not_centre():
    img = torch.full((1, 64, 64, 3), 0.6)
    out = optics.apply_vignette(img, 0.6)
    assert float(out[0, 32, 32, 0]) == pytest.approx(0.6, abs=0.01)
    assert float(out[0, 0, 0, 0]) < 0.5


def test_negative_vignette_brightens_corners():
    img = torch.full((1, 64, 64, 3), 0.4)
    out = optics.apply_vignette(img, -0.6)
    assert float(out[0, 0, 0, 0]) > 0.45


def test_vignette_is_symmetric():
    img = torch.full((1, 64, 64, 3), 0.6)
    out = optics.apply_vignette(img, 0.5)
    corners = [float(out[0, y, x, 0]) for y in (0, 63) for x in (0, 63)]
    assert max(corners) - min(corners) < 1e-4


def test_vignette_holds_neutrality():
    """Applied as exposure in linear light, not a multiply, so it must not
    introduce a colour cast."""
    img = torch.full((1, 64, 64, 3), 0.6)
    out = optics.apply_vignette(img, 0.7)
    assert float((out[..., 0] - out[..., 2]).abs().max()) < 1e-5


def test_vignette_roundness_changes_the_shape():
    img = torch.full((1, 64, 128, 3), 0.6)
    ellipse = optics.apply_vignette(img, 0.6, roundness=1.0)
    boxy = optics.apply_vignette(img, 0.6, roundness=0.2)
    assert not torch.allclose(ellipse, boxy, atol=1e-3)


# -- optics: chromatic aberration -------------------------------------------


def test_ca_zero_is_a_no_op():
    img = _image()
    assert torch.equal(optics.apply_chromatic_aberration(img, 0.0), img)


def test_ca_separates_channels_at_the_edges_not_the_centre():
    """Real lateral CA grows with distance from the optical axis; a uniform
    shift would put fringing in the middle of the frame, where a lens has none."""
    img = torch.zeros(1, 64, 64, 3)
    img[:, :, 30:34] = 1.0  # a vertical bar through the centre
    out = optics.apply_chromatic_aberration(img, 1.0)
    centre = float((out[0, 32, 30:34, 0] - out[0, 32, 30:34, 2]).abs().max())
    edge = float((out[0, 2, 30:34, 0] - out[0, 2, 30:34, 2]).abs().max())
    assert edge >= centre


def test_ca_preserves_shape_and_range():
    img = _image()
    out = optics.apply_chromatic_aberration(img, 0.5)
    assert out.shape == img.shape
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


def test_optics_alpha_passes_through():
    img = torch.cat((_bright_window(), torch.rand(1, 96, 96, 1)), dim=-1)
    for fn in (
        lambda x: optics.apply_halation(x, 0.5),
        lambda x: optics.apply_vignette(x, 0.5),
        lambda x: optics.apply_chromatic_aberration(x, 0.5),
    ):
        assert torch.equal(fn(img)[..., 3:], img[..., 3:])


def test_optics_are_deterministic():
    img = _bright_window()
    assert torch.equal(optics.apply_halation(img, 0.4), optics.apply_halation(img, 0.4))


# -- scopes ------------------------------------------------------------------


@pytest.mark.parametrize("mode", SCOPE_MODES)
def test_scope_renders_at_the_requested_size(mode: str):
    out = render_scope(_image(), mode, width=320, height=200)
    assert out.shape == (1, 200, 320, 3)
    assert out.dtype == torch.float32
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


def test_scope_rejects_unknown_mode():
    with pytest.raises(ValueError, match="scope mode"):
        render_scope(_image(), "vectorscope")


def test_histogram_of_black_sits_at_the_left():
    out = render_scope(torch.zeros(1, 32, 32, 3), "histogram", 256, 128)[0]
    left = float(out[:, :8].mean())
    right = float(out[:, -8:].mean())
    assert left > right * 1.5


def test_histogram_of_white_sits_at_the_right():
    out = render_scope(torch.ones(1, 32, 32, 3), "histogram", 256, 128)[0]
    assert float(out[:, -8:].mean()) > float(out[:, :8].mean()) * 1.5


def test_waveform_puts_a_bright_left_half_on_the_left():
    """A waveform must show *where* in the frame the tones are, which is the
    whole difference between it and a histogram.

    Measured as top-vs-bottom energy *within* each half rather than left-vs-
    right: the panel background and graticule are a non-zero floor across the
    whole scope, so a bare left/right comparison mostly measures the backdrop.
    """
    img = torch.zeros(1, 64, 64, 3)
    img[:, :, :32] = 1.0  # left half white, right half black
    out = render_scope(img, "waveform", 128, 128)[0]

    # Where the brightest row sits in each half is the claim, directly.
    lum = out.mean(dim=-1)
    left_peak = int(lum[:, 8:56].mean(dim=1).argmax())
    right_peak = int(lum[:, 72:120].mean(dim=1).argmax())
    assert left_peak < 16, f"white half traced at row {left_peak}, expected near the top"
    assert right_peak > 112, f"black half traced at row {right_peak}, expected near the bottom"


def test_waveform_has_no_gaps_when_the_scope_is_wider_than_the_image():
    """Mapping source columns onto a wider scope lights only every nth column
    and leaves the trace combed with vertical gaps."""
    img = torch.full((1, 32, 40, 3), 0.5)
    out = render_scope(img, "waveform", 400, 128)[0]
    lum = out.mean(dim=-1)
    # Every output column must carry some trace above the panel background.
    floor = float(lum.min())
    per_column_peak = lum.max(dim=0).values
    assert float(per_column_peak.min()) > floor + 0.05, "trace has empty columns"


def test_parade_shows_a_colour_cast():
    """The fastest way to see a cast: the red trace sits higher than the blue."""
    img = torch.zeros(1, 32, 64, 3)
    img[..., 0] = 0.9
    img[..., 1] = 0.5
    img[..., 2] = 0.1
    out = render_scope(img, "parade", 300, 120)[0]
    third = 300 // 3
    # Red panel's energy is near the top, blue panel's near the bottom.
    red_top = float(out[:40, :third].mean())
    blue_top = float(out[:40, 2 * third :].mean())
    assert red_top > blue_top


def test_scope_is_deterministic():
    img = _image()
    assert torch.equal(render_scope(img, "all"), render_scope(img, "all"))


def test_scope_handles_a_flat_image_without_dividing_by_zero():
    for value in (0.0, 1.0, 0.5):
        out = render_scope(torch.full((1, 16, 16, 3), value), "all")
        assert torch.isfinite(out).all()


def test_scope_ignores_alpha():
    rgb = _image()
    rgba = torch.cat((rgb, torch.rand(1, 64, 96, 1)), dim=-1)
    assert torch.equal(render_scope(rgb, "all"), render_scope(rgba, "all"))
