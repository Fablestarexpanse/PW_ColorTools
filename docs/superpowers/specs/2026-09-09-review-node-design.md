# PW Review: hold a batch, rate it, send the keepers on

Date: 2026-09-09. Status: approved, building. Ships as 2.2.0.

## Problem

A generation run produces a batch and every image in it goes downstream,
whether it is worth keeping or not. Judging them means running the graph,
looking at a Preview Image, then editing the graph or re-running to act on
what you saw. There is no point in a ComfyUI graph where a human looks at
what came out and decides what continues.

PW Review is that point. It holds the batch at the node, shows it, takes a
one-to-five star rating on each image, and on release sends the rated ones
downstream best first. Unrated means rejected. A toggle turns the gate off
entirely for unattended runs.

## Decisions taken with the user

- **Unrated images are dropped.** Leaving an image unrated is how you reject
  it. There is no separate reject action and no minimum-stars widget.
- **One run at a time.** Each run pauses at the node. Images do not
  accumulate across queued runs.
- **The node outputs images only.** No ratings output, no top-pick output.
- **It waits indefinitely.** No timeout. The run sits held until released or
  cancelled from the ComfyUI queue.

## What was measured (ComfyUI 0.34.0, frontend 1.49.6, 2026-09-09)

- The V3 node API supports `async def execute`: `FUNCTION` resolves to
  `EXECUTE_NORMALIZED_ASYNC` when the method is a coroutine function
  (`comfy_api/latest/_io.py`), and the executor awaits it, parking the node as
  `ExecutionResult.PENDING` meanwhile. So the node can wait without blocking
  the event loop it runs on.
- **The prompt executor runs on its own loop, in its own thread.** `main.py`
  starts `prompt_worker` on a `threading.Thread`, and `PromptExecutor.execute`
  calls `asyncio.run(...)`. The aiohttp server's loop is a different loop on a
  different thread. Therefore an `asyncio.Event` set from a route handler
  would be set on the wrong loop. The hold uses a `threading.Event` and the
  node polls it.
- `PromptServer.send_sync` is thread-safe by construction: it defers through
  `loop.call_soon_threadsafe`. The node may call it directly.
- `comfy.model_management.throw_exception_if_processing_interrupted()` raises
  when the user cancels. Polling it is what makes Cancel work while held.
- `ExecutionBlocker` (`comfy_execution/graph_utils.py`) blocks consumers of an
  output. With a `None` message it blocks silently.
- `fingerprint_inputs` is the V3 equivalent of `IS_CHANGED`.

## Design

### `pw_color/review.py` — the hold registry

Host-free and importable without ComfyUI, so the ordering rule can be tested
on its own.

```python
STARS = 5

@dataclass
class Hold:
    node_id: str
    count: int
    thumbs: list[bytes]     # filmstrip JPEGs
    views: list[bytes]      # focus-size JPEGs
    released: threading.Event
    ratings: list[int]      # one per image, 0 means unrated

def open_hold(node_id: str, images: torch.Tensor) -> Hold
def get_hold(node_id: str) -> Hold | None
def release(node_id: str, ratings: Sequence[int]) -> bool   # False if nothing held
def close(node_id: str) -> None
def kept_order(ratings: Sequence[int]) -> list[int]
```

`kept_order` is the whole selection rule: take the indices whose rating is
above zero, then sort those by rating descending. Python's sort is stable, so
ties come out in input order for free — the implementation says so in a
comment, because the tie rule is a promise to the user rather than an
accident of the sort.

One hold per node id. Opening a second closes the first, so a re-queued run
supersedes a stale hold rather than deadlocking behind it. Ratings arriving
for a hold that has already closed are dropped and the route answers 404.

Images are held as the caller's tensor plus two encoded sizes per image:
a 160px filmstrip thumbnail and a 768px focus view, both JPEG, reusing the
encoder in `preview_cache`. The tensor itself is what goes downstream, so it
stays in memory for the life of the hold. A batch of four 1024px images is
about 50 MB; this is stated in the node's description because it is the one
cost a user cannot see.

### `pw_color/nodes/review.py` — the node

```python
class PW_Review(io.ComfyNode):
    # schema: image in, auto_pass boolean, image out, unique_id hidden
    @classmethod
    def fingerprint_inputs(cls, **kwargs) -> float:
        return float("nan")     # never equal to itself, so the gate always runs

    @classmethod
    async def execute(cls, image, auto_pass: bool = False) -> io.NodeOutput:
        if auto_pass:
            return io.NodeOutput(image)
        hold = open_hold(node_id, image)
        try:
            push("pw_color.review", {"node_id": node_id, "count": hold.count})
            while not hold.released.is_set():
                throw_exception_if_processing_interrupted()
                await asyncio.sleep(POLL_SECONDS)     # 0.2
            keep = kept_order(hold.ratings)
            if not keep:
                return io.NodeOutput(ExecutionBlocker(None))
            return io.NodeOutput(image[keep])
        finally:
            close(node_id)
            push("pw_color.review", {"node_id": node_id, "count": 0})
```

