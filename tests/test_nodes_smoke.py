"""Node-level smoke tests: every node, at defaults and at the extremes.

The per-module tests exercise the maths. These exercise the *node*: schema
validity, that defaults actually run, and that a user dragging every slider to
its stated minimum or maximum gets an image rather than a traceback. Widget
ranges are read from the schema itself, so a range that changes is covered
automatically.

Skipped when ComfyUI is not importable, which is the case in a bare checkout.
"""

from __future__ import annotations

import itertools

import pytest
import torch

comfy_io = pytest.importorskip("comfy_api.latest", reason="needs ComfyUI on the path").io  # noqa: F401

from pw_color.nodes import curves, grain, look, look_io, match_source, optics, palette, scopes  # noqa: E402

NODE_MODULES = (look, curves, grain, optics, match_source, palette, scopes, look_io)
ALL_NODES = [n for m in NODE_MODULES for n in m.NODES]


def _img(b: int = 1, h: int = 24, w: int = 32, seed: int = 5) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return torch.rand(b, h, w, 3, generator=g)


class _Hidden:
    unique_id = "smoke"


def _prepare(node) -> None:
    node.hidden = _Hidden()


def _schema(node):
    """The schema as ComfyUI sees it.

    `finalize()` before `validate()`, matching the order in `_io.py`: finalize
    is what assigns default ids to outputs that did not declare one, so
    validating first reports every output as a duplicate `None`.
    """
    s = node.define_schema()
    s.finalize()
    return s


# ---------------------------------------------------------------------------
# Schema hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("node", ALL_NODES, ids=lambda n: n.__name__)
def test_schema_is_valid(node):
    s = _schema(node)
    s.validate()  # raises on duplicate or malformed ids
    assert s.node_id.startswith("PW_")
    assert s.display_name and s.display_name.startswith("PW ")
    assert s.category.startswith("PW Color")
    assert s.description, f"{s.node_id} has no description"
    assert s.outputs, f"{s.node_id} has no outputs"


@pytest.mark.parametrize("node", ALL_NODES, ids=lambda n: n.__name__)
def test_every_input_has_a_tooltip_or_an_obvious_name(node):
    """A slider called `vignette_feather` can go without a tooltip; one called
    `space` or `quality` cannot."""
    obvious = {
        "image", "original", "processed", "mask", "look_in", "look", "seed",
        "strength", "opacity", "amount", "size", "count", "sort", "blend",
        "exposure", "contrast", "highlights", "shadows", "whites", "blacks",
        "warmth", "tint", "vibrance", "saturation", "red", "green", "blue",
        "width", "height", "preset", "mode",
    }
    for inp in _schema(node).inputs:
        name = inp.id
        if name in obvious or "_" in name:
            continue
        assert getattr(inp, "tooltip", None), f"{node.__name__}.{name} needs a tooltip"


@pytest.mark.parametrize("node", ALL_NODES, ids=lambda n: n.__name__)
def test_node_ids_and_display_names_are_unique(node):
    ids = [_schema(n).node_id for n in ALL_NODES]
    names = [_schema(n).display_name for n in ALL_NODES]
    assert len(ids) == len(set(ids))
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Defaults run
# ---------------------------------------------------------------------------


def _defaults(node) -> dict:
    out = {}
    for inp in _schema(node).inputs:
        d = getattr(inp, "default", None)
        if d is not None:
            out[inp.id] = d
    return out


def test_look_runs_at_defaults():
    _prepare(look.PW_Look)
    img = _img()
    out = look.PW_Look.execute(image=img, **_defaults(look.PW_Look))
    assert out.result[0].shape == img.shape
    # Defaults are a neutral grade: the image must come back essentially as-is.
    assert float((out.result[0] - img).abs().max()) * 255 < 1.0


def test_curves_runs_at_defaults():
    _prepare(curves.PW_Curves)
    img = _img()
    d = _defaults(curves.PW_Curves)
    out = curves.PW_Curves.execute(image=img, curves=d["curves"], preserve_hue=True, strength=1.0)
    assert float((out.result[0] - img).abs().max()) * 255 < 1.0


def test_grain_runs_at_defaults():
    img = _img()
    d = _defaults(grain.PW_Grain)
    out = grain.PW_Grain.execute(image=img, **{k: v for k, v in d.items() if k != "plate"})
    assert out.result[0].shape == img.shape


def test_optics_runs_at_defaults():
    img = _img()
    out = optics.PW_Optics.execute(image=img, **_defaults(optics.PW_Optics))
    assert out.result[0].shape == img.shape


def test_scopes_runs_at_defaults():
    _prepare(scopes.PW_Scopes)
    img = _img()
    out = scopes.PW_Scopes.execute(image=img, **_defaults(scopes.PW_Scopes))
    assert torch.equal(out.result[0], img), "scopes must pass the image through untouched"


def test_match_source_runs_at_defaults():
    img = _img()
    d = _defaults(match_source.PW_MatchSource)
    out = match_source.PW_MatchSource.execute(original=img, processed=img, **d)
    assert float((out.result[0] - img).abs().max()) * 255 < 1.0


def test_palette_runs_at_defaults():
    _prepare(palette.PW_Palette)
    d = _defaults(palette.PW_Palette)
    out = palette.PW_Palette.execute(image=_img(), **d)
    assert len(out.result[0]["colors"]) == d["count"]


