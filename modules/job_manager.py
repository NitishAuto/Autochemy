"""Job Manager: running and earlier cluster jobs for logins that are already open."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from modules.base_module import BaseModule
from modules.hpc_remote import RemoteSSH, list_open_remotes


class JobManagerModule(BaseModule):
    def get_name(self) -> str:
        return "Job Manager"

    def create_ui(self):
        self.main_frame = ttk.Frame(self.parent_frame)
        self._rows: dict[str, dict] = {}
        self._cache: dict[str, dict] = {}
        self._label_to_id: dict[str, str] = {}
        self._busy_ids: set[str] = set()
        self._log_busy = False
        self._log_pending = None

        bar = ttk.Frame(self.main_frame)
        bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=8)
        ttk.Label(bar, text="Remote:").pack(side=tk.LEFT)
        self.remote_var = tk.StringVar(value="(no remote logged in)")
        self.remote_combo = ttk.Combobox(
            bar,
            textvariable=self.remote_var,
            state="readonly",
            width=36,
            values=["(no remote logged in)"],
        )
        self.remote_combo.pack(side=tk.LEFT, padx=(6, 6))
        self.remote_combo.bind("<<ComboboxSelected>>", self._on_remote)
        ttk.Button(bar, text="Refresh", command=self._refresh_selected).pack(side=tk.LEFT)
        self.status_var = tk.StringVar(
            value="Log in from Input Creator. This page uses that same login and checks ~/.bashrc for squeue or qstat."
        )
        ttk.Label(bar, textvariable=self.status_var, wraplength=720, justify=tk.LEFT).pack(
            side=tk.LEFT, padx=(10, 0)
        )

        tree_wrap = ttk.Frame(self.main_frame)
        self.tree = ttk.Treeview(
            tree_wrap,
            columns=("section", "jobid", "name", "state", "time", "path", "logfile"),
            show="headings",
            selectmode="browse",
        )
        for col, title, width in (
            ("section", "When", 90),
            ("jobid", "Job ID", 120),
            ("name", "Name", 160),
            ("state", "State", 100),
            ("time", "Time", 90),
            ("path", "Folder", 280),
            ("logfile", "Log", 220),
        ):
            self.tree.heading(col, text=title, anchor=tk.W)
            self.tree.column(col, width=width, anchor=tk.W)
        scroll = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        self.log_txt = tk.Text(
            self.main_frame,
            height=12,
            wrap=tk.NONE,
            font=("Menlo", 11),
            bg="#1e1e1e",
            fg="#d9ffd9",
        )
        self.log_txt.insert("1.0", "Select a job to read its log.\n")
        self.log_txt.configure(state=tk.DISABLED)
        self.log_txt.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=8)
        tree_wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)

    def activate(self):
        super().activate()
        self.sync_remotes(fetch_if_needed=True)

    def sync_remotes(self, fetch_if_needed: bool = False) -> None:
        if getattr(self, "remote_combo", None) is None:
            return
        remotes = list_open_remotes()
        labels = []
        mapping = {}
        for info in remotes:
            label = info.get("label") or info.get("id")
            if label in mapping:
                label = f"{label} ({info.get('id')})"
            labels.append(label)
            mapping[label] = info["id"]
            self._cache.setdefault(info["id"], {"info": info, "fetched": False})
            self._cache[info["id"]]["info"] = info
        self._label_to_id = mapping
        if not labels:
            self.remote_combo.configure(values=["(no remote logged in)"])
            self.remote_var.set("(no remote logged in)")
            self._paint(None)
            self.status_var.set("Log in from Input Creator. Job Manager uses that login.")
            return
        self.remote_combo.configure(values=labels)
        if self.remote_var.get() not in mapping:
            self.remote_var.set(labels[0])
        session_id = mapping[self.remote_var.get()]
        self._paint(session_id)
        cached = self._cache.get(session_id) or {}
        if fetch_if_needed and not cached.get("fetched"):
            self._fetch(session_id)

    def _selected_id(self) -> str:
        return self._label_to_id.get(self.remote_var.get(), "")

    def _on_remote(self, _event=None) -> None:
        session_id = self._selected_id()
        cached = self._cache.get(session_id) or {}
        self._paint(session_id)
        if session_id and not cached.get("fetched"):
            self._fetch(session_id)

    def _refresh_selected(self) -> None:
        session_id = self._selected_id()
        if not session_id:
            self.status_var.set("Log in from Input Creator first.")
            return
        self._fetch(session_id)

    def _paint(self, session_id: str | None) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._rows = {}
        cached = self._cache.get(session_id or "")
        if not cached or not cached.get("fetched"):
            return
        rows = list(cached.get("running") or []) + list(cached.get("recent") or [])
        for index, row in enumerate(rows):
            iid = f"j{index}"
            self._rows[iid] = row
            self.tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    row.get("section", ""),
                    row.get("id", ""),
                    row.get("name", ""),
                    row.get("state", ""),
                    row.get("time", ""),
                    row.get("path", ""),
                    row.get("logfile", ""),
                ),
            )
        note = cached.get("note") or ""
        running = len(cached.get("running") or [])
        earlier = len(cached.get("recent") or [])
        self.status_var.set(f"{note}  {running} in the queue, {earlier} earlier.")

    def _fetch(self, session_id: str) -> None:
        cached = self._cache.get(session_id)
        if not cached or session_id in self._busy_ids:
            return
        info = cached.get("info") or {}
        if not info.get("control_path"):
            return
        self._busy_ids.add(session_id)
        self.status_var.set(f"Checking ~/.bashrc and the queue on {info.get('label') or session_id} ...")
        hint = info.get("queue_system") or "SLURM"

        def work():
            error = ""
            running, recent, note = [], [], ""
            try:
                remote = RemoteSSH(info["host"], info["username"], int(info.get("port") or 22), info["control_path"])
                running, recent, note = remote.job_snapshot(hint)
            except Exception as exc:
                error = str(exc)

            def done():
                self._busy_ids.discard(session_id)
                current = self._cache.get(session_id)
                if current is None:
                    return
                if error:
                    current["fetched"] = False
                    if self._selected_id() == session_id:
                        self.status_var.set(error)
                    return
                current["running"] = running
                current["recent"] = recent
                current["note"] = note
                current["fetched"] = True
                if self._selected_id() == session_id:
                    self._paint(session_id)

            try:
                self.main_frame.after(0, done)
            except tk.TclError:
                self._busy_ids.discard(session_id)

        threading.Thread(target=work, daemon=True, name="job-manager").start()

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self._rows.get(selection[0])
        if not row:
            return
        self._load_log(self._selected_id(), row)

    def _load_log(self, session_id: str, row: dict) -> None:
        cached = self._cache.get(session_id) or {}
        info = cached.get("info") or {}
        if not info.get("control_path"):
            return
        if self._log_busy:
            self._log_pending = (session_id, row)
            return
        self._log_busy = True
        self._log_pending = None

        def work():
            error = ""
            found = ""
            body = ""
            try:
                remote = RemoteSSH(info["host"], info["username"], int(info.get("port") or 22), info["control_path"])
                found, body = remote.read_job_log(row.get("path") or "", row.get("logfile") or "", row.get("id") or "")
            except Exception as exc:
                error = str(exc)

            def done():
                self._log_busy = False
                try:
                    self.log_txt.configure(state=tk.NORMAL)
                    self.log_txt.delete("1.0", tk.END)
                    if error:
                        self.log_txt.insert("1.0", error + "\n")
                    else:
                        self.log_txt.insert("1.0", (found or "(no log file found next to the job)") + "\n\n" + (body or ""))
                    self.log_txt.configure(state=tk.DISABLED)
                except tk.TclError:
                    pass
                pending = self._log_pending
                self._log_pending = None
                if pending and pending[0] == self._selected_id() and pending[1] is not row:
                    self._load_log(pending[0], pending[1])

            try:
                self.main_frame.after(0, done)
            except tk.TclError:
                self._log_busy = False

        threading.Thread(target=work, daemon=True, name="job-manager-log").start()
