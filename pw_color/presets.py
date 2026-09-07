"""Shipped presets, loaded the same way for every node that has them.

Three rules, applied identically to PW Look's presets and PW Curves':

* **The code owns 'none'.** It is always the first option, whether or not the
  file describes it, so the sentinel exists in one layer rather than two.
* **An unknown id raises**, naming the node. The alternative is a node that
  quietly grades nothing and a user who cannot tell why.
* **Only successful reads are cached.** A malformed file costs the user their
  presets, not their node — and fixing it recovers without a restart, which is
  what an `lru_cache` around the read could not do.

Successes are cached because this runs during schema construction, which the
frontend hits often.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

__all__ = ["NONE", "load_presets", "preset_ids", "preset_name", "resolve_preset"]

_log = logging.getLogger("PW_Color")

#: Schema version of a presets file. Checked like LOOK_SCHEMA and
#: PALETTE_SCHEMA are, so a file from a newer build says so rather than
#: silently losing whatever it added.
PRESETS_SCHEMA = 1

#: The "leave it alone" entry every preset combo carries as its first option.
NONE = "none"


class _PresetCache:
    """Successful reads, and the files already complained about.

    A class rather than two module-level dicts so the two pieces of state that
    have to agree - a path is cached *or* it is known-bad, never both - live in
    one place with the rule between them.
    """

    def __init__(self) -> None:
        self.good: dict[Path, dict[str, dict[str, Any]]] = {}
        self.warned: set[Path] = set()

    def clear(self) -> None:
        self.good.clear()
        self.warned.clear()


_presets = _PresetCache()


def _read(path: Path) -> tuple[dict[str, dict], bool]:
    """Parse a presets file into ``({id: preset}, read_succeeded)``, 'none' first.

    On failure the mapping holds 'none' alone — never empty, because 'none' is
    the code's to supply — and the flag is False so the caller knows not to
    cache it. A missing or malformed preset file should cost the user their
    presets, not their node.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        schema = int(data.get("schema", PRESETS_SCHEMA))
        if schema > PRESETS_SCHEMA:
            raise ValueError(f"presets use schema {schema}, this build understands up to {PRESETS_SCHEMA}")
        entries = list(data["presets"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if path not in _presets.warned:
            _presets.warned.add(path)
            _log.warning("PW Color: could not read presets from %s (%s); continuing with none only", path, exc)
        return {NONE: {"id": NONE, "name": "None", "params": {}}}, False

    presets = {NONE: {"id": NONE, "name": "None", "params": {}}}
    for entry in entries:
        if isinstance(entry, dict) and "id" in entry:
            presets[str(entry["id"])] = entry
    return presets, True


def load_presets(path: Path) -> dict[str, dict]:
    """Presets from ``path``, read once per successful load."""
    cached = _presets.good.get(path)
    if cached is not None:
        return cached
    presets, ok = _read(path)
    if ok:
        # A success caches, and clears the complaint so the *next* failure is
        # reported rather than swallowed as already-known.
        _presets.good[path] = presets
        _presets.warned.discard(path)
    return presets


def preset_ids(path: Path) -> list[str]:
    """Combo options for a preset input — 'none' first, then the file's order."""
    return list(load_presets(path))


def resolve_preset(path: Path, preset: str, node: str) -> dict[str, Any]:
    """The whole preset record for ``preset``, or ``{}`` for 'none'.

    The record, not its ``params``: callers want the name too, and returning
    half of it would mean a second lookup for the other half.

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
