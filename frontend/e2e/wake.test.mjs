// Unit test for wake-word matching in every script. Run: node frontend/e2e/wake.test.mjs (Node 22.18+ runs the .ts directly).
import assert from "node:assert/strict";
import { norm, wakeRegex, wakeWords } from "../src/wake.ts";

const heard = (text, lang, wake = "jarvis") => wakeRegex(wakeWords(wake, lang)).test(norm(text));
const strip = (text, lang, wake = "jarvis") => text.replace(wakeRegex(wakeWords(wake, lang), true), " ").replace(/\s+/g, " ").trim();
let n = 0;
const ok = (name, fn) => { fn(); n++; console.log(`ok - ${name}`); };

ok("norm keeps Indic letters and marks (the old a-z only version emptied them)", () => {
  assert.equal(norm("ஜார்விஸ், நேரம் என்ன?"), "ஜார்விஸ் நேரம் என்ன");
  assert.equal(norm("जार्विस, समय क्या है?"), "जार्विस समय क्या है");
  assert.equal(norm("Hey JARVIS!"), "hey jarvis");
});
ok("English wake word as before", () => {
  assert.ok(heard("hey jarvis what time is it", "en-IN"));
  assert.ok(heard("Jarvis, open notepad", "en-US"));
  assert.ok(!heard("jarvisland is a place", "en-US"));
  assert.ok(!heard("what time is it", "en-US"));
});
ok("Tamil: Jarvis written in Tamil script, or in Latin", () => {
  assert.ok(heard("ஜார்விஸ் நேரம் என்ன", "ta-IN"));
  assert.ok(heard("jarvis நேரம் என்ன", "ta-IN"));
  assert.ok(!heard("நேரம் என்ன", "ta-IN"));
  assert.equal(strip("ஜார்விஸ், நேரம் என்ன?", "ta-IN"), "நேரம் என்ன?");
});
ok("Hindi: Devanagari spellings, danda after the wake word", () => {
  assert.ok(heard("जार्विस समय क्या है", "hi-IN"));
  assert.ok(heard("जारविस समय क्या है", "hi-IN"));
  assert.equal(strip("जार्विस। समय क्या है", "hi-IN"), "समय क्या है");
  assert.ok(!heard("समय क्या है", "hi-IN"));
});
ok("a script's alias only applies in its language", () => {
  assert.ok(!heard("ஜார்விஸ் நேரம்", "en-US"));
});
ok("custom wake words, comma-separated, any script", () => {
  assert.ok(heard("friday play music", "en-US", "friday"));
  assert.ok(!heard("jarvis play music", "en-US", "friday"));  // a custom word replaces jarvis and its aliases
  assert.ok(heard("நண்பா பாட்டு போடு", "ta-IN", "jarvis, நண்பா"));
  assert.equal(strip("நண்பா, பாட்டு போடு", "ta-IN", "jarvis, நண்பா"), "பாட்டு போடு");
});
console.log(`\n${n} passed`);
