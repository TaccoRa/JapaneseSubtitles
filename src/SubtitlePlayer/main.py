#Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Restricted
# keep this for later information: to download subtitles:
# https://kitsunekko.net/dirlist.php?dir=subtitles/japanese/One_Piece/&sort=date&order=asc

from app import SubtitlePlayerApp

if __name__ == "__main__":
    app = SubtitlePlayerApp()
    app.run()


# To achieve both goals:

# Allow users to enter 4,7 and have it corrected to 4.7
# If the entry is empty or invalid on focus out/return, restore the last used value (not the default)
# You need to:

# Update your parse_time_value function to replace commas with dots.
# Track the last valid value for each entry and restore it if the user input is invalid or empty.
# 1. Update parse_time_value to accept commas
#     def parse_time_value(text: str, default_skip: float) -> float:
#         text = str(text).strip().replace(",", ".")  # Accept comma as decimal separator
#         if text.endswith("s"):
#             text = text[:-2].strip()
#         if ":" in text:
#             parts = text.split(":")
#             if len(parts) == 2:
#                 try:
#                     minutes = int(parts[0])
#                     seconds = int(parts[1])
#                     return minutes * 60 + seconds
#                 except ValueError:
#                     return default_skip
#             else:
#                 return default_skip
#         else:
#             try:
#                 return float(text)
#             except ValueError:
#                 return default_skip   
# 2. Track and restore the last valid value
# In your ControlUI class, add:
# self._last_offset_value = self.offset_var.get()
# self._last_skip_value = self.skip_var.get()
# Update your clear handlers to store the last value:
# def _on_setting_clear_offset_entry(self, event):
#     self._last_offset_value = self.offset_entry.get()
#     self.offset_entry.delete(0, tk.END)

# def _on_setting_clear_skip_entry(self, event):
#     self._last_skip_value = self.skip_entry.get()
#     self.skip_entry.delete(0, tk.END)
# Update your <FocusOut> and <Return> handlers for both entries:
# def _on_offset_focus_out(self, event):
#     val = self.offset_entry.get()
#     try:
#         parsed = parse_time_value(val, default_skip=None)
#         if val.strip() == "" or parsed is None:
#             self.offset_entry.delete(0, tk.END)
#             self.offset_entry.insert(0, self._last_offset_value)
#         else:
#             self._last_offset_value = f"{parsed:.1f} s"
#             self.offset_entry.delete(0, tk.END)
#             self.offset_entry.insert(0, self._last_offset_value)
#     except Exception:
#         self.offset_entry.delete(0, tk.END)
#         self.offset_entry.insert(0, self._last_offset_value)

# def _on_skip_focus_out(self, event):
#     val = self.skip_entry.get()
#     try:
#         parsed = parse_time_value(val, default_skip=None)
#         if val.strip() == "" or parsed is None:
#             self.skip_entry.delete(0, tk.END)
#             self.skip_entry.insert(0, self._last_skip_value)
#         else:
#             self._last_skip_value = f"{parsed:.1f} s"
#             self.skip_entry.delete(0, tk.END)
#             self.skip_entry.insert(0, self._last_skip_value)
#     except Exception:
#         self.skip_entry.delete(0, tk.END)
#         self.skip_entry.insert(0, self._last_skip_value)
# And bind them:
# self.offset_entry.bind("<FocusOut>", self._on_offset_focus_out)
# self.offset_entry.bind("<Return>", self._on_offset_focus_out)
# self.skip_entry.bind("<FocusOut>", self._on_skip_focus_out)
# self.skip_entry.bind("<Return>", self._on_skip_focus_out)
# Summary:

# Commas are accepted and converted to dots.
# If the entry is empty or invalid, the last valid value is restored (not the default).
# The last valid value is updated on every successful edit.
# Let me know if you want the code for a specific file!