// The optional 3D core of the Assistant screen (Settings > Layout > "3D core"). Loaded on demand, so three.js is only
// fetched when the setting is on. Our own design: a fresnel glass shell around a glowing nucleus, a geodesic lattice,
// three tilted gyro rings and a particle swarm, with ripples, comets and a timer ring that follow the assistant state.
// Budget (the laptop's CPU is often full): one small canvas, <= 30 fps (10-15 when idle), no frames while the page is
// hidden or off-screen, and only redraws on change when the user prefers reduced motion.
import { useEffect, useRef } from "react";
import {
  AdditiveBlending, BufferAttribute, BufferGeometry, CanvasTexture, Color, DoubleSide, EdgesGeometry, Group, IcosahedronGeometry,
  LineBasicMaterial, LineSegments, Mesh, MeshBasicMaterial, PerspectiveCamera, Points, RingGeometry, Scene, ShaderMaterial, Sprite,
  SpriteMaterial, TorusGeometry, WebGLRenderer,
} from "three";
import type { AssistantState } from "../api";

export interface CoreInput {
  state: AssistantState;
  following: boolean; // the offline listener's conversation window is open (no wake word needed)
  approval: boolean; // an action is waiting for the user's approval
  timer: { due: number; seconds: number } | null; // the timer that rings next
}
interface Props extends CoreInput {
  size: number;
  reduced: boolean; // prefers-reduced-motion: no motion, redraw only when something changes
  level?: () => number; // live amplitude 0..1 (the page's own speech or the microphone)
  onFail: () => void; // WebGL could not start or was lost: the caller shows the 2D orb
}

type Mode = "idle" | "listening" | "following" | "thinking" | "executing" | "speaking" | "approval" | "error";
interface Look {
  c1: number; c2: number; // main and secondary colour
  spin: number; // gyro ring speed (rad/s)
  orbit: number; // particle swarm speed (rad/s)
  breathe: number; // slow breathing depth
  ripple: number; rippleRate: number; // ripples out of the core (strength, rings per second)
  comet: number; cometRate: number; // bright comet running round the rings (strength, turns per second)
  step: number; // 1 = the inner ring ticks round in steps (working)
  glow: number;
}
const LOOKS: Record<Mode, Look> = {
  idle: { c1: 0x22d3ee, c2: 0x2563eb, spin: 0.16, orbit: 0.05, breathe: 1, ripple: 0, rippleRate: 0.5, comet: 0, cometRate: 0.3, step: 0, glow: 0.8 },
  listening: { c1: 0x67e8f9, c2: 0x22d3ee, spin: 0.35, orbit: 0.12, breathe: 0.25, ripple: 1, rippleRate: 0.8, comet: 0, cometRate: 0.3, step: 0, glow: 1.15 },
  following: { c1: 0x4ade80, c2: 0x22d3ee, spin: 0.22, orbit: 0.07, breathe: 0.5, ripple: 0.75, rippleRate: 0.4, comet: 0, cometRate: 0.3, step: 0, glow: 1 },
  thinking: { c1: 0xa78bfa, c2: 0x6366f1, spin: 1.5, orbit: 0.45, breathe: 0.2, ripple: 0, rippleRate: 0.5, comet: 1, cometRate: 0.9, step: 0, glow: 1 },
  executing: { c1: 0x2dd4bf, c2: 0x0ea5e9, spin: 1.0, orbit: 0.3, breathe: 0.2, ripple: 0, rippleRate: 0.5, comet: 0.6, cometRate: 0.6, step: 1, glow: 1 },
  speaking: { c1: 0x7dd3fc, c2: 0x0ea5e9, spin: 0.4, orbit: 0.14, breathe: 0, ripple: 0, rippleRate: 0.5, comet: 0, cometRate: 0.3, step: 0, glow: 1.15 },
  approval: { c1: 0xfbbf24, c2: 0xf59e0b, spin: 0.08, orbit: 0.03, breathe: 0.6, ripple: 0, rippleRate: 0.5, comet: 1.3, cometRate: 0.45, step: 0, glow: 0.95 },
  error: { c1: 0xf87171, c2: 0xb91c1c, spin: 0.05, orbit: 0.02, breathe: 0, ripple: 0, rippleRate: 0.5, comet: 0, cometRate: 0.3, step: 0, glow: 0.9 },
};
const AMBER = new Color(0xfbbf24);
const TAU = Math.PI * 2;

