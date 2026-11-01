import os
import shutil
import sys
from pathlib import Path

from config import (
    BASE_DIR,
    DB_PATH,
    NOTES_DIR,
    DEFAULT_FREE_MODEL,
    CONFIGURED_MODEL,
    ALLOWED_FREE_MODELS,
    validate_free_model,
)


def mask_key(key: str) -> str:
    """Mask API key for safe display in diagnostics."""
    if not key:
        return "<Not Configured>"
    key = key.strip()
    if len(key) <= 8:
        return "***"
    return key[:6] + "..." + key[-4:]


def check_python_environment() -> dict:
    """Check Python runtime version and platform."""
    version = sys.version.split()[0]
    major, minor = sys.version_info[:2]
    is_supported = (major == 3 and minor >= 11)
    return {
        "name": "Python Runtime",
        "status": "PASS" if is_supported else "WARN",
        "details": f"Python {version} ({sys.platform})",
    }


def check_sapi_tts() -> dict:
    """Check Windows SAPI speech engine."""
    try:
        import win32com.client
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        voices = speaker.GetVoices()
        count = voices.Count
        return {
            "name": "Windows SAPI TTS",
            "status": "PASS" if count > 0 else "WARN",
            "details": f"Available ({count} installed system voice(s))",
        }
    except Exception as e:
        return {
            "name": "Windows SAPI TTS",
            "status": "FAIL",
            "details": f"Unavailable: {e}",
        }


def check_microphone() -> dict:
    """Check microphone input via SpeechRecognition."""
    try:
        import speech_recognition as sr
        try:
            with sr.Microphone() as source:
                return {
                    "name": "Microphone Device",
                    "status": "PASS",
                    "details": "Microphone accessible",
                }
        except Exception as mic_err:
            return {
                "name": "Microphone Device",
                "status": "WARN",
                "details": f"Hardware access notice: {mic_err}",
            }
    except Exception as e:
        return {
            "name": "Microphone Device",
            "status": "WARN",
            "details": f"SpeechRecognition module notice: {e}",
        }


def check_sqlite_memory() -> dict:
    """Check SQLite database read/write."""
    try:
        from assistant.memory import remember_fact, recall_facts, DB_PATH
        res = remember_fact("Diagnostics health check probe")
        return {
            "name": "SQLite Memory DB",
            "status": "PASS",
            "details": f"Database verified at {DB_PATH.name}",
        }
    except Exception as e:
        return {
            "name": "SQLite Memory DB",
            "status": "FAIL",
            "details": f"Error: {e}",
        }


def check_notes_directory() -> dict:
    """Check Markdown notes directory permissions."""
    try:
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        test_file = NOTES_DIR / "_diagnostics_test.tmp"
        test_file.write_text("diagnostics probe", encoding="utf-8")
        test_file.unlink()
        return {
            "name": "Markdown Notes Storage",
            "status": "PASS",
            "details": f"Writable at {NOTES_DIR}",
        }
    except Exception as e:
        return {
            "name": "Markdown Notes Storage",
            "status": "FAIL",
            "details": f"Error: {e}",
        }


def check_applications() -> dict:
    """Check presence of common Windows applications."""
    apps = {
        "Chrome": [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            shutil.which("chrome"),
        ],
        "Notepad": [shutil.which("notepad.exe") or "notepad.exe"],
        "Calculator": [shutil.which("calc.exe") or "calc.exe"],
        "VS Code": [
            os.path.expandvars(r"%LocalAppData%\Programs\Microsoft VS Code\Code.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft VS Code\Code.exe"),
            shutil.which("code.cmd"),
            shutil.which("code.exe"),
        ],
    }

    found = []
    missing = []
    for app_name, paths in apps.items():
        if any(p and os.path.exists(p) for p in paths):
            found.append(app_name)
        else:
            missing.append(app_name)

    return {
        "name": "Windows Applications",
        "status": "PASS" if len(found) >= 2 else "WARN",
        "details": f"Found: {', '.join(found)} | Missing: {', '.join(missing) if missing else 'None'}",
    }


