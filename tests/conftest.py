"""Put ComfyUI on the path so the node tests actually run.

Roughly a third of the suite tests the nodes themselves, and those need
`comfy_api` importable. Each of those modules guards with `importorskip`, which
is correct — the pure-colour tests should still run for someone who has only
cloned the repo — but a guard that is never satisfied is worse than no test at
all: the suite reports green while the tests that check the shipped behaviour
quietly sit out.

So: find ComfyUI if it is anywhere findable, and if it is not, say so once at
the top of the run rather than leaving it to be inferred from a skip count.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _candidates():
    """Where ComfyUI plausibly is, most explicit first."""
    if env := os.environ.get("COMFYUI_PATH"):
        yield Path(env)
    # Installed the normal way: <ComfyUI>/custom_nodes/<this repo>/
    yield ROOT.parents[1]
    # Developed alongside a checkout: <dev dir>/{ComfyUI,this repo}/
    for parent in ROOT.parents[:3]:
        yield parent / "ComfyUI"


def _find_comfyui() -> Path | None:
    for path in _candidates():
        try:
            if (path / "comfy_api" / "latest").is_dir() and (path / "nodes.py").is_file():
                return path
        except OSError:  # a candidate that walks off the top of the drive
            continue
    return None


_comfyui = _find_comfyui()
if _comfyui is not None:
    sys.path.insert(0, str(_comfyui))


def pytest_report_header() -> str:
    if _comfyui is None:
        return (
            "pw_color: ComfyUI not found - node tests will SKIP. "
            "Set COMFYUI_PATH to run them."
        )
    return f"pw_color: ComfyUI at {_comfyui}"
