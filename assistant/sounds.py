"""
assistant/sounds.py
===================
Short UI chimes (generated once as WAV files in ``sounds/``) played asynchronously.
"""

from __future__ import annotations

import math
import struct
import wave

from config import BASE_DIR

SOUNDS_DIR = BASE_DIR / "sounds"
_RATE = 22050


def _write_chime(path, notes, note_len=0.09, volume=0.32):
    frames = bytearray()
    for i, freq in enumerate(notes):
        n = int(_RATE * note_len * (1.6 if i == len(notes) - 1 else 1.0))
        for s in range(n):
            t = s / _RATE
            env = min(1.0, s / (_RATE * 0.005)) * math.exp(-4.5 * t / (note_len * 1.6))
            val = volume * env * (math.sin(2 * math.pi * freq * t) + 0.25 * math.sin(4 * math.pi * freq * t))
            frames += struct.pack("<h", int(max(-1.0, min(1.0, val)) * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(bytes(frames))


_CHIMES = {
    "wake": [880.0, 1318.5],       # rising: "I'm listening"
    "done": [1046.5, 784.0],       # falling: "finished"
    "notify": [1318.5, 1318.5, 1760.0],
}


def chime_path(name: str):
    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    path = SOUNDS_DIR / f"{name}.wav"
    if not path.exists() and name in _CHIMES:
        _write_chime(path, _CHIMES[name])
    return path


def play(name: str):
    """Play a chime without blocking. Silently ignores failures."""
    try:
        import winsound

        path = chime_path(name)
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass
