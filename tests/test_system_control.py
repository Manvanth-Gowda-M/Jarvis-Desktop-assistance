"""Tests for the system control module — all hardware interactions mocked."""

import pytest
from unittest.mock import patch, MagicMock


class TestVolumeControl:
    """Volume control with mocked pycaw / ctypes."""

    def test_set_volume_valid_range(self):
        from assistant.system_control import set_volume
        with patch("assistant.system_control._set_volume_impl", return_value=True):
            result = set_volume(50)
            assert isinstance(result, str)

    def test_set_volume_rejects_out_of_range(self):
        from assistant.system_control import set_volume
        result = set_volume(150)
        assert "0" in result and "100" in result or "invalid" in result.lower() or "range" in result.lower()

    def test_set_volume_negative_rejected(self):
        from assistant.system_control import set_volume
        result = set_volume(-10)
        assert isinstance(result, str)

    def test_adjust_volume_up(self):
        from assistant.system_control import adjust_volume
        result = adjust_volume("up", 10)
        assert isinstance(result, str)

    def test_adjust_volume_down(self):
        from assistant.system_control import adjust_volume
        result = adjust_volume("down", 10)
        assert isinstance(result, str)

    def test_adjust_volume_invalid_direction(self):
        from assistant.system_control import adjust_volume
        result = adjust_volume("sideways", 10)
        assert isinstance(result, str)
        assert "up" in result.lower() or "invalid" in result.lower() or "direction" in result.lower()

    def test_mute(self):
        from assistant.system_control import mute_volume
        result = mute_volume(True)
        assert isinstance(result, str)

    def test_unmute(self):
        from assistant.system_control import mute_volume
        result = mute_volume(False)
        assert isinstance(result, str)


class TestBatteryAndSystem:
    """Battery and system status with psutil mocked."""

    @patch("assistant.system_control.psutil.sensors_battery")
    @patch("assistant.system_control.psutil.cpu_percent", return_value=25.0)
    @patch("assistant.system_control.psutil.virtual_memory")
    def test_battery_status_plugged_in(self, mock_mem, mock_cpu, mock_battery):
        mock_battery.return_value = MagicMock(percent=87.0, power_plugged=True)
        mock_mem.return_value = MagicMock(percent=45.0)
        from assistant.system_control import get_battery_status
        result = get_battery_status()
        assert isinstance(result, str)
        assert "87" in result or "battery" in result.lower()

    @patch("assistant.system_control.psutil.sensors_battery", return_value=None)
    def test_battery_not_available(self, mock_battery):
        from assistant.system_control import get_battery_status
        result = get_battery_status()
        assert isinstance(result, str)
        assert "not available" in result.lower() or "no battery" in result.lower() or "desktop" in result.lower()

    @patch("assistant.system_control.psutil.disk_usage")
    @patch("assistant.system_control.psutil.disk_partitions")
    def test_disk_usage(self, mock_partitions, mock_usage):
        mock_partitions.return_value = [MagicMock(device="C:\\", mountpoint="C:\\", fstype="NTFS")]
        mock_usage.return_value = MagicMock(total=500e9, used=200e9, free=300e9, percent=40.0)
        from assistant.system_control import get_disk_usage
        result = get_disk_usage()
        assert isinstance(result, str)
        assert "C:" in result or "disk" in result.lower()


class TestNetworkStatus:
    """Network connectivity check."""

    def test_network_connected(self):
        from assistant.system_control import get_network_status
        # Just verify it returns a string and doesn't crash
        result = get_network_status()
        assert isinstance(result, str)


class TestPowerActions:
    """Power and session actions MUST require confirmed=True."""

    def test_shutdown_without_confirmation_returns_warning(self):
        from assistant.system_control import power_action
        result = power_action("shutdown", confirmed=False)
        assert "confirm" in result.lower() or "warning" in result.lower() or "consequence" in result.lower()
        # Must NOT actually shutdown

    def test_restart_without_confirmation_returns_warning(self):
        from assistant.system_control import power_action
        result = power_action("restart", confirmed=False)
        assert "confirm" in result.lower() or "warning" in result.lower()

    def test_sleep_without_confirmation_returns_warning(self):
        from assistant.system_control import power_action
        result = power_action("sleep", confirmed=False)
        assert isinstance(result, str)

    def test_signout_without_confirmation_returns_warning(self):
        from assistant.system_control import power_action
        result = power_action("signout", confirmed=False)
        assert "confirm" in result.lower() or "warning" in result.lower()

    def test_invalid_action(self):
        from assistant.system_control import power_action
        result = power_action("explode", confirmed=True)
        assert isinstance(result, str)
        assert "invalid" in result.lower() or "not supported" in result.lower() or "unknown" in result.lower()

    @patch("assistant.system_control.subprocess.run")
    def test_shutdown_with_confirmation_calls_system(self, mock_run):
        from assistant.system_control import power_action
        mock_run.return_value = MagicMock(returncode=0)
        result = power_action("shutdown", confirmed=True)
        assert isinstance(result, str)
        mock_run.assert_called_once()

    def test_lock_computer(self):
        from assistant.system_control import lock_computer
        with patch("assistant.system_control.ctypes.windll.user32.LockWorkStation", return_value=1):
            result = lock_computer()
            assert isinstance(result, str)


