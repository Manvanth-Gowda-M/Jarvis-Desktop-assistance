"""
assistant/desktop_ui.py
=======================
PySide6 animated desktop orb interface, compact control panel, and Windows system tray
for the Jarvis personal AI desktop companion.
"""

from __future__ import annotations

import ctypes
import math
import os
import sys
import winreg
from typing import Optional

from PySide6.QtCore import (
    Qt,
    QPoint,
    QTimer,
    Signal,
    QObject,
    QRectF,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QBrush,
    QPen,
    QRadialGradient,
    QConicalGradient,
    QIcon,
    QPixmap,
    QFont,
    QCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QSystemTrayIcon,
    QMenu,
    QFrame,
    QGraphicsDropShadowEffect,
    QComboBox,
)

from config import JARVIS_NAME
from assistant.audio_coordinator import AudioCoordinator, AssistantState
from assistant.wake_word import get_wake_word_setup_instructions
from assistant.email_assistant import _default_assistant

# ---------------------------------------------------------------------------
# COLOR PALETTE: Futuristic Cyan / Electric Blue / Deep Void
# ---------------------------------------------------------------------------
CYAN_GLOW = QColor(0, 229, 255)         # #00e5ff
ELECTRIC_BLUE = QColor(0, 119, 255)     # #0077ff
CORE_DARK = QColor(6, 15, 30)           # #060f1e
AMBER_PAUSED = QColor(255, 179, 0)      # #ffb300
CRIMSON_ERROR = QColor(255, 45, 85)     # #ff2d55
EMERALD_SUCCESS = QColor(0, 230, 118)   # #00e676
PANEL_BG = QColor(10, 18, 36, 240)      # Translucent dark navy

_SINGLE_INSTANCE_MUTEX = None


def acquire_single_instance_mutex(mutex_name: str = "JarvisDesktopCompanionSingleInstanceMutex") -> bool:
    """Acquire a named Windows mutex to enforce a single running application instance."""
    global _SINGLE_INSTANCE_MUTEX
    if sys.platform != "win32":
        return True

    try:
        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183

        kernel32.SetLastError(0)
        mutex = kernel32.CreateMutexW(None, False, mutex_name)
        last_error = kernel32.GetLastError()

        if last_error == ERROR_ALREADY_EXISTS:
            if mutex:
                kernel32.CloseHandle(mutex)
            return False

        _SINGLE_INSTANCE_MUTEX = mutex
        return True
    except Exception as exc:
        print(f"[Desktop UI] Mutex check notice: {exc}")
        return True


def release_single_instance_mutex():
    """Release the single-instance mutex handle upon application termination."""
    global _SINGLE_INSTANCE_MUTEX
    if _SINGLE_INSTANCE_MUTEX and sys.platform == "win32":
        try:
            ctypes.windll.kernel32.CloseHandle(_SINGLE_INSTANCE_MUTEX)
        except Exception:
            pass
        _SINGLE_INSTANCE_MUTEX = None


def set_startup_registry(enable: bool) -> bool:
    """Toggle launch on Windows user login via Registry."""
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "JarvisDesktopCompanion"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
            if enable:
                cmd = f'"{sys.executable}" "{os.path.abspath("main.py")}" --desktop'
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
        return True
    except Exception as exc:
        print(f"[Desktop UI] Startup registry toggle error: {exc}")
        return False


def is_startup_enabled() -> bool:
    """Check if startup at login is currently enabled in Windows Registry."""
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "JarvisDesktopCompanion"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, app_name)
            return True
    except Exception:
        return False


class UIBridge(QObject):
    """Thread-safe signal bridge between AudioCoordinator background threads and Qt GUI."""
    state_changed = Signal(str, str)     # state_name, detail
    result_received = Signal(str, str)   # detailed_text, spoken_text


