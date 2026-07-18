# app.py
import faulthandler
import tkinter as tk
import logging
import os
import sys
import threading
import time
from tkinter import messagebox

from view.settings_ui import SettingsUI
from view.subtitle_overlay import SubtitleOverlayUI
from view.popup import CopyPopup
from view.overlays import LoadingOverlay, set_startup_overlay

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer

from controller.controller import SubtitleController
from logging_setup import setup_logging
from utils import TkMainThreadDispatcher, dispatch_to_tk

# from video_sync_server import start_server, get_video_time

logger = logging.getLogger("SubtitlePlayer.App")


class SubtitlePlayerApp:
    def __init__(self):
        self.root = None
        self.config = None
        self.log_path = ""

        self._startup_done = threading.Event()
        self._startup_error = self._startup_result = self._startup_thread = self._startup_overlay = None
        self._startup_error_info = None
        self._closing = False
        self._startup_check_job = None
        self._last_error_popup_at = 0.0
        self._fault_log_handle = None
        self._crash_marker_path = ""
        self._previous_run_unclean = False
        self._tk_dispatcher = None

        self.sub_manager = None
        self.renderer = None
        self.controller = None
        self.settings_ui = None
        self.sub_overlay_ui = None
        self.popup = None
        self.total_duration = None

    def run(self):
        self._load_config()
        try:
            logger.info("Starting SubtitlePlayerApp")
            self._build_root()
            self._show_startup_overlay()
            self._start_startup_worker()
            self._startup_check_job = self.root.after(50, self._check_startup_worker)
            self.root.mainloop()
            logger.info("SubtitlePlayer main loop exited normally (closing=%s)", self._closing)
        except Exception as exc:
            logger.critical("SubtitlePlayer terminated because of an unhandled error", exc_info=True)
            self._show_error_message_once("SubtitlePlayer stopped", f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self._closing = True
            self._close_crash_diagnostics()

    def _load_config(self):
        try:
            self.config = ConfigManager("config.json")
            log_path = setup_logging(self.config)
            self.log_path = str(log_path or "")
            self._enable_crash_diagnostics()
            logger.debug("Logging to %s", log_path)
        except Exception:
            logger.exception("Failed to load config.json")
            raise SystemExit(1)
    
    def _build_root(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("SubtitlePlayer")
        self.root.geometry("280x115")
        self.root.report_callback_exception = self._report_tk_callback_exception
        threading.excepthook = self._report_thread_exception
        sys.excepthook = self._report_main_exception
        self._tk_dispatcher = TkMainThreadDispatcher(self.root)
        self._restore_window_position()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _enable_crash_diagnostics(self) -> None:
        log_dir = os.path.dirname(os.path.abspath(self.log_path or os.path.join("logs", "subtitleplayer.log")))
        os.makedirs(log_dir, exist_ok=True)
        self._crash_marker_path = os.path.join(log_dir, "subtitleplayer.running")
        self._previous_run_unclean = os.path.exists(self._crash_marker_path)
        try:
            with open(self._crash_marker_path, "w", encoding="ascii") as marker:
                marker.write(f"pid={os.getpid()} started={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        except Exception:
            logger.debug("Failed to create crash marker", exc_info=True)

        try:
            crash_path = os.path.join(log_dir, "native_crash.log")
            self._fault_log_handle = open(crash_path, "a", encoding="utf-8", buffering=1)
            self._fault_log_handle.write(
                f"\n--- SubtitlePlayer run {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} ---\n"
            )
            faulthandler.enable(file=self._fault_log_handle, all_threads=True)
        except Exception:
            self._fault_log_handle = None
            logger.debug("Failed to enable native crash diagnostics", exc_info=True)

    def _close_crash_diagnostics(self) -> None:
        marker_path = str(getattr(self, "_crash_marker_path", "") or "")
        if marker_path:
            try:
                os.remove(marker_path)
            except FileNotFoundError:
                pass
            except Exception:
                logger.debug("Failed to remove crash marker", exc_info=True)
        try:
            if faulthandler.is_enabled():
                faulthandler.disable()
        except Exception:
            pass
        handle = getattr(self, "_fault_log_handle", None)
        self._fault_log_handle = None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    def _report_main_exception(self, exc_type, exc, tb) -> None:
        if exc_type is KeyboardInterrupt:
            return
        logger.critical("Unhandled main-thread exception", exc_info=(exc_type, exc, tb))
        self._show_error_message_once("Unexpected app error", f"{exc_type.__name__}: {exc}")

    def _report_tk_callback_exception(self, exc_type, exc, tb) -> None:
        logger.exception("Unhandled Tkinter callback exception", exc_info=(exc_type, exc, tb))
        self._show_error_message_once("Unexpected app error", f"{exc_type.__name__}: {exc}")

    def _report_thread_exception(self, args) -> None:
        if getattr(args, "exc_type", None) is SystemExit:
            return
        logger.exception(
            "Unhandled background thread exception",
            exc_info=(getattr(args, "exc_type", None), getattr(args, "exc_value", None), getattr(args, "exc_traceback", None)),
        )
        root = getattr(self, "root", None)
        if root is None or self._closing:
            return
        try:
            dispatch_to_tk(
                root,
                self._show_error_message_once,
                "Unexpected background error",
                f"{getattr(args, 'exc_type', Exception).__name__}: {getattr(args, 'exc_value', '')}",
            )
        except Exception:
            pass

    def _show_error_message_once(self, title: str, detail: str) -> None:
        now = time.monotonic()
        if now - float(getattr(self, "_last_error_popup_at", 0.0) or 0.0) < 2.0:
            return
        self._last_error_popup_at = now
        message = str(detail or "An unexpected error occurred.").strip()
        if self.log_path:
            message = f"{message}\n\nDetails were written to:\n{self.log_path}"
        try:
            messagebox.showerror(title, message, parent=self.root)
        except Exception:
            logger.debug("Failed to show error message", exc_info=True)
    def _show_startup_overlay(self):
        self._startup_overlay = LoadingOverlay(self.root,text="Starting SubtitlePlayer...",modal=False)
        set_startup_overlay(self._startup_overlay)

    def _start_startup_worker(self):
        self._startup_thread = threading.Thread(target=self._startup_worker,daemon=True)
        self._startup_thread.start()

    def _startup_worker(self):
        try:
            sub_manager = SubtitleManager(self.config)
            total_duration = sub_manager.get_total_duration()
            if self._closing:
                try:
                    shutdown = getattr(sub_manager, "shutdown", None)
                    if callable(shutdown):
                        shutdown()
                except Exception:
                    pass
                return
            self._startup_result = (sub_manager, total_duration)
            logger.info("Startup data preparation completed")
        except Exception as exc:
            self._startup_error = exc
            self._startup_error_info = sys.exc_info()
            logger.error("Startup data preparation failed: %s", exc, exc_info=self._startup_error_info)
        finally:
            self._startup_done.set()

    def _check_startup_worker(self):
        if self._closing:
            return
        if not self._startup_done.is_set():
            self._startup_check_job = self.root.after(50, self._check_startup_worker)
            return
        self._startup_check_job = None
        self.root.after(0, self._finish_startup)

    def _finish_startup(self):
        if self._closing:
            return
        if self._startup_error or not self._startup_result:
            self._close_startup_overlay()
            error = self._startup_error or RuntimeError("Startup did not return a result.")
            logger.error("Startup failed: %s", error, exc_info=self._startup_error_info)
            try:
                self.root.deiconify()
                self.root.lift()
            except Exception:
                pass
            self._show_error_message_once("SubtitlePlayer startup failed", f"{type(error).__name__}: {error}")
            self._startup_error_info = None
            self._destroy_root()
            return

        self.sub_manager, self.total_duration = self._startup_result
        self.root.after(0, self._finish_startup_ui)

    def _finish_startup_ui(self):
        if self._closing:
            return
        self._build_ui()
        self._build_renderer()
        self._build_controller()

        self._update_title()
        self.root.deiconify()
        self._close_startup_overlay()
        self.sub_overlay_ui.show()
        self.controller.schedule_update()
        if self._previous_run_unclean:
            self.root.after(
                250,
                lambda: self._show_error_message_once(
                    "Previous run ended unexpectedly",
                    "The previous SubtitlePlayer run did not shut down normally. Native crash details, if available, were written to logs/native_crash.log.",
                ),
            )

    def _close_startup_overlay(self):
        if self._startup_overlay:
            try:
                self._startup_overlay.close()
            except Exception:
                logger.debug("Failed to close startup overlay", exc_info=True)
            self._startup_overlay = None
            set_startup_overlay(None)

    # =========================
    # Build components
    # =========================
    def _build_ui(self):
        self.popup = CopyPopup(root=self.root, config=self.config)

        overlay_geometry = self.sub_manager.calculate_geometry()
        cleaned_subs = [item[0] for item in self.sub_manager.display_data]

        self.sub_overlay_ui = SubtitleOverlayUI(
            root=self.root,
            config=self.config,
            cleaned_subs=cleaned_subs,
            overlay_geometry=overlay_geometry,
            start_hidden=True,
        )

        self.settings_ui = SettingsUI(
            root=self.root,
            config=self.config,
            total_duration=self.total_duration,
            initial_episode=self.sub_manager.get_current_episode(),
        )

    def _build_renderer(self):
        self.renderer = SubtitleRenderer(
            config=self.config,
            canvas=self.sub_overlay_ui.subtitle_canvas,
        )

    def _build_controller(self):
        self.controller = SubtitleController(
            manager=self.sub_manager,
            renderer=self.renderer,
            settings_ui=self.settings_ui,
            overlay_ui=self.sub_overlay_ui,
            popup=self.popup,
            config=self.config,
            total_duration=self.total_duration,
        )

    # =========================
    # Window / UI helpers
    # =========================
    def _update_title(self):
        s = self.sub_manager.get_current_season()
        e = self.sub_manager.get_current_episode()
        n = self.sub_manager.get_anime_name() or "SubtitlePlayer"

        if s is None and e is None:
            title = n
        elif s is None:
            title = f"E{e} {n}"
        else:
            title = f"S{s}E{e} {n}"

        self.root.title(title)

    def _get_screen_size(self):
        sw = int(self.root.winfo_vrootwidth() or 0)
        sh = int(self.root.winfo_vrootheight() or 0)
        if sw <= 1 or sh <= 1:
            sw = int(self.root.winfo_screenwidth() or 1920)
            sh = int(self.root.winfo_screenheight() or 1080)
        return sw, sh

    def _restore_window_position(self):
        x,y = self.config.get("LAST_SETTINGS_WINDOW_X"), self.config.get("LAST_SETTINGS_WINDOW_Y")
        w,h = self.config.get("LAST_SETTINGS_WINDOW_WIDTH") or 280, self.config.get("LAST_SETTINGS_WINDOW_HEIGHT") or 115
        sw, sh = self._get_screen_size()
        w = max(120, min(int(w), sw))
        h = max(80, min(int(h), sh))
        if not isinstance(x, int) or not isinstance(y, int):
            x = int((sw - w) / 2)
            y = int((sh - h) / 2)
        else:
            x = max(0, min(x, sw - w))
            y = max(0, min(y, sh - h))

        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _read_geometry(self):
        try:
            geo = self.root.winfo_geometry()
            size, pos = geo.split("+", 1)
            w, h = map(int, size.split("x"))
            x, y = map(int, pos.split("+"))
            return w, h, x, y
        except Exception:
            logger.debug("Failed to read root geometry", exc_info=True)
            return None

    def _on_close(self):
        if self._closing:
            return
        self._closing = True
        logger.info("SubtitlePlayer shutdown requested")
        if self._startup_check_job is not None:
            try:
                self.root.after_cancel(self._startup_check_job)
            except Exception:
                pass
            self._startup_check_job = None

        try:
            if self.controller is not None:
                self.controller.shutdown()
                return
        except Exception:
            logger.debug("Controller shutdown failed", exc_info=True)

        try:
            if self.sub_manager is not None:
                shutdown = getattr(self.sub_manager, "shutdown", None)
                if callable(shutdown):
                    shutdown()
        except Exception:
            pass

        try:
            if self.popup is not None:
                self.popup._close()
        except Exception:
            pass

        try:
            if self.settings_ui is not None and getattr(self.settings_ui, "advanced_window", None):
                win = self.settings_ui.advanced_window
                if win.winfo_exists():
                    win.destroy()
        except Exception:
            pass
        self._close_startup_overlay()
        self._destroy_root()

    def _destroy_root(self):
        dispatcher = getattr(self, "_tk_dispatcher", None)
        self._tk_dispatcher = None
        if dispatcher is not None:
            try:
                dispatcher.close()
            except Exception:
                pass
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            if self.root is not None and self.root.winfo_exists():
                self.root.destroy()
        except Exception:
            pass
