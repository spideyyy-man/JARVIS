// JARVIS Connector: keeps a WebSocket open to JARVIS on this computer and runs one command at a time on the ACTIVE tab.
// It talks only to 127.0.0.1, with the connector key from JARVIS Settings > Browser connector.
importScripts("page.js");

const DEFAULTS = { port: 8765, key: "" };
let ws = null;
let backoff = 1000;
let retry = null;

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
  if (!s.key) { await setStatus("no key - paste it from JARVIS Settings"); return; }
  try {
    ws = new WebSocket(`ws://127.0.0.1:${s.port}/ws/connector?key=${encodeURIComponent(s.key)}`);
  } catch (e) {
    await setStatus("cannot connect"); scheduleRetry(); return;
  }
  ws.onopen = async () => {
    backoff = 1000;
    ws.send(JSON.stringify({ type: "hello", browser: navigator.userAgent, version: chrome.runtime.getManifest().version }));
    await setStatus("connected");
  };
  ws.onmessage = async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
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
    await setStatus(ev.code === 4401 ? "key refused - copy it again from JARVIS Settings" : "JARVIS is not running");
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
chrome.alarms.onAlarm.addListener(() => connect());
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => connect());
chrome.storage.onChanged.addListener((changes) => {
  if (changes.key || changes.port) { if (ws) ws.close(); backoff = 1000; connect(); }
});
connect();

async function activeTab() {
  let [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) {  // no focused window (just started, or minimised): the active tab used most recently
    const tabs = await chrome.tabs.query({ active: true });
    tab = tabs.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))[0];
  }
  if (!tab) throw new Error("no browser tab is open");
  if (!/^(https?|file):/.test(tab.url || "")) throw new Error(`JARVIS can't work on this kind of page (${(tab.url || "").split(":")[0] || "blank"})`);
  return tab;
}

async function inPage(tab, func, args) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func, args: args || [] });
  if (!res) throw new Error("the page did not answer");
  return res.result;
}

async function handle(cmd, args) {
  switch (cmd) {
    case "tab": { const t = await activeTab(); return { title: t.title, url: t.url }; }
    case "read_page": return inPage(await activeTab(), jarvisReadPage);
    case "read_form": return inPage(await activeTab(), jarvisReadForm, [JARVIS_SENSITIVE_SOURCE]);
    case "fill": return inPage(await activeTab(), jarvisFill, [args.values || [], JARVIS_SENSITIVE_SOURCE]);
    case "click": return inPage(await activeTab(), jarvisClick, [String(args.text || "")]);
    case "scroll": return inPage(await activeTab(), jarvisScroll, [String(args.direction || "down")]);
    default: throw new Error(`unknown command ${cmd}`);
  }
}
