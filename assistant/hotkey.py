"""
assistant/hotkey.py
===================
System-wide push-to-talk hotkey (default Ctrl+Alt+J) using the Win32 RegisterHotKey API.
No extra dependencies; runs a tiny message loop on its own daemon thread.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

_MODIFIERS = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "win": 0x0008}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_SPECIAL_KEYS = {"space": 0x20, "enter": 0x0D, "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
                 "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
                 "f11": 0x7A, "f12": 0x7B}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse 'ctrl+alt+j' into (modifier_flags, virtual_key)."""
    parts = [p.strip().lower() for p in str(spec or "").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods = 0
    for p in parts[:-1]:
        if p not in _MODIFIERS:
            raise ValueError(f"unknown modifier '{p}'")
        mods |= _MODIFIERS[p]
    key = parts[-1]
    if key in _SPECIAL_KEYS:
        vk = _SPECIAL_KEYS[key]
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    else:
        raise ValueError(f"unsupported key '{key}'")
    if mods == 0:
        raise ValueError("a hotkey needs at least one modifier (ctrl/alt/shift/win)")
    return mods, vk


class GlobalHotkey:
    def __init__(self, spec: str, callback: Callable[[], None], hotkey_id: int = 0xA11E):
        self.spec = spec
        self.callback = callback
        self.hotkey_id = hotkey_id
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self.registered = False
        self.error = ""

    def start(self) -> bool:
        try:
            mods, vk = parse_hotkey(self.spec)
        except ValueError as exc:
            self.error = str(exc)
            return False
        ready = threading.Event()

        def _loop():
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            self._thread_id = kernel32.GetCurrentThreadId()
            if not user32.RegisterHotKey(None, self.hotkey_id, mods | _MOD_NOREPEAT, vk):
                self.error = "hotkey already in use by another application"
                ready.set()
                return
            self.registered = True
            ready.set()
            msg = wintypes.MSG()
            try:
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == _WM_HOTKEY and msg.wParam == self.hotkey_id:
                        try:
                            self.callback()
                        except Exception as exc:
                            print(f"[Hotkey] Callback error: {exc}")
            finally:
                user32.UnregisterHotKey(None, self.hotkey_id)
                self.registered = False

        self._thread = threading.Thread(target=_loop, name="GlobalHotkey", daemon=True)
        self._thread.start()
        ready.wait(timeout=2.0)
        return self.registered

    def stop(self):
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.0)
