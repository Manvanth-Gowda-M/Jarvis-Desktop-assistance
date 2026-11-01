"""
assistant/audio_coordinator.py
==============================
Coordinates audio input, speech output, wake-word detection, and state transitions
for the Jarvis desktop application without competing microphone streams.
"""

from __future__ import annotations

import enum
import threading
import time
from typing import Callable, Optional

from assistant.listen import Listener
from assistant.speech import Speaker, prepare_spoken_answer, normalize_phrase
from assistant.router import route_request
from assistant.wake_word import WakeWordDetector
from config import FOLLOW_UP_ENABLED, FOLLOW_UP_TIMEOUT, FOLLOW_UP_MAX_TURNS, WAKE_CHIME

_DISMISSALS = {
    "no", "nope", "no thanks", "no thank you", "that s all", "thats all", "that is all", "nothing",
    "never mind", "nevermind", "i m good", "im good", "all good", "thanks", "thank you", "thanks jarvis",
    "thank you jarvis", "cancel", "stop", "goodbye", "bye",
}


def _normalize_dismissal(text: str) -> bool:
    """True when a follow-up utterance just ends the conversation."""
    return normalize_phrase(text) in _DISMISSALS


class AssistantState(enum.Enum):
    STANDBY = "standby"
    LISTENING = "listening"
    THINKING = "thinking"
    EXECUTING = "executing"
    SPEAKING = "speaking"
    PAUSED = "paused"
    ERROR = "error"
    SUCCESS = "success"


