"""PW Look — the main grade panel.

Everything a user reaches for first, in one node and in plain language:
exposure, contrast, highlights, shadows, whites, blacks, warmth, tint,
vibrance, saturation, glow. Plus an eight-band HSL mixer, a gradient map that
can take a PALETTE, and reference matching.

The pipeline, in order, and the order matters:

1. **Reference match** — normalises the image toward a reference before any
   creative decision. Image-dependent, so it is not LUT-exportable.
2. **The lattice** — tone, colour, HSL and gradient map bake into a single 3D
   LUT, which is why the preview is exact and why the grade exports to ``.cube``.
3. **Glow** — spatial, so it cannot be baked. This is the one control on the
   node that breaks LUT export, and the UI badges it.
4. **Mask** — restricts everything above to a region. White means graded.
5. **Master strength and blend** — composite the whole grade over the input.
"""

from __future__ import annotations

import json

import torch
from comfy_api.latest import io

from ..blend import BLEND_MODES, composite
from ..glow import apply_glow
from ..lattice import DEFAULT_SIZE, FINAL_SIZE, Lattice
from ..look import HSL_BANDS, ramp_from_palette
from ..match import MATCH_TIERS, match_least_squares, match_mean_std
from ..ops import build_sample_fn
from ..paths import LOOK_PRESETS
from ..presets import preset_name, resolve_preset
from ..presets import preset_ids as _preset_ids
from ..types import Look, LookOp, Palette

GRADIENT_BLENDS = ("colour", "normal", "soft light", "overlay", "multiply", "screen")


def preset_ids() -> list[str]:
    return _preset_ids(LOOK_PRESETS)


def _empty_hsl() -> dict[str, dict[str, float]]:
    return {name: {"hue": 0.0, "sat": 0.0, "lum": 0.0} for name, _ in HSL_BANDS}


