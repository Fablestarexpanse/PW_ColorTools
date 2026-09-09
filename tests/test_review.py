"""The hold: what it keeps, in what order, and for how long.

Every other module in this pack is a pure function of its inputs. This one
holds a batch while a person looks at it, so the tests are about state: that a
release wakes the run, that a superseded hold never leaves one waiting, and
that what a browser sends is taken as a suggestion rather than as gospel.
"""

from __future__ import annotations

import threading

import pytest
import torch

from pw_color import review


@pytest.fixture(autouse=True)
def no_holds():
    """No test inherits a hold from the one before it."""
    review._holds.clear()
    yield
    review._holds.clear()


def _batch(n: int = 3, h: int = 16, w: int = 24) -> torch.Tensor:
    g = torch.Generator().manual_seed(7)
    return torch.rand(n, h, w, 3, generator=g)


class TestKeptOrder:
    def test_orders_best_first_and_drops_unrated(self):
        assert review.kept_order([5, 3, 0, 4]) == [0, 3, 1]

    def test_equal_ratings_keep_input_order(self):
        assert review.kept_order([3, 5, 3, 5]) == [1, 3, 0, 2]

    def test_nothing_rated_keeps_nothing(self):
        assert review.kept_order([0, 0, 0]) == []

    def test_empty_is_empty(self):
        assert review.kept_order([]) == []


class TestHold:
    def test_open_encodes_one_thumb_and_view_per_image(self):
        hold = review.open_hold("4", _batch(3))
        assert hold.count == 3
        assert len(hold.thumbs) == 3 and len(hold.views) == 3
        assert all(b[:2] == b"\xff\xd8" for b in hold.thumbs)  # JPEG SOI
        assert hold.ratings == [0, 0, 0]
        assert not hold.released.is_set()

    def test_get_hold_finds_it_and_close_clears_it(self):
        review.open_hold("4", _batch(2))
        assert review.get_hold("4") is not None
        review.close("4")
        assert review.get_hold("4") is None

    def test_release_records_ratings_and_wakes_the_waiter(self):
        hold = review.open_hold("4", _batch(2))
        assert review.release("4", [3, 0]) is True
        assert hold.ratings == [3, 0]
        assert hold.released.is_set()

    def test_release_pads_and_clamps_what_the_browser_sent(self):
        hold = review.open_hold("4", _batch(3))
        review.release("4", [9, -2])
        assert hold.ratings == [5, 0, 0]

    def test_release_survives_rubbish_in_the_list(self):
        hold = review.open_hold("4", _batch(2))
        review.release("4", ["three", None])
        assert hold.ratings == [0, 0]

    def test_release_without_a_hold_says_so(self):
        assert review.release("nope", [1]) is False

    def test_opening_a_second_hold_releases_the_first(self):
        first = review.open_hold("4", _batch(2))
        review.open_hold("4", _batch(2))
        assert first.released.is_set(), "a superseded hold must never leave a run waiting forever"
        assert review.get_hold("4") is not first

    def test_a_waiting_thread_wakes_on_release(self):
        hold = review.open_hold("4", _batch(1))
        woke = threading.Event()
        threading.Thread(target=lambda: (hold.released.wait(5), woke.set()), daemon=True).start()
        review.release("4", [4])
        assert woke.wait(5), "release must wake a thread already waiting"

    def test_holds_are_kept_apart_by_node(self):
        a = review.open_hold("4", _batch(1))
        b = review.open_hold("9", _batch(2))
        review.release("9", [5, 5])
        assert b.released.is_set()
        assert not a.released.is_set(), "releasing one node must not release another"
