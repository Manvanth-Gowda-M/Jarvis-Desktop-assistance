"""
diagnostics/calibrate_microphones.py
====================================
Interactive Microphone Calibration Tool for Jarvis openWakeWord Recognition.

Tests each available Windows microphone device with real speech:
1. Captures 3 seconds of background audio (ambient noise baseline).
2. Prompts the user to say "Hey Jarvis" at normal speaking volume.
3. Captures the complete phrase (~5 seconds).
4. Calculates background RMS, speech RMS, and signal-to-noise ratio.
5. Feeds the speech into a fresh instance of the Hey Jarvis ONNX model.
6. Reports maximum prediction score and detection decisions.
7. Ranks devices and identifies the recommended input device.

Audio is processed strictly in-memory (never saved to disk or transmitted).
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

from config import WAKE_WORD_THRESHOLD


def calculate_rms(samples: np.ndarray) -> float:
    """Calculate Root Mean Square (RMS) amplitude of audio samples."""
    if len(samples) == 0:
        return 0.0
    float_samples = samples.astype(np.float32)
    return float(np.sqrt(np.mean(float_samples ** 2)))


def get_candidate_input_devices() -> List[Dict[str, Any]]:
    """Enumerate all PyAudio devices that support audio input and can open at 16kHz."""
    import pyaudio

    p = pyaudio.PyAudio()
    candidates = []

    try:
        default_info = p.get_default_input_device_info()
        default_index = default_info.get("index")
    except Exception:
        default_index = None

    device_count = p.get_device_count()
    for idx in range(device_count):
        try:
            info = p.get_device_info_by_index(idx)
        except Exception:
            continue

        if info.get("maxInputChannels", 0) <= 0:
            continue

        host_api_idx = info.get("hostApi", 0)
        try:
            host_api = p.get_host_api_info_by_index(host_api_idx).get("name", "Unknown")
        except Exception:
            host_api = "Unknown"

        # Test if 16kHz mono 16-bit PCM capture stream can be opened
        can_open_16k = False
        probe_rms = 0.0
        try:
            test_stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                input_device_index=idx,
                frames_per_buffer=1280,
            )
            raw = test_stream.read(1280, exception_on_overflow=False)
            test_stream.stop_stream()
            test_stream.close()
            can_open_16k = True
            arr = np.frombuffer(raw, dtype=np.int16)
            probe_rms = calculate_rms(arr)
        except Exception:
            can_open_16k = False

        candidates.append({
            "index": idx,
            "name": info.get("name", f"Device {idx}"),
            "host_api": host_api,
            "is_default": (idx == default_index),
            "max_channels": info.get("maxInputChannels", 1),
            "default_rate": info.get("defaultSampleRate", 44100.0),
            "supports_16k": can_open_16k,
            "probe_rms": probe_rms,
        })

    p.terminate()
    return candidates


def load_fresh_model():
    """Instantiate a fresh openWakeWord model instance."""
    from openwakeword.model import Model

    models_dir = PROJECT_ROOT / "models"
    ww_path = models_dir / "hey_jarvis_v0.1.onnx"
    melspec_path = models_dir / "melspectrogram.onnx"
    embedding_path = models_dir / "embedding_model.onnx"

    for path, name in [(ww_path, "hey_jarvis_v0.1.onnx"), (melspec_path, "melspectrogram.onnx"), (embedding_path, "embedding_model.onnx")]:
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"Missing required model file: {name} at {path}")

    return Model(
        wakeword_models=[str(ww_path)],
        inference_framework="onnx",
        melspec_model_path=str(melspec_path),
        embedding_model_path=str(embedding_path),
    )


def test_microphone_device(
    device_info: Dict[str, Any],
    threshold: float = WAKE_WORD_THRESHOLD,
    background_seconds: float = 3.0,
    speech_seconds: float = 5.0,
) -> Dict[str, Any]:
    """Run real speech calibration on a single microphone device."""
    import pyaudio

    dev_idx = device_info["index"]
    dev_name = device_info["name"]
    host_api = device_info["host_api"]

    print("\n" + "=" * 75)
    print(f"TESTING DEVICE [{dev_idx}]: {dev_name}")
    print(f"Host API: {host_api} | Default Rate: {device_info['default_rate']} Hz | Channels: {device_info['max_channels']}")
    print("=" * 75)

    p = pyaudio.PyAudio()
    stream = None

    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            input_device_index=dev_idx,
            frames_per_buffer=1280,
        )
    except Exception as exc:
        p.terminate()
        print(f"  [ERROR]: Cannot open audio stream on device {dev_idx}: {exc}")
        return {
            "index": dev_idx,
            "name": dev_name,
            "host_api": host_api,
            "success": False,
            "error": str(exc),
            "max_score": 0.0,
            "bg_rms": 0.0,
            "speech_rms": 0.0,
        }

    samples_per_chunk = 1280
    bytes_per_chunk = samples_per_chunk * 2
    chunks_per_sec = 16000 // samples_per_chunk  # 12.5 chunks/sec

    # Step 1: Capture background noise
    print("\n[PHASE 1] Capturing Background Audio Baseline...")
    print(f"  Please remain SILENT for {background_seconds:.0f} seconds...")
    bg_chunks: List[bytes] = []
    bg_start = time.time()
    while time.time() - bg_start < background_seconds:
        try:
            chunk = stream.read(samples_per_chunk, exception_on_overflow=False)
            bg_chunks.append(chunk)
        except Exception as e:
            print(f"  Read error: {e}")
            break

    bg_raw = b"".join(bg_chunks)
    bg_arr = np.frombuffer(bg_raw, dtype=np.int16) if bg_raw else np.array([], dtype=np.int16)
    bg_rms = calculate_rms(bg_arr)
    print(f"  -> Background noise baseline RMS: {bg_rms:6.2f}")

    # Step 2: Prompt user to say "Hey Jarvis"
    print("\n[PHASE 2] Speech Capture Prompt")
    for sec_left in range(3, 0, -1):
        print(f"  Get ready in {sec_left}... ", end="", flush=True)
        time.sleep(1.0)
    print("\n  >>> NOW SAY: 'Hey Jarvis' <<< (normal conversational volume)")

    # Step 3: Capture speech
    speech_chunks: List[bytes] = []
    speech_start = time.time()
    while time.time() - speech_start < speech_seconds:
        try:
            chunk = stream.read(samples_per_chunk, exception_on_overflow=False)
            speech_chunks.append(chunk)
        except Exception as e:
            print(f"  Read error: {e}")
            break

    stream.stop_stream()
    stream.close()
    p.terminate()

    speech_raw = b"".join(speech_chunks)
    speech_arr = np.frombuffer(speech_raw, dtype=np.int16) if speech_raw else np.array([], dtype=np.int16)
    speech_rms = calculate_rms(speech_arr)
    peak_val = int(np.max(np.abs(speech_arr))) if len(speech_arr) > 0 else 0

    print(f"  -> Speech Audio RMS: {speech_rms:6.2f} | Peak Amplitude: {peak_val}")

    # Step 4: Evaluate with fresh Hey Jarvis ONNX model
    print("  -> Loading fresh ONNX openWakeWord model...")
    model = load_fresh_model()

    scores: List[float] = []
    window = deque(maxlen=3)
    rule_activated = False
    activation_frame = None

    # Process all speech chunks sequentially
    for idx, chunk in enumerate(speech_chunks):
        arr = np.frombuffer(chunk, dtype=np.int16)
        pred = model.predict(arr)

        score = 0.0
        if isinstance(pred, dict):
            for k, v in pred.items():
                if "jarvis" in k.lower():
                    score = float(v)
                    break
        if score == 0.0 and hasattr(model, "prediction_buffer"):
            for k, buf in model.prediction_buffer.items():
                if "jarvis" in k.lower() and len(buf) > 0:
                    score = float(buf[-1])
                    break

        scores.append(score)
        window.append(score)
        hits = sum(1 for s in window if s >= threshold)
        if hits >= 2 and not rule_activated:
            rule_activated = True
            activation_frame = idx

    max_score = float(max(scores)) if scores else 0.0
    max_frame = int(scores.index(max_score)) if scores else -1
    snr_ratio = (speech_rms / max(bg_rms, 0.1))

    print("\n  RESULTS FOR THIS DEVICE:")
    print(f"    Background RMS       : {bg_rms:6.2f}")
    print(f"    Speech RMS           : {speech_rms:6.2f}")
    print(f"    Signal / Noise Ratio : {snr_ratio:6.2f}x")
    print(f"    Max Wake-Word Score  : {max_score:8.5f} (Frame #{max_frame})")
    print(f"    Threshold Configured : {threshold:6.2f}")
    print(f"    2-of-3 Rule Triggered: {'YES (Frame #' + str(activation_frame) + ')' if rule_activated else 'NO'}")

    return {
        "index": dev_idx,
        "name": dev_name,
        "host_api": host_api,
        "success": True,
        "bg_rms": bg_rms,
        "speech_rms": speech_rms,
        "snr": snr_ratio,
        "max_score": max_score,
        "max_frame": max_frame,
        "rule_activated": rule_activated,
        "activation_frame": activation_frame,
    }


def main():
    parser = argparse.ArgumentParser(description="Microphone Device Comparison & Calibration for Jarvis Wake-Word")
    parser.add_argument("--device", type=int, default=None, help="Specific device index to test")
    parser.add_argument("--threshold", type=float, default=WAKE_WORD_THRESHOLD, help="Wake-word score threshold (default: 0.35)")
    parser.add_argument("--list-only", action="store_true", help="List all candidate devices and exit")
    parser.add_argument("--all-candidate", action="store_true", help="Sequentially test all viable candidate microphones")
    args = parser.parse_args()

    candidates = get_candidate_input_devices()

    print("\n" + "=" * 80)
    print("JARVIS MICROPHONE ENUMERATION & CAPABILITY REPORT")
    print("=" * 80)
    print(f"{'Idx':>3} | {'Default':<7} | {'Host API':<18} | {'16k OK?':<8} | {'Probe RMS':<10} | {'Device Name'}")
    print("-" * 80)
    for c in candidates:
        def_str = "YES" if c["is_default"] else "no"
        ok_str = "YES" if c["supports_16k"] else "NO"
        print(f"{c['index']:3d} | {def_str:<7} | {c['host_api']:<18} | {ok_str:<8} | {c['probe_rms']:9.2f}  | {c['name']}")
    print("=" * 80)

    if args.list_only:
        return

    # Determine which devices to test
    devices_to_test = []
    if args.device is not None:
        matched = [c for c in candidates if c["index"] == args.device]
        if not matched:
            print(f"\n[ERROR]: Device index {args.device} not found in available input devices.")
            return
        devices_to_test = matched
    elif args.all_candidate:
        # Test candidate devices that support 16k
        devices_to_test = [c for c in candidates if c["supports_16k"]]
    else:
        # By default, prioritize the DirectSound candidate and default device
        # Index 5 (DirectSound AMD) and Index 1 (MME default)
        directsound_devs = [c for c in candidates if "DirectSound" in c["host_api"] and c["supports_16k"]]
        default_devs = [c for c in candidates if c["is_default"]]
        seen = set()
        for d in directsound_devs + default_devs:
            if d["index"] not in seen:
                seen.add(d["index"])
                devices_to_test.append(d)

    print(f"\nStarting test for {len(devices_to_test)} device(s)...")

    results = []
    for dev in devices_to_test:
        res = test_microphone_device(dev, threshold=args.threshold)
        results.append(res)
        if len(devices_to_test) > 1 and dev != devices_to_test[-1]:
            print("\nPausing 2 seconds before next device test...")
            time.sleep(2.0)

    # Rank and summarize
    print("\n" + "=" * 80)
    print("MICROPHONE COMPARISON SUMMARY & RANKING")
    print("=" * 80)
    print(f"{'Rank':<5} | {'Idx':>3} | {'Host API':<18} | {'Speech RMS':<10} | {'Max Score':<10} | {'Activated?':<11} | {'Device Name'}")
    print("-" * 80)

    valid_results = [r for r in results if r.get("success")]
    valid_results.sort(key=lambda x: (x.get("rule_activated", False), x.get("max_score", 0.0), x.get("speech_rms", 0.0)), reverse=True)

    for rank, r in enumerate(valid_results, start=1):
        act_str = f"YES (F#{r['activation_frame']})" if r.get("rule_activated") else "NO"
        print(f"#{rank:<4} | {r['index']:3d} | {r['host_api']:<18} | {r['speech_rms']:10.2f} | {r['max_score']:10.5f} | {act_str:<11} | {r['name']}")
    print("=" * 80)

    if valid_results:
        best = valid_results[0]
        print(f"\nRECOMMENDED DEVICE FOR CONFIG.PY:")
        print(f"  Device Index: {best['index']} ({best['name']} on {best['host_api']})")
        print(f"  Achieved Max Score: {best['max_score']:.5f} (Speech RMS: {best['speech_rms']:.2f})")
        print(f"  To use this device, set in config.py or .env:")
        print(f"    WAKE_WORD_DEVICE_INDEX={best['index']}")
        print(f"    MICROPHONE_DEVICE_INDEX={best['index']}")
    print()


if __name__ == "__main__":
    main()

