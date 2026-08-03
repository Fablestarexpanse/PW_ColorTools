"""PW Match Source — repair VAE round-trip colour drift.

The problem it solves: every encode/decode through a VAE shifts colour slightly.
On a full-frame img2img nobody notices, because the whole frame shifts together.
On an inpaint or outpaint you composite the decoded region back over pixels that
never went through the VAE, and the shift becomes a visible seam — usually a
faint warm or magenta patch with a hard edge exactly on the mask boundary.

This node measures the drift where it can be measured (the region the model did
*not* paint, which exists in both images) and applies the same correction to the
whole frame, including the part where it could not be measured.

Render-only, not LUT-exportable: the correction depends on the statistics of
this specific pair of images, so there is no fixed transform to bake. It still
emits a LOOK so the stack stays inspectable downstream.
"""

from __future__ import annotations

import torch
from comfy_api.latest import io

from ..match import MATCH_SPACES, match_mean_std
from ..types import Look, LookOp


class PW_MatchSource(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="PW_MatchSource",
            display_name="PW Match source",
            category="PW Color",
            search_aliases=["colour match", "color match", "vae drift", "inpaint seam", "match source"],
            description=(
                "Removes the colour drift a VAE encode/decode introduces, so an inpaint or "
                "img2img round trip composites back without a seam. Measures the shift on the "
                "region that was not regenerated and applies it to the whole frame."
            ),
            inputs=[
                io.Image.Input("original", tooltip="The image before it went through the VAE."),
                io.Image.Input("processed", tooltip="The image after. Must be the same size."),
                io.Mask.Input(
                    "mask",
                    optional=True,
                    tooltip=(
                        "The inpaint mask: white where the model painted. Statistics are taken "
                        "from the black region, which both images share. Without a mask the whole "
                        "frame is used, which is what you want for plain img2img."
                    ),
                ),
                io.Float.Input(
                    "strength",
                    default=1.0,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Blend toward the uncorrected image. 1.0 is a full correction.",
                    display_mode=io.NumberDisplay.slider,
                ),
                io.Combo.Input(
                    "space",
                    options=list(MATCH_SPACES),
                    default="oklab",
                    tooltip=(
                        "Where the statistics are measured. oklab is perceptual and the right "
                        "default; linear is closer to what the VAE actually did; srgb is there "
                        "for parity with other packs."
                    ),
                ),
                io.Float.Input(
                    "max_gain",
                    default=4.0,
                    min=1.0,
                    max=16.0,
                    step=0.5,
                    optional=True,
                    tooltip=(
                        "Ceiling on the per-channel contrast correction. Guards against a flat "
                        "sampled region — a clear sky — producing an enormous gain."
                    ),
                    display_mode=io.NumberDisplay.slider,
                ),
            ],
            outputs=[
                io.Image.Output(display_name="image"),
                io.Custom("LOOK").Output(display_name="look"),
            ],
        )

    @classmethod
    def execute(
        cls,
        original: torch.Tensor,
        processed: torch.Tensor,
        mask: torch.Tensor | None = None,
        strength: float = 1.0,
        space: str = "oklab",
        max_gain: float = 4.0,
    ) -> io.NodeOutput:
        if mask is not None and mask.ndim == 2:
            mask = mask.unsqueeze(0)
        # Batch broadcast: a single original against a batch of processed frames
        # is the common case when you sample several variations of one inpaint.
        if original.shape[0] == 1 and processed.shape[0] > 1:
            original = original.expand(processed.shape[0], -1, -1, -1)
        if mask is not None and mask.shape[0] == 1 and processed.shape[0] > 1:
            mask = mask.expand(processed.shape[0], -1, -1)

        out = match_mean_std(
            processed=processed,
            original=original,
            mask=mask,
            strength=strength,
            space=space,
            max_gain=max_gain,
        )

        look = Look(
            name="match source",
            ops=[
                LookOp(
                    type="match_source",
                    params={"space": space, "max_gain": float(max_gain), "masked": mask is not None},
                    strength=float(strength),
                    # Image-dependent, so there is no fixed transform to bake.
                    lut_safe=False,
                )
            ],
        )
        return io.NodeOutput(out, look.to_dict())


NODES = [PW_MatchSource]
