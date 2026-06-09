"""Mouse helper for global click handling and synthetic video clicks."""

import pyautogui
from pynput.mouse import Button
from typing import Any

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

class MouseController(_ControllerProxy):
    """Mouse helper for global click handling and synthetic video clicks."""

    def __init__(self, controller: Any) -> None:
        super().__init__(controller)

def simulate_video_click(self, above_window=None):
        if not self.video_click: return
        def _rect(win):
            if win is None:
                return None
            try:
                win.update_idletasks()
            except Exception:
                pass
            try:
                x = int(win.winfo_rootx())
                y = int(win.winfo_rooty())
                w = int(win.winfo_width()) or int(win.winfo_reqwidth())
                h = int(win.winfo_height()) or int(win.winfo_reqheight())
                return (x, y, x + w, y + h)
            except Exception:
                return None

        def _contains(r, x, y):
            if not r:
                return False
            l, t, rr, bb = r
            return l <= x <= rr and t <= y <= bb

        original_pos = pyautogui.position()
        try:
            screen_w, screen_h = pyautogui.size()
        except Exception:
            screen_w, screen_h = (1920, 1080)

        # Build a list of windows we want to click *outside* of so the click can land on the video.
        block_wins = [
            above_window,
            getattr(self.popup, "_popup", None),
            getattr(self.overlay, "sub_window", None),
            getattr(self.settings, "control_window", None),
            getattr(self.settings, "root", None),
        ]
        block_rects = [r for r in (_rect(w) for w in block_wins) if r]

        def _clamp(x, y):
            # Avoid corners (pyautogui FAILSAFE triggers on (0,0)).
            x = max(5, min(int(x), int(screen_w) - 5))
            y = max(5, min(int(y), int(screen_h) - 5))
            return x, y

        def _blocked(x, y):
            return any(_contains(r, x, y) for r in block_rects)

        candidates = []
        # Preferred: click above the *current mouse position*.
        # This tends to land on the video area even if the cursor is near our UI.
        try:
            mx = int(original_pos.x)
            my = int(original_pos.y) - 80
            # If a popup window is provided, ensure we click above it (not inside it).
            if above_window is not None:
                try:
                    above_window.update_idletasks()
                    popup_top = int(above_window.winfo_rooty())
                    my = min(my, popup_top - 80)
                except Exception:
                    pass
            candidates.append((mx, my))
        except Exception:
            pass

        r_popup = _rect(above_window) if above_window is not None else None
        if r_popup:
            l, t, rr, bb = r_popup
            cx = int((l + rr) / 2)
            cy = int((t + bb) / 2)
            candidates.extend([
                (cx, t - 80),        # above popup (preferred)
                (cx, bb + 80),       # below popup
                (l - 80, cy),        # left of popup
                (rr + 80, cy),       # right of popup
                (cx, t - 160),       # further above
            ])

        # Fallback: click above the control window.
        r_ctrl = _rect(getattr(self.settings, "control_window", None))
        if r_ctrl:
            l, t, rr, bb = r_ctrl
            candidates.append((int(l + 50), int(t - 80)))

        # Final fallback: a safe spot near the top-middle of the primary screen.
        candidates.append((int(screen_w / 2), 80))

        target_x, target_y = None, None
        for (cx, cy) in candidates:
            x, y = _clamp(cx, cy)
            # If this point is still blocked by one of our windows, walk upwards a bit.
            for _ in range(10):
                if not _blocked(x, y):
                    break
                x, y = _clamp(x, y - 40)
            if not _blocked(x, y):
                target_x, target_y = x, y
                break

        if target_x is None or target_y is None:
            # Worst-case: just use the first candidate.
            target_x, target_y = _clamp(*candidates[0])

        try:
            pyautogui.moveTo(target_x, target_y)
            pyautogui.click(target_x, target_y)
        except Exception:
            # Don't crash the app (and don't show a warning popup), but do log for debugging.
            print("simulate_video_click failed")
        finally:
            try:
                # Bring our UI back in front.
                self.settings.control_window.attributes("-topmost", True)
                self.settings.control_window.lift()
            except Exception:
                pass
            try:
                # Keep the popup above our other topmost windows.
                self.popup.ensure_on_top()
            except Exception:
                pass
            try:
                pyautogui.moveTo(original_pos.x, original_pos.y)
            except Exception:
                pass

def _on_global_click(self, x, y, button, pressed):
        if button == Button.x2 and pressed:
            self._enqueue_input_action("clear_subtitle")
        if button == Button.x1 and pressed:
            self._enqueue_input_action("toggle_m3_mode")
