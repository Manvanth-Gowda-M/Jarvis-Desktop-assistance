import os
from pathlib import Path
from dotenv import load_dotenv

# Base directories
BASE_DIR = Path(__file__).resolve().parent
DATABASE_DIR = BASE_DIR / "database"
NOTES_DIR = BASE_DIR / "notes"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"

DATABASE_DIR.mkdir(parents=True, exist_ok=True)
NOTES_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATABASE_DIR / "jarvis_memory.db"
ACTION_HISTORY_DB_PATH = DATABASE_DIR / "action_history.db"

# Load environment variables
load_dotenv(dotenv_path=BASE_DIR / ".env")

# Assistant identity
JARVIS_NAME = os.getenv("JARVIS_NAME", "Jarvis")

# OpenRouter Free Models Only
DEFAULT_FREE_MODEL = "openrouter/free"

# Only verified free models and the dynamic free router.
# Stale hardcoded endpoints (such as meta-llama/llama-3.3-70b-instruct:free) have been removed.
# openrouter/free automatically selects currently healthy free models with tool-calling capabilities.
ALLOWED_FREE_MODELS = [
    "openrouter/free",
]


def validate_free_model(model_name: str | None) -> str:
    """Ensure that only free models can be selected. Never silently select a paid model."""
    if not model_name:
        return DEFAULT_FREE_MODEL

    model_clean = model_name.strip()
    if not model_clean:
        return DEFAULT_FREE_MODEL

    # Model MUST end with :free or match openrouter/free
    if model_clean == "openrouter/free" or model_clean.endswith(":free"):
        return model_clean

    raise ValueError(
        f"Model '{model_name}' is not an approved free OpenRouter model. "
        f"Jarvis is strictly configured to only use free models ending in ':free' or 'openrouter/free'."
    )


_env_model = os.getenv("OPENROUTER_MODEL", DEFAULT_FREE_MODEL)
if _env_model and "llama-3.3-70b-instruct:free" in _env_model:
    _env_model = DEFAULT_FREE_MODEL
CONFIGURED_MODEL = validate_free_model(_env_model)

# Microphone & Wake-Word Device Configuration
def _parse_device_index(env_var: str) -> int | None:
    val = os.getenv(env_var, "").strip()
    if not val:
        return None
    try:
        return int(val)
    except ValueError:
        return None


MICROPHONE_DEVICE_INDEX = _parse_device_index("MICROPHONE_DEVICE_INDEX")
WAKE_WORD_DEVICE_INDEX = _parse_device_index("WAKE_WORD_DEVICE_INDEX")

# Voice & Audio Settings
VOICE_RATE = int(os.getenv("VOICE_RATE", "2"))        # SAPI SpVoice Rate (-10 to 10), default 2 for responsive speed
VOICE_VOLUME = int(os.getenv("VOICE_VOLUME", "100"))  # SAPI SpVoice Volume (0 to 100)
LISTEN_TIMEOUT = float(os.getenv("LISTEN_TIMEOUT", "8.0"))          # Initial start timeout waiting for user to begin speaking (seconds)
PHRASE_TIME_LIMIT = float(os.getenv("PHRASE_TIME_LIMIT", "25.0"))   # Maximum full-utterance recording duration (seconds)
PAUSE_THRESHOLD = float(os.getenv("PAUSE_THRESHOLD", "1.3"))        # End-of-speech silence threshold (seconds); accommodates natural pauses
NON_SPEAKING_DURATION = float(os.getenv("NON_SPEAKING_DURATION", "1.0"))  # Non-speaking buffer duration (seconds)
MAX_SPOKEN_CHARS = int(os.getenv("MAX_SPOKEN_CHARS", "240"))
DEFAULT_BRIGHTNESS_STEP = int(os.getenv("DEFAULT_BRIGHTNESS_STEP", "10"))  # Default step for brightness adjustments
WAKE_WORD_THRESHOLD = float(os.getenv("WAKE_WORD_THRESHOLD", "0.25"))  # Calibrated on-device threshold (0.0 to 1.0)
WAKE_WORD_COOLDOWN = float(os.getenv("WAKE_WORD_COOLDOWN", "2.0"))     # Cooldown in seconds to prevent duplicate activations
WAKE_WORD_WINDOW_SIZE = int(os.getenv("WAKE_WORD_WINDOW_SIZE", "3"))   # Multi-frame validation window size
WAKE_WORD_MIN_HITS = int(os.getenv("WAKE_WORD_MIN_HITS", "2"))        # Required frames meeting threshold within window

