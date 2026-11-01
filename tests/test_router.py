"""Tests for request routing: fast commands vs AI agent delegation."""

import pytest
from unittest.mock import patch, MagicMock

from assistant.router import route_request, route_request_text
from assistant.commands import ActionResult


class TestRouterFastPaths:
    """Verify that fast local commands are handled without calling the AI."""

    @patch("assistant.router.run_agent")
    def test_time_does_not_call_agent(self, mock_agent):
        result = route_request("what time is it")
        assert result.handled is True
        assert "It is currently" in result.detailed_text
        mock_agent.assert_not_called()

    @patch("assistant.router.run_agent")
    def test_exit_does_not_call_agent(self, mock_agent):
        result = route_request("exit")
        assert result.should_exit is True
        mock_agent.assert_not_called()

    @patch("assistant.router.run_agent")
    def test_empty_input_handled(self, mock_agent):
        result = route_request("")
        assert result.handled is True
        mock_agent.assert_not_called()

    @patch("assistant.router.run_agent")
    def test_system_status_handled_locally(self, mock_agent):
        result = route_request("system status")
        assert result.handled is True
        assert "CPU usage is" in result.detailed_text
        mock_agent.assert_not_called()


class TestRouterAIDelegation:
    """Verify that unhandled commands delegate to the AI agent."""

    @patch("assistant.router.run_agent", return_value="The capital of France is Paris.")
    def test_complex_query_delegates_to_agent(self, mock_agent):
        result = route_request("What is the capital of France?")
        assert result.handled is True
        assert "Paris" in result.detailed_text
        mock_agent.assert_called_once()

    @patch("assistant.router.run_agent", return_value="Here is a detailed analysis...")
    def test_spoken_text_is_concise(self, mock_agent):
        result = route_request("explain quantum physics in detail")
        assert result.handled is True
        assert result.spoken_text is not None
        # Spoken text should not contain markdown artifacts
        assert "```" not in result.spoken_text


class TestRouterVolumeControl:
    """Verify that volume commands execute locally without calling the AI agent."""

    @patch("assistant.commands.set_volume", return_value="Volume set to 40%.")
    @patch("assistant.router.run_agent")
    def test_increase_volume_to_percent_bypasses_ai(self, mock_agent, mock_set_vol):
        result = route_request("increase the volume to 40%")
        assert result.handled is True
        assert "Volume set to 40%." in result.detailed_text
        mock_set_vol.assert_called_once_with(40)
        mock_agent.assert_not_called()

    @patch("assistant.commands.set_volume", return_value="Volume set to 40%.")
    @patch("assistant.router.run_agent")
    def test_reduce_volume_to_percent_bypasses_ai(self, mock_agent, mock_set_vol):
        result = route_request("reduce the volume to 40%")
        assert result.handled is True
        assert "Volume set to 40%." in result.detailed_text
        mock_set_vol.assert_called_once_with(40)
        mock_agent.assert_not_called()

    @patch("assistant.commands.set_volume", return_value="Volume set to 30%.")
    @patch("assistant.router.run_agent")
    def test_set_volume_to_percent_bypasses_ai(self, mock_agent, mock_set_vol):
        result = route_request("set volume to 30%")
        assert result.handled is True
        assert "Volume set to 30%." in result.detailed_text
        mock_set_vol.assert_called_once_with(30)
        mock_agent.assert_not_called()

    @patch("assistant.commands.mute_volume", return_value="Audio has been muted.")
    @patch("assistant.router.run_agent")
    def test_mute_volume_bypasses_ai(self, mock_agent, mock_mute):
        result = route_request("mute the volume")
        assert result.handled is True
        assert "Audio has been muted." in result.detailed_text
        mock_mute.assert_called_once_with(True)
        mock_agent.assert_not_called()

    @patch("assistant.commands.mute_volume", return_value="Audio has been unmuted.")
    @patch("assistant.router.run_agent")
    def test_unmute_volume_bypasses_ai(self, mock_agent, mock_mute):
        result = route_request("unmute")
        assert result.handled is True
        assert "Audio has been unmuted." in result.detailed_text
        mock_mute.assert_called_once_with(False)
        mock_agent.assert_not_called()

    @patch("assistant.commands.get_volume", return_value="System volume is currently at 45% (Muted: False).")
    @patch("assistant.router.run_agent")
    def test_get_volume_bypasses_ai(self, mock_agent, mock_get):
        result = route_request("what is the current volume")
        assert result.handled is True
        assert "currently at 45%" in result.detailed_text
        mock_get.assert_called_once()
        mock_agent.assert_not_called()

    @patch("assistant.commands.adjust_volume", return_value="Volume increased by 15% -> now at 65%.")
    @patch("assistant.router.run_agent")
    def test_adjust_volume_by_amount_bypasses_ai(self, mock_agent, mock_adj):
        result = route_request("increase volume by 15%")
        assert result.handled is True
        assert "increased by 15%" in result.detailed_text
        mock_adj.assert_called_once_with("up", 15)
        mock_agent.assert_not_called()

    @patch("assistant.commands.adjust_volume", return_value="Volume decreased by 10% -> now at 40%.")
    @patch("assistant.router.run_agent")
    def test_adjust_volume_relative_default_bypasses_ai(self, mock_agent, mock_adj):
        result = route_request("turn down the volume")
        assert result.handled is True
        assert "decreased by 10%" in result.detailed_text
        mock_adj.assert_called_once_with("down", 10)
        mock_agent.assert_not_called()

    @patch("assistant.router.run_agent", return_value="Volume is V = pi * r^2 * h.")
    def test_math_volume_query_delegates_to_agent(self, mock_agent):
        result = route_request("Calculate the volume of a cylinder with radius 3 and height 5")
        assert result.handled is True
        mock_agent.assert_called_once()

    @patch("assistant.router.run_agent", return_value="Check your audio drivers or default playback device.")
    def test_conversational_volume_query_delegates_to_agent(self, mock_agent):
        result = route_request("Why is my volume not working properly?")
        assert result.handled is True
        mock_agent.assert_called_once()


