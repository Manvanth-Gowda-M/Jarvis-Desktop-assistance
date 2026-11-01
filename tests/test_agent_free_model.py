import pytest
from unittest.mock import patch, MagicMock

from config import validate_free_model, DEFAULT_FREE_MODEL
from assistant.agent import Agent


def test_free_model_validation_valid():
    # Valid free models
    assert validate_free_model("meta-llama/llama-3.3-70b-instruct:free") == "meta-llama/llama-3.3-70b-instruct:free"
    assert validate_free_model("openrouter/free") == "openrouter/free"
    assert validate_free_model("google/gemini-2.0-flash-exp:free") == "google/gemini-2.0-flash-exp:free"
    assert validate_free_model(None) == DEFAULT_FREE_MODEL


def test_free_model_validation_rejects_paid_models():
    # Attempting to use paid models must raise ValueError
    paid_models = [
        "openai/gpt-4o",
        "anthropic/claude-3.5-sonnet",
        "meta-llama/llama-3-70b-instruct",
        "mistralai/mistral-large",
    ]
    for model in paid_models:
        with pytest.raises(ValueError) as excinfo:
            validate_free_model(model)
        assert "not an approved free OpenRouter model" in str(excinfo.value)


@patch("assistant.agent.requests.post")
def test_agent_simple_response(mock_post):
    # Mocking successful OpenRouter response without tool calls
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello! I am ready to help you.",
                    "tool_calls": [],
                }
            }
        ]
    }
    mock_post.return_value = mock_response

    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey12345678"}):
        agent = Agent()
        result = agent.run("Hello there")
        assert result == "Hello! I am ready to help you."


@patch("assistant.agent.requests.post")
def test_agent_tool_call_flow(mock_post):
    # Step 1: Assistant requests tool get_current_time
    mock_resp1 = MagicMock()
    mock_resp1.status_code = 200
    mock_resp1.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "get_current_time",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            }
        ]
    }

    # Step 2: After tool result, assistant gives final answer
    mock_resp2 = MagicMock()
    mock_resp2.status_code = 200
    mock_resp2.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "The current time is 03:30 PM.",
                    "tool_calls": [],
                }
            }
        ]
    }

    mock_post.side_effect = [mock_resp1, mock_resp2]

    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey12345678"}):
        agent = Agent()
        result = agent.run("What time is it right now?")
        assert "The current time is 03:30 PM." in result
        assert mock_post.call_count == 2


def test_allowed_free_models_strictness():
    from config import ALLOWED_FREE_MODELS, CONFIGURED_MODEL
    # None of the allowed models should be paid
    for model in ALLOWED_FREE_MODELS:
        assert model == "openrouter/free" or model.endswith(":free")

    # Stale broken model should not be in ALLOWED_FREE_MODELS
    assert "meta-llama/llama-3.3-70b-instruct:free" not in ALLOWED_FREE_MODELS
    assert CONFIGURED_MODEL == "openrouter/free" or CONFIGURED_MODEL.endswith(":free")


@patch("assistant.agent.requests.post")
def test_agent_handles_http_404_gracefully(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "Model unavailable for free"
    mock_post.return_value = mock_resp

    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey12345678"}):
        agent = Agent()
        result = agent.run("Hello")
        assert "I couldn't reach the AI service right now." in result
        assert "HTTP 404" in result or "unavailable" in result
        # Verify it did not try any paid models
        for call_args in mock_post.call_args_list:
            payload = call_args.kwargs.get("json", {})
            model = payload.get("model", "")
            assert model == "openrouter/free" or model.endswith(":free")


@patch("assistant.agent.requests.post")
def test_agent_handles_http_429_rate_limit(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "Rate limited"
    mock_post.return_value = mock_resp

    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-testkey12345678"}):
        agent = Agent()
        result = agent.run("Hello")
        assert "I couldn't reach the AI service right now." in result
        assert "HTTP 429" in result or "Rate limited" in result
