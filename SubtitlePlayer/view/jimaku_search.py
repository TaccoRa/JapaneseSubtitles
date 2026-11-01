"""Asynchronous Jimaku title/file picker; all Tk work stays on the owner thread."""
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

from model.jimaku import JimakuClient
from utils import get_monitor_rects


def choose_jimaku(parent, config, cache_dir, *, initial_query="", initial_kind="anime", client=None, parser=None,
                  entry_only=False, initial_entry_id=None, initial_anilist_id=None):
    client = client or JimakuClient(cache_dir)
    history_path = Path(config.local_path).resolve().parent / "jimaku_recent_searches.json"
    try:
        history = json.loads(history_path.read_text("utf-8"))
        history = [row for row in history if isinstance(row, dict) and isinstance(row.get("query"), str)
                   and row.get("kind") in ("anime", "drama")][:25]
    except (OSError, ValueError, TypeError):
        history = []
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title("Jimaku subtitles")
    dialog_root = win._root()
    previous_source_window = getattr(dialog_root, "_subtitleplayer_source_window", None)
    dialog_root._subtitleplayer_source_window = win
    def restore_source_window(event):
        if event.widget is win and getattr(dialog_root, "_subtitleplayer_source_window", None) is win:
            dialog_root._subtitleplayer_source_window = previous_source_window
    win.bind("<Destroy>", restore_source_window, add="+")
    win.transient(parent)
    win.geometry("820x510")
    win.minsize(560, 340)
    win.columnconfigure(0, weight=1)
    win.rowconfigure(2, weight=1)
    kind = tk.StringVar(win, initial_kind)
    query = tk.StringVar(win, initial_query)
    file_filter = tk.StringVar(win)
    status = tk.StringVar(win, "Anime oder Drama wählen, Titel eingeben und suchen.")
    result = {"path": None, "entry": None}
    jobs, sequence = queue.Queue(), {"search": 0, "files": 0, "download": 0}
    state = {"entries": [], "files": [], "visible": [], "entry": None, "closed": False,
             "download_busy": False, "source_files": [], "archive": None, "members": [], "search_job": None, "poll_job": None}
    top = ttk.Frame(win, padding=8)
    top.grid(row=0, column=0, sticky="ew")
    top.columnconfigure(2, weight=1)
    for column, (label, value) in enumerate((("Anime", "anime"), ("Drama", "drama"))):
        ttk.Radiobutton(top, text=label, variable=kind, value=value, style="Toolbutton").grid(row=0, column=column, padx=(0, 5))
    search_entry = ttk.Entry(top, textvariable=query)
    search_entry.grid(row=0, column=2, sticky="ew", padx=(5, 5))
    recent = ttk.Combobox(top, state="readonly", width=30)
    ttk.Label(top, text="Recent searches").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
    recent.grid(row=1, column=2, columnspan=2, sticky="ew", pady=(8, 0))
    def update_history():
        recent["values"] = [f"{row['kind'].title()}: {row['query']}" for row in history]
    update_history()
    panes = ttk.Panedwindow(win, orient="horizontal")
    panes.grid(row=2, column=0, sticky="nsew", padx=8)
    left, right = ttk.Frame(panes), ttk.Frame(panes)
    panes.add(left, weight=1)
    panes.add(right, weight=1)
    for frame in (left, right):
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
    ttk.Label(left, text="Title suggestions — select to show files").grid(row=0, column=0, sticky="w", pady=5)
    titles = ttk.Treeview(left, show="tree", selectmode="browse")
    titles.column("#0", width=300, minwidth=120)
    titles.grid(row=1, column=0, sticky="nsew")
    title_scroll = ttk.Scrollbar(left, orient="vertical", command=titles.yview)
    title_scroll.grid(row=1, column=1, sticky="ns")
    titles.configure(yscrollcommand=title_scroll.set)
    file_top = ttk.Frame(right)
    file_top.grid(row=0, column=0, sticky="ew", pady=5)
    file_top.columnconfigure(0, weight=1)
    ttk.Entry(file_top, textvariable=file_filter).grid(row=0, column=0, sticky="ew", padx=(0, 5))
    files = ttk.Treeview(right, show="tree", selectmode="browse")
    files.column("#0", width=360, minwidth=130)
    files.grid(row=1, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(right, orient="vertical", command=files.yview)
    scroll.grid(row=1, column=1, sticky="ns")
    files.configure(yscrollcommand=scroll.set)
    # Horizontal scrolling preserves full provider filenames at compact widths.
    for tree, frame in ((titles, left), (files, right)):
        bar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        bar.grid(row=2, column=0, sticky="ew")
        tree.configure(xscrollcommand=bar.set)
        tree.column("#0", stretch=False)
    ttk.Label(win, textvariable=status, wraplength=760).grid(row=3, column=0, sticky="ew", padx=8, pady=8)
    footer = ttk.Frame(win, padding=(8, 0, 8, 8))
    footer.grid(row=4, column=0, sticky="e")

    def close():
        state["closed"] = True
        for key in ("search_job", "poll_job"):
            if state[key] is not None:
                try:
                    win.after_cancel(state[key])
                except tk.TclError:
                    pass
        win.destroy()

    def launch(channel, work, apply):
        sequence[channel] += 1
        token = sequence[channel]
        def worker():
            try:
                value, error = work(), None
            except Exception as exc:
                value, error = None, str(exc)
            jobs.put((channel, token, apply, value, error))
        threading.Thread(target=worker, name="jimaku-" + channel, daemon=True).start()

    def render_files(*_args):
        files.delete(*files.get_children())
        needle = file_filter.get().strip().casefold()
        state["visible"] = [row for row in state["files"] if needle in str(row.get("name", "")).casefold()]
        for index, row in enumerate(state["visible"]):
            files.insert("", "end", iid=str(index), text=str(row.get("name", "")))
        files.column("#0", width=max(360, max((tkfont.nametofont("TkDefaultFont").measure(str(row.get("name", ""))) + 30
                                              for row in state["visible"]), default=0)))
    file_filter.trace_add("write", render_files)

    def load_files(_event=None, refresh=False):
        selection = titles.selection()
        if not selection:
            return
        entry = state["entries"][int(selection[0])]
        state["entry"], state["files"] = entry, []
        if entry_only:
            status.set("Titel auswählen und mit Use title übernehmen; es wird nichts heruntergeladen.")
            return
        state["archive"] = None
        state["members"] = []
        state["download_busy"] = False
        sequence["download"] += 1
        render_files()
        status.set("Loading subtitle files…")
        def apply(value):
            state["files"] = value
            state["source_files"] = list(value)
            render_files()
            status.set(f"{len(value)} files. Filter filenames, then select a subtitle and click Use.")
        launch("files", lambda: client.files(entry["id"], refresh=refresh), apply)

    def search(_event=None, *, remember=True):
        if state["search_job"] is not None:
            win.after_cancel(state["search_job"])
            state["search_job"] = None
        term, category = query.get().strip(), kind.get()
        if not term:
            status.set("Bitte einen Titel eingeben.")
            return
        sequence["files"] += 1
        sequence["download"] += 1
        titles.delete(*titles.get_children())
        state.update(entries=[], files=[], entry=None)
        state["download_busy"] = False
        render_files()
        status.set("Searching Jimaku…")
        def apply(value):
            state["entries"] = value
            for index, row in enumerate(value):
                label = " / ".join(dict.fromkeys(str(row[key]) for key in ("name", "english_name", "japanese_name") if row.get(key)))
                titles.insert("", "end", iid=str(index), text=label)
            titles.column("#0", width=max(300, max((tkfont.nametofont("TkDefaultFont").measure(titles.item(item, "text")) + 30
                                                    for item in titles.get_children()), default=0)))
            if remember:
                history[:] = [row for row in history if (row["query"].casefold(), row["kind"]) != (term.casefold(), category)]
                history.insert(0, {"query": term, "kind": category})
                del history[25:]
                update_history()
                try:
                    history_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = history_path.with_suffix(".tmp")
                    temporary.write_text(json.dumps(history, ensure_ascii=False), "utf-8")
                    temporary.replace(history_path)
                except OSError:
                    pass
            status.set(f"{len(value)} titles found. Select a title." if value else "No titles found. Try another name.")
        launch("search", lambda: client.search(term, category == "anime"), apply)

    def pick(_event=None):
        if entry_only:
            if state["entry"]:
                result["entry"] = state["entry"]
                close()
            return
        if state["download_busy"]:
            return
        selection, entry = files.selection(), state["entry"]
        if not selection or not entry:
            return
        row = state["visible"][int(selection[0])]
        chosen_entry = dict(entry, _selection={"file": state["archive"] or row,
                                              "files": list(state["source_files"]), "members": list(state["members"]),
                                              "member_names": {path: client.member_names.get(path, Path(path).name) for path in state["members"]}})
        if row.get("local_path"):
            result.update(path=row["local_path"], entry=chosen_entry)
            close()
            return
        status.set("Downloading subtitle…")
        state["download_busy"] = True
        def apply(paths):
            if len(paths) == 1:
                chosen_entry["_selection"]["members"] = paths if str(row.get("name", "")).lower().endswith(".zip") else []
                chosen_entry["_selection"]["member_names"] = {path: client.member_names.get(path, Path(path).name) for path in paths}
                result.update(path=paths[0], entry=chosen_entry)
                close()
            else:
                state["archive"] = row
                state["members"] = paths
                state["files"] = [{"name": Path(path).name, "local_path": path} for path in paths]
                file_filter.set("")
                render_files()
                status.set("ZIP opened. Select a subtitle from the archive, then click Use.")
        launch("download", lambda: client.download(entry["id"], row, title=entry.get("name"), parser=parser), apply)

    def poll():
        if state["closed"]:
            return
        try:
            while True:
                channel, token, apply, value, error = jobs.get_nowait()
                if token != sequence[channel]:
                    continue
                if channel == "download":
                    state["download_busy"] = False
                if error:
                    status.set(error)
                else:
                    apply(value)
                if state["closed"]:
                    return
        except queue.Empty:
            pass
        state["poll_job"] = win.after(70, poll)

    def use_recent(_event=None):
        index = recent.current()
        if 0 <= index < len(history):
            query.set(history[index]["query"])
            kind.set(history[index]["kind"])
            search()
    ttk.Button(top, text="Search", command=search).grid(row=0, column=3)
    ttk.Button(file_top, text="↻", width=3, command=lambda: load_files(refresh=True)).grid(row=0, column=1)
    ttk.Button(footer, text="Use title" if entry_only else "Use subtitle", command=pick).pack(side="left", padx=5)
    def use_backup():
        result["entry"] = {"_github_backup": True}
        close()
    if not entry_only:
        ttk.Button(footer, text="GitHub backup…", command=use_backup).pack(side="left", padx=5)
    ttk.Button(footer, text="Cancel", command=close).pack(side="left")
    recent.bind("<<ComboboxSelected>>", use_recent)
    titles.bind("<<TreeviewSelect>>", load_files)
    files.bind("<Double-Button-1>", pick)
    files.bind("<Return>", pick)
    search_entry.bind("<Return>", search)
    win.bind("<Escape>", lambda _event: close())
    win.protocol("WM_DELETE_WINDOW", close)
    win.update_idletasks()
    anchor = parent.winfo_toplevel() if parent is not None else win.master
    center_x = anchor.winfo_rootx() + anchor.winfo_width() // 2
    center_y = anchor.winfo_rooty() + anchor.winfo_height() // 2
    monitors = get_monitor_rects(anchor)
    monitor = next((rect for rect in monitors if rect[0] <= center_x < rect[0] + rect[2]
                    and rect[1] <= center_y < rect[1] + rect[3]), monitors[0])
    mx, my, mw, mh = monitor
    width, height = min(820, max(300, mw - 40)), min(510, max(240, mh - 80))
    win.minsize(min(560, width), min(340, height))
    win.geometry(f"{width}x{height}{mx + (mw-width)//2:+d}{my + (mh-height)//2:+d}")
    win.deiconify()
    pass  # Keep the subtitle overlay draggable while this dialog is open.
    state["poll_job"] = win.after(70, poll)
    search_entry.focus_set()
    def suggest(*_args):
        for channel in sequence:
            sequence[channel] += 1
        state["download_busy"] = False
        if state["search_job"] is not None:
            win.after_cancel(state["search_job"])
        def run():
            state["search_job"] = None
            if not state["closed"]:
                search(remember=False)
        state["search_job"] = win.after(250, run)
    query.trace_add("write", suggest)
    kind.trace_add("write", suggest)
    if initial_entry_id is not None or initial_anilist_id is not None:
        def apply_initial(rows):
            state["entries"] = rows
            titles.delete(*titles.get_children())
            for i, entry in enumerate(rows):
                titles.insert("", "end", iid=str(i), text=entry.get("name") or entry.get("english_name"))
            if rows:
                titles.selection_set("0")
                load_files()
            else:
                search()
        launch("search", lambda: [entry for entry in client.catalog(initial_kind == "anime") if
               (initial_entry_id is not None and entry["id"] == int(initial_entry_id)) or
               (initial_anilist_id is not None and entry.get("anilist_id") == int(initial_anilist_id))], apply_initial)
    elif initial_query:
        win.after_idle(search)
    win.wait_window()
    return result["path"], result["entry"]