class JarvisOrbWidget(QWidget):
    """Frameless, draggable, animated desktop orb with futuristic glowing visual states."""

    def __init__(self, coordinator: AudioCoordinator, bridge: UIBridge, parent=None):
        super().__init__(parent)
        self.coordinator = coordinator
        self.bridge = bridge

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.SubWindow
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(140, 140)

        # Interaction state
        self._drag_pos = QPoint()
        self._panel_visible = False
        self.current_state = AssistantState.STANDBY
        self.state_detail = "Ready"

        # Animation parameters
        self._tick = 0
        self._angle = 0.0
        self._pulse = 0.0

        # Animation timer at ~60 FPS
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._on_animation_frame)
        self._anim_timer.start(16)

        # Connect thread-safe bridge signals
        self.bridge.state_changed.connect(self._on_state_changed_gui)

        self.setToolTip(f"{JARVIS_NAME} Desktop Companion\nClick to open Control Panel\nDrag to move")

    def _on_state_changed_gui(self, state_str: str, detail: str):
        try:
            self.current_state = AssistantState(state_str)
        except Exception:
            self.current_state = AssistantState.STANDBY
        self.state_detail = detail
        self.update()

    def _on_animation_frame(self):
        self._tick += 1
        self._angle = (self._angle + 2.5) % 360
        self._pulse = (math.sin(self._tick * 0.05) + 1.0) / 2.0  # 0.0 to 1.0
        self.update()

    # ------------------ Painting / Rendering ------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center_x = self.width() / 2.0
        center_y = self.height() / 2.0
        base_radius = 45.0

        # Choose primary glow colors based on current state
        if self.current_state == AssistantState.PAUSED:
            primary_color = AMBER_PAUSED
            secondary_color = QColor(180, 100, 0)
        elif self.current_state == AssistantState.ERROR:
            primary_color = CRIMSON_ERROR
            secondary_color = QColor(160, 20, 40)
        elif self.current_state == AssistantState.SUCCESS:
            primary_color = EMERALD_SUCCESS
            secondary_color = CYAN_GLOW
        else:
            primary_color = CYAN_GLOW
            secondary_color = ELECTRIC_BLUE

        # 1. Outer Halo / Ambient Glow
        halo_radius = base_radius + 15 + (self._pulse * 8 if self.current_state != AssistantState.PAUSED else 2)
        glow_grad = QRadialGradient(center_x, center_y, halo_radius)
        glow_grad.setColorAt(0.0, QColor(primary_color.red(), primary_color.green(), primary_color.blue(), 100))
        glow_grad.setColorAt(0.7, QColor(secondary_color.red(), secondary_color.green(), secondary_color.blue(), 40))
        glow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(glow_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPoint(int(center_x), int(center_y)), int(halo_radius), int(halo_radius))

        # 2. Dynamic Rings / Arcs depending on State
        if self.current_state == AssistantState.LISTENING:
            # Concentric expanding acoustic waveform rings
            for i in range(3):
                ring_r = base_radius + 6 + (self._tick * 1.5 + i * 12) % 22
                alpha = int(max(0, 255 - (ring_r - base_radius) * 11))
                ring_pen = QPen(QColor(0, 229, 255, alpha), 2.0)
                painter.setPen(ring_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPoint(int(center_x), int(center_y)), int(ring_r), int(ring_r))

        elif self.current_state in (AssistantState.THINKING, AssistantState.EXECUTING):
            # Rotating futuristic segmented arcs
            painter.save()
            painter.translate(center_x, center_y)
            painter.rotate(self._angle if self.current_state == AssistantState.THINKING else -self._angle * 1.5)
            arc_pen = QPen(primary_color, 2.5, Qt.PenStyle.DashLine)
            painter.setPen(arc_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(-base_radius - 6, -base_radius - 6, (base_radius + 6) * 2, (base_radius + 6) * 2))
            painter.restore()

        elif self.current_state == AssistantState.SPEAKING:
            # Rhythmic speech ripples
            for i in range(2):
                r_ripple = base_radius + 4 + math.sin(self._tick * 0.15 + i) * 8
                ripple_pen = QPen(QColor(0, 200, 255, 180), 2.0)
                painter.setPen(ripple_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPoint(int(center_x), int(center_y)), int(r_ripple), int(r_ripple))

        # 3. Main Orb Core Body (Spherical Radial Gradient)
        core_radius = base_radius - 2
        core_grad = QRadialGradient(center_x - 10, center_y - 12, core_radius)
        core_grad.setColorAt(0.0, QColor(primary_color.red(), primary_color.green(), primary_color.blue(), 230))
        core_grad.setColorAt(0.35, QColor(secondary_color.red(), secondary_color.green(), secondary_color.blue(), 200))
        core_grad.setColorAt(0.85, CORE_DARK)
        core_grad.setColorAt(1.0, QColor(2, 6, 14, 255))

        painter.setBrush(QBrush(core_grad))
        painter.setPen(QPen(QColor(primary_color.red(), primary_color.green(), primary_color.blue(), 180), 1.5))
        painter.drawEllipse(QPoint(int(center_x), int(center_y)), int(core_radius), int(core_radius))

        # 4. Inner Cybernetic Core Accent / Node
        inner_r = 10 + self._pulse * 4
        inner_grad = QRadialGradient(center_x, center_y, inner_r)
        inner_grad.setColorAt(0.0, QColor(255, 255, 255, 240))
        inner_grad.setColorAt(0.5, QColor(primary_color.red(), primary_color.green(), primary_color.blue(), 200))
        inner_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(inner_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPoint(int(center_x), int(center_y)), int(inner_r), int(inner_r))

    # ------------------ Mouse & Drag Handling ------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            if hasattr(self, "control_panel") and self.control_panel.isVisible():
                self.control_panel.reposition_near_orb()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # If released roughly where clicked (not a large drag), toggle control panel
            if hasattr(self, "control_panel"):
                self.control_panel.toggle_visibility()
            event.accept()


