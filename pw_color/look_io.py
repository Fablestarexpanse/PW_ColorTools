"""Saving, loading and baking LOOK documents.

A ``.look`` is our own format: the versioned LOOK dict, written as canonical
JSON so an unchanged look produces a byte-identical file and diffs stay
readable. A ``.cube`` is the interchange format, and it is *lossy by
construction* — it can only carry per-pixel operations.

That last point is why this module exists rather than being three lines in the
node. Every op records whether it is LUT-safe, so we can tell the user exactly
which parts of their look a ``.cube`` export drops instead of writing a file
that quietly does less than they think.
"""

from __future__ import annotations

import re
from pathlib import Path

from .lattice import DEFAULT_SIZE, Lattice
from .ops import build_sample_fn
from .types import Look

__all__ = ["look_dir", "list_saved", "save_look", "load_look", "bake_cube", "export_report"]

_SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._-]")


def look_dir() -> Path:
    """Where looks are saved: ComfyUI's output folder, not the pack folder.

    Same reasoning as palettes — these are the user's work and must survive
    updating or reinstalling this node pack.
    """
    try:
        import folder_paths  # type: ignore

        root = Path(folder_paths.get_output_directory())
    except Exception:  # pragma: no cover - outside ComfyUI
        root = Path(__file__).resolve().parents[1] / "output"
    d = root / "looks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_saved() -> list[str]:
    """Saved ``.look`` filenames, newest first."""
    d = look_dir()
    if not d.is_dir():
        return []
    files = [p for p in d.iterdir() if p.suffix.lower() == ".look"]
    return [p.name for p in sorted(files, key=lambda p: (-p.stat().st_mtime, p.name))]


def safe_name(name: str, suffix: str) -> str:
    """Sanitise a user-supplied filename.

    Path separators and ``..`` are stripped rather than escaped: the value comes
    from a text widget, and the only correct handling of a traversal attempt is
    for the result not to be a path at all.
    """
    stem = _SAFE_NAME.sub("_", Path(name.strip()).name).strip(" .") or "look"
    return f"{stem}{suffix}"


def save_look(look: Look, name: str) -> Path:
    path = look_dir() / safe_name(name, ".look")
    path.write_text(look.to_json(), encoding="utf-8")
    return path


def load_look(filename: str) -> Look:
    path = look_dir() / Path(filename).name
    if not path.is_file():
        raise ValueError(f"look {filename!r} not found in {look_dir()}")
    if path.suffix.lower() != ".look":
        raise ValueError(f"{filename!r} is not a .look file")
    return Look.from_json(path.read_text(encoding="utf-8"))


def bake_cube(look: Look, size: int = DEFAULT_SIZE, title: str = "") -> str:
    """Bake the LUT-safe part of a look into an Adobe ``.cube``.

    Only ops flagged ``lut_safe`` are included, because the rest cannot be
    expressed in a lattice at all. Use :func:`export_report` to tell the user
    what was left behind.
    """
    ops = [op.to_dict() for op in look.ops if op.enabled and op.lut_safe]
    lattice = Lattice.from_fn(build_sample_fn(ops), size, encoding=None)
    return lattice.to_cube(title=title or look.name or "PW Color")


def export_report(look: Look) -> tuple[bool, list[str], list[str]]:
    """``(is_complete, included, dropped)`` for a ``.cube`` export.

    Returned rather than printed so the node can put it in an output string and
    the UI can badge it, instead of it vanishing into a server log nobody reads.
    """
    included = [op.type for op in look.ops if op.enabled and op.lut_safe]
    dropped = [op.type for op in look.ops if op.enabled and not op.lut_safe]
    return (not dropped, included, dropped)
