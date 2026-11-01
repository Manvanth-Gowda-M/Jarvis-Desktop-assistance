"""
assistant/skills.py
===================
Everyday "Jarvis" skills that need no paid API:

* Weather (Open-Meteo, no key) with IP-based location fallback
* News headlines (DuckDuckGo News)
* Wikipedia summaries (Wikipedia REST API)
* Safe calculator (AST-based, no eval)
* Media keys (play/pause, next, previous, stop)
* Clipboard read/write
* Open a website by name or URL
"""

from __future__ import annotations

import ast
import ctypes
import math
import operator
import os
import re
import webbrowser
from urllib.parse import quote, quote_plus, urlparse

import requests

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover - optional dependency
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

from config import DEFAULT_LOCATION

_HTTP_HEADERS = {"User-Agent": "JarvisDesktopAssistant/3.0 (personal, non-commercial)"}
_TIMEOUT = 8

# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

_WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle", 61: "light rain", 63: "rain",
    65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain", 71: "light snow",
    73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


def _geocode(location: str) -> tuple[float, float, str] | None:
    resp = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1, "language": "en", "format": "json"},
        headers=_HTTP_HEADERS,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None
    r = results[0]
    label = ", ".join(p for p in (r.get("name"), r.get("admin1"), r.get("country")) if p)
    return float(r["latitude"]), float(r["longitude"]), label


def _ip_location() -> tuple[float, float, str] | None:
    try:
        resp = requests.get("https://ipapi.co/json/", headers=_HTTP_HEADERS, timeout=_TIMEOUT)
        if resp.status_code == 200:
            d = resp.json()
            if d.get("latitude") is not None:
                label = ", ".join(p for p in (d.get("city"), d.get("region"), d.get("country_name")) if p)
                return float(d["latitude"]), float(d["longitude"]), label
    except Exception:
        pass
    try:
        resp = requests.get("http://ip-api.com/json/", headers=_HTTP_HEADERS, timeout=_TIMEOUT)
        d = resp.json()
        if d.get("status") == "success":
            label = ", ".join(p for p in (d.get("city"), d.get("regionName"), d.get("country")) if p)
            return float(d["lat"]), float(d["lon"]), label
    except Exception:
        pass
    return None


def get_weather(location: str = "") -> str:
    """Current weather plus today's and tomorrow's forecast for a city (or your location)."""
    location = str(location or "").strip() or os.getenv("DEFAULT_LOCATION", DEFAULT_LOCATION).strip()
    try:
        place = _geocode(location) if location else _ip_location()
        if not place:
            return f"I couldn't find a location called '{location}'." if location else (
                "I couldn't determine your location. Tell me a city, or set DEFAULT_LOCATION in .env."
            )
        lat, lon, label = place

        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 2,
            },
            headers=_HTTP_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        cur = data.get("current", {})
        daily = data.get("daily", {})

        cond = _WEATHER_CODES.get(int(cur.get("weather_code", -1)), "unknown conditions")
        lines = [
            f"Weather in {label}: {cond}, {cur.get('temperature_2m')}°C "
            f"(feels like {cur.get('apparent_temperature')}°C), humidity {cur.get('relative_humidity_2m')}%, "
            f"wind {cur.get('wind_speed_10m')} km/h."
        ]
        for idx, day_name in enumerate(("Today", "Tomorrow")):
            try:
                d_cond = _WEATHER_CODES.get(int(daily["weather_code"][idx]), "mixed conditions")
                lines.append(
                    f"{day_name}: {d_cond}, high {daily['temperature_2m_max'][idx]}°C, "
                    f"low {daily['temperature_2m_min'][idx]}°C, "
                    f"{daily['precipitation_probability_max'][idx]}% chance of precipitation."
                )
            except (KeyError, IndexError, TypeError, ValueError):
                break
        return "\n".join(lines)
    except Exception as exc:
        return f"Weather service is unavailable right now: {exc}"


# ---------------------------------------------------------------------------
# News & Wikipedia
# ---------------------------------------------------------------------------

