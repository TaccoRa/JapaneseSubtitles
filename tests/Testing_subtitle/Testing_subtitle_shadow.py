# import tkinter as tk
# from tkinter import colorchooser, font as tkfont
# from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageTk

# class SubtitleApp:
#     def __init__(self, root):
#         # Default parameters
#         self.params = {
#             "TEXT":       "リパパ おはようございます。トビアス様、元気ですか",
#             "FONT_PATH":  "meiryo.ttc",
#             "FONT_SIZE":  72,
#             "GLOW_RADIUS":15,
#             "GLOW_ALPHA": 200,
#             "GLOW_COLOR": (0,0,0),
#             "TEXT_COLOR": (255,255,255),
#             "BG_COLOR":   (255,255,255),
#             "WINDOW_W":   1000,
#             "WINDOW_H":   300,
#             "TEXT_X":     50,
#             "TEXT_Y":     150,
#         }

#         # Main preview window
#         self.preview = tk.Toplevel(root)
#         self.preview.title("Subtitle Preview")
#         self.canvas = tk.Canvas(self.preview,
#                                 width=self.params["WINDOW_W"],
#                                 height=self.params["WINDOW_H"])
#         self.canvas.pack()

#         # Settings window
#         self.settings = tk.Toplevel(root)
#         self.settings.title("Settings")
#         self.build_settings_ui()

#         # Initial render
#         self.render_and_update()

#     def build_settings_ui(self):
#         p = self.params
#         row = 0

#         def add_label(text):
#             nonlocal row
#             tk.Label(self.settings, text=text).grid(row=row, column=0, sticky="w")
        
#         def add_entry(param, width=10):
#             nonlocal row
#             e = tk.Entry(self.settings, width=width)
#             e.insert(0, str(p[param]))
#             e.grid(row=row, column=1)
#             return e

#         # Text string
#         add_label("Text:")
#         self.text_entry = add_entry("TEXT", width=40)
#         row += 1

#         # Font size
#         add_label("Font Size:")
#         self.font_size_entry = add_entry("FONT_SIZE")
#         row += 1

#         # Glow radius
#         add_label("Glow Radius:")
#         self.glow_radius_entry = add_entry("GLOW_RADIUS")
#         row += 1

#         # Glow alpha
#         add_label("Glow Alpha (0–255):")
#         self.glow_alpha_entry = add_entry("GLOW_ALPHA")
#         row += 1

#         # Text position X,Y
#         add_label("Text X:")
#         self.text_x_entry = add_entry("TEXT_X")
#         row += 1
#         add_label("Text Y:")
#         self.text_y_entry = add_entry("TEXT_Y")
#         row += 1

#         # Color pickers
#         def make_color_button(param, label):
#             nonlocal row
#             add_label(f"{label} Color:")
#             btn = tk.Button(self.settings, text="Choose…",
#                             command=lambda p=param: self.choose_color(p))
#             btn.grid(row=row, column=1, sticky="w")
#             row +=1

#         make_color_button("BG_COLOR",   "Background")
#         make_color_button("GLOW_COLOR", "Glow")
#         make_color_button("TEXT_COLOR", "Text")

#         # Font chooser (just shows family list)
#         add_label("Font Family:")
#         fonts = list(tkfont.families())
#         self.font_var = tk.StringVar(value=p["FONT_PATH"])
#         font_menu = tk.OptionMenu(self.settings, self.font_var, *fonts)
#         font_menu.grid(row=row, column=1, sticky="w")
#         row +=1

#         # Apply button
#         apply_btn = tk.Button(self.settings, text="Apply & Refresh",
#                               command=self.on_apply)
#         apply_btn.grid(row=row, column=0, columnspan=2, pady=10)

#     def choose_color(self, param):
#         # Open color chooser, pack into RGB tuple
#         c = colorchooser.askcolor(color=self.params[param])
#         if c and c[0]:
#             self.params[param] = tuple(int(v) for v in c[0])

