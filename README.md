# Jarvis 3.0: Personal AI & Windows Automation Assistant

A voice-first AI assistant for Windows 10/11, built in Python. It listens for "Hey Jarvis" on-device, understands you with Whisper speech recognition, answers in a neural British voice, and controls your PC through 60+ tools: apps, websites, volume, brightness, media, windows, files, screen vision, weather, news, reminders, notes, memory, and draft-only email.

Everything runs on **free AI providers only**: Groq, NVIDIA NIM, and OpenRouter `:free` models, with automatic failover between them when one is rate-limited or down.

## What's new in 3.0

| Area | Before | Now |
|---|---|---|
| AI brain | One provider (OpenRouter free router) | **Groq + NVIDIA NIM + OpenRouter** with automatic failover and per-model rate-limit cooldowns |
| Token use | All 49 tool schemas (~4.5K tokens) sent on every request | **Smart tool selection**: only relevant groups are sent (~800–2,000 tokens), which keeps free-tier limits usable |
| Personality | Generic assistant prompt | J.A.R.V.I.S.-style persona, current date/time and your saved memories injected into context |
| Voice out | Robotic Windows SAPI | **Neural voice** `en-GB-RyanNeural` via free Edge TTS, SAPI fallback when offline |
| Voice in | Google Web Speech | **Groq Whisper** (`whisper-large-v3-turbo`) with Google fallback and hallucination filtering |
| Conversation | Wake word needed for every command | **Follow-up mode**: Jarvis keeps listening ~5 s after answering |
| Vision | None | "What's on my screen?" / "Explain this error" using free vision models |
| Skills | Time, apps (4 hard-coded), web search | Weather (Open-Meteo), news, Wikipedia, safe calculator, timers & persistent reminders, media keys, clipboard, open **any** installed app or website |
| Desktop | Orb + panel | Global push-to-talk hotkey (**Ctrl+Alt+J**), spoken greeting, reminder tray balloons, Markdown-rendered answers |

---

## Technology Stack

| Layer | Component / Library | Purpose |
|---|---|---|
| **Language & Runtime** | Python 3.12 (64-bit) | Core runtime environment on Windows 10/11 |
| **User Interface** | PySide6 (Qt 6 for Python) | Frameless, translucent desktop floating orb with state animations (STANDBY, LISTENING, THINKING, SPEAKING) |
| **Wake-Word Engine** | openWakeWord + ONNX Runtime | On-device, low-latency "Hey Jarvis" neural detection running locally on CPU |
| **Speech Recognition** | Groq Whisper API + SpeechRecognition + PyAudio | Microphone capture; Whisper transcription with Google Web Speech fallback |
| **Text-to-Speech (TTS)** | `edge-tts` neural voices + Windows SAPI | Natural British voice, played through Windows MCI; SAPI fallback; keyboard & voice interruption |
| **AI & LLM Reasoning** | Groq, NVIDIA NIM, OpenRouter (`requests`) | OpenAI-compatible tool-calling agent with cross-provider failover, smart tool selection and synthesis pass |
| **Vision** | Free vision models (Nemotron VL, Llama 3.2 Vision, Llama 4 Scout) | Screen and image understanding |
| **Live data** | Open-Meteo, Wikipedia REST, DuckDuckGo (`ddgs`) | Weather, encyclopedia, news and web research with no API keys |
| **Email Integration** | Google OAuth 2.0 + MSAL / Graph API | Secure, draft-only email composition for Gmail and Microsoft Outlook |
| **Token Security** | Windows DPAPI (`crypt32` / `msal-extensions`) | Hardware-backed local token encryption tied to the Windows user session |
| **Desktop Automation** | `pywin32` + `pyautogui` + `pillow` | Window management (focus, minimize, maximize), desktop screenshots, and controlled typing |
| **System Control** | `pycaw` + `screen-brightness-control` + `psutil` | Audio endpoint volume, monitor brightness, battery, power, and session control |
| **File Safety** | `pathlib` + `send2trash` | Path traversal prevention, approved root boundaries, and safe recycle bin deletions |
| **Database & Audit** | SQLite3 | Persistent user memory and immutable action audit logging with secret redaction |
| **Testing** | `pytest` + `pytest-asyncio` + `unittest.mock` | Automated test suite (277 passing tests) |

---

## Key Features

