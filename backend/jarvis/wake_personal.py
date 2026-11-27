"""A wake detector trained on the owner's own "Jarvis".

Why: on the owner's live calls the ready-made keyword spotter caught 0 of 2 at every setting, and plain "Jarvis" was
only caught by the slower Whisper check (~1 s late; 2026-11-21). Commercial assistants solve this with a model
trained on the user's voice. This one is small and needs no deep-learning library:

- Features: the speech embeddings openWakeWord already computes for every 80 ms of microphone audio (Google's speech
  embedding model, 96 numbers per frame; the last 16 frames = ~1.3 s are one window). At run time they come for free
  from the "hey jarvis" model's pass (`Model.preprocessor.get_features(16)`).
- Training: the owner's "Jarvis" clips (enrolment: say it ~10 times; plus calls the Whisper check confirmed later),
  augmented (volume, room noise, small shifts), against every window of room speech and ordinary requests. A
  logistic regression on the 1536 numbers of a window - trained in seconds with numpy.
- Threshold: chosen on held-out windows so that no held-out room speech reaches it, with a margin.

Files (this computer only): <data>/wake-personal/model.npz, report.json, positives/*.wav.
"""
from __future__ import annotations

import json
import time
import wave
from pathlib import Path

import numpy as np

RATE, FRAME, WINDOW = 16000, 1280, 16  # 80 ms frames; 16 embedding frames per window
MIN_POSITIVES = 6
MIN_THRESHOLD = 0.8  # 0.55 let a same-voice "Javascript is fun" through (2026-11-21)


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        a, sr = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768, w.getframerate()
    if sr != RATE:
        a = np.interp(np.arange(0, len(a), sr / RATE), np.arange(len(a)), a).astype(np.float32)
    return a


