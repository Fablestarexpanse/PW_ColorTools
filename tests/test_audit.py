"""Cross-cutting invariants, applied to every operation in the pack.

The per-module tests check that each thing does what it says. This file checks
the properties that must hold *everywhere*, because those are the ones that get
broken by a change somewhere else:

* output range and finiteness, including on hostile inputs
* alpha passes through untouched
* batches are handled, and frames do not leak into each other
* the identity setting really is the identity
* determinism
* non-contiguous and odd-shaped inputs do not crash

A bug found here is almost always a bug the module's own tests were too polite
to look for.
"""

from __future__ import annotations


import pytest
import torch

from pw_color import blend, glow, grain, look, optics, scopes
from pw_color.lattice import DEFAULT_SIZE, Lattice
from pw_color.match import match_mean_std
from pw_color.ops import build_sample_fn
from pw_color.palette import extract_palette
from pw_color.swatch_strip import render_strip

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _img(b: int = 1, h: int = 24, w: int = 32, seed: int = 5) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return torch.rand(b, h, w, 3, generator=g)


#: Inputs that have historically broken image code somewhere.
HOSTILE = {
    "all black": torch.zeros(1, 8, 8, 3),
    "all white": torch.ones(1, 8, 8, 3),
    "mid grey": torch.full((1, 8, 8, 3), 0.5),
    "pure red": torch.tensor([[[[1.0, 0.0, 0.0]]]]).expand(1, 8, 8, 3).contiguous(),
    "pure blue": torch.tensor([[[[0.0, 0.0, 1.0]]]]).expand(1, 8, 8, 3).contiguous(),
    "single pixel": torch.rand(1, 1, 1, 3, generator=torch.Generator().manual_seed(1)),
    "one row": torch.rand(1, 1, 16, 3, generator=torch.Generator().manual_seed(2)),
    "one column": torch.rand(1, 16, 1, 3, generator=torch.Generator().manual_seed(3)),
    "hard edges": torch.cat([torch.zeros(1, 8, 4, 3), torch.ones(1, 8, 4, 3)], dim=2),
}

#: Every image-to-image operation in the pack, at a setting that does something.
#: Name -> (callable, is_spatial).
IMAGE_OPS: dict[str, tuple] = {
    "lattice/tone": (
        lambda x: Lattice.from_fn(
            build_sample_fn([{"type": "tone", "params": {"contrast": 0.3, "shadows": 0.25, "highlights": -0.2}}]),
            DEFAULT_SIZE,
        ).apply(x),
        False,
    ),
    "lattice/colour": (
        lambda x: Lattice.from_fn(
            build_sample_fn([{"type": "colour", "params": {"warmth": 0.3, "vibrance": 0.4, "saturation": 1.2}}]),
            DEFAULT_SIZE,
        ).apply(x),
        False,
    ),
    "lattice/hsl": (
        lambda x: Lattice.from_fn(
            build_sample_fn([{"type": "hsl", "params": {"bands": {"blue": {"sat": 0.5, "hue": 0.3}}}}]), DEFAULT_SIZE
        ).apply(x),
        False,
    ),
    "lattice/curves": (
        lambda x: Lattice.from_fn(
            build_sample_fn([{"type": "curves", "params": {"luma": [[0, 0.06], [0.5, 0.55], [1, 0.95]]}}]), DEFAULT_SIZE
        ).apply(x),
        False,
    ),
    "lattice/gradient_map": (
        lambda x: Lattice.from_fn(
            build_sample_fn(
                [{"type": "gradient_map", "params": {"amount": 0.6, "blend": "colour",
                                                     "stops": [[0.0, [0.05, 0.04, 0.12]], [1.0, [0.98, 0.9, 0.75]]]}}]
            ),
            DEFAULT_SIZE,
        ).apply(x),
        False,
    ),
    "grain": (
        lambda x: grain.apply_grain(
            x, grain.procedural_field(x.shape[0], x.shape[1], x.shape[2], 1.4, 7), grain.TonalResponse(), amount=0.2
        ),
        True,
    ),
    "dither": (lambda x: grain.apply_dither(x, seed=7), True),
    "glow": (lambda x: glow.apply_glow(x, 0.5, radius=8.0), True),
    "halation": (lambda x: optics.apply_halation(x, 0.6, radius=8.0), True),
    "vignette": (lambda x: optics.apply_vignette(x, 0.5), True),
    "chromatic aberration": (lambda x: optics.apply_chromatic_aberration(x, 0.6), True),
    "match source": (lambda x: match_mean_std(x, _img(x.shape[0], x.shape[1], x.shape[2], seed=9)), False),
}

