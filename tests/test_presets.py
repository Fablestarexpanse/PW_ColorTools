"""Loading the shipped presets.

The three rules the module states: the code owns 'none', an unknown id raises,
and only successful reads are cached — the last being the bug an `lru_cache`
around the read could not avoid.
"""

from __future__ import annotations

import json

import pytest

from pw_color import presets as P
from pw_color.paths import CURVE_PRESETS, LOOK_PRESETS


@pytest.fixture(autouse=True)
def _clear_preset_cache():
    P._presets.clear()
    yield
    P._presets.clear()


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


def test_a_success_after_a_failure_is_cached(tmp_path):
    """The bug the `warned` set introduced: a path that failed once was never
    cached again, so every schema construction re-read it from disk even after
    the user fixed the file — while the docstring said successes were cached."""
    path = tmp_path / "presets.json"
    path.write_text("{ not json", encoding="utf-8")
    assert P.preset_ids(path) == [P.NONE]

    path.write_text(json.dumps({"presets": [{"id": "fixed", "name": "Fixed"}]}), encoding="utf-8")
    first = P.load_presets(path)
    assert "fixed" in first

    path.unlink()
    assert P.load_presets(path) is first, "the recovered read should be cached like any other"


def test_a_presets_file_from_a_newer_build_says_so(tmp_path, caplog):
    """Every other format in the pack checks its schema version; these files
    declare one and it was never read."""
    path = tmp_path / "presets.json"
    path.write_text(
        json.dumps({"schema": P.PRESETS_SCHEMA + 1, "presets": [{"id": "future"}]}), encoding="utf-8"
    )
    with caplog.at_level("WARNING"):
        assert P.preset_ids(path) == [P.NONE]
    assert any("schema" in r.message for r in caplog.records)


def test_the_current_schema_version_is_accepted(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text(json.dumps({"schema": P.PRESETS_SCHEMA, "presets": [{"id": "ok"}]}), encoding="utf-8")
    assert P.preset_ids(path) == [P.NONE, "ok"]
