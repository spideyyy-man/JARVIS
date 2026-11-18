// Unit test for automatic English/Tamil language handling. Run: node frontend/e2e/lang.test.mjs (Node 22.18+).
import assert from "node:assert/strict";
import { EN, TA, languageSwitch, nextListenLang, replyVoiceLang, scriptOf, typedLang } from "../src/lang.ts";

let n = 0;
const ok = (name, fn) => { fn(); n++; console.log(`ok - ${name}`); };

ok("script of a text", () => {
  assert.equal(scriptOf("நேரம் என்ன"), "ta");
  assert.equal(scriptOf("volume ஐ increase பண்ணு"), "ta"); // code-mixed Tamil is Tamil
  assert.equal(scriptOf("what time is it"), "en");
  assert.equal(scriptOf("समय क्या है"), "hi");
  assert.equal(scriptOf("123 ?"), "");
});

ok("asking for a language switches it (with the wake word, or while already listening)", () => {
  for (const t of ["Jarvis, Tamil", "jarvis speak in tamil", "Hey Jarvis switch to Tamil please", "ஜார்விஸ், தமிழ்ல பேசு", "ஜார்விஸ் தமிழ்", "Vanakkam Jarvis", "vanakam, jarvis", "வணக்கம் ஜார்விஸ்"])
    assert.equal(languageSwitch(t), TA, t);
  for (const t of ["Jarvis, English", "jarvis talk in english", "ஜார்விஸ் இங்கிலீஷ்ல பேசு", "ஜார்விஸ், ஆங்கிலத்தில் பேசு"])
    assert.equal(languageSwitch(t), EN, t);
  assert.equal(languageSwitch("தமிழ்", true), TA); // right after "Jarvis", the command alone
  assert.equal(languageSwitch("English", true), EN);
});

ok("ordinary requests that mention a language are not switches", () => {
  for (const t of ["Jarvis, translate hello into Tamil", "Jarvis what is the capital of Tamil Nadu", "tamil", "english", "Jarvis, open notepad", "வணக்கம்"])
    assert.equal(languageSwitch(t), null, t);
});

ok("listening follows the language actually spoken", () => {
  // the Tamil recogniser writes English speech in Latin letters
  assert.equal(nextListenLang(TA, "what is the weather today"), EN);
  assert.equal(nextListenLang(TA, "volume ஐ increase பண்ணு"), TA); // mixed: stay in Tamil
  assert.equal(nextListenLang(TA, "OK"), TA); // one word is not enough evidence
  assert.equal(nextListenLang(EN, "what time is it"), EN);
});

ok("typing sets the language", () => {
  assert.equal(typedLang(EN, "நேரம் என்ன"), TA);
  assert.equal(typedLang(TA, "open notepad"), EN);
  assert.equal(typedLang(TA, "ok"), TA);
});

ok("replies are spoken in the language they are written in", () => {
  assert.equal(replyVoiceLang("இப்போது மாலை 6:16.", "auto"), TA);
  assert.equal(replyVoiceLang("It's 6:16 PM.", "auto"), EN);
  assert.equal(replyVoiceLang("It's 6:16 PM.", "ta-IN"), EN); // an English reply in Tamil mode gets an English voice
  assert.equal(replyVoiceLang("It's 6:16 PM.", ""), ""); // browser default stays the browser's
  assert.equal(replyVoiceLang("It's 6:16 PM.", "en-GB"), "en-GB");
});

console.log(`\n${n} passed`);
