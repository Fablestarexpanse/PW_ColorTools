"""PW Grain — procedural and plate-based film grain.

Render-only, and badged as such: grain has a spatial correlation length, which
is precisely what cannot be expressed in a colour lattice. It still emits a LOOK
so the stack stays inspectable, with ``lut_safe`` false so that PW Look I/O can
tell the user a ``.cube`` export will not include it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import torch
from comfy_api.latest import io

from ..grain import (
    DEFAULT_CHROMA,
    GRAIN_BLEND_MODES,
    TonalResponse,
    apply_dither,
    apply_grain,
    plate_field,
    procedural_field,
)
from ..paths import GRAIN_DIR
from ..preview_server import store_input_for_node, store_output_for_node
from ..types import Look, LookOp

PLATES_DIR = GRAIN_DIR
PLATE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")


@lru_cache(maxsize=1)
def plate_names() -> tuple[str, ...]:
    """Grain plates shipped in ``grain/``, plus 'none'.

    Cached: this runs during schema construction, which the frontend hits often.
    A user adding a plate needs a ComfyUI restart, which is the same contract as
    adding a checkpoint.
    """
    if not PLATES_DIR.is_dir():
        return ("none",)
    names = sorted(p.name for p in PLATES_DIR.iterdir() if p.suffix.lower() in PLATE_SUFFIXES)
    return ("none", *names)


def _load_plate(name: str) -> torch.Tensor:
    """Load a shipped plate as ``[1,H,W,3]`` in sRGB-encoded [0,1].

    ``name`` is checked against the list the combo offers rather than joined
    onto PLATES_DIR as given. It arrives from a widget, and a widget value comes
    back from whatever is in the saved workflow JSON — the sibling loaders in
    look_io and palette_io both sanitise for the same reason.
    """
    if name not in plate_names():
        raise ValueError(f"PW Grain: {name!r} is not a shipped grain plate")
    # Pillow and numpy are ComfyUI runtime dependencies rather than ours, and
    # only the rendering paths need them. Deferred so importing the pack stays
    # cheap and a colour-only use never touches them.
    import numpy as np
    from PIL import Image

    path = PLATES_DIR / name
    if not path.is_file():
        raise ValueError(f"PW Grain: grain plate {name!r} not found in {PLATES_DIR}")
    try:
        with Image.open(path) as im:
            arr = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    except OSError as exc:
        # Pillow raises OSError for a truncated or unrecognised image. Every
        # other failure this node can produce is a ValueError with its name on
        # it, and a bare OSError in the ComfyUI log says nothing about grain.
        raise ValueError(f"PW Grain: could not read grain plate {name!r} ({exc})") from exc
    return torch.from_numpy(arr).unsqueeze(0)


class PW_Grain(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="PW_Grain",
            display_name="PW Grain",
            category="PW Color",
            search_aliases=["grain", "film grain", "noise", "dither", "banding"],
            description=(
                "Film grain with tonal response: strongest in the midtones, absent in pure "
                "black and white. Grain size is absolute, so 1.4px stays 1.4px at any "
                "resolution. Includes an always-on dither floor that removes 8-bit banding "
                "in skies and soft gradients even at zero grain."
            ),
            inputs=[
                io.Image.Input("image"),
                io.Float.Input(
                    "amount",
                    default=0.05,
                    min=0.0,
                    max=1.0,
                    step=0.005,
                    tooltip="Overall grain strength. 0.03-0.08 is a normal range.",
                    display_mode=io.NumberDisplay.slider,
                ),
                io.Float.Input(
                    "size",
                    default=1.4,
                    min=0.5,
                    max=8.0,
                    step=0.1,
                    tooltip=(
                        "Grain diameter in output pixels, measured at half maximum. Absolute: "
                        "1.4 looks the same at 1024 and at 4096."
                    ),
                    display_mode=io.NumberDisplay.slider,
                ),
                io.Float.Input("shadows", default=0.20, min=0.0, max=2.0, step=0.01, tooltip="Grain weight in the shadows.", display_mode=io.NumberDisplay.slider),
                io.Float.Input("midtones", default=1.00, min=0.0, max=2.0, step=0.01, tooltip="Grain weight in the midtones.", display_mode=io.NumberDisplay.slider),
                io.Float.Input("highlights", default=0.10, min=0.0, max=2.0, step=0.01, tooltip="Grain weight in the highlights.", display_mode=io.NumberDisplay.slider),
                io.Combo.Input("blend", options=list(GRAIN_BLEND_MODES), default="overlay"),
                io.Float.Input("opacity", default=1.0, min=0.0, max=1.0, step=0.01, display_mode=io.NumberDisplay.slider),
                io.Int.Input("seed", default=0, min=0, max=0xFFFFFFFF, control_after_generate=True),
                io.Boolean.Input(
                    "vary_per_frame",
                    default=False,
                    tooltip="Offset the seed by batch index, so a batch is not identically grained.",
                ),
                io.Float.Input(
                    "chroma",
                    default=DEFAULT_CHROMA,
                    min=0.0,
                    max=1.0,
                    step=0.05,
                    optional=True,
                    tooltip=(
                        "How coloured procedural grain is. 0 is pure luminance grain; 1 is fully "
                        "independent channels, which reads as digital sensor noise rather than "
                        "film. Ignored when a plate is used."
                    ),
                    display_mode=io.NumberDisplay.slider,
                ),
                io.Combo.Input(
                    "plate",
                    options=list(plate_names()),
                    default="none",
                    optional=True,
                    tooltip="A scanned grain plate from the grain/ folder. Overrides procedural grain.",
                ),
                io.Image.Input(
                    "plate_image",
                    optional=True,
                    tooltip=(
                        "A grain plate from the graph, overriding the folder choice. A batch here "
                        "maps to output batch index, so an image-sequence loader gives per-frame plates."
                    ),
                ),
                io.Float.Input("red", default=1.0, min=0.0, max=2.0, step=0.05, optional=True, display_mode=io.NumberDisplay.slider),
                io.Float.Input("green", default=1.0, min=0.0, max=2.0, step=0.05, optional=True, display_mode=io.NumberDisplay.slider),
                io.Float.Input(
                    "blue",
                    default=1.15,
                    min=0.0,
                    max=2.0,
                    step=0.05,
                    optional=True,
                    tooltip="Defaults hotter than red and green, matching real stock.",
                    display_mode=io.NumberDisplay.slider,
                ),
                io.Float.Input(
                    "dither",
                    default=1.0,
                    min=0.0,
                    max=4.0,
                    step=0.25,
                    optional=True,
                    tooltip=(
                        "Sub-LSB noise added before 8-bit quantisation, in code values. Kills "
                        "banding in skies. Leave at 1.0 unless you have a reason."
                    ),
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
        amount: float = 0.05,
        size: float = 1.4,
        shadows: float = 0.20,
        midtones: float = 1.00,
        highlights: float = 0.10,
        blend: str = "overlay",
        opacity: float = 1.0,
        seed: int = 0,
        vary_per_frame: bool = False,
        chroma: float = DEFAULT_CHROMA,
        plate: str = "none",
        plate_image: torch.Tensor | None = None,
        red: float = 1.0,
        green: float = 1.0,
        blue: float = 1.15,
        dither: float = 1.0,
        look_in: dict | None = None,
    ) -> io.NodeOutput:
        store_input_for_node(cls, image)

        b, h, w = image.shape[0], image.shape[1], image.shape[2]
        tonal = TonalResponse(shadows, midtones, highlights)

        if plate_image is not None:
            source = "plate_image"
        elif plate and plate != "none":
            source = plate
        else:
            source = "procedural"

        out = image
        if amount > 0.0 and opacity > 0.0:
            # Built here rather than above: a full-resolution noise field is the
            # expensive part of this node, and at amount 0 nothing consumes it.
            if plate_image is not None:
                field = plate_field(plate_image, batch=b, height=h, width=w, seed=seed, vary_per_frame=vary_per_frame)
            elif source != "procedural":
                field = plate_field(
                    _load_plate(plate), batch=b, height=h, width=w, seed=seed, vary_per_frame=vary_per_frame
                )
            else:
                field = procedural_field(
                    batch=b, height=h, width=w, size=size, seed=seed,
                    vary_per_frame=vary_per_frame, device=image.device, chroma=chroma,
                )
            out = apply_grain(
                out,
                field,
                tonal,
                amount=amount,
                channel_amounts=(red, green, blue),
                blend=blend,
                opacity=opacity,
            )

        # Always on, including at zero grain — this is the anti-banding floor.
        out = apply_dither(out, seed, strength=dither)

        op = LookOp(
            type="grain",
            params={
                "source": source,
                "amount": float(amount),
                "size": float(size),
                "shadows": float(shadows),
                "midtones": float(midtones),
                "highlights": float(highlights),
                "channels": [float(red), float(green), float(blue)],
                "seed": int(seed),
                "vary_per_frame": bool(vary_per_frame),
                "dither": float(dither),
            },
            strength=float(opacity),
            blend="normal",
            # Spatial. Cannot be represented in a lattice, and PW Look I/O uses
            # this to warn that a .cube export will not include it.
            lut_safe=False,
        )
        # Grain is spatial, so the browser cannot reproduce it. Caching what we
        # actually produced is the only preview that tells the truth.
        store_output_for_node(cls, out)

        look = Look.from_dict(look_in) if look_in else Look()
        return io.NodeOutput(out, look.appended(op).to_dict())


NODES = [PW_Grain]
