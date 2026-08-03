"""Palette save/load.

A palette you liked in a generation has to be keepable and portable, so the
tests cover both directions for every format, and specifically cover the fact
that ASE, GPL and TXT cannot carry coverage — that has to be flagged, not
invented.
"""

from __future__ import annotations

import pytest

from pw_color import palette_io
from pw_color.palette_io import (
    PALETTE_FORMATS,
    from_bytes,
    list_saved,
    load_palette,
    safe_name,
    save_palette,
    to_bytes,
)
from pw_color.types import Palette, Swatch


@pytest.fixture(autouse=True)
def _tmp_palette_dir(tmp_path, monkeypatch):
    """Never write into the real output folder from a test."""
    d = tmp_path / "palettes"
    d.mkdir()
    monkeypatch.setattr(palette_io, "palette_dir", lambda: d)
    return d


def _palette() -> Palette:
    return Palette(
        source_hash="deadbeef",
        sort="coverage",
        colors=[
            Swatch("#7F77DD", (0.551230, -0.012300, -0.154320), 0.412),
            Swatch("#1B1A20", (0.120000, 0.001000, -0.004000), 0.318),
            Swatch("#E0A44C", (0.750000, 0.030000, 0.120000), 0.270),
        ],
    )


# -- formats -----------------------------------------------------------------


@pytest.mark.parametrize("fmt", list(PALETTE_FORMATS))
def test_every_format_writes_non_empty_bytes(fmt: str):
    data = to_bytes(_palette(), fmt)
    assert isinstance(data, bytes) and len(data) > 0


@pytest.mark.parametrize("fmt", list(PALETTE_FORMATS))
def test_every_format_round_trips_the_colours(fmt: str):
    pal = _palette()
    back = from_bytes(to_bytes(pal, fmt), fmt)
    assert [c.hex for c in back.colors] == [c.hex for c in pal.colors], fmt


def test_json_round_trip_is_fully_lossless():
    """The one format that keeps coverage and OKLab. This is what 'bring it
    back into ComfyUI later' relies on."""
    pal = _palette()
    back = from_bytes(to_bytes(pal, "json"), "json")
    assert back.to_dict() == pal.to_dict()


@pytest.mark.parametrize("fmt", ["ase", "gpl", "txt"])
def test_lossy_formats_flag_that_coverage_was_reconstructed(fmt: str):
    """Inventing plausible coverage would be worse than saying it is unknown —
    a downstream node cannot tell a guess from a measurement."""
    back = from_bytes(to_bytes(_palette(), fmt), fmt)
    assert "coverage" in back.meta
    assert all(abs(c.coverage - 1 / 3) < 1e-6 for c in back.colors)


def test_gpl_header_is_what_gimp_expects():
    text = to_bytes(_palette(), "gpl", name="my palette").decode()
    lines = text.splitlines()
    assert lines[0] == "GIMP Palette"
    assert lines[1] == "Name: my palette"
    assert lines[2].startswith("Columns:")
    assert lines[3] == "#"
    assert lines[4].split()[:3] == ["127", "119", "221"]  # #7F77DD


def test_ase_header_is_what_adobe_expects():
    data = to_bytes(_palette(), "ase")
    assert data[:4] == b"ASEF"
    assert int.from_bytes(data[4:6], "big") == 1  # major version
    assert int.from_bytes(data[8:12], "big") == 3  # block count


def test_txt_is_one_hex_per_line():
    lines = to_bytes(_palette(), "txt").decode().strip().splitlines()
    assert lines == ["#7F77DD", "#1B1A20", "#E0A44C"]


def test_txt_reader_accepts_hex_without_hashes():
    back = from_bytes(b"7F77DD\n1b1a20\n", "txt")
    assert [c.hex for c in back.colors] == ["#7F77DD", "#1B1A20"]


def test_ase_reader_skips_group_blocks():
    """Real ASE files from Illustrator wrap swatches in groups."""
    import struct

    body = to_bytes(_palette(), "ase")
    group_start = struct.pack(">HI", 0xC001, 2) + struct.pack(">H", 0)
    doctored = body[:12] + group_start + body[12:]
    doctored = doctored[:8] + struct.pack(">I", 4) + doctored[12:]
    back = from_bytes(doctored, "ase")
    assert [c.hex for c in back.colors] == [c.hex for c in _palette().colors]


def test_unknown_format_is_rejected_both_ways():
    with pytest.raises(ValueError, match="format"):
        to_bytes(_palette(), "aco")
    with pytest.raises(ValueError, match="format"):
        from_bytes(b"", "aco")


def test_malformed_files_raise_rather_than_return_nothing():
    for fmt, data in (("ase", b"NOPE"), ("gpl", b"GIMP Palette\n#\n"), ("txt", b"no colours here")):
        with pytest.raises(ValueError):
            from_bytes(data, fmt)


# -- disk --------------------------------------------------------------------


def test_save_then_load_round_trips(_tmp_palette_dir):
    pal = _palette()
    path = save_palette(pal, "sunset", "json")
    assert path.is_file() and path.name == "sunset.json"
    assert load_palette("sunset.json").to_dict() == pal.to_dict()


@pytest.mark.parametrize("fmt", list(PALETTE_FORMATS))
def test_save_uses_the_right_extension(fmt: str):
    assert save_palette(_palette(), "x", fmt).suffix == f".{fmt}"


def test_saving_twice_overwrites_rather_than_accumulating(_tmp_palette_dir):
    save_palette(_palette(), "same", "json")
    save_palette(_palette(), "same", "json")
    assert len(list(_tmp_palette_dir.iterdir())) == 1


def test_list_saved_is_newest_first(_tmp_palette_dir):
    import os
    import time

    save_palette(_palette(), "older", "json")
    time.sleep(0.01)
    save_palette(_palette(), "newer", "json")
    os.utime(_tmp_palette_dir / "newer.json", (time.time() + 10, time.time() + 10))
    assert list_saved()[0] == "newer.json"


def test_list_saved_ignores_unrelated_files(_tmp_palette_dir):
    (_tmp_palette_dir / "notes.md").write_text("hello")
    save_palette(_palette(), "real", "json")
    assert list_saved() == ["real.json"]


def test_loading_a_missing_palette_is_an_explicit_error():
    with pytest.raises(ValueError, match="not found"):
        load_palette("nope.json")


def test_loading_a_non_palette_file_is_rejected(_tmp_palette_dir):
    (_tmp_palette_dir / "thing.md").write_text("x")
    with pytest.raises(ValueError, match="not a palette"):
        load_palette("thing.md")


# -- filename safety ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["../../etc/passwd", "..\\..\\windows\\system32\\x", "/absolute/path", "C:\\Users\\x\\evil"],
)
def test_path_traversal_is_stripped_not_escaped(raw: str, _tmp_palette_dir):
    """This string comes from a text widget. The only correct handling of a
    traversal attempt is for the result not to be a path at all."""
    path = save_palette(_palette(), raw, "json")
    assert path.parent == _tmp_palette_dir
    assert "/" not in path.name and "\\" not in path.name
    assert ".." not in path.name


def test_empty_name_gets_a_default():
    assert safe_name("   ", "json") == "palette.json"
    assert safe_name("...", "json") == "palette.json"


def test_unsafe_characters_are_replaced():
    assert safe_name('my:pal*ette?', "gpl") == "my_pal_ette_.gpl"


def test_spaces_and_dashes_survive():
    assert safe_name("warm sunset-02", "ase") == "warm sunset-02.ase"
