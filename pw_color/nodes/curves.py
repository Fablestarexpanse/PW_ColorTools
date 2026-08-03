"""PW Curves — interactive multi-channel curve editor.

This is the architecture proof: everything hard lives here. The browser draws
the editor and bakes a lattice; this node bakes the same lattice from the same
control points and applies it. `tests/test_parity.py` is what guarantees the
two agree.

Two things distinguish it from the existing options:

* **Monotone cubic interpolation.** No arrangement of control points can
  overshoot or reverse. See `pw_color/curve.py`.
* **`preserve hue`.** The luma curve drives OKLab lightness with chroma and hue
  held, instead of being applied to R, G and B independently. The latter is what
  everything else does, and it is why raising contrast drags skin tones orange:
  a steep S-curve raises R faster than B, which *is* a saturation and hue shift.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import torch
from comfy_api.latest import io

from ..curve import IDENTITY_POINTS
from ..lattice import DEFAULT_SIZE, FINAL_SIZE, Lattice
from ..ops import build_sample_fn
from ..types import Look, LookOp

PRESETS_PATH = Path(__file__).resolve().parents[2] / "looks" / "curves" / "presets.json"


@lru_cache(maxsize=1)
def _presets() -> dict[str, dict]:
    """Presets, read once. Shipped as JSON rather than hardcoded so a user can
    drop their own in without touching Python."""
    try:
        data = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {p["id"]: p for p in data.get("presets", [])}


def preset_ids() -> list[str]:
    return ["none", *_presets().keys()]


_IDENTITY = [list(p) for p in IDENTITY_POINTS]

_DEFAULT_CURVES = json.dumps(
    {"luma": _IDENTITY, "r": _IDENTITY, "g": _IDENTITY, "b": _IDENTITY},
    separators=(",", ":"),
)


def _normalise(points) -> list[list[float]]:
    """Coerce whatever came out of the workflow JSON into control points.

    Deliberately permissive: a preset file, a hand-edited widget value and the
    editor's own output all land here, and rejecting a slightly-off shape would
    mean a user loses their curve on reload.
    """
    if not points or len(points) < 2:
        return [list(p) for p in _IDENTITY]
    out = []
    for p in points:
        if isinstance(p, dict):
            out.append([float(p.get("x", 0.0)), float(p.get("y", 0.0))])
        else:
            out.append([float(p[0]), float(p[1])])
    return sorted(out, key=lambda q: q[0])


class PW_Curves(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="PW_Curves",
            display_name="PW Curves",
            category="PW Color",
            search_aliases=["curve", "tone curve", "rgb curves", "levels", "contrast"],
            description=(
                "Multi-channel curve editor with monotone cubic interpolation, so no point "
                "arrangement can overshoot or reverse. 'preserve hue' applies the luma curve "
                "to OKLab lightness with chroma held, so contrast does not drag skin orange."
            ),
            inputs=[
                io.Image.Input("image"),
                io.String.Input(
                    "curves",
                    multiline=True,
                    default=_DEFAULT_CURVES,
                    tooltip="Control points, written by the editor. Editable by hand if you must.",
                ),
                io.Boolean.Input(
                    "preserve_hue",
                    default=True,
                    tooltip=(
                        "Apply the luma curve to OKLab lightness with chroma and hue held. "
                        "Off applies it to R, G and B independently, which is how other curve "
                        "nodes behave and will shift hue as contrast rises."
                    ),
                ),
                io.Float.Input(
                    "strength",
                    default=1.0,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Blend toward the identity curve.",
                ),
                io.Combo.Input(
                    "preset",
                    options=preset_ids(),
                    default="none",
                    optional=True,
                    tooltip="Replaces the curves above when set to anything but none.",
                ),
                io.Boolean.Input(
                    "final_quality",
                    default=False,
                    optional=True,
                    tooltip=f"Bake at {FINAL_SIZE}³ instead of {DEFAULT_SIZE}³. Slower, marginally more accurate.",
                ),
                io.Custom("LOOK").Input(
                    "look_in",
                    optional=True,
                    tooltip="Upstream grade stack. This node appends to it.",
                ),
            ],
            outputs=[
                io.Image.Output(display_name="image"),
                io.Custom("LOOK").Output(display_name="look"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls,
        image: torch.Tensor,
        curves: str,
        preserve_hue: bool = True,
        strength: float = 1.0,
        preset: str = "none",
        final_quality: bool = False,
        look_in: dict | None = None,
    ) -> io.NodeOutput:
        # Cache the input so the editor can draw its histogram and preview
        # without waiting for a second execution. Best effort — never let a
        # preview concern break a render.
        try:
            from ..preview_server import store

            store(str(cls.hidden.unique_id), image)
        except Exception:  # pragma: no cover
            pass

        try:
            raw = json.loads(curves) if curves.strip() else {}
        except ValueError as exc:
            raise ValueError(f"PW Curves: could not read the curve data ({exc}). Reset the node to recover.") from exc

        if preset and preset != "none":
            p = _presets().get(preset)
            if p is None:
                raise ValueError(f"PW Curves: unknown preset {preset!r}")
            raw = {**{k: _IDENTITY for k in ("luma", "r", "g", "b")}, **p.get("curves", {})}

        params = {k: _normalise(raw.get(k)) for k in ("luma", "r", "g", "b")}
        params["preserve_hue"] = bool(preserve_hue)

        op = LookOp(type="curves", params=params, strength=float(strength), lut_safe=True)
        size = FINAL_SIZE if final_quality else DEFAULT_SIZE

        lattice = Lattice.from_fn(build_sample_fn([op.to_dict()]), size)
        out = lattice.apply(image)

        look = Look.from_dict(look_in) if look_in else Look()
        return io.NodeOutput(out, look.appended(op).to_dict())


NODES = [PW_Curves]
