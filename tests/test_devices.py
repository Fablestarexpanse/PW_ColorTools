"""Device and dtype handling.

ComfyUI hands nodes whatever the previous node produced. On a CUDA box that is
usually a CUDA tensor, and depending on the VAE and the launch flags it may be
float16 or bfloat16 rather than float32. Every op has to cope, and anything
seeded has to give the *same* answer regardless — a grain look that changes when
you move machines is not a look.
"""

from __future__ import annotations

import pytest
import torch

from pw_color import glow, grain, optics, scopes
from pw_color.lattice import DEFAULT_SIZE, Lattice
from pw_color.match import match_mean_std
from pw_color.ops import build_sample_fn
from pw_color.palette import extract_palette

CUDA = pytest.mark.skipif(not torch.cuda.is_available(), reason="no CUDA device")

OPS = {
    "lattice": lambda x: Lattice.from_fn(
        build_sample_fn([{"type": "colour", "params": {"warmth": 0.3, "saturation": 1.2}}]), DEFAULT_SIZE
    ).apply(x),
    "grain": lambda x: grain.apply_grain(
        x, grain.procedural_field(x.shape[0], x.shape[1], x.shape[2], 1.4, 7, device=x.device),
        grain.TonalResponse(), amount=0.2
    ),
    "dither": lambda x: grain.dither(x, seed=7),
    "glow": lambda x: glow.apply_glow(x, 0.5, radius=8.0),
    "halation": lambda x: optics.apply_halation(x, 0.5, radius=8.0),
    "vignette": lambda x: optics.apply_vignette(x, 0.5),
    "aberration": lambda x: optics.apply_chromatic_aberration(x, 0.5),
}


def _img(device="cpu", dtype=torch.float32) -> torch.Tensor:
    g = torch.Generator().manual_seed(5)
    return torch.rand(1, 24, 32, 3, generator=g).to(device=device, dtype=dtype)


# -- dtype -------------------------------------------------------------------


@pytest.mark.parametrize("name", list(OPS))
@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16, torch.float64])
def test_ops_accept_non_float32_input(name: str, dtype: torch.dtype):
    """A VAE running in half precision hands the next node a half tensor."""
    out = OPS[name](_img(dtype=dtype))
    assert torch.isfinite(out).all(), f"{name} produced non-finite output at {dtype}"
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
def test_palette_accepts_half_precision(dtype: torch.dtype):
    pal = extract_palette(_img(dtype=dtype), count=4)
    assert len(pal.colors) == 4


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
def test_scopes_accept_half_precision(dtype: torch.dtype):
    out = scopes.render_scope(_img(dtype=dtype), "all", 128, 96)
    assert torch.isfinite(out).all()


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
def test_match_source_accepts_half_precision(dtype: torch.dtype):
    out = match_mean_std(_img(dtype=dtype), _img(dtype=dtype))
    assert torch.isfinite(out).all()


# -- device ------------------------------------------------------------------


@CUDA
@pytest.mark.parametrize("name", list(OPS))
def test_ops_run_on_cuda(name: str):
    out = OPS[name](_img(device="cuda"))
    assert out.device.type == "cuda", f"{name} moved the image off the GPU"
    assert torch.isfinite(out).all()


@CUDA
@pytest.mark.parametrize("name", list(OPS))
def test_cuda_and_cpu_agree_at_8bit(name: str):
    """The output a user saves must not depend on where it was computed."""
    cpu = OPS[name](_img(device="cpu"))
    cuda = OPS[name](_img(device="cuda")).cpu()
    q = lambda x: (x.clamp(0, 1) * 255 + 0.5).floor().to(torch.int32)
    diff = int((q(cpu) - q(cuda)).abs().max())
    assert diff <= 1, f"{name} differs by {diff} code values between CPU and CUDA"


@CUDA
def test_seeded_grain_is_identical_on_cpu_and_cuda():
    """Grain is generated on the CPU regardless of target device precisely so
    this holds: CUDA's RNG does not produce the same stream."""
    a = grain.procedural_field(2, 32, 32, 1.4, 1234, device="cpu")
    b = grain.procedural_field(2, 32, 32, 1.4, 1234, device="cuda").cpu()
    assert torch.equal(a, b)


@CUDA
def test_palette_is_identical_on_cpu_and_cuda():
    """Clustering is forced to the CPU because CUDA reductions are not
    order-deterministic; this is the test that pins that decision."""
    assert extract_palette(_img(device="cpu"), count=5).to_json() == extract_palette(_img(device="cuda"), count=5).to_json()


@CUDA
def test_lattice_apply_matches_across_devices():
    lat = Lattice.from_fn(build_sample_fn([{"type": "tone", "params": {"contrast": 0.3}}]), DEFAULT_SIZE)
    img = _img()
    cpu = lat.apply(img)
    cuda = Lattice(lat.data.cuda()).apply(img.cuda()).cpu()
    assert float((cpu - cuda).abs().max()) < 1e-5
