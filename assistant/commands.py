import datetime as dt
from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import quote_plus
import webbrowser

from assistant.tools import (
    get_current_time,
    get_current_date,
    open_application,
    get_system_status,
    remember_fact,
    recall_facts,
    list_memories,
    list_research_notes,
)
from assistant.system_control import (
    set_volume,
    adjust_volume,
    mute_volume,
    get_volume,
    set_brightness,
    adjust_brightness,
    get_brightness,
)
from config import DEFAULT_BRIGHTNESS_STEP
from assistant.speech import prepare_spoken_answer


def _parse_volume_command(clean: str) -> tuple[str, Any] | None:
    """Parse volume intent from natural text, filtering out math/informational queries."""
    clean = clean.rstrip(".?! ")

    # Conversational or math filter
    conversational_triggers = (
        "why ", "why's ", "how ", "explain ", "calculate ", "formula ", "what does ", "sound like",
        "solve ", "help ", "definition ", "define ", "meaning of ", "difference between ",
        "what is the volume of", "what's the volume of", "can you explain"
    )
    if any(clean.startswith(trig) for trig in conversational_triggers):
        return None
    if " of a " in clean or " of the " in clean or " of this " in clean:
        return None
    for shape in ("sphere", "cylinder", "cube", "cone", "pyramid", "box", "tank", "prism", "liquid", "gas", "container"):
        if shape in clean:
            return None

    # Mute / Unmute
    if clean in {
        "mute", "mute volume", "mute the volume", "mute audio", "mute sound",
        "silence", "silence audio", "turn off volume", "turn off sound", "silence the volume"
    }:
        return ("mute", True)

    if clean in {
        "unmute", "unmute volume", "unmute the volume", "unmute audio", "unmute sound",
        "turn on volume", "turn on sound", "enable sound", "restore volume", "unmute the audio"
    }:
        return ("mute", False)

    # Get / Query volume
    if clean in {
        "get volume", "what is the volume", "what is the current volume",
        "check volume", "check the volume", "current volume", "volume level",
        "what's the volume", "what is volume", "tell me the volume", "show volume",
        "what is the audio level", "check the sound level"
    }:
        return ("get", None)

    # Relative adjustment with explicit "by <number>":
    # e.g., "increase volume by 15%", "reduce volume by 20%", "turn down volume by 10%"
    adj_by_match = re.search(r"\bby\s+(\d+)\s*(?:%|percent)?\b", clean)
    if adj_by_match:
        amount = int(adj_by_match.group(1))
        if any(w in clean for w in ("increase", "turn up", "raise", "boost", "up")):
            return ("adjust_up", amount)
        if any(w in clean for w in ("decrease", "turn down", "lower", "reduce", "drop", "down")):
            return ("adjust_down", amount)

    # Target volume set:
    # Any command setting volume to a specific target level (e.g., "reduce the volume to 40%",
    # "increase the volume to 40%", "turn down the volume to 20%", "set volume to 30%", "volume to 50%")
    if any(w in clean for w in ("volume", "sound", "audio")):
        # 1) Look for "to <number>" or "at <number>" or "= <number>"
        target_to = re.search(r"\b(?:to|at|=)\s*(\d+)\s*(?:%|percent)?\b", clean)
        if target_to:
            return ("set", int(target_to.group(1)))

        # 2) Look for setting commands with a number at the end
        target_direct = re.search(
            r"^(?:(?:set|change|turn|put|make|reduce|increase|decrease|raise|lower|adjust|drop|boost|bring)\s+)?(?:the\s+)?(?:system\s+)?(?:master\s+)?(?:volume|sound|audio)\s*(?:to|at|=)?\s*(\d+)\s*(?:%|percent)?$",
            clean
        )
        if target_direct:
            return ("set", int(target_direct.group(1)))

        # 3) "<number> percent volume" or "set to <number>"
        num_vol = re.match(r"^(\d+)\s*(?:%|percent)\s*(?:volume|sound|audio)?$", clean)
        if num_vol:
            return ("set", int(num_vol.group(1)))

    # Relative adjustments without numbers (defaults to 10%):
    up_triggers = {
        "increase volume", "increase the volume", "turn up the volume", "turn up volume",
        "volume up", "raise volume", "raise the volume", "louder", "make it louder",
        "boost volume", "boost the volume", "turn it up"
    }
    if clean in up_triggers:
        return ("adjust_up", 10)

    down_triggers = {
        "decrease volume", "decrease the volume", "turn down the volume", "turn down volume",
        "volume down", "lower volume", "lower the volume", "quieter", "make it quieter",
        "reduce volume", "reduce the volume", "softer", "turn it down", "drop the volume", "drop volume"
    }
    if clean in down_triggers:
        return ("adjust_down", 10)

    return None


