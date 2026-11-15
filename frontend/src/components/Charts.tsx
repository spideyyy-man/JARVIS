// Hand-rolled SVG charts: Sparkline, LineChart (with axis), Gauge, Bar.
import { useId } from "react";

export const levelColor = (pct: number) => (pct >= 90 ? "var(--red)" : pct >= 70 ? "var(--amber)" : "var(--cyan)");

function path(values: number[], w: number, h: number, max: number, pad = 2): { line: string; area: string } {
  if (values.length === 0) return { line: "", area: "" };
  const n = Math.max(values.length - 1, 1);
  const pts = values.map((v, i) => [(i / n) * w, h - pad - (Math.min(v, max) / max) * (h - pad * 2)] as const);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return { line, area: `${line} L${w},${h} L0,${h} Z` };
}

export function Sparkline({ values, color = "var(--cyan)", height = 36, max = 100, label }: { values: number[]; color?: string; height?: number; max?: number; label: string }) {
  const id = useId();
  const w = 200;
  const { line, area } = path(values, w, height, max);
  return (
    <svg className="sparkline" viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" style={{ height }} role="img" aria-label={label}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.32" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {values.length > 1 && (
        <>
          <path d={area} fill={`url(#${id})`} />
          <path d={line} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        </>
      )}
    </svg>
  );
}

export function LineChart({ values, color = "var(--cyan)", height = 150, label, span }: { values: number[]; color?: string; height?: number; label: string; span?: string }) {
  const id = useId();
  const w = 600;
  const { line, area } = path(values, w, height, 100, 4);
  const last = values.length ? values[values.length - 1] : null;
  return (
    <div className="linechart" style={{ height }}>
      <div className="linechart-axis num" aria-hidden="true">
        <span>100</span>
        <span>50</span>
        <span>0</span>
      </div>
      <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" role="img" aria-label={`${label}${last !== null ? `, currently ${last.toFixed(1)} percent` : ""}`}>
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={color} stopOpacity="0.3" />
            <stop offset="1" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1="0" x2={w} y1={height * f} y2={height * f} stroke="rgba(56,189,248,0.1)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        ))}
        {values.length > 1 && (
          <>
            <path d={area} fill={`url(#${id})`} />
            <path d={line} fill="none" stroke={color} strokeWidth="1.8" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          </>
        )}
      </svg>
      {span && <span className="linechart-span dim">{span}</span>}
    </div>
  );
}

export function Gauge({ value, label, sub }: { value: number | null; label: string; sub?: string }) {
  const r = 40;
  const circ = 2 * Math.PI * r;
  const sweep = 0.75; // 270 degree arc
  const v = Math.max(0, Math.min(100, value ?? 0));
  const color = levelColor(v);
  return (
    <div className="gauge" role="img" aria-label={`${label} ${value === null ? "unknown" : `${v.toFixed(0)} percent`}`}>
      <svg viewBox="0 0 100 100">
        <g transform="rotate(135 50 50)">
          <circle cx="50" cy="50" r={r} fill="none" stroke="rgba(56,189,248,0.13)" strokeWidth="7" strokeLinecap="round" strokeDasharray={`${circ * sweep} ${circ}`} />
          <circle cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="7" strokeLinecap="round" strokeDasharray={`${Math.max(0.01, (circ * sweep * v) / 100)} ${circ}`} style={{ transition: "stroke-dasharray .6s ease, stroke .3s", filter: `drop-shadow(0 0 4px ${color})` }} />
        </g>
        <text x="50" y="50" textAnchor="middle" dominantBaseline="central" className="gauge-value">
          {value === null ? "—" : v.toFixed(0)}
          <tspan className="gauge-unit" dx="1" dy="-1">%</tspan>
        </text>
        <text x="50" y="84" textAnchor="middle" className="gauge-label">{label}</text>
      </svg>
      {sub && <div className="gauge-sub num">{sub}</div>}
    </div>
  );
}

export function Bar({ value, color }: { value: number; color?: string }) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className="bar" role="img" aria-label={`${v.toFixed(0)} percent`}>
      <span style={{ width: `${v}%`, background: color ?? levelColor(v) }} />
    </div>
  );
}