#: The same operations at their neutral setting. Must be exact no-ops.
IDENTITY_OPS: dict[str, object] = {
    "lattice/identity": lambda x: Lattice.identity(DEFAULT_SIZE).apply(x),
    "grain amount 0": lambda x: grain.apply_grain(
        x, grain.procedural_field(x.shape[0], x.shape[1], x.shape[2], 1.4, 7), grain.TonalResponse(), amount=0.0
    ),
    "grain opacity 0": lambda x: grain.apply_grain(
        x, grain.procedural_field(x.shape[0], x.shape[1], x.shape[2], 1.4, 7), grain.TonalResponse(), amount=0.5, opacity=0.0
    ),
    "dither 0": lambda x: grain.apply_dither(x, seed=7, strength=0.0),
    "glow 0": lambda x: glow.apply_glow(x, 0.0),
    "halation 0": lambda x: optics.apply_halation(x, 0.0),
    "vignette 0": lambda x: optics.apply_vignette(x, 0.0),
    "aberration 0": lambda x: optics.apply_chromatic_aberration(x, 0.0),
    "match to self": lambda x: match_mean_std(x, x),
}


# ---------------------------------------------------------------------------
# Universal properties
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(IMAGE_OPS))
@pytest.mark.parametrize("case", list(HOSTILE))
def test_every_op_survives_hostile_input(name: str, case: str):
    """No NaNs, no infs, nothing outside [0,1], on any degenerate frame."""
    fn, _ = IMAGE_OPS[name]
    out = fn(HOSTILE[case].clone())
    assert torch.isfinite(out).all(), f"{name} produced non-finite values on {case}"
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0, f"{name} left [0,1] on {case}"


@pytest.mark.parametrize("name", list(IMAGE_OPS))
def test_every_op_preserves_shape(name: str):
    fn, _ = IMAGE_OPS[name]
    src = _img()
    assert fn(src).shape == src.shape


@pytest.mark.parametrize("name", list(IMAGE_OPS))
def test_every_op_passes_alpha_through(name: str):
    """An RGBA image must come back with byte-identical alpha."""
    rgb = _img()
    alpha = torch.rand(1, 24, 32, 1, generator=torch.Generator().manual_seed(4))
    fn, _ = IMAGE_OPS[name]
    out = fn(torch.cat((rgb, alpha), dim=-1))
    assert out.shape[-1] == 4, f"{name} dropped the alpha channel"
    assert torch.equal(out[..., 3:], alpha), f"{name} modified alpha"


@pytest.mark.parametrize("name", list(IMAGE_OPS))
def test_every_op_is_deterministic(name: str):
    fn, _ = IMAGE_OPS[name]
    src = _img()
    assert torch.equal(fn(src.clone()), fn(src.clone())), f"{name} is not deterministic"


@pytest.mark.parametrize("name", list(IMAGE_OPS))
def test_every_op_leaves_its_input_unmodified(name: str):
    """In-place mutation of the input is a classic ComfyUI footgun: the same
    tensor is often wired into two branches."""
    fn, _ = IMAGE_OPS[name]
    src = _img()
    before = src.clone()
    fn(src)
    assert torch.equal(src, before), f"{name} mutated its input in place"


@pytest.mark.parametrize("name", list(IMAGE_OPS))
def test_every_op_handles_a_batch(name: str):
    fn, _ = IMAGE_OPS[name]
    batch = _img(b=3)
    out = fn(batch)
    assert out.shape == batch.shape, f"{name} changed batch shape"
    assert torch.isfinite(out).all()


@pytest.mark.parametrize("name", [n for n, (_, spatial) in IMAGE_OPS.items() if not spatial])
def test_pointwise_ops_do_not_leak_between_frames(name: str):
    """A per-pixel op applied to a batch must give each frame the same result
    as processing it alone. Catches accidental cross-frame reductions."""
    fn, _ = IMAGE_OPS[name]
    if name == "match source":
        pytest.skip("match source is intentionally a whole-frame statistic")
    a, b = _img(seed=11), _img(seed=12)
    together = fn(torch.cat((a, b), dim=0))
    assert torch.allclose(together[0:1], fn(a), atol=1e-6), f"{name} leaked across frames"
    assert torch.allclose(together[1:2], fn(b), atol=1e-6), f"{name} leaked across frames"


