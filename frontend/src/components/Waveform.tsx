import { useEffect, useRef } from "react";
import type { AssistantState } from "../api";

interface Props {
  state: AssistantState;
  getWave: () => Float32Array | null;
  getLevel: () => number;
  height?: number;
}

const COLORS: Record<AssistantState, string> = { idle: "#38bdf8", listening: "#22d3ee", thinking: "#38bdf8", speaking: "#7dd3fc", executing: "#fbbf24", error: "#f87171" };

/** Canvas waveform: the real microphone signal when available, otherwise a synthetic one driven by state/level. */
export function Waveform({ state, getWave, getLevel, height = 96 }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const live = useRef({ state, getWave, getLevel });
  live.current = { state, getWave, getLevel };

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let amp = 0.05;
    const draw = (now: number) => {
      const { state: st, getWave: wave, getLevel: lvl } = live.current;
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const mid = h / 2;
      const color = COLORS[st];
      ctx.strokeStyle = "rgba(56,189,248,0.14)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, mid);
      ctx.lineTo(w, mid);
      ctx.stroke();

      const samples = st === "listening" ? wave() : null;
      const t = reduced ? 0 : now / 1000;
      if (samples) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.shadowColor = color;
        ctx.shadowBlur = 10;
        ctx.beginPath();
        const step = samples.length / w;
        for (let x = 0; x <= w; x += 2) {
          const v = samples[Math.min(samples.length - 1, Math.floor(x * step))];
          const edge = Math.sin((x / w) * Math.PI);
          const y = mid + Math.max(-1, Math.min(1, v * 3)) * (mid - 6) * edge;
          if (x === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.shadowBlur = 0;
      } else {
        const target = st === "speaking" ? 0.25 + lvl() * 0.75 : st === "listening" ? 0.3 + 0.18 * Math.sin(t * 2.4) : st === "thinking" ? 0.2 : st === "executing" ? 0.24 : st === "error" ? 0.1 : 0.06;
        amp += (target - amp) * 0.12;
        const speed = st === "thinking" || st === "executing" ? 5 : st === "speaking" ? 7 : st === "listening" ? 3 : 1.2;
        for (let layer = 0; layer < 3; layer++) {
          ctx.strokeStyle = color;
          ctx.globalAlpha = [1, 0.45, 0.22][layer];
          ctx.lineWidth = layer === 0 ? 2 : 1.2;
          ctx.shadowColor = color;
          ctx.shadowBlur = layer === 0 ? 10 : 0;
          ctx.beginPath();
          for (let x = 0; x <= w; x += 3) {
            const p = x / w;
            const edge = Math.sin(p * Math.PI) ** 1.5;
            const y = mid + (Math.sin(p * (9 + layer * 4) + t * speed + layer * 1.7) * 0.6 + Math.sin(p * (23 - layer * 5) - t * speed * 1.4) * 0.4) * amp * (mid - 6) * edge * (1 - layer * 0.22);
            if (x === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          }
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
        ctx.shadowBlur = 0;
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  return <canvas ref={ref} className="waveform" style={{ height }} aria-hidden="true" />;
}
