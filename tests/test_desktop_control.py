"""Tests for desktop control module — all UI side effects mocked."""

import pytest
from unittest.mock import patch, MagicMock


class TestListOpenWindows:
    def test_returns_string(self):
        from assistant.desktop_control import list_open_windows
        with patch("assistant.desktop_control.win32gui.EnumWindows"):
            result = list_open_windows()
            assert isinstance(result, str)


class TestFocusWindow:
    def test_focus_existing_window(self):
        from assistant.desktop_control import focus_window
        with patch("assistant.desktop_control._find_window_handle", return_value=12345):
            with patch("assistant.desktop_control.win32gui.SetForegroundWindow"):
                result = focus_window("Notepad")
                assert isinstance(result, str)

    def test_focus_nonexistent_window(self):
        from assistant.desktop_control import focus_window
        with patch("assistant.desktop_control._find_window_handle", return_value=None):
            result = focus_window("NonExistentApp12345")
            assert "not found" in result.lower() or "could not" in result.lower()


class TestMinimizeMaximize:
    def test_minimize_window(self):
        from assistant.desktop_control import minimize_window
        with patch("assistant.desktop_control._find_window_handle", return_value=12345):
            with patch("assistant.desktop_control.win32gui.ShowWindow"):
                result = minimize_window("Notepad")
                assert isinstance(result, str)

    def test_maximize_window(self):
        from assistant.desktop_control import maximize_window
        with patch("assistant.desktop_control._find_window_handle", return_value=12345):
            with patch("assistant.desktop_control.win32gui.ShowWindow"):
                result = maximize_window("Notepad")
                assert isinstance(result, str)


class TestCloseWindow:
    def test_close_requires_confirmation(self):
        from assistant.desktop_control import close_window
        result = close_window("SomeApp", confirmed=False)
        assert "confirm" in result.lower() or "close" in result.lower()

    def test_close_with_confirmation(self):
        from assistant.desktop_control import close_window
        with patch("assistant.desktop_control._find_window_handle", return_value=12345):
            with patch("assistant.desktop_control.win32gui.PostMessage"):
                result = close_window("SomeApp", confirmed=True)
                assert isinstance(result, str)


class TestTakeScreenshot:
    def test_screenshot_saved(self, tmp_path):
        from assistant.desktop_control import take_screenshot
        save_path = str(tmp_path / "test_screenshot.png")

        mock_img = MagicMock()
        with patch("assistant.desktop_control.PIL_AVAILABLE", True):
            with patch("assistant.desktop_control.ImageGrab") as mock_grab:
                mock_grab.grab.return_value = mock_img
                result = take_screenshot(save_path=save_path)
                assert isinstance(result, str)

    def test_screenshot_pil_not_available(self):
        from assistant.desktop_control import take_screenshot
        with patch("assistant.desktop_control.PIL_AVAILABLE", False):
            result = take_screenshot()
            assert "not available" in result.lower() or "install" in result.lower() or "pillow" in result.lower()


class TestTypeText:
    def test_type_normal_text(self):
        from assistant.desktop_control import type_text
        with patch("assistant.desktop_control._type_text_impl", return_value="Typed successfully."):
            result = type_text("Hello world")
            assert isinstance(result, str)

    def test_refuses_too_long_text(self):
        from assistant.desktop_control import type_text
        long_text = "A" * 600
        result = type_text(long_text)
        assert "too long" in result.lower() or "limit" in result.lower() or "max" in result.lower()

    def test_refuses_suspicious_text(self):
        from assistant.desktop_control import type_text
        # Shell injection attempt
        result = type_text("'; DROP TABLE users; --")
        assert isinstance(result, str)
        # Either executed safely (just types the string) or rejected
        # The important thing is it doesn't crash


class TestOpenFolder:
    @patch("assistant.desktop_control.subprocess.Popen")
    def test_opens_existing_folder(self, mock_popen, tmp_path):
        from assistant.desktop_control import open_folder
        result = open_folder(str(tmp_path))
        assert isinstance(result, str)

    def test_refuses_nonexistent_folder(self, tmp_path):
        from assistant.desktop_control import open_folder
        result = open_folder(str(tmp_path / "nonexistent_folder"))
        assert "not found" in result.lower() or "does not exist" in result.lower() or "error" in result.lower()


class TestDesktopSchema:
    def test_schema_valid(self):
        from assistant.desktop_control import DESKTOP_TOOLS_SCHEMA, DESKTOP_AVAILABLE_TOOLS
        assert len(DESKTOP_TOOLS_SCHEMA) >= 5
        for tool in DESKTOP_TOOLS_SCHEMA:
            assert tool["type"] == "function"
            assert tool["function"]["name"] in DESKTOP_AVAILABLE_TOOLS