1. **Local "Hey Jarvis" Wake-Word Detection (`assistant/wake_word.py`)**:
   - Runs locally without cloud dependencies using ONNX neural models (`hey_jarvis_v0.1.onnx`, `melspectrogram.onnx`, `embedding_model.onnx`).
   - Calibrated multi-frame temporal window validation to prevent false positives and duplicate activations.
   - Smooth microphone handoff between standby wake-word monitoring and command capture.

2. **Interactive Desktop Orb UI (`assistant/desktop_ui.py`)**:
   - Sleek, movable, translucent floating orb widget with dynamic glowing animations indicating assistant state.
   - Fallback manual **Speak** button, **Email** status dialog, and quick diagnostic toggles.

3. **Advanced Windows System Control (`assistant/system_control.py`)**:
   - Volume control: set percentage (0–100%), relative up/down, mute/unmute, and volume status queries.
   - Display brightness: adjust monitor brightness up/down or set exact percentage.
   - Power & session management: lock computer, safe power actions (shutdown, restart, sleep, signout) requiring explicit confirmation (`confirmed=True`).
   - Hardware metrics: real-time battery status, disk storage usage, memory, and network connectivity.

4. **Desktop Automation & Window Management (`assistant/desktop_control.py`)**:
   - Window control: list open windows, bring window to front (`focus_window`), minimize, maximize, restore, and safe close (`close_window` with confirmation).
   - Desktop actions: full screen capture (`take_screenshot` saved to `screenshots/`), typing simulation (`type_text` with 500-char max and command-injection protection), and explorer folder opening (`open_folder`).

5. **Safe File Management (`assistant/file_manager.py`)**:
   - Operations: search files, list directories, read text files (with truncation safety), create folders, create/overwrite text files, copy, move, rename, find duplicates, and open files with their default application.
   - Security: strictly constrained to `APPROVED_ROOTS` (Desktop, Documents, Downloads, Pictures, Music, Videos, notes, screenshots, and the Jarvis workspace). Path traversal (`..`) is blocked. Executables (`.exe`, `.bat`, `.ps1`, `.cmd`, `.vbs`, `.js`) are strictly forbidden from execution.
   - Recoverable Deletion: deletions use `send2trash` to send files to the Windows Recycle Bin (requires `confirmed=True`).

6. **Email Drafting & Provider Integration (`assistant/email_assistant.py`)**:
   - Safe drafting: compose email drafts with recipient, subject, and body. Always displays a prominent "NOT SENT" preview.
   - Provider support: supports draft-only mode out of the box, with built-in OAuth support for Gmail and Microsoft Outlook.
   - Zero-send guarantee: no tool exists in the agent for sending emails. Only drafts are created.

7. **User Approval & Action Audit History (`assistant/approvals.py`)**:
   - Risk classification: actions are classified as LOW, MEDIUM, HIGH, or CRITICAL.
   - Audit trail: persists all actions to `database/action_history.db` with timestamps, risk levels, and outcomes. Automatically redacts API keys and sensitive tokens before writing to disk.

8. **Intelligent Multi-Step Reasoning (`assistant/agent.py`)**:
   - Multi-step tool execution loop with dynamic routing across 40+ registered tools.
   - Automatic synthesis pass: ensures comprehensive summaries with source URLs even when tool limits are approached.
   - Strict OpenRouter free model enforcement: exclusively uses `:free` models (default `openrouter/free`).

9. **Voice & Text Experience (`assistant/speech.py` & `assistant/listen.py`)**:
   - Spoken answers are concise and conversational. Full details, markdown, code, and source links are displayed in the terminal.
   - Interruptibility: press `Space`, `Esc`, `q`, or `Enter` to instantly stop speech. Voice interrupt listening for stop phrases.

---

## Project Structure

