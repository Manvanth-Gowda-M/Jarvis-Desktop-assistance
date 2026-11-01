import re
import sys
import threading
import time

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

from config import VOICE_RATE, VOICE_VOLUME, MAX_SPOKEN_CHARS


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


class Speaker:
    """Windows SAPI text-to-speech speaker with voice and keyboard interruption support."""

    def __init__(self, rate: int = VOICE_RATE, volume: int = VOICE_VOLUME):
        self.rate = rate
        self.volume = volume
        self._lock = threading.Lock()
        self._sapi_available = win32com is not None
        self._current_speaker = None
        self._is_speaking = False
        self._mic = None

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

    def cancel_speech(self) -> bool:
        """Immediately cancel and purge any active SAPI speech."""
        if self._current_speaker is not None:
            try:
                self._current_speaker.Speak("", 2)
                return True
            except Exception:
                pass
        return False

    def stop(self) -> bool:
        """Alias for cancel_speech."""
        return self.cancel_speech()

    def speak(self, text: str, enable_voice_interrupt: bool = True) -> bool:
        """Speak concise text asynchronously with keyboard and voice interruption.

        The caller is expected to pass already-prepared spoken text.
        Returns True if speech completed, or False if interrupted.
        """
        spoken_text = str(text).strip() if text else ""
        if not spoken_text:
            return True

        if not self._sapi_available:
            print(f"[Speech SAPI unavailable]: {spoken_text}")
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

                # Voice interruption listener
                if enable_voice_interrupt and sr is not None:
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

                            print("[Voice Interrupt] Microphone audio detected, transcribing...")

                            def _async_recognize():
                                if interrupted.is_set():
                                    return
                                try:
                                    phrase = rec.recognize_google(audio, language="en-US")
                                    print(f"[Voice Interrupt] Heard phrase: '{phrase}'")
                                    if is_interruption_phrase(phrase, spoken_text):
                                        print(f"[Voice Interrupt] Stop phrase detected ('{phrase}') -> Signalling interruption!")
                                        interrupted.set()
                                        try:
                                            import pythoncom
                                            pythoncom.CoInitialize()
                                            speaker.Speak("", 2)
                                        except Exception as sapi_err:
                                            print(f"[Voice Interrupt] SAPI thread-cancel notice: {sapi_err}")
                                    else:
                                        print(f"[Voice Interrupt] Ignored (not an interruption command): '{phrase}'")
                                except sr.UnknownValueError:
                                    print("[Voice Interrupt] Unrecognized audio (no clear words detected)")
                                except sr.RequestError as req_err:
                                    print(f"[Voice Interrupt] Speech recognition service error: {req_err}")
                                except Exception as err:
                                    print(f"[Voice Interrupt] Recognition error: {err}")

                            worker = threading.Thread(target=_async_recognize, daemon=True)
                            worker.start()

                        if self._mic is None:
                            self._mic = sr.Microphone()
                        stop_voice_listener = recognizer.listen_in_background(
                            self._mic,
                            _voice_interrupt_callback,
                            phrase_time_limit=1.8,
                        )
                        print("[Voice Interrupt] Background listener active (monitoring for stop commands)...")

                    except Exception as mic_err:
                        print(f"[Voice Interrupt] Background microphone listener unavailable: {mic_err}")

                # Asynchronous speech flag (SVSFlagsAsync = 1)
                speaker.Speak(spoken_text, 1)

                # Poll for completion, checking both keyboard and voice interruption
                while not speaker.WaitUntilDone(25):
                    # 1. Keyboard interruption check (Windows msvcrt)
                    if msvcrt and msvcrt.kbhit():
                        key = msvcrt.getch()
                        # Space (0x20), Escape (0x1b), 'q', 's', Enter (0x0d)
                        if key in (b" ", b"\x1b", b"q", b"Q", b"s", b"S", b"\r"):
                            interrupted.set()
                            try:
                                speaker.Speak("", 2)
                            except Exception:
                                pass
                            break
                        elif key in (b"\x00", b"\xe0") and msvcrt.kbhit():
                            # Discard second byte of extended key sequence
                            msvcrt.getch()

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
                if stop_voice_listener:
                    try:
                        # wait_for_stop=True ensures the microphone stream is fully closed
                        # and the background thread joins before returning to the main listening loop
                        stop_voice_listener(wait_for_stop=True)
                        print("[Voice Interrupt] Background listener cleaned up.")
                    except Exception as clean_err:
                        print(f"[Voice Interrupt] Listener cleanup warning: {clean_err}")


# Global singleton instance for backward-compatible functional API
_speaker_instance = Speaker()


def speak(text: str, enable_voice_interrupt: bool = True) -> bool:
    """Standalone speak function maintaining backward compatibility."""
    return _speaker_instance.speak(text, enable_voice_interrupt=enable_voice_interrupt)
