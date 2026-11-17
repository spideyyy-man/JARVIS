# Running JARVIS entirely on local models

No API key is required. Each capability is switched on independently in `.env`; anything you leave
out falls back to the built-in behaviour.

| Capability | Without a local model | With a local model | Protocol JARVIS speaks |
|---|---|---|---|
| Reasoning / conversation | Built-in rule engine (fixed request shapes) | Any chat model with tool calling | OpenAI chat completions |
| Speech-to-text + wake word | Browser speech service (Chrome/Edge) | Your STT model | `POST {base}/audio/transcriptions` |
| Text-to-speech | Browser voices | Your TTS model | `POST {base}/audio/speech` |
| Image generation | Not available | Your image model | Automatic1111 `POST /sdapi/v1/txt2img`, or `POST {base}/images/generations` |
| Embeddings (search quality) | Lexical hashing embedder | Any embedding model | `POST {base}/embeddings` |

## Language model (e.g. Ollama, LM Studio, llama.cpp server, vLLM)

```bash
JARVIS_LLM_PROVIDER=openai_compat
JARVIS_OPENAI_BASE_URL=http://127.0.0.1:11434/v1
JARVIS_OPENAI_MODEL=<model name>
```

The model must support tool/function calling, otherwise the agents can talk but not act. If the
server is down, JARVIS falls back to the rule engine and tells you.

## Speech (e.g. Speaches / faster-whisper-server, whisper.cpp server, Kokoro-FastAPI, LocalAI, openedai-speech)

```bash
JARVIS_STT_PROVIDER=openai_compat
JARVIS_TTS_PROVIDER=openai_compat
JARVIS_VOICE_BASE_URL=http://localhost:8000/v1
JARVIS_STT_MODEL=<stt model name>
JARVIS_TTS_MODEL=<tts model name>
JARVIS_TTS_VOICE=<voice name>
```

One base URL serves both. If your STT and TTS run on different ports, set only the provider you
have on that URL and leave the other on `browser`. With STT set, the browser records the microphone,
detects where an utterance ends by loudness, and uploads it; the wake word is then matched on your
model's transcript, so no audio leaves your machine. If your models are command-line only (plain
Whisper or Piper binaries), put one of the servers above in front of them.

## Images (Stable Diffusion WebUI / Forge / SD.Next with `--api`, or LocalAI)

```bash
JARVIS_IMAGE_PROVIDER=automatic1111
JARVIS_IMAGE_BASE_URL=http://127.0.0.1:7860
```

Then ask: "generate an image of a red fox in the snow". The picture appears in the conversation and
is saved under `~/.jarvis/images`. ComfyUI's native workflow API is not supported; use a bridge that
exposes one of the two protocols above.

## How this was tested

`frontend/e2e/local_models.mjs` runs the real UI and backend against `frontend/e2e/stub_local_models.py`,
a stand-in that implements these protocols and returns canned results. That proves the wiring
(capture, upload, playback, image display). It does **not** prove compatibility with any particular
model server - field names such as the voice or model name vary between them.

```bash
python frontend/e2e/stub_local_models.py --mic-wav /tmp/mic.wav
python frontend/e2e/stub_local_models.py 9911 &
JARVIS_PORT=8767 JARVIS_API_TOKEN=dev-token JARVIS_STT_PROVIDER=openai_compat JARVIS_TTS_PROVIDER=openai_compat \
  JARVIS_VOICE_BASE_URL=http://127.0.0.1:9911/v1 JARVIS_IMAGE_PROVIDER=automatic1111 \
  JARVIS_IMAGE_BASE_URL=http://127.0.0.1:9911 backend/.venv/bin/python -m jarvis &
MIC_WAV=/tmp/mic.wav node frontend/e2e/local_models.mjs
```
