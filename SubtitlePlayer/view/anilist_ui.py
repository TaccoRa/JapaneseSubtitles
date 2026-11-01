"""AniList account setup and asynchronous personal-list selection."""
import queue
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser

from model.anilist_auth import ClientSecretStore, DEVELOPER_URL, REDIRECT_URI
from view.jimaku_series import center_dialog


def setup_client(parent, initial_id="", secret_store=None):
    secret_store = secret_store or ClientSecretStore()
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title("AniList · einmalige Einrichtung")
    win.transient(parent.winfo_toplevel())
    result = {"id": None}
    client_id, status = tk.StringVar(win, initial_id), tk.StringVar(win)
    client_secret = tk.StringVar(win)
    ttk.Label(win, text="Einmal eine AniList-Anwendung registrieren. Danach genügt der Browser-Login.\n"
              "Unter Create New Application diese beiden Werte verwenden:", justify="left").pack(padx=14, pady=12)
    def copy(value):
        win.clipboard_clear()
        win.clipboard_append(value)
    for label, value in (("Name", "SubtitlePlayer"), ("Redirect URL", REDIRECT_URI)):
        row = ttk.Frame(win)
        row.pack(fill="x", padx=14, pady=3)
        ttk.Label(row, text=label+": "+value).pack(side="left")
        ttk.Button(row, text="Kopieren", command=lambda value=value: copy(value)).pack(side="right", padx=6)
    ttk.Button(win, text="AniList-Einrichtung öffnen", command=lambda: webbrowser.open(DEVELOPER_URL)).pack(pady=8)
    ttk.Label(win, text="Die Client ID und das Client Secret der Anwendung hier einfügen.").pack(padx=14)
    ttk.Entry(win, textvariable=client_id, width=18).pack(pady=6)
    ttk.Label(win, text="Client Secret (wird im Windows-Anmeldeinformationsspeicher gespeichert)").pack(padx=14)
    ttk.Entry(win, textvariable=client_secret, show="*", width=52).pack(padx=14, pady=6)
    ttk.Label(win, text="Leer lassen, um ein bereits gespeichertes Secret dieser Client ID zu behalten.").pack(padx=14)
    ttk.Label(win, textvariable=status, wraplength=520).pack(padx=14)
    def accept():
        value = client_id.get().strip()
        if not value.isdigit():
            status.set("Bitte die numerische Client ID eingeben.")
            return
        try:
            secret = client_secret.get().strip()
            if secret:
                secret_store.write(value, secret)
            elif not secret_store.read(value):
                status.set("Bitte auch das Client Secret dieser AniList-Anwendung eingeben.")
                return
        except (ValueError, OSError) as exc:
            status.set(str(exc))
            return
        client_secret.set("")
        result["id"] = value
        win.destroy()
    footer = ttk.Frame(win)
    footer.pack(pady=12)
    ttk.Button(footer, text="Weiter zur Anmeldung", command=accept).pack(side="left", padx=5)
    ttk.Button(footer, text="Abbrechen", command=win.destroy).pack(side="left")
    center_dialog(win, parent)
    win.deiconify()
    pass  # Keep the subtitle overlay draggable while this dialog is open.
    parent.wait_window(win)
    return result["id"]


def choose_user_anime(parent, account):
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title("Meine AniList-Liste")
    win.transient(parent.winfo_toplevel())
    category = tk.StringVar(win, "Watching")
    status = tk.StringVar(win)
    choices = {"Watching": "CURRENT", "Planning": "PLANNING", "Completed": "COMPLETED",
               "Paused": "PAUSED", "Dropped": "DROPPED", "Rewatching": "REPEATING"}
    combo = ttk.Combobox(win, values=list(choices), textvariable=category, state="readonly")
    combo.pack(padx=10, pady=8, anchor="w")
    tree = ttk.Treeview(win, columns=("title", "progress"), show="headings", height=15)
    tree.heading("title", text="Anime")
    tree.heading("progress", text="Gesehene Folgen")
    tree.column("title", width=440)
    tree.column("progress", width=110)
    tree.pack(fill="both", expand=True, padx=10)
    ttk.Label(win, textvariable=status, wraplength=540).pack(padx=10, pady=6)
    jobs = queue.Queue()
    state = {"sequence": 0, "rows": [], "result": None, "closed": False, "poll": None}
    def close():
        state["closed"] = True
        if state["poll"]:
            win.after_cancel(state["poll"])
        win.destroy()
    def load(_event=None):
        state["sequence"] += 1
        sequence, epoch, selected = state["sequence"], account.epoch, choices[category.get()]
        credential = account.credential
        tree.delete(*tree.get_children())
        state["rows"] = []
        if not credential:
            status.set("Bitte zuerst mit AniList verbinden.")
            return
        status.set("Liste wird geladen…")
        def work():
            try:
                rows, error = account.client.user_list(credential["token"], credential["user_id"], selected), None
            except Exception as exc:
                rows, error = [], str(exc)
            jobs.put((sequence, epoch, rows, error))
        threading.Thread(target=work, name="anilist-list", daemon=True).start()
    def accept(_event=None):
        chosen = tree.selection()
        if chosen:
            media = state["rows"][int(chosen[0])]["media"]
            state["result"] = media
            close()
    def poll():
        try:
            while True:
                sequence, epoch, rows, error = jobs.get_nowait()
                if sequence != state["sequence"] or epoch != account.epoch:
                    continue
                state["rows"] = rows
                tree.delete(*tree.get_children())
                for i, row in enumerate(rows):
                    title = row["media"]["title"]
                    tree.insert("", "end", iid=str(i), values=(title.get("romaji") or title.get("english") or title.get("native"), row["progress"]))
                status.set(error or f"{len(rows)} Anime. Auswahl öffnet die Jimaku-Quellenauswahl.")
        except queue.Empty:
            pass
        if not state["closed"]:
            state["poll"] = win.after(80, poll)
    ttk.Button(win, text="In Jimaku auswählen", command=accept).pack(pady=6)
    combo.bind("<<ComboboxSelected>>", load)
    tree.bind("<Double-Button-1>", accept)
    win.protocol("WM_DELETE_WINDOW", close)
    center_dialog(win, parent)
    win.deiconify()
    pass  # Keep the subtitle overlay draggable while this dialog is open.
    load()
    poll()
    parent.wait_window(win)
    return state["result"]