def _parse_brightness_command(clean: str) -> tuple[str, Any] | None:
    """Parse screen brightness intent from natural text, filtering out informational queries."""
    clean = clean.rstrip(".?! ")

    # Conversational or educational filter
    conversational_triggers = (
        "why ", "why's ", "how ", "explain ", "what is brightness", "what's brightness",
        "definition ", "define ", "meaning of ", "difference between ", "formula ",
        "can you explain", "what does brightness mean", "history of "
    )
    if any(clean.startswith(trig) for trig in conversational_triggers):
        return None

    # Get / Query brightness
    if clean in {
        "get brightness", "what is the brightness", "what is the current brightness",
        "check brightness", "check the brightness", "current brightness", "brightness level",
        "what's the brightness", "tell me the brightness", "show brightness",
        "what is the screen brightness", "check screen brightness", "how bright is the screen"
    }:
        return ("get", None)

    # Relative adjustment with explicit "by <number>":
    # e.g., "increase brightness by 15%", "dim the screen by 20%", "lower brightness by 10%"
    adj_by_match = re.search(r"\bby\s+(\d+)\s*(?:%|percent)?\b", clean)
    if adj_by_match and any(w in clean for w in ("brightness", "screen", "display")):
        amount = int(adj_by_match.group(1))
        if any(w in clean for w in ("increase", "turn up", "raise", "boost", "up", "brighter")):
            return ("adjust_up", amount)
        if any(w in clean for w in ("decrease", "turn down", "lower", "reduce", "drop", "down", "dim", "dimmer")):
            return ("adjust_down", amount)

    # Target brightness set:
    # Look for "to <number>" or "at <number>" or "= <number>" or ending with number
    # e.g., "set brightness to 60 percent", "set screen brightness to 60", "dim the screen to 30%",
    # "brightness to 50%", "brightness 60%", "make brightness 80%", "turn brightness to 70%"
    if any(w in clean for w in ("brightness", "screen", "display")):
        target_to = re.search(r"\b(?:to|at|=)\s*(\d+)\s*(?:%|percent)?\b", clean)
        if target_to:
            return ("set", int(target_to.group(1)))

        target_direct = re.search(
            r"^(?:(?:set|change|turn|put|make|reduce|increase|decrease|raise|lower|adjust|drop|boost|bring)\s+)?(?:the\s+)?(?:screen\s+|display\s+)?(?:brightness)\s*(?:to|at|=)?\s*(\d+)\s*(?:%|percent)?$",
            clean
        )
        if target_direct:
            return ("set", int(target_direct.group(1)))

        num_bright = re.match(r"^(\d+)\s*(?:%|percent)\s*(?:brightness|screen brightness)?$", clean)
        if num_bright:
            return ("set", int(num_bright.group(1)))

    # Relative adjustments without numbers (defaults to DEFAULT_BRIGHTNESS_STEP):
    up_triggers = {
        "increase brightness", "increase the brightness", "turn up brightness", "turn up the brightness",
        "brightness up", "raise brightness", "raise the brightness", "make the screen brighter",
        "make screen brighter", "brighter", "screen brighter", "boost brightness", "make it brighter"
    }
    if clean in up_triggers or ("brighter" in clean and any(w in clean for w in ("screen", "display", "make", "turn"))):
        return ("adjust_up", DEFAULT_BRIGHTNESS_STEP)

    down_triggers = {
        "decrease brightness", "decrease the brightness", "turn down brightness", "turn down the brightness",
        "brightness down", "lower brightness", "lower the brightness", "dim the screen", "dim screen",
        "make the screen dimmer", "make screen dimmer", "dimmer", "screen dimmer", "dim brightness",
        "make it dimmer", "reduce brightness", "reduce the brightness"
    }
    if clean in down_triggers or ("dim" in clean and any(w in clean for w in ("screen", "display", "the screen", "it"))):
        return ("adjust_down", DEFAULT_BRIGHTNESS_STEP)

    return None


@dataclass
class ActionResult:
    handled: bool
    detailed_text: str = ""
    spoken_text: str = ""
    should_exit: bool = False
    mode_change: str | None = None  # "text" or "voice"


