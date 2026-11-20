# JARVIS against industry assistants

Scored 1-10 on the owner's laptop (Windows 11, no GPU, CPU often busy with other apps). "Industry" is the best of
the mainstream assistants for that dimension - Siri, Google Assistant / Gemini, Alexa, Microsoft Copilot, ChatGPT
voice - as a user experiences them, not a lab benchmark. Scores are judgements grounded in measurements where we have
them (see CLAUDE.md "Verified state"); they are meant to steer the work, not to market it.

## 2026-11-20 (before the everyday-skills round)

| Dimension | JARVIS | Industry | Evidence / gap |
|---|---|---|---|
| Wake word and hearing | 6 | 9 | openWakeWord "Hey Jarvis" + Whisper base.en with vocabulary: 2.3% word errors on Indian-English laptop-mic test clips; one mic, no far-field array, no speaker recognition |
| Response speed | 4 | 9 | instant for rule-engine commands (0.1-0.4 s); model answers ~4 s to the first word in a conversation, 10-13 s total; industry ~1-2 s (cloud GPUs) |
| Conversation (multi-turn, barge-in) | 7 | 9 | conversation mode (8 s follow-up window, no wake word), barge-in by "Hey Jarvis", history cached; no full-duplex talking over each other |
| Voice quality | 7 | 9 | Piper neural voice offline, gapless; industry voices are more expressive |
| Everyday skills (timers, alarms, weather, maths, lists, music, news) | 3 | 9 | none of timers, alarms, weather, calculator, lists or "play a song" existed |
| Computer control (files, apps, windows, browser, screen) | 8 | 6 | files with approvals and trash, Store apps, window switching, own browser, screen reading by OCR, workflows - beyond what Siri/Alexa/Google do on a PC |
| Knowledge and reasoning | 5 | 9 | Qwen3 4B local + web search; small-model mistakes are caught by nudges, not eliminated |
| Personal context (memory, calendar, email, contacts) | 5 | 8 | long-term memory, ICS calendar (read), email (not set up), no contacts |
| Privacy (local-first) | 9 | 4 | everything runs on the laptop; voice samples stay local; web search only when asked |
| Safety (approvals, audit, scopes) | 9 | 7 | every change asks; capabilities off by default; folder scope + blocked paths; audit log |
| Languages (Tamil) | 5 | 8 | Tamil commands and answers via English + NLLB; no Tamil voice; Tamil conversation mode not done |
| UI and UX | 6 | 8 | animated orb with per-pixel alpha, hover panel, approval bar; panel is basic, no timers/status widgets |
| Reliability | 6 | 9 | 287 tests; behaviour depends on CPU load (slow transcription seen under 100% CPU) |
| **Overall (unweighted)** | **6.2** | **8.0** | strongest where industry is weak (privacy, safety, PC control); weakest at the everyday basics and speed |

The biggest gap for a *personal* assistant is everyday skills: they are what people ask a voice assistant for most,
and they can be answered instantly and offline without the language model. That is the next round.

## 2026-11-20 (after the everyday-skills round)

Added (all instant, no language model; `tools/everyday.py`, 55 tests in `tests/test_everyday.py`): timers with labels
and alarms that ring on time and are spoken with a chime; weather now and tomorrow (Open-Meteo, home city by voice);
spoken maths and unit conversion (safe evaluator, no `eval`); shopping and other lists; "play <song>" (YouTube search
in the user's browser). UI: timer countdown ring on the orb and in the hover panel, timer/alarm chips with live
countdown and cancel in the window, quick-try chips for discoverability. Announcements (timers, reminders) are spoken
by JARVIS itself, so they work with the window minimised.

| Dimension | Before | After | Industry | What changed / what is still missing |
|---|---|---|---|---|
| Everyday skills | 3 | 7 | 9 | timers, alarms, weather, maths, units, lists, music now instant; still no news briefing, contacts/calls, smart home, recurring alarms |
| UI and UX | 6 | 7 | 8 | timers visible on orb, panel and window; quick-try chips; settings UI for the new voice/skills not built |
| Response speed (for everyday requests) | 4 | 6 | 9 | the most common requests no longer touch the model (0.0-0.1 s; weather ~2 s); open questions are still several seconds |
| Personal context | 5 | 6 | 8 | home city and lists are remembered; no contacts, calendar writing or email set up |
| **Overall (unweighted)** | **6.2** | **6.7** | **8.0** | |

Next most valuable, in order: a spoken news/day briefing that includes weather, timers and lists; recurring alarms
and "every weekday" reminders; Tamil for the new skills; contacts; smart-home control if the owner has devices.

## 2026-11-20 (Tamil round: Tamil works the way English does)

Added (`tests/test_tamil_voice.py`): JARVIS speaks Tamil itself with an offline voice (MMS Tamil, ~3x real time, read
back almost word for word by Whisper; English words inside Tamil replies are said by Piper), so Tamil replies open the
5 s conversation window too. "Jarvis, <Tamil request>" in one breath, Tamil follow-ups (with a language check, as Tamil
speakers answer "yes"/"okay" in English), unclear Tamil ("மன்னிக்கவும், சரியாகக் கேட்கவில்லை") and Tamil conversation ends
(நன்றி, போதும், நிறுத்து...). Timers, alarms, weather, maths, lists and "play <song>" in Tamil are instant with Tamil
replies; a timer set in Tamil rings in Tamil.

| Dimension | Before | After | Industry | What changed / what is still missing |
|---|---|---|---|---|
| Languages (Tamil) | 5 | 7 | 8 | Tamil voice, conversation mode, one-breath requests, instant everyday skills; the Tamil voice says loanwords (டைமர்) poorly; Tamil speech is slower than English (the bigger model, 3-7 s) |
| **Overall (unweighted)** | **6.7** | **6.8** | **8.0** | |
