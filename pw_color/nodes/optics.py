"""PW Optics — halation, vignette and chromatic aberration.

Render-only, all of it: these read pixel neighbourhoods, so none of it can be
baked into a lattice. The node emits a LOOK with ``lut_safe`` false on every op
so that PW Look I/O can tell the user a ``.cube`` export will not include any
of this.

Order is fixed and deliberate: halation, then chromatic aberration, then
vignette. Halation is emulsion, CA is glass, and the vignette is the last thing
that happens to the light — applying the vignette first would then have its
darkened corners bleed back out through halation.
"""

from __future__ import annotations

import torch
from comfy_api.latest import io

from ..optics import apply_chromatic_aberration, apply_halation, apply_vignette
from ..types import Look, LookOp


class PW_Optics(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="PW_Optics",
            display_name="PW Optics",
            category="PW Color",
            search_aliases=["halation", "vignette", "chromatic aberration", "bloom", "lens", "optics"],
            description=(
                "Halation, vignette and chromatic aberration. Halation is the warm bleed around "
                "highlights that reads as film more than any other single effect. Render-only: "
                "none of this can be baked into a LUT."
            ),
            inputs=[
                io.Image.Input("image"),
                io.Float.Input(
                    "halation",
                    default=0.35,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Warm bleed out of the highlights. The single biggest jump toward a film look.",
                    display_mode=io.NumberDisplay.slider,
                ),
                io.Float.Input("halation_radius", default=28.0, min=1.0, max=200.0, step=1.0, display_mode=io.NumberDisplay.slider),
                io.Float.Input("halation_threshold", default=0.70, min=0.0, max=1.0, step=0.01, display_mode=io.NumberDisplay.slider),
                io.Float.Input(
                    "vignette",
                    default=0.0,
                    min=-1.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Negative brightens the corners. Applied as exposure in linear light, not a multiply.",
                    display_mode=io.NumberDisplay.slider,
                ),
                io.Float.Input("vignette_midpoint", default=0.5, min=0.0, max=1.0, step=0.01, optional=True, display_mode=io.NumberDisplay.slider),
                io.Float.Input("vignette_feather", default=0.6, min=0.0, max=1.0, step=0.01, optional=True, display_mode=io.NumberDisplay.slider),
                io.Float.Input(
                    "vignette_roundness",
                    default=1.0,
                    min=0.1,
                    max=1.0,
                    step=0.05,
                    optional=True,
                    tooltip="1.0 is an ellipse fitted to the frame; lower pushes toward a rectangle.",
                    display_mode=io.NumberDisplay.slider,
                ),
                io.Float.Input(
                    "chromatic_aberration",
                    default=0.0,
                    min=-1.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Radial red/blue separation, growing toward the edges as a real lens does.",
                    display_mode=io.NumberDisplay.slider,
                ),
                io.Custom("LOOK").Input("look_in", optional=True),
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
        halation: float = 0.35,
        halation_radius: float = 28.0,
        halation_threshold: float = 0.70,
        vignette: float = 0.0,
        vignette_midpoint: float = 0.5,
        vignette_feather: float = 0.6,
        vignette_roundness: float = 1.0,
        chromatic_aberration: float = 0.0,
        look_in: dict | None = None,
    ) -> io.NodeOutput:
        from ..preview_server import store_for_node, store_output_for_node

        store_for_node(cls, image)

        out = image
        ops: list[LookOp] = []

        # Emulsion, then glass, then falloff. Vignetting first would let its
        # darkened corners bleed back out through halation.
        if halation > 0.0:
            out = apply_halation(out, halation, halation_radius, halation_threshold)
            ops.append(
                LookOp(
                    type="halation",
                    params={"amount": float(halation), "radius": float(halation_radius), "threshold": float(halation_threshold)},
                    lut_safe=False,
                )
            )
        if chromatic_aberration != 0.0:
            out = apply_chromatic_aberration(out, chromatic_aberration)
            ops.append(LookOp(type="chromatic_aberration", params={"amount": float(chromatic_aberration)}, lut_safe=False))
        if vignette != 0.0:
            out = apply_vignette(out, vignette, vignette_midpoint, vignette_roundness, vignette_feather)
            ops.append(
                LookOp(
                    type="vignette",
                    params={
                        "amount": float(vignette),
                        "midpoint": float(vignette_midpoint),
                        "feather": float(vignette_feather),
                        "roundness": float(vignette_roundness),
                    },
                    lut_safe=False,
                )
            )

        # Spatial, so there is nothing to bake and nothing the browser can
        # reproduce. The real output is the only honest preview.
        store_output_for_node(cls, out)

        look = Look.from_dict(look_in) if look_in else Look()
        for op in ops:
            look = look.appended(op)
        return io.NodeOutput(out, look.to_dict())


NODES = [PW_Optics]
