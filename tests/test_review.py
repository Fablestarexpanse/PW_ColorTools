"""The tray: what it collects, what leaves it, and in what order.

PW Review collects images across runs instead of holding a run while someone
looks. The state that used to live in a waiting execution now lives here, so
the tests are about that state: frames accumulate, ratings survive between
arrivals, a release takes exactly the frames that were rated, and anything that
arrives while a release is on its way is kept for the next review.
"""

from __future__ import annotations

import pytest
import torch

from pw_color import review


@pytest.fixture(autouse=True)
def empty():
    review._reset()
    yield
    review._reset()


def _frames(n: int, start: float = 0.0, h: int = 8, w: int = 12) -> torch.Tensor:
    """Frames that are each one flat grey, so it is obvious which is which."""
    return torch.stack([torch.full((h, w, 3), start + i / 10.0) for i in range(n)])


class TestKeptOrder:
    def test_orders_best_first_and_drops_unrated(self):
        assert review.kept_order([5, 3, 0, 4]) == [0, 3, 1]

    def test_equal_ratings_keep_arrival_order(self):
        assert review.kept_order([3, 5, 3, 5]) == [1, 3, 0, 2]

    def test_nothing_rated_keeps_nothing(self):
        assert review.kept_order([0, 0]) == []
        assert review.kept_order([]) == []


class TestCollecting:
    def test_frames_accumulate_across_runs(self):
        review.add("4", _frames(1))
        review.add("4", _frames(1, 0.5))
        review.add("4", _frames(2, 0.8))
        tray = review.get("4")
        assert tray.count == 4
        assert len(tray.thumbs) == 4 and len(tray.views) == 4
        assert tray.ratings == [0, 0, 0, 0]

    def test_each_frame_is_kept_whole_and_off_the_gpu_path(self):
        review.add("4", _frames(2))
        tray = review.get("4")
        assert tray.images[0].shape == (8, 12, 3)
        assert tray.images[0].device.type == "cpu"

    def test_thumbnails_are_jpeg(self):
        review.add("4", _frames(1))
        assert review.get("4").thumbs[0][:2] == b"\xff\xd8"

    def test_ratings_survive_new_arrivals(self):
        review.add("4", _frames(2))
        review.set_ratings("4", [4, 2])
        review.add("4", _frames(1, 0.5))
        assert review.get("4").ratings == [4, 2, 0]

    def test_trays_are_kept_apart_by_node(self):
        review.add("4", _frames(1))
        review.add("9", _frames(3))
        assert review.get("4").count == 1
        assert review.get("9").count == 3

    def test_an_empty_tray_is_no_tray(self):
        assert review.get("4") is None


class TestRatings:
    def test_are_clamped_and_padded(self):
        review.add("4", _frames(3))
        review.set_ratings("4", [9, -1])
        assert review.get("4").ratings == [5, 0, 0]

    def test_rubbish_counts_as_unrated(self):
        review.add("4", _frames(2))
        review.set_ratings("4", ["three", None])
        assert review.get("4").ratings == [0, 0]

    def test_rating_an_empty_tray_is_refused(self):
        assert review.set_ratings("4", [1]) is False


class TestRelease:
    def test_request_reports_how_many_will_leave(self):
        review.add("4", _frames(4))
        assert review.request_release("4", [3, 0, 5, 1]) == 3
        assert review.release_pending("4")

    def test_the_release_takes_the_rated_frames_best_first(self):
        frames = _frames(4)
        review.add("4", frames)
        review.request_release("4", [3, 0, 5, 1])
        kept = review.take_release("4")
        assert [float(k[0, 0, 0]) for k in kept] == [float(frames[2, 0, 0, 0]), float(frames[0, 0, 0, 0]), float(frames[3, 0, 0, 0])]
        assert review.get("4") is None, "a delivered tray is empty"
        assert not review.release_pending("4")

    def test_releasing_nothing_rated_just_empties_the_tray(self):
        review.add("4", _frames(2))
        assert review.request_release("4", [0, 0]) == 0
        assert review.get("4") is None
        assert not review.release_pending("4"), "nothing to deliver, so no run should wait for it"

    def test_frames_that_arrive_after_the_request_stay_for_next_time(self):
        review.add("4", _frames(2))
        review.request_release("4", [5, 0])
        review.add("4", _frames(1, 0.7))
        kept = review.take_release("4")
        assert len(kept) == 1
        tray = review.get("4")
        assert tray is not None and tray.count == 1, "the late frame was not part of that review"
        assert tray.ratings == [0]

    def test_take_without_a_request_is_none(self):
        review.add("4", _frames(1))
        assert review.take_release("4") is None
        assert review.get("4").count == 1

    def test_clear_drops_the_tray_and_any_pending_release(self):
        review.add("4", _frames(2))
        review.request_release("4", [5, 5])
        review.clear("4")
        assert review.get("4") is None
        assert not review.release_pending("4")