#     def on_apply(self):
#         p = self.params
#         try:
#             p["TEXT"]       = self.text_entry.get()
#             p["FONT_SIZE"]  = int(self.font_size_entry.get())
#             p["GLOW_RADIUS"]= int(self.glow_radius_entry.get())
#             p["GLOW_ALPHA"] = int(self.glow_alpha_entry.get())
#             p["TEXT_X"]     = int(self.text_x_entry.get())
#             p["TEXT_Y"]     = int(self.text_y_entry.get())
#             p["FONT_PATH"]  = self.font_var.get()
#         except ValueError:
#             # ignore invalid entries
#             pass
#         self.render_and_update()

#     def render_and_update(self):
#         p = self.params

#         # 1) Render text mask
#         font = ImageFont.truetype(p["FONT_PATH"], p["FONT_SIZE"])
#         mask = Image.new("L", (p["WINDOW_W"], p["WINDOW_H"]), 0)
#         draw = ImageDraw.Draw(mask)
#         draw.text((p["TEXT_X"], p["TEXT_Y"]), p["TEXT"], font=font, fill=255)

#         # 2) Blur for glow
#         glow_mask = mask.filter(ImageFilter.GaussianBlur(radius=p["GLOW_RADIUS"]))

#         # 3) Create glow layer
#         glow = Image.new("RGBA", (p["WINDOW_W"], p["WINDOW_H"]), p["GLOW_COLOR"]+(0,))
#         glow_mask_scaled = glow_mask.point(lambda v: v * (p["GLOW_ALPHA"]/255.0))
#         glow.putalpha(glow_mask_scaled)

#         # 4) Create text layer
#         text_layer = Image.new("RGBA", (p["WINDOW_W"], p["WINDOW_H"]), p["TEXT_COLOR"]+(0,))
#         text_layer.putalpha(mask)

#         # 5) Composite
#         frame = Image.new("RGBA", (p["WINDOW_W"], p["WINDOW_H"]), p["BG_COLOR"]+(255,))
#         frame.alpha_composite(glow)
#         frame.alpha_composite(text_layer)

#         # Update Tkinter canvas
#         self.photo = ImageTk.PhotoImage(frame)
#         self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
#         self.canvas.image = self.photo  # prevent GC

# if __name__ == "__main__":
#     root = tk.Tk()
#     root.withdraw()  # hide the “root” window
#     app = SubtitleApp(root)
#     root.mainloop()


# import tkinter as tk
# from tkinter import colorchooser, font as tkfont
# from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageTk

# class SubtitleApp:
#     def __init__(self, root):
#         # Default parameters
#         self.params = {
#             "TEXT":       "リパパ おはようございます。トビアス様、元気ですか",
#             "FONT_PATH":  "meiryo.ttc",
#             "FONT_SIZE":  72,
#             "GLOW_RADIUS":15,
#             "GLOW_ALPHA": 200,
#             "GLOW_COLOR": (0,0,0),
#             "TEXT_COLOR": (255,255,255),
#             "BG_COLOR":   (255,255,255),
#             "WINDOW_W":   1000,
#             "WINDOW_H":   400,  # increased to fit two lines
#             "TEXT_X":     50,
#             "TEXT_Y1":    100,  # glow line y
#             "TEXT_Y2":    300,  # plain line y
#         }

#         # Preview window
#         self.preview = tk.Toplevel(root)
#         self.preview.title("Subtitle Comparison")
#         self.canvas = tk.Canvas(self.preview,
#                                 width=self.params["WINDOW_W"],
#                                 height=self.params["WINDOW_H"])
#         self.canvas.pack()

#         # Settings window
#         self.settings = tk.Toplevel(root)
#         self.settings.title("Settings")
#         self.build_settings_ui()

#         # Initial render
#         self.render_and_update()

#     def build_settings_ui(self):
#         p = self.params
#         row = 0

#         def add_label(text):
#             nonlocal row
#             tk.Label(self.settings, text=text).grid(row=row, column=0, sticky="w")
#         def add_entry(param, width=10):
#             nonlocal row
#             e = tk.Entry(self.settings, width=width)
#             e.insert(0, str(p[param]))
#             e.grid(row=row, column=1)
#             return e

