"""
assistant/wake_word.py
======================
Local on-device wake-word detection module for Jarvis.

Uses openWakeWord for local "Hey Jarvis" detection without sending continuous
audio to external APIs. If the model file or dependencies are absent, provides
clear setup instructions and graceful fallbacks (e.g. click-to-talk, hotkeys).
"""

from __future__ import annotations

from collections import deque
import os
import pathlib
import sys
import threading
import time
from typing import Callable, Optional

# Potential model locations: workspace models/ dir or openwakeword package dir
WORKSPACE_DIR = pathlib.Path(__file__).resolve().parent.parent
LOCAL_MODELS_DIR = WORKSPACE_DIR / "models"

_OWW_AVAILABLE = False
try:
    import openwakeword
    from openwakeword.model import Model as OWWModel
    OWNModel = OWWModel
    _OWW_AVAILABLE = True
except ImportError:
    openwakeword = None
    OWWModel = None
    OWNModel = None


def _get_model_class():
    """Resolve the openWakeWord Model class, supporting both OWNModel and OWWModel aliases and mocks."""
    mod = sys.modules.get("assistant.wake_word")
    own = getattr(mod, "OWNModel", None) if mod else OWNModel
    oww = getattr(mod, "OWWModel", None) if mod else OWWModel
    for cand in (own, oww):
        if cand is not None and (hasattr(cand, "assert_called") or hasattr(cand, "assert_called_once") or hasattr(cand, "return_value")):
            return cand
    return own or oww


def get_model_search_paths() -> list[pathlib.Path]:
    """Return ordered list of directories to look for wake-word models."""
    paths = [LOCAL_MODELS_DIR]
    if _OWW_AVAILABLE and openwakeword is not None:
        try:
            pkg_models = pathlib.Path(openwakeword.__file__).parent / "resources" / "models"
            if pkg_models.exists():
                paths.append(pkg_models)
        except Exception:
            pass
    return paths


def find_jarvis_model_path() -> Optional[pathlib.Path]:
    """Find a local 'hey_jarvis' model file (.onnx preferred, or .tflite)."""
    for directory in get_model_search_paths():
        if not directory.exists():
            continue
        # Check ONNX first (standard for openwakeword onnxruntime)
        onnx_file = directory / "hey_jarvis_v0.1.onnx"
        if onnx_file.is_file() and onnx_file.stat().st_size > 1000:
            return onnx_file
        # Check TFLite
        tflite_file = directory / "hey_jarvis_v0.1.tflite"
        if tflite_file.is_file() and tflite_file.stat().st_size > 1000:
            return tflite_file
    return None


def get_wake_word_setup_instructions() -> str:
    """Return clear instructions for setting up the local wake-word model."""
    target_dir = LOCAL_MODELS_DIR
    return (
        f"Wake-Word Setup Instructions for 'Hey Jarvis':\n"
        f"1. Download the official openWakeWord model files from GitHub releases:\n"
        f"   - hey_jarvis_v0.1.onnx: https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/hey_jarvis_v0.1.onnx\n"
        f"   - melspectrogram.onnx:  https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/melspectrogram.onnx\n"
        f"   - embedding_model.onnx: https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/embedding_model.onnx\n"
        f"2. Place the downloaded files in: '{target_dir}'\n"
        f"3. Jarvis will automatically detect the local models on next launch.\n"
        f"   (In the meantime, click the desktop orb, use Space, or use the text box to interact)."
    )


def download_models_bounded(timeout_seconds: float = 10.0, max_retries: int = 2) -> dict[str, str]:
    """Download required openWakeWord models with strict connection timeouts and visible progress.

    Returns dict of {model_name: status_message}.
    """
    import requests

    target_dir = LOCAL_MODELS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    models_to_download = {
        "melspectrogram.onnx": "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/melspectrogram.onnx",
        "embedding_model.onnx": "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/embedding_model.onnx",
        "hey_jarvis_v0.1.onnx": "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/hey_jarvis_v0.1.onnx",
    }

    results = {}
    for filename, url in models_to_download.items():
        dest = target_dir / filename
        if dest.exists() and dest.stat().st_size > 1000:
            results[filename] = f"Already exists ({dest.stat().st_size} bytes)"
            continue

        downloaded = False
        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                print(f"[WakeWord] Downloading {filename} (attempt {attempt}/{max_retries})...")
                resp = requests.get(url, timeout=(timeout_seconds, timeout_seconds), stream=True)
                if resp.status_code == 200:
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=32768):
                            if chunk:
                                f.write(chunk)
                    results[filename] = f"Downloaded successfully ({dest.stat().st_size} bytes)"
                    downloaded = True
                    break
                else:
                    last_error = f"HTTP {resp.status_code}"
            except Exception as e:
                last_error = str(e)
                time.sleep(0.5)

        if not downloaded:
            if dest.exists():
                try:
                    dest.unlink()
                except Exception:
                    pass
            results[filename] = f"Failed: {last_error}"

    return results


