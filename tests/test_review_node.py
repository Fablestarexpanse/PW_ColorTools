"""The node: collect, pass, or deliver - and never wait.

Each run does exactly one of three things. With a release waiting it delivers
the keepers and asks for no new image, so the sampler upstream is skipped. With
auto_pass on, live or queued, it sends the fresh image straight through.
Otherwise it drops the image in the tray and blocks downstream for that run.
"""

from __future__ import annotations

import math

import pytest
import torch

pytest.importorskip("comfy_api.latest", reason="needs ComfyUI on the path")

from comfy_execution.graph_utils import ExecutionBlocker  # noqa: E402

from pw_color import review  # noqa: E402
from pw_color.nodes import review as node_mod  # noqa: E402

Node = node_mod.PW_Review


@pytest.fixture(autouse=True)
def tray_node(monkeypatch):
    review._reset()
    monkeypatch.setattr(node_mod, "_node_id", lambda cls: "4")
    monkeypatch.setattr(node_mod, "_push", lambda *a, **k: None)
    yield
    review._reset()


def _frames(n: int = 1, start: float = 0.0, h: int = 8, w: int = 12) -> torch.Tensor:
    return torch.stack([torch.full((h, w, 3), start + i / 10.0) for i in range(n)])


def _out(result):
    return result.result[0]


def test_a_run_drops_its_frames_in_the_tray_and_blocks_downstream():
    out = _out(Node.execute(image=_frames(2), auto_pass=False))
    assert isinstance(out, ExecutionBlocker) and out.message is None
    assert review.get("4").count == 2


def test_runs_accumulate_rather_than_waiting():
    for i in range(4):
        Node.execute(image=_frames(1, i / 10), auto_pass=False)
    assert review.get("4").count == 4


def test_auto_pass_sends_the_fresh_image_through_and_leaves_the_tray_alone():
    image = _frames(1)
    assert torch.equal(_out(Node.execute(image=image, auto_pass=True)), image)
    assert review.get("4") is None


def test_the_live_switch_beats_the_value_queued_with_the_run():
    image = _frames(1)
    review.set_auto_pass("4", True)
    assert torch.equal(_out(Node.execute(image=image, auto_pass=False)), image), "switched on after queueing: must pass"
    review.set_auto_pass("4", False)
    out = _out(Node.execute(image=image, auto_pass=True))
    assert isinstance(out, ExecutionBlocker), "switched off after queueing: must collect"


def test_without_a_release_it_asks_for_the_image():
    assert Node.check_lazy_status(image=None, auto_pass=False) == ["image"]


def test_with_a_release_waiting_it_asks_for_nothing_so_the_sampler_is_skipped():
    review.add("4", _frames(2))
    review.request_release("4", [5, 3])
    assert Node.check_lazy_status(image=None, auto_pass=False) == []


def test_the_release_run_delivers_the_keepers_best_first():
    frames = _frames(3)
    review.add("4", frames)
    review.request_release("4", [2, 0, 5])
    out = _out(Node.execute(image=None, auto_pass=False))
    assert out.shape[0] == 2
    assert torch.equal(out[0], frames[2]) and torch.equal(out[1], frames[0])
    assert review.get("4") is None


def test_delivery_wins_even_with_auto_pass_on():
    review.add("4", _frames(2))
    review.request_release("4", [5, 0])
    review.set_auto_pass("4", True)
    out = _out(Node.execute(image=None, auto_pass=True))
    assert out.shape[0] == 1


def test_keepers_of_different_sizes_are_matched_to_the_best_one():
    """The same as core's Image Batch: later frames resized to the first."""
    review.add("4", _frames(1, h=8, w=12))
    review.add("4", _frames(1, 0.5, h=16, w=10))
    review.request_release("4", [3, 5])
    out = _out(Node.execute(image=None, auto_pass=False))
    assert out.shape == (2, 16, 10, 3), "the five-star frame sets the size"


def test_a_release_that_vanished_between_check_and_execute_blocks_quietly():
    out = _out(Node.execute(image=None, auto_pass=False))
    assert isinstance(out, ExecutionBlocker) and out.message is None


def test_without_a_node_id_it_passes_through_rather_than_collecting_for_nobody(monkeypatch):
    monkeypatch.setattr(node_mod, "_node_id", lambda cls: None)
    image = _frames(1)
    assert torch.equal(_out(Node.execute(image=image, auto_pass=False)), image)
    assert review.get("4") is None


def test_the_schema():
    schema = Node.define_schema()
    assert schema.node_id == "PW_Review"
    assert [i.id for i in schema.inputs] == ["image", "auto_pass"], "widget order is a saved-workflow promise"
    assert schema.inputs[0].lazy is True, "without a lazy image the release run would re-run the sampler"


def test_every_run_executes():
    value = Node.fingerprint_inputs()
    assert isinstance(value, float) and math.isnan(value)
