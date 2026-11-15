import { useEffect, useRef } from "react";
import type { AssistantState } from "../api";

interface Props {
  state: AssistantState;
  size?: "sm" | "md" | "lg";
  /** Optional live amplitude source (0..1) for speaking/listening. */
  level?: () => number;
}

/** The assistant orb. Each state is visibly distinct; see orb rules in app.css. */
export function Orb({ state, size = "sm", level }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || !level || (state !== "speaking" && state !== "listening")) {
      el?.style.removeProperty("--amp");
      return;
    }
    let raf = 0;
    let smooth = 0;
    const tick = () => {
      smooth += (level() - smooth) * 0.35;
      el.style.setProperty("--amp", smooth.toFixed(3));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [state, level]);

  return (
    <div ref={ref} className={`orb orb-${size}${level ? " orb-reactive" : ""}`} data-state={state} role="img" aria-label={`Assistant is ${state}`}>
      <span className="orb-halo" />
      <span className="orb-pulse p1" />
      <span className="orb-pulse p2" />
      <span className="orb-pulse p3" />
      <svg className="orb-rings" viewBox="0 0 100 100" aria-hidden="true">
        <circle className="ring-outer" cx="50" cy="50" r="47.5" />
        <circle className="ring-mid" cx="50" cy="50" r="40" />
        <circle className="ring-seg" cx="50" cy="50" r="44" />
        <circle className="ring-sweep" cx="50" cy="50" r="44" />
        <circle className="ring-arc a1" cx="50" cy="50" r="44" />
        <circle className="ring-arc a2" cx="50" cy="50" r="36" />
      </svg>
      <span className="orb-core" />
    </div>
  );
}
