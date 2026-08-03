// src/comfy.ts
import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";
var MIN_FRONTEND = [1, 40, 0];
function getWidget(node, name) {
  return node.widgets?.find((w) => w.name === name);
}
function chainHandler(node, key, handler) {
  const original = node[key];
  node[key] = function(...args) {
    const ours = handler.apply(this, args);
    const theirs = original ? original.apply(this, args) : void 0;
    return ours || theirs;
  };
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
    onAccent: "#1A172E"
    // text on accent fills
  },
  /** Curve editor channel tabs and scope traces. */
  channel: {
    luma: "#F0EEF8",
    r: "#D96A6A",
    g: "#7FBF9E",
    b: "#7FA8DD",
    warm: "#E0A44C"
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
   */
  interaction: {
    fineDragScale: 0.15,
    // shift-drag
    doubleClickResets: true,
    /** Hold-to-compare key. Held, not toggled: comparison is a glance. */
    compareKey: "Alt"
  }
};
var BADGE = {
  lut: { label: "LUT", fill: PW.color.chipActive, text: PW.color.text },
  render: { label: "render only", fill: PW.color.surface, text: PW.color.textMute },
  approx: { label: "preview approximate", fill: PW.color.surface, text: PW.channel.warm }
};

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
    if (now - this.lastClickTime < 300 && idx < 0) {
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
  applyPreset(preset) {
    for (const k of ["luma", "r", "g", "b"]) {
      const v = preset[k];
      if (v) this.state[k] = v.map((p) => [p[0], p[1]]);
    }
    this.changed();
  }
  changed() {
    this.onChange?.();
  }
};
function clamp012(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
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

// src/widgets/slider.ts
var LABEL_W = 86;
var VALUE_W = 52;
var Slider = class {
  spec;
  value;
  dragging = false;
  dragStartX = 0;
  dragStartValue = 0;
  lastClick = 0;
  constructor(spec, value) {
    const neutral = spec.neutral ?? spec.min;
    this.spec = {
      label: spec.label,
      min: spec.min,
      max: spec.max,
      neutral,
      default: spec.default ?? neutral,
      step: spec.step ?? 0.01,
      decimals: spec.decimals ?? 2,
      unit: spec.unit ?? ""
    };
    this.value = value ?? this.spec.default;
  }
  trackRect(r) {
    const x = r.x + LABEL_W;
    return { x, y: r.y + r.h / 2 - 3, w: r.w - LABEL_W - VALUE_W - PW.metrics.gapControl, h: 6 };
  }
  toValue(px, track) {
    const t = Math.min(1, Math.max(0, (px - track.x) / track.w));
    return this.spec.min + t * (this.spec.max - this.spec.min);
  }
  toPixel(v, track) {
    const t = (v - this.spec.min) / (this.spec.max - this.spec.min);
    return track.x + Math.min(1, Math.max(0, t)) * track.w;
  }
  quantise(v) {
    const { min, max, step } = this.spec;
    const snapped = Math.round(v / step) * step;
    return Math.min(max, Math.max(min, snapped));
  }
  draw(ctx, r) {
    const track = this.trackRect(r);
    const { neutral } = this.spec;
    text(ctx, this.spec.label, r.x, r.y + r.h / 2, { colour: PW.color.textDim });
    fillPanel(ctx, track, PW.color.well, 3);
    const nx = this.toPixel(neutral, track);
    const vx = this.toPixel(this.value, track);
    if (Math.abs(vx - nx) > 0.5) {
      fillPanel(ctx, { x: Math.min(nx, vx), y: track.y, w: Math.abs(vx - nx), h: track.h }, PW.color.accent, 3);
    }
    if (neutral > this.spec.min && neutral < this.spec.max) {
      ctx.fillStyle = PW.color.textMute;
      ctx.fillRect(Math.round(nx), track.y - 2, 1, track.h + 4);
    }
    ctx.beginPath();
    ctx.arc(vx, track.y + track.h / 2, PW.metrics.knob, 0, Math.PI * 2);
    ctx.fillStyle = PW.color.text;
    ctx.fill();
    text(ctx, formatValue(this.value, this.spec.decimals) + this.spec.unit, r.x + r.w, r.y + r.h / 2, {
      colour: PW.color.textMute,
      align: "right",
      font: PW.font.mono
    });
  }
  /** @returns true if the event was consumed. */
  onPointerDown(x, y, r, now) {
    const track = this.trackRect(r);
    const knobX = this.toPixel(this.value, track);
    const onKnob = Math.abs(x - knobX) <= PW.metrics.hitSlop && Math.abs(y - (track.y + track.h / 2)) <= PW.metrics.hitSlop;
    if (!onKnob && !hit(track, x, y, PW.metrics.hitSlop)) return false;
    if (now - this.lastClick < 300) {
      this.value = this.spec.default;
      this.lastClick = 0;
      return true;
    }
    this.lastClick = now;
    this.dragging = true;
    this.dragStartX = x;
    this.dragStartValue = onKnob ? this.value : this.quantise(this.toValue(x, track));
    this.value = this.dragStartValue;
    return true;
  }
  onPointerMove(x, _y, r, shift) {
    if (!this.dragging) return false;
    const track = this.trackRect(r);
    const perPixel = (this.spec.max - this.spec.min) / track.w;
    const scale = shift ? PW.interaction.fineDragScale : 1;
    this.value = this.quantise(this.dragStartValue + (x - this.dragStartX) * perPixel * scale);
    return true;
  }
  onPointerUp() {
    const was = this.dragging;
    this.dragging = false;
    return was;
  }
  get isDragging() {
    return this.dragging;
  }
  /** Hit region for the numeric readout, for click-to-type. */
  valueRect(r) {
    return { x: r.x + r.w - VALUE_W, y: r.y, w: VALUE_W, h: r.h };
  }
};

// src/widgets/layout.ts
function widgetHeight(node) {
  const compute = node.computeSize;
  if (typeof compute === "function") {
    const size = compute.call(node);
    if (Array.isArray(size) && Number.isFinite(size[1])) return size[1];
  }
  const visible = (node.widgets ?? []).filter((w) => w.type !== "hidden").length;
  return 40 + visible * (PW.metrics.controlHeight + 4);
}
function fitPanel(node, panelHeight, minWidth) {
  node.size[0] = Math.max(node.size[0], minWidth);
  node.size[1] = Math.max(node.size[1], widgetHeight(node) + panelHeight);
}

// src/nodes/curves.ts
var M = PW.metrics;
var HEADER_H = 18;
var TABS_H = M.controlHeight;
var ROW_H = M.controlHeight;
var MIN_EDITOR_H = 160;
var CHANNEL_TABS = [
  { id: "luma", label: "Luma", colour: PW.channel.luma },
  { id: "r", label: "R", colour: PW.channel.r },
  { id: "g", label: "G", colour: PW.channel.g },
  { id: "b", label: "B", colour: PW.channel.b }
];
var uis = /* @__PURE__ */ new WeakMap();
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
  node.setDirtyCanvas?.(true, true);
}
async function loadHistogram(node, ui) {
  try {
    const res = await fetch(`/pw_color/histogram/${node.id}`);
    if (!res.ok) return;
    const data = await res.json();
    const h = data.histogram;
    ui.editor.histogram = {
      luma: Float32Array.from(h.luma),
      r: Float32Array.from(h.r),
      g: Float32Array.from(h.g),
      b: Float32Array.from(h.b)
    };
    node.setDirtyCanvas?.(true, true);
  } catch {
  }
}
function makeUI(node) {
  const editor = new CurveEditor();
  const tabs = new Segmented(CHANNEL_TABS);
  const strengthWidget = getWidget(node, "strength");
  const strength = new Slider(
    { label: "Strength", min: 0, max: 1, neutral: 1, default: 1, step: 0.01, decimals: 2 },
    typeof strengthWidget?.value === "number" ? strengthWidget.value : 1
  );
  const layout2 = (n) => {
    const x = M.padding;
    const w = n.size[0] - M.padding * 2;
    let y = widgetHeight(n) + M.gapSection;
    const header = { x, y, w, h: HEADER_H };
    y += HEADER_H + 4;
    const tabsR = { x, y, w, h: TABS_H };
    y += TABS_H + M.gapControl;
    const editorH = Math.max(MIN_EDITOR_H, n.size[1] - y - ROW_H - M.gapSection - M.padding);
    const editorR = { x, y, w, h: editorH };
    y += editorH + M.gapControl;
    return { header, tabs: tabsR, editor: editorR, strength: { x, y, w, h: ROW_H } };
  };
  const ui = { editor, tabs, strength, layout: layout2 };
  editor.state = readState(node);
  editor.onChange = () => writeState(node, ui);
  return ui;
}
function registerCurves() {
  app.registerExtension({
    name: "pw.color.curves",
    async beforeRegisterNodeDef(nodeType, nodeData) {
      if (nodeData?.name !== "PW_Curves") return;
      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function() {
        const r = onCreated?.apply(this, arguments);
        const ui = makeUI(this);
        uis.set(this, ui);
        for (const name of ["curves", "strength"]) {
          const w = getWidget(this, name);
          if (!w) continue;
          w.type = "hidden";
          w.computeSize = () => [0, -4];
        }
        fitPanel(this, HEADER_H + TABS_H + MIN_EDITOR_H + ROW_H + M.gapSection * 2 + M.padding, 360);
        chainHandler(this, "onDrawForeground", function(ctx) {
          if (this.flags?.collapsed) return;
          const L = ui.layout(this);
          sectionHeader(ctx, "Curves", L.header, BADGE.lut);
          ui.tabs.draw(ctx, L.tabs);
          ui.editor.draw(ctx, L.editor);
          ui.strength.draw(ctx, L.strength);
        });
        chainHandler(this, "onMouseDown", function(e, pos) {
          const L = ui.layout(this);
          const [x, y] = pos;
          const now = e?.timeStamp ?? 0;
          const shift = !!e?.shiftKey;
          const tab = ui.tabs.onPointerDown(x, y, L.tabs);
          if (tab) {
            ui.editor.channel = tab;
            this.setDirtyCanvas?.(true, true);
            return true;
          }
          if (ui.strength.onPointerDown(x, y, L.strength, now)) {
            syncStrength(this, ui);
            return true;
          }
          if (x >= L.editor.x && x <= L.editor.x + L.editor.w && y >= L.editor.y && y <= L.editor.y + L.editor.h) {
            ui.editor.onPointerDown(x, y, L.editor, shift, now);
            this.setDirtyCanvas?.(true, true);
            return true;
          }
          return false;
        });
        chainHandler(this, "onMouseMove", function(e, pos) {
          const L = ui.layout(this);
          const shift = !!e?.shiftKey;
          if (ui.strength.onPointerMove(pos[0], pos[1], L.strength, shift)) {
            syncStrength(this, ui);
            return true;
          }
          if (ui.editor.onPointerMove(pos[0], pos[1], L.editor, shift)) {
            this.setDirtyCanvas?.(true, true);
            return ui.editor.isDragging;
          }
          return false;
        });
        chainHandler(this, "onMouseUp", function() {
          const a = ui.strength.onPointerUp();
          const b = ui.editor.onPointerUp();
          if (a || b) this.setDirtyCanvas?.(true, true);
          return a || b;
        });
        void loadHistogram(this, ui);
        return r;
      };
      const onConfigure = nodeType.prototype.onConfigure;
      nodeType.prototype.onConfigure = function(info) {
        const r = onConfigure?.apply(this, arguments);
        const ui = uis.get(this);
        if (ui) {
          ui.editor.state = readState(this);
          const sw = getWidget(this, "strength");
          if (typeof sw?.value === "number") ui.strength.value = sw.value;
          void loadHistogram(this, ui);
        }
        return r;
      };
    }
  });
}
function syncStrength(node, ui) {
  const w = getWidget(node, "strength");
  if (w && w.value !== ui.strength.value) {
    w.value = ui.strength.value;
    w.callback?.(w.value);
  }
  node.setDirtyCanvas?.(true, true);
}

