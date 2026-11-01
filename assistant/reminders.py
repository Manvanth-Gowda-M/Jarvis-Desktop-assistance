"""
assistant/reminders.py
======================
Timers and reminders that fire in the background and notify the user.

Reminders are persisted to SQLite so they survive a restart; anything that came due
while Jarvis was closed fires shortly after the next start.

UI layers call :func:`register_notifier` to decide how a due reminder is delivered
(speech, tray balloon, chat panel). Without a notifier the reminder is printed and
spoken with the default speaker.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta
from typing import Callable

from config import DATABASE_DIR

REMINDERS_DB = DATABASE_DIR / "reminders.db"

_notifiers: list[Callable[[str], None]] = []
_timers: dict[int, threading.Timer] = {}
_lock = threading.Lock()
_started = False


def _connect():
    REMINDERS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(REMINDERS_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS reminders (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               message TEXT NOT NULL,
               due_at TEXT NOT NULL,
               created_at TEXT NOT NULL,
               fired INTEGER NOT NULL DEFAULT 0
           )"""
    )
    return conn


def register_notifier(callback: Callable[[str], None]):
    """Register a function that receives the reminder text when it fires."""
    with _lock:
        if callback not in _notifiers:
            _notifiers.append(callback)


def unregister_notifier(callback: Callable[[str], None]):
    with _lock:
        if callback in _notifiers:
            _notifiers.remove(callback)


def _deliver(reminder_id: int, message: str):
    with _lock:
        _timers.pop(reminder_id, None)
        notifiers = list(_notifiers)
    try:
        with closing(_connect()) as conn:
            conn.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))
            conn.commit()
    except Exception as exc:
        print(f"[Reminders] Could not mark reminder {reminder_id} as fired: {exc}")

    text = f"Reminder: {message}"
    print(f"\n[Reminder] {message}")
    if not notifiers:
        try:
            from assistant.speech import speak
            speak(text, enable_voice_interrupt=False)
        except Exception:
            pass
        return
    for notify in notifiers:
        try:
            notify(text)
        except Exception as exc:
            print(f"[Reminders] Notifier error: {exc}")


def _schedule(reminder_id: int, message: str, due: datetime):
    delay = max(0.5, (due - datetime.now()).total_seconds())
    timer = threading.Timer(delay, _deliver, args=(reminder_id, message))
    timer.daemon = True
    timer.name = f"Reminder-{reminder_id}"
    with _lock:
        _timers[reminder_id] = timer
    timer.start()