```
Jarvis/
├── .env.example             # Clean configuration template with environment variable placeholders
├── .gitignore               # Excludes secrets, credentials, OAuth tokens, DBs, venvs, caches
├── requirements.txt         # Project dependencies with pinned versions
├── config.py                # Centralized settings & free-model validation
├── main.py                  # Entry point (default app, --legacy-orb, --voice, --text, --diagnostics)
├── README.md                # Documentation and setup instructions
├── credentials/             # Directory for local credentials (git-ignored, tracked by .gitkeep)
├── database/                # SQLite storage (jarvis_memory.db, action_history.db) (.gitkeep)
├── diagnostics/             # Live microphone and device calibration diagnostics
│   ├── calibrate_microphones.py
│   ├── enumerate_devices.py
│   └── wake_word_live_test.py
├── models/                  # ONNX wake-word models (~3.5 MB total)
│   ├── hey_jarvis_v0.1.onnx
│   ├── melspectrogram.onnx
│   └── embedding_model.onnx
├── notes/                   # Persistent Markdown notes directory (.gitkeep)
├── screenshots/             # Desktop screenshots directory (.gitkeep)
├── sounds/                  # Audio cues and UI sound effects (.gitkeep)
├── tests/                   # Pytest automated test suite (277 tests)
│   ├── test_agent_errors.py
│   ├── test_approvals.py
│   ├── test_audio_coordinator.py
│   ├── test_desktop_control.py
│   ├── test_email_assistant.py
│   ├── test_email_integration.py
│   ├── test_file_manager.py
│   ├── test_free_models.py
│   ├── test_memory.py
│   ├── test_system_control.py
│   └── test_wake_word.py
└── assistant/
    ├── __init__.py          # Unified package exports
    ├── agent.py             # Multi-provider free-model tool-calling agent (Groq / NVIDIA NIM / OpenRouter)
    ├── llm_providers.py     # Free provider registry, failover order, rate-limit cooldowns
    ├── skills.py            # Weather, news, Wikipedia, calculator, media keys, clipboard, websites
    ├── reminders.py         # Persistent timers and reminders with UI/voice notifications
    ├── vision.py            # Screen and image understanding with free vision models
    ├── hotkey.py            # Global push-to-talk hotkey (Win32 RegisterHotKey)
    ├── approvals.py         # Risk classification and SQLite action audit history
    ├── audio_coordinator.py # Thread-safe microphone ownership and state coordinator
    ├── brain.py             # Brain compatibility interface
    ├── commands.py          # Fast local zero-latency command handlers
    ├── desktop_control.py   # Windows desktop and window management
    ├── desktop_ui.py        # PySide6 animated floating orb and control panel
    ├── diagnostics.py       # Hardware, service, and environment diagnostics
    ├── email_assistant.py   # Safe email drafting and OAuth provider integration
    ├── file_manager.py      # Safe file manager constrained to approved roots
    ├── jarvis.py            # Main application coordinator
    ├── listen.py            # Speech recognition audio loop
    ├── memory.py            # SQLite memory storage and keyword retrieval
    ├── notes.py             # Markdown notes management with traversal safety
    ├── router.py            # Intent dispatcher (fast commands -> AI agent)
    ├── speech.py            # Windows SAPI TTS with voice & keyboard interruption
    ├── system_control.py    # Windows volume, brightness, battery, power control
    ├── tools.py             # Registered tool implementations and schemas
    └── wake_word.py         # openWakeWord ONNX engine and stream listener
```

---

## Setup & Installation

### 1. Prerequisites
- **Operating System**: Windows 10 or Windows 11 (64-bit)
- **Python**: Python 3.12 (recommended) or 3.11+
- **Hardware**: Working microphone and speakers / headphones

### 2. Virtual Environment Setup
Open PowerShell or Command Prompt:

