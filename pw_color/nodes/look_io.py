"""PW Look I/O — save, load and bake a LOOK.

Three jobs in one node because they are the same job from different angles:
getting a grade out of the graph and back into it.

The part that matters is the honesty of the ``.cube`` export. A ``.cube`` can
only carry per-pixel operations, so grain, glow and reference matching cannot be
in it. Rather than writing a file that silently does less than the user's graph,
this node reports exactly what was included and what was dropped.
"""

from __future__ import annotations

from pathlib import Path

import torch
from comfy_api.latest import io

from ..lattice import DEFAULT_SIZE, FINAL_SIZE, Lattice
from ..look_io import bake_cube, export_report, list_saved, load_look, look_dir, safe_name, save_look
from ..ops import build_sample_fn
from ..types import Look


class PW_LookIO(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="PW_LookIO",
            display_name="PW Look I/O",
            category="PW Color",
            search_aliases=["look io", "save look", "load look", "cube", "lut export", "bake lut"],
            description=(
                "Saves a LOOK to disk, loads one back, and bakes it to an Adobe .cube. "
                "Reports exactly which operations the .cube carries and which it had to drop, "
                "because a LUT cannot hold grain, glow or reference matching."
            ),
            inputs=[
                io.Custom("LOOK").Input("look", optional=True, tooltip="The grade stack to save or bake."),
                io.Image.Input(
                    "image",
                    optional=True,
                    tooltip="Optional. If connected, the loaded or incoming look is applied to it.",
                ),
                io.String.Input(
                    "save_as",
                    default="",
                    optional=True,
                    tooltip="Filename for a .look in ComfyUI's output/looks folder. Empty to not save.",
                ),
                io.Combo.Input(
                    "load",
                    options=["none", *list_saved()],
                    default="none",
                    optional=True,
                    tooltip="Load a saved .look instead of using the input. Refresh the browser to see new files.",
                ),
                io.String.Input(
                    "export_cube",
                    default="",
                    optional=True,
                    tooltip="Filename for a .cube in output/looks. Empty to not export.",
                ),
                io.Combo.Input(
                    "cube_size",
                    options=["33", "65"],
                    default="33",
                    optional=True,
                    tooltip="LUT resolution. 33 is the near-universal default; 65 is more accurate on heavy grades.",
                ),
            ],
            outputs=[
                io.Custom("LOOK").Output(display_name="look"),
                io.Image.Output(display_name="image"),
                io.String.Output(display_name="report"),
            ],
        )

    @classmethod
    def execute(
        cls,
        look: dict | None = None,
        image: torch.Tensor | None = None,
        save_as: str = "",
        load: str = "none",
        export_cube: str = "",
        cube_size: str = "33",
    ) -> io.NodeOutput:
        # A loaded file wins over the wired input: it is the more deliberate act.
        if load and load != "none":
            doc = load_look(load)
            source = f"loaded {load}"
        elif look:
            doc = Look.from_dict(look)
            source = "input"
        else:
            doc = Look()
            source = "empty"

        lines = [f"source    {source}", f"ops       {len(doc.ops)}"]
        for op in doc.ops:
            flag = "lut" if op.lut_safe else "render only"
            state = "" if op.enabled else "  (disabled)"
            lines.append(f"  - {op.type:<16} {flag}{state}")

        if save_as.strip():
            lines.append(f"saved     {save_look(doc, save_as)}")

        if export_cube.strip():
            complete, included, dropped = export_report(doc)
            size = int(cube_size) if cube_size in ("33", "65") else DEFAULT_SIZE
            path = look_dir() / safe_name(export_cube, ".cube")
            path.write_text(bake_cube(doc, size=size, title=doc.name or Path(export_cube).stem), encoding="utf-8")
            lines.append(f"exported  {path}  ({size}³, {len(included)} ops)")
            if not complete:
                # The whole reason this node reports rather than just writing.
                lines.append(f"WARNING   the .cube does not include: {', '.join(dropped)}")
                lines.append("          those are spatial or image-dependent and cannot be a LUT")

        out_image = image
        if image is not None:
            lut_ops = [op.to_dict() for op in doc.ops if op.enabled and op.lut_safe]
            if lut_ops:
                size = FINAL_SIZE if cube_size == "65" else DEFAULT_SIZE
                out_image = Lattice.from_fn(build_sample_fn(lut_ops), size).apply(image)
            complete, _, dropped = export_report(doc)
            if not complete:
                lines.append(f"NOTE      applied image excludes: {', '.join(dropped)}")
        else:
            # An IMAGE output must always be a tensor; 1x1 black is the least
            # surprising placeholder and costs nothing downstream.
            out_image = torch.zeros(1, 1, 1, 3)

        return io.NodeOutput(doc.to_dict(), out_image, "\n".join(lines))


NODES = [PW_LookIO]