`push` is a two-line helper over `PromptServer.instance.send_sync`, wrapped so
a missing server (tests, or a headless import) is a no-op rather than an
exception. `send_sync` defers through `call_soon_threadsafe`, so calling it
from the worker thread is safe.

`finally` is load-bearing: an interrupt, an upstream error or a browser that
never answers must not leave a hold that the next run inherits.

Two failure modes have chosen answers. A node with no `unique_id` cannot be
addressed by any panel, so holding would wait for a release that can never
arrive; it logs and passes the batch through instead. And the silent block is
written as `io.NodeOutput(ExecutionBlocker(None))` rather than through
`NodeOutput`'s `block_execution` argument, because that argument treats
`None` as "do not block" and an output carrying no args never reaches the code
that would apply it.

The batch is indexed with a list, which torch reads as a gather, so the output
is a new tensor in the order the ratings asked for.

### `pw_color/review_server.py` — the routes

A separate module from `preview_server`, whose docstring opens by saying every
route it serves is read-only. These routes mutate state, and a file that says
one thing and does another is worse than two files.

| Route | Answers |
| --- | --- |
| `GET /pw_color/review/{node_id}` | `{holding, count, ratings}`, so a reloaded page recovers |
| `GET /pw_color/review/{node_id}/image/{index}` | the focus JPEG for one image |
| `GET /pw_color/review/{node_id}/thumb/{index}` | the filmstrip JPEG |
| `POST /pw_color/review/{node_id}/release` | `{"ratings": [...]}`, releases the hold |

All send `Cache-Control: no-store`. A release for a node that is not holding
answers 404 rather than pretending. The access stance is the existing one and
is repeated in the docstring: anything that can reach the ComfyUI server can
release a hold, exactly as anything that can reach it can already queue a
prompt.

`register_review_routes()` mirrors `register_routes()`, including the
already-registered guard, and the extension entrypoint calls both inside the
same try block so neither can take the pack down.

### `web/src/nodes/review.ts` — the panel

Hosted on the shared panel, so it renders in both node designs.

Layout, top to bottom: header with the held count and a `release` chip; the
focus image; the filmstrip, each thumbnail with five stars beneath it.

- Click a thumbnail to focus it.
- Click a star to rate. Clicking the star that is already the rating clears it
  back to unrated, which is the only way to change your mind.
- Click `release` to send. The chip is inert while nothing is held.
- Idle text when nothing is held: "Nothing held. Run the graph."
- After release with nothing rated: "Nothing kept."

State arrives two ways. The websocket message wakes the panel when a run
reaches the node, and the panel fetches the hold state on creation, so
reloading the page mid-hold recovers rather than stranding the run.

Stars are drawn as a path, not a text glyph: a font without U+2605 would draw
tofu, and the pack owns its own drawing anyway. Filled stars use the accent
colour, empty ones the muted text colour, both from the theme.

### Reset

`reset.ts` gains `PW_Review: { auto_pass: true }` in its pass-through table,
because "reset — pass image through unchanged" must mean the gate stops
gating. That table is typed `Record<string, number>` today and widens to
`number | boolean`.

### Not in the example workflow

The shipped template must not include this node. A template that stops on
first run and waits for a stranger to rate something is a bad first
impression. The README gets a section and a screenshot instead.

## Testing

`tests/test_review.py`:

- `kept_order`: ratings `[5, 3, 0, 4]` give `[0, 3, 1]`; equal ratings keep
  input order; all zeroes give `[]`.
- The hold lifecycle: open, `get_hold` sees it, release sets the event and
  records ratings, close clears it.
- Opening a second hold for one node closes the first.
- `release` for an unknown node returns False.
- Routes against a fake `web`, in the style of `test_preview_server.py`:
  state, image, thumb, release, and the 404s.
- Node behaviour: `auto_pass` returns the input tensor unchanged and opens no
  hold; a release with no ratings yields an `ExecutionBlocker`; a release with
  ratings yields the images in rated order; an interrupt while held closes the
  hold and propagates.

`web/test/review.test.ts`:

- Star hit testing: a point maps to the right image and star.
- Rating toggle: setting a new value, and clearing when the same star is
  clicked twice.
- Release payload carries one entry per image, unrated as zero.

Live, on the test instance, in both node designs: a run stops at the node, the
panel shows the batch, stars set, release sends the right images in the right
order, Cancel works while held, and reloading the page mid-hold recovers.

## Out of scope

Accumulating across runs. Ratings as an output. A timeout. Keyboard
shortcuts. Saving ratings into image metadata. Any of these is a later
request, and none is assumed by this design.
