/**
 * The live preview: the surface the whole lattice architecture exists to serve.
 *
 * Draws the node's own input with the node's lattice applied, using a fragment
 * shader that samples the lattice with **hand-written trilinear interpolation**
 * — the same eight-corner gather as `core/lattice.ts` and `pw_color/lattice.py`.
 *
 * Hardware texture filtering is deliberately not used. `TEXTURE_MIN_FILTER` is
 * NEAREST and every tap is a `texelFetch`, because GPU filtering precision is
 * not something we can pin across vendors, and a preview that differs from the
 * render by a code value is exactly the failure this pack was built to avoid.
 *
 * One WebGL context for the whole page, not one per node. Browsers cap live
 * contexts at around sixteen and silently kill the oldest; a graph with a dozen
 * colour nodes would start losing previews at random.
 */

import { Lattice } from '../core/lattice.ts';
import { PW } from '../theme.ts';
import { fillPanel, text, type Ctx, type Rect } from '../widgets/draw.ts';

const VERT = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main() {
  v_uv = vec2(a_pos.x, 1.0 - a_pos.y);
  gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0);
}`;

const FRAG = `#version 300 es
precision highp float;
precision highp sampler3D;

in vec2 v_uv;
out vec4 outColour;

uniform sampler2D u_image;
uniform sampler3D u_lut;
uniform float u_size;     // lattice edge length
uniform float u_wipe;     // 0 = all original, 1 = all graded
uniform float u_enabled;  // 0 disables the lattice entirely

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
  vec3 src = texture(u_image, v_uv).rgb;
  vec3 graded = u_enabled > 0.5 ? clamp(sampleLattice(src), 0.0, 1.0) : src;
  outColour = vec4(v_uv.x <= u_wipe ? graded : src, 1.0);
}`;

class Renderer {
  readonly canvas: HTMLCanvasElement;
  private gl: WebGL2RenderingContext | null = null;
  private program: WebGLProgram | null = null;
  private lut: WebGLTexture | null = null;
  private uniforms: Record<string, WebGLUniformLocation | null> = {};
  private lutDigest = '';
  failed = false;

  constructor() {
    this.canvas = document.createElement('canvas');
    this.canvas.width = 512;
    this.canvas.height = 512;
  }

  private init(): boolean {
    if (this.gl) return true;
    if (this.failed) return false;
    const gl = this.canvas.getContext('webgl2', { premultipliedAlpha: false, antialias: false });
    if (!gl) {
      this.failed = true;
      console.warn('[PW Color] WebGL2 unavailable, live preview disabled');
      return false;
    }
    const compile = (type: number, src: string) => {
      const s = gl.createShader(type)!;
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        console.error('[PW Color] shader error', gl.getShaderInfoLog(s));
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
    const p = gl.createProgram()!;
    gl.attachShader(p, vs);
    gl.attachShader(p, fs);
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      console.error('[PW Color] link error', gl.getProgramInfoLog(p));
      this.failed = true;
      return false;
    }

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(p, 'a_pos');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    gl.useProgram(p);
    for (const name of ['u_image', 'u_lut', 'u_size', 'u_wipe', 'u_enabled']) {
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
  private uploadLut(lattice: Lattice, digest: string): void {
    const gl = this.gl!;
    if (this.lut && digest === this.lutDigest) return;
    if (!this.lut) this.lut = gl.createTexture();
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_3D, this.lut);
    // NEAREST throughout: the shader does its own interpolation so that it
    // matches torch exactly.
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    for (const axis of [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T, gl.TEXTURE_WRAP_R]) {
      gl.texParameteri(gl.TEXTURE_3D, axis, gl.CLAMP_TO_EDGE);
    }
    const n = lattice.size;
    // Flat storage is red-fastest, which is exactly x-fastest for texImage3D.
    gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGB32F, n, n, n, 0, gl.RGB, gl.FLOAT, lattice.data);
    this.lutDigest = digest;
  }

  render(
    image: TexSource,
    lattice: Lattice | null,
    digest: string,
    w: number,
    h: number,
    wipe: number,
  ): HTMLCanvasElement | null {
    if (!this.init()) return null;
    const gl = this.gl!;
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

    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    return this.canvas;
  }
}

/** An image uploaded once and reused across frames. */
export class TexSource {
  private tex: WebGLTexture | null = null;
  private uploaded = false;
  readonly width: number;
  readonly height: number;

  constructor(private readonly bitmap: ImageBitmap) {
    this.width = bitmap.width;
    this.height = bitmap.height;
  }

  texture(gl: WebGL2RenderingContext): WebGLTexture {
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
    return this.tex!;
  }

  dispose(): void {
    this.bitmap.close?.();
  }
}

let renderer: Renderer | null = null;

function shared(): Renderer {
  if (!renderer) renderer = new Renderer();
  return renderer;
}

/**
 * A node's preview panel: fetches its own input, holds the current lattice,
 * and draws either through `2d` fallback or the shared WebGL renderer.
 */
export class Preview {
  source: TexSource | null = null;
  lattice: Lattice | null = null;
  digest = '';
  /** 0 shows the original across the whole panel; 1 shows the grade. */
  wipe = 1;
  /** True while the compare key is held. */
  comparing = false;
  private loading = false;
  private failedFetch = false;

  /** Pull this node's cached input from the preview route. */
  async load(nodeId: string | number, onReady: () => void): Promise<void> {
    if (this.loading || this.failedFetch) return;
    this.loading = true;
    try {
      const res = await fetch(`/pw_color/input/${nodeId}`);
      if (!res.ok) {
        // 404 simply means the graph has not run yet.
        this.loading = false;
        return;
      }
      const bmp = await createImageBitmap(await res.blob());
      this.source?.dispose();
      this.source = new TexSource(bmp);
      onReady();
    } catch {
      this.failedFetch = true;
    } finally {
      this.loading = false;
    }
  }

  get hasImage(): boolean {
    return this.source !== null;
  }

  draw(ctx: Ctx, r: Rect): void {
    fillPanel(ctx, r, PW.color.well, PW.metrics.radiusPanel, PW.color.border);
    if (!this.source) {
      text(ctx, 'Run the graph once to preview', r.x + r.w / 2, r.y + r.h / 2, {
        colour: PW.color.textMute,
        align: 'center',
      });
      return;
    }

    // Cover-fit inside the panel, then clip: letterboxing wastes the little
    // vertical space a node has.
    const scale = Math.max(r.w / this.source.width, r.h / this.source.height);
    const dw = Math.round(this.source.width * scale);
    const dh = Math.round(this.source.height * scale);
    const wipe = this.comparing ? 0 : this.wipe;

    const out = shared().render(this.source, this.lattice, this.digest, dw, dh, wipe);

    ctx.save();
    fillPanel(ctx, r, PW.color.well, PW.metrics.radiusPanel);
    ctx.clip();
    if (out) {
      ctx.drawImage(out, r.x + (r.w - dw) / 2, r.y + (r.h - dh) / 2, dw, dh);
    } else {
      text(ctx, 'WebGL2 unavailable', r.x + r.w / 2, r.y + r.h / 2, {
        colour: PW.color.textMute,
        align: 'center',
      });
    }
    ctx.restore();

    // Wipe handle, only when the wipe is actually in play.
    if (!this.comparing && this.wipe > 0.001 && this.wipe < 0.999) {
      const x = r.x + this.wipe * r.w;
      ctx.strokeStyle = PW.color.accent;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + 0.5, r.y);
      ctx.lineTo(Math.round(x) + 0.5, r.y + r.h);
      ctx.stroke();
    }
    if (this.comparing) {
      text(ctx, 'before', r.x + 8, r.y + 12, { colour: PW.color.accent });
    }
  }

  /** @returns true if the drag was consumed. */
  onPointer(x: number, r: Rect, dragging: boolean): boolean {
    if (!dragging || !this.source) return false;
    this.wipe = Math.min(1, Math.max(0, (x - r.x) / r.w));
    return true;
  }
}
