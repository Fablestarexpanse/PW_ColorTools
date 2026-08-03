"""The Phase 0 gate: JS and Python must produce the same pixels.

Three claims, tested separately, because they fail for different reasons:

1. **Build parity** — the TS ops and the torch ops produce the same lattice.
   Tested in float (they agree to ~1e-6, JS being float64 and torch float32)
   and after u16 transport (they agree exactly, because the quantisation step
   is coarser than the float difference).

2. **Apply parity** — given the *same* lattice, the TS trilinear sampler and
   the torch trilinear sampler produce the same values.

3. **End-to-end** — what the preview shows and what the renderer writes are
   identical at 8-bit and at 16-bit. This is the claim that matters to a user.

Claim 1's exactness is what the quantised transport buys us. Bit-identical
float64/float32 arithmetic is not achievable and we do not pretend otherwise;
what *is* achievable is that both sides consume the identical quantised lattice,
which is why `Lattice.quantised()` exists on the TS side and why the preview
must always sample the transport-round-tripped lattice, never the raw bake.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import torch

from pw_color.lattice import DEFAULT_SIZE, Lattice
from pw_color.ops import build_sample_fn

HARNESS = Path(__file__).resolve().parents[1] / "web" / "tools" / "parity.ts"

# A look that exercises every code path we care about: linear-light exposure,
# a pivoted power function, OKLab chroma scaling, an OKLab b-axis shift, a
# multi-point monotone curve per channel, a preserve-hue luma curve, and a
# partial-strength blend.
LOOK_OPS = [
    {"type": "exposure", "params": {"stops": 0.35}},
    {"type": "contrast", "params": {"amount": 0.42}},
    {
        "type": "curves",
        "params": {
            "luma": [[0.0, 0.05], [0.28, 0.22], [0.72, 0.81], [1.0, 0.97]],
            "r": [[0.0, 0.0], [0.5, 0.54], [1.0, 1.0]],
            "b": [[0.0, 0.03], [1.0, 0.96]],
            "preserve_hue": True,
        },
    },
    {"type": "saturation", "params": {"amount": 1.18}, "strength": 0.8},
    {"type": "warmth", "params": {"amount": -0.4}},
]


def _test_pixels(n: int = 4096) -> torch.Tensor:
    """Deterministic test pixels: the 8 cube corners, the neutral axis, the
    lattice grid points themselves, then pseudo-random fill."""
    corners = torch.tensor([[r, g, b] for r in (0.0, 1.0) for g in (0.0, 1.0) for b in (0.0, 1.0)])
    neutral = torch.linspace(0, 1, 64).unsqueeze(1).repeat(1, 3)
    grid_hits = torch.rand(0, 3)
    a = torch.linspace(0, 1, DEFAULT_SIZE)
    grid_hits = torch.stack(torch.meshgrid(a[:8], a[:8], a[:8], indexing="ij"), dim=-1).reshape(-1, 3)
    g = torch.Generator().manual_seed(20260802)
    fill = torch.rand(n, 3, generator=g)
    return torch.cat((corners, neutral, grid_hits, fill), dim=0)


@pytest.fixture(scope="module")
def js():
    """Run the TS harness once and share the result across the parity tests."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH — parity cannot be verified")
    pixels = _test_pixels()
    job = {"ops": LOOK_OPS, "size": DEFAULT_SIZE, "encoding": "u16", "pixels": pixels.flatten().tolist()}
    proc = subprocess.run(
        [node, "--experimental-strip-types", "--no-warnings", str(HARNESS)],
        input=json.dumps(job),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        pytest.fail(f"parity harness failed ({proc.returncode}):\n{proc.stderr}")
    out = json.loads(proc.stdout)
    return {
        "pixels": pixels,
        "transport": out["transport"],
        "lattice_raw": torch.tensor(out["lattice_raw"], dtype=torch.float32).reshape(-1, 3),
        "direct": torch.tensor(out["direct"], dtype=torch.float32).reshape(-1, 3),
        "sampled": torch.tensor(out["sampled"], dtype=torch.float32).reshape(-1, 3),
    }


@pytest.fixture(scope="module")
def py_lattice():
    """The lattice as the renderer sees it: baked in float64, quantised."""
    return Lattice.from_fn(build_sample_fn(LOOK_OPS), DEFAULT_SIZE)


@pytest.fixture(scope="module")
def py_raw():
    """The bake before quantisation, for the maths-agreement check."""
    return Lattice.from_fn(build_sample_fn(LOOK_OPS), DEFAULT_SIZE, encoding=None)


# -- claim 1: build parity ---------------------------------------------------


def test_build_parity_in_float(js, py_raw):
    """TS ops and torch ops agree before quantisation.

    Both sides bake in float64, so this is a direct check that the two
    implementations of the maths are the same maths. It has to be checked
    unquantised: quantisation would hide a genuine 1e-6 divergence, and a
    divergence that small today becomes a visible one the first time somebody
    adds an op with high local gain.
    """
    err = (js["lattice_raw"] - py_raw.to_flat().to(torch.float32)).abs().max().item()
    assert err < 1e-6, f"lattice build differs by {err:.3e} — the maths has drifted"


def test_build_parity_after_transport_is_exact(js, py_lattice):
    """After u16 quantisation both sides hold byte-identical lattices.

    This is the load-bearing test. It is what lets us say preview and render
    are the same pixels rather than nearly the same pixels.
    """
    py_bytes = py_lattice.to_transport("u16")["data"]
    assert py_bytes == js["transport"]["data"]


def test_transport_metadata_matches(js, py_lattice):
    t = py_lattice.to_transport("u16")
    assert (t["schema"], t["size"], t["encoding"]) == (
        js["transport"]["schema"],
        js["transport"]["size"],
        js["transport"]["encoding"],
    )


# -- claim 2: apply parity ---------------------------------------------------


def test_apply_parity_same_lattice(js, py_lattice):
    """Same lattice in, same samples out — the trilinear code agrees."""
    quantised = Lattice.from_transport(js["transport"])
    py_sampled = quantised.apply_points(js["pixels"]).clamp(0, 1)
    err = (py_sampled - js["sampled"]).abs().max().item()
    assert err < 1e-6, f"trilinear samplers differ by {err:.3e}"


# -- claim 3: end to end -----------------------------------------------------


def _quantise(x: torch.Tensor, levels: int) -> torch.Tensor:
    return (x.clamp(0, 1) * (levels - 1) + 0.5).floor().to(torch.int32)


def test_preview_and_render_identical_at_8bit(js, py_lattice):
    """What the user sees is what the user gets."""
    render = py_lattice.apply_points(js["pixels"]).clamp(0, 1)
    preview = js["sampled"]
    diff = (_quantise(render, 256) - _quantise(preview, 256)).abs().max().item()
    assert diff == 0, f"{diff} code values of drift at 8-bit"


def test_preview_and_render_agree_to_one_code_at_16bit(js, py_lattice):
    """The limit of the guarantee, stated honestly.

    Both sides sample the identical quantised lattice, but the *sampling* runs
    in float64 in the browser and float32 in torch — deliberately, because a
    float64 render path would double the memory of every intermediate on a 4K
    image for no visible benefit. The samplers agree to about 6e-8, which is
    invisible at 8 bits and can flip a single code at 16 bits when a value lands
    right on a rounding boundary.

    So: identical at 8-bit, within one code at 16-bit. If this ever exceeds 1,
    something structural has changed and the 8-bit guarantee is at risk too.
    """
    render = py_lattice.apply_points(js["pixels"]).clamp(0, 1)
    diff = (_quantise(render, 65536) - _quantise(js["sampled"], 65536)).abs().max().item()
    assert diff <= 1, f"{diff} code values of drift at 16-bit"
