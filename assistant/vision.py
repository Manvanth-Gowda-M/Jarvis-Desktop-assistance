"""
assistant/vision.py
===================
Screen understanding: capture the screen (or an image file) and ask a free
vision-capable model about it. Uses the same free providers as the agent
(OpenRouter free vision models, NVIDIA NIM vision models, Groq vision models).
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import requests

from config import API_TIMEOUT
from assistant.llm_providers import CooldownTracker, get_providers, ordered_candidates

try:
    from PIL import Image, ImageGrab
except ImportError:  # pragma: no cover
    Image = ImageGrab = None

_cooldowns = CooldownTracker()
_MAX_SIDE = 1280


def _encode_image(img) -> str:
    img = img.convert("RGB")
    img.thumbnail((_MAX_SIDE, _MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=72)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def ask_vision(question: str, image_data_url: str) -> str:
    providers = get_providers()
    candidates = ordered_candidates(providers, _cooldowns, vision=True)
    if not candidates:
        return "No vision-capable free provider is configured. Add OPENROUTER_API_KEY, NVIDIA_API_KEY or GROQ_API_KEY to .env."

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ],
    }]
    errors = []
    for provider, model in candidates:
        try:
            resp = requests.post(
                provider.chat_url,
                headers=provider.headers(),
                json={"model": model, "messages": messages, "max_tokens": 700, "temperature": 0.2},
                timeout=max(API_TIMEOUT, 45),
            )
        except requests.RequestException as exc:
            errors.append(f"{provider.label}/{model}: {exc}")
            continue
        if resp.status_code == 429:
            _cooldowns.mark(provider.name, model, 60)
        if resp.status_code != 200:
            errors.append(f"{provider.label}/{model}: HTTP {resp.status_code}")
            continue
        try:
            content = resp.json()["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError):
            content = ""
        if content.strip():
            return content.strip()
        errors.append(f"{provider.label}/{model}: empty reply")
    return "I couldn't analyse the image with any free vision model. " + "; ".join(errors[-3:])


def analyze_screen(question: str = "Describe what is on the screen.") -> str:
    """Take a screenshot and answer a question about what is visible."""
    if ImageGrab is None:
        return "Screen analysis needs Pillow (pip install pillow)."
    question = str(question or "").strip() or "Describe what is on the screen."
    try:
        shot = ImageGrab.grab()
    except Exception as exc:
        return f"Couldn't capture the screen: {exc}"
    prompt = (
        "You are looking at a screenshot of the user's Windows desktop. "
        f"Answer concisely and specifically. Question: {question}"
    )
    return ask_vision(prompt, _encode_image(shot))


def analyze_image(path: str, question: str = "Describe this image.") -> str:
    """Answer a question about an image file inside an approved folder."""
    if Image is None:
        return "Image analysis needs Pillow (pip install pillow)."
    from assistant.file_manager import _validate_path

    try:
        safe = _validate_path(path)
    except Exception as exc:
        return f"Access denied: {exc}"
    if isinstance(safe, str):
        return safe
    p = Path(safe)
    if not p.is_file():
        return f"Image not found: {path}"
    if p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
        return "That file is not a supported image type."
    try:
        with Image.open(p) as img:
            data_url = _encode_image(img)
    except Exception as exc:
        return f"Couldn't open the image: {exc}"
    return ask_vision(str(question or "Describe this image."), data_url)


VISION_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "analyze_screen",
            "description": "Look at the user's screen (screenshot) and answer a question about it: read errors, summarize a page, explain what is open.",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string", "description": "What to look for or answer about the screen."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_image",
            "description": "Answer a question about an image file in an approved folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full path to the image."},
                    "question": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
]

VISION_AVAILABLE_TOOLS = {
    "analyze_screen": analyze_screen,
    "analyze_image": analyze_image,
}