#         # Text
#         add_label("Text:")
#         self.text_entry = add_entry("TEXT", width=40)
#         row += 1
#         # Font size
#         add_label("Font Size:")
#         self.font_size_entry = add_entry("FONT_SIZE")
#         row += 1
#         # Glow radius
#         add_label("Glow Radius:")
#         self.glow_radius_entry = add_entry("GLOW_RADIUS")
#         row += 1
#         # Glow alpha
#         add_label("Glow Alpha (0–255):")
#         self.glow_alpha_entry = add_entry("GLOW_ALPHA")
#         row += 1
#         # Text positions
#         add_label("Text Y (glow line):")
#         self.text_y1_entry = add_entry("TEXT_Y1")
#         row += 1
#         add_label("Text Y (plain line):")
#         self.text_y2_entry = add_entry("TEXT_Y2")
#         row += 1

#         # Color pickers
#         def make_color_button(param, label):
#             nonlocal row
#             add_label(f"{label} Color:")
#             btn = tk.Button(self.settings, text="Choose…",
#                             command=lambda p=param: self.choose_color(p))
#             btn.grid(row=row, column=1, sticky="w")
#             row +=1

#         make_color_button("BG_COLOR",   "Background")
#         make_color_button("GLOW_COLOR", "Glow")
#         make_color_button("TEXT_COLOR", "Text")

#         # Apply button
#         apply_btn = tk.Button(self.settings, text="Apply & Refresh",
#                               command=self.on_apply)
#         apply_btn.grid(row=row, column=0, columnspan=2, pady=10)

#     def choose_color(self, param):
#         c = colorchooser.askcolor(color=self.params[param])
#         if c and c[0]:
#             self.params[param] = tuple(int(v) for v in c[0])

#     def on_apply(self):
#         p = self.params
#         try:
#             p["TEXT"]       = self.text_entry.get()
#             p["FONT_SIZE"]  = int(self.font_size_entry.get())
#             p["GLOW_RADIUS"]= int(self.glow_radius_entry.get())
#             p["GLOW_ALPHA"] = int(self.glow_alpha_entry.get())
#             p["TEXT_Y1"]    = int(self.text_y1_entry.get())
#             p["TEXT_Y2"]    = int(self.text_y2_entry.get())
#         except ValueError:
#             pass
#         self.render_and_update()

#     def render_and_update(self):
#         p = self.params
#         # 1) Text mask for both lines
#         font = ImageFont.truetype(p["FONT_PATH"], p["FONT_SIZE"])
#         mask1 = Image.new("L", (p["WINDOW_W"], p["WINDOW_H"]), 0)
#         draw1 = ImageDraw.Draw(mask1)
#         draw1.text((p["TEXT_X"], p["TEXT_Y1"]), p["TEXT"], font=font, fill=255, anchor="lm")
#         mask2 = Image.new("L", (p["WINDOW_W"], p["WINDOW_H"]), 0)
#         draw2 = ImageDraw.Draw(mask2)
#         draw2.text((p["TEXT_X"], p["TEXT_Y2"]), p["TEXT"], font=font, fill=255, anchor="lm")

#         # 2) Glow for line1
#         glow_mask = mask1.filter(ImageFilter.GaussianBlur(radius=p["GLOW_RADIUS"]))
#         glow = Image.new("RGBA", (p["WINDOW_W"], p["WINDOW_H"]), p["GLOW_COLOR"]+(0,))
#         glow_mask_scaled = glow_mask.point(lambda v: v * (p["GLOW_ALPHA"]/255.0))
#         glow.putalpha(glow_mask_scaled)

#         # 3) Text layers
#         text_layer1 = Image.new("RGBA", (p["WINDOW_W"], p["WINDOW_H"]), p["TEXT_COLOR"]+(0,))
#         text_layer1.putalpha(mask1)
#         text_layer2 = Image.new("RGBA", (p["WINDOW_W"], p["WINDOW_H"]), p["TEXT_COLOR"]+(0,))
#         text_layer2.putalpha(mask2)

#         # 4) Composite
#         frame = Image.new("RGBA", (p["WINDOW_W"], p["WINDOW_H"]), p["BG_COLOR"]+(255,))
#         frame.alpha_composite(glow)
#         frame.alpha_composite(text_layer1)
#         frame.alpha_composite(text_layer2)

#         # 5) Update canvas
#         self.photo = ImageTk.PhotoImage(frame)
#         self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
#         self.canvas.image = self.photo

