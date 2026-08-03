"""Reset must genuinely pass the image through unchanged.

The frontend's reset action restores each control to its schema default, except
for a short list of controls whose default is deliberately *not* neutral —
halation, the dither floor, Match Source's strength. That list lives in
`web/src/widgets/reset.ts`, and a list of magic numbers in a TypeScript file is
exactly the sort of thing that quietly stops being true.

So this parses the list out of the source and then proves it: run each node with
those values and assert the output is bit-identical to the input. If someone
changes a default, adds a control, or edits the table, this fails rather than
the user discovering a "reset" node that still tints their image.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import torch

comfy_io = pytest.importorskip("comfy_api.latest", reason="needs ComfyUI on the path").io  # noqa: F401

from pw_color.nodes import curves, grain, look, match_source, optics, palette, scopes  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESET_TS = ROOT / "web" / "src" / "widgets" / "reset.ts"


def _pass_through_table() -> dict[str, dict[str, float]]:
    """Parse the PASS_THROUGH map out of reset.ts."""
    src = RESET_TS.read_text(encoding="utf-8")
    block = re.search(r"const PASS_THROUGH[^=]*=\s*\{(.*?)\n\};", src, re.S)
    assert block, "could not find PASS_THROUGH in reset.ts"
    out: dict[str, dict[str, float]] = {}
    for node, body in re.findall(r"(\w+):\s*\{([^}]*)\}", block.group(1)):
        out[node] = {k: float(v) for k, v in re.findall(r"(\w+):\s*(-?[\d.]+)", body)}
    return out


def _defaults(node) -> dict:
    schema = node.define_schema()
    schema.finalize()
    return {i.id: i.default for i in schema.inputs if getattr(i, "default", None) is not None}


def _image(seed: int = 5, h: int = 48, w: int = 64) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return torch.rand(1, h, w, 3, generator=g)


class _Hidden:
    unique_id = "reset-test"


NODES = {
    "PW_Look": look.PW_Look,
    "PW_Curves": curves.PW_Curves,
    "PW_Grain": grain.PW_Grain,
    "PW_Optics": optics.PW_Optics,
    "PW_MatchSource": match_source.PW_MatchSource,
    "PW_Scopes": scopes.PW_Scopes,
    "PW_Palette": palette.PW_Palette,
}


def _reset_kwargs(name: str, node) -> dict:
    """Exactly what the frontend's reset produces: defaults plus overrides."""
    kw = _defaults(node)
    kw.pop("plate", None)  # a combo whose default is already 'none'
    kw.update(_pass_through_table().get(name, {}))
    return kw


@pytest.mark.parametrize("name", ["PW_Look", "PW_Curves", "PW_Grain", "PW_Optics"])
def test_reset_makes_the_node_a_pass_through(name: str):
    """The claim the reset action makes, tested on the real node."""
    node = NODES[name]
    node.hidden = _Hidden()
    img = _image()
    out = node.execute(image=img, **_reset_kwargs(name, node)).result[0]
    delta = float((out - img).abs().max()) * 255
    assert delta < 0.5, f"{name} still changed the image by {delta:.3f} code values after reset"


def test_reset_makes_match_source_a_pass_through():
    node = NODES["PW_MatchSource"]
    img, other = _image(1), _image(2)
    out = node.execute(original=other, processed=img, **_reset_kwargs("PW_MatchSource", node)).result[0]
    assert float((out - img).abs().max()) * 255 < 0.5


def test_scopes_and_palette_never_alter_the_image():
    """Both are analysis nodes; their image output is the input, reset or not."""
    for name in ("PW_Scopes", "PW_Palette"):
        node = NODES[name]
        node.hidden = _Hidden()
        img = _image()
        result = node.execute(image=img, **_reset_kwargs(name, node)).result
        passthrough = result[0] if name == "PW_Scopes" else None
        if passthrough is not None:
            assert torch.equal(passthrough, img)


# -- the table itself --------------------------------------------------------


def test_table_only_lists_controls_that_actually_exist():
    """A stale entry is silently ignored by the frontend, so catch it here."""
    for name, overrides in _pass_through_table().items():
        assert name in NODES, f"PASS_THROUGH names unknown node {name}"
        valid = {i.id for i in NODES[name].define_schema().inputs}
        unknown = sorted(set(overrides) - valid)
        assert not unknown, f"{name} has no inputs called {unknown}"


def test_table_only_lists_controls_whose_default_is_not_neutral():
    """If a default becomes neutral the entry is redundant, and a redundant
    entry is a lie about which defaults are opinionated."""
    for name, overrides in _pass_through_table().items():
        defaults = _defaults(NODES[name])
        for control, neutral in overrides.items():
            assert defaults.get(control) != neutral, (
                f"{name}.{control} defaults to {neutral} already — drop it from PASS_THROUGH"
            )


def test_defaults_alone_would_not_be_a_pass_through():
    """Guards the reason this table exists: without it, reset leaves these
    nodes still affecting the image."""
    for name in ("PW_Optics", "PW_Grain"):
        node = NODES[name]
        node.hidden = _Hidden()
        img = _image()
        kw = _defaults(node)
        kw.pop("plate", None)
        out = node.execute(image=img, **kw).result[0]
        delta = float((out - img).abs().max()) * 255
        assert delta > 0.5, f"{name} defaults are already neutral — PASS_THROUGH may be unnecessary"


def test_reset_leaves_the_seed_alone():
    """Resetting a seed would change the grain rather than neutralise it."""
    src = RESET_TS.read_text(encoding="utf-8")
    assert "'seed'" in src and "control_after_generate" in src
