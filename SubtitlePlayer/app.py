# app.py
import tkinter as tk
import logging
import threading
from threading import Thread

from view.settings_ui import SettingsUI
from view.subtitle_overlay import SubtitleOverlayUI
from view.popup import CopyPopup
from view.overlays import LoadingOverlay, set_startup_overlay

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer

from controller.controller import SubtitleController
from controller.playback_controller import PlaybackController

# from video_sync_server import start_server, get_video_time

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class SubtitlePlayerApp:
    def __init__(self):
        # Core state
        self.root = None
        self.config = None

        # Startup state
        self._startup_done = threading.Event()
        self._startup_error = None
        self._startup_result = None
        self._startup_thread = None
        self._startup_overlay = None

        # App components
        self.sub_manager = None
        self.total_duration = None
        self.renderer = None
        self.controller = None
        self.settings_ui = None
        self.sub_overlay_ui = None
        self.popup = None

    def run(self):
        logger.info("Starting SubtitlePlayerApp")

        self._load_config()
        self._build_root()

        self._show_startup_overlay()
        self._start_startup_worker()

        self.root.after(50, self._check_startup_worker)
        self.root.mainloop()

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
        self._startup_overlay = LoadingOverlay(
            self.root,
            text="Starting SubtitlePlayer...",
            modal=False,
        )
        set_startup_overlay(self._startup_overlay)

    def _start_startup_worker(self):
        self._startup_thread = Thread(
            target=self._startup_worker,
            daemon=True
        )
        self._startup_thread.start()

    def _startup_worker(self):
        try:
            sub_manager = SubtitleManager(self.config)
            total_duration = sub_manager.get_total_duration()
            self._startup_result = (sub_manager, total_duration)
        except Exception as exc:
            self._startup_error = exc
        finally:
            self._startup_done.set()

    def _check_startup_worker(self):
        if not self._startup_done.is_set():
            self.root.after(50, self._check_startup_worker)
            return

        self._finish_startup()

    def _finish_startup(self):
        self._close_startup_overlay()

        if self._startup_error or not self._startup_result:
            logger.exception("Startup failed", exc_info=self._startup_error)
            self.root.destroy()
            return

        self.sub_manager, self.total_duration = self._startup_result

        self._build_ui()
        self._build_renderer()
        self._build_controller()

        self._update_title()

        self.root.deiconify()
        self.sub_overlay_ui.show()

        self.root.after(
            self.config.get("UPDATE_INTERVAL_MS"),
            self.controller.update_loop,
        )

    def _close_startup_overlay(self):
        if self._startup_overlay:
            try:
                self._startup_overlay.close()
            except Exception:
                pass
            self._startup_overlay = None
            set_startup_overlay(None)

    # =========================
    # Build components
    # =========================
    def _build_ui(self):
        self.popup = CopyPopup(root=self.root, config=self.config)

        overlay_geometry = self.sub_manager.get_subtitle_geometry()

        self.sub_overlay_ui = SubtitleOverlayUI(
            root=self.root,
            config=self.config,
            cleaned_subs=[item[0] for item in self.sub_manager.display_data],
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
        self.playback = PlaybackController(self)
        self.controller = SubtitleController(
            manager=self.sub_manager,
            renderer=self.renderer,
            settings_ui=self.settings_ui,
            overlay_ui=self.sub_overlay_ui,
            popup=self.popup,
            config=self.config,
            playback=self.playback,
            total_duration=self.total_duration,
        )
        self.playback.set_controller(self.controller)

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
        x = self.config.get("LAST_SETTINGS_WINDOW_X")
        y = self.config.get("LAST_SETTINGS_WINDOW_Y")
        w = self.config.get("LAST_SETTINGS_WINDOW_WIDTH") or 280
        h = self.config.get("LAST_SETTINGS_WINDOW_HEIGHT") or 115

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
            return None

    # =========================
    # Shutdown
    # =========================
    def _on_close(self):
        geom = self._read_geometry()

        if geom:
            w, h, x, y = geom
            self.config.set("LAST_SETTINGS_WINDOW_X", x)
            self.config.set("LAST_SETTINGS_WINDOW_Y", y)
            self.config.set("LAST_SETTINGS_WINDOW_WIDTH", w)
            self.config.set("LAST_SETTINGS_WINDOW_HEIGHT", h)

        for comp in (self.settings_ui, self.sub_overlay_ui, self.sub_manager):
            try:
                comp.save_state()
            except Exception:
                pass

        self.root.destroy()