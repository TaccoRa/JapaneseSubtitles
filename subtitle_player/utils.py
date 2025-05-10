import tkinter as tk
from tkinter import font as tkFont

def parse_time_value(text: str, default_skip: float) -> float:
    text = text.strip()
    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2:
            try:
                minutes = int(parts[0])
                seconds = int(parts[1])
                return minutes * 60 + seconds
            except ValueError:
                return default_skip
        else:
            return default_skip
    else:
        if text.isdigit():
            return float(text) if len(text) <= 2 else int(text[:-2]) * 60 + int(text[-2:])
        try:
            return float(text)
        except ValueError:
            return default_skip
            
def reformat_time_entry(entry: tk.Entry, parse_func) -> None:
    text = entry.get()
    secs = parse_func(text)
    minutes, seconds = divmod(int(secs), 60)
    formatted = f"{minutes:02d}:{seconds:02d}"
    entry.delete(0, tk.END)
    entry.insert(0, formatted)

def format_skip_entry(skip_entry: tk.Entry, parse_func) -> None:
    reformat_time_entry(skip_entry, parse_func)
