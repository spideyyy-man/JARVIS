"""Runtime configuration. Everything comes from environment variables (or a .env file);
nothing secret is ever hardcoded or written to the database."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing environment wins."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _paths(value: str) -> list[Path]:
    return [Path(p).expanduser().resolve() for p in value.split(os.pathsep) if p.strip()]


@dataclass
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    # LLM
    llm_provider: str = "auto"  # auto | anthropic | openai_compat | offline
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    openai_base_url: str = ""
    openai_model: str = ""
    max_agent_steps: int = 12
    max_tools: int = 12  # tool definitions shown to a (non-offline) model per step; 0 = all
    llm_timeout: float = 300.0  # seconds per model call before failing over
    openai_stream: bool = True
    # Files JARVIS may see at all. Reads outside these roots are refused.
    allowed_roots: list[Path] = field(default_factory=list)
    # Research
    search_provider: str = "duckduckgo"  # duckduckgo | wikipedia
    # Voice (server-side providers are optional; the browser handles voice by default)
    stt_provider: str = "browser"
    tts_provider: str = "browser"
    voice_base_url: str = ""
    wake_word: str = "jarvis"
    stt_model: str = "whisper-1"
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"
    # Image generation (optional, local): automatic1111 | openai_compat | none
    image_provider: str = "none"
    image_base_url: str = ""
    image_model: str = ""
    # Embeddings
    embedder: str = "hashing"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jarvis.db"

    @property
    def trash_dir(self) -> Path:
        return self.data_dir / "trash"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    def api_token(self) -> str:
        """Per-install token protecting the local API from other origins/processes."""
        env = os.environ.get("JARVIS_API_TOKEN")
        if env:
            return env
        path = self.data_dir / "api_token"
        if not path.exists():
            path.write_text(secrets.token_urlsafe(32))
            path.chmod(0o600)
        return path.read_text().strip()


def load_settings(env_file: Path | None = None) -> Settings:
    _load_dotenv(env_file or Path.cwd() / ".env")
    env = os.environ.get
    data_dir = Path(env("JARVIS_DATA_DIR", "~/.jarvis")).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    s = Settings(
        data_dir=data_dir,
        host=env("JARVIS_HOST", "127.0.0.1"),
        port=int(env("JARVIS_PORT", "8765")),
        llm_provider=env("JARVIS_LLM_PROVIDER", "auto"),
        anthropic_model=env("JARVIS_ANTHROPIC_MODEL", "claude-opus-5-5"),
        anthropic_effort=env("JARVIS_ANTHROPIC_EFFORT", "medium"),
        openai_base_url=env("JARVIS_OPENAI_BASE_URL", ""),
        openai_model=env("JARVIS_OPENAI_MODEL", ""),
        max_agent_steps=int(env("JARVIS_MAX_AGENT_STEPS", "12")),
        max_tools=int(env("JARVIS_MAX_TOOLS", "12")),
        llm_timeout=float(env("JARVIS_LLM_TIMEOUT", "300")),
        openai_stream=env("JARVIS_OPENAI_STREAM", "1") != "0",
        allowed_roots=_paths(env("JARVIS_ALLOWED_ROOTS", str(Path.home()))),
        search_provider=env("JARVIS_SEARCH_PROVIDER", "duckduckgo"),
        stt_provider=env("JARVIS_STT_PROVIDER", "browser"),
        tts_provider=env("JARVIS_TTS_PROVIDER", "browser"),
        voice_base_url=env("JARVIS_VOICE_BASE_URL", ""),
        wake_word=env("JARVIS_WAKE_WORD", "jarvis"),
        embedder=env("JARVIS_EMBEDDER", "hashing"),
        stt_model=env("JARVIS_STT_MODEL", "whisper-1"),
        tts_model=env("JARVIS_TTS_MODEL", "tts-1"),
        tts_voice=env("JARVIS_TTS_VOICE", "alloy"),
        image_provider=env("JARVIS_IMAGE_PROVIDER", "none"),
        image_base_url=env("JARVIS_IMAGE_BASE_URL", ""),
        image_model=env("JARVIS_IMAGE_MODEL", ""),
    )
    for d in (s.trash_dir, s.reports_dir, s.images_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


def get_secret(name: str) -> str | None:
    """Secrets resolve from the environment first, then the OS keyring. Never from the DB."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        import keyring

        return keyring.get_password("jarvis", name)
    except Exception:
        return None


def store_secret(name: str, value: str) -> bool:
    """Store in the OS keyring. Returns False (and stores nothing) when no secure backend exists."""
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring

        if isinstance(keyring.get_keyring(), FailKeyring):
            return False
        keyring.set_password("jarvis", name, value)
        return True
    except Exception:
        return False
