import datetime
import os
import re
import shutil
import subprocess
import webbrowser
from urllib.parse import quote_plus

import psutil
import requests

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

from assistant.memory import (
    remember_fact,
    recall_facts,
    list_memories,
)
from assistant.notes import (
    save_research_note,
    read_research_note,
    list_research_notes,
)
from assistant.system_control import (
    SYSTEM_CONTROL_TOOLS_SCHEMA,
    SYSTEM_CONTROL_AVAILABLE_TOOLS,
)
from assistant.file_manager import (
    FILE_MANAGER_TOOLS_SCHEMA,
    FILE_MANAGER_AVAILABLE_TOOLS,
)
from assistant.email_assistant import (
    EMAIL_TOOLS_SCHEMA,
    EMAIL_AVAILABLE_TOOLS,
)
from assistant.desktop_control import (
    DESKTOP_TOOLS_SCHEMA,
    DESKTOP_AVAILABLE_TOOLS,
)
from assistant.skills import SKILLS_TOOLS_SCHEMA, SKILLS_AVAILABLE_TOOLS
from assistant.reminders import REMINDER_TOOLS_SCHEMA, REMINDER_AVAILABLE_TOOLS
from assistant.vision import VISION_TOOLS_SCHEMA, VISION_AVAILABLE_TOOLS
from assistant.media import MEDIA_TOOLS_SCHEMA, MEDIA_AVAILABLE_TOOLS


def get_current_time() -> str:
    """Get the current local time."""
    return datetime.datetime.now().strftime("%I:%M %p")


def get_current_date() -> str:
    """Get today's date."""
    return datetime.datetime.now().strftime("%A, %d %B %Y")


def open_application(application: str) -> str:
    """Open a common Windows application reliably."""
    app = str(application).lower().strip()

    # ---------------- CHROME ----------------
    if any(k in app for k in ("chrome", "google chrome", "browser")):
        possible_paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        chrome_path = None
        for p in possible_paths:
            if os.path.exists(p):
                chrome_path = p
                break
        if not chrome_path:
            chrome_path = shutil.which("chrome")

        if chrome_path:
            try:
                subprocess.Popen([chrome_path])
                return "Google Chrome is now open."
            except Exception as e:
                return f"Failed to start Google Chrome: {e}"
        else:
            return "Google Chrome executable was not found on this system."

    # ---------------- NOTEPAD ----------------
    if "notepad" in app:
        try:
            subprocess.Popen(["notepad.exe"])
            return "Notepad is now open."
        except Exception as e:
            return f"Failed to start Notepad: {e}"

    # ---------------- CALCULATOR ----------------
    if any(k in app for k in ("calc", "calculator")):
        try:
            subprocess.Popen(["calc.exe"])
            return "Calculator is now open."
        except Exception as e:
            return f"Failed to start Calculator: {e}"

    # ---------------- VS CODE ----------------
    if any(k in app for k in ("code", "vscode", "vs code", "visual studio code")):
        vscode_paths = [
            os.path.expandvars(r"%LocalAppData%\Programs\Microsoft VS Code\Code.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft VS Code\Code.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft VS Code\Code.exe"),
        ]
        code_path = None
        for p in vscode_paths:
            if os.path.exists(p):
                code_path = p
                break
        if not code_path:
            code_path = shutil.which("code.cmd") or shutil.which("code.exe") or shutil.which("code")

        if code_path:
            try:
                subprocess.Popen([code_path])
                return "Visual Studio Code is now open."
            except Exception as e:
                return f"Failed to start Visual Studio Code: {e}"
        else:
            return "Visual Studio Code was not found on this system."

    return _open_via_start_menu(application)


# Built-in Windows tools that are safe to launch by name.
_BUILTIN_APPS = {
    "file explorer": "explorer.exe", "explorer": "explorer.exe", "files": "explorer.exe",
    "task manager": "taskmgr.exe", "paint": "mspaint.exe", "wordpad": "write.exe",
    "command prompt": "cmd.exe", "cmd": "cmd.exe", "powershell": "powershell.exe",
    "terminal": "wt.exe", "windows terminal": "wt.exe", "snipping tool": "SnippingTool.exe",
    "control panel": "control.exe", "registry editor": None,  # deliberately not launchable
    "settings": "ms-settings:", "camera": "microsoft.windows.camera:", "store": "ms-windows-store:",
    "microsoft store": "ms-windows-store:", "clock": "ms-clock:", "alarms": "ms-clock:",
    "photos": "ms-photos:", "mail": "outlookmail:", "calendar": "outlookcal:",
}


