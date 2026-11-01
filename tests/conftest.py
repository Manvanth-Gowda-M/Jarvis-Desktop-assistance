"""Shared test setup: keep tests offline and deterministic regardless of the user's .env."""

import os

# Must be set before assistant modules are imported (module-level singletons read these).
os.environ["TTS_ENGINE"] = "sapi"
os.environ["STT_ENGINE"] = "google"
# Follow-up conversation mode re-opens the mic after answers; the coordinator tests
# assert single-capture handoff semantics, so keep it off under test.
os.environ["FOLLOW_UP_ENABLED"] = "false"
os.environ["DESKTOP_GREETING"] = "false"
os.environ["WAKE_CHIME"] = "false"

import pytest


@pytest.fixture(autouse=True)
def _isolate_llm_providers(monkeypatch):
    """Only OpenRouter (mocked per test) is visible to the agent during tests."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("TTS_ENGINE", "sapi")
    monkeypatch.setenv("STT_ENGINE", "google")
    yield
