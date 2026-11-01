"""
assistant/settings_store.py
===========================
Read and update the project's ``.env`` file from the dashboard.

* Preserves comments and ordering.
* Un-comments template lines like ``# KEY=...`` when a value is set.
* Applies new values to ``os.environ`` immediately, so providers and voice
  settings that read the environment at call time pick them up without a restart.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from config import BASE_DIR

ENV_PATH = BASE_DIR / ".env"
ENV_TEMPLATE = BASE_DIR / ".env.example"

_LINE_RE = re.compile(r"^\s*(#\s*)?([A-Z][A-Z0-9_]*)\s*=(.*)$")


def _strip_inline_comment(value: str) -> str:
    value = value.strip()
    if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
        return value[1:-1]
    # "value   # comment" -> "value"
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def read_env() -> dict[str, str]:
    """Return active (uncommented) KEY=value pairs from .env."""
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        m = _LINE_RE.match(line)
        if m and not m.group(1):
            values[m.group(2)] = _strip_inline_comment(m.group(3))
    return values


def get_setting(key: str, default: str = "") -> str:
    return os.getenv(key, read_env().get(key, default))


def update_env(updates: dict[str, str]) -> Path:
    """Write values into .env (creating it from the template if needed) and apply them live."""
    if not ENV_PATH.exists():
        if ENV_TEMPLATE.exists():
            ENV_PATH.write_text(ENV_TEMPLATE.read_text(encoding="utf-8-sig"), encoding="utf-8")
        else:
            ENV_PATH.write_text("", encoding="utf-8")

    clean = {}
    for key, value in updates.items():
        value = "" if value is None else str(value)
        if "\n" in value or "\r" in value:
            raise ValueError(f"{key} must be a single line")
        clean[key] = value.strip()

    lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines()
    remaining = dict(clean)

    # First pass: replace active lines.
    for i, line in enumerate(lines):
        m = _LINE_RE.match(line)
        if m and not m.group(1) and m.group(2) in remaining:
            lines[i] = f"{m.group(2)}={remaining.pop(m.group(2))}"

    # Second pass: un-comment template lines for keys still pending.
    for i, line in enumerate(lines):
        m = _LINE_RE.match(line)
        if m and m.group(1) and m.group(2) in remaining:
            lines[i] = f"{m.group(2)}={remaining.pop(m.group(2))}"

    if remaining:
        lines.append("")
        lines.append("# --- Saved from the Jarvis dashboard ---")
        for key, value in remaining.items():
            lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for key, value in clean.items():
        os.environ[key] = value
    return ENV_PATH
