import { useState } from "react";
import { Globe, IdCard } from "lucide-react";
import { api } from "../api";
import { errText, useApi } from "../store";
import { Loading, Panel } from "./Panel";

const LABELS: Record<string, string> = {
  full_name: "Full name", first_name: "First name", last_name: "Last name", email: "Email", phone: "Phone", date_of_birth: "Date of birth",
  age: "Age", gender: "Gender", address: "Address", city: "City", district: "District", state: "State", pincode: "PIN code", country: "Country",
  nationality: "Nationality", school: "School", college: "College", course: "Course / class", year_of_study: "Year of study",
  roll_number: "Roll / register number", father_name: "Father's name", mother_name: "Mother's name", occupation: "Occupation",
  company: "Company", website: "Website", linkedin: "LinkedIn", github: "GitHub",
};
const COMMON = ["full_name", "email", "phone", "date_of_birth", "gender", "address", "city", "state", "pincode", "college", "course", "roll_number"];

/** Settings: the JARVIS Connector extension (status + key) and the details JARVIS fills forms with. */
export function BrowserConnector() {
  const conn = useApi(() => api.connector(), []);
  const det = useApi(() => api.details(), []);
  const [shown, setShown] = useState(false);
  const [copied, setCopied] = useState(false);
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [more, setMore] = useState(false);
  const [note, setNote] = useState("");
  const saved = det.data?.details ?? {};
  const keys = more ? det.data?.fields ?? COMMON : [...new Set([...COMMON, ...Object.keys(saved)])];

  const save = async () => {
    setNote("");
    try {
      const r = await api.putDetails(edit);
      setEdit({});
      setNote(r.refused.length ? `Not saved: ${r.refused.join(", ")} (JARVIS never keeps passwords, OTPs, card, bank or ID numbers).` : "Saved.");
      det.reload();
    } catch (e) {
      setNote(errText(e));
    }
  };

  return (
    <>
      <Panel title="Browser connector" icon={<Globe size={14} />} testid="browser-connector">
        <Loading error={conn.error} loading={conn.loading} empty={!conn.data} />
        {conn.data && (
          <div className="col" style={{ gap: 8 }}>
            <div className="row between">
              <span>{conn.data.connected ? "Connected" : "Not connected"}</span>
              <span className={conn.data.connected ? "ok" : "soft"} data-testid="connector-state">
                {conn.data.connected ? `extension ${conn.data.version}` : "install the extension below"}
              </span>
            </div>
            <p className="soft">
              Lets JARVIS read the page you are on and fill forms in your own Chrome or Edge - after you say yes. It never fills
              passwords, OTPs, card or ID numbers and never presses Submit on its own.
            </p>
            <ol className="soft" style={{ margin: 0, paddingLeft: 18 }}>
              <li>Open <span className="num">edge://extensions</span> (or <span className="num">chrome://extensions</span>) and turn on Developer mode.</li>
              <li>Load unpacked → choose the <span className="num">extension</span> folder inside the JARVIS folder.</li>
              <li>Click the extension's icon, paste the key below, Save.</li>
            </ol>
            <div className="row" style={{ gap: 6 }}>
              <input className="input grow num" readOnly value={shown ? conn.data.key : "•".repeat(20)} data-testid="connector-key" />
              <button type="button" className="btn" onClick={() => setShown((v) => !v)}>{shown ? "Hide" : "Show"}</button>
              <button type="button" className="btn" data-testid="connector-copy"
                onClick={async () => { await navigator.clipboard.writeText(conn.data!.key); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <button type="button" className="btn" onClick={conn.reload}>Check connection</button>
          </div>
        )}
      </Panel>
      <Panel title="My details (for filling forms)" icon={<IdCard size={14} />} testid="my-details">
        <Loading error={det.error} loading={det.loading} empty={!det.data} />
        {det.data && (
          <div className="col" style={{ gap: 6 }}>
            <p className="soft">Say "Jarvis, fill this form" on any form. Stored only on this computer. You can also just say "my email is …".</p>
            {keys.map((k) => (
              <label key={k} className="field">
                <span>{LABELS[k] ?? k}</span>
                <input className="input" value={edit[k] ?? saved[k] ?? ""} data-testid={`detail-${k}`}
                  onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} placeholder={k === "date_of_birth" ? "15/08/2007" : ""} />
              </label>
            ))}
            <div className="row between">
              <button type="button" className="btn ghost" onClick={() => setMore((v) => !v)}>{more ? "Fewer fields" : "More fields"}</button>
              <button type="button" className="btn" disabled={!Object.keys(edit).length} onClick={save} data-testid="details-save">Save details</button>
            </div>
            {note && <p className="soft" data-testid="details-note">{note}</p>}
          </div>
        )}
      </Panel>
    </>
  );
}
