"""
diagnostics/wake_word_live_test.py
==================================
Standalone real-microphone diagnostic tool for Jarvis openWakeWord detection.

Isolates the microphone-to-ONNX prediction pipeline from PySide6 desktop UI
and AudioCoordinator state machines to establish direct ground-truth metrics.

Usage:
    python diagnostics/wake_word_live_test.py
    python diagnostics/wake_word_live_test.py --device 1 --threshold 0.35
"""

from __future__ import annotations

import argparse
from collections import deque
import os
import pathlib
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Ensure project root is in sys.path
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import WAKE_WORD_THRESHOLD, WAKE_WORD_COOLDOWN


def calculate_rms(samples: np.ndarray) -> float:
    """Calculate Root Mean Square (RMS) amplitude of audio samples."""
    if len(samples) == 0:
        return 0.0
    float_samples = samples.astype(np.float32)
    return float(np.sqrt(np.mean(float_samples ** 2)))


def evaluate_detection_rule(
    scores: List[float],
    threshold: float = 0.35,
    window_size: int = 3,
    min_hits: int = 2,
) -> Dict[str, Any]:
    """Evaluate sliding m-of-n window activation over a sequence of frame scores.

    Returns a dict containing:
        - activated (bool): Whether the rule triggered.
        - activation_frame (int or None): First frame index where threshold was met.
        - max_score (float): Highest score observed.
        - max_score_frame (int): Frame index of the peak score.
        - frames_above_threshold (int): Total count of frames >= threshold.
    """
    if not scores:
        return {
            "activated": False,
            "activation_frame": None,
            "max_score": 0.0,
            "max_score_frame": -1,
            "frames_above_threshold": 0,
        }

    max_score = float(max(scores))
    max_score_frame = int(scores.index(max_score))
    frames_above = sum(1 for s in scores if s >= threshold)

    window: deque[float] = deque(maxlen=window_size)
    activated = False
    activation_frame: Optional[int] = None

    for idx, s in enumerate(scores):
        window.append(s)
        hits = sum(1 for w in window if w >= threshold)
        if hits >= min_hits and not activated:
            activated = True
            activation_frame = idx

    return {
        "activated": activated,
        "activation_frame": activation_frame,
        "max_score": max_score,
        "max_score_frame": max_score_frame,
        "frames_above_threshold": frames_above,
    }