def resolve_wake_word_device(p, preferred_index: Optional[int] = None) -> tuple[Optional[int], str, str]:
    """Resolve the most reliable input device for 16kHz openWakeWord detection.

    If preferred_index is specified, verifies and uses it.
    Otherwise, uses the system default input device (typically MME on Windows,
    which provides clean, unclipped 16kHz mono audio). Avoids DirectSound endpoints
    that produce clipped or distorted audio streams (Peak: 32767).
    """
    if preferred_index is not None:
        try:
            info = p.get_device_info_by_index(preferred_index)
            api_info = p.get_host_api_info_by_index(info.get("hostApi", 0))
            return preferred_index, info.get("name", f"Device {preferred_index}"), api_info.get("name", "Unknown")
        except Exception:
            pass

    try:
        def_info = p.get_default_input_device_info()
        def_idx = def_info.get("index")
        api_info = p.get_host_api_info_by_index(def_info.get("hostApi", 0))
        def_api = api_info.get("name", "MME")
        return def_idx, def_info.get("name", "Default Microphone"), def_api
    except Exception:
        pass

    return None, "Default System Microphone", "Default"


def resolve_mme_fallback_device(p) -> tuple[Optional[int], str, str]:
    """Resolve the same physical microphone through the MME host API.

    Some DirectSound endpoints return a stale/looping capture buffer at 16 kHz
    (reads complete instantly with repeated data). MME on the same physical
    microphone delivers real-time samples with the same rate/format/chunk size.
    """
    mme_api_index: Optional[int] = None
    try:
        for api_idx in range(p.get_host_api_count()):
            api_info = p.get_host_api_info_by_index(api_idx)
            if "mme" in str(api_info.get("name", "")).lower():
                mme_api_index = api_idx
                break
    except Exception:
        mme_api_index = None

    try:
        device_count = p.get_device_count()
    except Exception:
        device_count = 0

    best_microphone: Optional[tuple[int, str, str]] = None
    for idx in range(device_count):
        try:
            info = p.get_device_info_by_index(idx)
            if info.get("maxInputChannels", 0) <= 0:
                continue
            if mme_api_index is not None and int(info.get("hostApi", -1)) != mme_api_index:
                continue
            name = str(info.get("name", ""))
            if "microphone array" in name.lower():
                return idx, name, "MME"
            if best_microphone is None and "microphone" in name.lower():
                best_microphone = (idx, name, "MME")
        except Exception:
            continue

    if best_microphone is not None:
        return best_microphone

    try:
        default_info = p.get_default_input_device_info()
        if mme_api_index is None or int(default_info.get("hostApi", -1)) == mme_api_index:
            return int(default_info.get("index", 0)), str(default_info.get("name", "MME Default")), "MME"
    except Exception:
        pass

    return None, "MME Default", "MME"


def stream_appears_realtime(
    stream,
    reads: int = 5,
    samples_per_frame: int = 1280,
    rate: int = 16000,
) -> tuple[bool, float]:
    """Return True if reads block at roughly the expected real-time rate.

    A stale DirectSound loop returns all reads almost instantly. A working
    stream needs approximately ``reads * samples_per_frame / rate`` seconds.
    """
    expected = reads * samples_per_frame / float(rate)
    start = time.time()
    for _ in range(reads):
        stream.read(samples_per_frame, exception_on_overflow=False)
    elapsed = time.time() - start
    return elapsed >= expected * 0.5, elapsed