class TestLiveAutoPass:
    def test_falls_back_to_the_widget_until_the_panel_says_otherwise(self):
        assert review.auto_pass("4", default=False) is False
        assert review.auto_pass("4", default=True) is True

    def test_the_live_setting_wins_over_the_queued_widget_value(self):
        """The whole point: a run queued with auto_pass off must pass once the
        switch has been turned on, and the other way round."""
        review.set_auto_pass("4", True)
        assert review.auto_pass("4", default=False) is True
        review.set_auto_pass("4", False)
        assert review.auto_pass("4", default=True) is False


def test_the_epoch_moves_whenever_frames_leave_so_the_panel_forgets_its_indices():
    review.add("4", torch.rand(3, 8, 8, 3))
    start = review.epoch("4")
    review.set_ratings("4", [1, 0, 0])
    review.add("4", torch.rand(1, 8, 8, 3))
    assert review.epoch("4") == start, "arrivals and ratings keep every index"
    review.request_release("4", [1, 0, 0, 0])
    assert review.epoch("4") == start, "a release waiting has not moved anything yet"
    review.take_release("4")
    assert review.epoch("4") == start + 1
    review.clear("4")
    assert review.epoch("4") == start + 2
    review.add("4", torch.rand(1, 8, 8, 3))
    review.request_release("4", [0])
    assert review.epoch("4") == start + 3, "rejecting everything empties the tray"


class TestSendEverything:
    def test_rated_leave_best_first_then_the_unrated_in_arrival_order(self):
        frames = _frames(4)
        review.add("4", frames)
        assert review.request_release("4", [0, 2, 0, 5], everything=True) == 4
        kept = review.take_release("4")
        order = [float(k[0, 0, 0]) for k in kept]
        assert order == [float(frames[i, 0, 0, 0]) for i in (3, 1, 0, 2)]

    def test_everything_from_an_unrated_tray_is_the_tray_as_it_arrived(self):
        review.add("4", _frames(3))
        assert review.request_release("4", [0, 0, 0], everything=True) == 3


def _prompt(seed=7, value=11):
    """A trimmed API prompt: a primitive seed feeding a sampler and a prompt node."""
    return {
        "14": {"class_type": "PrimitiveInt", "inputs": {"value": value}},
        "16": {"class_type": "KSampler", "inputs": {"seed": ["14", 0], "steps": 8, "cfg": 1.0}},
        "11": {"class_type": "Prompt (LoraManager)", "inputs": {"seed": ["14", 0], "text": "a wolf"}},
        "30": {"class_type": "KSamplerAdvanced", "inputs": {"noise_seed": seed, "steps": 20}},
        "83": {"class_type": "PW_Review", "inputs": {"image": ["17", 0], "auto_pass": False}, "_meta": {"title": "PW Review"}},
    }


class _Rng:
    def __init__(self):
        self.n = 1000

    def randrange(self, stop):
        self.n += 1
        return self.n


class TestReroll:
    def test_literal_seeds_and_the_primitive_feeding_a_seed_are_rerolled(self):
        out = review.reroll_seeds(_prompt(), _Rng())
        assert out["30"]["inputs"]["noise_seed"] != 7
        assert out["14"]["inputs"]["value"] != 11

    def test_a_shared_seed_source_is_rolled_once_so_its_consumers_agree(self):
        out = review.reroll_seeds(_prompt(), _Rng())
        assert out["16"]["inputs"]["seed"] == ["14", 0] and out["11"]["inputs"]["seed"] == ["14", 0]
        rolled = [out["14"]["inputs"]["value"], out["30"]["inputs"]["noise_seed"]]
        assert len(set(rolled)) == 2 and all(v > 1000 for v in rolled)

    def test_everything_else_is_left_exactly_as_it_was(self):
        before = _prompt()
        out = review.reroll_seeds(before, _Rng())
        assert out["16"]["inputs"]["steps"] == 8 and out["11"]["inputs"]["text"] == "a wolf"
        assert before["14"]["inputs"]["value"] == 11, "the stored prompt is not modified"

    def test_a_non_integer_seed_is_not_touched(self):
        p = {"1": {"class_type": "X", "inputs": {"seed": "abc"}}}
        assert review.reroll_seeds(p, _Rng())["1"]["inputs"]["seed"] == "abc"


