"""Python mirror of the design system palette.

``web/src/theme.ts`` is the source of truth. This exists only because a few
things are drawn server-side — the palette swatch strip, and later the scope
renders — and they must match the node chrome exactly or the strip looks like it
came from a different product.

``tests/test_theme.py`` parses ``theme.ts`` and asserts these agree, so the two
cannot drift. If you change a colour, change it there and this test will tell
you to change it here.
"""

from __future__ import annotations

THEME: dict[str, str] = {
    "panel": "#1B1A20",
    "header": "#272433",
    "surface": "#201E28",
    "well": "#131218",
    "chip": "#272433",
    "chipActive": "#3E3856",
    "border": "#4A4358",
    "borderSoft": "#3A3545",
    "grid": "#2A2733",
    "text": "#F0EEF8",
    "textDim": "#B9B5C8",
    "textMute": "#8F8AA3",
    "accent": "#7F77DD",
    "onAccent": "#1A172E",
}

CHANNEL: dict[str, str] = {
    "luma": "#F0EEF8",
    "r": "#D96A6A",
    "g": "#7FBF9E",
    "b": "#7FA8DD",
    "warm": "#E0A44C",
}

PORT: dict[str, str] = {
    "IMAGE": "#7FBF9E",
    "MASK": "#8F8AA3",
    "LOOK": "#7F77DD",
    "PALETTE": "#E0A44C",
}
