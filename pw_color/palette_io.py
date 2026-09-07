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
from typing import Literal

import torch

from . import colour as _colour
from .colour import hex_to_srgb, srgb_to_hex
from .types import Palette, Swatch
from .userdata import PARSE_FAILURES, checked_name, newest_first, safe_name as _safe_name, write_atomic
from .userdata import user_dir

__all__ = [
    "PALETTE_FORMATS",
    "PaletteFormat",
    "palette_dir",
    "writable_dir",
    "list_saved",
    "save_palette",
    "load_palette",
    "to_bytes",
    "from_bytes",
    "safe_name",
]

#: The formats a palette can be written as.
PaletteFormat = Literal["json", "ase", "gpl", "txt"]

#: Extension -> human label, in the order they appear in the UI. Keys are
#: `PaletteFormat`, so the vocabulary and its labels cannot drift apart.
PALETTE_FORMATS: dict[PaletteFormat, str] = {
    "json": "PW palette (.json) - reopens here, keeps coverage",
    "ase": "Adobe swatch exchange (.ase) - Photoshop, Illustrator, Affinity",
    "gpl": "GIMP palette (.gpl) - GIMP, Krita, Inkscape, Aseprite",
    "txt": "Hex list (.txt) - one per line",
}

def palette_dir() -> Path:
    """Where palettes are saved.

    ComfyUI's output folder, not the pack folder: palettes are the user's work
    and must survive updating or reinstalling this node pack.
    """
    return user_dir("palettes")


def writable_dir() -> Path:
    """`palette_dir`, created. Called when about to write, never when listing —
    see the same note in `look_io`.
    """
    d = palette_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_saved() -> list[str]:
    """Saved palette filenames, newest first."""
    return newest_first(palette_dir(), tuple(PALETTE_FORMATS))


def safe_name(name: str, fmt: str) -> str:
    """Sanitise a user-supplied palette filename. See `userdata.safe_name`."""
    return _safe_name(name, fmt, "palette")


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _to_ase(palette: Palette) -> bytes:
    """Adobe Swatch Exchange, RGB float groups. Written by hand because it is
    forty lines and the alternative is a dependency.

    Here rather than on Palette itself: `_from_ase` and the other three writers
    live in this module, and a format's reader and writer belong side by side —
    they are two halves of one decision about bytes on disk. `types.py` is where
    the LOOK and PALETTE *types* live, not where file formats do.
    """

    def block(sw: Swatch) -> bytes:
        r, g, b = hex_to_srgb(sw.hex)
        name = sw.hex + "\x00"
        name_bytes = name.encode("utf-16-be")
        body = struct.pack(">H", len(name)) + name_bytes + b"RGB " + struct.pack(">fff", r, g, b) + struct.pack(">H", 0)
        return struct.pack(">HI", 0x0001, len(body)) + body

    blocks = b"".join(block(c) for c in palette.colors)
    return b"ASEF" + struct.pack(">HHI", 1, 0, len(palette.colors)) + blocks


def _to_gpl(palette: Palette, name: str) -> bytes:
    lines = ["GIMP Palette", f"Name: {name}", f"Columns: {min(len(palette.colors), 8)}", "#"]
    for sw in palette.colors:
        r, g, b = (int(v * 255 + 0.5) for v in hex_to_srgb(sw.hex))
        lines.append(f"{r:3d} {g:3d} {b:3d}\t{sw.hex}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _to_txt(palette: Palette) -> bytes:
    return ("\n".join(sw.hex for sw in palette.colors) + "\n").encode("utf-8")


def to_bytes(palette: Palette, fmt: PaletteFormat, name: str = "palette") -> bytes:
    if fmt == "json":
        return palette.to_json().encode("utf-8")
    if fmt == "ase":
        return _to_ase(palette)
    if fmt == "gpl":
        return _to_gpl(palette, name)
    if fmt == "txt":
        return _to_txt(palette)
    raise ValueError(f"unknown palette format {fmt!r}, expected one of {tuple(PALETTE_FORMATS)}")


def save_palette(palette: Palette, name: str, fmt: PaletteFormat = "json") -> Path:
    """Write a palette and return the path actually written."""
    if fmt not in PALETTE_FORMATS:
        raise ValueError(f"unknown palette format {fmt!r}")
    path = writable_dir() / safe_name(name, fmt)
    return write_atomic(path, to_bytes(palette, fmt, name=path.stem))


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
                # A .gpl line that is not three integers is a name or a
                # comment, which every real palette has plenty of. Logging
                # each would drown the log; a file with no colours at all
                # raises below, which is the failure worth reporting.
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


#: What a parser raises when the bytes are not what it expected.
#:
#: The readers signal "this file is not readable" with ValueError, but three of
#: them parse binary or text by hand and can fail before they get that far — a
#: truncated ASE runs off the end of a struct.unpack, a malformed JSON palette
#: comes back as a list where a dict was expected. Those reached the user as
#: a raw struct.error, KeyError or AttributeError, none of which says which
#: file was bad — or is what a caller guarding a load would think to catch.
_PARSE_FAILURES = PARSE_FAILURES


def from_bytes(data: bytes, fmt: str) -> Palette:
    """Parse palette bytes. Every unreadable-file failure is a ValueError."""
    readers = {
        "json": lambda: Palette.from_json(data.decode("utf-8")),
        "ase": lambda: _from_ase(data),
        "gpl": lambda: _from_gpl(data),
        "txt": lambda: _from_txt(data),
    }
    reader = readers.get(fmt)
    if reader is None:
        raise ValueError(f"unknown palette format {fmt!r}")
    try:
        return reader()
    except _PARSE_FAILURES as exc:
        raise ValueError(f"could not read this as a .{fmt} palette: {exc}") from exc


def load_palette(filename: str) -> Palette:
    """Load a saved palette by filename from the palettes folder."""
    path = palette_dir() / checked_name(filename, list_saved(), "palette")
    fmt = path.suffix.lower().lstrip(".")
    if fmt not in PALETTE_FORMATS:
        raise ValueError(f"{filename!r} is not a palette file")
    return from_bytes(path.read_bytes(), fmt)
