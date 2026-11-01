"""
assistant/llm_providers.py
==========================
Free LLM provider registry for Jarvis.

Every provider here exposes an OpenAI-compatible ``/chat/completions`` endpoint,
so one request format works for all of them:

* **Groq**        – free tier, extremely fast LPU inference (great for voice).
* **NVIDIA NIM**  – free developer API on build.nvidia.com (``nvapi-`` keys).
* **OpenRouter**  – restricted to ``:free`` models and the ``openrouter/free`` router.

Providers are built from environment variables at call time, so changing ``.env``
(or patching ``os.environ`` in tests) takes effect without re-importing modules.
A provider is only used when its API key is present.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

import config
from config import validate_free_model


@dataclass
class Provider:
    name: str
    label: str
    base_url: str
    api_key: str
    models: list[str]
    vision_models: list[str] = field(default_factory=list)
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }


def _split(value: str) -> list[str]:
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def _openrouter_models(primary: str | None) -> list[str]:
    """OpenRouter models, filtered so only free models can ever be requested."""
    candidates = _dedupe([primary or ""] + config.ALLOWED_FREE_MODELS + _split(
        os.getenv("OPENROUTER_EXTRA_MODELS", config.OPENROUTER_EXTRA_MODELS)
    ))
    safe = []
    for model in candidates:
        try:
            safe.append(validate_free_model(model))
        except ValueError:
            print(f"[Providers] Skipping non-free OpenRouter model '{model}'.")
    return _dedupe(safe)


def build_provider(name: str, openrouter_primary: str | None = None) -> Provider | None:
    """Build a single provider from the environment. Returns None if it has no key."""
    name = name.strip().lower()

    if name == "groq":
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            return None
        return Provider(
            name="groq",
            label="Groq",
            base_url=os.getenv("GROQ_BASE_URL", config.GROQ_BASE_URL),
            api_key=key,
            models=_split(os.getenv("GROQ_MODELS", config.GROQ_MODELS)),
            vision_models=_split(os.getenv("GROQ_VISION_MODEL", config.GROQ_VISION_MODEL)),
        )

    if name in {"nvidia", "nim", "nvidia_nim"}:
        key = os.getenv("NVIDIA_API_KEY", "").strip()
        if not key:
            return None
        return Provider(
            name="nvidia",
            label="NVIDIA NIM",
            base_url=os.getenv("NVIDIA_BASE_URL", config.NVIDIA_BASE_URL),
            api_key=key,
            models=_split(os.getenv("NVIDIA_MODELS", config.NVIDIA_MODELS)),
            vision_models=_split(os.getenv("NVIDIA_VISION_MODEL", config.NVIDIA_VISION_MODEL)),
        )

    if name == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY", config.OPENROUTER_API_KEY).strip()
        if not key:
            return None
        vision = []
        for model in _split(os.getenv("OPENROUTER_VISION_MODEL", config.OPENROUTER_VISION_MODEL)):
            try:
                vision.append(validate_free_model(model))
            except ValueError:
                pass
        return Provider(
            name="openrouter",
            label="OpenRouter",
            base_url=os.getenv("OPENROUTER_BASE_URL", config.OPENROUTER_BASE_URL),
            api_key=key,
            models=_openrouter_models(openrouter_primary),
            vision_models=vision,
            extra_headers={
                "HTTP-Referer": "https://github.com/jarvis-assistant",
                "X-Title": "Jarvis Personal Assistant",
            },
        )

    return None


def get_providers(openrouter_primary: str | None = None) -> list[Provider]:
    """Return all configured providers in failover order."""
    order = _split(os.getenv("LLM_PROVIDER_ORDER", config.LLM_PROVIDER_ORDER)) or ["groq", "nvidia", "openrouter"]
    # Always consider every known provider; ones missing from the order go last.
    for known in ("groq", "nvidia", "openrouter"):
        if known not in [o.lower() for o in order]:
            order.append(known)

    providers: list[Provider] = []
    seen: set[str] = set()
    for name in order:
        provider = build_provider(name, openrouter_primary=openrouter_primary)
        if provider and provider.name not in seen and provider.models:
            providers.append(provider)
            seen.add(provider.name)
    return providers


def configured_provider_names() -> list[str]:
    return [p.label for p in get_providers()]


class CooldownTracker:
    """Remembers rate-limited (provider, model) pairs so the next request skips them."""

    def __init__(self):
        self._until: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def mark(self, provider: str, model: str, seconds: float):
        with self._lock:
            self._until[(provider, model)] = time.monotonic() + max(1.0, seconds)

    def is_cooling(self, provider: str, model: str) -> bool:
        with self._lock:
            until = self._until.get((provider, model))
            if until is None:
                return False
            if time.monotonic() >= until:
                self._until.pop((provider, model), None)
                return False
            return True

    def clear(self):
        with self._lock:
            self._until.clear()


def ordered_candidates(
    providers: list[Provider],
    cooldowns: CooldownTracker,
    vision: bool = False,
) -> list[tuple[Provider, str]]:
    """Flatten providers into (provider, model) candidates; rate-limited ones go last."""
    fresh: list[tuple[Provider, str]] = []
    cooling: list[tuple[Provider, str]] = []
    for provider in providers:
        models = provider.vision_models if vision else provider.models
        for model in models:
            target = cooling if cooldowns.is_cooling(provider.name, model) else fresh
            target.append((provider, model))
    return fresh + cooling
