"""The review routes, against a stand-in for aiohttp.

Same shape as `test_preview_server.py`: aiohttp is ComfyUI's dependency rather
than ours, so the tests hand the routing table a fake `web` and call the
handlers directly. The failure answers are the part worth testing, because
those are the ones a user meets.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import torch

from pw_color import review
from pw_color import review_server as rs


class _Response:
    def __init__(self, *, body=None, text=None, status=200, content_type=None, headers=None):
        self.body = body
        self.text = text
        self.status = status
        self.content_type = content_type
        self.headers = headers or {}


class _Web:
    Response = _Response

    @staticmethod
    def json_response(data, *, status=200, headers=None):
        return _Response(text=json.dumps(data), status=status, headers=headers, content_type="application/json")


class _Request:
    def __init__(self, match_info, payload=None):
        self.match_info = match_info
        self._payload = payload

    async def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


def _handlers():
    return {(method, path): handler for path, method, handler in rs._routing_table(_Web)}


def _call(method, path, match_info, payload=None):
    return asyncio.run(_handlers()[(method, path)](_Request(match_info, payload)))


STATE = "/pw_color/review/{node_id}"
THUMB = "/pw_color/review/{node_id}/thumb/{index}"
VIEW = "/pw_color/review/{node_id}/image/{index}"
RATINGS = "/pw_color/review/{node_id}/ratings"
RELEASE = "/pw_color/review/{node_id}/release"
CLEAR = "/pw_color/review/{node_id}/clear"
MODE = "/pw_color/review/{node_id}/mode"
RERUN = "/pw_color/review/{node_id}/rerun/{index}"
NODE = {"node_id": "4"}


@pytest.fixture(autouse=True)
def empty():
    review._reset()
    yield
    review._reset()


def _frames(n=2):
    return torch.rand(n, 8, 8, 3)


def _json(res):
    return json.loads(res.text)


def test_state_of_an_empty_tray():
    assert _json(_call("GET", STATE, NODE)) == {
        "count": 0, "ratings": [], "pending": 0, "epoch": 0, "reruns": [], "rerunnable": [],
    }


def test_state_of_a_filling_tray():
    review.add("4", _frames(3))
    review.set_ratings("4", [4, 0, 2])
    assert _json(_call("GET", STATE, NODE)) == {
        "count": 3, "ratings": [4, 0, 2], "pending": 0, "epoch": 0, "reruns": [False] * 3, "rerunnable": [False] * 3,
    }


def test_state_shows_a_release_on_its_way():
    review.add("4", _frames(3))
    review.request_release("4", [4, 0, 2])
    assert _json(_call("GET", STATE, NODE))["pending"] == 2


def test_frames_serve_jpeg_that_must_not_be_cached():
    review.add("4", _frames(2))
    for path in (THUMB, VIEW):
        res = _call("GET", path, {"node_id": "4", "index": "1"})
        assert res.status == 200 and res.content_type == "image/jpeg"
        assert res.body[:2] == b"\xff\xd8"
        assert res.headers["Cache-Control"] == "no-store"


def test_frame_errors_are_answers_not_crashes():
    assert _call("GET", THUMB, {"node_id": "4", "index": "0"}).status == 404
    review.add("4", _frames(1))
    assert _call("GET", THUMB, {"node_id": "4", "index": "9"}).status == 404
    assert _call("GET", THUMB, {"node_id": "4", "index": "one"}).status == 400


def test_ratings_are_saved_on_the_server():
    review.add("4", _frames(2))
    assert _call("POST", RATINGS, NODE, {"ratings": [3, 1]}).status == 200
    assert review.get("4").ratings == [3, 1]


def test_ratings_for_an_empty_tray_are_404_and_rubbish_is_400():
    assert _call("POST", RATINGS, NODE, {"ratings": [1]}).status == 404
    review.add("4", _frames(1))
    assert _call("POST", RATINGS, NODE, {"ratings": "five"}).status == 400


def test_release_reports_how_many_will_leave():
    review.add("4", _frames(3))
    res = _call("POST", RELEASE, NODE, {"ratings": [5, 0, 1]})
    assert res.status == 200 and _json(res) == {"kept": 2}
    assert review.release_pending("4")


def test_releasing_nothing_rated_empties_the_tray():
    review.add("4", _frames(2))
    assert _json(_call("POST", RELEASE, NODE, {"ratings": [0, 0]})) == {"kept": 0}
    assert review.get("4") is None


def test_release_errors():
    assert _call("POST", RELEASE, NODE, {"ratings": [1]}).status == 404
    review.add("4", _frames(1))
    assert _call("POST", RELEASE, NODE).status == 400


def test_clear_empties_the_tray():
    review.add("4", _frames(2))
    assert _call("POST", CLEAR, NODE).status == 200
    assert review.get("4") is None


def test_mode_sets_the_live_switch():
    assert _call("POST", MODE, NODE, {"auto_pass": True}).status == 200
    assert review.auto_pass("4", default=False) is True


def test_mode_insists_on_a_real_boolean():
    """"false" as a string is truthy in Python; accepting it would be the bug."""
    assert _call("POST", MODE, NODE, {"auto_pass": "false"}).status == 400
    assert _call("POST", MODE, NODE).status == 400


def test_every_route_is_registered_once_and_changes_are_post_only():
    table = rs._routing_table(_Web)
    keys = [(method, path) for path, method, _ in table]
    assert len(keys) == len(set(keys))
    for path in (RATINGS, RELEASE, CLEAR, MODE, RERUN):
        assert ("POST", path) in keys and ("GET", path) not in keys


def test_registering_twice_is_a_no_op():
    rs._routes_registered = True
    try:
        assert rs.register_review_routes() is True
    finally:
        rs._routes_registered = False


def test_send_everything_counts_the_unrated_too():
    review.add("4", _frames(3))
    assert _json(_call("POST", RELEASE, NODE, {"ratings": [0, 4, 0], "everything": True})) == {"kept": 3}


def test_everything_must_be_a_real_true():
    review.add("4", _frames(3))
    assert _json(_call("POST", RELEASE, NODE, {"ratings": [0, 4, 0], "everything": "yes"})) == {"kept": 1}


def test_rerun_returns_the_frames_prompt_with_new_seeds():
    prompt = {"4": {"class_type": "PW_Review", "inputs": {}}, "9": {"class_type": "KSampler", "inputs": {"seed": 5}}}
    review.add("4", _frames(1), prompt=prompt, workflow={"w": 1})
    res = _call("POST", RERUN, {"node_id": "4", "index": "0"})
    job = _json(res)
    assert res.status == 200 and job["workflow"] == {"w": 1}
    assert job["prompt"]["9"]["inputs"]["seed"] != 5
    assert job["prompt"]["4"]["_meta"][review.RERUN_KEY] == review.get("4").uids[0]
    assert _json(_call("GET", STATE, NODE))["rerunnable"] == [True]


def test_rerun_errors():
    review.add("4", _frames(1))
    assert _call("POST", RERUN, {"node_id": "4", "index": "0"}).status == 404, "no prompt was kept"
    assert _call("POST", RERUN, {"node_id": "4", "index": "x"}).status == 400
