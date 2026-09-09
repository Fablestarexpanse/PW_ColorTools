"""PW Review — a batch, stopped for a look.

Every other node in this pack is a pure function of its inputs. This one waits
for a person, and *where* it waits is the design: inside `execute`, as a
coroutine, so the executor parks it as pending and the server keeps answering
while a run is held.

Ratings are one to five stars, and leaving an image unrated is how you reject
it: only rated images continue, ordered best first. One gesture doing two
jobs, which is why there is no separate reject button to forget to press.

`auto_pass` is the way out for unattended work. It is also what the pack's
reset turns on, because reset here means the image comes out as it went in,
and for a gate that means it stops gating.
"""

from __future__ import annotations

import asyncio
import logging

import torch
from comfy_api.latest import io

from ..preview_cache import _executing_node_id
from ..review import close, kept_order, open_hold

__all__ = ["PW_Review", "NODES", "POLL_SECONDS"]

_log = logging.getLogger("PW_Color")

#: How often a held run asks whether it may continue, in seconds. It is also
#: how often Cancel is noticed, so it is short enough to feel immediate and
#: long enough to cost nothing while a batch sits there for a minute.
POLL_SECONDS = 0.2


def _node_id(cls: type) -> str | None:
    """The id of the node executing right now, or None with a reason logged."""
    return _executing_node_id(cls, quiet=False)


def _push(event: str, data: dict) -> None:
    """Tell the browser a batch is waiting. Never raises.

    `send_sync` defers through `call_soon_threadsafe`, so calling it from the
    executor's own thread is safe. Outside a running server there is nobody to
    tell, which is not an error.
    """
    try:
        from server import PromptServer

        PromptServer.instance.send_sync(event, data)
    except Exception:  # pragma: no cover - no server in tests
        _log.debug("PW Color: could not push %s", event, exc_info=True)


def _check_interrupt() -> None:
    """Raise if the user pressed Cancel. Silent outside ComfyUI."""
    try:
        from comfy.model_management import throw_exception_if_processing_interrupted
    except Exception:  # pragma: no cover - outside ComfyUI
        return
    throw_exception_if_processing_interrupted()


class PW_Review(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="PW_Review",
            display_name="PW Review",
            category="PW Color",
            search_aliases=["review", "rate", "stars", "gate", "pick", "cull", "approve", "hold"],
            description=(
                "Holds the batch here and waits. Rate each image one to five stars, then release: "
                "the rated ones carry on, best first, and the unrated are dropped. Turn on auto_pass "
                "to let runs through untouched. The batch stays in memory while it waits, and the "
                "run keeps its place in the queue."
            ),
            inputs=[
                io.Image.Input("image"),
                io.Boolean.Input(
                    "auto_pass",
                    default=False,
                    tooltip="Send every image straight through without stopping. For unattended runs.",
                ),
            ],
            outputs=[io.Image.Output(display_name="image")],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs) -> float:
        # Never equal to itself, so a re-queued prompt stops at the gate again
        # instead of being handed the batch it was given last time.
        return float("nan")

    @classmethod
    async def execute(cls, image: torch.Tensor, auto_pass: bool = False) -> io.NodeOutput:
        if auto_pass:
            return io.NodeOutput(image)

        node_id = _node_id(cls)
        if node_id is None:
            # No panel could ever address this hold, so waiting would be
            # waiting for ever. Passing through is the failure that costs the
            # least, and the warning says why the gate did nothing.
            _log.warning("PW Color: PW Review has no node id, so it cannot hold; passing the batch through")
            return io.NodeOutput(image)

        hold = open_hold(node_id, image)
        try:
            _push("pw_color.review", {"node_id": node_id, "count": hold.count})
            while not hold.released.is_set():
                _check_interrupt()
                await asyncio.sleep(POLL_SECONDS)
            keep = kept_order(hold.ratings)
            if not keep:
                from comfy_execution.graph_utils import ExecutionBlocker

                # Rejecting every frame is a decision, not an error, so this
                # blocks silently and the panel is what says nothing was kept.
                #
                # The blocker travels as the output *value* rather than through
                # NodeOutput's `block_execution` argument, and it has to:
                # `block_execution=None` reads as "do not block", and an output
                # carrying no args never reaches the code that would apply it.
                return io.NodeOutput(ExecutionBlocker(None))
            return io.NodeOutput(image[keep])
        finally:
            # An interrupt, a failure upstream, or a browser that never answers
            # must not leave a hold for the next run to inherit.
            close(node_id)
            _push("pw_color.review", {"node_id": node_id, "count": 0})


NODES = [PW_Review]
