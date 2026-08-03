import torch

from pw_color import colour


def _grid(n: int = 17) -> torch.Tensor:
    a = torch.linspace(0.0, 1.0, n)
    r, g, b = torch.meshgrid(a, a, a, indexing="ij")
    return torch.stack((r, g, b), dim=-1).reshape(-1, 3)


def test_srgb_linear_round_trip():
    x = _grid()
    assert torch.allclose(colour.linear_to_srgb(colour.srgb_to_linear(x)), x, atol=1e-6)


def test_srgb_linear_known_values():
    # The two anchors every implementation must agree on.
    assert abs(colour.srgb_to_linear(torch.tensor(1.0)).item() - 1.0) < 1e-6
    assert abs(colour.srgb_to_linear(torch.tensor(0.5)).item() - 0.21404114) < 1e-6


def test_srgb_linear_handles_negatives():
    """Odd extension: intermediate overshoot must survive a round trip."""
    x = torch.tensor([-0.4, -0.01, 0.0, 1.5])
    assert torch.allclose(colour.linear_to_srgb(colour.srgb_to_linear(x)), x, atol=1e-5)


def test_oklab_round_trip():
    x = _grid()
    back = colour.oklab_to_linear(colour.linear_to_oklab(x))
    assert torch.allclose(back, x, atol=1e-5)


def test_oklab_white_is_L1():
    lab = colour.linear_to_oklab(torch.tensor([1.0, 1.0, 1.0]))
    assert abs(lab[0].item() - 1.0) < 1e-4
    assert abs(lab[1].item()) < 1e-4
    assert abs(lab[2].item()) < 1e-4


def test_oklab_grey_has_no_chroma():
    lab = colour.srgb_to_oklab(torch.tensor([[0.2, 0.2, 0.2], [0.7, 0.7, 0.7]]))
    assert torch.allclose(lab[:, 1:], torch.zeros(2, 2), atol=1e-6)


def test_oklch_round_trip():
    lab = colour.srgb_to_oklab(_grid(9))
    assert torch.allclose(colour.oklch_to_oklab(colour.oklab_to_oklch(lab)), lab, atol=1e-6)


def test_hex_round_trip():
    for h in ("#7F77DD", "#000000", "#FFFFFF", "#D96A6A"):
        assert colour.srgb_to_hex(colour.hex_to_srgb(h)) == h


def test_hex_short_form():
    assert colour.hex_to_srgb("#fff") == (1.0, 1.0, 1.0)
