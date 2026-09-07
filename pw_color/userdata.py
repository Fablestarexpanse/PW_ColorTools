"""Where the user's saved work goes, and what their filenames are allowed to be.

Looks and palettes both belong in ComfyUI's output folder rather than the pack
folder: they are the user's work and must survive updating or reinstalling this
node pack.

Three rules live here because all of them are the kind that must hold
everywhere or not at all:

* `safe_name` strips path separators rather than escaping them. The value comes
  from a text widget, and the only correct handling of a traversal attempt is
  for the result not to be a path.
* `checked_name` is the containment gate for loading. For files the pack
  enumerates the valid set is known, which is a stronger guarantee than
  sanitising a string.
* `write_atomic` writes beside the target and renames, so an interrupted save
  cannot destroy the file it was replacing.

`user_dir` deliberately does not create anything. Both node schemas list saved
files while ComfyUI builds them at startup, so a directory-creating lookup meant
importing the pack wrote to the user's disk before they had saved anything.
Reading does not create; saving does.
"""

from __future__ import annotations

import os
import re
import struct
from pathlib import Path

from .paths import PACK_ROOT

__all__ = [
    "output_root",
    "user_dir",
    "safe_name",
    "newest_first",
    "write_atomic",
    "PARSE_FAILURES",
    "checked_name",
]

#: What a hand-written parser raises when the bytes are not what it expected.
#:
#: The readers signal "this file is not readable" with ValueError, but they
#: parse binary and JSON by hand and can fail before getting that far. Those
#: escaped as struct.error or AttributeError — exceptions that say nothing about
#: which file was bad, and that a caller guarding a load would not catch.
PARSE_FAILURES = (struct.error, IndexError, KeyError, TypeError, AttributeError, UnicodeDecodeError)

_UNSAFE = re.compile(r"[^A-Za-z0-9 ._-]")
_SEPARATORS = re.compile(r"[\\/]")


def output_root() -> Path:
    """ComfyUI's output directory, or a local one when running outside it."""
    try:
        # ComfyUI-only module: absent when the pack is imported for tests.
        import folder_paths  # type: ignore

        return Path(folder_paths.get_output_directory())
    except Exception:  # pragma: no cover - outside ComfyUI
        return PACK_ROOT / "output"


def user_dir(name: str) -> Path:
    """Where ``name`` is saved. Deliberately does not create it."""
    return output_root() / name


def safe_name(name: str, ext: str, fallback: str) -> str:
    """Sanitise a user-supplied filename into ``<stem>.<ext>``.

    Path separators and ``..`` are stripped rather than escaped: the value comes
    from a text widget, and the only correct handling of a traversal attempt is
    for the result not to be a path at all.

    Both separators are cut on every platform. ``Path(...).name`` only knows the
    host's own, so on Linux ``..\\x`` survived as one long segment and its
    ``..`` reached the filename; CI caught it, the Windows machine it was
    written on never could.
    """
    last = _SEPARATORS.split(name.strip())[-1]
    stem = _UNSAFE.sub("_", last).strip(" .") or fallback
    return f"{stem}.{ext.lstrip('.')}"


def newest_first(directory: Path, extensions: tuple[str, ...]) -> list[str]:
    """Filenames in ``directory`` with those extensions, newest first.

    Newest first because the file you just saved is the one you want back, and
    an alphabetical dropdown buries it.
    """
    if not directory.is_dir():
        return []
    wanted = {e.lower().lstrip(".") for e in extensions}
    files = [p for p in directory.iterdir() if p.suffix.lower().lstrip(".") in wanted]
    return [p.name for p in sorted(files, key=lambda p: (-p.stat().st_mtime, p.name))]


def write_atomic(path: Path, data: bytes) -> Path:
    """Write ``data`` to ``path`` without ever leaving a half-written file.

    Saving is the one operation in this pack that can destroy something the
    user cannot get back. A direct write truncates the target first, so an
    interruption — a full disk, a killed process, a crash in the encoder — turns
    the look or palette they had into an empty or partial file. Writing beside
    it and renaming means the old file survives intact until the new one is
    complete, and ``os.replace`` is atomic on both POSIX and Windows.

    The temporary file goes in the destination directory rather than the system
    temp, because a rename across filesystems is not atomic and would fall back
    to a copy.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def checked_name(filename: str, offered: list[str], kind: str) -> str:
    """The one containment rule for loading a user-named file.

    Every loader in the pack was doing this differently: one took the basename
    and checked the suffix, one took the basename and checked the extension
    against a table, one checked membership of the list its combo offers. Only
    the third is actually a containment check — for files the pack itself
    enumerates, the valid set is *known*, which is a stronger guarantee than
    sanitising a string and hoping.

    The value arrives from a widget, and a widget value is whatever the saved
    workflow JSON says, so it is never trusted.
    """
    name = Path(filename).name
    if name not in offered:
        available = ", ".join(offered[:6]) or "none"
        raise ValueError(
            f"{kind} {filename!r} is not available. Saved {kind}s: {available}"
            + (" ..." if len(offered) > 6 else "")
        )
    return name