class CompactControlPanel(QWidget):
    """Compact companion control panel with text input, mic controls, and full response viewer."""

    def __init__(self, orb: JarvisOrbWidget, coordinator: AudioCoordinator, bridge: UIBridge, parent=None):
        super().__init__(parent)
        self.orb = orb
        self.coordinator = coordinator
        self.bridge = bridge

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(380)
        self.email_panel = None

        self._init_ui()

        # Connect signals
        self.bridge.state_changed.connect(self._on_state_changed)
        self.bridge.result_received.connect(self._on_result_received)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Frosted dark container card
        self.card = QFrame(self)
        self.card.setStyleSheet("""
            QFrame {
                background-color: rgba(10, 18, 36, 0.94);
                border: 1px solid rgba(0, 229, 255, 0.4);
                border-radius: 12px;
            }
        """)
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 229, 255, 60))
        shadow.setOffset(0, 4)
        self.card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.card)
        card_layout.setSpacing(10)
        card_layout.setContentsMargins(14, 14, 14, 14)

        # Header Bar
        header_layout = QHBoxLayout()
        self.title_lbl = QLabel(f"{JARVIS_NAME} Desktop")
        self.title_lbl.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 14px; border: none;")

        self.state_badge = QLabel("STANDBY")
        self.state_badge.setStyleSheet("""
            color: #ffffff;
            background-color: rgba(0, 229, 255, 0.25);
            border: 1px solid #00e5ff;
            border-radius: 6px;
            padding: 2px 8px;
            font-size: 11px;
            font-weight: bold;
        """)

        self.close_btn = QPushButton("×")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setStyleSheet("""
            QPushButton {
                color: #aaaaaa;
                background: transparent;
                border: none;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover { color: #ff2d55; }
        """)
        self.close_btn.clicked.connect(self.hide)

        header_layout.addWidget(self.title_lbl)
        header_layout.addStretch()
        header_layout.addWidget(self.state_badge)
        header_layout.addWidget(self.close_btn)
        card_layout.addLayout(header_layout)

        # Wake-Word / Trigger notice
        wake_status = self.coordinator.wake_detector.status_message
        self.wake_lbl = QLabel(f"Wake-Word: {wake_status}")
        self.wake_lbl.setWordWrap(True)
        self.wake_lbl.setStyleSheet("color: #8899aa; font-size: 11px; border: none;")
        card_layout.addWidget(self.wake_lbl)

        # Quick Voice & Action Toolbar
        action_layout = QHBoxLayout()

        self.listen_btn = QPushButton("🎙 Speak")
        self.listen_btn.setToolTip("Trigger voice listening immediately")
        self.listen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.listen_btn.setStyleSheet("""
            QPushButton {
                background-color: #0077ff;
                color: white;
                font-weight: bold;
                border-radius: 6px;
                padding: 6px 12px;
                border: none;
            }
            QPushButton:hover { background-color: #0099ff; }
        """)
        self.listen_btn.clicked.connect(lambda: self.coordinator.trigger_listening())

        self.pause_btn = QPushButton("⏸ Pause Mic")
        self.pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: #cccccc;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 6px;
                padding: 6px 10px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.16); color: white; }
        """)
        self.pause_btn.clicked.connect(self._toggle_mic_pause)

        self.stop_speech_btn = QPushButton("⏹ Stop Speech")
        self.stop_speech_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_speech_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 45, 85, 0.15);
                color: #ff5577;
                border: 1px solid rgba(255, 45, 85, 0.35);
                border-radius: 6px;
                padding: 6px 10px;
            }
            QPushButton:hover { background-color: rgba(255, 45, 85, 0.35); color: white; }
        """)
        self.stop_speech_btn.clicked.connect(self.coordinator.stop_speaking)

        self.email_btn = QPushButton("✉ Email")
        self.email_btn.setToolTip("Open Email & Draft Manager (Gmail + Outlook)")
        self.email_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.email_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 229, 255, 0.15);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.35);
                border-radius: 6px;
                padding: 6px 10px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: rgba(0, 229, 255, 0.3); color: white; }
        """)
        self.email_btn.clicked.connect(self.toggle_email_panel)

        action_layout.addWidget(self.listen_btn)
        action_layout.addWidget(self.pause_btn)
        action_layout.addWidget(self.stop_speech_btn)
        action_layout.addWidget(self.email_btn)
        card_layout.addLayout(action_layout)

        # Text Command Fallback Field
        input_layout = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Type a command (fallback to voice)...")
        self.input_field.setStyleSheet("""
            QLineEdit {
                background-color: rgba(0, 0, 0, 0.4);
                color: #e0f0ff;
                border: 1px solid rgba(0, 229, 255, 0.3);
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 1px solid #00e5ff;
            }
        """)
        self.input_field.returnPressed.connect(self._send_text_command)

        self.send_btn = QPushButton("Send")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 229, 255, 0.2);
                color: #00e5ff;
                border: 1px solid #00e5ff;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #00e5ff; color: black; }
        """)
        self.send_btn.clicked.connect(self._send_text_command)

        input_layout.addWidget(self.input_field)
        input_layout.addWidget(self.send_btn)
        card_layout.addLayout(input_layout)

        # Full Detailed Text Response Viewer
        self.response_view = QTextEdit()
        self.response_view.setReadOnly(True)
        self.response_view.setPlaceholderText("Detailed assistant responses and actions appear here...")
        self.response_view.setFixedHeight(120)
        self.response_view.setStyleSheet("""
            QTextEdit {
                background-color: rgba(3, 8, 20, 0.6);
                color: #c8e0f8;
                border: 1px solid rgba(0, 229, 255, 0.2);
                border-radius: 6px;
                padding: 8px;
                font-size: 11px;
                line-height: 1.4;
            }
        """)
        card_layout.addWidget(self.response_view)

        main_layout.addWidget(self.card)

    def reposition_near_orb(self):
        """Align the control panel conveniently beside the floating orb."""
        orb_geo = self.orb.frameGeometry()
        x = orb_geo.right() + 10
        y = orb_geo.top() - 10

        # Screen boundary protection
        screen = QApplication.primaryScreen().availableGeometry()
        if x + self.width() > screen.right():
            x = orb_geo.left() - self.width() - 10
        if y + self.height() > screen.bottom():
            y = screen.bottom() - self.height() - 10
        if y < screen.top():
            y = screen.top() + 10

        self.move(max(10, x), max(10, y))

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.reposition_near_orb()
            self.show()
            self.raise_()
            self.input_field.setFocus()

    def _toggle_mic_pause(self):
        self.coordinator.toggle_pause()
        if self.coordinator.is_paused:
            self.pause_btn.setText("▶ Resume Mic")
            self.pause_btn.setStyleSheet("background-color: rgba(255, 179, 0, 0.2); color: #ffb300; border: 1px solid #ffb300; border-radius: 6px; padding: 6px 10px;")
        else:
            self.pause_btn.setText("⏸ Pause Mic")
            self.pause_btn.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); color: #cccccc; border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 6px; padding: 6px 10px;")

    def _send_text_command(self):
        text = self.input_field.text().strip()
        if text:
            self.input_field.clear()
            self.response_view.append(f"\n<b>You:</b> {text}")
            self.coordinator.process_text_command(text)

    def toggle_email_panel(self):
        """Toggle visibility of the compact Email & Draft Manager panel."""
        if self.email_panel is None:
            self.email_panel = CompactEmailPanel(control_panel=self)
        if self.email_panel.isVisible():
            self.email_panel.hide()
        else:
            self.email_panel.refresh_status()
            orb_geo = self.orb.frameGeometry()
            x = max(10, orb_geo.left() - self.email_panel.width() - 10)
            y = max(10, orb_geo.top() - 60)
            self.email_panel.move(x, y)
            self.email_panel.show()
            self.email_panel.raise_()

    def _on_state_changed(self, state_str: str, detail: str):
        self.state_badge.setText(state_str.upper())
        colors = {
            "standby": ("#00e5ff", "rgba(0, 229, 255, 0.2)"),
            "listening": ("#00ffea", "rgba(0, 255, 234, 0.35)"),
            "thinking": ("#0088ff", "rgba(0, 136, 255, 0.3)"),
            "executing": ("#7928ca", "rgba(121, 40, 202, 0.3)"),
            "speaking": ("#00e5ff", "rgba(0, 229, 255, 0.35)"),
            "paused": ("#ffb300", "rgba(255, 179, 0, 0.25)"),
            "error": ("#ff2d55", "rgba(255, 45, 85, 0.3)"),
            "success": ("#00e676", "rgba(0, 230, 118, 0.3)"),
        }
        fg, bg = colors.get(state_str.lower(), ("#ffffff", "rgba(255, 255, 255, 0.2)"))
        self.state_badge.setStyleSheet(f"color: {fg}; background-color: {bg}; border: 1px solid {fg}; border-radius: 6px; padding: 2px 8px; font-weight: bold; font-size: 11px;")

        if detail and detail != "Ready":
            self.wake_lbl.setText(detail)

    def _on_result_received(self, detailed: str, spoken: str):
        self.response_view.append(f"<b>{JARVIS_NAME}:</b> {detailed}")
        # Scroll to bottom
        sb = self.response_view.verticalScrollBar()
        sb.setValue(sb.maximum())


