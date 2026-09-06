"""Shipped presets, loaded the same way for every node that has them.

PW Look and PW Curves each had their own copy of this — same `lru_cache`, same
`json.loads`, same bare `except (OSError, ValueError): return {}` — and the
copies had already disagreed on the two things that matter:

* **Where 'none' comes from.** Curves synthesised it in code; Look expected the
  JSON to ship it, and carried a defensive ``or ["none"]`` for when it didn't.
  So the sentinel existed twice, in two layers, with a fallback papering over
  the gap. Here the code owns it: 'none' is always first and always present,
  whether or not the file describes it.
* **What an unknown preset does.** Curves raised; Look silently graded nothing.
  A typo'd preset id in a saved workflow is a mistake either way, and one of the
  two behaviours told the user about it. Now both do.

The third shared bug was the caching. A read failure was cached forever by
`lru_cache` and never logged, so a malformed `presets.json` meant an empty
preset list for the life of the process with nothing in the log to say why.
Successes are still cached — this runs during schema construction, which the
frontend hits often — but failures are not, and they are logged once.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

__all__ = ["NONE", "load_presets", "preset_ids", "preset_name", "resolve_preset"]

log = logging.getLogger(__name__)

#: The "leave it alone" entry every preset combo carries as its first option.
NONE = "none"

_cache: dict[Path, dict[str, dict]] = {}
_warned: set[Path] = set()


def _read(path: Path) -> dict[str, dict]:
    """Parse a presets file into ``{id: preset}``, 'none' first.

    Returns an empty mapping on failure, having said so in the log — a missing
    or malformed preset file should cost the user their presets, not their node.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = list(data["presets"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if path not in _warned:
            _warned.add(path)
            log.warning("PW Color: could not read presets from %s (%s); continuing with none only", path, exc)
        return {NONE: {"id": NONE, "name": "None", "params": {}}}

    presets = {NONE: {"id": NONE, "name": "None", "params": {}}}
    for entry in entries:
        if isinstance(entry, dict) and "id" in entry:
            presets[str(entry["id"])] = entry
    return presets


def load_presets(path: Path) -> dict[str, dict]:
    """Presets from ``path``, read once per successful load."""
    cached = _cache.get(path)
    if cached is not None:
        return cached
    presets = _read(path)
    # Only a real read earns a cache entry; a failure should recover if the
    # user fixes the file and reloads the node pack.
    if path not in _warned:
        _cache[path] = presets
    return presets


def preset_ids(path: Path) -> list[str]:
    """Combo options for a preset input — 'none' first, then the file's order."""
    return list(load_presets(path))


def resolve_preset(path: Path, preset: str, node: str) -> dict:
    """The parameters for ``preset``, or ``{}`` for 'none'.

    Raises on an id that is not in the file, because the alternative is a node
    that quietly does nothing and a user who cannot tell why.
    """
    if not preset or preset == NONE:
        return {}
    found = load_presets(path).get(preset)
    if found is None:
        raise ValueError(f"{node}: unknown preset {preset!r}")
    return found


def preset_name(path: Path, preset: str) -> str:
    """The display name for ``preset``, or empty for 'none' and unknowns."""
    if not preset or preset == NONE:
        return ""
    return str(load_presets(path).get(preset, {}).get("name", ""))