@pytest.mark.parametrize("name", list(IDENTITY_OPS))
def test_neutral_settings_are_true_no_ops(name: str):
    """A control at its neutral value must change nothing at all. Anything
    else means a chain of untouched nodes slowly degrades the image."""
    src = _img()
    out = IDENTITY_OPS[name](src)
    delta = float((out - src).abs().max()) * 255
    assert delta < 0.5, f"{name} shifted the image by {delta:.3f} code values"


@pytest.mark.parametrize("name", list(IMAGE_OPS))
def test_every_op_accepts_a_non_contiguous_input(name: str):
    """Slicing and permuting upstream is normal in ComfyUI graphs, and torch
    silently produces non-contiguous tensors when it happens."""
    fn, _ = IMAGE_OPS[name]
    src = _img(h=32, w=32)[:, ::2, ::2, :]
    assert not src.is_contiguous()
    out = fn(src)
    assert torch.isfinite(out).all(), f"{name} failed on a non-contiguous input"


# ---------------------------------------------------------------------------
# Analysis outputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(HOSTILE))
@pytest.mark.parametrize("mode", list(scopes.SCOPE_MODES))
def test_scopes_survive_hostile_input(case: str, mode: str):
    out = scopes.render_scope(HOSTILE[case], mode, 128, 96)
    assert torch.isfinite(out).all()
    assert out.shape == (1, 96, 128, 3)


@pytest.mark.parametrize("case", list(HOSTILE))
def test_palette_survives_hostile_input(case: str):
    pal = extract_palette(HOSTILE[case], count=4)
    assert len(pal.colors) >= 1
    assert all(c.hex.startswith("#") and len(c.hex) == 7 for c in pal.colors)
    assert all(0.0 <= c.coverage <= 1.0001 for c in pal.colors)
    strip = render_strip(pal, 200, 80)
    assert torch.isfinite(strip).all()


def test_palette_coverage_always_sums_to_one():
    for case, img in HOSTILE.items():
        pal = extract_palette(img, count=3)
        total = sum(c.coverage for c in pal.colors)
        assert abs(total - 1.0) < 1e-3, f"{case}: coverage sums to {total}"


# ---------------------------------------------------------------------------
# Blend modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", blend.BLEND_MODES)
def test_blend_modes_survive_extremes(mode: str):
    for base in (0.0, 0.5, 1.0):
        for layer in (0.0, 0.5, 1.0):
            out = blend.composite(torch.full((4, 3), base), torch.full((4, 3), layer), mode)
            assert torch.isfinite(out).all(), f"{mode} at base={base} layer={layer}"
            assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


@pytest.mark.parametrize("mode", grain.GRAIN_BLEND_MODES)
def test_grain_blend_modes_survive_extremes(mode: str):
    field = grain.procedural_field(1, 8, 8, 1.4, 3)
    for value in (0.0, 0.5, 1.0):
        out = grain.apply_grain(torch.full((1, 8, 8, 3), value), field, grain.TonalResponse(), amount=1.0, blend=mode)
        assert torch.isfinite(out).all() and float(out.min()) >= 0.0 and float(out.max()) <= 1.0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_errors_name_the_node_or_the_argument():
    """An error a user sees in the ComfyUI log has to say what to change."""
    cases = [
        (lambda: look.op_gradient_map(_img(), {"amount": 1.0, "blend": "nope", "stops": [[0, [0, 0, 0]], [1, [1, 1, 1]]]}), "blend"),
        (lambda: blend.composite(torch.zeros(2, 3), torch.zeros(2, 3), "nope"), "blend mode"),
        (lambda: scopes.render_scope(_img(), "nope"), "scope mode"),
        (lambda: grain.apply_grain(_img(), grain.procedural_field(1, 24, 32, 1.4, 1), grain.TonalResponse(), blend="nope"), "blend mode"),
        (lambda: match_mean_std(_img(h=8), _img(h=16)), "same size"),
        (lambda: extract_palette(_img(), count=3, sort="nope"), "sort mode"),
    ]
    for fn, expected in cases:
        with pytest.raises(ValueError) as exc:
            fn()
        assert expected in str(exc.value).lower() or expected in str(exc.value), f"unhelpful message: {exc.value}"


# ---------------------------------------------------------------------------
# Node surface
# ---------------------------------------------------------------------------


