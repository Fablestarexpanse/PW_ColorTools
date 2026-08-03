"""PW Palette — extract, display and lock a colour palette.

Small node, ships on its own, and the swatch strip is a genuine shareable
output. The interesting parts are all in `pw_color/palette.py`: OKLab
clustering, and determinism strict enough that the same image gives a
byte-identical palette every run.
"""

from __future__ import annotations

import torch
from comfy_api.latest import io

from ..palette import SORT_MODES, extract_palette
from ..palette_io import PALETTE_FORMATS, list_saved, load_palette, save_palette
from ..swatch_strip import render_strip
from ..types import Palette


class PW_Palette(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="PW_Palette",
            display_name="PW Palette",
            category="PW Color",
            search_aliases=["palette", "colour palette", "color palette", "swatches", "kmeans", "dominant colours"],
            description=(
                "Extracts a colour palette by k-means clustering in OKLab, so the clusters "
                "land where the eye sees difference rather than where RGB does. Outputs a "
                "PALETTE, a hex string and a rendered swatch strip. Deterministic: the same "
                "image gives the same palette every run."
            ),
            inputs=[
                io.Image.Input("image"),
                io.Int.Input("count", default=5, min=1, max=16, tooltip="How many colours to extract."),
                io.Mask.Input(
                    "mask",
                    optional=True,
                    tooltip="Restrict extraction to a region — the palette of a character rather than the whole frame.",
                ),
                io.Combo.Input("sort", options=list(SORT_MODES), default="coverage"),
                io.Boolean.Input(
                    "ignore_near_black",
                    default=True,
                    tooltip="Without this a night scene returns five blacks: the clusters go where the pixels are.",
                ),
                io.Boolean.Input(
                    "ignore_near_white",
                    default=True,
                    tooltip="The same problem at the other end — snow, overexposure, white backgrounds.",
                ),
                io.Boolean.Input(
                    "weight_by_chroma",
                    default=False,
                    tooltip=(
                        "Give saturated pixels more pull, so a 2% accent red gets its own colour "
                        "instead of being absorbed. Does not change the reported coverage, which "
                        "stays a true pixel fraction."
                    ),
                ),
                io.Int.Input("seed", default=0, min=0, max=0xFFFFFFFF, optional=True),
                io.Int.Input("strip_width", default=1024, min=64, max=4096, step=8, optional=True),
                io.Int.Input("strip_height", default=200, min=32, max=1024, step=8, optional=True),
                io.Boolean.Input("strip_labels", default=True, optional=True, tooltip="Draw hex and coverage under each swatch."),
                io.String.Input(
                    "save_as",
                    default="",
                    optional=True,
                    tooltip=(
                        "Filename to save this palette under, in ComfyUI's output/palettes "
                        "folder. Leave empty to not save. Saved on every run, so use a fixed "
                        "name to keep one file rather than accumulating."
                    ),
                ),
                io.Combo.Input(
                    "save_format",
                    options=list(PALETTE_FORMATS),
                    default="json",
                    optional=True,
                    tooltip=(
                        "json reopens here and keeps coverage. ase goes to Photoshop, "
                        "Illustrator and Affinity. gpl goes to GIMP, Krita, Inkscape and "
                        "Aseprite. txt is one hex per line."
                    ),
                ),
                io.Combo.Input(
                    "load",
                    options=["none", *list_saved()],
                    default="none",
                    optional=True,
                    tooltip=(
                        "Load a previously saved palette instead of extracting. Refresh the "
                        "browser after saving for a new file to appear here."
                    ),
                ),
                io.String.Input(
                    "locked",
                    multiline=True,
                    default="",
                    optional=True,
                    tooltip=(
                        "A locked palette, written by the node's 'lock as target' action. "
                        "When set, it is passed through unchanged instead of extracting."
                    ),
                ),
            ],
            outputs=[
                io.Custom("PALETTE").Output(display_name="palette"),
                io.String.Output(display_name="hex"),
                io.Image.Output(display_name="swatches"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls,
        image: torch.Tensor,
        count: int = 5,
        mask: torch.Tensor | None = None,
        sort: str = "coverage",
        ignore_near_black: bool = True,
        ignore_near_white: bool = True,
        weight_by_chroma: bool = False,
        seed: int = 0,
        strip_width: int = 1024,
        strip_height: int = 200,
        strip_labels: bool = True,
        save_as: str = "",
        save_format: str = "json",
        load: str = "none",
        locked: str = "",
    ) -> io.NodeOutput:
        # Precedence, most deliberate first: an explicitly loaded file beats a
        # lock, and a lock beats extracting from the image. Anything else would
        # mean a user loads a palette and silently gets a different one.
        if load and load != "none":
            palette = load_palette(load)
        elif locked.strip():
            palette = Palette.from_json(locked)
        else:
            palette = extract_palette(
                image,
                count=count,
                mask=mask,
                seed=seed,
                ignore_near_black=ignore_near_black,
                ignore_near_white=ignore_near_white,
                weight_by_chroma=weight_by_chroma,
                sort=sort,
            )

        try:
            from ..preview_server import store

            store(str(cls.hidden.unique_id), image)
        except Exception:  # pragma: no cover
            pass

        saved = ""
        if save_as.strip():
            saved = str(save_palette(palette, save_as, save_format))

        strip = render_strip(palette, width=strip_width, height=strip_height, show_labels=strip_labels)
        # The palette also goes back as UI data so the node can draw its own
        # swatch strip. Return values are not delivered to the frontend; the
        # execution message is the only channel, and reusing the standard one
        # means no bespoke websocket handling.
        return io.NodeOutput(
            palette.to_dict(),
            palette.hex_string(),
            strip,
            ui={"pw_palette": [palette.to_json()], "pw_saved": [saved]},
        )


NODES = [PW_Palette]