class AudioCoordinator:
    """Coordinates microphone listening, wake-word detection, speech output, and assistant states."""

    def __init__(
        self,
        on_state_change: Optional[Callable[[AssistantState, str], None]] = None,
        on_result: Optional[Callable[[str, str], None]] = None,
        microphone_device_index: Optional[int] = None,
        wake_word_device_index: Optional[int] = None,
        on_heard: Optional[Callable[[str], None]] = None,
    ):
        self.on_state_change = on_state_change
        self.on_result = on_result
        self.on_heard = on_heard

        self._state = AssistantState.STANDBY
        self._state_lock = threading.Lock()
        self._audio_lock = threading.Lock()
        self._audio_owner: Optional[str] = None  # None, "wake_word", or "active_listener"
        self._is_paused = False
        self._is_running = True

        self.listener = Listener(device_index=microphone_device_index)
        self.speaker = Speaker()
        self.wake_detector = WakeWordDetector(
            on_wake_callback=self._handle_wake_word_detected,
            device_index=wake_word_device_index,
        )

        self._worker_thread: Optional[threading.Thread] = None
        self._speech_interrupted = False
        self._last_should_exit = False
        self.follow_up_enabled = FOLLOW_UP_ENABLED
        self.wake_chime = WAKE_CHIME

    @property
    def state(self) -> AssistantState:
        return self._state

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def audio_owner(self) -> Optional[str]:
        """Track current microphone owner: 'wake_word', 'active_listener', or None."""
        return self._audio_owner

    def set_state(self, new_state: AssistantState, detail: str = ""):
        """Update current assistant state and notify listener callback."""
        cur = threading.current_thread()
        with self._state_lock:
            old_state = self._state
            self._state = new_state
        print(f"[Coordinator] State transition: {old_state.value.upper()} -> {new_state.value.upper()} ({detail}) [Thread: {cur.name}({cur.ident})]")
        if self.on_state_change:
            try:
                self.on_state_change(new_state, detail)
            except Exception as e:
                print(f"[Coordinator] State callback error: {e}")

    def pause_listening(self):
        """Pause all microphone listening and return to paused state."""
        self._is_paused = True
        self.wake_detector.stop_listening()
        with self._audio_lock:
            self._audio_owner = None
        self.speaker.stop()
        self.set_state(AssistantState.PAUSED, "Listening paused.")

    def resume_listening(self):
        """Resume standby listening."""
        self._is_paused = False
        self.set_state(AssistantState.STANDBY, "Ready. Waiting for command.")
        self._resume_wake_detector_if_ready()

    def toggle_pause(self):
        if self._is_paused:
            self.resume_listening()
        else:
            self.pause_listening()

    def stop_speaking(self):
        """Immediately stop active speech synthesis."""
        self.speaker.stop()
        if self._state == AssistantState.SPEAKING:
            self.set_state(AssistantState.STANDBY, "Speech cancelled.")

    def trigger_listening(self, prompt_text: str = ""):
        """Trigger active listening (e.g. from orb click, hotkey, or wake word)."""
        if self._is_paused:
            return

        # If already processing, cancel current speech or wait
        if self._state == AssistantState.SPEAKING:
            self.stop_speaking()

        with self._state_lock:
            if self._state in (AssistantState.LISTENING, AssistantState.THINKING, AssistantState.EXECUTING):
                return

        cur = threading.current_thread()
        print(f"[Coordinator] Initiating listening handoff [Caller Thread: {cur.name}({cur.ident})]")

        # 1. Stop wake-word detector and wait for its audio stream to be completely released
        self.wake_detector.stop_listening()
        with self._audio_lock:
            if self._audio_owner == "wake_word":
                self._audio_owner = None
        print(f"[Coordinator] Wake-word stream released. Safe to acquire active listener.")

        # 2. Only after old audio stream is released, transition to LISTENING
        self.set_state(AssistantState.LISTENING, "Listening for your command...")

        # 3. Run user capture and execution in a dedicated background worker
        self._worker_thread = threading.Thread(
            target=self._capture_and_process_task,
            args=(prompt_text,),
            name="AudioCaptureWorker",
            daemon=True,
        )
        self._worker_thread.start()

    def process_text_command(self, text: str):
        """Process a typed text command from the control panel."""
        if not text.strip():
            return

        if self._state == AssistantState.SPEAKING:
            self.stop_speaking()

        self.wake_detector.stop_listening()
        with self._audio_lock:
            if self._audio_owner == "wake_word":
                self._audio_owner = None
        self.set_state(AssistantState.THINKING, f"Processing: '{text}'")
        self._worker_thread = threading.Thread(
            target=self._execute_command,
            args=(text,),
            name="CommandExecutionWorker",
            daemon=True,
        )
        self._worker_thread.start()

    def _handle_wake_word_detected(self):
        """Internal callback invoked when openWakeWord detects 'Hey Jarvis'."""
        cur = threading.current_thread()
        print(f"[Coordinator] Wake word detected -> Activating listening window... [Thread: {cur.name}({cur.ident})]")
        self.trigger_listening()

    def _capture_and_process_task(self, prompt_text: str = ""):
        """Background worker: captures speech, executes it, then listens for follow-ups.

        After Jarvis answers a voice command it keeps the microphone open for a few
        seconds so you can continue the conversation without repeating "Hey Jarvis".
        """
        cur = threading.current_thread()
        print(f"[Coordinator] AudioCaptureWorker started [Thread: {cur.name}({cur.ident})]")
        try:
            command = prompt_text
            if not command:
                if self.wake_chime:
                    try:
                        from assistant import sounds
                        sounds.play("wake")
                        time.sleep(0.32)  # let the chime finish before the mic opens
                    except Exception:
                        pass
                command = self._listen_once()

            if not command:
                self.set_state(AssistantState.STANDBY, "No speech detected.")
                self._resume_wake_detector_if_ready()
                return

            turns = 0
            while command and not self._is_paused and self._is_running:
                print(f"[Coordinator] Heard command: '{command}'")
                if self.on_heard:
                    try:
                        self.on_heard(command)
                    except Exception as e:
                        print(f"[Coordinator] Heard callback error: {e}")
                completed = self._execute_command(command, resume_after=False)
                turns += 1
                if not (self.follow_up_enabled and completed and turns < FOLLOW_UP_MAX_TURNS):
                    break
                if self._is_paused or not self._is_running or self._last_should_exit:
                    break
                self.set_state(AssistantState.LISTENING, "Listening for a follow-up...")
                command = self._listen_once(timeout=FOLLOW_UP_TIMEOUT)
                if command and _normalize_dismissal(command):
                    command = ""

        except Exception as exc:
            print(f"[Coordinator] Error during speech capture: {exc}")
            self.set_state(AssistantState.ERROR, f"Capture error: {exc}")
            time.sleep(1.5)
        finally:
            if not self._is_paused:
                self.set_state(AssistantState.STANDBY, "Ready.")
                self._resume_wake_detector_if_ready()

    def _listen_once(self, timeout: float | None = None) -> str:
        cur = threading.current_thread()
        with self._audio_lock:
            self._audio_owner = "active_listener"
        print(f"[Coordinator] Microphone ownership acquired: active_listener [Thread: {cur.name}({cur.ident})]")
        try:
            if timeout is None:
                return self.listener.listen()
            return self.listener.listen(timeout=timeout)
        finally:
            with self._audio_lock:
                self._audio_owner = None
            print(f"[Coordinator] Microphone ownership released: active_listener [Thread: {cur.name}({cur.ident})]")

    def _execute_command(self, command: str, resume_after: bool = True) -> bool:
        """Execute command through Jarvis router (local fast path or AI agent).

        Returns True when the spoken answer finished without interruption.
        """
        completed = False
        self._last_should_exit = False
        try:
            self.set_state(AssistantState.THINKING, "Thinking...")

            # Run through existing router
            result = route_request(command)
            self._last_should_exit = bool(getattr(result, "should_exit", False))

            detailed = result.detailed_text or "Task complete."
            spoken = result.spoken_text or detailed

            print(f"[Coordinator] Response: {detailed}")

            # Notify UI of the result
            if self.on_result:
                try:
                    self.on_result(detailed, spoken)
                except Exception as e:
                    print(f"[Coordinator] Result callback error: {e}")

            # Transition to speaking state
            self.set_state(AssistantState.SPEAKING, spoken)

            # Speak concise response
            completed = bool(self.speaker.speak(spoken))

            if completed:
                self.set_state(AssistantState.SUCCESS, "Done.")
                time.sleep(0.2)
            else:
                self.set_state(AssistantState.STANDBY, "Speech interrupted.")

        except Exception as exc:
            print(f"[Coordinator] Execution error: {exc}")
            self.set_state(AssistantState.ERROR, f"Error: {exc}")
            time.sleep(2.0)
            completed = False

        finally:
            if resume_after and not self._is_paused:
                self.set_state(AssistantState.STANDBY, "Ready.")
                self._resume_wake_detector_if_ready()
        return completed

    def announce(self, text: str, speak: bool = True):
        """Proactively tell the user something (reminders, greetings) without a command."""
        text = str(text or "").strip()
        if not text:
            return
        if self.on_result:
            try:
                self.on_result(text, text)
            except Exception as e:
                print(f"[Coordinator] Result callback error: {e}")
        if not speak or self._is_paused:
            return

        def _run():
            # Wait (briefly) for any in-flight interaction to finish.
            deadline = time.time() + 30
            while time.time() < deadline and self._state not in (AssistantState.STANDBY, AssistantState.SUCCESS):
                time.sleep(0.2)
            if self._is_paused or not self._is_running:
                return
            self.wake_detector.stop_listening()
            with self._audio_lock:
                if self._audio_owner == "wake_word":
                    self._audio_owner = None
            try:
                self.set_state(AssistantState.SPEAKING, text)
                self.speaker.speak(prepare_spoken_answer(text), enable_voice_interrupt=False)
            finally:
                if not self._is_paused:
                    self.set_state(AssistantState.STANDBY, "Ready.")
                    self._resume_wake_detector_if_ready()

        threading.Thread(target=_run, name="AnnounceWorker", daemon=True).start()

    def _resume_wake_detector_if_ready(self):
        """Restart wake-word detector when returning to standby."""
        if not self._is_paused and self.wake_detector.is_available:
            with self._audio_lock:
                self._audio_owner = "wake_word"
            cur = threading.current_thread()
            print(f"[Coordinator] Resuming wake-word monitoring [Thread: {cur.name}({cur.ident})] [Mic Owner: {self._audio_owner}]")
            self.wake_detector.start_listening()

    def shutdown(self):
        """Clean shutdown of all background threads, microphone streams, and audio."""
        cur = threading.current_thread()
        print(f"[Coordinator] Initiating shutdown [Thread: {cur.name}({cur.ident})]")
        self._is_running = False
        self._is_paused = True
        self.wake_detector.stop_listening()
        with self._audio_lock:
            self._audio_owner = None
        self.speaker.stop()
        if self._worker_thread and self._worker_thread.is_alive():
            if threading.current_thread() is not self._worker_thread:
                self._worker_thread.join(timeout=1.0)
        self.set_state(AssistantState.STANDBY, "Shutdown complete.")
        print(f"[Coordinator] Shutdown complete.")

