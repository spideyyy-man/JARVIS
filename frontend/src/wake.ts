/** Wake-word matching that works in any script (Latin, Devanagari, Tamil, ...).
 *
 * The old matcher kept only a-z/0-9, so with Hindi or Tamil speech recognition every utterance
 * normalised to "" and the wake word could never be heard. Word boundaries are Unicode-aware here
 * (`\b` is ASCII-only in JavaScript, and Indic scripts use combining marks inside words). */

const WORD_CHAR = "[\\p{L}\\p{M}\\p{N}]";

/** Lower-case, punctuation to spaces, collapse whitespace - keeping letters and marks of every script. */
export const norm = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{M}\p{N}\s]/gu, " ").replace(/\s+/g, " ").trim();

export const escapeRe = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** How speech recognition in each language tends to write "Jarvis". Used only when the wake word is "jarvis". */
export const WAKE_ALIASES: Record<string, string[]> = {
  hi: ["जार्विस", "जारविस", "जार्वीस"],
  mr: ["जार्विस", "जारविस"],
  ta: ["ஜார்விஸ்", "ஜார்விஸ", "ஜார்வீஸ்"],
  te: ["జార్విస్", "జార్విస"],
  kn: ["ಜಾರ್ವಿಸ್", "ಜಾರ್ವಿಸ"],
  ml: ["ജാർവിസ്", "ജാര്വിസ്"],
  bn: ["জার্ভিস", "জারভিস"],
  gu: ["જાર્વિસ", "જારવિસ"],
  pa: ["ਜਾਰਵਿਸ"],
  ur: ["جاروس", "جارویس"],
};

/** Speech-recognition languages offered in Settings. "" = the browser's own language. */
export const SPEECH_LANGUAGES: { code: string; label: string }[] = [
  { code: "", label: "Browser default" },
  { code: "en-IN", label: "English (India)" },
  { code: "en-US", label: "English (US)" },
  { code: "en-GB", label: "English (UK)" },
  { code: "hi-IN", label: "हिन्दी · Hindi" },
  { code: "ta-IN", label: "தமிழ் · Tamil" },
  { code: "te-IN", label: "తెలుగు · Telugu" },
  { code: "kn-IN", label: "ಕನ್ನಡ · Kannada" },
  { code: "ml-IN", label: "മലയാളം · Malayalam" },
  { code: "mr-IN", label: "मराठी · Marathi" },
  { code: "bn-IN", label: "বাংলা · Bengali" },
  { code: "gu-IN", label: "ગુજરાતી · Gujarati" },
  { code: "pa-IN", label: "ਪੰਜਾਬੀ · Punjabi" },
  { code: "ur-IN", label: "اردو · Urdu" },
];

/** The wake word(s) to listen for: the user's (comma-separated alternatives allowed) plus, for the default
 * "jarvis", its spellings in the recognition language's script. */
export function wakeWords(wake: string, lang: string): string[] {
  const own = (wake || "").split(",").map((w) => norm(w)).filter(Boolean);
  const words = own.length ? own : ["jarvis"];
  if (words.includes("jarvis")) words.push(...(WAKE_ALIASES[(lang || "").split("-")[0].toLowerCase()] ?? []));
  return [...new Set(words)];
}

/** Matches an optional "hey/ok/hi" plus any wake word as a whole word. `strip` also eats trailing punctuation
 * (including the Devanagari danda) so the command that follows can be cut out cleanly. */
export function wakeRegex(words: string[], strip = false): RegExp {
  const alt = words.map((w) => escapeRe(norm(w))).filter(Boolean).join("|") || "jarvis";
  const tail = strip ? "[\\s,.!?:;।॥-]*" : "";
  return new RegExp(`(?:(?:hey|ok|okay|hi)[\\s,]+)?(?<!${WORD_CHAR})(?:${alt})(?!${WORD_CHAR})${tail}`, "iu");
}