export function modeOf(i: CoreInput): Mode {
  if (i.state === "error") return "error";
  if (i.state === "speaking") return "speaking";
  if (i.state === "listening") return "listening";
  if (i.approval) return "approval";
  if (i.state === "thinking" || i.state === "executing") return i.state;
  return i.following ? "following" : "idle";
}

// -- shaders ----------------------------------------------------------------------------------------------------
const SHELL_VS = `
varying vec3 vN; varying vec3 vV; varying float vY;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vN = normalize(normalMatrix * normal); vV = normalize(-mv.xyz); vY = position.y;
  gl_Position = projectionMatrix * mv;
}`;
const SHELL_FS = `
uniform vec3 uC1; uniform vec3 uC2; uniform float uTime; uniform float uGlow;
varying vec3 vN; varying vec3 vV; varying float vY;
void main() {
  float f = pow(1.0 - max(dot(normalize(vN), normalize(vV)), 0.0), 2.4);
  float bands = 0.5 + 0.5 * sin(vY * 34.0 - uTime * 1.6);
  float a = (f * (0.7 + 0.3 * bands) + 0.05) * uGlow;
  gl_FragColor = vec4(mix(uC2, uC1, f), a);
}`;
const RING_VS = `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const RING_FS = `
uniform vec3 uColor; uniform vec3 uCometColor; uniform float uOpacity; uniform float uDash; uniform float uFill;
uniform float uHead; uniform float uComet; uniform float uTail;
varying vec2 vUv;
void main() {
  float dash = uDash > 0.5 ? step(fract(vUv.x * uDash), uFill) : 1.0;
  float behind = fract(uHead - vUv.x);
  float comet = pow(1.0 - behind, uTail) * uComet;
  gl_FragColor = vec4(mix(uColor, uCometColor, clamp(comet, 0.0, 1.0)), uOpacity * dash + comet);
}`;
const DOTS_VS = `
uniform float uTime; uniform float uPulse; uniform float uSize;
attribute float aSeed;
varying float vA;
void main() {
  float wobble = 0.03 * sin(uTime * 0.9 + aSeed * 12.0) + uPulse * 0.14 * sin(uTime * 7.0 + aSeed * 6.283);
  vec4 mv = modelViewMatrix * vec4(position * (1.0 + wobble), 1.0);
  gl_PointSize = uSize * (0.6 + 0.9 * fract(aSeed * 7.13)) * (4.6 / -mv.z);
  vA = 0.3 + 0.7 * fract(aSeed * 3.7);
  gl_Position = projectionMatrix * mv;
}`;
const DOTS_FS = `
uniform vec3 uColor; uniform float uOpacity;
varying float vA;
void main() {
  float a = smoothstep(0.5, 0.0, length(gl_PointCoord - 0.5));
  gl_FragColor = vec4(uColor, a * a * vA * uOpacity);
}`;
const TIMER_VS = `varying vec2 vP; void main() { vP = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const TIMER_FS = `
uniform vec3 uColor; uniform float uProgress; uniform float uOpacity;
varying vec2 vP;
void main() {
  float a = atan(vP.x, vP.y);
  float f = (a < 0.0 ? a + 6.2831853 : a) / 6.2831853; // 0 at the top, clockwise
  float on = step(f, uProgress);
  gl_FragColor = vec4(uColor, mix(0.12, 0.9, on) * uOpacity);
}`;

function glowTexture(): CanvasTexture {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.25, "rgba(255,255,255,0.55)");
  grad.addColorStop(0.6, "rgba(255,255,255,0.12)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  return new CanvasTexture(c);
}

const additive = { transparent: true, depthWrite: false, blending: AdditiveBlending } as const;

function ringMaterial(dash: number, fill: number, opacity: number, tail: number): ShaderMaterial {
  return new ShaderMaterial({
    vertexShader: RING_VS, fragmentShader: RING_FS, ...additive,
    uniforms: {
      uColor: { value: new Color() }, uCometColor: { value: new Color() }, uOpacity: { value: opacity }, uDash: { value: dash },
      uFill: { value: fill }, uHead: { value: 0 }, uComet: { value: 0 }, uTail: { value: tail },
    },
  });
}

/** Builds the scene once; `frame` advances it by dt seconds for the given input and draws it. */
function buildCore(canvas: HTMLCanvasElement, size: number) {
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "low-power", failIfMajorPerformanceCaveat: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.setSize(size, size, false);
  renderer.setClearColor(0x000000, 0);

  const scene = new Scene();
  const camera = new PerspectiveCamera(35, 1, 0.1, 20);
  camera.position.set(0, 0, 4.6);

  const root = new Group(); // shakes on error
  scene.add(root);
  const body = new Group(); // tilted so the rings read as 3D
  body.rotation.set(0.38, 0, -0.12);
  root.add(body);

  const tex = glowTexture();
  const halo = new Sprite(new SpriteMaterial({ map: tex, color: 0xffffff, ...additive, opacity: 0.32 }));
  halo.scale.setScalar(3.1);
  root.add(halo);
  const nucleus = new Sprite(new SpriteMaterial({ map: tex, color: 0xffffff, ...additive, opacity: 1 }));
  nucleus.scale.setScalar(0.95);
  body.add(nucleus);

  const shellMat = new ShaderMaterial({
    vertexShader: SHELL_VS, fragmentShader: SHELL_FS, ...additive,
    uniforms: { uC1: { value: new Color() }, uC2: { value: new Color() }, uTime: { value: 0 }, uGlow: { value: 1 } },
  });
  const shell = new Mesh(new IcosahedronGeometry(0.56, 3), shellMat);
  body.add(shell);

  const latticeMat = new LineBasicMaterial({ color: 0xffffff, ...additive, opacity: 0.3 });
  const lattice = new LineSegments(new EdgesGeometry(new IcosahedronGeometry(0.64, 1)), latticeMat);
  body.add(lattice);

  // Gyro rings: an inner dashed ring, a fine solid ring and an outer ring of tick marks (the comet/sweep runs here).
  const rings = [
    { mat: ringMaterial(40, 0.62, 0.55, 5), radius: 0.86, tube: 0.011, tilt: [1.25, 0.2, 0], axis: 1 },
    { mat: ringMaterial(0, 1, 0.28, 7), radius: 0.97, tube: 0.006, tilt: [-0.95, 0.55, 0], axis: -1 },
    { mat: ringMaterial(90, 0.38, 0.5, 3), radius: 1.08, tube: 0.013, tilt: [Math.PI / 2, 0, 0], axis: 1 },
  ].map((r) => {
    const holder = new Group();
    holder.rotation.set(r.tilt[0], r.tilt[1], r.tilt[2]);
    const mesh = new Mesh(new TorusGeometry(r.radius, r.tube, 6, 160), r.mat);
    holder.add(mesh);
    body.add(holder);
    return { ...r, mesh, angle: 0, head: Math.random() };
  });

  // Particle swarm on a shell around the core.
  const N = 320;
  const pos = new Float32Array(N * 3);
  const seed = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const u = Math.random() * 2 - 1, th = Math.random() * TAU, r = 1.0 + Math.random() * 0.32, s = Math.sqrt(1 - u * u);
    pos.set([r * s * Math.cos(th), r * u, r * s * Math.sin(th)], i * 3);
    seed[i] = Math.random();
  }
  const dotsGeo = new BufferGeometry();
  dotsGeo.setAttribute("position", new BufferAttribute(pos, 3));
  dotsGeo.setAttribute("aSeed", new BufferAttribute(seed, 1));
  const dotsMat = new ShaderMaterial({
    vertexShader: DOTS_VS, fragmentShader: DOTS_FS, ...additive,
    uniforms: { uTime: { value: 0 }, uPulse: { value: 0 }, uSize: { value: 2.4 * renderer.getPixelRatio() }, uColor: { value: new Color() }, uOpacity: { value: 0.9 } },
  });
  const dots = new Points(dotsGeo, dotsMat);
  body.add(dots);

  // Ripples (listening / following) and the timer ring face the camera.
  const rippleGeo = new RingGeometry(0.965, 1.0, 96);
  const ripples = [0, 1, 2].map(() => {
    const m = new Mesh(rippleGeo, new MeshBasicMaterial({ color: 0xffffff, ...additive, opacity: 0, side: DoubleSide }));
    m.visible = false;
    root.add(m);
    return m;
  });
  const timerMat = new ShaderMaterial({
    vertexShader: TIMER_VS, fragmentShader: TIMER_FS, ...additive,
    uniforms: { uColor: { value: new Color() }, uProgress: { value: 1 }, uOpacity: { value: 0 } },
  });
  const timerRing = new Mesh(new RingGeometry(1.3, 1.34, 128), timerMat);
  timerRing.visible = false;
  root.add(timerRing);

  // Animated values, eased towards the current look.
  const cur = { ...LOOKS.idle, c1: new Color(LOOKS.idle.c1), c2: new Color(LOOKS.idle.c2), timer: 0, amp: 0 };
  const tmp1 = new Color(), tmp2 = new Color();
  let t = 0, rippleClock = 0, orbitAngle = 0, mode: Mode = "idle", modeSince = 0;

  function frame(dt: number, input: CoreInput, level: number, snap: boolean) {
    const m = modeOf(input);
    if (m !== mode) { mode = m; modeSince = t; }
    const look = LOOKS[m];
    const k = snap ? 1 : 1 - Math.exp(-dt * 4);
    cur.c1.lerp(tmp1.set(look.c1), k);
    cur.c2.lerp(tmp2.set(look.c2), k);
    for (const key of ["spin", "orbit", "breathe", "ripple", "rippleRate", "comet", "cometRate", "step", "glow"] as const) cur[key] += (look[key] - cur[key]) * k;
    t += dt;

    // speaking/listening amplitude; while the server speaks (no level here) a soft synthetic babble
    let target = m === "speaking" || m === "listening" ? level : 0;
    if (m === "speaking" && target < 0.05) target = 0.45 + 0.28 * Math.sin(t * 11.3) * Math.sin(t * 4.1) + 0.14 * Math.sin(t * 2.3);
    cur.amp += (target - cur.amp) * (snap ? 1 : 1 - Math.exp(-dt * 14));

    const breath = 0.5 + 0.5 * Math.sin((t * TAU) / 4.6);
    const coreScale = 1 + 0.06 * breath * cur.breathe + cur.amp * 0.28;
    shell.scale.setScalar(coreScale);
    lattice.scale.setScalar(1 + cur.amp * 0.12);
    lattice.rotation.y += dt * (0.1 + cur.spin * 0.15);
    lattice.rotation.x += dt * 0.03;
    const glow = cur.glow * (0.82 + 0.3 * breath * cur.breathe) + cur.amp * 0.7;
    shellMat.uniforms.uC1.value.copy(cur.c1);
    shellMat.uniforms.uC2.value.copy(cur.c2);
    shellMat.uniforms.uTime.value = t;
    shellMat.uniforms.uGlow.value = glow * (m === "error" ? 0.75 + 0.25 * Math.sin(t * 23) : 1);
    nucleus.material.color.copy(cur.c1).lerp(tmp1.set(0xffffff), 0.45);
    nucleus.scale.setScalar(0.85 * coreScale + cur.amp * 0.35);
    halo.material.color.copy(cur.c1);
    halo.material.opacity = 0.22 + 0.12 * glow;
    halo.scale.setScalar(3.0 + cur.amp * 0.8);
    latticeMat.color.copy(cur.c1);

    rings.forEach((r, i) => {
      r.angle += dt * cur.spin * r.axis * (1 + i * 0.35);
      const stepped = Math.round(r.angle / (TAU / 40)) * (TAU / 40);
      r.mesh.rotation.z = i === 0 && cur.step > 0.5 ? stepped : r.angle;
      const u = r.mat.uniforms;
      u.uColor.value.copy(i === 1 ? cur.c2 : cur.c1);
      // the outer ring's sweep turns amber while an approval waits, whatever else is going on
      const sweep = i === 2 ? Math.max(cur.comet, input.approval ? 1 : 0) : i === 1 ? cur.comet * 0.8 : 0;
      r.head = (r.head + dt * cur.cometRate * (i === 1 ? -1.3 : 1)) % 1;
      u.uHead.value = r.head < 0 ? r.head + 1 : r.head;
      u.uComet.value = sweep;
      u.uCometColor.value.copy(input.approval && i === 2 ? AMBER : tmp1.copy(cur.c1).lerp(tmp2.set(0xffffff), 0.5));
    });

    orbitAngle += dt * cur.orbit;
    dots.rotation.y = orbitAngle;
    dots.rotation.z = orbitAngle * 0.3;
    dotsMat.uniforms.uTime.value = t;
    dotsMat.uniforms.uPulse.value = cur.amp;
    dotsMat.uniforms.uColor.value.copy(cur.c1).lerp(tmp1.set(0xffffff), 0.25);

    rippleClock += dt * cur.rippleRate;
    ripples.forEach((rp, i) => {
      const ph = (rippleClock + i / 3) % 1;
      const a = Math.pow(1 - ph, 1.6) * 0.55 * cur.ripple;
      rp.visible = a > 0.01;
      rp.scale.setScalar(0.5 + ph * 0.9);
      rp.material.opacity = a;
      rp.material.color.copy(cur.c1);
    });

    const now = Date.now() / 1000;
    const left = input.timer ? Math.max(0, Math.min(1, (input.timer.due - now) / input.timer.seconds)) : 0;
    cur.timer += ((input.timer ? 1 : 0) - cur.timer) * (snap ? 1 : 1 - Math.exp(-dt * 5));
    timerRing.visible = cur.timer > 0.01;
    timerMat.uniforms.uProgress.value = left;
    timerMat.uniforms.uOpacity.value = cur.timer;
    timerMat.uniforms.uColor.value.set(left < 0.15 ? 0xfbbf24 : 0xe0f2fe);

    const shake = m === "error" ? Math.max(0, 1 - (t - modeSince) / 0.6) : 0;
    root.position.x = Math.sin(t * 42) * 0.06 * shake;

    renderer.render(scene, camera);
  }

  function dispose() {
    scene.traverse((o) => {
      const any = o as unknown as { geometry?: { dispose(): void }; material?: { dispose(): void } };
      any.geometry?.dispose();
      any.material?.dispose();
    });
    tex.dispose();
    renderer.dispose();
  }
  return { frame, dispose };
}

