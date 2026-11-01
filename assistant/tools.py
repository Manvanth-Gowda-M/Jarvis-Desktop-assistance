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

    return f"I don't know how to open '{application}' yet. Supported: Chrome, Notepad, Calculator, VS Code."


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
            "description": "Open a supported Windows application (Chrome, Notepad, Calculator, VS Code).",
            "parameters": {
                "type": "object",
                "properties": {
                    "application": {
                        "type": "string",
                        "description": "Application name, e.g. 'chrome', 'notepad', 'calc', 'vscode'.",
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

AVAILABLE_TOOLS.update(SYSTEM_CONTROL_AVAILABLE_TOOLS)
AVAILABLE_TOOLS.update(FILE_MANAGER_AVAILABLE_TOOLS)
AVAILABLE_TOOLS.update(EMAIL_AVAILABLE_TOOLS)
AVAILABLE_TOOLS.update(DESKTOP_AVAILABLE_TOOLS)
