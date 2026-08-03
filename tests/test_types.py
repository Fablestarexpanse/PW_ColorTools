import json

import pytest

from pw_color.types import Look, LookOp, Palette, Swatch, canonical_json, content_hash


def _look() -> Look:
    return Look(
        name="Faded matte",
        ops=[
            LookOp(type="curves", params={"luma": [[0.0, 0.06], [0.5, 0.5], [1.0, 0.94]], "preserve_hue": True}),
            LookOp(type="saturation", params={"amount": 0.88}, strength=0.75, blend="soft light"),
            LookOp(type="grain", params={"size": 1.4}, lut_safe=False, enabled=False),
        ],
        meta={"author": "promptwaffle", "unknown_future_key": {"nested": [1, 2, 3]}},
    )


def test_look_round_trip_is_lossless():
    a = _look()
    b = Look.from_dict(json.loads(a.to_json()))
    assert b.to_dict() == a.to_dict()


def test_look_round_trip_is_byte_stable():
    """An unchanged look must write byte-identical JSON, so .look files diff."""
    a = _look()
    assert Look.from_json(a.to_json()).to_json() == a.to_json()


def test_look_preserves_unknown_param_keys():
    """An older build must not silently drop parts of a newer look."""
    a = _look()
    b = Look.from_json(a.to_json())
    assert b.meta["unknown_future_key"] == {"nested": [1, 2, 3]}


def test_look_float_precision_survives():
    v = 0.1 + 0.2  # 0.30000000000000004
    a = Look(ops=[LookOp(type="saturation", params={"amount": v})])
    b = Look.from_json(a.to_json())
    assert b.ops[0].params["amount"] == v


def test_look_rejects_future_schema():
    d = _look().to_dict()
    d["schema"] = 99
    with pytest.raises(ValueError, match="schema 99"):
        Look.from_dict(d)


def test_look_rejects_missing_schema():
    with pytest.raises(ValueError):
        Look.from_dict({"ops": []})


def test_look_rejects_unknown_blend_mode():
    with pytest.raises(ValueError, match="blend"):
        LookOp.from_dict({"type": "saturation", "blend": "divide"})


def test_lut_exportable_ignores_disabled_ops():
    look = _look()
    assert look.lut_exportable  # the grain op is disabled
    look.ops[2].enabled = True
    assert not look.lut_exportable


def test_appended_does_not_mutate():
    a = Look(ops=[LookOp(type="exposure")])
    b = a.appended(LookOp(type="saturation"))
    assert len(a.ops) == 1 and len(b.ops) == 2


def _palette() -> Palette:
    return Palette(
        source_hash="abc123",
        sort="coverage",
        colors=[
            Swatch("#7F77DD", (0.55123, -0.0123, -0.15432), 0.412),
            Swatch("#1B1A20", (0.12, 0.001, -0.004), 0.318),
            Swatch("#E0A44C", (0.75, 0.03, 0.12), 0.27),
        ],
    )


def test_palette_round_trip_is_lossless():
    a = _palette()
    assert Palette.from_json(a.to_json()).to_dict() == a.to_dict()


def test_palette_hex_string():
    assert _palette().hex_string() == "#7F77DD, #1B1A20, #E0A44C"


def test_palette_rejects_bad_oklab():
    with pytest.raises(ValueError, match="3 components"):
        Swatch.from_dict({"hex": "#000000", "oklab": [0.1, 0.2], "coverage": 1.0})


def test_palette_ase_export_header():
    data = _palette().to_ase_bytes()
    assert data[:4] == b"ASEF"
    assert int.from_bytes(data[8:12], "big") == 3


def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert content_hash({"b": 1, "a": 2}) == content_hash({"a": 2, "b": 1})
