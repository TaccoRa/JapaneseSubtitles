from PyQt5 import QtCore, QtWidgets, QtGui
from typing import List, Optional

from model.config_manager import ConfigManager
from utils import make_draggable

class SubtitleOverlayUI(QtWidgets.QLabel):

    def __init__(self, parent: QtWidgets.QWidget, config: ConfigManager, cleaned_subs: Optional[List[str]] = None):
        super().__init__(parent)
        self.config = config
        self.cleaned_subs = cleaned_subs or []
        
        font = QtGui.QFont(config.get("SUBTITLE_FONT"), config.get("SUBTITLE_FONT_SIZE"), QtGui.QFont.Weight.Bold)
        metrics = QtGui.QFontMetrics(font)
        self.line_height = metrics.height()
        self.setFont(font)

        # Compute initial max width from subtitles
        self.max_width = self.compute_max_width(metrics)
        init_height = self.line_height * 2

        # Configure window
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.resize(self.max_width, init_height)
        self._center_bottom(init_height)

        # Track drag offset
        self._drag_offset = None
        self.show()

    def _center_bottom(self, height: int):
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        x = (screen.width() - self.max_width) // 2
        y = screen.height() - height - 215
        self.move(x, y)

    def update_pixmap(self, qpix: QtGui.QPixmap):
        """
        Update the displayed subtitle image.
        """
        self.setPixmap(qpix)

    # — Dragging events —
    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_offset = event.pos()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        if self._drag_offset is not None:
            new_pos = event.globalPos() - self._drag_offset
            self.move(new_pos)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_offset = None

    def compute_max_width(self, metrics: QtGui.QFontMetrics) -> int:







    # def build_overlay(self) -> None:
    #     self.max_width = self.compute_max_width(self.cleaned_subs)
    #     init_height = self.line_height * 2
    #     sw = self.root.winfo_screenwidth()
    #     sh = self.root.winfo_screenheight()
    #     pos_x = (sw - self.max_width) // 2
    #     pos_y = sh - init_height - 215

    #     # main subtitle window
    #     self.sub_window = tk.Toplevel(self.root)
    #     # self.sub_window.withdraw() # was too fix white flash in the beginning (just configure grey)
    #     self.sub_window.overrideredirect(True)
    #     self.sub_window.attributes("-topmost", True)
    #     self.sub_window.configure(bg="grey")
    #     self.sub_window.attributes("-transparentcolor", "grey")
    #     self.sub_window.geometry(f"{self.max_width}x{init_height}+{pos_x}+{pos_y}")
    #     self.sub_window.update_idletasks()
    #     self.bottom_anchor = pos_y + init_height

    #     self.border_frame = tk.Frame(self.sub_window, bg="grey")
    #     self.border_frame.pack(fill="both", expand=True)
    #     self.subtitle_canvas = tk.Canvas(
    #         self.border_frame,
    #         bg="grey",
    #         highlightthickness=0
    #     )
    #     self.subtitle_canvas.pack(fill="both", expand=True)
    #     make_draggable(self.sub_window, self.sub_window,on_drag=lambda bottom: setattr(self, "bottom_anchor", bottom))
    #     # self.sub_window.update_idletasks() # was too fix white flash in the beginning (just configure grey)
    #     # self.sub_window.deiconify()

    # def show_handle(self):
    #     if self.subtitle_handle is not None:
    #         return  # Already exists
    #     drag_w, drag_h = 60, self.line_height * 2
    #     sw = self.root.winfo_screenwidth()
    #     sh = self.root.winfo_screenheight()
    #     pos_x = (sw - self.max_width) // 2
    #     pos_y = sh - drag_h - 215
    #     self.subtitle_handle = tk.Toplevel(self.root)
    #     self.subtitle_handle.overrideredirect(True)
    #     self.subtitle_handle.attributes("-topmost", True)
    #     self.subtitle_handle.attributes("-alpha", 0.05)
    #     self.subtitle_handle.geometry(f"{drag_w}x{drag_h}+{pos_x}+{pos_y}")
    #     make_draggable(self.subtitle_handle, self.sub_window, sync_windows=[self.subtitle_handle],on_drag=lambda bottom: setattr(self, "bottom_anchor", bottom))
    #     make_draggable(self.sub_window, self.sub_window, sync_windows=[self.subtitle_handle],on_drag=lambda bottom: setattr(self, "bottom_anchor", bottom))

    # def hide_handle(self):
        if self.subtitle_handle is not None:
            self.subtitle_handle.destroy()
            self.subtitle_handle = None

        # Measure every line in cleaned_subs
        widths = []
        for text in self.cleaned_subs:
            for line in text.splitlines():
                widths.append(metrics.horizontalAdvance(line))
        return max(widths) if widths else ValueError("No subtitles available to compute max width.")