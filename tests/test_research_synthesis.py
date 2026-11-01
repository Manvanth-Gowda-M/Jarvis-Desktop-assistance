"""Regression tests for the research → synthesis orchestration bug.

Bug: When the model called tools (e.g. research_web) on every iteration of the
agent loop, the loop exhausted MAX_TOOL_STEPS and returned the hard fallback
"I completed tool executions, but reached the maximum reasoning steps."
without ever letting the model summarise the tool results.

Fix: After the tool-loop exhausts, a final synthesis request is made with
tool_choice="none" so the model composes a natural-language answer from the
accumulated tool results.

All API calls are mocked — these tests consume zero API quota.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from assistant.agent import Agent
from assistant.tools import AVAILABLE_TOOLS
from config import MAX_TOOL_STEPS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RESEARCH_RESULTS = (
    "Web research results for: 'Java inheritance'\n\n"
    "1. Java Inheritance - W3Schools\n"
    "Source: https://www.w3schools.com/java/java_inheritance.asp\n"
    "Summary: Inheritance allows one class to inherit fields and methods from another.\n\n"
    "2. Inheritance in Java - Oracle Docs\n"
    "Source: https://docs.oracle.com/javase/tutorial/java/IandI/subclasses.html\n"
    "Summary: A subclass inherits all public and protected members of its parent class."
)

SYNTHESIS_ANSWER = (
    "## Java Inheritance Summary\n\n"
    "Inheritance in Java allows a child class to reuse fields and methods from a parent class.\n\n"
    "**Sources:**\n"
    "- [W3Schools](https://www.w3schools.com/java/java_inheritance.asp)\n"
    "- [Oracle Docs](https://docs.oracle.com/javase/tutorial/java/IandI/subclasses.html)"
)


def _mock_response(content=None, tool_calls=None):
    """Build a mock requests.Response matching OpenRouter's shape."""
    msg = {"role": "assistant"}
    if content is not None:
        msg["content"] = content
    msg["tool_calls"] = tool_calls or []

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": msg}]}
    return resp


def _fake_research_web(**kwargs):
    """Replacement for the real research_web tool during tests."""
    return RESEARCH_RESULTS


def _fake_research_fail(**kwargs):
    """Simulates a research tool failure."""
    return "Web research temporarily unavailable: Connection error."


# ---------------------------------------------------------------------------
# 1.  Successful research → final synthesis
# ---------------------------------------------------------------------------

