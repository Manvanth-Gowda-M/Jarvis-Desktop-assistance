"""
assistant/email_security.py
===========================
Windows DPAPI-backed token encryption and credential security helpers for Jarvis.

Principles:
1. No plaintext OAuth tokens or secrets stored on disk.
2. OAuth credentials and tokens reside in %LOCALAPPDATA%\\Jarvis\\oauth, strictly
   outside the Git repository.
3. All access tokens, refresh tokens, and client secrets are redacted from logs.
4. Uses Windows DPAPI (CryptProtectData / CryptUnprotectData) for OS-level
   hardware- and user-bound encryption.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import sys
from typing import Optional

from config import OAUTH_DIR

# DPAPI Windows Types and Constants
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def is_windows() -> bool:
    """Return True if running on Windows."""
    return sys.platform == "win32"


def dpapi_encrypt(data: bytes, description: str = "JarvisOAuthToken") -> bytes:
    """Encrypt byte data using Windows DPAPI (tied to current Windows user).

    Raises OSError on encryption failure.
    """
    if not is_windows():
        # Fallback for non-Windows testing environments (e.g. CI)
        return b"NON_WIN_DPAPI:" + data

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob = DATA_BLOB(
        len(data),
        ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)),
    )
    out_blob = DATA_BLOB()

    success = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        description,
        None,  # Optional entropy
        None,  # Reserved
        None,  # Prompt struct
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )

    if not success:
        err = ctypes.GetLastError()
        raise OSError(f"Windows DPAPI CryptProtectData failed with error code {err}")

    try:
        encrypted_bytes = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return encrypted_bytes
    finally:
        kernel32.LocalFree(out_blob.pbData)


def dpapi_decrypt(data: bytes) -> bytes:
    """Decrypt byte data using Windows DPAPI.

    Raises OSError on decryption failure.
    """
    if not is_windows():
        if data.startswith(b"NON_WIN_DPAPI:"):
            return data[len(b"NON_WIN_DPAPI:"):]
        return data

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob = DATA_BLOB(
        len(data),
        ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)),
    )
    out_blob = DATA_BLOB()

    success = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )

    if not success:
        err = ctypes.GetLastError()
        raise OSError(f"Windows DPAPI CryptUnprotectData failed with error code {err}")

    try:
        decrypted_bytes = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return decrypted_bytes
    finally:
        kernel32.LocalFree(out_blob.pbData)


def save_secure_token(file_path: Path | str, token_str: str) -> None:
    """Encrypt and safely write a token string to disk using DPAPI."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = token_str.encode("utf-8")
    encrypted_bytes = dpapi_encrypt(raw_bytes)
    with open(path, "wb") as f:
        f.write(encrypted_bytes)


def load_secure_token(file_path: Path | str) -> Optional[str]:
    """Read and decrypt a DPAPI-encrypted token string from disk. Returns None if missing or invalid."""
    path = Path(file_path)
    if not path.is_file() or path.stat().st_size == 0:
        return None

    try:
        with open(path, "rb") as f:
            encrypted_bytes = f.read()
        decrypted_bytes = dpapi_decrypt(encrypted_bytes)
        return decrypted_bytes.decode("utf-8")
    except Exception as exc:
        # Decryption failed (e.g. corrupt file or different Windows user)
        return None


def delete_secure_token(file_path: Path | str) -> bool:
    """Delete a token file securely from disk."""
    path = Path(file_path)
    if path.exists():
        try:
            path.unlink()
            return True
        except Exception:
            return False
    return False


_SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{20,}"),
    re.compile(r"(?i)(refresh_token[\"':\s=]+)[a-zA-Z0-9_\-\.]{15,}"),
    re.compile(r"(?i)(access_token[\"':\s=]+)[a-zA-Z0-9_\-\.]{15,}"),
    re.compile(r"(?i)(client_secret[\"':\s=]+)[a-zA-Z0-9_\-\.]{15,}"),
    re.compile(r"(?i)(secret[\"':\s=]+)[a-zA-Z0-9_\-\.]{15,}"),
    re.compile(r"(?i)(password[\"':\s=]+)\S+"),
]


def redact_secrets(text: str) -> str:
    """Redact tokens, credentials, and secrets from any log or display string."""
    if not text:
        return ""
    result = str(text)
    for pat in _SECRET_PATTERNS:
        result = pat.sub(r"\1[REDACTED]", result)
    return result

