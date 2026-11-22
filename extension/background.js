// JARVIS Connector: keeps a WebSocket open to JARVIS on this computer and runs one command at a time on the ACTIVE tab.
// It talks only to 127.0.0.1, with the connector key from JARVIS Settings > Browser connector.
importScripts("page.js");

const DEFAULTS = { port: 8765, key: "" };
let ws = null;
let backoff = 1000;
let retry = null;
let pairingDenied = false;

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "reconnect") { pairingDenied = false; backoff = 1000; if (ws) ws.close(); connect(); return false; }  // "Connect again"
  if (msg && msg.type === "popup") { askJarvis(msg).then(sendResponse); return true; }  // "Fill this form" / "Fill" / "Cancel"
  if (msg && msg.type === "version") { sendResponse(chrome.runtime.getManifest().version); return false; }  // the popup's update check
  return false;
});

// The popup's buttons go to JARVIS over the same socket; JARVIS answers with {type: "popup_result", rid, ...}.
let rids = 0;
const waiting = new Map();
function askJarvis(msg) {
  return new Promise((resolve) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) { connect(); resolve({ ok: false, text: "Not connected to JARVIS yet - click Connect again." }); return; }
    const rid = ++rids;
    const timer = setTimeout(() => { waiting.delete(rid); resolve({ ok: false, text: "JARVIS did not answer in time - try again." }); }, 300000);
    waiting.set(rid, (r) => { clearTimeout(timer); resolve(r); });
    ws.send(JSON.stringify({ type: "popup", rid, action: msg.action, approval: msg.approval, approve: msg.approve }));
  });
}

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(["port", "key"])) };
}

async function setStatus(status) {
  await chrome.storage.local.set({ status, statusAt: Date.now() });
  chrome.action.setBadgeText({ text: status === "connected" ? "" : "!" });
  chrome.action.setBadgeBackgroundColor({ color: "#f59e0b" });
}

async function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
  const s = await settings();
  const pairing = !s.key; // no key yet: ask JARVIS to pair - the user approves it once in JARVIS
  try {
    ws = new WebSocket(pairing ? `ws://127.0.0.1:${s.port}/ws/connector?pair=1`
                               : `ws://127.0.0.1:${s.port}/ws/connector?key=${encodeURIComponent(s.key)}`);
  } catch (e) {
    await setStatus("cannot connect"); scheduleRetry(); return;
  }
  ws.onopen = async () => {
    backoff = 1000;
    await setStatus(pairing ? "waiting - approve it on the JARVIS icon or in the JARVIS window" : "connected");
    ws.send(JSON.stringify({ type: "hello", browser: navigator.userAgent, version: chrome.runtime.getManifest().version }));
  };
  ws.onmessage = async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg && msg.type === "paired" && msg.key) { await chrome.storage.local.set({ key: msg.key }); return; } // reconnects with the key
    if (msg && msg.type === "denied") { pairingDenied = true; await setStatus("JARVIS said no - click 'Connect again' to ask again"); return; }
    if (msg && msg.type === "popup_result") { const done = waiting.get(msg.rid); if (done) { waiting.delete(msg.rid); done(msg); } return; }
    if (msg && msg.type === "busy") { await setStatus("another browser is pairing - try again in a minute"); return; }
    if (!msg || typeof msg.id === "undefined") return;
    let reply;
    try {
      reply = { id: msg.id, ok: true, result: await handle(msg.cmd, msg.args || {}) };
    } catch (e) {
      reply = { id: msg.id, ok: false, error: String((e && e.message) || e) };
    }
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(reply));
  };
  ws.onclose = async (ev) => {
    ws = null;
    if (ev.code === 4401) {  // the saved key is no longer valid: forget it and pair again
      await chrome.storage.local.remove("key");
      return;
    }
    if (pairingDenied) return;  // asked and refused: wait for the user to click "Connect again"
    const st = (await chrome.storage.local.get("status")).status || "";
    if (!st.startsWith("JARVIS said no")) await setStatus("JARVIS is not running (start it, then this connects by itself)");
    scheduleRetry();
  };
  ws.onerror = () => {};
}

function scheduleRetry() {
  if (retry) return;
  retry = setTimeout(() => { retry = null; connect(); }, backoff);
  backoff = Math.min(backoff * 2, 30000);
}

// an open socket that exchanges messages keeps the service worker alive; the alarm wakes it if it was stopped
setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" })); }, 20000);
chrome.alarms.create("jarvis-connect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => { if (!pairingDenied) connect(); });
// Several browsers (Chrome profiles, Edge) can be connected; JARVIS works in the one used last, so say when it is used.
function used() { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "focus" })); else connect(); }
chrome.windows.onFocusChanged.addListener((id) => { if (id !== chrome.windows.WINDOW_ID_NONE) used(); });
chrome.tabs.onActivated.addListener(() => used());
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => connect());
chrome.storage.onChanged.addListener((changes) => {
  if (changes.key || changes.port) { if (ws) ws.close(); backoff = 1000; connect(); }
});
connect();

