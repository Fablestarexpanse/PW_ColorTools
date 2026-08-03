"""3D LUT lattice: build, apply, transport, and ``.cube`` import/export.

The lattice is the contract between preview and render. Whoever builds it, both
sides *apply* it the same way, so preview and render cannot drift as long as
the sampling code matches. This module owns that sampling code on the Python
side; ``web/src/core/lattice.ts`` is its line-by-line mirror in TypeScript, and
``tests/test_parity.py`` pins them together.

Indexing conventions, fixed once so nothing has to guess:

* ``Lattice.data`` is ``[N, N, N, 3]`` indexed ``data[ri, gi, bi] -> (r, g, b)``.
* The *flat* order used by both ``.cube`` files and the JSON transport is
  **red fastest**, i.e. ``for b: for g: for r:``. One order everywhere.
* The lattice input domain is the node's incoming encoding, which for a
  VAE-decoded ComfyUI ``IMAGE`` is display-referred sRGB in ``[0,1]``. That is
  also what ``.cube`` consumers expect, so export is a straight write with no
  hidden transfer function.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Callable, Literal

import torch

__all__ = ["Lattice", "DEFAULT_SIZE", "FINAL_SIZE"]

DEFAULT_SIZE = 33
FINAL_SIZE = 65

#: The lattice stores the *unclamped* result of the op stack over this range,
#: and the clamp to [0,1] happens after sampling.
#:
#: Why: a clamp is a kink, and a lattice cannot represent a kink. Baking the
#: clamp in costs 1.9 code values on a +0.4 stop exposure and 3.8 on a
#: preserve-hue curve, because the corners either side of where the function
#: crosses white get interpolated into a grey haze. Storing the unclamped
#: function and clamping afterwards drops those to 0.12 and well under one.
#: A clamp is trivially identical in torch and in a fragment shader, so this
#: costs nothing in parity.
#:
#: The range is a fixed constant rather than fitted to the data on purpose.
#: Fitting would make the quantisation grid depend on float values that agree
#: between JS and torch only to ~1e-6, so a look whose extreme landed on a grid
#: boundary could quantise differently on the two sides. A constant cannot.
#: Anything beyond this range is already fully clipped on output, so clamping
#: the stored value at the ends is lossless in the only sense that matters.
OUT_MIN = -0.5
OUT_MAX = 2.0

Encoding = Literal["u16", "f32"]

# A callable that maps [M,3] sRGB-encoded sample points to [M,3] output values.
SampleFn = Callable[[torch.Tensor], torch.Tensor]


class Lattice:
    """A cubic 3D LUT.

    Immutable by convention — every operation returns a new Lattice — because
    lattices get cached by hash and shared between the preview route and the
    render path.
    """

    __slots__ = ("data", "size")

    def __init__(self, data: torch.Tensor, _dtype: torch.dtype = torch.float32) -> None:
        if data.ndim != 4 or data.shape[3] != 3 or len({*data.shape[:3]}) != 1:
            raise ValueError(f"lattice data must be [N,N,N,3], got {tuple(data.shape)}")
        self.data = data.to(_dtype).contiguous()
        self.size = int(data.shape[0])

    # -- construction -------------------------------------------------------

    @classmethod
    def identity(
        cls,
        size: int = DEFAULT_SIZE,
        device: torch.device | str = "cpu",
        dtype: torch.dtype = torch.float32,
    ) -> "Lattice":
        """The do-nothing lattice. Also the blend target for every strength slider."""
        axis = torch.linspace(0.0, 1.0, size, dtype=dtype, device=device)
        r = axis.view(size, 1, 1).expand(size, size, size)
        g = axis.view(1, size, 1).expand(size, size, size)
        b = axis.view(1, 1, size).expand(size, size, size)
        return cls(torch.stack((r, g, b), dim=-1), _dtype=dtype)

    @classmethod
    def from_fn(
        cls,
        fn: SampleFn,
        size: int = DEFAULT_SIZE,
        device: torch.device | str = "cpu",
        encoding: Encoding | None = "u16",
    ) -> "Lattice":
        """Bake an arbitrary per-pixel operation into a lattice.

        ``fn`` receives ``[N**3, 3]`` sample points in flat (red-fastest) order
        and must be pure — same input, same output — or determinism is gone.

        Two things here are load-bearing for preview/render parity:

        * The bake runs in **float64**, because the browser's arithmetic is
          float64 and we need both sides to agree *before* quantisation. In
          float32 the ~1e-7 difference between the two gets amplified about a
          hundredfold wherever an op runs off the edge of the sRGB gamut, which
          was enough to flip u16 codes and put a visible code value between
          preview and render.
        * The result is **quantised on construction**. A lattice that has not
          been through the transport encoding must never reach the renderer,
          because the preview only ever sees the quantised one. Making that the
          constructor's job means it cannot be forgotten. Pass ``encoding=None``
          for ``.cube`` authoring, where full float precision is the point.
        """
        ident = cls.identity(size, device, dtype=torch.float64)
        pts = ident.to_flat()
        out = fn(pts)
        if out.shape != pts.shape:
            raise ValueError(f"sample fn returned {tuple(out.shape)}, expected {tuple(pts.shape)}")
        raw = cls.from_flat(out.to(torch.float64), size, _dtype=torch.float64)
        return raw if encoding is None else Lattice.from_transport(raw.to_transport(encoding))

    @classmethod
    def from_flat(cls, flat: torch.Tensor, size: int, _dtype: torch.dtype = torch.float32) -> "Lattice":
        """Build from red-fastest flat order ``[N**3, 3]``."""
        if flat.shape != (size**3, 3):
            raise ValueError(f"expected [{size**3},3], got {tuple(flat.shape)}")
        # flat is ordered (b, g, r); permute back to data[r, g, b].
        return cls(flat.reshape(size, size, size, 3).permute(2, 1, 0, 3).contiguous(), _dtype=_dtype)

    def to_flat(self) -> torch.Tensor:
        """Red-fastest flat order ``[N**3, 3]`` — the ``.cube`` / transport order."""
        return self.data.permute(2, 1, 0, 3).reshape(-1, 3).contiguous()

    # -- combination --------------------------------------------------------

    def blend_to_identity(self, strength: float) -> "Lattice":
        """Lerp toward identity. ``strength=1`` is the full effect, ``0`` a no-op.

        Done on the lattice rather than on pixels so that the strength slider is
        itself LUT-exportable — a 50% look bakes to a 50% ``.cube``.
        """
        if strength >= 1.0:
            return self
        ident = Lattice.identity(self.size, self.data.device)
        return Lattice(torch.lerp(ident.data, self.data, float(strength)))

    def then(self, other: "Lattice") -> "Lattice":
        """Composition: ``self`` first, then ``other``.

        Resampling the second lattice through the first loses a little precision
        at each stage — the usual LUT-stacking caveat. At 33³ that is under a
        code value for smooth ops, but a stack of hard clips will show it; use a
        single lattice built from the whole op chain where you can.
        """
        if other.size != self.size:
            raise ValueError("cannot compose lattices of different sizes")
        return Lattice.from_flat(other.apply_points(self.to_flat()), self.size)

    # -- application --------------------------------------------------------

    def apply_points(self, pts: torch.Tensor) -> torch.Tensor:
        """Trilinearly sample the lattice at ``[..., 3]`` sRGB-encoded points.

        Written as an explicit 8-corner gather rather than ``grid_sample`` so
        that the TypeScript mirror can be identical instruction-for-instruction.
        ``grid_sample``'s align_corners handling and its CUDA kernel's internal
        precision are not something we can pin across backends.
        """
        n = self.size
        shape = pts.shape
        p = pts.reshape(-1, 3).to(torch.float32)

        # Continuous lattice coordinates. Clamping the *coordinate* (not the
        # input) means out-of-range values are held at the edge of the LUT,
        # which is what every LUT applier on earth does.
        c = (p.clamp(0.0, 1.0) * (n - 1)).clamp(0.0, float(n - 1))
        i0 = c.floor().to(torch.int64).clamp(0, n - 2)
        f = c - i0.to(torch.float32)
        i1 = i0 + 1

        r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
        r1, g1, b1 = i1[:, 0], i1[:, 1], i1[:, 2]
        fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]

        d = self.data.reshape(-1, 3)

        def corner(ri: torch.Tensor, gi: torch.Tensor, bi: torch.Tensor) -> torch.Tensor:
            return d[(ri * n + gi) * n + bi]

        c00 = torch.lerp(corner(r0, g0, b0), corner(r1, g0, b0), fr)
        c10 = torch.lerp(corner(r0, g1, b0), corner(r1, g1, b0), fr)
        c01 = torch.lerp(corner(r0, g0, b1), corner(r1, g0, b1), fr)
        c11 = torch.lerp(corner(r0, g1, b1), corner(r1, g1, b1), fr)

        c0 = torch.lerp(c00, c10, fg)
        c1 = torch.lerp(c01, c11, fg)
        return torch.lerp(c0, c1, fb).reshape(shape)

    def apply(self, image: torch.Tensor) -> torch.Tensor:
        """Apply to a ComfyUI ``IMAGE`` tensor ``[B,H,W,3]`` (or ``[B,H,W,4]``).

        The output clamp lives here rather than in the lattice, so that the
        lattice can hold the unclamped function — see :data:`OUT_MIN`. The
        preview shader clamps in exactly the same place. An alpha channel, if
        present, passes through untouched.
        """
        if image.shape[-1] == 4:
            rgb = self.apply_points(image[..., :3].to(self.data.device)).clamp(0.0, 1.0)
            return torch.cat((rgb, image[..., 3:].to(rgb.device)), dim=-1)
        return self.apply_points(image.to(self.data.device)).clamp(0.0, 1.0)

    # -- transport ----------------------------------------------------------

    def to_transport(self, encoding: Encoding = "u16") -> dict:
        """Serialize for the workflow JSON / HTTP route.

        ``u16`` is the default: 1/65535 quantization is two orders of magnitude
        below 8-bit output, and it halves the payload. The quantization happens
        *once*, before either side applies the lattice, so preview and render
        both consume the identical quantized values and parity is exact. ``f32``
        exists for ``.cube`` authoring and for the parity tests.
        """
        flat = self.to_flat().cpu()
        if encoding == "u16":
            norm = ((flat - OUT_MIN) / (OUT_MAX - OUT_MIN)).clamp(0.0, 1.0)
            # via numpy: torch.uint16 exists but has no stable .numpy() path.
            q = (norm * 65535.0 + 0.5).to(torch.int32).clamp(0, 65535)
            raw = q.numpy().astype("<u2").tobytes()
        elif encoding == "f32":
            raw = flat.contiguous().numpy().astype("<f4").tobytes()
        else:
            raise ValueError(f"unknown encoding {encoding!r}")
        return {
            "schema": 1,
            "size": self.size,
            "encoding": encoding,
            "out_min": OUT_MIN,
            "out_max": OUT_MAX,
            "data": base64.b64encode(raw).decode("ascii"),
        }

    @classmethod
    def from_transport(cls, obj: dict) -> "Lattice":
        import numpy as np

        if int(obj.get("schema", 1)) != 1:
            raise ValueError(f"unsupported lattice transport schema {obj.get('schema')!r}")
        size = int(obj["size"])
        raw = base64.b64decode(obj["data"])
        enc = obj.get("encoding", "u16")
        if enc == "u16":
            lo = float(obj.get("out_min", 0.0))
            hi = float(obj.get("out_max", 1.0))
            arr = np.frombuffer(raw, dtype="<u2").astype("float32") / 65535.0
            arr = arr * (hi - lo) + lo
        elif enc == "f32":
            arr = np.frombuffer(raw, dtype="<f4").astype("float32")
        else:
            raise ValueError(f"unknown encoding {enc!r}")
        flat = torch.from_numpy(arr.copy()).reshape(size**3, 3)
        return cls.from_flat(flat, size)

    def digest(self) -> str:
        """Stable content hash — cache key for the preview route."""
        return hashlib.sha256(self.to_flat().cpu().numpy().astype("<f4").tobytes()).hexdigest()[:16]

    # -- .cube --------------------------------------------------------------

    def to_cube(self, title: str = "PW Color") -> str:
        """Write an Adobe ``.cube``. Values are clamped to ``[0,1]`` — the format
        allows a wider DOMAIN but most consumers ignore it and clip anyway."""
        flat = self.to_flat().clamp(0.0, 1.0)
        lines = [
            f'TITLE "{title}"',
            f"LUT_3D_SIZE {self.size}",
            "DOMAIN_MIN 0.0 0.0 0.0",
            "DOMAIN_MAX 1.0 1.0 1.0",
            "",
        ]
        lines.extend(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in flat.tolist())
        return "\n".join(lines) + "\n"

    @classmethod
    def from_cube(cls, text: str) -> "Lattice":
        """Read an Adobe ``.cube``. 1D LUTs are rejected rather than silently
        promoted, because a promoted 1D LUT looks right until it doesn't."""
        size: int | None = None
        dom_min = [0.0, 0.0, 0.0]
        dom_max = [1.0, 1.0, 1.0]
        rows: list[tuple[float, float, float]] = []
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            head, *rest = s.split()
            key = head.upper()
            if key == "LUT_3D_SIZE":
                size = int(rest[0])
            elif key == "LUT_1D_SIZE":
                raise ValueError("1D .cube files are not supported")
            elif key == "DOMAIN_MIN":
                dom_min = [float(v) for v in rest[:3]]
            elif key == "DOMAIN_MAX":
                dom_max = [float(v) for v in rest[:3]]
            elif key in ("TITLE", "LUT_3D_INPUT_RANGE"):
                continue
            else:
                try:
                    rows.append((float(head), float(rest[0]), float(rest[1])))
                except (ValueError, IndexError):
                    continue
        if size is None:
            raise ValueError("no LUT_3D_SIZE in .cube file")
        if len(rows) != size**3:
            raise ValueError(f".cube declares size {size} ({size**3} rows) but has {len(rows)}")
        flat = torch.tensor(rows, dtype=torch.float32)
        if dom_min != [0.0, 0.0, 0.0] or dom_max != [1.0, 1.0, 1.0]:
            lo = torch.tensor(dom_min, dtype=torch.float32)
            hi = torch.tensor(dom_max, dtype=torch.float32)
            flat = (flat - lo) / (hi - lo).clamp(min=1e-9)
        return cls.from_flat(flat, size)
