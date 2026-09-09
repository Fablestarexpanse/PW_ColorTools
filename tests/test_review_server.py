"""The review routes, against a stand-in for aiohttp.

Same shape as `test_preview_server.py`: aiohttp is ComfyUI's dependency rather
than ours, so the tests hand the routing table a fake `web` and call the
handlers directly. What is worth testing here is the failure answers — an
index past the batch, a release for a hold that has gone, a body from a
browser that sent the wrong shape — because those are the ones a user meets.
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
        return _Response(
            text=json.dumps(data), status=status, headers=headers, content_type="application/json"
        )


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


def _call(handler, match_info, payload=None):
    return asyncio.run(handler(_Request(match_info, payload)))


STATE = "/pw_color/review/{node_id}"
THUMB = "/pw_color/review/{node_id}/thumb/{index}"
VIEW = "/pw_color/review/{node_id}/image/{index}"
RELEASE = "/pw_color/review/{node_id}/release"


@pytest.fixture(autouse=True)
def no_holds():
    review._holds.clear()
    yield
    review._holds.clear()


def _batch(n: int = 2) -> torch.Tensor:
    return torch.rand(n, 8, 8, 3)


def test_state_says_nothing_is_held():
    body = json.loads(_call(_handlers()[("GET", STATE)], {"node_id": "4"}).text)
    assert body == {"holding": False, "count": 0, "ratings": []}


def test_state_describes_a_held_batch():
    review.open_hold("4", _batch(3))
    body = json.loads(_call(_handlers()[("GET", STATE)], {"node_id": "4"}).text)
    assert body == {"holding": True, "count": 3, "ratings": [0, 0, 0]}


def test_state_reports_ratings_already_given():
    review.open_hold("4", _batch(2))
    review.release("4", [4, 0])
    body = json.loads(_call(_handlers()[("GET", STATE)], {"node_id": "4"}).text)
    assert body["ratings"] == [4, 0], "a reloaded page has to see the ratings so far"


def test_thumb_and_view_serve_jpeg_that_must_not_be_cached():
    review.open_hold("4", _batch(2))
    for path in (THUMB, VIEW):
        res = _call(_handlers()[("GET", path)], {"node_id": "4", "index": "1"})
        assert res.status == 200
        assert res.content_type == "image/jpeg"
        assert res.body[:2] == b"\xff\xd8"
        assert res.headers["Cache-Control"] == "no-store"


def test_the_view_is_the_bigger_of_the_two():
    review.open_hold("4", torch.rand(1, 900, 900, 3))
    thumb = _call(_handlers()[("GET", THUMB)], {"node_id": "4", "index": "0"})
    view = _call(_handlers()[("GET", VIEW)], {"node_id": "4", "index": "0"})
    assert len(view.body) > len(thumb.body)


def test_an_image_with_nothing_held_is_404():
    res = _call(_handlers()[("GET", THUMB)], {"node_id": "4", "index": "0"})
    assert res.status == 404


def test_an_index_past_the_batch_is_404_not_a_crash():
    review.open_hold("4", _batch(2))
    res = _call(_handlers()[("GET", THUMB)], {"node_id": "4", "index": "9"})
    assert res.status == 404


def test_an_index_that_is_not_a_number_is_400():
    review.open_hold("4", _batch(2))
    res = _call(_handlers()[("GET", THUMB)], {"node_id": "4", "index": "second"})
    assert res.status == 400


def test_release_records_and_wakes():
    hold = review.open_hold("4", _batch(2))
    res = _call(_handlers()[("POST", RELEASE)], {"node_id": "4"}, {"ratings": [5, 0]})
    assert res.status == 200
    assert hold.ratings == [5, 0]
    assert hold.released.is_set()


def test_release_without_a_hold_is_404():
    res = _call(_handlers()[("POST", RELEASE)], {"node_id": "4"}, {"ratings": [1]})
    assert res.status == 404


def test_release_with_a_broken_body_is_400():
    review.open_hold("4", _batch(1))
    res = _call(_handlers()[("POST", RELEASE)], {"node_id": "4"}, {"ratings": "five"})
    assert res.status == 400


def test_release_with_no_body_at_all_is_400():
    review.open_hold("4", _batch(1))
    res = _call(_handlers()[("POST", RELEASE)], {"node_id": "4"})
    assert res.status == 400


def test_every_route_is_registered_once():
    table = rs._routing_table(_Web)
    paths = [(method, path) for path, method, _ in table]
    assert len(paths) == len(set(paths))
    assert ("POST", RELEASE) in paths, "the release must not be reachable by GET"


def test_registering_twice_is_a_no_op():
    rs._routes_registered = True
    try:
        assert rs.register_review_routes() is True
    finally:
        rs._routes_registered = False