def test_look_io_runs_at_defaults():
    out = look_io.PW_LookIO.execute(**_defaults(look_io.PW_LookIO))
    assert out.result[0]["schema"] == 1
    assert "source" in out.result[2]


# ---------------------------------------------------------------------------
# Extremes
# ---------------------------------------------------------------------------


def _numeric_extremes(node) -> dict[str, list]:
    """Every numeric input's min and max, straight from the schema."""
    out: dict[str, list] = {}
    for inp in _schema(node).inputs:
        lo, hi = getattr(inp, "min", None), getattr(inp, "max", None)
        if lo is None or hi is None:
            continue
        if inp.id == "seed":
            continue  # a huge seed is valid and slow to nothing
        out[inp.id] = [lo, hi]
    return out


@pytest.mark.parametrize(
    "node,kwargs_fn",
    [
        (look.PW_Look, lambda img: {"image": img}),
        (optics.PW_Optics, lambda img: {"image": img}),
        (grain.PW_Grain, lambda img: {"image": img}),
        (match_source.PW_MatchSource, lambda img: {"original": img, "processed": img}),
    ],
    ids=["look", "optics", "grain", "match_source"],
)
def test_every_slider_at_both_extremes_produces_a_valid_image(node, kwargs_fn):
    """One slider at a time, pinned to each end of its declared range."""
    _prepare(node)
    img = _img(h=16, w=16)
    base = _defaults(node)
    for name, values in _numeric_extremes(node).items():
        for value in values:
            kw = {**base, **kwargs_fn(img), name: value}
            kw.pop("plate", None)
            out = node.execute(**kw).result[0]
            assert torch.isfinite(out).all(), f"{node.__name__}.{name}={value} produced non-finite output"
            assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0, f"{node.__name__}.{name}={value} left [0,1]"


@pytest.mark.parametrize("node", [look.PW_Look, optics.PW_Optics, grain.PW_Grain], ids=lambda n: n.__name__)
def test_all_sliders_at_maximum_together(node):
    """Everything at once, which is what a user does when they are exploring."""
    _prepare(node)
    img = _img(h=16, w=16)
    kw = {**_defaults(node), "image": img}
    kw.pop("plate", None)
    for name, values in _numeric_extremes(node).items():
        kw[name] = values[1]
    out = node.execute(**kw).result[0]
    assert torch.isfinite(out).all()
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


@pytest.mark.parametrize("combo", ["blend", "sort", "space", "mode", "quality", "gradient_blend", "save_format"])
def test_every_combo_option_is_accepted(combo: str):
    """Each dropdown value must actually work, not just be listed."""
    img = _img(h=16, w=16)
    for node in ALL_NODES:
        _prepare(node)
        matching = [i for i in _schema(node).inputs if i.id == combo and getattr(i, "options", None)]
        if not matching:
            continue
        base = _defaults(node)
        for option in matching[0].options:
            kw = {**base, combo: option}
            kw.pop("plate", None)
            if node is match_source.PW_MatchSource:
                kw.update({"original": img, "processed": img})
            elif node is look_io.PW_LookIO:
                pass
            else:
                kw["image"] = img
            result = node.execute(**kw).result
            first = result[0]
            if isinstance(first, torch.Tensor):
                assert torch.isfinite(first).all(), f"{node.__name__} {combo}={option}"


# ---------------------------------------------------------------------------
# LOOK chaining
# ---------------------------------------------------------------------------


def test_look_accumulates_down_a_chain():
    """The LOOK wire must carry the whole stack, not just the last node."""
    _prepare(look.PW_Look)
    _prepare(curves.PW_Curves)
    img = _img()

    a = look.PW_Look.execute(image=img, **{**_defaults(look.PW_Look), "preset": "golden-hour"})
    b = curves.PW_Curves.execute(
        image=a.result[0],
        curves='{"luma": [[0, 0.05], [1, 0.97]]}',
        preserve_hue=True,
        strength=1.0,
        look_in=a.result[1],
    )
    c = optics.PW_Optics.execute(image=b.result[0], **{**_defaults(optics.PW_Optics), "look_in": b.result[1]})

    types = [op["type"] for op in c.result[1]["ops"]]
    assert "tone" in types and "curves" in types and "halation" in types
    assert len(types) >= 5


def test_chained_look_reports_lut_export_correctly():
    """A chain containing a spatial op must report itself as not exportable."""
    from pw_color.types import Look

    _prepare(look.PW_Look)
    img = _img()
    a = look.PW_Look.execute(image=img, **_defaults(look.PW_Look))
    assert Look.from_dict(a.result[1]).lut_exportable

    b = optics.PW_Optics.execute(image=img, **{**_defaults(optics.PW_Optics), "look_in": a.result[1]})
    assert not Look.from_dict(b.result[1]).lut_exportable


def test_batch_flows_through_every_image_node():
    _prepare(look.PW_Look)
    _prepare(curves.PW_Curves)
    img = _img(b=3)
    out = look.PW_Look.execute(image=img, **_defaults(look.PW_Look)).result[0]
    assert out.shape[0] == 3
    out = optics.PW_Optics.execute(image=out, **_defaults(optics.PW_Optics)).result[0]
    assert out.shape[0] == 3
    d = _defaults(grain.PW_Grain)
    d.pop("plate", None)
    out = grain.PW_Grain.execute(image=out, **d).result[0]
    assert out.shape[0] == 3
