import speech_recognition as sr
from config import (
    LISTEN_TIMEOUT,
    PHRASE_TIME_LIMIT,
    PAUSE_THRESHOLD,
    NON_SPEAKING_DURATION,
    MICROPHONE_DEVICE_INDEX,
)


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
        try:
            text = self.recognizer.recognize_google(audio)
            cleaned_text = text.strip().lower()
            self.last_recognition_duration = time.time() - t_rec_start
            print(
                f"[Audio Latency] Capture: {self.last_capture_duration:.2f}s | "
                f"STT: {self.last_recognition_duration:.2f}s | "
                f"Heard: '{cleaned_text}'"
            )
            return cleaned_text
        except sr.UnknownValueError:
            self.last_recognition_duration = time.time() - t_rec_start
            return ""
        except sr.RequestError as req_err:
            self.last_recognition_duration = time.time() - t_rec_start
            print(f"[Speech Service Unavailable]: {req_err}")
            return ""
        except Exception as e:
            self.last_recognition_duration = time.time() - t_rec_start
            print(f"[Speech Recognition Error]: {e}")
            return ""


# Global singleton instance for backward compatibility
_listener_instance = Listener()


def listen(timeout: float = LISTEN_TIMEOUT, phrase_time_limit: float = PHRASE_TIME_LIMIT) -> str:
    """Standalone listen function maintaining backward compatibility."""
    return _listener_instance.listen(timeout=timeout, phrase_time_limit=phrase_time_limit)
