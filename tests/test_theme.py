"""Pins the Python theme mirror to `web/src/theme.ts`.

The TS file is the source of truth. The Python copy exists because the swatch
strip is rendered server-side and must match the node chrome exactly. Parsing
the TS rather than trusting a comment is what makes "keep them in sync" a fact
instead of an intention.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pw_color.theme import CHANNEL, PORT, THEME

THEME_TS = Path(__file__).resolve().parents[1] / "web" / "src" / "theme.ts"


def _parse_block(name: str) -> dict[str, str]:
    """Pull `name: { key: '#RRGGBB', ... }` out of theme.ts."""
    src = THEME_TS.read_text(encoding="utf-8")
    m = re.search(rf"\b{name}:\s*\{{(.*?)\n  \}}", src, re.S)
    if not m:
        pytest.fail(f"could not find the `{name}` block in theme.ts")
    return {k: v.upper() for k, v in re.findall(r"(\w+):\s*'(#[0-9a-fA-F]{6})'", m.group(1))}


@pytest.mark.parametrize(
    "block,python",
    [("color", THEME), ("channel", CHANNEL), ("port", PORT)],
)
def test_python_theme_matches_typescript(block: str, python: dict[str, str]):
    ts = _parse_block(block)
    assert ts, f"parsed no colours from the `{block}` block"
    py = {k: v.upper() for k, v in python.items()}
    assert py == ts, (
        f"theme.ts and pw_color/theme.py disagree on `{block}`. "
        f"theme.ts is the source of truth — update the Python mirror."
    )


def test_every_colour_is_a_full_hex_triplet():
    for name, block in (("THEME", THEME), ("CHANNEL", CHANNEL), ("PORT", PORT)):
        for key, value in block.items():
            assert re.fullmatch(r"#[0-9A-F]{6}", value.upper()), f"{name}.{key} = {value!r}"
