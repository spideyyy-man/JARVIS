// The Assistant screen's core: the 2D orb, or the 3D core when Settings > Layout > "3D core" is on and WebGL works.
// three.js lives in a separate chunk that is only loaded when the 3D core is shown.
import { Component, lazy, Suspense, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { AssistantState } from "../api";
import { useCore3D } from "../util";
import { Orb } from "./Orb";

const Core3D = lazy(() => import("./Core3D"));
const SIZE = 160;

let webgl: boolean | null = null;
/** Hardware WebGL is available (a software fallback would load the CPU, so it counts as missing). */
export function hasWebGL(): boolean {
  if (webgl !== null) return webgl;
  try {
    const c = document.createElement("canvas");
    const opts = { failIfMajorPerformanceCaveat: true };
    const gl = (c.getContext("webgl2", opts) || c.getContext("webgl", opts)) as WebGLRenderingContext | null;
    webgl = !!gl;
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
  } catch {
    webgl = false;
  }
  return webgl;
}

export function useReducedMotion(): boolean {
  const q = "(prefers-reduced-motion: reduce)";
  const [on, setOn] = useState(() => window.matchMedia?.(q).matches ?? false);
  useEffect(() => {
    const mq = window.matchMedia?.(q);
    if (!mq) return;
    const sync = () => setOn(mq.matches);
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return on;
}

class Fallback extends Component<{ fallback: ReactNode; onError: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    this.props.onError();
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

interface Props {
  state: AssistantState;
  following: boolean;
  approval: boolean;
  timer: { due: number; seconds: number } | null;
  level?: () => number;
}

export function AssistantCore({ level, ...input }: Props) {
  const want = useCore3D();
  const reduced = useReducedMotion();
  const [failed, setFailed] = useState(false); // WebGL failed to start or was lost: the 2D orb for this session
  const orb = <Orb state={input.state} size="md" level={level} />;
  if (!want || failed || !hasWebGL()) return orb;
  const box = (child: ReactNode) => <div className="core3d" style={{ width: SIZE, height: SIZE }}>{child}</div>;
  return (
    <Fallback fallback={orb} onError={() => setFailed(true)}>
      <Suspense fallback={box(orb)}>
        {box(<Core3D {...input} size={SIZE} reduced={reduced} level={level} onFail={() => setFailed(true)} />)}
      </Suspense>
    </Fallback>
  );
}