def test_no_development_nodes_are_registered():
    """The parity probe was a scaffold. It must not ship."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert not (root / "pw_color" / "nodes" / "parity.py").exists()
    assert not (root / "web" / "src" / "nodes" / "parity.ts").exists()
    assert "parity" not in (root / "__init__.py").read_text(encoding="utf-8")


def test_version_is_consistent_across_metadata():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    py = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    mod = re.search(r'__version__ = "([^"]+)"', (root / "pw_color" / "__init__.py").read_text(encoding="utf-8")).group(1)
    assert py == mod, f"pyproject says {py}, pw_color says {mod}"


def test_every_node_module_exports_nodes():
    import pkgutil
    from pathlib import Path

    pkg = Path(__file__).resolve().parents[1] / "pw_color" / "nodes"
    # Underscore-prefixed modules are shared helpers, not nodes.
    names = [m.name for m in pkgutil.iter_modules([str(pkg)]) if not m.name.startswith("_")]
    assert names, "no node modules found"
    for name in names:
        src = (pkg / f"{name}.py").read_text(encoding="utf-8")
        assert "NODES = [" in src, f"{name} does not export NODES"


def test_example_workflows_reference_only_nodes_that_exist():
    """A shipped workflow that names a removed node fails to load, and the
    user blames the pack rather than the example."""
    import json
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    known = set()
    for p in (root / "pw_color" / "nodes").glob("*.py"):
        known |= set(re.findall(r'node_id="(PW_\w+)"', p.read_text(encoding="utf-8")))
    assert known, "found no node ids to check against"

    found = sorted(root.glob("example_workflows/*.json"))
    assert found, "no example workflows to check"
    for wf in found:
        doc = json.loads(wf.read_text(encoding="utf-8"))
        used = {n["type"] for n in doc.get("nodes", []) if str(n.get("type", "")).startswith("PW_")}
        stale = sorted(used - known)
        assert not stale, f"{wf.name} references nodes that no longer exist: {stale}"


def test_shipped_json_assets_parse():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for path in sorted(root.glob("looks/**/*.json")) + sorted(root.glob("example_workflows/*.json")):
        json.loads(path.read_text(encoding="utf-8"))


def test_readme_images_all_exist():
    """A README that renders a broken image on the registry page is worse than
    one with no images."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "README.md").read_text(encoding="utf-8")
    refs = set(re.findall(r'!\[[^\]]*\]\(([^)]+)\)', text)) | set(re.findall(r'<img src="([^"]+)"', text))
    for ref in refs:
        if ref.startswith("http"):
            continue
        assert (root / ref).is_file(), f"README references missing file {ref}"


def test_every_look_emitting_node_can_also_receive_one():
    """The LOOK wire is what makes the pack a chain rather than eight nodes.

    A node that emits a LOOK but cannot accept one truncates the stack the
    moment someone puts it mid-chain, silently — the image still flows, so
    nothing looks wrong until a .cube export is missing half the grade. This
    caught PW_MatchSource, which was the only one.

    Read from the live schemas rather than from the source text: the shared
    `_schema` helpers mean the literal `io.Custom("LOOK")` no longer appears in
    any node file, and a grep-based version of this test would now pass by
    finding nothing.
    """
    pytest.importorskip("comfy_api.latest", reason="needs ComfyUI on the path")
    from pw_color.nodes import curves, grain, look, look_io, match_source, optics, palette, scopes

    checked = 0
    for module in (curves, grain, look, look_io, match_source, optics, palette, scopes):
        for cls in module.NODES:
            schema = cls.define_schema()
            schema.finalize()
            emits = any(getattr(o, "io_type", None) == "LOOK" for o in schema.outputs)
            if not emits:
                continue
            receives = [i for i in schema.inputs if getattr(i, "io_type", None) == "LOOK"]
            assert receives, (
                f"{cls.__name__} emits a LOOK but cannot receive one, so it "
                f"truncates any grade stack upstream of it"
            )
            checked += 1
    assert checked >= 5, f"expected several LOOK-emitting nodes, found {checked}"

