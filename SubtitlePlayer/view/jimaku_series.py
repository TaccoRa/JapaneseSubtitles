"""One-time, editable TV-series confirmation. Network work never runs on Tk."""
from copy import deepcopy
import queue
import threading
import tkinter as tk
from tkinter import ttk

from utils import get_monitor_rects


def center_dialog(win, parent):
    win.update_idletasks()
    anchor = parent.winfo_toplevel()
    x, y = anchor.winfo_rootx()+anchor.winfo_width()//2, anchor.winfo_rooty()+anchor.winfo_height()//2
    rects = get_monitor_rects(anchor)
    left, top, width, height = next((r for r in rects if r[0] <= x < r[0]+r[2] and r[1] <= y < r[1]+r[3]), rects[0])
    w, h = min(win.winfo_reqwidth(), width-40), min(win.winfo_reqheight(), height-80)
    win.geometry(f"{w}x{h}{left+(width-w)//2:+d}{top+(height-h)//2:+d}")


def choose_series_map(parent, library, descriptor, parser, *, proposal=None, refresh=False, valid=None, seed=None):
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title("Jimaku · Anime-Map bestätigen")
    dialog_root = win._root()
    previous_source_window = getattr(dialog_root, "_subtitleplayer_source_window", None)
    dialog_root._subtitleplayer_source_window = win
    def restore_source_window(event):
        if event.widget is win and getattr(dialog_root, "_subtitleplayer_source_window", None) is win:
            dialog_root._subtitleplayer_source_window = previous_source_window
    win.bind("<Destroy>", restore_source_window, add="+")
    win.transient(parent.winfo_toplevel())
    win.columnconfigure(0, weight=1)
    win.rowconfigure(1, weight=1)
    status = tk.StringVar(win, "Staffeln und verfügbare Dateien werden im Hintergrund ermittelt…")
    title = tk.StringVar(win, descriptor.get("title", "Anime"))
    ttk.Entry(win, textvariable=title).grid(row=0, column=0, sticky="ew", padx=10, pady=8)
    tree = ttk.Treeview(win, columns=("season", "title", "source", "range", "end"), show="tree headings", height=11)
    for key, label, width in (("season", "App-Staffel", 80), ("title", "TV-Abschnitt", 280),
                              ("source", "Jimaku / Quellstaffel", 145), ("range", "Quellfolgen", 95), ("end", "Staffelende", 110)):
        tree.heading(key, text=label)
        tree.column(key, width=width, stretch=key == "title")
    tree.heading("#0", text="Staffel / Folge / Datei")
    tree.column("#0", width=235, minwidth=120)
    tree.grid(row=1, column=0, sticky="nsew", padx=(10, 0))
    scrollbar = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
    scrollbar.grid(row=1, column=1, sticky="ns", padx=(0, 10))
    tree.configure(yscrollcommand=scrollbar.set)
    edit = ttk.Frame(win, padding=10)
    edit.grid(row=2, column=0, sticky="ew")
    fields = {key: tk.StringVar(win) for key in ("season", "first", "last", "global_first", "source_season")}
    known = tk.BooleanVar(win)
    for column, (key, label) in enumerate((("season", "App-Staffel"), ("first", "Erste Quellfolge"), ("last", "Letzte Quellfolge"), ("global_first", "Erste Gesamtfolge"), ("source_season", "Quellstaffel"))):
        row, col = (0, column*2) if column < 3 else (1, (column-3)*2)
        ttk.Label(edit, text=label).grid(row=row, column=col, padx=(0, 3), pady=3)
        ttk.Entry(edit, textvariable=fields[key], width=6).grid(row=row, column=col+1, padx=(0, 10))
    ttk.Checkbutton(edit, text="Staffelende bestätigt", variable=known).grid(row=2, column=0, columnspan=4, sticky="w", pady=6)
    ttk.Label(win, textvariable=status, wraplength=760, justify="left").grid(row=3, column=0, sticky="ew", padx=10, pady=6)
    footer = ttk.Frame(win, padding=10)
    footer.grid(row=4, column=0, sticky="ew")
    jobs = queue.Queue()
    state = {"proposal": None, "closed": False, "busy": False, "selected": None, "result": None, "poll": None}

    def launch(work):
        state["busy"] = True
        def worker():
            try:
                value, error = work(), None
            except Exception as exc:
                value, error = None, str(exc)
            jobs.put((value, error))
        threading.Thread(target=worker, name="jimaku-series-proposal", daemon=True).start()

    def commit():
        index = state["selected"]
        if index is None or state["proposal"] is None:
            return True
        try:
            values = {key: int(var.get()) for key, var in fields.items() if var.get().strip()}
            if any(key not in values for key in ("season", "first", "last")):
                raise ValueError()
            if values.get("source_season", 1) < 1:
                raise ValueError()
            if "source_season" not in values:
                values["source_season"] = None
            if values.get("global_first", 1) < 1:
                raise ValueError()
            if "global_first" not in values:
                state["proposal"]["parts"][index].pop("global_first", None)
            if (values["season"] < 1 or values["first"] < 1 or values["last"] < values["first"]
                    or values["last"]-values["first"] >= 10000):
                raise ValueError()
            part = state["proposal"]["parts"][index]
            changed = any(part.get(k) != v for k, v in values.items()) or part["end_confirmed"] != known.get()
            part.update(values, end_confirmed=known.get())
            if changed and tree.exists(str(index)):
                tree.delete(*tree.get_children(str(index)))
                tree.insert(str(index), "end", iid=f"{index}:pending", text="Folgen anzeigen…")
                if tree.item(str(index), "open"):
                    populate(str(index))
            if changed and tree.exists(str(index)):
                part = state["proposal"]["parts"][index]
                tree.item(str(index), text=f"Staffel {part['season']}", values=(part["season"], part["title"],
                    f"{part['entry_id']} / {part['source_season'] or '—'}", f"{part['first']}–{part['last']}",
                    "bestätigt" if part["end_confirmed"] else "unbekannt"))
            return True
        except ValueError:
            status.set("Bitte positive Staffel-/Folgenzahlen und einen gültigen Folgenbereich eingeben.")
            return False

    file_nodes = {}
    branch_generation = {}
    population_generation = [0]

    def populate(item):
        if not item or not tree.exists(item):
            return
        pending = f"{item}:pending"
        if not tree.exists(pending):
            return
        tree.delete(pending)
        generation = population_generation[0]
        branch_generation[item] = branch_generation.get(item, 0)+1
        branch = branch_generation[item]
        if item in file_nodes:
            seen = set()
            values = []
            for file in file_nodes[item]:
                name = file["name"]
                if name in seen:
                    continue
                seen.add(name)
                values.append((name, ("", name, "", "", "im Cache" if file.get("path") else "verfügbar"), None))
        elif ":" not in item:
            part = state["proposal"]["parts"][int(item)]
            # Index once rather than scanning every file for every episode.
            indexed = {}
            for file in part["files"]:
                indexed.setdefault(file.get("episode"), []).append(file)
            start = part.get("global_first")
            if start is None:
                start = 1
                for previous in state["proposal"]["parts"][:int(item)]:
                    base = previous.get("global_first", start)
                    start = (base+previous["last"]-previous["first"]+1
                             if base is not None and previous.get("end_confirmed") else None)
            values = []
            for episode in range(part["first"], part["last"]+1):
                local = episode-part["first"]+1
                label = f"S{part['season']:02}E{local:02}"
                if start is not None:
                    label += f" · Gesamtfolge {start+local-1}"
                files = indexed.get(episode, [])
                values.append((label, ("", "", "", f"Quellfolge {episode}",
                    f"{len(files)} Dateien" if files else "Archiv ungeprüft" if part.get("archive_pending") else "Datei fehlt"), files))
        else:
            return
        iterator = iter(values)

        def batch():
            if (state["closed"] or generation != population_generation[0]
                    or branch != branch_generation.get(item) or not tree.exists(item)):
                return
            for _ in range(30):
                try:
                    label, columns, files = next(iterator)
                except StopIteration:
                    return
                child = tree.insert(item, "end", text=label, values=columns)
                if files:
                    file_nodes[child] = files
                    tree.insert(child, "end", iid=f"{child}:pending", text="Dateien anzeigen…")
            win.after(1, batch)
        batch()

    def render(selected=None):
        population_generation[0] += 1
        file_nodes.clear()
        tree.delete(*tree.get_children())
        for index, part in enumerate(state["proposal"]["parts"]):
            tree.insert("", "end", iid=str(index), text=f"Staffel {part['season']}", values=(part["season"], part["title"],
                f"{part['entry_id']} / {part['source_season'] or '—'}", f"{part['first']}–{part['last']}",
                "bestätigt" if part["end_confirmed"] else "unbekannt"))
            tree.insert(str(index), "end", iid=f"{index}:pending", text="Folgen anzeigen…")
        state["selected"] = None
        if selected is not None and tree.exists(str(selected)):
            tree.selection_set(str(selected))

    def select(_event=None):
        chosen = tree.selection()
        if not chosen:
            return
        if not commit():
            return
        owner = chosen[0]
        while tree.parent(owner):
            owner = tree.parent(owner)
        if not owner.isdigit():
            return
        index = int(owner)
        if state["selected"] == index:
            return
        state["selected"] = index
        part = state["proposal"]["parts"][index]
        for key, var in fields.items():
            value = part.get(key, "")
            if value is None:
                value = ""
            if key == "global_first" and value == "":
                # Show the proposed correspondence explicitly before approval.
                value = 1
                for previous in state["proposal"]["parts"][:index]:
                    base = previous.get("global_first", value)
                    value = (base+previous["last"]-previous["first"]+1
                             if base is not None and previous.get("end_confirmed") else None)
                if not state["proposal"].get("numbering_confirmed") and part["first"] > 1:
                    value = part["first"]
                value = "" if value is None else value
            var.set(str(value))
        known.set(part["end_confirmed"])

    def change(direction):
        if state["busy"] or not commit() or state["selected"] is None:
            return
        index = state["selected"]
        parts = state["proposal"]["parts"]
        if direction == 0:
            state["proposal"].setdefault("excluded_parts", []).append(parts[index]["id"])
            parts.pop(index)
            render(min(index, len(parts)-1))
        elif 0 <= index+direction < len(parts):
            parts[index], parts[index+direction] = parts[index+direction], parts[index]
            render(index+direction)

    def add(replace=False):
        if state["busy"] or state["proposal"] is None or not commit():
            return
        from view.jimaku_search import choose_jimaku
        replacing = state["selected"] if replace else None
        _, entry = choose_jimaku(win, library.config, library.client.cache_dir, initial_query=title.get(),
                                client=library.client, parser=parser, entry_only=True)
        pass  # Keep the subtitle overlay draggable while this dialog is open.
        if entry:
            if entry.get("flags", {}).get("movie") or not entry.get("flags", {}).get("anime", True):
                status.set("Filme und Drama gehören nicht in diese TV-Reihenfolge.")
                return
            draft = deepcopy(state["proposal"])
            status.set("Dateien des zusätzlichen Abschnitts werden geladen…")
            def rebuild():
                if replacing is None:
                    return library.series.add_entry(draft, entry, parser)
                old = draft["parts"][replacing]
                updated = library.series.add_entry(dict(draft, parts=[]), entry, parser)
                if not updated["parts"]:
                    raise ValueError("Keine nummerierten Staffelabschnitte gefunden.")
                additions = updated["parts"]
                for offset, part in enumerate(additions):
                    part["season"] = old["season"]+offset
                if old.get("global_first") is not None:
                    additions[0]["global_first"] = old["global_first"]
                draft.setdefault("excluded_parts", []).append(old["id"])
                draft["parts"][replacing:replacing+1] = additions
                return draft
            launch(rebuild)

    def close():
        state["closed"] = True
        if state["poll"]:
            win.after_cancel(state["poll"])
        win.destroy()

    def accept():
        if state["busy"] or not commit() or state["proposal"] is None:
            return
        if valid is not None and not valid():
            status.set("Video/Quelle hat gewechselt. Bitte die Map für das aktuelle Video erneut öffnen.")
            return
        try:
            draft = state["proposal"]
            next_global = 1
            for part in draft["parts"]:
                if "global_first" not in part and not draft.get("numbering_confirmed") and part["first"] > 1:
                    part["global_first"] = part["first"]
                if "global_first" not in part and next_global is not None:
                    part["global_first"] = next_global
                start = part.get("global_first")
                next_global = start+part["last"]-part["first"]+1 if start is not None and part["end_confirmed"] else None
            state["proposal"]["numbering_confirmed"] = True
            state["proposal"]["title"] = title.get().strip() or descriptor["title"]
            state["result"] = library.series.confirm(state["proposal"])
        except (ValueError, OSError) as exc:
            status.set(str(exc))
            return
        close()

    def poll():
        try:
            value, error = jobs.get_nowait()
            state["busy"] = False
            if error:
                status.set(error)
            elif value.get("separate"):
                state["result"] = value
                close()
                return
            else:
                state["proposal"] = value
                title.set(value["title"])
                status.set("\n".join(value.get("notices", [])) or
                           "Quellfolgen und Staffelstruktur prüfen. Erste Gesamtfolge leer: automatisch ab 1 bzw. nach bestätigter vorheriger Staffel. Für Fortsetzungen (z. B. One Piece) den tatsächlichen Start eintragen. Unbekannte Staffelenden stoppen die automatische Zuordnung.")
                render(0 if value["parts"] else None)
        except queue.Empty:
            pass
        if not state["closed"]:
            state["poll"] = win.after(80, poll)

    tree.bind("<<TreeviewSelect>>", select)
    tree.bind("<<TreeviewOpen>>", lambda _event: populate(tree.focus()))
    for label, callback in (("↑", lambda: change(-1)), ("↓", lambda: change(1)), ("Entfernen", lambda: change(0)), ("Staffel hinzufügen…", add), ("Staffelquelle ersetzen…", lambda: add(True))):
        ttk.Button(footer, text=label, command=callback).pack(side="left", padx=3)
    ttk.Button(footer, text="Bestätigen", command=accept).pack(side="right", padx=3)
    ttk.Button(footer, text="Abbrechen", command=close).pack(side="right", padx=3)
    win.protocol("WM_DELETE_WINDOW", close)
    win.bind("<Escape>", lambda _event: close())
    if proposal:
        jobs.put((deepcopy(proposal), None))
    else:
        launch(lambda: library.series.propose(descriptor, parser, refresh, seed))
    center_dialog(win, parent)
    win.deiconify()
    pass  # Keep the subtitle overlay draggable while this dialog is open.
    poll()
    parent.wait_window(win)
    return state["result"]
