import { useEffect, useState } from "react";
import {
  Activity, Bell, BookOpen, Bot, Code2, FlaskConical, Home as HomeIcon, Lock, MessageSquare, Mic, MonitorCog, Settings as SettingsIcon,
  ShieldAlert, ShieldCheck, Workflow, X, WifiOff, CloudOff, Cpu,
} from "lucide-react";
import { api } from "./api";
import { dismissToast, setState, usePendingApprovals, useAssistantState, useStore } from "./store";
import { ago, go, pad, useNow, useRoute } from "./util";
import { Orb } from "./components/Orb";
import { ApprovalCard } from "./components/ApprovalCard";
import { Home } from "./screens/Home";
import { Voice } from "./screens/Voice";
import { Conversations } from "./screens/Conversations";
import { Agents } from "./screens/Agents";
import { Computer } from "./screens/Computer";
import { Coding } from "./screens/Coding";
import { Knowledge } from "./screens/Knowledge";
import { Research } from "./screens/Research";
import { Automation } from "./screens/Automation";
import { SettingsScreen } from "./screens/Settings";

const NAV = [
  { id: "home", label: "Command Center", icon: HomeIcon },
  { id: "voice", label: "Voice", icon: Mic },
  { id: "conversations", label: "Conversations", icon: MessageSquare },
  { id: "agents", label: "Agents", icon: Bot },
  { id: "computer", label: "Computer", icon: MonitorCog },
  { id: "coding", label: "Coding", icon: Code2 },
  { id: "knowledge", label: "Knowledge", icon: BookOpen },
  { id: "research", label: "Research", icon: FlaskConical },
  { id: "automation", label: "Automation", icon: Workflow },
  { id: "settings", label: "Settings", icon: SettingsIcon },
] as const;

function LockedGate() {
  const [token, setToken] = useState("");
  return (
    <main className="gate" data-testid="locked-gate">
      <form
        className="gate-card"
        onSubmit={(e) => {
          e.preventDefault();
          if (token.trim()) location.href = `/api/auth?token=${encodeURIComponent(token.trim())}&next=/`;
        }}
      >
        <Orb state="idle" size="md" />
        <h1><Lock size={18} aria-hidden="true" /> JARVIS is locked</h1>
        <p className="soft">This browser is not authorised. Enter the access token printed when JARVIS started. It is exchanged for a secure cookie and never stored by this page.</p>
        <label className="field">
          <span>Access token</span>
          <input className="input" type="password" autoFocus autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} data-testid="token-input" />
        </label>
        <button className="btn primary" type="submit" disabled={!token.trim()}>Unlock</button>
      </form>
    </main>
  );
}

function Clock() {
  const now = new Date(useNow(1000));
  return (
    <time className="topbar-clock num" aria-label="Local time">
      {pad(now.getHours())}:{pad(now.getMinutes())}
      <span className="dim">:{pad(now.getSeconds())}</span>
    </time>
  );
}

function TopBar({ onApprovals, onBell, bellOpen }: { onApprovals: () => void; onBell: () => void; bellOpen: boolean }) {
  const state = useAssistantState();
  const detail = useStore((s) => s.stateDetail);
  const status = useStore((s) => s.status);
  const connected = useStore((s) => s.connected);
  const unread = useStore((s) => s.notifications.filter((n) => !n.read).length);
  const pending = usePendingApprovals();
  const llm = status?.llm;
  const primary = llm?.chain[0];
  return (
    <header className="topbar">
      <div className="topbar-state" data-testid="assistant-state" data-state={state}>
        <Orb state={state} size="sm" />
        <div className="col" style={{ gap: 0 }}>
          <span className="label">Assistant</span>
          <span className="topbar-state-text">{state}{state === "executing" && detail ? <span className="dim num"> · {detail}</span> : null}</span>
        </div>
      </div>
      <div className="topbar-task grow truncate">
        {status?.active_task ? (
          <><Activity size={14} aria-hidden="true" /> <span className="truncate">{status.active_task.title}</span></>
        ) : null}
      </div>
      {!connected && <span className="badge red" data-testid="ws-offline"><WifiOff size={12} /> reconnecting</span>}
      {llm?.degraded ? (
        <span className="badge amber" data-testid="offline-badge" title="No language-model key is configured. JARVIS is answering with its built-in rule-based engine, which understands a limited set of requests.">
          <CloudOff size={12} /> offline engine
        </span>
      ) : primary ? (
        <span className="badge cyan" data-testid="provider-badge" title={`Model: ${primary.model}`}><Cpu size={12} /> {primary.name}</span>
      ) : null}
      <button type="button" className={`btn topbar-approvals ${pending.length ? "has-pending" : ""}`} onClick={onApprovals} data-testid="approvals-button" aria-label={`${pending.length} pending approvals. Open approval tray`}>
        {pending.length ? <ShieldAlert size={15} /> : <ShieldCheck size={15} />}
        <span>Approvals</span>
        <span className="count num" data-testid="approvals-count">{pending.length}</span>
      </button>
      <button type="button" className="btn icon ghost topbar-bell" onClick={onBell} aria-label={`Notifications, ${unread} unread`} aria-expanded={bellOpen} data-testid="bell-button">
        <Bell size={16} />
        {unread > 0 && <span className="bell-count num">{unread}</span>}
      </button>
      <Clock />
    </header>
  );
}

