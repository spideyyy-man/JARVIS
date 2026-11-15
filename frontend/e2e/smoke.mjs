// Browser end-to-end check. Needs a running JARVIS backend that serves the built frontend.
//   BASE=http://127.0.0.1:8765 TOKEN=dev-token E2E_HOME=/path/to/allowed/root node frontend/e2e/smoke.mjs
// Speech hardware is replaced by fakes (headless Chromium has no recogniser); everything else is real.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.env.BASE || "http://127.0.0.1:8765";
const TOKEN = process.env.TOKEN || "dev-token";
const HOME = process.env.E2E_HOME || "/home/user";
const SHOTS = path.join(path.dirname(fileURLToPath(import.meta.url)), "screenshots");
const results = [];
const problems = [];
const check = (name, ok, detail = "") => { results.push({ name, ok }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  (" + detail + ")" : ""}`); };

const downloads = path.join(HOME, "Downloads");
fs.mkdirSync(downloads, { recursive: true });
for (const f of ["report.pdf", "photo.png", "notes.txt", "budget.xlsx"]) if (!fs.existsSync(path.join(downloads, f))) fs.writeFileSync(path.join(downloads, f), "demo");
fs.writeFileSync(path.join(HOME, "e2e-notes.md"), "# Deploy notes\n\nThe deploy pipeline uses blue green releases and a canary stage before full rollout.\n");

const fakes = () => {
  window.__spoken = []; window.__cancels = 0; window.__recs = []; window.__ttsDelay = 40;
  class FakeRecognition {
    constructor() { this.continuous = false; this.interimResults = false; this.lang = ""; this.onresult = this.onend = this.onerror = this.onstart = null; window.__recs.push(this); }
    start() { this.running = true; setTimeout(() => this.onstart && this.onstart(), 0); }
    stop() { this.running = false; setTimeout(() => this.onend && this.onend(), 0); }
    abort() { this.stop(); }
  }
  window.SpeechRecognition = FakeRecognition;
  window.__say = (text) => { const r = window.__recs.filter((x) => x.running).pop(); if (!r) return false; r.onresult({ resultIndex: 0, results: { length: 1, 0: { isFinal: true, length: 1, 0: { transcript: text } } } }); return true; };
  window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; this.rate = 1; this.onend = this.onerror = this.onstart = null; } };
  const synth = {
    speaking: false, pending: false, paused: false,
    speak(u) { window.__spoken.push(u.text); synth.speaking = true; u.onstart && u.onstart(); u.__t = setTimeout(() => { synth.speaking = false; u.onend && u.onend(); }, window.__ttsDelay); },
    cancel() { window.__cancels++; synth.speaking = false; }, getVoices: () => [], addEventListener() {}, removeEventListener() {}, pause() {}, resume() {},
  };
  Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true });
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
await ctx.addInitScript(fakes);
const page = await ctx.newPage();
page.on("console", (m) => { if (m.type() === "error" && !/501|favicon/.test(m.text())) problems.push("console: " + m.text()); });
page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
page.on("response", (r) => { if (r.status() >= 400 && r.url().includes("/api/") && r.status() !== 501) problems.push(`http ${r.status()} ${r.url()}`); });
const shot = (name) => page.screenshot({ path: path.join(SHOTS, name + ".png") });
const goto = async (id) => { await page.click(`[data-testid="nav-${id}"]`); await page.waitForSelector(`[data-testid="screen-${id}"]`); };

