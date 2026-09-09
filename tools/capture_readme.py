"""Recapture the README's per-node screenshots.

Drives a headless Chrome (Playwright, `channel="chrome"`, so nothing is
downloaded) against a ComfyUI instance on port 8199 with this pack installed:
loads the example workflow, runs it once so previews and the palette are
populated, applies a preset and two curve points, then screenshots each node
element in Modern Node Design at 2x. Writes into docs/images.

    pip install playwright   # any Python; Chrome must be installed
    python main.py --cpu --port 8199 --disable-auto-launch    # in ComfyUI
    python tools/capture_readme.py
"""

import json
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8199"
import os
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "images")
NODES = {
    "PW_Look": "pw_look.png",
    "PW_Curves": "pw_curves.png",
    "PW_Grain": "pw_grain.png",
    "PW_Palette": "pw_palette.png",
    "PW_MatchSource": "pw_matchsource.png",
    "PW_Optics": "pw_optics.png",
    "PW_Scopes": "pw_scopes.png",
    "PW_LookIO": "pw_lookio.png",
}


def queue_idle():
    with urllib.request.urlopen(BASE + "/api/queue", timeout=5) as r:
        d = json.load(r)
    return not d["queue_running"] and not d["queue_pending"]


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1900, "height": 2400}, device_scale_factor=2)
    page.goto(BASE + "/?capture=1")
    page.wait_for_function("() => !!window.app?.graph", timeout=60000)
    page.wait_for_timeout(3000)
    vue = page.evaluate("() => window.app.extensionManager.setting.get('Comfy.VueNodes.Enabled')")
    if not vue:
        page.evaluate("() => window.app.extensionManager.setting.set('Comfy.VueNodes.Enabled', true)")
        page.wait_for_timeout(500)
        page.reload()
        page.wait_for_function("() => !!window.app?.graph", timeout=60000)
        page.wait_for_timeout(3000)
    print("vue mode:", page.evaluate("() => window.LiteGraph.vueNodesMode"))

    page.evaluate("""async () => {
        const doc = await (await fetch('/api/workflow_templates/PW_ColorTools/pw_color_basic.json')).json();
        await window.app.loadGraphData(doc, true, false, 'capture');
    }""")
    page.wait_for_timeout(4000)

    # Run once so every preview, thumbnail and the palette are populated. Fresh
    # seeds, or ComfyUI serves the whole prompt from its cache and no node
    # executes, which means no 'executed' message and an empty palette.
    page.evaluate("""() => { for (const t of ['PW_Palette', 'PW_Grain']) { const n = window.app.graph._nodes.find(n => n.type === t);
        const w = n.widgets.find(w => w.name === 'seed'); if (w) { w.value = Math.floor(Math.random() * 1e9); w.callback?.(w.value); } } }""")
    page.evaluate("() => window.app.queuePrompt(0, 1)")
    for _ in range(120):
        time.sleep(2)
        if queue_idle():
            break
    print("run done")
    page.wait_for_timeout(3000)

    # Give the panels something to show: a preset on Look, a point on Curves.
    print(page.evaluate("""async () => {
        const app = window.app;
        const paint = () => new Promise(r => requestAnimationFrame(() => setTimeout(r, 80)));
        const ev = (cv, type, lx, ly, extra = {}) => { const r = cv.getBoundingClientRect(); const s = r.width / cv.offsetWidth;
            cv.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, clientX: r.x + lx * s, clientY: r.y + ly * s, pointerId: 1, pointerType: 'mouse', button: 0, buttons: 1, isPrimary: true, ...extra })); };
        const click = (cv, x, y) => { ev(cv, 'pointerdown', x, y); ev(cv, 'pointerup', x, y); };
        const panel = (t) => { const n = app.graph._nodes.find(n => n.type === t); return [n, n.widgets.find(w => w.name === 'pw_panel').element.querySelector('canvas')]; };
        const [look, lcv] = panel('PW_Look');
        await paint();
        click(lcv, 96 + 8 + 48, 218 + 37);   // second preset cell
        await paint();
        const [curves, ccv] = panel('PW_Curves');
        const h = ccv.offsetHeight, top = 204, eh = h - 12 - top;
        ev(ccv, 'pointerdown', ccv.offsetWidth * 0.3, top + eh * 0.75);
        ev(ccv, 'pointermove', ccv.offsetWidth * 0.3, top + eh * 0.78);
        ev(ccv, 'pointerup', ccv.offsetWidth * 0.3, top + eh * 0.78);
        await paint();
        await new Promise(r => setTimeout(r, 450));   // outside the double-click window
        ev(ccv, 'pointerdown', ccv.offsetWidth * 0.7, top + eh * 0.25);
        ev(ccv, 'pointermove', ccv.offsetWidth * 0.7, top + eh * 0.22);
        ev(ccv, 'pointerup', ccv.offsetWidth * 0.7, top + eh * 0.22);
        await paint();
        return { curves: curves.widgets[0].value.slice(0, 80), preset: look.widgets.find(w => w.name === 'preset').value, sizes: ['PW_Look','PW_Grain'].map(t => Math.round(app.graph._nodes.find(n => n.type === t).size[1])) };
    }"""))
    page.wait_for_timeout(1500)
    # Shrink every panel node so the panel's own deficit path grows it to an
    # exact fit; the template's saved heights are larger than needed.
    page.evaluate("""() => { for (const n of window.app.graph._nodes) { if (n.widgets?.some(w => w.name === 'pw_panel')) n.setSize([n.size[0], 120]); } window.app.canvas.setDirty(true, true); }""")
    page.wait_for_timeout(2500)
    print(page.evaluate("() => Object.fromEntries(window.app.graph._nodes.filter(n => n.type.startsWith('PW_')).map(n => [n.type, Math.round(n.size[1])]))"))

    for node_type, filename in NODES.items():
        info = page.evaluate("""(t) => {
            const app = window.app;
            const n = app.graph._nodes.find(n => n.type === t);
            app.canvas.setZoom ? app.canvas.setZoom(1, [0, 0]) : (app.canvas.ds.scale = 1);
            app.canvas.ds.offset[0] = -(n.pos[0]) + 480;
            app.canvas.ds.offset[1] = -(n.pos[1]) + 260;
            app.canvas.setDirty(true, true);
            return { id: String(n.id), size: n.size.map(Math.round) };
        }""", node_type)
        page.wait_for_timeout(900)
        # Hover the panel so the backing store is re-measured at this zoom.
        page.mouse.move(400, 400)
        page.wait_for_timeout(600)
        el = page.locator(f'[data-node-id="{info["id"]}"]').first
        # Nudge the pointer over the panel canvas for a fresh measure, then away so no hover state shows.
        try:
            cv = el.locator("[data-capture-wheel] canvas").first
            box = cv.bounding_box()
            if box:
                page.mouse.move(box["x"] + 5, box["y"] + 5)
                page.wait_for_timeout(300)
                page.mouse.move(5, 900)
                page.wait_for_timeout(300)
        except Exception:
            pass
        el.screenshot(path=f"{OUT}/{filename}")
        print(node_type, info, "->", filename)

    # PW Review only looks like anything while it is holding, so this one is
    # staged rather than taken from the template: four grades of one frame is
    # the situation the node is for, and it is also the only way to get four
    # visibly different images without a model.
    review_id = page.evaluate(
        """(presets) => {
        const app = window.app;
        app.graph.clear();
        const mk = (type, pos) => { const n = window.LiteGraph.createNode(type); n.pos = pos; app.graph.add(n); return n; };
        const setw = (n, name, v) => { const w = n.widgets.find(w => w.name === name); if (w) { w.value = v; w.callback?.(v); } };
        const load = mk('LoadImage', [20, 60]);
        setw(load, 'image', 'example.png');
        const looks = presets.map((preset, i) => {
            const look = mk('PW_Look', [340, 60 + i * 40]);
            setw(look, 'preset', preset);
            load.connect(0, look, 0);
            return look;
        });
        let batch = looks[0];
        for (let i = 1; i < looks.length; i++) {
            const b = mk('ImageBatch', [820, 60 + i * 60]);
            batch.connect(0, b, 0);
            looks[i].connect(0, b, 1);
            batch = b;
        }
        const review = mk('PW_Review', [1180, 60]);
        const preview = mk('PreviewImage', [1700, 60]);
        batch.connect(0, review, 0);
        review.connect(0, preview, 0);
        app.canvas.setDirty(true, true);
        return String(review.id);
    }""",
        ["warm-portrait", "cool-cinematic", "golden-hour", "bleach-bypass"],
    )
    page.wait_for_timeout(1500)
    page.evaluate("() => window.app.queuePrompt(0, 1)")
    for _ in range(120):
        time.sleep(1)
        with urllib.request.urlopen(f"{BASE}/api/pw_color/review/{review_id}", timeout=5) as r:
            if json.load(r)["holding"]:
                break
    page.wait_for_timeout(3000)

    click_panel = """([id, lx, ly]) => {
        const n = window.app.graph.getNodeById(id);
        const cv = n.widgets.find(w => w.name === 'pw_panel').element.querySelector('canvas');
        const r = cv.getBoundingClientRect(); const s = r.width / cv.offsetWidth;
        for (const type of ['pointerdown', 'pointerup'])
            cv.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true,
                clientX: r.x + lx * s, clientY: r.y + ly * s, pointerId: 1, pointerType: 'mouse',
                button: 0, buttons: 1, isPrimary: true }));
    }"""
    # The star row sits under each 76px thumbnail, 16px per star, below a
    # header, the focus view and a section gap.
    star_y = 18 + 6 + 220 + 20 + 76 + 8
    for x in (16 * 3.5, 96 + 8 + 16 * 1.5, 2 * (96 + 8) + 16 * 4.5):
        page.evaluate(click_panel, [review_id, x, star_y])
        page.wait_for_timeout(200)
    page.evaluate(click_panel, [review_id, 2 * (96 + 8) + 48, 300])  # focus the five-star frame
    page.wait_for_timeout(900)
    page.evaluate(
        """(id) => { const app = window.app; const n = app.graph.getNodeById(id);
            app.canvas.setZoom ? app.canvas.setZoom(1, [0, 0]) : (app.canvas.ds.scale = 1);
            app.canvas.ds.offset[0] = -(n.pos[0]) + 300; app.canvas.ds.offset[1] = -(n.pos[1]) + 160;
            app.canvas.setDirty(true, true); }""",
        review_id,
    )
    page.wait_for_timeout(1200)
    page.locator(f'[data-node-id="{review_id}"]').first.screenshot(path=f"{OUT}/pw_review.png")
    print("pw_review.png")
    # Let the run finish rather than leaving a held prompt behind.
    urllib.request.urlopen(
        urllib.request.Request(
            f"{BASE}/api/pw_color/review/{review_id}/release",
            data=json.dumps({"ratings": [4, 2, 5, 0]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        ),
        timeout=5,
    )

    # The whole example workflow, fitted. Reloaded, because the staged graph
    # above cleared it.
    page.evaluate("""async () => {
        const doc = await (await fetch('/api/workflow_templates/PW_ColorTools/pw_color_basic.json')).json();
        await window.app.loadGraphData(doc, true, false, 'capture');
    }""")
    page.wait_for_timeout(4000)
    page.evaluate("""() => {
        const app = window.app;
        const nodes = app.graph._nodes;
        let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
        for (const n of nodes) { x0 = Math.min(x0, n.pos[0]); y0 = Math.min(y0, n.pos[1] - 40); x1 = Math.max(x1, n.pos[0] + n.size[0]); y1 = Math.max(y1, n.pos[1] + n.size[1]); }
        const el = app.canvas.canvas; const W = el.clientWidth, H = el.clientHeight;
        const s = Math.min((W - 80) / (x1 - x0), (H - 80) / (y1 - y0));
        app.canvas.setZoom ? app.canvas.setZoom(s, [0, 0]) : (app.canvas.ds.scale = s);
        app.canvas.ds.offset[0] = -x0 + 40 / s; app.canvas.ds.offset[1] = -y0 + 40 / s;
        app.canvas.setDirty(true, true);
        window.__bounds = { x0, y0, x1, y1, s };
    }""")
    page.wait_for_timeout(1500)
    graph = page.locator(".graph-canvas-container, #graph-canvas").first
    graph.screenshot(path=f"{OUT}/example_workflow.png")
    from PIL import Image
    im = Image.open(f"{OUT}/example_workflow.png"); im = im.resize((im.width // 2, im.height // 2), Image.LANCZOS); im.save(f"{OUT}/example_workflow.png", optimize=True)
    print("example_workflow.png", im.size)
    browser.close()