def start_scheduler():
    """Load pending reminders from disk and schedule them (idempotent)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    try:
        with closing(_connect()) as conn:
            rows = conn.execute("SELECT id, message, due_at FROM reminders WHERE fired = 0").fetchall()
    except Exception as exc:
        print(f"[Reminders] Could not load reminders: {exc}")
        return
    for rid, message, due_at in rows:
        try:
            _schedule(rid, message, datetime.fromisoformat(due_at))
        except ValueError:
            continue


def _humanize(delta: timedelta) -> str:
    secs = int(round(delta.total_seconds()))
    if secs < 60:
        return f"{secs} second{'s' if secs != 1 else ''}"
    mins, secs = divmod(secs, 60)
    hours, mins = divmod(mins, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    if secs and not hours:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " and ".join(parts)


def _parse_clock(at_time: str) -> datetime | None:
    """Parse '17:30', '5:30 pm', '7pm', optionally prefixed with 'tomorrow'."""
    text = str(at_time or "").strip().lower()
    tomorrow = "tomorrow" in text
    text = re.sub(r"\b(?:tomorrow|at)\b", " ", text).strip()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?$", text)
    if not m:
        try:
            return datetime.fromisoformat(str(at_time).strip())
        except ValueError:
            return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    meridiem = (m.group(3) or "").replace(".", "")
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    now = datetime.now()
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if tomorrow:
        due += timedelta(days=1)
    elif due <= now:
        due += timedelta(days=1)
    return due


def set_reminder(message: str, minutes: float = 0, seconds: float = 0, hours: float = 0, at_time: str = "") -> str:
    """Schedule a reminder after a delay (minutes/seconds/hours) or at a clock time ('18:30', '7 pm')."""
    message = str(message or "").strip() or "Time's up"
    if len(message) > 300:
        return "That reminder text is too long."
    start_scheduler()

    if at_time:
        due = _parse_clock(at_time)
        if due is None:
            return f"I couldn't understand the time '{at_time}'. Try a format like '18:30' or '7 pm'."
    else:
        try:
            total = float(hours or 0) * 3600 + float(minutes or 0) * 60 + float(seconds or 0)
        except (TypeError, ValueError):
            return "The reminder delay must be a number."
        if total <= 0:
            return "Please tell me when: for example 'in 10 minutes' or 'at 6 pm'."
        if total > 60 * 60 * 24 * 30:
            return "I can only schedule reminders up to 30 days ahead."
        due = datetime.now() + timedelta(seconds=total)

    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO reminders (message, due_at, created_at) VALUES (?, ?, ?)",
            (message, due.isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        rid = cur.lastrowid
    _schedule(rid, message, due)
    return (
        f"Reminder #{rid} set for {due.strftime('%I:%M %p').lstrip('0')}"
        f"{' tomorrow' if due.date() > datetime.now().date() else ''}"
        f" (in {_humanize(due - datetime.now())}): {message}"
    )


def set_timer(minutes: float = 0, seconds: float = 0, hours: float = 0, label: str = "") -> str:
    """Start a countdown timer."""
    result = set_reminder(label or "Your timer is done", minutes=minutes, seconds=seconds, hours=hours)
    return result.replace("Reminder", "Timer", 1) if result.startswith("Reminder") else result


def get_pending_reminders() -> list[dict]:
    """Structured pending reminders for UIs: [{'id', 'message', 'due'}]."""
    start_scheduler()
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT id, message, due_at FROM reminders WHERE fired = 0 ORDER BY due_at"
        ).fetchall()
    out = []
    for rid, message, due_at in rows:
        try:
            out.append({"id": rid, "message": message, "due": datetime.fromisoformat(due_at)})
        except ValueError:
            continue
    return out


def list_reminders() -> str:
    """List pending reminders and timers."""
    start_scheduler()
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT id, message, due_at FROM reminders WHERE fired = 0 ORDER BY due_at"
        ).fetchall()
    if not rows:
        return "You have no pending reminders."
    lines = ["Pending reminders:"]
    now = datetime.now()
    for rid, message, due_at in rows:
        due = datetime.fromisoformat(due_at)
        lines.append(f"#{rid} at {due.strftime('%a %I:%M %p')} (in {_humanize(max(due - now, timedelta()))}): {message}")
    return "\n".join(lines)


def cancel_reminder(reminder_id: int) -> str:
    """Cancel a pending reminder by its number."""
    try:
        rid = int(reminder_id)
    except (TypeError, ValueError):
        return "Please give the reminder number to cancel (see list_reminders)."
    with _lock:
        timer = _timers.pop(rid, None)
    if timer:
        timer.cancel()
    with closing(_connect()) as conn:
        cur = conn.execute("UPDATE reminders SET fired = 1 WHERE id = ? AND fired = 0", (rid,))
        conn.commit()
    return f"Reminder #{rid} cancelled." if cur.rowcount else f"No pending reminder #{rid} found."


REMINDER_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Remind the user about something later, either after a delay or at a clock time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "What to remind the user about."},
                    "minutes": {"type": "number", "description": "Delay in minutes."},
                    "seconds": {"type": "number", "description": "Delay in seconds."},
                    "hours": {"type": "number", "description": "Delay in hours."},
                    "at_time": {"type": "string", "description": "Clock time instead of a delay, e.g. '18:30', '7 pm', 'tomorrow 9am'."},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "Start a countdown timer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number"},
                    "seconds": {"type": "number"},
                    "hours": {"type": "number"},
                    "label": {"type": "string", "description": "Optional label, e.g. 'pasta'."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "List pending reminders and timers.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_reminder",
            "description": "Cancel a pending reminder or timer by its number.",
            "parameters": {
                "type": "object",
                "properties": {"reminder_id": {"type": "integer"}},
                "required": ["reminder_id"],
            },
        },
    },
]

REMINDER_AVAILABLE_TOOLS = {
    "set_reminder": set_reminder,
    "set_timer": set_timer,
    "list_reminders": list_reminders,
    "cancel_reminder": cancel_reminder,
}