# OpenRouter Network Settings
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
API_TIMEOUT = float(os.getenv("API_TIMEOUT", "30.0"))
MAX_TOOL_STEPS = int(os.getenv("MAX_TOOL_STEPS", "6"))

# ---------------------------------------------------------------------------
# Multi-provider free LLM settings (all OpenAI-compatible endpoints)
#   - Groq        : https://console.groq.com  (free tier, very fast LPU inference)
#   - NVIDIA NIM  : https://build.nvidia.com  (free developer API, nvapi- keys)
#   - OpenRouter  : https://openrouter.ai     (only ':free' models / openrouter/free)
# Providers without an API key are skipped automatically. The order below is the
# failover order: if one provider is rate-limited or down, the next one is tried.
# Model lists are comma-separated so you can update them when providers rotate models.
# ---------------------------------------------------------------------------
LLM_PROVIDER_ORDER = os.getenv("LLM_PROVIDER_ORDER", "groq,nvidia,openrouter")

GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODELS = os.getenv("GROQ_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")

NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODELS = os.getenv(
    "NVIDIA_MODELS",
    "nvidia/nemotron-3.5-super-vl-120b-a12b,nvidia/nemotron-3.5-lightning-30b-a3b,"
    "nvidia/nemotron-3-ultra-550b-a55b,mistralai/mistral-large-4-1t-a52b",
)
NVIDIA_VISION_MODEL = os.getenv("NVIDIA_VISION_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")

OPENROUTER_EXTRA_MODELS = os.getenv(
    "OPENROUTER_EXTRA_MODELS",
    "nvidia/nemotron-3.5-super-vl:free,google/gemma-4-31b-it:free",
)
OPENROUTER_VISION_MODEL = os.getenv("OPENROUTER_VISION_MODEL", "nvidia/nemotron-3.5-super-vl:free,openrouter/free")

LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.6"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1200"))
RATE_LIMIT_COOLDOWN = float(os.getenv("RATE_LIMIT_COOLDOWN", "45"))
# "smart" sends only the tool groups relevant to each request (saves free-tier tokens);
# "all" sends every tool schema on every request.
TOOL_SELECTION = os.getenv("TOOL_SELECTION", "smart").strip().lower()

# ---------------------------------------------------------------------------
# Personality
# ---------------------------------------------------------------------------
USER_NAME = os.getenv("USER_NAME", "")
USER_TITLE = os.getenv("USER_TITLE", "sir")          # how Jarvis addresses you: sir / ma'am / boss / your name
DEFAULT_LOCATION = os.getenv("DEFAULT_LOCATION", "")  # used for weather when no city is given

# ---------------------------------------------------------------------------
# Voice engines
#   TTS_ENGINE: "edge" (free Microsoft neural voices, needs internet; falls back to SAPI) or "sapi"
#   STT_ENGINE: "auto" (Groq Whisper when GROQ_API_KEY is set, else Google), "groq", or "google"
# ---------------------------------------------------------------------------
TTS_ENGINE = os.getenv("TTS_ENGINE", "edge").strip().lower()
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "en-GB-RyanNeural")
EDGE_TTS_RATE = os.getenv("EDGE_TTS_RATE", "+8%")
EDGE_TTS_PITCH = os.getenv("EDGE_TTS_PITCH", "-2Hz")
STT_ENGINE = os.getenv("STT_ENGINE", "auto").strip().lower()
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "en")

