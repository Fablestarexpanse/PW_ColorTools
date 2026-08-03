"""Saving and loading palettes.

A palette you found in a generation is worth keeping, and worth taking to other
tools. Four formats, chosen so that between them a palette goes anywhere:

* ``.json`` — our own format. The only one that round-trips losslessly, because
  it carries OKLab coordinates and coverage as well as the hex. Use this to
  bring a palette back into ComfyUI.
* ``.ase`` — Adobe Swatch Exchange. Photoshop, Illustrator, InDesign, Affinity.
* ``.gpl`` — GIMP palette. GIMP, Krita, Inkscape, Aseprite.
* ``.txt`` — one hex per line. For everything else, and for pasting.

``.ase`` and ``.gpl`` are lossy by nature: they carry colours and names, not
coverage. Loading one back gives even coverage across the swatches, which is
flagged in ``meta`` rather than silently invented.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from .colour import hex_to_srgb, srgb_to_hex
from .types import Palette, Swatch

__all__ = [
    "PALETTE_FORMATS",
    "palette_dir",
    "list_saved",
    "save_palette",
    "load_palette",
    "to_bytes",
    "from_bytes",
]

#: Extension -> human label, in the order they appear in the UI.
PALETTE_FORMATS = {
    "json": "PW palette (.json) - reopens here, keeps coverage",
    "ase": "Adobe swatch exchange (.ase) - Photoshop, Illustrator, Affinity",
    "gpl": "GIMP palette (.gpl) - GIMP, Krita, Inkscape, Aseprite",
    "txt": "Hex list (.txt) - one per line",
}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._-]")


def palette_dir() -> Path:
    """Where palettes are saved.

    ComfyUI's output folder, not the pack folder: palettes are the user's work
    and must survive updating or reinstalling this node pack.
    """
    try:
        import folder_paths  # type: ignore

        root = Path(folder_paths.get_output_directory())
    except Exception:  # pragma: no cover - outside ComfyUI
        root = Path(__file__).resolve().parents[1] / "output"
    d = root / "palettes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_saved() -> list[str]:
    """Saved palette filenames, newest first.

    Newest first because the one you just saved is the one you want, and a
    dropdown sorted alphabetically buries it.
    """
    d = palette_dir()
    if not d.is_dir():
        return []
    files = [p for p in d.iterdir() if p.suffix.lower().lstrip(".") in PALETTE_FORMATS]
    return [p.name for p in sorted(files, key=lambda p: (-p.stat().st_mtime, p.name))]


def safe_name(name: str, fmt: str) -> str:
    """Sanitise a user-supplied filename.

    Path separators and ``..`` are stripped rather than escaped: this string
    comes from a text widget, and the only correct handling of a traversal
    attempt is for it not to be a path at all.
    """
    stem = _SAFE_NAME.sub("_", Path(name.strip()).name).strip(" .") or "palette"
    return f"{stem}.{fmt}"


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _to_gpl(palette: Palette, name: str) -> bytes:
    lines = ["GIMP Palette", f"Name: {name}", f"Columns: {min(len(palette.colors), 8)}", "#"]
    for sw in palette.colors:
        r, g, b = (int(v * 255 + 0.5) for v in hex_to_srgb(sw.hex))
        lines.append(f"{r:3d} {g:3d} {b:3d}\t{sw.hex}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _to_txt(palette: Palette) -> bytes:
    return ("\n".join(sw.hex for sw in palette.colors) + "\n").encode("utf-8")


def to_bytes(palette: Palette, fmt: str, name: str = "palette") -> bytes:
    if fmt == "json":
        return palette.to_json().encode("utf-8")
    if fmt == "ase":
        return palette.to_ase_bytes()
    if fmt == "gpl":
        return _to_gpl(palette, name)
    if fmt == "txt":
        return _to_txt(palette)
    raise ValueError(f"unknown palette format {fmt!r}, expected one of {tuple(PALETTE_FORMATS)}")


def save_palette(palette: Palette, name: str, fmt: str = "json") -> Path:
    """Write a palette and return the path actually written."""
    if fmt not in PALETTE_FORMATS:
        raise ValueError(f"unknown palette format {fmt!r}")
    path = palette_dir() / safe_name(name, fmt)
    path.write_bytes(to_bytes(palette, fmt, name=path.stem))
    return path


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def _swatches_from_hex(hexes: list[str], lossy: bool) -> Palette:
    """Rebuild a palette from bare colours.

    Coverage is unknown in these formats, so it is split evenly and ``meta``
    records that it was reconstructed. Inventing plausible-looking coverage
    numbers would be worse — a downstream node cannot tell a guess from a
    measurement.
    """
    import torch

    from . import colour as _colour

    n = max(1, len(hexes))
    colors = []
    for h in hexes:
        lab = _colour.srgb_to_oklab(torch.tensor(hex_to_srgb(h)))
        colors.append(
            Swatch(
                hex=srgb_to_hex(hex_to_srgb(h)),
                oklab=(round(float(lab[0]), 6), round(float(lab[1]), 6), round(float(lab[2]), 6)),
                coverage=round(1.0 / n, 6),
            )
        )
    meta = {"coverage": "even (not carried by this format)"} if lossy else {}
    return Palette(colors=colors, source_hash="", sort="coverage", meta=meta)


def _from_gpl(data: bytes) -> Palette:
    hexes = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.lower().startswith(("gimp palette", "name:", "columns:")):
            continue
        parts = s.split()
        if len(parts) >= 3:
            try:
                r, g, b = (int(parts[i]) for i in range(3))
            except ValueError:
                continue
            hexes.append(srgb_to_hex((r / 255.0, g / 255.0, b / 255.0)))
    if not hexes:
        raise ValueError("no colours found in .gpl file")
    return _swatches_from_hex(hexes, lossy=True)


def _from_ase(data: bytes) -> Palette:
    """Read an ASE. Only RGB colour blocks; groups and other spaces are skipped.

    Written by hand for the same reason the writer was: it is a short format and
    the alternative is a dependency.
    """
    if data[:4] != b"ASEF":
        raise ValueError("not an ASE file")
    count = struct.unpack(">I", data[8:12])[0]
    hexes: list[str] = []
    pos = 12
    for _ in range(count):
        if pos + 6 > len(data):
            break
        block_type, block_len = struct.unpack(">HI", data[pos : pos + 6])
        body = data[pos + 6 : pos + 6 + block_len]
        pos += 6 + block_len
        if block_type != 0x0001 or len(body) < 2:
            continue  # group start/end
        name_len = struct.unpack(">H", body[:2])[0]
        off = 2 + name_len * 2
        model = body[off : off + 4]
        if model == b"RGB ":
            r, g, b = struct.unpack(">fff", body[off + 4 : off + 16])
            hexes.append(srgb_to_hex((r, g, b)))
    if not hexes:
        raise ValueError("no RGB colours found in .ase file")
    return _swatches_from_hex(hexes, lossy=True)


def _from_txt(data: bytes) -> Palette:
    hexes = re.findall(r"#?([0-9a-fA-F]{6})\b", data.decode("utf-8", errors="replace"))
    if not hexes:
        raise ValueError("no hex colours found")
    return _swatches_from_hex([f"#{h.upper()}" for h in hexes], lossy=True)


def from_bytes(data: bytes, fmt: str) -> Palette:
    if fmt == "json":
        return Palette.from_json(data.decode("utf-8"))
    if fmt == "ase":
        return _from_ase(data)
    if fmt == "gpl":
        return _from_gpl(data)
    if fmt == "txt":
        return _from_txt(data)
    raise ValueError(f"unknown palette format {fmt!r}")


def load_palette(filename: str) -> Palette:
    """Load a saved palette by filename from the palettes folder."""
    path = palette_dir() / Path(filename).name
    if not path.is_file():
        raise ValueError(f"palette {filename!r} not found in {palette_dir()}")
    fmt = path.suffix.lower().lstrip(".")
    if fmt not in PALETTE_FORMATS:
        raise ValueError(f"{filename!r} is not a palette file")
    return from_bytes(path.read_bytes(), fmt)
