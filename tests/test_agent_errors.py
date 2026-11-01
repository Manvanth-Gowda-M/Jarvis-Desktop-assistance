"""Tests for agent error handling: malformed tool calls, API failures, bounded iterations."""

import json
import pytest
from unittest.mock import patch, MagicMock, call

from assistant.agent import Agent
from config import validate_free_model, MAX_TOOL_STEPS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_call_response(tool_name, arguments="{}", call_id="call_1"):
    """Build a mock OpenRouter response that requests a single tool call."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": arguments,
                            },
                        }
                    ],
                }
            }
        ]
    }
    return resp


def _make_text_response(text):
    """Build a mock OpenRouter response with a plain text answer (no tool calls)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": [],
                }
            }
        ]
    }
    return resp


# ---------------------------------------------------------------------------
# Malformed tool calls
# ---------------------------------------------------------------------------

class TestAgentMalformedToolCalls:
    """Verify the agent handles broken or unexpected tool call data gracefully."""

    @patch("assistant.agent.requests.post")
    def test_malformed_json_arguments(self, mock_post):
        """Tool call with invalid JSON in arguments should not crash."""
        mock_post.side_effect = [
            _make_tool_call_response("get_current_time", "{not valid json!!!"),
            _make_text_response("Here is the result."),
        ]

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            agent = Agent()
            result = agent.run("What time is it?")
            assert isinstance(result, str)

    @patch("assistant.agent.requests.post")
    def test_unknown_tool_name(self, mock_post):
        """Tool call to a non-existent tool should return a clear error, not crash."""
        mock_post.side_effect = [
            _make_tool_call_response("nonexistent_tool_xyz"),
            _make_text_response("I could not find that tool."),
        ]

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            agent = Agent()
            result = agent.run("Do something impossible")
            assert isinstance(result, str)

    @patch("assistant.agent.requests.post")
    def test_empty_choices_returns_graceful_message(self, mock_post):
        """API returns empty choices list."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": []}
        mock_post.return_value = mock_resp

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            agent = Agent()
            result = agent.run("Hello")
            assert "valid response" in result.lower() or "didn't receive" in result.lower()


# ---------------------------------------------------------------------------
# Bounded iterations
# ---------------------------------------------------------------------------

class TestAgentBoundedIterations:
    """Verify the agent does not loop forever on repeated tool calls."""

    @patch("assistant.agent.requests.post")
    def test_max_tool_steps_then_synthesis(self, mock_post):
        """When the model keeps calling tools until MAX_TOOL_STEPS, a final
        synthesis pass with tool_choice='none' must produce a summary."""

        # Every iteration inside the loop returns a tool call
        tool_resp = _make_tool_call_response("get_current_time")
        # The synthesis pass returns a final text answer
        synth_resp = _make_text_response("Here is the summary of all results.")

        mock_post.side_effect = [tool_resp] * MAX_TOOL_STEPS + [synth_resp]

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            agent = Agent()
            result = agent.run("Keep calling tools")
            assert "summary" in result.lower()

    @patch("assistant.agent.requests.post")
    def test_synthesis_failure_returns_fallback(self, mock_post):
        """If even the synthesis pass fails, the hard fallback message appears."""

        tool_resp = _make_tool_call_response("get_current_time")
        # Synthesis pass returns empty content
        empty_resp = MagicMock()
        empty_resp.status_code = 200
        empty_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": ""}}]
        }

        mock_post.side_effect = [tool_resp] * MAX_TOOL_STEPS + [empty_resp]

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            agent = Agent()
            result = agent.run("Keep calling tools")
            assert "maximum" in result.lower() or "completed" in result.lower()


# ---------------------------------------------------------------------------
# API failures
# ---------------------------------------------------------------------------

class TestAgentAPIFailures:
    """Verify the agent handles HTTP and network failures."""

    @patch("assistant.agent.requests.post")
    def test_http_429_rate_limit(self, mock_post):
        """All models rate limited — should return helpful error."""
        mock_post.side_effect = RuntimeError(
            "Rate limited (HTTP 429) on model 'openrouter/free'."
        )

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            agent = Agent()
            result = agent.run("Hello")
            assert "couldn't reach" in result.lower() or "rate limit" in result.lower()

    @patch("assistant.agent.requests.post", side_effect=Exception("Connection refused"))
    def test_total_network_failure(self, mock_post):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            agent = Agent()
            result = agent.run("Hello")
            assert "couldn't reach" in result.lower()

    def test_empty_input(self):
        agent = Agent()
        result = agent.run("")
        assert result == "How can I assist you today?"

    def test_missing_api_key(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}):
            agent = Agent()
            result = agent.run("Hello")
            assert "API" in result or "couldn't reach" in result.lower()


# ---------------------------------------------------------------------------
# Free model validation
# ---------------------------------------------------------------------------

class TestFreeModelValidation:
    """Additional model validation edge cases."""

    def test_whitespace_model_name(self):
        result = validate_free_model("  openrouter/free  ")
        assert result == "openrouter/free"

    def test_none_returns_default(self):
        result = validate_free_model(None)
        assert result.endswith(":free") or result == "openrouter/free"

    def test_empty_string_returns_default(self):
        result = validate_free_model("")
        assert result.endswith(":free") or result == "openrouter/free"
