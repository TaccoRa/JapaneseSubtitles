"""OCR capture, timecode extraction, and OCR sync helper."""

import os
import re
import subprocess
import tempfile
import threading
import time
import pyautogui
from PIL import ImageGrab, ImageOps, ImageStat, Image
from typing import Any
from utils import get_monitor_rects, show_window_no_activate, parse_time_value

class _ControllerProxy:
    """Proxy base that forwards attribute access and assignment to SubtitleController."""

    def __init__(self, controller: Any) -> None:
        object.__setattr__(self, "controller", controller)

    def __getattr__(self, name: str):
        return getattr(self.controller, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "controller":
            object.__setattr__(self, name, value)
        else:
            setattr(self.controller, name, value)

class OCRController(_ControllerProxy):
    """OCR capture, timecode extraction, and OCR sync helper."""

    def __init__(self, controller: Any) -> None:
        super().__init__(controller)

    def _schedule_ocr_time_jump(self, reason: str) -> None:
            if self._shutting_down:
                return
            if not self.config.get("OCR_ENABLED"):
                return
            delay_ms = 800 if reason == "startup" else 500
            try:
                if self._ocr_job is not None:
                    self.settings.root.after_cancel(self._ocr_job)
            except Exception:
                pass
            self._ocr_generation += 1
            try:
                self._ocr_pending_time = float(self.current_time)
            except Exception:
                self._ocr_pending_time = None

            generation = self._ocr_generation

            def _kickoff():
                self._run_ocr_time_jump_async(generation)

            try:
                self._ocr_job = self.settings.root.after(delay_ms, _kickoff)
            except Exception:
                self._ocr_job = None

    def _run_ocr_time_jump_async(self, generation: int) -> None:
            if self._shutting_down:
                return

            def worker():
                seconds = self._ocr_find_time_seconds()
                if seconds is None:
                    return
                try:
                    self.settings.root.after(0, lambda: self._apply_ocr_time(seconds, generation))
                except Exception:
                    pass

            self._ocr_thread = threading.Thread(target=worker, daemon=True)
            self._ocr_thread.start()

    def _apply_ocr_time(self, seconds: float, generation: int) -> None:
            if self._shutting_down:
                return
            if generation != self._ocr_generation:
                return
            try:
                if self.playing:
                    return
            except Exception:
                pass
            pending = getattr(self, "_ocr_pending_time", None)
            if pending is not None:
                try:
                    if abs(float(self.current_time) - float(pending)) > 0.75:
                        return
                except Exception:
                    pass
            self.playback.set_current_time(seconds)

    @staticmethod
    def _rects_intersect(a, b) -> bool:
            if not a or not b:
                return False
            ax, ay, aw, ah = a
            bx, by, bw, bh = b
            return (ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by)
    
    @staticmethod
    def _window_screen_rect(win):
            if win is None:
                return None
            try:
                if not win.winfo_exists():
                    return None
                win.update_idletasks()
                x = int(win.winfo_rootx())
                y = int(win.winfo_rooty())
                w = int(win.winfo_width()) or int(win.winfo_reqwidth())
                h = int(win.winfo_height()) or int(win.winfo_reqheight())
                if w <= 0 or h <= 0:
                    return None
                return (x, y, w, h)
            except Exception:
                return None

    def _temporarily_hide_windows_for_ocr(self, override: dict | None = None):
            try:
                ocr_regions = [tuple(region) for _idx, region, _custom in self._get_ocr_capture_regions(override)]
            except Exception:
                ocr_regions = []
            if not ocr_regions:
                return lambda: None

            windows = [
                getattr(self.settings, "control_window", None),
                getattr(self.overlay, "sub_window", None),
                getattr(self.overlay, "subtitle_handle", None),
                getattr(self.settings, "advanced_window", None),
                getattr(self.settings, "root", None),
                getattr(self.popup, "_popup", None),
                getattr(self, "_anki_wait_window", None),
                getattr(self, "_anki_success_popup", None),
            ]
            hidden = []
            seen = set()

            for win in windows:
                if win is None:
                    continue
                try:
                    key = str(win)
                except Exception:
                    key = id(win)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    if not win.winfo_exists():
                        continue
                    state = str(win.state())
                    if state == "withdrawn":
                        continue
                    rect = self._window_screen_rect(win)
                    if not any(self._rects_intersect(rect, region) for region in ocr_regions):
                        continue
                    hidden.append((win, state))
                    win.withdraw()
                except Exception:
                    pass

            try:
                self.settings.root.update_idletasks()
            except Exception:
                pass

            passive_windows = {
                getattr(self.overlay, "subtitle_handle", None),
                getattr(self.popup, "_popup", None),
                getattr(self, "_anki_success_popup", None),
            }

            def restore():
                for win, state in hidden:
                    try:
                        if not win.winfo_exists():
                            continue
                        if state == "iconic":
                            win.iconify()
                            continue
                        if win in passive_windows:
                            show_window_no_activate(win)
                        else:
                            win.deiconify()
                            try:
                                if win is getattr(self.settings, "control_window", None):
                                    win.attributes("-topmost", True)
                                elif win is getattr(self.overlay, "sub_window", None):
                                    win.attributes("-topmost", True)
                                elif win is getattr(self.settings, "advanced_window", None):
                                    win.attributes("-topmost", True)
                            except Exception:
                                pass
                    except Exception:
                        pass
                try:
                    self.popup.ensure_on_top()
                except Exception:
                    pass

            return restore

    def on_ocr_read_now(self, override: dict | None = None) -> None:
            if self._shutting_down:
                return

            restore_windows = self._temporarily_hide_windows_for_ocr(override=override)

            def start_worker():
                def worker():
                    try:
                        seconds = self._ocr_find_time_seconds(override=override)
                        if seconds is None:
                            self._log_ocr_read_failure(override=override)
                            return
                        try:
                            self.settings.root.after(0, lambda: self._apply_ocr_time_manual(seconds))
                        except Exception:
                            pass
                    finally:
                        try:
                            self.settings.root.after(0, restore_windows)
                        except Exception:
                            restore_windows()

                threading.Thread(target=worker, daemon=True).start()

            try:
                self.settings.root.after(140, start_worker)
            except Exception:
                start_worker()

    def _log_ocr_read_failure(self, override: dict | None = None) -> None:
            try:
                regions = self._get_ocr_capture_regions(override=override)
            except Exception:
                regions = []
            if not regions:
                print("OCR read-now failed: no capture region available.")
                return
            details = []
            for region_idx, region, is_custom in regions:
                try:
                    x, y, w, h = region
                    mode = "custom" if is_custom else "default"
                    details.append(f"#{region_idx} {x},{y} {w}x{h} ({mode})")
                except Exception:
                    details.append(f"#{region_idx} <invalid region>")
            joined = "; ".join(details)
            print(f"OCR read-now failed: no valid timecode detected. Checked {len(regions)} region(s): {joined}")

    def on_ocr_sync_now(self, override: dict | None = None) -> None:
            if self._shutting_down:
                return
            if not self.playing:
                try:
                    self.playback.toggle_play()
                except Exception:
                    pass
            self._start_ocr_live_sync(duration_sec=5.0, interval_sec=0.25, override=override)

    def _apply_ocr_time_manual(self, seconds: float) -> None:
            if self._shutting_down:
                return
            self.playback.set_current_time(seconds)

    def _start_ocr_live_sync(
            self,
            duration_sec: float = 5.0,
            interval_sec: float = 0.25,
            override: dict | None = None,
        ) -> None:
            if self._shutting_down:
                return
            if not self.config.get("OCR_ENABLED"):
                return
            try:
                duration_sec = float(duration_sec)
            except Exception:
                duration_sec = 5.0
            try:
                interval_sec = float(interval_sec)
            except Exception:
                interval_sec = 1.0
            duration_sec = max(1.0, duration_sec)
            interval_sec = max(0.1, interval_sec)

            self._ocr_sync_generation += 1
            generation = self._ocr_sync_generation

            def worker():
                deadline = time.perf_counter() + duration_sec
                snapped_initial = False
                while time.perf_counter() < deadline:
                    if self._shutting_down or generation != self._ocr_sync_generation:
                        return
                    started = time.perf_counter()
                    try:
                        base_time = float(self.current_time)
                    except Exception:
                        base_time = None
                    seconds = self._ocr_find_time_seconds(override=override)
                    if seconds is not None and base_time is not None:
                        if not snapped_initial:
                            try:
                                self.settings.root.after(0, lambda s=seconds: self._apply_ocr_time_manual(s))
                            except Exception:
                                pass
                            snapped_initial = True
                            sleep_for = interval_sec - (time.perf_counter() - started)
                            if sleep_for > 0:
                                time.sleep(sleep_for)
                            continue
                        elapsed = time.perf_counter() - started
                        if self.playing:
                            base_time += elapsed
                        delta = seconds - base_time
                        if abs(delta) >= 0.15:
                            adjust = max(-0.5, min(0.5, delta * 0.5))
                            try:
                                self.settings.root.after(0, lambda d=adjust: self._apply_ocr_sync_delta(d))
                            except Exception:
                                pass
                    sleep_for = interval_sec - (time.perf_counter() - started)
                    if sleep_for > 0:
                        time.sleep(sleep_for)

            threading.Thread(target=worker, daemon=True).start()

    def _schedule_ocr_sync_after_anki(self, duration_sec: float = 5.0, interval_sec: float = 1.0) -> None:
            if self._shutting_down:
                return
            if not self.config.get("OCR_ENABLED"):
                return
            if not self._ocr_sync_after_anki_enabled():
                return
            try:
                duration_sec = float(duration_sec)
            except Exception:
                duration_sec = 5.0
            try:
                interval_sec = float(interval_sec)
            except Exception:
                interval_sec = 1.0
            duration_sec = max(1.0, duration_sec)
            interval_sec = max(0.4, interval_sec)

            self._ocr_sync_generation += 1
            generation = self._ocr_sync_generation

            def worker():
                diffs = []
                deadline = time.perf_counter() + duration_sec
                while time.perf_counter() < deadline:
                    if self._shutting_down or generation != self._ocr_sync_generation:
                        return
                    started = time.perf_counter()
                    try:
                        base_time = float(self.current_time)
                    except Exception:
                        base_time = None
                    seconds = self._ocr_find_time_seconds(override={"OCR_DEBUG": False})
                    if seconds is not None and base_time is not None:
                        elapsed = time.perf_counter() - started
                        if self.playing:
                            base_time += elapsed
                        diffs.append(seconds - base_time)
                    sleep_for = interval_sec - (time.perf_counter() - started)
                    if sleep_for > 0:
                        time.sleep(sleep_for)
                if not diffs:
                    return
                diffs.sort()
                mid = len(diffs) // 2
                if len(diffs) % 2 == 1:
                    median = diffs[mid]
                else:
                    median = (diffs[mid - 1] + diffs[mid]) / 2.0
                try:
                    self.settings.root.after(0, lambda: self._apply_ocr_sync_delta(median))
                except Exception:
                    pass

            self._ocr_sync_thread = threading.Thread(target=worker, daemon=True)
            self._ocr_sync_thread.start()

    def _apply_ocr_sync_delta(self, delta: float) -> None:
            if self._shutting_down:
                return
            try:
                delta = float(delta)
            except Exception:
                return
            if abs(delta) < 0.15:
                return
            try:
                new_time = float(self.current_time) + delta
            except Exception:
                return
            self.playback.set_current_time(new_time)
            print(f"OCR sync: adjusted by {delta:+.2f}s")

    def _ocr_find_time_seconds(self, override: dict | None = None):
            regions = self._get_ocr_capture_regions(override)
            if not regions:
                return None

            started = time.perf_counter()
            variant_makers = []

            def _make_autocontrast(img):
                gray = ImageOps.grayscale(img)
                return ImageOps.autocontrast(gray)

            def _make_gray(img):
                return ImageOps.grayscale(img)

            variant_makers.append(("autocontrast", _make_autocontrast))
            variant_makers.append(("gray", _make_gray))

            # Threshold variant (disabled for now; keep for later tuning)
            # def _make_threshold(img):
            #     gray = ImageOps.grayscale(img)
            #     stat = ImageStat.Stat(gray)
            #     median = int(stat.median[0]) if stat.median else 128
            #     threshold = min(255, max(0, median + 10))
            #     thresh_img = gray.point(lambda p, t=threshold: 255 if p >= t else 0)
            #     if median < 128:
            #         thresh_img = ImageOps.invert(thresh_img)
            #     return thresh_img
            # variant_makers.append(("threshold", _make_threshold))

            region_images = []
            for region_idx, region, is_custom in regions:
                img = self._capture_ocr_image(region)
                if img is None:
                    region_images.append((region_idx, None))
                    continue
                if is_custom:
                    try:
                        img = img.resize((img.width * 2, img.height * 2), Image.BICUBIC)
                    except Exception:
                        pass
                region_images.append((region_idx, img))

            for label, maker in variant_makers:
                for region_idx, img in region_images:
                    if img is None:
                        continue
                    try:
                        variant = maker(img)
                    except Exception:
                        variant = img
                    text = self._ocr_image_to_text(variant, override=override)
                    if not text:
                        continue
                    result = self._extract_time_from_ocr_text(text, override=override)
                    if result is None:
                        continue
                    seconds, left, right = result
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    print(f"OCR result: {left} / {right} -> {seconds:.2f}s (box {region_idx}, {label}, {elapsed_ms:.0f} ms)")
                    return seconds
            return None

    def _ocr_image_to_text(self, image, override: dict | None = None) -> str:
            config_str, config_args = self._build_tesseract_config(override)
            tesseract_cmd = self._resolve_tesseract_cmd(override)
            try:
                import pytesseract
                if tesseract_cmd:
                    try:
                        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
                    except Exception:
                        pass
                return pytesseract.image_to_string(image, config=config_str) or ""
            except Exception:
                pass

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    tmp_path = tmp.name
                    image.save(tmp_path)
                cmd = tesseract_cmd or "tesseract"
                result = subprocess.run(
                    [cmd, tmp_path, "stdout", *config_args],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                return result.stdout or ""
            except Exception:
                return ""
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

    def _extract_time_from_ocr_text(self, text: str, override: dict | None = None):
            if not text:
                return None
            cleaned = text.replace(" ", "").replace("\n", "").replace("\t", "")
            cleaned = (cleaned
                    .replace("O", "0")
                    .replace("o", "0")
                    .replace("I", "1")
                    .replace("l", "1")
                    .replace(";", ":"))

            matches = self.OCR_TIME_PATTERN.findall(cleaned)
            if not matches:
                # Fallback 1: digit-only timecodes (e.g. 1823/2341)
                digit_matches = re.findall(r"(\d{3,4})[/\\|](\d{3,4})", cleaned)
                if digit_matches:
                    matches = digit_matches

            if not matches:
                # Fallback 2: 8-digit compact timecodes (e.g. 18232341 -> 18:23 / 23:41)
                digits_only = re.sub(r"\D", "", cleaned)
                for i in range(max(0, len(digits_only) - 7)):
                    run = digits_only[i:i + 8]
                    if len(run) < 8:
                        continue
                    left_digits = run[:4]
                    right_digits = run[4:]
                    try:
                        mm1 = int(left_digits[:2])
                        ss1 = int(left_digits[2:])
                        mm2 = int(right_digits[:2])
                        ss2 = int(right_digits[2:])
                    except Exception:
                        continue
                    if mm1 > 59 or ss1 > 59 or mm2 > 59 or ss2 > 59:
                        continue
                    left = f"{mm1:02d}:{ss1:02d}"
                    right = f"{mm2:02d}:{ss2:02d}"
                    matches = [(left, right)]
                    break

            if not matches:
                # Fallback 3: two timecodes found back-to-back (missing separator)
                time_re = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")
                time_hits = list(time_re.finditer(cleaned))
                for i in range(len(time_hits) - 1):
                    gap = time_hits[i + 1].start() - time_hits[i].end()
                    if gap <= 2:
                        matches = [(time_hits[i].group(0), time_hits[i + 1].group(0))]
                        break

            if not matches:
                # Fallback 4: left time + trailing digits (e.g. 18:2312341 -> 18:23 / 23:41)
                tail_match = re.search(r"(\d{1,2}:\d{2})(\d{4,6})", cleaned)
                if tail_match:
                    left = tail_match.group(1)
                    tail = re.sub(r"\D", "", tail_match.group(2) or "")
                    if len(tail) >= 4:
                        right_digits = tail[-4:]
                        try:
                            mm = int(right_digits[:2])
                            ss = int(right_digits[2:])
                        except Exception:
                            mm, ss = 99, 99
                        if mm > 59 or ss > 59:
                            right_digits = ""
                        if not right_digits:
                            pass
                        else:
                            right = f"{right_digits[:2]}:{right_digits[2:]}"
                            matches = [(left, right)]
            if not matches:
                return None

            max_allow = None
            try:
                max_allow = float(self.total_duration) + float(self.settings._last_offset_value or 0.0)
            except Exception:
                max_allow = None

            for left, right in matches:
                try:
                    left_sec = parse_time_value(left)
                    right_sec = parse_time_value(right)
                except Exception:
                    continue
                if right_sec > 0 and left_sec > right_sec + 1.0:
                    continue
                if max_allow is not None and max_allow > 0 and left_sec > (max_allow + 2.0):
                    continue
                return left_sec, left, right
            return None

    @staticmethod
    def coerce_int(value, default: int = 0) -> int:
            try:
                return int(float(str(value).strip().replace(",", ".")))
            except Exception:
                return int(default)
    
    @staticmethod
    def coerce_float(value, default: float = 0.0) -> float:
            try:
                return float(str(value).strip().replace(",", "."))
            except Exception:
                return float(default)

    def _ocr_sync_after_anki_enabled(self) -> bool:
            raw = self.config.get("OCR_SYNC_AFTER_ANKI")
            if raw is None:
                return True
            return bool(raw)

    def _build_tesseract_config(self, override: dict | None = None):
            psm = self.coerce_int(
                (override or {}).get("OCR_TESSERACT_PSM", self.config.get("OCR_TESSERACT_PSM")),
                default=6,
            )
            oem = self.coerce_int(
                (override or {}).get("OCR_TESSERACT_OEM", self.config.get("OCR_TESSERACT_OEM")),
                default=3,
            )
            whitelist = (override or {}).get("OCR_CHAR_WHITELIST", self.config.get("OCR_CHAR_WHITELIST"))
            whitelist = str(whitelist or "").strip() or "0123456789:/"

            args = ["--psm", str(psm)]
            if oem >= 0:
                args += ["--oem", str(oem)]
            if whitelist:
                args += ["-c", f"tessedit_char_whitelist={whitelist}"]

            return " ".join(args), args

    def _resolve_tesseract_cmd(self, override: dict | None = None):
            raw = ""
            if override and "OCR_TESSERACT_CMD" in override:
                raw = str(override.get("OCR_TESSERACT_CMD") or "").strip()
            if not raw:
                raw = str(self.config.get("OCR_TESSERACT_CMD") or "").strip()
            if not raw:
                raw = (os.environ.get("TESSERACT_CMD") or os.environ.get("TESSERACT_PATH") or "").strip()
            if not raw:
                return None
            path = os.path.expandvars(raw)
            if os.path.isdir(path):
                exe = os.path.join(path, "tesseract.exe")
                if os.path.exists(exe):
                    return exe
            return path

    def _get_ocr_setting(self, key: str, override: dict | None = None, default=None):
            if override and key in override:
                return override.get(key)
            value = self.config.get(key)
            return default if value is None else value

    def _get_ocr_region_count(self, override: dict | None = None) -> int:
            raw = self._get_ocr_setting("OCR_REGION_COUNT", override, default=2)
            count = self.coerce_int(raw, default=2)
            return max(1, min(8, count))

    def _get_ocr_capture_regions(self, override: dict | None = None):
            monitors = get_monitor_rects(self.settings.root)
            if not monitors:
                monitors = [(0, 0, 1920, 1080)]

            regions = []
            count = self._get_ocr_region_count(override)
            default_screen = self.coerce_int(
                self._get_ocr_setting("OCR_SCREEN_INDEX", override, default=1),
                default=1,
            )

            def _suffix(idx: int) -> str:
                return "" if idx == 1 else str(idx)

            def _build_region(idx: int, default_bottom: bool):
                suffix = _suffix(idx)
                region_screen = self.coerce_int(
                    self._get_ocr_setting(f"OCR_REGION{suffix}_SCREEN", override, default=default_screen),
                    default=default_screen,
                )
                base_x, base_y, base_w, base_h = self._resolve_ocr_screen_rect(monitors, region_screen)
                rx = self.coerce_int(self._get_ocr_setting(f"OCR_REGION{suffix}_X", override, default=0), default=0)
                ry = self.coerce_int(self._get_ocr_setting(f"OCR_REGION{suffix}_Y", override, default=0), default=0)
                rw = self.coerce_int(self._get_ocr_setting(f"OCR_REGION{suffix}_W", override, default=0), default=0)
                rh = self.coerce_int(self._get_ocr_setting(f"OCR_REGION{suffix}_H", override, default=0), default=0)

                if rw <= 0 or rh <= 0:
                    if not default_bottom:
                        return None
                    half_h = max(1, int(base_h / 2))
                    return (base_x, base_y + (base_h - half_h), base_w, half_h), False

                if rx < 0:
                    rx = 0
                if ry < 0:
                    ry = 0
                if rw > base_w:
                    rw = base_w
                if rh > base_h:
                    rh = base_h
                return (base_x + rx, base_y + ry, rw, rh), True

            for idx in range(1, count + 1):
                default_bottom = (idx == 1)
                built = _build_region(idx, default_bottom=default_bottom)
                if built:
                    region, is_custom = built
                    regions.append((idx, region, is_custom))
            return regions
    
    @staticmethod
    def _resolve_ocr_screen_rect(monitors, screen_idx: int):
            if not monitors:
                return (0, 0, 1920, 1080)
            if screen_idx <= 0:
                min_x = min(r[0] for r in monitors)
                min_y = min(r[1] for r in monitors)
                max_x = max(r[0] + r[2] for r in monitors)
                max_y = max(r[1] + r[3] for r in monitors)
                x, y = int(min_x), int(min_y)
                w, h = int(max_x - min_x), int(max_y - min_y)
                return x, y, w, h

            if screen_idx > len(monitors):
                screen_idx = 1
            x, y, w, h = monitors[screen_idx - 1]
            return int(x), int(y), int(w), int(h)

    def _get_ocr_base_rect(self, override: dict | None = None):
            monitors = get_monitor_rects(self.settings.root)
            if not monitors:
                monitors = [(0, 0, 1920, 1080)]
            screen_idx = self.coerce_int(
                self._get_ocr_setting("OCR_SCREEN_INDEX", override, default=1),
                default=1,
            )
            return self._resolve_ocr_screen_rect(monitors, screen_idx)

    def _get_ocr_capture_region(self, override: dict | None = None):
            regions = self._get_ocr_capture_regions(override)
            if not regions:
                return None
            return regions[0][1]

    def _capture_ocr_image(self, region):
            if not region:
                return None
            x, y, w, h = region
            bbox = (int(x), int(y), int(x + w), int(y + h))
            try:
                return ImageGrab.grab(bbox=bbox, all_screens=True)
            except Exception:
                pass
            try:
                return ImageGrab.grab(bbox=bbox)
            except Exception:
                pass
            try:
                # Fallback to pyautogui (may ignore negative coords)
                if x >= 0 and y >= 0:
                    return pyautogui.screenshot(region=(int(x), int(y), int(w), int(h)))
                return pyautogui.screenshot()
            except Exception:
                return None