```powershell
# Clone the repository
git clone https://github.com/your-username/jarvis.git
cd jarvis

# Create a clean virtual environment
python -m venv .venv

# Activate the virtual environment
# In PowerShell:
.\.venv\Scripts\Activate.ps1
# In Command Prompt (cmd):
# .\.venv\Scripts\activate.bat

# Upgrade pip
python -m pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy the template configuration file to `.env`:

```powershell
cp .env.example .env
```

Open `.env` and add **at least one** free API key. Adding all three gives the best reliability, because Jarvis fails over between them automatically:

```env
GROQ_API_KEY=gsk_...        # https://console.groq.com/keys  (fastest; also enables Whisper speech recognition)
NVIDIA_API_KEY=nvapi-...    # https://build.nvidia.com       (click "Get API Key" on any model page)
OPENROUTER_API_KEY=sk-or-...# https://openrouter.ai/keys     (only ':free' models are ever used)
USER_TITLE=sir
DEFAULT_LOCATION=Hyderabad
```

None of these require a credit card. Free tiers have rate limits (for example Groq allows roughly 30 requests/minute on chat models); when one provider is rate-limited, Jarvis cools that model down and moves to the next. Free model catalogs change often, so model IDs are configurable in `.env` (`GROQ_MODELS`, `NVIDIA_MODELS`, `OPENROUTER_EXTRA_MODELS`).

Run `python main.py --diagnostics` to confirm each provider responds.

### 4. Wake-Word ONNX Models
The required ONNX models are included in `models/` (~3.5 MB total):
- `models/hey_jarvis_v0.1.onnx`
- `models/melspectrogram.onnx`
- `models/embedding_model.onnx`

If you ever need to re-download the official openWakeWord models, run:
```powershell
python -m assistant.wake_word --download
```

---

## How to Run Jarvis

### 0. The Jarvis App (Recommended)
```powershell
python main.py --install   # one time: creates Desktop + Start Menu shortcuts with the Jarvis icon
python main.py             # or double-click the "Jarvis" shortcut
```
Jarvis runs in the background with a **Dynamic Island**, a black pill at the top-centre of the screen. Say **"Hey Jarvis"** (or press **Ctrl+Alt+J**). You hear a chime, and the island drops down showing:
1. **Listening…** with a live waveform
2. **Working on it…** with what you said, and a live checklist of each action ("Opening Spotify ✓", "Checking the weather in Hyderabad…")
3. The **answer** while Jarvis speaks. It then shrinks away on its own.

Click the island (or the tray icon) to open the full dashboard; right-click it for options. The island never takes keyboard focus, so typing actions go to the window you were using.

The dashboard window has six pages:
- **Home**: animated arc-reactor HUD showing the live state, the conversation, quick actions (weather, news, screen, system, reminders), and Talk / Pause mic / Stop buttons.
- **System**: live CPU, memory, disk, network, battery and uptime graphs, plus top processes.
- **Reminders**: add, view, and cancel reminders and timers.
- **Memory**: view, add, and delete what Jarvis remembers about you.
- **Activity**: what Jarvis heard, which AI model answered, and every tool it used.
- **Settings**: API keys, name/title/city, neural voice with a test button, speech recognition, follow-up mode, wake sensitivity, hotkey, and launch at sign-in. Saved to `.env`.

Closing the window keeps Jarvis in the system tray, so "Hey Jarvis" and **Ctrl+Alt+J** keep working. Right-click the tray icon to quit or show the floating orb. Logs are written to `logs/jarvis.log`. `python main.py --uninstall` removes the shortcuts and the launch-at-sign-in entry.

### 1. Legacy Floating Orb (old UI)
`python main.py --desktop` now opens the new app with the Dynamic Island (same as `python main.py`). To run the old floating-orb window on its own:
```powershell
python main.py --legacy-orb
```
- Say **"Hey Jarvis"** hands-free to activate listening, or press **Ctrl+Alt+J** from any app.
- After Jarvis answers, just keep talking for a follow-up. Say "that's all" or stay silent to end.
- Click the **Speak** button as a manual fallback.
- Click the **✉ Email** button to inspect or connect email accounts.

### 2. Console Voice Mode
Runs Jarvis directly in your terminal with voice interaction:
```powershell
python main.py
```
*(Speak your command; press `Space`, `Esc`, or `q` anytime while Jarvis is speaking to interrupt.)*

### 3. Text Mode
Runs Jarvis in interactive text-prompt mode (ideal for quiet environments or testing):
```powershell
python main.py --text
```

### 4. Diagnostics & Calibration
Check all connected hardware, audio input devices, and system dependencies:
```powershell
# Full system diagnostics
python main.py --diagnostics

# Calibrate microphone and test wake-word detection in real time
python diagnostics/wake_word_live_test.py
```

### 5. Run Automated Unit Tests
Verify the entire test suite across all modules:
```powershell
python -m pytest -q
```
*(All 277 unit and integration tests should pass.)*

---

## Email Provider OAuth Setup & Security (Gmail + Outlook)

Jarvis features a strict **Draft-Only** email integration that connects securely to both **Gmail** and **Microsoft Outlook**.

### Safety Contract
- **Strict Draft-Only**: Jarvis only creates drafts in your Gmail or Outlook Drafts folder.
- **Zero Send Tools**: No `send_email` or `send_draft` tool is registered or exposed to the AI agent. Sending is disabled.
- **Scope Transparency**:
  - Gmail uses `https://www.googleapis.com/auth/gmail.compose`. Restricted strictly to `users.drafts.create`.
  - Outlook uses Microsoft Graph delegated `Mail.ReadWrite`. Jarvis never requests or uses `Mail.Send`.
