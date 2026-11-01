from assistant.commands import execute_fast_command, ActionResult
from assistant.agent import run_agent
from assistant.speech import prepare_spoken_answer


def route_request(command: str) -> ActionResult:
    """Route a user request to fast local actions first, then to the AI agent if needed."""
    command = str(command).strip()
    if not command:
        return ActionResult(
            handled=True,
            detailed_text="I did not hear any command.",
            spoken_text="I did not hear any command.",
        )

    # 1. Check Fast Local Commands (zero LLM latency and zero API cost)
    fast_result = execute_fast_command(command)
    if fast_result.handled:
        return fast_result

    # 2. Delegate to the OpenRouter Free-Model AI Agent
    ai_response = run_agent(command)
    spoken_summary = prepare_spoken_answer(ai_response)

    return ActionResult(
        handled=True,
        detailed_text=ai_response,
        spoken_text=spoken_summary,
    )


def route_request_text(command: str) -> str:
    """Convenience string-returning router for procedural callers."""
    res = route_request(command)
    return res.detailed_text