class PW_Look(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="PW_Look",
            display_name="PW Look",
            category="PW Color",
            search_aliases=["look", "grade", "colour grade", "color grade", "lightroom", "tone", "hsl"],
            description=(
                "The main grade panel: exposure, contrast, highlights, shadows, whites, blacks, "
                "warmth, tint, vibrance, saturation and glow, plus an 8-band HSL mixer and a "
                "gradient map. Everything except glow bakes into a single LUT, so the preview is "
                "exact and the grade exports to .cube."
            ),
            inputs=[
                io.Image.Input("image"),
                io.Combo.Input(
                    "preset",
                    options=preset_ids(),
                    default="none",
                    tooltip="Presets lead, sliders follow. Choosing one replaces the controls below.",
                ),
                # -- light --
                io.Float.Input("exposure", default=0.0, min=-4.0, max=4.0, step=0.01, tooltip="Stops, applied in linear light.", display_mode=io.NumberDisplay.slider),
                io.Float.Input("contrast", default=0.0, min=-1.0, max=1.0, step=0.01, display_mode=io.NumberDisplay.slider),
                io.Float.Input("highlights", default=0.0, min=-1.0, max=1.0, step=0.01, display_mode=io.NumberDisplay.slider),
                io.Float.Input("shadows", default=0.0, min=-1.0, max=1.0, step=0.01, display_mode=io.NumberDisplay.slider),
                io.Float.Input("whites", default=0.0, min=-1.0, max=1.0, step=0.01, display_mode=io.NumberDisplay.slider),
                io.Float.Input("blacks", default=0.0, min=-1.0, max=1.0, step=0.01, display_mode=io.NumberDisplay.slider),
                # -- colour --
                io.Float.Input("warmth", default=0.0, min=-1.0, max=1.0, step=0.01, tooltip="Blue to yellow.", display_mode=io.NumberDisplay.slider),
                io.Float.Input("tint", default=0.0, min=-1.0, max=1.0, step=0.01, tooltip="Green to magenta.", display_mode=io.NumberDisplay.slider),
                io.Float.Input(
                    "vibrance",
                    default=0.0,
                    min=-1.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Lifts muted colour more than colour that is already saturated.",
                    display_mode=io.NumberDisplay.slider,
                ),
                io.Float.Input("saturation", default=1.0, min=0.0, max=2.0, step=0.01, display_mode=io.NumberDisplay.slider),
                # -- glow (spatial) --
                io.Float.Input(
                    "glow",
                    default=0.0,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip=(
                        "Highlight bloom, in linear light. The only control here that is not "
                        "LUT-exportable: anything above zero means a .cube export cannot carry "
                        "the whole look."
                    ),
                    display_mode=io.NumberDisplay.slider,
                ),
                io.Float.Input("glow_radius", default=24.0, min=1.0, max=200.0, step=1.0, optional=True, display_mode=io.NumberDisplay.slider),
                io.Float.Input("glow_threshold", default=0.65, min=0.0, max=1.0, step=0.01, optional=True, display_mode=io.NumberDisplay.slider),
                # -- master --
                io.Float.Input("strength", default=1.0, min=0.0, max=1.0, step=0.01, tooltip="Blend the whole grade toward the input.", display_mode=io.NumberDisplay.slider),
                io.Combo.Input("blend", options=list(BLEND_MODES), default="normal", optional=True),
                # -- structured / wired --
                io.String.Input(
                    "hsl",
                    multiline=True,
                    default="{}",
                    optional=True,
                    tooltip="Eight-band HSL mixer, written by the node's UI.",
                ),
                io.Float.Input("gradient_map", default=0.0, min=0.0, max=1.0, step=0.01, optional=True, display_mode=io.NumberDisplay.slider),
                io.Combo.Input("gradient_blend", options=list(GRADIENT_BLENDS), default="colour", optional=True),
                io.Custom("PALETTE").Input(
                    "palette",
                    optional=True,
                    tooltip="Builds the gradient map ramp automatically, ordered dark to light.",
                ),
                io.Mask.Input("mask", optional=True, tooltip="Restrict the grade to a region. White is graded."),
                io.Image.Input("reference", optional=True, tooltip="Match this image's colour before grading."),
                io.Float.Input("reference_strength", default=1.0, min=0.0, max=1.0, step=0.01, optional=True, display_mode=io.NumberDisplay.slider),
                io.Combo.Input(
                    "reference_mode",
                    options=list(MATCH_TIERS),
                    default="mean_std",
                    optional=True,
                    tooltip=(
                        "mean_std matches each channel's average and contrast: predictable, and "
                        "enough for most references. least_squares fits a tone curve plus a full "
                        "3x3 matrix, so it can reproduce cross-channel looks like teal shadows "
                        "against neutral highlights, at the cost of being able to overfit when "
                        "the two images have very different content."
                    ),
                ),
                io.Combo.Input(
                    "quality",
                    options=["high", "fast"],
                    default="high",
                    optional=True,
                    tooltip=(
                        f"Lattice resolution. high is {FINAL_SIZE}³, fast is {DEFAULT_SIZE}³. "
                        "Unlike PW Curves this defaults to high: a full grade stacks several "
                        "chroma ops, and measured at 33³ that costs about 14 code values in "
                        "saturated areas versus 5 at 65³. The extra bake is around 30 ms."
                    ),
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
        preset: str = "none",
        exposure: float = 0.0,
        contrast: float = 0.0,
        highlights: float = 0.0,
        shadows: float = 0.0,
        whites: float = 0.0,
        blacks: float = 0.0,
        warmth: float = 0.0,
        tint: float = 0.0,
        vibrance: float = 0.0,
        saturation: float = 1.0,
        glow: float = 0.0,
        glow_radius: float = 24.0,
        glow_threshold: float = 0.65,
        strength: float = 1.0,
        blend: str = "normal",
        hsl: str = "{}",
        gradient_map: float = 0.0,
        gradient_blend: str = "colour",
        palette: dict | None = None,
        mask: torch.Tensor | None = None,
        reference: torch.Tensor | None = None,
        reference_strength: float = 1.0,
        reference_mode: str = "mean_std",
        quality: str = "high",
        look_in: dict | None = None,
    ) -> io.NodeOutput:
        from ..preview_server import store_for_node

        store_for_node(cls, image)

        params = resolve_preset(LOOK_PRESETS, preset, "PW Look").get("params", {})

        def val(name: str, widget: float, default: float) -> float:
            """Presets lead, sliders follow.

            The preset supplies the value while the slider is untouched; the
            moment the user moves that slider, their hand wins. This is the only
            precedence rule on the node — including for the gradient map, which
            used to have its own.
            """
            if name in params and widget == default:
                return float(params[name])
            return float(widget)

        # -- 1. reference match -------------------------------------------
        out = image
        ops: list[LookOp] = []
        ref_strength = float(reference_strength)
        if reference is not None and ref_strength > 0.0:
            if reference_mode == "least_squares":
                out = match_least_squares(out, reference, mask=None, strength=ref_strength)
            else:
                out = match_mean_std(out, reference, mask=None, strength=ref_strength, space="oklab")
            ops.append(
                LookOp(
                    type="reference_match",
                    params={"space": "oklab", "mode": reference_mode},
                    strength=ref_strength,
                    lut_safe=False,  # depends on this specific pair of images
                )
            )

        # -- 2. the lattice ------------------------------------------------
        tone = {
            "exposure": val("exposure", exposure, 0.0),
            "contrast": val("contrast", contrast, 0.0),
            "highlights": val("highlights", highlights, 0.0),
            "shadows": val("shadows", shadows, 0.0),
            "whites": val("whites", whites, 0.0),
            "blacks": val("blacks", blacks, 0.0),
        }
        colour_p = {
            "warmth": val("warmth", warmth, 0.0),
            "tint": val("tint", tint, 0.0),
            "vibrance": val("vibrance", vibrance, 0.0),
            "saturation": val("saturation", saturation, 1.0),
        }

        hsl_bands = _empty_hsl()
        try:
            raw_hsl = json.loads(hsl or "{}")
        except ValueError as exc:
            raise ValueError(f"PW Look: could not read the HSL mixer data ({exc}). Reset the node to recover.") from exc
        for name, band in (params.get("hsl") or {}).items():
            if name in hsl_bands:
                hsl_bands[name].update({k: float(v) for k, v in band.items()})
        for name, band in raw_hsl.items():
            if name in hsl_bands and isinstance(band, dict):
                hsl_bands[name].update({k: float(v) for k, v in band.items() if k in ("hue", "sat", "lum")})

        # The preset spells the gradient map with a prefix, because "amount"
        # and "blend" on their own would collide with the master controls.
        grad_amount = val("gradient_map_amount", gradient_map, 0.0)
        grad_blend = gradient_blend
        if "gradient_map_amount" in params and gradient_map == 0.0:
            grad_blend = params.get("gradient_map_blend", grad_blend)
        grad_stops = params.get("gradient_map_stops")
        if palette:
            # A wired palette wins: it is the more deliberate input.
            grad_stops = ramp_from_palette([c.hex for c in Palette.from_dict(palette).colors])

        lattice_ops = [
            LookOp(type="tone", params=tone),
            LookOp(type="colour", params=colour_p),
            LookOp(type="hsl", params={"bands": hsl_bands}),
            LookOp(
                type="gradient_map",
                params={"amount": grad_amount, "blend": grad_blend, "stops": grad_stops or []},
            ),
        ]
        size = DEFAULT_SIZE if quality == "fast" else FINAL_SIZE
        lattice = Lattice.from_fn(build_sample_fn([o.to_dict() for o in lattice_ops]), size)
        graded = lattice.apply(out)
        ops.extend(lattice_ops)

        # -- 3. glow (spatial) ---------------------------------------------
        glow_amount = val("glow", glow, 0.0)
        if glow_amount > 0.0:
            glow_params = {
                "amount": glow_amount,
                "radius": val("glow_radius", glow_radius, 24.0),
                "threshold": val("glow_threshold", glow_threshold, 0.65),
            }
            graded = apply_glow(
                graded,
                amount=glow_params["amount"],
                radius=glow_params["radius"],
                threshold=glow_params["threshold"],
            )
            ops.append(LookOp(type="glow", params=glow_params, lut_safe=False))  # spatial

        # -- 4. mask --------------------------------------------------------
        if mask is not None:
            m = mask
            if m.ndim == 2:
                m = m.unsqueeze(0)
            if m.shape[-2:] != image.shape[1:3]:
                raise ValueError(f"PW Look: mask {tuple(m.shape[-2:])} does not match image {tuple(image.shape[1:3])}")
            m = m.clamp(0.0, 1.0).unsqueeze(-1).to(graded.device)
            graded = torch.lerp(out[..., :3], graded[..., :3], m)

        # -- 5. master strength and blend ------------------------------------
        result = composite(out[..., :3], graded[..., :3], blend, float(strength))
        if image.shape[-1] == 4:
            result = torch.cat((result, image[..., 3:]), dim=-1)

        look = Look.from_dict(look_in) if look_in else Look()
        for op in ops:
            look = look.appended(op)
        name = preset_name(LOOK_PRESETS, preset)
        if name:
            look.name = name
        return io.NodeOutput(result, look.to_dict())


NODES = [PW_Look]
