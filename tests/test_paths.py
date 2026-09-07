"""Where the pack's shipped assets are.

One module states the layout, replacing seven derivations at two depths. If the
layout moves, this is what says so.
"""

from __future__ import annotations

from pw_color.paths import CURVE_PRESETS, GRAIN_DIR, LOOK_PRESETS, PACK_ROOT


def test_every_shipped_asset_location_exists():
    """These are the seven hardcoded derivations replaced by one module; if the
    layout moves, this is what says so."""
    assert (PACK_ROOT / "pw_color").is_dir(), "PACK_ROOT is not the repository root"
    assert LOOK_PRESETS.is_file()
    assert CURVE_PRESETS.is_file()
    assert GRAIN_DIR.is_dir()


