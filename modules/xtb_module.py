"""
Standalone xTB module: build inputs (XYZ, xcontrol), run calculations, inspect outputs.
Independent of ORCA input templates — similar role to Output Viewer in the suite.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from modules import app_theme
except ImportError:
    app_theme = None  # type: ignore

from modules.base_module import BaseModule
from modules import xtb_support


class XTBModule(BaseModule):
    """GFN-xTB calculations with local geometry and job controls."""

    JOB_LABELS = (
        "Single point",
        "Geometry optimization",
        "Frequencies (Hessian)",
        "Optimization + frequencies",
        "Relaxed PES scan",
        "Constrained optimization",
    )

    def __init__(self, parent_frame):
        super().__init__(parent_frame)
        self._root = parent_frame.winfo_toplevel()
        self.xtb_process_holder: list = [None]
        self.xtb_queue = None
        self.constraint_rows: list = []
        self._last_xtb_run_folder = None
        self._last_xtb_is_scan = False
        self._last_xtb_job = ""
        self._last_run_engine = ""
        self._last_run_folder = None
        self._last_run_job = ""
        self.xtb_energy_unit = tk.StringVar(value="kcal/mol")
        self._last_xtb_scan_meta = None
        self._last_xtb_geom_text = ""

    def get_name(self) -> str:
        return "xTB"

    def get_icon(self) -> str:
        return "⚗️"

    def create_ui(self) -> None:
        self.main_frame = ttk.Frame(self.parent_frame, padding=10)

        top = ttk.Frame(self.main_frame)
        top.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(top, text="xTB (GFN)", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="ℹ Cite xTB", command=self._show_citation).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(
            top,
            text="📖 Setup docs",
            command=lambda: webbrowser.open("https://xtb-docs.readthedocs.io/en/latest/setup.html"),
        ).pack(side=tk.RIGHT)

        outer = ttk.PanedWindow(self.main_frame, orient=tk.HORIZONTAL)
        outer.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(outer, padding=(0, 0, 8, 0))
        outer.add(left, weight=2)

        right = ttk.Frame(outer, padding=(8, 0, 0, 0))
        outer.add(right, weight=3)

        # --- Left: geometry + job ---
        gf = ttk.LabelFrame(left, text="Geometry (XYZ — atom lines or full XYZ)")
        gf.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        cm = ttk.Frame(gf)
        cm.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self.charge = tk.StringVar(value="0")
        self.mult = tk.StringVar(value="1")
        ttk.Label(cm, text="Charge:").pack(side=tk.LEFT)
        ttk.Entry(cm, textvariable=self.charge, width=6).pack(side=tk.LEFT, padx=(2, 12))
        ttk.Label(cm, text="Mult:").pack(side=tk.LEFT)
        ttk.Entry(cm, textvariable=self.mult, width=6).pack(side=tk.LEFT, padx=(2, 0))
        geo_btns = ttk.Frame(gf)
        geo_btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Button(geo_btns, text="Load XYZ…", command=self._load_xyz).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(geo_btns, text="Save XYZ…", command=self._save_xyz).pack(side=tk.LEFT)

        self.geom_txt = tk.Text(gf, height=14, wrap=tk.NONE, font=("Consolas", 10))
        gy = ttk.Scrollbar(gf, orient=tk.VERTICAL, command=self.geom_txt.yview)
        gx = ttk.Scrollbar(gf, orient=tk.HORIZONTAL, command=self.geom_txt.xview)
        self.geom_txt.configure(yscrollcommand=gy.set, xscrollcommand=gx.set)
        self.geom_txt.grid(row=2, column=0, sticky="nsew")
        gy.grid(row=2, column=1, sticky="ns")
        gx.grid(row=3, column=0, sticky="ew")
        gf.grid_rowconfigure(2, weight=1)
        gf.grid_columnconfigure(0, weight=1)

        self.jf = ttk.LabelFrame(left, text="Calculation")
        self.jf.pack(fill=tk.X, pady=(0, 8))
        jf = self.jf

        self.xtb_job_choice = tk.StringVar(value=self.JOB_LABELS[1])
        ttk.Label(jf, text="Job:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.job_cb = ttk.Combobox(
            jf, textvariable=self.xtb_job_choice, values=self.JOB_LABELS, state="readonly", width=32
        )
        self.job_cb.grid(row=0, column=1, sticky="ew", padx=4, pady=2)

        self.xtb_gfn_level = tk.StringVar(value="2")
        self.xtb_version_choice = tk.StringVar(value=xtb_support.default_xtb_version_label())
        self.xtb_opt_level = tk.StringVar(value="normal")
        
        self.xtb_settings_summary = ttk.Label(jf, text="", font=("Segoe UI", 9))
        self.xtb_settings_summary.grid(row=1, column=0, columnspan=2, sticky="w", padx=4, pady=2)
        ttk.Button(jf, text="⚙ xTB Settings", command=self._open_xtb_settings).grid(row=2, column=0, columnspan=2, sticky="w", padx=4, pady=2)

        self.job_hint = ttk.Label(jf, text="", font=("Segoe UI", 9, "italic"))
        self.job_hint.grid(row=3, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        jf.columnconfigure(1, weight=1)

        self.scan_frame = ttk.LabelFrame(left, text="Scan (1-based atom indices as in xcontrol)")
        self.scan_ctype = tk.StringVar(value="Bond")
        self.scan_a1 = tk.StringVar(value="1")
        self.scan_a2 = tk.StringVar(value="2")
        self.scan_a3 = tk.StringVar(value="3")
        self.scan_a4 = tk.StringVar(value="4")
        self.scan_start = tk.StringVar(value="1.5")
        self.scan_end = tk.StringVar(value="3.0")
        self.scan_steps = tk.StringVar(value="11")

        sf = ttk.Frame(self.scan_frame)
        sf.pack(fill=tk.X, padx=4, pady=4)
        ttk.Combobox(sf, textvariable=self.scan_ctype, values=["Bond", "Angle", "Dihedral"], width=9, state="readonly").pack(
            side=tk.LEFT
        )
        ttk.Label(sf, text="Atoms:").pack(side=tk.LEFT, padx=(6, 0))
        self.scan_e1 = ttk.Entry(sf, textvariable=self.scan_a1, width=4)
        self.scan_e2 = ttk.Entry(sf, textvariable=self.scan_a2, width=4)
        self.scan_e3 = ttk.Entry(sf, textvariable=self.scan_a3, width=4)
        self.scan_e4 = ttk.Entry(sf, textvariable=self.scan_a4, width=4)
        self.scan_e1.pack(side=tk.LEFT, padx=1)
        self.scan_e2.pack(side=tk.LEFT, padx=1)
        ttk.Label(sf, text="Start:").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(sf, textvariable=self.scan_start, width=6).pack(side=tk.LEFT)
        ttk.Label(sf, text="End:").pack(side=tk.LEFT, padx=(4, 0))
        ttk.Entry(sf, textvariable=self.scan_end, width=6).pack(side=tk.LEFT)
        ttk.Label(sf, text="Steps:").pack(side=tk.LEFT, padx=(4, 0))
        ttk.Entry(sf, textvariable=self.scan_steps, width=5).pack(side=tk.LEFT)

        self.const_frame = ttk.LabelFrame(left, text="Constraints (1-based atom indices)")
        cr_top = ttk.Frame(self.const_frame)
        cr_top.pack(fill=tk.X, padx=4, pady=4)
        self.const_rows_frame = ttk.Frame(self.const_frame)
        self.const_rows_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        ttk.Button(cr_top, text="+ Add constraint", command=self._add_constraint_row).pack(side=tk.LEFT)

        

        inf = ttk.LabelFrame(left, text="Save inputs (no run)")
        inf.pack(fill=tk.X)
        ib = ttk.Frame(inf)
        ib.pack(fill=tk.X, padx=4, pady=6)
        ttk.Button(ib, text="Save xcontrol.inp…", command=self._save_xcontrol_dialog).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ib, text="Save run script…", command=self._save_run_script_dialog).pack(side=tk.LEFT)

        self.job_cb.bind("<<ComboboxSelected>>", lambda e: self._on_job_changed())
        self.scan_ctype.trace_add("write", lambda *a: self._refresh_scan_atom_entries())
        
        self._on_job_changed()

        # --- Right: run + log ---
        runf = ttk.Frame(right)
        runf.pack(fill=tk.X, pady=(0, 8))
        self.btn_run = ttk.Button(runf, text="▶ Run xTB", command=self._start_run)
        self.btn_run.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_stop = ttk.Button(runf, text="Stop", command=self._stop_run, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 12))
        self.external_viewer = tk.StringVar(value="Chemcraft")
        ttk.Label(runf, text="Open result:").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Combobox(
            runf,
            textvariable=self.external_viewer,
            values=["Chemcraft", "Jmol", "Default app"],
            state="readonly",
            width=14,
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(runf, text="Open XYZ file…", command=self._open_xyz_external).pack(side=tk.LEFT)


        ttk.Button(runf, text="Open folder", command=self._open_output_folder).pack(side=tk.RIGHT, padx=(0, 6))

        xtb_panes = ttk.PanedWindow(right, orient=tk.HORIZONTAL)
        xtb_panes.pack(fill=tk.BOTH, expand=True)

        xtb_console_frame = ttk.Frame(xtb_panes)
        xtb_panes.add(xtb_console_frame, weight=1)

        xtb_left_vert = ttk.PanedWindow(xtb_console_frame, orient=tk.VERTICAL)
        xtb_left_vert.pack(fill=tk.BOTH, expand=True)

        xtb_log_wrap = ttk.Frame(xtb_left_vert)
        xtb_left_notebook = ttk.Notebook(xtb_left_vert)
        xtb_left_vert.add(xtb_log_wrap, weight=1)
        xtb_left_vert.add(xtb_left_notebook, weight=1)
        
        self.xtb_left_vert = xtb_left_vert

        log_top_frame = ttk.Frame(xtb_log_wrap)
        log_top_frame.pack(fill=tk.X)
        ttk.Label(log_top_frame, text="Live Log Stream:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.btn_full_log = ttk.Button(log_top_frame, text="Open Full Log", command=self._open_full_log, state=tk.DISABLED)
        self.btn_full_log.pack(side=tk.LEFT, padx=10)

        log_txt_frame = ttk.Frame(xtb_log_wrap)
        log_txt_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        self.log_txt = tk.Text(log_txt_frame, height=10, width=40, font=("Consolas", 9), bg="#1e1e1e", fg="#00ff00", wrap=tk.NONE)
        ly = ttk.Scrollbar(log_txt_frame, orient="vertical", command=self.log_txt.yview)
        lx = ttk.Scrollbar(log_txt_frame, orient="horizontal", command=self.log_txt.xview)
        self.log_txt.configure(yscrollcommand=ly.set, xscrollcommand=lx.set)
        ly.pack(side=tk.RIGHT, fill=tk.Y)
        lx.pack(side=tk.BOTTOM, fill=tk.X)
        self.log_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        num_f = ttk.Frame(xtb_left_notebook)
        geom_out_f = ttk.Frame(xtb_left_notebook)
        xtb_left_notebook.add(num_f, text="Numbers")
        xtb_left_notebook.add(geom_out_f, text="xTB Output Geometry")

        self.num_txt = tk.Text(num_f, height=10, width=40, font=("Consolas", 9), wrap=tk.NONE)
        ny = ttk.Scrollbar(num_f, orient="vertical", command=self.num_txt.yview)
        nx = ttk.Scrollbar(num_f, orient="horizontal", command=self.num_txt.xview)
        self.num_txt.configure(yscrollcommand=ny.set, xscrollcommand=nx.set)
        ny.pack(side=tk.RIGHT, fill=tk.Y)
        nx.pack(side=tk.BOTTOM, fill=tk.X)
        self.num_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.num_txt.insert("1.0", "Important values will appear here after xTB run...")
        self.num_txt.config(state=tk.DISABLED)

        self.geom_out_txt = tk.Text(geom_out_f, height=10, width=40, font=("Consolas", 10), wrap=tk.NONE)
        gy = ttk.Scrollbar(geom_out_f, orient="vertical", command=self.geom_out_txt.yview)
        gx = ttk.Scrollbar(geom_out_f, orient="horizontal", command=self.geom_out_txt.xview)
        self.geom_out_txt.configure(yscrollcommand=gy.set, xscrollcommand=gx.set)
        gy.pack(side=tk.RIGHT, fill=tk.Y)
        gx.pack(side=tk.BOTTOM, fill=tk.X)
        self.geom_out_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        xtb_right_vert = ttk.PanedWindow(xtb_panes, orient=tk.VERTICAL)
        xtb_panes.add(xtb_right_vert, weight=1)

        scan_f = ttk.Frame(xtb_right_vert)
        xtb_right_vert.add(scan_f, weight=1)

        self.scan_plot_title = ttk.Label(scan_f, text="Graphs will appear here.", font=("Segoe UI", 9, "italic"))
        self.scan_plot_title.pack(anchor="w", pady=(2, 2))
        self.scan_plot_host = ttk.Frame(scan_f)
        self.scan_plot_host.pack(fill=tk.BOTH, expand=True)
        self.right_out_split = xtb_right_vert


        bf = ttk.Frame(right)
        bf.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bf, text="Use output XYZ in geometry", command=self._use_output_geometry).pack(side=tk.LEFT, padx=(0, 6))
        self.btn_vis = ttk.Button(bf, text="Visualize last result", command=self._visualize_last, state=tk.DISABLED)
        self.btn_vis.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_graph = ttk.Button(bf, text="Energy / scan plot", command=self._scan_graph, state=tk.DISABLED)
        self.btn_graph.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bf, text="Full log window", command=self._open_full_log).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bf, text="Output folder", command=self._open_output_folder).pack(side=tk.LEFT)

        self.folder_var = tk.StringVar(
            value="Last run folder: (none yet — xTB runs use external_modules/xtb/xtb_runs/, CREST runs use external_modules/xtb/crest_runs/)"
        )
        ttk.Label(right, textvariable=self.folder_var, font=("Consolas", 8), wraplength=560, justify=tk.LEFT).pack(
            fill=tk.X, pady=(8, 0), anchor="w"
        )

        self._refresh_xtb_settings_summary()
        self._refresh_job_hint()
        self.main_frame.after(500, self._balance_scan_plot_pane)

    def get_session_state(self):
        constraints = []
        for row in self.constraint_rows:
            constraints.append(
                {
                    "type": row["type"].get(),
                    "a1": row["a1"].get(),
                    "a2": row["a2"].get(),
                    "a3": row["a3"].get(),
                    "a4": row["a4"].get(),
                }
            )
        var_names = (
            "charge",
            "mult",
            "xtb_job_choice",
            "xtb_version_choice",
            "xtb_gfn_level",
            "xtb_opt_level",
            "xtb_energy_unit",
            "scan_ctype",
            "scan_a1",
            "scan_a2",
            "scan_a3",
            "scan_a4",
            "scan_start",
            "scan_end",
            "scan_steps",
            "crest_mode",
            "crest_gfn_level",
            "crest_ewin",
            "crest_temp",
            "crest_threads",
            "crest_solvent",
            "crest_extra_args",
            "external_viewer",
        )
        vars_blob = {}
        for name in var_names:
            var_obj = getattr(self, name, None)
            if var_obj is not None and hasattr(var_obj, "get"):
                vars_blob[name] = var_obj.get()
        return {
            "version": 1,
            "vars": vars_blob,
            "constraints": constraints,
            "texts": {
                "geom_txt": self.geom_txt.get("1.0", "end-1c"),
                "num_txt": self.num_txt.get("1.0", "end-1c"),
                "geom_out_txt": self.geom_out_txt.get("1.0", "end-1c"),
                "log_txt_tail": self.log_txt.get("1.0", "end-1c")[-50000:],
            },
            "last_run": {
                "last_xtb_run_folder": self._last_xtb_run_folder,
                "last_xtb_is_scan": self._last_xtb_is_scan,
                "last_xtb_job": self._last_xtb_job,
                "last_run_engine": self._last_run_engine,
                "last_run_folder": self._last_run_folder,
                "last_run_job": self._last_run_job,
                "last_xtb_scan_meta": self._last_xtb_scan_meta,
                "folder_label": self.folder_var.get() if hasattr(self, "folder_var") else "",
            },
        }

    def apply_session_state(self, state):
        if not isinstance(state, dict):
            return
        for name, value in (state.get("vars") or {}).items():
            var_obj = getattr(self, name, None)
            if var_obj is not None and hasattr(var_obj, "set"):
                try:
                    var_obj.set(value)
                except Exception:
                    pass

        for row in list(self.constraint_rows):
            try:
                row["frame"].destroy()
            except Exception:
                pass
        self.constraint_rows = []

        for row in state.get("constraints") or []:
            self._add_constraint_row()
            current = self.constraint_rows[-1]
            current["type"].set(row.get("type", "Bond"))
            current["a1"].set(row.get("a1", ""))
            current["a2"].set(row.get("a2", ""))
            current["a3"].set(row.get("a3", ""))
            current["a4"].set(row.get("a4", ""))

        texts = state.get("texts") or {}
        self._restore_text_widget(self.geom_txt, texts.get("geom_txt", ""))
        self._restore_text_widget(self.num_txt, texts.get("num_txt", ""))
        self._restore_text_widget(self.geom_out_txt, texts.get("geom_out_txt", ""))
        self._restore_text_widget(self.log_txt, texts.get("log_txt_tail", ""))

        last_run = state.get("last_run") or {}
        self._last_xtb_run_folder = last_run.get("last_xtb_run_folder")
        self._last_xtb_is_scan = bool(last_run.get("last_xtb_is_scan", False))
        self._last_xtb_job = last_run.get("last_xtb_job", "")
        self._last_run_engine = last_run.get("last_run_engine", "")
        self._last_run_folder = last_run.get("last_run_folder")
        self._last_run_job = last_run.get("last_run_job", "")
        self._last_xtb_scan_meta = last_run.get("last_xtb_scan_meta")
        if hasattr(self, "folder_var"):
            self.folder_var.set(
                last_run.get("folder_label")
                or "Last run folder: (none yet - xTB runs use external_modules/xtb/xtb_runs/, CREST runs use external_modules/xtb/crest_runs/)"
            )

        self._on_job_changed()
        self._refresh_scan_atom_entries()
        self._refresh_xtb_settings_summary()
        self._refresh_crest_settings_summary()
        self._refresh_job_hint()
        if hasattr(self, "btn_vis"):
            self.btn_vis.config(state=tk.NORMAL if self._last_run_folder else tk.DISABLED)
        if hasattr(self, "btn_graph"):
            graph_ready = False
            if self._last_run_engine == "xtb" and self._last_xtb_is_scan:
                graph_ready = True
            elif self._last_run_engine == "crest" and self._last_run_folder:
                graph_ready = True
            self.btn_graph.config(state=tk.NORMAL if graph_ready else tk.DISABLED)

    def _restore_text_widget(self, widget, text):
        try:
            prior_state = widget.cget("state")
        except tk.TclError:
            prior_state = None
        if str(prior_state) == "disabled":
            try:
                widget.config(state=tk.NORMAL)
            except tk.TclError:
                pass
        widget.delete("1.0", tk.END)
        if text:
            widget.insert("1.0", text)
        if str(prior_state) == "disabled":
            try:
                widget.config(state=tk.DISABLED)
            except tk.TclError:
                pass

    def _refresh_xtb_settings_summary(self):
        if getattr(self, "xtb_settings_summary", None) is None:
            return
        self.xtb_settings_summary.config(
            text=f"{self.xtb_version_choice.get()} | GFN{self.xtb_gfn_level.get()} | {self.xtb_opt_level.get()} | {self.xtb_energy_unit.get()}"
        )

    def _open_xtb_settings(self):
        win = tk.Toplevel(self.parent_frame)
        win.title("xTB Settings")
        win.geometry("420x225")
        win.resizable(False, False)
        body = ttk.Frame(win, padding=10)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="xTB Version:").grid(row=0, column=0, sticky="w", pady=4)
        version_cb = ttk.Combobox(body, values=xtb_support.XTB_VERSION_LABELS, state="readonly", width=18)
        current_version = self.xtb_version_choice.get() or xtb_support.default_xtb_version_label()
        if current_version not in xtb_support.XTB_VERSION_LABELS:
            current_version = xtb_support.default_xtb_version_label()
        version_cb.set(current_version)
        version_cb.grid(row=0, column=1, sticky="w", pady=4)
        path_var = tk.StringVar(value=xtb_support.bundled_xtb_versions().get(version_cb.get(), ""))
        ttk.Label(body, textvariable=path_var, font=("Segoe UI", 8), wraplength=280).grid(
            row=1, column=1, sticky="w", pady=(0, 4)
        )

        def _sync_path(_event=None):
            path_var.set(xtb_support.bundled_xtb_versions().get(version_cb.get(), ""))

        version_cb.bind("<<ComboboxSelected>>", _sync_path)

        ttk.Label(body, text="GFN Level:").grid(row=2, column=0, sticky="w", pady=4)
        gfn_cb = ttk.Combobox(body, values=["2", "1", "0"], state="readonly", width=8)
        gfn_cb.set(self.xtb_gfn_level.get())
        gfn_cb.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(body, text="Opt Level:").grid(row=3, column=0, sticky="w", pady=4)
        opt_cb = ttk.Combobox(
            body,
            values=["normal", "tight", "vtight", "extreme", "loose", "vloose"],
            state="readonly",
            width=12,
        )
        opt_cb.set(self.xtb_opt_level.get())
        opt_cb.grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(body, text="Energy Unit:").grid(row=4, column=0, sticky="w", pady=4)
        en_cb = ttk.Combobox(body, values=["kcal/mol", "kJ/mol", "Eh"], state="readonly", width=12)
        en_cb.set(self.xtb_energy_unit.get())
        en_cb.grid(row=4, column=1, sticky="w", pady=4)

        def _apply():
            self.xtb_version_choice.set(version_cb.get())
            self.xtb_gfn_level.set(gfn_cb.get())
            self.xtb_opt_level.set(opt_cb.get())
            self.xtb_energy_unit.set(en_cb.get())
            self._refresh_xtb_settings_summary()
            win.destroy()

        ttk.Button(body, text="Apply", command=_apply).grid(row=5, column=0, pady=(10, 0), sticky="w")
        ttk.Button(body, text="Close", command=win.destroy).grid(row=5, column=1, pady=(10, 0), sticky="e")

    def _set_last_run_state(self, *, engine: str, folder: str | None, job: str, is_scan: bool = False) -> None:
        self._last_run_engine = engine
        self._last_run_folder = folder
        self._last_run_job = job
        self._last_xtb_run_folder = folder if engine == "xtb" else self._last_xtb_run_folder
        self._last_xtb_is_scan = is_scan if engine == "xtb" else False
        self._last_xtb_job = job if engine == "xtb" else self._last_xtb_job

    def _build_crest_cfg(self) -> dict:
        return {
            "mode": self.crest_mode.get().strip(),
            "gfn": self.crest_gfn_level.get().strip(),
            "ewin": float(self.crest_ewin.get()),
            "temp": float(self.crest_temp.get()),
            "threads": int(self.crest_threads.get()),
            "solvent": self.crest_solvent.get().strip(),
            "extra_args": self.crest_extra_args.get().strip(),
        }

    def apply_app_theme(self, ctx) -> None:
        if not app_theme:
            return
            
        is_beginner = ctx.get("beginner_mode", True)
        if hasattr(self, "scan_frame") and hasattr(self, "const_frame"):
            if is_beginner:
                self.scan_frame.config(text="Scan (xTB numbering starts from 1)")
                self.const_frame.config(text="Constraints (xTB numbering starts from 1)")
            else:
                self.scan_frame.config(text="Scan")
                self.const_frame.config(text="Constraints")
                
        for w in (getattr(self, "geom_txt", None), getattr(self, "log_txt", None), getattr(self, "num_txt", None), getattr(self, "geom_out_txt", None)):
            if w is not None:
                try:
                    app_theme.apply_editor_style(w, ctx)
                except Exception:
                    pass
        if getattr(self, "log_txt", None) is not None:
            try:
                p = ctx.get("palette", {})
                self.log_txt.configure(bg=p.get("editor_bg", "#0d1117"), fg="#3fb950")
            except tk.TclError:
                pass

    def _balance_scan_plot_pane(self) -> None:
        try:
            pw = getattr(self, "right_out_split", None)
            if pw is None:
                return
            pw.update_idletasks()
            h = pw.winfo_height()
            if h > 80:
                pw.sashpos(0, max(int(h * 0.4), 100))
        except (tk.TclError, AttributeError):
            pass

    def _on_job_changed(self) -> None:
        choice = self.xtb_job_choice.get()
        self.scan_frame.pack_forget()
        self.const_frame.pack_forget()
        if choice == "Relaxed PES scan":
            self.scan_frame.pack(fill=tk.X, pady=(0, 8), after=self.jf)
            self._refresh_scan_atom_entries()
        elif choice == "Constrained optimization":
            self.const_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 8), after=self.jf)
            if not self.constraint_rows:
                self._add_constraint_row()
        self._refresh_job_hint()

    def _refresh_scan_atom_entries(self) -> None:
        t = self.scan_ctype.get()
        self.scan_e3.pack_forget()
        self.scan_e4.pack_forget()
        self.scan_e1.pack(side=tk.LEFT, padx=1)
        self.scan_e2.pack(side=tk.LEFT, padx=1)
        if t in ("Angle", "Dihedral"):
            self.scan_e3.pack(side=tk.LEFT, padx=1)
        if t == "Dihedral":
            self.scan_e4.pack(side=tk.LEFT, padx=1)

    def _refresh_job_hint(self) -> None:
        m = {
            "Single point": "Command: xtb input.xyz --chrg … --uhf … --gfn …",
            "Geometry optimization": "Command: xtb input.xyz --opt …",
            "Frequencies (Hessian)": "Command: xtb input.xyz --hess …",
            "Optimization + frequencies": "Command: xtb input.xyz --ohess …",
            "Relaxed PES scan": "Uses xcontrol.inp: $constrain + $scan (relaxed scan)",
            "Constrained optimization": "Uses xcontrol.inp with frozen distances/angles/dihedrals",
        }
        self.job_hint.config(text=m.get(self.xtb_job_choice.get(), ""))

    def _add_constraint_row(self) -> None:
        row_f = ttk.Frame(self.const_rows_frame)
        row_f.pack(fill=tk.X, pady=2)
        cv = {
            "type": tk.StringVar(value="Bond"),
            "a1": tk.StringVar(),
            "a2": tk.StringVar(),
            "a3": tk.StringVar(),
            "a4": tk.StringVar(),
            "frame": row_f,
        }
        cb = ttk.Combobox(row_f, textvariable=cv["type"], values=["Bond", "Angle", "Dihedral"], state="readonly", width=8)
        cb.pack(side=tk.LEFT, padx=2)
        atom_f = ttk.Frame(row_f)
        atom_f.pack(side=tk.LEFT)
        e1 = ttk.Entry(atom_f, textvariable=cv["a1"], width=4)
        e2 = ttk.Entry(atom_f, textvariable=cv["a2"], width=4)
        e3 = ttk.Entry(atom_f, textvariable=cv["a3"], width=4)
        e4 = ttk.Entry(atom_f, textvariable=cv["a4"], width=4)
        e1.pack(side=tk.LEFT, padx=1)
        e2.pack(side=tk.LEFT, padx=1)

        def _upd(*_a):
            t = cv["type"].get()
            e3.pack_forget()
            e4.pack_forget()
            if t in ("Angle", "Dihedral"):
                e3.pack(side=tk.LEFT, padx=1)
            if t == "Dihedral":
                e4.pack(side=tk.LEFT, padx=1)

        cb.bind("<<ComboboxSelected>>", _upd)
        _upd()

        def _del():
            row_f.destroy()
            if cv in self.constraint_rows:
                self.constraint_rows.remove(cv)

        ttk.Button(row_f, text="−", width=2, command=_del).pack(side=tk.LEFT, padx=5)
        self.constraint_rows.append(cv)

    def _map_job_choice_to_xtb(self) -> str:
        c = self.xtb_job_choice.get()
        if c == "Single point":
            return "sp"
        if c == "Geometry optimization":
            return "opt"
        if c == "Frequencies (Hessian)":
            return "hess"
        if c == "Optimization + frequencies":
            return "ohess"
        if c == "Relaxed PES scan":
            return "scan"
        return "opt"

    def _build_task_cfg(self) -> dict:
        job = self._map_job_choice_to_xtb()
        cfg: dict = {
            "job": job,
            "constraints": [],
            "scan": None,
            "scan_constraint": None,
        }
        choice = self.xtb_job_choice.get()
        if choice == "Constrained optimization":
            for cv in self.constraint_rows:
                try:
                    c_type = cv["type"].get()
                    if c_type == "Bond":
                        cfg["constraints"].append(
                            f"distance: {int(cv['a1'].get())}, {int(cv['a2'].get())}, auto"
                        )
                    elif c_type == "Angle":
                        cfg["constraints"].append(
                            f"angle: {int(cv['a1'].get())}, {int(cv['a2'].get())}, "
                            f"{int(cv['a3'].get())}, auto"
                        )
                    elif c_type == "Dihedral":
                        cfg["constraints"].append(
                            f"dihedral: {int(cv['a1'].get())}, {int(cv['a2'].get())}, "
                            f"{int(cv['a3'].get())}, {int(cv['a4'].get())}, auto"
                        )
                except Exception:
                    pass
        if choice == "Relaxed PES scan":
            try:
                sc_type = self.scan_ctype.get()
                sc_start = float(self.scan_start.get())
                sc_end = float(self.scan_end.get())
                sc_steps = int(self.scan_steps.get())
                a1, a2 = int(self.scan_a1.get()), int(self.scan_a2.get())
                sc_constraint = ""
                if sc_type == "Bond":
                    sc_constraint = f"distance: {a1},{a2},{sc_start}"
                elif sc_type == "Angle":
                    sc_constraint = (
                        f"angle: {a1},{a2},{int(self.scan_a3.get())},{sc_start}"
                    )
                elif sc_type == "Dihedral":
                    sc_constraint = (
                        f"dihedral: {a1},{a2},{int(self.scan_a3.get())},"
                        f"{int(self.scan_a4.get())},{sc_start}"
                    )
                if sc_constraint:
                    cfg["scan_constraint"] = sc_constraint
                    cfg["scan"] = {"start": sc_start, "end": sc_end, "steps": sc_steps}
            except Exception:
                pass
        return cfg

    def _build_scan_meta_from_ui(self) -> dict | None:
        if self.xtb_job_choice.get() != "Relaxed PES scan":
            return None
        try:
            ctype = self.scan_ctype.get()
            meta = {
                "ctype": ctype,
                "a1": int(self.scan_a1.get()),
                "a2": int(self.scan_a2.get()),
                "a3": int(self.scan_a3.get()) if ctype in ("Angle", "Dihedral") else None,
                "a4": int(self.scan_a4.get()) if ctype == "Dihedral" else None,
                "start": float(self.scan_start.get()),
                "end": float(self.scan_end.get()),
                "steps": int(self.scan_steps.get()),
            }
            return meta
        except Exception:
            return None

    def xcontrol_text(self) -> str:
        content, _ = xtb_support.format_xcontrol_content(self._build_task_cfg())
        return content

    def _load_xyz(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("XYZ", "*.xyz"), ("All", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                self.geom_txt.delete("1.0", tk.END)
                self.geom_txt.insert("1.0", f.read())
        except Exception as e:
            messagebox.showerror("Load XYZ", str(e))

    def _save_xyz(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".xyz", filetypes=[("XYZ", "*.xyz")])
        if not path:
            return
        try:
            n = xtb_support.write_input_xyz(path, self.geom_txt.get("1.0", tk.END))
            messagebox.showinfo("Saved", f"Wrote {n} atoms to {path}")
        except Exception as e:
            messagebox.showerror("Save XYZ", str(e))

    def _save_xcontrol_dialog(self) -> None:
        txt = self.xcontrol_text()
        if not txt.strip():
            messagebox.showinfo("xcontrol.inp", "No constraints/scan for this job — file would be empty.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".inp", initialfile="xcontrol.inp")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(txt)
            messagebox.showinfo("Saved", path)
        except Exception as e:
            messagebox.showerror("Save", str(e))

    def _save_run_script_dialog(self) -> None:
        exe = xtb_support.find_xtb_exe(self.xtb_version_choice.get())
        if not exe:
            messagebox.showerror("xTB", "Bundled g-xTB executable was not found under external_modules/xtb/g-xtb.")
            return
        try:
            chrg = int(self.charge.get())
            uhf = int(self.mult.get()) - 1
        except ValueError:
            messagebox.showwarning("xTB", "Charge and multiplicity must be integers.")
            return
        cfg = self._build_task_cfg()
        use_xc = bool(self.xcontrol_text().strip())
        args = xtb_support.build_xtb_argv(
            cfg["job"], self.xtb_opt_level.get().strip(), self.xtb_gfn_level.get().strip(), chrg, uhf, use_xc
        )
        arg_str = " ".join(f'"{a}"' if " " in str(a) else str(a) for a in args)
        if os.name == "nt":
            body = (
                "@echo off\n"
                "cd /d \"%~dp0\"\n"
                f"\"{exe}\" input.xyz {arg_str} > res.out\n"
                "pause\n"
            )
            path = filedialog.asksaveasfilename(defaultextension=".bat", filetypes=[("Batch", "*.bat")])
        else:
            body = (
                "#!/usr/bin/env bash\nset -e\ncd \"$(dirname \"$0\")\"\n"
                f"\"{exe}\" input.xyz {arg_str} > res.out\n"
            )
            path = filedialog.asksaveasfilename(defaultextension=".sh", filetypes=[("Shell", "*.sh")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            if os.name != "nt":
                os.chmod(path, 0o755)
            messagebox.showinfo("Saved", f"{path}\n\nPlace input.xyz (and xcontrol.inp if used) in the same folder.")
        except Exception as e:
            messagebox.showerror("Save", str(e))

    def _start_run(self) -> None:
        geom = self.geom_txt.get("1.0", tk.END).strip()
        if not geom:
            messagebox.showwarning("xTB", "Geometry is empty.")
            return
        try:
            chrg = int(self.charge.get())
            uhf = int(self.mult.get()) - 1
            if int(self.mult.get()) < 1:
                raise ValueError
        except ValueError:
            messagebox.showwarning("xTB", "Invalid charge or multiplicity.")
            return
        exe = xtb_support.find_xtb_exe(self.xtb_version_choice.get())
        if not exe:
            messagebox.showerror(
                "xTB not found",
                "Could not locate xtb.\n\n"
                "Expected the bundled g-xTB executable under external_modules/xtb/g-xtb.",
            )
            return
        cfg = self._build_task_cfg()
        job = cfg["job"]
        if not messagebox.askyesno(
            "Run xTB",
            f"Run GFN{self.xtb_gfn_level.get()}-xTB ({job})?\n\n"
            "xTB is semiempirical — for screening / pre-optimization only.",
        ):
            return

        self.btn_run.config(state=tk.DISABLED)
        self.btn_crest.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_vis.config(state=tk.DISABLED)
        self.btn_graph.config(state=tk.DISABLED)
        self.folder_var.set("Running... output will appear in project external_modules/xtb/xtb_runs/ folder.")
        self.log_txt.delete("1.0", tk.END)
        self.geom_out_txt.delete("1.0", tk.END)
        self.num_txt.config(state=tk.NORMAL)
        self.num_txt.delete("1.0", tk.END)
        self.num_txt.insert("1.0", "Computing... values will appear here after run.")
        self.num_txt.config(state=tk.DISABLED)
        self.log_txt.insert(tk.END, f"Starting GFN{self.xtb_gfn_level.get()}-xTB ({job})…\n")

        self.xtb_queue = queue.Queue()
        self._set_last_run_state(engine="xtb", folder=None, job=job, is_scan=False)
        self._last_xtb_scan_meta = self._build_scan_meta_from_ui()
        self._last_xtb_geom_text = geom
        for w in self.scan_plot_host.winfo_children():
            w.destroy()
        self.scan_plot_title.config(text="Scan graph will appear here after run.")
        opt = self.xtb_opt_level.get().strip()
        gfn = self.xtb_gfn_level.get().strip()

        def work():
            suite_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            work_parent = os.path.join(suite_root, "external_modules", "xtb", "xtb_runs")
            xtb_support.xtb_thread_worker(
                self.xtb_queue,
                self.xtb_process_holder,
                exe,
                geom,
                chrg,
                uhf,
                opt,
                gfn,
                "gfn2",
                cfg,
                work_parent_dir=work_parent,
            )

        threading.Thread(target=work, daemon=True).start()
        self._poll_queue()


    def _poll_queue(self) -> None:
        if not self.xtb_queue:
            return
        while not self.xtb_queue.empty():
            msg_type, content = self.xtb_queue.get_nowait()
            if msg_type == "log":
                self.log_txt.insert(tk.END, content)
                self.log_txt.see(tk.END)
            elif msg_type == "error":
                self.log_txt.insert(tk.END, f"\n[ERROR] {content}\n")
            elif msg_type == "result":
                self.geom_out_txt.insert(tk.END, content)
            elif msg_type == "done":
                self.btn_run.config(state=tk.NORMAL)
                self.btn_stop.config(state=tk.DISABLED)
                self.xtb_queue = None
                if isinstance(content, dict):
                    engine = content.get("engine", "xtb")
                    folder = content.get("folder")
                    is_scan = bool(content.get("is_scan", False))
                    job = content.get("job", "") or ""
                    self._set_last_run_state(engine=engine, folder=folder, job=job, is_scan=is_scan)
                    self.btn_vis.config(state=tk.NORMAL if self._last_run_folder else tk.DISABLED)
                    if self._last_run_folder:
                        self.folder_var.set(f"Last run folder ({self._last_run_engine}):\n{self._last_run_folder}")
                    if self._last_run_engine == "xtb" and self._last_xtb_is_scan:
                        self.btn_graph.config(state=tk.NORMAL)
                    if self._last_run_folder and self._last_run_engine == "xtb":
                        self._append_log_summary()
                        self._root.after(400, self._open_chemcraft_log)
                    elif self._last_run_folder and self._last_run_engine == "crest":
                        self._append_crest_summary()
                        if self._last_run_folder:
                            self.btn_graph.config(state=tk.NORMAL)
                return
        self._root.after(100, self._poll_queue)

    def _stop_run(self) -> None:
        p = self.xtb_process_holder[0]
        if p:
            try:
                p.terminate()
                label = self._last_run_engine.upper() if self._last_run_engine else "JOB"
                self.log_txt.insert(tk.END, f"\n[{label} stopped by user]\n")
            except Exception:
                pass

    def _append_log_summary(self) -> None:
        folder = self._last_xtb_run_folder
        if not folder:
            return
        log_path = None
        for name in ("xtb_full.log", "res.out"):
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                log_path = p
                break
        if not log_path:
            return
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                full_text = f.read()
        except Exception:
            return
        lines = full_text.splitlines()
        sections = []
        for marker in ("TOTAL ENERGY", "HOMO-LUMO GAP", "GRADIENT NORM", "Frequency Printout"):
            for i, line in enumerate(lines):
                if marker in line:
                    start = max(0, i - 1)
                    end = min(len(lines), i + 4)
                    sections.append("\n".join(lines[start:end]))
                    break
        if sections:
            self.num_txt.config(state=tk.NORMAL)
            self.num_txt.delete("1.0", tk.END)
            self.num_txt.insert(tk.END, "──── Key results ────\n\n" + "\n\n".join(sections) + "\n")
            self.num_txt.config(state=tk.DISABLED)

    def _append_crest_summary(self) -> None:
        folder = self._last_run_folder
        if not folder:
            return

        def count_structures(path: str) -> int:
            count = 0
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    while True:
                        header = f.readline()
                        if not header:
                            break
                        header = header.strip()
                        if not header:
                            continue
                        try:
                            n_atoms = int(header)
                        except ValueError:
                            continue
                        f.readline()
                        for _ in range(n_atoms):
                            if not f.readline():
                                break
                        count += 1
            except Exception:
                return 0
            return count

        best_xyz = os.path.join(folder, "crest_best.xyz")
        conformers_xyz = os.path.join(folder, "crest_conformers.xyz")
        rotamers_xyz = os.path.join(folder, "crest_rotamers.xyz")
        energies = os.path.join(folder, "crest.energies")
        eh_list = xtb_support.collect_crest_energies_hartree(folder)
        csv_path = xtb_support.write_crest_conformers_csv(folder, eh_list) if eh_list else None
        summary_lines = [
            "CREST summary",
            "",
            f"Mode: {self._last_run_job or self.crest_mode.get()}",
            f"Best structure written: {'yes' if os.path.isfile(best_xyz) else 'no'}",
            f"Unique conformers: {count_structures(conformers_xyz) if os.path.isfile(conformers_xyz) else 0}",
            f"Rotamers: {count_structures(rotamers_xyz) if os.path.isfile(rotamers_xyz) else 0}",
            f"Energy table: {'found' if os.path.isfile(energies) else 'not found'}",
        ]
        if csv_path:
            summary_lines.append(f"Energy CSV: {csv_path}")
        elif eh_list is None:
            summary_lines.append("Energy CSV: (could not build — need crest_conformers.xyz or crest.energies + anchor XYZ)")
        self.num_txt.config(state=tk.NORMAL)
        self.num_txt.delete("1.0", tk.END)
        self.num_txt.insert(tk.END, "\n".join(summary_lines) + "\n")
        self.num_txt.config(state=tk.DISABLED)
        if eh_list and len(eh_list) >= 1:
            self._plot_crest_energies(eh_list)

    def _visualize_last(self) -> None:
        folder = self._last_run_folder
        if not folder:
            messagebox.showinfo("Viewer", "No run folder yet.")
            return
        names = ("xtbopt.xyz", "xtblast.xyz", "input.xyz")
        if self._last_run_engine == "crest":
            names = ("crest_best.xyz", "crest_conformers.xyz", "crest_rotamers.xyz", "input.xyz")
        for name in names:
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                self._open_file_external(p)
                return
        messagebox.showinfo("Viewer", "No XYZ found in the last run folder.")

    def _open_xyz_external(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("XYZ", "*.xyz"), ("All", "*.*")])
        if path:
            self._open_file_external(path)

    def _open_file_external(self, path: str) -> None:
        choice = self.external_viewer.get()
        try:
            if choice == "Default app":
                if os.name == "nt":
                    os.startfile(path)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
                return
            if choice == "Chemcraft":
                exe = xtb_support.find_chemcraft_exe()
                if exe:
                    subprocess.Popen([exe, path])
                else:
                    messagebox.showwarning("Chemcraft", "Chemcraft.exe not found.")
                return
            if choice == "Jmol":
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                jmol_jar = os.path.join(base, "jmol-16.3.55", "Jmol.jar")
                if os.path.isfile(jmol_jar):
                    subprocess.Popen(["java", "-jar", jmol_jar, path])
                    return
                env_jar = (os.environ.get("JMOL_JAR") or "").strip()
                if env_jar and os.path.isfile(env_jar):
                    subprocess.Popen(["java", "-jar", env_jar, path])
                    return
                jmol_exe = shutil.which("jmol") or shutil.which("Jmol")
                if jmol_exe:
                    subprocess.Popen([jmol_exe, path])
                else:
                    messagebox.showwarning("Jmol", "Jmol not found.")
        except Exception as e:
            messagebox.showerror("Open", str(e))

    def _open_chemcraft_log(self) -> None:
        folder = self._last_xtb_run_folder
        if not folder:
            return
        exe = xtb_support.find_chemcraft_exe()
        if not exe:
            return
        is_scan = self._last_xtb_is_scan
        job = self._last_xtb_job or ""
        candidates = []
        if is_scan:
            candidates.append("xtbscan.log")
        if job in ("opt", "ohess", "scan"):
            candidates.append("xtbopt.log")
        candidates.extend(["res.out", "xtb_full.log", "xtbopt.log", "xtbscan.log"])
        seen = set()
        for name in candidates:
            if name in seen:
                continue
            seen.add(name)
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                try:
                    subprocess.Popen([exe, p])
                except Exception:
                    pass
                return

    def _plot_crest_energies(self, energies: list[float]) -> None:
        msg = xtb_support.embed_crest_energy_bar_chart(self.scan_plot_host, energies)
        if msg:
            self.scan_plot_title.config(text=msg)
        else:
            self.scan_plot_title.config(text="CREST relative energies (kcal/mol)")
        self._root.after(200, self._balance_scan_plot_pane)

    def _scan_graph(self) -> None:
        if getattr(self, "_last_run_engine", "") == "crest":
            folder = self._last_run_folder
            if not folder:
                messagebox.showinfo("CREST", "No CREST run folder.")
                return
            eh = xtb_support.collect_crest_energies_hartree(folder)
            if not eh:
                messagebox.showinfo("CREST", "No CREST energies found (need crest_conformers.xyz or crest.energies).")
                return
            self._plot_crest_energies(eh)
            return

        folder = self._last_xtb_run_folder
        if not folder:
            return
        scan_log = os.path.join(folder, "xtbscan.log")
        if not os.path.isfile(scan_log):
            messagebox.showinfo("Scan", "xtbscan.log not found.")
            return
        try:
            energies = xtb_support.parse_xtbscan_energies(scan_log)
        except Exception as e:
            messagebox.showerror("Scan", str(e))
            return
        if len(energies) < 2:
            messagebox.showinfo("Scan", "Not enough scan points.")
            return
        try:
            import matplotlib

            matplotlib.use("TkAgg")
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
        except ImportError:
            messagebox.showerror("Scan", "Install matplotlib: pip install matplotlib")
            return
        scan_meta = self._last_xtb_scan_meta
        x_vals = None
        x_label = "Scan Point Index"
        title = "xTB Relaxed PES Scan"
        if scan_meta:
            try:
                x_vals = xtb_support.build_scan_axis_values(
                    float(scan_meta["start"]),
                    float(scan_meta["end"]),
                    int(scan_meta["steps"]),
                )
                x_label, title = xtb_support.build_scan_coordinate_label(
                    scan_meta, self._last_xtb_geom_text or self.geom_txt.get("1.0", tk.END)
                )
            except Exception:
                x_vals = None
        if not x_vals or len(x_vals) != len(energies):
            x_vals = list(range(1, len(energies) + 1))
            if scan_meta:
                x_label = "Scan Point Index (fallback)"

        rel_vals, y_label = xtb_support.convert_relative_energies(energies, self.xtb_energy_unit.get())
        if len(rel_vals) < 2:
            messagebox.showinfo("Scan", "Not enough scan points.")
            return

        fig = Figure(figsize=(8, 4.8), dpi=100)
        ax = fig.add_subplot(111)
        ax.plot(x_vals, rel_vals, "o-", color="#0969da")
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        fig.subplots_adjust(bottom=0.24, left=0.10, right=0.98, top=0.90)

        for w in self.scan_plot_host.winfo_children():
            w.destroy()
        self.scan_plot_title.config(text=f"Scan graph ({self.xtb_energy_unit.get()})")
        canvas = FigureCanvasTkAgg(fig, master=self.scan_plot_host)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def _show_citation(self) -> None:
        papers = {
            "GFN2-xTB": "https://doi.org/10.1021/acs.jctc.8b01176",
            "WIREs": "https://doi.org/10.1002/wcms.1493",
            "GFN1-xTB": "https://doi.org/10.1021/acs.jctc.7b00118",
        }
        win = tk.Toplevel(self._root)
        win.title("xTB — citations")
        win.geometry("560x300")
        ttk.Label(win, text="xTB (S. Grimme et al.)", font=("Segoe UI", 12, "bold")).pack(pady=12)
        for label, url in papers.items():
            row = ttk.Frame(win)
            row.pack(fill=tk.X, padx=16, pady=4)
            ttk.Label(row, text=label).pack(side=tk.LEFT)
            ttk.Button(row, text="Open", command=lambda u=url: webbrowser.open(u)).pack(side=tk.RIGHT)


    def _xtb_job_history_json_path(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "AutoChemy_User_Data", "standalone_xtb_job_history.json")

    def _load_xtb_job_history(self):
        self.xtb_job_history = []
        path = self._xtb_job_history_json_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.xtb_job_history = __import__('json').load(f)
            except Exception:
                pass

    def _save_xtb_job_history(self):
        path = self._xtb_job_history_json_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                __import__('json').dump(self.xtb_job_history, f, indent=4)
        except Exception:
            pass

    def _show_xtb_history_dialog(self):
        if not hasattr(self, "xtb_job_history"):
            self._load_xtb_job_history()
        win = tk.Toplevel(self.parent_frame)
        win.title("Standalone xTB Job History")
        win.geometry("600x300")
        tree = ttk.Treeview(win, columns=("date", "job", "status"), show="headings")
        tree.heading("date", text="Date")
        tree.heading("job", text="Job Type")
        tree.heading("status", text="Status")
        tree.pack(fill=tk.BOTH, expand=True)
        for job in self.xtb_job_history:
            tree.insert("", tk.END, values=(job.get("date", ""), job.get("job", ""), job.get("status", "")))
        
        def _open_folder():
            sel = tree.selection()
            if not sel: return
            idx = tree.index(sel[0])
            job = self.xtb_job_history[idx]
            folder = job.get("folder_path")
            if folder and os.path.exists(folder):
                import subprocess, sys
                if sys.platform == "win32":
                    os.startfile(folder)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", folder])
                else:
                    subprocess.Popen(["xdg-open", folder])
            else:
                __import__('tkinter.messagebox').showinfo("History", "Folder not found.")

        btn = ttk.Button(win, text="Open Folder", command=_open_folder)
        btn.pack(pady=10)

    def _open_output_folder(self) -> None:
        folder = self._last_run_folder
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("Folder", "No output folder yet.")
            return
        try:
            if os.name == "nt":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showerror("Folder", str(e))

    def _open_full_log(self) -> None:
        folder = self._last_run_folder
        if not folder:
            messagebox.showinfo("Log", "No run yet.")
            return
        log_path = None
        names = ("xtb_full.log", "res.out")
        if self._last_run_engine == "crest":
            names = ("crest.out", "xtb_full.log", "res.out")
        for name in names:
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                log_path = p
                break
        if not log_path:
            messagebox.showinfo("Log", "No log file found.")
            return
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("Log", str(e))
            return
        win = tk.Toplevel(self._root)
        title = "CREST full log" if self._last_run_engine == "crest" else "xTB full log"
        win.title(title)
        win.geometry("900x650")
        txt = tk.Text(win, font=("Consolas", 10), wrap=tk.NONE)
        sy = ttk.Scrollbar(win, orient=tk.VERTICAL, command=txt.yview)
        sx = ttk.Scrollbar(win, orient=tk.HORIZONTAL, command=txt.xview)
        txt.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        sx.pack(side=tk.BOTTOM, fill=tk.X)
        txt.pack(fill=tk.BOTH, expand=True)
        txt.insert("1.0", content)
        txt.config(state=tk.DISABLED)

    def _use_output_geometry(self) -> None:
        block = self.geom_out_txt.get("1.0", tk.END).strip()
        if not block:
            messagebox.showinfo("xTB", "No geometry in the output geometry pane.")
            return
        lines = block.split("\n")
        raw_atoms = []
        for line in lines[2:]:
            if line.strip():
                raw_atoms.append(line)
        if not raw_atoms:
            messagebox.showinfo("xTB", "Could not parse XYZ from output pane (need standard XYZ block).")
            return
        self.geom_txt.delete("1.0", tk.END)
        self.geom_txt.insert("1.0", "\n".join(raw_atoms) + "\n")
        messagebox.showinfo("xTB", "Geometry text updated from output XYZ block.")
