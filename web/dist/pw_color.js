// src/comfy.ts
import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";
var MIN_FRONTEND = [1, 40, 0];
function getWidget(node, name) {
  return node.widgets?.find((w) => w.name === name);
}
function frontendVersion() {
  const raw = globalThis.__COMFYUI_FRONTEND_VERSION__ ?? app?.frontendVersion ?? app?.extensionManager?.version;
  if (typeof raw !== "string") return null;
  const m = raw.match(/(\d+)\.(\d+)\.(\d+)/);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}
function warnIfUnsupported() {
  const v = frontendVersion();
  if (!v) return;
  const [a, b] = v;
  const [minA, minB] = MIN_FRONTEND;
  if (a < minA || a === minA && b < minB) {
    console.warn(
      `[PW Color] ComfyUI frontend ${v.join(".")} is older than the tested minimum ${MIN_FRONTEND.join(".")}. Nodes may render incorrectly.`
    );
  }
}

// src/theme.ts
var PW = {
  color: {
    panel: "#1B1A20",
    // node body
    header: "#272433",
    // header bar
    surface: "#201E28",
    // inset control group
    well: "#131218",
    // viewers, canvases, scopes
    chip: "#272433",
    // button / chip resting
    chipActive: "#3E3856",
    // selected chip
    border: "#4A4358",
    // panel + control borders
    borderSoft: "#3A3545",
    // internal dividers
    grid: "#2A2733",
    // graph gridlines
    text: "#F0EEF8",
    // primary
    textDim: "#B9B5C8",
    // labels
    textMute: "#8F8AA3",
    // values, hints
    accent: "#7F77DD",
    // Promptwaffle purple
    onAccent: "#1A172E",
    // text on accent fills
    // Not a channel: the accent for the grain response curve and the
    // approximate-preview badge. It used to live in `channel`, whose other
    // four keys are the colour channels scopes and curve tabs index by.
    warm: "#E0A44C"
  },
  /** Curve editor channel tabs and scope traces. */
  channel: {
    luma: "#F0EEF8",
    r: "#D96A6A",
    g: "#7FBF9E",
    b: "#7FA8DD"
  },
  /** Port colours, applied to the custom types on extension registration. */
  port: {
    IMAGE: "#7FBF9E",
    MASK: "#8F8AA3",
    LOOK: "#7F77DD",
    PALETTE: "#E0A44C"
  },
  /**
   * Two type sizes only. A third size always turns into a fourth.
   * Sentence case everywhere — no Title Case, no ALL CAPS.
   */
  font: {
    title: '500 14px Inter, "Segoe UI", system-ui, sans-serif',
    body: '400 12px Inter, "Segoe UI", system-ui, sans-serif',
    /** Values and readouts. Tabular so digits stop jittering during a drag. */
    mono: '400 12px "Roboto Mono", ui-monospace, Consolas, monospace',
    minSize: 11
  },
  metrics: {
    controlHeight: 26,
    radiusControl: 4,
    radiusPanel: 8,
    radiusNode: 12,
    border: 1,
    borderHair: 0.5,
    padding: 12,
    // node internal padding
    gapControl: 8,
    // between sibling controls
    gapSection: 20,
    // between labelled sections
    /** Slider knob radius and the hit slop around it. Fitts's law, not taste. */
    knob: 5,
    hitSlop: 8
  },
  /**
   * Drag modifiers. Centralised so every control behaves the same way — the
   * moment one canvas invents its own fine-adjust ratio, muscle memory breaks.
   *
   * Only settings that are actually wired up live here. A constant describing
   * behaviour the pack does not have is worse than no constant: it reads as
   * implemented. Hold-to-compare is on the roadmap, not in this object.
   */
  interaction: {
    /** Shift-drag multiplier, used by every slider and the curve editor. */
    fineDragScale: 0.15,
    /** Milliseconds within which a second press counts as a double click. */
    doubleClickMs: 300
  }
};
var BADGE = {
  lut: { label: "LUT", fill: PW.color.chipActive, text: PW.color.text },
  render: { label: "render only", fill: PW.color.surface, text: PW.color.textMute },
  approx: { label: "preview approximate", fill: PW.color.surface, text: PW.color.warm }
};

// src/fetch.ts
async function fetchPw(path) {
  const { api: api2 } = await import("/scripts/api.js");
  return api2.fetchApi(path, { cache: "no-store" });
}
async function postPw(path, body) {
  const { api: api2 } = await import("/scripts/api.js");
  return api2.fetchApi(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store"
  });
}

