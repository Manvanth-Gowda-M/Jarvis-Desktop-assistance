"""Unit tests for voice and keyboard speech interruption.

Tests all interruption mechanics using mocks for hardware, SAPI COM, and microphone.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from assistant.speech import (
    Speaker,
    is_interruption_phrase,
    normalize_phrase,
    STOP_PHRASES,
)


class TestPhraseNormalization:
    """Requirement 4 & 10: Robust normalization across punctuation, casing, and spaces."""

    def test_normalize_strips_punctuation_and_casing(self):
        assert normalize_phrase("Jarvis, Stop!") == "jarvis stop"
        assert normalize_phrase("  STOP TALKING...  ") == "stop talking"
        assert normalize_phrase("Be Quiet!!??") == "be quiet"
        assert normalize_phrase("Cancel-speech.") == "cancel speech"
        assert normalize_phrase("") == ""
        assert normalize_phrase(None) == ""


class TestInterruptionPhraseDetection:
    """Verify that stop phrases are correctly recognized and self-echo is filtered out."""

    def test_required_specification_phrases(self):
        """Requirement 4: Support 'Jarvis, stop', 'stop', 'stop talking', 'stop speaking', 'be quiet', 'cancel speech'."""
        assert is_interruption_phrase("Jarvis, stop")
        assert is_interruption_phrase("stop")
        assert is_interruption_phrase("stop talking")
        assert is_interruption_phrase("stop speaking")
        assert is_interruption_phrase("be quiet")
        assert is_interruption_phrase("cancel speech")

    def test_phrase_casing_and_punctuation(self):
        """Requirement 4: Punctuation and casing variations."""
        assert is_interruption_phrase("JARVIS, STOP!")
        assert is_interruption_phrase("Stop!")
        assert is_interruption_phrase("Stop Talking.")
        assert is_interruption_phrase("Be Quiet...")
        assert is_interruption_phrase("CANCEL SPEECH!!")

    def test_phrase_with_extra_words(self):
        """Requirement 4: Extra conversational words surrounding stop command."""
        assert is_interruption_phrase("hey jarvis, stop talking now")
        assert is_interruption_phrase("please stop speaking")
        assert is_interruption_phrase("jarvis, please be quiet")
        assert is_interruption_phrase("could you stop please")
        assert is_interruption_phrase("jarvis please stop")
        assert is_interruption_phrase("shut up")

    def test_non_interruption_phrases(self):
        """Verify normal queries do not trigger false positive interruptions."""
        assert not is_interruption_phrase("what time is it")
        assert not is_interruption_phrase("tell me about python threads")
        assert not is_interruption_phrase("open google chrome")
        assert not is_interruption_phrase("continue speaking")
        assert not is_interruption_phrase("")
        assert not is_interruption_phrase(None)

    def test_avoid_self_echo_when_jarvis_speaks_stop_word(self):
        """Requirement 6: Avoid mistaking Jarvis's own spoken output for a stop command.

        If Jarvis is reciting a response like 'Here are ways to stop a background service',
        an echo picked up by the microphone should not interrupt speech.
        """
        spoken_text = "Here are three ways to stop a background service on Windows."

        # Echo of Jarvis's own words
        assert not is_interruption_phrase("stop a background service", current_spoken_text=spoken_text)
        assert not is_interruption_phrase("ways to stop a", current_spoken_text=spoken_text)

        # An explicit command like 'Jarvis, stop' or 'be quiet' SHOULD still interrupt
        assert is_interruption_phrase("Jarvis, stop", current_spoken_text=spoken_text)
        assert is_interruption_phrase("be quiet", current_spoken_text=spoken_text)
        assert is_interruption_phrase("stop talking", current_spoken_text=spoken_text)


class TestSapiSpeechCancellation:
    """Verify that speech cancellation calls SAPI purge operation (Speak('', 2))."""

    def test_cancel_speech_method(self):
        speaker = Speaker()
        mock_sapi = MagicMock()
        speaker._current_speaker = mock_sapi

        assert speaker.cancel_speech() is True
        # Verify SVSFPurgeBeforeSpeak = 2 was passed to purge speech
        mock_sapi.Speak.assert_called_once_with("", 2)

    def test_cancel_speech_when_not_speaking(self):
        speaker = Speaker()
        speaker._current_speaker = None
        assert speaker.cancel_speech() is False

    @patch("assistant.speech.win32com.client.Dispatch")
    def test_speech_executes_asynchronously(self, mock_dispatch):
        mock_sapi = MagicMock()
        mock_sapi.WaitUntilDone.return_value = True  # Finishes immediately
        mock_dispatch.return_value = mock_sapi

        speaker = Speaker()
        res = speaker.speak("Short test phrase", enable_voice_interrupt=False)
        assert res is True
        # Verify SVSFlagsAsync = 1 was passed
        mock_sapi.Speak.assert_called_with("Short test phrase", 1)


class TestKeyboardInterruption:
    """Requirement 7: Space or Escape should immediately interrupt speech."""

    @patch("assistant.speech.msvcrt")
    @patch("assistant.speech.win32com.client.Dispatch")
    def test_space_key_interrupts_speech(self, mock_dispatch, mock_msvcrt):
        mock_sapi = MagicMock()
        # Simulate speech that takes several polling intervals
        mock_sapi.WaitUntilDone.side_effect = [False, False, True]
        mock_dispatch.return_value = mock_sapi

        # Keyboard press: space
        mock_msvcrt.kbhit.side_effect = [True]
        mock_msvcrt.getch.return_value = b" "

        speaker = Speaker()
        result = speaker.speak("Long test text that should be interrupted", enable_voice_interrupt=False)

        assert result is False  # Interrupted
        # Verify SAPI purge was called
        mock_sapi.Speak.assert_any_call("", 2)

    @patch("assistant.speech.msvcrt")
    @patch("assistant.speech.win32com.client.Dispatch")
    def test_escape_key_interrupts_speech(self, mock_dispatch, mock_msvcrt):
        mock_sapi = MagicMock()
        mock_sapi.WaitUntilDone.side_effect = [False, False, True]
        mock_dispatch.return_value = mock_sapi

        # Keyboard press: escape
        mock_msvcrt.kbhit.side_effect = [True]
        mock_msvcrt.getch.return_value = b"\x1b"

        speaker = Speaker()
        result = speaker.speak("Another test text", enable_voice_interrupt=False)

        assert result is False
        mock_sapi.Speak.assert_any_call("", 2)


class TestVoiceInterruptionAndCleanup:
    """Requirements 2, 4, 5, 8: Voice trigger, microphone error handling, and cleanup."""

    @patch("assistant.speech.sr")
    @patch("assistant.speech.win32com.client.Dispatch")
    def test_voice_interruption_triggers_purge(self, mock_dispatch, mock_sr):
        mock_sapi = MagicMock()
        mock_dispatch.return_value = mock_sapi

        # Captured callback from listen_in_background
        captured_callback = None
        mock_stop_listener = MagicMock()

        def fake_listen_in_background(source, callback, phrase_time_limit=None):
            nonlocal captured_callback
            captured_callback = callback
            return mock_stop_listener

        mock_recognizer = MagicMock()
        mock_recognizer.listen_in_background.side_effect = fake_listen_in_background
        mock_sr.Recognizer.return_value = mock_recognizer
        mock_sr.Microphone.return_value = MagicMock()

        # Speech loop runs while waiting
        poll_count = 0

        def fake_wait(ms):
            nonlocal poll_count
            poll_count += 1
            if poll_count == 2 and captured_callback:
                # Simulate recognizer hearing 'stop talking'
                mock_audio = MagicMock()
                mock_recognizer.recognize_google.return_value = "stop talking"
                captured_callback(mock_recognizer, mock_audio)
                time.sleep(0.05)
            if poll_count > 5:
                return True
            return False

        mock_sapi.WaitUntilDone.side_effect = fake_wait

        speaker = Speaker()
        res = speaker.speak("Some spoken response", enable_voice_interrupt=True)

        assert res is False
        # SAPI purge operation was performed
        mock_sapi.Speak.assert_any_call("", 2)
        # Requirement 5 & 8: Background listener was stopped with wait_for_stop=True
        mock_stop_listener.assert_called_once_with(wait_for_stop=True)

    @patch("assistant.speech.sr")
    @patch("assistant.speech.win32com.client.Dispatch")
    def test_microphone_init_failure_degrades_gracefully(self, mock_dispatch, mock_sr):
        mock_sapi = MagicMock()
        mock_sapi.WaitUntilDone.return_value = True
        mock_dispatch.return_value = mock_sapi

        # Microphone unavailable or busy
        mock_sr.Microphone.side_effect = OSError("Microphone device busy")
        mock_sr.Recognizer.return_value = MagicMock()

        speaker = Speaker()
        # Should not crash; keyboard fallback remains active
        res = speaker.speak("Testing graceful degradation", enable_voice_interrupt=True)
        assert res is True
        mock_sapi.Speak.assert_called_with("Testing graceful degradation", 1)


class TestJarvisListeningLoopReturn:
    """Requirement 8: After interruption, return to the assistant listening loop."""

    def test_jarvis_handle_continues_loop_after_interruption(self):
        from assistant.jarvis import Jarvis
        with patch.object(Jarvis, "__init__", return_value=None):
            jarvis = Jarvis()
            jarvis.voice_mode = True
            jarvis.speaker = MagicMock()
            # Speaker.speak returns False (interrupted)
            jarvis.speaker.speak.return_value = False

            with patch("assistant.jarvis.route_request") as mock_route:
                mock_route.return_value = MagicMock(
                    detailed_text="Detailed answer",
                    spoken_text="Spoken answer",
                    mode_change=None,
                    should_exit=False,
                )
                continue_loop = jarvis.handle("hello")
                # Must return True to continue the main listening loop
                assert continue_loop is True

