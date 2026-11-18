"""Local machine translation for talking to a small model in Tamil (and other Indian languages).

Measured on the owner's laptop (2026-11-18): the local chat models understand Tamil well enough and know the answers in
English, but write poor Tamil - "New Delhi" came back as a made-up word, a poem as gibberish. So for a Tamil request
JARVIS translates it to English with a dedicated translation model, lets the chat model answer in English (with all its
tools), and translates the answer back. The translation model is NLLB-200 distilled 600M (Meta, CC-BY-NC 4.0 -
fine for personal use) converted to CTranslate2 int8: ~620 MB on disk, ~1 s per sentence on this CPU, loaded only
while in use. Optional: without the model or the `ctranslate2`/`sentencepiece` packages, `available()` is False.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

NLLB = {"en": "eng_Latn", "ta": "tam_Taml", "hi": "hin_Deva", "te": "tel_Telu", "kn": "kan_Knda", "ml": "mal_Mlym",
        "mr": "mar_Deva", "bn": "ben_Beng", "gu": "guj_Gujr", "pa": "pan_Guru", "ur": "urd_Arab"}
SCRIPTS = {"ta": r"[஀-௿]", "hi": r"[ऀ-ॿ]", "te": r"[ఀ-౿]", "kn": r"[ಀ-೿]",
           "ml": r"[ഀ-ൿ]", "bn": r"[ঀ-৿]", "gu": r"[઀-૿]", "pa": r"[਀-੿]"}
_SENTENCE = re.compile(r"(?<=[.!?।])\s+")
_PREFIX = re.compile(r"^(\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)?)(.*)$")


def language_of(text: str) -> str | None:
    """The Indian language `text` is written in (by script), or None for English/other. Hindi and Marathi share a
    script; it is reported as Hindi."""
    best, best_n = None, 0
    for lang, pattern in SCRIPTS.items():
        n = len(re.findall(pattern, text))
        if n > best_n:
            best, best_n = lang, n
    return best


class Translator:
    def __init__(self, model_dir: Path, idle_unload: float = 600.0, threads: int = 4):
        self.model_dir, self.idle_unload, self.threads = Path(model_dir), idle_unload, threads
        self._model = self._sp = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def available(self) -> bool:
        if not ((self.model_dir / "model.bin").is_file() and (self.model_dir / "sentencepiece.bpe.model").is_file()):
            return False
        try:
            import ctranslate2  # noqa: F401
            import sentencepiece  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self):
        if self._model is None:
            import ctranslate2
            import sentencepiece

            self._sp = sentencepiece.SentencePieceProcessor(model_file=str(self.model_dir / "sentencepiece.bpe.model"))
            self._model = ctranslate2.Translator(str(self.model_dir), device="cpu", compute_type="int8", intra_threads=self.threads)
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.idle_unload, self.unload)
        self._timer.daemon = True
        self._timer.start()

    def unload(self) -> None:
        """Give the memory back (the next translation reloads it in a second or two)."""
        with self._lock:
            self._model = self._sp = None

    def _batch(self, sentences: list[str], src: str, tgt: str) -> list[str]:
        sources = [[NLLB[src], *self._sp.encode(s, out_type=str), "</s>"] for s in sentences]
        results = self._model.translate_batch(sources, target_prefix=[[NLLB[tgt]]] * len(sources), beam_size=2,
                                              max_decoding_length=256, repetition_penalty=1.1)
        return [self._sp.decode(r.hypotheses[0][1:]) for r in results]

    def translate(self, text: str, src: str, tgt: str) -> str:
        """Translate line by line, sentence by sentence, keeping Markdown structure; code blocks and lines that are
        code, links or identifiers (backticks, URLs) are left exactly as they are."""
        if src == tgt or not text.strip():
            return text
        with self._lock:
            self._load()
            lines, jobs, in_code = text.split("\n"), [], False
            for i, line in enumerate(lines):
                if line.strip().startswith("```"):
                    in_code = not in_code
                    continue
                if in_code or not re.search(r"[^\W\d_]", line) or "`" in line or re.search(r"https?://", line):
                    continue
                prefix, body = _PREFIX.match(line).groups()
                parts = [p for p in _SENTENCE.split(body.strip()) if p]
                jobs.append((i, prefix, parts))
            flat = [p for _, _, parts in jobs for p in parts]
            done = iter(self._batch(flat, src, tgt) if flat else [])
            for i, prefix, parts in jobs:
                lines[i] = prefix + " ".join(next(done) for _ in parts)
            return "\n".join(lines)


def measure(translator: Translator, text: str, src: str, tgt: str) -> tuple[str, float]:
    t0 = time.time()
    return translator.translate(text, src, tgt), time.time() - t0


MODEL_URL = "https://huggingface.co/JustFrederik/nllb-200-distilled-600M-ct2-int8/resolve/main/"
MODEL_FILES = {"config.json": 159, "shared_vocabulary.txt": 2568098, "sentencepiece.bpe.model": 4852054, "model.bin": 622595991}


def download_model(dest: Path, log=print) -> bool:
    """Fetch the translation model (~630 MB) into `dest`; resumes a partial download and checks every file's size."""
    import httpx

    dest.mkdir(parents=True, exist_ok=True)
    for name, size in MODEL_FILES.items():
        path = dest / name
        for _ in range(20):
            have = path.stat().st_size if path.exists() else 0
            if have == size:
                break
            try:
                with httpx.stream("GET", MODEL_URL + name, headers={"Range": f"bytes={have}-"} if have else {},
                                  follow_redirects=True, timeout=httpx.Timeout(60, connect=20)) as r:
                    r.raise_for_status()
                    with open(path, "ab" if have and r.status_code == 206 else "wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
            except httpx.HTTPError as e:
                log(f"   {name}: retrying after {type(e).__name__}")
        log(f"   {name}: {'ok' if path.exists() and path.stat().st_size == size else 'INCOMPLETE'}")
    return all((dest / n).is_file() and (dest / n).stat().st_size == s for n, s in MODEL_FILES.items())


if __name__ == "__main__":  # python -m jarvis.translate   -> download the model where JARVIS looks for it
    import sys

    from .config import load_settings

    s = load_settings()
    target = s.translator_dir or s.data_dir / "models" / "nllb-200-distilled-600M-ct2-int8"
    print(f"Translation model for Tamil and other Indian languages -> {target}")
    sys.exit(0 if download_model(target) else 1)