# Global push-to-talk hotkey for desktop mode (modifiers + key), e.g. "ctrl+alt+j"
GLOBAL_HOTKEY = os.getenv("GLOBAL_HOTKEY", "ctrl+alt+j").strip().lower()
DESKTOP_GREETING = os.getenv("DESKTOP_GREETING", "true").strip().lower() in {"1", "true", "yes", "on"}

# Conversation mode: after answering a voice command, keep listening briefly for a follow-up
FOLLOW_UP_ENABLED = os.getenv("FOLLOW_UP_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
FOLLOW_UP_TIMEOUT = float(os.getenv("FOLLOW_UP_TIMEOUT", "5.0"))
FOLLOW_UP_MAX_TURNS = int(os.getenv("FOLLOW_UP_MAX_TURNS", "6"))

_on = lambda v: str(v).strip().lower() in {"1", "true", "yes", "on"}  # noqa: E731
# Dynamic Island overlay (top-centre pill that appears when you call Jarvis)
ISLAND_ENABLED = _on(os.getenv("ISLAND_ENABLED", "true"))
ISLAND_IDLE_VISIBLE = _on(os.getenv("ISLAND_IDLE_VISIBLE", "false"))  # keep a small pill visible when idle
OPEN_DASHBOARD_ON_START = _on(os.getenv("OPEN_DASHBOARD_ON_START", "false"))
WAKE_CHIME = _on(os.getenv("WAKE_CHIME", "true"))

# Email Provider & OAuth Configuration
# OAuth credentials and token caches are strictly stored in %LOCALAPPDATA%\Jarvis\oauth
# outside the Git repository for DPAPI-backed token security.
_local_appdata = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
OAUTH_DIR = Path(_local_appdata) / "Jarvis" / "oauth"
OAUTH_DIR.mkdir(parents=True, exist_ok=True)

# Gmail OAuth Settings
GMAIL_CREDENTIALS_FILE = Path(os.getenv("GMAIL_CREDENTIALS_FILE", str(OAUTH_DIR / "google_credentials.json")))
GMAIL_TOKEN_FILE = Path(os.getenv("GMAIL_TOKEN_FILE", str(OAUTH_DIR / "gmail_token.dpapi")))
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
]

# Outlook / Microsoft Graph OAuth Settings
JARVIS_OUTLOOK_CLIENT_ID = os.getenv("JARVIS_OUTLOOK_CLIENT_ID", "")
JARVIS_OUTLOOK_AUTHORITY = os.getenv("JARVIS_OUTLOOK_AUTHORITY", "https://login.microsoftonline.com/common")
OUTLOOK_TOKEN_CACHE_FILE = Path(os.getenv("OUTLOOK_TOKEN_CACHE_FILE", str(OAUTH_DIR / "outlook_token_cache.bin")))
OUTLOOK_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
]

# Preferred default provider ("gmail", "outlook", or "auto")
EMAIL_DEFAULT_PROVIDER = os.getenv("EMAIL_DEFAULT_PROVIDER", "auto")

# Legacy compatibility bindings
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "")
EMAIL_OAUTH_CREDENTIALS_FILE = str(GMAIL_CREDENTIALS_FILE)
EMAIL_OAUTH_TOKEN_FILE = str(GMAIL_TOKEN_FILE)

# File Manager — approved directory roots (comma-separated in env)
_home = Path.home()
_default_roots = ",".join([
    str(_home / "Desktop"),
    str(_home / "Documents"),
    str(_home / "Downloads"),
    str(_home / "Pictures"),
    str(_home / "Music"),
    str(_home / "Videos"),
    str(BASE_DIR / "notes"),
    str(BASE_DIR / "screenshots"),
    str(BASE_DIR),
])
FILE_APPROVED_ROOTS_STR = os.getenv("FILE_APPROVED_ROOTS", _default_roots)
FILE_APPROVED_ROOTS = [Path(p.strip()) for p in FILE_APPROVED_ROOTS_STR.split(",") if p.strip()]