def save_wav(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


class Featurizer:
    """Streams audio through openWakeWord's feature pipeline exactly as the listener does, window by window."""

    def __init__(self):
        from openwakeword.utils import AudioFeatures

        self.af = AudioFeatures(inference_framework="onnx")

    def windows(self, audio: np.ndarray) -> np.ndarray:
        """One (16*96) window per 80 ms frame of `audio` (after 1 s of silence to fill the buffers)."""
        self.af.reset()
        pcm = (np.clip(np.concatenate([np.zeros(RATE, np.float32), audio]), -1, 1) * 32767).astype(np.int16)
        out = []
        for i in range(0, len(pcm) - FRAME + 1, FRAME):
            self.af(pcm[i:i + FRAME])
            if i >= RATE:  # skip the windows that are only the padding
                out.append(self.af.get_features(WINDOW).reshape(-1))
        return np.array(out, np.float32) if out else np.zeros((0, WINDOW * 96), np.float32)


def speech_end(audio: np.ndarray) -> float:
    """Seconds at which the loud part of a short clip ends (energy above 20% of its peak)."""
    n = len(audio) // FRAME
    if n == 0:
        return 0.0
    rms = np.sqrt(np.mean(audio[: n * FRAME].reshape(n, FRAME) ** 2, axis=1))
    loud = np.where(rms >= 0.2 * rms.max())[0]
    return float((loud[-1] + 1) * FRAME / RATE) if len(loud) else 0.0


def positive_windows(feat: Featurizer, clip: np.ndarray) -> np.ndarray:
    """The windows that end around the end of the word (where the listener should fire)."""
    w = feat.windows(clip)
    end = int(round(speech_end(clip) / (FRAME / RATE)))
    lo, hi = max(0, end - 2), min(len(w), end + 4)
    return w[lo:hi]


def augment(clip: np.ndarray, noises: list[np.ndarray], rng: np.random.Generator) -> list[np.ndarray]:
    """Variants of one "Jarvis": quieter, louder, with room noise behind it, starting a little later."""
    out = [clip, clip * 0.5, np.clip(clip * 1.6, -1, 1)]
    for _ in range(3):
        if noises:
            n = noises[rng.integers(len(noises))]
            if len(n) >= len(clip):
                start = rng.integers(0, len(n) - len(clip) + 1)
                gain = 10 ** (-rng.uniform(8, 20) / 20) * (np.sqrt(np.mean(clip ** 2)) / (np.sqrt(np.mean(n ** 2)) + 1e-6))
                out.append(np.clip(clip + n[start:start + len(clip)] * gain, -1, 1))
    for shift in (0.15, 0.3):
        out.append(np.concatenate([np.zeros(int(shift * RATE), np.float32), clip]))
    return out


GENERAL_FILE = "validation_set_features.npy"  # openWakeWord's ~11 h of varied speech/noise as embedding frames (CC-BY-NC-SA)
GENERAL_URL = "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/" + GENERAL_FILE
GENERAL_SHA256 = "a56a8a0f8e0efb91900acc6de4c0cdf4c564842e8475a7d49b36c039e17a690f"


def general_windows(path: Path, n: int, seed: int, part: tuple[float, float] = (0.0, 1.0)) -> np.ndarray:
    """`n` windows of 16 consecutive frames from the general speech/noise stream (a slice of it, so training and
    threshold windows never overlap)."""
    frames = np.load(path, mmap_mode="r")
    lo, hi = int(part[0] * (len(frames) - WINDOW)), int(part[1] * (len(frames) - WINDOW))
    starts = np.sort(np.random.default_rng(seed).integers(lo, hi, n))
    return np.stack([np.asarray(frames[s:s + WINDOW]).reshape(-1) for s in starts]).astype(np.float32)


def download_general(dest: Path, log=print) -> bool:
    """The general negatives (~176 MB, checksum-checked) - needed only for training."""
    import hashlib

    import httpx

    path = Path(dest) / GENERAL_FILE
    if path.is_file():
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    try:
        with httpx.stream("GET", GENERAL_URL, follow_redirects=True, timeout=httpx.Timeout(300, connect=20)) as r:
            r.raise_for_status()
            h = hashlib.sha256()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
        if h.hexdigest() != GENERAL_SHA256:
            raise ValueError("checksum mismatch")
        tmp.replace(path)
        return True
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        log(f"   general wake negatives: failed ({type(e).__name__}: {e})")
        return False


def hard_negatives(clip: np.ndarray) -> list[np.ndarray]:
    """Parts of the owner's own "Jarvis" that must NOT wake it: the first half alone ("Jar-" - like "Javascript"), the
    second half alone, and the word backwards. Without these a same-voice "Javascript is fun" woke it (2026-11-21)."""
    end = int(speech_end(clip) * RATE) or len(clip)
    loud = clip[:end]
    start = int(np.argmax(np.abs(loud) > 0.2 * np.abs(loud).max())) if len(loud) else 0
    word = loud[start:]
    half = len(word) // 2
    pad = np.zeros(int(0.3 * RATE), np.float32)
    return [np.concatenate([pad, word[:half], pad]), np.concatenate([pad, word[half:], pad]), np.concatenate([pad, word[::-1], pad])]


def _fit(x: np.ndarray, y: np.ndarray, l2: float = 1e-2, steps: int = 400, lr: float = 0.5) -> tuple[np.ndarray, float]:
    """Logistic regression, class-balanced, plain gradient descent (x already standardised)."""
    w, b = np.zeros(x.shape[1], np.float32), 0.0
    pos, neg = y == 1, y == 0
    weights = np.where(pos, 0.5 / max(1, pos.sum()), 0.5 / max(1, neg.sum())).astype(np.float32)
    for _ in range(steps):
        z = x @ w + b
        p = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
        g = (p - y) * weights
        w -= lr * (x.T @ g + l2 * w)
        b -= lr * float(g.sum())
    return w, b


class PersonalWake:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.w = self.b = self.mean = self.std = None
        self.threshold = 1.0
        self.info: dict = {}

    @property
    def model_path(self) -> Path:
        return self.folder / "model.npz"

    @property
    def positives_dir(self) -> Path:
        return self.folder / "positives"

    def ready(self) -> bool:
        return self.w is not None

    def load(self) -> bool:
        try:
            d = np.load(self.model_path)
            self.w, self.b, self.mean, self.std = d["w"], float(d["b"]), d["mean"], d["std"]
            self.threshold = float(d["threshold"])
            self.info = json.loads((self.folder / "report.json").read_text(encoding="utf-8"))
            return True
        except Exception:  # noqa: BLE001 - no model yet
            self.w = None
            return False

    def score(self, features: np.ndarray) -> float:
        """Probability that the last ~1.3 s ended in the owner's "Jarvis" (features: (1, 16, 96) from openWakeWord)."""
        if self.w is None:
            return 0.0
        x = (features.reshape(-1) - self.mean) / self.std
        return float(1 / (1 + np.exp(-np.clip(x @ self.w + self.b, -30, 30))))

    def add_positive(self, audio: np.ndarray, kind: str) -> Path:
        path = self.positives_dir / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.wav"
        save_wav(path, audio)
        return path

    def positives(self) -> list[Path]:
        return sorted(self.positives_dir.glob("*.wav"))

    def new_calls(self) -> int:
        """Live calls (call-*.wav) saved after the model file was written."""
        if not self.model_path.is_file():
            return 0
        trained = self.model_path.stat().st_mtime
        return sum(1 for p in self.positives_dir.glob("call-*.wav") if p.stat().st_mtime > trained)

    def train(self, negatives: list[np.ndarray], general: Path | None = None, seed: int = 7) -> dict:
        """Train on the saved positives against `negatives` (room speech, ordinary requests) and, when given, the
        general speech/noise windows in `general`. Saves and loads the model; returns a report. Raises ValueError
        when there are too few positives."""
        clips = [load_wav(p) for p in self.positives()]
        if len(clips) < MIN_POSITIVES:
            raise ValueError(f"need at least {MIN_POSITIVES} recordings of 'Jarvis', have {len(clips)}")
        t0 = time.time()
        rng = np.random.default_rng(seed)
        feat = Featurizer()
        noises = [n for n in negatives if len(n) >= RATE // 2]
        # hold out every 4th positive clip and every 4th negative clip to choose the threshold honestly
        held_pos = [c for i, c in enumerate(clips) if i % 4 == 3] or clips[-1:]
        train_pos = [c for i, c in enumerate(clips) if i % 4 != 3] or clips
        held_neg = [n for i, n in enumerate(negatives) if i % 4 == 3]
        train_neg = [n for i, n in enumerate(negatives) if i % 4 != 3]
        xp = np.concatenate([positive_windows(feat, a) for c in train_pos for a in augment(c, noises, rng)])
        hard = [h for c in train_pos for h in hard_negatives(c)]
        xn = np.concatenate([feat.windows(n) for n in train_neg + hard] or [np.zeros((0, WINDOW * 96), np.float32)])
        xg = general_windows(general, 25000, seed, (0.0, 0.7)) if general and Path(general).is_file() else np.zeros((0, WINDOW * 96), np.float32)
        x = np.concatenate([xp, xn, xg])
        y = np.concatenate([np.ones(len(xp), np.float32), np.zeros(len(xn) + len(xg), np.float32)])
        mean, std = x.mean(axis=0), x.std(axis=0) + 1e-3
        w, b = _fit((x - mean) / std, y, l2=0.05)
        del x

        def scores(windows):
            return 1 / (1 + np.exp(-np.clip(((windows - mean) / std) @ w + b, -30, 30))) if len(windows) else np.zeros(0)
        held_hard = [h for c in held_pos for h in hard_negatives(c)]
        neg_held = np.concatenate([scores(feat.windows(n)) for n in held_neg + held_hard] or [np.zeros(0)])
        gen_held = scores(general_windows(general, 10000, seed + 1, (0.7, 1.0))) if len(xg) else np.zeros(0)
        pos_held = [float(scores(feat.windows(c)).max()) for c in held_pos]
        worst_room = float(neg_held.max()) if len(neg_held) else 0.0
        worst_general = float(gen_held.max()) if len(gen_held) else 0.0
        # just above the worst held-out room speech, look-alike parts and general audio; at least MIN_THRESHOLD, and
        # below what the owner's held-out calls reach where possible
        threshold = max(MIN_THRESHOLD, max(worst_room, worst_general) + 0.05)
        if pos_held and min(pos_held) - 0.02 > threshold:
            threshold = min(threshold + 0.5 * (min(pos_held) - 0.02 - threshold), 0.99)  # halfway into the safe gap
        threshold = min(threshold, 0.99)
        caught = sum(p >= threshold for p in pos_held)
        report = {"trained_at": time.strftime("%Y-%m-%d %H:%M"), "positives": len(clips), "negative_clips": len(negatives),
                  "positive_windows": int(len(xp)), "negative_windows": int(len(xn)), "general_windows": int(len(xg)),
                  "threshold": round(threshold, 3), "held_out_calls_caught": f"{caught}/{len(pos_held)}",
                  "held_out_call_scores": [round(p, 3) for p in pos_held], "worst_room_speech_score": round(worst_room, 3),
                  "worst_general_score": round(worst_general, 3), "seconds": round(time.time() - t0, 1)}
        self.folder.mkdir(parents=True, exist_ok=True)
        np.savez(self.model_path, w=w, b=np.float32(b), mean=mean, std=std, threshold=np.float32(threshold))
        (self.folder / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        self.load()
        return report