def test_no_source_file_contains_a_control_character():
    """A stray NUL or other control byte in a source file is a syntax error
    Python reports without a line number, and it is invisible in a diff.

    Easy to introduce by accident when a file is written by a script that
    processes escape sequences one time too many; hard to spot afterwards.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    allowed = {9, 10, 13}  # tab, newline, carriage return
    for pattern in ("pw_color/**/*.py", "tests/*.py", "web/src/**/*.ts", "web/tools/*.ts"):
        for path in sorted(root.glob(pattern)):
            data = path.read_bytes()
            bad = sorted({b for b in data if b < 32 and b not in allowed})
            assert not bad, f"{path.relative_to(root)} contains control bytes {bad}"


def test_execute_signature_defaults_match_the_schema():
    """Every node states each default twice — once in `define_schema` for the
    UI, once as a parameter default on `execute` for direct calls.

    That is the ComfyUI V3 pattern and not worth fighting, but the two copies
    can disagree silently: the node would then behave one way in a graph and
    another way when called from a test or another node. So they are compared.
    """
    import inspect

    pytest.importorskip("comfy_api.latest", reason="needs ComfyUI on the path")
    from pw_color.nodes import curves, grain, look, look_io, match_source, optics, palette, scopes

    modules = (curves, grain, look, look_io, match_source, optics, palette, scopes)
    checked = 0
    for module in modules:
        for cls in module.NODES:
            schema = cls.define_schema()
            schema.finalize()
            declared = {
                i.id: i.default
                for i in schema.inputs
                if getattr(i, "default", None) is not None
            }
            params = inspect.signature(cls.execute).parameters
            for name, default in declared.items():
                param = params.get(name)
                if param is None or param.default is inspect.Parameter.empty:
                    continue
                assert param.default == default, (
                    f"{cls.__name__}.execute has {name}={param.default!r} but its "
                    f"schema declares {default!r}; the node behaves differently "
                    f"depending on who calls it"
                )
                checked += 1
    assert checked > 50, f"expected to compare many defaults, only saw {checked}"


def test_every_enumerated_vocabulary_is_a_literal_and_its_tuple_agrees():
    """A closed set of strings is stated twice: as a Literal the type checker
    reads, and as a tuple the ComfyUI combo reads.

    They must be the same set. Deriving the tuple with `get_args` is what makes
    that true by construction, so this checks the derivation is actually being
    used rather than the two being typed out separately and drifting.
    """
    from typing import get_args

    from pw_color import grain, palette, palette_io, scopes
    from pw_color import match as match_mod
    from pw_color import types as types_mod

    pairs = [
        ("BlendMode", types_mod.BlendMode, types_mod.BLEND_MODES),
        ("MatchSpace", match_mod.MatchSpace, match_mod.MATCH_SPACES),
        ("MatchTier", match_mod.MatchTier, match_mod.MATCH_TIERS),
        ("ScopeMode", scopes.ScopeMode, scopes.SCOPE_MODES),
        ("SortMode", palette.SortMode, palette.SORT_MODES),
        ("GrainBlendMode", grain.GrainBlendMode, grain.GRAIN_BLEND_MODES),
        ("PaletteFormat", palette_io.PaletteFormat, tuple(palette_io.PALETTE_FORMATS)),
    ]
    for name, alias, values in pairs:
        assert tuple(get_args(alias)) == tuple(values), (
            f"{name} and its runtime tuple disagree: the combo would offer "
            f"{tuple(values)} while the type allows {get_args(alias)}"
        )


def test_library_functions_from_a_closed_set_are_typed_as_that_set():
    """The same rule one layer down.

    Narrowing only the node signatures left the library functions they call
    still taking `str`, so the alias stopped at the door.
    """
    import inspect

    from pw_color import blend, grain, palette, scopes

    expected = {
        (palette.extract_palette, "sort", "SortMode"),
        (scopes.render_scope, "mode", "ScopeMode"),
        (grain.apply_grain, "blend", "GrainBlendMode"),
        (blend.blend_pixels, "mode", "BlendMode"),
        (blend.composite, "mode", "BlendMode"),
    }
    for fn, param, alias in sorted(expected, key=lambda e: (e[0].__name__, e[1])):
        annotation = inspect.signature(fn).parameters[param].annotation
        assert annotation == alias, (
            f"{fn.__module__}.{fn.__name__} types {param} as {annotation!r}, "
            f"discarding the {alias} vocabulary defined in the same module"
        )


def test_node_inputs_from_a_closed_set_are_typed_as_that_set():
    """A combo input whose options come from a Literal should be annotated with
    it, not widened back to `str` on the way into execute()."""
    import inspect

    pytest.importorskip("comfy_api.latest", reason="needs ComfyUI on the path")
    from pw_color.nodes import grain, look, match_source, palette, scopes

    expected = {
        (match_source.PW_MatchSource, "space"),
        (grain.PW_Grain, "blend"),
        (look.PW_Look, "blend"),
        (look.PW_Look, "reference_mode"),
        (look.PW_Look, "gradient_blend"),
        (scopes.PW_Scopes, "mode"),
        (palette.PW_Palette, "sort"),
        (palette.PW_Palette, "save_format"),
    }
    for cls, param in sorted(expected, key=lambda p: (p[0].__name__, p[1])):
        annotation = inspect.signature(cls.execute).parameters[param].annotation
        assert annotation != "str", (
            f"{cls.__name__}.execute widens {param} to str, discarding the "
            f"vocabulary its combo options are built from"
        )
