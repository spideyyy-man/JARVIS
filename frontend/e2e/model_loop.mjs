// End-to-end check with a model in the loop: real UI -> real backend -> a stand-in model server over HTTP (stub_llm.py).
// Proves streaming, tool-call repair, single-agent mode, approval -> automatic continuation, and the instant fast path.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.env.BASE || "http://127.0.0.1:8768";
const STUB = process.env.STUB || "http://127.0.0.1:9922";
const HOME = process.env.E2E_HOME;
const SHOTS = path.join(path.dirname(fileURLToPath(import.meta.url)), "screenshots");
let failed = 0;
const check = (name, ok, detail = "") => { if (!ok) failed++; console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  (" + detail + ")" : ""}`); };
const note = path.join(HOME, "note.txt");

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })).newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const composer = '[data-testid="conv-composer"] textarea, [data-testid="conv-composer"] input';
const lastAssistant = () => page.evaluate(() => [...document.querySelectorAll('[data-testid="msg-assistant"]')].pop()?.textContent || "");
const send = async (text) => { await page.fill(composer, text); await page.keyboard.press("Enter"); };
try {
  await page.goto(`${BASE}/api/auth?token=dev-token&next=/%23/conversations`);
  await page.waitForSelector('[data-testid="screen-conversations"]', { timeout: 15000 });
  check("a model is configured (no offline badge)", !(await page.isVisible('[data-testid="offline-badge"]')));

  await send("is my laptop struggling right now?");
  await page.waitForFunction(() => /Your CPU is at [\d.?]+ percent/.test([...document.querySelectorAll('[data-testid="msg-assistant"]')].pop()?.textContent || ""), null, { timeout: 30000 });
  const a1 = await lastAssistant();
  check("model answered using a real tool result; its text-form tool call was repaired and hidden", /Your CPU is at/.test(a1) && !/```|"name"/.test(a1), a1.slice(0, 70));
  let log = await (await fetch(STUB + "/log")).json();
  check("requests were streamed, single-agent mode, shortlist of tools", log.every((l) => l.stream === true && !l.has_delegate && l.tools_offered > 0 && l.tools_offered <= 12), JSON.stringify(log[0]));
  check("one tool round-trip took 2 model calls (not 4)", log.length === 2, `${log.length} calls`);

  await send(`write the note ${note} saying hello from the loop, then confirm it`);
  await page.waitForSelector('[data-testid="thread"] [data-testid="approve-btn"]', { timeout: 30000 });
  check("write is held for approval", !fs.existsSync(note));
  await page.click('[data-testid="thread"] [data-testid="approve-btn"]');
  await page.waitForFunction(() => /Verified: the note now contains/.test([...document.querySelectorAll('[data-testid="msg-assistant"]')].pop()?.textContent || ""), null, { timeout: 30000 });
  check("after approval the task continued by itself and verified the result", fs.readFileSync(note, "utf8") === "hello from the loop" && /hello from the loop/.test(await lastAssistant()), (await lastAssistant()).slice(0, 80));
  await page.screenshot({ path: path.join(SHOTS, "13-model-loop.png") });

  const before = (await (await fetch(STUB + "/log")).json()).length;
  await send("how is my computer performing?");
  await page.waitForFunction(() => /System performance/.test([...document.querySelectorAll('[data-testid="msg-assistant"]')].pop()?.textContent || ""), null, { timeout: 20000 });
  check("simple command took the instant fast path (model not called)", (await (await fetch(STUB + "/log")).json()).length === before);

  await send("hello there");
  await page.waitForFunction(() => /stand-in model/.test([...document.querySelectorAll('[data-testid="msg-assistant"]')].pop()?.textContent || ""), null, { timeout: 20000 });
  check("model's private <think> text never reaches the user", !/just chatting|think>/.test(await lastAssistant()));
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (e) {
  check("run completed without an exception", false, String(e).split("\n")[0]);
  await page.screenshot({ path: path.join(SHOTS, "failure-model.png") }).catch(() => {});
  console.log(await lastAssistant().catch(() => ""), JSON.stringify(await (await fetch(STUB + "/log")).json().catch(() => [])));
}
await browser.close();
process.exit(failed ? 1 : 0);
