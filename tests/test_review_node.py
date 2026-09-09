"""The gate itself: what it passes, what it drops, and how it stops waiting.

The node is the one place in this pack where an execution can sit still, so
the tests that matter are about the ways it must let go: a release, a cancel,
and a node that could never be released at all.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
import torch

pytest.importorskip("comfy_api.latest", reason="needs ComfyUI on the path")

from comfy_execution.graph_utils import ExecutionBlocker  # noqa: E402

from pw_color import review  # noqa: E402
from pw_color.nodes import review as node_mod  # noqa: E402


@pytest.fixture(autouse=True)
def held_node(monkeypatch):
    """A node id the hold can be addressed by, and no push to a server."""
    review._holds.clear()
    monkeypatch.setattr(node_mod, "_node_id", lambda cls: "4")
    monkeypatch.setattr(node_mod, "_push", lambda *a, **k: None)
    yield
    review._holds.clear()


def _batch(n: int = 3) -> torch.Tensor:
    """A batch whose frames are each a different flat grey, so they compare."""
    return torch.stack([torch.full((4, 4, 3), i / 10.0) for i in range(n)])


def _run(image: torch.Tensor, **kwargs):
    return asyncio.run(node_mod.PW_Review.execute(image, **kwargs))


def _release_soon(ratings, delay: float = 0.05) -> None:
    """Release from another thread, the way the route does."""
    threading.Timer(delay, lambda: review.release("4", ratings)).start()


def test_auto_pass_returns_the_batch_untouched_and_holds_nothing():
    image = _batch(3)
    out = _run(image, auto_pass=True)
    assert torch.equal(out.result[0], image)
    assert review.get_hold("4") is None


def test_a_release_sends_the_rated_images_best_first():
    image = _batch(4)
    _release_soon([3, 0, 5, 1])
    kept = _run(image).result[0]
    assert kept.shape[0] == 3
    assert torch.equal(kept[0], image[2])
    assert torch.equal(kept[1], image[0])
    assert torch.equal(kept[2], image[3])


def test_rating_nothing_blocks_what_is_downstream():
    _release_soon([0, 0, 0])
    out = _run(_batch(3))
    assert isinstance(out.result[0], ExecutionBlocker)
    assert out.result[0].message is None, "rejecting every frame is a decision, not an error"


def test_the_hold_is_gone_once_the_run_continues():
    _release_soon([5, 0, 0])
    _run(_batch(3))
    assert review.get_hold("4") is None


def test_it_waits_rather_than_passing_the_batch_straight_through():
    """The point of the node: without a release it does not return."""
    with pytest.raises(TimeoutError):
        asyncio.run(asyncio.wait_for(node_mod.PW_Review.execute(_batch(2)), timeout=0.6))
    review.release("4", [0, 0])


def test_cancelling_while_held_stops_the_run_and_clears_the_hold(monkeypatch):
    polls = {"n": 0}

    def interrupt_after_a_few_polls():
        polls["n"] += 1
        if polls["n"] > 2:
            raise KeyboardInterrupt("cancelled")

    monkeypatch.setattr(node_mod, "_check_interrupt", interrupt_after_a_few_polls)
    with pytest.raises(KeyboardInterrupt):
        _run(_batch(2))
    assert review.get_hold("4") is None, "a cancelled run must not leave a hold behind"


def test_without_a_node_id_it_passes_through_rather_than_waiting_forever(monkeypatch):
    monkeypatch.setattr(node_mod, "_node_id", lambda cls: None)
    image = _batch(2)
    out = _run(image)
    assert torch.equal(out.result[0], image)
    assert review.get_hold("4") is None


def test_the_schema_declares_what_the_panel_and_the_reset_need():
    schema = node_mod.PW_Review.define_schema()
    assert schema.node_id == "PW_Review"
    assert [i.id for i in schema.inputs] == ["image", "auto_pass"], "widget order is a saved-workflow promise"
    assert schema.hidden and any("unique_id" in str(h) for h in schema.hidden), (
        "without unique_id the node cannot be addressed and can never be released"
    )


def test_it_never_reports_the_same_fingerprint_twice():
    """A re-queued prompt has to stop at the gate again, not reuse last time's."""
    first = node_mod.PW_Review.fingerprint_inputs()
    assert first != first or first != node_mod.PW_Review.fingerprint_inputs()
