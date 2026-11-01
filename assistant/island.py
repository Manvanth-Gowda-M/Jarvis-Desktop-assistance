"""
assistant/island.py
===================
"Dynamic Island" overlay for Jarvis: a black pill at the top-centre of the screen
that drops down when you say "Hey Jarvis", shows what Jarvis heard, each step it
takes (opening apps, searching, looking at the screen...), the answer while it
speaks, then shrinks away.

It never takes keyboard focus, so tools like ``type_text`` still type into the
window you were using. Click it to open the full dashboard; right-click for options.
"""

from __future__ import annotations

import math
import re
import time

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QMenu, QWidget

STATE_COLORS = {
    "idle": QColor(0, 229, 255),
    "listening": QColor(0, 255, 200),
    "thinking": QColor(70, 150, 255),
    "speaking": QColor(0, 210, 255),
    "result": QColor(0, 230, 118),
    "error": QColor(255, 60, 95),
    "paused": QColor(255, 179, 0),
    "notify": QColor(255, 196, 0),
}

# Friendly, on-screen descriptions of what Jarvis is doing.
TOOL_LABELS = {
    "play_media": "Playing {query}",
    "play_next_result": "Playing another result",
    "open_application": "Opening {application}",
    "open_website": "Opening {site}",
    "search_web": "Searching Google for “{query}”",
    "research_web": "Searching the web: {query}",
    "get_weather": "Checking the weather",
    "get_news": "Fetching the latest news",
    "wikipedia_summary": "Looking up {topic}",
    "calculate": "Calculating {expression}",
    "analyze_screen": "Looking at your screen",
    "analyze_image": "Analysing the image",
    "take_screenshot": "Taking a screenshot",
    "set_volume": "Setting volume to {level}%",
    "adjust_volume": "Turning volume {direction}",
    "mute_volume": "Toggling mute",
    "set_brightness": "Setting brightness to {level}%",
    "adjust_brightness": "Adjusting brightness",
    "media_control": "Media: {action}",
    "set_reminder": "Setting a reminder",
    "set_timer": "Starting a timer",
    "list_reminders": "Checking your reminders",
    "remember_fact": "Saving to memory",
    "recall_facts": "Searching my memory",
    "list_open_windows": "Checking open windows",
    "focus_window": "Switching to {title}",
    "minimize_window": "Minimizing {title}",
    "maximize_window": "Maximizing {title}",
    "close_window": "Closing {title}",
    "type_text": "Typing text",
    "open_folder": "Opening folder",
    "search_files": "Searching your files",
    "list_directory": "Listing files",
    "read_file_content": "Reading a file",
    "create_text_file": "Writing a file",
    "create_folder": "Creating a folder",
    "copy_file": "Copying a file",
    "move_file": "Moving a file",
    "rename_file": "Renaming a file",
    "delete_to_recycle": "Moving to Recycle Bin",
    "compose_email_draft": "Drafting your email",
    "save_research_note": "Saving a note",
    "get_clipboard": "Reading your clipboard",
    "set_clipboard": "Copying to clipboard",
    "get_system_status": "Checking system status",
    "get_battery_status": "Checking battery",
    "lock_computer": "Locking the PC",
    "power_action": "Preparing power action",
    "open_settings": "Opening Windows Settings",
}

_ARG_RE = re.compile(r"(\w+)=(['\"])(.*?)\2(?:,|\)$)")


def describe_tool(detail: str) -> str:
    """Turn "open_application(application='spotify')" into "Opening spotify"."""
    name = detail.split("(", 1)[0].strip()
    args = {m.group(1): m.group(3) for m in _ARG_RE.finditer(detail)}
    template = TOOL_LABELS.get(name)
    if not template:
        return name.replace("_", " ").capitalize()

    class _Safe(dict):
        def __missing__(self, key):
            return ""

    text = template.format_map(_Safe(args))
    if name == "get_weather" and args.get("location"):
        text += f" in {args['location']}"
    if name == "get_news" and args.get("topic"):
        text += f" about {args['topic']}"
    return re.sub(r"\s+", " ", text.replace("“”", "")).strip()


