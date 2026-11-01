import asyncio
import ctypes
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import speech_recognition as sr
except ImportError:
    sr = None

try:
    import win32com.client
except ImportError:
    win32com = None

try:
    import edge_tts
except ImportError:
    edge_tts = None

from config import (
    VOICE_RATE,
    VOICE_VOLUME,
    MAX_SPOKEN_CHARS,
    TTS_ENGINE,
    EDGE_TTS_VOICE,
    EDGE_TTS_RATE,
    EDGE_TTS_PITCH,
)


STOP_PHRASES = {
    "stop",
    "jarvis stop",
    "stop jarvis",
    "stop talking",
    "jarvis stop talking",
    "stop speaking",
    "jarvis stop speaking",
    "be quiet",
    "jarvis be quiet",
    "be quiet jarvis",
    "cancel speech",
    "cancel speaking",
    "shut up",
    "jarvis shut up",
    "please stop",
    "stop it",
    "pause speech",
    "halt",
}

STOP_TRIGGERS = (
    "jarvis stop",
    "stop jarvis",
    "stop talking",
    "stop speaking",
    "be quiet",
    "cancel speech",
    "cancel speaking",
    "shut up",
    "please stop",
    "stop it",
    "stop now",
    "stop please",
)


def normalize_phrase(phrase: str) -> str:
    """Normalize phrase: lowercase, strip punctuation and extra spaces."""
    if not phrase:
        return ""
    clean = re.sub(r"[^a-zA-Z\s]", " ", str(phrase))
    return re.sub(r"\s+", " ", clean).strip().lower()


def is_interruption_phrase(phrase: str, current_spoken_text: str = "") -> bool:
    """Determine whether the recognized speech is an interruption command.

    Filters out self-echo if the recognized phrase simply repeats words that
    Jarvis is currently speaking, unless an explicit stop command was uttered.
    """
    clean = normalize_phrase(phrase)
    if not clean:
        return False

    # 1. Exact match against known interrupt keywords
    if clean in STOP_PHRASES:
        return True

    spoken_clean = normalize_phrase(current_spoken_text)

    # 2. Key trigger phrases appearing anywhere inside the recognized text
    for trigger in STOP_TRIGGERS:
        if trigger in clean:
            # Check self-echo: if the words are an exact subset of what Jarvis is saying,
            # ignore it unless it contains the invocation word 'jarvis'
            if spoken_clean and clean in spoken_clean and "jarvis" not in clean:
                return False
            return True

    # 3. Word-level matching for standalone or combination 'stop'
    words = clean.split()
    if "stop" in words:
        # If the phrase contains 'jarvis' anywhere with 'stop' (e.g. 'jarvis please stop')
        if "jarvis" in words:
            return True
        # If very short command ('stop', 'stop now', 'stop please')
        if len(words) <= 2:
            return True
        # For longer phrases containing 'stop', check if it's echoing Jarvis's speech
        if spoken_clean and clean in spoken_clean:
            return False
        return True

    # 4. Check for 'quiet' variants ('be quiet', 'quiet jarvis')
    if "quiet" in words and ("be" in words or "jarvis" in words):
        return True

    return False


