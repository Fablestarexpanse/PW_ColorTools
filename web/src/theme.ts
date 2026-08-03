/**
 * The PW Color design system. Single source of truth.
 *
 * Nothing in `widgets/`, `canvas/` or `nodes/` may hardcode a colour, a radius
 * or a spacing value. If two nodes draw a slider differently that is a bug, and
 * this file is how we make that bug impossible rather than merely discouraged.
 *
 * Values are plain numbers/strings rather than CSS custom properties because
 * most of our surface is canvas, where `var(--pw-accent)` means nothing. The
 * `cssVars()` helper emits the same palette for the handful of DOM overlays
 * (text inputs, context menus) that do want CSS.
 */

export const PW = {
  color: {
    panel: '#1B1A20', // node body
    header: '#272433', // header bar
    surface: '#201E28', // inset control group
    well: '#131218', // viewers, canvases, scopes
    chip: '#272433', // button / chip resting
    chipActive: '#3E3856', // selected chip
    border: '#4A4358', // panel + control borders
    borderSoft: '#3A3545', // internal dividers
    grid: '#2A2733', // graph gridlines
    text: '#F0EEF8', // primary
    textDim: '#B9B5C8', // labels
    textMute: '#8F8AA3', // values, hints
    accent: '#7F77DD', // Promptwaffle purple
    onAccent: '#1A172E', // text on accent fills
  },

  /** Curve editor channel tabs and scope traces. */
  channel: {
    luma: '#F0EEF8',
    r: '#D96A6A',
    g: '#7FBF9E',
    b: '#7FA8DD',
    warm: '#E0A44C',
  },

  /** Port colours, applied to the custom types on extension registration. */
  port: {
    IMAGE: '#7FBF9E',
    MASK: '#8F8AA3',
    LOOK: '#7F77DD',
    PALETTE: '#E0A44C',
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
    minSize: 11,
  },

  metrics: {
    controlHeight: 26,
    radiusControl: 4,
    radiusPanel: 8,
    radiusNode: 12,
    border: 1,
    borderHair: 0.5,
    padding: 12, // node internal padding
    gapControl: 8, // between sibling controls
    gapSection: 20, // between labelled sections
    /** Slider knob radius and the hit slop around it. Fitts's law, not taste. */
    knob: 5,
    hitSlop: 8,
  },

  /**
   * Drag modifiers. Centralised so every control behaves the same way — the
   * moment one canvas invents its own fine-adjust ratio, muscle memory breaks.
   */
  interaction: {
    fineDragScale: 0.15, // shift-drag
    doubleClickResets: true,
    /** Hold-to-compare key. Held, not toggled: comparison is a glance. */
    compareKey: 'Alt',
  },
} as const;

/**
 * A section badge tells the user whether a control group bakes into a LUT or
 * only exists at render time. Per the architecture: surface the split, do not
 * pretend it is seamless.
 */
export const BADGE = {
  lut: { label: 'LUT', fill: PW.color.chipActive, text: PW.color.text },
  render: { label: 'render only', fill: PW.color.surface, text: PW.color.textMute },
  approx: { label: 'preview approximate', fill: PW.color.surface, text: PW.channel.warm },
} as const;

/** Emit the palette as CSS custom properties for the DOM overlay elements. */
export function cssVars(): string {
  const lines = Object.entries(PW.color).map(([k, v]) => `  --pw-${kebab(k)}: ${v};`);
  return `:root {\n${lines.join('\n')}\n}`;
}

function kebab(s: string): string {
  return s.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
}