class DynamicIsland(QWidget):
    MARGIN = 10          # transparent margin for the glow
    TOP_OFFSET = 8
    WIDTH_IDLE = 170
    WIDTH_ACTIVE = 440
    WIDTH_TEXT = 540

    def __init__(self, on_open_dashboard=None, on_toggle_mic=None, on_quit=None, idle_visible: bool = False):
        super().__init__(None)
        self.on_open_dashboard = on_open_dashboard
        self.on_toggle_mic = on_toggle_mic
        self.on_quit = on_quit
        self.idle_visible = idle_visible

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.mode = "idle"
        self.title = "Jarvis"
        self.text = ""
        self.steps: list[str] = []
        self.full_text = ""
        self._hovered = False
        self._t0 = time.time()

        self._title_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
        self._text_font = QFont("Segoe UI", 10)
        self._step_font = QFont("Segoe UI", 9)

        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(220)
        self._fade.finished.connect(self._after_fade)
        self._fading_out = False

        self._frame = QTimer(self)
        self._frame.timeout.connect(self.update)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.collapse)

        self.setGeometry(self._target_rect(self.WIDTH_IDLE, 34))
        if idle_visible:
            self._show_animated()

    # ------------------------------------------------------------------ geometry
    def _screen_rect(self) -> QRect:
        screen = QApplication.primaryScreen()
        return screen.geometry() if screen else QRect(0, 0, 1920, 1080)

    def _target_rect(self, pill_w: int, pill_h: int) -> QRect:
        scr = self._screen_rect()
        w = pill_w + self.MARGIN * 2
        h = pill_h + self.MARGIN * 2
        x = scr.x() + (scr.width() - w) // 2
        y = scr.y() + self.TOP_OFFSET - self.MARGIN + 2
        return QRect(x, y, w, h)

    def _content_height(self, width: int) -> int:
        h = 54  # header row
        text_w = width - 70
        if self.text:
            fm = QFontMetrics(self._text_font)
            r = fm.boundingRect(QRect(0, 0, text_w, 2000), Qt.TextFlag.TextWordWrap, self.text)
            h += min(r.height(), fm.lineSpacing() * 5) + 8
        if self.steps:
            h += len(self.steps[-4:]) * 20 + 6
        return h + 6

    def _relayout(self):
        if self.mode == "idle" or self.mode == "paused":
            w, h = self.WIDTH_IDLE, 34
        elif self.mode in ("speaking", "result", "notify", "error") and self.text:
            w = self.WIDTH_TEXT
            h = self._content_height(w)
        else:
            w = self.WIDTH_ACTIVE
            h = self._content_height(w) if (self.text or self.steps) else 54
        target = self._target_rect(w, h)
        if self.isVisible() and not self._fading_out:
            self._anim.stop()
            self._anim.setStartValue(self.geometry())
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.setGeometry(target)
        self.update()

    # ------------------------------------------------------------------ visibility
    def _show_animated(self):
        self._hide_timer.stop()
        if not self._frame.isActive():
            self._frame.start(16)
        if self._fading_out or not self.isVisible():
            self._fading_out = False
            self._fade.stop()
            if not self.isVisible():
                self.setWindowOpacity(0.0)
                self.show()
            self._fade.setStartValue(self.windowOpacity())
            self._fade.setEndValue(1.0)
            self._fade.start()

    def _after_fade(self):
        if self._fading_out:
            self.hide()
            self._frame.stop()
            self._fading_out = False

    def collapse(self):
        """Return to idle: shrink to the small pill, or fade out completely."""
        if self._hovered:
            self._hide_timer.start(1500)
            return
        self.mode = "idle"
        self.title = "Jarvis"
        self.text = ""
        self.steps = []
        if self.idle_visible:
            self._relayout()
            return
        self._fading_out = True
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _schedule_hide(self, ms: int):
        self._hide_timer.start(ms)

    # ------------------------------------------------------------------ public API
    def set_mode(self, mode: str, title: str | None = None, text: str | None = None, clear_steps: bool = False):
        self.mode = mode
        if title is not None:
            self.title = title
        if text is not None:
            self.text = text
        if clear_steps:
            self.steps = []
        self._t0 = time.time()
        self._show_animated()
        self._relayout()

    def on_state(self, state: str, detail: str):
        if state == "listening":
            if "follow-up" in (detail or "").lower():
                # Keep the last answer on screen while waiting for a follow-up.
                self.set_mode("listening", "Listening for a follow-up…")
            else:
                self.set_mode("listening", "Listening…", "", clear_steps=True)
        elif state in ("thinking", "executing"):
            if self.mode != "thinking":
                self.set_mode("thinking", "Working on it…", self.text if self.text.startswith("“") else None)
        elif state == "speaking":
            self.set_mode("speaking", "Jarvis", self.text or detail)
        elif state == "success":
            self.mode = "result"
            self.update()
        elif state == "standby":
            low = (detail or "").lower()
            if "no speech" in low:
                self.set_mode("error", "I didn't catch that", "Say “Hey Jarvis” and try again.", clear_steps=True)
                self._schedule_hide(2200)
            elif "interrupted" in low or "cancel" in low:
                self._schedule_hide(600)
            elif self.mode in ("result", "speaking", "thinking", "error"):
                self.mode = "result" if self.mode != "error" else "error"
                words = len(self.text.split())
                self._schedule_hide(min(9000, 3500 + words * 120))
            elif self.mode == "listening":
                if self.text:
                    self.mode = "result"
                    self.title = "Jarvis"
                    self.update()
                    self._schedule_hide(3000)
                else:
                    self._schedule_hide(400)
            elif self.mode == "paused":
                self.collapse()
        elif state == "paused":
            self.set_mode("paused", "Mic paused", "", clear_steps=True)
            if not self.idle_visible:
                self._schedule_hide(2500)
        elif state == "error":
            self.set_mode("error", "Something went wrong", detail[:200])
            self._schedule_hide(6000)

    def on_heard(self, text: str):
        self.set_mode("thinking", "Working on it…", f"“{text.strip()}”", clear_steps=True)

    def on_activity(self, kind: str, detail: str):
        if kind == "tool":
            label = describe_tool(detail)
            if not self.steps or self.steps[-1] != label:
                self.steps.append(label)
            if self.mode not in ("thinking",):
                self.mode = "thinking"
                self.title = "Working on it…"
            self._show_animated()
            self._relayout()

    def on_result(self, detailed: str, spoken: str):
        self.full_text = detailed or ""
        answer = (spoken or detailed or "").strip()
        answer = answer.replace(" More details are in the terminal.", "")
        if len(self.full_text) > len(answer) + 40:
            answer += "  ·  Click for details"
        self.text = answer
        self.title = "Jarvis"
        self.mode = "result"
        self._show_animated()
        self._relayout()

    def notify(self, title: str, message: str):
        self.set_mode("notify", title, message, clear_steps=True)
        self._schedule_hide(9000)

    # ------------------------------------------------------------------ events
    def enterEvent(self, event):
        self._hovered = True
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        if self.mode in ("result", "error", "notify") and not self._hide_timer.isActive():
            self._hide_timer.start(2500)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.on_open_dashboard:
                self.on_open_dashboard()
            self._hide_timer.start(300)
        elif event.button() == Qt.MouseButton.RightButton:
            menu = QMenu()
            menu.addAction("Open dashboard", lambda: self.on_open_dashboard and self.on_open_dashboard())
            if self.on_toggle_mic:
                menu.addAction("Pause / resume mic", self.on_toggle_mic)
            menu.addAction("Dismiss", self.collapse)
            if self.on_quit:
                menu.addSeparator()
                menu.addAction("Quit Jarvis", self.on_quit)
            menu.exec(event.globalPosition().toPoint())

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        m = self.MARGIN
        rect = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        radius = min(rect.height() / 2, 26)
        col = STATE_COLORS.get(self.mode, STATE_COLORS["idle"])
        t = time.time() - self._t0
        busy = self.mode in ("listening", "thinking", "speaking")

        # Glow
        for i in range(m, 0, -2):
            alpha = int((28 if busy else 14) * (1 - i / m) + 2)
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), alpha), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-i, -i, i, i), radius + i, radius + i)

        # Body
        p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 110 if busy else 60), 1.2))
        p.setBrush(QColor(4, 6, 12, 246))
        p.drawRoundedRect(rect, radius, radius)

        compact = rect.height() < 40
        cy = rect.top() + (rect.height() / 2 if compact else 27)
        orb_x = rect.left() + (20 if compact else 28)
        self._paint_orb(p, QPointF(orb_x, cy), 8 if compact else 11, col, t, busy)

        # Title
        p.setFont(self._title_font)
        p.setPen(QColor(255, 255, 255, 240))
        title_x = orb_x + (16 if compact else 22)
        wave_w = 70 if busy and not compact else 0
        title_rect = QRectF(title_x, cy - 11, rect.right() - title_x - wave_w - 18, 22)
        title = self.title if not compact else ("Mic paused" if self.mode == "paused" else "Jarvis")
        p.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   QFontMetrics(self._title_font).elidedText(title, Qt.TextElideMode.ElideRight, int(title_rect.width())))

        if wave_w:
            self._paint_wave(p, QRectF(rect.right() - wave_w - 16, cy - 11, wave_w, 22), col, t)

        if compact:
            p.end()
            return

        y = rect.top() + 50
        text_x = rect.left() + 26
        text_w = rect.width() - 52
        if self.text:
            p.setFont(self._text_font)
            p.setPen(QColor(205, 225, 255, 235))
            fm = QFontMetrics(self._text_font)
            max_h = fm.lineSpacing() * 5
            br = fm.boundingRect(QRect(0, 0, int(text_w), 2000), Qt.TextFlag.TextWordWrap, self.text)
            h = min(br.height(), max_h)
            p.save()
            p.setClipRect(QRectF(text_x, y, text_w, h + 2))
            p.drawText(QRectF(text_x, y, text_w, max_h + fm.lineSpacing()),
                       Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft, self.text)
            p.restore()
            y += h + 8

        if self.steps:
            p.setFont(self._step_font)
            steps = self.steps[-4:]
            for i, step in enumerate(steps):
                last = i == len(steps) - 1
                running = last and self.mode == "thinking"
                mark_c = QPointF(text_x + 6, y + 9)
                if running:
                    p.setPen(QPen(col, 2))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    start = int((t * 360 * 1.5) % 360) * 16
                    p.drawArc(QRectF(mark_c.x() - 5, mark_c.y() - 5, 10, 10), start, 270 * 16)
                else:
                    p.setPen(QPen(QColor(0, 230, 118), 2))
                    path = QPainterPath()
                    path.moveTo(mark_c.x() - 4, mark_c.y())
                    path.lineTo(mark_c.x() - 1, mark_c.y() + 3)
                    path.lineTo(mark_c.x() + 4, mark_c.y() - 3)
                    p.drawPath(path)
                p.setPen(QColor(170, 195, 230, 230 if running else 170))
                fm = QFontMetrics(self._step_font)
                p.drawText(QRectF(text_x + 18, y, text_w - 18, 18), Qt.AlignmentFlag.AlignVCenter,
                           fm.elidedText(step, Qt.TextElideMode.ElideRight, int(text_w - 18)))
                y += 20
        p.end()

    @staticmethod
    def _paint_orb(p: QPainter, c: QPointF, r: float, col: QColor, t: float, busy: bool):
        pulse = (math.sin(t * (7 if busy else 2.5)) + 1) / 2
        rr = r * (1.0 + (0.18 * pulse if busy else 0.06 * pulse))
        g = QRadialGradient(c, rr * 2.2)
        g.setColorAt(0, QColor(col.red(), col.green(), col.blue(), 120))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, rr * 2.2, rr * 2.2)
        core = QRadialGradient(QPointF(c.x() - rr * 0.3, c.y() - rr * 0.3), rr * 1.3)
        core.setColorAt(0, QColor(255, 255, 255))
        core.setColorAt(0.35, col)
        core.setColorAt(1, QColor(0, 40, 90))
        p.setBrush(core)
        p.drawEllipse(c, rr, rr)
        if busy:
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 200), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(c.x() - rr - 4, c.y() - rr - 4, (rr + 4) * 2, (rr + 4) * 2),
                      int((t * 300) % 360) * 16, 110 * 16)

    def _paint_wave(self, p: QPainter, r: QRectF, col: QColor, t: float):
        bars = 9
        gap = r.width() / bars
        speed = {"listening": 9, "speaking": 12, "thinking": 4}.get(self.mode, 5)
        amp = {"thinking": 0.35}.get(self.mode, 1.0)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(bars):
            phase = math.sin(t * speed + i * 0.8) * 0.5 + math.sin(t * speed * 0.6 + i * 1.7) * 0.5
            h = max(3.0, (0.25 + 0.75 * abs(phase) * amp) * r.height())
            x = r.left() + i * gap + gap * 0.25
            p.setBrush(QColor(col.red(), col.green(), col.blue(), 210))
            p.drawRoundedRect(QRectF(x, r.center().y() - h / 2, gap * 0.5, h), 2, 2)
