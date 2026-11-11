from __future__ import annotations

from dataclasses import asdict, replace
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .core import Service, Settings, data_root, endpoint, find_tool


class App:
    def __init__(self, root: tk.Tk, storage: Path | None = None):
        self.root = root
        self.storage = storage or data_root()
        self.storage.mkdir(parents=True, exist_ok=True)
        self.state_path = self.storage / "settings.json"
        self.paths: dict[str, Path] = {}
        self.settings = Settings()
        self.events = queue.SimpleQueue()
        self.logs = queue.Queue(maxsize=500)
        self.closing = False
        self.log = logging.getLogger(f"rtspdesk.{id(self)}")
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        handler = RotatingFileHandler(self.storage / "rtspdesk.log", maxBytes=2_000_000,
                                      backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self.log.addHandler(handler)
        self.service = Service(self.emit, self.storage)
        self.root.title("RTSP Desk")
        scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{min(int(1120 * scale), screen_w - 80)}x{min(int(800 * scale), screen_h - 100)}")
        self.root.minsize(min(int(920 * scale), screen_w - 80), min(int(700 * scale), screen_h - 100))
        self.root.configure(background="#f3f5f8")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TFrame", background="#f3f5f8")
        style.configure("TLabel", background="#f3f5f8", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10), padding=(10, 5))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 23), foreground="#102c48")
        style.configure("Sub.TLabel", foreground="#52647a")
        style.configure("Treeview", rowheight=31, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
        self.mode = tk.StringVar(value="One endpoint per file")
        self.loop = tk.BooleanVar(value=True)
        self.codec = tk.StringVar(value="Compatible · H.264 / AAC")
        self.playlist_status = tk.StringVar(value="Ready")
        self.notice = tk.StringVar(value="Add videos to get started. Streams use RTSP over TCP.")
        self._build()
        self._load()
        self._refresh_urls()
        self.root.after(100, self.poll)

    def _build(self):
        body = ttk.Frame(self.root, padding=24)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(4, weight=1)
        header = ttk.Frame(body)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="RTSP Desk", style="Title.TLabel").pack(side="left")
        self.settings_button = ttk.Button(header, text="Settings", command=self.edit_settings)
        self.settings_button.pack(side="right")
        ttk.Label(body, text="Turn your video files into live RTSP streams.", style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 20))

        options = ttk.Frame(body)
        options.grid(row=2, column=0, sticky="ew", pady=(0, 16))
        ttk.Label(options, text="Playback").pack(side="left", padx=(0, 8))
        self.mode_box = ttk.Combobox(options, textvariable=self.mode, state="readonly", width=25,
                                     values=("One endpoint per file", "Playlist · one endpoint"))
        self.mode_box.pack(side="left")
        self.mode_box.bind("<<ComboboxSelected>>", lambda _: self.mode_changed())
        self.loop_check = ttk.Checkbutton(options, text="Loop", variable=self.loop)
        self.loop_check.pack(side="left", padx=18)
        self.codec_box = ttk.Combobox(options, textvariable=self.codec, state="readonly", width=29,
                                      values=("Compatible · H.264 / AAC", "Direct copy · low CPU"))
        self.codec_box.pack(side="right")

        toolbar = ttk.Frame(body)
        toolbar.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self.add_button = ttk.Button(toolbar, text="+ Add videos", command=self.add_files)
        self.add_button.pack(side="left")
        self.remove_button = ttk.Button(toolbar, text="Remove", command=self.remove)
        self.remove_button.pack(side="left", padx=6)
        self.up_button = ttk.Button(toolbar, text="Move up", command=lambda: self.move(-1))
        self.up_button.pack(side="left")
        self.down_button = ttk.Button(toolbar, text="Move down", command=lambda: self.move(1))
        self.down_button.pack(side="left", padx=6)
        ttk.Button(toolbar, text="Copy URL", command=self.copy_url).pack(side="right")

        table_frame = ttk.Frame(body)
        table_frame.grid(row=4, column=0, sticky="nsew")
        self.table = ttk.Treeview(table_frame, columns=("file", "status", "url"), show="headings", selectmode="extended", height=8)
        for key, label, width in (("file", "Video", 270), ("status", "Status", 170), ("url", "RTSP URL", 520)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=100, stretch=True)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table.tag_configure("streaming", foreground="#176448")
        self.table.tag_configure("error", foreground="#b23434")
        self.table.bind("<Double-1>", lambda _: self.copy_url())
        self.table.bind("<Control-c>", lambda _: self.copy_url())

        self.playlist_label = ttk.Label(body, textvariable=self.playlist_status, style="Sub.TLabel", wraplength=1000)
        self.playlist_label.grid(row=5, column=0, sticky="w", pady=(10, 2))
        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, sticky="ew", pady=(10, 8))
        self.start_all = ttk.Button(buttons, text="Start all", command=lambda: self.start(False))
        self.start_all.pack(side="left")
        self.start_selected = ttk.Button(buttons, text="Start selected", command=lambda: self.start(True))
        self.start_selected.pack(side="left", padx=6)
        self.stop_selected = ttk.Button(buttons, text="Stop selected", command=self.stop_selection)
        self.stop_selected.pack(side="left")
        self.stop_all = ttk.Button(buttons, text="Stop all", command=self.stop_everything)
        self.stop_all.pack(side="left", padx=6)
        ttk.Button(buttons, text="Open logs", command=self.open_logs).pack(side="right")
        ttk.Label(body, textvariable=self.notice, style="Sub.TLabel", wraplength=1000).grid(row=7, column=0, sticky="w", pady=(0, 10))

        ttk.Label(body, text="Activity", style="Sub.TLabel").grid(row=8, column=0, sticky="w")
        self.log_text = tk.Text(body, height=7, font=("Consolas", 9), bg="#e9edf2", fg="#34465b",
                                relief="flat", wrap="word", padx=10, pady=8, state="disabled")
        self.log_text.grid(row=9, column=0, sticky="ew", pady=(5, 0))

    def emit(self, kind: str, key: str, value: str):
        if kind == "log":
            self.log.info("[%s] %s", key, value)
            try:
                self.logs.put_nowait(f"[{key}] {value}")
            except queue.Full:
                pass
        else:
            self.events.put((kind, key, value))

    def poll(self):
        while not self.events.empty():
            kind, key, value = self.events.get()
            if kind == "status":
                if key == "playlist":
                    self.playlist_status.set(f"Playlist: {value}  •  {self.current_settings().url('playlist')}")
                    keys = self.table.get_children()
                else:
                    keys = [key] if self.table.exists(key) else []
                for item in keys:
                    self.table.set(item, "status", value)
                    tag = "streaming" if value == "Streaming" else "error" if value.startswith("Error") else ""
                    self.table.item(item, tags=(tag,))
                if value.startswith("Error"):
                    self.notice.set("A stream could not start or stopped unexpectedly. Details are in Activity and the log file.")
        lines = []
        for _ in range(100):
            try:
                lines.append(self.logs.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "\n".join(lines) + "\n")
            count = int(self.log_text.index("end-1c").split(".")[0])
            if count > 400:
                self.log_text.delete("1.0", f"{count - 400}.0")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        active = self.service.active()
        if self.closing and not active:
            for handler in list(self.log.handlers):
                handler.close()
                self.log.removeHandler(handler)
            self.root.destroy()
            return
        locked = active or self.service.server.child is not None
        for widget in (self.settings_button, self.mode_box, self.loop_check, self.codec_box):
            widget.configure(state="disabled" if locked else "readonly" if isinstance(widget, ttk.Combobox) else "normal")
        if self.is_playlist():
            self.codec_box.configure(state="disabled")
        for widget in (self.add_button, self.remove_button, self.up_button, self.down_button):
            widget.configure(state="disabled" if active else "normal")
        self.start_all.configure(state="disabled" if self.service.stopping or self.closing else "normal")
        self.start_selected.configure(state="disabled" if self.is_playlist() or self.service.stopping or self.closing else "normal")
        self.stop_selected.configure(state="disabled" if self.is_playlist() else "normal")
        self.root.after(100, self.poll)

    def current_settings(self) -> Settings:
        return replace(self.settings, loop=self.loop.get(), copy=self.codec.get().startswith("Direct"))

    def is_playlist(self) -> bool:
        return self.mode.get().startswith("Playlist")

    def mode_changed(self):
        self.start_all.configure(text="Start playlist" if self.is_playlist() else "Start all")
        self.playlist_status.set("Playlist prepares 720p video and audio once, then plays the list in order." if self.is_playlist()
                                 else "One independent RTSP endpoint for each video. Double-click a row to copy its URL.")
        self._refresh_urls()

    def _refresh_urls(self):
        for key in self.table.get_children():
            self.table.set(key, "url", self.current_settings().url("playlist" if self.is_playlist() else key))

    def add_files(self):
        files = filedialog.askopenfilenames(title="Choose videos", filetypes=[
            ("Video files", "*.mp4 *.m4v *.mov *.mkv *.avi *.webm *.ts *.mts *.wmv"), ("All files", "*.*")])
        for name in files:
            self.add_path(Path(name))
        self._save()

    def add_path(self, path: Path):
        path = path.resolve()
        key = endpoint(path)
        if key not in self.paths:
            self.paths[key] = path
            self.table.insert("", "end", iid=key, values=(path.name, "Ready", ""))
        self._refresh_urls()

    def remove(self):
        for key in self.table.selection():
            self.table.delete(key)
            self.paths.pop(key, None)
        self._save()

    def move(self, delta: int):
        chosen = set(self.table.selection())
        ordered = list(self.table.get_children())
        indices = range(len(ordered)) if delta < 0 else range(len(ordered) - 1, -1, -1)
        for index in indices:
            target = index + delta
            if ordered[index] in chosen and 0 <= target < len(ordered) and ordered[target] not in chosen:
                ordered[index], ordered[target] = ordered[target], ordered[index]
        for index, key in enumerate(ordered):
            self.table.move(key, "", index)
        self._save()

    def start(self, selected: bool):
        keys = self.table.selection() if selected else self.table.get_children()
        if not keys:
            messagebox.showinfo("Choose videos", "Select one or more videos first.", parent=self.root)
            return
        try:
            self.service.start([self.paths[k] for k in keys], self.current_settings(), self.is_playlist())
            self.notice.set("Open a URL in VLC or another RTSP player using TCP. Stop all to change playback settings.")
            self._save()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Unable to start", str(exc), parent=self.root)

    def stop_selection(self):
        self.service.stop(list(self.table.selection()))

    def stop_everything(self):
        self.notice.set("Stopping streams and the local RTSP server…")
        self.service.stop_all()
        self.root.after(300, self._stopped_notice)

    def _stopped_notice(self):
        if self.service.stopping:
            self.root.after(200, self._stopped_notice)
        elif not self.closing:
            self.notice.set("Stopped. Playback and server settings can now be changed.")

    def copy_url(self):
        keys = self.table.selection()
        if not keys:
            if self.is_playlist():
                urls = [self.current_settings().url("playlist")]
            else:
                self.notice.set("Select a video row to copy its URL.")
                return
        else:
            urls = list(dict.fromkeys(self.table.set(k, "url") for k in keys))
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(urls))
        self.notice.set(f"Copied {len(urls)} RTSP URL(s). Streams must be running before a player can connect.")

    def _load(self):
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            allowed = Settings.__dataclass_fields__
            self.settings = Settings(**{k: v for k, v in saved.get("settings", {}).items() if k in allowed})
            self.settings.validate()
            self.loop.set(self.settings.loop)
            self.codec.set("Direct copy · low CPU" if self.settings.copy else "Compatible · H.264 / AAC")
            if saved.get("playlist"):
                self.mode.set("Playlist · one endpoint")
            for name in saved.get("files", []):
                self.add_path(Path(name))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            self.settings = Settings()
            self.emit("log", "app", f"Saved settings could not be read: {exc}")
        self.mode_changed()

    def _save(self):
        data = {"settings": asdict(self.current_settings()), "playlist": self.is_playlist(),
                "files": [str(self.paths[k]) for k in self.table.get_children()]}
        try:
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.state_path)
        except OSError as exc:
            self.emit("log", "app", f"Could not save settings: {exc}")

    def open_logs(self):
        os.startfile(str(self.storage))

    def edit_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("RTSP Desk · Settings")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=24)
        frame.pack(fill="both", expand=True)
        port = tk.StringVar(value=str(self.settings.port))
        lan = tk.BooleanVar(value=self.settings.lan)
        host = tk.StringVar(value=self.settings.host)
        ffdir = tk.StringVar(value=self.settings.ffmpeg_dir)
        mtxdir = tk.StringVar(value=self.settings.mediamtx_dir)
        fields = [("RTSP port", port), ("Host shown in LAN URLs", host),
                  ("FFmpeg / ffprobe folder", ffdir), ("MediaMTX folder", mtxdir)]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=7, padx=(0, 12))
            ttk.Entry(frame, textvariable=variable, width=47).grid(row=row, column=1, sticky="ew", pady=7)
            if row >= 2:
                def choose(var=variable):
                    folder = filedialog.askdirectory(parent=dialog, title="Choose tools folder")
                    if folder:
                        var.set(folder)
                ttk.Button(frame, text="Browse…", command=choose).grid(row=row, column=2, padx=(8, 0))
        ttk.Checkbutton(frame, text="Allow devices on my network to view streams", variable=lan).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(12, 6))
        ttk.Label(frame, text="Local-only by default. Network viewing has no password. Use on a trusted network.\n"
                  "For LAN URLs, enter this computer's IPv4 address or hostname. Only TCP is used.\n"
                  "Leave tool folders blank to use the bundled tools folder or PATH.",
                  style="Sub.TLabel", wraplength=660).grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 15))

        def save():
            try:
                new = replace(self.current_settings(), port=int(port.get()), lan=lan.get(), host=host.get().strip(),
                              ffmpeg_dir=ffdir.get().strip(), mediamtx_dir=mtxdir.get().strip())
                new.validate()
                for tool, folder in (("ffmpeg", new.ffmpeg_dir), ("ffprobe", new.ffmpeg_dir), ("mediamtx", new.mediamtx_dir)):
                    find_tool(tool, folder)
                self.settings = new
                self._refresh_urls()
                self._save()
                dialog.destroy()
            except (ValueError, OSError) as exc:
                messagebox.showerror("Check settings", str(exc), parent=dialog)

        def clear_cache():
            try:
                count = 0
                for path in self.service.cache.glob("*.mp4"):
                    path.unlink(missing_ok=True)
                    count += 1
                messagebox.showinfo("Playlist cache", f"Removed {count} prepared files. Source videos are unchanged.", parent=dialog)
            except OSError as exc:
                messagebox.showerror("Playlist cache", str(exc), parent=dialog)

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky="ew")
        ttk.Button(buttons, text="Clear playlist cache", command=clear_cache).pack(side="left")
        ttk.Button(buttons, text="Save", command=save).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right", padx=8)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self._save()
        self.notice.set("Closing streams…")
        self.service.stop_all(close=True)


def main(storage: Path | None = None):
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    try:
        App(root, storage)
        root.mainloop()
    except Exception as exc:
        messagebox.showerror("RTSP Desk", f"The application could not start:\n{exc}", parent=root)
        root.destroy()
        raise