def _start_menu_shortcuts() -> list:
    from pathlib import Path

    roots = [
        Path(os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs")),
        Path(os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs")),
        Path.home() / "Desktop",
        Path(os.path.expandvars(r"%Public%\Desktop")),
    ]
    found = []
    for root in roots:
        if not root.exists():
            continue
        try:
            for p in root.rglob("*"):
                if p.suffix.lower() in {".lnk", ".url", ".appref-ms"}:
                    found.append(p)
        except OSError:
            continue
    return found


def _open_via_start_menu(application: str) -> str:
    """Launch any installed app by matching Start Menu / Desktop shortcuts."""
    import difflib

    name = re.sub(r"\b(app|application|program|the|please)\b", " ", str(application).lower())
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return f"I don't know how to open '{application}' yet."

    if name in _BUILTIN_APPS:
        target = _BUILTIN_APPS[name]
        if not target:
            return f"For safety I won't open '{application}' automatically."
        try:
            os.startfile(target)
            return f"{application.strip().title()} is now open."
        except Exception as e:
            return f"Failed to open {application}: {e}"

    shortcuts = _start_menu_shortcuts()
    skip_words = ("uninstall", "readme", "help", "documentation", "website", "release notes")
    best, best_score = None, 0.0
    for sc in shortcuts:
        stem = sc.stem.lower()
        if any(w in stem for w in skip_words):
            continue
        if stem == name:
            score = 1.0
        elif stem.startswith(name) or f" {name}" in f" {stem}":
            score = 0.9 - min(len(stem) - len(name), 40) / 200
        else:
            score = difflib.SequenceMatcher(None, name, stem).ratio()
        if score > best_score:
            best, best_score = sc, score

    if best is not None and best_score >= 0.72:
        try:
            os.startfile(str(best))
            return f"{best.stem} is now open."
        except Exception as e:
            return f"Found {best.stem} but couldn't start it: {e}"

    exe = shutil.which(name.replace(" ", "")) if len(name) <= 40 else None
    if exe and not exe.lower().endswith((".bat", ".cmd", ".ps1", ".vbs")):
        try:
            subprocess.Popen([exe])
            return f"{application.strip()} is now open."
        except Exception as e:
            return f"Failed to start {application}: {e}"

    return (
        f"I don't know how to open '{application}' yet. I couldn't find it in the Start Menu. "
        "Try the exact app name as it appears in Start."
    )


def search_web(query: str) -> str:
    """Open a web search query in the user's browser."""
    query = str(query).strip()
    if not query:
        return "No search query was provided."

    url = f"https://www.google.com/search?q={quote_plus(query)}"
    try:
        webbrowser.open(url)
        return f"Web search opened in browser for: '{query}'"
    except Exception as e:
        return f"Failed to open web search: {e}"


def research_web(query: str) -> str:
    """Search the web for information and return source titles, URLs, and summaries."""
    query = str(query).strip()
    if not query:
        return "Please provide a research query."

    # First attempt: Try duckduckgo_search library if available
    try:
        if DDGS is None:
            raise ImportError("duckduckgo_search not installed")
        results = list(DDGS().text(query, max_results=5))
        if results:
            output = [f"Web research results for: '{query}'"]
            for idx, r in enumerate(results, 1):
                title = r.get("title", "Untitled")
                url = r.get("href") or r.get("url", "")
                snippet = r.get("body", "")
                output.append(f"{idx}. {title}\nSource: {url}\nSummary: {snippet[:400]}")
            return "\n\n".join(output)
    except Exception:
        pass

    # Second attempt: Direct HTTP request to DuckDuckGo Instant Answer / HTML
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        # Try DuckDuckGo HTML Lite search
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=headers,
            timeout=8,
        )

        if resp.status_code == 200:
            # Parse links and snippets from HTML
            raw_html = resp.text
            # Match results: class="result__snippet" and class="result__url"
            snippets = re.findall(r'<a class="result__snippet[^"]*"[^>]*>(.*?)</a>', raw_html, re.DOTALL)
            urls = re.findall(r'<a class="result__url"[^>]*href="([^"]+)"', raw_html)
            titles = re.findall(r'<h2 class="result__title"[^>]*>.*?<a[^>]*>(.*?)</a>', raw_html, re.DOTALL)

            results_text = []
            for i in range(min(4, len(snippets), len(urls))):
                clean_title = re.sub(r"<[^>]+>", "", titles[i] if i < len(titles) else f"Result {i+1}").strip()
                clean_snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()
                clean_url = urls[i].strip()
                if "duckduckgo.com/l/?uddg=" in clean_url:
                    from urllib.parse import unquote
                    m = re.search(r"uddg=([^&]+)", clean_url)
                    if m:
                        clean_url = unquote(m.group(1))

                results_text.append(f"{i+1}. {clean_title}\nSource: {clean_url}\nSummary: {clean_snippet[:350]}")

            if results_text:
                return f"Web research results for: '{query}'\n\n" + "\n\n".join(results_text)

        # Fallback to DuckDuckGo Instant Answer API
        api_url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_html=1&skip_disambig=1"
        api_resp = requests.get(api_url, headers=headers, timeout=6)
        if api_resp.status_code == 200:
            data = api_resp.json()
            abstract = data.get("AbstractText", "")
            source_url = data.get("AbstractURL", "")
            heading = data.get("Heading", "Instant Answer")
            if abstract:
                return (
                    f"Web research results for: '{query}'\n\n"
                    f"1. {heading}\nSource: {source_url}\nSummary: {abstract}"
                )

    except Exception as e:
        return f"Web research temporarily unavailable: {e}. You can open a browser search using search_web."

    return f"No direct search summary found for '{query}'. You can open a full search using search_web."


def get_system_status() -> str:
    """Get CPU, RAM, and Battery status."""
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory().percent
        battery = psutil.sensors_battery()
        msg = f"System Status: CPU usage is {cpu:.0f}%, RAM usage is {ram:.0f}%."
        if battery:
            msg += f" Battery level is {battery.percent:.0f}% ({'plugged in' if battery.power_plugged else 'on battery'})."
        return msg
    except Exception as e:
        return f"Could not retrieve system status: {e}"


# Tool Schemas for OpenRouter tool calling
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get current local time on this computer.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_date",
            "description": "Get today's date.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Open any installed Windows application by name (Chrome, Spotify, Word, Discord, File Explorer, Task Manager, Settings...). Searches the Start Menu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application": {
                        "type": "string",
                        "description": "Application name as it appears in the Start Menu, e.g. 'chrome', 'spotify', 'word'.",
                    }
                },
                "required": ["application"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Open a search query directly in the user's web browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search topic or query to open.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_web",
            "description": (
                "Search the web for up-to-date facts, sources, summaries, and information. "
                "Returns source URLs and snippets to analyze."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Topic or question to research.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_research_note",
            "description": "Save research notes, summaries, or findings to a local Markdown file in the notes directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the note."},
                    "content": {"type": "string", "description": "Markdown body content of the note."},
                    "tags": {"type": "string", "description": "Optional comma-separated tags."},
                    "sources": {"type": "string", "description": "Optional URLs or source references."},
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_research_note",
            "description": "Read an existing research note from the notes directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title or filename of the note to read."}
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_research_notes",
            "description": "List all saved Markdown research notes.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": "Save a personal fact to permanent SQLite long-term memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The fact to remember."}
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_facts",
            "description": "Search saved long-term memories for relevant facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Topic or question to search memory for."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_memories",
            "description": "List recent long-term memories stored in the database.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": "Check computer CPU, RAM, and Battery status.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

