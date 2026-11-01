"""
tests/test_desktop_assistant.py
===============================
Automated tests for the Jarvis Desktop AI Companion:
- Wake-word manager & events
- State transitions (STANDBY -> LISTENING -> THINKING -> SPEAKING -> STANDBY)
- Speech interruption & microphone stream cleanup
- Application shutdown & worker cleanup
- Local action execution bypassing OpenRouter
- Desktop UI and widget rendering (headless)
"""

import os
import pytest
from unittest.mock import patch, MagicMock

# Force offscreen rendering for Qt tests in CI / headless environments
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from assistant.wake_word import (
    WakeWordDetector,
    get_wake_word_setup_instructions,
    find_jarvis_model_path,
    OWNModel,
)
from assistant.audio_coordinator import AudioCoordinator, AssistantState
from assistant.router import route_request


class TestWakeWordManager:
    """Test wake-word detection status, model verification, and setup instructions."""

    def test_setup_instructions_contain_clear_steps(self):
        instructions = get_wake_word_setup_instructions()
        assert "Hey Jarvis" in instructions
        assert "hey_jarvis_v0.1.onnx" in instructions
        assert "models" in instructions

    def test_detector_reports_unavailable_when_model_missing(self):
        with patch("assistant.wake_word.find_jarvis_model_path", return_value=None):
            detector = WakeWordDetector()
            assert detector.is_available is False
            assert "No local 'Hey Jarvis' model found" in detector.status_message

    def test_detector_initializes_with_mocked_model(self):
        mock_path = MagicMock()
        mock_path.name = "hey_jarvis_v0.1.onnx"
        mock_path.suffix = ".onnx"
        with patch("assistant.wake_word._OWW_AVAILABLE", True), \
             patch("assistant.wake_word.find_jarvis_model_path", return_value=mock_path), \
             patch("assistant.wake_word.OWWModel") as mock_oww:
            detector = WakeWordDetector()
            assert detector.is_available is True
            assert "Ready" in detector.status_message
            mock_oww.assert_called_once()

    def test_process_audio_chunk_triggers_callback(self):
        mock_path = MagicMock()
        mock_path.suffix = ".onnx"
        with patch("assistant.wake_word._OWW_AVAILABLE", True), \
             patch("assistant.wake_word.find_jarvis_model_path", return_value=mock_path), \
             patch("assistant.wake_word.OWWModel") as mock_oww:
            instance = MagicMock()
            instance.prediction_buffer = {"hey_jarvis": [0.85]}
            mock_oww.return_value = instance

            detector = WakeWordDetector(sensitivity=0.5)
            # Create dummy 16-bit PCM chunk (1280 samples = 2560 bytes)
            raw_audio = b"\x00" * 2560
            # 2 of 3 frames required for activation
            assert detector.process_audio_chunk(raw_audio) is False  # 1st hit (1 of 3)
            assert detector.process_audio_chunk(raw_audio) is True   # 2nd hit (2 of 3) -> Activated!
            instance.reset.assert_called_once()

    def test_detector_passes_all_three_model_paths_to_ownmodel(self):
        mock_path = MagicMock()
        mock_path.name = "hey_jarvis_v0.1.onnx"
        mock_path.suffix = ".onnx"
        mock_path.__str__.return_value = "/path/to/models/hey_jarvis_v0.1.onnx"

        with patch("assistant.wake_word._OWW_AVAILABLE", True), \
             patch("assistant.wake_word.find_jarvis_model_path", return_value=mock_path), \
             patch("assistant.wake_word.OWNModel") as mock_own:
            detector = WakeWordDetector()
            assert detector.is_available is True
            mock_own.assert_called_once()
            _, kwargs = mock_own.call_args
            assert kwargs["wakeword_models"] == [str(mock_path)]
            assert kwargs["inference_framework"] == "onnx"
            assert "melspectrogram.onnx" in kwargs["melspec_model_path"]
            assert "embedding_model.onnx" in kwargs["embedding_model_path"]

    def test_detector_handles_missing_supporting_model_files(self, tmp_path):
        mock_path = tmp_path / "hey_jarvis_v0.1.onnx"
        mock_path.write_bytes(b"dummy")

        with patch("assistant.wake_word._OWW_AVAILABLE", True), \
             patch("assistant.wake_word.find_jarvis_model_path", return_value=mock_path), \
             patch("assistant.wake_word.LOCAL_MODELS_DIR", tmp_path):
            # tmp_path has hey_jarvis_v0.1.onnx, but does NOT have melspectrogram.onnx or embedding_model.onnx
            detector = WakeWordDetector()
            assert detector.is_available is False
            assert "Missing or empty required model file" in detector.status_message
            assert detector._model is None

    def test_is_available_only_after_successful_initialization(self):
        mock_path = MagicMock()
        mock_path.name = "hey_jarvis_v0.1.onnx"
        mock_path.suffix = ".onnx"

        # Case: Initialization exception occurs
        with patch("assistant.wake_word._OWW_AVAILABLE", True), \
             patch("assistant.wake_word.find_jarvis_model_path", return_value=mock_path), \
             patch("assistant.wake_word.OWNModel", side_effect=RuntimeError("Corrupt ONNX graph")):
            detector = WakeWordDetector()
            assert detector.is_available is False
            assert "Failed to load model" in detector.status_message
            assert "Corrupt ONNX graph" in detector.status_message
            assert detector._model is None

    def test_local_onnx_model_initialization_smoke(self):
        """Smoke test using the real local downloaded model files in models/."""
        from assistant.wake_word import LOCAL_MODELS_DIR
        hey_jarvis = LOCAL_MODELS_DIR / "hey_jarvis_v0.1.onnx"
        melspec = LOCAL_MODELS_DIR / "melspectrogram.onnx"
        embedding = LOCAL_MODELS_DIR / "embedding_model.onnx"
        if not (hey_jarvis.is_file() and melspec.is_file() and embedding.is_file()):
            pytest.skip("Local model files not present on disk")

        detector = WakeWordDetector()
        assert detector.is_available is True
        assert "Ready" in detector.status_message
        assert detector._model is not None


