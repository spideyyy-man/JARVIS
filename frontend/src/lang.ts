// Automatic language for speaking with JARVIS in English and Tamil.
//
// Browsers cannot detect the spoken language: the Web Speech API recognises one language at a time, chosen before
// the user speaks (`recognition.lang`). So "auto" decides which language to listen in from what JARVIS can see:
//   - the language the user last spoke or typed (it sticks until there is a reason to change);
//   - asking for it: "Jarvis, Tamil" / "ஜார்விஸ், தமிழ்ல பேசு" / "Jarvis, English" / "இங்கிலீஷ்ல பேசு";
//   - greeting in Tamil: "Vanakkam Jarvis" heard by the English recogniser switches to Tamil;
//   - evidence in the transcript: the Tamil recogniser writes English speech in Latin letters, so a sentence with no
//     Tamil letters at all switches back to English; typing in Tamil or English sets the language too.
// Replies are spoken in the language of the reply text (Tamil script -> a Tamil voice), whatever is being listened to.

export const AUTO = "auto";
export const EN = "en-IN";
export const TA = "ta-IN";

const TAMIL = /[஀-௿]/g;
const DEVANAGARI = /[ऀ-ॿ]/g;
const LATIN = /[A-Za-z]/g;
const count = (re: RegExp, s: string) => (s.match(re) || []).length;

/** The language a piece of text is written in, judged by its letters: "ta", "hi", "en" or "" (no letters). */
export function scriptOf(text: string): "ta" | "hi" | "en" | "" {
  const ta = count(TAMIL, text), hi = count(DEVANAGARI, text), en = count(LATIN, text);
  if (ta && ta >= hi) return "ta";
  if (hi) return "hi";
  return en ? "en" : "";
}

/** The language to speak a reply in, from its own letters: Tamil text gets a Tamil voice even while listening in
 * English, and English text an English voice even in Tamil mode. `chosen` is the speech-language setting. */
export function replyVoiceLang(text: string, chosen: string): string {
  const s = scriptOf(text);
  if (s === "ta") return TA;
  if (s === "hi") return "hi-IN";
  if (!chosen) return ""; // browser default
  if (s === "en" && (chosen === AUTO || chosen.startsWith("ta") || chosen.startsWith("hi"))) return EN;
  return chosen === AUTO ? EN : chosen;
}

const WAKE = String.raw`(?:(?:hey|ok|okay|hi)\s+)?(?:jarvis|ஜார்விஸ்|ஜார்விஸ|ஜார்வீஸ்)`;
const ASK_TA = String.raw`(?:(?:speak|talk|reply|answer|switch(?:\s+to)?|change(?:\s+to)?)\s+)?(?:in\s+)?tamil(?:\s+please)?|தமிழ்(?:\s*(?:ல|லே|ில்|இல்|லயே))?(?:\s*(?:பேசு|பேசுங்க|பேசுங்கள்|பேசலாம்|சொல்லு|சொல்லுங்க))?`;
const ASK_EN = String.raw`(?:(?:speak|talk|reply|answer|switch(?:\s+to)?|change(?:\s+to)?)\s+)?(?:in\s+)?english(?:\s+please)?|(?:இங்கிலீஷ்|இங்கிலிஷ்|ஆங்கிலம்|ஆங்கிலத்தில்)(?:\s*(?:ல|லே|ில்|இல்))?(?:\s*(?:பேசு|பேசுங்க|பேசுங்கள்|பேசலாம்|சொல்லு|சொல்லுங்க))?`;
const VANAKKAM = String.raw`v[ae]n+[ae]k+[ae]m+|vanakam|வணக்கம்`;
const clean = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{M}\p{N}\s]/gu, " ").replace(/\s+/g, " ").trim();

/** A request to change the listening language, or null. `direct` = said without the wake word, while JARVIS was
 * already listening for a command. Only whole utterances count, so "translate this into Tamil" is not a switch. */
export function languageSwitch(text: string, direct = false): typeof TA | typeof EN | null {
  const t = clean(text);
  const wake = direct ? `(?:${WAKE}\\s*)?` : WAKE;
  if (new RegExp(`^(?:${VANAKKAM})\\s+${WAKE}$|^${WAKE}\\s+(?:${VANAKKAM})$`, "iu").test(t)) return TA;
  if (new RegExp(`^${wake}\\s*(?:${ASK_TA})$|^(?:${ASK_TA})\\s+${WAKE}$`, "iu").test(t)) return TA;
  if (new RegExp(`^${wake}\\s*(?:${ASK_EN})$|^(?:${ASK_EN})\\s+${WAKE}$`, "iu").test(t)) return EN;
  return null;
}

/** After a request in `text` was recognised while listening in `current`: the language to listen in next. */
export function nextListenLang(current: string, text: string): string {
  const words = text.trim().split(/\s+/).filter((w) => /\p{L}/u.test(w));
  if (current === TA && words.length >= 2 && count(TAMIL, text) === 0 && count(LATIN, text) > 0) return EN; // spoke English
  return current;
}

/** The listening language implied by something the user typed (they write in the language they want). */
export function typedLang(current: string, text: string): string {
  const s = scriptOf(text);
  return s === "ta" ? TA : s === "en" && text.trim().split(/\s+/).length >= 2 ? EN : current;
}

/** What JARVIS says after switching. */
export function switchedMessage(lang: string): string {
  return lang === TA ? "சரி, தமிழில் பேசலாம். சொல்லுங்கள்." : "Okay, English. Go ahead.";
}