# if __name__ == "__main__":
#     root = tk.Tk()
#     root.withdraw()
#     app = SubtitleApp(root)
#     root.mainloop()

import sys
import ctypes
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# — your parameters —
TEXT        = "リパパ おはようございます。トビアス様、元気ですか"
FONT_PATH   = "meiryo.ttc"
FONT_SIZE   = 72
GLOW_RADIUS = 15
GLOW_ALPHA  = 200
GLOW_COLOR  = (0, 0, 0)
TEXT_COLOR  = (255, 255, 255)
W, H        = 1000, 300
POS         = (50, H//2)

# — build your RGBA glow+text image exactly as before —
font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
mask = Image.new("L", (W, H), 0)
draw = ImageDraw.Draw(mask)
draw.text(POS, TEXT, font=font, fill=255, anchor="lm")
glow_mask = mask.filter(ImageFilter.GaussianBlur(GLOW_RADIUS))
glow_mask = glow_mask.point(lambda v: v * (GLOW_ALPHA/255.0))

glow = Image.new("RGBA", (W, H), GLOW_COLOR + (0,))
glow.putalpha(glow_mask)
text = Image.new("RGBA", (W, H), TEXT_COLOR + (0,))
text.putalpha(mask)

def make_frame():
    frame = Image.new("RGBA", (W, H), (0,0,0,0))
    frame.alpha_composite(glow)
    frame.alpha_composite(text)
    return frame

# — Win32 layered-window boilerplate —
if sys.platform != "win32":
    raise RuntimeError("This demo is Win32-only")

# Win32 constants
GWL_EXSTYLE   = -20
WS_EX_LAYERED = 0x00080000
ULW_ALPHA     = 0x00000002

# Set up Tk
root = tk.Tk()
root.overrideredirect(True)
root.attributes("-topmost", True)
root.geometry(f"{W}x{H}+100+100")  # position as you like

# Fetch HWND
hwnd = ctypes.windll.user32.GetParent(root.winfo_id())

# Turn on layered style
style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)

# Prepare the bitmap
frame = make_frame()
b_data = frame.tobytes("raw", "BGRA")

# Create a DIB section
hdc_screen = ctypes.windll.user32.GetDC(0)
hdc_mem    = ctypes.windll.gdi32.CreateCompatibleDC(hdc_screen)

class BITMAPINFO(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32),
                ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16),
                ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]
bmp_info = BITMAPINFO()
bmp_info.biSize      = ctypes.sizeof(BITMAPINFO)
bmp_info.biWidth     = W
bmp_info.biHeight    = -H   # negative to indicate top-down
bmp_info.biPlanes    = 1
bmp_info.biBitCount  = 32
bmp_info.biCompression = 0  # BI_RGB

bits_ptr = ctypes.c_void_p()
hbitmap = ctypes.windll.gdi32.CreateDIBSection(
    hdc_screen,
    ctypes.byref(bmp_info),
    0,                  # DIB_RGB_COLORS
    ctypes.byref(bits_ptr),
    None,
    0
)
# Copy pixel data into DIB
ctypes.memmove(bits_ptr, b_data, len(b_data))
ctypes.windll.gdi32.SelectObject(hdc_mem, hbitmap)

# Set up BLENDFUNCTION
class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte),
                ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte),
                ("AlphaFormat", ctypes.c_byte)]
bf = BLENDFUNCTION()
bf.BlendOp            = 0x00  # AC_SRC_OVER
bf.BlendFlags         = 0
bf.SourceConstantAlpha= 255    # use per-pixel alpha
bf.AlphaFormat        = 0x01  # AC_SRC_ALPHA

# Finally, push it to the window
ctypes.windll.user32.UpdateLayeredWindow(
    hwnd,
    hdc_screen,
    None,                                   # no move
    ctypes.byref(ctypes.wintypes.POINT(0,0)),
    hdc_mem,
    ctypes.byref(ctypes.wintypes.POINT(0,0)),
    0,
    ctypes.byref(bf),
    ULW_ALPHA
)

# Clean up DCs
ctypes.windll.user32.ReleaseDC(hwnd, hdc_screen)
ctypes.windll.gdi32.DeleteDC(hdc_mem)

# You can still use Tk’s event loop to keep the window alive:
root.mainloop()
