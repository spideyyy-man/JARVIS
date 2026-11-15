import { useState } from "react";
import { Check, ChevronDown, ChevronRight, ShieldAlert, ShieldCheck, ShieldX, X } from "lucide-react";
import type { Approval } from "../api";
import { decideApproval, errText } from "../store";
import { ago, go, pretty } from "../util";
import { StatusBadge } from "./Panel";

/** One action awaiting (or past) the user's explicit decision. Shows exactly what will happen. */
export function ApprovalCard({ approval, showSource = false }: { approval: Approval; showSource?: boolean }) {
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);
  const [error, setError] = useState("");
  const [showArgs, setShowArgs] = useState(false);
  const pending = approval.status === "pending";

  const decide = async (approve: boolean) => {
    setBusy(approve ? "approve" : "deny");
    setError("");
    try {
      await decideApproval(approval.id, approve);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(null);
    }
  };

  const Icon = pending ? ShieldAlert : approval.status === "denied" || approval.status === "failed" ? ShieldX : ShieldCheck;
  return (
    <article className={`approval ${pending ? "is-pending" : `is-${approval.status}`}`} data-testid="approval-card" data-approval-id={approval.id} data-status={approval.status} aria-label={`Approval request for ${approval.tool}`}>
      <header className="approval-head">
        <Icon size={18} className="approval-icon" aria-hidden="true" />
        <div className="grow">
          <div className="row wrap" style={{ gap: 6 }}>
            <span className="approval-title">{pending ? "Approval required" : "Approval"}</span>
            <span className="badge mono amber">{approval.tool}</span>
            {!pending && <StatusBadge status={approval.status} />}
          </div>
          <div className="approval-sub dim">
            requested by <b className="soft">{approval.agent}</b> agent · {ago(approval.created)}
            {pending && " · modifies your system — nothing happens until you decide"}
          </div>
        </div>
      </header>
      <div className="label" style={{ marginBottom: 4 }}>Exactly what will happen</div>
      <pre className="code approval-preview" data-testid="approval-preview">{approval.preview}</pre>
      <button type="button" className="btn ghost sm approval-args-toggle" aria-expanded={showArgs} onClick={() => setShowArgs((v) => !v)}>
        {showArgs ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Raw arguments
      </button>
      {showArgs && <pre className="code">{pretty(approval.args)}</pre>}
      {!pending && approval.result && (
        <>
          <div className="label" style={{ margin: "8px 0 4px" }}>Result</div>
          <pre className="code approval-result">{pretty(approval.result)}</pre>
        </>
      )}
      {error && <div className="error-text" role="alert" style={{ marginTop: 8 }}>{error}</div>}
      {pending && (
        <footer className="approval-actions">
          <button type="button" className="btn approve" disabled={busy !== null} onClick={() => decide(true)} data-testid="approve-btn">
            <Check size={15} /> {busy === "approve" ? "Executing…" : "Approve & run"}
          </button>
          <button type="button" className="btn danger" disabled={busy !== null} onClick={() => decide(false)} data-testid="deny-btn">
            <X size={15} /> {busy === "deny" ? "Denying…" : "Deny"}
          </button>
          {showSource && approval.conversation_id && (
            <button type="button" className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={() => go(`conversations/${approval.conversation_id}`)}>
              Open conversation
            </button>
          )}
        </footer>
      )}
    </article>
  );
}