function ApprovalTray({ onClose }: { onClose: () => void }) {
  const pending = usePendingApprovals();
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="tray" role="dialog" aria-label="Pending approvals" data-testid="approval-tray">
        <header className="tray-head">
          <ShieldAlert size={18} aria-hidden="true" />
          <div className="grow">
            <h2>Pending approvals</h2>
            <p className="dim">Actions that change your system wait here until you decide.</p>
          </div>
          <button type="button" className="btn icon ghost" onClick={onClose} aria-label="Close approval tray"><X size={16} /></button>
        </header>
        <div className="tray-body">
          {pending.length === 0 && <div className="empty">Nothing is waiting for approval.</div>}
          {pending.map((a) => <ApprovalCard key={a.id} approval={a} showSource />)}
        </div>
        <footer className="tray-foot">
          <button type="button" className="btn ghost sm" onClick={() => { onClose(); go("settings"); }}>Approval history &amp; permissions</button>
        </footer>
      </aside>
    </>
  );
}

function NotificationsPopover({ onClose }: { onClose: () => void }) {
  const items = useStore((s) => s.notifications);
  useEffect(() => {
    if (items.some((n) => !n.read)) {
      api.markNotificationsRead().then(() => setState({ notifications: items.map((n) => ({ ...n, read: 1 })) })).catch(() => {});
    }
    // mark read once on open
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <>
      <div className="scrim clear" onClick={onClose} />
      <div className="popover" role="dialog" aria-label="Notifications" data-testid="notifications-popover">
        <div className="label" style={{ padding: "2px 4px 8px" }}>Notifications</div>
        {items.length === 0 && <div className="empty">No notifications yet.</div>}
        {items.map((n) => (
          <div key={n.id} className={`note note-${n.level}`}>
            <div className="row between"><b className="truncate">{n.title}</b><span className="dim num">{ago(n.created)}</span></div>
            <div className="soft">{n.body}</div>
          </div>
        ))}
      </div>
    </>
  );
}

function Toasts({ onOpenApprovals }: { onOpenApprovals: () => void }) {
  const toasts = useStore((s) => s.toasts);
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`} role="status">
          <div className="grow">
            <b>{t.title}</b>
            <div className="soft truncate">{t.body}</div>
          </div>
          {t.kind === "approval" && <button type="button" className="btn sm" onClick={() => { dismissToast(t.id); onOpenApprovals(); }}>Review</button>}
          <button type="button" className="btn icon ghost sm" onClick={() => dismissToast(t.id)} aria-label="Dismiss"><X size={13} /></button>
        </div>
      ))}
    </div>
  );
}

export function App() {
  const auth = useStore((s) => s.auth);
  const route = useRoute();
  const [tray, setTray] = useState(false);
  const [bell, setBell] = useState(false);
  const pending = usePendingApprovals();
  const screen = NAV.some((n) => n.id === route[0]) ? route[0] : "home";

  useEffect(() => {
    if (!location.hash) history.replaceState(null, "", "#/home");
  }, []);
  useEffect(() => {
    document.title = `${pending.length ? `(${pending.length}) ` : ""}JARVIS · ${NAV.find((n) => n.id === screen)?.label ?? ""}`;
  }, [screen, pending.length]);

  if (auth === "locked") return <LockedGate />;
  if (auth === "unknown") {
    return (
      <main className="gate" data-testid="booting">
        <div className="gate-card"><Orb state="thinking" size="md" /><p className="soft">Connecting to JARVIS…</p></div>
      </main>
    );
  }
  return (
    <div className="shell" data-screen={screen}>
      <nav className="rail" aria-label="Primary">
        <a className="brand" href="#/home" aria-label="JARVIS home">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">JARVIS</span>
        </a>
        <ul>
          {NAV.map(({ id, label, icon: Icon }) => (
            <li key={id}>
              <a href={`#/${id}`} className={`rail-link ${screen === id ? "active" : ""}`} aria-current={screen === id ? "page" : undefined} data-testid={`nav-${id}`}>
                <Icon size={17} aria-hidden="true" />
                <span>{label}</span>
              </a>
            </li>
          ))}
        </ul>
        <RailFooter />
      </nav>
      <TopBar onApprovals={() => setTray(true)} onBell={() => setBell((v) => !v)} bellOpen={bell} />
      <main className="main" data-testid={`screen-${screen}`} key={screen}>
        {screen === "home" && <Home />}
        {screen === "voice" && <Voice />}
        {screen === "conversations" && <Conversations cid={route[1] ?? null} />}
        {screen === "agents" && <Agents taskId={route[1] ?? null} />}
        {screen === "computer" && <Computer />}
        {screen === "coding" && <Coding />}
        {screen === "knowledge" && <Knowledge />}
        {screen === "research" && <Research />}
        {screen === "automation" && <Automation />}
        {screen === "settings" && <SettingsScreen />}
      </main>
      {tray && <ApprovalTray onClose={() => setTray(false)} />}
      {bell && <NotificationsPopover onClose={() => setBell(false)} />}
      <Toasts onOpenApprovals={() => setTray(true)} />
    </div>
  );
}

function RailFooter() {
  const status = useStore((s) => s.status);
  const connected = useStore((s) => s.connected);
  return (
    <div className="rail-foot">
      <div className="row"><span className={`dot ${connected ? "on" : "bad"}`} /> <span className="soft">{connected ? "Live link" : "Offline"}</span></div>
      <div className="dim num">v{status?.version ?? "—"} · localhost</div>
    </div>
  );
}