class CompactEmailPanel(QWidget):
    """Compact desktop email manager, OAuth credentials status, and draft composer."""

    def __init__(self, control_panel=None, parent=None):
        super().__init__(parent)
        self.control_panel = control_panel
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(400)
        self._drag_pos = QPoint()

        self._init_ui()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and not self._drag_pos.isNull():
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Card container
        self.card = QFrame(self)
        self.card.setStyleSheet("""
            QFrame {
                background-color: rgba(10, 18, 36, 0.96);
                border: 1px solid rgba(0, 229, 255, 0.4);
                border-radius: 12px;
            }
        """)
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 229, 255, 60))
        shadow.setOffset(0, 4)
        self.card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.card)
        card_layout.setSpacing(10)
        card_layout.setContentsMargins(14, 14, 14, 14)

        # 1. Header Bar
        header = QHBoxLayout()
        title = QLabel("📧 Email & Draft Manager")
        title.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 13px; border: none;")
        close_btn = QPushButton("×")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("color: #aaaaaa; background: transparent; border: none; font-size: 16px; font-weight: bold;")
        close_btn.clicked.connect(self.hide)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        card_layout.addLayout(header)

        # 2. Account Connections Section
        acc_frame = QFrame()
        acc_frame.setStyleSheet("background-color: rgba(3, 8, 20, 0.5); border: 1px solid rgba(0, 229, 255, 0.2); border-radius: 8px; padding: 6px;")
        acc_layout = QVBoxLayout(acc_frame)
        acc_layout.setSpacing(6)

        # Gmail row
        gmail_row = QHBoxLayout()
        gmail_lbl = QLabel("Gmail:")
        gmail_lbl.setStyleSheet("color: #e0f0ff; font-weight: bold; font-size: 11px; border: none;")
        self.gmail_badge = QLabel("Checking...")
        self.gmail_badge.setStyleSheet("color: #aaaaaa; font-size: 10px; border: none;")
        self.gmail_btn = QPushButton("Connect")
        self.gmail_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gmail_btn.setStyleSheet("background-color: #0077ff; color: white; border-radius: 4px; padding: 3px 8px; font-size: 10px; border: none;")
        self.gmail_btn.clicked.connect(self._on_gmail_action)

        gmail_row.addWidget(gmail_lbl)
        gmail_row.addWidget(self.gmail_badge)
        gmail_row.addStretch()
        gmail_row.addWidget(self.gmail_btn)
        acc_layout.addLayout(gmail_row)

        # Outlook row
        outlook_row = QHBoxLayout()
        outlook_lbl = QLabel("Outlook:")
        outlook_lbl.setStyleSheet("color: #e0f0ff; font-weight: bold; font-size: 11px; border: none;")
        self.outlook_badge = QLabel("Checking...")
        self.outlook_badge.setStyleSheet("color: #aaaaaa; font-size: 10px; border: none;")
        self.outlook_btn = QPushButton("Connect")
        self.outlook_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.outlook_btn.setStyleSheet("background-color: #0077ff; color: white; border-radius: 4px; padding: 3px 8px; font-size: 10px; border: none;")
        self.outlook_btn.clicked.connect(self._on_outlook_action)

        outlook_row.addWidget(outlook_lbl)
        outlook_row.addWidget(self.outlook_badge)
        outlook_row.addStretch()
        outlook_row.addWidget(self.outlook_btn)
        acc_layout.addLayout(outlook_row)

        card_layout.addWidget(acc_frame)

        # 3. Draft Composer Section
        prov_row = QHBoxLayout()
        prov_lbl = QLabel("Provider:")
        prov_lbl.setStyleSheet("color: #8899aa; font-size: 11px; border: none;")
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["Auto (Connected)", "Gmail", "Outlook"])
        self.provider_combo.setStyleSheet("""
            QComboBox {
                background-color: rgba(0, 0, 0, 0.4);
                color: #e0f0ff;
                border: 1px solid rgba(0, 229, 255, 0.3);
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
            }
        """)
        prov_row.addWidget(prov_lbl)
        prov_row.addWidget(self.provider_combo)
        prov_row.addStretch()
        card_layout.addLayout(prov_row)

        # Recipient Field
        self.to_input = QLineEdit()
        self.to_input.setPlaceholderText("To: professor@university.edu (explicit email required)")
        self.to_input.setStyleSheet("background-color: rgba(0, 0, 0, 0.4); color: #e0f0ff; border: 1px solid rgba(0, 229, 255, 0.3); border-radius: 4px; padding: 4px 8px; font-size: 11px;")
        card_layout.addWidget(self.to_input)

        # Subject Field
        self.subject_input = QLineEdit()
        self.subject_input.setPlaceholderText("Subject: Appointment Request / Follow-up")
        self.subject_input.setStyleSheet("background-color: rgba(0, 0, 0, 0.4); color: #e0f0ff; border: 1px solid rgba(0, 229, 255, 0.3); border-radius: 4px; padding: 4px 8px; font-size: 11px;")
        card_layout.addWidget(self.subject_input)

        # Message Body Field
        self.body_input = QTextEdit()
        self.body_input.setPlaceholderText("Message body: Write your email or ask Jarvis to draft it...")
        self.body_input.setFixedHeight(110)
        self.body_input.setStyleSheet("background-color: rgba(3, 8, 20, 0.7); color: #c8e0f8; border: 1px solid rgba(0, 229, 255, 0.2); border-radius: 6px; padding: 6px; font-size: 11px;")
        card_layout.addWidget(self.body_input)

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.polish_btn = QPushButton("✨ Make Professional")
        self.polish_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.polish_btn.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); color: #cccccc; border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 4px; padding: 5px 8px; font-size: 11px;")
        self.polish_btn.clicked.connect(self._polish_draft)

        self.save_btn = QPushButton("💾 Save to Drafts")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setStyleSheet("background-color: #00e5ff; color: #060f1e; font-weight: bold; border-radius: 4px; padding: 5px 12px; font-size: 11px; border: none;")
        self.save_btn.clicked.connect(self._save_draft)

        btn_layout.addWidget(self.polish_btn)
        btn_layout.addWidget(self.save_btn)
        card_layout.addLayout(btn_layout)

        # Result / Status Notification
        self.result_lbl = QLabel("Strict Draft-Only: Jarvis never sends emails.")
        self.result_lbl.setWordWrap(True)
        self.result_lbl.setStyleSheet("color: #8899aa; font-size: 10px; border: none;")
        card_layout.addWidget(self.result_lbl)

        main_layout.addWidget(self.card)

    def refresh_status(self):
        """Update provider status indicators and buttons."""
        import threading
        def _check():
            st = _default_assistant.get_status()
            gmail_st = st["gmail"]
            outlook_st = st["outlook"]

            # Update UI on main thread safely
            def _update():
                if gmail_st["connected"]:
                    email_str = gmail_st.get("email") or "Connected"
                    self.gmail_badge.setText(f"Connected ({email_str})")
                    self.gmail_badge.setStyleSheet("color: #00e676; font-size: 10px; border: none;")
                    self.gmail_btn.setText("Disconnect")
                    self.gmail_btn.setStyleSheet("background-color: rgba(255, 45, 85, 0.2); color: #ff5577; border-radius: 4px; padding: 3px 8px; font-size: 10px; border: 1px solid #ff5577;")
                else:
                    self.gmail_badge.setText(gmail_st.get("detail", "Disconnected")[:26])
                    self.gmail_badge.setStyleSheet("color: #aaaaaa; font-size: 10px; border: none;")
                    self.gmail_btn.setText("Connect")
                    self.gmail_btn.setStyleSheet("background-color: #0077ff; color: white; border-radius: 4px; padding: 3px 8px; font-size: 10px; border: none;")

                if outlook_st["connected"]:
                    email_str = outlook_st.get("email") or "Connected"
                    self.outlook_badge.setText(f"Connected ({email_str})")
                    self.outlook_badge.setStyleSheet("color: #00e676; font-size: 10px; border: none;")
                    self.outlook_btn.setText("Disconnect")
                    self.outlook_btn.setStyleSheet("background-color: rgba(255, 45, 85, 0.2); color: #ff5577; border-radius: 4px; padding: 3px 8px; font-size: 10px; border: 1px solid #ff5577;")
                else:
                    self.outlook_badge.setText(outlook_st.get("detail", "Disconnected")[:26])
                    self.outlook_badge.setStyleSheet("color: #aaaaaa; font-size: 10px; border: none;")
                    self.outlook_btn.setText("Connect")
                    self.outlook_btn.setStyleSheet("background-color: #0077ff; color: white; border-radius: 4px; padding: 3px 8px; font-size: 10px; border: none;")

            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, _update)

        threading.Thread(target=_check, daemon=True).start()

    def _on_gmail_action(self):
        st = _default_assistant.get_status()["gmail"]
        if st["connected"]:
            ok, msg = _default_assistant.disconnect_gmail()
            self.result_lbl.setText(msg)
            self.refresh_status()
        else:
            self.result_lbl.setText("Opening browser for Google sign-in...")
            import threading
            def _connect():
                ok, msg = _default_assistant.connect_gmail()
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.result_lbl.setText(msg))
                QTimer.singleShot(0, self.refresh_status)
            threading.Thread(target=_connect, daemon=True).start()

    def _on_outlook_action(self):
        st = _default_assistant.get_status()["outlook"]
        if st["connected"]:
            ok, msg = _default_assistant.disconnect_outlook()
            self.result_lbl.setText(msg)
            self.refresh_status()
        else:
            self.result_lbl.setText("Opening browser for Microsoft sign-in...")
            import threading
            def _connect():
                ok, msg = _default_assistant.connect_outlook()
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.result_lbl.setText(msg))
                QTimer.singleShot(0, self.refresh_status)
            threading.Thread(target=_connect, daemon=True).start()

    def _polish_draft(self):
        subj = self.subject_input.text().strip()
        body = self.body_input.toPlainText().strip()
        if not body:
            self.result_lbl.setText("Please enter some draft text to polish.")
            return

        # Ensure professional salutation and signoff
        polished = body
        if not any(polished.lower().startswith(s) for s in ("dear", "hello", "hi", "good")):
            polished = f"Dear Colleague,\n\n{polished}"
        if not any(s in polished.lower() for s in ("sincerely", "best regards", "thank you", "warm regards")):
            polished = f"{polished}\n\nSincerely,\n[Your Name]"

        self.body_input.setPlainText(polished)
        self.result_lbl.setText("✨ Draft text formatted with professional structure.")

    def _save_draft(self):
        to_addr = self.to_input.text().strip()
        subject = self.subject_input.text().strip()
        body = self.body_input.toPlainText().strip()

        if not to_addr:
            self.result_lbl.setText("❌ Recipient email is required.")
            return

        choice = self.provider_combo.currentText().lower()
        provider = "auto"
        if "gmail" in choice:
            provider = "gmail"
        elif "outlook" in choice:
            provider = "outlook"

        self.result_lbl.setText("Saving draft to provider...")
        import threading
        def _do_save():
            res = _default_assistant.compose_draft(
                to_addresses=to_addr,
                subject=subject,
                body=body,
                provider=provider,
                save_to_provider=True,
            )
            from PySide6.QtCore import QTimer
            def _update():
                if "✅ Verified" in res:
                    self.result_lbl.setStyleSheet("color: #00e676; font-size: 10px; border: none;")
                    self.result_lbl.setText("✅ Verified: Draft created in your official Drafts folder.")
                else:
                    self.result_lbl.setStyleSheet("color: #e0f0ff; font-size: 10px; border: none;")
                    self.result_lbl.setText(res[:140] + ("…" if len(res) > 140 else ""))
            QTimer.singleShot(0, _update)

        threading.Thread(target=_do_save, daemon=True).start()


