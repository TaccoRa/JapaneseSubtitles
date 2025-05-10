def format_time(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"

def reformat_time_entry(self, entry: tk.Entry) -> None:
    seconds_val = self.parse_time_value(entry.get())
    minutes, seconds = divmod(int(seconds_val), 60)
    formatted = f"{minutes:02d}:{seconds:02d}"
    entry.delete(0, tk.END)
    entry.insert(0, formatted)

def format_skip_entry(self) -> None:
    self.reformat_time_entry(self.skip_entry)

def parse_time_value(self, text: str) -> float:
    text = text.strip()
    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2:
            try:
                minutes = int(parts[0])
                seconds = int(parts[1])
                return minutes * 60 + seconds
            except ValueError:
                return self.default_skip
        else:
            return self.default_skip
    else:
        if text.isdigit():
            return float(text) if len(text) <= 2 else int(text[:-2]) * 60 + int(text[-2:])
        try:
            return float(text)
        except ValueError:
            return self.default_skip
            
