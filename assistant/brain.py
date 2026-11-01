"""Brain module providing compatibility for ask_ai and class Brain."""

from assistant.agent import Agent, Brain, run_agent


def ask_ai(user_message: str) -> str:
    """Send message to AI brain and return the response."""
    return run_agent(user_message)


__all__ = ["Brain", "Agent", "ask_ai", "run_agent"]