// src/nodes/grain.ts
var M2 = PW.metrics;
var PANEL_H = 96;
var EDGE_FALLOFF = 0.04;
function smoothstep(e0, e1, x) {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}
function tonalWeight(t, shadows, mids, highlights) {
  const shadow = 1 - smoothstep(0, 0.5, t);
  const highlight = smoothstep(0.5, 1, t);
  const mid = Math.max(0, 1 - shadow - highlight);
  const w = shadow * shadows + mid * mids + highlight * highlights;
  return w * smoothstep(0, EDGE_FALLOFF, t) * smoothstep(0, EDGE_FALLOFF, 1 - t);
}
function num(node, name, fallback) {
  const v = getWidget(node, name)?.value;
  return typeof v === "number" ? v : fallback;
}
function drawResponse(ctx, r, node) {
  fillPanel(ctx, r, PW.color.well, M2.radiusPanel, PW.color.border);
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
  ctx.strokeStyle = PW.channel.warm;
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
      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function() {
        const r = onCreated?.apply(this, arguments);
        fitPanel(this, PANEL_H + 22 + M2.gapSection + M2.padding, 320);
        chainHandler(this, "onDrawForeground", function(ctx) {
          if (this.flags?.collapsed) return;
          const x = M2.padding;
          const w = this.size[0] - M2.padding * 2;
          const y = this.size[1] - PANEL_H - M2.padding - 18;
          sectionHeader(ctx, "Tonal response", { x, y, w, h: 18 }, BADGE.render);
          drawResponse(ctx, { x, y: y + 20, w, h: PANEL_H - 20 }, this);
        });
        return r;
      };
      const onWidgetChanged = nodeType.prototype.onWidgetChanged;
      nodeType.prototype.onWidgetChanged = function() {
        const res = onWidgetChanged?.apply(this, arguments);
        this.setDirtyCanvas?.(true, true);
        return res;
      };
    }
  });
}