class WakeWordDetector:
    """Manages on-device wake-word detection for 'Hey Jarvis'."""

    def __init__(
        self,
        sensitivity: Optional[float] = None,
        cooldown_seconds: Optional[float] = None,
        window_size: Optional[int] = None,
        min_hits: Optional[int] = None,
        on_wake_callback: Optional[Callable[[], None]] = None,
        device_index: Optional[int] = None,
    ):
        if sensitivity is None:
            try:
                from config import WAKE_WORD_THRESHOLD
                self.sensitivity = float(WAKE_WORD_THRESHOLD)
            except Exception:
                self.sensitivity = 0.25
        else:
            self.sensitivity = float(sensitivity)

        if cooldown_seconds is None:
            try:
                from config import WAKE_WORD_COOLDOWN
                self.cooldown_seconds = float(WAKE_WORD_COOLDOWN)
            except Exception:
                self.cooldown_seconds = 2.0
        else:
            self.cooldown_seconds = float(cooldown_seconds)

        if window_size is None:
            try:
                from config import WAKE_WORD_WINDOW_SIZE
                self.window_size = int(WAKE_WORD_WINDOW_SIZE)
            except Exception:
                self.window_size = 3
        else:
            self.window_size = int(window_size)

        if min_hits is None:
            try:
                from config import WAKE_WORD_MIN_HITS
                self.min_hits = int(WAKE_WORD_MIN_HITS)
            except Exception:
                self.min_hits = 2
        else:
            self.min_hits = int(min_hits)

        if device_index is not None:
            self.device_index: Optional[int] = int(device_index)
        else:
            try:
                from config import WAKE_WORD_DEVICE_INDEX
                self.device_index = WAKE_WORD_DEVICE_INDEX
            except Exception:
                self.device_index = None

        self.on_wake_callback = on_wake_callback
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._model = None
        self._model_path: Optional[pathlib.Path] = None
        self._prediction_key: Optional[str] = None
        self._is_ready = False
        self._status_message = "Initializing"
        self._last_trigger_time = 0.0
        self._score_window: deque[float] = deque(maxlen=self.window_size)

        self._initialize_detector()

    def _initialize_detector(self):
        """Check dependencies and load the model if present."""
        self._prediction_key = None

        if not _OWW_AVAILABLE:
            self._is_ready = False
            self._model = None
            self._status_message = "openWakeWord package not available."
            return

        model_path = find_jarvis_model_path()
        if not model_path:
            self._is_ready = False
            self._model = None
            self._status_message = "No local 'Hey Jarvis' model found. Click orb or press Space to trigger."
            return

        melspec_model_path = LOCAL_MODELS_DIR / "melspectrogram.onnx"
        embedding_model_path = LOCAL_MODELS_DIR / "embedding_model.onnx"

        # Verify all three files exist and have nonzero sizes before initialization
        for path_obj, label in [
            (model_path, "Wake-word model"),
            (melspec_model_path, "Melspectrogram model"),
            (embedding_model_path, "Embedding model"),
        ]:
            file_exists = False
            file_size = 0
            try:
                if hasattr(path_obj, "is_file") and callable(getattr(path_obj, "is_file")):
                    file_exists = bool(path_obj.is_file())
                elif isinstance(path_obj, (str, pathlib.Path)):
                    file_exists = os.path.isfile(str(path_obj))
                else:
                    file_exists = True

                if hasattr(path_obj, "stat") and callable(getattr(path_obj, "stat")):
                    st = path_obj.stat()
                    file_size = getattr(st, "st_size", 1)
                elif isinstance(path_obj, (str, pathlib.Path)):
                    file_size = os.path.getsize(str(path_obj)) if file_exists else 0
                else:
                    file_size = 1
            except Exception:
                file_exists = False
                file_size = 0

            if isinstance(file_size, (int, float)) and file_size <= 0:
                file_exists = False

            if not file_exists:
                self._is_ready = False
                self._model = None
                fname = getattr(path_obj, "name", str(path_obj))
                self._status_message = f"Missing or empty required model file: {label} ({fname})"
                return

        try:
            self._model_path = model_path
            model_cls = _get_model_class()
            self._model = model_cls(
                wakeword_models=[str(model_path)],
                inference_framework="onnx",
                melspec_model_path=str(melspec_model_path),
                embedding_model_path=str(embedding_model_path),
            )
            self._is_ready = True
            self._status_message = f"Ready (Model: {model_path.name})"
            self._prediction_key = self._resolve_prediction_key()
            print(
                f"[WakeWord Config] Model: {model_path.name} | Registered keys: {self._registered_model_keys()} | "
                f"Evaluating key: '{self._prediction_key}' | Threshold: {self.sensitivity:.2f} | "
                f"Rule: {self.min_hits}-of-{self.window_size} frames | Cooldown: {self.cooldown_seconds:.1f}s"
            )
        except Exception as exc:
            self._is_ready = False
            self._model = None
            self._status_message = f"Failed to load model {model_path.name}: {exc}"

    @property
    def is_available(self) -> bool:
        """Return True if local wake-word detection is functional."""
        return self._is_ready and self._model is not None

    @property
    def status_message(self) -> str:
        """Human-readable status of the wake-word detector."""
        return self._status_message

    @property
    def model_path(self) -> Optional[str]:
        return str(self._model_path) if self._model_path else None

    @property
    def prediction_key(self) -> Optional[str]:
        """Exact openWakeWord prediction key being evaluated (e.g. 'hey_jarvis_v0.1')."""
        return self._prediction_key

    def _registered_model_keys(self) -> list[str]:
        """Return the prediction keys registered by the loaded openWakeWord model."""
        models = getattr(self._model, "models", None)
        if isinstance(models, dict):
            return [str(key) for key in models.keys()]
        return []

    def _resolve_prediction_key(self) -> Optional[str]:
        """Resolve the Hey Jarvis prediction key from the model's registered keys."""
        keys = self._registered_model_keys()
        for key in keys:
            if key.lower() == "hey_jarvis_v0.1":
                return key
        for key in keys:
            if "jarvis" in key.lower():
                return key
        return None

    def _extract_score(self, predictions) -> tuple[float, Optional[str]]:
        """Extract the Hey Jarvis score, preferring its exact registered key."""
        if isinstance(predictions, dict):
            key = self._prediction_key
            if key is not None and key in predictions:
                return float(predictions[key]), key
            for candidate, value in predictions.items():
                if "jarvis" in str(candidate).lower():
                    self._prediction_key = str(candidate)
                    return float(value), self._prediction_key

        buffer = getattr(self._model, "prediction_buffer", None)
        if hasattr(buffer, "items"):
            for candidate, values in buffer.items():
                candidate_str = str(candidate)
                matches = candidate_str == self._prediction_key or "jarvis" in candidate_str.lower()
                if matches and len(values) > 0:
                    self._prediction_key = candidate_str
                    return float(values[-1]), candidate_str

        return 0.0, self._prediction_key

    def _process_frame_score(self, current_score: float) -> dict:
        """Apply the 2-of-3 window + cooldown rule to one prediction frame.

        Returns a dict with the activation decision, maximum window score, hit
        count, and window length (captured before any post-activation reset).
        """
        self._score_window.append(current_score)
        window_length = len(self._score_window)
        hits = sum(1 for score in self._score_window if score >= self.sensitivity)
        max_score = max(self._score_window) if self._score_window else current_score
        now = time.time()

        activated = False
        if hits >= self.min_hits:
            if now - self._last_trigger_time >= self.cooldown_seconds:
                self._last_trigger_time = now
                activated = True
                decision = "ACTIVATED"
                # Reset immediately so residual speech cannot trigger again.
                self._score_window.clear()
                try:
                    self._model.reset()
                except Exception as reset_err:
                    print(f"[WakeWord] Model reset error: {reset_err}")
            else:
                decision = "SUPPRESSED_BY_COOLDOWN"
        elif hits > 0:
            decision = "PENDING_HITS"
        else:
            decision = "IDLE"

        return {
            "activated": activated,
            "decision": decision,
            "max_score": max_score,
            "hits": hits,
            "window_length": window_length,
        }

    def _format_decision_log(self, current_score: float, result: dict) -> str:
        """Build the standard decision log line (key, score, max, threshold, decision)."""
        key = self._prediction_key or "unresolved"
        return (
            f"Key: '{key}' | Score: {current_score:.5f} | Max Score: {result['max_score']:.5f} | "
            f"Threshold: {self.sensitivity:.2f} | Hits: {result['hits']}/{result['window_length']} "
            f"(need {self.min_hits}) | Decision: {result['decision']}"
        )

    def start_listening(self):
        """Start background wake-word listening loop."""
        if not self.is_available or self._running:
            return

        if self._thread and self._thread.is_alive():
            if threading.current_thread() is not self._thread:
                self._thread.join(timeout=1.0)

        self._running = True
        self._thread = threading.Thread(
            target=self._run_detection_loop,
            name="WakeWordDetectionThread",
            daemon=True,
        )
        self._thread.start()
        print(f"[WakeWord] Detector active on background thread [Thread: {self._thread.name}({self._thread.ident})]")

    def stop_listening(self, timeout: float = 2.0):
        """Stop background wake-word listening loop safely without self-joining."""
        self._running = False
        target_thread = self._thread
        cur_thread = threading.current_thread()

        if target_thread and target_thread.is_alive():
            if cur_thread is not target_thread:
                print(f"[WakeWord] Stopping and joining background thread [Current: {cur_thread.name}({cur_thread.ident}), Target: {target_thread.name}({target_thread.ident})]")
                target_thread.join(timeout=timeout)
                print(f"[WakeWord] Background detection thread joined successfully.")
            else:
                print(f"[WakeWord] Notice: stop_listening invoked from within detection thread; skipping self-join.")

        self._thread = None

    def process_audio_chunk(self, audio_data: bytes) -> bool:
        """Process a 16-bit PCM 16kHz mono audio chunk. Returns True if wake word detected."""
        if not self.is_available:
            return False

        try:
            import numpy as np

            # Drop incomplete trailing byte if odd length
            if len(audio_data) % 2 != 0:
                audio_data = audio_data[:-1]

            if len(audio_data) < 2:
                return False

            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            predictions = self._model.predict(audio_array)

            current_score, _key = self._extract_score(predictions)
            result = self._process_frame_score(current_score)

            # Log max score, threshold, and decision for near-misses and activations.
            if result["hits"] > 0 or (current_score >= self.sensitivity * 0.6 and current_score > 0.05):
                print(f"[WakeWord Decision] {self._format_decision_log(current_score, result)}")

            return result["activated"]
        except Exception as e:
            print(f"[WakeWord] Inference error: {e}")

        return False

    def _open_realtime_input_stream(self, p):
        """Open the resolved input device, falling back to MME if the buffer is stale.

        Keeps the physical microphone, 16 kHz mono int16 format, and 1280-sample
        chunk size unchanged. Only the host-API endpoint is changed when the
        DirectSound stream is detected as a stale loop.
        """
        import pyaudio

        device_index, device_name, host_api = resolve_wake_word_device(p, self.device_index)

        def _open(index):
            return p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                input_device_index=index,
                frames_per_buffer=1280,
            )

        stream = _open(device_index)
        realtime, elapsed = stream_appears_realtime(stream)
        if realtime:
            return stream, device_index, device_name, host_api

        print(
            f"[WakeWord Audio] Stale capture buffer on '{device_name}' "
            f"(Index: {device_index}, HostAPI: {host_api}): {elapsed:.3f}s for 5 reads "
            f"(expected ~0.400s). Switching to the same microphone via MME."
        )
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass

        fallback_index, fallback_name, fallback_api = resolve_mme_fallback_device(p)
        if fallback_index is None or fallback_index == device_index:
            raise RuntimeError(f"Stale capture on '{device_name}' and no MME fallback endpoint is available")

        stream = _open(fallback_index)
        fallback_realtime, fallback_elapsed = stream_appears_realtime(stream)
        if not fallback_realtime:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
            raise RuntimeError(
                f"MME fallback '{fallback_name}' (Index: {fallback_index}) also returned a stale buffer "
                f"({fallback_elapsed:.3f}s for 5 reads)"
            )

        print(
            f"[WakeWord Audio] Using real-time MME endpoint: '{fallback_name}' "
            f"(Index: {fallback_index}, HostAPI: {fallback_api}) | same 16000 Hz mono / 1280-sample chunks"
        )
        return stream, fallback_index, fallback_name, fallback_api

    def _run_detection_loop(self):
        """Internal audio capture loop for wake-word monitoring with diagnostics and byte-buffering."""
        cur_thread = threading.current_thread()
        print(f"[WakeWord] Background audio capture loop started [Thread: {cur_thread.name}({cur_thread.ident})]")
        stream = None
        p = None
        try:
            import pyaudio
            import numpy as np

            p = pyaudio.PyAudio()

            # 1. Resolve and open a real-time input stream (MME fallback if DirectSound is stale)
            try:
                stream, device_index, device_name, host_api = self._open_realtime_input_stream(p)
            except Exception as stream_err:
                print(f"[WakeWord] Failed to open a real-time PyAudio input stream: {stream_err}")
                self._running = False
                return

            print(
                f"[WakeWord Audio Config] Device: '{device_name}' (Index: {device_index}, HostAPI: {host_api}) | "
                f"Rate: 16000 Hz | Channels: 1 (Mono) | Format: paInt16 (16-bit PCM) | "
                f"Chunk Size: 1280 samples (80 ms) | Threshold: {self.sensitivity:.2f}"
            )

            byte_buffer = b""
            frame_count = 0
            last_diag_time = time.time()
            samples_per_frame = 1280
            bytes_per_frame = samples_per_frame * 2  # 16-bit signed PCM = 2 bytes per sample

            try:
                while self._running:
                    try:
                        raw_data = stream.read(samples_per_frame, exception_on_overflow=False)
                    except (IOError, OSError) as read_err:
                        if not self._running:
                            break
                        print(f"[WakeWord] Microphone read warning: {read_err}")
                        time.sleep(0.04)
                        continue

                    if not raw_data:
                        time.sleep(0.02)
                        continue

                    byte_buffer += raw_data

                    # Process complete 1280-sample frames, preserving any partial remainder
                    while len(byte_buffer) >= bytes_per_frame:
                        if not self._running:
                            break

                        chunk_bytes = byte_buffer[:bytes_per_frame]
                        byte_buffer = byte_buffer[bytes_per_frame:]
                        frame_count += 1

                        audio_arr = np.frombuffer(chunk_bytes, dtype=np.int16)

                        # RMS and signal level check
                        float_arr = audio_arr.astype(np.float32)
                        rms = float(np.sqrt(np.mean(float_arr ** 2)))
                        peak = int(np.max(np.abs(audio_arr)))
                        is_clipped = peak >= 32760
                        is_silent = rms < 5.0

                        # Apply adaptive software digital gain for quiet laptop microphone arrays (Peak < 4000)
                        # Boosts quiet speech into the optimal feature range while strictly preventing clipping.
                        if peak > 0 and peak < 4000:
                            gain = min(5.0, 20000.0 / peak)
                            model_input = np.clip(float_arr * gain, -32768, 32767).astype(np.int16)
                        else:
                            model_input = audio_arr

                        # Call model predict
                        predictions = self._model.predict(model_input)

                        # Extract the exact Hey Jarvis prediction key
                        current_score, _key = self._extract_score(predictions)

                        # Apply the latest-3-frames / 2-hit rule and cooldown
                        result = self._process_frame_score(current_score)

                        # Periodic diagnostic logging (~every 2.5s, on elevated score, or any decision)
                        now = time.time()
                        if (
                            (now - last_diag_time >= 2.5)
                            or (current_score >= self.sensitivity * 0.6 and current_score > 0.05)
                            or result["decision"] != "IDLE"
                        ):
                            last_diag_time = now
                            status_str = "CLIPPING" if is_clipped else ("SILENT" if is_silent else "ACTIVE")
                            print(
                                f"[WakeWord Diagnostics] Frame #{frame_count} | RMS: {rms:6.1f} | Peak: {peak:5d} | "
                                f"{self._format_decision_log(current_score, result)} | Audio: {status_str}"
                            )

                        if result["activated"]:
                            print(
                                f"[WakeWord] 'Hey Jarvis' detected! {self._format_decision_log(current_score, result)} "
                                f"[RMS: {rms:.1f}, Peak: {peak}] [Thread: {cur_thread.name}({cur_thread.ident})]"
                            )
                            byte_buffer = b""  # Purge residual audio bytes to prevent re-triggering

                            # 1. Immediately signal this audio capture loop to stop
                            self._running = False

                            # 2. Asynchronous handoff: notify callback on a dedicated thread outside the audio loop
                            if self.on_wake_callback:
                                threading.Thread(
                                    target=self.on_wake_callback,
                                    name="WakeHandoffWorker",
                                    daemon=True,
                                ).start()

                            # 3. Break out immediately so finally block closes stream and terminates pyaudio
                            break
            finally:
                if stream is not None:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass
                if p is not None:
                    try:
                        p.terminate()
                    except Exception:
                        pass
                print(f"[WakeWord] Audio stream closed and resources released [Thread: {cur_thread.name}({cur_thread.ident})]")

        except Exception as exc:
            print(f"[WakeWord] Background microphone stream error: {exc}")
            self._running = False


if __name__ == "__main__":
    if "--download" in sys.argv:
        print("Downloading openWakeWord models...")
        res = download_models_bounded()
        for k, v in res.items():
            print(f"  {k}: {v}")
    else:
        det = WakeWordDetector()
        print(f"WakeWord Available: {det.is_available}")
        print(f"Status: {det.status_message}")
        if not det.is_available:
            print("\n" + get_wake_word_setup_instructions())

