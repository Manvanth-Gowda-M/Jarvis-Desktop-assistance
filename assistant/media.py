"""
assistant/media.py
==================
Play songs and videos directly: finds the top YouTube result for a request and
opens the video page itself (which starts playing), instead of a search page.
No API key needed.
"""

from __future__ import annotations

import json
import re
import webbrowser
from urllib.parse import quote_plus

import requests

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
_VIDEO_RE = re.compile(r'"videoRenderer":\{"videoId":"([\w-]{11})"')
_ID_FROM_URL = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})")

_last_results: list[dict] = []


def _search_youtube_page(query: str, limit: int = 5) -> list[dict]:
    resp = requests.get(
        "https://www.youtube.com/results",
        params={"search_query": query},
        headers=_HEADERS,
        cookies={"CONSENT": "YES+1"},
        timeout=10,
    )
    resp.raise_for_status()
    html = resp.text
    results = []
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html)
    if m:
        try:
            data = json.loads(m.group(1))
            sections = (data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]
                        ["sectionListRenderer"]["contents"])
            for sec in sections:
                for item in sec.get("itemSectionRenderer", {}).get("contents", []):
                    vr = item.get("videoRenderer")
                    if not vr or not vr.get("videoId"):
                        continue
                    title = "".join(r.get("text", "") for r in vr.get("title", {}).get("runs", []))
                    channel = "".join(r.get("text", "") for r in vr.get("ownerText", {}).get("runs", []))
                    length = vr.get("lengthText", {}).get("simpleText", "")
                    if not length:  # live streams / upcoming have no length; skip unless nothing else
                        continue
                    results.append({"id": vr["videoId"], "title": title, "channel": channel, "length": length})
                    if len(results) >= limit:
                        return results
        except (KeyError, TypeError, ValueError):
            pass
    if not results:
        for vid in dict.fromkeys(_VIDEO_RE.findall(html)):
            results.append({"id": vid, "title": "", "channel": "", "length": ""})
            if len(results) >= limit:
                break
    return results


def _search_ddg_videos(query: str, limit: int = 5) -> list[dict]:
    if DDGS is None:
        return []
    out = []
    for r in DDGS().videos(f"{query} youtube", max_results=limit * 2):
        url = r.get("content") or r.get("embed_url") or ""
        m = _ID_FROM_URL.search(url)
        if m:
            out.append({"id": m.group(1), "title": r.get("title", ""), "channel": r.get("publisher", ""),
                        "length": r.get("duration", "")})
        if len(out) >= limit:
            break
    return out


def find_videos(query: str, limit: int = 5) -> list[dict]:
    for finder in (_search_youtube_page, _search_ddg_videos):
        try:
            found = finder(query, limit)
            if found:
                return found
        except Exception as exc:
            print(f"[Media] {finder.__name__} failed: {exc}")
    return []


def play_media(query: str, platform: str = "youtube", result_number: int = 1) -> str:
    """Find and immediately play a song/video. platform: youtube (default), youtube_music, spotify."""
    global _last_results
    query = str(query or "").strip()
    platform = str(platform or "youtube").strip().lower().replace(" ", "_")
    try:
        n = max(1, min(int(result_number or 1), 5))
    except (TypeError, ValueError):
        n = 1

    if platform == "spotify":
        try:
            import os
            os.startfile(f"spotify:search:{quote_plus(query)}")
            return (f"Opened Spotify search for '{query}'. Spotify doesn't allow starting playback without its "
                    "paid API, so press play on the top result (or ask me to play it on YouTube).")
        except Exception:
            webbrowser.open(f"https://open.spotify.com/search/{quote_plus(query)}")
            return f"Opened Spotify web search for '{query}'."

    if not query:
        if not _last_results:
            return "Tell me what to play, for example 'play Barsaat song'."
        results = _last_results
    else:
        results = find_videos(query)
        if results:
            _last_results = results
            globals()["_cursor"] = n

    if not results:
        webbrowser.open(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
        return f"I couldn't fetch results directly, so I opened a YouTube search for '{query}'."

    pick = results[min(n, len(results)) - 1]
    host = "https://music.youtube.com" if platform == "youtube_music" else "https://www.youtube.com"
    url = f"{host}/watch?v={pick['id']}"
    webbrowser.open(url)
    label = pick["title"] or query
    extra = f" by {pick['channel']}" if pick.get("channel") else ""
    length = f" ({pick['length']})" if pick.get("length") else ""
    return f"Now playing: {label}{extra}{length} on {'YouTube Music' if platform == 'youtube_music' else 'YouTube'}. {url}"


def play_next_result() -> str:
    """Play the next video from the last search (e.g. 'play a different one')."""
    if not _last_results:
        return "There's no previous search. Tell me what to play."
    global _cursor
    _cursor = (globals().get("_cursor", 1) % len(_last_results)) + 1
    return play_media("", result_number=_cursor)


MEDIA_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "play_media",
            "description": (
                "PLAY a song, music, video, playlist, or any YouTube content immediately. Finds the top result and "
                "opens the video so it starts playing. Use this whenever the user says play/put on/listen to/watch. "
                "Do NOT use search_web or open_website for playing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to play, e.g. 'Barsaat song', 'trending hindi songs 2026', 'lofi beats'."},
                    "platform": {"type": "string", "enum": ["youtube", "youtube_music", "spotify"], "description": "Default youtube."},
                    "result_number": {"type": "integer", "description": "Which search result to play (1 = top). Default 1."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_next_result",
            "description": "Play a different result from the last play_media search (e.g. 'play another one', 'not this one').",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

MEDIA_AVAILABLE_TOOLS = {"play_media": play_media, "play_next_result": play_next_result}
