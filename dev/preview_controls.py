"""Isolated settings preview/benchmark; never loads personal runtime config."""
import argparse
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'SubtitlePlayer'))
from model.config_manager import ConfigManager
from view.settings_ui import SettingsUI
from utils import TkMainThreadDispatcher


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    folder = Path(tempfile.mkdtemp(prefix='controls-preview-', dir=ROOT / 'dev'))
    (folder / 'config.json').write_text((ROOT / 'config.json').read_text('utf-8-sig'), encoding='utf-8')
    os.chdir(folder)
    root = tk.Tk()
    root.title('Controls QA host - isolated')
    dispatcher = TkMainThreadDispatcher(root)
    ui = SettingsUI(root, ConfigManager('config.json'), 100, start_hidden=True)
    errors = []
    root.report_callback_exception = lambda *values: errors.append(str(values[1]))
    root.update_idletasks()
    start = time.perf_counter()
    ui.adv_settings._open_advanced_settings_window()
    root.update_idletasks()
    timings = {'first_open_ms': round((time.perf_counter() - start) * 1000, 1)}
    for tab in ui._advanced_notebook.tabs():
        start = time.perf_counter()
        ui._advanced_notebook.select(tab)
        ui.adv_settings._on_advanced_tab_changed()
        root.update_idletasks()
        timings[ui._advanced_notebook.tab(tab, 'text')] = round((time.perf_counter() - start) * 1000, 1)
    ui.advanced_window.withdraw()
    ui._advanced_closed = False
    start = time.perf_counter()
    ui.adv_settings._open_advanced_settings_window()
    root.update_idletasks()
    timings['reopen_ms'] = round((time.perf_counter() - start) * 1000, 1)
    timings['errors'] = errors
    output = ROOT / 'dev/reviews/unified-controls'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'settings-timings.json').write_text(json.dumps(timings, indent=2), encoding='utf-8')
    print(json.dumps(timings), flush=True)
    ui.advanced_window.title('Controls QA - isolated settings')
    if args.keep:
        ui.advanced_window.attributes('-topmost', True)
    if args.keep:
        root.after(180000, root.destroy)
        root.mainloop()
    else:
        root.destroy()


if __name__ == '__main__':
    main()