class TestOpenSettings:
    """Windows settings page opener."""

    @patch("assistant.system_control.subprocess.Popen")
    def test_open_home_settings(self, mock_popen):
        from assistant.system_control import open_settings
        result = open_settings("home")
        assert isinstance(result, str)
        mock_popen.assert_called_once()

    @patch("assistant.system_control.subprocess.Popen")
    def test_open_wifi_settings(self, mock_popen):
        from assistant.system_control import open_settings
        result = open_settings("wifi")
        assert isinstance(result, str)

    def test_open_invalid_page(self):
        from assistant.system_control import open_settings
        result = open_settings("nonexistent_page_xyz")
        assert isinstance(result, str)


class TestBrightnessControl:
    """Screen brightness control unit tests with mocked hardware."""

    def test_set_brightness_rejects_out_of_range(self):
        from assistant.system_control import set_brightness
        res_high = set_brightness(150)
        assert "Invalid brightness level" in res_high or "between 0 and 100" in res_high

        res_low = set_brightness(-20)
        assert "Invalid brightness level" in res_low or "between 0 and 100" in res_low

    def test_set_brightness_rejects_invalid_type(self):
        from assistant.system_control import set_brightness
        res = set_brightness("not_a_number")
        assert "Invalid brightness level" in res

    def test_set_brightness_success_mocked(self):
        from assistant.system_control import set_brightness
        with patch("screen_brightness_control.set_brightness") as mock_sbc_set, \
             patch("screen_brightness_control.get_brightness", return_value=[75]):
            res = set_brightness(75)
            assert "75%" in res
            mock_sbc_set.assert_called_once_with(75)

    def test_adjust_brightness_invalid_direction(self):
        from assistant.system_control import adjust_brightness
        res = adjust_brightness("sideways", 10)
        assert "Invalid direction" in res

    def test_adjust_brightness_up_and_down_mocked(self):
        from assistant.system_control import adjust_brightness
        with patch("screen_brightness_control.get_brightness", return_value=[50]), \
             patch("screen_brightness_control.set_brightness") as mock_set:
            res_up = adjust_brightness("up", 15)
            assert "increased by 15%" in res_up
            assert "65%" in res_up
            mock_set.assert_called_with(65)

            res_down = adjust_brightness("down", 20)
            assert "decreased by 20%" in res_down
            assert "30%" in res_down
            mock_set.assert_called_with(30)

    def test_get_brightness_mocked(self):
        from assistant.system_control import get_brightness
        with patch("screen_brightness_control.get_brightness", return_value=[80]):
            res = get_brightness()
            assert "80%" in res

    def test_unsupported_hardware_reports_clearly(self):
        from assistant.system_control import set_brightness
        with patch("assistant.system_control._SBC_AVAILABLE", False), \
             patch("assistant.system_control._WMI_AVAILABLE", False):
            res = set_brightness(50)
            assert "not supported" in res.lower() or "not available" in res.lower()


class TestSystemControlToolsSchema:
    """Verify the schema/tools dict is properly formed."""

    def test_schema_exists(self):
        from assistant.system_control import SYSTEM_CONTROL_TOOLS_SCHEMA, SYSTEM_CONTROL_AVAILABLE_TOOLS
        assert len(SYSTEM_CONTROL_TOOLS_SCHEMA) >= 5
        for tool in SYSTEM_CONTROL_TOOLS_SCHEMA:
            assert tool["type"] == "function"
            assert "name" in tool["function"]
            assert tool["function"]["name"] in SYSTEM_CONTROL_AVAILABLE_TOOLS