AVAILABLE_TOOLS = {
    "get_current_time": get_current_time,
    "get_current_date": get_current_date,
    "open_application": open_application,
    "search_web": search_web,
    "research_web": research_web,
    "save_research_note": save_research_note,
    "read_research_note": read_research_note,
    "list_research_notes": list_research_notes,
    "remember_fact": remember_fact,
    "recall_facts": recall_facts,
    "list_memories": list_memories,
    "get_system_status": get_system_status,
}

TOOLS_SCHEMA.extend(SYSTEM_CONTROL_TOOLS_SCHEMA)
TOOLS_SCHEMA.extend(FILE_MANAGER_TOOLS_SCHEMA)
TOOLS_SCHEMA.extend(EMAIL_TOOLS_SCHEMA)
TOOLS_SCHEMA.extend(DESKTOP_TOOLS_SCHEMA)
TOOLS_SCHEMA.extend(SKILLS_TOOLS_SCHEMA)
TOOLS_SCHEMA.extend(REMINDER_TOOLS_SCHEMA)
TOOLS_SCHEMA.extend(VISION_TOOLS_SCHEMA)
TOOLS_SCHEMA.extend(MEDIA_TOOLS_SCHEMA)

AVAILABLE_TOOLS.update(SYSTEM_CONTROL_AVAILABLE_TOOLS)
AVAILABLE_TOOLS.update(FILE_MANAGER_AVAILABLE_TOOLS)
AVAILABLE_TOOLS.update(EMAIL_AVAILABLE_TOOLS)
AVAILABLE_TOOLS.update(DESKTOP_AVAILABLE_TOOLS)
AVAILABLE_TOOLS.update(SKILLS_AVAILABLE_TOOLS)
AVAILABLE_TOOLS.update(REMINDER_AVAILABLE_TOOLS)
AVAILABLE_TOOLS.update(VISION_AVAILABLE_TOOLS)
AVAILABLE_TOOLS.update(MEDIA_AVAILABLE_TOOLS)