// src/nodes/palette.ts
var M3 = PW.metrics;
var STRIP_H = 92;
var BLOCK_H = 44;
var PANEL_BLOCK = STRIP_H + 22 + M3.gapSection + M3.padding;
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
    fillPanel(ctx, r, PW.color.well, M3.radiusPanel, PW.color.border);
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
    roundRect(ctx, { x: c.x, y: c.y, w: c.w, h: BLOCK_H }, M3.radiusControl);
    ctx.fillStyle = sw.hex;
    ctx.fill();
    ctx.strokeStyle = PW.color.border;
    ctx.lineWidth = M3.borderHair;
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
  fillPanel(ctx, exp, PW.color.chip, M3.radiusControl, PW.color.borderSoft);
  text(ctx, "export", exp.x + exp.w / 2, r.y + r.h / 2, {
    colour: hasPalette ? PW.color.textMute : PW.color.borderSoft,
    align: "center"
  });
  fillPanel(ctx, lock, locked ? PW.color.chipActive : PW.color.chip, M3.radiusControl, PW.color.borderSoft);
  text(ctx, lockLabel, lock.x + lock.w / 2, r.y + r.h / 2, {
    colour: locked ? PW.color.text : PW.color.textMute,
    align: "center"
  });
}
function layout(node) {
  const x = M3.padding;
  const w = node.size[0] - M3.padding * 2;
  const y = node.size[1] - STRIP_H - M3.padding - 22;
  return { header: { x, y, w, h: 18 }, strip: { x, y: y + 22, w, h: STRIP_H } };
}
function toGpl(data, name) {
  const lines = ["GIMP Palette", `Name: ${name}`, `Columns: ${Math.min(data.colors.length, 8)}`, "#"];
  for (const sw of data.colors) {
    const r = parseInt(sw.hex.slice(1, 3), 16);
    const g = parseInt(sw.hex.slice(3, 5), 16);
    const b = parseInt(sw.hex.slice(5, 7), 16);
    lines.push(`${String(r).padStart(3)} ${String(g).padStart(3)} ${String(b).padStart(3)}	${sw.hex}`);
  }
  return lines.join("\n") + "\n";
}
function toAse(data) {
  const blocks = [];
  for (const sw of data.colors) {
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
  hv.setUint32(8, data.colors.length);
  return new Blob([head, ...blocks], { type: "application/octet-stream" });
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
    download(toAse(data), `${name}.ase`);
  } else if (format === "gpl") {
    download(new Blob([toGpl(data, name)], { type: "text/plain" }), `${name}.gpl`);
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
  toasts.set(node, { text: message, until: performance.now() + 1400 });
  node.setDirtyCanvas?.(true, true);
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
          node.setDirtyCanvas?.(true, true);
        } catch {
        }
      });
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
      if (nodeData?.name !== "PW_Palette") return;
      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function() {
        const r = onCreated?.apply(this, arguments);
        const lockedWidget = getWidget(this, "locked");
        if (lockedWidget) {
          lockedWidget.type = "hidden";
          lockedWidget.computeSize = () => [0, -4];
        }
        fitPanel(this, PANEL_BLOCK, 360);
        chainHandler(this, "onDrawForeground", function(ctx) {
          if (this.flags?.collapsed) return;
          const L = layout(this);
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
        });
        chainHandler(this, "onMouseDown", function(e, pos) {
          const L = layout(this);
          const [x, y] = pos;
          const ctx = app.canvas?.ctx;
          if (!ctx) return false;
          const { lock, exp } = headerChips(ctx, L.header, this);
          if (hit(exp, x, y)) {
            if (!palettes.get(this)) {
              toast(this, "nothing to export \u2014 run the graph first");
              return true;
            }
            new globalThis.LiteGraph.ContextMenu(
              EXPORT_FORMATS.map((f) => ({ content: f.label, callback: () => exportPalette(this, f.id) })),
              { event: e, title: "Export palette" }
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

// src/core/lattice.ts
var DEFAULT_SIZE = 33;
var FINAL_SIZE = 65;
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

// src/nodes/parity.ts
function bake(node) {
  const lookW = getWidget(node, "look_json");
  const outW = getWidget(node, "js_lattice");
  const finalW = getWidget(node, "final_quality");
  if (!lookW || !outW) return;
  let ops;
  try {
    ops = JSON.parse(String(lookW.value)).ops ?? [];
  } catch {
    outW.value = "";
    return;
  }
  const size = finalW?.value ? FINAL_SIZE : DEFAULT_SIZE;
  const t0 = performance.now();
  const lattice = Lattice.fromFn(buildSampleFn(ops), size);
  outW.value = JSON.stringify(lattice.toTransport("u16"));
  console.debug(`[PW Color] baked ${size}\xB3 lattice in ${(performance.now() - t0).toFixed(1)}ms`);
}
function registerParityProbe() {
  app.registerExtension({
    name: "pw.color.parity",
    async beforeRegisterNodeDef(nodeType, nodeData) {
      if (nodeData?.name !== "PW_ParityProbe") return;
      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function() {
        const r = onCreated?.apply(this, arguments);
        for (const name of ["look_json", "final_quality"]) {
          const w = getWidget(this, name);
          if (!w) continue;
          const prev = w.callback;
          w.callback = (v) => {
            const out = prev?.call(w, v);
            bake(this);
            return out;
          };
        }
        bake(this);
        return r;
      };
    }
  });
}

// src/index.ts
function registerPortColours() {
  const canvas = app.canvas;
  if (!canvas) {
    console.warn("[PW Color] no canvas at setup \u2014 port colours not applied");
    return;
  }
  for (const key of ["default_connection_color_byType", "default_connection_color_byTypeOff"]) {
    const map = canvas[key];
    if (!map) continue;
    for (const [type, colour] of Object.entries(PW.port)) map[type] = colour;
  }
}
app.registerExtension({
  name: "pw.color",
  async setup() {
    warnIfUnsupported();
    registerPortColours();
  }
});
registerCurves();
registerGrain();
registerPalette();
registerParityProbe();
