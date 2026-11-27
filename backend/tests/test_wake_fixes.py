"""Wake word fixes from the owner's recordings (2026-11-27): the owner's "Jarvis" was caught 4/10 by the personal model
(threshold 0.99) and 7/10 by Whisper's exact spelling; combining them (personal >= 0.85 + a Whisper spelling that sounds
like Jarvis) caught 9/10 with no false wake on 67 room clips and requests. Also: retraining was counted in memory and
never happened across restarts, and Whisper's misses were fed to training as negatives."""
import json
import threading
import time
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from jarvis import listen
from jarvis.listen import PERSONAL_CANDIDATE, RETRAIN_AFTER, LocalListener, sounds_like_jarvis


@pytest.mark.parametrize("text,ok", [
    ("Jarvis.", True), ("And Jodabis.", True), ("Darvish", True), ("Jarrus", True), ("Dharis", True),
    ("Masters.", False), ("The service was nervous", False), ("JavaScript is fun", False), ("Good job, guys.", False),
    ("Got it.", False), ("Hello, man.", False),
])
def test_sounds_like_jarvis(text, ok):
    assert (sounds_like_jarvis(text) is not None) is ok


def test_the_rest_after_a_sound_alike_is_kept():
    text = "Jodabis, what time is it?"
    assert text[sounds_like_jarvis(text):].strip(" ,") == "what time is it?"


class Model:
    def __init__(self, text, lp=-0.2):
        self.text, self.lp = text, lp

    def transcribe(self, audio, language=None, **opts):
        return iter([SimpleNamespace(text=self.text, no_speech_prob=0.02, avg_logprob=self.lp)]), None


def _listener(tmp_path, text="Jarvis."):
    lst = LocalListener(tmp_path / "models", lambda *a: None, lambda *a: None, english_size="base.en")
    lst._whisper_ready.set()
    lst._whisper = lst._english = Model(text)
    lst.keep_samples = False
    return lst


def test_a_sound_alike_counts_only_when_the_voice_model_agrees(tmp_path):
    lst = _listener(tmp_path, "And Jodabis.")
    audio = np.zeros(16000, "float32")
    assert lst._verify_wake(audio) is None  # Whisper alone: not "Jarvis"
    rest, info = lst._verify_wake(audio, loose=True)
    assert rest == "" and info["matched"] == "sound-alike"
    lst._english = Model("Jarvis.")
    assert lst._verify_wake(audio)[1]["matched"] == "exact"


def _run_worker(lst, items):
    for it in items:
        lst._second.put(it)
    t = threading.Thread(target=lst._second_chance_worker, daemon=True)
    t.start()
    deadline = time.time() + 5
    while not lst._second.empty() and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    lst._stop.set()
    t.join(2)


def test_a_likely_call_wakes_via_the_second_chance_but_does_not_teach_the_model(tmp_path):
    lst = _listener(tmp_path, "And Jodabis.")
    _run_worker(lst, [(np.zeros(16000, "float32"), None, 0.9)])
    assert lst._late is not None and lst._late[1]["matched"] == "sound-alike"
    assert lst.personal.positives() == []  # self-confirmed calls are not saved as training examples


def test_an_exact_call_wakes_and_is_kept_as_an_example(tmp_path):
    lst = _listener(tmp_path, "Jarvis.")
    _run_worker(lst, [(np.zeros(16000, "float32"), None, 0.1)])
    assert lst._late is not None and [p.name.split("-")[0] for p in lst.personal.positives()] == ["call"]


def test_without_the_voice_model_a_sound_alike_does_not_wake(tmp_path):
    lst = _listener(tmp_path, "And Jodabis.")
    _run_worker(lst, [(np.zeros(16000, "float32"), None, 0.2)])
    assert lst._late is None


def _fake_model(folder, age_s=100):
    folder.mkdir(parents=True, exist_ok=True)
    np.savez(folder / "model.npz", w=np.zeros(4, np.float32), b=np.float32(0), mean=np.zeros(4, np.float32), std=np.ones(4, np.float32),
             threshold=np.float32(0.99))
    (folder / "report.json").write_text("{}", encoding="utf-8")
    past = time.time() - age_s
    import os
    os.utime(folder / "model.npz", (past, past))


def test_retraining_is_counted_from_saved_calls_and_survives_restarts(tmp_path, monkeypatch):
    lst = _listener(tmp_path)
    started = []
    monkeypatch.setattr(lst, "train_personal", lambda reason="": started.append(reason))
    assert lst.retrain_if_due() is False  # no model yet: enrolment comes first
    _fake_model(lst.personal.folder)
    assert lst.personal.load()
    for _ in range(RETRAIN_AFTER - 1):
        lst.personal.add_positive(np.zeros(8000, "float32"), "call")
        time.sleep(0.002)
    assert lst.retrain_if_due() is False and lst.personal.new_calls() == RETRAIN_AFTER - 1
    restarted = _listener(tmp_path)  # a new process: the count comes from the files, not memory
    monkeypatch.setattr(restarted, "train_personal", lambda reason="": started.append(reason))
    restarted.personal.add_positive(np.zeros(8000, "float32"), "call")
    assert restarted.personal.new_calls() == RETRAIN_AFTER and restarted.retrain_if_due() is True
    deadline = time.time() + 2
    while not started and time.time() < deadline:
        time.sleep(0.02)
    assert started == ["new calls"]


def _wav(path, seconds=1.0):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.zeros(int(16000 * seconds), np.int16).tobytes())


def test_calls_whisper_missed_are_not_trained_as_negatives(tmp_path, monkeypatch):
    lst = _listener(tmp_path)
    samples = tmp_path / "wake-samples"
    samples.mkdir()
    for name, note in (("room", {"jarvis": False, "personal": 0.3}), ("missed-call", {"jarvis": False, "personal": 0.95}),
                       ("call", {"jarvis": True, "personal": 0.99})):
        _wav(samples / f"{name}.wav")
        (samples / f"{name}.json").write_text(json.dumps(note), encoding="utf-8")
    seen = {}
    monkeypatch.setattr("jarvis.wake_personal.download_general", lambda *a, **k: False)  # no 176 MB download in tests
    monkeypatch.setattr(lst.personal, "train", lambda negatives, general=None: seen.setdefault("n", len(negatives)) and {} or {})
    lst.train_personal("test")
    assert seen["n"] == 1  # only the room clip: the missed call and the confirmed call are not negatives
    assert PERSONAL_CANDIDATE == 0.85 and listen.SECOND_CHANCE_MAX == 2.5
