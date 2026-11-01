"""
assistant/agent.py
==================
Tool-calling agent ("the brain") for Jarvis.

* Uses only free providers: Groq, NVIDIA NIM, and OpenRouter ``:free`` models.
* Fails over automatically across providers/models on rate limits, outages, or bad replies.
* Sends only the tool groups relevant to the request to stay inside free-tier token limits.
* Injects the current date/time and relevant long-term memories into the persona prompt.
* After the tool loop, runs a synthesis pass (``tool_choice="none"``) so tool results are
  always turned into a natural answer.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime

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
    LLM_TEMPERATURE,
    LLM_MAX_TOKENS,
    RATE_LIMIT_COOLDOWN,
    TOOL_SELECTION,
    USER_NAME,
    USER_TITLE,
    DEFAULT_LOCATION,
)
from assistant.tools import TOOLS_SCHEMA, AVAILABLE_TOOLS, select_tools, select_tool_groups
from assistant.llm_providers import CooldownTracker, Provider, get_providers, ordered_candidates


PERSONA_PROMPT = """You are {name}, a highly capable AI assistant running on the user's Windows PC, modelled on J.A.R.V.I.S. from Iron Man.

Personality:
- Calm, precise, quietly witty, unfailingly polite. Address the user as "{title}" occasionally, not in every sentence.
- Lead with the answer. No filler like "Certainly!" or "Great question". Never say you are "just an AI".
- Replies are usually spoken aloud: keep them to 1-3 short sentences unless the user asks for detail, a list, code, or research.
- Use the user's saved memories to personalise answers when relevant.

Acting:
- You control this PC through tools. When a request needs an action or live data, call the tool instead of describing what you would do.
- Chain tools when needed (e.g. research_web then save_research_note). Use calculate for arithmetic, get_weather for weather, analyze_screen when the user refers to what is on screen.
- To play any song, music, or video, call play_media: it opens the top result and it starts playing by itself. You CAN play media, so never tell the user you can't click play, and never just open a search page for a play request. For "another one" / "not this one", use play_next_result.
- Report only what tools actually returned. If a tool fails, say so briefly and suggest the next step. Never invent results, file paths, or email addresses.
- Confirmation rules: power_action, delete_to_recycle, move_file, rename_file and close_window need confirmed=true. Only set it when the user has clearly confirmed in this conversation; otherwise call with confirmed=false to get a preview, or ask.
- Email is draft-only. Never claim an email was sent. Ask for the recipient address if it is missing.
- Never reveal API keys, tokens, or passwords.