def execute_fast_command(text: str) -> ActionResult:
    """Evaluate if the user query is a fast local command.

    Returns an ActionResult indicating if handled, without calling external AI APIs.
    """
    clean = text.lower().strip()
    if not clean:
        return ActionResult(handled=False)

    # 1. Exit Commands
    if clean in {"exit", "quit", "goodbye", "shutdown jarvis", "close jarvis"}:
        msg = "Goodbye. Have a productive day."
        return ActionResult(
            handled=True,
            detailed_text=msg,
            spoken_text=msg,
            should_exit=True,
        )

    # 2. Mode Switches
    if clean in {"text mode", "switch to text mode"}:
        return ActionResult(
            handled=True,
            detailed_text="Switched to text mode.",
            spoken_text="Switched to text mode.",
            mode_change="text",
        )

    if clean in {"voice mode", "switch to voice mode"}:
        return ActionResult(
            handled=True,
            detailed_text="Switched to voice mode.",
            spoken_text="Voice mode enabled.",
            mode_change="voice",
        )

    # 3. Time and Date (Natural matching)
    if (
        clean in {"time", "what time is it", "tell me the time", "current time", "what's the time"}
        or re.search(r"\b(what time is it|what's the time|tell me the time|current time|what is the time|time now)\b", clean)
    ):
        time_str = get_current_time()
        msg = f"It is currently {time_str}."
        return ActionResult(
            handled=True,
            detailed_text=msg,
            spoken_text=msg,
        )

    if (
        clean in {"date", "what is today's date", "what is the date", "today's date", "what's today's date"}
        or re.search(r"\b(what is today'?s date|what's today'?s date|what date is it|today'?s date|current date)\b", clean)
    ):
        date_str = get_current_date()
        msg = f"Today is {date_str}."
        return ActionResult(
            handled=True,
            detailed_text=msg,
            spoken_text=msg,
        )

    # 4. System Status & Diagnostics
    if clean in {"system status", "check system status", "computer status", "status", "cpu", "system diagnostics"}:
        status = get_system_status()
        spoken = prepare_spoken_answer(status)
        return ActionResult(
            handled=True,
            detailed_text=status,
            spoken_text=spoken,
        )

    if clean in {"battery", "battery status", "check battery", "how much battery", "battery percentage"}:
        from assistant.system_control import get_battery_status
        status = get_battery_status()
        return ActionResult(handled=True, detailed_text=status, spoken_text=status)

    if clean in {"disk", "disk usage", "storage", "storage status", "check disk", "check storage"}:
        from assistant.system_control import get_disk_usage
        usage = get_disk_usage()
        spoken = prepare_spoken_answer(usage)
        return ActionResult(handled=True, detailed_text=usage, spoken_text=spoken)

    if clean in {"network", "network status", "internet status", "wifi status", "check internet", "connectivity"}:
        from assistant.system_control import get_network_status
        status = get_network_status()
        return ActionResult(handled=True, detailed_text=status, spoken_text=status)

    # 4.1. Fast Screen Lock & Screenshots
    if clean in {"lock workstation", "lock computer", "lock screen", "lock pc", "lock the computer", "lock the pc"}:
        from assistant.system_control import lock_workstation
        res = lock_workstation()
        return ActionResult(handled=True, detailed_text=res, spoken_text="Workstation locked.")

    if clean in {"take a screenshot", "take screenshot", "capture screen", "screenshot", "save screenshot"}:
        from assistant.desktop_control import take_screenshot
        res = take_screenshot()
        return ActionResult(handled=True, detailed_text=res, spoken_text="Screenshot captured.")

    # 4.5. Audio & Volume Control (Local Windows execution, bypasses LLM)
    vol_action = _parse_volume_command(clean)
    if vol_action:
        action_type, arg = vol_action
        if action_type == "mute":
            res = mute_volume(arg)
        elif action_type == "get":
            res = get_volume()
        elif action_type == "set":
            res = set_volume(arg)
        elif action_type == "adjust_up":
            res = adjust_volume("up", arg)
        elif action_type == "adjust_down":
            res = adjust_volume("down", arg)
        else:
            res = "Unsupported volume operation."

        return ActionResult(
            handled=True,
            detailed_text=res,
            spoken_text=res,
        )

    # 4.6. Screen Brightness Control (Local Windows execution, bypasses LLM)
    bright_action = _parse_brightness_command(clean)
    if bright_action:
        action_type, arg = bright_action
        if action_type == "get":
            res = get_brightness()
        elif action_type == "set":
            res = set_brightness(arg)
        elif action_type == "adjust_up":
            res = adjust_brightness("up", arg)
        elif action_type == "adjust_down":
            res = adjust_brightness("down", arg)
        else:
            res = "Unsupported brightness operation."

        return ActionResult(
            handled=True,
            detailed_text=res,
            spoken_text=res,
        )

    # 5. Direct Web Shortcuts
    if clean in {"open google", "google"}:
        webbrowser.open("https://www.google.com")
        msg = "Opening Google in your browser."
        return ActionResult(handled=True, detailed_text=msg, spoken_text=msg)

    if clean in {"open youtube", "youtube"}:
        webbrowser.open("https://www.youtube.com")
        msg = "Opening YouTube in your browser."
        return ActionResult(handled=True, detailed_text=msg, spoken_text=msg)

    if clean.startswith("google "):
        q = text[7:].strip()
        webbrowser.open("https://www.google.com/search?q=" + quote_plus(q))
        msg = f"Searching Google for '{q}'."
        return ActionResult(handled=True, detailed_text=msg, spoken_text=msg)

    if clean.startswith("youtube "):
        q = text[8:].strip()
        webbrowser.open("https://www.youtube.com/results?search_query=" + quote_plus(q))
        msg = f"Searching YouTube for '{q}'."
        return ActionResult(handled=True, detailed_text=msg, spoken_text=msg)

    if clean.startswith("search for "):
        q = text[11:].strip()
        webbrowser.open("https://www.google.com/search?q=" + quote_plus(q))
        msg = f"Searching for '{q}'."
        return ActionResult(handled=True, detailed_text=msg, spoken_text=msg)

    # 6. Application Launchers
    app_mappings = {
        ("open chrome", "launch chrome"): "chrome",
        ("open notepad", "launch notepad"): "notepad",
        ("open calculator", "launch calculator", "open calc"): "calculator",
        ("open vscode", "open visual studio code", "launch vscode", "open code"): "vscode",
    }
    for triggers, app_name in app_mappings.items():
        if clean in triggers:
            result = open_application(app_name)
            return ActionResult(
                handled=True,
                detailed_text=result,
                spoken_text=result,
            )

    # 7. Memory Operations
    if clean.startswith("remember "):
        fact = text[9:].strip()
        if fact:
            res = remember_fact(fact)
            return ActionResult(
                handled=True,
                detailed_text=res,
                spoken_text=res,
            )

    if "what do you remember" in clean or clean in {"list memories", "show memories"}:
        if "about" in clean:
            topic = clean.split("about", 1)[1].strip()
            res = recall_facts(topic)
        else:
            res = list_memories()
        spoken = prepare_spoken_answer(res)
        return ActionResult(
            handled=True,
            detailed_text=res,
            spoken_text=spoken,
        )

    # 8. Research Notes
    if clean in {"list notes", "list research notes", "show notes"}:
        notes = list_research_notes()
        spoken = prepare_spoken_answer(notes)
        return ActionResult(
            handled=True,
            detailed_text=notes,
            spoken_text=spoken,
        )

    # 9. Email Draft Quick Actions
    from assistant.email_assistant import (
        _default_assistant,
        get_email_provider_status,
        list_email_drafts,
    )

    if clean in {"email status", "check email status", "email connection", "check email connection"}:
        status_text = get_email_provider_status()
        spoken = "Email status checked. See the desktop panel for provider details."
        return ActionResult(handled=True, detailed_text=status_text, spoken_text=spoken)

    if clean in {"list email drafts", "show email drafts", "list drafts", "show drafts"}:
        drafts_text = list_email_drafts()
        spoken = "Here are your active email drafts."
        return ActionResult(handled=True, detailed_text=drafts_text, spoken_text=spoken)

    if clean in {
        "read email draft", "read the email draft", "read draft", "read the draft",
        "read the latest draft", "read latest draft", "read email draft to me", "read the email draft to me"
    }:
        latest = _default_assistant.store.get_latest()
        if not latest:
            msg = "No email drafts found in memory to read."
            return ActionResult(handled=True, detailed_text=msg, spoken_text=msg)
        spoken = f"Draft to {', '.join(latest.to)}. Subject: {latest.subject}. {latest.body}"
        return ActionResult(handled=True, detailed_text=latest.preview(), spoken_text=spoken)

    if clean in {"use gmail for this draft", "save this draft in gmail", "save draft in gmail", "save to gmail"}:
        latest = _default_assistant.store.get_latest()
        if not latest:
            msg = "No active draft found to save to Gmail."
            return ActionResult(handled=True, detailed_text=msg, spoken_text=msg)
        result_text = _default_assistant.save_draft_to_provider(latest.draft_id, provider="gmail")
        spoken = "Your Gmail draft has been saved." if "✅" in result_text else "Failed to save draft in Gmail."
        return ActionResult(handled=True, detailed_text=result_text, spoken_text=spoken)

    if clean in {"use outlook for this draft", "save this draft in outlook", "save draft in outlook", "save to outlook"}:
        latest = _default_assistant.store.get_latest()
        if not latest:
            msg = "No active draft found to save to Outlook."
            return ActionResult(handled=True, detailed_text=msg, spoken_text=msg)
        result_text = _default_assistant.save_draft_to_provider(latest.draft_id, provider="outlook")
        spoken = "Your Outlook draft has been saved." if "✅" in result_text else "Failed to save draft in Outlook."
        return ActionResult(handled=True, detailed_text=result_text, spoken_text=spoken)

    return ActionResult(handled=False)


class CommandRouter:
    """CommandRouter class preserving OOP compatibility."""

    def __init__(self, speaker=None, memory=None):
        self.speaker = speaker
        self.memory = memory

    def try_execute(self, text: str) -> ActionResult | str | bool:
        """Execute fast command and return ActionResult."""
        return execute_fast_command(text)
