"""
assistant/app.py
================
Jarvis desktop application: a full window with a HUD dashboard, chat, live system
monitor, reminders, memory, activity log and settings, plus an always-on
"Hey Jarvis" wake word, global hotkey, and system-tray integration.

Launch with ``python main.py`` (or ``--app``). Closing the window keeps Jarvis
running in the tray so the wake word keeps working; use Quit from the tray menu
to exit.
"""

from __future__ import annotations

import html
import math
import os
import sys
import threading
import time
import winreg
from collections import deque
from datetime import datetime

import psutil
from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QStackedWidget, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout, QWidget,
)

import config
from config import BASE_DIR, JARVIS_NAME
from assistant.audio_coordinator import AssistantState, AudioCoordinator
from assistant.desktop_ui import (
    JarvisOrbWidget, UIBridge, _markdown_to_html, _time_greeting,
    acquire_single_instance_mutex, release_single_instance_mutex,
)

APP_TITLE = f"{JARVIS_NAME} — AI Assistant"
STARTUP_VALUE_NAME = "JarvisAssistantApp"
IPC_NAME = "JarvisAssistantAppIPC"


def _env_on(key: str, default) -> bool:
    return str(os.getenv(key, str(default))).strip().lower() in {"1", "true", "yes", "on"}

CYAN = "#00e5ff"
BG = "#060b16"
PANEL = "#0b1426"
PANEL_2 = "#0f1b33"
BORDER = "#16314f"
TEXT = "#d6e6ff"
MUTED = "#7b8fb0"

STATE_COLORS = {
    "standby": QColor(0, 229, 255),
    "listening": QColor(0, 255, 200),
    "thinking": QColor(64, 140, 255),
    "executing": QColor(150, 90, 255),
    "speaking": QColor(0, 200, 255),
    "paused": QColor(255, 179, 0),
    "error": QColor(255, 45, 85),
    "success": QColor(0, 230, 118),
}

EDGE_VOICES = [
    "en-GB-RyanNeural", "en-GB-ThomasNeural", "en-US-GuyNeural", "en-US-ChristopherNeural",
    "en-US-AndrewNeural", "en-US-BrianNeural", "en-AU-WilliamNeural", "en-IN-PrabhatNeural",
    "en-GB-SoniaNeural", "en-US-AriaNeural", "en-US-JennyNeural", "en-IN-NeerjaNeural",
]

