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
