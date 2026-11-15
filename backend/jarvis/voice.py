"""Speech provider abstraction.

Default ("browser"): recognition, wake word and synthesis run in the browser via the Web Speech
API - nothing leaves the UI process and the backend only sees text.
Optional server-side providers speak the OpenAI audio protocol (/audio/transcriptions,
/audio/speech), which is also implemented by local servers (faster-whisper-server/Speaches,
Kokoro-FastAPI, LocalAI). Configure with JARVIS_STT_PROVIDER / JARVIS_TTS_PROVIDER=openai_compat
and JARVIS_VOICE_BASE_URL."""
from __future__ import annotations

from typing import Protocol

import httpx

from .config import Settings, get_secret


class STTProvider(Protocol):
    name: str

    async def transcribe(self, audio: bytes, filename: str, language: str | None = None) -> str: ...


class TTSProvider(Protocol):
    name: str

    async def synthesize(self, text: str, voice: str | None = None) -> tuple[bytes, str]: ...


class OpenAICompatSpeech:
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str | None, stt_model: str = "whisper-1", tts_model: str = "tts-1", transport: httpx.AsyncBaseTransport | None = None, voice: str = "alloy"):
        self.base_url, self.api_key, self.stt_model, self.tts_model, self.transport, self.voice = base_url.rstrip("/"), api_key, stt_model, tts_model, transport, voice

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def transcribe(self, audio: bytes, filename: str, language: str | None = None) -> str:
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as c:
            r = await c.post(f"{self.base_url}/audio/transcriptions", headers=self._headers(), files={"file": (filename, audio)},
                             data={"model": self.stt_model, **({"language": language} if language else {})})
        r.raise_for_status()
        return r.json()["text"]

    async def synthesize(self, text: str, voice: str | None = None) -> tuple[bytes, str]:
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as c:
            r = await c.post(f"{self.base_url}/audio/speech", headers=self._headers(),
                             json={"model": self.tts_model, "input": text, "voice": voice or self.voice, "response_format": "mp3"})
        r.raise_for_status()
        return r.content, r.headers.get("content-type", "audio/mpeg")


def build_voice(settings: Settings) -> tuple[STTProvider | None, TTSProvider | None]:
    """Returns (stt, tts); None means 'handled in the browser'."""
    server = None
    if "openai_compat" in (settings.stt_provider, settings.tts_provider) and settings.voice_base_url:
        server = OpenAICompatSpeech(settings.voice_base_url, get_secret("JARVIS_VOICE_API_KEY") or get_secret("OPENAI_API_KEY"),
                                    settings.stt_model, settings.tts_model, voice=settings.tts_voice)
    return (server if settings.stt_provider == "openai_compat" else None, server if settings.tts_provider == "openai_compat" else None)
