"""Recapture the README's per-node screenshots.

Drives a headless Chrome (Playwright, `channel="chrome"`, so nothing is
downloaded) against a ComfyUI instance on port 8199 with this pack installed:
loads the example workflow, runs it once so previews and the palette are
populated, applies a preset and two curve points, then screenshots each node
element in Modern Node Design at 2x. Writes into docs/images.

    pip install playwright   # any Python; Chrome must be installed
    python main.py --cpu --port 8199 --disable-auto-launch    # in ComfyUI
    python tools/capture_readme.py [--review-folder DIR]

PW Review only looks like anything with images in its tray, so its main photo
needs real images: pass --review-folder with a folder of generated frames
(the README's shot is 25 Krea-2 images from one prompt). That capture uses
LoadImagesFromFolderKJ, so it needs KJNodes installed. Without the flag the
existing pw_review.png is left alone rather than replaced by something staged.
The wiring photo, pw_review_wired.png, needs no images and is always taken.
"""

import argparse
import json
import os
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8199"
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


ARGS = argparse.ArgumentParser(description="Recapture the README screenshots.")
ARGS.add_argument("--review-folder", default="", help="folder of generated images for the PW Review shot")
REVIEW_FOLDER = ARGS.parse_args().review_folder

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

    # A brand-new, empty workflow whose node ids start at 5001.
    #
    # graph.clear() is not enough. It restarts ids at 1, and the host keeps
    # per-id state from whatever workflow was open - cached outputs, source
    # badges, error markers, node sizes - so a new node silently inherits the
    # look of the old node that had its number. That is how PW Review came out
    # 3500px tall with the template's palette drawn into it, and how VAE Decode
    # wore a PW_ColorTools badge. Ids nothing has used cannot inherit anything.
    fresh = """async (name) => {
        await window.app.loadGraphData(
            { last_node_id: 5000, last_link_id: 5000, nodes: [], links: [], groups: [], config: {}, extra: {}, version: 0.4 },
            true, false, name);
        await new Promise(r => setTimeout(r, 1200));
    }"""

    click_panel = """([id, lx, ly]) => {
        const n = window.app.graph.getNodeById(id);
        const cv = n.widgets.find(w => w.name === 'pw_panel').element.querySelector('canvas');
        const r = cv.getBoundingClientRect(); const s = r.width / cv.offsetWidth;
        for (const type of ['pointerdown', 'pointerup'])
            cv.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true,
                clientX: r.x + lx * s, clientY: r.y + ly * s, pointerId: 1, pointerType: 'mouse',
                button: 0, buttons: 1, isPrimary: true }));
    }"""

    if REVIEW_FOLDER:
        # A real batch, collected in the tray and rated across the grid. Widened to five across
        # so 25 frames is five rows rather than nine.
        page.evaluate(fresh, "capture-review")
        review_id = page.evaluate(
            """(folder) => {
            const app = window.app;
            const mk = (type, pos) => { const n = window.LiteGraph.createNode(type); n.pos = pos; app.graph.add(n); return n; };
            const setw = (n, name, v) => { const w = n.widgets.find(w => w.name === name); if (w) { w.value = v; w.callback?.(v); } };
            const load = mk('LoadImagesFromFolderKJ', [20, 60]);
            setw(load, 'folder', folder); setw(load, 'width', -1); setw(load, 'height', -1);
            const review = mk('PW_Review', [600, 60]);
            review.size[0] = 560;
            const preview = mk('PreviewImage', [1300, 60]);
            load.connect(0, review, 0); review.connect(0, preview, 0);
            app.canvas.setDirty(true, true);
            return String(review.id);
        }""",
            REVIEW_FOLDER,
        )
        page.wait_for_timeout(1500)
        page.evaluate("() => window.app.queuePrompt(0, 1)")
        for _ in range(180):
            time.sleep(1)
            with urllib.request.urlopen(f"{BASE}/api/pw_color/review/{review_id}", timeout=5) as r:
                if json.load(r)["count"]:
                    break
        page.wait_for_timeout(9000)
        cols, panel_h = page.evaluate(
            """(id) => { const n = window.app.graph.getNodeById(id);
                const cv = n.widgets.find(w => w.name === 'pw_panel').element.querySelector('canvas');
                return [Math.max(1, Math.floor((cv.offsetWidth + 8) / (96 + 8))), cv.offsetHeight]; }""",
            review_id,
        )
        ratings = [5, 0, 3, 4, 0, 2, 5, 0, 4, 1, 0, 3, 5, 2, 0, 4, 3, 0, 1, 5, 0, 2, 4, 0, 3]
        # Star rows sit under each 76px thumbnail, 16px per star, below the
        # header, the toolbar, the focus view and its grip. The view takes
        # whatever height the rest leaves, so it is measured, not assumed.
        rows = -(-len(ratings) // cols)
        strip_h = rows * 92 + (rows - 1) * 8
        view_h = panel_h - (18 + 4 + 20 + 6 + 12 + strip_h + 12)
        strip_top = 18 + 4 + 20 + 6 + view_h + 12
        for i, stars in enumerate(ratings):
            if stars:
                x = (i % cols) * (96 + 8) + (stars - 0.5) * 16
                y = strip_top + (i // cols) * (92 + 8) + 76 + 8
                page.evaluate(click_panel, [review_id, x, y])
                page.wait_for_timeout(80)
        page.evaluate(click_panel, [review_id, 48, strip_top + 30])  # focus a five-star frame
        page.wait_for_timeout(1200)
        # Clear of the canvas toolbar, which otherwise draws over the title.
        page.evaluate(
            """(id) => { const app = window.app; const n = app.graph.getNodeById(id);
                app.canvas.setZoom ? app.canvas.setZoom(1, [0, 0]) : (app.canvas.ds.scale = 1);
                app.canvas.ds.offset[0] = -(n.pos[0]) + 300; app.canvas.ds.offset[1] = -(n.pos[1]) + 220;
                app.canvas.setDirty(true, true); }""",
            review_id,
        )
        page.wait_for_timeout(1500)
        page.locator(f'[data-node-id="{review_id}"]').first.screenshot(path=f"{OUT}/pw_review.png")
        print("pw_review.png")
        urllib.request.urlopen(
            urllib.request.Request(
                f"{BASE}/api/pw_color/review/{review_id}/clear",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=10,
        )
        page.wait_for_timeout(2500)
    else:
        print("pw_review.png left as it is (pass --review-folder to retake it)")

    # Where PW Review goes: the tail of a generation graph and nothing else,
    # laid out from the widths the renderer actually drew. Under Modern Node
    # Design node.size disagrees with the DOM, and placing by it stacks nodes.
    page.evaluate(fresh, "capture-wired")
    tail_ids = page.evaluate(
        """async () => {
        const app = window.app, g = app.graph;
        const mk = (type) => { const n = window.LiteGraph.createNode(type); g.add(n); return n; };
        const setw = (n, name, v) => { const w = n.widgets?.find(w => w.name === name); if (w) { w.value = v; w.callback?.(v); } };
        const ks = mk('KSampler');
        setw(ks, 'steps', 11); setw(ks, 'cfg', 1); setw(ks, 'sampler_name', 'euler'); setw(ks, 'scheduler', 'simple');
        const dec = mk('VAEDecode'), review = mk('PW_Review'), save = mk('SaveImage');
        setw(save, 'filename_prefix', 'keepers');
        ks.connect(0, dec, 0); dec.connect(0, review, 0); review.connect(0, save, 0);
        app.canvas.setZoom ? app.canvas.setZoom(1, [0, 0]) : (app.canvas.ds.scale = 1);
        app.canvas.ds.offset[0] = 120; app.canvas.ds.offset[1] = 220;
        const order = [ks, dec, review, save];
        order.forEach((n, i) => { n.pos = [i * 1000, 0]; });
        app.canvas.setDirty(true, true);
        await new Promise(r => setTimeout(r, 1500));
        const width = (n) => { const el = document.querySelector(`[data-node-id="${n.id}"]`);
            return el ? el.getBoundingClientRect().width / app.canvas.ds.scale : n.size[0]; };
        let x = 0;
        for (const n of order) { n.pos = [x, 0]; x += width(n) + 90; }
        // Links can be hidden in the user settings (Comfy.LinkRenderMode -1).
        // Assigning the canvas property turns them on for this page only;
        // setting.set() would write to the shared user settings file.
        app.canvas.links_render_mode = window.LiteGraph.SPLINE_LINK ?? 2;
        app.canvas.setDirty(true, true);
        await new Promise(r => setTimeout(r, 2000));
        return order.map(n => String(n.id));
    }"""
    )
    page.wait_for_timeout(1500)
    boxes = [page.locator(f'[data-node-id="{i}"]').first.bounding_box() for i in tail_ids]
    boxes = [bx for bx in boxes if bx]
    pad = 24
    x0 = min(bx["x"] for bx in boxes) - pad
    y0 = min(bx["y"] for bx in boxes) - pad
    x1 = max(bx["x"] + bx["width"] for bx in boxes) + pad
    y1 = max(bx["y"] + bx["height"] for bx in boxes) + pad
    page.screenshot(path=f"{OUT}/pw_review_wired.png", clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0})
    print("pw_review_wired.png")

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
