"""Closing settings must not manufacture variable writes and layout work."""
import tkinter as tk
from types import SimpleNamespace as NS

from SubtitlePlayer.view.settings_advanced_ui import SettingsAdvancedUI


def test_collect_unchanged_values_does_not_trigger_tk_write_traces():
    interp = tk.Tcl()
    writes = []
    variables = {key: tk.StringVar(interp, value=value) for key, value in
                 [('TEXT', 'hello'), ('INT', '42'), ('FLOAT', '1.5')]}
    for key, var in variables.items():
        var.trace_add('write', lambda *args, key=key: writes.append(key))
    ui = SettingsAdvancedUI(NS(_advanced_vars=variables, _advanced_meta={
        key: dict(type=kind, label=key) for key, kind in
        [('TEXT', 'str'), ('INT', 'int'), ('FLOAT', 'float')]},
        _format_number=lambda n: format(n, 'g')))
    assert ui._collect_advanced_values() == {'values': {'TEXT': 'hello', 'INT': 42, 'FLOAT': 1.5}}
    assert writes == []
    variables['INT'].set('999')
    ui._advanced_meta['INT']['max'] = 100
    writes.clear()
    assert ui._collect_advanced_values()['values']['INT'] == 100
    assert variables['INT'].get() == '100'
    assert writes == ['INT']


def test_save_window_position_does_not_flush_unrelated_idle_callbacks():
    saved = []
    def unexpected_flush():
        raise AssertionError('Saving position must not run layout callbacks')
    ui = SettingsAdvancedUI(NS(config=NS(get=lambda key: None, set_many=saved.append)))
    window = NS(winfo_exists=lambda: True, winfo_x=lambda: 125, winfo_y=lambda: 250,
                update_idletasks=unexpected_flush)
    ui._save_advanced_window_position(window)
    assert saved == [{'LAST_ADV_SETTINGS_WINDOW_X': 125, 'LAST_ADV_SETTINGS_WINDOW_Y': 250}]
