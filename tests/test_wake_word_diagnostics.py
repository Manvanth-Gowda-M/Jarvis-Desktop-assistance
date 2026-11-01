"""
tests/test_wake_word_diagnostics.py
===================================
Unit tests for the standalone wake-word live diagnostic tool.
"""

from unittest.mock import MagicMock
import numpy as np
import pytest

from diagnostics.wake_word_live_test import (
    calculate_rms,
    evaluate_detection_rule,
    resolve_model_paths,
    list_audio_input_devices,
)


class TestDiagnosticCalculations:
    """Test calculation functions in wake_word_live_test."""

    def test_calculate_rms_empty_returns_zero(self):
        assert calculate_rms(np.array([], dtype=np.int16)) == 0.0

    def test_calculate_rms_silence_returns_zero(self):
        zeros = np.zeros(1280, dtype=np.int16)
        assert calculate_rms(zeros) == 0.0

    def test_calculate_rms_dc_offset(self):
        constant = np.full(1280, 1000, dtype=np.int16)
        rms = calculate_rms(constant)
        assert pytest.approx(rms, rel=1e-3) == 1000.0

    def test_calculate_rms_sine_wave(self):
        # 16-bit sine wave with peak amplitude A: RMS = A / sqrt(2)
        amplitude = 10000.0
        t = np.linspace(0, 1.0, 16000, endpoint=False)
        sine = (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
        expected_rms = amplitude / np.sqrt(2)
        assert pytest.approx(calculate_rms(sine), rel=0.05) == expected_rms


class TestEvaluateDetectionRule:
    """Test sliding m-of-n window evaluation."""

    def test_empty_scores_returns_unactivated(self):
        res = evaluate_detection_rule([])
        assert res["activated"] is False
        assert res["activation_frame"] is None
        assert res["max_score"] == 0.0
        assert res["frames_above_threshold"] == 0

    def test_sub_threshold_scores_never_activate(self):
        scores = [0.001, 0.05, 0.12, 0.28, 0.34, 0.20, 0.01]
        res = evaluate_detection_rule(scores, threshold=0.35, window_size=3, min_hits=2)
        assert res["activated"] is False
        assert res["activation_frame"] is None
        assert res["max_score"] == 0.34
        assert res["max_score_frame"] == 4
        assert res["frames_above_threshold"] == 0

    def test_single_spike_does_not_activate(self):
        # Frame 3 reaches 0.38, but neighboring frames are low
        scores = [0.01, 0.05, 0.10, 0.38, 0.12, 0.05]
        res = evaluate_detection_rule(scores, threshold=0.35, window_size=3, min_hits=2)
        assert res["activated"] is False
        assert res["frames_above_threshold"] == 1
        assert res["max_score"] == 0.38
        assert res["max_score_frame"] == 3

    def test_consecutive_hits_activate_on_second_hit(self):
        # Frame 3 is 0.36, Frame 4 is 0.37 -> 2 hits in window of 3 -> Activates at Frame 4
        scores = [0.01, 0.05, 0.10, 0.36, 0.37, 0.25, 0.02]
        res = evaluate_detection_rule(scores, threshold=0.35, window_size=3, min_hits=2)
        assert res["activated"] is True
        assert res["activation_frame"] == 4
        assert res["max_score"] == 0.37
        assert res["max_score_frame"] == 4
        assert res["frames_above_threshold"] == 2

    def test_separated_hits_within_window_activate(self):
        # Frame 2 is 0.36, Frame 3 is 0.20, Frame 4 is 0.38 -> 2 hits in window [0.36, 0.20, 0.38] -> Activates at Frame 4
        scores = [0.01, 0.05, 0.36, 0.20, 0.38, 0.10]
        res = evaluate_detection_rule(scores, threshold=0.35, window_size=3, min_hits=2)
        assert res["activated"] is True
        assert res["activation_frame"] == 4


class TestModelPathResolution:
    """Verify diagnostic locates real model files."""

    def test_resolve_model_paths_finds_all_three_files(self):
        ww, melspec, embedding = resolve_model_paths()
        assert ww.name == "hey_jarvis_v0.1.onnx"
        assert melspec.name == "melspectrogram.onnx"
        assert embedding.name == "embedding_model.onnx"
        assert ww.is_file() and ww.stat().st_size > 0
        assert melspec.is_file() and melspec.stat().st_size > 0
        assert embedding.is_file() and embedding.stat().st_size > 0


class TestDeviceListing:
    """Verify input device enumeration filters properly."""

    def test_list_audio_input_devices(self):
        mock_p = MagicMock()
        mock_p.get_device_count.return_value = 3
        devices_data = [
            {"index": 0, "name": "Output Speaker", "maxInputChannels": 0},
            {"index": 1, "name": "Input Mic", "maxInputChannels": 2, "defaultSampleRate": 44100.0},
            {"index": 2, "name": "Headset Mic", "maxInputChannels": 1, "defaultSampleRate": 16000.0},
        ]
        mock_p.get_device_info_by_index.side_effect = devices_data
        mock_p.get_device_info_by_host_api_device_index.side_effect = devices_data
        devs = list_audio_input_devices(mock_p)
        assert len(devs) == 2
        assert devs[0]["name"] == "Input Mic"
        assert devs[1]["name"] == "Headset Mic"

    def test_resolve_wake_word_device_uses_default_or_preferred(self):
        from assistant.wake_word import resolve_wake_word_device
        mock_p = MagicMock()
        mock_p.get_default_input_device_info.return_value = {"index": 1, "name": "Microphone Array (AMD Audio Dev", "hostApi": 0}
        mock_p.get_host_api_info_by_index.return_value = {"name": "MME"}

        # Without preferred index, uses default system device (MME)
        idx, name, api = resolve_wake_word_device(mock_p)
        assert idx == 1
        assert "Microphone Array" in name
        assert api == "MME"

        # With preferred index, uses that index
        mock_p.get_device_info_by_index.return_value = {"index": 3, "name": "USB Mic", "hostApi": 1}
        mock_p.get_host_api_info_by_index.return_value = {"name": "DirectSound"}
        idx, name, api = resolve_wake_word_device(mock_p, preferred_index=3)
        assert idx == 3
        assert name == "USB Mic"

    def test_resolve_mme_fallback_prefers_same_microphone_array(self):
        from assistant.wake_word import resolve_mme_fallback_device
        mock_p = MagicMock()
        mock_p.get_host_api_count.return_value = 2
        mock_p.get_device_count.return_value = 4

        def mock_api(api_idx):
            return {"name": "MME"} if api_idx == 0 else {"name": "Windows DirectSound"}

        def mock_info(i):
            return {
                0: {"index": 0, "name": "Microsoft Sound Mapper - Input", "maxInputChannels": 2, "hostApi": 0},
                1: {"index": 1, "name": "Microphone Array (AMD Audio Device)", "maxInputChannels": 2, "hostApi": 0},
                2: {"index": 2, "name": "Microphone Array (AMD Audio Device)", "maxInputChannels": 2, "hostApi": 1},
                3: {"index": 3, "name": "Headset Mic", "maxInputChannels": 1, "hostApi": 0},
            }[i]

        mock_p.get_host_api_info_by_index.side_effect = mock_api
        mock_p.get_device_info_by_index.side_effect = mock_info

        idx, name, api = resolve_mme_fallback_device(mock_p)
        assert idx == 1
        assert "Microphone Array" in name
        assert api == "MME"

    def test_stream_appears_realtime_distinguishes_stale_loop(self):
        import time
        from assistant.wake_word import stream_appears_realtime

        class InstantStream:
            def read(self, frames, exception_on_overflow=False):
                return b"\x00" * (frames * 2)

        class RealtimeStream:
            def read(self, frames, exception_on_overflow=False):
                time.sleep(0.05)
                return b"\x00" * (frames * 2)

        stale, stale_elapsed = stream_appears_realtime(InstantStream(), reads=3)
        live, live_elapsed = stream_appears_realtime(RealtimeStream(), reads=3)

        assert stale is False
        assert stale_elapsed < 0.05
        assert live is True
        assert live_elapsed >= 0.10


