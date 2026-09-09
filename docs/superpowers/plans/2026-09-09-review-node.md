# PW Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A node that holds a generated batch, lets you rate each image one to five stars, and sends only the rated ones downstream, best first.

**Architecture:** The node's `execute` is a coroutine. It stashes the batch in a per-node hold registry, pushes a websocket message so the panel wakes, then polls a `threading.Event` while checking for interrupt. A release route records ratings and sets the event. The panel is hosted on the pack's existing DOM panel, so it draws in both node designs.

**Tech Stack:** Python 3.12, torch, aiohttp (ComfyUI's), ComfyUI V3 node API (`comfy_api.latest.io`), TypeScript with esbuild, node's built-in test runner.

**Spec:** `docs/superpowers/specs/2026-09-09-review-node-design.md`

## Global Constraints

- The prompt executor runs on its own asyncio loop on its own thread. Never signal the node with an `asyncio.Event`; the hold uses `threading.Event` and the node polls it.
- `pw_color/review.py` must import cleanly without ComfyUI (pytest collects it), so aiohttp and `server` imports stay inside functions.
- `widgets_values` order is a compatibility promise: the node's widgets are declared once and never reordered.
- No colour, radius or spacing literal outside `web/src/theme.ts`.
- `web/src/widgets/panel.ts` and anything under `web/src/core/` must stay loadable by node: no top-level import of `./comfy.ts` or `/scripts/*`.
- Never mention the pack the design style came from in anything shipped.
- Python test command: `PYTHONPATH=<scratchpad>/pylibs COMFYUI_PATH="F:/Stability Matrix/Packages/ComfyUI" "F:/Stability Matrix/Packages/ComfyUI/venv/Scripts/python.exe" -m pytest tests/ -q`. Expect exactly 1 skip.
- Frontend: `npm test` and `npx tsc --noEmit` in `web/`, node >= 22.6.
- After any TypeScript change: `npm run build`, and commit `web/dist/pw_color.js` with the sources. `tests/test_build.py` fails if they disagree.

---

### Task 1: The hold registry and the selection rule

**Files:**
- Create: `pw_color/review.py`
- Modify: `pw_color/preview_cache.py` (extract a reusable JPEG encoder)
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `pw_color.preview_cache.encode_jpeg(image, long_edge)` (added in this task).
- Produces:
  ```python
  STARS = 5
  THUMB_EDGE = 160
  VIEW_EDGE = 768

  class Hold:                      # dataclass
      node_id: str
      count: int
      thumbs: list[bytes]
      views: list[bytes]
      released: threading.Event
      ratings: list[int]

  def open_hold(node_id: str, images: "torch.Tensor") -> Hold
  def get_hold(node_id: str) -> Hold | None
  def release(node_id: str, ratings: Sequence[int]) -> bool
  def close(node_id: str) -> None
  def kept_order(ratings: Sequence[int]) -> list[int]
  ```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_review.py
"""The hold: what it keeps, in what order, and for how long."""

from __future__ import annotations

import threading

import pytest
import torch

from pw_color import review


@pytest.fixture(autouse=True)
def no_holds():
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
        assert all(b[:2] == b"\xff\xd8" for b in hold.thumbs)   # JPEG SOI
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_review.py -q`
Expected: FAIL, no module named `pw_color.review`.

- [ ] **Step 3: Extract the encoder in `preview_cache.py`**

Replace the body of `_encode_proxy` so the resizing JPEG encoder is callable with any edge, and keep the old name as the caller it already is:

```python
def encode_jpeg(image: torch.Tensor, long_edge: int) -> bytes:
    """One image, as a JPEG no longer than ``long_edge`` on its long side.

    Takes a single image in HWC, not a batch: callers that hold several want
    them one at a time, and slicing at the call site reads better than a batch
    index buried in here.
    """
    from PIL import Image

    img = image[..., :3].float().clamp(0, 1)
    h, w = img.shape[0], img.shape[1]
    scale = min(1.0, long_edge / max(h, w))
    arr = (img * 255.0 + 0.5).clamp(0, 255).to(torch.uint8).cpu().numpy()
    pil = Image.fromarray(arr, "RGB")
    if scale < 1.0:
        pil = pil.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = _io.BytesIO()
    pil.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _encode_proxy(image: torch.Tensor) -> bytes:
    return encode_jpeg(image[0], PROXY_LONG_EDGE)
```

Add `"encode_jpeg"` to `__all__`.

- [ ] **Step 4: Write `pw_color/review.py`**

```python
"""A batch, held at a node until someone rates it.

The pack's other nodes are pure functions of their inputs. This one waits for
a person, which makes the state it keeps the whole design: one hold per node,
the encoded images the panel draws, the ratings that come back, and an event
the executing node polls.

Why `threading.Event` and not `asyncio.Event`: ComfyUI runs the prompt
executor on its own loop on its own thread (`main.py` starts `prompt_worker`
as a thread; `PromptExecutor.execute` calls `asyncio.run`). The release
arrives on the aiohttp server's loop, on a different thread, where setting an
asyncio primitive belonging to another loop is undefined. A threading event
crosses threads by design and the node polls it, which is also where the
interrupt check belongs.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from .preview_cache import encode_jpeg

if TYPE_CHECKING:  # pragma: no cover - torch is a runtime import for callers
    import torch

__all__ = ["STARS", "Hold", "open_hold", "get_hold", "release", "close", "kept_order"]

_log = logging.getLogger("PW_Color")

#: Rating scale. One to five stars, or zero for unrated.
STARS = 5
#: Long edge of a filmstrip thumbnail, and of the focus view.
THUMB_EDGE = 160
VIEW_EDGE = 768


@dataclass
class Hold:
    node_id: str
    count: int
    thumbs: list[bytes]
    views: list[bytes]
    released: threading.Event = field(default_factory=threading.Event)
    ratings: list[int] = field(default_factory=list)


_holds: dict[str, Hold] = {}
_lock = threading.Lock()


def kept_order(ratings: Sequence[int]) -> list[int]:
    """Indices of the rated images, best first, ties in the order they arrived.

    Ties keeping their input order is a promise to the user, not an accident:
    two four-star frames come out in the order they were generated, which is
    the order they are shown in. `sorted` is stable, which is what makes that
    true, so the filter and the sort are two steps rather than one clever key.
    """
    rated = [i for i, r in enumerate(ratings) if r > 0]
    return sorted(rated, key=lambda i: -ratings[i])


def open_hold(node_id: str, images: "torch.Tensor") -> Hold:
    """Hold a batch and encode what the panel needs to show it.

    Any hold already open for this node is released first. A re-queued run
    must supersede a stale hold rather than queue up behind one nobody is
    looking at any more.
    """
    node_id = str(node_id)
    count = int(images.shape[0])
    hold = Hold(
        node_id=node_id,
        count=count,
        thumbs=[encode_jpeg(images[i], THUMB_EDGE) for i in range(count)],
        views=[encode_jpeg(images[i], VIEW_EDGE) for i in range(count)],
        ratings=[0] * count,
    )
    with _lock:
        stale = _holds.get(node_id)
        _holds[node_id] = hold
    if stale is not None:
        _log.debug("PW Color: superseding a held batch on node %s", node_id)
        stale.released.set()
    return hold


def get_hold(node_id: str) -> Hold | None:
    with _lock:
        return _holds.get(str(node_id))


def release(node_id: str, ratings: Sequence[int]) -> bool:
    """Record the ratings and wake the node. False if nothing is held.

    What arrives is whatever a browser sent, so it is padded to the batch and
    clamped to the scale rather than trusted: a short list would otherwise
    raise inside the executor thread, where the traceback is far from the
    cause.
    """
    hold = get_hold(node_id)
    if hold is None:
        return False
    clean = [0] * hold.count
    for i in range(min(len(ratings), hold.count)):
        try:
            clean[i] = max(0, min(STARS, int(ratings[i])))
        except (TypeError, ValueError):
            clean[i] = 0
    hold.ratings = clean
    hold.released.set()
    return True


def close(node_id: str) -> None:
    with _lock:
        _holds.pop(str(node_id), None)
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_review.py tests/test_preview_cache.py -q`
Expected: PASS, and the existing cache tests still pass because `_encode_proxy` kept its behaviour.

- [ ] **Step 6: Commit**

```bash
git add pw_color/review.py pw_color/preview_cache.py tests/test_review.py
git commit -m "The hold: a batch parked at a node, and the rule for what leaves it"
```

---

### Task 2: The routes

**Files:**
- Create: `pw_color/review_server.py`
- Modify: `__init__.py` (register the new routes beside the existing ones)
- Test: `tests/test_review_server.py`

**Interfaces:**
- Consumes: `review.get_hold`, `review.release`, `review.Hold`.
- Produces: `register_review_routes() -> bool`, `_routing_table(web) -> list[tuple[str, str, Handler]]` where the middle element is the HTTP method, `"GET"` or `"POST"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_review_server.py
"""The review routes, against a stand-in for aiohttp.

Same shape as `test_preview_server.py`: aiohttp is ComfyUI's dependency, not
ours, so the tests hand the routing table a fake `web` and call the handlers
directly.
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


def _call(handler, match_info, payload=None):
    return asyncio.run(handler(_Request(match_info, payload)))


@pytest.fixture(autouse=True)
def no_holds():
    review._holds.clear()
    yield
    review._holds.clear()


def _batch(n=2):
    return torch.rand(n, 8, 8, 3)


def test_state_says_nothing_is_held():
    handler = _handlers()[("GET", "/pw_color/review/{node_id}")]
    body = json.loads(_call(handler, {"node_id": "4"}).text)
    assert body == {"holding": False, "count": 0, "ratings": []}


def test_state_describes_a_held_batch():
    review.open_hold("4", _batch(3))
    handler = _handlers()[("GET", "/pw_color/review/{node_id}")]
    body = json.loads(_call(handler, {"node_id": "4"}).text)
    assert body == {"holding": True, "count": 3, "ratings": [0, 0, 0]}


def test_thumb_and_view_serve_jpeg():
    review.open_hold("4", _batch(2))
    for path in ("/pw_color/review/{node_id}/thumb/{index}", "/pw_color/review/{node_id}/image/{index}"):
        res = _call(_handlers()[("GET", path)], {"node_id": "4", "index": "1"})
        assert res.status == 200
        assert res.content_type == "image/jpeg"
        assert res.body[:2] == b"\xff\xd8"
        assert res.headers["Cache-Control"] == "no-store"


def test_an_index_past_the_batch_is_404_not_a_crash():
    review.open_hold("4", _batch(2))
    res = _call(_handlers()[("GET", "/pw_color/review/{node_id}/thumb/{index}")], {"node_id": "4", "index": "9"})
    assert res.status == 404


def test_release_records_and_wakes():
    hold = review.open_hold("4", _batch(2))
    res = _call(_handlers()[("POST", "/pw_color/review/{node_id}/release")], {"node_id": "4"}, {"ratings": [5, 0]})
    assert res.status == 200
    assert hold.ratings == [5, 0]
    assert hold.released.is_set()


def test_release_without_a_hold_is_404():
    res = _call(_handlers()[("POST", "/pw_color/review/{node_id}/release")], {"node_id": "4"}, {"ratings": [1]})
    assert res.status == 404


def test_release_with_a_broken_body_is_400():
    review.open_hold("4", _batch(1))
    res = _call(_handlers()[("POST", "/pw_color/review/{node_id}/release")], {"node_id": "4"}, {"ratings": "five"})
    assert res.status == 400


def test_registering_twice_is_a_no_op():
    rs._routes_registered = True
    try:
        assert rs.register_review_routes() is True
    finally:
        rs._routes_registered = False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_review_server.py -q`
Expected: FAIL, no module named `pw_color.review_server`.

- [ ] **Step 3: Write `pw_color/review_server.py`**

```python
"""The HTTP layer over a held batch.

Separate from `preview_server`, whose first paragraph promises that every
route it serves is read-only. One of these releases a run, and a module that
says one thing while doing another is worse than two modules.

On who can release. Entries are keyed by graph-local node id and nothing else,
so any client that can reach the ComfyUI server can release a hold, exactly as
any client that can reach it can already queue a prompt or read
`/history`. Authentication is the deployment's job; this is stated rather than
left to be discovered.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from .review import get_hold, release

__all__ = ["register_review_routes"]

_log = logging.getLogger("PW_Color")

_routes_registered = False
_NO_STORE = {"Cache-Control": "no-store"}

Handler = Callable[[Any], Awaitable[Any]]


def _image_handler(web: Any, attribute: str) -> Handler:
    """Serve one image out of a held batch, or 404 with a reason."""

    async def handler(request: Any) -> Any:
        hold = get_hold(request.match_info["node_id"])
        if hold is None:
            return web.Response(status=404, text="nothing held")
        try:
            index = int(request.match_info["index"])
        except (TypeError, ValueError):
            return web.Response(status=400, text="index must be a number")
        frames = getattr(hold, attribute)
        if not 0 <= index < len(frames):
            return web.Response(status=404, text="no such image in the held batch")
        return web.Response(body=frames[index], content_type="image/jpeg", headers=_NO_STORE)

    return handler


def _state_handler(web: Any) -> Handler:
    """What the panel needs to draw itself, including after a page reload."""

    async def handler(request: Any) -> Any:
        hold = get_hold(request.match_info["node_id"])
        if hold is None:
            return web.json_response({"holding": False, "count": 0, "ratings": []}, headers=_NO_STORE)
        return web.json_response(
            {"holding": True, "count": hold.count, "ratings": list(hold.ratings)}, headers=_NO_STORE
        )

    return handler


def _release_handler(web: Any) -> Handler:
    """Take the ratings and let the run continue."""

    async def handler(request: Any) -> Any:
        try:
            payload = await request.json()
            ratings = payload["ratings"]
            ratings = [int(r) for r in ratings]
        except Exception:
            return web.Response(status=400, text="expected {'ratings': [numbers]}")
        if not release(request.match_info["node_id"], ratings):
            return web.Response(status=404, text="nothing held")
        return web.json_response({"released": True}, headers=_NO_STORE)

    return handler


def _routing_table(web: Any) -> list[tuple[str, str, Handler]]:
    """Every route this module serves, with its method, in one readable list."""
    return [
        ("/pw_color/review/{node_id}", "GET", _state_handler(web)),
        ("/pw_color/review/{node_id}/thumb/{index}", "GET", _image_handler(web, "thumbs")),
        ("/pw_color/review/{node_id}/image/{index}", "GET", _image_handler(web, "views")),
        ("/pw_color/review/{node_id}/release", "POST", _release_handler(web)),
    ]


def register_review_routes() -> bool:
    """Attach the review routes. Never raises; idempotent.

    Same contract as `preview_server.register_routes`, and for the same
    reason: ComfyUI skips the entire extension if the entrypoint raises, so a
    missing aiohttp must cost the review panel and nothing else.
    """
    global _routes_registered
    if _routes_registered:
        _log.debug("PW Color: review routes already registered")
        return True

    try:
        from aiohttp import web
        from server import PromptServer

        routes = PromptServer.instance.routes
    except Exception:  # pragma: no cover - outside a running ComfyUI server
        _log.debug("PW Color: review routes unavailable", exc_info=True)
        return False

    for path, method, handler in _routing_table(web):
        (routes.post if method == "POST" else routes.get)(path)(handler)

    _routes_registered = True
    return True
```

- [ ] **Step 4: Register them in `__init__.py`**

In `on_load`, beside the existing registration, inside the same `try`:

```python
                from .pw_color.review_server import register_review_routes

                ok = register_routes() and register_review_routes()
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_review_server.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pw_color/review_server.py tests/test_review_server.py __init__.py
git commit -m "Routes for a held batch: read it, look at it, release it"
```

---

### Task 3: The node

**Files:**
- Create: `pw_color/nodes/review.py`
- Modify: `__init__.py` (add the node to the list)
- Test: `tests/test_review_node.py`

**Interfaces:**
- Consumes: `review.open_hold`, `review.get_hold`, `review.close`, `review.kept_order`, `preview_cache._executing_node_id`.
- Produces: `PW_Review`, `NODES = [PW_Review]`, and module-level `POLL_SECONDS = 0.2`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_review_node.py
"""The gate itself: what it passes, what it drops, and how it stops waiting."""

from __future__ import annotations

import asyncio
import threading

import pytest
import torch

pytest.importorskip("comfy_api.latest", reason="needs ComfyUI on the path")

from comfy_execution.graph_utils import ExecutionBlocker  # noqa: E402

from pw_color import review  # noqa: E402
from pw_color.nodes import review as node_mod  # noqa: E402


@pytest.fixture(autouse=True)
def no_holds(monkeypatch):
    review._holds.clear()
    monkeypatch.setattr(node_mod, "_node_id", lambda cls: "4")
    monkeypatch.setattr(node_mod, "_push", lambda *a, **k: None)
    yield
    review._holds.clear()


def _batch(n=3):
    return torch.stack([torch.full((4, 4, 3), i / 10.0) for i in range(n)])


def _run(image, **kwargs):
    return asyncio.run(node_mod.PW_Review.execute(image, **kwargs))


def _released(ratings, delay=0.05):
    """Release the hold from another thread, the way a route would."""
    threading.Timer(delay, lambda: review.release("4", ratings)).start()


def test_auto_pass_returns_the_batch_untouched_and_holds_nothing():
    image = _batch(3)
    out = _run(image, auto_pass=True)
    assert torch.equal(out.result[0], image)
    assert review.get_hold("4") is None


def test_a_release_sends_the_rated_images_best_first():
    image = _batch(4)
    _released([3, 0, 5, 1])
    out = _run(image)
    kept = out.result[0]
    assert kept.shape[0] == 3
    assert torch.equal(kept[0], image[2])
    assert torch.equal(kept[1], image[0])
    assert torch.equal(kept[2], image[3])


def test_rating_nothing_blocks_what_is_downstream():
    _released([0, 0, 0])
    out = _run(_batch(3))
    assert isinstance(out.result[0], ExecutionBlocker)


def test_the_hold_is_gone_once_the_run_continues():
    _released([5, 0, 0])
    _run(_batch(3))
    assert review.get_hold("4") is None


def test_cancelling_while_held_stops_the_run_and_clears_the_hold(monkeypatch):
    calls = {"n": 0}

    def interrupt_after_a_few_polls():
        calls["n"] += 1
        if calls["n"] > 2:
            raise KeyboardInterrupt("cancelled")

    monkeypatch.setattr(node_mod, "_check_interrupt", interrupt_after_a_few_polls)
    with pytest.raises(KeyboardInterrupt):
        _run(_batch(2))
    assert review.get_hold("4") is None, "a cancelled run must not leave a hold behind"


def test_without_a_node_id_it_passes_through_rather_than_waiting_forever(monkeypatch):
    monkeypatch.setattr(node_mod, "_node_id", lambda cls: None)
    image = _batch(2)
    out = _run(image)
    assert torch.equal(out.result[0], image)


def test_the_schema_declares_what_the_panel_and_the_reset_need():
    schema = node_mod.PW_Review.define_schema()
    assert schema.node_id == "PW_Review"
    names = [i.id for i in schema.inputs]
    assert names == ["image", "auto_pass"], "widget order is a saved-workflow promise"


def test_it_never_reports_the_same_fingerprint_twice():
    a = node_mod.PW_Review.fingerprint_inputs()
    assert a != a or a != node_mod.PW_Review.fingerprint_inputs()
```

- [ ] **Step 2: Run to verify it fails**

Run the Python test command from Global Constraints against `tests/test_review_node.py`.
Expected: FAIL, no module named `pw_color.nodes.review`.

- [ ] **Step 3: Write `pw_color/nodes/review.py`**

```python
"""PW Review — a batch, stopped for a look.

Every other node in this pack is a pure function of its inputs. This one waits
for a person, and where it waits is the design: inside `execute`, as a
coroutine, so the executor parks it as pending and the server keeps answering
while a run is held.

Ratings are one to five stars, and leaving an image unrated is how you reject
it: only rated images continue, ordered best first. That is one gesture doing
two jobs, which is why there is no separate reject button.

`auto_pass` is the way out for unattended work. It is also what "reset" sets,
because reset in this pack means the image comes out as it went in.
"""

from __future__ import annotations

import asyncio
import logging

import torch
from comfy_api.latest import io

from ..preview_cache import _executing_node_id
from ..review import close, kept_order, open_hold

__all__ = ["PW_Review", "NODES", "POLL_SECONDS"]

_log = logging.getLogger("PW_Color")

#: How often the held run checks whether it may continue, in seconds. Also the
#: interval at which Cancel is noticed, so it is short enough to feel instant
#: and long enough to cost nothing.
POLL_SECONDS = 0.2


def _node_id(cls: type) -> str | None:
    return _executing_node_id(cls, quiet=False)


def _push(event: str, data: dict) -> None:
    """Tell the browser a batch is waiting. Never raises.

    `send_sync` defers through `call_soon_threadsafe`, so calling it from the
    executor's thread is safe. Outside a running server there is nothing to
    tell, and that is not an error.
    """
    try:
        from server import PromptServer

        PromptServer.instance.send_sync(event, data)
    except Exception:  # pragma: no cover - no server in tests
        _log.debug("PW Color: could not push %s", event, exc_info=True)


def _check_interrupt() -> None:
    """Raise if the user pressed Cancel. Never raises anything else."""
    try:
        from comfy.model_management import throw_exception_if_processing_interrupted
    except Exception:  # pragma: no cover - outside ComfyUI
        return
    throw_exception_if_processing_interrupted()


class PW_Review(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="PW_Review",
            display_name="PW Review",
            category="PW Color",
            search_aliases=["review", "rate", "stars", "gate", "pick", "cull", "approve", "hold"],
            description=(
                "Holds the batch here and waits. Rate each image one to five stars, then release: "
                "the rated ones continue, best first, and the unrated are dropped. Turn on auto_pass "
                "to let runs through untouched. The batch stays in memory while it waits."
            ),
            inputs=[
                io.Image.Input("image"),
                io.Boolean.Input(
                    "auto_pass",
                    default=False,
                    tooltip="Send every image straight through without stopping. For unattended runs.",
                ),
            ],
            outputs=[io.Image.Output(display_name="image")],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs) -> float:
        # Never equal to itself, so a re-queued prompt stops at the gate again
        # instead of being served the batch it was given last time.
        return float("nan")

    @classmethod
    async def execute(cls, image: torch.Tensor, auto_pass: bool = False) -> io.NodeOutput:
        if auto_pass:
            return io.NodeOutput(image)

        node_id = _node_id(cls)
        if node_id is None:
            # Nothing can address this hold, so waiting would be waiting for
            # ever. Passing through is the failure that costs the least.
            _log.warning("PW Color: PW Review has no node id, so it cannot hold; passing the batch through")
            return io.NodeOutput(image)

        hold = open_hold(node_id, image)
        try:
            _push("pw_color.review", {"node_id": node_id, "count": hold.count})
            while not hold.released.is_set():
                _check_interrupt()
                await asyncio.sleep(POLL_SECONDS)
            keep = kept_order(hold.ratings)
            if not keep:
                from comfy_execution.graph_utils import ExecutionBlocker

                # Rejecting everything is a decision, not an error, so this
                # blocks silently and the panel is what says nothing was kept.
                #
                # The blocker travels as the output value rather than through
                # NodeOutput's `block_execution` argument, and it has to.
                # `block_execution=None` means "do not block" (execution.py
                # tests it against None), and an output carrying no args never
                # reaches the branch that would apply it at all. Passing the
                # blocker as the value is the only form that blocks silently.
                return io.NodeOutput(ExecutionBlocker(None))
            return io.NodeOutput(image[keep])
        finally:
            # An interrupt, a failure upstream, or a browser that never
            # answers must not leave a hold for the next run to inherit.
            close(node_id)
            _push("pw_color.review", {"node_id": node_id, "count": 0})


NODES = [PW_Review]
```

- [ ] **Step 4: Register the node in `__init__.py`**

Add `review` to the import list in `get_node_list`, and to the module tuple. It goes last, after `look_io`, because it is not part of a grade chain:

```python
            for module in (look, curves, grain, optics, match_source, palette, scopes, look_io, review):
```

- [ ] **Step 5: Run the tests**

Run the Python test command against `tests/test_review_node.py`, then the whole suite.
Expected: PASS, and no new skips.

- [ ] **Step 6: Commit**

```bash
git add pw_color/nodes/review.py tests/test_review_node.py __init__.py
git commit -m "PW Review: hold the batch inside execute and let the rated images out"
```

---

### Task 4: The panel

**Files:**
- Create: `web/src/nodes/review.ts`
- Create: `web/test/review.test.ts`
- Create: `web/src/core/stars.ts`
- Modify: `web/src/fetch.ts` (add a POST), `web/src/index.ts` (register), `web/src/widgets/reset.ts` (pass-through entry)

**Interfaces:**
- Consumes: `attachPanel`, `fitNode`, `panelOf`, `Panel` from `../widgets/panel.ts`; `fetchPw` from `../fetch.ts`; `sectionHeader`, `headerChip`, `hit`, `fillPanel`, `text` from `../widgets/draw.ts`.
- Produces: `web/src/core/stars.ts` exporting
  ```ts
  export interface StarHit { index: number; star: number }
  export function starHit(x: number, y: number, cells: {x:number;y:number;w:number;h:number}[], starSize: number): StarHit | null
  export function nextRating(current: number, clicked: number): number
  export function keptCount(ratings: number[]): number
  ```

- [ ] **Step 1: Write the failing frontend tests**

```ts
// web/test/review.test.ts
/**
 * The rating gestures, as pure functions.
 *
 * The panel around them needs a browser; these do not, and they are where the
 * behaviour a user would notice lives: which star a click lands on, and what
 * clicking the star you already chose should do.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { keptCount, nextRating, starHit } from '../src/core/stars.ts';

const CELLS = [
  { x: 0, y: 0, w: 100, h: 80 },
  { x: 110, y: 0, w: 100, h: 80 },
];

describe('starHit', () => {
  it('finds the star under the pointer', () => {
    // Stars sit in a row along the bottom of a cell, 16px each.
    assert.deepEqual(starHit(8, 72, CELLS, 16), { index: 0, star: 1 });
    assert.deepEqual(starHit(40, 72, CELLS, 16), { index: 0, star: 3 });
    assert.deepEqual(starHit(118, 72, CELLS, 16), { index: 1, star: 1 });
  });

  it('is null above the star row', () => {
    assert.equal(starHit(8, 10, CELLS, 16), null);
  });

  it('is null between cells', () => {
    assert.equal(starHit(105, 72, CELLS, 16), null);
  });

  it('never returns a star past the scale', () => {
    const hit = starHit(99, 72, CELLS, 16);
    assert.ok(hit && hit.star <= 5);
  });
});

describe('nextRating', () => {
  it('sets the star that was clicked', () => {
    assert.equal(nextRating(0, 4), 4);
    assert.equal(nextRating(2, 5), 5);
  });

  it('clicking the current rating clears it', () => {
    assert.equal(nextRating(3, 3), 0);
  });
});

describe('keptCount', () => {
  it('counts only the rated', () => {
    assert.equal(keptCount([5, 0, 3, 0]), 2);
    assert.equal(keptCount([0, 0]), 0);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run in `web/`: `node --experimental-strip-types --no-warnings --test test/review.test.ts`
Expected: FAIL, cannot find `../src/core/stars.ts`.

- [ ] **Step 3: Write `web/src/core/stars.ts`**

```ts
/**
 * Where the stars are, and what clicking one means.
 *
 * Host-free on purpose: this is the part of the review panel that decides
 * what a click did, and it is worth testing without a browser.
 */

export const STARS = 5;

export interface Cell {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface StarHit {
  index: number;
  star: number;
}

/** The star row sits along the bottom of each cell, `starSize` tall. */
export function starHit(x: number, y: number, cells: Cell[], starSize: number): StarHit | null {
  for (let index = 0; index < cells.length; index++) {
    const c = cells[index];
    const top = c.y + c.h - starSize;
    if (x < c.x || x > c.x + c.w || y < top || y > c.y + c.h) continue;
    const star = Math.floor((x - c.x) / starSize) + 1;
    if (star < 1 || star > STARS) return null;
    return { index, star };
  }
  return null;
}

/**
 * Clicking the star you already chose clears the rating.
 *
 * Without it there would be no way back to unrated, and unrated is how an
 * image is rejected — so the gesture that rejects has to be reachable by the
 * same clicks that rate.
 */
export function nextRating(current: number, clicked: number): number {
  return current === clicked ? 0 : clicked;
}

/** How many images would leave the gate. */
export function keptCount(ratings: number[]): number {
  return ratings.filter((r) => r > 0).length;
}
```

- [ ] **Step 4: Run the frontend tests**

Run in `web/`: `node --experimental-strip-types --no-warnings --test test/review.test.ts`
Expected: PASS.

- [ ] **Step 5: Add a POST to `web/src/fetch.ts`**

```ts
/** POST JSON to one of the pack's routes, through the host's own fetch. */
export async function postPw(path: string, body: unknown): Promise<Response> {
  // @ts-ignore - provided by ComfyUI at runtime, no types published
  const { api } = await import('/scripts/api.js');
  return api.fetchApi(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    cache: 'no-store',
  });
}
```

- [ ] **Step 6: Write `web/src/nodes/review.ts`**

```ts
/**
 * PW Review — node wiring.
 *
 * The panel is the node: without it there is no way to release a held run
 * except cancelling it. So it draws the batch, takes the ratings, and posts
 * them back.
 *
 * State arrives two ways. A websocket message wakes the panel when a run
 * reaches the node, and the panel asks the server for the hold when it is
 * created — which is what makes reloading the page mid-hold recover rather
 * than stranding the run.
 */

import { fetchPw, postPw } from '../fetch.ts';
import { api, app, type NodeLike } from '../comfy.ts';
import { BADGE, PW } from '../theme.ts';
import { STARS, keptCount, nextRating, starHit, type Cell } from '../core/stars.ts';
import { fillPanel, headerChip, hit, sectionHeader, text, type Ctx, type Rect } from '../widgets/draw.ts';
import { attachPanel, fitNode, panelOf, type Panel } from '../widgets/panel.ts';
import { addResetMenu } from '../widgets/reset.ts';

const M = PW.metrics;
const HEADER_H = 18;
const VIEW_H = 220;
const THUMB_H = 76;
const STAR_H = 16;
const CELL_H = THUMB_H + STAR_H;
const CELL_W = 96;
const MIN_WIDTH = 420;

interface ReviewUI {
  holding: boolean;
  count: number;
  ratings: number[];
  focus: number;
  /** Decoded frames, by index, once fetched. */
  views: Map<number, HTMLImageElement>;
  thumbs: Map<number, HTMLImageElement>;
  note: string;
}

const uis = new WeakMap<object, ReviewUI>();

function blank(): ReviewUI {
  return { holding: false, count: 0, ratings: [], focus: 0, views: new Map(), thumbs: new Map(), note: '' };
}

function gridCells(r: Rect, count: number): Cell[] {
  const cols = Math.max(1, Math.floor((r.w + 8) / (CELL_W + 8)));
  return Array.from({ length: count }, (_, i) => ({
    x: r.x + (i % cols) * (CELL_W + 8),
    y: r.y + Math.floor(i / cols) * (CELL_H + 8),
    w: CELL_W,
    h: CELL_H,
  }));
}

function rows(width: number, count: number): number {
  const cols = Math.max(1, Math.floor((width + 8) / (CELL_W + 8)));
  return Math.max(1, Math.ceil(Math.max(1, count) / cols));
}

function layout(w: number): { header: Rect; view: Rect; strip: Rect } {
  let y = 0;
  const header = { x: 0, y, w, h: HEADER_H };
  y += HEADER_H + 6;
  const view = { x: 0, y, w, h: VIEW_H };
  y += VIEW_H + M.gapSection;
  return { header, view, strip: { x: 0, y, w, h: 0 } };
}

function panelHeight(w: number, ui: ReviewUI): number {
  const n = rows(w, ui.count);
  return HEADER_H + 6 + VIEW_H + M.gapSection + n * CELL_H + (n - 1) * 8 + M.padding;
}

/** A five-pointed star, drawn rather than typed: a font without the glyph
 *  would draw a box, and the pack owns its own drawing anyway. */
function star(ctx: Ctx, cx: number, cy: number, radius: number, filled: boolean): void {
  ctx.beginPath();
  for (let i = 0; i < 10; i++) {
    const r = i % 2 === 0 ? radius : radius * 0.45;
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    const x = cx + Math.cos(a) * r;
    const y = cy + Math.sin(a) * r;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
  if (filled) {
    ctx.fillStyle = PW.color.accent;
    ctx.fill();
  } else {
    ctx.strokeStyle = PW.color.textMute;
    ctx.lineWidth = 1;
    ctx.stroke();
  }
}

async function loadFrame(node: NodeLike, ui: ReviewUI, kind: 'image' | 'thumb', index: number): Promise<void> {
  const into = kind === 'image' ? ui.views : ui.thumbs;
  if (into.has(index)) return;
  try {
    const res = await fetchPw(`/pw_color/review/${node.id}/${kind}/${index}`);
    if (!res.ok) return;
    const url = URL.createObjectURL(await res.blob());
    const img = new Image();
    await new Promise((done) => {
      img.onload = done;
      img.onerror = done;
      img.src = url;
    });
    into.set(index, img);
    panelOf(node)?.invalidate();
  } catch {
    /* the hold went away underneath us; the next state fetch will say so */
  }
}

async function syncState(node: NodeLike, ui: ReviewUI): Promise<void> {
  try {
    const res = await fetchPw(`/pw_color/review/${node.id}`);
    if (!res.ok) return;
    const state = await res.json();
    const fresh = !ui.holding && state.holding;
    ui.holding = !!state.holding;
    ui.count = state.count ?? 0;
    ui.ratings = Array.isArray(state.ratings) ? state.ratings : [];
    if (fresh) {
      ui.views.clear();
      ui.thumbs.clear();
      ui.focus = 0;
      ui.note = '';
    }
    const panel = panelOf(node);
    if (panel) fitNode(node, panel);
    if (ui.holding) {
      void loadFrame(node, ui, 'image', ui.focus);
      for (let i = 0; i < ui.count; i++) void loadFrame(node, ui, 'thumb', i);
    }
    panel?.invalidate();
  } catch {
    /* offline; the next message or the next run will bring us back */
  }
}

async function releaseHold(node: NodeLike, ui: ReviewUI): Promise<void> {
  if (!ui.holding) return;
  const kept = keptCount(ui.ratings);
  try {
    const res = await postPw(`/pw_color/review/${node.id}/release`, { ratings: ui.ratings });
    ui.note = res.ok ? (kept ? `sent ${kept}` : 'nothing kept') : 'the hold went away';
  } catch {
    ui.note = 'could not reach the server';
  }
  ui.holding = false;
  panelOf(node)?.invalidate();
}

export function registerReview(): void {
  app.registerExtension({
    name: 'pw.color.review',
    async setup() {
      // The node pushes this when a run reaches it and again when it lets go.
      api.addEventListener('pw_color.review', (e: any) => {
        const node = app.graph?.getNodeById?.(e?.detail?.node_id);
        const ui = node && uis.get(node);
        if (node && ui) void syncState(node, ui);
      });
    },

    async beforeRegisterNodeDef(nodeType: any, nodeData: any) {
      if (nodeData?.name !== 'PW_Review') return;
      addResetMenu(nodeType);

      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function (this: NodeLike) {
        const r = onCreated?.apply(this, arguments as any);
        const ui = blank();
        uis.set(this, ui);

        const panel: Panel = attachPanel(this, {
          minWidth: MIN_WIDTH,
          height: (w) => panelHeight(w, ui),
          draw: (ctx, rr) => {
            const L = layout(rr.w);
            const label = ui.holding ? `Review — ${ui.count} held, ${keptCount(ui.ratings)} kept` : 'Review';
            sectionHeader(ctx, label, L.header, BADGE.render);
            if (ui.holding) headerChip(ctx, L.header, 'release', BADGE.render.label);

            fillPanel(ctx, L.view, PW.color.well, M.radiusPanel, PW.color.border);
            const focus = ui.views.get(ui.focus);
            if (focus) {
              const s = Math.min(L.view.w / focus.width, L.view.h / focus.height);
              const w = focus.width * s;
              const h = focus.height * s;
              ctx.drawImage(focus, L.view.x + (L.view.w - w) / 2, L.view.y + (L.view.h - h) / 2, w, h);
            } else {
              const idle = ui.holding ? 'loading' : ui.note || 'Nothing held. Run the graph.';
              text(ctx, idle, L.view.x + L.view.w / 2, L.view.y + L.view.h / 2, {
                colour: PW.color.textMute,
                align: 'center',
              });
            }

            const cells = gridCells({ ...L.strip, y: L.strip.y }, ui.count);
            cells.forEach((c, i) => {
              const thumb = ui.thumbs.get(i);
              fillPanel(ctx, { x: c.x, y: c.y, w: c.w, h: THUMB_H }, PW.color.well, M.radiusControl);
              if (thumb) {
                ctx.save();
                fillPanel(ctx, { x: c.x, y: c.y, w: c.w, h: THUMB_H }, PW.color.well, M.radiusControl);
                ctx.clip();
                const s = Math.max(c.w / thumb.width, THUMB_H / thumb.height);
                const w = thumb.width * s;
                const h = thumb.height * s;
                ctx.drawImage(thumb, c.x + (c.w - w) / 2, c.y + (THUMB_H - h) / 2, w, h);
                ctx.restore();
              }
              ctx.strokeStyle = i === ui.focus ? PW.color.accent : PW.color.borderSoft;
              ctx.lineWidth = i === ui.focus ? 2 : 1;
              ctx.strokeRect(c.x + 0.5, c.y + 0.5, c.w - 1, THUMB_H - 1);
              const rating = ui.ratings[i] ?? 0;
              for (let s = 1; s <= STARS; s++) {
                star(ctx, c.x + (s - 0.5) * STAR_H, c.y + THUMB_H + STAR_H / 2, STAR_H * 0.4, s <= rating);
              }
            });
          },
          onPointerDown: (x, y) => {
            const L = layout(panel.width);
            if (ui.holding && hit(headerChip(panel.context, L.header, 'release', BADGE.render.label), x, y, 3)) {
              void releaseHold(this, ui);
              return true;
            }
            if (!ui.holding) return false;
            const cells = gridCells(L.strip, ui.count);
            const onStar = starHit(x, y, cells, STAR_H);
            if (onStar) {
              ui.ratings[onStar.index] = nextRating(ui.ratings[onStar.index] ?? 0, onStar.star);
              return true;
            }
            const cell = cells.findIndex((c) => hit({ x: c.x, y: c.y, w: c.w, h: THUMB_H }, x, y));
            if (cell >= 0) {
              ui.focus = cell;
              void loadFrame(this, ui, 'image', cell);
              return true;
            }
            return false;
          },
        });

        // A page reloaded while a run is held has to find it again.
        setTimeout(() => void syncState(this, ui), 0);
        return r;
      };
    },
  });
}
```

`layout` returns `strip` with a zero height because the strip's height is
whatever the cells need; `gridCells` places them from `strip.y` and the node
height comes from `panelHeight`. Keep it that way rather than computing the
same rows twice.

- [ ] **Step 7: Register it and give reset something to do**

In `web/src/index.ts`: import `registerReview` and call it beside the others, and add `'PW_Review'` to `PW_NODES`.

In `web/src/widgets/reset.ts`, widen the table and add the entry:

```ts
const PASS_THROUGH: Record<string, Record<string, number | boolean>> = {
  PW_Optics: { halation: 0 },
  PW_Grain: { amount: 0, dither: 0 },
  PW_MatchSource: { strength: 0 },
  // Reset means the image comes out as it went in, and for a gate that means
  // it stops gating.
  PW_Review: { auto_pass: true },
};
```

- [ ] **Step 8: Typecheck, build, test**

Run in `web/`: `npx tsc --noEmit && npm run build && npm test`
Expected: clean, and 31 frontend tests passing.

- [ ] **Step 9: Commit**

```bash
git add web/src/nodes/review.ts web/src/core/stars.ts web/test/review.test.ts web/src/fetch.ts web/src/index.ts web/src/widgets/reset.ts web/dist/pw_color.js
git commit -m "The review panel: the batch, the stars, and the release"
```

---

### Task 5: Documentation and version

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml`, `pw_color/__init__.py`, `web/package.json`
- Modify: `tools/capture_readme.py` (add the node to the capture)
- Create: `docs/images/pw_review.png`

- [ ] **Step 1: Bump the version to 2.2.0 in all three places**

`pyproject.toml`, `pw_color/__init__.py`, `web/package.json`.

- [ ] **Step 2: Write the CHANGELOG entry**

```markdown
## 2.2.0

### Added

- **PW Review.** A gate you put wherever you want to look before continuing.
  It holds the batch at the node, shows every image, and takes a one-to-five
  star rating on each. Release, and the rated images carry on downstream best
  first; the unrated are dropped, which is how you reject one. `auto_pass`
  sends everything through untouched for unattended runs, and is what the
  node's reset turns on. The batch stays in memory while it waits, and the
  run holds its place in the queue.
```

- [ ] **Step 3: Add the README section**

After the PW Scopes section and before PW Look I/O, in the same format as its
neighbours: an `<img>` at width 420, a paragraph, then the controls. Text:

```markdown
### PW Review

<img src="docs/images/pw_review.png" alt="PW Review" width="420">

A place to stop and look. Wire it after your sampler and the run halts at the
node with the whole batch in front of you: click a frame to see it large, give
each one one to five stars, then release. The rated images carry on down the
graph best first, and the ones you left unrated are dropped — that is how you
reject a frame, and why there is no separate reject button.

Rating the same star twice clears it back to unrated. `auto_pass` sends every
image straight through without stopping, for unattended runs, and it is what
the node's reset turns on.

Two things to know: the batch stays in memory while it waits, and the run
keeps its place in the queue, so anything queued behind it waits too.
```

- [ ] **Step 4: Extend the capture script**

`tools/capture_readme.py` builds from the example workflow, which will not
contain this node. Add a capture that builds the situation instead, after the
existing per-node loop:

```python
    # PW Review only looks like anything while it is holding, so this one is
    # staged: add the node, wire the template's image source into it, queue,
    # wait for the hold, rate two frames, shoot, then release so the run ends.
    review_id = page.evaluate("""() => {
        const app = window.app;
        const src = app.graph._nodes.find(n => n.type === 'PW_Look');
        const node = window.LiteGraph.createNode('PW_Review');
        node.pos = [src.pos[0], src.pos[1] + 1500];
        app.graph.add(node);
        src.connect(0, node, 0);
        app.canvas.setDirty(true, true);
        return String(node.id);
    }""")
    page.evaluate("() => window.app.queuePrompt(0, 1)")
    page.wait_for_function(
        """async (id) => {
            const r = await fetch(`/api/pw_color/review/${id}`, { cache: 'no-store' });
            return r.ok && (await r.json()).holding;
        }""",
        arg=review_id,
        timeout=120000,
    )
    page.wait_for_timeout(2500)
    page.evaluate("""(id) => {
        const node = window.app.graph.getNodeById(id);
        const cv = node.widgets.find(w => w.name === 'pw_panel').element.querySelector('canvas');
        const click = (lx, ly) => { const r = cv.getBoundingClientRect(); const s = r.width / cv.offsetWidth;
            for (const type of ['pointerdown', 'pointerup'])
                cv.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true,
                    clientX: r.x + lx * s, clientY: r.y + ly * s, pointerId: 1, pointerType: 'mouse',
                    button: 0, buttons: 1, isPrimary: true })); };
        // Star rows sit under each 76px thumbnail, 16px per star.
        const stripTop = 18 + 6 + 220 + 20;
        click(16 * 4.5, stripTop + 76 + 8);        // four stars on the first frame
        click(96 + 8 + 16 * 2.5, stripTop + 76 + 8);  // two on the second
    }""", review_id)
    page.wait_for_timeout(800)
    page.locator(f'[data-node-id="{review_id}"]').first.screenshot(path=f"{OUT}/pw_review.png")
    page.evaluate("""async (id) => {
        await fetch(`/api/pw_color/review/${id}/release`, { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ratings: [4, 2] }) });
    }""", review_id)
    print("pw_review.png")
```

- [ ] **Step 5: Run the whole suite and commit**

```bash
git add README.md CHANGELOG.md pyproject.toml pw_color/__init__.py web/package.json tools/capture_readme.py docs/images/pw_review.png
git commit -m "2.2.0: document PW Review and capture it"
```

---

### Task 6: Live verification, merge, release

- [ ] **Step 1: Start the test instance**

`python main.py --cpu --port 8199 --disable-auto-launch` in the ComfyUI directory, with the clone on this branch.

- [ ] **Step 2: Verify in Modern Node Design**

Build a graph that makes a batch of four, feed it to PW Review, feed that to a Preview Image. Queue it. Confirm: the run stops, the panel shows four thumbnails, clicking a thumbnail changes the focus image, stars set and clear, the header count follows, release sends, and the Preview Image downstream receives exactly the rated images in rated order.

- [ ] **Step 3: Verify the edges**

Nothing rated then release: downstream gets nothing and the panel says "nothing kept". Cancel while held: the run ends and a fresh queue holds again rather than passing straight through. Reload the page while held: the panel comes back with the batch and the ratings so far. `auto_pass` on: no hold at all.

- [ ] **Step 4: Verify in Classic Node Design**

The same graph, the same gestures.

- [ ] **Step 5: Full suite, merge, tag, push**

```bash
git checkout main && git merge --ff-only review-node
git tag -a v2.2.0 -m "PW Color Tools 2.2.0: PW Review, a rating gate for generated batches."
git push origin main && git push origin v2.2.0
```

- [ ] **Step 6: Watch CI green, update the live clone, stop the test instance**