def prepare_spoken_answer(text: str, max_chars: int = MAX_SPOKEN_CHARS) -> str:
    """Create a short, direct spoken version while preserving full text in UI/terminal."""
    if not text:
        return "I've finished processing your request."

    cleaned = str(text).strip()

    # Special case: Email Draft Saved / Preview
    low = cleaned.lower()
    if "gmail draft saved" in low or "saved to your gmail drafts" in low or ("gmail" in low and "draft id" in low and "verified" in low):
        return "Your Gmail draft has been saved."
    if "outlook draft saved" in low or "saved to your outlook drafts" in low or ("outlook" in low and "draft id" in low and "verified" in low):
        return "Your Outlook draft has been saved."
    if "[email draft preview]" in low or "email draft created" in low or ("to:" in low and "subject:" in low and "draft id" in low):
        return "Your email draft is ready for review."

    # Special case: Raw JSON or dictionary dumps
    if (cleaned.startswith("{") and cleaned.endswith("}")) or (cleaned.startswith("[") and cleaned.endswith("]")):
        return "Task completed successfully."

    # Never read code aloud.
    if "```" in cleaned:
        cleaned = re.sub(r"```[\s\S]*?```", "", cleaned)
        cleaned = cleaned.split("```", 1)[0]

    # Convert Markdown links [label](url) to their visible labels.
    cleaned = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", cleaned)

    # Remove URLs and source-only lines.
    cleaned = re.sub(r"(?im)^\s*(?:sources?|urls?|links?)\s*:\s*.*$", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)

    # Remove Markdown headers and list indicators.
    cleaned = re.sub(r"(?m)^\s*#+\s*", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*[-*•]\s+", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*\d+[.)]\s+", "", cleaned)

    # Strip inline markdown symbols.
    cleaned = re.sub(r"[`*_>~]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Strip conversational filler introductions.
    filler_intros = [
        r"^(?:certainly|sure thing|sure|of course|as an ai(?: assistant)?|happy to help|i can help with that|here (?:is|are) (?:the|what|your))\b[:,\s-]*",
        r"^(?:okay|ok|alright)[,\s-]*",
    ]
    for pattern in filler_intros:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    # Strip conversational filler closing questions.
    filler_closings = [
        r"(?:is there anything else (?:i can help (?:you )?with|you need)|let me know if (?:you need anything else|i can help)|feel free to ask if you have (?:any )?more questions|hope this helps)[^.?!]*[.?!]?$",
    ]
    for pattern in filler_closings:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    if not cleaned:
        return "I've finished processing your request."

    # Speak at most two sentences.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    spoken = " ".join(sentences[:2]).strip()

    if len(spoken) > max_chars:
        spoken = spoken[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."

    # Let the user know when the full answer is longer in the terminal.
    if len(cleaned) > len(spoken) + 25:
        suffix = " More details are in the terminal."
        available = max_chars - len(suffix)
        if len(spoken) > available:
            spoken = spoken[:available].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
        spoken += suffix

    return spoken


class _MciPlayer:
    """Minimal MP3 player built on the Windows MCI API (winmm), no extra dependencies.

    All MCI calls for one clip happen on the thread that created the player.
    """

    def __init__(self, path: str, volume: int = 100):
        self.alias = f"jarvis_{uuid.uuid4().hex[:8]}"
        self._winmm = ctypes.windll.winmm
        err = self._send(f'open "{path}" type mpegvideo alias {self.alias}')
        if err:
            raise RuntimeError(f"MCI open failed (code {err})")
        self._send(f"setaudio {self.alias} volume to {max(0, min(1000, int(volume) * 10))}")

    def _send(self, command: str) -> int:
        buf = ctypes.create_unicode_buffer(128)
        return self._winmm.mciSendStringW(command, buf, 127, 0)

    def _query(self, command: str) -> str:
        buf = ctypes.create_unicode_buffer(128)
        self._winmm.mciSendStringW(command, buf, 127, 0)
        return buf.value

    def play(self):
        err = self._send(f"play {self.alias}")
        if err:
            raise RuntimeError(f"MCI play failed (code {err})")

    def is_done(self) -> bool:
        return self._query(f"status {self.alias} mode") not in ("playing", "seeking")

    def stop(self):
        self._send(f"stop {self.alias}")

    def close(self):
        self._send(f"close {self.alias}")


class Speaker:
    """Text-to-speech with neural Edge voices (free) or Windows SAPI, plus voice and keyboard interruption.

    Engine selection: ``TTS_ENGINE=edge`` (default) uses Microsoft Edge neural voices
    (e.g. en-GB-RyanNeural, a British "Jarvis" voice) and falls back to SAPI if the
    edge-tts package or the network is unavailable. ``TTS_ENGINE=sapi`` uses SAPI only.
    """

    def __init__(self, rate: int = VOICE_RATE, volume: int = VOICE_VOLUME, engine: str | None = None):
        self.rate = rate
        self.volume = volume
        self.engine = (engine or os.getenv("TTS_ENGINE", TTS_ENGINE) or "sapi").strip().lower()
        self.edge_voice = os.getenv("EDGE_TTS_VOICE", EDGE_TTS_VOICE)
        self._lock = threading.Lock()
        self._sapi_available = win32com is not None
        self._current_speaker = None
        self._is_speaking = False
        self._mic = None
        self._edge_cancel = threading.Event()
        self._edge_playing = False
        self._edge_failures = 0

        if self._sapi_available:
            try:
                self._current_speaker = win32com.client.Dispatch("SAPI.SpVoice")
                self._current_speaker.Rate = self.rate
                self._current_speaker.Volume = self.volume
            except Exception:
                pass

    @property
    def is_speaking(self) -> bool:
        """Return True if speech is actively in progress."""
        return self._is_speaking

    @property
    def uses_edge(self) -> bool:
        # After repeated network failures, stay on SAPI for this session to avoid delays.
        return self.engine == "edge" and edge_tts is not None and self._edge_failures < 3

    def cancel_speech(self) -> bool:
        """Immediately cancel any active speech (neural or SAPI)."""
        cancelled = False
        if self._edge_playing:
            self._edge_cancel.set()
            cancelled = True
        if self._current_speaker is not None:
            try:
                self._current_speaker.Speak("", 2)
                cancelled = True
            except Exception:
                pass
        return cancelled

    def stop(self) -> bool:
        """Alias for cancel_speech."""
        return self.cancel_speech()

    # ------------------------------------------------------------------ helpers
    def _start_voice_interrupt(self, spoken_text: str, interrupted: threading.Event, on_interrupt):
        """Start a background listener that sets *interrupted* when a stop phrase is heard."""
        if sr is None:
            return None
        try:
            recognizer = sr.Recognizer()
            recognizer.pause_threshold = 0.3
            recognizer.phrase_threshold = 0.1
            recognizer.non_speaking_duration = 0.2
            recognizer.energy_threshold = 150
            recognizer.dynamic_energy_threshold = True

            def _voice_interrupt_callback(rec, audio):
                if interrupted.is_set():
                    return

                def _async_recognize():
                    if interrupted.is_set():
                        return
                    try:
                        phrase = rec.recognize_google(audio, language="en-US")
                        if is_interruption_phrase(phrase, spoken_text):
                            print(f"[Voice Interrupt] Stop phrase detected ('{phrase}')")
                            interrupted.set()
                            try:
                                on_interrupt()
                            except Exception as cancel_err:
                                print(f"[Voice Interrupt] Cancel notice: {cancel_err}")
                    except Exception:
                        pass  # Unrecognised audio or service error: keep speaking.

                threading.Thread(target=_async_recognize, daemon=True).start()

            if self._mic is None:
                self._mic = sr.Microphone()
            stop_fn = recognizer.listen_in_background(
                self._mic,
                _voice_interrupt_callback,
                phrase_time_limit=1.8,
            )
            return stop_fn
        except Exception as mic_err:
            print(f"[Voice Interrupt] Background microphone listener unavailable: {mic_err}")
            return None

    @staticmethod
    def _keyboard_interrupt_pressed() -> bool:
        if msvcrt and msvcrt.kbhit():
            key = msvcrt.getch()
            # Space, Escape, q, s, Enter
            if key in (b" ", b"\x1b", b"q", b"Q", b"s", b"S", b"\r"):
                return True
            if key in (b"\x00", b"\xe0") and msvcrt.kbhit():
                msvcrt.getch()  # discard second byte of extended key
        return False

    @staticmethod
    def _stop_listener(stop_fn):
        if stop_fn:
            try:
                # wait_for_stop=True ensures the microphone stream is fully closed before returning
                stop_fn(wait_for_stop=True)
            except Exception as clean_err:
                print(f"[Voice Interrupt] Listener cleanup warning: {clean_err}")

    # ------------------------------------------------------------------ Edge neural TTS
    def _synthesize_edge(self, text: str) -> str | None:
        """Render *text* to a temporary MP3 with edge-tts. Returns the path or None on failure."""
        path = Path(tempfile.gettempdir()) / f"jarvis_tts_{uuid.uuid4().hex}.mp3"

        async def _render():
            communicate = edge_tts.Communicate(
                text,
                self.edge_voice,
                rate=os.getenv("EDGE_TTS_RATE", EDGE_TTS_RATE),
                pitch=os.getenv("EDGE_TTS_PITCH", EDGE_TTS_PITCH),
            )
            await asyncio.wait_for(communicate.save(str(path)), timeout=12)

        try:
            asyncio.run(_render())
            if path.exists() and path.stat().st_size > 0:
                self._edge_failures = 0
                return str(path)
        except Exception as err:
            self._edge_failures += 1
            print(f"[Speech] Neural voice unavailable ({err}); using Windows voice.")
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return None

    def _speak_edge(self, spoken_text: str, enable_voice_interrupt: bool) -> bool | None:
        """Speak with the neural voice. Returns True/False like speak(), or None to fall back to SAPI."""
        path = self._synthesize_edge(spoken_text)
        if not path:
            return None

        interrupted = threading.Event()
        self._edge_cancel.clear()
        stop_listener = None
        player = None
        try:
            try:
                player = _MciPlayer(path, self.volume)
                player.play()
            except Exception as err:
                print(f"[Speech] Audio playback failed ({err}); using Windows voice.")
                return None

            self._is_speaking = True
            self._edge_playing = True
            if enable_voice_interrupt:
                stop_listener = self._start_voice_interrupt(spoken_text, interrupted, self._edge_cancel.set)

            while not player.is_done():
                if self._keyboard_interrupt_pressed() or self._edge_cancel.is_set():
                    interrupted.set()
                    break
                time.sleep(0.03)

            if interrupted.is_set():
                player.stop()
                print("\n[Speech interrupted]")
                return False
            return True
        finally:
            self._edge_playing = False
            self._is_speaking = False
            if player is not None:
                try:
                    player.close()
                except Exception:
                    pass
            self._stop_listener(stop_listener)
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass

    # ------------------------------------------------------------------ public API
    def speak(self, text: str, enable_voice_interrupt: bool = True) -> bool:
        """Speak already-prepared text with keyboard and voice interruption.

        Returns True if speech completed, or False if interrupted.
        """
        spoken_text = str(text).strip() if text else ""
        if not spoken_text:
            return True

        if self.uses_edge:
            with self._lock:
                result = self._speak_edge(spoken_text, enable_voice_interrupt)
            if result is not None:
                return result

        if not self._sapi_available:
            print(f"[Speech unavailable]: {spoken_text}")
            return True

        interrupted = threading.Event()
        stop_voice_listener = None
        speaker = None

        with self._lock:
            try:
                if self._current_speaker is None:
                    self._current_speaker = win32com.client.Dispatch("SAPI.SpVoice")
                speaker = self._current_speaker
                speaker.Rate = self.rate
                speaker.Volume = self.volume
                self._is_speaking = True

                if enable_voice_interrupt:
                    def _cancel_sapi():
                        import pythoncom
                        pythoncom.CoInitialize()
                        speaker.Speak("", 2)

                    stop_voice_listener = self._start_voice_interrupt(spoken_text, interrupted, _cancel_sapi)

                # Asynchronous speech flag (SVSFlagsAsync = 1)
                speaker.Speak(spoken_text, 1)

                while not speaker.WaitUntilDone(25):
                    if self._keyboard_interrupt_pressed():
                        interrupted.set()
                    if interrupted.is_set():
                        try:
                            speaker.Speak("", 2)
                        except Exception:
                            pass
                        break

                if interrupted.is_set():
                    try:
                        speaker.Speak("", 2)
                    except Exception:
                        pass
                    print("\n[Speech interrupted]")
                    return False

                return True

            except Exception as e:
                print(f"[Speech Error]: {e}")
                return False

            finally:
                self._current_speaker = None
                self._is_speaking = False
                self._stop_listener(stop_voice_listener)


def list_edge_voices(language_prefix: str = "en-") -> list[str]:
    """Return available neural voice names (requires internet)."""
    if edge_tts is None:
        return []
    try:
        voices = asyncio.run(edge_tts.list_voices())
    except Exception:
        return []
    return sorted(v["ShortName"] for v in voices if v.get("ShortName", "").startswith(language_prefix))


# Global singleton instance for backward-compatible functional API
_speaker_instance = Speaker()


def speak(text: str, enable_voice_interrupt: bool = True) -> bool:
    """Standalone speak function maintaining backward compatibility."""
    return _speaker_instance.speak(text, enable_voice_interrupt=enable_voice_interrupt)
