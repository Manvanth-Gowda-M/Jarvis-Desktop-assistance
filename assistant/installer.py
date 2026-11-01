"""
assistant/installer.py
======================
Creates Desktop and Start Menu shortcuts that launch the Jarvis app without a
console window (pythonw), with a generated Jarvis icon.
"""

from __future__ import annotations

import os
from pathlib import Path

from config import BASE_DIR

ASSETS_DIR = BASE_DIR / "assets"
ICON_PATH = ASSETS_DIR / "jarvis.ico"


def build_icon() -> Path:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841 - needed for QPixmap
    from assistant.app import make_app_icon

    png = ASSETS_DIR / "jarvis.png"
    make_app_icon(256).pixmap(256, 256).save(str(png), "PNG")
    from PIL import Image

    with Image.open(png) as img:
        img.save(ICON_PATH, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ICON_PATH


def _shortcut_targets() -> list[Path]:
    desktop = Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))
    try:
        import win32com.client

        desktop = Path(win32com.client.Dispatch("WScript.Shell").SpecialFolders("Desktop"))
    except Exception:
        pass
    start_menu = Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"))
    return [desktop / "Jarvis.lnk", start_menu / "Jarvis.lnk"]


def install_shortcuts() -> list[str]:
    import sys
    import win32com.client

    from assistant.app import _pythonw

    icon = build_icon()
    shell = win32com.client.Dispatch("WScript.Shell")
    created = []
    for path in _shortcut_targets():
        path.parent.mkdir(parents=True, exist_ok=True)
        sc = shell.CreateShortCut(str(path))
        sc.TargetPath = _pythonw()
        sc.Arguments = f'"{BASE_DIR / "main.py"}" --app'
        sc.WorkingDirectory = str(BASE_DIR)
        sc.IconLocation = str(icon)
        sc.Description = "Jarvis personal AI assistant"
        sc.save()
        created.append(str(path))
    return created


def uninstall_shortcuts() -> list[str]:
    removed = []
    for path in _shortcut_targets():
        if path.exists():
            path.unlink()
            removed.append(str(path))
    return removed
