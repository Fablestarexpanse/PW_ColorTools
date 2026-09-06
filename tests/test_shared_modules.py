"""The three modules the duplicated helpers were consolidated into.

`paths`, `presets` and `userdata` were extracted from copies that lived in two
or seven places each. Extraction is only an improvement if the single copy is
right, and these carry the parts that were subtly wrong before: which layer
owns the 'none' preset, whether a failed read is cached forever, and what a
filename sanitiser does with a traversal attempt.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from pw_color import presets as P
from pw_color import userdata as U
from pw_color.paths import CURVE_PRESETS, GRAIN_DIR, LOOK_PRESETS, PACK_ROOT

BACKSLASH = chr(92)


# -- paths -------------------------------------------------------------------


def test_every_shipped_asset_location_exists():
    """These are the seven hardcoded derivations replaced by one module; if the
    layout moves, this is what says so."""
    assert (PACK_ROOT / "pw_color").is_dir(), "PACK_ROOT is not the repository root"
    assert LOOK_PRESETS.is_file()
    assert CURVE_PRESETS.is_file()
    assert GRAIN_DIR.is_dir()


# -- presets -----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_preset_cache():
    P._cache.clear()
    P._warned.clear()
    yield
    P._cache.clear()
    P._warned.clear()


@pytest.mark.parametrize("path", [LOOK_PRESETS, CURVE_PRESETS])
def test_none_is_first_whether_or_not_the_file_ships_it(path):
    """The code owns the sentinel. looks/presets.json happens to carry a 'none'
    entry and looks/curves/presets.json does not, and that used to be the
    difference between two node behaviours."""
    ids = P.preset_ids(path)
    assert ids[0] == P.NONE
    assert ids.count(P.NONE) == 1


def test_resolve_returns_nothing_for_none():
    assert P.resolve_preset(LOOK_PRESETS, "none", "PW Look") == {}
    assert P.resolve_preset(LOOK_PRESETS, "", "PW Look") == {}


def test_unknown_preset_raises_with_the_node_name():
    """It used to raise in PW Curves and silently do nothing in PW Look."""
    with pytest.raises(ValueError, match="PW Look: unknown preset 'nope'"):
        P.resolve_preset(LOOK_PRESETS, "nope", "PW Look")


def test_a_shipped_preset_carries_its_parameters():
    found = P.resolve_preset(LOOK_PRESETS, "warm-portrait", "PW Look")
    assert found["params"], "expected the preset to carry params"
    assert P.preset_name(LOOK_PRESETS, "warm-portrait")


def test_a_bad_file_costs_the_presets_not_the_node(tmp_path, caplog):
    bad = tmp_path / "presets.json"
    bad.write_text("{ not json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        ids = P.preset_ids(bad)
    assert ids == [P.NONE], "a malformed file should still leave the node usable"
    assert any("could not read presets" in r.message for r in caplog.records), "the failure must be logged"


def test_a_read_failure_is_not_cached_forever(tmp_path):
    """The bug both copies had: lru_cache pinned the empty result, so fixing
    the file did nothing until the process restarted."""
    path = tmp_path / "presets.json"
    path.write_text("{ not json", encoding="utf-8")
    assert P.preset_ids(path) == [P.NONE]

    path.write_text(json.dumps({"presets": [{"id": "later", "name": "Later"}]}), encoding="utf-8")
    assert "later" in P.preset_ids(path), "fixing the file should recover"


def test_a_good_read_is_cached(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text(json.dumps({"presets": [{"id": "a", "name": "A"}]}), encoding="utf-8")
    first = P.load_presets(path)
    path.unlink()
    assert P.load_presets(path) is first, "schema construction hits this often; it must be cached"


def test_entries_without_an_id_are_skipped(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text(json.dumps({"presets": [{"name": "no id"}, {"id": "ok"}]}), encoding="utf-8")
    assert P.preset_ids(path) == [P.NONE, "ok"]


# -- userdata ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["../../etc/passwd", "/absolute/path", "a/b/c", "..", "...", "   ", "con:trol*chars?"],
)
def test_safe_name_never_returns_a_path(raw: str):
    out = U.safe_name(raw, "json", "fallback")
    assert "/" not in out and BACKSLASH not in out
    assert not out.startswith(".")
    assert out.endswith(".json")


def test_safe_name_falls_back_when_nothing_survives():
    assert U.safe_name("   ", "look", "look") == "look.look"
    assert U.safe_name("...", "json", "palette") == "palette.json"


def test_safe_name_keeps_a_reasonable_name_intact():
    assert U.safe_name("warm sunset-02", "ase", "palette") == "warm sunset-02.ase"


def test_safe_name_takes_a_bare_extension_or_a_dotted_one():
    """The two copies disagreed about this, which is exactly the sort of thing
    a single implementation exists to settle."""
    assert U.safe_name("x", "json", "p") == U.safe_name("x", ".json", "p")


def test_user_dir_does_not_create_anything(tmp_path, monkeypatch):
    """Both node schemas list saved files at startup. If this created
    directories, importing the pack would write to disk before the user had
    saved anything."""
    monkeypatch.setattr(U, "output_root", lambda: tmp_path)
    assert not U.user_dir("looks").exists()


def test_newest_first_orders_by_mtime_then_name(tmp_path):
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "notes.md").write_text("x", encoding="utf-8")
    future = time.time() + 10
    os.utime(tmp_path / "a.json", (future, future))

    assert U.newest_first(tmp_path, ("json",)) == ["a.json", "b.json"]


def test_newest_first_on_a_missing_directory_is_empty(tmp_path):
    assert U.newest_first(tmp_path / "nope", ("json",)) == []


# -- atomic writes -----------------------------------------------------------


def test_write_atomic_creates_the_file_and_leaves_no_partial(tmp_path):
    out = U.write_atomic(tmp_path / "a" / "b.look", b"hello")
    assert out.read_bytes() == b"hello"
    assert list(out.parent.iterdir()) == [out], "a .partial file was left behind"


def test_a_failed_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    """The reason this exists. A direct write truncates the target first, so an
    interruption turns the look the user had into an empty file."""
    target = tmp_path / "keep.look"
    target.write_bytes(b"the original")

    def boom(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(type(target), "write_bytes", boom)
    with pytest.raises(OSError):
        U.write_atomic(target, b"the replacement")

    assert target.read_bytes() == b"the original", "an interrupted save destroyed the old file"
    assert not (tmp_path / "keep.look.partial").exists(), "a .partial file was left behind"


def test_write_atomic_replaces_an_existing_file(tmp_path):
    target = tmp_path / "x.json"
    target.write_bytes(b"old")
    U.write_atomic(target, b"new")
    assert target.read_bytes() == b"new"
    assert len(list(tmp_path.iterdir())) == 1