def create_tray_icon_pixmap() -> QPixmap:
    """Create a sharp cyan orb icon for the Windows system tray."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Gradient circle
    grad = QRadialGradient(16, 16, 15)
    grad.setColorAt(0.0, QColor(0, 229, 255, 255))
    grad.setColorAt(0.6, QColor(0, 119, 255, 220))
    grad.setColorAt(1.0, QColor(6, 15, 30, 255))
    painter.setBrush(QBrush(grad))
    painter.setPen(QPen(QColor(0, 229, 255), 1.5))
    painter.drawEllipse(2, 2, 28, 28)

    painter.end()
    return pixmap


class JarvisTrayIcon(QSystemTrayIcon):
    """Windows System Tray manager with status controls, startup toggle, and clean exit."""

    def __init__(self, orb: JarvisOrbWidget, control_panel: CompactControlPanel, coordinator: AudioCoordinator, parent=None):
        super().__init__(parent)
        self.orb = orb
        self.control_panel = control_panel
        self.coordinator = coordinator

        self.setIcon(QIcon(create_tray_icon_pixmap()))
        self.setToolTip(f"{JARVIS_NAME} — AI Desktop Companion")

        self._build_menu()
        self.activated.connect(self._on_tray_activated)

    def _build_menu(self):
        menu = QMenu()

        self.toggle_orb_act = menu.addAction("Hide Orb")
        self.toggle_orb_act.triggered.connect(self._toggle_orb)

        menu.addSeparator()

        self.listen_act = menu.addAction("🎙 Start Listening")
        self.listen_act.triggered.connect(lambda: self.coordinator.trigger_listening())

        self.email_act = menu.addAction("✉ Email & Drafts")
        self.email_act.triggered.connect(self.control_panel.toggle_email_panel)

        self.pause_act = menu.addAction("⏸ Pause Listening")
        self.pause_act.triggered.connect(self._toggle_pause)

        self.stop_speech_act = menu.addAction("⏹ Stop Speaking")
        self.stop_speech_act.triggered.connect(self.coordinator.stop_speaking)

        menu.addSeparator()

        self.startup_act = menu.addAction("Launch at Startup")
        self.startup_act.setCheckable(True)
        self.startup_act.setChecked(is_startup_enabled())
        self.startup_act.triggered.connect(self._toggle_startup)

        menu.addSeparator()

        quit_act = menu.addAction("Quit Jarvis")
        quit_act.triggered.connect(self._quit_application)

        self.setContextMenu(menu)

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._toggle_orb()

    def _toggle_orb(self):
        if self.orb.isVisible():
            self.orb.hide()
            self.control_panel.hide()
            self.toggle_orb_act.setText("Show Orb")
        else:
            self.orb.show()
            self.toggle_orb_act.setText("Hide Orb")

    def _toggle_pause(self):
        self.coordinator.toggle_pause()
        if self.coordinator.is_paused:
            self.pause_act.setText("▶ Resume Listening")
        else:
            self.pause_act.setText("⏸ Pause Listening")

    def _toggle_startup(self, checked: bool):
        success = set_startup_registry(checked)
        self.startup_act.setChecked(is_startup_enabled() if not success else checked)

    def _quit_application(self):
        print("[Desktop UI] Quitting application...")
        self.coordinator.shutdown()
        self.hide()
        QApplication.quit()


def run_desktop_app(check_single_instance: bool = True) -> int:
    """Main entry point to launch Jarvis in desktop orb mode.

    Enforces single-instance execution to prevent duplicate overlapping orbs or tray icons.
    """
    if check_single_instance and sys.platform == "win32":
        if not acquire_single_instance_mutex():
            print("[Desktop UI] Jarvis desktop assistant is already running. Refusing duplicate launch.")
            return 0

    try:
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)

        app.setQuitOnLastWindowClosed(False)

        bridge = UIBridge()

        coordinator = AudioCoordinator(
            on_state_change=lambda state, detail: bridge.state_changed.emit(state.value, detail),
            on_result=lambda detailed, spoken: bridge.result_received.emit(detailed, spoken),
        )

        orb = JarvisOrbWidget(coordinator=coordinator, bridge=bridge)
        panel = CompactControlPanel(orb=orb, coordinator=coordinator, bridge=bridge)
        orb.control_panel = panel

        tray = JarvisTrayIcon(orb=orb, control_panel=panel, coordinator=coordinator)
        tray.show()

        # Initial position: bottom right corner above taskbar
        screen = app.primaryScreen().availableGeometry()
        init_x = screen.right() - orb.width() - 30
        init_y = screen.bottom() - orb.height() - 40
        orb.move(init_x, init_y)
        orb.show()

        # If wake-word detector is available, start standby listening immediately
        if coordinator.wake_detector.is_available:
            coordinator.wake_detector.start_listening()
            print("[Desktop UI] Local 'Hey Jarvis' wake-word detector active.")
        else:
            print("[Desktop UI] Wake-word model not loaded. Click the orb, press Space, or use the panel to talk.")
            print(get_wake_word_setup_instructions())

        exit_code = app.exec()
        coordinator.shutdown()
        return exit_code
    finally:
        if check_single_instance and sys.platform == "win32":
            release_single_instance_mutex()