Context:
- Now: {now}
- User: {user}
- Default location: {location}
{memories}"""

# Kept for backward compatibility (imported by older code/tests).
SYSTEM_PROMPT = PERSONA_PROMPT

_CONFIRM_WORDS = {"yes", "yeah", "yep", "sure", "ok", "okay", "confirm", "confirmed", "do it", "go ahead",
                  "proceed", "please do", "continue", "again", "that one", "the same"}


def _clean_assistant_message(msg: dict) -> dict:
    """Keep only fields every OpenAI-compatible provider accepts when echoing history back."""
    clean = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        calls = []
        for i, call in enumerate(msg["tool_calls"]):
            fn = call.get("function", {}) or {}
            args = fn.get("arguments", "{}")
            if not isinstance(args, str):
                args = json.dumps(args)
            calls.append({
                "id": call.get("id") or f"call_{i}",
                "type": "function",
                "function": {"name": fn.get("name", ""), "arguments": args},
            })
        clean["tool_calls"] = calls
    return clean


def _strip_reasoning(text: str) -> str:
    """Remove <think>...</think> blocks some open models emit."""
    if not text:
        return text
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    return text.strip()


# Activity listeners receive (kind, detail) events: "tool", "provider", "error".
_activity_listeners: list = []


def add_activity_listener(callback):
    if callback not in _activity_listeners:
        _activity_listeners.append(callback)


def remove_activity_listener(callback):
    if callback in _activity_listeners:
        _activity_listeners.remove(callback)


def _emit_activity(kind: str, detail: str):
    for cb in list(_activity_listeners):
        try:
            cb(kind, detail)
        except Exception:
            pass


class Agent:
    """Multi-provider free-model tool-calling agent."""

    MAX_HISTORY = 16

    def __init__(self, model: str = CONFIGURED_MODEL):
        # OpenRouter model must be free; Groq/NVIDIA are free-tier accounts by design.
        self.model = validate_free_model(model)
        self.conversation_history: list[dict] = []
        self.api_url = f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"
        self.cooldowns = CooldownTracker()
        self.last_provider: str = ""
        self.last_model: str = ""
        self._last_groups: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ helpers
    def _get_api_key(self) -> str:
        key = os.getenv("OPENROUTER_API_KEY", OPENROUTER_API_KEY)
        if not key:
            raise ValueError("OPENROUTER_API_KEY is missing. Please set it in your .env file.")
        return key.strip()

    def _build_system_prompt(self, user_message: str) -> str:
        memories = ""
        try:
            from assistant.memory import recall_facts

            recalled = recall_facts(user_message)
            if recalled and recalled.startswith("- "):
                memories = "- Relevant saved memories about the user:\n" + recalled
        except Exception:
            pass
        user = os.getenv("USER_NAME", USER_NAME) or "the owner of this PC"
        return PERSONA_PROMPT.format(
            name=os.getenv("JARVIS_NAME", JARVIS_NAME),
            title=os.getenv("USER_TITLE", USER_TITLE) or "sir",
            now=datetime.now().strftime("%A, %d %B %Y, %I:%M %p"),
            user=user,
            location=os.getenv("DEFAULT_LOCATION", DEFAULT_LOCATION) or "unknown (weather tool can auto-detect)",
            memories=memories,
        )

    def _tools_for(self, user_message: str) -> list[dict]:
        mode = os.getenv("TOOL_SELECTION", TOOL_SELECTION).strip().lower()
        extra: set[str] = set()
        words = re.findall(r"[a-z']+", user_message.lower())
        # Short follow-ups ("yes", "do it", "the second one") keep the previous turn's tools.
        if len(words) <= 6 or any(w in user_message.lower() for w in _CONFIRM_WORDS):
            extra = set(self._last_groups)
        self._last_groups = select_tool_groups(user_message) | extra
        return select_tools(user_message, extra_groups=extra, mode=mode)

    def _call_provider(
        self,
        provider: Provider,
        model: str,
        messages: list[dict],
        tool_choice: str = "auto",
        tools: list[dict] | None = None,
    ) -> dict:
        payload: dict = {
            "model": model,
            "messages": messages,
            "tool_choice": tool_choice,
            "temperature": LLM_TEMPERATURE,
            "max_tokens": LLM_MAX_TOKENS,
        }
        if tool_choice != "none":
            payload["tools"] = tools if tools is not None else TOOLS_SCHEMA
        elif provider.name != "openrouter" and tools:
            # OpenAI-style APIs (Groq, NIM) reject tool_choice without tools, so send the
            # schemas with tool_choice="none" to force a plain-text answer.
            payload["tools"] = tools

        try:
            response = requests.post(
                provider.chat_url,
                headers=provider.headers(),
                json=payload,
                timeout=API_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            # Slow/overloaded free endpoint: skip it for a few minutes so later turns stay fast.
            self.cooldowns.mark(provider.name, model, 300)
            raise RuntimeError(f"{provider.label} request timed out after {API_TIMEOUT}s on model '{model}'.")
        except requests.exceptions.RequestException as err:
            raise RuntimeError(f"{provider.label} network error on model '{model}': {err}")

        status = response.status_code
        if status == 404:
            # Model not available for this account/region: don't retry it this session.
            self.cooldowns.mark(provider.name, model, 3600)
            raise RuntimeError(f"Model '{model}' is currently unavailable on {provider.label} (HTTP 404).")
        if status == 429:
            detail = ""
            try:
                detail = response.json().get("error", {}).get("message", "")
            except Exception:
                pass
            wait = RATE_LIMIT_COOLDOWN
            try:
                retry_after = response.headers.get("retry-after") if hasattr(response, "headers") else None
                if retry_after:
                    wait = min(600.0, float(retry_after))
            except (TypeError, ValueError):
                pass
            self.cooldowns.mark(provider.name, model, wait)
            raise RuntimeError(
                f"Rate limited (HTTP 429) on {provider.label} model '{model}'" + (f": {detail}" if detail else ".")
            )
        if status != 200:
            body = ""
            try:
                body = str(response.text)[:200]
            except Exception:
                pass
            if status in (401, 403):
                self.cooldowns.mark(provider.name, model, 600)
            raise RuntimeError(f"{provider.label} returned HTTP {status} on '{model}': {body}")

        try:
            data = response.json()
        except Exception as json_err:
            raise RuntimeError(f"Failed to parse {provider.label} response as JSON: {json_err}")
        if isinstance(data, dict) and data.get("error") and not data.get("choices"):
            raise RuntimeError(f"{provider.label} error on '{model}': {data['error']}")
        return data

    def _call_openrouter(self, messages: list[dict], model: str, tool_choice: str = "auto") -> dict:
        """Backward-compatible single OpenRouter call."""
        provider = Provider(
            name="openrouter",
            label="OpenRouter",
            base_url=OPENROUTER_BASE_URL,
            api_key=self._get_api_key(),
            models=[validate_free_model(model)],
        )
        return self._call_provider(provider, model, messages, tool_choice=tool_choice)

    def _try_models(
        self,
        messages: list[dict],
        active_model: str = "",
        tool_choice: str = "auto",
        tools: list[dict] | None = None,
    ) -> tuple[dict | None, str, Exception | None]:
        """Try every configured (provider, model) in failover order.

        Returns (data, model_used, last_error).
        """
        providers = get_providers(openrouter_primary=self.model)
        if not providers:
            return None, active_model, ValueError(
                "No AI provider API key is configured. Add GROQ_API_KEY, NVIDIA_API_KEY, or "
                "OPENROUTER_API_KEY to your .env file (all have free tiers)."
            )

        candidates = ordered_candidates(providers, self.cooldowns)
        # Stick with the model that worked last time inside one conversation turn.
        if self.last_provider and self.last_model:
            for i, (p, m) in enumerate(candidates):
                if p.name == self.last_provider and m == self.last_model and not self.cooldowns.is_cooling(p.name, m):
                    candidates.insert(0, candidates.pop(i))
                    break

        last_error: Exception | None = None
        for provider, model in candidates:
            if provider.name == "openrouter":
                try:
                    validate_free_model(model)
                except ValueError as val_err:
                    last_error = val_err
                    continue
            try:
                data = self._call_provider(provider, model, messages, tool_choice=tool_choice, tools=tools)
            except Exception as err:
                last_error = err
                print(f"[Agent] {provider.label}/{model} failed: {err}")
                _emit_activity("error", f"{provider.label} · {model}: {str(err)[:120]}")
                continue

            choices = data.get("choices") if isinstance(data, dict) else None
            if choices:
                msg = choices[0].get("message", {}) or {}
                if not msg.get("tool_calls") and not (msg.get("content") or "").strip() and tool_choice != "none":
                    # Empty reply: try another model rather than giving up.
                    last_error = RuntimeError(f"{provider.label}/{model} returned an empty reply.")
                    continue
            self.last_provider, self.last_model = provider.name, model
            _emit_activity("provider", f"{provider.label} · {model}")
            return data, model, None
        return None, active_model, last_error

    def _remember_turn(self, user_message: str, answer: str):
        self.conversation_history.extend([
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": answer},
        ])
        if len(self.conversation_history) > self.MAX_HISTORY:
            self.conversation_history = self.conversation_history[-self.MAX_HISTORY:]

    @staticmethod
    def _execute_tool(name: str, raw_args) -> str:
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args or {})
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}

        tool_func = AVAILABLE_TOOLS.get(name)
        if not tool_func:
            return f"Error: Tool '{name}' is not recognized."
        try:
            print(f"[Agent] Tool -> {name}({', '.join(f'{k}={str(v)[:40]!r}' for k, v in args.items())})")
            _emit_activity("tool", f"{name}({', '.join(f'{k}={str(v)[:30]!r}' for k, v in args.items())})")
            result = tool_func(**args)
        except TypeError as e:
            return f"Tool '{name}' was called with invalid arguments: {e}"
        except Exception as e:
            return f"Tool '{name}' execution failed: {e}"
        text = str(result)
        if len(text) > 6000:
            text = text[:6000] + "\n...[output truncated]"
        return text

    # ------------------------------------------------------------------ main loop
    def run(self, user_message: str) -> str:
        user_message = str(user_message).strip()
        if not user_message:
            return "How can I assist you today?"

        with self._lock:
            return self._run_locked(user_message)

    def _run_locked(self, user_message: str) -> str:
        self.last_provider = self.last_model = ""
        tools = self._tools_for(user_message)
        messages = [
            {"role": "system", "content": self._build_system_prompt(user_message)},
            *self.conversation_history,
            {"role": "user", "content": user_message},
        ]

        active_model = self.model
        executed_any_tools = False

        for _step in range(MAX_TOOL_STEPS):
            data, active_model, last_error = self._try_models(messages, active_model, tools=tools)
            if not data:
                return (
                    "I couldn't reach the AI service right now. "
                    f"Details: {last_error or 'Free model service temporarily busy.'}"
                )

            choices = data.get("choices", [])
            if not choices:
                return "I didn't receive a valid response from the AI model."

            assistant_msg = choices[0].get("message", {}) or {}
            tool_calls = assistant_msg.get("tool_calls") or []

            if not tool_calls:
                answer = _strip_reasoning(assistant_msg.get("content") or "") or "I've completed processing."
                self._remember_turn(user_message, answer)
                return answer

            messages.append(_clean_assistant_message(assistant_msg))
            for call in tool_calls:
                fn = call.get("function", {}) or {}
                name = fn.get("name")
                result = self._execute_tool(name, fn.get("arguments", "{}"))
                # If the model used a tool outside the selected set, include its group next round.
                if name and not any(t["function"]["name"] == name for t in tools):
                    tools = tools + [t for t in TOOLS_SCHEMA if t["function"]["name"] == name]
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result,
                })
            executed_any_tools = True

        # Synthesis pass: force a text answer from the collected tool results.
        if executed_any_tools:
            data, active_model, _ = self._try_models(messages, active_model, tool_choice="none", tools=tools)
            if data:
                choices = data.get("choices", [])
                if choices:
                    answer = _strip_reasoning(choices[0].get("message", {}).get("content", "") or "")
                    if answer:
                        self._remember_turn(user_message, answer)
                        return answer

        return "I completed tool executions, but reached the maximum reasoning steps."

    def ask(self, user_message: str) -> str:
        return self.run(user_message)

    def reset(self):
        """Forget the short-term conversation."""
        self.conversation_history.clear()
        self._last_groups.clear()


_agent_instance = Agent()


def run_agent(user_message: str) -> str:
    return _agent_instance.run(user_message)


def reset_conversation():
    _agent_instance.reset()


def get_agent() -> Agent:
    return _agent_instance


class Brain(Agent):
    """Alias for Agent providing backward compatibility."""
    pass
