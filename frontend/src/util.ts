import { useEffect, useState } from "react";
import type { Schedule } from "./api";

export const pad = (n: number) => String(n).padStart(2, "0");
export function clock(ts: number): string {
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
export function dateTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function span(sec: number): string {
  if (sec < 60) return `${Math.max(0, Math.round(sec))}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h`;
  return `${Math.round(sec / 86400)}d`;
}
export function ago(ts: number | null | undefined): string {
  if (!ts) return "never";
  const s = Date.now() / 1000 - ts;
  return s < 5 ? "just now" : `${span(s)} ago`;
}
export function until(ts: number | null | undefined): string {
  if (!ts) return "—";
  const s = ts - Date.now() / 1000;
  return s <= 0 ? "due now" : `in ${span(s)}`;
}
export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
export function scheduleText(s: Schedule): string {
  if (s.type === "interval") {
    const sec = Number(s.seconds ?? 0);
    return sec % 3600 === 0 && sec >= 3600 ? `every ${sec / 3600} h` : sec % 60 === 0 && sec >= 60 ? `every ${sec / 60} min` : `every ${sec} s`;
  }
  if (s.type === "cron") return `cron ${s.expr ?? ""}`;
  if (s.type === "once") return `once at ${dateTime(s.at)}`;
  return "manual";
}
export const basename = (p: string) => p.replace(/\/+$/, "").split("/").pop() || p;
export const pretty = (v: unknown) => {
  if (typeof v === "string") {
    try {
      return JSON.stringify(JSON.parse(v), null, 2);
    } catch {
      return v;
    }
  }
  return JSON.stringify(v, null, 2);
};

/** Hash routing: "#/conversations/c_123" -> ["conversations", "c_123"]. */
export function useRoute(): string[] {
  const read = () => location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [parts, setParts] = useState(read);
  useEffect(() => {
    const on = () => setParts(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parts;
}
export const go = (path: string) => {
  location.hash = path.startsWith("#") ? path : `#/${path.replace(/^\//, "")}`;
};

/** Re-render every `ms` so relative times stay fresh. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}
