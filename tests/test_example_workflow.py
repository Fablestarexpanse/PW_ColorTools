"""The drop-in workflow has to be true on arrival.

`example_workflows/pw_color_basic.json` is the first thing most people will run,
and it makes two promises the README repeats: it is the whole pack wired in the
right order, and it starts neutral — dropping it in changes nothing until you
move a control. Neither promise survives on its own. Add a widget to a node and
the saved `widgets_values` silently shifts by one; change a default and "starts
neutral" quietly becomes "starts with a look on it".

So both are checked against the running nodes rather than trusted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

pytest.importorskip("comfy_api.latest", reason="needs ComfyUI on the path")

from pw_color.nodes import curves, grain, look, match_source, optics  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "example_workflows" / "pw_color_basic.json"

# Widget order as ComfyUI serialises it, which is schema order with
# `control_after_generate` inserted after each seed. Kept here so that adding a
# control fails this file loudly instead of shifting every value by one.
WIDGET_ORDER: dict[str, list[str]] = {
    "PW_MatchSource": ["strength", "space", "max_gain"],
    "PW_Look": [
        "preset", "exposure", "contrast", "highlights", "shadows", "whites", "blacks",
        "warmth", "tint", "vibrance", "saturation", "glow", "strength", "glow_radius",
        "glow_threshold", "blend", "hsl", "gradient_map", "gradient_blend",
        "reference_strength", "reference_mode", "quality",
    ],
    "PW_Curves": ["curves", "preserve_hue", "strength", "preset", "final_quality"],
    "PW_Optics": [
        "halation", "halation_radius", "halation_threshold", "vignette",
        "chromatic_aberration", "vignette_midpoint", "vignette_feather",
        "vignette_roundness",
    ],
    "PW_Grain": [
        "amount", "size", "shadows", "midtones", "highlights", "blend", "opacity",
        "seed", "control_after_generate", "vary_per_frame", "chroma", "plate",
        "red", "green", "blue", "dither",
    ],
}

# Not node inputs: frontend state, and the seed's control mode.
NOT_INPUTS = {"control_after_generate", "hsl", "preset", "locked"}


@pytest.fixture(scope="module")
def doc() -> dict:
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_type(doc) -> dict[str, dict]:
    nodes = [n for n in doc["nodes"] if str(n["type"]).startswith("PW_")]
    assert len(nodes) == len({n["type"] for n in nodes}), "expected one of each PW node"
    return {n["type"]: n for n in nodes}


def test_every_node_in_the_pack_is_present(by_type):
    """The pitch is 'the whole pack, wired'. Adding a node without adding it
    here leaves people to discover it on their own."""
    import re

    shipped = set()
    for p in (ROOT / "pw_color" / "nodes").glob("*.py"):
        shipped |= set(re.findall(r'node_id="(PW_\w+)"', p.read_text(encoding="utf-8")))
    assert shipped == set(by_type), f"workflow is missing {sorted(shipped - set(by_type))}"


def test_the_image_chain_runs_in_the_documented_order(doc, by_type):
    """Match source repairs before anything creative; the two spatial nodes go
    last, and grain goes after optics so nothing blurs it."""
    links = {l[0]: l for l in doc["links"]}  # id -> [id, from_node, from_slot, to_node, to_slot, type]
    ids = {n["id"]: n["type"] for n in doc["nodes"]}

    def image_source(node: dict) -> str | None:
        for inp in node.get("inputs", []):
            if inp.get("type") == "IMAGE" and inp.get("link") is not None:
                return ids[links[inp["link"]][1]]
        return None

    expected = [
        ("PW_MatchSource", "LoadImage"),
        ("PW_Look", "PW_MatchSource"),
        ("PW_Curves", "PW_Look"),
        ("PW_Optics", "PW_Curves"),
        ("PW_Grain", "PW_Optics"),
        ("PW_Scopes", "PW_Grain"),
    ]
    for node_type, upstream in expected:
        assert image_source(by_type[node_type]) == upstream, (
            f"{node_type} should take its image from {upstream}"
        )


def test_the_look_wire_runs_the_full_length_of_the_chain(doc, by_type):
    """The LOOK output is what makes the pack more than five separate nodes:
    it carries the accumulated grade to Look I/O so it can be exported."""
    ids = {n["id"]: n["type"] for n in doc["nodes"]}
    links = {l[0]: l for l in doc["links"]}
    look_io = by_type["PW_LookIO"]
    wired = [i for i in look_io["inputs"] if i["type"] == "LOOK" and i.get("link") is not None]
    assert wired, "PW_LookIO has nothing on its look input"
    assert ids[links[wired[0]["link"]][1]] == "PW_Grain", "the look wire should end at the last node"


def test_the_workflow_names_an_image_comfyui_ships_with(by_type, doc):
    """A missing input file greets a first-time user with a red node."""
    load = next(n for n in doc["nodes"] if n["type"] == "LoadImage")
    assert load["widgets_values"][0] == "example.png"


# -- starts neutral ----------------------------------------------------------


def _values(node: dict, node_type: str) -> dict:
    names = WIDGET_ORDER[node_type]
    saved = node["widgets_values"]
    assert len(saved) == len(names), (
        f"{node_type} saved {len(saved)} widget values but WIDGET_ORDER names "
        f"{len(names)} — a control was added or removed, so every value below "
        f"it is being read under the wrong name"
    )
    return {n: v for n, v in zip(names, saved) if n not in NOT_INPUTS}


def _image(seed: int = 7) -> torch.Tensor:
    return torch.rand(1, 48, 64, 3, generator=torch.Generator().manual_seed(seed))


class _Hidden:
    unique_id = "example-workflow-test"


@pytest.mark.parametrize(
    "node_type,cls",
    [
        ("PW_Look", look.PW_Look),
        ("PW_Curves", curves.PW_Curves),
        ("PW_Optics", optics.PW_Optics),
        ("PW_Grain", grain.PW_Grain),
    ],
)
def test_the_workflow_starts_neutral(by_type, node_type, cls):
    """Dropped in and run, it should hand back the image it was given."""
    cls.hidden = _Hidden()
    img = _image()
    out = cls.execute(image=img, **_values(by_type[node_type], node_type)).result[0]
    delta = float((out - img).abs().max()) * 255
    assert delta < 0.5, f"{node_type} changes the image by {delta:.3f} code values on load"


def test_match_source_starts_neutral(by_type):
    img, other = _image(1), _image(2)
    kw = _values(by_type["PW_MatchSource"], "PW_MatchSource")
    out = match_source.PW_MatchSource.execute(original=other, processed=img, **kw).result[0]
    assert float((out - img).abs().max()) * 255 < 0.5