const WEB = /^(https?|file):/;
let formTab = null;  // the tab whose form was read last: "fill" goes to that same tab

// The tab in front - or, when that is the browser's own page (edge://extensions, a new tab...), the web page used most
// recently, so "fill this form" still works right after setting up the extension.
async function activeTab() {
  let [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) {  // no focused window (just started, or minimised): the active tab used most recently
    const tabs = await chrome.tabs.query({ active: true });
    tab = tabs.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))[0];
  }
  if (tab && WEB.test(tab.url || "")) return tab;
  const web = (await chrome.tabs.query({})).filter((t) => WEB.test(t.url || ""))
    .sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0));
  if (web.length) return web[0];
  if (!tab) throw new Error("no browser tab is open");
  throw new Error("no web page is open - open your form in a tab, then ask again");
}

async function fillTab() {  // the tab the form was read from, if it is still showing the same page
  if (formTab) {
    try {
      const t = await chrome.tabs.get(formTab.id);
      if (t && t.url === formTab.url) return t;
    } catch { /* closed */ }
  }
  return activeTab();
}

async function inPage(tab, func, args) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func, args: args || [] });
  if (!res) throw new Error("the page did not answer");
  return res.result;
}

// Every frame of the tab: learning sites and test portals put the lesson and its questions in embedded frames, so the
// top page alone held only the menu (2026-11-22). Results come back as [{frameId, result}], main frame first.
async function inFrames(tab, func, args) {
  let res;
  try {
    res = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func, args: args || [] });
  } catch (e) {  // a frame that cannot be scripted must not cost the rest: fall back to the main page
    res = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func, args: args || [] });
  }
  return (res || []).filter((r) => r && r.result != null).sort((a, b) => a.frameId - b.frameId);
}

async function readPage(tab) {
  const frames = await inFrames(tab, jarvisReadPage);
  if (!frames.length) throw new Error("the page did not answer");
  const top = frames.find((f) => f.frameId === 0) || frames[0];
  const parts = frames.map((f) => f.result.text || "").filter((t) => t.length > 40);
  return { title: top.result.title, url: top.result.url, text: parts.join("\n\n").slice(0, 30000), frames: frames.length };
}

async function readForm(tab) {
  const frames = await inFrames(tab, jarvisReadForm, [JARVIS_SENSITIVE_SOURCE]);
  if (!frames.length) throw new Error("the page did not answer");
  const top = frames.find((f) => f.frameId === 0) || frames[0];
  const fields = [];
  for (const f of frames) for (const x of f.result.fields || []) fields.push({ ...x, id: `${f.frameId}:${x.id}` });
  return { title: top.result.title, url: top.result.url, fields };
}

async function fillFrames(tab, values) {  // ids are "<frameId>:<field id>"; each frame fills its own fields
  const byFrame = new Map();
  for (const v of values) {
    const m = /^(\d+):(.+)$/.exec(String(v.id));
    const fid = m ? Number(m[1]) : 0;
    if (!byFrame.has(fid)) byFrame.set(fid, []);
    byFrame.get(fid).push({ ...v, id: m ? m[2] : String(v.id) });
  }
  const filled = [], skipped = [];
  for (const [fid, vals] of byFrame) {
    try {
      const [r] = await chrome.scripting.executeScript({ target: { tabId: tab.id, frameIds: [fid] }, func: jarvisFill, args: [vals, JARVIS_SENSITIVE_SOURCE] });
      for (const id of (r && r.result ? r.result.filled : [])) filled.push(`${fid}:${id}`);
      for (const s of (r && r.result ? r.result.skipped : [])) skipped.push({ ...s, id: `${fid}:${s.id}` });
    } catch (e) {
      for (const v of vals) skipped.push({ id: `${fid}:${v.id}`, reason: "that part of the page is gone - read it again" });
    }
  }
  return { filled, skipped };
}

async function clickFrames(tab, text) {
  for (const f of await inFrames(tab, jarvisClick, [text])) if (f.result) return f.result;
  throw new Error(`nothing called "${text}" to click on this page`);
}

async function handle(cmd, args) {
  switch (cmd) {
    case "tab": { const t = await activeTab(); return { title: t.title, url: t.url }; }
    case "read_page": return readPage(await activeTab());
    case "read_form": { const t = await activeTab(); formTab = { id: t.id, url: t.url }; return readForm(t); }
    case "fill": return fillFrames(await fillTab(), args.values || []);
    case "click": return clickFrames(await activeTab(), String(args.text || ""));
    case "inspect": return (await inFrames(await activeTab(), jarvisInspect, [String(args.probe || "")])).map((f) => ({ frameId: f.frameId, ...f.result }));
    case "scroll": return inPage(await activeTab(), jarvisScroll, [String(args.direction || "down")]);
    default: throw new Error(`unknown command ${cmd}`);
  }
}
