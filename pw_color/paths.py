"""Where the pack's shipped assets live.

``looks/presets.json`` and the ``grain/`` plates were located by walking up from
whichever module wanted them, which meant ``parents[1]`` in ``pw_color/`` and
``parents[2]`` in ``pw_color/nodes/`` — seven derivations at two depths, all
encoding the same fact about the layout. Moving a module between those two
directories silently changed where it looked.

One module holds the layout, so there is one thing to change if it ever moves.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["PACK_ROOT", "LOOKS_DIR", "CURVE_PRESETS", "LOOK_PRESETS", "GRAIN_DIR"]

#: The repository root — the directory ComfyUI loads as the custom node.
PACK_ROOT = Path(__file__).resolve().parents[1]

LOOKS_DIR = PACK_ROOT / "looks"
LOOK_PRESETS = LOOKS_DIR / "presets.json"
CURVE_PRESETS = LOOKS_DIR / "curves" / "presets.json"

#: Grain plates, scanned at schema-construction time.
GRAIN_DIR = PACK_ROOT / "grain"