try {
  // locked without the token
  await page.goto(BASE + "/");
  await page.waitForSelector('[data-testid="locked-gate"]', { timeout: 10000 });
  check("unauthenticated browser sees the locked gate", true);
  problems.length = 0; // the 401s above are the point of this step
  await page.goto(`${BASE}/api/auth?token=${TOKEN}&next=/`);
  await page.waitForSelector('[data-testid="screen-home"]', { timeout: 15000 });

  // --- 1 Home
  await page.waitForFunction(() => /\d/.test(document.querySelector('[data-testid="vital-cpu"]')?.textContent || ""), null, { timeout: 15000 });
  check("home: live CPU/memory/disk vitals", true, (await page.textContent('[data-testid="vital-memory"]')).replace(/\s+/g, " ").trim().slice(0, 40));
  check("home: offline-engine state shown honestly", await page.isVisible('[data-testid="offline-badge"]'));
  await page.fill('[data-testid="home-command-composer"] textarea, [data-testid="home-command-composer"] input', "how is my computer performing?");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => /System performance/.test(document.querySelector('[data-testid="home-assistant"]')?.textContent || ""), null, { timeout: 20000 });
  check("home: command streamed a real answer", true);
  await shot("01-home");

  // --- 3 Conversations + approval round trip
  await goto("conversations");
  await page.fill('[data-testid="conv-composer"] textarea, [data-testid="conv-composer"] input', "organize my downloads folder");
  await page.keyboard.press("Enter");
  await page.waitForSelector('[data-testid="thread"] [data-testid="approval-card"]', { timeout: 20000 });
  const preview = await page.textContent('[data-testid="thread"] [data-testid="approval-preview"]');
  check("conversation: approval card shows what will happen", /Organize .*Downloads.*move \d+ files/s.test(preview), preview.trim().slice(0, 70));
  check("approval: files untouched while pending", fs.existsSync(path.join(downloads, "report.pdf")));
  check("approval: global pending indicator", (await page.textContent('[data-testid="approvals-count"]')).trim() === "1");
  await shot("03-conversations-approval");
  await page.click('[data-testid="thread"] [data-testid="approve-btn"]');
  await page.waitForSelector('[data-testid="msg-event"]', { timeout: 15000 });
  check("approval: action executed only after Approve", fs.existsSync(path.join(downloads, "Documents", "report.pdf")) && !fs.existsSync(path.join(downloads, "report.pdf")));
  await page.waitForSelector('[data-testid="conv-item"]');
  check("conversation: list shows saved conversations", (await page.locator('[data-testid="conv-item"]').count()) >= 2);

  // --- 2 Voice
  await goto("voice");
  check("voice: recognition + synthesis detected", !(await page.isVisible('[data-testid="stt-unsupported"]')) && !(await page.isVisible('[data-testid="tts-unsupported"]')));
  await page.click('[role="switch"][aria-label="Wake word listening"]');
  await page.waitForFunction(() => window.__recs.some((r) => r.running));
  const chatReq = page.waitForRequest((r) => r.url().endsWith("/api/chat") && r.method() === "POST", { timeout: 8000 }).catch(() => null);
  await page.evaluate(() => window.__say("what a lovely day outside"));
  await page.waitForTimeout(400);
  check("voice: speech without the wake word is ignored", (await page.evaluate(() => window.__spoken.length)) === 0 && /ignored/.test(await page.textContent('[data-testid="voice-transcript"]')));
  await page.evaluate(() => { window.__ttsDelay = 4000; window.__say("jarvis how is my computer performing"); });
  const req = await chatReq;
  const body = req ? req.postDataJSON() : {};
  check("voice: wake-word utterance sent as a voice request, wake word stripped", body.source === "voice" && /^how is my computer performing/i.test(body.text || ""), JSON.stringify({ text: body.text, source: body.source }));
  await page.waitForFunction(() => /System performance/.test(document.querySelector('[data-testid="voice-reply"]')?.textContent || ""), null, { timeout: 20000 });
  check("voice: reply streamed into the voice screen", true);
  await page.waitForFunction(() => window.__spoken.length > 0, null, { timeout: 8000 });
  const spoken = await page.evaluate(() => window.__spoken.join(" "));
  check("voice: reply spoken aloud with Markdown stripped", /System performance/.test(spoken) && !/\*\*/.test(spoken), spoken.slice(0, 60));
  await shot("02-voice");
  const before = await page.evaluate(() => window.__cancels);
  await page.click('[data-testid="voice-stop"]');
  await page.waitForTimeout(300);
  check("voice: barge-in cancels speech", (await page.evaluate(() => window.__cancels)) > before);
  await page.click('[role="switch"][aria-label="Wake word listening"]');

  // --- 4 Agents
  await goto("agents");
  await page.waitForSelector('[data-testid="agent-card"]');
  check("agents: all seven agents listed", (await page.locator('[data-testid="agent-card"]').count()) === 7);
  await page.waitForSelector('[data-testid="agent-msg"]');
  check("agents: structured inter-agent messages shown", (await page.locator('[data-testid="agent-msg"]').count()) >= 2);
  await shot("04-agents");

  // --- 5 Computer
  await goto("computer");
  await page.waitForSelector('[data-testid="explorer-entry"]', { timeout: 15000 });
  check("computer: file explorer lists a real folder", (await page.locator('[data-testid="explorer-entry"]').count()) > 0);
  await page.waitForSelector('[data-testid="disk-row"]');
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="processes"] tbody tr, [data-testid="process-row"]').length > 5 || document.querySelectorAll("table tbody tr").length > 8, null, { timeout: 15000 });
  check("computer: disks and process table", true);
  await shot("05-computer");

  // --- 6 Coding
  await goto("coding");
  await page.fill('[data-testid="project-path"]', process.env.E2E_PROJECT || path.resolve(SHOTS, "../../.."));
  if (await page.isEnabled('[data-testid="project-save"]')) await page.click('[data-testid="project-save"]');
  await page.waitForSelector('[data-testid="test-command"]', { timeout: 20000 });
  check("coding: repository overview with detected test command", /pytest/.test(await page.textContent('[data-testid="test-command"]')));
  await page.fill('[data-testid="dev-task-input"]', "e2e: review approval flow");
  await page.click('[data-testid="dev-task-add"]');
  await page.waitForFunction(() => [...document.querySelectorAll('[data-testid="dev-task"]')].some((n) => n.textContent.includes("e2e: review approval flow")));
  check("coding: dev task added to the board", true);
  await shot("06-coding");

  // --- 7 Knowledge
  await goto("knowledge");
  await page.fill('[data-testid="kb-path"]', path.join(HOME, "e2e-notes.md"));
  await page.fill('[data-testid="kb-target-space"]', "ops");
  await page.click('[data-testid="kb-ingest-btn"]');
  await page.waitForSelector('[data-testid="kb-ingest-msg"]');
  await page.fill('[data-testid="kb-query"]', "blue green deploy pipeline");
  await page.click('[data-testid="kb-search-btn"]');
  await page.waitForSelector('[data-testid="kb-result"]');
  check("knowledge: indexed a document and retrieved the passage with its source", /e2e-notes\.md/.test(await page.textContent('[data-testid="kb-result"]')));
  await shot("07-knowledge");

  // --- 8 Research
  await goto("research");
  await page.waitForSelector('[data-testid="research-tasks"]');
  check("research: workspace renders tasks, sources and reports panels", (await page.isVisible('[data-testid="research-sources"]')) && (await page.isVisible('[data-testid="research-reports"]')));
  await shot("08-research");

  // --- 9 Automation
  await goto("automation");
  await page.fill('[data-testid="wf-name"]', "E2E vitals check");
  await page.selectOption('[data-testid="wf-type"]', "cron");
  await page.fill('[data-testid="wf-cron"]', "not a cron");
  await page.click('[data-testid="wf-create"]');
  await page.waitForSelector('[data-testid="wf-error"]');
  check("automation: invalid schedule rejected with the server's reason", /cron/i.test(await page.textContent('[data-testid="wf-error"]')));
  problems.splice(0, problems.length, ...problems.filter((p) => !/400 .*\/api\/workflows/.test(p) && !/status of 400/.test(p)));
  await page.selectOption('[data-testid="wf-type"]', "manual");
  await page.click('[data-testid="wf-create"]');
  await page.waitForSelector('[data-testid="workflow"]');
  await page.click('[data-testid="wf-run"]');
  await page.waitForSelector('[data-testid="wf-run-row"]');
  await page.click('[data-testid="wf-run-row"] button');
  await page.waitForSelector('[data-testid="wf-run-log"]');
  const log = await page.textContent('[data-testid="wf-run-log"]');
  check("automation: workflow created, run, and per-step log shown", /stats/.test(log) && /ok/.test(log) && /skipped|warn/.test(log));
  await shot("09-automation");

  // --- 10 Settings
  await goto("settings");
  await page.waitForSelector('[data-testid="tool-row"]');
  const del = page.locator('[data-testid="tool-row"][data-tool="delete_path"]');
  check("settings: destructive tools cannot be allowlisted", /never/.test(await del.textContent()) && (await del.locator('[role="switch"]').count()) === 0);
  await page.locator('[data-testid="tool-row"][data-tool="create_directory"] [role="switch"]').click();
  await page.waitForSelector('[data-testid="settings-saved"]');
  check("settings: low-risk tool allowlisted for trusted automation", (await page.locator('[data-testid="tool-row"][data-tool="create_directory"] [role="switch"]').getAttribute("aria-checked")) === "true");
  await page.locator('[data-testid="tool-row"][data-tool="create_directory"] [role="switch"]').click();
  await page.waitForSelector('[data-testid="audit-row"]');
  const audit = await page.textContent('[data-testid="audit-log"]');
  check("settings: audit log records the approval decision", /organize_folder/.test(audit) && /approved/.test(audit));
  const cap = page.locator('[data-testid="capability"][data-cap="screen_access"] [role="switch"]');
  check("settings: high-impact capabilities are off by default", (await page.locator('[data-testid="capability"] [role="switch"][aria-checked="true"]').count()) === 0 && (await page.locator('[data-testid="capability"]').count()) === 3);
  await cap.click();
  await page.waitForFunction(() => document.querySelector('[data-testid="capability"][data-cap="screen_access"] [role="switch"]')?.getAttribute("aria-checked") === "true");
  await cap.click();
  await page.waitForFunction(() => document.querySelector('[data-testid="capability"][data-cap="screen_access"] [role="switch"]')?.getAttribute("aria-checked") === "false");
  check("settings: a capability can be switched on and off by the user", true);
  check("settings: degraded model state disclosed", await page.isVisible('[data-testid="llm-degraded"]'));
  await shot("10-settings");

  check("no console errors or failed API requests during the run", problems.length === 0, problems.slice(0, 4).join(" | "));
} catch (e) {
  check("run completed without an exception", false, String(e).split("\n")[0]);
  await shot("failure").catch(() => {});
  if (problems.length) console.log(problems.slice(0, 8).join("\n"));
}
await browser.close();
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