// src/core/curve.ts
var IDENTITY_POINTS = [
  [0, 0],
  [1, 1]
];
function isIdentity(points) {
  if (points.length !== 2) return false;
  const [[x0, y0], [x1, y1]] = points;
  return Math.abs(x0) < 1e-9 && Math.abs(y0) < 1e-9 && Math.abs(x1 - 1) < 1e-9 && Math.abs(y1 - 1) < 1e-9;
}
function sortedUnique(points) {
  const pts = points.map((p) => [p[0], p[1]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const xs = [];
  const ys = [];
  for (const [x, y] of pts) {
    if (xs.length && Math.abs(x - xs[xs.length - 1]) < 1e-7) ys[ys.length - 1] = y;
    else {
      xs.push(x);
      ys.push(y);
    }
  }
  if (xs.length < 2) {
    const y = ys.length ? ys[0] : 0;
    return { xs: [0, 1], ys: [y, ys.length ? y : 1] };
  }
  return { xs, ys };
}
function monotoneTangents(xs, ys) {
  const n = xs.length;
  const h = [];
  const delta = [];
  for (let i = 0; i < n - 1; i++) {
    h.push(xs[i + 1] - xs[i]);
    delta.push((ys[i + 1] - ys[i]) / h[i]);
  }
  const m = new Array(n).fill(0);
  m[0] = delta[0];
  m[n - 1] = delta[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (delta[i - 1] * delta[i] <= 0) {
      m[i] = 0;
    } else {
      const w1 = 2 * h[i] + h[i - 1];
      const w2 = h[i] + 2 * h[i - 1];
      m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i]);
    }
  }
  for (let i = 0; i < n - 1; i++) {
    if (delta[i] === 0) {
      m[i] = 0;
      m[i + 1] = 0;
      continue;
    }
    const a = m[i] / delta[i];
    const b = m[i + 1] / delta[i];
    const s = a * a + b * b;
    if (s > 9) {
      const t = 3 / Math.sqrt(s);
      m[i] = t * a * delta[i];
      m[i + 1] = t * b * delta[i];
    }
  }
  return m;
}
var Curve = class {
  xs;
  ys;
  m;
  constructor(points) {
    const { xs, ys } = sortedUnique(points);
    this.xs = xs;
    this.ys = ys;
    this.m = monotoneTangents(xs, ys);
  }
  /** Evaluate at x, linearly extended past the ends, clamped to [0,1]. */
  at(x) {
    const { xs, ys, m } = this;
    const n = xs.length;
    if (x < xs[0]) return clamp01(ys[0] + (x - xs[0]) * m[0]);
    if (x > xs[n - 1]) return clamp01(ys[n - 1] + (x - xs[n - 1]) * m[n - 1]);
    let lo = 0;
    let hi = n - 1;
    while (hi - lo > 1) {
      const mid = lo + hi >> 1;
      if (xs[mid] <= x) lo = mid;
      else hi = mid;
    }
    const h = xs[lo + 1] - xs[lo];
    const t = (x - xs[lo]) / h;
    const t2 = t * t;
    const t3 = t2 * t;
    const h00 = 2 * t3 - 3 * t2 + 1;
    const h10 = t3 - 2 * t2 + t;
    const h01 = -2 * t3 + 3 * t2;
    const h11 = t3 - t2;
    return clamp01(h00 * ys[lo] + h10 * h * m[lo] + h01 * ys[lo + 1] + h11 * h * m[lo + 1]);
  }
};
function clamp01(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

// src/widgets/draw.ts
function hit(r, x, y, slop = 0) {
  return x >= r.x - slop && x <= r.x + r.w + slop && y >= r.y - slop && y <= r.y + r.h + slop;
}
function roundRect(ctx, r, radius) {
  ctx.beginPath();
  if (typeof ctx.roundRect === "function") {
    ctx.roundRect(r.x, r.y, r.w, r.h, radius);
    return;
  }
  const rad = Math.min(radius, r.w / 2, r.h / 2);
  ctx.moveTo(r.x + rad, r.y);
  ctx.arcTo(r.x + r.w, r.y, r.x + r.w, r.y + r.h, rad);
  ctx.arcTo(r.x + r.w, r.y + r.h, r.x, r.y + r.h, rad);
  ctx.arcTo(r.x, r.y + r.h, r.x, r.y, rad);
  ctx.arcTo(r.x, r.y, r.x + r.w, r.y, rad);
  ctx.closePath();
}
function fillPanel(ctx, r, fill, radius = PW.metrics.radiusPanel, border) {
  roundRect(ctx, r, radius);
  ctx.fillStyle = fill;
  ctx.fill();
  if (border) {
    ctx.strokeStyle = border;
    ctx.lineWidth = PW.metrics.border;
    ctx.stroke();
  }
}
function hairline(ctx, x0, y0, x1, y1, colour = PW.color.borderSoft) {
  ctx.strokeStyle = colour;
  ctx.lineWidth = PW.metrics.borderHair;
  ctx.beginPath();
  ctx.moveTo(Math.round(x0) + 0.5, Math.round(y0) + 0.5);
  ctx.lineTo(Math.round(x1) + 0.5, Math.round(y1) + 0.5);
  ctx.stroke();
}
function text(ctx, s, x, y, opts = {}) {
  ctx.font = opts.font ?? PW.font.body;
  ctx.fillStyle = opts.colour ?? PW.color.textDim;
  ctx.textAlign = opts.align ?? "left";
  ctx.textBaseline = opts.baseline ?? "middle";
  ctx.fillText(s, x, y);
}
function sectionHeader(ctx, label, r, badge) {
  text(ctx, label, r.x, r.y + r.h / 2, { colour: PW.color.textDim });
  if (!badge) return;
  ctx.font = PW.font.body;
  const w = ctx.measureText(badge.label).width + 12;
  const bx = r.x + r.w - w;
  fillPanel(ctx, { x: bx, y: r.y + (r.h - 16) / 2, w, h: 16 }, badge.fill, PW.metrics.radiusControl);
  text(ctx, badge.label, bx + w / 2, r.y + r.h / 2, { colour: badge.text, align: "center" });
}
function headerChip(ctx, r, label, badgeLabel) {
  const measure = (s) => ctx ? (ctx.font = PW.font.body, ctx.measureText(s).width) : s.length * 6.2;
  const badgeW = badgeLabel ? measure(badgeLabel) + 12 + 6 : 0;
  const w = measure(label) + 14;
  const rect = { x: r.x + r.w - badgeW - w, y: r.y + (r.h - 16) / 2, w, h: 16 };
  if (ctx) {
    fillPanel(ctx, rect, PW.color.chip, PW.metrics.radiusControl, PW.color.borderSoft);
    text(ctx, label, rect.x + rect.w / 2, r.y + r.h / 2, { colour: PW.color.textMute, align: "center" });
  }
  return rect;
}
function formatValue(v, decimals = 2) {
  const s = v.toFixed(decimals);
  return s === "-0.00" || s === "-0.0" || s === "-0" ? s.slice(1) : s;
}

// src/canvas/curve_editor.ts
var GRID_DIVISIONS = 4;
var POINT_RADIUS = 4;
var GRAB_SLOP = 10;
var MIN_POINTS = 2;
var MIN_X_GAP = 8e-3;
function identityState() {
  return {
    luma: IDENTITY_POINTS.map((p) => [p[0], p[1]]),
    r: IDENTITY_POINTS.map((p) => [p[0], p[1]]),
    g: IDENTITY_POINTS.map((p) => [p[0], p[1]]),
    b: IDENTITY_POINTS.map((p) => [p[0], p[1]])
  };
}
var CHANNEL_COLOUR = {
  luma: PW.channel.luma,
  r: PW.channel.r,
  g: PW.channel.g,
  b: PW.channel.b
};
var CurveEditor = class {
  state = identityState();
  channel = "luma";
  /** 256-bin per-channel histogram of the node's input, or null before we have one. */
  histogram = null;
  /** Called whenever the curve changes, so the node can re-bake and serialise. */
  onChange = null;
  dragIndex = -1;
  ghost = null;
  hoverIndex = -1;
  lastClickTime = 0;
  dragMoved = false;
  get points() {
    return this.state[this.channel];
  }
  set points(v) {
    this.state[this.channel] = v;
  }
  // -- coordinate mapping ---------------------------------------------------
  // Curve space is (0,0) bottom-left to (1,1) top-right. Canvas y is inverted.
  toCanvas(r, p) {
    return [r.x + p[0] * r.w, r.y + (1 - p[1]) * r.h];
  }
  toCurve(r, x, y) {
    return [clamp012((x - r.x) / r.w), clamp012(1 - (y - r.y) / r.h)];
  }
  // -- drawing --------------------------------------------------------------
  draw(ctx, r) {
    fillPanel(ctx, r, PW.color.well, PW.metrics.radiusPanel, PW.color.border);
    ctx.save();
    ctx.beginPath();
    ctx.rect(r.x, r.y, r.w, r.h);
    ctx.clip();
    this.drawHistogram(ctx, r);
    this.drawGrid(ctx, r);
    this.drawIdentity(ctx, r);
    if (this.ghost) this.drawCurve(ctx, r, this.ghost, PW.color.textMute, 1, true);
    this.drawCurve(ctx, r, this.points, CHANNEL_COLOUR[this.channel], 2, false);
    this.drawPoints(ctx, r);
    ctx.restore();
    this.drawReadout(ctx, r);
  }
  drawGrid(ctx, r) {
    for (let i = 1; i < GRID_DIVISIONS; i++) {
      const t = i / GRID_DIVISIONS;
      hairline(ctx, r.x + t * r.w, r.y, r.x + t * r.w, r.y + r.h, PW.color.grid);
      hairline(ctx, r.x, r.y + t * r.h, r.x + r.w, r.y + t * r.h, PW.color.grid);
    }
  }
  /**
   * The input histogram, behind the grid.
   *
   * Drawn on a mild power scale rather than linear or log: a linear histogram
   * of a normal photograph is one spike and a flat line, and a log one makes
   * three stray pixels look like a tonal region. 0.4 is the usual compromise.
   */
  drawHistogram(ctx, r) {
    const h = this.histogram;
    if (!h) return;
    const bins = this.channel === "luma" ? h.luma : this.channel === "r" ? h.r : this.channel === "g" ? h.g : h.b;
    let peak = 0;
    for (let i = 0; i < bins.length; i++) peak = Math.max(peak, bins[i]);
    if (peak <= 0) return;
    ctx.fillStyle = PW.color.surface;
    ctx.beginPath();
    ctx.moveTo(r.x, r.y + r.h);
    for (let i = 0; i < bins.length; i++) {
      const t = i / (bins.length - 1);
      const v = Math.pow(bins[i] / peak, 0.4);
      ctx.lineTo(r.x + t * r.w, r.y + r.h - v * r.h * 0.92);
    }
    ctx.lineTo(r.x + r.w, r.y + r.h);
    ctx.closePath();
    ctx.fill();
  }
  drawIdentity(ctx, r) {
    ctx.save();
    ctx.setLineDash([3, 4]);
    ctx.strokeStyle = PW.color.borderSoft;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(r.x, r.y + r.h);
    ctx.lineTo(r.x + r.w, r.y);
    ctx.stroke();
    ctx.restore();
  }
  drawCurve(ctx, r, pts, colour, width, dashed) {
    const curve = new Curve(pts);
    ctx.save();
    if (dashed) {
      ctx.setLineDash([2, 3]);
      ctx.globalAlpha = 0.7;
    }
    ctx.strokeStyle = colour;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.beginPath();
    const steps = Math.max(64, Math.ceil(r.w));
    for (let i = 0; i <= steps; i++) {
      const x = i / steps;
      const [cx, cy] = this.toCanvas(r, [x, curve.at(x)]);
      if (i === 0) ctx.moveTo(cx, cy);
      else ctx.lineTo(cx, cy);
    }
    ctx.stroke();
    ctx.restore();
  }
  drawPoints(ctx, r) {
    const colour = CHANNEL_COLOUR[this.channel];
    this.points.forEach((p, i) => {
      const [x, y] = this.toCanvas(r, p);
      const active = i === this.dragIndex || i === this.hoverIndex;
      ctx.beginPath();
      ctx.arc(x, y, active ? POINT_RADIUS + 1.5 : POINT_RADIUS, 0, Math.PI * 2);
      ctx.fillStyle = active ? PW.color.text : PW.color.panel;
      ctx.fill();
      ctx.strokeStyle = colour;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    });
  }
  drawReadout(ctx, r) {
    const i = this.dragIndex >= 0 ? this.dragIndex : this.hoverIndex;
    if (i < 0 || i >= this.points.length) return;
    const p = this.points[i];
    const label = `in ${formatValue(p[0], 3)}   out ${formatValue(p[1], 3)}`;
    text(ctx, label, r.x + r.w - 8, r.y + 12, {
      colour: PW.color.textMute,
      align: "right",
      font: PW.font.mono
    });
  }
  // -- interaction ----------------------------------------------------------
  findPoint(r, x, y) {
    let best = -1;
    let bestD = GRAB_SLOP;
    this.points.forEach((p, i) => {
      const [px, py] = this.toCanvas(r, p);
      const d = Math.hypot(px - x, py - y);
      if (d <= bestD) {
        bestD = d;
        best = i;
      }
    });
    return best;
  }
  onPointerDown(x, y, r, shift, now) {
    const idx = this.findPoint(r, x, y);
    if (shift && idx >= 0) {
      if (this.points.length <= MIN_POINTS) return true;
      this.points = this.points.filter((_, i) => i !== idx);
      this.hoverIndex = -1;
      this.changed();
      return true;
    }
    if (now - this.lastClickTime < PW.interaction.doubleClickMs && idx < 0) {
      this.resetChannel();
      this.lastClickTime = 0;
      return true;
    }
    this.lastClickTime = now;
    this.ghost = this.points.map((p2) => [p2[0], p2[1]]);
    this.dragMoved = false;
    if (idx >= 0) {
      this.dragIndex = idx;
      return true;
    }
    const p = this.toCurve(r, x, y);
    if (this.points.some((q) => Math.abs(q[0] - p[0]) < MIN_X_GAP)) {
      this.ghost = null;
      return true;
    }
    const next = [...this.points, p].sort((a, b) => a[0] - b[0]);
    this.points = next;
    this.dragIndex = next.findIndex((q) => q === p);
    this.changed();
    return true;
  }
  onPointerMove(x, y, r, shift) {
    if (this.dragIndex < 0) {
      const idx = this.findPoint(r, x, y);
      const changed = idx !== this.hoverIndex;
      this.hoverIndex = idx;
      return changed;
    }
    let p = this.toCurve(r, x, y);
    if (shift && this.ghost) {
      const start = this.ghost[Math.min(this.dragIndex, this.ghost.length - 1)];
      p = [
        clamp012(start[0] + (p[0] - start[0]) * PW.interaction.fineDragScale),
        clamp012(start[1] + (p[1] - start[1]) * PW.interaction.fineDragScale)
      ];
    }
    const isFirst = this.dragIndex === 0;
    const isLast = this.dragIndex === this.points.length - 1;
    if (isFirst) p = [0, p[1]];
    if (isLast) p = [1, p[1]];
    if (!isFirst && !isLast) {
      const lo = this.points[this.dragIndex - 1][0] + MIN_X_GAP;
      const hi = this.points[this.dragIndex + 1][0] - MIN_X_GAP;
      p = [Math.min(Math.max(p[0], lo), hi), p[1]];
    }
    const cur = this.points[this.dragIndex];
    if (cur[0] === p[0] && cur[1] === p[1]) return false;
    this.points = this.points.map((q, i) => i === this.dragIndex ? p : q);
    this.dragMoved = true;
    this.changed();
    return true;
  }
  onPointerUp() {
    const was = this.dragIndex >= 0;
    this.dragIndex = -1;
    this.ghost = null;
    if (was && !this.dragMoved) this.changed();
    return was;
  }
  get isDragging() {
    return this.dragIndex >= 0;
  }
  resetChannel() {
    this.points = IDENTITY_POINTS.map((p) => [p[0], p[1]]);
    this.hoverIndex = -1;
    this.changed();
  }
  resetAll() {
    this.state = identityState();
    this.changed();
  }
  changed() {
    this.onChange?.();
  }
};
function clamp012(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

// src/core/lattice.ts
var DEFAULT_SIZE = 33;
var OUT_MIN = -0.5;
var OUT_MAX = 2;
var Lattice = class _Lattice {
  size;
  /** Red-fastest flat storage, length size³ * 3. */
  data;
  constructor(size, data) {
    if (data.length !== size * size * size * 3) {
      throw new Error(`lattice data length ${data.length} does not match size ${size}`);
    }
    this.size = size;
    this.data = data;
  }
  static identity(size = DEFAULT_SIZE) {
    const d = new Float32Array(size * size * size * 3);
    let i = 0;
    for (let bi = 0; bi < size; bi++) {
      for (let gi = 0; gi < size; gi++) {
        for (let ri = 0; ri < size; ri++) {
          d[i++] = ri / (size - 1);
          d[i++] = gi / (size - 1);
          d[i++] = bi / (size - 1);
        }
      }
    }
    return new _Lattice(size, d);
  }
  /**
   * Bake a pure per-pixel function. `fn` must not close over mutable state.
   *
   * Two things here are load-bearing for preview/render parity, and both are
   * mirrored in `Lattice.from_fn` on the Python side:
   *
   * - The bake accumulates into a **Float64Array**. Rounding each sample to
   *   float32 before quantising would put us half a float32 ULP away from
   *   torch's float64 bake, which is enough to flip a u16 code wherever an op
   *   runs off the edge of the sRGB gamut and the local gain is large.
   * - The result is **quantised on construction**, so an unquantised lattice
   *   can never reach the preview shader while the renderer holds a quantised
   *   one. Pass `encoding: null` only for `.cube` authoring.
   */
  static fromFn(fn, size = DEFAULT_SIZE, encoding = "u16") {
    const d = new Float64Array(size * size * size * 3);
    const s = size - 1;
    let i = 0;
    for (let bi = 0; bi < size; bi++) {
      const b = bi / s;
      for (let gi = 0; gi < size; gi++) {
        const g = gi / s;
        for (let ri = 0; ri < size; ri++) {
          const out = fn([ri / s, g, b]);
          d[i++] = out[0];
          d[i++] = out[1];
          d[i++] = out[2];
        }
      }
    }
    if (encoding === null) return new _Lattice(size, Float32Array.from(d));
    return _Lattice.fromTransport(quantise(size, d, encoding));
  }
  /** Sample and clamp — the image-output path. Mirrors `Lattice.apply`. */
  applyImage(rgb) {
    const o = this.applyPoints(rgb);
    return [clamp(o[0], 0, 1), clamp(o[1], 0, 1), clamp(o[2], 0, 1)];
  }
  /** Lerp toward identity, so the strength slider is itself LUT-exportable. */
  blendToIdentity(strength) {
    if (strength >= 1) return this;
    const id = _Lattice.identity(this.size);
    const d = new Float32Array(this.data.length);
    for (let i = 0; i < d.length; i++) d[i] = id.data[i] + (this.data[i] - id.data[i]) * strength;
    return new _Lattice(this.size, d);
  }
  /** Trilinear sample. Must stay identical to `Lattice.apply_points` in Python. */
  applyPoints(rgb) {
    const n = this.size;
    const d = this.data;
    const max = n - 1;
    const cr = clamp(rgb[0], 0, 1) * max;
    const cg = clamp(rgb[1], 0, 1) * max;
    const cb = clamp(rgb[2], 0, 1) * max;
    const r0 = Math.min(Math.floor(cr), n - 2);
    const g0 = Math.min(Math.floor(cg), n - 2);
    const b0 = Math.min(Math.floor(cb), n - 2);
    const fr = cr - r0;
    const fg = cg - g0;
    const fb = cb - b0;
    const idx = (r, g, b) => ((b * n + g) * n + r) * 3;
    const out = [0, 0, 0];
    for (let c = 0; c < 3; c++) {
      const c000 = d[idx(r0, g0, b0) + c];
      const c100 = d[idx(r0 + 1, g0, b0) + c];
      const c010 = d[idx(r0, g0 + 1, b0) + c];
      const c110 = d[idx(r0 + 1, g0 + 1, b0) + c];
      const c001 = d[idx(r0, g0, b0 + 1) + c];
      const c101 = d[idx(r0 + 1, g0, b0 + 1) + c];
      const c011 = d[idx(r0, g0 + 1, b0 + 1) + c];
      const c111 = d[idx(r0 + 1, g0 + 1, b0 + 1) + c];
      const x00 = c000 + (c100 - c000) * fr;
      const x10 = c010 + (c110 - c010) * fr;
      const x01 = c001 + (c101 - c001) * fr;
      const x11 = c011 + (c111 - c011) * fr;
      const y0 = x00 + (x10 - x00) * fg;
      const y1 = x01 + (x11 - x01) * fg;
      out[c] = y0 + (y1 - y0) * fb;
    }
    return out;
  }
  // -- transport ------------------------------------------------------------
  toTransport(encoding = "u16") {
    return quantise(this.size, this.data, encoding);
  }
  static fromTransport(t) {
    const bytes = base64Decode(t.data);
    const n = t.size;
    const count = n * n * n * 3;
    const out = new Float32Array(count);
    if (t.encoding === "u16") {
      const lo = t.out_min ?? 0;
      const hi = t.out_max ?? 1;
      const span = hi - lo;
      const q = new Uint16Array(bytes.buffer, bytes.byteOffset, count);
      for (let i = 0; i < count; i++) out[i] = q[i] / 65535 * span + lo;
    } else {
      out.set(new Float32Array(bytes.buffer, bytes.byteOffset, count));
    }
    return new _Lattice(n, out);
  }
  /** Round-trip through the transport encoding, so preview holds exactly the
   *  numbers the renderer will hold. Called before the preview texture upload. */
  quantised(encoding = "u16") {
    return _Lattice.fromTransport(this.toTransport(encoding));
  }
  toCube(title = "PW Color") {
    const lines = [`TITLE "${title}"`, `LUT_3D_SIZE ${this.size}`, "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0", ""];
    for (let i = 0; i < this.data.length; i += 3) {
      lines.push(
        `${clamp(this.data[i], 0, 1).toFixed(6)} ${clamp(this.data[i + 1], 0, 1).toFixed(6)} ${clamp(this.data[i + 2], 0, 1).toFixed(6)}`
      );
    }
    return lines.join("\n") + "\n";
  }
};
function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}
function quantise(size, d, encoding) {
  let bytes;
  if (encoding === "u16") {
    const span = OUT_MAX - OUT_MIN;
    const q = new Uint16Array(d.length);
    for (let i = 0; i < q.length; i++) {
      q[i] = Math.min(65535, Math.max(0, Math.floor(clamp((d[i] - OUT_MIN) / span, 0, 1) * 65535 + 0.5)));
    }
    bytes = new Uint8Array(q.buffer);
  } else {
    bytes = new Uint8Array(Float32Array.from(d).buffer);
  }
  return { schema: 1, size, encoding, out_min: OUT_MIN, out_max: OUT_MAX, data: base64Encode(bytes) };
}
function base64Encode(bytes) {
  const g = globalThis;
  if (g.Buffer) return g.Buffer.from(bytes).toString("base64");
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return g.btoa(s);
}
function base64Decode(text2) {
  const g = globalThis;
  if (g.Buffer) {
    const b = g.Buffer.from(text2, "base64");
    return new Uint8Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
  }
  const bin = g.atob(text2);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// src/canvas/preview.ts
var VERT = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main() {
  v_uv = vec2(a_pos.x, 1.0 - a_pos.y);
  gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0);
}`;
var FRAG = `#version 300 es
precision highp float;
precision highp sampler3D;

in vec2 v_uv;
out vec4 outColour;

uniform sampler2D u_image;
uniform sampler3D u_lut;
uniform float u_size;     // lattice edge length
uniform float u_wipe;     // 0 = all original, 1 = all graded
uniform float u_enabled;  // 0 disables the lattice entirely
uniform vec2 u_uvScale;   // panel -> image mapping, for fit / zoom
uniform vec2 u_uvOffset;
uniform vec3 u_bg;        // shown outside the image, i.e. the letterbox

// Eight-corner trilinear, matching Lattice.applyPoints line for line.
vec3 sampleLattice(vec3 rgb) {
  float n = u_size;
  vec3 c = clamp(rgb, 0.0, 1.0) * (n - 1.0);
  vec3 i0 = min(floor(c), vec3(n - 2.0));
  vec3 f = c - i0;
  ivec3 b0 = ivec3(i0);

  vec3 c000 = texelFetch(u_lut, b0 + ivec3(0, 0, 0), 0).rgb;
  vec3 c100 = texelFetch(u_lut, b0 + ivec3(1, 0, 0), 0).rgb;
  vec3 c010 = texelFetch(u_lut, b0 + ivec3(0, 1, 0), 0).rgb;
  vec3 c110 = texelFetch(u_lut, b0 + ivec3(1, 1, 0), 0).rgb;
  vec3 c001 = texelFetch(u_lut, b0 + ivec3(0, 0, 1), 0).rgb;
  vec3 c101 = texelFetch(u_lut, b0 + ivec3(1, 0, 1), 0).rgb;
  vec3 c011 = texelFetch(u_lut, b0 + ivec3(0, 1, 1), 0).rgb;
  vec3 c111 = texelFetch(u_lut, b0 + ivec3(1, 1, 1), 0).rgb;

  vec3 x00 = mix(c000, c100, f.r);
  vec3 x10 = mix(c010, c110, f.r);
  vec3 x01 = mix(c001, c101, f.r);
  vec3 x11 = mix(c011, c111, f.r);
  vec3 y0 = mix(x00, x10, f.g);
  vec3 y1 = mix(x01, x11, f.g);
  return mix(y0, y1, f.b);
}

void main() {
  vec2 uv = v_uv * u_uvScale + u_uvOffset;
  // Outside the image is the panel background, not a smeared edge pixel:
  // CLAMP_TO_EDGE would streak the border colour across the letterbox.
  if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
    outColour = vec4(u_bg, 1.0);
    return;
  }
  vec3 src = texture(u_image, uv).rgb;
  vec3 graded = u_enabled > 0.5 ? clamp(sampleLattice(src), 0.0, 1.0) : src;
  // The wipe is in panel space, so it stays put while you pan and zoom.
  outColour = vec4(v_uv.x <= u_wipe ? graded : src, 1.0);
}`;
var Renderer = class {
  canvas;
  gl = null;
  program = null;
  lut = null;
  uniforms = {};
  lutDigest = "";
  failed = false;
  constructor() {
    this.canvas = document.createElement("canvas");
    this.canvas.width = 512;
    this.canvas.height = 512;
  }
  init() {
    if (this.gl) return true;
    if (this.failed) return false;
    const gl = this.canvas.getContext("webgl2", { premultipliedAlpha: false, antialias: false });
    if (!gl) {
      this.failed = true;
      console.warn("[PW Color] WebGL2 unavailable, live preview disabled");
      return false;
    }
    const compile = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        console.error("[PW Color] shader error", gl.getShaderInfoLog(s));
        return null;
      }
      return s;
    };
    const vs = compile(gl.VERTEX_SHADER, VERT);
    const fs = compile(gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) {
      this.failed = true;
      return false;
    }
    const p = gl.createProgram();
    gl.attachShader(p, vs);
    gl.attachShader(p, fs);
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      console.error("[PW Color] link error", gl.getProgramInfoLog(p));
      this.failed = true;
      return false;
    }
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(p, "a_pos");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.useProgram(p);
    for (const name of ["u_image", "u_lut", "u_size", "u_wipe", "u_enabled", "u_uvScale", "u_uvOffset", "u_bg"]) {
      this.uniforms[name] = gl.getUniformLocation(p, name);
    }
    gl.uniform1i(this.uniforms.u_image, 0);
    gl.uniform1i(this.uniforms.u_lut, 1);
    this.gl = gl;
    this.program = p;
    return true;
  }
  /** Upload a lattice as a 3D texture. Cached by digest — a drag re-renders
   *  every frame but only re-uploads when the maths actually changed. */
  uploadLut(lattice, digest) {
    const gl = this.gl;
    if (this.lut && digest === this.lutDigest) return;
    if (!this.lut) this.lut = gl.createTexture();
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_3D, this.lut);
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    for (const axis of [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T, gl.TEXTURE_WRAP_R]) {
      gl.texParameteri(gl.TEXTURE_3D, axis, gl.CLAMP_TO_EDGE);
    }
    const n = lattice.size;
    gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGB32F, n, n, n, 0, gl.RGB, gl.FLOAT, lattice.data);
    this.lutDigest = digest;
  }
  render(image, lattice, digest, w, h, wipe, uvScale, uvOffset, bg) {
    if (!this.init()) return null;
    const gl = this.gl;
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    gl.viewport(0, 0, w, h);
    gl.useProgram(this.program);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, image.texture(gl));
    if (lattice) this.uploadLut(lattice, digest);
    gl.uniform1f(this.uniforms.u_size, lattice ? lattice.size : 2);
    gl.uniform1f(this.uniforms.u_enabled, lattice ? 1 : 0);
    gl.uniform1f(this.uniforms.u_wipe, wipe);
    gl.uniform2f(this.uniforms.u_uvScale, uvScale[0], uvScale[1]);
    gl.uniform2f(this.uniforms.u_uvOffset, uvOffset[0], uvOffset[1]);
    gl.uniform3f(this.uniforms.u_bg, bg[0], bg[1], bg[2]);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    return this.canvas;
  }
};
var TexSource = class {
  tex = null;
  uploaded = false;
  bitmap;
  width;
  height;
  // Written out rather than declared as a constructor parameter property:
  // node's strip-only type removal rejects those, and this module has to load
  // under bare node for `web/test/preview.test.ts` — the same constraint the
  // parity harnesses live under, and for the same reason.
  constructor(bitmap) {
    this.bitmap = bitmap;
    this.width = bitmap.width;
    this.height = bitmap.height;
  }
  texture(gl) {
    if (!this.tex) this.tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    if (!this.uploaded) {
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, this.bitmap);
      this.uploaded = true;
    }
    return this.tex;
  }
  dispose() {
    this.bitmap.close?.();
  }
};
var renderer = null;
function shared() {
  if (!renderer) renderer = new Renderer();
  return renderer;
}
var Preview = class _Preview {
  source = null;
  /**
   * The node's actual rendered output, for spatial nodes.
   *
   * Grain, halation and vignette cannot be baked into a lattice and cannot be
   * reproduced in the browser, so the only honest preview is what the node
   * really produced. When this is set it is what gets drawn, and holding the
   * compare key falls back to `source` — the node's input.
   */
  output = null;
  /**
   * A native-resolution centre crop of the output.
   *
   * Used once zoomed in, because grain size is *absolute*: a downscaled
   * preview would show 1.4px grain finer than it renders, which is worse than
   * showing nothing.
   */
  crop = null;
  lattice = null;
  digest = "";
  /** 0 shows the original across the whole panel; 1 shows the grade. */
  wipe = 1;
  /** True while the compare key is held. */
  comparing = false;
  /** 1 fits the whole image in the panel; above that zooms in. */
  zoom = 1;
  /** Pan, in units of the visible image width/height. 0 is centred. */
  panX = 0;
  panY = 0;
  loading = false;
  loadingOutput = false;
  dragging = null;
  dragFrom = { x: 0, y: 0, panX: 0, panY: 0 };
  /**
   * Pull this node's cached input from the preview route.
   *
   * Always retryable. An earlier version latched a `failedFetch` flag on any
   * error, which meant one transient failure disabled the preview for the life
   * of the node — and since the first attempt happens before the graph has ever
   * run, "no proxy yet" is the normal case rather than an error.
   */
  async load(nodeId, onReady) {
    if (this.loading) return;
    this.loading = true;
    try {
      const res = await fetchPw(`/pw_color/input/${nodeId}`);
      if (!res.ok) return;
      const bmp = await createImageBitmap(await res.blob());
      this.source?.dispose();
      this.source = new TexSource(bmp);
      onReady();
    } catch (err) {
      console.debug("[PW Color] preview fetch failed, will retry after the next run", err);
    } finally {
      this.loading = false;
    }
  }
  /**
   * Fetch the node's rendered output and its 1:1 crop.
   *
   * For spatial nodes only — colour nodes reproduce themselves exactly through
   * the lattice and have no need of this.
   */
  async loadOutput(nodeId, onReady) {
    if (this.loadingOutput) return;
    this.loadingOutput = true;
    try {
      const [full, crop] = await Promise.all([
        fetchPw(`/pw_color/output/${nodeId}`),
        fetchPw(`/pw_color/output_crop/${nodeId}`)
      ]);
      if (!full.ok) return;
      const bmp = await createImageBitmap(await full.blob());
      this.output?.dispose();
      this.output = new TexSource(bmp);
      if (crop.ok) {
        const cbmp = await createImageBitmap(await crop.blob());
        this.crop?.dispose();
        this.crop = new TexSource(cbmp);
      }
      onReady();
    } catch (err) {
      console.debug("[PW Color] output fetch failed, will retry after the next run", err);
    } finally {
      this.loadingOutput = false;
    }
  }
  /** Whether there is anything to draw — and therefore anything to interact with. */
  get hasImage() {
    return this.source !== null || this.output !== null;
  }
  /** Zoom past this switches to the native crop, where there is one. */
  static CROP_AT = 1.4;
  /** What to draw right now, and whether it is the true-scale crop. */
  active() {
    if (this.comparing && this.source) return { tex: this.source, native: false };
    if (this.crop && this.zoom >= _Preview.CROP_AT) return { tex: this.crop, native: true };
    return { tex: this.output ?? this.source, native: false };
  }
  draw(ctx, r) {
    fillPanel(ctx, r, PW.color.well, PW.metrics.radiusPanel, PW.color.border);
    const { tex, native } = this.active();
    if (!tex) {
      text(ctx, "Run the graph once to preview", r.x + r.w / 2, r.y + r.h / 2, {
        colour: PW.color.textMute,
        align: "center"
      });
      return;
    }
    const { uvScale, uvOffset } = this.view(r, tex);
    const wipe = this.comparing ? 0 : this.wipe;
    const w = Math.max(1, Math.round(r.w));
    const h = Math.max(1, Math.round(r.h));
    const lattice = this.output ? null : this.lattice;
    const out = shared().render(
      tex,
      lattice,
      this.digest,
      w,
      h,
      wipe,
      uvScale,
      uvOffset,
      hexToRgb(PW.color.well)
    );
    ctx.save();
    fillPanel(ctx, r, PW.color.well, PW.metrics.radiusPanel);
    ctx.clip();
    if (out) {
      ctx.drawImage(out, r.x, r.y, r.w, r.h);
    } else {
      text(ctx, "WebGL2 unavailable", r.x + r.w / 2, r.y + r.h / 2, {
        colour: PW.color.textMute,
        align: "center"
      });
    }
    ctx.restore();
    if (!this.comparing && this.wipe > 1e-3 && this.wipe < 0.999) {
      const x = r.x + this.wipe * r.w;
      ctx.strokeStyle = PW.color.accent;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + 0.5, r.y);
      ctx.lineTo(Math.round(x) + 0.5, r.y + r.h);
      ctx.stroke();
    }
    if (this.comparing) {
      text(ctx, "before", r.x + 8, r.y + 12, { colour: PW.color.accent });
    } else if (native) {
      text(ctx, "1:1 crop", r.x + r.w - 8, r.y + 12, {
        colour: PW.color.accent,
        align: "right",
        font: PW.font.mono
      });
    } else if (this.zoom > 1.01) {
      text(ctx, `${this.zoom.toFixed(1)}x`, r.x + r.w - 8, r.y + 12, {
        colour: PW.color.textMute,
        align: "right",
        font: PW.font.mono
      });
    } else if (this.crop) {
      text(ctx, "zoom for 1:1", r.x + r.w - 8, r.y + 12, {
        colour: PW.color.textMute,
        align: "right"
      });
    }
  }
  /**
   * Map the panel to a region of the image.
   *
   * At `zoom` 1 the whole image is visible — **contain**, not cover. Cover fit
   * crops, and a preview you cannot see all of is not much use for judging a
   * grade on a portrait frame in a wide panel.
   */
  view(r, tex = this.active().tex) {
    const iw = tex.width;
    const ih = tex.height;
    const fit = Math.min(r.w / iw, r.h / ih);
    const s = fit * this.zoom;
    const sx = r.w / (iw * s);
    const sy = r.h / (ih * s);
    return {
      uvScale: [sx, sy],
      uvOffset: [(1 - sx) / 2 - this.panX * sx, (1 - sy) / 2 - this.panY * sy]
    };
  }
  /** Keep at least part of the image on screen when panning. */
  clampPan(r) {
    const { uvScale } = this.view(r);
    const limitX = uvScale[0] >= 1 ? 0 : (1 - uvScale[0]) / (2 * uvScale[0]);
    const limitY = uvScale[1] >= 1 ? 0 : (1 - uvScale[1]) / (2 * uvScale[1]);
    this.panX = Math.min(limitX, Math.max(-limitX, this.panX));
    this.panY = Math.min(limitY, Math.max(-limitY, this.panY));
  }
  /** Back to showing the whole image. */
  resetView() {
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
  }
  /**
   * @returns true if the press was consumed.
   *
   * Plain drag pans — the hand tool, and the thing you reach for most. Shift
   * drags the wipe, which is a deliberate act rather than something you want
   * to trigger by accident while looking around a zoomed image.
   */
  onPointerDown(x, y, r, shift, doubleClick) {
    if (!this.hasImage) return false;
    if (doubleClick) {
      this.resetView();
      return true;
    }
    this.dragging = shift ? "wipe" : "pan";
    this.dragFrom = { x, y, panX: this.panX, panY: this.panY };
    if (this.dragging === "wipe") this.wipe = Math.min(1, Math.max(0, (x - r.x) / r.w));
    return true;
  }
  onPointerMove(x, y, r) {
    if (!this.dragging || !this.hasImage) return false;
    if (this.dragging === "wipe") {
      this.wipe = Math.min(1, Math.max(0, (x - r.x) / r.w));
      return true;
    }
    const { uvScale } = this.view(r);
    this.panX = this.dragFrom.panX + (x - this.dragFrom.x) / r.w * uvScale[0];
    this.panY = this.dragFrom.panY + (y - this.dragFrom.y) / r.h * uvScale[1];
    this.clampPan(r);
    return true;
  }
  onPointerUp() {
    const was = this.dragging !== null;
    this.dragging = null;
    return was;
  }
  /** Wheel zoom about the cursor, so the pixel under it stays put. */
  onWheel(x, y, r, delta) {
    if (!this.hasImage) return false;
    const before = this.view(r);
    const prev = this.zoom;
    this.zoom = Math.min(16, Math.max(1, this.zoom * (delta < 0 ? 1.15 : 1 / 1.15)));
    if (this.zoom === prev) return false;
    if (this.zoom <= 1.001) {
      this.resetView();
      return true;
    }
    const tx = (x - r.x) / r.w;
    const ty = (y - r.y) / r.h;
    const uvx = tx * before.uvScale[0] + before.uvOffset[0];
    const uvy = ty * before.uvScale[1] + before.uvOffset[1];
    const after = this.view(r);
    this.panX += (tx * after.uvScale[0] + after.uvOffset[0] - uvx) / after.uvScale[0];
    this.panY += (ty * after.uvScale[1] + after.uvOffset[1] - uvy) / after.uvScale[1];
    this.clampPan(r);
    return true;
  }
};
function hexToRgb(hex) {
  const v = hex.replace("#", "");
  return [
    parseInt(v.slice(0, 2), 16) / 255,
    parseInt(v.slice(2, 4), 16) / 255,
    parseInt(v.slice(4, 6), 16) / 255
  ];
}

// src/core/colour.ts
var SRGB_LINEAR_CUTOFF = 31308e-7;
var SRGB_ENCODED_CUTOFF = 0.04045;
function srgbToLinear(x) {
  const s = Math.sign(x);
  const a = Math.abs(x);
  return s * (a <= SRGB_ENCODED_CUTOFF ? a / 12.92 : Math.pow((a + 0.055) / 1.055, 2.4));
}
function linearToSrgb(x) {
  const s = Math.sign(x);
  const a = Math.abs(x);
  return s * (a <= SRGB_LINEAR_CUTOFF ? a * 12.92 : 1.055 * Math.pow(Math.max(a, 1e-12), 1 / 2.4) - 0.055);
}
function cbrt(x) {
  return Math.sign(x) * Math.pow(Math.abs(x), 1 / 3);
}
function linearToOklab(r, g, b) {
  const l = cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
  ];
}
function oklabToLinear(L, a, bb) {
  const l_ = L + 0.3963377774 * a + 0.2158037573 * bb;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * bb;
  const s_ = L - 0.0894841775 * a - 1.291485548 * bb;
  const l = l_ * l_ * l_;
  const m = m_ * m_ * m_;
  const s = s_ * s_ * s_;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s
  ];
}
function srgbToOklab(r, g, b) {
  return linearToOklab(srgbToLinear(r), srgbToLinear(g), srgbToLinear(b));
}
function oklabToSrgb(L, a, b) {
  const lin = oklabToLinear(L, a, b);
  return [linearToSrgb(lin[0]), linearToSrgb(lin[1]), linearToSrgb(lin[2])];
}
function oklabToOklch(L, a, b) {
  return [L, Math.sqrt(a * a + b * b), Math.atan2(b, a)];
}
function oklchToOklab(L, c, h) {
  return [L, c * Math.cos(h), c * Math.sin(h)];
}

// src/core/look_ops.ts
var HSL_BANDS = [
  ["red", 0.510228],
  ["orange", 0.924757],
  ["yellow", 1.915835],
  ["green", 2.487012],
  ["aqua", -2.883826],
  ["blue", -1.674608],
  ["purple", -1.153006],
  ["magenta", -0.552163]
];
var BAND_HALF = 0.36;
var TONE_CENTRES = [0, 0.33, 0.67, 1];
function smoothstep(e0, e1, x) {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}
function band(x, centre, half = BAND_HALF) {
  return smoothstep(centre - half, centre, x) * (1 - smoothstep(centre, centre + half, x));
}
function opTone(rgb, p) {
  const exposure = p.exposure ?? 0, contrast = p.contrast ?? 0;
  let out = [rgb[0], rgb[1], rgb[2]];
  if (exposure !== 0 || contrast !== 0) {
    const k = Math.pow(2, exposure);
    const f = (v) => {
      let lin = srgbToLinear(v);
      if (exposure !== 0) lin *= k;
      if (contrast !== 0) lin = Math.pow(Math.max(lin, 1e-6) / 0.18, 1 + contrast) * 0.18;
      return linearToSrgb(lin);
    };
    out = [f(out[0]), f(out[1]), f(out[2])];
  }
  const amounts = [p.blacks ?? 0, p.shadows ?? 0, p.highlights ?? 0, p.whites ?? 0];
  if (amounts.some((a) => a !== 0)) {
    const lab = srgbToOklab(out[0], out[1], out[2]);
    let delta = 0;
    for (let i = 0; i < 4; i++) {
      if (amounts[i] !== 0) delta += amounts[i] * 0.25 * band(lab[0], TONE_CENTRES[i]);
    }
    out = oklabToSrgb(lab[0] + delta, lab[1], lab[2]);
  }
  return out;
}
function opColour(rgb, p) {
  const warmth = p.warmth ?? 0, tint = p.tint ?? 0;
  const vibrance = p.vibrance ?? 0, saturation = p.saturation ?? 1;
  if (warmth === 0 && tint === 0 && vibrance === 0 && saturation === 1) return rgb;
  const lab = srgbToOklab(rgb[0], rgb[1], rgb[2]);
  const l = lab[0];
  let a = lab[1], b = lab[2];
  if (warmth !== 0) b += warmth * 0.1 * l;
  if (tint !== 0) a += tint * 0.1 * l;
  if (vibrance !== 0 || saturation !== 1) {
    const c = Math.sqrt(a * a + b * b);
    let scale = saturation;
    if (vibrance !== 0) {
      const headroom = 1 - Math.min(1, Math.max(0, c / 0.25));
      scale *= 1 + vibrance * headroom;
    }
    if (c > 1e-9) {
      a *= scale;
      b *= scale;
    }
  }
  return oklabToSrgb(l, a, b);
}
function hueDistance(h, centre) {
  const d = h - centre;
  return d - 2 * Math.PI * Math.round(d / (2 * Math.PI));
}
function opHsl(rgb, p) {
  const bands = p.bands ?? {};
  const active = Object.entries(bands).filter(
    ([, v]) => v && ((v.hue ?? 0) !== 0 || (v.sat ?? 0) !== 0 || (v.lum ?? 0) !== 0)
  );
  if (active.length === 0) return rgb;
  const lab = srgbToOklab(rgb[0], rgb[1], rgb[2]);
  const l = lab[0], a = lab[1], b = lab[2];
  const c = Math.sqrt(a * a + b * b);
  const h = Math.atan2(b, a);
  const half = Math.PI / HSL_BANDS.length;
  const chromaGate = Math.min(1, Math.max(0, c / 0.04));
  let dHue = 0, satScale = 1, dLum = 0;
  for (const [name, centre] of HSL_BANDS) {
    const bandv = bands[name];
    if (!bandv) continue;
    const dist = Math.abs(hueDistance(h, centre));
    const w = (1 - smoothstep(0, half * 1.6, dist)) * chromaGate;
    if (bandv.hue) dHue += w * bandv.hue * (Math.PI / 12);
    if (bandv.sat) satScale *= 1 + w * bandv.sat;
    if (bandv.lum) dLum += w * bandv.lum * 0.15;
  }
  const h2 = h + dHue;
  const c2 = Math.max(0, c * satScale);
  return oklabToSrgb(l + dLum, c2 * Math.cos(h2), c2 * Math.sin(h2));
}
function opGradientMap(rgb, p) {
  const stops = p.stops ?? [];
  const amount = p.amount ?? 0;
  if (amount <= 0 || stops.length < 2) return rgb;
  const lab = srgbToOklab(rgb[0], rgb[1], rgb[2]);
  const l = Math.min(1, Math.max(0, lab[0]));
  let i = 0;
  while (i < stops.length - 2 && l >= stops[i + 1][0]) i++;
  const [p0, c0] = stops[i];
  const [p1, c1] = stops[i + 1];
  const t = Math.min(1, Math.max(0, (l - p0) / Math.max(p1 - p0, 1e-9)));
  const mapped = [
    c0[0] + (c1[0] - c0[0]) * t,
    c0[1] + (c1[1] - c0[1]) * t,
    c0[2] + (c1[2] - c0[2]) * t
  ];
  const mode = p.blend ?? "normal";
  let blended;
  if (mode === "normal") blended = mapped;
  else if (mode === "multiply") blended = [rgb[0] * mapped[0], rgb[1] * mapped[1], rgb[2] * mapped[2]];
  else if (mode === "screen") blended = [0, 1, 2].map((i2) => 1 - (1 - rgb[i2]) * (1 - mapped[i2]));
  else if (mode === "overlay")
    blended = [0, 1, 2].map((i2) => rgb[i2] <= 0.5 ? 2 * rgb[i2] * mapped[i2] : 1 - 2 * (1 - rgb[i2]) * (1 - mapped[i2]));
  else if (mode === "soft light")
    blended = [0, 1, 2].map((i2) => {
      const base = rgb[i2], layer = mapped[i2];
      const d = base <= 0.25 ? ((16 * base - 12) * base + 4) * base : Math.sqrt(Math.max(base, 0));
      return layer <= 0.5 ? base - (1 - 2 * layer) * base * (1 - base) : base + (2 * layer - 1) * (d - base);
    });
  else if (mode === "colour") {
    const rl = srgbToOklab(mapped[0], mapped[1], mapped[2]);
    blended = oklabToSrgb(lab[0], rl[1], rl[2]);
  } else throw new Error(`unknown gradient map blend ${mode}`);
  return [
    rgb[0] + (blended[0] - rgb[0]) * amount,
    rgb[1] + (blended[1] - rgb[1]) * amount,
    rgb[2] + (blended[2] - rgb[2]) * amount
  ];
}

// src/core/ops.ts
function opExposure(rgb, stops) {
  const k = Math.pow(2, stops);
  return [
    linearToSrgb(srgbToLinear(rgb[0]) * k),
    linearToSrgb(srgbToLinear(rgb[1]) * k),
    linearToSrgb(srgbToLinear(rgb[2]) * k)
  ];
}
function opContrast(rgb, amount, pivot = 0.18) {
  const k = 1 + amount;
  const f = (v) => linearToSrgb(Math.pow(Math.max(srgbToLinear(v), 1e-6) / pivot, k) * pivot);
  return [f(rgb[0]), f(rgb[1]), f(rgb[2])];
}
function opSaturation(rgb, amount) {
  const lab = srgbToOklab(rgb[0], rgb[1], rgb[2]);
  const lch = oklabToOklch(lab[0], lab[1], lab[2]);
  const back = oklchToOklab(lch[0], lch[1] * amount, lch[2]);
  return oklabToSrgb(back[0], back[1], back[2]);
}
function opWarmth(rgb, amount) {
  const lab = srgbToOklab(rgb[0], rgb[1], rgb[2]);
  return oklabToSrgb(lab[0], lab[1], lab[2] + amount * 0.1 * lab[0]);
}
var CurvesOp = class {
  chan;
  luma;
  preserveHue;
  constructor(params) {
    const prep = (p) => p && p.length >= 2 && !isIdentity(p) ? new Curve(p) : null;
    this.chan = [prep(params.r), prep(params.g), prep(params.b)];
    this.luma = prep(params.luma);
    this.preserveHue = params.preserve_hue !== false;
  }
  apply(rgb) {
    let out = [rgb[0], rgb[1], rgb[2]];
    for (let i = 0; i < 3; i++) {
      const c = this.chan[i];
      if (c) out[i] = c.at(out[i]);
    }
    if (this.luma) {
      if (this.preserveHue) {
        const lab = srgbToOklab(out[0], out[1], out[2]);
        out = oklabToSrgb(this.luma.at(lab[0]), lab[1], lab[2]);
      } else {
        out = [this.luma.at(out[0]), this.luma.at(out[1]), this.luma.at(out[2])];
      }
    }
    return out;
  }
};
function buildSampleFn(ops) {
  const stages = [];
  for (const op of ops) {
    if (op.enabled === false) continue;
    const p = op.params ?? {};
    let fn = null;
    switch (op.type) {
      case "exposure":
        fn = (rgb) => opExposure(rgb, p.stops ?? 0);
        break;
      case "contrast":
        fn = (rgb) => opContrast(rgb, p.amount ?? 0, p.pivot ?? 0.18);
        break;
      case "saturation":
        fn = (rgb) => opSaturation(rgb, p.amount ?? 1);
        break;
      case "warmth":
        fn = (rgb) => opWarmth(rgb, p.amount ?? 0);
        break;
      case "curves": {
        const c = new CurvesOp(p);
        fn = (rgb) => c.apply(rgb);
        break;
      }
      case "tone":
        fn = (rgb) => opTone(rgb, p);
        break;
      case "colour":
        fn = (rgb) => opColour(rgb, p);
        break;
      case "hsl":
        fn = (rgb) => opHsl(rgb, p);
        break;
      case "gradient_map":
        fn = (rgb) => opGradientMap(rgb, p);
        break;
      default:
        continue;
    }
    const s = op.strength ?? 1;
    stages.push(
      s >= 1 ? fn : (rgb) => {
        const o = fn(rgb);
        return [
          rgb[0] + (o[0] - rgb[0]) * s,
          rgb[1] + (o[1] - rgb[1]) * s,
          rgb[2] + (o[2] - rgb[2]) * s
        ];
      }
    );
  }
  return (rgb) => {
    let out = rgb;
    for (const st of stages) out = st(out);
    return out;
  };
}

// src/widgets/compare.ts
var listeners = /* @__PURE__ */ new Set();
var held = false;
var installed = false;
function set(next) {
  if (next === held) return;
  held = next;
  for (const fn of listeners) fn(held);
}
function install() {
  if (installed) return;
  installed = true;
  window.addEventListener("keydown", (e) => {
    if (e.key === "Alt") set(true);
  });
  window.addEventListener("keyup", (e) => {
    if (e.key === "Alt") set(false);
  });
  window.addEventListener("blur", () => set(false));
}
function isComparing() {
  return held;
}
function onCompareChange(fn) {
  install();
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// src/widgets/run_events.ts
var listeners2 = /* @__PURE__ */ new Set();
var installed2 = false;
var pending = null;
function fire() {
  if (pending) clearTimeout(pending);
  pending = setTimeout(() => {
    pending = null;
    for (const fn of listeners2) {
      try {
        fn();
      } catch (err) {
        console.warn("[PW Color] run listener failed", err);
      }
    }
  }, 80);
}
function install2() {
  if (installed2) return;
  installed2 = true;
  for (const name of ["execution_success", "execution_error", "executed"]) {
    api.addEventListener(name, fire);
  }
}
function onRunComplete(fn) {
  install2();
  listeners2.add(fn);
  return () => listeners2.delete(fn);
}

// src/widgets/reset.ts
var PASS_THROUGH = {
  // Vignette and aberration already default to zero; only halation is on.
  PW_Optics: { halation: 0 },
  PW_Grain: { amount: 0, dither: 0 },
  PW_MatchSource: { strength: 0 },
  // A gate's pass-through state is not gating, so reset switches auto_pass on
  // rather than leaving a node that stops every run it is asked to reset.
  PW_Review: { auto_pass: true }
};
function defaultFor(node, name) {
  const defs = node.constructor?.nodeData?.input ?? {};
  for (const section of ["required", "optional"]) {
    const entry = defs[section]?.[name];
    if (!entry) continue;
    const spec = entry[1];
    if (spec && typeof spec === "object" && "default" in spec) return spec.default;
    if (Array.isArray(entry[0]) && entry[0].length) return entry[0][0];
  }
  return void 0;
}
function resetNode(node, opts = {}) {
  const keep = new Set(opts.keep ?? ["seed", "control_after_generate"]);
  const neutral = PASS_THROUGH[String(node.type)] ?? {};
  for (const w of node.widgets ?? []) {
    if (keep.has(w.name)) continue;
    const value = w.name in neutral ? neutral[w.name] : defaultFor(node, w.name);
    if (value === void 0) continue;
    if (w.value !== value) {
      w.value = value;
      w.callback?.(w.value);
    }
  }
  opts.after?.();
  node.setDirtyCanvas?.(true, true);
}
function addResetMenu(nodeType, opts = () => ({})) {
  const prior = nodeType.prototype.getExtraMenuOptions;
  nodeType.prototype.getExtraMenuOptions = function(canvas, options) {
    const result = prior?.apply(this, arguments);
    options.push(null, {
      content: "Reset \u2014 pass image through unchanged",
      callback: () => resetNode(this, opts(this))
    });
    return result;
  };
}

// src/widgets/segmented.ts
var Segmented = class {
  segments;
  selected;
  constructor(segments, selected) {
    if (segments.length === 0) throw new Error("Segmented needs at least one segment");
    this.segments = segments;
    this.selected = selected ?? segments[0].id;
  }
  cellRects(r) {
    const gap = 4;
    const w = (r.w - gap * (this.segments.length - 1)) / this.segments.length;
    return this.segments.map((_, i) => ({ x: r.x + i * (w + gap), y: r.y, w, h: r.h }));
  }
  draw(ctx, r) {
    const cells = this.cellRects(r);
    this.segments.forEach((seg, i) => {
      const active = seg.id === this.selected;
      const cell = cells[i];
      fillPanel(
        ctx,
        cell,
        active ? PW.color.chipActive : PW.color.chip,
        PW.metrics.radiusControl,
        active ? PW.color.border : PW.color.borderSoft
      );
      text(ctx, seg.label, cell.x + cell.w / 2, cell.y + cell.h / 2, {
        colour: active ? seg.colour ?? PW.color.text : PW.color.textMute,
        align: "center"
      });
      if (active && seg.colour) {
        ctx.fillStyle = seg.colour;
        ctx.fillRect(cell.x + 6, cell.y + cell.h - 3, cell.w - 12, 2);
      }
    });
  }
  /** @returns the id that was clicked, or null. */
  onPointerDown(x, y, r) {
    const cells = this.cellRects(r);
    for (let i = 0; i < cells.length; i++) {
      const c = cells[i];
      if (x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h) {
        this.selected = this.segments[i].id;
        return this.selected;
      }
    }
    return null;
  }
};

// src/widgets/layout.ts
var INTERNAL_WIDGET = /^\$\$/;
function collapseInternalPreview(node) {
  for (const w of node.widgets ?? []) {
    if (!INTERNAL_WIDGET.test(w.name) || w.__pwCollapsed) continue;
    w.__pwCollapsed = true;
    w.computeSize = () => [0, -4];
    w.computedHeight = 0;
    w.hidden = true;
  }
}
function widgetHeight(node) {
  const widgets = (node.widgets ?? []).filter(
    (w) => w.type !== "hidden" && !INTERNAL_WIDGET.test(w.name) && !w.hidden
  );
  let bottom = 0;
  for (const w of widgets) {
    const y = w.last_y;
    const h = w.computedHeight ?? w.height ?? PW.metrics.controlHeight;
    if (Number.isFinite(y)) bottom = Math.max(bottom, y + h);
  }
  if (bottom > 0) return bottom;
  const compute = node.computeSize;
  if (typeof compute === "function") {
    const size = compute.call(node);
    if (Array.isArray(size) && Number.isFinite(size[1])) {
      let internal = 0;
      for (const w of node.widgets ?? []) {
        if (INTERNAL_WIDGET.test(w.name)) internal += w.computedHeight ?? 0;
      }
      return Math.max(40, size[1] - internal);
    }
  }
  return 40 + widgets.length * (PW.metrics.controlHeight + 4);
}
function fitPanel(node, panelHeight3, minWidth) {
  node.size[0] = Math.max(node.size[0], minWidth);
  node.size[1] = Math.max(node.size[1], widgetHeight(node) + panelHeight3);
}

// src/widgets/panel.ts
function coalesced(schedule, fn) {
  let pending2 = false;
  return () => {
    if (pending2) return;
    pending2 = true;
    schedule(() => {
      pending2 = false;
      fn();
    });
  };
}
var Panel = class {
  spec;
  /**
   * Graph zoom. The element's CSS box is scaled by the host's transform, so
   * client coordinates come in scaled; dividing puts them back in node units,
   * which is what every layout function in the pack works in.
   */
  scale = 1;
  /**
   * Height this panel has added to its node beyond what the node had, and
   * may therefore take back when the panel turns out taller than it asked
   * for. A user's own resize never counts, so it is never removed.
   */
  grown = 0;
  canvas;
  env;
  ctx;
  w = 0;
  h = 0;
  /** Backing pixels per node unit, fixed at the last resize. */
  dpr = 1;
  pending = false;
  disposed = false;
  constructor(canvas, spec, env) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("PW Color panel: no 2D context");
    this.canvas = canvas;
    this.spec = spec;
    this.env = env;
    this.ctx = ctx;
  }
  /** Content width in node units. */
  get width() {
    return this.w;
  }
  get height() {
    return this.h;
  }
  /** The live context, so hit tests can measure text exactly as drawing did. */
  get context() {
    return this.ctx;
  }
  /** Backing pixels per node unit in use. */
  get density() {
    return this.dpr;
  }
  /** Set the drawing size in node units and repaint now. */
  resize(width, height) {
    this.w = Math.max(0, Math.floor(width));
    this.h = Math.max(0, Math.floor(height));
    this.dpr = this.env.dpr();
    this.canvas.width = Math.round(this.w * this.dpr);
    this.canvas.height = Math.round(this.h * this.dpr);
    this.draw();
  }
  draw() {
    if (this.disposed || this.w === 0 || this.h === 0) return;
    const dpr = this.dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.ctx.clearRect(0, 0, this.w, this.h);
    this.spec.draw(this.ctx, { x: 0, y: 0, w: this.w, h: this.h });
  }
  /** Repaint before the next frame. A burst of calls costs one draw. */
  invalidate() {
    if (this.pending || this.disposed) return;
    this.pending = true;
    this.env.schedule(() => {
      this.pending = false;
      this.draw();
    });
  }
  dispose() {
    this.disposed = true;
  }
  local(cx, cy) {
    const r = this.canvas.getBoundingClientRect();
    return [(cx - r.left) / this.scale, (cy - r.top) / this.scale];
  }
  route(handled) {
    if (handled) this.invalidate();
    return handled;
  }
  pointerDown(cx, cy, m) {
    const [x, y] = this.local(cx, cy);
    return this.route(this.spec.onPointerDown?.(x, y, m) ?? false);
  }
  pointerMove(cx, cy, m) {
    const [x, y] = this.local(cx, cy);
    return this.route(this.spec.onPointerMove?.(x, y, m) ?? false);
  }
  pointerUp(cx, cy, m) {
    const [x, y] = this.local(cx, cy);
    return this.route(this.spec.onPointerUp?.(x, y, m) ?? false);
  }
  wheel(cx, cy, delta) {
    const [x, y] = this.local(cx, cy);
    return this.route(this.spec.onWheel?.(x, y, delta) ?? false);
  }
};
var WIDGET_NAME = "pw_panel";
var panels = /* @__PURE__ */ new WeakMap();
function panelOf(node) {
  return panels.get(node);
}
function contentWidth(nodeWidth, minWidth) {
  return Math.max(nodeWidth, minWidth) - 2 * PW.metrics.padding;
}
function fitNode(node, panel) {
  const wanted = panel.spec.height(contentWidth(node.size[0], panel.spec.minWidth));
  const before = node.size[1];
  fitPanel(node, wanted, panel.spec.minWidth);
  panel.grown += node.size[1] - before;
  node.setSize?.([node.size[0], node.size[1]]);
  node.setDirtyCanvas?.(true, true);
}
function resizeNodeBy(node, by) {
  const target = node.size[1] + by;
  node.size[1] = target;
  node.setSize?.([node.size[0], target]);
  node.setDirtyCanvas?.(true, true);
}
function elementScale(el) {
  const w = el.offsetWidth;
  return w > 0 ? el.getBoundingClientRect().width / w : 1;
}
function attachPanel(node, spec) {
  const box = document.createElement("div");
  box.dataset.captureWheel = "true";
  box.style.cssText = "display:flex;flex-direction:column;width:100%;height:100%;";
  const canvas = document.createElement("canvas");
  canvas.tabIndex = -1;
  canvas.style.cssText = "flex:1 1 auto;display:block;width:100%;min-height:0;outline:none;touch-action:none;";
  box.appendChild(canvas);
  const density = () => Math.min(3, Math.max(1, (globalThis.devicePixelRatio || 1) * elementScale(canvas)));
  const panel = new Panel(canvas, spec, {
    dpr: density,
    schedule: (fn) => requestAnimationFrame(fn)
  });
  panels.set(node, panel);
  const mods = (e) => ({
    shift: e.shiftKey,
    double: e.detail === 2,
    time: e.timeStamp,
    event: e
  });
  canvas.addEventListener("pointerenter", () => canvas.focus({ preventScroll: true }));
  canvas.addEventListener("pointerdown", (e) => {
    canvas.focus({ preventScroll: true });
    panel.scale = elementScale(canvas);
    if (panel.pointerDown(e.clientX, e.clientY, mods(e))) canvas.setPointerCapture?.(e.pointerId);
    e.stopPropagation();
  });
  canvas.addEventListener("pointermove", (e) => {
    panel.scale = elementScale(canvas);
    if (panel.pointerMove(e.clientX, e.clientY, mods(e))) e.stopPropagation();
  });
  canvas.addEventListener("pointerup", (e) => {
    panel.pointerUp(e.clientX, e.clientY, mods(e));
    if (canvas.hasPointerCapture?.(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
  });
  canvas.addEventListener(
    "wheel",
    (e) => {
      panel.scale = elementScale(canvas);
      if (panel.wheel(e.clientX, e.clientY, e.deltaY)) {
        e.preventDefault();
        e.stopPropagation();
      }
    },
    { passive: false }
  );
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  const widget = node.addDOMWidget(WIDGET_NAME, "pwpanel", box, {
    serialize: false,
    hideOnZoom: false
  });
  widget.serialize = false;
  const widgetChanged = coalesced(
    (fn) => requestAnimationFrame(fn),
    () => {
      spec.onWidgetChange?.();
      panel.invalidate();
    }
  );
  for (const w of node.widgets ?? []) {
    if (w.name === WIDGET_NAME) continue;
    const prior = w.callback;
    w.callback = function(...args) {
      const result = prior?.apply(this, args);
      widgetChanged();
      return result;
    };
  }
  const priorWidgetChanged = node.onWidgetChanged;
  node.onWidgetChanged = function(...args) {
    const result = priorWidgetChanged?.apply(this, args);
    widgetChanged();
    return result;
  };
  collapseInternalPreview(node);
  fitNode(node, panel);
  const measure = () => {
    const w = canvas.offsetWidth;
    const h = canvas.offsetHeight;
    if (w <= 0) return;
    panel.scale = elementScale(canvas);
    const stale = w !== panel.width || h !== panel.height || Math.abs(density() - panel.density) > 0.01;
    if (h > 0 && stale) panel.resize(w, h);
    const deficit = spec.height(w) - h;
    if (deficit > 0.5) {
      panel.grown += deficit;
      requestAnimationFrame(() => resizeNodeBy(node, deficit));
    } else if (deficit < -0.5 && panel.grown > 0) {
      const back = Math.min(-deficit, panel.grown);
      panel.grown -= back;
      requestAnimationFrame(() => resizeNodeBy(node, -back));
    }
  };
  const observer = new ResizeObserver(measure);
  observer.observe(canvas);
  setTimeout(measure, 0);
  canvas.addEventListener("pointerenter", measure);
  const priorRemoved = node.onRemoved;
  node.onRemoved = function() {
    observer.disconnect();
    panel.dispose();
    panels.delete(node);
    priorRemoved?.call(this);
  };
  return panel;
}
function hideSerialisationWidget(node, name) {
  const w = node.widgets?.find((x) => x.name === name);
  if (!w) return;
  w.type = "hidden";
  w.computeSize = () => [0, -4];
  w.computeLayoutSize = void 0;
  const stub = document.createElement("div");
  stub.style.cssText = "display:none;height:0;";
  w.element = stub;
  w.options ??= {};
  w.options.hidden = true;
}

// src/nodes/curves.ts
var M = PW.metrics;
var HEADER_H = 18;
var TABS_H = M.controlHeight;
var MIN_EDITOR_H = 160;
var PREVIEW_H = 140;
var MIN_WIDTH = 360;
var PANEL_MIN_H = HEADER_H + 4 + PREVIEW_H + M.gapControl + TABS_H + M.gapControl + MIN_EDITOR_H + M.padding;
var CHANNEL_TABS = [
  { id: "luma", label: "Luma", colour: PW.channel.luma },
  { id: "r", label: "R", colour: PW.channel.r },
  { id: "g", label: "G", colour: PW.channel.g },
  { id: "b", label: "B", colour: PW.channel.b }
];
var uis = /* @__PURE__ */ new WeakMap();
function layout(w, h) {
  let y = 0;
  const header = { x: 0, y, w, h: HEADER_H };
  y += HEADER_H + 4;
  const preview = { x: 0, y, w, h: PREVIEW_H };
  y += PREVIEW_H + M.gapControl;
  const tabs = { x: 0, y, w, h: TABS_H };
  y += TABS_H + M.gapControl;
  const editor = { x: 0, y, w, h: Math.max(MIN_EDITOR_H, h - y - M.padding) };
  return { header, preview, tabs, editor };
}
function readState(node) {
  const w = getWidget(node, "curves");
  try {
    const raw = JSON.parse(String(w?.value ?? ""));
    const s = identityState();
    for (const k of ["luma", "r", "g", "b"]) {
      if (Array.isArray(raw?.[k]) && raw[k].length >= 2) s[k] = raw[k].map((p) => [p[0], p[1]]);
    }
    return s;
  } catch {
    return identityState();
  }
}
function writeState(node, ui) {
  const w = getWidget(node, "curves");
  if (!w) return;
  const s = ui.editor.state;
  w.value = JSON.stringify({ luma: s.luma, r: s.r, g: s.g, b: s.b });
  panelOf(node)?.invalidate();
}
async function loadHistogram(node, ui) {
  try {
    const res = await fetchPw(`/pw_color/histogram/${node.id}`);
    if (!res.ok) return;
    const data = await res.json();
    const h = data.histogram;
    ui.editor.histogram = {
      luma: Float32Array.from(h.luma),
      r: Float32Array.from(h.r),
      g: Float32Array.from(h.g),
      b: Float32Array.from(h.b)
    };
    panelOf(node)?.invalidate();
  } catch {
  }
}
function makeUI(node) {
  const editor = new CurveEditor();
  const tabs = new Segmented(CHANNEL_TABS);
  const preview = new Preview();
  const rebake = (n) => {
    const op = {
      type: "curves",
      params: {
        ...editor.state,
        preserve_hue: getWidget(n, "preserve_hue")?.value !== false
      },
      strength: typeof getWidget(n, "strength")?.value === "number" ? getWidget(n, "strength").value : 1
    };
    preview.lattice = Lattice.fromFn(buildSampleFn([op]), DEFAULT_SIZE);
    preview.digest = JSON.stringify([op.params, op.strength]);
  };
  const ui = { editor, tabs, preview, rebake };
  editor.state = readState(node);
  editor.onChange = () => {
    writeState(node, ui);
    rebake(node);
  };
  rebake(node);
  return ui;
}
function registerCurves() {
  app.registerExtension({
    name: "pw.color.curves",
    async beforeRegisterNodeDef(nodeType, nodeData) {
      if (nodeData?.name !== "PW_Curves") return;
      addResetMenu(nodeType, (node) => ({
        after: () => {
          const ui = uis.get(node);
          if (!ui) return;
          ui.editor.resetAll();
          ui.rebake(node);
          panelOf(node)?.invalidate();
        }
      }));
      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function() {
        const r = onCreated?.apply(this, arguments);
        const ui = makeUI(this);
        uis.set(this, ui);
        hideSerialisationWidget(this, "curves");
        const reset = () => resetNode(this, {
          after: () => {
            ui.editor.resetAll();
            ui.rebake(this);
          }
        });
        const panel = attachPanel(this, {
          minWidth: MIN_WIDTH,
          height: () => PANEL_MIN_H,
          draw: (ctx, rr) => {
            const L = layout(rr.w, rr.h);
            sectionHeader(ctx, "Curves", L.header, BADGE.lut);
            headerChip(ctx, L.header, "reset", BADGE.lut.label);
            ui.preview.comparing = isComparing();
            ui.preview.draw(ctx, L.preview);
            ui.tabs.draw(ctx, L.tabs);
            ui.editor.draw(ctx, L.editor);
          },
          onPointerDown: (x, y, m) => {
            const L = layout(panel.width, panel.height);
            if (hit(headerChip(panel.context, L.header, "reset", BADGE.lut.label), x, y, 3)) {
              reset();
              return true;
            }
            const tab = ui.tabs.onPointerDown(x, y, L.tabs);
            if (tab) {
              ui.editor.channel = tab;
              return true;
            }
            if (hit(L.preview, x, y)) {
              ui.preview.onPointerDown(x, y, L.preview, m.shift, m.double);
              return true;
            }
            if (hit(L.editor, x, y)) {
              ui.editor.onPointerDown(x, y, L.editor, m.shift, m.time);
              return true;
            }
            return false;
          },
          onPointerMove: (x, y, m) => {
            const L = layout(panel.width, panel.height);
            if (ui.preview.onPointerMove(x, y, L.preview)) return true;
            return ui.editor.onPointerMove(x, y, L.editor, m.shift);
          },
          onPointerUp: () => {
            const editor = ui.editor.onPointerUp();
            const preview = ui.preview.onPointerUp();
            return editor || preview;
          },
          onWheel: (x, y, delta) => {
            const L = layout(panel.width, panel.height);
            return hit(L.preview, x, y) && ui.preview.onWheel(x, y, L.preview, delta);
          },
          // `strength` and `preserve_hue` are part of the grade the preview
          // shows, so the lattice is rebaked whenever a widget moves.
          onWidgetChange: () => ui.rebake(this)
        });
        const repaint2 = () => panel.invalidate();
        const refresh = () => {
          void ui.preview.load(this.id, repaint2);
          void loadHistogram(this, ui);
        };
        setTimeout(refresh, 0);
        const stopCompare = onCompareChange(repaint2);
        const stopRun = onRunComplete(refresh);
        const priorRemoved = this.onRemoved;
        this.onRemoved = function() {
          stopCompare();
          stopRun();
          priorRemoved?.call(this);
        };
        return r;
      };
      const onConfigure = nodeType.prototype.onConfigure;
      nodeType.prototype.onConfigure = function(info) {
        const r = onConfigure?.apply(this, arguments);
        const ui = uis.get(this);
        const panel = panelOf(this);
        if (ui && panel) {
          fitNode(this, panel);
          ui.editor.state = readState(this);
          ui.rebake(this);
          void loadHistogram(this, ui);
          void ui.preview.load(this.id, () => panel.invalidate());
          panel.invalidate();
        }
        return r;
      };
    }
  });
}

// src/core/tonal.ts
var EDGE_FALLOFF = 0.04;
function smoothstep2(e0, e1, x) {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}
function tonalWeight(t, shadows, midtones, highlights) {
  const shadow = 1 - smoothstep2(0, 0.5, t);
  const highlight = smoothstep2(0.5, 1, t);
  const mid = Math.max(0, 1 - shadow - highlight);
  const w = shadow * shadows + mid * midtones + highlight * highlights;
  return w * smoothstep2(0, EDGE_FALLOFF, t) * smoothstep2(0, EDGE_FALLOFF, 1 - t);
}

// src/nodes/spatial_preview.ts
var M2 = PW.metrics;
var HEADER_H2 = 18;
function attachSpatialPreview(nodeType, opts) {
  const onCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function() {
    const r = onCreated?.apply(this, arguments);
    const preview = new Preview();
    const extraH = () => opts.extra?.(this) ?? 0;
    const headerRect = (w) => ({ x: 0, y: extraH(), w, h: HEADER_H2 });
    const previewRect = (w) => ({ x: 0, y: extraH() + HEADER_H2 + 6, w, h: opts.height });
    const panel = attachPanel(this, {
      minWidth: opts.minWidth,
      height: () => extraH() + HEADER_H2 + 6 + opts.height + M2.padding,
      draw: (ctx, rr) => {
        opts.drawExtra?.(ctx, this, 0, rr.w);
        const hr = headerRect(rr.w);
        sectionHeader(ctx, opts.label ?? "Result", hr, BADGE.render);
        headerChip(ctx, hr, "reset", BADGE.render.label);
        preview.comparing = isComparing();
        preview.draw(ctx, previewRect(rr.w));
      },
      onPointerDown: (x, y, m) => {
        const hr = headerRect(panel.width);
        if (hit(headerChip(panel.context, hr, "reset", BADGE.render.label), x, y, 3)) {
          resetNode(this);
          return true;
        }
        const pr = previewRect(panel.width);
        if (!hit(pr, x, y)) return false;
        preview.onPointerDown(x, y, pr, m.shift, m.double);
        return true;
      },
      onPointerMove: (x, y) => preview.onPointerMove(x, y, previewRect(panel.width)),
      onPointerUp: () => preview.onPointerUp(),
      onWheel: (x, y, delta) => {
        const pr = previewRect(panel.width);
        return hit(pr, x, y) && preview.onWheel(x, y, pr, delta);
      }
    });
    const repaint2 = () => panel.invalidate();
    const refresh = () => {
      void preview.load(this.id, repaint2);
      void preview.loadOutput(this.id, repaint2);
    };
    setTimeout(refresh, 0);
    const stopCompare = onCompareChange(repaint2);
    const stopRun = onRunComplete(refresh);
    const priorRemoved = this.onRemoved;
    this.onRemoved = function() {
      stopCompare();
      stopRun();
      priorRemoved?.call(this);
    };
    return r;
  };
}

// src/nodes/grain.ts
var M3 = PW.metrics;
var PANEL_H = 96;
function num(node, name, fallback) {
  const v = getWidget(node, name)?.value;
  return typeof v === "number" ? v : fallback;
}
function drawResponse(ctx, r, node) {
  fillPanel(ctx, r, PW.color.well, M3.radiusPanel, PW.color.border);
  const s = num(node, "shadows", 0.2);
  const m = num(node, "midtones", 1);
  const h = num(node, "highlights", 0.1);
  const peak = Math.max(1e-6, s, m, h);
  const strip = 8;
  for (let px = 0; px < r.w; px++) {
    const v = Math.round(px / Math.max(1, r.w - 1) * 255);
    ctx.fillStyle = `rgb(${v},${v},${v})`;
    ctx.fillRect(r.x + px, r.y + r.h - strip, 1, strip);
  }
  for (let i = 1; i < 4; i++) {
    const x = r.x + i / 4 * r.w;
    hairline(ctx, x, r.y, x, r.y + r.h - strip, PW.color.grid);
  }
  const plotH = r.h - strip - 6;
  ctx.beginPath();
  ctx.moveTo(r.x, r.y + plotH);
  const steps = Math.max(48, Math.ceil(r.w));
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const w = tonalWeight(t, s, m, h) / peak;
    ctx.lineTo(r.x + t * r.w, r.y + plotH - w * (plotH - 6));
  }
  ctx.lineTo(r.x + r.w, r.y + plotH);
  ctx.closePath();
  ctx.fillStyle = PW.color.surface;
  ctx.fill();
  ctx.beginPath();
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const w = tonalWeight(t, s, m, h) / peak;
    const x = r.x + t * r.w;
    const y = r.y + plotH - w * (plotH - 6);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.strokeStyle = PW.color.warm;
  ctx.lineWidth = 2;
  ctx.stroke();
  text(ctx, "shadows", r.x + 4, r.y + 10, { colour: PW.color.textMute });
  text(ctx, "highlights", r.x + r.w - 4, r.y + 10, { colour: PW.color.textMute, align: "right" });
}
function registerGrain() {
  app.registerExtension({
    name: "pw.color.grain",
    async beforeRegisterNodeDef(nodeType, nodeData) {
      if (nodeData?.name !== "PW_Grain") return;
      attachSpatialPreview(nodeType, {
        height: 190,
        minWidth: 340,
        label: "Result",
        extra: () => PANEL_H + 18 + M3.gapSection,
        drawExtra: (ctx, node, top, w) => {
          sectionHeader(ctx, "Tonal response", { x: 0, y: top, w, h: 18 }, BADGE.render);
          drawResponse(ctx, { x: 0, y: top + 20, w, h: PANEL_H - 20 }, node);
        }
      });
    }
  });
}

// src/nodes/look.ts
var M4 = PW.metrics;
var THUMB_H = 74;
var THUMB_W = 96;
var HSL_ROW_H = 22;
var HEADER_H3 = 18;
var PREVIEW_H2 = 150;
var MIN_WIDTH2 = 420;
var HSL_SWATCH_W = 46;
var HSL_TRACK_X = HSL_SWATCH_W + 62;
var HSL_TRACK_PAD = HSL_TRACK_X + 40;
var uis2 = /* @__PURE__ */ new WeakMap();
function repaint(node) {
  panelOf(node)?.invalidate();
}
function refreshPreview(node) {
  const ui = uis2.get(node);
  if (!ui) return;
  const num2 = (name, d) => {
    const v = getWidget(node, name)?.value;
    return typeof v === "number" ? v : d;
  };
  let bands = {};
  try {
    bands = JSON.parse(String(getWidget(node, "hsl")?.value ?? "{}"));
  } catch {
  }
  const ops = [
    {
      type: "tone",
      params: {
        exposure: num2("exposure", 0),
        contrast: num2("contrast", 0),
        highlights: num2("highlights", 0),
        shadows: num2("shadows", 0),
        whites: num2("whites", 0),
        blacks: num2("blacks", 0)
      }
    },
    {
      type: "colour",
      params: {
        warmth: num2("warmth", 0),
        tint: num2("tint", 0),
        vibrance: num2("vibrance", 0),
        saturation: num2("saturation", 1)
      }
    },
    { type: "hsl", params: { bands } }
  ];
  ui.preview.lattice = Lattice.fromFn(buildSampleFn(ops), DEFAULT_SIZE);
  ui.preview.digest = JSON.stringify(ops);
  repaint(node);
}
var presets = null;
async function loadPresets() {
  if (presets) return presets;
  try {
    const res = await fetchPw("/pw_color/presets");
    if (!res.ok) return [];
    presets = (await res.json()).presets ?? [];
    return presets ?? [];
  } catch {
    return [];
  }
}
var PRESET_SLIDERS = [
  "exposure",
  "contrast",
  "highlights",
  "shadows",
  "whites",
  "blacks",
  "warmth",
  "tint",
  "vibrance",
  "saturation",
  "glow",
  "glow_radius",
  "glow_threshold",
  "gradient_map"
];
function applyPreset(node, preset) {
  const combo = getWidget(node, "preset");
  if (combo) {
    combo.value = preset.id;
    combo.callback?.(combo.value);
  }
  if (preset.id !== "none") {
    for (const name of PRESET_SLIDERS) {
      const w = getWidget(node, name);
      if (!w) continue;
      const key = name === "gradient_map" ? "gradient_map_amount" : name;
      const neutral = defaultFor(node, name);
      const next = typeof preset.params[key] === "number" ? preset.params[key] : neutral;
      if (typeof next !== "number") continue;
      if (w.value !== next) {
        w.value = next;
        w.callback?.(w.value);
      }
    }
    const blend = getWidget(node, "gradient_blend");
    if (blend && preset.params.gradient_map_blend) {
      blend.value = preset.params.gradient_map_blend;
      blend.callback?.(blend.value);
    }
    const hsl = getWidget(node, "hsl");
    if (hsl) {
      hsl.value = JSON.stringify(preset.params.hsl ?? {});
      hsl.callback?.(hsl.value);
    }
  }
  refreshPreview(node);
  node.setDirtyCanvas?.(true, true);
}
function presetOps(p) {
  const num2 = (k, d = 0) => typeof p[k] === "number" ? p[k] : d;
  return [
    {
      type: "tone",
      params: {
        exposure: num2("exposure"),
        contrast: num2("contrast"),
        highlights: num2("highlights"),
        shadows: num2("shadows"),
        whites: num2("whites"),
        blacks: num2("blacks")
      }
    },
    {
      type: "colour",
      params: {
        warmth: num2("warmth"),
        tint: num2("tint"),
        vibrance: num2("vibrance"),
        saturation: num2("saturation", 1)
      }
    },
    { type: "hsl", params: { bands: p.hsl ?? {} } },
    {
      type: "gradient_map",
      params: {
        amount: num2("gradient_map_amount"),
        blend: p.gradient_map_blend ?? "colour",
        stops: p.gradient_map_stops ?? []
      }
    }
  ];
}
function buildThumbnails(node, ui) {
  if (!ui.source) return;
  const { width, height, data } = ui.source;
  ui.thumbs.clear();
  for (const preset of ui.presets) {
    const cv = document.createElement("canvas");
    cv.width = width;
    cv.height = height;
    const ctx = cv.getContext("2d");
    const out = ctx.createImageData(width, height);
    if (preset.id === "none") {
      out.data.set(data);
    } else {
      const lat = Lattice.fromFn(buildSampleFn(presetOps(preset.params)), DEFAULT_SIZE);
      for (let i = 0; i < data.length; i += 4) {
        const c = lat.applyImage([data[i] / 255, data[i + 1] / 255, data[i + 2] / 255]);
        out.data[i] = c[0] * 255;
        out.data[i + 1] = c[1] * 255;
        out.data[i + 2] = c[2] * 255;
        out.data[i + 3] = 255;
      }
    }
    ctx.putImageData(out, 0, 0);
    ui.thumbs.set(preset.id, cv);
  }
  repaint(node);
}
async function loadSource(node, ui) {
  try {
    const res = await fetchPw(`/pw_color/input/${node.id}`);
    if (!res.ok) return;
    const blob = await res.blob();
    const bmp = await createImageBitmap(blob);
    const cv = document.createElement("canvas");
    cv.width = THUMB_W;
    cv.height = THUMB_H;
    const ctx = cv.getContext("2d");
    const scale = Math.max(THUMB_W / bmp.width, THUMB_H / bmp.height);
    const w = bmp.width * scale, h = bmp.height * scale;
    ctx.drawImage(bmp, (THUMB_W - w) / 2, (THUMB_H - h) / 2, w, h);
    bmp.close();
    ui.source = ctx.getImageData(0, 0, THUMB_W, THUMB_H);
    buildThumbnails(node, ui);
  } catch {
  }
}
function readHsl(node) {
  const out = {};
  for (const [name] of HSL_BANDS) out[name] = { hue: 0, sat: 0, lum: 0 };
  try {
    const raw = JSON.parse(String(getWidget(node, "hsl")?.value ?? "{}"));
    for (const [k, v] of Object.entries(raw)) {
      if (out[k] && v && typeof v === "object") Object.assign(out[k], v);
    }
  } catch {
  }
  return out;
}
function writeHsl(node, bands) {
  const w = getWidget(node, "hsl");
  if (!w) return;
  const trimmed = {};
  for (const [k, v] of Object.entries(bands)) {
    if (v.hue || v.sat || v.lum) trimmed[k] = v;
  }
  w.value = JSON.stringify(trimmed);
  repaint(node);
}
var HSL_AXES = ["hue", "sat", "lum"];
function drawHsl(ctx, r, node, ui) {
  const bands = readHsl(node);
  const axis = ui.hslTab.selected;
  const rowW = r.w;
  HSL_BANDS.forEach(([name, hue], i) => {
    const y = r.y + i * HSL_ROW_H;
    const c = 0.11;
    const rgbCss = oklchCss(0.62, c, hue);
    fillPanel(ctx, { x: r.x, y: y + 3, w: HSL_SWATCH_W, h: HSL_ROW_H - 7 }, rgbCss, M4.radiusControl);
    text(ctx, name, r.x + HSL_SWATCH_W + 8, y + HSL_ROW_H / 2, { colour: PW.color.textDim });
    const trackX = r.x + HSL_TRACK_X;
    const trackW = rowW - HSL_TRACK_PAD;
    const track = { x: trackX, y: y + HSL_ROW_H / 2 - 2, w: trackW, h: 4 };
    fillPanel(ctx, track, PW.color.well, 2);
    const v = bands[name][axis] ?? 0;
    const mid = trackX + trackW / 2;
    const px = mid + v / 1 * (trackW / 2);
    if (Math.abs(px - mid) > 0.5) {
      fillPanel(ctx, { x: Math.min(mid, px), y: track.y, w: Math.abs(px - mid), h: 4 }, PW.color.accent, 2);
    }
    ctx.beginPath();
    ctx.arc(px, track.y + 2, 4, 0, Math.PI * 2);
    ctx.fillStyle = PW.color.text;
    ctx.fill();
    text(ctx, v.toFixed(2), r.x + rowW, y + HSL_ROW_H / 2, {
      colour: PW.color.textMute,
      align: "right",
      font: PW.font.mono
    });
  });
}
function oklchCss(l, c, h) {
  return `oklch(${(l * 100).toFixed(1)}% ${c.toFixed(3)} ${(h * 180 / Math.PI).toFixed(1)}deg)`;
}
var CELL_H = THUMB_H + 18;
function gridShape(width, count) {
  const cols = Math.max(1, Math.floor((width + 8) / (THUMB_W + 8)));
  const rows = Math.max(1, Math.ceil(count / cols));
  const cellW = (width - 8 * (cols - 1)) / cols;
  return { cols, rows, cellW };
}
function layout2(w, ui) {
  const { rows } = gridShape(w, Math.max(1, ui.presets.length));
  let y = 0;
  const previewHeader = { x: 0, y, w, h: HEADER_H3 };
  y += HEADER_H3 + 6;
  const preview = { x: 0, y, w, h: PREVIEW_H2 };
  y += PREVIEW_H2 + M4.gapSection;
  const presetHeader = { x: 0, y, w, h: HEADER_H3 };
  y += HEADER_H3 + 6;
  const strip = { x: 0, y, w, h: rows * CELL_H + (rows - 1) * 6 };
  y += strip.h + M4.gapSection;
  const hslHeader = { x: 0, y, w, h: HEADER_H3 };
  y += HEADER_H3 + 6;
  const hslTabs = { x: 0, y, w: Math.min(w, 220), h: 22 };
  const hslRows = { x: 0, y: y + 26, w, h: HSL_BANDS.length * HSL_ROW_H };
  return { previewHeader, preview, presetHeader, strip, hslHeader, hslTabs, hslRows };
}
function panelHeight(w, ui) {
  const { rows } = gridShape(Math.max(200, w), Math.max(1, ui.presets.length));
  const strip = rows * CELL_H + (rows - 1) * 6;
  const base = HEADER_H3 + 6 + PREVIEW_H2 + M4.gapSection + HEADER_H3 + 6 + strip + M4.gapSection + HEADER_H3 + 6 + M4.padding;
  return base + (ui.hslOpen ? 26 + HSL_BANDS.length * HSL_ROW_H + M4.gapControl : 0);
}
function registerLook() {
  app.registerExtension({
    name: "pw.color.look",
    async beforeRegisterNodeDef(nodeType, nodeData) {
      if (nodeData?.name !== "PW_Look") return;
      const resetOptions = (node) => ({
        after: () => {
          const hsl = getWidget(node, "hsl");
          if (hsl) hsl.value = "{}";
          refreshPreview(node);
        }
      });
      addResetMenu(nodeType, resetOptions);
      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function() {
        const r = onCreated?.apply(this, arguments);
        hideSerialisationWidget(this, "hsl");
        const ui = {
          presets: [],
          thumbs: /* @__PURE__ */ new Map(),
          source: null,
          hslOpen: false,
          hslTab: new Segmented(HSL_AXES.map((a) => ({ id: a, label: a }))),
          preview: new Preview()
        };
        uis2.set(this, ui);
        const panel = attachPanel(this, {
          minWidth: MIN_WIDTH2,
          height: (w) => panelHeight(w, ui),
          draw: (ctx, rr) => {
            const L = layout2(rr.w, ui);
            sectionHeader(ctx, "Preview", L.previewHeader, BADGE.lut);
            headerChip(ctx, L.previewHeader, "reset", BADGE.lut.label);
            ui.preview.comparing = isComparing();
            ui.preview.draw(ctx, L.preview);
            sectionHeader(ctx, "Presets, on your image", L.presetHeader, BADGE.lut);
            drawStrip(ctx, L.strip, this, ui);
            const arrow = ui.hslOpen ? "v" : ">";
            sectionHeader(ctx, `${arrow}  Colour mixer`, L.hslHeader, BADGE.lut);
            if (ui.hslOpen) {
              ui.hslTab.draw(ctx, L.hslTabs);
              drawHsl(ctx, L.hslRows, this, ui);
            }
          },
          onPointerDown: (x, y, m) => {
            const L = layout2(panel.width, ui);
            if (hit(headerChip(panel.context, L.previewHeader, "reset", BADGE.lut.label), x, y, 3)) {
              resetNode(this, resetOptions(this));
              return true;
            }
            if (hit(L.preview, x, y)) {
              ui.preview.onPointerDown(x, y, L.preview, m.shift, m.double);
              return true;
            }
            if (hit(L.hslHeader, x, y)) {
              ui.hslOpen = !ui.hslOpen;
              fitNode(this, panel);
              return true;
            }
            if (hit(L.strip, x, y) && ui.presets.length) {
              const { cols, cellW } = gridShape(L.strip.w, ui.presets.length);
              const col = Math.floor((x - L.strip.x) / (cellW + 8));
              const row = Math.floor((y - L.strip.y) / (CELL_H + 6));
              const preset = col >= 0 && col < cols ? ui.presets[row * cols + col] : void 0;
              if (preset) applyPreset(this, preset);
              return true;
            }
            if (ui.hslOpen) {
              if (ui.hslTab.onPointerDown(x, y, L.hslTabs)) return true;
              const row = Math.floor((y - L.hslRows.y) / HSL_ROW_H);
              if (row >= 0 && row < HSL_BANDS.length && x >= L.hslRows.x && x <= L.hslRows.x + L.hslRows.w) {
                const bands = readHsl(this);
                const name = HSL_BANDS[row][0];
                const trackX = L.hslRows.x + HSL_TRACK_X;
                const trackW = L.hslRows.w - HSL_TRACK_PAD;
                const v = Math.max(-1, Math.min(1, (x - trackX) / trackW * 2 - 1));
                bands[name][ui.hslTab.selected] = m.double ? 0 : Math.round(v * 100) / 100;
                writeHsl(this, bands);
                refreshPreview(this);
                return true;
              }
            }
            return false;
          },
          onPointerMove: (x, y) => ui.preview.onPointerMove(x, y, layout2(panel.width, ui).preview),
          onPointerUp: () => ui.preview.onPointerUp(),
          onWheel: (x, y, delta) => {
            const L = layout2(panel.width, ui);
            return hit(L.preview, x, y) && ui.preview.onWheel(x, y, L.preview, delta);
          },
          // Every slider on this node reshapes the grade, so the lattice the
          // preview samples is rebuilt whenever one moves.
          onWidgetChange: () => refreshPreview(this)
        });
        refreshPreview(this);
        const refresh = () => {
          void loadSource(this, ui);
          void ui.preview.load(this.id, () => panel.invalidate());
        };
        void (async () => {
          ui.presets = await loadPresets();
          fitNode(this, panel);
          refresh();
          panel.invalidate();
        })();
        const priorConfigure = this.onConfigure;
        this.onConfigure = function(info) {
          const res = priorConfigure?.call(this, info);
          fitNode(this, panel);
          refreshPreview(this);
          return res;
        };
        const stopCompare = onCompareChange(() => panel.invalidate());
        const stopRun = onRunComplete(refresh);
        const priorRemoved = this.onRemoved;
        this.onRemoved = function() {
          stopCompare();
          stopRun();
          priorRemoved?.call(this);
        };
        return r;
      };
    }
  });
}
function drawStrip(ctx, r, node, ui) {
  if (!ui.presets.length) {
    fillPanel(ctx, r, PW.color.well, M4.radiusPanel, PW.color.border);
    text(ctx, "Loading presets...", r.x + r.w / 2, r.y + r.h / 2, { colour: PW.color.textMute, align: "center" });
    return;
  }
  const current = String(getWidget(node, "preset")?.value ?? "none");
  const { cols, cellW } = gridShape(r.w, ui.presets.length);
  ctx.save();
  ctx.beginPath();
  ctx.rect(r.x, r.y, r.w, r.h);
  ctx.clip();
  ui.presets.forEach((p, i) => {
    const col = i % cols, row = Math.floor(i / cols);
    const x = r.x + col * (cellW + 8);
    const cell = { x, y: r.y + row * (CELL_H + 6), w: cellW, h: THUMB_H };
    const thumb = ui.thumbs.get(p.id);
    if (thumb) {
      ctx.save();
      fillPanel(ctx, cell, PW.color.well, M4.radiusControl);
      ctx.clip();
      ctx.drawImage(thumb, cell.x, cell.y, cell.w, cell.h);
      ctx.restore();
    } else {
      fillPanel(ctx, cell, PW.color.well, M4.radiusControl);
      text(ctx, "run once", cell.x + cell.w / 2, cell.y + cell.h / 2, {
        colour: PW.color.textMute,
        align: "center"
      });
    }
    ctx.strokeStyle = p.id === current ? PW.color.accent : PW.color.borderSoft;
    ctx.lineWidth = p.id === current ? 2 : 1;
    ctx.strokeRect(cell.x + 0.5, cell.y + 0.5, cell.w - 1, cell.h - 1);
    text(ctx, p.name, cell.x + cell.w / 2, cell.y + THUMB_H + 10, {
      colour: p.id === current ? PW.color.text : PW.color.textMute,
      align: "center"
    });
  });
  ctx.restore();
}

// src/nodes/optics.ts
function registerOptics() {
  app.registerExtension({
    name: "pw.color.optics",
    async beforeRegisterNodeDef(nodeType, nodeData) {
      if (nodeData?.name !== "PW_Optics") return;
      attachSpatialPreview(nodeType, { height: 200, minWidth: 340, label: "Result" });
    }
  });
}

// src/core/palette_export.ts
function channels(hex) {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16)
  ];
}
function toGpl(colors, name) {
  const lines = ["GIMP Palette", `Name: ${name}`, `Columns: ${Math.min(colors.length, 8)}`, "#"];
  for (const sw of colors) {
    const [r, g, b] = channels(sw.hex);
    lines.push(`${String(r).padStart(3)} ${String(g).padStart(3)} ${String(b).padStart(3)}	${sw.hex}`);
  }
  return lines.join("\n") + "\n";
}
function toAseBytes(colors) {
  const blocks = [];
  for (const sw of colors) {
    const name = sw.hex + "\0";
    const bodyLen = 2 + name.length * 2 + 4 + 12 + 2;
    const buf = new ArrayBuffer(6 + bodyLen);
    const view = new DataView(buf);
    let o = 0;
    view.setUint16(o, 1);
    o += 2;
    view.setUint32(o, bodyLen);
    o += 4;
    view.setUint16(o, name.length);
    o += 2;
    for (let i = 0; i < name.length; i++) {
      view.setUint16(o, name.charCodeAt(i));
      o += 2;
    }
    for (const ch of "RGB ") {
      view.setUint8(o, ch.charCodeAt(0));
      o += 1;
    }
    for (let i = 0; i < 3; i++) {
      view.setFloat32(o, parseInt(sw.hex.slice(1 + i * 2, 3 + i * 2), 16) / 255);
      o += 4;
    }
    view.setUint16(o, 0);
    blocks.push(buf);
  }
  const head = new ArrayBuffer(12);
  const hv = new DataView(head);
  for (let i = 0; i < 4; i++) hv.setUint8(i, "ASEF".charCodeAt(i));
  hv.setUint16(4, 1);
  hv.setUint16(6, 0);
  hv.setUint32(8, colors.length);
  const total = 12 + blocks.reduce((n, b) => n + b.byteLength, 0);
  const out = new Uint8Array(new ArrayBuffer(total));
  out.set(new Uint8Array(head), 0);
  let at = 12;
  for (const b of blocks) {
    out.set(new Uint8Array(b), at);
    at += b.byteLength;
  }
  return out;
}

// src/nodes/palette.ts
var M5 = PW.metrics;
var STRIP_H = 92;
var BLOCK_H = 44;
var HEADER_H4 = 18;
var PANEL_BLOCK = HEADER_H4 + 4 + STRIP_H + 22 + M5.padding;
var MIN_WIDTH3 = 360;
var TOAST_MS = 1400;
var palettes = /* @__PURE__ */ new WeakMap();
var toasts = /* @__PURE__ */ new WeakMap();
var saved = /* @__PURE__ */ new WeakMap();
function swatchRects(r, n) {
  if (n === 0) return [];
  const gap = 4;
  const w = (r.w - gap * (n - 1)) / n;
  return Array.from({ length: n }, (_, i) => ({ x: r.x + i * (w + gap), y: r.y, w, h: r.h }));
}
function drawPalette(ctx, r, node) {
  const data = palettes.get(node);
  if (!data || data.colors.length === 0) {
    fillPanel(ctx, r, PW.color.well, M5.radiusPanel, PW.color.border);
    text(ctx, "Run the graph to extract a palette", r.x + r.w / 2, r.y + r.h / 2, {
      colour: PW.color.textMute,
      align: "center"
    });
    return;
  }
  const cells = swatchRects(r, data.colors.length);
  const peak = Math.max(...data.colors.map((c) => c.coverage), 1e-6);
  data.colors.forEach((sw, i) => {
    const c = cells[i];
    roundRect(ctx, { x: c.x, y: c.y, w: c.w, h: BLOCK_H }, M5.radiusControl);
    ctx.fillStyle = sw.hex;
    ctx.fill();
    ctx.strokeStyle = PW.color.border;
    ctx.lineWidth = M5.borderHair;
    ctx.stroke();
    ctx.font = PW.font.mono;
    if (ctx.measureText(sw.hex).width <= c.w - 2) {
      text(ctx, sw.hex, c.x + c.w / 2, c.y + BLOCK_H + 12, {
        colour: PW.color.textDim,
        align: "center",
        font: PW.font.mono
      });
    }
    const barY = c.y + BLOCK_H + 22;
    fillPanel(ctx, { x: c.x, y: barY, w: c.w, h: 4 }, PW.color.well, 2);
    fillPanel(ctx, { x: c.x, y: barY, w: c.w * (sw.coverage / peak), h: 4 }, PW.color.accent, 2);
    text(ctx, `${Math.round(sw.coverage * 100)}%`, c.x + c.w / 2, barY + 14, {
      colour: PW.color.textMute,
      align: "center",
      font: PW.font.mono
    });
  });
}
function headerChips(ctx, r, node) {
  const locked = !!String(getWidget(node, "locked")?.value ?? "").trim();
  const lockLabel = locked ? "unlock" : "lock as target";
  ctx.font = PW.font.body;
  const lockW = ctx.measureText(lockLabel).width + 14;
  const expW = ctx.measureText("export").width + 14;
  const y = r.y + (r.h - 18) / 2;
  return {
    lock: { x: r.x + r.w - lockW, y, w: lockW, h: 18 },
    exp: { x: r.x + r.w - lockW - expW - 6, y, w: expW, h: 18 },
    lockLabel
  };
}
function drawHeader(ctx, r, node) {
  const locked = !!String(getWidget(node, "locked")?.value ?? "").trim();
  text(ctx, locked ? "Palette (locked)" : "Palette", r.x, r.y + r.h / 2, {
    colour: locked ? PW.color.accent : PW.color.textDim
  });
  const { lock, exp, lockLabel } = headerChips(ctx, r, node);
  const hasPalette = !!palettes.get(node);
  fillPanel(ctx, exp, PW.color.chip, M5.radiusControl, PW.color.borderSoft);
  text(ctx, "export", exp.x + exp.w / 2, r.y + r.h / 2, {
    colour: hasPalette ? PW.color.textMute : PW.color.borderSoft,
    align: "center"
  });
  fillPanel(ctx, lock, locked ? PW.color.chipActive : PW.color.chip, M5.radiusControl, PW.color.borderSoft);
  text(ctx, lockLabel, lock.x + lock.w / 2, r.y + r.h / 2, {
    colour: locked ? PW.color.text : PW.color.textMute,
    align: "center"
  });
}
function layout3(w) {
  return { header: { x: 0, y: 0, w, h: HEADER_H4 }, strip: { x: 0, y: HEADER_H4 + 4, w, h: STRIP_H } };
}
function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
function exportPalette(node, format) {
  const data = palettes.get(node);
  if (!data) return;
  const stamp = (/* @__PURE__ */ new Date()).toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const name = `pw-palette-${stamp}`;
  if (format === "ase") {
    download(new Blob([toAseBytes(data.colors)], { type: "application/octet-stream" }), `${name}.ase`);
  } else if (format === "gpl") {
    download(new Blob([toGpl(data.colors, name)], { type: "text/plain" }), `${name}.gpl`);
  } else if (format === "txt") {
    download(new Blob([data.colors.map((c) => c.hex).join("\n") + "\n"], { type: "text/plain" }), `${name}.txt`);
  } else if (format === "css") {
    const css = `:root {
${data.colors.map((c, i) => `  --palette-${i + 1}: ${c.hex};`).join("\n")}
}
`;
    download(new Blob([css], { type: "text/css" }), `${name}.css`);
  } else {
    download(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }), `${name}.json`);
  }
  toast(node, `exported .${format}`);
}
var EXPORT_FORMATS = [
  { id: "json", label: "PW palette (.json) - reopens here" },
  { id: "ase", label: "Adobe (.ase) - Photoshop, Illustrator" },
  { id: "gpl", label: "GIMP (.gpl) - GIMP, Krita, Inkscape" },
  { id: "txt", label: "Hex list (.txt)" },
  { id: "css", label: "CSS variables (.css)" }
];
function toast(node, message) {
  toasts.set(node, { text: message, until: performance.now() + TOAST_MS });
  panelOf(node)?.invalidate();
  setTimeout(() => panelOf(node)?.invalidate(), TOAST_MS + 50);
}
async function copyHex(node, hex) {
  try {
    await navigator.clipboard.writeText(hex);
    toast(node, `copied ${hex}`);
  } catch {
    toast(node, "clipboard blocked (needs https)");
  }
}
function registerPalette() {
  app.registerExtension({
    name: "pw.color.palette",
    async setup() {
      api.addEventListener("executed", (e) => {
        const detail = e?.detail;
        const node = app.graph?.getNodeById?.(detail?.node);
        if (!node || node.type !== "PW_Palette") return;
        const raw = detail?.output?.pw_palette?.[0];
        if (!raw) return;
        try {
          palettes.set(node, typeof raw === "string" ? JSON.parse(raw) : raw);
          const path = detail?.output?.pw_saved?.[0];
          if (path) saved.set(node, String(path));
          else saved.delete(node);
          panelOf(node)?.invalidate();
        } catch {
        }
      });
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
      if (nodeData?.name !== "PW_Palette") return;
      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function() {
        const r = onCreated?.apply(this, arguments);
        hideSerialisationWidget(this, "locked");
        const panel = attachPanel(this, {
          minWidth: MIN_WIDTH3,
          height: () => PANEL_BLOCK,
          draw: (ctx, rr) => {
            const L = layout3(rr.w);
            drawHeader(ctx, L.header, this);
            drawPalette(ctx, L.strip, this);
            drawSavedHint(ctx, L.strip, this);
            const t = toasts.get(this);
            if (t && performance.now() < t.until) {
              text(ctx, t.text, L.header.x + L.header.w / 2, L.strip.y + L.strip.h + 12, {
                colour: PW.color.accent,
                align: "center"
              });
            }
          },
          onPointerDown: (x, y, m) => {
            const L = layout3(panel.width);
            const { lock, exp } = headerChips(panel.context, L.header, this);
            if (hit(exp, x, y)) {
              if (!palettes.get(this)) {
                toast(this, "nothing to export \u2014 run the graph first");
                return true;
              }
              new globalThis.LiteGraph.ContextMenu(
                EXPORT_FORMATS.map((f) => ({ content: f.label, callback: () => exportPalette(this, f.id) })),
                { event: m.event, title: "Export palette" }
              );
              return true;
            }
            if (hit(lock, x, y)) {
              const w = getWidget(this, "locked");
              if (!w) return true;
              if (String(w.value ?? "").trim()) {
                w.value = "";
                toast(this, "unlocked");
              } else {
                const data2 = palettes.get(this);
                if (!data2) {
                  toast(this, "nothing to lock \u2014 run the graph first");
                  return true;
                }
                w.value = JSON.stringify(data2);
                toast(this, "locked");
              }
              w.callback?.(w.value);
              return true;
            }
            const data = palettes.get(this);
            if (data && y >= L.strip.y && y <= L.strip.y + BLOCK_H) {
              const cells = swatchRects(L.strip, data.colors.length);
              const i = cells.findIndex((c) => x >= c.x && x <= c.x + c.w);
              if (i >= 0) {
                void copyHex(this, data.colors[i].hex);
                return true;
              }
            }
            return false;
          }
        });
        return r;
      };
    }
  });
}
function drawSavedHint(ctx, strip, node) {
  const path = saved.get(node);
  if (!path) return;
  const name = path.split(/[\\/]/).pop() ?? path;
  text(ctx, `saved ${name}`, strip.x, strip.y + strip.h + 12, { colour: PW.color.textMute });
}

// src/core/reach.ts
var MUTED = 2;
var BYPASSED = 4;
function outputsDownstream(start, reach) {
  const seen = /* @__PURE__ */ new Set([String(start)]);
  const found = [];
  const queue = [...reach.next(start)];
  while (queue.length) {
    const id = queue.shift();
    const key = String(id);
    if (seen.has(key)) continue;
    seen.add(key);
    const mode = reach.mode(id);
    if (mode === MUTED) continue;
    if (mode !== BYPASSED && reach.isOutput(id)) found.push(key);
    queue.push(...reach.next(id));
  }
  return found;
}

// src/core/stars.ts
var STARS = 5;
function starHit(x, y, cells, starSize) {
  for (let index = 0; index < cells.length; index++) {
    const c = cells[index];
    const top = c.y + c.h - starSize;
    if (x < c.x || x > c.x + c.w || y < top || y > c.y + c.h) continue;
    const star2 = Math.floor((x - c.x) / starSize) + 1;
    if (star2 < 1 || star2 > STARS) return null;
    return { index, star: star2 };
  }
  return null;
}
function nextRating(current, clicked) {
  return current === clicked ? 0 : clicked;
}
function keptCount(ratings) {
  return ratings.filter((r) => r > 0).length;
}
function rerunHit(x, y, cells, starSize) {
  for (let index = 0; index < cells.length; index++) {
    const c = cells[index];
    const top = c.y + c.h - starSize;
    const left = c.x + STARS * starSize;
    if (x >= left && x <= c.x + c.w && y >= top && y <= c.y + c.h) return index;
  }
  return null;
}
var VIEW_MIN = 120;
var VIEW_MAX = 2400;
function clampView(h) {
  return Math.round(Math.min(VIEW_MAX, Math.max(VIEW_MIN, h)));
}
function viewHeight(base, spare) {
  return clampView(base + Math.max(0, spare));
}

// src/nodes/review.ts
var M6 = PW.metrics;
var HEADER_H5 = 18;
var TOOLBAR_H = 20;
var VIEW_DEFAULT = 220;
var HANDLE_H = 12;
var THUMB_H2 = 76;
var STAR_H = 16;
var CELL_H2 = THUMB_H2 + STAR_H;
var CELL_W = 96;
var CELL_GAP = 8;
var MIN_WIDTH4 = 420;
var VIEW_PROP = "pw_view_h";
var uis3 = /* @__PURE__ */ new WeakMap();
function blank() {
  return {
    count: 0,
    ratings: [],
    reruns: [],
    rerunnable: [],
    pending: 0,
    epoch: -1,
    focus: 0,
    views: /* @__PURE__ */ new Map(),
    thumbs: /* @__PURE__ */ new Map(),
    note: "",
    flash: "",
    flashTimer: null,
    sentAutoPass: null,
    drag: null
  };
}
function baseView(node) {
  const saved2 = Number(node.properties?.[VIEW_PROP]);
  return Number.isFinite(saved2) && saved2 > 0 ? clampView(saved2) : VIEW_DEFAULT;
}
function setBaseView(node, h) {
  const n = node;
  n.properties ??= {};
  n.properties[VIEW_PROP] = clampView(h);
}
function columns(width) {
  return Math.max(1, Math.floor((width + CELL_GAP) / (CELL_W + CELL_GAP)));
}
function rowCount(width, count) {
  return Math.max(1, Math.ceil(Math.max(1, count) / columns(width)));
}
function stripHeight(w, count) {
  const rows = rowCount(w, count);
  return rows * CELL_H2 + (rows - 1) * CELL_GAP;
}
function gridCells(strip, count) {
  const cols = columns(strip.w);
  return Array.from({ length: count }, (_, i) => ({
    x: strip.x + i % cols * (CELL_W + CELL_GAP),
    y: strip.y + Math.floor(i / cols) * (CELL_H2 + CELL_GAP),
    w: CELL_W,
    h: CELL_H2
  }));
}
function fixedHeight(w, ui) {
  return HEADER_H5 + 4 + TOOLBAR_H + 6 + HANDLE_H + stripHeight(w, ui.count) + M6.padding;
}
function panelHeight2(w, node, ui) {
  return fixedHeight(w, ui) + baseView(node);
}
function layout4(w, h, node, ui) {
  const viewH = viewHeight(baseView(node), h - panelHeight2(w, node, ui));
  let y = 0;
  const header = { x: 0, y, w, h: HEADER_H5 };
  y += HEADER_H5 + 4;
  const toolbar = { x: 0, y, w, h: TOOLBAR_H };
  y += TOOLBAR_H + 6;
  const view = { x: 0, y, w, h: viewH };
  y += viewH;
  const handle = { x: 0, y, w, h: HANDLE_H };
  y += HANDLE_H;
  return { header, toolbar, view, handle, strip: { x: 0, y, w, h: 0 } };
}
function tools(ctx, bar) {
  const release2 = headerChip(ctx, bar, "release");
  const clear = headerChip(ctx, { ...bar, w: release2.x - bar.x - 6 }, "clear");
  const measure = (s) => ctx ? (ctx.font = PW.font.body, ctx.measureText(s).width) : s.length * 6.2;
  const chipAt = (x, label) => {
    const r = { x, y: bar.y + (bar.h - 16) / 2, w: measure(label) + 14, h: 16 };
    if (ctx) {
      fillPanel(ctx, r, PW.color.chip, M6.radiusControl, PW.color.borderSoft);
      text(ctx, label, r.x + r.w / 2, bar.y + bar.h / 2, { colour: PW.color.textMute, align: "center" });
    }
    return r;
  };
  const rateAll = chipAt(bar.x, "rate all 5");
  const sendAll = chipAt(rateAll.x + rateAll.w + 6, "send all");
  return { release: release2, clear, sendAll, rateAll };
}
function star(ctx, cx, cy, radius, filled) {
  ctx.beginPath();
  for (let i = 0; i < 10; i++) {
    const r = i % 2 === 0 ? radius : radius * 0.45;
    const a = -Math.PI / 2 + i * Math.PI / 5;
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
    ctx.lineWidth = PW.metrics.border;
    ctx.stroke();
  }
}
function letter(ctx, x, y, size, label, strong) {
  const r = { x, y, w: size, h: size };
  fillPanel(ctx, r, strong ? PW.color.accent : PW.color.chip, M6.radiusControl, PW.color.borderSoft);
  text(ctx, label, x + size / 2, y + size / 2, { colour: strong ? PW.color.well : PW.color.textMute, align: "center" });
}
function flash(node, ui, message) {
  ui.flash = message;
  if (ui.flashTimer) clearTimeout(ui.flashTimer);
  ui.flashTimer = setTimeout(() => {
    ui.flash = "";
    panelOf(node)?.invalidate();
  }, 2500);
  panelOf(node)?.invalidate();
}
async function loadFrame(node, ui, kind, index) {
  const into = kind === "image" ? ui.views : ui.thumbs;
  if (into.has(index)) return;
  const epoch = ui.epoch;
  try {
    const res = await fetchPw(`/pw_color/review/${node.id}/${kind}/${index}`);
    if (!res.ok) return;
    const img = new Image();
    const url = URL.createObjectURL(await res.blob());
    await new Promise((done) => {
      img.onload = () => done();
      img.onerror = () => done();
      img.src = url;
    });
    URL.revokeObjectURL(url);
    if (ui.epoch !== epoch) return;
    into.set(index, img);
    panelOf(node)?.invalidate();
  } catch {
  }
}
function reportAutoPass(node, ui) {
  const value = !!getWidget(node, "auto_pass")?.value;
  if (ui.sentAutoPass === value || node.id == null || Number(node.id) < 0) return;
  ui.sentAutoPass = value;
  postPw(`/pw_color/review/${node.id}/mode`, { auto_pass: value }).catch(() => {
    ui.sentAutoPass = null;
  });
}
async function syncState(node, ui) {
  reportAutoPass(node, ui);
  try {
    const res = await fetchPw(`/pw_color/review/${node.id}`);
    if (!res.ok) return;
    const state = await res.json();
    const epoch = state.epoch ?? 0;
    if (epoch !== ui.epoch) {
      ui.views.clear();
      ui.thumbs.clear();
      ui.focus = 0;
      ui.epoch = epoch;
    }
    const hadPending = ui.pending;
    ui.count = state.count ?? 0;
    ui.ratings = Array.isArray(state.ratings) ? state.ratings.slice() : [];
    ui.reruns = Array.isArray(state.reruns) ? state.reruns.slice() : [];
    ui.rerunnable = Array.isArray(state.rerunnable) ? state.rerunnable.slice() : [];
    ui.pending = state.pending ?? 0;
    if (hadPending && !ui.pending && ui.note.startsWith("sending")) ui.note = `sent ${hadPending}`;
    if (ui.focus >= ui.count) ui.focus = Math.max(0, ui.count - 1);
    const panel = panelOf(node);
    if (panel) fitNode(node, panel);
    if (ui.count) {
      void loadFrame(node, ui, "image", ui.focus);
      for (let i = 0; i < ui.count; i++) void loadFrame(node, ui, "thumb", i);
    }
    panel?.invalidate();
  } catch {
  }
}
function saveRatings(node, ui) {
  postPw(`/pw_color/review/${node.id}/ratings`, { ratings: ui.ratings }).catch(() => {
  });
}
function deliveryTargets(node) {
  const graph = node.graph ?? app.graph;
  const byId = (id) => graph?.getNodeById?.(id);
  const link = (id) => graph?.getLink?.(id) ?? graph?.links?.get?.(id) ?? graph?.links?.[id];
  try {
    return outputsDownstream(node.id, {
      next: (id) => (byId(id)?.outputs ?? []).flatMap((o) => o?.links ?? []).map((l) => link(l)?.target_id).filter((t) => t != null),
      isOutput: (id) => !!byId(id)?.constructor?.nodeData?.output_node,
      mode: (id) => byId(id)?.mode ?? 0
    });
  } catch {
    return [];
  }
}
async function release(node, ui, everything = false) {
  if (!ui.count || ui.pending) return;
  try {
    const res = await postPw(`/pw_color/review/${node.id}/release`, { ratings: ui.ratings, everything });
    if (!res.ok) {
      ui.note = "the tray was already empty";
    } else {
      const { kept } = await res.json();
      if (!kept) {
        ui.note = "nothing rated, so the tray was emptied";
      } else {
        ui.pending = kept;
        ui.note = `sending ${kept}`;
        const targets = deliveryTargets(node);
        await app.queuePrompt(-1, 1, targets.length ? targets : void 0);
      }
    }
  } catch {
    ui.note = "could not queue the delivery; the next run will carry it";
  }
  await syncState(node, ui);
}
async function rerun(node, ui, index) {
  try {
    const res = await postPw(`/pw_color/review/${node.id}/rerun/${index}`, {});
    if (!res.ok) {
      flash(node, ui, "that frame cannot be re-run");
      return;
    }
    const job = await res.json();
    await api.queuePrompt(-1, { output: job.prompt, workflow: job.workflow });
    flash(node, ui, `re-run of #${index + 1} queued`);
  } catch {
    flash(node, ui, "could not queue the re-run");
  }
}
async function clearTray(node, ui) {
  try {
    await postPw(`/pw_color/review/${node.id}/clear`, {});
    ui.note = "tray cleared";
  } catch {
    ui.note = "could not reach the server";
  }
  await syncState(node, ui);
}
function idleText(node, ui) {
  if (ui.note) return ui.note;
  return getWidget(node, "auto_pass")?.value ? "auto_pass is on: images go straight through." : "The tray is empty. Each run adds its images here.";
}
function setNodeHeight(node, h) {
  node.size[1] = h;
  node.setSize?.([node.size[0], h]);
  node.setDirtyCanvas?.(true, true);
}
function draw(node, ui, ctx, rr) {
  const L = layout4(rr.w, rr.h, node, ui);
  const label = ui.flash ? `Review \u2014 ${ui.flash}` : ui.pending ? `Review \u2014 sending ${ui.pending}` : ui.count ? `Review \u2014 ${ui.count} in tray, ${keptCount(ui.ratings)} kept` : "Review";
  sectionHeader(ctx, label, L.header);
  if (ui.count && !ui.pending) tools(ctx, L.toolbar);
  fillPanel(ctx, L.view, PW.color.well, M6.radiusPanel, PW.color.border);
  const focus = ui.views.get(ui.focus);
  if (focus) {
    ctx.save();
    fillPanel(ctx, L.view, PW.color.well, M6.radiusPanel);
    ctx.clip();
    const s = Math.min(L.view.w / focus.width, L.view.h / focus.height);
    const w = focus.width * s;
    const h = focus.height * s;
    ctx.drawImage(focus, L.view.x + (L.view.w - w) / 2, L.view.y + (L.view.h - h) / 2, w, h);
    ctx.restore();
  } else {
    text(ctx, ui.count ? "loading" : idleText(node, ui), L.view.x + L.view.w / 2, L.view.y + L.view.h / 2, {
      colour: PW.color.textMute,
      align: "center"
    });
  }
  const gy = L.handle.y + L.handle.h / 2;
  ctx.strokeStyle = ui.drag ? PW.color.accent : PW.color.textMute;
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (const dy of [-2, 2]) {
    ctx.moveTo(L.handle.w / 2 - 18, gy + dy);
    ctx.lineTo(L.handle.w / 2 + 18, gy + dy);
  }
  ctx.stroke();
  gridCells(L.strip, ui.count).forEach((c, i) => {
    const cell = { x: c.x, y: c.y, w: c.w, h: THUMB_H2 };
    fillPanel(ctx, cell, PW.color.well, M6.radiusControl);
    const thumb = ui.thumbs.get(i);
    if (thumb) {
      ctx.save();
      fillPanel(ctx, cell, PW.color.well, M6.radiusControl);
      ctx.clip();
      const s = Math.max(cell.w / thumb.width, THUMB_H2 / thumb.height);
      const w = thumb.width * s;
      const h = thumb.height * s;
      ctx.drawImage(thumb, c.x + (cell.w - w) / 2, c.y + (THUMB_H2 - h) / 2, w, h);
      ctx.restore();
    }
    const focused = i === ui.focus;
    ctx.strokeStyle = focused ? PW.color.accent : PW.color.borderSoft;
    ctx.lineWidth = focused ? 2 : 1;
    ctx.strokeRect(c.x + 0.5, c.y + 0.5, cell.w - 1, THUMB_H2 - 1);
    if (ui.reruns[i]) letter(ctx, c.x + 3, c.y + 3, 14, "R", true);
    const rating = ui.ratings[i] ?? 0;
    for (let s = 1; s <= STARS; s++) {
      star(ctx, c.x + (s - 0.5) * STAR_H, c.y + THUMB_H2 + STAR_H / 2, STAR_H * 0.4, s <= rating);
    }
    if (ui.rerunnable[i]) letter(ctx, c.x + STARS * STAR_H + 1, c.y + THUMB_H2 + 1, STAR_H - 2, "R", false);
  });
}
function registerReview() {
  app.registerExtension({
    name: "pw.color.review",
    async setup() {
      api.addEventListener("pw_color.review", (e) => {
        const node = app.graph?.getNodeById?.(e?.detail?.node_id);
        const ui = node ? uis3.get(node) : void 0;
        if (node && ui) void syncState(node, ui);
      });
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
      if (nodeData?.name !== "PW_Review") return;
      addResetMenu(nodeType);
      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function() {
        const created = onCreated?.apply(this, arguments);
        const ui = blank();
        uis3.set(this, ui);
        const panel = attachPanel(this, {
          minWidth: MIN_WIDTH4,
          height: (w) => panelHeight2(w, this, ui),
          onWidgetChange: () => reportAutoPass(this, ui),
          draw: (ctx, rr) => draw(this, ui, ctx, rr),
          onPointerDown: (x, y) => {
            const L = layout4(panel.width, panel.height, this, ui);
            if (hit(L.handle, x, y, 2)) {
              ui.drag = { y, view: L.view.h, nodeH: this.size[1] };
              return true;
            }
            if (ui.count && !ui.pending) {
              const t = tools(panel.context, L.toolbar);
              if (hit(t.release, x, y, 3)) {
                void release(this, ui);
                return true;
              }
              if (hit(t.clear, x, y, 3)) {
                void clearTray(this, ui);
                return true;
              }
              if (hit(t.sendAll, x, y, 3)) {
                void release(this, ui, true);
                return true;
              }
              if (hit(t.rateAll, x, y, 3)) {
                ui.ratings = Array.from({ length: ui.count }, () => STARS);
                saveRatings(this, ui);
                return true;
              }
            }
            if (!ui.count) return false;
            const cells = gridCells(L.strip, ui.count);
            if (!ui.pending) {
              const again = rerunHit(x, y, cells, STAR_H);
              if (again != null && ui.rerunnable[again]) {
                void rerun(this, ui, again);
                return true;
              }
              const onStar = starHit(x, y, cells, STAR_H);
              if (onStar) {
                ui.ratings[onStar.index] = nextRating(ui.ratings[onStar.index] ?? 0, onStar.star);
                saveRatings(this, ui);
                return true;
              }
            }
            const picked = cells.findIndex((c) => hit({ x: c.x, y: c.y, w: c.w, h: THUMB_H2 }, x, y));
            if (picked >= 0) {
              ui.focus = picked;
              void loadFrame(this, ui, "image", picked);
              return true;
            }
            return false;
          },
          onPointerMove: (_x, y) => {
            if (!ui.drag) return false;
            setBaseView(this, ui.drag.view + (y - ui.drag.y));
            setNodeHeight(this, ui.drag.nodeH + baseView(this) - ui.drag.view);
            return true;
          },
          onPointerUp: () => {
            if (!ui.drag) return false;
            ui.drag = null;
            return true;
          }
        });
        setTimeout(() => void syncState(this, ui), 0);
        return created;
      };
    }
  });
}

// src/index.ts
function registerPortColours() {
  const canvas = app.canvas;
  if (!canvas) {
    console.warn("[PW Color] no canvas at setup, port colours not applied");
    return;
  }
  for (const key of ["default_connection_color_byType", "default_connection_color_byTypeOff"]) {
    const map = canvas[key];
    if (!map) continue;
    for (const [type, colour] of Object.entries(PW.port)) map[type] = colour;
  }
}
var PW_NODES = [
  "PW_Look",
  "PW_Curves",
  "PW_Grain",
  "PW_Optics",
  "PW_MatchSource",
  "PW_Palette",
  "PW_Scopes",
  "PW_LookIO",
  "PW_Review"
];
app.registerExtension({
  name: "pw.color",
  async setup() {
    warnIfUnsupported();
    registerPortColours();
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!PW_NODES.includes(nodeData?.name)) return;
    addResetMenu(nodeType);
  }
});
registerCurves();
registerGrain();
registerLook();
registerOptics();
registerPalette();
registerReview();