- **Token Security**:
  - OAuth tokens and credentials are kept strictly outside the Git repository in `%LOCALAPPDATA%\Jarvis\oauth`.
  - Stored tokens are encrypted using **Windows DPAPI** (`CryptProtectData`), tying decryption exclusively to the active Windows user account.
  - No plaintext credentials, access tokens, refresh tokens, or passwords are ever logged or saved.

---

### A. Gmail OAuth 2.0 Setup Guide
1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project named **Jarvis Desktop Assistant** (or select an existing one).
3. Navigate to **APIs & Services > Library**, search for **Gmail API**, and click **Enable**.
4. Navigate to **APIs & Services > OAuth consent screen**:
   - User Type: **External** (for personal Gmail accounts).
   - App Name: `Jarvis Desktop Assistant`.
   - User support and Developer contact email: your personal Gmail.
   - Scopes: Add `https://www.googleapis.com/auth/gmail.compose`.
   - Test Users: Add your personal Gmail address.
   - Click **Save and Continue**.
5. Navigate to **APIs & Services > Credentials**:
   - Click **Create Credentials > OAuth client ID**.
   - Application Type: **Desktop app**.
   - Name: `Jarvis Desktop`.
   - Download the client configuration JSON file.
6. Save the downloaded JSON file to:
   ```
   %LOCALAPPDATA%\Jarvis\oauth\google_credentials.json
   ```
   *(e.g. `C:\Users\<Username>\AppData\Local\Jarvis\oauth\google_credentials.json`)*
7. In the old floating-orb interface (`python main.py --legacy-orb`), click the **✉ Email** button and click **Connect** next to Gmail. Your default browser will open for Google authentication. Once approved, your encrypted token is securely stored in `%LOCALAPPDATA%\Jarvis\oauth\gmail_token.dpapi`.

---

### B. Microsoft Outlook / Microsoft Graph Setup Guide
1. Open the [Microsoft Azure Portal - App Registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade).
2. Click **New registration**:
   - Name: `Jarvis Desktop Assistant`.
   - Supported account types: **Accounts in any organizational directory and personal Microsoft accounts (e.g. Skype, Xbox, Outlook.com)**.
   - Redirect URI: Select platform **Public client/native** and enter `http://localhost`.
   - Click **Register**.
3. Copy the **Application (client) ID** (a GUID format string) from the Overview page.
4. Navigate to **API Permissions > Add a permission**:
   - Select **Microsoft Graph > Delegated permissions**.
   - Search for and check `Mail.ReadWrite`. *(Do NOT add `Mail.Send`)*.
   - Click **Add permissions**.
5. Add your client ID to `.env` in the Jarvis project directory:
   ```env
   JARVIS_OUTLOOK_CLIENT_ID=your-azure-application-client-id-guid
   JARVIS_OUTLOOK_AUTHORITY=https://login.microsoftonline.com/common
   ```
6. In the Jarvis desktop interface, click **✉ Email** and click **Connect** next to Outlook. Your browser will open for Microsoft account sign-in. Cached tokens are DPAPI-encrypted via `msal-extensions` at `%LOCALAPPDATA%\Jarvis\oauth\outlook_token_cache.bin`.

---

## Security Warnings & Limitations

1. **Never Commit Secrets**:
   - Your `.env` file containing API keys and credentials in `credentials/` are git-ignored. Never remove these entries from `.gitignore` or force-push them to public repositories.
2. **Local Token Security**:
   - OAuth tokens are DPAPI-encrypted and saved in `%LOCALAPPDATA%\Jarvis\oauth`. This means tokens cannot be transferred or decrypted by other users or machines.
3. **Drafts Only**:
   - Jarvis is intentionally architected without automated email sending. You retain complete human-in-the-loop oversight to review, edit, and send any email draft directly from your email client.
4. **Filesystem Boundaries**:
   - The file manager strictly validates real paths against `FILE_APPROVED_ROOTS`. Symlink and path traversal attacks are rejected. Any deletion is non-destructive (routed to Windows Recycle Bin via `send2trash`) and requires confirmation.
