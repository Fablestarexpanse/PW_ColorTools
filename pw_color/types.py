"""The two custom types the pack shares: ``LOOK`` and ``PALETTE``.

Both are plain JSON-serializable dicts rather than classes on the wire, because
they have to survive being written into a workflow JSON, saved into PNG
metadata, reloaded by a different version of the pack, and round-tripped
through a ``.look`` file on disk. A dict with an explicit ``schema`` integer
does all of that; a pickled object does none of it.

The dataclasses here are ergonomic wrappers over those dicts. ``to_dict`` /
``from_dict`` are the contract, and ``tests/test_types.py`` pins the round trip.

Forward compatibility rule: unknown keys inside ``params`` and ``meta`` are
*preserved* through a round trip. An older build of the pack loading a newer
look must not silently delete the parts it doesn't understand, because the user
will save it again and lose work.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = [
    "LOOK_SCHEMA",
    "PALETTE_SCHEMA",
    "Look",
    "LookOp",
    "Palette",
    "Swatch",
    "canonical_json",
    "content_hash",
    "BLEND_MODES",
]

LOOK_SCHEMA = 1
PALETTE_SCHEMA = 1

#: Blend modes offered by any node that composites its result over its input.
#: Deliberately the Photoshop set our audience already knows, not a colour
#: science set. Order is the order they appear in the UI.
BLEND_MODES = (
    "normal",
    "multiply",
    "screen",
    "overlay",
    "soft light",
    "add",
)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no incidental whitespace.

    Used for hashing and for on-disk ``.look`` files, so that an unchanged look
    produces a byte-identical file and diffs stay readable. Floats go through
    ``repr``, which is lossless for float64 round trips.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(obj: Any) -> str:
    """Stable short hash of any JSON-able object. Cache keys and source hashes."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# LOOK
# ---------------------------------------------------------------------------


@dataclass
class LookOp:
    """One entry in the grade stack.

    ``type`` names the node that produced it (``"curves"``, ``"look"``,
    ``"gradient_map"``...). ``params`` is that node's own schema and is
    deliberately opaque here — types.py must not need updating every time a
    node gains a slider.

    ``lut_safe`` records whether the op can be baked into a lattice. It travels
    with the op so that ``PW_LookIO`` can tell the user *which* parts of their
    look a ``.cube`` export will drop, instead of exporting something subtly
    wrong and saying nothing.
    """

    type: str
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    strength: float = 1.0
    blend: str = "normal"
    lut_safe: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "params": self.params,
            "enabled": bool(self.enabled),
            "strength": float(self.strength),
            "blend": self.blend,
            "lut_safe": bool(self.lut_safe),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LookOp":
        if "type" not in d:
            raise ValueError("look op has no 'type'")
        blend = d.get("blend", "normal")
        if blend not in BLEND_MODES:
            raise ValueError(f"unknown blend mode {blend!r}")
        return cls(
            type=str(d["type"]),
            params=dict(d.get("params") or {}),
            enabled=bool(d.get("enabled", True)),
            strength=float(d.get("strength", 1.0)),
            blend=blend,
            lut_safe=bool(d.get("lut_safe", True)),
        )


@dataclass
class Look:
    """A full grade stack. Emitted by every node that changes colour."""

    ops: list[LookOp] = field(default_factory=list)
    name: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": LOOK_SCHEMA,
            "name": self.name,
            "ops": [op.to_dict() for op in self.ops],
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Look":
        schema = int(d.get("schema", 0))
        if schema > LOOK_SCHEMA:
            raise ValueError(
                f"look uses schema {schema}, this build understands up to {LOOK_SCHEMA} — update ComfyUI-PW-Color"
            )
        if schema < 1:
            raise ValueError("look has no schema version")
        return cls(
            ops=[LookOp.from_dict(o) for o in d.get("ops") or []],
            name=str(d.get("name", "")),
            meta=dict(d.get("meta") or {}),
        )

    def appended(self, op: LookOp) -> "Look":
        """Non-mutating append — nodes must never edit the LOOK handed to them,
        since the same object may be wired into two downstream branches."""
        return Look(ops=[*self.ops, op], name=self.name, meta=dict(self.meta))

    @property
    def lut_exportable(self) -> bool:
        """True when every enabled op can be baked into a ``.cube``."""
        return all(op.lut_safe for op in self.ops if op.enabled)

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, text: str) -> "Look":
        return cls.from_dict(json.loads(text))


# ---------------------------------------------------------------------------
# PALETTE
# ---------------------------------------------------------------------------


@dataclass
class Swatch:
    hex: str
    oklab: tuple[float, float, float]
    coverage: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "hex": self.hex,
            "oklab": [float(v) for v in self.oklab],
            "coverage": float(self.coverage),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Swatch":
        lab = d["oklab"]
        if len(lab) != 3:
            raise ValueError(f"swatch oklab must have 3 components, got {len(lab)}")
        return cls(hex=str(d["hex"]), oklab=(float(lab[0]), float(lab[1]), float(lab[2])), coverage=float(d["coverage"]))


@dataclass
class Palette:
    """An ordered list of swatches plus the hash of the image they came from.

    ``source_hash`` is what lets a downstream node tell "the palette I was given
    is stale" from "the user locked this palette deliberately".
    """

    colors: list[Swatch] = field(default_factory=list)
    source_hash: str = ""
    sort: str = "coverage"
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PALETTE_SCHEMA,
            "source_hash": self.source_hash,
            "sort": self.sort,
            "colors": [c.to_dict() for c in self.colors],
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Palette":
        schema = int(d.get("schema", 0))
        if schema > PALETTE_SCHEMA:
            raise ValueError(f"palette uses schema {schema}, this build understands up to {PALETTE_SCHEMA}")
        if schema < 1:
            raise ValueError("palette has no schema version")
        return cls(
            colors=[Swatch.from_dict(c) for c in d.get("colors") or []],
            source_hash=str(d.get("source_hash", "")),
            sort=str(d.get("sort", "coverage")),
            meta=dict(d.get("meta") or {}),
        )

    def hex_string(self, sep: str = ", ") -> str:
        return sep.join(c.hex for c in self.colors)

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, text: str) -> "Palette":
        return cls.from_dict(json.loads(text))

    def to_ase_bytes(self) -> bytes:
        """Adobe Swatch Exchange, RGB float groups. Written by hand because it
        is 40 lines and the alternative is a dependency."""
        import struct

        def block(sw: Swatch) -> bytes:
            from .colour import hex_to_srgb

            r, g, b = hex_to_srgb(sw.hex)
            name = sw.hex + "\x00"
            name_bytes = name.encode("utf-16-be")
            body = struct.pack(">H", len(name)) + name_bytes + b"RGB " + struct.pack(">fff", r, g, b) + struct.pack(">H", 0)
            return struct.pack(">HI", 0x0001, len(body)) + body

        blocks = b"".join(block(c) for c in self.colors)
        return b"ASEF" + struct.pack(">HHI", 1, 0, len(self.colors)) + blocks


def swatches_from_iterable(items: Iterable[dict[str, Any]]) -> list[Swatch]:
    return [Swatch.from_dict(i) for i in items]