class TestAudioCoordinatorStateTransitions:
    """Test state machine, command execution, and returning to standby."""

    def test_initial_state_is_standby(self):
        coordinator = AudioCoordinator()
        assert coordinator.state == AssistantState.STANDBY
        assert coordinator.is_paused is False

    def test_pause_and_resume_transitions(self):
        states_recorded = []
        coordinator = AudioCoordinator(
            on_state_change=lambda st, msg: states_recorded.append(st)
        )

        coordinator.pause_listening()
        assert coordinator.state == AssistantState.PAUSED
        assert coordinator.is_paused is True

        coordinator.resume_listening()
        assert coordinator.state == AssistantState.STANDBY
        assert coordinator.is_paused is False
        assert AssistantState.PAUSED in states_recorded
        assert AssistantState.STANDBY in states_recorded

    @patch("assistant.audio_coordinator.route_request")
    def test_execute_command_lifecycle_to_standby(self, mock_route):
        from assistant.commands import ActionResult
        mock_route.return_value = ActionResult(
            handled=True,
            detailed_text="Volume set to 40%.",
            spoken_text="Volume set to 40%.",
        )

        states_recorded = []
        coordinator = AudioCoordinator(
            on_state_change=lambda st, msg: states_recorded.append(st)
        )
        coordinator.speaker = MagicMock()
        coordinator.speaker.speak.return_value = True

        coordinator._execute_command("reduce the volume to 40%")

        # Must cycle through THINKING -> SPEAKING -> SUCCESS -> STANDBY
        assert AssistantState.THINKING in states_recorded
        assert AssistantState.SPEAKING in states_recorded
        assert AssistantState.STANDBY == coordinator.state

    @patch("assistant.audio_coordinator.route_request")
    def test_speech_interruption_returns_to_standby(self, mock_route):
        from assistant.commands import ActionResult
        mock_route.return_value = ActionResult(
            handled=True,
            detailed_text="Detailed response here.",
            spoken_text="Spoken response here.",
        )

        coordinator = AudioCoordinator()
        coordinator.speaker = MagicMock()
        # Simulate user pressing Space/Escape or saying "Jarvis, stop" -> speak returns False
        coordinator.speaker.speak.return_value = False

        coordinator._execute_command("tell me a long story")
        assert coordinator.state == AssistantState.STANDBY

    def test_shutdown_stops_all_streams(self):
        coordinator = AudioCoordinator()
        coordinator.wake_detector = MagicMock()
        coordinator.speaker = MagicMock()

        coordinator.shutdown()
        assert coordinator.is_paused is True
        coordinator.wake_detector.stop_listening.assert_called_once()
        coordinator.speaker.stop.assert_called_once()