def check_openrouter_api() -> dict:
    """Check OpenRouter configuration and test free-model endpoint connectivity."""
    key = os.getenv("OPENROUTER_API_KEY", "")
    masked = mask_key(key)

    if not key:
        return {
            "name": "OpenRouter API Key",
            "status": "WARN",
            "details": f"Key: {masked} (Set OPENROUTER_API_KEY in .env)",
        }

    try:
        validate_free_model(CONFIGURED_MODEL)
    except ValueError as e:
        return {
            "name": "OpenRouter Model Config",
            "status": "FAIL",
            "details": str(e),
        }

    # Ping OpenRouter chat completion with a minimal free test query
    try:
        import requests
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key.strip()}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": CONFIGURED_MODEL,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            return {
                "name": "OpenRouter Free Model",
                "status": "PASS",
                "details": f"Connected to {CONFIGURED_MODEL} (Key: {masked})",
            }
        elif resp.status_code == 429:
            return {
                "name": "OpenRouter Free Model",
                "status": "WARN",
                "details": f"Rate limited on {CONFIGURED_MODEL} (Key: {masked})",
            }
        else:
            return {
                "name": "OpenRouter Free Model",
                "status": "WARN",
                "details": f"HTTP {resp.status_code}: {resp.text[:80]} (Key: {masked})",
            }
    except Exception as e:
        return {
            "name": "OpenRouter Free Model",
            "status": "WARN",
            "details": f"Network probe notice: {e} (Key: {masked})",
        }


def check_llm_providers() -> list[dict]:
    """Probe every configured free provider (Groq, NVIDIA NIM, OpenRouter) with a tiny request."""
    import requests
    from assistant.llm_providers import build_provider

    results = []
    key_envs = {"groq": "GROQ_API_KEY", "nvidia": "NVIDIA_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
    for name, env in key_envs.items():
        provider = build_provider(name, openrouter_primary=CONFIGURED_MODEL)
        label = {"groq": "Groq", "nvidia": "NVIDIA NIM", "openrouter": "OpenRouter"}[name]
        if provider is None:
            results.append({"name": f"{label} API", "status": "SKIP", "details": f"{env} not set (optional)"})
            continue
        masked = mask_key(provider.api_key)
        model = provider.models[0]
        try:
            resp = requests.post(
                provider.chat_url,
                headers=provider.headers(),
                json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
                timeout=15,
            )
            if resp.status_code == 200:
                status, details = "PASS", f"{model} reachable (Key: {masked})"
            elif resp.status_code == 429:
                status, details = "WARN", f"Rate limited on {model} (Key: {masked})"
            else:
                status, details = "WARN", f"HTTP {resp.status_code} on {model}: {resp.text[:80]} (Key: {masked})"
        except Exception as e:
            status, details = "WARN", f"Network probe notice: {e} (Key: {masked})"
        results.append({"name": f"{label} API", "status": status, "details": details})

    if all(r["status"] == "SKIP" for r in results):
        results.append({
            "name": "AI Providers",
            "status": "FAIL",
            "details": "No provider key set. Add GROQ_API_KEY, NVIDIA_API_KEY or OPENROUTER_API_KEY to .env",
        })
    return results


def check_voice_engines() -> list[dict]:
    from config import TTS_ENGINE, EDGE_TTS_VOICE, STT_ENGINE
    results = []
    try:
        import edge_tts  # noqa: F401
        tts = ("PASS", f"Neural voice {EDGE_TTS_VOICE} (engine={TTS_ENGINE})")
    except ImportError:
        tts = ("WARN", "edge-tts not installed; using Windows SAPI voice (pip install edge-tts)")
    results.append({"name": "Neural TTS (Edge)", "status": tts[0], "details": tts[1]})

    groq = bool(os.getenv("GROQ_API_KEY", "").strip())
    if STT_ENGINE == "google" or not groq:
        stt = ("WARN" if not groq else "PASS", "Google Web Speech (set GROQ_API_KEY for Whisper accuracy)")
    else:
        stt = ("PASS", "Groq Whisper (whisper-large-v3-turbo) with Google fallback")
    results.append({"name": "Speech-to-Text", "status": stt[0], "details": stt[1]})
    return results


def run_diagnostics():
    """Run full diagnostic test suite and print results table."""
    print("=" * 65)
    print("         JARVIS SYSTEM DIAGNOSTICS & HEALTH CHECK")
    print("=" * 65)

    checks = [
        check_python_environment(),
        check_sapi_tts(),
        *check_voice_engines(),
        check_microphone(),
        check_sqlite_memory(),
        check_notes_directory(),
        check_applications(),
        *check_llm_providers(),
    ]

    for check in checks:
        status_box = f"[{check['status']}]"
        print(f"{status_box:<8} {check['name']:<25} : {check['details']}")

    print("=" * 65)


if __name__ == "__main__":
    run_diagnostics()
