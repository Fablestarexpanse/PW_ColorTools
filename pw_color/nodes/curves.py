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
from typing import Any

import torch
from comfy_api.latest import io

from ._schema import image_and_look_outputs, look_in, look_out
from ..curve import IDENTITY_POINTS
from ..lattice import DEFAULT_SIZE, FINAL_SIZE, Lattice
from ..ops import build_sample_fn
from ..paths import CURVE_PRESETS
from ..presets import preset_ids as _preset_ids
from ..presets import resolve_preset
from ..preview_cache import store_input_for_node
from ..types import Look, LookOp


def preset_ids() -> list[str]:
    """Shipped as JSON rather than hardcoded so a user can drop their own in
    without touching Python."""
    return _preset_ids(CURVE_PRESETS)


_IDENTITY = [list(p) for p in IDENTITY_POINTS]

_DEFAULT_CURVES = json.dumps(
    {"luma": _IDENTITY, "r": _IDENTITY, "g": _IDENTITY, "b": _IDENTITY},
    separators=(",", ":"),
)


def _normalise(points: object) -> list[list[float]]:
    """Coerce whatever came out of the workflow JSON into control points.

    Permissive about *missing* data — a short or absent list falls back to the
    identity rather than losing the user their curve on reload — but not about
    the point shape. It used to also accept ``{"x": .., "y": ..}`` dicts, which
    no producer emits and which the TypeScript reader rejects: a curve Python
    accepted would then bake differently in the browser, and this pack's whole
    argument is that the two agree.
    """
    if not points or len(points) < 2:
        return [list(p) for p in _IDENTITY]
    out = [[float(p[0]), float(p[1])] for p in points]
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
                    display_mode=io.NumberDisplay.slider,
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
                look_in(),
            ],
            outputs=image_and_look_outputs(),
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
        store_input_for_node(cls, image)

        try:
            raw = json.loads(curves) if curves.strip() else {}
        except ValueError as exc:
            raise ValueError(f"PW Curves: could not read the curve data ({exc}). Reset the node to recover.") from exc

        chosen = resolve_preset(CURVE_PRESETS, preset, "PW Curves")
        if chosen:
            raw = {**{k: _IDENTITY for k in ("luma", "r", "g", "b")}, **chosen.get("curves", {})}

        # Heterogeneous on purpose: four curves plus the flag that says how to
        # apply them, which is the shape ops.op_curves consumes.
        params: dict[str, Any] = {k: _normalise(raw.get(k)) for k in ("luma", "r", "g", "b")}
        params["preserve_hue"] = bool(preserve_hue)

        op = LookOp(type="curves", params=params, strength=float(strength), lut_safe=True)
        size = FINAL_SIZE if final_quality else DEFAULT_SIZE

        lattice = Lattice.from_fn(build_sample_fn([op.to_dict()]), size)
        out = lattice.apply(image)

        return io.NodeOutput(out, look_out(look_in, op))


NODES = [PW_Curves]
