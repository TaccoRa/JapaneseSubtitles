#app.py
import os
import tkinter as tk
import tkinter.font as tkFont
from threading import Thread
import threading
import time
import logging

from view.settings_ui import SettingsUI
from view.subtitle_overlay import SubtitleOverlayUI
from view.popup import CopyPopup
from view.overlays import LoadingOverlay, set_startup_overlay
from model.browser_video_controller import BrowserVideoController
from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer
from controller.controller import SubtitleController

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class SubtitlePlayerApp:
    def __init__(self):
        logger.info("Starting SubtitlePlayerApp")

        self._load_config()
        self._build_app_window()

        # Startup splashscreen (simple + lightweight).
        # Keep it minimal to avoid CPU spikes (no images/gifs).
        self._startup_overlay = LoadingOverlay(self.root, text="Starting SubtitlePlayer...", modal=False)
        set_startup_overlay(self._startup_overlay)

        # Let Tk's event loop run so the progressbar can animate, while we init in a worker thread.
        self._startup_done = threading.Event()
        self._startup_error = None
        self._startup_result = None  # (SubtitleManager, total_duration)
        self._startup_thread = Thread(target=self._startup_worker, daemon=True)
        # Start the worker once the Tk mainloop is running (so the splash can animate).
        url = "https://animekai.to/watch/one-piece-dk6r#ep=430"
        self.browser = BrowserVideoController(url)
        self.browser.start()
        self.root.after(0, self._start_startup_worker)
        self.root.after(50, self._poll_startup_worker)

    def _load_config(self):
        try:
            self.config = ConfigManager("config.json")
        except Exception:
            logger.exception("Failed to load config.json")
            raise SystemExit(1)

    def _load_subtitle_metadata(self):
        self.sub_manager = SubtitleManager(self.config)
        self.total_duration = self.sub_manager.get_total_duration()

    def _startup_worker(self):
        """
        Background startup initialization.

        Important: avoid touching Tk from this thread. It should only do file/network work.
        """
        try:
            sub_manager = SubtitleManager(self.config)
            total_duration = sub_manager.get_total_duration()
            self._startup_result = (sub_manager, total_duration)
        except Exception as e:
            self._startup_error = e
            logger.exception("Startup initialization failed")
        finally:
            self._startup_done.set()

    def _start_startup_worker(self):
        try:
            self._startup_thread.start()
        except RuntimeError:
            # Thread already started; ignore.
            pass

    def _poll_startup_worker(self):
        if not getattr(self, "_startup_done", None) or not self._startup_done.is_set():
            self.root.after(50, self._poll_startup_worker)
            return

        overlay = getattr(self, "_startup_overlay", None)
        if overlay is not None:
            try:
                overlay.close()
            except Exception:
                pass
            self._startup_overlay = None
            set_startup_overlay(None)

        if self._startup_error or not self._startup_result:
            # Don't show warning popups (user preference). Just log and exit cleanly.
            logger.error("Failed to start. See logs above.")
            set_startup_overlay(None)
            try:
                self.root.destroy()
            except Exception:
                pass
            return

        self.sub_manager, self.total_duration = self._startup_result
        self._update_root_title()
        self._build_ui()
        self._build_model()
        self._build_controller()

        self.root.deiconify()
        try:
            self.sub_overlay_ui.show()
        except Exception:
            pass
        self.root.after(self.config.get("UPDATE_INTERVAL_MS"), self.controller.update_loop)

    def _build_app_window(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("SubtitlePlayer")
        self.root.geometry("320x123")
        self.root.minsize(320, 123)
        self._restore_window_position()
        self.root.protocol("WM_DELETE_WINDOW", self._on_root_close)

    def _update_root_title(self):
        s = self.sub_manager.get_current_season()
        e = self.sub_manager.get_current_episode()
        n = self.sub_manager.get_anime_name()

        if n is None:
            n = "SubtitlePlayer"

        if s is None and e is None:
            title = f"{n}"
        elif s is None:
            title = f"E{e} {n}"
        else:
            title = f"S{s}E{e} {n}"
        self.root.title(title)

    def _restore_window_position(self):  # gets last saved position of settings window or centers it on the screen if out of bounds
        x = self.config.get("LAST_SETTINGS_WINDOW_X")
        y = self.config.get("LAST_SETTINGS_WINDOW_Y")
        self.root.update_idletasks()
        w = self.root.winfo_width() or self.root.winfo_reqwidth()
        h = self.root.winfo_height() or self.root.winfo_reqheight()
        sw = self.root.winfo_vrootwidth()
        sh = self.root.winfo_vrootheight()

        if not isinstance(x, int) or not isinstance(y, int):
            x = int((sw - w) / 2)
            y = int((sh - h) / 2)
        else:
            x = max(0, min(x, sw - w))
            y = max(0, min(y, sh - h))
        self.root.geometry(f"+{x}+{y}")

    def _on_root_close(self):
        x, y = self.root.winfo_x(), self.root.winfo_y()
        if (x, y) != (self.config.get("LAST_SETTINGS_WINDOW_X"), self.config.get("LAST_SETTINGS_WINDOW_Y")):
            self.config.set("LAST_SETTINGS_WINDOW_X", x)
            self.config.set("LAST_SETTINGS_WINDOW_Y", y)

        # Persist per-window state.
        try:
            self.settings_ui.save_state()
        except Exception:
            pass
        try:
            self.sub_overlay_ui.save_state()
        except Exception:
            pass
        try:
            self.sub_manager.save_state()
        except Exception:
            pass

        self.root.destroy()

    def _build_ui(self):
        # UI (View)
        self.popup = CopyPopup(root=self.root, config=self.config)

        overlay_geometry = self.sub_manager.get_subtitle_geometry()
        self.sub_overlay_ui = SubtitleOverlayUI(
            root=self.root,
            config=self.config,
            cleaned_subs=[item[0] for item in self.sub_manager.display_data],
            overlay_geometry=overlay_geometry,  # width, height
            start_hidden=True,  # show after startup splash is closed
        )

        self.settings_ui = SettingsUI(
            root=self.root,
            config=self.config,
            total_duration=self.total_duration,
            initial_episode=self.sub_manager.get_current_episode(),
        )

    def _build_model(self):
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

    def run(self):
        self.root.mainloop()
