"""
Conformational Sampling module.
Provides a separate module for conformational workflows such as CREST.
"""

from __future__ import annotations

import csv
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from modules import app_theme
except ImportError:
    app_theme = None  # type: ignore

from modules.base_module import BaseModule
from modules import xtb_support
from modules.orca_parser import ORCAParser


class ConformationalSamplingModule(BaseModule):
    """Separate module for conformational sampling workflows."""

    def __init__(self, parent_frame):
        super().__init__(parent_frame)
        self._root = parent_frame.winfo_toplevel()
        self.process_holder: list = [None]
        self.run_queue = None
        self._active_view = "home"
        self._last_run_folder = None
        self._last_run_job = ""
        self._last_geom_text = ""
        self._orca_inp_template: str | None = None

    def get_name(self) -> str:
        return "Conformational Sampling"

    def get_icon(self) -> str:
        return "🧭"

    def create_ui(self) -> None:
        self.main_frame = ttk.Frame(self.parent_frame, padding=10)

        header = ttk.Frame(self.main_frame)
        header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(header, text="Conformational Sampling", font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        self.back_btn = ttk.Button(header, text="← Back to options", command=self._show_home)
        self.back_btn.pack(side=tk.RIGHT)

        self.view_host = ttk.Frame(self.main_frame)
        self.view_host.pack(fill=tk.BOTH, expand=True)

        self.home_frame = ttk.Frame(self.view_host)
        self.crest_frame = ttk.Frame(self.view_host)

        self._build_home()
        self._build_crest_view()
        self._show_home()

    def _build_home(self) -> None:
        hero = ttk.LabelFrame(self.home_frame, text="Choose a workflow", padding=16)
        hero.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            hero,
            text="Open a conformational sampling workflow.",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        ttk.Label(
            hero,
            text="Pick CREST for conformer/rotamer sampling.",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(0, 18))

        cards = ttk.Frame(hero)
        cards.pack(fill=tk.BOTH, expand=True)



        crest_card = ttk.LabelFrame(cards, text="CREST", padding=16)
        crest_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        ttk.Label(
            crest_card,
            text="Conformer-rotamer ensemble sampling with a full visual runner and results panel.",
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 12))
        ttk.Button(crest_card, text="Open CREST", command=self._show_crest).pack(anchor="w")

    def _build_crest_view(self) -> None:
        top = ttk.Frame(self.crest_frame)
        top.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(top, text="CREST Conformational Sampling", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)

        outer = ttk.PanedWindow(self.crest_frame, orient=tk.HORIZONTAL)
        outer.pack(fill=tk.BOTH, expand=True)

        left_host = ttk.Frame(outer, padding=(0, 0, 8, 0))
        outer.add(left_host, weight=2)
        right = ttk.Frame(outer, padding=(8, 0, 0, 0))
        outer.add(right, weight=3)

        # Scrollable left column so all controls are reachable on smaller screens
        left_canvas = tk.Canvas(left_host, highlightthickness=0)
        left_scroll = ttk.Scrollbar(left_host, orient=tk.VERTICAL, command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        left = ttk.Frame(left_canvas)
        left_canvas_window = left_canvas.create_window((0, 0), window=left, anchor="nw")

        def _sync_left_width(event):
            left_canvas.itemconfigure(left_canvas_window, width=event.width)

        def _sync_left_scroll(_event=None):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))

        left_canvas.bind("<Configure>", _sync_left_width)
        left.bind("<Configure>", _sync_left_scroll)

        def _on_mousewheel(event):
            delta = -1 * int(event.delta / 120) if event.delta else 0
            if delta:
                left_canvas.yview_scroll(delta, "units")

        left_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        gf = ttk.LabelFrame(left, text="Geometry (XYZ - atom lines or full XYZ)")
        gf.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        cm = ttk.Frame(gf)
        cm.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self.loaded_file_var = tk.StringVar(value="No structure loaded")
        ttk.Label(cm, textvariable=self.loaded_file_var, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(cm, text="Load structure...", command=self._load_structure_file).pack(side=tk.LEFT)

        cm2 = ttk.Frame(gf)
        cm2.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self.charge = tk.StringVar(value="0")
        self.mult = tk.StringVar(value="1")
        ttk.Label(cm2, text="Charge:").pack(side=tk.LEFT)
        ttk.Entry(cm2, textvariable=self.charge, width=6).pack(side=tk.LEFT, padx=(2, 12))
        ttk.Label(cm2, text="Mult:").pack(side=tk.LEFT)
        ttk.Entry(cm2, textvariable=self.mult, width=6).pack(side=tk.LEFT, padx=(2, 0))

        geo_btns = ttk.Frame(gf)
        geo_btns.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Button(geo_btns, text="Load XYZ...", command=self._load_xyz).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(geo_btns, text="Save XYZ...", command=self._save_xyz).pack(side=tk.LEFT)

        self.geom_txt = tk.Text(gf, height=14, wrap=tk.NONE, font=("Consolas", 10))
        gy = ttk.Scrollbar(gf, orient=tk.VERTICAL, command=self.geom_txt.yview)
        gx = ttk.Scrollbar(gf, orient=tk.HORIZONTAL, command=self.geom_txt.xview)
        self.geom_txt.configure(yscrollcommand=gy.set, xscrollcommand=gx.set)
        self.geom_txt.grid(row=3, column=0, sticky="nsew")
        gy.grid(row=3, column=1, sticky="ns")
        gx.grid(row=4, column=0, sticky="ew")
        gf.grid_rowconfigure(3, weight=1)
        gf.grid_columnconfigure(0, weight=1)

        preopt = ttk.LabelFrame(left, text="Pre-optimization (optional)")
        preopt.pack(fill=tk.X, pady=(0, 8))
        self.preopt_var = tk.BooleanVar(value=False)
        self.preopt_gfn = tk.StringVar(value="2")
        self.preopt_opt_level = tk.StringVar(value="normal")
        ttk.Checkbutton(
            preopt,
            text="Optimize geometry with xTB before CREST",
            variable=self.preopt_var,
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=4, pady=2)
        ttk.Label(preopt, text="xTB GFN:").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        ttk.Combobox(
            preopt,
            textvariable=self.preopt_gfn,
            values=["2", "1"],
            state="readonly",
            width=8,
        ).grid(row=1, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(preopt, text="Opt level:").grid(row=1, column=2, sticky="w", padx=4, pady=2)
        ttk.Combobox(
            preopt,
            textvariable=self.preopt_opt_level,
            values=["normal", "tight", "vtight", "loose", "vloose", "extreme"],
            state="readonly",
            width=10,
        ).grid(row=1, column=3, sticky="w", padx=4, pady=2)

        cf = ttk.LabelFrame(left, text="CREST Settings")
        cf.pack(fill=tk.X, pady=(0, 8))
        self.crest_mode = tk.StringVar(value="Conformer search")
        self.crest_gfn_level = tk.StringVar(value="2")
        self.crest_ewin = tk.StringVar(value="6.0")
        self.crest_temp = tk.StringVar(value="298.15")
        self.crest_threads = tk.StringVar(value="4")
        self.crest_solvent = tk.StringVar(value="")
        self.crest_solvent_model = tk.StringVar(value="ALPB")
        self.crest_extra_args = tk.StringVar(value="")

        ttk.Label(cf, text="Mode:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        ttk.Combobox(
            cf,
            textvariable=self.crest_mode,
            values=[
                "Conformer search",
                "Quick search",
                "Entropy workflow",
                "Protonation screening",
                "Deprotonation screening",
                "Tautomer screening",
                "NCI complexes",
                "Nanoreactor setup",
            ],
            state="readonly",
            width=18,
        ).grid(row=0, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(cf, text="GFN:").grid(row=0, column=2, sticky="w", padx=4, pady=2)
        ttk.Combobox(
            cf,
            textvariable=self.crest_gfn_level,
            values=["2", "1"],
            state="readonly",
            width=8,
        ).grid(row=0, column=3, sticky="w", padx=4, pady=2)
        ttk.Label(cf, text="Ewin (kcal/mol):").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(cf, textvariable=self.crest_ewin, width=10).grid(row=1, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(cf, text="Temp (K):").grid(row=1, column=2, sticky="w", padx=4, pady=2)
        ttk.Entry(cf, textvariable=self.crest_temp, width=10).grid(row=1, column=3, sticky="w", padx=4, pady=2)
        ttk.Label(cf, text="Threads:").grid(row=2, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(cf, textvariable=self.crest_threads, width=10).grid(row=2, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(cf, text="Solvent model:").grid(row=2, column=2, sticky="w", padx=4, pady=2)
        ttk.Combobox(
            cf,
            textvariable=self.crest_solvent_model,
            values=["None", "ALPB", "GBSA"],
            state="readonly",
            width=8,
        ).grid(row=2, column=3, sticky="w", padx=4, pady=2)
        ttk.Label(cf, text="Solvent:").grid(row=3, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(cf, textvariable=self.crest_solvent, width=12).grid(row=3, column=1, sticky="w", padx=4, pady=2)
        self.nanoreactor_density = tk.StringVar(value="1.0")
        self.nanoreactor_length = tk.StringVar(value="5.0")
        ttk.Label(cf, text="Nanoreactor density:").grid(row=3, column=2, sticky="w", padx=4, pady=2)
        ttk.Entry(cf, textvariable=self.nanoreactor_density, width=8).grid(row=3, column=3, sticky="w", padx=4, pady=2)
        ttk.Label(cf, text="Nanoreactor length (ps):").grid(row=4, column=2, sticky="w", padx=4, pady=2)
        ttk.Entry(cf, textvariable=self.nanoreactor_length, width=8).grid(row=4, column=3, sticky="w", padx=4, pady=2)
        ttk.Label(cf, text="Extra args:").grid(row=4, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(cf, textvariable=self.crest_extra_args, width=42).grid(
            row=5, column=0, columnspan=4, sticky="ew", padx=4, pady=2
        )
        self.crest_settings_summary = ttk.Label(cf, text="", font=("Segoe UI", 9))
        self.crest_settings_summary.grid(row=6, column=0, columnspan=4, sticky="w", padx=4, pady=(4, 2))
        for idx in range(4):
            cf.columnconfigure(idx, weight=1 if idx in (1, 3) else 0)
        def _update_crest_preview(*_args):
            self._refresh_crest_settings_summary()
            try:
                from modules.xtb_support import build_crest_argv
                mode = self.crest_mode.get().strip()
                gfn = self.crest_gfn_level.get().strip()
                try: chrg = int(self.charge.get() or 0)
                except ValueError: chrg = 0
                try: uhf = int(self.mult.get() or 1) - 1
                except ValueError: uhf = 0
                try: ewin = float(self.crest_ewin.get() or 6.0)
                except ValueError: ewin = 6.0
                try: temp = float(self.crest_temp.get() or 298.15)
                except ValueError: temp = 298.15
                try: threads = int(self.crest_threads.get() or 4)
                except ValueError: threads = 4
                solvent = self.crest_solvent.get().strip()
                solvent_model = self.crest_solvent_model.get().strip()
                extra_args = self.crest_extra_args.get().strip()
                
                argv = build_crest_argv(
                    mode=mode, gfn=gfn, chrg=chrg, uhf=uhf, ewin=ewin,
                    temp=temp, threads=threads, solvent=solvent,
                    solvent_model=solvent_model, extra_args=extra_args
                )
                cmd_str = "crest input.xyz " + " ".join(argv)
                
                # Check for nanoreactor
                if mode == "Nanoreactor":
                    cmd_str += f" -nanoreactor {self.nanoreactor_density.get()} {self.nanoreactor_length.get()}"
                
                self.cmd_preview_txt.config(state=tk.NORMAL)
                self.cmd_preview_txt.delete("1.0", tk.END)
                self.cmd_preview_txt.insert("1.0", cmd_str)
                self.cmd_preview_txt.config(state=tk.DISABLED)
            except Exception as e:
                pass

        for var in (
            self.crest_mode,
            self.crest_gfn_level,
            self.crest_ewin,
            self.crest_temp,
            self.crest_threads,
            self.crest_solvent,
            self.crest_solvent_model,
            self.crest_extra_args,
            self.charge,
            self.mult,
            self.nanoreactor_density,
            self.nanoreactor_length
        ):
            var.trace_add("write", _update_crest_preview)

        wsl_f = ttk.Frame(left)
        wsl_f.pack(fill=tk.X, pady=(0, 8))
        import sys
        if sys.platform == "win32":
            ttk.Label(
                wsl_f,
                text="ℹ Windows Note: CREST requires a Linux environment. You can install WSL (Windows Subsystem for Linux)\nby running 'wsl --install' in PowerShell, then compile or run CREST binaries within WSL.",
                font=("Segoe UI", 8, "italic"),
                foreground="#0284c7"
            ).pack(anchor="w")

        preview_f = ttk.LabelFrame(left, text="CREST Command Preview")
        preview_f.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.cmd_preview_txt = tk.Text(preview_f, height=3, font=("Consolas", 9), wrap=tk.WORD, bg="#f0f0f0")
        self.cmd_preview_txt.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        
        _update_crest_preview()

        runf = ttk.Frame(right)
        runf.pack(fill=tk.X, pady=(0, 8))
        self.btn_run = ttk.Button(runf, text="Run CREST", command=self._start_crest_run)
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
        ttk.Button(runf, text="Open XYZ file...", command=self._open_xyz_external).pack(side=tk.LEFT)

        ttk.Label(right, text="Live Log Stream:", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.log_txt = tk.Text(right, height=11, font=("Consolas", 9), bg="#1e1e1e", fg="#00ff00", wrap=tk.NONE)
        ly = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=ly.set)
        self.log_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ly.pack(side=tk.RIGHT, fill=tk.Y)

        bottom = ttk.Frame(self.crest_frame)
        bottom.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        left_bottom = ttk.Frame(bottom)
        left_bottom.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        right_bottom = ttk.Frame(bottom)
        right_bottom.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))

        post_box = ttk.LabelFrame(left_bottom, text="Post-processing", padding=(6, 6))
        post_box.pack(fill=tk.X, pady=(0, 6))
        self.post_dedupe_mode = tk.StringVar(value="cregen")
        self.post_split_var = tk.BooleanVar(value=True)
        self.post_orca_var = tk.BooleanVar(value=False)
        self.post_csv_var = tk.BooleanVar(value=True)
        self.post_energy_window = tk.StringVar(value="6.0")
        ttk.Label(
            post_box,
            text="Deduplicate pool → crest_ensemble.xyz:",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(0, 2))
        dedupe_row = ttk.Frame(post_box)
        dedupe_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Combobox(
            dedupe_row,
            textvariable=self.post_dedupe_mode,
            values=("cregen", "python", "none"),
            state="readonly",
            width=28,
        ).pack(side=tk.LEFT)
        ttk.Label(
            post_box,
            text=(
                "cregen = runs CREST as crest ref.xyz --cregen crest_rotamers.xyz (ref = crest_best.xyz, "
                "input.xyz, or first rotamer frame); python = Kabsch RMSD (0.125 Å) + ΔE window, no crest; none = skip."
            ),
            font=("Segoe UI", 8),
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 2))
        ttk.Checkbutton(post_box, text="Split ensemble into individual XYZ files", variable=self.post_split_var).pack(
            anchor="w"
        )
        ttk.Checkbutton(post_box, text="Generate ORCA inputs per conformer", variable=self.post_orca_var).pack(
            anchor="w"
        )
        ttk.Checkbutton(post_box, text="Create CSV of conformer energies", variable=self.post_csv_var).pack(
            anchor="w"
        )
        ew_row = ttk.Frame(post_box)
        ew_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(ew_row, text="Energy window (kcal/mol):").pack(side=tk.LEFT)
        ttk.Entry(ew_row, textvariable=self.post_energy_window, width=8).pack(side=tk.LEFT, padx=(4, 0))

        orca_box = ttk.LabelFrame(left_bottom, text="ORCA input settings", padding=(6, 6))
        orca_box.pack(fill=tk.X, pady=(0, 6))
        self.orca_method = tk.StringVar(value="B3LYP")
        self.orca_basis = tk.StringVar(value="def2-SVP")
        self.orca_job = tk.StringVar(value="Opt")
        self.orca_extra = tk.StringVar(value="")
        ttk.Label(orca_box, text="Method:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(orca_box, textvariable=self.orca_method, width=12).grid(row=0, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(orca_box, text="Basis:").grid(row=0, column=2, sticky="w", padx=4, pady=2)
        ttk.Entry(orca_box, textvariable=self.orca_basis, width=12).grid(row=0, column=3, sticky="w", padx=4, pady=2)
        ttk.Label(orca_box, text="Job:").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(orca_box, textvariable=self.orca_job, width=12).grid(row=1, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(orca_box, text="Extra keywords:").grid(row=1, column=2, sticky="w", padx=4, pady=2)
        ttk.Entry(orca_box, textvariable=self.orca_extra, width=20).grid(row=1, column=3, sticky="w", padx=4, pady=2)
        for idx in range(4):
            orca_box.columnconfigure(idx, weight=1 if idx in (1, 3) else 0)

        tpl_row = ttk.Frame(orca_box)
        tpl_row.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Button(tpl_row, text="Import template from Input Creator…", command=self._import_template_from_ic5).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(tpl_row, text="Open Input Creator", command=self._switch_to_input_creator_5).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(tpl_row, text="Clear template", command=self._clear_orca_template).pack(side=tk.LEFT)
        self.orca_template_status = ttk.Label(
            orca_box,
            text="ORCA .inp mode: simple line from Method / Basis / Job above (or import a full template from Input Creator).",
            font=("Segoe UI", 8),
            wraplength=520,
            justify=tk.LEFT,
        )
        self.orca_template_status.grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))

        num_box = ttk.LabelFrame(left_bottom, text="Summary", padding=(6, 6))
        num_box.pack(fill=tk.BOTH, expand=True, pady=(0, 6))
        self.num_txt = tk.Text(num_box, height=10, font=("Consolas", 9), wrap=tk.NONE)
        self.num_txt.pack(fill=tk.BOTH, expand=True)
        self.num_txt.insert("1.0", "CREST summary will appear here after a run.\n")
        self.num_txt.config(state=tk.DISABLED)

        out_box = ttk.LabelFrame(right_bottom, text="Output Geometry / Ensemble Preview", padding=(6, 6))
        out_box.pack(fill=tk.BOTH, expand=True, pady=(0, 6))
        self.geom_out_txt = tk.Text(out_box, height=8, font=("Consolas", 10), wrap=tk.NONE)
        self.geom_out_txt.pack(fill=tk.BOTH, expand=True)

        plot_box = ttk.LabelFrame(right_bottom, text="CREST relative energies", padding=(6, 6))
        plot_box.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.crest_plot_title = ttk.Label(
            plot_box,
            text="After CREST: sorted energies, E_min (Eh), Boltzmann % @ 298 K, and rank vs CREST file index.",
            font=("Segoe UI", 9, "italic"),
        )
        self.crest_plot_title.pack(anchor="w", pady=(0, 4))
        self.crest_plot_host = ttk.Frame(plot_box)
        self.crest_plot_host.pack(fill=tk.BOTH, expand=True)

        bf = ttk.Frame(self.crest_frame)
        bf.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bf, text="Use output XYZ in geometry", command=self._use_output_geometry).pack(side=tk.LEFT, padx=(0, 6))
        self.btn_vis = ttk.Button(bf, text="Visualize last result", command=self._visualize_last, state=tk.DISABLED)
        self.btn_vis.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bf, text="Full log window", command=self._open_full_log).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bf, text="Output folder", command=self._open_output_folder).pack(side=tk.LEFT)

        self.folder_var = tk.StringVar(value="Last run folder: (none yet - runs use project-local crest_runs/)")
        ttk.Label(self.crest_frame, textvariable=self.folder_var, font=("Consolas", 8), wraplength=900, justify=tk.LEFT).pack(
            fill=tk.X, pady=(8, 0), anchor="w"
        )

    def get_session_state(self):
        var_names = (
            "loaded_file_var",
            "charge",
            "mult",
            "preopt_var",
            "preopt_gfn",
            "preopt_opt_level",
            "crest_mode",
            "crest_gfn_level",
            "crest_ewin",
            "crest_temp",
            "crest_threads",
            "crest_solvent",
            "crest_solvent_model",
            "crest_extra_args",
            "nanoreactor_density",
            "nanoreactor_length",
            "external_viewer",
            "post_dedupe_mode",
            "post_split_var",
            "post_orca_var",
            "post_csv_var",
            "post_energy_window",
            "orca_method",
            "orca_basis",
            "orca_job",
            "orca_extra",
        )
        vars_blob = {}
        for name in var_names:
            var_obj = getattr(self, name, None)
            if var_obj is not None and hasattr(var_obj, "get"):
                vars_blob[name] = var_obj.get()
        return {
            "version": 1,
            "active_view": self._active_view,
            "vars": vars_blob,
            "texts": {
                "geom_txt": self.geom_txt.get("1.0", "end-1c"),
                "num_txt": self.num_txt.get("1.0", "end-1c"),
                "geom_out_txt": self.geom_out_txt.get("1.0", "end-1c"),
                "log_txt_tail": self.log_txt.get("1.0", "end-1c")[-50000:],
            },
            "last_run": {
                "folder": self._last_run_folder,
                "job": self._last_run_job,
                "geom_text": self._last_geom_text,
                "folder_label": self.folder_var.get() if hasattr(self, "folder_var") else "",
            },
            "orca_template": self._orca_inp_template,
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

        texts = state.get("texts") or {}
        self._restore_text_widget(self.geom_txt, texts.get("geom_txt", ""))
        self._restore_text_widget(self.num_txt, texts.get("num_txt", ""))
        self._restore_text_widget(self.geom_out_txt, texts.get("geom_out_txt", ""))
        self._restore_text_widget(self.log_txt, texts.get("log_txt_tail", ""))

        last_run = state.get("last_run") or {}
        self._last_run_folder = last_run.get("folder")
        self._last_run_job = last_run.get("job", "")
        self._last_geom_text = last_run.get("geom_text", "")
        self._orca_inp_template = state.get("orca_template")

        if self._orca_inp_template:
            self.orca_template_status.config(
                text="ORCA .inp mode: full template from Input Creator - geometry is replaced for each conformer; charge/multiplicity use the values in this CREST panel."
            )
        else:
            self.orca_template_status.config(
                text="ORCA .inp mode: simple line from Method / Basis / Job above (or import a full template from Input Creator)."
            )

        if hasattr(self, "folder_var"):
            self.folder_var.set(
                last_run.get("folder_label") or "Last run folder: (none yet - runs use project-local crest_runs/)"
            )
        if hasattr(self, "btn_vis"):
            self.btn_vis.config(state=tk.NORMAL if self._last_run_folder else tk.DISABLED)

        self._refresh_crest_settings_summary()
        if state.get("active_view") == "crest":
            self._show_crest()
        else:
            self._show_home()

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

    def _show_home(self) -> None:
        self._active_view = "home"
        self.crest_frame.pack_forget()
        self.home_frame.pack(fill=tk.BOTH, expand=True)
        self.back_btn.config(state=tk.DISABLED)

    def _show_crest(self) -> None:
        self._active_view = "crest"
        self.home_frame.pack_forget()
        self.crest_frame.pack(fill=tk.BOTH, expand=True)
        self.back_btn.config(state=tk.NORMAL)



    def _refresh_crest_settings_summary(self) -> None:
        solvent_model = self.crest_solvent_model.get().strip()
        solvent = self.crest_solvent.get().strip() or "gas phase"
        self.crest_settings_summary.config(
            text=(
                f"{self.crest_mode.get()} | GFN{self.crest_gfn_level.get()} | "
                f"ewin {self.crest_ewin.get()} | {self.crest_temp.get()} K | "
                f"{self.crest_threads.get()} threads | {solvent_model} {solvent}"
            )
        )

    def apply_app_theme(self, ctx) -> None:
        if not app_theme:
            return
        for w in (
            getattr(self, "geom_txt", None),
            getattr(self, "log_txt", None),
            getattr(self, "num_txt", None),
            getattr(self, "geom_out_txt", None),
        ):
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

    def _switch_to_input_creator_5(self) -> None:
        app = getattr(self._root, "_orca_app", None)
        if app and hasattr(app, "_switch_module"):
            app._switch_module("Input Creator")
            return
        messagebox.showerror("Navigation", "Could not open Input Creator from the main window.")

    def _import_template_from_ic5(self) -> None:
        app = getattr(self._root, "_orca_app", None)
        if not app or not hasattr(app, "modules"):
            messagebox.showerror("Import", "Could not reach the main application.")
            return
        ic5 = app.modules.get("Input Creator")
        if ic5 is None or not hasattr(ic5, "txt_inp"):
            messagebox.showerror("Import", "Input Creator is not available.")
            return
        raw = ic5.txt_inp.get("1.0", tk.END).strip()
        if not raw or not re.search(r"\*\s*xyz\b", raw, re.IGNORECASE):
            messagebox.showwarning(
                "Import",
                "Input Creator preview is empty or has no ORCA coordinate block.\n\n"
                "Open Input Creator, set up your calculation, then click **Generate Preview** so the "
                "Input (.inp) tab is filled — then import again.",
            )
            return
        self._orca_inp_template = raw
        self.orca_template_status.config(
            text="ORCA .inp mode: full template from Input Creator — geometry is replaced for each conformer; "
            "charge/multiplicity use the values in this CREST panel."
        )
        messagebox.showinfo(
            "Import",
            "Template loaded from Input Creator.\n\n"
            "Enable “Generate ORCA inputs per conformer” in post-processing after CREST finishes.",
        )

    def _clear_orca_template(self) -> None:
        self._orca_inp_template = None
        self.orca_template_status.config(
            text="ORCA .inp mode: simple line from Method / Basis / Job above (or import a full template from Input Creator)."
        )

    def _apply_geometry_to_orca_template(self, template: str, atom_lines: list[str]) -> str:
        ch, mult = self.charge.get().strip(), self.mult.get().strip()
        lines = template.splitlines()
        out: list[str] = []
        i = 0
        replaced = False
        while i < len(lines):
            stripped = lines[i].strip()
            low = stripped.lower()
            if low.startswith("* xyz") or low.startswith("*xyz"):
                replaced = True
                out.append(f"* xyz {ch} {mult}")
                i += 1
                while i < len(lines) and lines[i].strip() != "*":
                    i += 1
                out.extend(atom_lines)
                out.append("*")
                while i < len(lines) and lines[i].strip() != "*":
                    i += 1
                if i < len(lines) and lines[i].strip() == "*":
                    i += 1
                continue
            out.append(lines[i])
            i += 1
        if not replaced:
            body = "\n".join(out).rstrip()
            return body + "\n\n" + f"* xyz {ch} {mult}\n" + "\n".join(atom_lines) + "\n*\n"
        return "\n".join(out).rstrip() + "\n"

    def _build_crest_cfg(self) -> dict:
        solvent_model = self.crest_solvent_model.get().strip().lower()
        solvent_val = self.crest_solvent.get().strip()
        if solvent_model in ("none", ""):
            solvent_val = ""
        extra_args = self.crest_extra_args.get().strip()
        mode = self.crest_mode.get().strip()
        mode_flag = ""
        if mode == "Protonation screening":
            mode_flag = "--protonate"
        elif mode == "Deprotonation screening":
            mode_flag = "--deprotonate"
        elif mode == "Tautomer screening":
            mode_flag = "--tautomerize"
        elif mode == "NCI complexes":
            mode_flag = "--nci"
        elif mode == "Nanoreactor setup":
            dens = self.nanoreactor_density.get().strip()
            length = self.nanoreactor_length.get().strip()
            mode_flag = f"--reactor --density {dens} --length {length}"
        if mode_flag:
            extra_args = f"{mode_flag} {extra_args}".strip()
        return {
            "mode": mode,
            "gfn": self.crest_gfn_level.get().strip(),
            "ewin": float(self.crest_ewin.get()),
            "temp": float(self.crest_temp.get()),
            "threads": int(self.crest_threads.get()),
            "solvent": solvent_val,
            "solvent_model": solvent_model,
            "extra_args": extra_args,
        }

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

    def _load_structure_file(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[
                ("Structure files", "*.xyz;*.out;*.log;*.gjf;*.com"),
                ("XYZ", "*.xyz"),
                ("Output", "*.out;*.log"),
                ("All", "*.*"),
            ]
        )
        if not path:
            return
        self.loaded_file_var.set(path)
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".xyz":
                with open(path, encoding="utf-8", errors="replace") as f:
                    self.geom_txt.delete("1.0", tk.END)
                    self.geom_txt.insert("1.0", f.read())
                return
            if ext in (".out", ".log"):
                geom = self._try_orca_geometry(path)
                if geom:
                    self._set_geom_from_list(geom)
                    return
                geom = self._try_gaussian_geometry(path)
                if geom:
                    self._set_geom_from_list(geom)
                    return
                messagebox.showwarning(
                    "Load structure",
                    "Could not detect optimized geometry in this file. Please load an XYZ file.",
                )
                return
            if ext in (".gjf", ".com"):
                geom = self._try_gaussian_geometry(path)
                if geom:
                    self._set_geom_from_list(geom)
                    return
            messagebox.showwarning("Load structure", "Unsupported file type.")
        except Exception as e:
            messagebox.showerror("Load structure", str(e))

    def _try_orca_geometry(self, path: str):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except Exception:
            return None

        patterns = [
            "CARTESIAN COORDINATES",
            "FINAL GEOMETRY",
            "Coordinates (Angstroms)",
        ]
        start_idx = -1
        for i in range(len(lines) - 1, -1, -1):
            if any(p.lower() in lines[i].lower() for p in patterns):
                start_idx = i
                break
        if start_idx < 0:
            try:
                parser = ORCAParser(path)
                geom = parser.get_geometry()
                if geom:
                    return [(g.atom, g.x, g.y, g.z) for g in geom]
            except Exception:
                return None
            return None

        atoms = []
        for line in lines[start_idx + 1 :]:
            line_s = line.strip()
            if not line_s:
                if atoms:
                    break
                continue
            if line_s.startswith("-") or line_s.startswith("="):
                if atoms:
                    break
                continue
            parts = line_s.split()
            if len(parts) < 4:
                if atoms:
                    break
                continue
            try:
                atom = parts[0]
                x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
            except Exception:
                if atoms:
                    break
                continue
            atoms.append((atom, x, y, z))
        return atoms if atoms else None

    def _try_gaussian_geometry(self, path: str):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except Exception:
            return None

        def find_last_orientation(tag: str):
            for i in range(len(lines) - 1, -1, -1):
                if tag in lines[i]:
                    return i
            return -1

        start = find_last_orientation("Standard orientation")
        if start < 0:
            start = find_last_orientation("Input orientation")
        if start < 0:
            return None

        idx = start + 5
        atoms = []
        while idx < len(lines):
            line = lines[idx].strip()
            if not line or line.startswith("-----"):
                if atoms:
                    break
                idx += 1
                continue
            parts = line.split()
            if len(parts) < 6:
                break
            try:
                atomic_num = int(parts[1])
                x, y, z = float(parts[3]), float(parts[4]), float(parts[5])
            except Exception:
                break
            symbol = self._atomic_number_to_symbol(atomic_num)
            atoms.append((symbol, x, y, z))
            idx += 1
        return atoms if atoms else None

    def _atomic_number_to_symbol(self, num: int) -> str:
        table = [
            "?",
            "H", "He",
            "Li", "Be", "B", "C", "N", "O", "F", "Ne",
            "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
            "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
            "Ga", "Ge", "As", "Se", "Br", "Kr",
        ]
        if 0 < num < len(table):
            return table[num]
        return "X"

    def _set_geom_from_list(self, atoms):
        lines = [f"{sym} {x:.6f} {y:.6f} {z:.6f}" for sym, x, y, z in atoms]
        self.geom_txt.delete("1.0", tk.END)
        self.geom_txt.insert("1.0", "\n".join(lines) + "\n")

    def _save_xyz(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".xyz", filetypes=[("XYZ", "*.xyz")])
        if not path:
            return
        try:
            n = xtb_support.write_input_xyz(path, self.geom_txt.get("1.0", tk.END))
            messagebox.showinfo("Saved", f"Wrote {n} atoms to {path}")
        except Exception as e:
            messagebox.showerror("Save XYZ", str(e))

    def _start_crest_run(self) -> None:
        geom = self.geom_txt.get("1.0", tk.END).strip()
        if not geom:
            messagebox.showwarning("CREST", "Geometry is empty.")
            return
        try:
            chrg = int(self.charge.get())
            uhf = int(self.mult.get()) - 1
            if int(self.mult.get()) < 1:
                raise ValueError
            cfg = self._build_crest_cfg()
        except ValueError:
            messagebox.showwarning(
                "CREST",
                "Charge, multiplicity, ewin, temperature, and threads must be valid numbers.",
            )
            return

        crest_exe = xtb_support.find_crest_exe()
        if not crest_exe:
            messagebox.showerror(
                "CREST not found",
                "Could not locate a compiled crest executable.\n\n"
                "I found the CREST source folder in this project, but not crest.exe.\n"
                "Build/install CREST first, then set CREST_EXE, add it to PATH, or place crest.exe in the project root.",
            )
            return

        if not messagebox.askyesno(
            "Run CREST",
            f"Run CREST in '{cfg['mode']}' mode?",
        ):
            return

        self.btn_run.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_vis.config(state=tk.DISABLED)
        self.folder_var.set("Running CREST... output will appear in project crest_runs/ folder.")
        self.log_txt.delete("1.0", tk.END)
        self.geom_out_txt.delete("1.0", tk.END)
        self.num_txt.config(state=tk.NORMAL)
        self.num_txt.delete("1.0", tk.END)
        self.num_txt.insert("1.0", "CREST is running... summary will appear here after completion.")
        self.num_txt.config(state=tk.DISABLED)
        self.log_txt.insert(tk.END, f"Starting CREST ({cfg['mode']}, GFN{cfg['gfn']})...\n")

        self.run_queue = queue.Queue()
        self._last_run_folder = None
        self._last_run_job = cfg["mode"]
        self._last_geom_text = geom
        self._crest_exe = crest_exe
        xtb_exe = xtb_support.find_xtb_exe()

        def work():
            geom_use = geom
            if self.preopt_var.get():
                opt_geom = self._run_xtb_preopt_sync(
                    geom=geom_use,
                    chrg=chrg,
                    uhf=uhf,
                    opt_level=self.preopt_opt_level.get().strip(),
                    gfn=self.preopt_gfn.get().strip(),
                )
                if opt_geom:
                    geom_use = opt_geom
                    self.run_queue.put(("log", "\n[xTB pre-opt] Using optimized geometry for CREST.\n"))
                else:
                    self.run_queue.put(("error", "xTB pre-optimization failed. Aborting CREST run."))
                    self.run_queue.put(("done", {"folder": None, "engine": "crest", "job": cfg["mode"]}))
                    return
            suite_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            work_parent = os.path.join(suite_root, "crest_runs")
            xtb_support.crest_thread_worker(
                self.run_queue,
                self.process_holder,
                crest_exe,
                geom_use,
                chrg,
                uhf,
                cfg,
                work_parent_dir=work_parent,
                xtb_exe=xtb_exe,
            )

        threading.Thread(target=work, daemon=True).start()
        self._poll_queue()

    def _poll_queue(self) -> None:
        if not self.run_queue:
            return
        while not self.run_queue.empty():
            msg_type, content = self.run_queue.get_nowait()
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
                self.run_queue = None
                if isinstance(content, dict):
                    self._last_run_folder = content.get("folder")
                    self._last_run_job = content.get("job", "") or self._last_run_job
                    self.btn_vis.config(state=tk.NORMAL if self._last_run_folder else tk.DISABLED)
                    if self._last_run_folder:
                        self.folder_var.set(f"Last run folder:\n{self._last_run_folder}")
                        self._append_crest_summary()
                        if (
                            (self.post_dedupe_mode.get() != "none")
                            or self.post_split_var.get()
                            or self.post_orca_var.get()
                            or self.post_csv_var.get()
                        ):
                            threading.Thread(
                                target=self._postprocess_after_crest,
                                args=(self._last_run_folder,),
                                daemon=True,
                            ).start()
                return
        self._root.after(100, self._poll_queue)

    def _stop_run(self) -> None:
        p = self.process_holder[0]
        if p:
            try:
                p.terminate()
                self.log_txt.insert(tk.END, "\n[CREST stopped by user]\n")
            except Exception:
                pass

    def _run_xtb_preopt_sync(self, geom: str, chrg: int, uhf: int, opt_level: str, gfn: str) -> str | None:
        xtb_exe = xtb_support.find_xtb_exe()
        if not xtb_exe:
            self.run_queue.put(("error", "xTB executable not found for pre-optimization."))
            return None
        work_parent = xtb_support.default_xtb_work_parent()
        os.makedirs(work_parent, exist_ok=True)
        preopt_dir = os.path.join(work_parent, f"xtb_preopt_{os.getpid()}")
        os.makedirs(preopt_dir, exist_ok=True)
        input_xyz_path = os.path.join(preopt_dir, "input.xyz")
        try:
            xtb_support.write_input_xyz(input_xyz_path, geom)
        except Exception as e:
            self.run_queue.put(("error", f"Failed to write xTB input.xyz: {e}"))
            return None

        args = xtb_support.build_xtb_argv("opt", opt_level, gfn, chrg, uhf, False)
        cmd = [xtb_exe, "input.xyz"] + args
        cmd_display = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        self.run_queue.put(("log", f"\n[xTB pre-opt] Working dir: {preopt_dir}\n$ {cmd_display}\n"))
        res_out = os.path.join(preopt_dir, "res.out")
        try:
            with open(res_out, "w", encoding="utf-8", errors="replace") as res_fh:
                proc = subprocess.Popen(
                    cmd,
                    cwd=preopt_dir,
                    stdout=res_fh,
                    stderr=subprocess.STDOUT,
                )
                proc.wait()
        except Exception as e:
            self.run_queue.put(("error", f"xTB pre-opt failed: {e}"))
            return None

        opt_xyz = os.path.join(preopt_dir, "xtbopt.xyz")
        if not os.path.isfile(opt_xyz):
            self.run_queue.put(("error", "xTB pre-opt did not produce xtbopt.xyz."))
            return None
        try:
            with open(opt_xyz, encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            self.run_queue.put(("error", f"Failed to read xtbopt.xyz: {e}"))
            return None

    def _postprocess_after_crest(self, folder: str) -> None:
        if self._last_run_job not in ("Conformer search", "Quick search", "Entropy workflow"):
            self._ui_log(
                f"\n[Post] Skipping CREGEN/splitting because workflow '{self._last_run_job}' is not a conformer search.\n"
            )
            return
        try:
            ewin_kcal = float(self.post_energy_window.get())
        except Exception:
            ewin_kcal = 6.0

        ensemble_path = os.path.join(folder, "crest_ensemble.xyz")
        energies_path = os.path.join(folder, "crest.energies")

        mode = (self.post_dedupe_mode.get() or "cregen").strip().lower()
        if mode == "cregen":
            self._ui_log("\n[Post] Running CREST CREGEN on crest_rotamers.xyz...\n")
            self._run_cregen(folder, ewin_kcal)
            if not os.path.isfile(ensemble_path):
                self._ui_log("\n[Post] CREGEN did not produce crest_ensemble.xyz — falling back to built-in RMSD dedup.\n")
                self._run_python_dedupe_ensemble(folder, ewin_kcal)
        elif mode == "python":
            self._ui_log("\n[Post] Built-in RMSD deduplication (no CREGEN / no crest call)...\n")
            self._run_python_dedupe_ensemble(folder, ewin_kcal)

        # Use fallback if dedup didn't produce the ensemble
        if not os.path.isfile(ensemble_path):
            fallback = os.path.join(folder, "crest_conformers.xyz")
            rotamer_fallback = os.path.join(folder, "crest_rotamers.xyz")
            if os.path.isfile(fallback):
                shutil.copy2(fallback, ensemble_path)
            elif os.path.isfile(rotamer_fallback):
                shutil.copy2(rotamer_fallback, ensemble_path)

        if self.post_split_var.get() or self.post_orca_var.get() or self.post_csv_var.get():
            if not os.path.isfile(ensemble_path):
                self._ui_error("crest_ensemble.xyz not found. Skipping post-processing.")
                return
            confs = self._read_multi_xyz(ensemble_path)
            if not confs:
                self._ui_error("Could not parse crest_ensemble.xyz.")
                return

            # Strictly use energies from the ensemble's comments
            energies = self._energies_from_comments(confs)

            rel_kcal = self._relative_kcal(energies) if energies else []
            chosen_indices = list(range(len(confs)))
            if rel_kcal:
                chosen_indices = [i for i, v in enumerate(rel_kcal) if v <= ewin_kcal]

            if self.post_split_var.get():
                self._write_conformer_xyz(folder, confs, energies, chosen_indices)

            if self.post_orca_var.get():
                self._write_orca_inputs(folder, confs, energies, chosen_indices)

            if self.post_csv_var.get():
                self._write_crest_csv(folder, energies)

    def _resolve_dedupe_pool_path(self, folder: str) -> str | None:
        """Multi-structure XYZ used as rotamer pool: prefer rotamers, else ensemble, else clustered."""
        rot = os.path.join(folder, "crest_rotamers.xyz")
        if os.path.isfile(rot):
            return rot
        ens = os.path.join(folder, "crest_ensemble.xyz")
        if os.path.isfile(ens):
            return ens
        clu = os.path.join(folder, "crest_clustered.xyz")
        if os.path.isfile(clu):
            return clu
        return None

    def _run_python_dedupe_ensemble(self, folder: str, ewin_kcal: float) -> None:
        pool = self._resolve_dedupe_pool_path(folder)
        if not pool:
            self._ui_error(
                "Built-in dedup: need crest_rotamers.xyz, crest_ensemble.xyz, or crest_clustered.xyz."
            )
            return
        out = os.path.join(folder, "crest_ensemble.xyz")
        n_in, n_out, err = xtb_support.dedupe_xyz_file_to_ensemble(pool, out, ewin_kcal, rmsd_threshold=0.125)
        if err:
            self._ui_error(f"Built-in dedup: {err}")
            return
        self._ui_log(
            f"\n[Post] Built-in dedup finished: {n_in} frames read → {n_out} kept "
            f"(ΔE ≤ {ewin_kcal} kcal/mol, RMSD ≥ 0.125 Å vs kept set).\n"
            f"         Wrote {out}\n"
        )

    def _run_cregen(self, folder: str, ewin_kcal: float) -> None:
        crest_exe = self._crest_exe or xtb_support.find_crest_exe()
        rotamers = os.path.join(folder, "crest_rotamers.xyz")
        ensemble_xyz = os.path.join(folder, "crest_ensemble.xyz")
        clustered = os.path.join(folder, "crest_clustered.xyz")

        # CREST 3.x often finishes without crest_rotamers.xyz on disk, while crest_ensemble.xyz
        # still holds the *full* optimization pool (hundreds of frames). CREGEN must read a
        # multi-structure file; without crest_rotamers.xyz we used to skip CREGEN and wrongly
        # kept that huge file as "the ensemble" for ORCA export.
        if not os.path.isfile(rotamers):
            staged_from = None
            if os.path.isfile(ensemble_xyz):
                staged_from = ensemble_xyz
            elif os.path.isfile(clustered):
                staged_from = clustered
            if staged_from:
                try:
                    shutil.copy2(staged_from, rotamers)
                    self._ui_log(
                        "\n[Post] crest_rotamers.xyz was missing (typical for CREST 3.x). "
                        f"Copied {os.path.basename(staged_from)} → crest_rotamers.xyz as the "
                        "structure pool for CREGEN deduplication.\n"
                    )
                except OSError as e:
                    self._ui_error(f"Could not create crest_rotamers.xyz for CREGEN: {e}")
                    return
            else:
                self._ui_error(
                    "crest_rotamers.xyz not found and no crest_ensemble.xyz / crest_clustered.xyz "
                    "to stage from — cannot run CREGEN."
                )
                return

        ref = xtb_support.ensure_cregen_reference_basename(folder)
        if not ref:
            self._ui_error(
                "CREGEN needs a reference structure as the first argument "
                "(crest ref.xyz --cregen ensemble.xyz). "
                "Add crest_best.xyz or input.xyz to the run folder, or ensure crest_rotamers.xyz "
                "has at least one frame."
            )
            return

        # Prevent CREST from hanging on a prompt asking to overwrite existing files
        for target in ("crest_ensemble.xyz", "crest_rotamers.xyz.sorted"):
            p = os.path.join(folder, target)
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

        log_out = os.path.join(folder, "sort_rot0_gp.txt")
        if str(crest_exe).startswith("wsl:"):
            wsl_cmd = xtb_support.get_wsl_cmd()
            wsl_folder = xtb_support.windows_to_wsl_path(folder)
            wsl_folder_q = shlex.quote(wsl_folder)
            ref_q = shlex.quote(ref)
            cmd = (
                f"cd {wsl_folder_q} && crest {ref_q} --cregen crest_rotamers.xyz --ewin {ewin_kcal}"
            )
            full_cmd = wsl_cmd + ["sh", "-lc", cmd]
        else:
            full_cmd = [crest_exe, ref, "--cregen", "crest_rotamers.xyz", "--ewin", str(ewin_kcal)]
            
        try:
            popen_kw_base = {
                "cwd": folder, 
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace"
            }
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                popen_kw_base["startupinfo"] = startupinfo

            proc = subprocess.Popen(full_cmd, **popen_kw_base)
            
            with open(log_out, "w", encoding="utf-8", errors="replace") as log_fh:
                if proc.stdout:
                    for line in proc.stdout:
                        self._ui_log(line)
                        log_fh.write(line)
                        log_fh.flush()
            rc = proc.wait()
            if rc != 0:
                self._ui_error(f"CREGEN exited with code {rc}. Check {log_out} and crest output above.")

            # CREGEN writes TWO files (see crest cregen.f90: oname = fname.sorted, cname = crest_ensemble.xyz):
            #   - crest_rotamers.xyz.sorted  → sorted rotamer ensemble
            #   - crest_ensemble.xyz         → unique conformers only (what we want for ORCA / CSV)
            # Do NOT copy .sorted over crest_ensemble.xyz — that was overwriting the conformer file and
            # made ensemble identical to the rotamer list.
            sorted_xyz = os.path.join(folder, "crest_rotamers.xyz.sorted")
            ensemble_xyz = os.path.join(folder, "crest_ensemble.xyz")
            if os.path.isfile(ensemble_xyz):
                self._ui_log(
                    "\n[Post] CREGEN wrote crest_ensemble.xyz (unique conformers). "
                    "Leaving it unchanged (not replacing with .sorted rotamer list).\n"
                )
            elif os.path.isfile(sorted_xyz):
                shutil.copy2(sorted_xyz, ensemble_xyz)
                self._ui_log(
                    "\n[Post] No crest_ensemble.xyz from CREGEN; copied crest_rotamers.xyz.sorted → crest_ensemble.xyz.\n"
                )
            else:
                self._ui_error(
                    "CREGEN finished but neither crest_ensemble.xyz nor crest_rotamers.xyz.sorted was found."
                )

        except Exception as e:
            self._ui_error(f"CREGEN failed: {e}")

    def _read_crest_energies(self, path: str) -> list[float]:
        energies = []
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_s = line.strip()
                    if not line_s:
                        continue
                    parts = re.split(r"\s+", line_s)
                    try:
                        energies.append(float(parts[0]))
                    except Exception:
                        continue
        except Exception:
            return []
        return energies

    def _read_multi_xyz(self, path: str):
        confs = []
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                while True:
                    n_line = f.readline()
                    if not n_line:
                        break
                    n_line = n_line.strip()
                    if not n_line:
                        continue
                    try:
                        n_atoms = int(n_line)
                    except Exception:
                        break
                    comment = f.readline().rstrip("\n")
                    atoms = []
                    for _ in range(n_atoms):
                        line = f.readline()
                        if not line:
                            break
                        atoms.append(line.rstrip("\n"))
                    confs.append((comment, atoms))
        except Exception:
            return []
        return confs

    def _energies_from_comments(self, confs) -> list[float]:
        energies = []
        for comment, _ in confs:
            try:
                energies.append(float(comment.strip().split()[0]))
            except Exception:
                energies.append(0.0)
        return energies

    def _relative_kcal(self, energies: list[float]) -> list[float]:
        if not energies:
            return []
        e_min = min(energies)
        rel = [e - e_min for e in energies]
        return [v * 627.509 for v in rel]

    def _write_conformer_xyz(self, folder: str, confs, energies, indices):
        out_dir = os.path.join(folder, "conformers_xyz")
        os.makedirs(out_dir, exist_ok=True)
        if energies:
            order = sorted(indices, key=lambda i: energies[i])
        else:
            order = indices
        for rank, i in enumerate(order, start=1):
            comment, atoms = confs[i]
            name = f"{rank}_conformer_lowest.xyz" if rank == 1 else f"{rank}_conformer.xyz"
            path = os.path.join(out_dir, name)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"{len(atoms)}\n{comment}\n")
                f.write("\n".join(atoms) + "\n")
        self._ui_log(f"\n[Post] Wrote {len(order)} conformer XYZ files to {out_dir}\n")

    def _write_orca_inputs(self, folder: str, confs, energies, indices):
        out_dir = os.path.join(folder, "orca_inputs")
        os.makedirs(out_dir, exist_ok=True)
        if energies:
            order = sorted(indices, key=lambda i: energies[i])
        else:
            order = indices
        template = getattr(self, "_orca_inp_template", None)
        for rank, i in enumerate(order, start=1):
            _, atoms = confs[i]
            name = f"{rank}_conformer.inp"
            path = os.path.join(out_dir, name)
            atom_lines = list(atoms)
            if template:
                body = self._apply_geometry_to_orca_template(template, atom_lines)
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(body)
                continue
            header = f"! {self.orca_method.get()} {self.orca_basis.get()} {self.orca_job.get()} {self.orca_extra.get()}".strip()
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(header + "\n")
                try:
                    threads_val = int(self.crest_threads.get())
                    if threads_val > 1:
                        f.write(f"%pal nprocs {threads_val} end\n")
                except ValueError:
                    pass
                f.write("\n")
                f.write(f"* xyz {self.charge.get()} {self.mult.get()}\n")
                f.write("\n".join(atom_lines) + "\n")
                f.write("*\n")
        self._ui_log(f"\n[Post] Wrote ORCA inputs to {out_dir}\n")

    def _write_crest_csv(self, folder: str, energies: list[float]):
        if not energies:
            self._ui_error("No energies available for CSV.")
            return
        e_min = min(energies)
        out_path = os.path.join(folder, "crest_conformers.csv")
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            w = csv.writer(f)
            w.writerow(["index", "energy_eh", "rel_eh", "rel_kj_mol", "rel_kcal_mol"])
            for idx, e in enumerate(energies, start=1):
                rel_eh = e - e_min
                w.writerow(
                    [
                        idx,
                        f"{e:.10f}",
                        f"{rel_eh:.10f}",
                        f"{rel_eh * 2625.49962:.6f}",
                        f"{rel_eh * 627.509:.6f}",
                    ]
                )
        self._ui_log(f"\n[Post] Wrote energy CSV to {out_path}\n")

    def _ui_log(self, text: str) -> None:
        def _write():
            try:
                self.log_txt.insert(tk.END, text)
                self.log_txt.see(tk.END)
            except Exception:
                pass
        self._root.after(0, _write)

    def _ui_error(self, text: str) -> None:
        self._ui_log(f"\n[ERROR] {text}\n")

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
        energies_path = os.path.join(folder, "crest.energies")
        eh_list = xtb_support.collect_crest_energies_hartree(folder)
        csv_written = xtb_support.write_crest_conformers_csv(folder, eh_list) if eh_list else None
        summary_lines = [
            "CREST summary",
            "",
            f"Mode: {self._last_run_job or self.crest_mode.get()}",
            f"Best structure written: {'yes' if os.path.isfile(best_xyz) else 'no'}",
            f"Unique conformers: {count_structures(conformers_xyz) if os.path.isfile(conformers_xyz) else 0}",
            f"Rotamers: {count_structures(rotamers_xyz) if os.path.isfile(rotamers_xyz) else 0}",
            f"Energy table: {'found' if os.path.isfile(energies_path) else 'not found'}",
        ]
        if csv_written:
            summary_lines.append(f"Energy CSV: {csv_written}")
        elif eh_list is None:
            summary_lines.append("Energy CSV: (skipped — need conformers/rotamers XYZ or crest.energies + anchor XYZ)")
        self.num_txt.config(state=tk.NORMAL)
        self.num_txt.delete("1.0", tk.END)
        self.num_txt.insert(tk.END, "\n".join(summary_lines) + "\n")
        self.num_txt.config(state=tk.DISABLED)
        if eh_list and getattr(self, "crest_plot_host", None) is not None:
            msg = xtb_support.embed_crest_energy_bar_chart(self.crest_plot_host, eh_list)
            if msg and getattr(self, "crest_plot_title", None) is not None:
                self.crest_plot_title.config(text=msg)
            elif getattr(self, "crest_plot_title", None) is not None:
                self.crest_plot_title.config(
                    text="Plot: stability order, ΔE labels, Boltzmann table (matplotlib) or compact canvas fallback."
                )
            self._root.after(200, lambda: self.crest_plot_host.update_idletasks())

    def _visualize_last(self) -> None:
        folder = self._last_run_folder
        if not folder:
            messagebox.showinfo("Viewer", "No run folder yet.")
            return
        for name in ("crest_best.xyz", "crest_conformers.xyz", "crest_rotamers.xyz", "input.xyz"):
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
        for name in ("crest.out", "xtb_full.log", "res.out"):
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
        win.title("CREST full log")
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
            messagebox.showinfo("CREST", "No geometry in the output pane.")
            return
        lines = block.split("\n")
        raw_atoms = []
        for line in lines[2:]:
            if line.strip():
                raw_atoms.append(line)
        if not raw_atoms:
            messagebox.showinfo("CREST", "Could not parse XYZ from output pane.")
            return
        self.geom_txt.delete("1.0", tk.END)
        self.geom_txt.insert("1.0", "\n".join(raw_atoms) + "\n")
        messagebox.showinfo("CREST", "Geometry text updated from output XYZ block.")
