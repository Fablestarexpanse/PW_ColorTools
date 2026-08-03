"""PW Parity probe — the Phase 0 gate, in node form.

Throwaway. Delete it once PW_Curves ships; it exists only to prove, inside a
running ComfyUI rather than in a test harness, that the lattice the browser
bakes and the lattice Python bakes are the same bytes, and that applying either
gives the same pixels.

How to read the report output:

* ``build`` — did the browser's lattice and Python's lattice come out identical
  after u16 transport? If this says MISMATCH, the TS and Python op
  implementations have drifted and every downstream node is suspect.
* ``apply`` — largest difference, in 8-bit code values, between rendering with
  the browser's lattice and rendering with Python's. Must be 0.
* ``bake`` — what the 33³ lattice costs against evaluating the ops per pixel.
  Not a parity number; a quality budget. Under 0.5 codes means the bake is free.
"""

from __future__ import annotations

import json

import torch
from comfy_api.latest import io

from ..lattice import DEFAULT_SIZE, FINAL_SIZE, Lattice
from ..ops import build_sample_fn
from ..types import Look

_DEFAULT_LOOK = json.dumps(
    {
        "schema": 1,
        "name": "parity probe",
        "ops": [
            {"type": "exposure", "params": {"stops": 0.35}},
            {"type": "contrast", "params": {"amount": 0.42}},
            {
                "type": "curves",
                "params": {
                    "luma": [[0.0, 0.05], [0.28, 0.22], [0.72, 0.81], [1.0, 0.97]],
                    "preserve_hue": True,
                },
            },
            {"type": "saturation", "params": {"amount": 1.18}, "strength": 0.8},
        ],
        "meta": {},
    },
    indent=2,
)


class PW_ParityProbe(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="PW_ParityProbe",
            display_name="PW Parity probe",
            category="PW Color/dev",
            description=(
                "Development only. Bakes a LOOK into a lattice in Python, compares it "
                "against the lattice the browser baked from the same LOOK, and reports "
                "whether preview and render agree."
            ),
            is_experimental=True,
            inputs=[
                io.Image.Input("image"),
                io.String.Input(
                    "look_json",
                    multiline=True,
                    default=_DEFAULT_LOOK,
                    tooltip="A LOOK document. Both sides bake this.",
                ),
                io.String.Input(
                    "js_lattice",
                    multiline=True,
                    default="",
                    optional=True,
                    tooltip="Lattice transport JSON written by the browser. Leave empty to test Python alone.",
                ),
                io.Boolean.Input("final_quality", default=False, tooltip=f"Use {FINAL_SIZE}³ instead of {DEFAULT_SIZE}³."),
            ],
            outputs=[
                io.Image.Output(display_name="image"),
                io.Custom("LOOK").Output(display_name="look"),
                io.String.Output(display_name="report"),
            ],
        )

    @classmethod
    def execute(cls, image: torch.Tensor, look_json: str, js_lattice: str = "", final_quality: bool = False) -> io.NodeOutput:
        look = Look.from_json(look_json)
        size = FINAL_SIZE if final_quality else DEFAULT_SIZE

        sample_fn = build_sample_fn([op.to_dict() for op in look.ops])
        # from_fn bakes in float64 and quantises on construction, so this is
        # already the exact lattice the browser holds.
        py_lattice = Lattice.from_fn(sample_fn, size)
        py_transport = py_lattice.to_transport("u16")
        applied = py_lattice
        out = applied.apply(image)

        lines = [
            f"size      {size}³   ops {len(look.ops)}   lut-exportable {look.lut_exportable}",
            f"python    digest {py_lattice.digest()}",
        ]

        if js_lattice.strip():
            js_transport = json.loads(js_lattice)
            js = Lattice.from_transport(js_transport)
            same_bytes = js_transport.get("data") == py_transport["data"]
            lines.append(f"browser   digest {js.digest()}")
            lines.append(f"build     {'OK — byte-identical' if same_bytes else 'MISMATCH — TS and Python have drifted'}")
            if js.size == applied.size:
                js_out = js.apply(image)
                delta = _codes_8bit(js_out, out)
                lines.append(f"apply     {delta} code values of drift at 8-bit {'(OK)' if delta == 0 else '(FAIL)'}")
            else:
                lines.append(f"apply     skipped — browser baked {js.size}³, python baked {size}³")
        else:
            lines.append("browser   no lattice supplied — python-only run")

        bake_err = _bake_cost(sample_fn, applied)
        note = "(free)" if bake_err < 0.5 else "(this look leaves the sRGB gamut — see docs)"
        lines.append(f"bake      {bake_err:.3f} code values at 8-bit {note}")

        return io.NodeOutput(out, look.to_dict(), "\n".join(lines))


def _codes_8bit(a: torch.Tensor, b: torch.Tensor) -> int:
    qa = (a.clamp(0, 1) * 255.0 + 0.5).floor().to(torch.int32)
    qb = (b.clamp(0, 1) * 255.0 + 0.5).floor().to(torch.int32)
    return int((qa - qb).abs().max().item())


def _bake_cost(sample_fn, lattice: Lattice) -> float:
    """Max error, in 8-bit codes, of the lattice against direct evaluation.

    Fixed seed so the number is comparable between runs — a drifting quality
    metric is worse than no metric.
    """
    g = torch.Generator().manual_seed(11)
    pts = torch.rand(20000, 3, generator=g)
    return float((lattice.apply_points(pts) - sample_fn(pts).to(torch.float32)).abs().max().item() * 255.0)


NODES = [PW_ParityProbe]
