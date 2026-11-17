from pathlib import Path

from jarvis.config import _dotenv_value, load_settings

EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def test_dotenv_values_drop_inline_comments_but_keep_quoted_text_and_url_fragments():
    assert _dotenv_value("12                   # tools per step") == "12"
    assert _dotenv_value("                     # e.g. imap.gmail.com") == ""
    assert _dotenv_value("") == ""
    assert _dotenv_value('"pa ss # word"   # note') == "pa ss # word"
    assert _dotenv_value("'single'") == "single"
    assert _dotenv_value("http://host/cal.ics#frag") == "http://host/cal.ics#frag"


def test_the_shipped_env_example_loads_as_is(tmp_path, monkeypatch):
    """Copying .env.example to .env (what setup.ps1 does) must give a working configuration."""
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            monkeypatch.delenv(line.split("=", 1)[0].strip(), raising=False)
    env = tmp_path / ".env"
    env.write_text(EXAMPLE.read_text(encoding="utf-8").replace("JARVIS_DATA_DIR=~/.jarvis", f"JARVIS_DATA_DIR={tmp_path / 'data'}"), encoding="utf-8")
    s = load_settings(env)
    assert s.max_tools == 12 and s.llm_timeout == 300 and s.port == 8765 and s.smtp_port == 587
    assert s.llm_provider == "auto" and s.agent_mode == "auto" and s.host == "127.0.0.1"
    assert s.calendar_ics == "" and s.imap_host == "" and s.openai_base_url == ""
    assert s.data_dir == (tmp_path / "data").resolve()
    assert s.allowed_roots == [Path.home().resolve()]  # blank JARVIS_ALLOWED_ROOTS = the documented default