def get_news(topic: str = "", max_results: int = 5) -> str:
    """Latest news headlines, optionally about a topic."""
    topic = str(topic or "").strip()
    query = topic or "top world news today"
    max_results = max(1, min(int(max_results or 5), 10))
    if DDGS is None:
        return "News search needs the duckduckgo-search package (pip install duckduckgo-search)."
    try:
        results = list(DDGS().news(query, max_results=max_results))
    except Exception as exc:
        return f"News search is unavailable right now: {exc}"
    if not results:
        return f"I couldn't find recent news about '{query}'."

    lines = [f"Latest headlines{' about ' + topic if topic else ''}:"]
    for i, r in enumerate(results, 1):
        source = r.get("source", "")
        date = (r.get("date") or "")[:10]
        lines.append(
            f"{i}. {r.get('title', 'Untitled')} ({source}{', ' + date if date else ''})\n"
            f"   {str(r.get('body', ''))[:220]}\n   Source: {r.get('url', '')}"
        )
    return "\n".join(lines)


def wikipedia_summary(topic: str) -> str:
    """Short encyclopedia summary of a person, place, or concept."""
    topic = str(topic or "").strip()
    if not topic:
        return "Please tell me what to look up."
    try:
        search = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": topic, "limit": 1, "namespace": 0, "format": "json"},
            headers=_HTTP_HEADERS,
            timeout=_TIMEOUT,
        )
        search.raise_for_status()
        titles = search.json()[1]
        if not titles:
            return f"Wikipedia has no article matching '{topic}'."
        title = titles[0]
        resp = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}",
            headers=_HTTP_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        extract = data.get("extract", "").strip()
        url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
        return f"{data.get('title', title)}: {extract}\nSource: {url}" if extract else f"No summary available for '{title}'."
    except Exception as exc:
        return f"Wikipedia is unavailable right now: {exc}"


# ---------------------------------------------------------------------------
# Safe calculator
# ---------------------------------------------------------------------------

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    name: getattr(math, name)
    for name in (
        "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "log10", "log2",
        "exp", "floor", "ceil", "factorial", "radians", "degrees", "hypot", "gcd",
    )
}
_FUNCS.update({"abs": abs, "round": round, "min": min, "max": max})
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("exponent too large")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        if node.func.id == "factorial":
            arg = _eval_node(node.args[0])
            if arg > 500:
                raise ValueError("factorial argument too large")
        return _FUNCS[node.func.id](*[_eval_node(a) for a in node.args])
    raise ValueError("unsupported expression")


def calculate(expression: str) -> str:
    """Evaluate a math expression safely, e.g. '15% of 2400', 'sqrt(144) + 2^3'."""
    raw = str(expression or "").strip()
    if not raw:
        return "Please give me an expression to calculate."
    expr = raw.lower().replace("×", "*").replace("÷", "/").replace("^", "**")
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*", r"(\1/100)*", expr)
    expr = expr.replace("percent of", "/100*")
    expr = re.sub(r"\b(?:what is|what's|calculate|compute|equals?)\b", "", expr).strip(" =?")
    if len(expr) > 200:
        return "That expression is too long."
    try:
        result = _eval_node(ast.parse(expr, mode="eval"))
    except ZeroDivisionError:
        return "That's a division by zero, which is undefined."
    except Exception:
        return f"I couldn't evaluate '{raw}'. Try a plain arithmetic expression."
    if isinstance(result, float):
        result = round(result, 10)
        if result.is_integer():
            result = int(result)
    return f"{raw} = {result:,}" if isinstance(result, int) else f"{raw} = {result}"


# ---------------------------------------------------------------------------
# Media keys
# ---------------------------------------------------------------------------

_MEDIA_KEYS = {
    "play_pause": 0xB3,
    "play": 0xB3,
    "pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
}


def media_control(action: str) -> str:
    """Send a system media key: play_pause, next, previous, or stop."""
    key = str(action or "").strip().lower().replace(" ", "_").replace("-", "_")
    key = {"prev": "previous", "skip": "next", "resume": "play_pause", "toggle": "play_pause"}.get(key, key)
    vk = _MEDIA_KEYS.get(key)
    if vk is None:
        return "Unsupported media action. Use play_pause, next, previous, or stop."
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(vk, 0, 0x1, 0)          # KEYEVENTF_EXTENDEDKEY
        user32.keybd_event(vk, 0, 0x1 | 0x2, 0)    # + KEYEVENTF_KEYUP
    except Exception as exc:
        return f"Media control failed: {exc}"
    labels = {"play_pause": "Toggled play/pause", "play": "Resumed playback", "pause": "Paused playback",
              "next": "Skipped to the next track", "previous": "Went back to the previous track",
              "stop": "Stopped playback"}
    return labels[key] + "."


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

