# app.py
import tkinter as tk
import logging
import threading

from view.settings_ui import SettingsUI
from view.subtitle_overlay import SubtitleOverlayUI
from view.popup import CopyPopup
from view.overlays import LoadingOverlay, set_startup_overlay

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer

from controller.controller import SubtitleController

# from video_sync_server import start_server, get_video_time

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s: %(message)s",
)
logger = logging.getLogger("SubtitlePlayer.App")


class SubtitlePlayerApp:
    def __init__(self):
        self.root = None
        self.config = None

        self._startup_done = threading.Event()
        self._startup_error = self._startup_result = self._startup_thread = self._startup_overlay = None
        self._closing = False
        self._startup_check_job = None

        self.sub_manager = None
        self.renderer = None
        self.controller = None
        self.settings_ui = None
        self.sub_overlay_ui = None
        self.popup = None
        self.total_duration = None

    def run(self):
        logger.info("Starting SubtitlePlayerApp")

        self._load_config()
        self._build_root()
        self._show_startup_overlay()
        self._start_startup_worker()

        self._startup_check_job = self.root.after(50, self._check_startup_worker)
        try:
            self.root.mainloop()
        finally:
            self._closing = True

    def _load_config(self):
        try:
            self.config = ConfigManager("config.json")
        except Exception:
            logger.exception("Failed to load config.json")
            raise SystemExit(1)
    
    def _build_root(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("SubtitlePlayer")
        self.root.geometry("280x115")
        self._restore_window_position()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
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
        except Exception as exc:
            self._startup_error = exc
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
            logger.exception("Startup failed", exc_info=self._startup_error)
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

    def _close_startup_overlay(self):
        if self._startup_overlay:
            try:
                self._startup_overlay.close()
            except Exception as e:
                print("ERROR:", e)
                pass
            self._startup_overlay = None
            set_startup_overlay(None)

    # =========================
    # Build components
    # =========================
    def _build_ui(self):
        self.popup = CopyPopup(root=self.root, config=self.config)

        overlay_geometry = self.sub_manager.calculate_geometry_for_longest_lines(5)
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
        except Exception as e:
            print("ERROR:", e)
            return None

    def _on_close(self):
        if self._closing:
            return
        self._closing = True
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
        except Exception as e:
            print("controller shutdown:", e)

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