class TestRouterBrightnessControl:
    """Verify that brightness commands execute locally without calling the AI agent."""

    @patch("assistant.commands.adjust_brightness", return_value="Brightness increased by 10% -> now at 70%.")
    @patch("assistant.router.run_agent")
    def test_increase_brightness_bypasses_ai(self, mock_agent, mock_adj):
        result = route_request("increase brightness")
        assert result.handled is True
        assert "increased by 10%" in result.detailed_text
        mock_adj.assert_called_once_with("up", 10)
        mock_agent.assert_not_called()

    @patch("assistant.commands.adjust_brightness", return_value="Brightness decreased by 10% -> now at 50%.")
    @patch("assistant.router.run_agent")
    def test_decrease_brightness_bypasses_ai(self, mock_agent, mock_adj):
        result = route_request("decrease brightness")
        assert result.handled is True
        assert "decreased by 10%" in result.detailed_text
        mock_adj.assert_called_once_with("down", 10)
        mock_agent.assert_not_called()

    @patch("assistant.commands.set_brightness", return_value="Screen brightness set to 60%.")
    @patch("assistant.router.run_agent")
    def test_set_brightness_to_percent_bypasses_ai(self, mock_agent, mock_set):
        result = route_request("set brightness to 60 percent")
        assert result.handled is True
        assert "Screen brightness set to 60%." in result.detailed_text
        mock_set.assert_called_once_with(60)
        mock_agent.assert_not_called()

    @patch("assistant.commands.adjust_brightness", return_value="Brightness increased by 10% -> now at 80%.")
    @patch("assistant.router.run_agent")
    def test_make_screen_brighter_bypasses_ai(self, mock_agent, mock_adj):
        result = route_request("make the screen brighter")
        assert result.handled is True
        assert "increased by 10%" in result.detailed_text
        mock_adj.assert_called_once_with("up", 10)
        mock_agent.assert_not_called()

    @patch("assistant.commands.adjust_brightness", return_value="Brightness decreased by 10% -> now at 40%.")
    @patch("assistant.router.run_agent")
    def test_dim_the_screen_bypasses_ai(self, mock_agent, mock_adj):
        result = route_request("dim the screen")
        assert result.handled is True
        assert "decreased by 10%" in result.detailed_text
        mock_adj.assert_called_once_with("down", 10)
        mock_agent.assert_not_called()

    @patch("assistant.commands.get_brightness", return_value="Current screen brightness is 65%.")
    @patch("assistant.router.run_agent")
    def test_get_brightness_bypasses_ai(self, mock_agent, mock_get):
        result = route_request("what is the current brightness")
        assert result.handled is True
        assert "65%" in result.detailed_text
        mock_get.assert_called_once()
        mock_agent.assert_not_called()

    @patch("assistant.commands.set_brightness", return_value="Screen brightness control is not supported on this display hardware.")
    @patch("assistant.router.run_agent")
    def test_unsupported_hardware_handled_safely(self, mock_agent, mock_set):
        result = route_request("set brightness to 70 percent")
        assert result.handled is True
        assert "not supported" in result.detailed_text
        mock_set.assert_called_once_with(70)
        mock_agent.assert_not_called()

    @patch("assistant.commands.set_brightness", return_value="Invalid brightness level 150. Brightness must be between 0 and 100.")
    @patch("assistant.router.run_agent")
    def test_invalid_brightness_value_handled_safely(self, mock_agent, mock_set):
        result = route_request("set brightness to 150 percent")
        assert result.handled is True
        assert "Invalid brightness level" in result.detailed_text
        mock_set.assert_called_once_with(150)
        mock_agent.assert_not_called()

    @patch("assistant.router.run_agent", return_value="Flickering can be caused by display drivers or hardware refresh rate.")
    def test_conversational_brightness_query_delegates_to_agent(self, mock_agent):
        result = route_request("Why is the screen brightness flickering?")
        assert result.handled is True
        mock_agent.assert_called_once()


class TestRouteRequestText:
    """Verify the string-returning convenience wrapper."""

    @patch("assistant.router.run_agent", return_value="AI response here.")
    def test_returns_string(self, mock_agent):
        result = route_request_text("tell me something")
        assert isinstance(result, str)
        assert "AI response here." in result

