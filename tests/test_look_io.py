"""Saving, loading and baking LOOK documents.

The part worth testing hardest is the honesty of the `.cube` export: a LUT can
only carry per-pixel operations, so the node has to tell the user exactly what
it dropped rather than writing a file that quietly does less than their graph.
"""

from __future__ import annotations

import pytest
import torch

from pw_color import look_io as lio
from pw_color.lattice import Lattice
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
    listed = lio.list_saved()
    assert "real.look" in listed
    assert not any(n.endswith(".txt") for n in listed)
    assert all(n.endswith(".look") for n in listed)


def test_loading_missing_or_wrong_type_is_explicit(_tmp_look_dir):
    with pytest.raises(ValueError, match="not found"):
        lio.load_look("nope.look")
    (_tmp_look_dir / "thing.cube").write_text("x")
    with pytest.raises(ValueError, match="not a .look"):
        lio.load_look("thing.cube")


# -- shipped looks -----------------------------------------------------------


def test_shipped_looks_exist_and_load():
    """Look I/O must be useful on a fresh install, before anything is saved."""
    names = sorted(p.name for p in lio.shipped_dir().glob("*.look"))
    assert names, "no shipped .look presets"
    for name in names:
        look = lio.load_look(name)
        assert look.name, f"{name} has no name"
        assert look.ops, f"{name} has no ops"


def test_shipped_looks_match_the_node_presets():
    """The .look files are generated from presets.json; if someone edits one
    without the other, the same preset means two different things."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    presets = json.loads((root / "looks" / "presets.json").read_text(encoding="utf-8"))["presets"]
    expected = {p["id"] for p in presets if p["id"] != "none"}
    shipped = {p.stem for p in lio.shipped_dir().glob("*.look")}
    assert shipped == expected, f"shipped looks {sorted(shipped)} != presets {sorted(expected)}"


def test_shipped_looks_report_cube_exportability_correctly():
    """The two presets containing glow must declare themselves unexportable."""
    with_glow = {op_name for op_name in ("moody-low-key", "golden-hour")}
    for path in lio.shipped_dir().glob("*.look"):
        look = lio.load_look(path.name)
        complete, _, dropped = lio.export_report(look)
        if path.stem in with_glow:
            assert not complete and "glow" in dropped, f"{path.stem} should report glow as dropped"
        else:
            assert complete, f"{path.stem} should be fully exportable but drops {dropped}"


def test_user_look_overrides_a_shipped_one_of_the_same_name(_tmp_look_dir):
    """Editing a preset and saving it under the same name should win."""
    shipped_name = next(lio.shipped_dir().glob("*.look")).name
    mine = Look(name="mine", ops=[LookOp(type="tone", params={"contrast": 0.9})])
    lio.save_look(mine, shipped_name[: -len(".look")])
    assert lio.load_look(shipped_name).name == "mine"


def test_list_saved_includes_shipped_after_user_looks(_tmp_look_dir):
    lio.save_look(_mixed_look(), "zzz mine")
    listed = lio.list_saved()
    assert listed[0] == "zzz mine.look", "user looks must come first"
    assert any(n.startswith("golden-hour") for n in listed), "shipped looks must be offered too"


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


