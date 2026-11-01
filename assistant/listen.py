import os
import re

import requests
import speech_recognition as sr
from config import (
    LISTEN_TIMEOUT,
    PHRASE_TIME_LIMIT,
    PAUSE_THRESHOLD,
    NON_SPEAKING_DURATION,
    MICROPHONE_DEVICE_INDEX,
    STT_ENGINE,
    STT_LANGUAGE,
    GROQ_BASE_URL,
    GROQ_STT_MODEL,
)

# Phrases Whisper commonly invents for silence/background noise.
_WHISPER_HALLUCINATIONS = {
    "", "you", "thank you", "thanks for watching", "thank you for watching", "bye",
    "thank you so much for watching", "please subscribe", "subtitles by the amara org community",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", str(text).lower())).strip()


class Listener:
    """Manages microphone input and SpeechRecognition with graceful error recovery."""

    def __init__(
        self,
        pause_threshold: float = PAUSE_THRESHOLD,
        energy_threshold: int = 300,
        dynamic_energy_threshold: bool = True,
        device_index: int | None = None,
    ):
        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold = pause_threshold
        self.recognizer.non_speaking_duration = NON_SPEAKING_DURATION
        self.recognizer.phrase_threshold = 0.2
        self.recognizer.energy_threshold = energy_threshold
        self.recognizer.dynamic_energy_threshold = dynamic_energy_threshold
        self.device_index = device_index if device_index is not None else MICROPHONE_DEVICE_INDEX
        self._mic = None
        self.last_capture_duration = 0.0
        self.last_recognition_duration = 0.0
        self._groq_failures = 0

    def _get_microphone(self) -> sr.Microphone:
        if self._mic is None:
            self._mic = sr.Microphone(device_index=self.device_index)
        return self._mic

    def is_available(self) -> bool:
        """Check if microphone hardware and speech recognizer are accessible."""
        try:
            with self._get_microphone() as source:
                return True
        except Exception:
            return False

    def listen(
        self,
        timeout: float = LISTEN_TIMEOUT,
        phrase_time_limit: float = PHRASE_TIME_LIMIT,
    ) -> str:
        """Listen to the microphone and convert speech to text.

        Captures full utterance up to phrase_time_limit (25.0s) with natural pause handling.
        Returns lowercase string of recognized speech, or empty string on silence/error.
        """
        import time

        t_start = time.time()
        audio = None
        try:
            with self._get_microphone() as source:
                print(f"[Listener] Listening for command (Timeout: {timeout:.1f}s, Max Duration: {phrase_time_limit:.1f}s, Pause: {self.recognizer.pause_threshold:.1f}s)...")
                audio = self.recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=phrase_time_limit,
                )
        except sr.WaitTimeoutError:
            self.last_capture_duration = time.time() - t_start
            return ""
        except Exception as mic_err:
            self.last_capture_duration = time.time() - t_start
            print(f"[Microphone Error]: {mic_err}")
            return ""

        self.last_capture_duration = time.time() - t_start

        if audio is None:
            return ""

        t_rec_start = time.time()
        text = ""
        engine_used = "google"
        if self._use_groq():
            text = self._recognize_groq(audio)
            if text:
                engine_used = "groq-whisper"
        if not text:
            text = self._recognize_google(audio)

        self.last_recognition_duration = time.time() - t_rec_start
        cleaned_text = text.strip().lower() if text else ""
        if cleaned_text:
            print(
                f"[Audio Latency] Capture: {self.last_capture_duration:.2f}s | "
                f"STT ({engine_used}): {self.last_recognition_duration:.2f}s | "
                f"Heard: '{cleaned_text}'"
            )
        return cleaned_text

    # ------------------------------------------------------------------ STT engines
    def _use_groq(self) -> bool:
        engine = os.getenv("STT_ENGINE", STT_ENGINE).strip().lower()
        if engine == "google":
            return False
        return bool(os.getenv("GROQ_API_KEY", "").strip()) and self._groq_failures < 3

    def _recognize_groq(self, audio) -> str:
        """Transcribe with Groq's free Whisper endpoint. Returns '' on failure."""
        try:
            wav = audio.get_wav_data(convert_rate=16000, convert_width=2)
        except Exception:
            return ""
        # Very short clips are almost always noise; Whisper tends to hallucinate on them.
        if len(wav) < 16000 * 2 * 0.35:
            return ""
        try:
            resp = requests.post(
                f"{os.getenv('GROQ_BASE_URL', GROQ_BASE_URL).rstrip('/')}/audio/transcriptions",
                headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '').strip()}"},
                files={"file": ("speech.wav", wav, "audio/wav")},
                data={
                    "model": os.getenv("GROQ_STT_MODEL", GROQ_STT_MODEL),
                    "language": os.getenv("STT_LANGUAGE", STT_LANGUAGE),
                    "response_format": "json",
                    "temperature": "0",
                },
                timeout=15,
            )
        except requests.RequestException as err:
            self._groq_failures += 1
            print(f"[Listener] Groq Whisper unavailable ({err}); falling back to Google.")
            return ""
        if resp.status_code != 200:
            if resp.status_code in (401, 403):
                self._groq_failures = 99
            else:
                self._groq_failures += 1
            print(f"[Listener] Groq Whisper HTTP {resp.status_code}; falling back to Google.")
            return ""
        self._groq_failures = 0
        try:
            text = str(resp.json().get("text", "")).strip()
        except ValueError:
            return ""
        if _normalize(text) in _WHISPER_HALLUCINATIONS:
            return ""
        return text

    def _recognize_google(self, audio) -> str:
        try:
            return self.recognizer.recognize_google(audio)
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as req_err:
            print(f"[Speech Service Unavailable]: {req_err}")
            return ""
        except Exception as e:
            print(f"[Speech Recognition Error]: {e}")
            return ""


# Global singleton instance for backward compatibility
_listener_instance = Listener()


def listen(timeout: float = LISTEN_TIMEOUT, phrase_time_limit: float = PHRASE_TIME_LIMIT) -> str:
    """Standalone listen function maintaining backward compatibility."""
    return _listener_instance.listen(timeout=timeout, phrase_time_limit=phrase_time_limit)
