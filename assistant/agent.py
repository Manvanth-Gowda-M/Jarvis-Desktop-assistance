import json
import os
import requests

from config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    CONFIGURED_MODEL,
    ALLOWED_FREE_MODELS,
    validate_free_model,
    API_TIMEOUT,
    MAX_TOOL_STEPS,
    JARVIS_NAME,
)
from assistant.tools import TOOLS_SCHEMA, AVAILABLE_TOOLS


SYSTEM_PROMPT = f"""
You are {JARVIS_NAME}, an intelligent and helpful personal AI assistant on Windows with advanced system control and desktop automation capabilities.

Capabilities and Tool Guidelines:
1. Natural Understanding: Understand the user's intent and select appropriate tools. You can chain multiple tool calls when needed.
2. System Control & Hardware:
   - Audio: 'set_volume' (0-100), 'adjust_volume' (up/down), 'mute_volume', 'get_volume'.
   - Hardware & Network: 'get_battery_status', 'get_disk_usage', 'get_network_status', 'set_brightness'.
   - Windows Settings & Session: 'open_settings', 'lock_computer', 'power_action' (shutdown/restart/sleep/signout).
3. Desktop & Windows Automation:
   - Windows: 'list_open_windows', 'focus_window', 'minimize_window', 'maximize_window', 'close_window'.
   - Input & Exploration: 'take_screenshot' (PNG capture), 'type_text' (type into active window), 'open_folder' (open in Explorer).
4. Safe File Management:
   - Operations: 'search_files', 'list_directory', 'read_file_content', 'create_folder', 'create_text_file', 'copy_file', 'move_file', 'rename_file', 'delete_to_recycle' (uses Recycle Bin), 'find_duplicates', 'open_file' (opens with default app; blocked for executables).
   - Security: Strictly restricted to approved roots. Path traversal is rejected.
5. Email Assistant (Strict Draft-Only):
   - Drafts: 'compose_email_draft' (creates clear, professional draft and saves directly to Gmail or Outlook Drafts folder; NEVER sends automatically), 'save_draft_to_provider', 'show_email_draft', 'list_email_drafts', 'get_email_provider_status', 'get_email_setup_instructions'.
   - Strictly draft creation and review only. Jarvis never sends emails.
   - Requires explicit recipient address; ask if missing. Never fabricate recipient emails.
6. Research, Memory & Applications:
   - Web Research: 'research_web' for current information. Synthesize findings with source URLs.
   - Memory: 'remember_fact', 'recall_facts', 'list_memories' for long-term SQLite memory.
   - Notes: 'save_research_note', 'read_research_note', 'list_research_notes'.
   - Applications: 'open_application' (Chrome, Notepad, Calculator, VS Code, etc.).
7. Safety, Approvals & Confirmation Rules:
   - High-impact and destructive actions REQUIRE explicit confirmation before execution:
     * 'power_action' (shutdown, restart, sleep, signout) requires confirmed=True.
     * 'delete_to_recycle', 'move_file', 'rename_file', and 'close_window' require confirmed=True.
   - If the user hasn't confirmed, run the tool with confirmed=False (which provides a preview/confirmation request) or ask the user to confirm.
   - Never fabricate results. Report only verified tool outputs.
   - Never disclose or log secrets, API keys, or passwords.
8. Communication Style:
   - Voice/Spoken output should be clear, concise, and conversational.
   - Detailed text, lists, and URLs are welcomed in written responses.
"""