# ---------------------------------------------------------------------------
# Smart tool selection
# ---------------------------------------------------------------------------
# Free tiers have tight tokens-per-minute limits (Groq: ~8K TPM on some models).
# Sending all ~65 tool schemas costs ~6K tokens per request, so we only send the
# groups that are relevant to the current request. Any registered tool can still
# be executed if the model calls it.

TOOL_GROUPS: dict[str, list[str]] = {
    "core": [
        "get_current_time", "get_current_date", "research_web", "remember_fact", "recall_facts",
        "open_application", "open_website", "calculate", "get_weather", "set_reminder", "play_media",
    ],
    "info": ["get_news", "wikipedia_summary", "search_web", "list_memories", "get_system_status"],
    "reminders": ["set_timer", "list_reminders", "cancel_reminder"],
    "notes": ["save_research_note", "read_research_note", "list_research_notes"],
    "media": ["media_control", "play_media", "play_next_result", "set_volume", "adjust_volume", "mute_volume", "get_volume"],
    "clipboard": ["get_clipboard", "set_clipboard", "type_text"],
    "vision": ["analyze_screen", "analyze_image", "take_screenshot"],
}

_GROUP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "info": ("news", "headline", "wiki", "who is", "who was", "what is", "tell me about", "history",
             "search", "google", "memor", "status", "cpu", "ram"),
    "reminders": ("timer", "remind", "alarm", "countdown", "schedule", "reminders"),
    "notes": ("note", "notes", "save that", "write down", "summary", "summar", "research"),
    "media": ("music", "song", "track", "play", "pause", "resume", "spotify", "volume", "mute",
              "louder", "quieter", "sound", "audio", "video", "watch", "listen", "youtube", "playlist",
              "another one", "different one", "first one", "first video"),
    "clipboard": ("clipboard", "copied", "copy", "paste", "type ", "typing"),
    "vision": ("screen", "see", "look", "screenshot", "image", "picture", "photo", "visible", "error on",
               "what's on", "read this", "this page", "this window"),
    "system": ("volume", "brightness", "battery", "disk", "storage", "network", "wifi", "internet",
               "settings", "lock", "shutdown", "shut down", "restart", "reboot", "sleep", "sign out",
               "signout", "log off", "power", "dim", "bright"),
    "desktop": ("window", "minimize", "maximise", "maximize", "focus", "switch to", "close", "screenshot",
                "type ", "folder", "explorer", "active app", "open windows"),
    "files": ("file", "folder", "directory", "document", "documents", "downloads", "desktop", "pictures",
              "copy", "move", "rename", "delete", "duplicate", "search for", "find", ".txt", ".md", ".py",
              ".pdf", "create", "read"),
    "email": ("email", "e-mail", "mail", "draft", "gmail", "outlook", "inbox", "recipient"),
}

# Module-sourced groups are filled from the per-module schemas so new tools are picked up automatically.
TOOL_GROUPS["system"] = [t["function"]["name"] for t in SYSTEM_CONTROL_TOOLS_SCHEMA]
TOOL_GROUPS["desktop"] = [t["function"]["name"] for t in DESKTOP_TOOLS_SCHEMA]
TOOL_GROUPS["files"] = [t["function"]["name"] for t in FILE_MANAGER_TOOLS_SCHEMA]
TOOL_GROUPS["email"] = [t["function"]["name"] for t in EMAIL_TOOLS_SCHEMA]

_SCHEMA_BY_NAME = {t["function"]["name"]: t for t in TOOLS_SCHEMA}


def select_tool_groups(text: str) -> set[str]:
    low = f" {str(text or '').lower()} "
    groups = {"core"}
    for group, words in _GROUP_KEYWORDS.items():
        if any(w in low for w in words):
            groups.add(group)
    return groups


def select_tools(text: str, extra_groups: set[str] | None = None, mode: str = "smart") -> list[dict]:
    """Return the tool schemas relevant to *text* (all tools when mode == 'all')."""
    if mode == "all":
        return list(TOOLS_SCHEMA)
    groups = select_tool_groups(text) | set(extra_groups or ())
    names: list[str] = []
    for group in groups:
        for name in TOOL_GROUPS.get(group, []):
            if name in _SCHEMA_BY_NAME and name not in names:
                names.append(name)
    return [_SCHEMA_BY_NAME[n] for n in names]
