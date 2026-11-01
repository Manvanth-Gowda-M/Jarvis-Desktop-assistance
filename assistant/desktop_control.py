"""
desktop_control.py — Voice-controlled desktop automation for Jarvis (Windows).

This module provides safe, auditable desktop-control primitives that Jarvis can
call in response to voice commands.  It operates in two modes:

  FULL mode  : pywinauto + PIL are available → rich UI Automation support.
  SAFE mode  : pywinauto absent → limited automation via win32gui / ctypes.

SAFETY CONTRACT
---------------
- No covert key-logging, screen recording, or surveillance.
- No UAC bypass or credential capture.
- Screenshots are taken ONLY when explicitly requested and are saved to an
  approved, user-visible location.
- `type_text` refuses shell commands and injection patterns.
- `close_window` never force-kills a process; it sends WM_CLOSE only.
- All public functions return ``str`` and never raise — errors are reported as
  human-readable strings so Jarvis can speak them aloud.

Dependencies (all on Windows by default or in the project venv):
  Required  : pywin32  (win32gui, win32con, win32api, win32process)
  Optional  : pywinauto  → FULL mode
  Optional  : Pillow     → screenshots (already installed: pillow 11.0.0)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Optional heavy dependencies — never crash at import time
# ---------------------------------------------------------------------------

try:
    from pywinauto import Application, Desktop  # type: ignore
    from pywinauto.findwindows import find_windows  # type: ignore

    PYWINAUTO_AVAILABLE: bool = True
except ImportError:
    PYWINAUTO_AVAILABLE = False

ImageGrab = None

try:
    from PIL import ImageGrab
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# pywin32 ships with the project venv and is required for SAFE-mode fallbacks.
try:
    import ctypes
    import win32api  # type: ignore
    import win32con  # type: ignore
    import win32gui  # type: ignore
    import win32process  # type: ignore

    WIN32_AVAILABLE: bool = True
except ImportError:
    WIN32_AVAILABLE = False

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_APPROVED_SCREENSHOT_ROOTS: tuple[str, ...] = (
    os.path.join(os.path.expanduser("~"), "Desktop", "Screenshots"),
    os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots"),
    os.path.join(os.path.expanduser("~"), "Pictures"),
    os.path.join(os.path.expanduser("~"), "Desktop"),
)

# Patterns that look like shell commands / script injection attempts
_DANGEROUS_TEXT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"),   # control characters
    re.compile(r"(rm\s+-rf|del\s+/[qfs]|format\s+[a-z]:)", re.I),
    re.compile(r"(powershell|cmd\.exe|bash|wscript|cscript)", re.I),
    re.compile(r"(invoke-|iex\s*\(|shell\s*\()", re.I),
    re.compile(r"(<script|javascript:|vbscript:)", re.I),
    re.compile(r"(net\s+user|net\s+localgroup)", re.I),
]

_MAX_TYPE_TEXT_LEN: int = 500


def _find_window_handle(partial_title: str) -> int | None:
    """
    Search all top-level windows for one whose title contains *partial_title*
    (case-insensitive).  Returns the first matching HWND or ``None``.
    """
    if not WIN32_AVAILABLE:
        return None

    results: list[int] = []
    needle = partial_title.lower()

    def _callback(hwnd: int, _: Any) -> bool:
        try:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if needle in title.lower():
                    results.append(hwnd)
        except Exception:
            pass
        return True  # keep enumerating

    win32gui.EnumWindows(_callback, None)
    return results[0] if results else None


_find_hwnd_by_partial_title = _find_window_handle


def _safe_get_title(hwnd: int) -> str:
    """Return the window title for *hwnd*, or an empty string on error."""
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def _is_approved_screenshot_path(path: str) -> bool:
    """
    Return True only if *path* resolves inside one of the approved
    screenshot root directories.
    """
    try:
        resolved = str(Path(path).resolve())
        for root in _APPROVED_SCREENSHOT_ROOTS:
            if resolved.startswith(str(Path(root).resolve())):
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_open_windows() -> str:
    """
    Return a formatted list of all visible, non-minimised top-level windows.

    Uses ``win32gui.EnumWindows``; falls back to a helpful error message if
    pywin32 is unavailable.

    Returns
    -------
    str
        Human-readable list of window titles, or an error/status message.
    """
    if not WIN32_AVAILABLE:
        return "Error: pywin32 is not installed. Cannot list windows."

    titles: list[str] = []

    def _callback(hwnd: int, _: Any) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            # Skip minimised windows
            placement = win32gui.GetWindowPlacement(hwnd)
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                return True
            title = win32gui.GetWindowText(hwnd)
            if title.strip():
                titles.append(title)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_callback, None)
    except Exception as exc:
        return f"Error enumerating windows: {exc}"

    if not titles:
        return "No open windows found."

    lines = [f"  {i + 1}. {t}" for i, t in enumerate(titles)]
    return "Open windows:\n" + "\n".join(lines)


def focus_window(window_title: str) -> str:
    """
    Bring the window whose title contains *window_title* to the foreground.

    Parameters
    ----------
    window_title:
        A partial (case-insensitive) substring of the target window's title.

    Returns
    -------
    str
        Confirmation or error message.
    """
    if not window_title.strip():
        return "Error: window_title must not be empty."
    if not WIN32_AVAILABLE:
        return "Error: pywin32 is not installed. Cannot focus windows."

    hwnd = _find_window_handle(window_title)
    if hwnd is None:
        return f"Window not found matching '{window_title}'."

    try:
        # Restore if minimised, then bring to front
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        actual_title = _safe_get_title(hwnd)
        return f"Focused window: '{actual_title}'."
    except Exception as exc:
        return f"Error focusing window '{window_title}': {exc}"


def minimize_window(window_title: str) -> str:
    """
    Minimise the window whose title contains *window_title*.

    Parameters
    ----------
    window_title:
        A partial (case-insensitive) substring of the target window's title.

    Returns
    -------
    str
        Confirmation or error message.
    """
    if not window_title.strip():
        return "Error: window_title must not be empty."
    if not WIN32_AVAILABLE:
        return "Error: pywin32 is not installed. Cannot minimize windows."

    hwnd = _find_window_handle(window_title)
    if hwnd is None:
        return f"Window not found matching '{window_title}'."

    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        actual_title = _safe_get_title(hwnd)
        return f"Minimised window: '{actual_title}'."
    except Exception as exc:
        return f"Error minimizing window '{window_title}': {exc}"


def maximize_window(window_title: str) -> str:
    """
    Maximise (or restore) the window whose title contains *window_title*.

    Parameters
    ----------
    window_title:
        A partial (case-insensitive) substring of the target window's title.

    Returns
    -------
    str
        Confirmation or error message.
    """
    if not window_title.strip():
        return "Error: window_title must not be empty."
    if not WIN32_AVAILABLE:
        return "Error: pywin32 is not installed. Cannot maximize windows."

    hwnd = _find_window_handle(window_title)
    if hwnd is None:
        return f"Window not found matching '{window_title}'."

    try:
        placement = win32gui.GetWindowPlacement(hwnd)
        if placement[1] == win32con.SW_SHOWMAXIMIZED:
            # Already maximised — restore instead
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            action = "Restored"
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            action = "Maximised"
        actual_title = _safe_get_title(hwnd)
        return f"{action} window: '{actual_title}'."
    except Exception as exc:
        return f"Error maximizing window '{window_title}': {exc}"


def close_window(window_title: str, confirmed: bool = False) -> str:
    """
    Close the window whose title contains *window_title*.

    A two-step confirmation is required to prevent accidental data loss:

    * ``confirmed=False`` (default) — returns a preview asking for confirmation.
    * ``confirmed=True``            — sends ``WM_CLOSE`` to the window.
    """
    if not window_title.strip():
        return "Error: window_title must not be empty."
    if not confirmed:
        return (
            f"Closing window '{window_title}' requires confirmation. "
            "Say 'yes, confirm' or set confirmed=True to proceed."
        )
    if not WIN32_AVAILABLE:
        return "Error: pywin32 is not installed. Cannot close windows."

    hwnd = _find_window_handle(window_title)
    if hwnd is None:
        return f"Window not found matching '{window_title}'."

    actual_title = _safe_get_title(hwnd)
    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return f"Close request sent to window: '{actual_title}'."
    except Exception as exc:
        return f"Error closing window '{actual_title}': {exc}"


def take_screenshot(save_path: str = "") -> str:
    """
    Capture the full screen and save it as a PNG.

    This function should be called **only when the user explicitly requests** a
    screenshot.

    Parameters
    ----------
    save_path:
        Full file path for the saved image.  If omitted, defaults to
        ``~/Desktop/Screenshots/screenshot_YYYYMMDD_HHMMSS.png``.
        Must resolve inside an approved directory.

    Returns
    -------
    str
        The absolute path of the saved screenshot, or an error message.
    """
    if not PIL_AVAILABLE:
        return (
            "Error: Pillow (PIL) is not installed. "
            "Install it with: pip install pillow"
        )

    # Build default path if none supplied
    if not save_path.strip():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_dir = Path.home() / "Desktop" / "Screenshots"
        save_path = str(default_dir / f"screenshot_{ts}.png")

    # Security: validate the resolved path is in an approved location
    if not _is_approved_screenshot_path(save_path):
        return (
            f"Error: '{save_path}' is not in an approved screenshot directory. "
            f"Approved roots: {', '.join(_APPROVED_SCREENSHOT_ROOTS)}"
        )

    try:
        dest = Path(save_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        screenshot = ImageGrab.grab()
        screenshot.save(str(dest), format="PNG")
        return f"Screenshot saved to: {dest.resolve()}"
    except Exception as exc:
        return f"Error taking screenshot: {exc}"


def _type_text_impl(sanitised: str) -> str:
    """Internal helper to type sanitised text."""
    # ── Type via pywinauto (FULL mode) ─────────────────────────────────────
    if PYWINAUTO_AVAILABLE:
        try:
            app = Application(backend="uia")
            focused = Desktop(backend="uia").get_focus()  # type: ignore[attr-defined]
            focused.type_keys(sanitised, with_spaces=True, pause=0.02)
            return f"Typed {len(sanitised)} character(s) into the active window."
        except Exception as exc:
            # Fall through to SAFE mode on failure
            _fallback_reason = str(exc)
    else:
        _fallback_reason = "pywinauto not available"

    # ── Type via PostMessage (SAFE mode) ───────────────────────────────────
    if not WIN32_AVAILABLE:
        return "Error: neither pywinauto nor pywin32 is installed. Cannot type text."

    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "Error: no active window to type into."
        for ch in sanitised:
            vk = win32api.VkKeyScan(ch)
            if vk == -1:
                continue  # skip characters with no VK mapping
            win32api.PostMessage(hwnd, win32con.WM_CHAR, ord(ch), 0)
            time.sleep(0.01)
        return (
            f"Typed {len(sanitised)} character(s) via PostMessage "
            f"(fallback; pywinauto reason: {_fallback_reason})."
        )
    except Exception as exc:
        return f"Error typing text: {exc}"


def type_text(text: str) -> str:
    """
    Type *text* into the currently focused window.

    Safety constraints
    ------------------
    * Refuses if *text* matches any dangerous shell/injection pattern.
    * Maximum length: 500 characters.
    * Only printable ASCII + common Unicode is allowed; raw escape sequences
      are stripped.
    """
    if not text:
        return "Error: text must not be empty."

    # ── Length guard ───────────────────────────────────────────────────────
    if len(text) > _MAX_TYPE_TEXT_LEN:
        return (
            f"Error: text exceeds maximum length of {_MAX_TYPE_TEXT_LEN} "
            f"characters ({len(text)} supplied)."
        )

    # ── Dangerous-pattern guard ────────────────────────────────────────────
    for pattern in _DANGEROUS_TEXT_PATTERNS:
        if pattern.search(text):
            return (
                "Refused: the text contains a pattern that looks like a shell "
                "command or script injection and cannot be typed for safety reasons."
            )

    # ── Sanitise: keep only printable characters ───────────────────────────
    sanitised = "".join(ch for ch in text if ch.isprintable() or ch in "\t\n")
    if sanitised != text:
        return (
            "Refused: the text contains non-printable characters. "
            "Please provide plain printable text only."
        )

    return _type_text_impl(sanitised)


def open_folder(path: str) -> str:
    """
    Open *path* in Windows Explorer.

    Parameters
    ----------
    path:
        Absolute or relative path to a directory.

    Returns
    -------
    str
        Confirmation or error message.
    """
    if not path.strip():
        return "Error: path must not be empty."

    try:
        resolved = Path(path).expanduser().resolve()
    except Exception as exc:
        return f"Error resolving path '{path}': {exc}"

    if not resolved.exists():
        return f"Error: path does not exist: '{resolved}'."
    if not resolved.is_dir():
        return f"Error: '{resolved}' is not a directory."

    try:
        subprocess.Popen(
            ["explorer.exe", str(resolved)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return f"Opened folder in Explorer: '{resolved}'."
    except Exception as exc:
        return f"Error opening folder '{resolved}': {exc}"


def get_active_window() -> str:
    """
    Return the title of the currently focused (foreground) window.

    Returns
    -------
    str
        Window title string, or an error/status message.
    """
    if not WIN32_AVAILABLE:
        return "Error: pywin32 is not installed. Cannot get active window."

    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "No active window detected."
        title = win32gui.GetWindowText(hwnd)
        return title if title.strip() else "Active window has no title."
    except Exception as exc:
        return f"Error retrieving active window: {exc}"


# ---------------------------------------------------------------------------
# Tool schema for Jarvis dispatcher
# ---------------------------------------------------------------------------

DESKTOP_TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_open_windows",
            "description": "List all visible, non-minimised windows currently open on the desktop.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": "Bring a window to the foreground by partial title match.",
            "parameters": {
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Partial (case-insensitive) title of the target window.",
                    }
                },
                "required": ["window_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "minimize_window",
            "description": "Minimise a window by partial title match.",
            "parameters": {
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Partial (case-insensitive) title of the target window.",
                    }
                },
                "required": ["window_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "maximize_window",
            "description": "Maximise (or restore if already maximised) a window by partial title match.",
            "parameters": {
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Partial (case-insensitive) title of the target window.",
                    }
                },
                "required": ["window_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_window",
            "description": (
                "Send a close request to a window. Requires confirmed=True to actually close. "
                "Never force-kills the process."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Partial (case-insensitive) title of the target window.",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": (
                            "Set to true only after the user has explicitly confirmed "
                            "the close action. Defaults to false (preview mode)."
                        ),
                        "default": False,
                    },
                },
                "required": ["window_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": (
                "Capture the full screen and save it as a PNG. "
                "Call ONLY when the user explicitly requests a screenshot."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "save_path": {
                        "type": "string",
                        "description": (
                            "Optional full file path for the saved image. "
                            "Defaults to ~/Desktop/Screenshots/screenshot_YYYYMMDD_HHMMSS.png."
                        ),
                        "default": "",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": (
                "Type plain text into the currently focused window. "
                "Refuses shell commands, injection patterns, and texts over 500 characters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Plain printable text to type (max 500 characters).",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_folder",
            "description": "Open a folder in Windows Explorer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to a directory.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_window",
            "description": "Return the title of the currently focused window.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

DESKTOP_AVAILABLE_TOOLS: dict[str, Any] = {
    "list_open_windows": list_open_windows,
    "focus_window": focus_window,
    "minimize_window": minimize_window,
    "maximize_window": maximize_window,
    "close_window": close_window,
    "take_screenshot": take_screenshot,
    "type_text": type_text,
    "open_folder": open_folder,
    "get_active_window": get_active_window,
}

# ---------------------------------------------------------------------------
# Module-level capability report (emitted on direct execution)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== desktop_control.py — capability report ===")
    print(f"  PYWINAUTO_AVAILABLE : {PYWINAUTO_AVAILABLE}")
    print(f"  PIL_AVAILABLE       : {PIL_AVAILABLE}")
    print(f"  WIN32_AVAILABLE     : {WIN32_AVAILABLE}")
    print()
    print("  Active window       :", get_active_window())
    print()
    print("  Open windows:")
    print(list_open_windows())