class TestResearchSynthesis:
    """The agent must call research_web, get results, then synthesise them."""

    @patch("assistant.agent.requests.post")
    def test_research_then_synthesis(self, mock_post):
        """Step 1: model requests research_web.
        Step 2: model receives results and returns a text summary.
        Verifies the summary contains source URLs from the tool results.
        """
        resp_tool = _mock_response(
            content=None,
            tool_calls=[
                {
                    "id": "call_rw1",
                    "type": "function",
                    "function": {
                        "name": "research_web",
                        "arguments": json.dumps({"query": "Java inheritance"}),
                    },
                }
            ],
        )
        resp_synth = _mock_response(content=SYNTHESIS_ANSWER)
        mock_post.side_effect = [resp_tool, resp_synth]

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            with patch.dict(AVAILABLE_TOOLS, {"research_web": _fake_research_web}):
                agent = Agent()
                result = agent.run(
                    "Research Java inheritance and summarize it with source URLs"
                )

        # Must NOT be the fallback message
        assert "maximum reasoning steps" not in result.lower()
        # Must include content from the synthesis
        assert "inheritance" in result.lower()
        assert "w3schools" in result.lower() or "oracle" in result.lower()

    @patch("assistant.agent.requests.post")
    def test_multi_tool_then_synthesis(self, mock_post):
        """Model calls research_web then several more tools, exhausting the
        tool-loop.  The final synthesis pass (tool_choice='none') should still
        produce a summary.
        """
        resp0 = _mock_response(
            content=None,
            tool_calls=[{
                "id": "call_rw",
                "type": "function",
                "function": {
                    "name": "research_web",
                    "arguments": json.dumps({"query": "Java inheritance"}),
                },
            }],
        )
        resp1 = _mock_response(
            content=None,
            tool_calls=[{
                "id": "call_sn",
                "type": "function",
                "function": {
                    "name": "save_research_note",
                    "arguments": json.dumps({
                        "title": "Java Inheritance",
                        "content": "Summary of Java inheritance research.",
                    }),
                },
            }],
        )
        resp2 = _mock_response(
            content=None,
            tool_calls=[{
                "id": "call_lt",
                "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }],
        )
        resp3 = _mock_response(
            content=None,
            tool_calls=[{
                "id": "call_gd",
                "type": "function",
                "function": {"name": "get_current_date", "arguments": "{}"},
            }],
        )
        resp_final = _mock_response(content=SYNTHESIS_ANSWER)

        mock_post.side_effect = [resp0, resp1, resp2, resp3, resp_final]

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            with patch.dict(AVAILABLE_TOOLS, {"research_web": _fake_research_web}):
                agent = Agent()
                result = agent.run(
                    "Research Java inheritance, save a note, and summarize"
                )

        assert "maximum reasoning steps" not in result.lower()
        assert "inheritance" in result.lower()

    @patch("assistant.agent.requests.post")
    def test_synthesis_pass_uses_tool_choice_none(self, mock_post):
        """Verify the final synthesis API call sets tool_choice='none'."""
        tool_resp = _mock_response(
            content=None,
            tool_calls=[{
                "id": "call_tc",
                "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }],
        )
        synth_resp = _mock_response(content="Final answer.")

        mock_post.side_effect = [tool_resp] * MAX_TOOL_STEPS + [synth_resp]

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            agent = Agent()
            agent.run("test query")

        # The last API call is the synthesis — its payload must set tool_choice="none"
        last_call_kwargs = mock_post.call_args_list[-1]
        payload = last_call_kwargs.kwargs.get("json") or last_call_kwargs[1].get("json")
        assert payload["tool_choice"] == "none"
        # tools key should be absent when tool_choice is "none"
        assert "tools" not in payload

    @patch("assistant.agent.requests.post")
    def test_synthesis_preserves_tool_results_in_messages(self, mock_post):
        """The synthesis request must include all prior tool results so the
        model can reference them in the summary.
        """
        tool_resp = _mock_response(
            content=None,
            tool_calls=[{
                "id": "call_t",
                "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }],
        )
        synth_resp = _mock_response(content="Done.")

        mock_post.side_effect = [tool_resp] * MAX_TOOL_STEPS + [synth_resp]

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            agent = Agent()
            agent.run("do something")

        # Inspect the messages sent in the synthesis call
        last_call_kwargs = mock_post.call_args_list[-1]
        payload = last_call_kwargs.kwargs.get("json") or last_call_kwargs[1].get("json")
        messages = payload["messages"]

        # Must contain tool results from the loop iterations
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == MAX_TOOL_STEPS


# ---------------------------------------------------------------------------
# 2.  Research failure
# ---------------------------------------------------------------------------

class TestResearchFailure:
    """When the research tool itself fails, the model should report the failure
    rather than fabricating results.
    """

    @patch("assistant.agent.requests.post")
    def test_failed_research_reported_honestly(self, mock_post):
        resp_tool = _mock_response(
            content=None,
            tool_calls=[{
                "id": "call_fail",
                "type": "function",
                "function": {
                    "name": "research_web",
                    "arguments": json.dumps({"query": "nonexistent topic xyzzy"}),
                },
            }],
        )
        resp_answer = _mock_response(
            content="I was unable to complete the research due to a connection error."
        )
        mock_post.side_effect = [resp_tool, resp_answer]

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            with patch.dict(AVAILABLE_TOOLS, {"research_web": _fake_research_fail}):
                agent = Agent()
                result = agent.run("Research something obscure")

        assert "unable" in result.lower() or "error" in result.lower()
        # Must not fabricate URLs
        assert "https://" not in result


# ---------------------------------------------------------------------------
# 3.  Quick path — no tools needed
# ---------------------------------------------------------------------------

class TestNoToolPath:
    """When the model answers immediately (no tool calls), no synthesis pass
    should be triggered.
    """

    @patch("assistant.agent.requests.post")
    def test_direct_answer_no_extra_calls(self, mock_post):
        mock_post.return_value = _mock_response(content="Hello there!")

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            agent = Agent()
            result = agent.run("Hi")

        assert result == "Hello there!"
        # Only one API call, no synthesis
        assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# 4.  Synthesis pass failure
# ---------------------------------------------------------------------------

class TestSynthesisFailure:
    """When the synthesis pass itself fails, the hard fallback is returned."""

    @patch("assistant.agent.requests.post")
    def test_synthesis_api_failure_returns_fallback(self, mock_post):
        """Synthesis request returns empty — hard fallback message."""
        tool_resp = _mock_response(
            content=None,
            tool_calls=[{
                "id": "call_x",
                "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }],
        )
        empty_synth = MagicMock()
        empty_synth.status_code = 200
        empty_synth.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": ""}}]
        }

        mock_post.side_effect = [tool_resp] * MAX_TOOL_STEPS + [empty_synth]

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            agent = Agent()
            result = agent.run("Keep calling tools")

        assert "maximum" in result.lower() or "completed" in result.lower()

    @patch("assistant.agent.requests.post")
    def test_synthesis_network_failure_returns_fallback(self, mock_post):
        """Synthesis request raises — hard fallback message."""
        tool_resp = _mock_response(
            content=None,
            tool_calls=[{
                "id": "call_y",
                "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }],
        )
        responses = [tool_resp] * MAX_TOOL_STEPS
        # The synthesis call raises an exception
        responses.append(Exception("Network down"))

        def side_effect(*args, **kwargs):
            val = responses.pop(0)
            if isinstance(val, Exception):
                raise val
            return val

        mock_post.side_effect = side_effect

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey"}):
            agent = Agent()
            result = agent.run("Keep calling tools")

        assert "maximum" in result.lower() or "completed" in result.lower()