def resolve_model_paths() -> Tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Resolve and validate the exact ONNX model paths."""
    models_dir = PROJECT_ROOT / "models"
    ww_path = models_dir / "hey_jarvis_v0.1.onnx"
    melspec_path = models_dir / "melspectrogram.onnx"
    embedding_path = models_dir / "embedding_model.onnx"

    for path, name in [
        (ww_path, "hey_jarvis_v0.1.onnx"),
        (melspec_path, "melspectrogram.onnx"),
        (embedding_path, "embedding_model.onnx"),
    ]:
        if not path.is_file() or path.stat().st_size <= 0:
            raise FileNotFoundError(f"Required model file missing or empty: {path}")

    return ww_path, melspec_path, embedding_path


def load_openwakeword_model(
    ww_path: pathlib.Path,
    melspec_path: pathlib.Path,
    embedding_path: pathlib.Path,
):
    """Initialize the official openWakeWord model instance using validated arguments."""
    try:
        from openwakeword.model import Model
    except ImportError as e:
        raise RuntimeError(f"openWakeWord package is not installed: {e}")

    model = Model(
        wakeword_models=[str(ww_path)],
        inference_framework="onnx",
        melspec_model_path=str(melspec_path),
        embedding_model_path=str(embedding_path),
    )
    return model


def list_audio_input_devices(p) -> List[Dict[str, Any]]:
    """Return available PyAudio input devices across all host APIs."""
    devices = []
    count = p.get_device_count()
    for i in range(count):
        info = None
        try:
            res = p.get_device_info_by_index(i)
            if isinstance(res, dict):
                info = res
        except Exception:
            pass
        if info is None:
            try:
                res = p.get_device_info_by_host_api_device_index(0, i)
                if isinstance(res, dict):
                    info = res
            except Exception:
                pass
        if isinstance(info, dict) and info.get("maxInputChannels", 0) > 0:
            devices.append(info)
    return devices


def run_live_diagnostic(
    threshold: float = 0.35,
    total_duration: float = 8.0,
    silence_duration: float = 2.0,
    device_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Capture live microphone audio in memory, evaluate against the ONNX model, and report metrics."""
    try:
        import pyaudio
    except ImportError as e:
        raise RuntimeError(f"PyAudio is required for live microphone diagnostic: {e}")

    ww_path, melspec_path, embedding_path = resolve_model_paths()

    print("=" * 70)
    print("JARVIS WAKE-WORD STANDALONE LIVE MICROPHONE DIAGNOSTIC")
    print("=" * 70)
    print(f"Loading ONNX models from '{PROJECT_ROOT / 'models'}'...")
    model = load_openwakeword_model(ww_path, melspec_path, embedding_path)
    model_keys = list(model.models.keys())
    print(f"Model initialized successfully. Registered Model Keys: {model_keys}")

    p = pyaudio.PyAudio()
    stream = None

    try:
        # Determine target device
        if device_index is None:
            try:
                from assistant.wake_word import resolve_wake_word_device
                device_index, device_name, host_api = resolve_wake_word_device(p)
            except Exception as e:
                device_index = None
                device_name = f"Default System Device (Query error: {e})"
                host_api = "Unknown"
        else:
            try:
                dev_info = p.get_device_info_by_index(device_index)
                device_name = dev_info.get("name", f"Device #{device_index}")
                api_info = p.get_host_api_info_by_index(dev_info.get("hostApi", 0))
                host_api = api_info.get("name", "Unknown")
            except Exception:
                device_name = f"Device #{device_index}"
                host_api = "Unknown"

        chunk_size = 1280  # 80ms at 16kHz
        rate = 16000
        channels = 1
        bytes_per_sample = 2  # paInt16

        print("\n--- Audio Input Configuration ---")
        print(f"Device Name      : {device_name}")
        print(f"Device Index     : {device_index} (Host API: {host_api})")
        print(f"Sample Rate      : {rate} Hz")
        print(f"Channels         : {channels} (Mono)")
        print(f"Format           : signed 16-bit PCM (paInt16)")
        print(f"Frame Size       : {chunk_size} samples (80 ms / {chunk_size * bytes_per_sample} bytes)")
        print(f"Wake Model       : {ww_path.name}")
        print(f"Prediction Keys  : {model_keys}")
        print(f"Target Threshold : {threshold:.2f} (2 of 3 consecutive frames required)")
        print("-" * 70)

        # Open stream
        stream = p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=chunk_size,
        )

        silence_frames_target = int(silence_duration * rate / chunk_size)
        total_frames_target = int(total_duration * rate / chunk_size)
        speech_frames_target = total_frames_target - silence_frames_target

        in_memory_frames: List[np.ndarray] = []
        phase_labels: List[str] = []

        print(f"\n[PHASE 1/2] Recording {silence_duration:.1f}s background silence baseline...")
        print(">>> Please REMAIN QUIET now <<<")
        for i in range(silence_frames_target):
            raw_bytes = stream.read(chunk_size, exception_on_overflow=False)
            arr = np.frombuffer(raw_bytes, dtype=np.int16)
            in_memory_frames.append(arr)
            phase_labels.append("silence")
            sys.stdout.write(".")
            sys.stdout.flush()

        print("\n\n" + "=" * 50)
        print(">>> [PHASE 2/2] SAY 'Hey Jarvis' NOW! <<<")
        print(">>> (Speak clearly at normal conversational volume) <<<")
        print("=" * 50)

        for i in range(speech_frames_target):
            raw_bytes = stream.read(chunk_size, exception_on_overflow=False)
            arr = np.frombuffer(raw_bytes, dtype=np.int16)
            in_memory_frames.append(arr)
            phase_labels.append("speech")
            time_remaining = (speech_frames_target - i) * chunk_size / rate
            if i % 10 == 0:
                sys.stdout.write(f"\nCapturing... {time_remaining:4.1f}s remaining ")
            else:
                sys.stdout.write(".")
            sys.stdout.flush()

        print("\n\nCapture complete! Audio stored in memory only. Closing microphone stream...")

    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        p.terminate()

    # Sequential Model Evaluation
    print("\n--- Evaluating Captured Audio with openWakeWord ONNX Engine ---")
    silence_rms_list: List[float] = []
    speech_rms_list: List[float] = []
    frame_scores: List[float] = []

    print(f"{'Frame':>5} | {'Phase':>7} | {'RMS':>7} | {'Peak':>6} | {'Hey Jarvis Score':>17} | {'Above ' + format(threshold, '.2f') + '?':>11}")
    print("-" * 65)

    for idx, (frame, phase) in enumerate(zip(in_memory_frames, phase_labels)):
        rms = calculate_rms(frame)
        peak = int(np.max(np.abs(frame))) if len(frame) > 0 else 0

        if phase == "silence":
            silence_rms_list.append(rms)
        else:
            speech_rms_list.append(rms)

        # Predict
        preds = model.predict(frame)

        # Extract score for Hey Jarvis
        score = 0.0
        if isinstance(preds, dict):
            for k, v in preds.items():
                if "jarvis" in k.lower():
                    score = float(v)
                    break

        if score == 0.0 and hasattr(model, "prediction_buffer"):
            for k, buf in model.prediction_buffer.items():
                if "jarvis" in k.lower() and len(buf) > 0:
                    score = float(buf[-1])
                    break

        frame_scores.append(score)

        # Print elevated scores or periodic samples
        is_above = score >= threshold
        if is_above or score > 0.05 or (idx % 10 == 0 and phase == "speech"):
            star = " *** HIT ***" if is_above else ""
            print(f"{idx:5d} | {phase:7} | {rms:7.1f} | {peak:6d} | {score:17.5f} | {str(is_above):>11}{star}")

    # Summary Statistics
    bg_rms_mean = float(np.mean(silence_rms_list)) if silence_rms_list else 0.0
    speech_rms_mean = float(np.mean(speech_rms_list)) if speech_rms_list else 0.0
    speech_rms_peak = float(np.max(speech_rms_list)) if speech_rms_list else 0.0

    eval_result = evaluate_detection_rule(
        frame_scores,
        threshold=threshold,
        window_size=3,
        min_hits=2,
    )

    print("\n" + "=" * 70)
    print("LIVE DIAGNOSTIC RESULTS SUMMARY")
    print("=" * 70)
    print(f"Background Audio RMS (Baseline) : {bg_rms_mean:6.2f}")
    print(f"Speech Audio RMS (Average)      : {speech_rms_mean:6.2f}")
    print(f"Speech Audio RMS (Peak)         : {speech_rms_peak:6.2f}")
    print(f"Threshold Configured            : {threshold:6.2f}")
    print(f"Maximum Observed Score          : {eval_result['max_score']:8.5f} (Frame #{eval_result['max_score_frame']})")
    print(f"Frames Meeting Threshold        : {eval_result['frames_above_threshold']}")
    print(f"Threshold Met Single Frame?     : {'YES' if eval_result['frames_above_threshold'] > 0 else 'NO'}")
    print(f"2-of-3 Rule Activated?          : {'YES (Frame #' + str(eval_result['activation_frame']) + ')' if eval_result['activated'] else 'NO'}")
    print("=" * 70)

    # Diagnostic Interpretation Guidance
    print("\nDIAGNOSTIC VERDICT:")
    if eval_result["activated"]:
        print("  [SUCCESS]: The wake-word model received clear speech and triggered activation!")
        print(f"  Frame #{eval_result['activation_frame']} reached the required 2-of-3 threshold rule.")
        print("  -> Next step: If desktop UI does not activate, verify AudioCoordinator event handoff.")
    elif eval_result["frames_above_threshold"] == 1:
        print("  [NEAR-MISS]: Score reached the threshold once, but missed the 2-of-3 consecutive hit requirement.")
        print("  -> Next step: Speak slightly more deliberately or adjust WAKE_WORD_MIN_HITS=1.")
    elif eval_result["max_score"] >= 0.20:
        print(f"  [ACOUSTIC GAIN/ACCENT]: Model detected 'Hey Jarvis' features (Peak: {eval_result['max_score']:.4f})")
        print(f"  but did not cross {threshold:.2f}. Check Windows microphone boost or speak slightly closer to the mic.")
    else:
        print(f"  [LOW PREDICTION LEVEL]: Peak score was only {eval_result['max_score']:.5f}.")
        if speech_rms_mean < 10.0:
            print("  -> Cause: Microphone input volume is extremely low (RMS < 10). Increase Windows input volume in Sound Settings.")
        else:
            print("  -> Cause: Audio was non-silent (RMS active), but model did not recognize 'Hey Jarvis' phonetic pattern.")

    print()
    return {
        "device_name": device_name,
        "device_index": device_index,
        "bg_rms": bg_rms_mean,
        "speech_rms": speech_rms_mean,
        "eval_result": eval_result,
        "frame_scores": frame_scores,
    }


def main():
    parser = argparse.ArgumentParser(description="Standalone Live Microphone openWakeWord Diagnostic")
    parser.add_argument("--threshold", type=float, default=WAKE_WORD_THRESHOLD, help="Score threshold (default from config: 0.35)")
    parser.add_argument("--duration", type=float, default=8.0, help="Total recording duration in seconds (default: 8.0)")
    parser.add_argument("--silence", type=float, default=2.0, help="Silence baseline duration in seconds (default: 2.0)")
    parser.add_argument("--device", type=int, default=None, help="Microphone device index (default: system default)")
    parser.add_argument("--list-devices", action="store_true", help="List available audio input devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        import pyaudio
        p = pyaudio.PyAudio()
        devs = list_audio_input_devices(p)
        print("Available Input Devices:")
        for d in devs:
            print(f"  Index {d['index']}: {d['name']} (Sample Rate: {d['defaultSampleRate']}, Channels: {d['maxInputChannels']})")
        p.terminate()
        return

    run_live_diagnostic(
        threshold=args.threshold,
        total_duration=args.duration,
        silence_duration=args.silence,
        device_index=args.device,
    )


if __name__ == "__main__":
    main()