def get_clipboard() -> str:
    """Read the current text on the Windows clipboard."""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(13):  # CF_UNICODETEXT
                text = win32clipboard.GetClipboardData(13)
            else:
                text = ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception as exc:
        return f"Couldn't read the clipboard: {exc}"
    if not text:
        return "The clipboard has no text."
    text = str(text)
    if len(text) > 4000:
        text = text[:4000] + "\n...[truncated]"
    return f"Clipboard contents:\n{text}"


def set_clipboard(text: str) -> str:
    """Put text on the Windows clipboard."""
    text = str(text or "")
    if not text:
        return "Nothing to copy."
    if len(text) > 20000:
        return "That text is too long to copy (20,000 character limit)."
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, 13)
        finally:
            win32clipboard.CloseClipboard()
    except Exception as exc:
        return f"Couldn't write to the clipboard: {exc}"
    return f"Copied {len(text)} characters to the clipboard."


# ---------------------------------------------------------------------------
# Websites
# ---------------------------------------------------------------------------

_SITES = {
    "google": "https://www.google.com", "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com", "github": "https://github.com",
    "chatgpt": "https://chatgpt.com", "whatsapp": "https://web.whatsapp.com",
    "netflix": "https://www.netflix.com", "spotify": "https://open.spotify.com",
    "linkedin": "https://www.linkedin.com", "twitter": "https://x.com", "x": "https://x.com",
    "instagram": "https://www.instagram.com", "facebook": "https://www.facebook.com",
    "reddit": "https://www.reddit.com", "amazon": "https://www.amazon.com",
    "stackoverflow": "https://stackoverflow.com", "stack overflow": "https://stackoverflow.com",
    "wikipedia": "https://www.wikipedia.org", "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com", "outlook": "https://outlook.live.com",
    "drive": "https://drive.google.com", "google drive": "https://drive.google.com",
    "calendar": "https://calendar.google.com", "openrouter": "https://openrouter.ai",
    "groq": "https://console.groq.com", "nvidia": "https://build.nvidia.com",
}


def open_website(site: str) -> str:
    """Open a website by well-known name (e.g. 'github') or full URL."""
    target = str(site or "").strip()
    if not target:
        return "Which website should I open?"
    key = target.lower().removeprefix("the ").removesuffix(" website").removesuffix(".com").strip()
    url = _SITES.get(key)
    if url is None:
        candidate = target if "://" in target else f"https://{target}"
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or "." not in parsed.netloc or " " in parsed.netloc:
            # Not a URL: fall back to a search so the request still does something useful.
            url = f"https://www.google.com/search?q={quote_plus(target)}"
        else:
            url = candidate
    try:
        webbrowser.open(url)
    except Exception as exc:
        return f"Couldn't open the browser: {exc}"
    return f"Opened {url}"


SKILLS_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather and today's/tomorrow's forecast. Leave location empty for the user's current location.",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string", "description": "City name, e.g. 'London' or 'Hyderabad'. Optional."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_news",
            "description": "Get the latest news headlines, optionally about a topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Optional topic, e.g. 'AI', 'cricket', 'stock market'."},
                    "max_results": {"type": "integer", "description": "Number of headlines (1-10). Default 5."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia_summary",
            "description": "Get a short factual Wikipedia summary about a person, place, or concept.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "What to look up."}},
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Evaluate a math expression exactly (supports + - * / ^ %, 'X% of Y', sqrt, sin, log, pi...). Use for any arithmetic.",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "e.g. '18% of 2450' or 'sqrt(2)*10'."}},
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_control",
            "description": "Control music/video playback with system media keys.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"]},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clipboard",
            "description": "Read the text currently on the clipboard (e.g. to summarize or translate what the user copied).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_clipboard",
            "description": "Copy text to the clipboard so the user can paste it.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Text to copy."}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": "Open a website in the browser by name (github, gmail, youtube, spotify...) or URL.",
            "parameters": {
                "type": "object",
                "properties": {"site": {"type": "string", "description": "Site name or URL."}},
                "required": ["site"],
            },
        },
    },
]

SKILLS_AVAILABLE_TOOLS = {
    "get_weather": get_weather,
    "get_news": get_news,
    "wikipedia_summary": wikipedia_summary,
    "calculate": calculate,
    "media_control": media_control,
    "get_clipboard": get_clipboard,
    "set_clipboard": set_clipboard,
    "open_website": open_website,
}