// -- measurement hook -------------------------------------------------------------------------------------------
// window.__jarvisCore3d.stats() -> frame time and rate since the last reset(); preview({...}) overrides the input
// (e.g. { state: "thinking" }) so every look can be checked and measured without driving the assistant; preview(null)
// clears it. Read-only towards JARVIS: it changes nothing but this drawing.
interface Stats { frames: number; ms: number[]; since: number; paused: boolean }
declare global {
  interface Window { __jarvisCore3d?: { stats: () => Record<string, number | boolean | string>; reset: () => void; preview: (p: Partial<CoreInput> | null) => void } }
}

export default function Core3D({ size, reduced, level, onFail, ...input }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const inputRef = useRef<CoreInput>(input);
  inputRef.current = input;
  const levelRef = useRef(level);
  levelRef.current = level;
  const redraw = useRef<() => void>(() => undefined);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let core: ReturnType<typeof buildCore>;
    try {
      core = buildCore(canvas, size);
    } catch {
      onFail();
      return;
    }
    let preview: Partial<CoreInput> | null = null;
    const stats: Stats = { frames: 0, ms: [], since: performance.now(), paused: false };
    const current = (): CoreInput => (preview ? { ...inputRef.current, ...preview } : inputRef.current);
    let raf = 0, wait = 0, last = 0, onScreen = true, timerTick = 0, active = false;

    const draw = (dt: number, snap: boolean) => {
      const t0 = performance.now();
      core.frame(dt, current(), levelRef.current?.() ?? 0, snap);
      stats.frames++;
      if (stats.ms.length >= 600) stats.ms.shift();
      stats.ms.push(performance.now() - t0);
    };
    // Each drawn frame costs Edge far more than our own script (compositing, the GPU process), so the frame rate is
    // the budget: 30 fps while something is happening, 15 when idle (its motion is slow) and 10 when idle in a window
    // without focus. A timer waits out the interval and only then asks for one display frame, so the browser is not
    // asked for frames that would be skipped. Times come from performance.now(): under load the frame timestamp lags.
    const loop = () => {
      raf = 0;
      if (!active) return;
      const t0 = performance.now();
      const interval = 1000 / (modeOf(current()) !== "idle" ? 30 : document.hasFocus() ? 15 : 10);
      if (t0 - last < interval - 4) { raf = requestAnimationFrame(loop); return; } // early: one more display frame
      draw(Math.min(t0 - last, 100) / 1000, false);
      last = t0;
      wait = window.setTimeout(() => { raf = requestAnimationFrame(loop); }, Math.max(0, interval - (performance.now() - t0) - 12));
    };
    const running = () => !document.hidden && onScreen;
    const start = () => {
      stats.paused = !running();
      if (reduced || active || !running()) return;
      active = true;
      last = performance.now();
      raf = requestAnimationFrame(loop);
    };
    const stop = () => {
      active = false;
      cancelAnimationFrame(raf);
      window.clearTimeout(wait);
      raf = wait = 0;
      stats.paused = true;
    };
    const sync = () => (running() ? start() : stop());
    // Reduced motion: a still picture, redrawn when the input changes and once a second while a timer runs.
    redraw.current = () => { if (reduced && running()) draw(0, true); };
    if (reduced) {
      draw(0, true);
      timerTick = window.setInterval(() => current().timer && redraw.current(), 1000);
    }

    document.addEventListener("visibilitychange", sync);
    const io = new IntersectionObserver((entries) => {
      onScreen = entries.some((e) => e.isIntersecting);
      sync();
    });
    io.observe(canvas);
    const lost = (e: Event) => {
      e.preventDefault();
      stop();
      onFail();
    };
    canvas.addEventListener("webglcontextlost", lost);
    start();

    window.__jarvisCore3d = {
      stats: () => {
        const sorted = [...stats.ms].sort((a, b) => a - b);
        const secs = (performance.now() - stats.since) / 1000;
        return {
          mode: modeOf(current()), frames: stats.frames, fps: +(stats.frames / secs).toFixed(1), seconds: +secs.toFixed(1),
          avgMs: +(sorted.reduce((a, b) => a + b, 0) / (sorted.length || 1)).toFixed(3),
          p95Ms: +(sorted[Math.floor(sorted.length * 0.95)] ?? 0).toFixed(3), maxMs: +(sorted[sorted.length - 1] ?? 0).toFixed(3),
          paused: stats.paused, hidden: document.hidden, reduced,
        };
      },
      reset: () => Object.assign(stats, { frames: 0, ms: [], since: performance.now() }),
      preview: (p) => { preview = p; redraw.current(); },
    };

    return () => {
      stop();
      window.clearInterval(timerTick);
      document.removeEventListener("visibilitychange", sync);
      io.disconnect();
      canvas.removeEventListener("webglcontextlost", lost);
      delete window.__jarvisCore3d;
      core.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, reduced]);

  // reduced motion: draw the new state once
  useEffect(() => redraw.current(), [input.state, input.following, input.approval, input.timer?.due, input.timer?.seconds]);

  return <canvas ref={canvasRef} className="core3d-canvas" style={{ width: size, height: size }} role="img" aria-label={`Assistant is ${modeOf(input)}`} data-testid="core3d" data-mode={modeOf(input)} />;
}
