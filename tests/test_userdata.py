"""Where the user's saved work goes, and what its filenames may be.

The three rules that must hold everywhere or not at all: a sanitiser that
cannot return a path, a containment gate for loading, and a write that cannot
destroy the file it replaces.
"""

from __future__ import annotations

import os
import time

import pytest

from pw_color import userdata as U

BACKSLASH = chr(92)


@pytest.mark.parametrize(
    "raw",
    ["../../etc/passwd", "/absolute/path", "a/b/c", "..", "...", "   ", "con:trol*chars?"],
)
def test_safe_name_never_returns_a_path(raw: str):
    out = U.safe_name(raw, "json", "fallback")
    assert "/" not in out and BACKSLASH not in out
    assert not out.startswith(".")
    assert out.endswith(".json")


@pytest.mark.parametrize("raw", ["..\\..\\windows\\x", "C:\\evil", "..\\x/..\\y"])
def test_safe_name_cuts_backslashes_on_every_platform(raw: str):
    """On Linux a backslash is an ordinary character, so `Path(raw).name` left
    the whole string intact and its `..` reached the filename. Found by CI on
    ubuntu; every Windows run passed."""
    out = U.safe_name(raw, "json", "fallback")
    assert BACKSLASH not in out and "/" not in out and ".." not in out


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