class TestRerun:
    def test_each_frame_keeps_the_prompt_that_made_it(self):
        review.add("4", _frames(2), prompt=_prompt(), workflow={"nodes": []})
        review.add("4", _frames(1))
        tray = review.get("4")
        assert tray.prompts[0] is not None and tray.prompts[1] is tray.prompts[0]
        assert tray.prompts[2] is None

    def test_rerun_hands_back_a_rerolled_prompt_marked_with_its_origin(self):
        review.add("4", _frames(2), prompt=_prompt(), workflow={"nodes": [1]})
        job = review.rerun_job("4", 1, "83", _Rng())
        assert job["workflow"] == {"nodes": [1]}
        assert job["prompt"]["14"]["inputs"]["value"] != 11
        origin = job["prompt"]["83"]["_meta"][review.RERUN_KEY]
        assert origin == review.get("4").uids[1]

    def test_a_frame_without_a_prompt_cannot_be_rerun(self):
        review.add("4", _frames(1))
        assert review.rerun_job("4", 0, "83", _Rng()) is None
        assert review.rerun_job("4", 5, "83", _Rng()) is None
        assert review.rerun_job("9", 0, "83", _Rng()) is None

    def test_the_rerun_lands_right_after_its_origin_and_after_earlier_reruns_of_it(self):
        review.add("4", _frames(3), prompt=_prompt())
        uid = review.get("4").uids[0]
        review.add("4", _frames(1, 0.5), prompt=_prompt(), rerun_of=uid)
        review.add("4", _frames(1, 0.7), prompt=_prompt(), rerun_of=uid)
        tray = review.get("4")
        assert tray.count == 5
        assert [round(float(f[0, 0, 0]), 2) for f in tray.images] == [0.0, 0.5, 0.7, 0.1, 0.2]
        assert tray.reruns == [False, True, True, False, False]

    def test_ratings_move_with_their_frames_when_a_rerun_is_inserted(self):
        review.add("4", _frames(3), prompt=_prompt())
        review.set_ratings("4", [1, 2, 3])
        review.add("4", _frames(1, 0.5), rerun_of=review.get("4").uids[0])
        assert review.get("4").ratings == [1, 0, 2, 3]

    def test_an_insertion_moves_the_epoch_and_an_append_does_not(self):
        review.add("4", _frames(2), prompt=_prompt())
        start = review.epoch("4")
        review.add("4", _frames(1))
        assert review.epoch("4") == start
        review.add("4", _frames(1), rerun_of=review.get("4").uids[0])
        assert review.epoch("4") == start + 1

    def test_a_rerun_whose_origin_has_gone_is_appended(self):
        review.add("4", _frames(1), prompt=_prompt())
        review.add("4", _frames(1, 0.5), rerun_of=987654)
        assert review.get("4").count == 2 and review.get("4").reruns == [False, True]

    def test_while_a_release_is_waiting_reruns_are_appended_not_inserted(self):
        review.add("4", _frames(2), prompt=_prompt())
        review.request_release("4", [5, 0])
        review.add("4", _frames(1, 0.5), rerun_of=review.get("4").uids[0])
        kept = review.take_release("4")
        assert len(kept) == 1 and review.get("4").count == 1


def test_a_rerun_job_is_valid_json_even_after_the_executor_marked_the_prompt():
    """The executor writes is_changed into the prompt it ran, NaN for PW Review,
    and a NaN made the browser reject the job outright."""
    import json

    prompt = _prompt()
    review.add("4", _frames(1), prompt=prompt)
    prompt["83"]["is_changed"] = [float("nan")]
    job = review.rerun_job("4", 0, "83", _Rng())
    json.dumps(job, allow_nan=False)
    assert "is_changed" not in job["prompt"]["83"]