class TestLocalActionsBypassAIWhenOffline:
    """Verify system controls execute locally without OpenRouter."""

    def test_volume_and_brightness_execute_locally(self):
        with patch("assistant.router.run_agent") as mock_agent:
            res_vol = route_request("reduce the volume to 40%")
            assert res_vol.handled is True
            assert "40%" in res_vol.detailed_text
            mock_agent.assert_not_called()

            res_bright = route_request("increase brightness")
            assert res_bright.handled is True
            mock_agent.assert_not_called()


class TestDesktopUIComponents:
    """Headless GUI tests verifying widget creation and signal emission."""

    def test_orb_and_control_panel_initialization(self):
        from PySide6.QtWidgets import QApplication
        from assistant.desktop_ui import JarvisOrbWidget, CompactControlPanel, UIBridge

        app = QApplication.instance()
        if app is None:
            app = QApplication([])

        coordinator = AudioCoordinator()
        bridge = UIBridge()

        orb = JarvisOrbWidget(coordinator=coordinator, bridge=bridge)
        panel = CompactControlPanel(orb=orb, coordinator=coordinator, bridge=bridge)

        assert orb.width() == 140
        assert orb.height() == 140
        assert panel.width() == 380

        # Test state change signal bridge
        bridge.state_changed.emit("listening", "Listening for speech...")
        assert orb.current_state == AssistantState.LISTENING
        assert panel.state_badge.text() == "LISTENING"

        coordinator.shutdown()

    def test_single_instance_mutex_prevents_duplicate(self):
        from assistant.desktop_ui import acquire_single_instance_mutex, release_single_instance_mutex
        test_mutex_name = "JarvisTestUniqueMutex123"
        first = acquire_single_instance_mutex(test_mutex_name)
        assert first is True

        second = acquire_single_instance_mutex(test_mutex_name)
        assert second is False

        release_single_instance_mutex()

    def test_run_desktop_app_duplicate_returns_immediately(self):
        from assistant.desktop_ui import run_desktop_app
        with patch("assistant.desktop_ui.acquire_single_instance_mutex", return_value=False):
            exit_code = run_desktop_app(check_single_instance=True)
            assert exit_code == 0


