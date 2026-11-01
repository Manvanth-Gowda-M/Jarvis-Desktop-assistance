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