5. **System Power Actions**:
   - Critical power actions (shutdown, restart, signout) require double-confirmation to prevent accidental invocation.
6. **Free-Model Constraints**:
   - Jarvis only talks to free tiers: Groq, NVIDIA NIM, and OpenRouter `:free` models (paid OpenRouter models are rejected in code). Free tiers are rate-limited; with several providers configured, Jarvis fails over automatically.
7. **Third-party voice and data services**:
   - Neural TTS sends the spoken sentence to Microsoft's Edge read-aloud service; Whisper STT sends your recorded command to Groq. Set `TTS_ENGINE=sapi` and `STT_ENGINE=google` to change this. Screen analysis sends a screenshot to the configured vision provider only when you ask about your screen.

---

## Voice Demo Script

Try speaking these natural phrases to Jarvis in voice or desktop mode:

1. **Wake-Word & Conversational**:
   - *"Hey Jarvis"* -> Activates the orb and starts listening.
   - *"What can you do?"*
   - *"What time is it?"*

2. **System Control**:
   - *"Jarvis, set the volume to 40 percent."*
   - *"Turn the volume down a bit."*
   - *"Set brightness to 80 percent."*
   - *"What is my battery level and disk space?"*
   - *"Open Windows display settings."*
   - *"Lock my computer."*

3. **Desktop Automation**:
   - *"What windows do I have open right now?"*
   - *"Switch focus to Google Chrome."*
   - *"Minimize Notepad."*
   - *"Take a screenshot."*

4. **Safe File Management**:
   - *"List the files in my Documents folder."*
   - *"Search for Python files in my project."*
   - *"Create a folder called InterviewPrep in my Documents."*
   - *"Write a note in notes/plan.md with today's objectives."*

5. **Web Research & Notes**:
   - *"Research the difference between async and multithreading in Python and summarize it."*
   - *"Save that summary into my research notes."*
   - *"Read back my latest research note."*

6. **Email Drafting**:
   - *"Draft an email to professor@university.edu requesting an appointment."*
   - *"Write a professional internship follow-up email to recruiter@company.com."*
   - *"Use Outlook for this draft."*
   - *"Save this draft in Gmail."*
   - *"Read my email draft."*

7. **Vision, Live Data & Productivity**:
   - *"What's on my screen?"* / *"Explain the error on my screen."*
   - *"What's the weather like?"* / *"Will it rain tomorrow in Mumbai?"*
   - *"Give me the latest AI news."* / *"Who is Jensen Huang?"*
   - *"Set a timer for 10 minutes."* / *"Remind me to call mom at 7 pm."*
   - *"What's 18% of 2450?"*
   - *"Pause the music."* / *"Next song."*
   - *"Summarize what I copied."* (reads the clipboard)
   - *"Open Spotify."* / *"Open GitHub."*

---

## Supported Fast Commands

| Command | Action |
|---|---|
| `what time is it` / `time` | Returns local time instantly |
| `what is today's date` / `date` | Returns today's date |
| `system status` / `cpu` / `battery` | Checks CPU, RAM, and Battery percentages |
| `open chrome` / `launch chrome` | Opens Google Chrome |
| `open notepad` / `open calculator` | Opens Notepad or Calculator |
| `open vscode` / `launch vscode` | Opens Visual Studio Code |
| `google <query>` / `search for <query>` | Opens Google search in your browser |
| `youtube <query>` | Searches YouTube in your browser |
| `remember <fact>` | Saves fact to SQLite long-term memory |
| `what do you remember [about <topic>]` | Recalls saved facts matching topic |
| `list notes` | Lists saved Markdown research notes |
| `weather` / `weather in <city>` | Current weather and forecast (Open-Meteo) |
| `set a timer for 5 minutes` | Starts a countdown timer |
| `list reminders` | Shows pending reminders and timers |
| `what is 25 * 4` / `18% of 2450` | Instant safe calculator |
| `pause music` / `next song` / `previous track` | System media keys |
| `new conversation` | Clears short-term conversation context |
| `help` | Lists capabilities |
| `text mode` / `voice mode` | Switches interaction mode dynamically |
| `exit` / `quit` / `goodbye` | Closes Jarvis |