class TestWakeWordHandoffAndThreading:
    """Regression tests for wake-word activation, safe thread stop, and audio handoff."""

    def test_no_thread_attempts_to_join_itself(self):
        """Calling stop_listening from the detection thread itself must not attempt self-join."""
        import threading
        detector = WakeWordDetector()
        detector._running = True

        exception_raised = None

        def runner():
            nonlocal exception_raised
            try:
                # Inside runner thread, call stop_listening while self._thread is this thread
                detector.stop_listening()
            except Exception as e:
                exception_raised = e

        t = threading.Thread(target=runner, name="TestSelfThread")
        detector._thread = t
        t.start()
        t.join(timeout=2.0)

        assert exception_raised is None
        assert detector._running is False

    def test_wake_word_triggers_coordinator_and_transitions_to_listening(self):
        """Wake-word detection signals the coordinator to transition to LISTENING."""
        recorded_states = []
        coordinator = AudioCoordinator(
            on_state_change=lambda st, msg: recorded_states.append(st)
        )
        coordinator.wake_detector = MagicMock()
        coordinator.wake_detector.stop_listening = MagicMock()
        coordinator.listener = MagicMock()
        coordinator.listener.listen.return_value = ""

        # Trigger wake word callback
        coordinator._handle_wake_word_detected()

        # Wake detector must be stopped first
        coordinator.wake_detector.stop_listening.assert_called_once()
        assert AssistantState.LISTENING in recorded_states

        # Allow worker to finish capture
        if coordinator._worker_thread:
            coordinator._worker_thread.join(timeout=2.0)

        assert coordinator.state == AssistantState.STANDBY

    def test_safe_microphone_handoff_sequence(self):
        """Wake-word stream must be stopped before active listener acquires mic."""
        events = []
        coordinator = AudioCoordinator()

        mock_wake = MagicMock()
        def on_stop_wake():
            events.append("wake_stopped")
        mock_wake.stop_listening.side_effect = on_stop_wake
        coordinator.wake_detector = mock_wake

        def on_listen(*args, **kwargs):
            events.append("listener_started")
            return "hello"
        coordinator.listener = MagicMock()
        coordinator.listener.listen.side_effect = on_listen

        coordinator.speaker = MagicMock()
        coordinator.speaker.speak.return_value = True

        with patch("assistant.audio_coordinator.route_request") as mock_route:
            from assistant.commands import ActionResult
            mock_route.return_value = ActionResult(handled=True, detailed_text="Hi", spoken_text="Hi")

            coordinator.trigger_listening()

            if coordinator._worker_thread:
                coordinator._worker_thread.join(timeout=2.0)

        # "wake_stopped" MUST precede "listener_started"
        assert events == ["wake_stopped", "listener_started"]

    def test_no_competing_microphone_streams(self):
        """Audio ownership tracking guarantees single owner."""
        coordinator = AudioCoordinator()

        # In standby, resuming wake detector sets owner to "wake_word"
        coordinator.wake_detector = MagicMock()
        coordinator.wake_detector.is_available = True
        coordinator._resume_wake_detector_if_ready()
        assert coordinator.audio_owner == "wake_word"

        # Triggering listening stops wake detector and clears owner before listener
        observed_owners = []
        def on_listen(*args, **kwargs):
            observed_owners.append(coordinator.audio_owner)
            return ""
        coordinator.listener = MagicMock()
        coordinator.listener.listen.side_effect = on_listen

        coordinator.trigger_listening()
        if coordinator._worker_thread:
            coordinator._worker_thread.join(timeout=2.0)

        # During listen, ownership was "active_listener"
        assert observed_owners == ["active_listener"]

    def test_shutdown_cleanup_releases_resources(self):
        """Shutdown releases all audio ownership and terminates cleanly."""
        coordinator = AudioCoordinator()
        coordinator.wake_detector = MagicMock()
        coordinator.speaker = MagicMock()

        coordinator.shutdown()
        assert coordinator.is_paused is True
        assert coordinator.audio_owner is None
        coordinator.wake_detector.stop_listening.assert_called_once()
        coordinator.speaker.stop.assert_called_once()


