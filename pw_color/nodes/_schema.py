"""The parts of a schema every colour node in the pack shares.

Five of the eight nodes end their schema the same way: an optional ``look_in``,
then an IMAGE and a LOOK output. That is not incidental repetition — it *is*
the contract that makes the pack a chain rather than eight separate nodes, and
`tests/test_audit.py` enforces half of it (a node that emits a LOOK must be able
to receive one).

Writing it out five times meant the tooltip existed in two versions and three
nodes had none, and it meant the contract was something you inferred by reading
five files rather than something stated once.
"""

from __future__ import annotations

from comfy_api.latest import io

__all__ = ["look_in", "image_and_look_outputs"]


def look_in() -> io.Input:
    """The upstream grade stack this node appends to.

    Optional on every node: images flow whether or not the LOOK wire is
    connected, and a user who only wants the image should not have to know the
    type exists.
    """
    return io.Custom("LOOK").Input(
        "look_in",
        optional=True,
        tooltip="Upstream grade stack. This node appends to it.",
    )


def image_and_look_outputs() -> list[io.Output]:
    """The graded image, and the grade stack that produced it.

    Image first, so a user rewiring one node for another does not have to check
    which socket is which. Every node that grades an image uses this.

    PW Look I/O is the exception and declares its own outputs: it leads with the
    LOOK because that is its subject, and its sockets cannot be reordered now
    without silently reconnecting the wires in every saved workflow — links are
    stored by slot index, so swapping two would move a wire rather than break
    it, which is the worse failure.
    """
    return [
        io.Image.Output(display_name="image"),
        io.Custom("LOOK").Output(display_name="look"),
    ]
