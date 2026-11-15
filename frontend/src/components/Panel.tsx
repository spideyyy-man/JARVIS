import type { ReactNode } from "react";

interface Props {
  title?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
  scroll?: boolean;
  testid?: string;
}

export function Panel({ title, icon, actions, children, className = "", style, scroll, testid }: Props) {
  return (
    <section className={`panel ${className}`} style={style} data-testid={testid}>
      {(title || actions) && (
        <header className="panel-head">
          {icon}
          <h2 className="label grow truncate">{title}</h2>
          {actions}
        </header>
      )}
      <div className={`panel-body${scroll ? " scroll" : ""}`}>{children}</div>
    </section>
  );
}

export function ScreenHead({ title, sub, children }: { title: string; sub: string; children?: ReactNode }) {
  return (
    <div className="screen-head">
      <div className="grow">
        <h1>{title}</h1>
        <p className="soft">{sub}</p>
      </div>
      {children}
    </div>
  );
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return <button type="button" role="switch" className="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)} />;
}

export function StatusBadge({ status }: { status: string }) {
  const tone =
    /^(done|ok|executed|succeeded|approved|indexed)$/.test(status) ? "green"
    : /^(failed|error|denied)$/.test(status) ? "red"
    : /^(pending|waiting_approval|running|in_progress|queued)$/.test(status) ? "amber"
    : "";
  return <span className={`badge ${tone}`}>{status.replace(/_/g, " ")}</span>;
}

export function Loading({ error, loading, empty, children }: { error: string | null; loading: boolean; empty?: boolean; children?: ReactNode }) {
  if (error) return <div className="error-text" role="alert">{error}</div>;
  if (loading && empty) return <div className="empty">Loading…</div>;
  return <>{children}</>;
}