class TestWakeWordSensitivityCalibration:
    """Test wake-word threshold calibration (0.35), 2-of-3 frame window, cooldown, and ambient rejection."""

    def test_scores_around_0_35_to_0_40_activate_with_2_of_3_frames(self):
        """Observed scores [0.35666, 0.35834] meet threshold in 2 of 3 frames and trigger."""
        mock_path = MagicMock()
        mock_path.suffix = ".onnx"
        with patch("assistant.wake_word._OWW_AVAILABLE", True), \
             patch("assistant.wake_word.find_jarvis_model_path", return_value=mock_path), \
             patch("assistant.wake_word.OWWModel") as mock_oww:
            instance = MagicMock()
            mock_oww.return_value = instance

            # Calibrated sensitivity 0.35, 2-of-3 window
            detector = WakeWordDetector(sensitivity=0.35, window_size=3, min_hits=2)
            raw_chunk = b"\x00" * 2560

            # Frame 1: score 0.35666 (1 hit) -> False
            instance.predict.return_value = {"hey_jarvis_v0.1": 0.35666}
            instance.prediction_buffer = {"hey_jarvis_v0.1": [0.35666]}
            assert detector.process_audio_chunk(raw_chunk) is False

            # Frame 2: score 0.35834 (2 hits in latest 2 frames) -> True (ACTIVATED)
            instance.predict.return_value = {"hey_jarvis_v0.1": 0.35834}
            instance.prediction_buffer = {"hey_jarvis_v0.1": [0.35834]}
            assert detector.process_audio_chunk(raw_chunk) is True
            instance.reset.assert_called_once()

    def test_single_spike_does_not_activate_without_second_hit(self):
        """A single isolated spike >= 0.35 does not trigger without a second confirmation frame."""
        mock_path = MagicMock()
        mock_path.suffix = ".onnx"
        with patch("assistant.wake_word._OWW_AVAILABLE", True), \
             patch("assistant.wake_word.find_jarvis_model_path", return_value=mock_path), \
             patch("assistant.wake_word.OWWModel") as mock_oww:
            instance = MagicMock()
            mock_oww.return_value = instance

            detector = WakeWordDetector(sensitivity=0.35, window_size=3, min_hits=2)
            raw_chunk = b"\x00" * 2560

            # Frame 1: spike to 0.38
            instance.predict.return_value = {"hey_jarvis_v0.1": 0.38}
            instance.prediction_buffer = {"hey_jarvis_v0.1": [0.38]}
            assert detector.process_audio_chunk(raw_chunk) is False

            # Frame 2: returns to normal speech 0.15
            instance.predict.return_value = {"hey_jarvis_v0.1": 0.15}
            instance.prediction_buffer = {"hey_jarvis_v0.1": [0.15]}
            assert detector.process_audio_chunk(raw_chunk) is False

            # Frame 3: returns to ambient 0.05
            instance.predict.return_value = {"hey_jarvis_v0.1": 0.05}
            instance.prediction_buffer = {"hey_jarvis_v0.1": [0.05]}
            assert detector.process_audio_chunk(raw_chunk) is False

            instance.reset.assert_not_called()

    def test_normal_speech_and_ambient_scores_do_not_activate(self):
        """Scores for conversational speech (0.10 - 0.30) and ambient noise (0.00001) never trigger."""
        mock_path = MagicMock()
        mock_path.suffix = ".onnx"
        with patch("assistant.wake_word._OWW_AVAILABLE", True), \
             patch("assistant.wake_word.find_jarvis_model_path", return_value=mock_path), \
             patch("assistant.wake_word.OWWModel") as mock_oww:
            instance = MagicMock()
            mock_oww.return_value = instance

            detector = WakeWordDetector(sensitivity=0.35, window_size=3, min_hits=2)
            raw_chunk = b"\x00" * 2560

            speech_and_ambient_scores = [0.00001, 0.00015, 0.05, 0.12, 0.22, 0.28, 0.30, 0.25, 0.10, 0.00001]
            for s in speech_and_ambient_scores:
                instance.predict.return_value = {"hey_jarvis_v0.1": s}
                instance.prediction_buffer = {"hey_jarvis_v0.1": [s]}
                assert detector.process_audio_chunk(raw_chunk) is False

            instance.reset.assert_not_called()

    def test_cooldown_and_window_reset_prevent_residual_retriggering(self):
        """After activation, the window is cleared and cooldown blocks immediate repeated triggers."""
        mock_path = MagicMock()
        mock_path.suffix = ".onnx"
        with patch("assistant.wake_word._OWW_AVAILABLE", True), \
             patch("assistant.wake_word.find_jarvis_model_path", return_value=mock_path), \
             patch("assistant.wake_word.OWWModel") as mock_oww:
            instance = MagicMock()
            mock_oww.return_value = instance

            detector = WakeWordDetector(sensitivity=0.35, cooldown_seconds=2.0, window_size=3, min_hits=2)
            raw_chunk = b"\x00" * 2560

            # 2 hits trigger activation
            instance.predict.return_value = {"hey_jarvis_v0.1": 0.40}
            instance.prediction_buffer = {"hey_jarvis_v0.1": [0.40]}
            assert detector.process_audio_chunk(raw_chunk) is False  # Frame 1 (1 hit)
            assert detector.process_audio_chunk(raw_chunk) is True   # Frame 2 (2 hits) -> Activated!

            # Detection window must be reset after activation
            assert len(detector._score_window) == 0

            # Immediate next high frame cannot trigger because cooldown is active
            assert detector.process_audio_chunk(raw_chunk) is False

            # Reset window to test fresh activation
            detector._score_window.clear()
            detector._last_trigger_time -= 2.1
            assert detector.process_audio_chunk(raw_chunk) is False  # Frame 1 after cooldown (1 hit)
            assert detector.process_audio_chunk(raw_chunk) is True   # Frame 2 after cooldown (2 hits) -> Activated!

    def test_exact_hey_jarvis_key_is_evaluated_and_logged(self, capsys):
        """Only the registered hey_jarvis key is scored, and the decision log includes max/threshold."""
        mock_path = MagicMock()
        mock_path.suffix = ".onnx"
        with patch("assistant.wake_word._OWW_AVAILABLE", True), \
             patch("assistant.wake_word.find_jarvis_model_path", return_value=mock_path), \
             patch("assistant.wake_word.OWWModel") as mock_oww:
            instance = MagicMock()
            instance.models = {"other_wakeword": object(), "hey_jarvis_v0.1": object()}
            mock_oww.return_value = instance

            detector = WakeWordDetector(sensitivity=0.35, window_size=3, min_hits=2)
            assert detector.prediction_key == "hey_jarvis_v0.1"

            raw_chunk = b"\x00" * 2560

            # An unrelated key with a perfect score must not cause activation.
            instance.predict.return_value = {"other_wakeword": 0.99, "hey_jarvis_v0.1": 0.10}
            assert detector.process_audio_chunk(raw_chunk) is False

            # The Hey Jarvis key itself is evaluated.
            instance.predict.return_value = {"other_wakeword": 0.99, "hey_jarvis_v0.1": 0.35666}
            assert detector.process_audio_chunk(raw_chunk) is False
            instance.predict.return_value = {"other_wakeword": 0.99, "hey_jarvis_v0.1": 0.35834}
            assert detector.process_audio_chunk(raw_chunk) is True

            out = capsys.readouterr().out
            assert "Evaluating key: 'hey_jarvis_v0.1'" in out
            assert "Max Score: 0.35834" in out
            assert "Threshold: 0.35" in out
            assert "Decision: ACTIVATED" in out

    def test_two_hits_must_fall_within_latest_three_frames(self):
        """Hits separated by more than the 3-frame window do not activate."""
        mock_path = MagicMock()
        mock_path.suffix = ".onnx"
        with patch("assistant.wake_word._OWW_AVAILABLE", True), \
             patch("assistant.wake_word.find_jarvis_model_path", return_value=mock_path), \
             patch("assistant.wake_word.OWWModel") as mock_oww:
            instance = MagicMock()
            mock_oww.return_value = instance

            detector = WakeWordDetector(sensitivity=0.35, window_size=3, min_hits=2)
            raw_chunk = b"\x00" * 2560

            scores = [0.36, 0.10, 0.10, 0.36, 0.10, 0.36]
            results = []
            for score in scores:
                instance.predict.return_value = {"hey_jarvis_v0.1": score}
                instance.prediction_buffer = {"hey_jarvis_v0.1": [score]}
                results.append(detector.process_audio_chunk(raw_chunk))

            # First 0.36 is out of window by the time the second arrives at frame 4;
            # activation happens only at frame 6 when two hits share the latest 3 frames.
            assert results == [False, False, False, False, False, True]

    def test_low_ambient_scores_never_activate(self):
        """Ambient baseline (~0.00001) stays far below threshold and never activates."""
        mock_path = MagicMock()
        mock_path.suffix = ".onnx"
        with patch("assistant.wake_word._OWW_AVAILABLE", True), \
             patch("assistant.wake_word.find_jarvis_model_path", return_value=mock_path), \
             patch("assistant.wake_word.OWWModel") as mock_oww:
            instance = MagicMock()
            mock_oww.return_value = instance

            detector = WakeWordDetector(sensitivity=0.35, cooldown_seconds=2.0, window_size=3, min_hits=2)
            raw_chunk = b"\x00" * 2560

            for score in [0.00001, 0.00002, 0.00001, 0.00003]:
                instance.predict.return_value = {"hey_jarvis_v0.1": score}
                instance.prediction_buffer = {"hey_jarvis_v0.1": [score]}
                assert detector.process_audio_chunk(raw_chunk) is False

            assert detector._last_trigger_time == 0.0
            instance.reset.assert_not_called()