class Agent:
    """OpenRouter tool-calling agent strictly enforcing free model usage."""

    def __init__(self, model: str = CONFIGURED_MODEL):
        # Strictly validate that the model is free
        self.model = validate_free_model(model)
        self.conversation_history = []
        self.api_url = f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"

    def _get_api_key(self) -> str:
        key = os.getenv("OPENROUTER_API_KEY", OPENROUTER_API_KEY)
        if not key:
            raise ValueError(
                "OPENROUTER_API_KEY is missing. Please set it in your .env file."
            )
        return key.strip()

    def _call_openrouter(
        self,
        messages: list[dict],
        model: str,
        tool_choice: str = "auto",
    ) -> dict:
        """Execute a POST request to OpenRouter with timeout and headers.

        Args:
            messages: Conversation messages to send.
            model: The model identifier (must be a free model).
            tool_choice: "auto" lets the model decide, "none" forces a text reply.
        """
        headers = {
            "Authorization": f"Bearer {self._get_api_key()}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/jarvis-assistant",
            "X-Title": "Jarvis Personal Assistant",
        }

        payload = {
            "model": model,
            "messages": messages,
            "tool_choice": tool_choice,
        }

        # Only include tool schemas when the model is allowed to call tools
        if tool_choice != "none":
            payload["tools"] = TOOLS_SCHEMA

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=API_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            raise RuntimeError(f"OpenRouter API request timed out after {API_TIMEOUT}s on model '{model}'.")
        except requests.exceptions.RequestException as err:
            raise RuntimeError(f"OpenRouter network error on model '{model}': {err}")

        if response.status_code == 404:
            raise RuntimeError(f"Model '{model}' is currently unavailable on OpenRouter (HTTP 404).")

        if response.status_code == 429:
            detail = ""
            try:
                err_json = response.json()
                detail = err_json.get("error", {}).get("message", "")
            except Exception:
                pass
            if detail:
                raise RuntimeError(f"Rate limited (HTTP 429) on model '{model}': {detail}")
            raise RuntimeError(f"Rate limited (HTTP 429) on model '{model}'.")

        if response.status_code != 200:
            raise RuntimeError(
                f"OpenRouter API returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            return response.json()
        except Exception as json_err:
            raise RuntimeError(f"Failed to parse OpenRouter response as JSON: {json_err}")

    def _try_models(
        self,
        messages: list[dict],
        active_model: str,
        tool_choice: str = "auto",
    ) -> tuple[dict | None, str, Exception | None]:
        """Try the active model then fallbacks. Returns (data, model_used, last_error)."""
        candidates = []
        for m in [active_model] + ALLOWED_FREE_MODELS:
            if m and m not in candidates:
                candidates.append(m)

        last_error = None
        for candidate in candidates:
            try:
                validate_free_model(candidate)
            except ValueError as val_err:
                last_error = val_err
                continue

            try:
                data = self._call_openrouter(messages, candidate, tool_choice=tool_choice)
                return data, candidate, None
            except Exception as err:
                last_error = err
        return None, active_model, last_error

    def run(self, user_message: str) -> str:
        """Run the multi-step agent loop using free models only.

        The loop allows up to MAX_TOOL_STEPS iterations of tool calling.
        After the loop (whether it finished via a tool call or exhausted all
        iterations), a final *synthesis* request with tool_choice="none" is
        made so the model can summarize the collected tool results into a
        natural-language answer.
        """
        user_message = str(user_message).strip()
        if not user_message:
            return "How can I assist you today?"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self.conversation_history,
            {"role": "user", "content": user_message},
        ]

        active_model = self.model
        executed_any_tools = False

        # ---- Tool-calling loop ----
        for step in range(MAX_TOOL_STEPS):
            data, active_model, last_error = self._try_models(
                messages, active_model,
            )

            if not data:
                return (
                    f"I couldn't reach the AI service right now. "
                    f"Details: {last_error or 'Free model service temporarily busy.'}"
                )

            choices = data.get("choices", [])
            if not choices:
                return "I didn't receive a valid response from the AI model."

            assistant_msg = choices[0].get("message", {})
            tool_calls = assistant_msg.get("tool_calls", [])

            # No tool calls → this IS the final answer
            if not tool_calls:
                answer = assistant_msg.get("content") or "I've completed processing."

                self.conversation_history.extend([
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": answer},
                ])
                if len(self.conversation_history) > 12:
                    self.conversation_history = self.conversation_history[-12:]

                return answer

            # Append the assistant's tool-call message to the conversation
            messages.append(assistant_msg)

            # Execute every requested tool call
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name")
                raw_args = fn.get("arguments", "{}")

                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}

                tool_func = AVAILABLE_TOOLS.get(name)
                if not tool_func:
                    result = f"Error: Tool '{name}' is not recognized."
                else:
                    try:
                        result = tool_func(**args)
                    except Exception as e:
                        result = f"Tool '{name}' execution failed: {e}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": str(result),
                })

            executed_any_tools = True

        # ---- Final synthesis pass ----
        # The loop exhausted MAX_TOOL_STEPS without the model giving a text-only
        # answer.  The last tool results are sitting in `messages` but the model
        # never had a chance to read them and compose a response.
        # Force a text answer by setting tool_choice="none".
        if executed_any_tools:
            data, active_model, last_error = self._try_models(
                messages, active_model, tool_choice="none",
            )

            if data:
                choices = data.get("choices", [])
                if choices:
                    answer = choices[0].get("message", {}).get("content", "")
                    if answer:
                        self.conversation_history.extend([
                            {"role": "user", "content": user_message},
                            {"role": "assistant", "content": answer},
                        ])
                        if len(self.conversation_history) > 12:
                            self.conversation_history = self.conversation_history[-12:]
                        return answer

        return "I completed tool executions, but reached the maximum reasoning steps."

    def ask(self, user_message: str) -> str:
        """Alias for run to support Brain compatibility."""
        return self.run(user_message)


# Global singleton instance
_agent_instance = Agent()


def run_agent(user_message: str) -> str:
    """Functional wrapper for Agent.run."""
    return _agent_instance.run(user_message)


# Brain class alias for jarvis.py compatibility
class Brain(Agent):
    """Alias for Agent providing backward compatibility."""
    pass
