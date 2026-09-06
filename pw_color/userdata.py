"""Where the user's saved work goes, and what their filenames are allowed to be.

Looks and palettes are saved the same way and for the same reason — they are
the user's work, so they belong in ComfyUI's output folder where they survive
updating or reinstalling this pack. That shared reasoning had two
implementations: `look_io` and `palette_io` each carried their own copy of the
`folder_paths` shim, the sanitiser regex, the sanitiser itself and the
newest-first listing.

Four copied helpers is bad; a copied *security* helper is worse. `safe_name`
strips path separators rather than escaping them, and a policy that exists
twice is a policy nothing enforces for the third module that wants it. So it
lives here once.

The two copies had also drifted where it is easiest to drift unnoticed: the
second argument meant a dotted suffix (``".look"``) in one and a bare extension
(``"json"``) in the other. This module takes the bare extension.

Directory *creation* is deliberately not part of `user_dir`. Both node schemas
list saved files while ComfyUI builds them at startup, so a `look_dir()` that
mkdir'd meant importing the pack wrote directories to disk before the user had
saved anything. Reading does not create; saving does.
"""

from __future__ import annotations

import re
from pathlib import Path

from .paths import PACK_ROOT

__all__ = ["output_root", "user_dir", "safe_name", "newest_first"]

_UNSAFE = re.compile(r"[^A-Za-z0-9 ._-]")


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
    """
    stem = _UNSAFE.sub("_", Path(name.strip()).name).strip(" .") or fallback
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
