"""Offline Vosk recognition and managed speech-model downloads."""

from __future__ import annotations

import array
import json
import logging
import math
import os
import queue
import shutil
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from typing import Any, Callable

from model.voice_commands import (
    VOICE_ACTIONS,
    build_voice_phrase_map,
    merge_wake_prefixes,
    normalize_voice_phrase,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceModelSpec:
    language: str
    name: str
    model_id: str
    url: str
    download_size_mb: int


VOICE_MODELS = {
    "en": VoiceModelSpec(
        language="en",
        name="English",
        model_id="vosk-model-small-en-us-0.15",
        url="https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        download_size_mb=40,
    ),
    "de": VoiceModelSpec(
        language="de",
        name="German",
        model_id="vosk-model-small-de-0.15",
        url="https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip",
        download_size_mb=45,
    ),
}


class VoiceDownloadCancelled(RuntimeError):
    pass


class VoiceModelManager:
    def __init__(self, config: Any) -> None:
        config_path = getattr(config, "local_path", None) or getattr(config, "path", "config.json")
        base_dir = os.path.dirname(os.path.abspath(config_path)) or os.getcwd()
        self.root = os.path.join(base_dir, "voice_models")

    @staticmethod
    def spec(language: str) -> VoiceModelSpec:
        return VOICE_MODELS["de" if str(language).lower() == "de" else "en"]

    def model_path(self, language: str) -> str:
        return os.path.join(self.root, self.spec(language).model_id)

    @staticmethod
    def _valid_model(path: str) -> bool:
        return all(
            os.path.isfile(os.path.join(path, relative))
            for relative in (os.path.join("am", "final.mdl"), os.path.join("conf", "mfcc.conf"))
        )

    def is_installed(self, language: str) -> bool:
        return self._valid_model(self.model_path(language))

    @staticmethod
    def _replace_directory_with_retry(
        source: str,
        destination: str,
        attempts: int = 10,
        cancel: threading.Event | None = None,
    ) -> None:
        """Atomically publish an extracted model after transient Windows locks clear."""
        attempts = max(1, int(attempts))
        last_error: OSError | None = None
        for attempt in range(attempts):
            if cancel is not None and cancel.is_set():
                raise VoiceDownloadCancelled("Voice model download cancelled.")
            try:
                os.replace(source, destination)
                return
            except OSError as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                delay = min(1.5, 0.2 * (2**attempt))
                if cancel is not None:
                    if cancel.wait(delay):
                        raise VoiceDownloadCancelled("Voice model download cancelled.")
                else:
                    time.sleep(delay)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Voice model installation failed before the final directory move.")

    def remove(self, language: str) -> bool:
        path = self.model_path(language)
        if not os.path.isdir(path):
            return False
        root = os.path.abspath(self.root)
        target = os.path.abspath(path)
        if os.path.commonpath([root, target]) != root or target == root:
            raise ValueError("Refusing to remove a voice model outside the managed model directory.")
        shutil.rmtree(target)
        return True

    @staticmethod
    def _safe_extract(archive: zipfile.ZipFile, destination: str, cancel: threading.Event) -> None:
        root = os.path.abspath(destination)
        members = archive.infolist()
        for info in members:
            if cancel.is_set():
                raise VoiceDownloadCancelled("Voice model download cancelled.")
            mode = (int(info.external_attr) >> 16) & 0o170000
            if mode == 0o120000:
                raise ValueError("Voice model archive contains an unsupported symbolic link.")
            target = os.path.abspath(os.path.join(root, info.filename))
            if os.path.commonpath([root, target]) != root:
                raise ValueError("Voice model archive contains an unsafe path.")
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info, "r") as source, open(target, "wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)

    def download(
        self,
        language: str,
        *,
        cancel: threading.Event,
        progress: Callable[[int, str], None] | None = None,
    ) -> str:
        spec = self.spec(language)
        if self.is_installed(language):
            return self.model_path(language)

        import requests

        os.makedirs(self.root, exist_ok=True)
        work_dir = tempfile.mkdtemp(prefix=f".{spec.model_id}-", dir=self.root)
        archive_path = os.path.join(work_dir, "model.zip")
        extract_dir = os.path.join(work_dir, "extract")
        try:
            if progress:
                progress(0, f"Downloading {spec.name} model...")
            with requests.get(spec.url, stream=True, timeout=(10, 60)) as response:
                response.raise_for_status()
                total = max(0, int(response.headers.get("content-length") or 0))
                downloaded = 0
                last_percent = -1
                with open(archive_path, "wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if cancel.is_set():
                            raise VoiceDownloadCancelled("Voice model download cancelled.")
                        if not chunk:
                            continue
                        output.write(chunk)
                        downloaded += len(chunk)
                        percent = int(downloaded * 100 / total) if total else 0
                        if progress and percent != last_percent:
                            progress(percent, f"Downloading {spec.name} model: {percent}%")
                            last_percent = percent

            if cancel.is_set():
                raise VoiceDownloadCancelled("Voice model download cancelled.")
            if progress:
                progress(100, f"Extracting {spec.name} model...")
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(archive_path, "r") as archive:
                self._safe_extract(archive, extract_dir, cancel)

            expected = os.path.join(extract_dir, spec.model_id)
            if not self._valid_model(expected):
                candidates = [
                    os.path.join(extract_dir, name)
                    for name in os.listdir(extract_dir)
                    if os.path.isdir(os.path.join(extract_dir, name))
                ]
                expected = next((path for path in candidates if self._valid_model(path)), "")
            if not expected or not self._valid_model(expected):
                raise ValueError("Downloaded archive does not contain a valid Vosk model.")

            final_path = self.model_path(language)
            if self._valid_model(final_path):
                return final_path
            if os.path.isdir(final_path):
                shutil.rmtree(final_path)
            elif os.path.lexists(final_path):
                os.remove(final_path)
            if progress:
                progress(100, f"Installing {spec.name} model...")
            self._replace_directory_with_retry(expected, final_path, cancel=cancel)
            if progress:
                progress(100, f"{spec.name} model installed.")
            return final_path
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


class VoiceCommandService:
    def __init__(
        self,
        config: Any,
        *,
        action_callback: Callable[[str, str, int], None],
        status_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.action_callback = action_callback
        self.status_callback = status_callback
        self.models = VoiceModelManager(config)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._generation = 0
        self._status = {"state": "disabled", "message": "Voice commands disabled."}
        self._download_thread: threading.Thread | None = None
        self._download_cancel: threading.Event | None = None
        self._recognition_signature: str | None = None

    @property
    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _emit_status(self, state: str, message: str, **extra: Any) -> None:
        status = {"state": str(state), "message": str(message), **extra}
        with self._lock:
            self._status = status
        callback = self.status_callback
        if callable(callback):
            try:
                callback(dict(status))
            except Exception:
                logger.debug("Voice status callback failed", exc_info=True)

    def _settings_snapshot(self) -> dict[str, Any]:
        language = "de" if str(self.config.get("VOICE_LANGUAGE") or "en").lower() == "de" else "en"
        try:
            cooldown = int(self.config.get("VOICE_COMMAND_COOLDOWN_MS") or 1000)
        except Exception:
            cooldown = 1000
        return {
            "enabled": bool(self.config.get("VOICE_ENABLED") or False),
            "language": language,
            "device": self.config.get("VOICE_INPUT_DEVICE") or {"name": "", "host_api": ""},
            "prefixes": self.config.get("VOICE_WAKE_PREFIXES") or {},
            "require_prefix": bool(self.config.get("VOICE_REQUIRE_WAKE_PREFIX") is not False),
            "commands": self.config.get("VOICE_COMMANDS") or {},
            "cooldown_ms": max(0, min(60000, cooldown)),
        }

    @staticmethod
    def _settings_signature(settings: dict[str, Any]) -> str:
        return json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    def start(self) -> None:
        settings = self._settings_snapshot()
        signature = self._settings_signature(settings)
        if not settings["enabled"]:
            self.stop(emit=False)
            with self._lock:
                self._recognition_signature = signature
            self._emit_status("disabled", "Voice commands disabled.")
            return
        with self._lock:
            if self._download_thread is not None and self._download_thread.is_alive():
                self._emit_status("downloading", "Waiting for the voice model download to finish.")
                return
        self.stop(emit=False)
        with self._lock:
            self._generation += 1
            generation = self._generation
            stop_event = threading.Event()
            self._stop_event = stop_event
            thread = threading.Thread(
                target=self._recognition_worker,
                args=(generation, stop_event, settings),
                daemon=True,
                name="voice-recognition",
            )
            self._thread = thread
            self._recognition_signature = signature
        thread.start()

    def stop(self, *, emit: bool = True) -> None:
        with self._lock:
            self._generation += 1
            stop_event = self._stop_event
            thread = self._thread
            self._stop_event = None
            self._thread = None
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        if emit:
            self._emit_status("disabled", "Voice commands disabled.")

    def reconfigure(self, *, force: bool = False) -> bool:
        settings = self._settings_snapshot()
        signature = self._settings_signature(settings)
        with self._lock:
            if not force and signature == self._recognition_signature:
                return False
        self.start()
        return True

    def shutdown(self) -> None:
        self.cancel_download()
        self.stop(emit=False)

    @staticmethod
    def list_input_devices() -> list[dict[str, Any]]:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "Offline voice dependencies are missing. Install requirements.txt before enabling voice commands."
            ) from exc

        host_apis = list(sd.query_hostapis())
        try:
            default_index = int(sd.default.device[0])
        except Exception:
            default_index = -1
        devices: list[dict[str, Any]] = []
        for index, device in enumerate(sd.query_devices()):
            if int(device.get("max_input_channels") or 0) <= 0:
                continue
            host_index = int(device.get("hostapi") or 0)
            host_name = ""
            if 0 <= host_index < len(host_apis):
                host_name = str(host_apis[host_index].get("name") or "")
            devices.append(
                {
                    "index": index,
                    "name": str(device.get("name") or f"Input {index}"),
                    "host_api": host_name,
                    "host_api_index": host_index,
                    "default": index == default_index,
                    "sample_rate": int(float(device.get("default_samplerate") or 16000)),
                }
            )
        return devices

    @classmethod
    def _resolve_input_device(cls, requested: Any) -> dict[str, Any]:
        devices = cls.list_input_devices()
        requested = requested if isinstance(requested, dict) else {}
        name = str(requested.get("name") or "").strip().casefold()
        host_api = str(requested.get("host_api") or "").strip().casefold()
        if not name:
            match = next((device for device in devices if device.get("default")), None)
            if match is None and devices:
                match = devices[0]
            if match is None:
                raise RuntimeError("No microphone input device is available.")
            return match
        for device in devices:
            if str(device.get("name") or "").strip().casefold() != name:
                continue
            if host_api and str(device.get("host_api") or "").strip().casefold() != host_api:
                continue
            return device
        raise RuntimeError(f"Saved microphone is not available: {requested.get('name') or name}")

    @staticmethod
    def _int16_audio_level(payload: bytes) -> dict[str, float]:
        samples = array.array("h")
        samples.frombytes(bytes(payload or b""))
        if not samples:
            return {"level": 0.0, "peak": 0.0}
        peak = max(abs(int(value)) for value in samples)
        mean_square = sum(int(value) * int(value) for value in samples) / float(len(samples))
        rms = math.sqrt(mean_square)
        if rms <= 0.0:
            level = 0.0
        else:
            dbfs = 20.0 * math.log10(min(1.0, rms / 32768.0))
            level = max(0.0, min(1.0, (dbfs + 60.0) / 60.0))
        if peak <= 0:
            peak_level = 0.0
        else:
            peak_dbfs = 20.0 * math.log10(min(1.0, peak / 32768.0))
            peak_level = max(0.0, min(1.0, (peak_dbfs + 60.0) / 60.0))
        return {
            "level": level,
            "peak": peak_level,
        }

    @classmethod
    def run_input_device_test(
        cls,
        requested: Any,
        stop_event: threading.Event,
        *,
        monitor_getter: Callable[[], bool] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run a live microphone meter and optional direct monitor until stopped."""
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "Offline voice dependencies are missing. Install requirements.txt before testing a microphone."
            ) from exc

        input_device = cls._resolve_input_device(requested)
        input_index = int(input_device["index"])
        input_info = sd.query_devices(input_index)
        input_host_api = input_info.get("hostapi")
        if input_host_api is None:
            input_host_api = input_device.get("host_api_index")
        host_api_index = int(0 if input_host_api is None else input_host_api)

        output_index = -1
        try:
            host_info = sd.query_hostapis(host_api_index)
            default_output = host_info.get("default_output_device")
            output_index = int(-1 if default_output is None else default_output)
        except Exception:
            output_index = -1
        if output_index < 0:
            try:
                candidate = int(sd.default.device[1])
                candidate_info = sd.query_devices(candidate)
                candidate_host_api = candidate_info.get("hostapi")
                if candidate_host_api is not None and int(candidate_host_api) == host_api_index:
                    output_index = candidate
            except Exception:
                output_index = -1

        output_info = None
        if output_index >= 0:
            try:
                output_info = sd.query_devices(output_index)
                if int(output_info.get("max_output_channels") or 0) <= 0:
                    output_info = None
            except Exception:
                output_info = None

        sample_candidates: list[int] = []
        for value in (
            input_info.get("default_samplerate"),
            output_info.get("default_samplerate") if output_info is not None else None,
            48000,
            44100,
            16000,
        ):
            try:
                rate = int(float(value))
            except Exception:
                continue
            if rate > 0 and rate not in sample_candidates:
                sample_candidates.append(rate)

        sample_rate = 0
        monitor_available = output_info is not None
        for rate in sample_candidates:
            try:
                sd.check_input_settings(device=input_index, channels=1, dtype="int16", samplerate=rate)
                if monitor_available:
                    sd.check_output_settings(device=output_index, channels=1, dtype="int16", samplerate=rate)
                sample_rate = rate
                break
            except Exception:
                continue
        if sample_rate <= 0 and monitor_available:
            monitor_available = False
            for rate in sample_candidates:
                try:
                    sd.check_input_settings(device=input_index, channels=1, dtype="int16", samplerate=rate)
                    sample_rate = rate
                    break
                except Exception:
                    continue
        if sample_rate <= 0:
            raise RuntimeError(f"The selected microphone does not support mono int16 input: {input_device['name']}")

        levels: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=2)

        def _queue_level(indata, status) -> None:
            event = {"type": "level", **cls._int16_audio_level(bytes(indata))}
            if status:
                event["stream_status"] = str(status)
            try:
                levels.put_nowait(event)
            except queue.Full:
                try:
                    levels.get_nowait()
                except queue.Empty:
                    pass
                try:
                    levels.put_nowait(event)
                except queue.Full:
                    pass

        def _input_callback(indata, _frames, _time_info, status) -> None:
            _queue_level(indata, status)

        def _duplex_callback(indata, outdata, _frames, _time_info, status) -> None:
            monitor = False
            if callable(monitor_getter):
                try:
                    monitor = bool(monitor_getter())
                except Exception:
                    monitor = False
            if monitor:
                outdata[:] = indata
            else:
                outdata[:] = b"\0" * len(outdata)
            _queue_level(indata, status)

        if monitor_available:
            stream = sd.RawStream(
                samplerate=sample_rate,
                blocksize=1024,
                device=(input_index, output_index),
                dtype=("int16", "int16"),
                channels=(1, 1),
                callback=_duplex_callback,
            )
        else:
            stream = sd.RawInputStream(
                samplerate=sample_rate,
                blocksize=1024,
                device=input_index,
                dtype="int16",
                channels=1,
                callback=_input_callback,
            )

        result = {
            "input_device": input_device,
            "output_device": (
                {
                    "index": output_index,
                    "name": str(output_info.get("name") or f"Output {output_index}"),
                }
                if monitor_available and output_info is not None
                else None
            ),
            "sample_rate": sample_rate,
            "monitor_available": monitor_available,
        }
        with stream:
            if callable(event_callback):
                event_callback({"type": "started", **result})
            while not stop_event.wait(0.04):
                latest = None
                while True:
                    try:
                        latest = levels.get_nowait()
                    except queue.Empty:
                        break
                if latest is not None and callable(event_callback):
                    event_callback(latest)
        return result

    def _recognition_worker(
        self,
        generation: int,
        stop_event: threading.Event,
        settings: dict[str, Any],
    ) -> None:
        language = settings["language"]
        spec = self.models.spec(language)
        try:
            model_path = self.models.model_path(language)
            if not self.models.is_installed(language):
                self._emit_status(
                    "error",
                    f"{spec.name} voice model is not installed. Open Voice settings and download it.",
                )
                return
            prefixes = merge_wake_prefixes(settings["prefixes"])
            wake_prefix = normalize_voice_phrase(prefixes.get(language))
            require_prefix = bool(settings.get("require_prefix")) and bool(wake_prefix)
            phrase_map, grammar = build_voice_phrase_map(
                language,
                settings["commands"],
                settings["prefixes"],
                require_prefix=require_prefix,
            )
            wake_phrase_map: dict[str, str | tuple[str, int]] = {}
            if require_prefix:
                wake_phrase_map, wake_grammar = build_voice_phrase_map(
                    language,
                    settings["commands"],
                    settings["prefixes"],
                    require_prefix=False,
                )
                grammar = list(
                    dict.fromkeys(
                        [
                            *(phrase for phrase in grammar if phrase != "[unk]"),
                            wake_prefix,
                            *(phrase for phrase in wake_grammar if phrase != "[unk]"),
                            "[unk]",
                        ]
                    )
                )
            if not phrase_map:
                self._emit_status("error", "No enabled voice command phrases are configured.")
                return

            self._emit_status("loading", f"Loading {spec.name} voice model...")
            try:
                import sounddevice as sd
                from vosk import KaldiRecognizer, Model, SetLogLevel
            except ImportError as exc:
                raise RuntimeError(
                    "Offline voice dependencies are missing. Install requirements.txt before enabling voice commands."
                ) from exc

            SetLogLevel(-1)
            device = self._resolve_input_device(settings["device"])
            model = Model(model_path)
            if stop_event.is_set() or generation != self._generation:
                return

            sample_rate = int(device.get("sample_rate") or 16000)
            recognizer = KaldiRecognizer(model, sample_rate, json.dumps(grammar, ensure_ascii=False))
            audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=32)

            def _audio_callback(indata, _frames, _time_info, status) -> None:
                if stop_event.is_set():
                    return
                if status:
                    logger.debug("Voice microphone stream status: %s", status)
                payload = bytes(indata)
                try:
                    audio_queue.put_nowait(payload)
                except queue.Full:
                    try:
                        audio_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        audio_queue.put_nowait(payload)
                    except queue.Full:
                        pass

            last_fired: dict[str, float] = {}
            cooldown_sec = float(settings["cooldown_ms"]) / 1000.0
            listening_message = f"Listening in {spec.name} on {device['name']}."
            wake_active = False
            wake_until = 0.0
            early_result_pending = False

            def _activate_wake() -> None:
                nonlocal wake_active, wake_until
                wake_until = time.monotonic() + 5.0
                if wake_active:
                    return
                wake_active = True
                self._emit_status(
                    "awaiting_command",
                    f'Wake prefix "{wake_prefix}" heard; waiting for a command.',
                    language=language,
                    device=device,
                )

            def _expire_wake_if_needed() -> None:
                nonlocal wake_active, wake_until
                if not wake_active or time.monotonic() < wake_until:
                    return
                wake_active = False
                wake_until = 0.0
                self._emit_status(
                    "listening",
                    listening_message,
                    language=language,
                    device=device,
                )

            with sd.RawInputStream(
                samplerate=sample_rate,
                blocksize=max(400, int(sample_rate * 0.05)),
                device=int(device["index"]),
                dtype="int16",
                channels=1,
                callback=_audio_callback,
            ):
                self._emit_status(
                    "listening",
                    listening_message,
                    language=language,
                    device=device,
                )
                while not stop_event.is_set() and generation == self._generation:
                    try:
                        payload = audio_queue.get(timeout=0.2)
                    except queue.Empty:
                        _expire_wake_if_needed()
                        continue
                    if not recognizer.AcceptWaveform(payload):
                        partial = ""
                        if require_prefix:
                            try:
                                partial = normalize_voice_phrase(json.loads(recognizer.PartialResult()).get("partial"))
                            except Exception:
                                partial = ""
                            if partial == wake_prefix or partial.startswith(wake_prefix + " "):
                                _activate_wake()
                            else:
                                _expire_wake_if_needed()
                        else:
                            try:
                                partial = normalize_voice_phrase(json.loads(recognizer.PartialResult()).get("partial"))
                            except Exception:
                                partial = ""
                        if not early_result_pending and partial:
                            active_partial_map = phrase_map
                            if require_prefix and wake_active and partial not in phrase_map:
                                active_partial_map = wake_phrase_map
                            if self._is_fast_terminal_partial(partial, active_partial_map):
                                early_result_pending = self.process_final_result(
                                    {"text": partial},
                                    phrase_map=active_partial_map,
                                    last_fired=last_fired,
                                    cooldown_sec=cooldown_sec,
                                    language=language,
                                )
                                if early_result_pending:
                                    wake_active = False
                                    wake_until = 0.0
                        continue
                    try:
                        raw_result = recognizer.Result()
                        result_data = json.loads(raw_result or "{}")
                        final_phrase = normalize_voice_phrase(result_data.get("text"))
                        if early_result_pending:
                            early_result_pending = False
                            wake_active = False
                            wake_until = 0.0
                            continue
                        if require_prefix and final_phrase == wake_prefix:
                            _activate_wake()
                            continue
                        active_phrase_map = phrase_map
                        if require_prefix and wake_active and final_phrase not in phrase_map:
                            active_phrase_map = wake_phrase_map
                        processed = self.process_final_result(
                            result_data,
                            phrase_map=active_phrase_map,
                            last_fired=last_fired,
                            cooldown_sec=cooldown_sec,
                            language=language,
                        )
                        if processed:
                            wake_active = False
                            wake_until = 0.0
                        else:
                            _expire_wake_if_needed()
                    except Exception:
                        logger.debug("Failed to process a final voice result", exc_info=True)
        except Exception as exc:
            if not stop_event.is_set() and generation == self._generation:
                logger.exception("Voice recognition stopped")
                self._emit_status("error", f"Voice recognition stopped: {exc}")

    @staticmethod
    def _mapped_action_name(mapped_action: Any) -> str:
        if isinstance(mapped_action, (tuple, list)) and mapped_action:
            return str(mapped_action[0])
        return str(mapped_action or "")

    @classmethod
    def _is_fast_terminal_partial(
        cls,
        phrase: str,
        phrase_map: dict[str, str | tuple[str, int]],
    ) -> bool:
        mapped_action = phrase_map.get(normalize_voice_phrase(phrase))
        action = cls._mapped_action_name(mapped_action).partition(":")[0]
        if action not in {"voice_play", "voice_pause"}:
            return False
        prefix = normalize_voice_phrase(phrase) + " "
        return not any(candidate.startswith(prefix) for candidate in phrase_map if candidate != phrase)

    def process_final_result(
        self,
        raw_result: str | dict,
        *,
        phrase_map: dict[str, str | tuple[str, int]],
        last_fired: dict[str, float],
        cooldown_sec: float,
        language: str,
        now: float | None = None,
    ) -> bool:
        """Dispatch one final recognizer result; partial results never use this path."""
        if isinstance(raw_result, dict):
            result = raw_result
        else:
            result = json.loads(raw_result or "{}")
        phrase = normalize_voice_phrase(result.get("text"))
        mapped_action = phrase_map.get(phrase)
        if not phrase or not mapped_action:
            return False
        if isinstance(mapped_action, (tuple, list)):
            action = str(mapped_action[0])
            repeat_count = max(1, min(20, int(mapped_action[1])))
        else:
            action = str(mapped_action)
            repeat_count = 1
        action_base = action.partition(":")[0]
        fired_at = time.monotonic() if now is None else float(now)
        previous = last_fired.get(action_base)
        if previous is not None and fired_at - float(previous) < max(0.0, float(cooldown_sec)):
            return False
        last_fired[action_base] = fired_at
        action_label = next(
            (
                str(spec.get("label") or action_base)
                for spec in VOICE_ACTIONS.values()
                if action_base == str(spec.get("dispatch") or "")
                or action_base.startswith(str(spec.get("dispatch") or "") + "_")
            ),
            "Combined command" if action_base == "voice_sequence" else action_base,
        )
        self._emit_status(
            "recognized",
            f'Recognized "{phrase}" -> {action_label}' + (f" x{repeat_count}." if repeat_count > 1 else "."),
            phrase=phrase,
            action=action,
            repeat_count=repeat_count,
            language=language,
        )
        self.action_callback(action, phrase, repeat_count)
        return True

    def model_status(self, language: str) -> dict[str, Any]:
        spec = self.models.spec(language)
        installed = self.models.is_installed(language)
        return {
            "language": spec.language,
            "name": spec.name,
            "model_id": spec.model_id,
            "download_size_mb": spec.download_size_mb,
            "installed": installed,
            "path": self.models.model_path(language),
        }

    def download_model(self, language: str) -> bool:
        with self._lock:
            if self._download_thread is not None and self._download_thread.is_alive():
                return False
            cancel = threading.Event()
            self._download_cancel = cancel

            def _progress(percent: int, message: str) -> None:
                self._emit_status("downloading", message, progress=int(percent), language=language)

            def _worker() -> None:
                restart = False
                try:
                    self.models.download(language, cancel=cancel, progress=_progress)
                    if not cancel.is_set():
                        self._emit_status("ready", f"{self.models.spec(language).name} voice model installed.")
                        restart = bool(self.config.get("VOICE_ENABLED") or False)
                except VoiceDownloadCancelled:
                    self._emit_status("disabled", "Voice model download cancelled.")
                except Exception as exc:
                    logger.exception("Voice model download failed")
                    self._emit_status("error", f"Voice model download failed: {exc}")
                finally:
                    with self._lock:
                        self._download_thread = None
                        self._download_cancel = None
                if restart and self._settings_snapshot()["enabled"]:
                    self.reconfigure(force=True)

            thread = threading.Thread(target=_worker, daemon=True, name="voice-model-download")
            self._download_thread = thread
        self.stop(emit=False)
        thread.start()
        return True

    def cancel_download(self) -> bool:
        with self._lock:
            cancel = self._download_cancel
        if cancel is None:
            return False
        cancel.set()
        return True

    def remove_model(self, language: str) -> bool:
        with self._lock:
            if self._download_thread is not None and self._download_thread.is_alive():
                raise RuntimeError("Cancel the active voice model download before removing a model.")
        selected = str(self.config.get("VOICE_LANGUAGE") or "en").lower()
        if selected == str(language).lower():
            self.stop(emit=False)
        removed = self.models.remove(language)
        if removed:
            self._emit_status("disabled", f"{self.models.spec(language).name} voice model removed.")
        return removed

    @classmethod
    def probe_input_device(cls, requested: Any, duration_sec: float = 0.6) -> dict[str, Any]:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "Offline voice dependencies are missing. Install requirements.txt before testing a microphone."
            ) from exc

        device = cls._resolve_input_device(requested)
        sample_rate = int(device.get("sample_rate") or 16000)
        peak = 0

        def _callback(indata, _frames, _time_info, _status) -> None:
            nonlocal peak
            samples = array.array("h")
            samples.frombytes(bytes(indata))
            if samples:
                peak = max(peak, max(abs(int(value)) for value in samples))

        with sd.RawInputStream(
            samplerate=sample_rate,
            blocksize=2048,
            device=int(device["index"]),
            dtype="int16",
            channels=1,
            callback=_callback,
        ):
            time.sleep(max(0.2, min(2.0, float(duration_sec))))
        return {"device": device, "peak": peak, "active": peak > 100}
