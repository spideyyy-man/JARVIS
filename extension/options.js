const keyEl = document.getElementById("key");
const portEl = document.getElementById("port");
const statusEl = document.getElementById("status");

async function show() {
  const s = await chrome.storage.local.get(["key", "port", "status"]);
  keyEl.value = s.key || "";
  portEl.value = s.port || 8765;
  const st = s.status || "starting…";
  statusEl.textContent = st === "connected" ? "Connected to JARVIS" : st;
  statusEl.className = st === "connected" ? "ok" : "bad";
}

document.getElementById("again").addEventListener("click", async () => {
  statusEl.textContent = "connecting…";
  await chrome.runtime.sendMessage({ type: "reconnect" });
  setTimeout(show, 1500);
});
document.getElementById("save").addEventListener("click", async () => {
  await chrome.storage.local.set({ key: keyEl.value.trim(), port: Number(portEl.value) || 8765 });
  statusEl.textContent = "connecting…";
  setTimeout(show, 1500);
});
chrome.storage.onChanged.addListener((c) => { if (c.status) show(); });
show();

// "Fill this form": JARVIS reads the form on this tab and shows what it would fill; nothing changes until "Fill".
const fillBtn = document.getElementById("fill");
const answerEl = document.getElementById("answer");
const decideEl = document.getElementById("decide");
let approval = null;

// JARVIS's replies use a little markdown (**bold**, "- " bullets, "→" answers). Escaped first: the text can contain words
// from the web page, which must never become HTML here.
function render(text) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = false;
  for (const raw of String(text).split("\n")) {
    const line = raw.trimEnd();
    const bullet = /^\s*[-•*]\s+(.*)$/.exec(line);
    if (bullet) {
      if (!list) { out.push("<ul>"); list = true; }
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    if (list) { out.push("</ul>"); list = false; }
    if (/^\s*→/.test(line)) out.push(`<div class="ans">${inline(line.trim())}</div>`);
    else if (/^\s+\S/.test(line)) out.push(`<div class="why">${inline(line.trim())}</div>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("");
}
function say(text) { answerEl.style.display = "block"; answerEl.innerHTML = render(text); }

const actionBtns = ["fill", "answerq", "summary"].map((id) => document.getElementById(id));
const WAIT = { fill: "Reading the form on this page…", answer: "Reading the questions and working out answers… (up to a minute or two)",
               summary: "Reading this page and summarising it… (up to a minute)" };

// Chrome shows this popup from the folder on disk, but keeps running the background script it loaded last. After an
// update that older script does not know the buttons (they came back empty - 2026-11-22), so the popup reloads the
// extension itself: at most once a minute, so a broken background script cannot cause a reload loop.
async function updateIfOld() {
  let v, disk;
  try { v = await chrome.runtime.sendMessage({ type: "version" }); } catch { v = undefined; }
  try { disk = (await (await fetch(chrome.runtime.getURL("manifest.json"), { cache: "no-store" })).json()).version; } catch { disk = v; }
  if (v && (!disk || v === disk)) return false;  // the running background script is the version in the folder
  const s = await chrome.storage.local.get("reloadedAt");
  if (Date.now() - (s.reloadedAt || 0) < 60000) {
    say("JARVIS Connector needs a reload: open chrome://extensions and click the ↻ (Reload) arrow on JARVIS Connector.");
    return true;
  }
  await chrome.storage.local.set({ reloadedAt: Date.now() });
  say("Updating JARVIS Connector to the new version… open this popup again in a few seconds.");
  setTimeout(() => chrome.runtime.reload(), 1200);
  return true;
}
updateIfOld();

async function ask(action) {
  if (await updateIfOld()) return;
  actionBtns.forEach((b) => { b.disabled = true; }); decideEl.style.display = "none"; approval = null;
  say(WAIT[action]);
  const r = await chrome.runtime.sendMessage({ type: "popup", action });
  actionBtns.forEach((b) => { b.disabled = false; });
  say(String((r && r.text) || "No answer from JARVIS.").split('Say "yes"')[0].trim());  // the popup has its own Fill button
  if (r && r.ok && r.approval) { approval = r.approval; decideEl.style.display = "flex"; }
}
fillBtn.addEventListener("click", () => ask("fill"));
document.getElementById("answerq").addEventListener("click", () => ask("answer"));
document.getElementById("summary").addEventListener("click", () => ask("summary"));

async function decide(approve) {
  if (!approval) return;
  decideEl.style.display = "none";
  say(approve ? "Filling…" : "Cancelling…");
  const r = await chrome.runtime.sendMessage({ type: "popup", action: "decide", approval, approve });
  approval = null;
  say((r && r.text) || "No answer from JARVIS.");
}
document.getElementById("yes").addEventListener("click", () => decide(true));
document.getElementById("no").addEventListener("click", () => decide(false));
