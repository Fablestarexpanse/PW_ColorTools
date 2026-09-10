"""PW Review — a tray for generated images.

Each run does one of three things, and none of them waits:

- **Collect.** The run's frames go in the node's tray and downstream is blocked
  for that run, silently. Generation carries on, so Run set to four fills the
  tray with four frames unattended.
- **Pass.** With auto_pass on, the fresh image goes straight through and the
  tray is left alone. The switch is read live, so flipping it reaches runs
  that were already queued.
- **Deliver.** After someone rates the tray and presses release, the next run
  sends the keepers downstream, best first. It asks for no new image, so the
  sampler and everything upstream of this node are skipped for that run.

A frame can also be **re-run**: the panel queues the prompt that made it
again with new seeds, marked with the frame's id, and the result is collected
right after the frame it re-ran. That is why the node keeps each run's prompt
and workflow, which it reads from its hidden inputs.

The delivery part is what the image input being *lazy* is for. ComfyUI asks the
node, through `check_lazy_status`, which lazy inputs it needs before running
anything upstream; a node with a delivery waiting says "none".

Ratings are one to five stars, and leaving a frame unrated is how you reject
it. `auto_pass` is also what the pack's reset turns on, because reset here
means the image comes out as it went in.
"""

from __future__ import annotations

import logging

import torch
from comfy_api.latest import io

from .. import review
from ..preview_cache import _executing_node_id

__all__ = ["PW_Review", "NODES"]

_log = logging.getLogger("PW_Color")


def _node_id(cls: type) -> str | None:
    """The id of the node executing right now, or None with a reason logged."""
    return _executing_node_id(cls, quiet=False)


def _push(node_id: str) -> None:
    """Tell the browser the tray changed. Never raises.

    `send_sync` defers through `call_soon_threadsafe`, so calling it from the
    executor's thread is safe. Outside a running server there is nobody to
    tell, which is not an error.
    """
    tray = review.get(node_id)
    data = {"node_id": node_id, "count": tray.count if tray else 0}
    try:
        from server import PromptServer

        PromptServer.instance.send_sync("pw_color.review", data)
    except Exception:  # pragma: no cover - no server in tests
        _log.debug("PW Color: could not push the tray state", exc_info=True)


def _run_record(cls: type, node_id: str) -> tuple[object, object, int | None]:
    """This run's prompt and workflow, and the frame it re-runs, if any."""
    hidden = getattr(cls, "hidden", None)
    prompt = getattr(hidden, "prompt", None)
    extra = getattr(hidden, "extra_pnginfo", None)
    workflow = extra.get("workflow") if isinstance(extra, dict) else None
    return prompt, workflow, review.rerun_origin(prompt, node_id)


def _blocked() -> io.NodeOutput:
    """Stop everything downstream of this node for this run, silently.

    The blocker travels as the output *value* rather than through NodeOutput's
    `block_execution` argument, and it has to: `block_execution=None` reads as
    "do not block", and an output carrying no args never reaches the code that
    would apply it.
    """
    from comfy_execution.graph_utils import ExecutionBlocker

    return io.NodeOutput(ExecutionBlocker(None))


def _as_batch(frames: list[torch.Tensor]) -> torch.Tensor:
    """One batch from the keepers, the best-rated frame setting the size.

    Frames from different runs can differ in size if the resolution changed
    between them. Core's Image Batch meets that by resizing the second input
    to the first; this does the same, with the first being the best keeper.
    """
    first = frames[0]
    h, w = first.shape[0], first.shape[1]
    matched = []
    for f in frames:
        if f.shape[0] != h or f.shape[1] != w:
            import comfy.utils

            f = comfy.utils.common_upscale(f.unsqueeze(0).movedim(-1, 1), w, h, "bilinear", "center")
            f = f.movedim(1, -1)[0]
        matched.append(f)
    return torch.stack(matched)


class PW_Review(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="PW_Review",
            display_name="PW Review",
            category="PW Color",
            search_aliases=["review", "rate", "stars", "tray", "pick", "cull", "approve", "keep"],
            description=(
                "Collects each run's images in a tray and lets the run finish. Rate them one to five "
                "stars when you are ready, then release: the next run sends the rated ones on, best "
                "first, and skips generation. Unrated images are dropped. auto_pass sends images "
                "straight through instead, and takes effect at once, even for runs already queued."
            ),
            inputs=[
                io.Image.Input("image", lazy=True),
                io.Boolean.Input(
                    "auto_pass",
                    default=False,
                    tooltip="Send every image straight through instead of collecting it. Takes effect at once.",
                ),
            ],
            outputs=[io.Image.Output(display_name="image")],
            hidden=[io.Hidden.unique_id, io.Hidden.prompt, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs) -> float:
        # Never equal to itself, so every run reaches the node: a run that was
        # served from the cache could neither collect nor deliver.
        return float("nan")

    @classmethod
    def check_lazy_status(cls, image=None, auto_pass: bool = False) -> list[str]:
        """Ask for a new image unless this run is the one delivering keepers."""
        node_id = _node_id(cls)
        if node_id is not None and review.release_pending(node_id):
            return []
        return ["image"] if image is None else []

    @classmethod
    def execute(cls, image: torch.Tensor | None = None, auto_pass: bool = False) -> io.NodeOutput:
        node_id = _node_id(cls)
        if node_id is None:
            # No panel could ever address this tray, so collecting would be
            # collecting for nobody. Passing through costs the least.
            _log.warning("PW Color: PW Review has no node id, so it cannot collect; passing the image through")
            return io.NodeOutput(image) if image is not None else _blocked()

        kept = review.take_release(node_id)
        if kept:
            _push(node_id)
            return io.NodeOutput(_as_batch(kept))

        if image is None:
            # The check saw a release waiting and asked for no image, and the
            # release was cleared before this ran. Nothing to send.
            return _blocked()

        if review.auto_pass(node_id, auto_pass):
            return io.NodeOutput(image)

        prompt, workflow, rerun_of = _run_record(cls, node_id)
        review.add(node_id, image, prompt=prompt, workflow=workflow, rerun_of=rerun_of)
        _push(node_id)
        return _blocked()


NODES = [PW_Review]
