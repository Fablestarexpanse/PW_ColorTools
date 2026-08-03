"""PW Scopes — analysis passthrough.

Passes the image through untouched and renders a scope alongside it, so it can
be dropped anywhere in a chain without changing what comes out. Wire the scope
output into a Preview Image to watch, or into a Save Image to keep it next to
the frame it measured.
"""

from __future__ import annotations

import torch
from comfy_api.latest import io

from ..scopes import SCOPE_MODES, render_scope


class PW_Scopes(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="PW_Scopes",
            display_name="PW Scopes",
            category="PW Color",
            search_aliases=["scopes", "histogram", "waveform", "parade", "vectorscope", "analysis"],
            description=(
                "Histogram, waveform and RGB parade, rendered from the full-resolution image. "
                "Passes the image through untouched, so it can sit anywhere in a chain."
            ),
            inputs=[
                io.Image.Input("image"),
                io.Combo.Input(
                    "mode",
                    options=list(SCOPE_MODES),
                    default="all",
                    tooltip="parade is the fastest way to see a colour cast; waveform shows where in the frame the tones are.",
                ),
                io.Int.Input("width", default=512, min=64, max=4096, step=8, optional=True),
                io.Int.Input("height", default=256, min=48, max=4096, step=8, optional=True),
            ],
            outputs=[
                io.Image.Output(display_name="image"),
                io.Image.Output(display_name="scope"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, image: torch.Tensor, mode: str = "all", width: int = 512, height: int = 256) -> io.NodeOutput:
        from ..preview_server import store_for_node

        store_for_node(cls, image)
        return io.NodeOutput(image, render_scope(image, mode, width, height))


NODES = [PW_Scopes]