STYLESHEET = f"""
QMainWindow, QWidget#root {{ background: {BG}; }}
QWidget {{ color: {TEXT}; font-family: 'Segoe UI'; font-size: 13px; }}
QFrame#sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
QPushButton#nav {{
    text-align: left; padding: 11px 18px; border: none; border-radius: 8px;
    color: {MUTED}; font-size: 14px; background: transparent;
}}
QPushButton#nav:hover {{ background: {PANEL_2}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: rgba(0,229,255,0.12); color: {CYAN}; font-weight: 600; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 14px; }}
QLabel#h1 {{ font-size: 22px; font-weight: 700; color: white; }}
QLabel#h2 {{ font-size: 15px; font-weight: 600; color: white; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#chip {{
    background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px 10px; color: {MUTED};
}}
QPushButton {{
    background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 14px; color: {TEXT};
}}
QPushButton:hover {{ border-color: {CYAN}; color: white; }}
QPushButton:disabled {{ color: #44506a; }}
QPushButton#primary {{ background: #0077ff; border: none; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: #1a8cff; }}
QPushButton#danger {{ background: rgba(255,45,85,0.14); border: 1px solid rgba(255,45,85,0.4); color: #ff6a85; }}
QPushButton#quick {{ border-radius: 14px; padding: 6px 12px; color: {CYAN}; background: rgba(0,229,255,0.06); }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: #050a14; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 10px; color: {TEXT};
    selection-background-color: #0077ff;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {CYAN}; }}
QTextBrowser, QListWidget, QTableWidget {{
    background: #050a14; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px;
}}
QHeaderView::section {{ background: {PANEL_2}; color: {MUTED}; border: none; padding: 6px; }}
QTableWidget {{ gridline-color: {BORDER}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 12px; margin-top: 14px; padding: 14px 12px 10px 12px; background: {PANEL}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; color: {CYAN}; font-weight: 600; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER}; border-radius: 4px; background: #050a14; }}
QCheckBox::indicator:checked {{ background: #0077ff; border-color: {CYAN}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


# ---------------------------------------------------------------------------
# Startup registration (launch at login, minimized to tray)
# ---------------------------------------------------------------------------

def _pythonw() -> str:
    exe = sys.executable
    candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return candidate if os.path.exists(candidate) else exe


def launch_command(minimized: bool = False) -> str:
    cmd = f'"{_pythonw()}" "{BASE_DIR / "main.py"}" --app'
    return cmd + (" --minimized" if minimized else "")


def set_app_startup(enable: bool) -> bool:
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
            if enable:
                winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, launch_command(minimized=True))
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:
        print(f"[App] Startup registry error: {exc}")
        return False


def is_app_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Icon
# ---------------------------------------------------------------------------

def make_app_icon(size: int = 256) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = size / 2
    grad = QRadialGradient(c, c, c)
    grad.setColorAt(0.0, QColor(255, 255, 255))
    grad.setColorAt(0.25, QColor(0, 229, 255))
    grad.setColorAt(0.7, QColor(0, 80, 200))
    grad.setColorAt(1.0, QColor(4, 10, 24))
    p.setBrush(QBrush(grad))
    p.setPen(QPen(QColor(0, 229, 255), size * 0.04))
    p.drawEllipse(QRectF(size * 0.06, size * 0.06, size * 0.88, size * 0.88))
    p.setPen(QPen(QColor(255, 255, 255, 200), size * 0.03))
    p.drawEllipse(QRectF(size * 0.3, size * 0.3, size * 0.4, size * 0.4))
    p.end()
    return QIcon(pix)


# ---------------------------------------------------------------------------
# Custom widgets
# ---------------------------------------------------------------------------

class ArcReactor(QWidget):
    """Animated HUD core that reflects the assistant state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "standby"
        self._t = 0.0
        self.setMinimumSize(260, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def set_state(self, state: str):
        self.state = state
        self.update()

    def _tick(self):
        self._t += 0.016
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = min(w, h) / 2 - 12
        cx, cy = w / 2, h / 2
        col = STATE_COLORS.get(self.state, STATE_COLORS["standby"])
        t = self._t
        active = self.state in ("listening", "thinking", "executing", "speaking")
        speed = {"thinking": 2.6, "executing": 3.2, "listening": 1.4, "speaking": 1.8}.get(self.state, 0.6)
        pulse = (math.sin(t * (6 if self.state == "speaking" else 2.2)) + 1) / 2

        def c(alpha):
            return QColor(col.red(), col.green(), col.blue(), int(alpha))

        # Ambient glow
        glow = QRadialGradient(cx, cy, r * 1.05)
        glow.setColorAt(0.0, c(70 + 50 * pulse))
        glow.setColorAt(0.55, c(25))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), r * 1.05, r * 1.05)

        # Outer tick ring
        p.save()
        p.translate(cx, cy)
        p.rotate(t * 12 * speed)
        for i in range(72):
            p.rotate(5)
            length = r * (0.06 if i % 6 == 0 else 0.03)
            p.setPen(QPen(c(200 if i % 6 == 0 else 90), 2))
            p.drawLine(QPointF(0, -r), QPointF(0, -r + length))
        p.restore()

        # Segmented arcs (counter-rotating)
        for k, (rad, width, segs, direction) in enumerate(
            [(0.84, 5, 3, 1), (0.72, 3, 5, -1), (0.62, 2, 8, 1)]
        ):
            rr = r * rad
            rect = QRectF(cx - rr, cy - rr, rr * 2, rr * 2)
            p.setPen(QPen(c(220 - k * 50), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            base = (t * 40 * speed * direction) % 360
            span = 360 / segs
            for s in range(segs):
                p.drawArc(rect, int((base + s * span) * 16), int(span * 0.55 * 16))

        # Listening / speaking ripples
        if self.state in ("listening", "speaking"):
            for i in range(3):
                phase = ((t * 0.9) + i / 3) % 1.0
                rr = r * (0.45 + 0.5 * phase)
                p.setPen(QPen(c(180 * (1 - phase)), 2))
                p.drawEllipse(QPointF(cx, cy), rr, rr)

        # Core
        core_r = r * (0.36 + (0.03 * pulse if active else 0.01 * pulse))
        core = QRadialGradient(cx - core_r * 0.2, cy - core_r * 0.25, core_r * 1.2)
        core.setColorAt(0.0, QColor(255, 255, 255, 250))
        core.setColorAt(0.25, c(240))
        core.setColorAt(0.75, QColor(4, 30, 70, 240))
        core.setColorAt(1.0, QColor(2, 8, 20, 255))
        p.setBrush(core)
        p.setPen(QPen(c(230), 2))
        p.drawEllipse(QPointF(cx, cy), core_r, core_r)

        # Triangle reactor motif
        p.save()
        p.translate(cx, cy)
        p.rotate(-t * 20 * speed)
        tri = QPainterPath()
        for i in range(3):
            a = math.radians(90 + i * 120)
            pt = QPointF(math.cos(a) * core_r * 0.62, -math.sin(a) * core_r * 0.62)
            tri.moveTo(pt) if i == 0 else tri.lineTo(pt)
        tri.closeSubpath()
        p.setPen(QPen(QColor(255, 255, 255, 140), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(tri)
        p.restore()

        # Label
        p.setPen(QColor(255, 255, 255, 235))
        f = QFont("Segoe UI", max(9, int(r * 0.075)), QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
        p.setFont(f)
        p.drawText(QRectF(cx - r, cy + r * 0.46, r * 2, r * 0.2), Qt.AlignmentFlag.AlignCenter, self.state.upper())
        p.end()


class Sparkline(QWidget):
    def __init__(self, color: str = CYAN, max_points: int = 60, max_value: float = 100.0, parent=None):
        super().__init__(parent)
        self.values = deque([0.0] * max_points, maxlen=max_points)
        self.color = QColor(color)
        self.max_value = max_value
        self._primed = False
        self.setMinimumHeight(54)

    def push(self, value: float):
        if not self._primed:
            self.values.extend([float(value)] * self.values.maxlen)
            self._primed = True
        self.values.append(float(value))
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        vals = list(self.values)
        top = max(self.max_value, max(vals) if vals else 1.0) or 1.0
        step = w / max(1, len(vals) - 1)
        path = QPainterPath()
        for i, v in enumerate(vals):
            pt = QPointF(i * step, h - 2 - (v / top) * (h - 6))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        fill = QPainterPath(path)
        fill.lineTo(w, h)
        fill.lineTo(0, h)
        fill.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, QColor(self.color.red(), self.color.green(), self.color.blue(), 90))
        grad.setColorAt(1, QColor(self.color.red(), self.color.green(), self.color.blue(), 0))
        p.fillPath(fill, grad)
        p.setPen(QPen(self.color, 2))
        p.drawPath(path)
        p.end()


class StatCard(QFrame):
    def __init__(self, title: str, color: str = CYAN, max_value: float = 100.0):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        self.title = QLabel(title)
        self.title.setObjectName("muted")
        self.value = QLabel("—")
        self.value.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {color};")
        self.sub = QLabel("")
        self.sub.setObjectName("muted")
        self.spark = Sparkline(color, max_value=max_value)
        lay.addWidget(self.title)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        lay.addWidget(self.spark)


def card(widget_or_layout=None) -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 16, 18, 16)
    if isinstance(widget_or_layout, QWidget):
        lay.addWidget(widget_or_layout)
    elif widget_or_layout is not None:
        lay.addLayout(widget_or_layout)
    return f


def page_header(title: str, subtitle: str) -> QVBoxLayout:
    lay = QVBoxLayout()
    t = QLabel(title)
    t.setObjectName("h1")
    s = QLabel(subtitle)
    s.setObjectName("muted")
    lay.addWidget(t)
    lay.addWidget(s)
    lay.addSpacing(8)
    return lay


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class AppBridge(UIBridge):
    heard = Signal(str)
    activity = Signal(str, str)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

class HomePage(QWidget):
    QUICK_ACTIONS = [
        ("☀ Weather", "What's the weather like today?"),
        ("📰 News", "Give me the top 3 news headlines."),
        ("🖥 My screen", "What's on my screen?"),
        ("⚙ System", "system status"),
        ("⏰ Reminders", "list reminders"),
        ("❔ Help", "help"),
    ]

    def __init__(self, window: "JarvisWindow"):
        super().__init__()
        self.win = window
        root = QHBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(18)

        # Left: HUD
        left = QVBoxLayout()
        self.reactor = ArcReactor()
        self.state_lbl = QLabel("Ready")
        self.state_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_lbl.setWordWrap(True)
        self.state_lbl.setStyleSheet(f"color: {TEXT}; font-size: 14px;")
        hint = QLabel(f'Say <b style="color:{CYAN}">"Hey Jarvis"</b> or press '
                      f'<b style="color:{CYAN}">{config.GLOBAL_HOTKEY.upper()}</b>')
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setObjectName("muted")

        btns = QHBoxLayout()
        self.talk_btn = QPushButton("🎙  Talk")
        self.talk_btn.setObjectName("primary")
        self.talk_btn.clicked.connect(lambda: self.win.coordinator.trigger_listening())
        self.mic_btn = QPushButton("⏸  Pause mic")
        self.mic_btn.clicked.connect(self.win.toggle_mic)
        stop_btn = QPushButton("⏹  Stop")
        stop_btn.setObjectName("danger")
        stop_btn.clicked.connect(self.win.coordinator.stop_speaking)
        for b in (self.talk_btn, self.mic_btn, stop_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            btns.addWidget(b)

        chips = QGridLayout()
        self.wake_chip = QLabel()
        self.ai_chip = QLabel()
        self.voice_chip = QLabel()
        for i, chip in enumerate((self.wake_chip, self.ai_chip, self.voice_chip)):
            chip.setObjectName("chip")
            chip.setWordWrap(True)
            chips.addWidget(chip, i, 0)

        left.addWidget(self.reactor, 1)
        left.addWidget(self.state_lbl)
        left.addWidget(hint)
        left.addSpacing(6)
        left.addLayout(btns)
        left.addSpacing(6)
        left.addLayout(chips)
        left_card = card(left)
        left_card.setMinimumWidth(340)
        left_card.setMaximumWidth(420)

        # Right: chat
        right = QVBoxLayout()
        head = QHBoxLayout()
        title = QLabel("Conversation")
        title.setObjectName("h2")
        clear_btn = QPushButton("New conversation")
        clear_btn.clicked.connect(self.win.new_conversation)
        head.addWidget(title)
        head.addStretch()
        head.addWidget(clear_btn)

        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(True)
        self.chat.document().setDefaultStyleSheet(
            f"a {{ color: {CYAN}; }} code {{ background:#0f1b33; }} p {{ margin: 2px 0; }}"
        )

        quick = QHBoxLayout()
        for label, cmd in self.QUICK_ACTIONS:
            b = QPushButton(label)
            b.setObjectName("quick")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=cmd: self.win.send_command(c))
            quick.addWidget(b)
        quick.addStretch()

        inp = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask Jarvis anything, or give a command…")
        self.input.returnPressed.connect(self._send)
        send = QPushButton("Send")
        send.setObjectName("primary")
        send.clicked.connect(self._send)
        inp.addWidget(self.input, 1)
        inp.addWidget(send)

        right.addLayout(head)
        right.addWidget(self.chat, 1)
        right.addLayout(quick)
        right.addLayout(inp)

        root.addWidget(left_card)
        root.addWidget(card(right), 1)

    def start(self):
        pass

    def stop(self):
        pass

    def _send(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.win.send_command(text)

    def add_user(self, text: str, voice: bool = False):
        icon = "🎙 " if voice else ""
        ts = datetime.now().strftime("%H:%M")
        self.chat.append(
            f"<div style='margin-top:10px'><span style='color:#8fd8ff;font-weight:600'>{icon}You</span>"
            f" <span style='color:{MUTED};font-size:11px'>{ts}</span></div>"
            f"<div>{html.escape(text)}</div>"
        )
        self._scroll()

    def add_jarvis(self, text: str):
        ts = datetime.now().strftime("%H:%M")
        self.chat.append(
            f"<div style='margin-top:10px'><span style='color:{CYAN};font-weight:700'>{html.escape(JARVIS_NAME)}</span>"
            f" <span style='color:{MUTED};font-size:11px'>{ts}</span></div>"
        )
        self.chat.append(_markdown_to_html(text))
        self._scroll()

    def _scroll(self):
        sb = self.chat.verticalScrollBar()
        sb.setValue(sb.maximum())


class SystemPage(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.addLayout(page_header("System Monitor", "Live performance of this PC"))

        grid = QGridLayout()
        grid.setSpacing(14)
        self.cpu = StatCard("CPU", "#00e5ff")
        self.ram = StatCard("Memory", "#7c5cff")
        self.disk = StatCard("Disk (system drive)", "#ffb300")
        self.net = StatCard("Network", "#00e676", max_value=1.0)
        self.bat = StatCard("Battery", "#ff5d8f")
        self.up = StatCard("Uptime", "#58a6ff")
        for i, w in enumerate((self.cpu, self.ram, self.disk, self.net, self.bat, self.up)):
            grid.addWidget(w, i // 3, i % 3)
        root.addLayout(grid)

        title = QLabel("Top processes")
        title.setObjectName("h2")
        root.addSpacing(8)
        root.addWidget(title)
        self.procs = QTableWidget(0, 3)
        self.procs.setHorizontalHeaderLabels(["Process", "CPU %", "Memory %"])
        self.procs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.procs.verticalHeader().setVisible(False)
        self.procs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.procs.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        root.addWidget(self.procs, 1)

        self._last_net = psutil.net_io_counters()
        self._last_t = time.time()
        self._sys_drive = os.getenv("SystemDrive", "C:") + "\\"
        psutil.cpu_percent(None)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._proc_tick = 0

    def start(self):
        self.refresh()
        self._timer.start(1000)

    def stop(self):
        self._timer.stop()

    def refresh(self):
        try:
            cpu = psutil.cpu_percent(None)
            self.cpu.value.setText(f"{cpu:.0f}%")
            self.cpu.sub.setText(f"{psutil.cpu_count(logical=True)} logical cores")
            self.cpu.spark.push(cpu)

            vm = psutil.virtual_memory()
            self.ram.value.setText(f"{vm.percent:.0f}%")
            self.ram.sub.setText(f"{vm.used / 2**30:.1f} / {vm.total / 2**30:.1f} GB")
            self.ram.spark.push(vm.percent)

            du = psutil.disk_usage(self._sys_drive)
            self.disk.value.setText(f"{du.percent:.0f}%")
            self.disk.sub.setText(f"{du.free / 2**30:.0f} GB free of {du.total / 2**30:.0f} GB")
            self.disk.spark.push(du.percent)

            now = time.time()
            n = psutil.net_io_counters()
            dt = max(0.1, now - self._last_t)
            down = (n.bytes_recv - self._last_net.bytes_recv) / dt / 2**20
            upv = (n.bytes_sent - self._last_net.bytes_sent) / dt / 2**20
            self._last_net, self._last_t = n, now
            self.net.value.setText(f"↓ {down:.2f} MB/s")
            self.net.sub.setText(f"↑ {upv:.2f} MB/s")
            self.net.spark.push(down)

            bat = psutil.sensors_battery()
            if bat:
                self.bat.value.setText(f"{bat.percent:.0f}%")
                self.bat.sub.setText("Plugged in" if bat.power_plugged else "On battery")
                self.bat.spark.push(bat.percent)
            else:
                self.bat.value.setText("AC")
                self.bat.sub.setText("No battery detected")

            up = int(now - psutil.boot_time())
            self.up.value.setText(f"{up // 3600}h {(up % 3600) // 60}m")
            self.up.sub.setText(f"Since {datetime.fromtimestamp(psutil.boot_time()).strftime('%d %b %H:%M')}")
            self.up.spark.push((up % 3600) / 36)

            self._proc_tick += 1
            if self._proc_tick % 3 == 1:
                self._refresh_procs()
        except Exception as exc:
            print(f"[App] System refresh error: {exc}")

    def _refresh_procs(self):
        # Reuse Process objects so cpu_percent() measures usage since the previous refresh.
        cache = getattr(self, "_proc_cache", {})
        seen = {}
        procs = []
        for pr in psutil.process_iter(["name"]):
            proc = cache.get(pr.pid, pr)
            seen[pr.pid] = proc
            try:
                name = proc.info.get("name") if hasattr(proc, "info") else proc.name()
                if not name or name in ("System Idle Process", "Idle"):
                    continue
                procs.append({"name": name, "cpu_percent": proc.cpu_percent(None),
                              "memory_percent": proc.memory_percent()})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        self._proc_cache = seen
        procs.sort(key=lambda d: (d.get("cpu_percent") or 0, d.get("memory_percent") or 0), reverse=True)
        top = procs[:8]
        self.procs.setRowCount(len(top))
        for r, info in enumerate(top):
            self.procs.setItem(r, 0, QTableWidgetItem(str(info.get("name"))))
            self.procs.setItem(r, 1, QTableWidgetItem(f"{(info.get('cpu_percent') or 0) / psutil.cpu_count():.1f}"))
            self.procs.setItem(r, 2, QTableWidgetItem(f"{info.get('memory_percent') or 0:.1f}"))


class RemindersPage(QWidget):
    def __init__(self):
        super().__init__()
        from assistant import reminders
        self.rem = reminders
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.addLayout(page_header("Reminders & Timers", "Jarvis will speak and show a notification when they're due"))

        form = QHBoxLayout()
        self.msg = QLineEdit()
        self.msg.setPlaceholderText("Remind me to…")
        self.mins = QSpinBox()
        self.mins.setRange(1, 60 * 24 * 7)
        self.mins.setValue(10)
        self.mins.setSuffix(" min")
        self.at = QLineEdit()
        self.at.setPlaceholderText("or at time, e.g. 18:30 / 7 pm")
        add = QPushButton("Add reminder")
        add.setObjectName("primary")
        add.clicked.connect(self._add)
        form.addWidget(self.msg, 2)
        form.addWidget(QLabel("in"))
        form.addWidget(self.mins)
        form.addWidget(self.at, 1)
        form.addWidget(add)
        root.addWidget(card(form))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "Due", "Reminder"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        cancel = QPushButton("Cancel selected")
        cancel.setObjectName("danger")
        cancel.clicked.connect(self._cancel)
        row.addStretch()
        row.addWidget(cancel)
        root.addLayout(row)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    def start(self):
        self.refresh()
        self._timer.start(5000)

    def stop(self):
        self._timer.stop()

    def refresh(self):
        items = self.rem.get_pending_reminders()
        self.table.setRowCount(len(items))
        for r, it in enumerate(items):
            self.table.setItem(r, 0, QTableWidgetItem(str(it["id"])))
            self.table.setItem(r, 1, QTableWidgetItem(it["due"].strftime("%a %d %b, %I:%M %p")))
            self.table.setItem(r, 2, QTableWidgetItem(it["message"]))

    def _add(self):
        msg = self.msg.text().strip() or "Reminder"
        at = self.at.text().strip()
        res = self.rem.set_reminder(msg, at_time=at) if at else self.rem.set_reminder(msg, minutes=self.mins.value())
        if not res.startswith("Reminder #"):
            QMessageBox.warning(self, "Reminder", res)
        self.msg.clear()
        self.at.clear()
        self.refresh()

    def _cancel(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        for r in rows:
            item = self.table.item(r, 0)
            if item:
                self.rem.cancel_reminder(int(item.text()))
        self.refresh()


class MemoryPage(QWidget):
    def __init__(self):
        super().__init__()
        from assistant import memory
        self.mem = memory
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.addLayout(page_header("Memory", "Long-term facts Jarvis uses to personalise answers"))

        form = QHBoxLayout()
        self.fact = QLineEdit()
        self.fact.setPlaceholderText("e.g. My birthday is on 14 March")
        self.fact.returnPressed.connect(self._add)
        add = QPushButton("Remember")
        add.setObjectName("primary")
        add.clicked.connect(self._add)
        form.addWidget(self.fact, 1)
        form.addWidget(add)
        root.addWidget(card(form))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "Saved", "Memory"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        delete = QPushButton("Forget selected")
        delete.setObjectName("danger")
        delete.clicked.connect(self._delete)
        row.addStretch()
        row.addWidget(delete)
        root.addLayout(row)

    def start(self):
        self.refresh()

    def stop(self):
        pass

    def refresh(self):
        rows = self.mem.get_memories()
        self.table.setRowCount(len(rows))
        for r, (mid, content, created) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(str(mid)))
            self.table.setItem(r, 1, QTableWidgetItem(created))
            self.table.setItem(r, 2, QTableWidgetItem(content))

    def _add(self):
        fact = self.fact.text().strip()
        if not fact:
            return
        res = self.mem.remember_fact(fact)
        if not res.startswith("Saved"):
            QMessageBox.information(self, "Memory", res)
        self.fact.clear()
        self.refresh()

    def _delete(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            return
        if QMessageBox.question(self, "Forget", f"Delete {len(rows)} memory item(s)?") != QMessageBox.StandardButton.Yes:
            return
        for r in rows:
            item = self.table.item(r, 0)
            if item:
                self.mem.delete_memory(int(item.text()))
        self.refresh()


class ActivityPage(QWidget):
    ICONS = {"tool": "🛠", "provider": "🧠", "error": "⚠", "heard": "🎙", "state": "•", "reminder": "⏰", "info": "ℹ"}

    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.addLayout(page_header("Activity", "What Jarvis heard, which AI model answered, and every tool it used"))
        self.list = QListWidget()
        root.addWidget(self.list, 1)
        row = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.list.clear)
        row.addStretch()
        row.addWidget(clear)
        root.addLayout(row)

    def start(self):
        pass

    def stop(self):
        pass

    def add(self, kind: str, detail: str):
        ts = datetime.now().strftime("%H:%M:%S")
        item = QListWidgetItem(f"{ts}   {self.ICONS.get(kind, '•')}  {detail}")
        if kind == "error":
            item.setForeground(QColor("#ff6a85"))
        elif kind == "tool":
            item.setForeground(QColor(CYAN))
        elif kind == "provider":
            item.setForeground(QColor("#9d8bff"))
        self.list.insertItem(0, item)
        while self.list.count() > 500:
            self.list.takeItem(self.list.count() - 1)


class SettingsPage(QWidget):
    def __init__(self, window: "JarvisWindow"):
        super().__init__()
        from assistant import settings_store
        self.store = settings_store
        self.win = window

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.addLayout(page_header("Settings", "Saved to your .env file. Keys stay on this PC."))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("settingsBody")
        body.setStyleSheet(f"QWidget#settingsBody {{ background: {BG}; }}")
        scroll.viewport().setStyleSheet(f"background: {BG};")
        lay = QVBoxLayout(body)
        lay.setSpacing(6)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        env = lambda k, d="": settings_store.get_setting(k, d)  # noqa: E731
        self.fields: dict[str, QWidget] = {}

        # AI providers
        g = QGroupBox("Free AI providers")
        f = QFormLayout(g)
        for key, label, link in (
            ("GROQ_API_KEY", "Groq key", "https://console.groq.com/keys"),
            ("NVIDIA_API_KEY", "NVIDIA NIM key", "https://build.nvidia.com"),
            ("OPENROUTER_API_KEY", "OpenRouter key", "https://openrouter.ai/keys"),
        ):
            row = QHBoxLayout()
            le = QLineEdit(env(key))
            le.setEchoMode(QLineEdit.EchoMode.Password)
            le.setPlaceholderText("not set")
            show = QPushButton("Show")
            show.setFixedWidth(64)
            show.setCheckable(True)
            show.toggled.connect(lambda on, w=le: w.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
            get = QLabel(f"<a style='color:{CYAN}' href='{link}'>get free key</a>")
            get.setOpenExternalLinks(True)
            row.addWidget(le, 1)
            row.addWidget(show)
            row.addWidget(get)
            f.addRow(label, row)
            self.fields[key] = le
        order = QLineEdit(env("LLM_PROVIDER_ORDER", config.LLM_PROVIDER_ORDER))
        f.addRow("Failover order", order)
        self.fields["LLM_PROVIDER_ORDER"] = order
        lay.addWidget(g)

        # Personality
        g = QGroupBox("Personality")
        f = QFormLayout(g)
        for key, label, default in (
            ("USER_NAME", "Your name", ""),
            ("USER_TITLE", "Jarvis calls you", "sir"),
            ("DEFAULT_LOCATION", "Your city (weather)", ""),
        ):
            le = QLineEdit(env(key, default))
            f.addRow(label, le)
            self.fields[key] = le
        lay.addWidget(g)

        # Voice
        g = QGroupBox("Voice")
        f = QFormLayout(g)
        tts = QComboBox()
        tts.addItems(["edge", "sapi"])
        tts.setCurrentText(env("TTS_ENGINE", config.TTS_ENGINE))
        f.addRow("Speech engine", tts)
        self.fields["TTS_ENGINE"] = tts
        voice = QComboBox()
        voice.setEditable(True)
        voice.addItems(EDGE_VOICES)
        voice.setCurrentText(env("EDGE_TTS_VOICE", config.EDGE_TTS_VOICE))
        vrow = QHBoxLayout()
        vrow.addWidget(voice, 1)
        test = QPushButton("▶ Test voice")
        test.clicked.connect(self._test_voice)
        vrow.addWidget(test)
        f.addRow("Neural voice", vrow)
        self.fields["EDGE_TTS_VOICE"] = voice
        rate = QLineEdit(env("EDGE_TTS_RATE", config.EDGE_TTS_RATE))
        f.addRow("Speaking rate", rate)
        self.fields["EDGE_TTS_RATE"] = rate
        stt = QComboBox()
        stt.addItems(["auto", "groq", "google"])
        stt.setCurrentText(env("STT_ENGINE", config.STT_ENGINE))
        f.addRow("Speech recognition", stt)
        self.fields["STT_ENGINE"] = stt
        follow = QCheckBox("Keep listening for a follow-up after each answer")
        follow.setChecked(env("FOLLOW_UP_ENABLED", "true").lower() in {"1", "true", "yes", "on"})
        f.addRow("", follow)
        self.fields["FOLLOW_UP_ENABLED"] = follow
        lay.addWidget(g)

        # Wake word & system
        g = QGroupBox("Wake word & system")
        f = QFormLayout(g)
        thr = QDoubleSpinBox()
        thr.setRange(0.05, 0.95)
        thr.setSingleStep(0.05)
        thr.setDecimals(2)
        thr.setValue(float(env("WAKE_WORD_THRESHOLD", str(config.WAKE_WORD_THRESHOLD)) or 0.25))
        f.addRow("Wake sensitivity (lower = more sensitive)", thr)
        self.fields["WAKE_WORD_THRESHOLD"] = thr
        hk = QLineEdit(env("GLOBAL_HOTKEY", config.GLOBAL_HOTKEY))
        f.addRow("Push-to-talk hotkey", hk)
        self.fields["GLOBAL_HOTKEY"] = hk
        greet = QCheckBox("Greet me when Jarvis starts")
        greet.setChecked(env("DESKTOP_GREETING", "true").lower() in {"1", "true", "yes", "on"})
        f.addRow("", greet)
        self.fields["DESKTOP_GREETING"] = greet
        chime = QCheckBox("Play a chime when Jarvis starts listening")
        chime.setChecked(env("WAKE_CHIME", "true").lower() in {"1", "true", "yes", "on"})
        f.addRow("", chime)
        self.fields["WAKE_CHIME"] = chime
        pill = QCheckBox("Keep a small Dynamic Island pill visible at the top of the screen when idle")
        pill.setChecked(env("ISLAND_IDLE_VISIBLE", "false").lower() in {"1", "true", "yes", "on"})
        f.addRow("", pill)
        self.fields["ISLAND_IDLE_VISIBLE"] = pill
        dash = QCheckBox("Open this dashboard when Jarvis starts (otherwise only the island appears)")
        dash.setChecked(env("OPEN_DASHBOARD_ON_START", "false").lower() in {"1", "true", "yes", "on"})
        f.addRow("", dash)
        self.fields["OPEN_DASHBOARD_ON_START"] = dash
        self.startup = QCheckBox("Launch Jarvis when I sign in to Windows (starts in the tray)")
        self.startup.setChecked(is_app_startup_enabled())
        f.addRow("", self.startup)
        lay.addWidget(g)
        lay.addStretch()

        row = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("muted")
        save = QPushButton("Save settings")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        row.addWidget(self.status, 1)
        row.addWidget(save)
        outer.addLayout(row)

    def start(self):
        pass

    def stop(self):
        pass

    def _values(self) -> dict[str, str]:
        out = {}
        for key, w in self.fields.items():
            if isinstance(w, QLineEdit):
                out[key] = w.text().strip()
            elif isinstance(w, QComboBox):
                out[key] = w.currentText().strip()
            elif isinstance(w, QCheckBox):
                out[key] = "true" if w.isChecked() else "false"
            elif isinstance(w, QDoubleSpinBox):
                out[key] = f"{w.value():.2f}"
        return out

    def _apply_live(self, values: dict[str, str]):
        sp = self.win.coordinator.speaker
        sp.engine = values.get("TTS_ENGINE", sp.engine)
        sp.edge_voice = values.get("EDGE_TTS_VOICE", sp.edge_voice)
        sp._edge_failures = 0
        self.win.coordinator.follow_up_enabled = values.get("FOLLOW_UP_ENABLED") == "true"
        self.win.coordinator.wake_chime = values.get("WAKE_CHIME", "true") == "true"
        self.win.coordinator.listener._groq_failures = 0
        island = getattr(self.win, "island", None)
        if island is not None and "ISLAND_IDLE_VISIBLE" in values:
            island.idle_visible = values["ISLAND_IDLE_VISIBLE"] == "true"
            if island.idle_visible:
                island._show_animated()
                island._relayout()
            elif island.mode == "idle":
                island.collapse()

    def _save(self):
        values = self._values()
        try:
            self.store.update_env(values)
        except Exception as exc:
            QMessageBox.critical(self, "Settings", f"Couldn't save settings: {exc}")
            return
        self._apply_live(values)
        set_app_startup(self.startup.isChecked())
        self.win.refresh_status_chips()
        self.status.setText("Saved. Wake sensitivity and hotkey changes apply after restarting Jarvis.")

    def _test_voice(self):
        values = self._values()
        os.environ["EDGE_TTS_RATE"] = values.get("EDGE_TTS_RATE", "+8%")
        self._apply_live(values)
        title = values.get("USER_TITLE") or "sir"
        self.win.coordinator.announce(f"Hello {title}. This is how I sound with the current voice settings.")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class _OrbPanelAdapter:
    """Lets the floating orb open the main window instead of the old mini panel."""

    def __init__(self, window: "JarvisWindow"):
        self.window = window

    def isVisible(self):
        return False

    def reposition_near_orb(self):
        pass

    def toggle_visibility(self):
        self.window.show_window()


class JarvisWindow(QMainWindow):
    NAV = [("home", "⌂  Home"), ("system", "▤  System"), ("reminders", "⏰  Reminders"),
           ("memory", "◈  Memory"), ("activity", "≋  Activity"), ("settings", "⚙  Settings")]

    def __init__(self, coordinator: AudioCoordinator, bridge: AppBridge):
        super().__init__()
        self.coordinator = coordinator
        self.bridge = bridge
        self._quitting = False
        self._tray_hint_shown = False
        self.island = None
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(make_app_icon())
        self.resize(1240, 780)
        self.setMinimumSize(980, 640)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # Sidebar
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(210)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(14, 20, 14, 16)
        brand = QLabel(f"<span style='color:{CYAN};font-size:22px;font-weight:800;letter-spacing:4px'>"
                       f"{html.escape(JARVIS_NAME.upper())}</span>")
        tag = QLabel("Personal AI assistant")
        tag.setObjectName("muted")
        sv.addWidget(brand)
        sv.addWidget(tag)
        sv.addSpacing(18)

        self.stack = QStackedWidget()
        self.pages = {
            "home": HomePage(self),
            "system": SystemPage(),
            "reminders": RemindersPage(),
            "memory": MemoryPage(),
            "activity": ActivityPage(),
            "settings": SettingsPage(self),
        }
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, label in self.NAV:
            b = QPushButton(label)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self.nav_group.addButton(b)
            self.nav_buttons[key] = b
            sv.addWidget(b)
            self.stack.addWidget(self.pages[key])
            if key == "home":
                b.setChecked(True)
        sv.addStretch()
        self.side_state = QLabel("● Standby")
        self.side_state.setStyleSheet(f"color: {CYAN}; font-weight: 600;")
        self.side_clock = QLabel()
        self.side_clock.setObjectName("muted")
        sv.addWidget(self.side_state)
        sv.addWidget(self.side_clock)

        h.addWidget(side)
        h.addWidget(self.stack, 1)
        self._current_page = "home"

        self._clock = QTimer(self)
        self._clock.timeout.connect(self._update_clock)
        self._clock.start(1000)
        self._update_clock()

        # Signals
        bridge.state_changed.connect(self._on_state)
        bridge.result_received.connect(lambda d, s: self.pages["home"].add_jarvis(d))
        bridge.heard.connect(self._on_heard)
        bridge.activity.connect(self.pages["activity"].add)

        self._build_tray()
        self.refresh_status_chips()

    # ---------------------------------------------------------------- tray
    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_app_icon(), self)
        self.tray.setToolTip(APP_TITLE)
        menu = QMenu()
        menu.addAction("Open Jarvis", self.show_window)
        menu.addAction("🎙 Talk", lambda: self.coordinator.trigger_listening())
        self.tray_pause = menu.addAction("⏸ Pause listening", self.toggle_mic)
        menu.addAction("⏹ Stop speaking", self.coordinator.stop_speaking)
        menu.addSeparator()
        self.orb_action = menu.addAction("Floating orb", self.toggle_orb)
        self.orb_action.setCheckable(True)
        menu.addSeparator()
        menu.addAction("Quit Jarvis", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()
        self.bridge.notification.connect(
            lambda t, m: self.tray.showMessage(t, m, QSystemTrayIcon.MessageIcon.Information, 8000)
        )
        self.orb = None

    def _on_tray(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.show_window()

    def toggle_orb(self):
        if self.orb is None:
            self.orb = JarvisOrbWidget(coordinator=self.coordinator, bridge=self.bridge)
            self.orb.control_panel = _OrbPanelAdapter(self)
            screen = QApplication.primaryScreen().availableGeometry()
            self.orb.move(screen.right() - self.orb.width() - 30, screen.bottom() - self.orb.height() - 40)
        if self.orb.isVisible():
            self.orb.hide()
            self.orb_action.setChecked(False)
        else:
            self.orb.show()
            self.orb_action.setChecked(True)

    # ---------------------------------------------------------------- navigation
    def show_page(self, key: str):
        if key == self._current_page:
            return
        self.nav_buttons[key].setChecked(True)
        self.pages[self._current_page].stop()
        self._current_page = key
        self.stack.setCurrentWidget(self.pages[key])
        self.pages[key].start()

    def show_window(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    # ---------------------------------------------------------------- actions
    def send_command(self, text: str):
        self.pages["home"].add_user(text)
        self.bridge.activity.emit("info", f"Typed: {text}")
        self.coordinator.process_text_command(text)

    def new_conversation(self):
        try:
            from assistant.agent import reset_conversation
            reset_conversation()
        except Exception:
            pass
        self.pages["home"].chat.clear()
        self.pages["home"].add_jarvis("Context cleared. What's next?")

    def toggle_mic(self):
        self.coordinator.toggle_pause()
        paused = self.coordinator.is_paused
        self.pages["home"].mic_btn.setText("▶  Resume mic" if paused else "⏸  Pause mic")
        self.tray_pause.setText("▶ Resume listening" if paused else "⏸ Pause listening")
        self.refresh_status_chips()

    def refresh_status_chips(self):
        from assistant.llm_providers import configured_provider_names
        home = self.pages["home"]
        wd = self.coordinator.wake_detector
        if self.coordinator.is_paused:
            home.wake_chip.setText("🎙 Wake word: paused")
        elif wd.is_available:
            home.wake_chip.setText("🎙 Wake word: listening for “Hey Jarvis”")
        else:
            home.wake_chip.setText(f"🎙 Wake word unavailable: {wd.status_message}")
        providers = configured_provider_names()
        home.ai_chip.setText("🧠 AI: " + (" → ".join(providers) if providers else "no API key — open Settings"))
        sp = self.coordinator.speaker
        voice = f"Neural ({sp.edge_voice})" if sp.uses_edge else "Windows voice"
        stt = "Groq Whisper" if self.coordinator.listener._use_groq() else "Google"
        home.voice_chip.setText(f"🔊 Voice: {voice} · Hearing: {stt}")

    # ---------------------------------------------------------------- events
    def _update_clock(self):
        self.side_clock.setText(datetime.now().strftime("%A %d %b · %I:%M:%S %p"))

    def _on_state(self, state: str, detail: str):
        home = self.pages["home"]
        home.reactor.set_state(state)
        col = STATE_COLORS.get(state, STATE_COLORS["standby"]).name()
        self.side_state.setText(f"● {state.capitalize()}")
        self.side_state.setStyleSheet(f"color: {col}; font-weight: 600;")
        if state == "speaking":
            home.state_lbl.setText("Speaking…")
        elif detail:
            home.state_lbl.setText(detail if len(detail) < 140 else detail[:137] + "…")
        if state in ("listening", "error"):
            self.bridge.activity.emit("state", f"{state.capitalize()}: {detail[:100]}")

    def _on_heard(self, text: str):
        self.pages["home"].add_user(text, voice=True)
        self.bridge.activity.emit("heard", f"Heard: {text}")

    def closeEvent(self, event):
        if self._quitting:
            event.accept()
            return
        event.ignore()
        self.hide()
        if not self._tray_hint_shown:
            self.tray.showMessage(
                JARVIS_NAME, "Still running in the tray. Say “Hey Jarvis” any time. Right-click the tray icon to quit.",
                QSystemTrayIcon.MessageIcon.Information, 5000,
            )
            self._tray_hint_shown = True

    def quit_app(self):
        self._quitting = True
        self.tray.hide()
        if self.orb:
            self.orb.close()
        if self.island:
            self.island.close()
        self.close()
        QApplication.quit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _signal_running_instance() -> bool:
    """Ask an already-running Jarvis to show its dashboard. Returns True if one answered."""
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(IPC_NAME)
    if not sock.waitForConnected(800):
        return False
    sock.write(b"show")
    sock.flush()
    sock.waitForBytesWritten(800)
    sock.disconnectFromServer()
    return True


def run_app(start_minimized: bool = False, check_single_instance: bool = True, force_dashboard: bool = False) -> int:
    if check_single_instance and not acquire_single_instance_mutex("JarvisAssistantAppSingleInstance"):
        app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841 - needed for QLocalSocket
        if _signal_running_instance():
            print("[App] Jarvis is already running; opened its dashboard.")
        else:
            print("[App] Jarvis is already running (check the system tray).")
        return 0

    try:
        try:
            ctypes = __import__("ctypes")
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Jarvis.Assistant.App")
        except Exception:
            pass

        app = QApplication.instance() or QApplication(sys.argv)
        app.setApplicationName(JARVIS_NAME)
        app.setQuitOnLastWindowClosed(False)
        app.setWindowIcon(make_app_icon())
        app.setStyleSheet(STYLESHEET)

        bridge = AppBridge()
        coordinator = AudioCoordinator(
            on_state_change=lambda st, d: bridge.state_changed.emit(st.value, d),
            on_result=lambda detailed, spoken: bridge.result_received.emit(detailed, spoken),
            on_heard=lambda text: bridge.heard.emit(text),
        )

        from assistant.agent import add_activity_listener
        add_activity_listener(lambda kind, detail: bridge.activity.emit(kind, detail))

        window = JarvisWindow(coordinator, bridge)

        # Dynamic Island overlay
        island = None
        if config.ISLAND_ENABLED:
            from assistant.island import DynamicIsland

            island = DynamicIsland(
                on_open_dashboard=window.show_window,
                on_toggle_mic=window.toggle_mic,
                on_quit=window.quit_app,
                idle_visible=_env_on("ISLAND_IDLE_VISIBLE", config.ISLAND_IDLE_VISIBLE),
            )
            window.island = island

            def _island_wanted() -> bool:
                # When you're working in the dashboard itself, the island stays out of the way.
                return not (window.isVisible() and window.isActiveWindow())

            bridge.state_changed.connect(lambda s, d: _island_wanted() and island.on_state(s, d))
            bridge.heard.connect(lambda t: island.on_heard(t))
            bridge.result_received.connect(lambda d, s: _island_wanted() and island.on_result(d, s))
            bridge.activity.connect(lambda k, d: _island_wanted() and island.on_activity(k, d))
            bridge.notification.connect(lambda t, m: island.notify(t, m))

        # Second launches (e.g. double-clicking the shortcut again) open the dashboard.
        from PySide6.QtNetwork import QLocalServer

        QLocalServer.removeServer(IPC_NAME)
        ipc = QLocalServer(window)

        def _on_ipc():
            conn = ipc.nextPendingConnection()
            if conn:
                conn.readyRead.connect(lambda c=conn: (c.readAll(), window.show_window()))

        ipc.newConnection.connect(_on_ipc)
        ipc.listen(IPC_NAME)

        # Reminders -> tray balloon + speech
        from assistant import reminders

        def _on_reminder(text: str):
            bridge.notification.emit(f"{JARVIS_NAME} reminder", text)
            bridge.activity.emit("reminder", text)
            coordinator.announce(text)

        reminders.register_notifier(_on_reminder)
        reminders.start_scheduler()

        # Global hotkey
        hotkey = None
        try:
            from assistant.hotkey import GlobalHotkey
            hotkey = GlobalHotkey(os.getenv("GLOBAL_HOTKEY", config.GLOBAL_HOTKEY), lambda: coordinator.trigger_listening())
            if hotkey.start():
                bridge.activity.emit("info", f"Push-to-talk hotkey active: {hotkey.spec.upper()}")
            else:
                bridge.activity.emit("error", f"Hotkey '{hotkey.spec}' unavailable: {hotkey.error}")
        except Exception as exc:
            bridge.activity.emit("error", f"Hotkey setup failed: {exc}")

        # Wake word
        if coordinator.wake_detector.is_available:
            coordinator._resume_wake_detector_if_ready()
            bridge.activity.emit("info", "Wake word active: say “Hey Jarvis”")
        else:
            bridge.activity.emit("error", f"Wake word unavailable: {coordinator.wake_detector.status_message}")

        from assistant.llm_providers import configured_provider_names
        has_ai = bool(configured_provider_names())
        show_dashboard = force_dashboard or (not start_minimized and (
            not island or not has_ai or _env_on("OPEN_DASHBOARD_ON_START", config.OPEN_DASHBOARD_ON_START)
        ))
        if show_dashboard:
            window.show()
        else:
            window.tray.showMessage(
                JARVIS_NAME,
                f"Jarvis is ready. Say “Hey Jarvis” or press {os.getenv('GLOBAL_HOTKEY', config.GLOBAL_HOTKEY).upper()}. "
                "Click the tray icon for the dashboard.",
                QSystemTrayIcon.MessageIcon.Information, 4000,
            )

        if not has_ai:
            window.pages["home"].add_jarvis(
                "**No AI provider key is set yet.** Open **Settings** and paste a free key from Groq, "
                "NVIDIA NIM, or OpenRouter. Local commands (volume, timers, weather, apps) already work."
            )
        elif _env_on("DESKTOP_GREETING", True):
            QTimer.singleShot(900, lambda: coordinator.announce(_time_greeting()))

        exit_code = app.exec()

        reminders.unregister_notifier(_on_reminder)
        if hotkey:
            hotkey.stop()
        coordinator.shutdown()
        return exit_code
    finally:
        if check_single_instance:
            release_single_instance_mutex()
