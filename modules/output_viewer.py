"""
ORCA Output Viewer Module
Parses ORCA output files and presents results as an interactive dashboard:
summary cards, 3D structure, optimization progress, IR / UV-Vis spectra,
orbitals, population analysis, thermochemistry, diagnostics and batch tables.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import glob
import csv
import json
import math
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.orca_parser import (
    ORCAParser, HARTREE_TO_KCAL, HARTREE_TO_KJ, HARTREE_TO_EV,
)
from modules.base_module import BaseModule
from modules import app_theme
from modules.autochemy_viewer import AutoChemyViewer

try:
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    _MPL_OK = True
except Exception:
    _MPL_OK = False


UNITS = {
    "Eh": 1.0,
    "kcal/mol": HARTREE_TO_KCAL,
    "kJ/mol": HARTREE_TO_KJ,
    "eV": HARTREE_TO_EV,
}

CHIP_COLORS = {
    "ok": "#16a34a",
    "bad": "#dc2626",
    "warn": "#d97706",
    "info": "#2563eb",
    "muted": "#64748b",
}

RAW_SECTIONS = [
    ("Input file", "INPUT FILE"),
    ("Final coordinates", "CARTESIAN COORDINATES (ANGSTROEM)"),
    ("SCF convergence", "SCF CONVERGED AFTER"),
    ("Orbital energies", "ORBITAL ENERGIES"),
    ("Mulliken charges", "MULLIKEN ATOMIC CHARGES"),
    ("Loewdin charges", "LOEWDIN ATOMIC CHARGES"),
    ("Mayer analysis", "MAYER POPULATION ANALYSIS"),
    ("Dipole moment", "DIPOLE MOMENT"),
    ("Geometry convergence", "|Geometry convergence|"),
    ("Vibrational frequencies", "VIBRATIONAL FREQUENCIES"),
    ("IR spectrum", "IR SPECTRUM"),
    ("Absorption spectrum", "ABSORPTION SPECTRUM"),
    ("Thermochemistry", "THERMOCHEMISTRY AT"),
    ("Timings / end", "TOTAL RUN TIME"),
]

BATCH_COLUMNS = [
    # key, heading, width
    ("file_name", "File", 200),
    ("status", "Status", 90),
    ("job_type", "Job Type", 150),
    ("final_sp_value", "Final SP (Eh)", 130),
    ("rel_energy_kcal", "ΔE (kcal/mol)", 105),
    ("geo_opt_conv", "Opt Conv", 70),
    ("img_mode", "Imag", 55),
    ("electronic_value", "E(el) (Eh)", 130),
    ("total_enthalpy", "H (Eh)", 130),
    ("final_gibbs_energy", "G (Eh)", 130),
    ("rel_gibbs_kcal", "ΔG (kcal/mol)", 105),
    ("thermal_correction", "G-E(el) (Eh)", 105),
    ("homo_lumo_gap_ev", "Gap (eV)", 80),
    ("charge", "Chg", 45),
    ("mult", "Mult", 45),
    ("time_taken(h)", "Time (h)", 75),
    ("s2_expectation", "<S²>", 75),
    ("s2_ideal", "S² Ideal", 75),
    ("s2_deviation", "S² Dev", 75),
    ("keywords", "Keywords", 260),
    ("path", "Path", 320),
]


def _user_data_path(name):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "AutoChemy_User_Data", name)


def _fmt(value, digits=6, none="—"):
    if value is None:
        return none
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


class _PlotPanel:
    """A themed matplotlib figure with the standard toolbar (zoom / pan / save)."""

    def __init__(self, parent, figsize=(6, 4)):
        self.frame = ttk.Frame(parent)
        self.fig = Figure(figsize=figsize, dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame)
        tb_host = ttk.Frame(self.frame)
        tb_host.pack(side=tk.BOTTOM, fill=tk.X)
        self.toolbar = NavigationToolbar2Tk(self.canvas, tb_host, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side=tk.LEFT)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def begin(self, p):
        self.fig.clear()
        self.fig.set_facecolor(p["bg_main"])
        try:
            self.toolbar.config(background=p["bg_main"])
            for child in self.toolbar.winfo_children():
                try:
                    child.config(background=p["bg_main"])
                except tk.TclError:
                    pass
        except tk.TclError:
            pass

    @staticmethod
    def style_axes(ax, p):
        ax.set_facecolor(p["tree_bg"])
        for spine in ax.spines.values():
            spine.set_color(p["border"])
        ax.tick_params(colors=p["fg"], labelsize=9)
        ax.xaxis.label.set_color(p["fg"])
        ax.yaxis.label.set_color(p["fg"])
        ax.title.set_color(p["fg"])
        ax.grid(True, linestyle="--", alpha=0.3, color=p["fg_muted"])

    def empty(self, p, message):
        self.begin(p)
        ax = self.fig.add_subplot(111)
        self.style_axes(ax, p)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.grid(False)
        ax.text(0.5, 0.5, message, ha="center", va="center", color=p["fg_muted"],
                fontsize=11, transform=ax.transAxes)
        self.canvas.draw_idle()

    def finish(self):
        try:
            self.fig.tight_layout()
        except Exception:
            pass
        self.canvas.draw_idle()


class OutputViewerModule(BaseModule):
    """Module for viewing and analyzing ORCA output files."""

    def __init__(self, parent_frame):
        super().__init__(parent_frame)
        self.parser = None
        self.report = None
        self.current_file = None
        self.batch_data = []
        self.batch_rows = []
        self.root = None
        self._mode = "light"
        self._themed = []
        self._viewers = {}
        self._raw_loaded = False
        self._anim_job = None
        self._traj_job = None
        self._watch_job = None
        self._watch_mtime = None
        self._toast_job = None
        self._batch_sort = (None, False)
        self.recent_files = self._load_recent()

    def get_name(self) -> str:
        return "Output Viewer"

    # ==================================================================
    # Theme helpers
    # ==================================================================
    def _p(self):
        return app_theme.PALETTES.get(self._mode, app_theme.PALETTES["light"])

    def _reg(self, widget, role):
        self._themed.append((widget, role))
        self._color_widget(widget, role)
        return widget

    def _color_widget(self, widget, role):
        p = self._p()
        roles = {
            "card": dict(bg=p["tree_bg"], highlightbackground=p["border"], highlightcolor=p["border"]),
            "card_title": dict(bg=p["tree_bg"], fg=p["fg_muted"]),
            "card_value": dict(bg=p["tree_bg"], fg=p["fg"]),
            "card_sub": dict(bg=p["tree_bg"], fg=p["accent"]),
            "surface": dict(bg=p["bg_main"]),
            "hint": dict(bg=p["bg_main"], fg=p["fg_muted"]),
            "editor": dict(bg=p["editor_bg"], fg=p["editor_fg"], insertbackground=p["editor_insert"],
                           selectbackground=p["editor_select_bg"], selectforeground=p["editor_select_fg"]),
        }
        try:
            widget.configure(**roles.get(role, {}))
        except tk.TclError:
            pass

    def apply_app_theme(self, ctx):
        mode = ctx.get("mode", "light")
        changed = mode != self._mode
        self._mode = mode
        alive = []
        for w, role in self._themed:
            try:
                if w.winfo_exists():
                    self._color_widget(w, role)
                    alive.append((w, role))
            except tk.TclError:
                pass
        self._themed = alive
        p = self._p()
        for name in ("raw_text", "input_text"):
            w = getattr(self, name, None)
            if w is not None:
                app_theme.apply_editor_style(w, ctx)
        self._configure_tags(p)
        if changed and self.report:
            self._rebuild_viewers()
            self._draw_all_plots()
        elif changed:
            self._draw_all_plots()

    def _configure_tags(self, p):
        bad = "#f87171" if self._mode == "dark" else "#b91c1c"
        warn = "#fbbf24" if self._mode == "dark" else "#b45309"
        good = "#4ade80" if self._mode == "dark" else "#15803d"
        for tree in (getattr(self, n, None) for n in ("freq_tree", "batch_tree", "orb_tree", "diag_tree", "opt_tree")):
            if tree is None:
                continue
            tree.tag_configure("bad", foreground=bad)
            tree.tag_configure("warn", foreground=warn)
            tree.tag_configure("good", foreground=good)
            tree.tag_configure("accent", foreground=p["accent"])
        if getattr(self, "raw_text", None) is not None:
            self.raw_text.tag_configure("hit", background="#fde047", foreground="#111827")
            self.raw_text.tag_configure("cur", background="#f97316", foreground="#111827")

    # ==================================================================
    # UI construction
    # ==================================================================
    def create_ui(self):
        """Create the output viewer interface."""
        if self.parent_frame:
            widget = self.parent_frame
            while widget.master:
                widget = widget.master
            self.root = widget
        app = getattr(self.root, "_orca_app", None)
        if app is not None:
            self._mode = getattr(app, "theme_mode", "light")

        self.main_frame = ttk.Frame(self.parent_frame, padding="10")

        self._build_header()
        self._build_chip_bar()

        self.notebook = ttk.Notebook(self.main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        tabs = [
            ("overview_frame", "  Overview  ", self._create_overview_tab),
            ("geometry_frame", "Geometry", self._create_geometry_tab),
            ("opt_frame", "Optimization", self._create_opt_tab),
            ("frequencies_frame", "Frequencies & IR", self._create_frequencies_tab),
            ("thermo_frame", "Thermochemistry", self._create_thermo_tab),
            ("orbital_frame", "Orbitals", self._create_orbital_tab),
            ("charges_frame", "Charges", self._create_charges_tab),
            ("uv_frame", "UV-Vis", self._create_uv_tab),
            ("diag_frame", "Input & Diagnostics", self._create_diag_tab),
            ("batch_frame", "Batch Results", self._create_batch_tab),
            ("raw_frame", "Raw Output", self._create_raw_tab),
        ]
        for attr, title, builder in tabs:
            frame = ttk.Frame(self.notebook, padding=8)
            setattr(self, attr, frame)
            self.notebook.add(frame, text=title)
            builder()

        self._toast_lbl = tk.Label(self.main_frame, text="", font=("Segoe UI", 10, "bold"),
                                   bg="#0f172a", fg="#ffffff", padx=14, pady=6)
        self._configure_tags(self._p())
        self._draw_all_plots()

    def _build_header(self):
        header = ttk.Frame(self.main_frame)
        header.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(header, text="ORCA Output Analyzer", font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT)

        ttk.Button(header, text="📂 Open .out", command=self.open_file).pack(side=tk.LEFT, padx=(15, 4))
        self.recent_btn = ttk.Menubutton(header, text="Recent ▾")
        self.recent_menu = tk.Menu(self.recent_btn, tearoff=0)
        self.recent_btn["menu"] = self.recent_menu
        self.recent_btn.pack(side=tk.LEFT, padx=4)
        self._rebuild_recent_menu()
        ttk.Button(header, text="⟳ Reload", command=self.reload_file).pack(side=tk.LEFT, padx=4)
        ttk.Button(header, text="Batch Process", command=self.open_batch_dialog).pack(side=tk.LEFT, padx=4)

        export_btn = ttk.Menubutton(header, text="Export ▾")
        export_menu = tk.Menu(export_btn, tearoff=0)
        export_menu.add_command(label="Summary report (.md / .txt)…", command=self.export_report)
        export_menu.add_command(label="Final geometry (.xyz)…", command=self.save_geometry_xyz)
        export_menu.add_command(label="Optimization trajectory (.xyz)…", command=self.save_trajectory_xyz)
        export_menu.add_separator()
        export_menu.add_command(label="Frequencies (.csv)…", command=self.export_frequencies_csv)
        export_menu.add_command(label="Charges (.csv)…", command=self.export_charges_csv)
        export_menu.add_command(label="Orbital energies (.csv)…", command=self.export_orbitals_csv)
        export_menu.add_separator()
        export_menu.add_command(label="Send geometry to Input Creator", command=self.send_to_input_creator)
        export_btn["menu"] = export_menu
        export_btn.pack(side=tk.LEFT, padx=4)

        right = ttk.Frame(header)
        right.pack(side=tk.RIGHT)
        self.watch_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text="Live refresh (running job)", variable=self.watch_var,
                        command=self._toggle_watch).pack(side=tk.RIGHT, padx=6)
        ttk.Label(right, text="Units:").pack(side=tk.LEFT, padx=(0, 4))
        self.unit_var = tk.StringVar(value="kcal/mol")
        unit_cb = ttk.Combobox(right, textvariable=self.unit_var, values=list(UNITS.keys()),
                               state="readonly", width=9)
        unit_cb.pack(side=tk.LEFT, padx=(0, 10))
        unit_cb.bind("<<ComboboxSelected>>", lambda _e: self._refresh_unit_views())

        self.file_label = ttk.Label(self.main_frame, text="No file loaded — open an ORCA .out file to begin.",
                                    font=("Segoe UI", 9))
        self.file_label.pack(fill=tk.X, anchor="w")

    def _build_chip_bar(self):
        self.chip_bar = self._reg(tk.Frame(self.main_frame), "surface")
        self.chip_bar.pack(fill=tk.X, pady=(4, 8))

    def _set_chips(self, chips):
        for w in self.chip_bar.winfo_children():
            w.destroy()
        for text, kind in chips:
            tk.Label(self.chip_bar, text=text, bg=CHIP_COLORS.get(kind, CHIP_COLORS["muted"]), fg="#ffffff",
                     font=("Segoe UI", 9, "bold"), padx=10, pady=3).pack(side=tk.LEFT, padx=(0, 6))

    # ---------------- Overview ----------------
    def _create_overview_tab(self):
        pane = ttk.PanedWindow(self.overview_frame, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        self.overview_pane = pane
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=3)
        pane.add(right, weight=2)

        cards = self._reg(tk.Frame(left), "surface")
        cards.pack(fill=tk.X)
        self.cards = {}
        card_defs = [
            ("final_energy", "FINAL SINGLE POINT", "Eh"),
            ("gibbs_energy", "GIBBS FREE ENERGY", "Eh"),
            ("total_enthalpy", "TOTAL ENTHALPY", "Eh"),
            ("gap", "HOMO–LUMO GAP", "eV"),
            ("dipole", "DIPOLE MOMENT", "Debye"),
            ("time", "WALL TIME", "hours"),
        ]
        for idx, (key, title, unit) in enumerate(card_defs):
            card = self._reg(tk.Frame(cards, highlightthickness=1, bd=0, padx=12, pady=8), "card")
            card.grid(row=idx // 3, column=idx % 3, sticky="nsew", padx=4, pady=4)
            cards.grid_columnconfigure(idx % 3, weight=1, uniform="cards")
            self._reg(tk.Label(card, text=title, font=("Segoe UI", 8, "bold"), anchor="w"), "card_title").pack(fill=tk.X)
            val = self._reg(tk.Label(card, text="—", font=("Segoe UI", 15, "bold"), anchor="w", cursor="hand2"),
                            "card_value")
            val.pack(fill=tk.X)
            sub = self._reg(tk.Label(card, text=unit, font=("Segoe UI", 8), anchor="w"), "card_sub")
            sub.pack(fill=tk.X)
            val.bind("<Button-1>", lambda _e, k=key: self._copy_card(k))
            self.cards[key] = (val, sub, unit)

        hdr = ttk.Frame(left)
        hdr.pack(fill=tk.X, pady=(10, 4))
        ttk.Label(hdr, text="Job details", font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(hdr, text="Copy All (Excel)", command=self.copy_summary_data).pack(side=tk.RIGHT, padx=2)
        ttk.Button(hdr, text="Copy Selected", command=self._copy_selected_detail).pack(side=tk.RIGHT, padx=2)

        tf = ttk.Frame(left)
        tf.pack(fill=tk.BOTH, expand=True)
        self.details_tree = ttk.Treeview(tf, columns=("Property", "Value"), show="headings", height=14)
        self.details_tree.heading("Property", text="Property")
        self.details_tree.heading("Value", text="Value")
        self.details_tree.column("Property", width=220, anchor="w")
        self.details_tree.column("Value", width=380, anchor="w")
        sy = ttk.Scrollbar(tf, orient="vertical", command=self.details_tree.yview)
        self.details_tree.configure(yscrollcommand=sy.set)
        self.details_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        self.details_tree.bind("<Double-1>", lambda _e: self._copy_selected_detail())

        ttk.Label(right, text="Final structure", font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=6)
        self.ov_view_host = self._reg(tk.Frame(right, height=360), "surface")
        self.ov_view_host.pack(fill=tk.BOTH, expand=True, padx=6, pady=(4, 0))
        self._placeholder(self.ov_view_host, "No structure loaded")

    # ---------------- Geometry ----------------
    def _create_geometry_tab(self):
        pane = ttk.PanedWindow(self.geometry_frame, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=1)
        pane.add(right, weight=1)

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.geometry_tree = ttk.Treeview(tree_frame, columns=("#", "Atom", "X", "Y", "Z"), show="headings", height=20)
        for col, text, w in (("#", "#", 50), ("Atom", "Atom", 70), ("X", "X (Å)", 120), ("Y", "Y (Å)", 120), ("Z", "Z (Å)", 120)):
            self.geometry_tree.heading(col, text=text)
            self.geometry_tree.column(col, width=w, anchor="e" if col in "XYZ" else "center")
        sy = ttk.Scrollbar(tree_frame, orient="vertical", command=self.geometry_tree.yview)
        self.geometry_tree.configure(yscrollcommand=sy.set)
        self.geometry_tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="right", fill="y")
        self.geometry_tree.bind("<<TreeviewSelect>>", self._on_geometry_select)

        self.geom_info_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.geom_info_var, font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))

        btn_frame = ttk.Frame(left)
        btn_frame.pack(fill=tk.X, pady=6)
        ttk.Button(btn_frame, text="Copy XYZ", command=self.copy_geometry).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Copy CSV", command=self.copy_geometry_csv).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Save .xyz", command=self.save_geometry_xyz).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="→ Input Creator", command=self.send_to_input_creator).pack(side=tk.LEFT, padx=2)

        traj = ttk.LabelFrame(right, text="Optimization trajectory", padding=6)
        traj.pack(fill=tk.X, padx=4)
        self.traj_var = tk.IntVar(value=1)
        self.traj_scale = ttk.Scale(traj, from_=1, to=1, orient=tk.HORIZONTAL,
                                    command=lambda _v: self._on_traj_scale())
        self.traj_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.traj_play_btn = ttk.Button(traj, text="▶ Play", width=8, command=self._toggle_traj_play)
        self.traj_play_btn.pack(side=tk.LEFT)
        self.traj_label = ttk.Label(right, text="Frame — / —", font=("Segoe UI", 9))
        self.traj_label.pack(anchor="w", padx=6, pady=(2, 4))

        self.geo_view_host = self._reg(tk.Frame(right, height=360), "surface")
        self.geo_view_host.pack(fill=tk.BOTH, expand=True, padx=4)
        self._placeholder(self.geo_view_host, "No structure loaded")

    # ---------------- Optimization ----------------
    def _create_opt_tab(self):
        pane = ttk.PanedWindow(self.opt_frame, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)

        cols = ("Cycle", "Energy (Eh)", "ΔE", "RMS grad", "MAX grad", "RMS step", "MAX step")
        tf = ttk.Frame(left)
        tf.pack(fill=tk.BOTH, expand=True)
        self.opt_tree = ttk.Treeview(tf, columns=cols, show="headings", height=20)
        for c in cols:
            self.opt_tree.heading(c, text=c)
            self.opt_tree.column(c, width=60 if c == "Cycle" else 105, anchor="e")
        sy = ttk.Scrollbar(tf, orient="vertical", command=self.opt_tree.yview)
        self.opt_tree.configure(yscrollcommand=sy.set)
        self.opt_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        self.opt_info_var = tk.StringVar(value="Converged criteria are shown in green.")
        ttk.Label(left, textvariable=self.opt_info_var, font=("Segoe UI", 9), wraplength=420).pack(anchor="w", pady=4)

        self.opt_plot = _PlotPanel(right, figsize=(6, 5)) if _MPL_OK else None
        if self.opt_plot:
            self.opt_plot.frame.pack(fill=tk.BOTH, expand=True)

    # ---------------- Frequencies & IR ----------------
    def _create_frequencies_tab(self):
        pane = ttk.PanedWindow(self.frequencies_frame, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.freq_tree = ttk.Treeview(tree_frame, columns=("Mode", "Frequency", "Intensity", "Note"),
                                      show="headings", height=14)
        for c, t, w in (("Mode", "Mode", 60), ("Frequency", "Freq (cm⁻¹)", 110),
                        ("Intensity", "IR Int (km/mol)", 120), ("Note", "Note", 110)):
            self.freq_tree.heading(c, text=t)
            self.freq_tree.column(c, width=w, anchor="e" if c != "Note" else "w")
        sy = ttk.Scrollbar(tree_frame, orient="vertical", command=self.freq_tree.yview)
        self.freq_tree.configure(yscrollcommand=sy.set)
        self.freq_tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="right", fill="y")
        self.freq_tree.bind("<<TreeviewSelect>>", self._on_freq_select)

        anim = ttk.LabelFrame(left, text="Normal-mode animation", padding=6)
        anim.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(anim, text="Amplitude").pack(side=tk.LEFT)
        self.amp_var = tk.DoubleVar(value=0.6)
        ttk.Scale(anim, from_=0.1, to=2.0, variable=self.amp_var, orient=tk.HORIZONTAL, length=110).pack(side=tk.LEFT, padx=4)
        self.anim_btn = ttk.Button(anim, text="▶ Animate", command=self._toggle_mode_anim)
        self.anim_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(anim, text="Save mode .xyz", command=self.save_mode_xyz).pack(side=tk.LEFT, padx=4)

        self.freq_view_host = self._reg(tk.Frame(left, height=260), "surface")
        self.freq_view_host.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self._placeholder(self.freq_view_host, "Select a mode to animate")

        btns = ttk.Frame(left)
        btns.pack(fill=tk.X, pady=4)
        ttk.Button(btns, text="Copy CSV", command=self.copy_frequencies_csv).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="Save spectrum CSV", command=self.export_ir_spectrum_csv).pack(side=tk.LEFT, padx=2)

        ctrl = ttk.Frame(right)
        ctrl.pack(fill=tk.X, pady=(0, 4))
        self.ir_fwhm = tk.DoubleVar(value=20.0)
        self.ir_scale = tk.DoubleVar(value=1.0)
        self.ir_sticks = tk.BooleanVar(value=True)
        self.ir_trans = tk.BooleanVar(value=False)
        ttk.Label(ctrl, text="FWHM (cm⁻¹)").pack(side=tk.LEFT)
        ttk.Spinbox(ctrl, from_=1, to=200, increment=5, textvariable=self.ir_fwhm, width=6,
                    command=self._draw_ir).pack(side=tk.LEFT, padx=(2, 10))
        ttk.Label(ctrl, text="Scale factor").pack(side=tk.LEFT)
        ttk.Spinbox(ctrl, from_=0.80, to=1.10, increment=0.005, textvariable=self.ir_scale, width=7,
                    command=self._draw_ir).pack(side=tk.LEFT, padx=(2, 10))
        ttk.Checkbutton(ctrl, text="Sticks", variable=self.ir_sticks, command=self._draw_ir).pack(side=tk.LEFT)
        ttk.Checkbutton(ctrl, text="Transmittance", variable=self.ir_trans, command=self._draw_ir).pack(side=tk.LEFT, padx=6)
        ttk.Button(ctrl, text="Redraw", command=self._draw_ir).pack(side=tk.LEFT, padx=6)

        self.ir_plot = _PlotPanel(right, figsize=(6, 4.5)) if _MPL_OK else None
        if self.ir_plot:
            self.ir_plot.frame.pack(fill=tk.BOTH, expand=True)

    # ---------------- Thermochemistry ----------------
    def _create_thermo_tab(self):
        top = ttk.Frame(self.thermo_frame)
        top.pack(fill=tk.X, pady=(0, 6))
        self.thermo_title = ttk.Label(top, text="Thermochemistry", font=("Segoe UI", 11, "bold"))
        self.thermo_title.pack(side=tk.LEFT)
        ttk.Button(top, text="Copy (Excel)", command=self.copy_thermo).pack(side=tk.RIGHT)

        tf = ttk.Frame(self.thermo_frame)
        tf.pack(fill=tk.BOTH, expand=True)
        self.thermo_tree = ttk.Treeview(tf, columns=("Quantity", "Eh", "Unit"), show="headings", height=20)
        self.thermo_tree.heading("Quantity", text="Quantity")
        self.thermo_tree.heading("Eh", text="Value (Eh)")
        self.thermo_tree.heading("Unit", text="Value (kcal/mol)")
        self.thermo_tree.column("Quantity", width=300, anchor="w")
        self.thermo_tree.column("Eh", width=180, anchor="e")
        self.thermo_tree.column("Unit", width=180, anchor="e")
        sy = ttk.Scrollbar(tf, orient="vertical", command=self.thermo_tree.yview)
        self.thermo_tree.configure(yscrollcommand=sy.set)
        self.thermo_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        self._reg(tk.Label(self.thermo_frame, justify="left", anchor="w", font=("Segoe UI", 9),
                           text="Corrections are relative quantities; absolute energies are converted for "
                                "convenience only. Values are taken from ORCA's last THERMOCHEMISTRY block."),
                  "hint").pack(fill=tk.X, pady=(6, 0))

    # ---------------- Orbitals ----------------
    def _create_orbital_tab(self):
        pane = ttk.PanedWindow(self.orbital_frame, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)

        ctrl = ttk.Frame(left)
        ctrl.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(ctrl, text="Levels around frontier:").pack(side=tk.LEFT)
        self.orb_window = tk.IntVar(value=8)
        ttk.Spinbox(ctrl, from_=2, to=40, textvariable=self.orb_window, width=5,
                    command=self._refresh_orbitals).pack(side=tk.LEFT, padx=4)
        ttk.Button(ctrl, text="Copy CSV", command=self.copy_orbitals_csv).pack(side=tk.RIGHT)

        self.frontier_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.frontier_var, font=("Segoe UI", 10, "bold"),
                  wraplength=420, justify="left").pack(anchor="w", pady=(0, 6))

        tf = ttk.Frame(left)
        tf.pack(fill=tk.BOTH, expand=True)
        cols = ("Spin", "No", "Occ", "E (Eh)", "E (eV)", "Label")
        self.orb_tree = ttk.Treeview(tf, columns=cols, show="headings", height=20)
        for c, w in zip(cols, (55, 60, 60, 110, 100, 90)):
            self.orb_tree.heading(c, text=c)
            self.orb_tree.column(c, width=w, anchor="e" if c not in ("Spin", "Label") else "center")
        sy = ttk.Scrollbar(tf, orient="vertical", command=self.orb_tree.yview)
        self.orb_tree.configure(yscrollcommand=sy.set)
        self.orb_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)

        self.orb_plot = _PlotPanel(right, figsize=(5, 5)) if _MPL_OK else None
        if self.orb_plot:
            self.orb_plot.frame.pack(fill=tk.BOTH, expand=True)

    # ---------------- Charges ----------------
    def _create_charges_tab(self):
        pane = ttk.PanedWindow(self.charges_frame, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)

        ctrl = ttk.Frame(left)
        ctrl.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(ctrl, text="Scheme:").pack(side=tk.LEFT)
        self.charge_scheme = tk.StringVar(value="Mulliken")
        cb = ttk.Combobox(ctrl, textvariable=self.charge_scheme, values=["Mulliken", "Loewdin"],
                          state="readonly", width=10)
        cb.pack(side=tk.LEFT, padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._refresh_charges())
        self.charge_plot_kind = tk.StringVar(value="Charge")
        cb2 = ttk.Combobox(ctrl, textvariable=self.charge_plot_kind, values=["Charge", "Spin population"],
                           state="readonly", width=14)
        cb2.pack(side=tk.LEFT, padx=4)
        cb2.bind("<<ComboboxSelected>>", lambda _e: self._draw_charges())
        ttk.Button(ctrl, text="Copy CSV", command=self.copy_charges_csv).pack(side=tk.RIGHT)

        tf = ttk.Frame(left)
        tf.pack(fill=tk.BOTH, expand=True)
        cols = ("#", "Atom", "Charge", "Spin")
        self.charge_tree = ttk.Treeview(tf, columns=cols, show="headings", height=20)
        for c, w in zip(cols, (50, 70, 120, 120)):
            self.charge_tree.heading(c, text=c)
            self.charge_tree.column(c, width=w, anchor="e" if c in ("Charge", "Spin") else "center")
        sy = ttk.Scrollbar(tf, orient="vertical", command=self.charge_tree.yview)
        self.charge_tree.configure(yscrollcommand=sy.set)
        self.charge_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        self.charge_sum_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.charge_sum_var, font=("Segoe UI", 9)).pack(anchor="w", pady=4)

        self.charge_plot = _PlotPanel(right, figsize=(6, 4.5)) if _MPL_OK else None
        if self.charge_plot:
            self.charge_plot.frame.pack(fill=tk.BOTH, expand=True)

    # ---------------- UV-Vis ----------------
    def _create_uv_tab(self):
        pane = ttk.PanedWindow(self.uv_frame, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)

        tf = ttk.Frame(left)
        tf.pack(fill=tk.BOTH, expand=True)
        cols = ("State", "E (eV)", "λ (nm)", "fosc")
        self.uv_tree = ttk.Treeview(tf, columns=cols, show="headings", height=20)
        for c, w in zip(cols, (80, 100, 100, 120)):
            self.uv_tree.heading(c, text=c)
            self.uv_tree.column(c, width=w, anchor="e")
        sy = ttk.Scrollbar(tf, orient="vertical", command=self.uv_tree.yview)
        self.uv_tree.configure(yscrollcommand=sy.set)
        self.uv_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        ttk.Button(left, text="Copy CSV", command=self.copy_uv_csv).pack(anchor="w", pady=4)

        ctrl = ttk.Frame(right)
        ctrl.pack(fill=tk.X, pady=(0, 4))
        self.uv_fwhm = tk.DoubleVar(value=0.30)
        self.uv_axis = tk.StringVar(value="Wavelength (nm)")
        ttk.Label(ctrl, text="FWHM (eV)").pack(side=tk.LEFT)
        ttk.Spinbox(ctrl, from_=0.05, to=1.5, increment=0.05, textvariable=self.uv_fwhm, width=6,
                    command=self._draw_uv).pack(side=tk.LEFT, padx=(2, 10))
        ax_cb = ttk.Combobox(ctrl, textvariable=self.uv_axis, values=["Wavelength (nm)", "Energy (eV)"],
                             state="readonly", width=16)
        ax_cb.pack(side=tk.LEFT)
        ax_cb.bind("<<ComboboxSelected>>", lambda _e: self._draw_uv())
        ttk.Button(ctrl, text="Redraw", command=self._draw_uv).pack(side=tk.LEFT, padx=6)

        self.uv_plot = _PlotPanel(right, figsize=(6, 4.5)) if _MPL_OK else None
        if self.uv_plot:
            self.uv_plot.frame.pack(fill=tk.BOTH, expand=True)

    # ---------------- Input & diagnostics ----------------
    def _create_diag_tab(self):
        pane = ttk.PanedWindow(self.diag_frame, orient=tk.VERTICAL)
        pane.pack(fill=tk.BOTH, expand=True)
        top = ttk.LabelFrame(pane, text="Input file (as echoed by ORCA)", padding=6)
        bottom = ttk.LabelFrame(pane, text="Warnings & errors", padding=6)
        pane.add(top, weight=1)
        pane.add(bottom, weight=1)

        bar = ttk.Frame(top)
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="Copy input", command=self._copy_input).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Save as .inp…", command=self._save_input).pack(side=tk.RIGHT, padx=4)
        self.input_text = tk.Text(top, height=10, wrap=tk.NONE, font=("Consolas", 11))
        self.input_text.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        tf = ttk.Frame(bottom)
        tf.pack(fill=tk.BOTH, expand=True)
        self.diag_tree = ttk.Treeview(tf, columns=("Type", "Count", "Message"), show="headings", height=8)
        self.diag_tree.heading("Type", text="Type")
        self.diag_tree.heading("Count", text="×")
        self.diag_tree.heading("Message", text="Message")
        self.diag_tree.column("Type", width=90, anchor="center")
        self.diag_tree.column("Count", width=50, anchor="center")
        self.diag_tree.column("Message", width=800, anchor="w")
        sy = ttk.Scrollbar(tf, orient="vertical", command=self.diag_tree.yview)
        self.diag_tree.configure(yscrollcommand=sy.set)
        self.diag_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        self.diag_tree.bind("<Double-1>", self._diag_jump_raw)

    # ---------------- Batch ----------------
    def _create_batch_tab(self):
        bar = ttk.Frame(self.batch_frame)
        bar.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(bar, text="Batch Process…", command=self.open_batch_dialog).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(bar, text="🔍 Filter:").pack(side=tk.LEFT, padx=(10, 4))
        self.batch_filter = tk.StringVar()
        self.batch_filter.trace_add("write", lambda *_: self._update_batch_results())
        ttk.Entry(bar, textvariable=self.batch_filter, width=24).pack(side=tk.LEFT)
        self.batch_hide_bad = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Only normal terminations", variable=self.batch_hide_bad,
                        command=self._update_batch_results).pack(side=tk.LEFT, padx=8)
        self.batch_summary_var = tk.StringVar(value="No batch results yet.")
        ttk.Label(bar, textvariable=self.batch_summary_var, font=("Segoe UI", 9)).pack(side=tk.RIGHT)

        tree_frame = ttk.Frame(self.batch_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        keys = [c[0] for c in BATCH_COLUMNS]
        self.batch_tree = ttk.Treeview(tree_frame, columns=keys, show="headings", height=20)
        for key, heading, width in BATCH_COLUMNS:
            self.batch_tree.heading(key, text=heading, command=lambda k=key: self._sort_batch(k))
            self.batch_tree.column(key, width=width, anchor="w" if key in ("file_name", "keywords", "path", "job_type") else "e")
        sy = ttk.Scrollbar(tree_frame, orient="vertical", command=self.batch_tree.yview)
        sx = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.batch_tree.xview)
        self.batch_tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sx.pack(side="bottom", fill="x")
        self.batch_tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="right", fill="y")
        self.batch_tree.bind("<Double-1>", self._open_batch_row)

        btn_frame = ttk.Frame(self.batch_frame)
        btn_frame.pack(fill=tk.X, pady=6)
        ttk.Button(btn_frame, text="Export to CSV", command=self.export_batch_csv).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Copy as Excel", command=self.copy_batch_csv).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Send to ML module", command=self.send_batch_to_ml).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Clear Results", command=self.clear_batch_results).pack(side=tk.LEFT, padx=2)
        ttk.Label(btn_frame, text="Double-click a row to open it · click a header to sort · ΔE/ΔG relative to the lowest value",
                  font=("Segoe UI", 8)).pack(side=tk.RIGHT)

    # ---------------- Raw ----------------
    def _create_raw_tab(self):
        bar = ttk.Frame(self.raw_frame)
        bar.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(bar, text="🔍 Find:").pack(side=tk.LEFT)
        self.raw_search_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.raw_search_var, width=28)
        ent.pack(side=tk.LEFT, padx=4)
        ent.bind("<Return>", lambda _e: self._raw_find(next_only=True))
        ttk.Button(bar, text="Next", command=lambda: self._raw_find(next_only=True)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Highlight all", command=lambda: self._raw_find(next_only=False)).pack(side=tk.LEFT, padx=2)
        self.raw_case = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Match case", variable=self.raw_case).pack(side=tk.LEFT, padx=6)
        self.raw_hits_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.raw_hits_var, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=6)

        ttk.Label(bar, text="Jump to:").pack(side=tk.LEFT, padx=(14, 4))
        self.raw_jump_var = tk.StringVar()
        self.raw_jump_cb = ttk.Combobox(bar, textvariable=self.raw_jump_var, state="readonly", width=24,
                                        values=[name for name, _ in RAW_SECTIONS])
        self.raw_jump_cb.pack(side=tk.LEFT)
        self.raw_jump_cb.bind("<<ComboboxSelected>>", lambda _e: self._raw_jump())
        ttk.Button(bar, text="Top", command=lambda: self.raw_text.see("1.0")).pack(side=tk.LEFT, padx=(8, 2))
        ttk.Button(bar, text="End", command=lambda: self.raw_text.see(tk.END)).pack(side=tk.LEFT, padx=2)

        tf = ttk.Frame(self.raw_frame)
        tf.pack(fill=tk.BOTH, expand=True)
        self.raw_text = tk.Text(tf, wrap=tk.NONE, font=("Consolas", 10), undo=False)
        sy = ttk.Scrollbar(tf, orient="vertical", command=self.raw_text.yview)
        sx = ttk.Scrollbar(tf, orient="horizontal", command=self.raw_text.xview)
        self.raw_text.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sx.pack(side=tk.BOTTOM, fill=tk.X)
        self.raw_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        self._raw_search_pos = "1.0"

    # ==================================================================
    # Small UI helpers
    # ==================================================================
    def _placeholder(self, host, text):
        for w in host.winfo_children():
            w.destroy()
        p = self._p()
        lbl = tk.Label(host, text=text, font=("Segoe UI", 13), bg=p["bg_main"], fg=p["fg_muted"])
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        self._themed.append((lbl, "hint"))

    def _toast(self, msg, ms=1800):
        try:
            self._toast_lbl.config(text=msg)
            self._toast_lbl.place(relx=0.5, rely=1.0, anchor="s", y=-14)
            self._toast_lbl.lift()
            if self._toast_job:
                self.main_frame.after_cancel(self._toast_job)
            self._toast_job = self.main_frame.after(ms, self._toast_lbl.place_forget)
        except tk.TclError:
            pass

    def _clip(self, text, msg="Copied to clipboard"):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            self._toast(msg)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to copy to clipboard:\n{str(e)}")

    def _need_file(self):
        if not self.report:
            messagebox.showinfo("No file", "Open an ORCA output file first.")
            return False
        return True

    @staticmethod
    def _fill_tree(tree, rows, tags=None):
        tree.delete(*tree.get_children())
        for i, row in enumerate(rows):
            tree.insert("", tk.END, values=row, tags=(tags[i],) if tags and tags[i] else ())

    def _ask_save(self, title, ext, types, initial=None):
        base = os.path.splitext(os.path.basename(self.current_file or "orca"))[0]
        return filedialog.asksaveasfilename(
            title=title, defaultextension=ext, filetypes=types,
            initialfile=initial or base + ext,
            initialdir=os.path.dirname(self.current_file) if self.current_file else None,
        )

    # ==================================================================
    # Recent files / session
    # ==================================================================
    def _load_recent(self):
        try:
            with open(_user_data_path("output_viewer_recent.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
            return [p for p in data if isinstance(p, str)][:12]
        except Exception:
            return []

    def _save_recent(self):
        try:
            os.makedirs(os.path.dirname(_user_data_path("x")), exist_ok=True)
            with open(_user_data_path("output_viewer_recent.json"), "w", encoding="utf-8") as f:
                json.dump(self.recent_files[:12], f, indent=1)
        except Exception:
            pass

    def _push_recent(self, path):
        path = os.path.abspath(path)
        self.recent_files = [p for p in self.recent_files if os.path.abspath(p) != path]
        self.recent_files.insert(0, path)
        self.recent_files = self.recent_files[:12]
        self._save_recent()
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        self.recent_menu.delete(0, tk.END)
        existing = [p for p in self.recent_files if os.path.exists(p)]
        if not existing:
            self.recent_menu.add_command(label="(no recent files)", state="disabled")
            return
        for p in existing:
            self.recent_menu.add_command(label=f"{os.path.basename(p)}   —   {os.path.dirname(p)}",
                                         command=lambda x=p: self.load_file(x))
        self.recent_menu.add_separator()
        self.recent_menu.add_command(label="Clear list", command=self._clear_recent)

    def _clear_recent(self):
        self.recent_files = []
        self._save_recent()
        self._rebuild_recent_menu()

    def get_session_state(self):
        return {
            "format": "output_viewer",
            "file": self.current_file,
            "unit": self.unit_var.get() if hasattr(self, "unit_var") else "kcal/mol",
            "tab": self.notebook.index(self.notebook.select()) if hasattr(self, "notebook") else 0,
        }

    def apply_session_state(self, state):
        if not isinstance(state, dict) or state.get("format") != "output_viewer":
            raise ValueError("not an output viewer session")
        if state.get("unit") in UNITS:
            self.unit_var.set(state["unit"])
        path = state.get("file")
        if path and os.path.exists(path):
            self.load_file(path, remember=False)
        try:
            self.notebook.select(int(state.get("tab", 0)))
        except Exception:
            pass

    # ==================================================================
    # Loading
    # ==================================================================
    def open_file(self):
        """Open and parse an ORCA output file."""
        filepath = filedialog.askopenfilename(
            title="Select ORCA Output File",
            filetypes=[("ORCA Output Files", "*.out *.log"), ("All Files", "*.*")]
        )
        if filepath:
            self.load_file(filepath)

    def reload_file(self):
        if self.current_file and os.path.exists(self.current_file):
            self.load_file(self.current_file, remember=False, keep_tab=True)
        else:
            self._toast("Nothing to reload")

    def load_file(self, filepath, remember=True, keep_tab=False):
        self._stop_mode_anim()
        self._stop_traj_play()
        try:
            if self.root is not None:
                self.root.config(cursor="watch")
                self.root.update_idletasks()
            self.parser = ORCAParser(filepath)
            self.report = self.parser.get_full_report()
            self.current_file = filepath
            try:
                self._watch_mtime = os.path.getmtime(filepath)
            except OSError:
                self._watch_mtime = None
        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse file:\n{str(e)}")
            return
        finally:
            if self.root is not None:
                self.root.config(cursor="")
        if remember:
            self._push_recent(filepath)
        size_mb = os.path.getsize(filepath) / 1e6 if os.path.exists(filepath) else 0
        self.file_label.config(text=f"📄 {filepath}    ({size_mb:.2f} MB, {len(self.parser.lines):,} lines, "
                                    f"parsed {time.strftime('%H:%M:%S')})")
        self.parse_and_display()
        if not keep_tab:
            self.notebook.select(self.overview_frame)

    def parse_and_display(self):
        """Populate every tab from the parsed report."""
        if not self.report:
            return
        try:
            r = self.report
            self._update_chips(r)
            self._update_overview(r)
            self._update_geometry(r.get('geometry', []))
            self._update_opt(r)
            self._update_frequencies(r.get('frequencies', []))
            self._update_thermo()
            self._refresh_orbitals()
            self._refresh_charges()
            self._update_uv()
            self._update_diag(r)
            self._raw_loaded = False
            if self.notebook.select() == str(self.raw_frame):
                self._update_raw_output()
            self._rebuild_viewers()
            self._draw_all_plots()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to display information:\n{str(e)}")

    def _on_tab_changed(self, _event=None):
        try:
            if self.notebook.select() == str(self.raw_frame) and not self._raw_loaded:
                self._update_raw_output()
        except tk.TclError:
            pass

    # ==================================================================
    # Live watch
    # ==================================================================
    def _toggle_watch(self):
        if self._watch_job:
            try:
                self.main_frame.after_cancel(self._watch_job)
            except tk.TclError:
                pass
            self._watch_job = None
        if self.watch_var.get():
            self._toast("Live refresh on — re-parsing when the file changes")
            self._watch_job = self.main_frame.after(4000, self._watch_tick)

    def _watch_tick(self):
        self._watch_job = None
        if not self.watch_var.get():
            return
        try:
            if self.current_file and os.path.exists(self.current_file):
                m = os.path.getmtime(self.current_file)
                if self._watch_mtime is not None and m != self._watch_mtime:
                    self.load_file(self.current_file, remember=False, keep_tab=True)
                    self._toast("File changed — refreshed")
                    if self.report and self.report.get("status") == "Normal":
                        self.watch_var.set(False)
                        self._toast("Job finished — live refresh stopped", 3000)
                        return
        except Exception:
            pass
        self._watch_job = self.main_frame.after(4000, self._watch_tick)

    # ==================================================================
    # Populate: chips & overview
    # ==================================================================
    def _update_chips(self, r):
        chips = []
        st = r.get("status")
        chips.append((f"● {'Normal termination' if st == 'Normal' else st}",
                      "ok" if st == "Normal" else ("bad" if st == "Error" else "warn")))
        chips.append((r.get("job_type", "—"), "info"))
        if r.get("opt_cycles") or "Opt" in r.get("job_type", ""):
            conv = r.get("geo_opt_converged")
            chips.append((f"Opt {'converged' if conv else 'NOT converged'} ({len(r.get('opt_cycles', []))} cycles)",
                          "ok" if conv else "bad"))
        if r.get("frequencies"):
            n = r.get("imaginary_modes", 0)
            chips.append(("No imaginary modes" if n == 0 else f"{n} imaginary mode{'s' if n > 1 else ''}",
                          "ok" if n == 0 else ("warn" if n == 1 else "bad")))
        scf = r.get("scf", {})
        if scf.get("not_converged"):
            chips.append((f"SCF not converged ×{scf['not_converged']}", "bad"))
        elif scf.get("cycles"):
            chips.append((f"SCF ok ({scf['cycles'][-1]} cycles)", "muted"))
        dev = r.get("s2_deviation")
        if dev is not None and r["job"].get("hf_type") == "UHF":
            chips.append((f"<S²> dev {dev:.3f}", "ok" if abs(dev) < 0.1 else "warn"))
        if r.get("warnings"):
            chips.append((f"{len(r['warnings'])} warnings", "warn"))
        if r["job"].get("version"):
            chips.append((f"ORCA {r['job']['version']}", "muted"))
        self._set_chips(chips)

    def _update_overview(self, r):
        job = r.get("job", {})
        fr = r.get("frontier", {})
        dip = r.get("dipole") or {}
        values = {
            "final_energy": (_fmt(r.get("final_energy"), 6), "Eh"),
            "gibbs_energy": (_fmt(r.get("gibbs_energy"), 6), "Eh"),
            "total_enthalpy": (_fmt(r.get("total_enthalpy"), 6), "Eh"),
            "gap": (_fmt(fr.get("gap_ev"), 3), f"eV  ·  HOMO {_fmt(fr.get('homo_ev'), 2)} / LUMO {_fmt(fr.get('lumo_ev'), 2)}"
                    if fr.get("gap_ev") is not None else "eV"),
            "dipole": (_fmt(dip.get("debye"), 3), "Debye"),
            "time": (_fmt(r.get("time_hours"), 3), "hours"),
        }
        for key, (val_lbl, sub_lbl, _unit) in self.cards.items():
            v, sub = values[key]
            val_lbl.config(text=v)
            sub_lbl.config(text=sub)

        rows = [
            ("File", r.get("filepath")),
            ("Status", r.get("status")),
            ("Job type", r.get("job_type")),
            ("Keywords", job.get("keywords") or "—"),
            ("ORCA version", job.get("version")),
            ("Reference (HFTyp)", job.get("hf_type")),
            ("Charge / Multiplicity", f"{_fmt(job.get('charge'))} / {_fmt(job.get('multiplicity'))}"),
            ("Atoms", len(r.get("geometry", [])) or job.get("n_atoms")),
            ("Electrons", job.get("n_electrons")),
            ("Basis functions", job.get("n_basis")),
            ("Final Single Point Energy (Eh)", r.get("final_energy")),
            ("Electronic Energy (Eh)", r.get("electronic_energy")),
            ("Total Enthalpy (Eh)", r.get("total_enthalpy")),
            ("Gibbs Free Energy (Eh)", r.get("gibbs_energy")),
            ("Thermal Correction G-E(el) (Eh)", r.get("thermal_correction")),
            ("Geo Opt Converged", r.get("geo_opt_converged")),
            ("Optimization cycles", len(r.get("opt_cycles", [])) or "—"),
            ("Imaginary Modes", r.get("imaginary_modes")),
            ("SCF cycles (last)", r["scf"]["cycles"][-1] if r.get("scf", {}).get("cycles") else None),
            ("HOMO (eV)", fr.get("homo_ev")),
            ("LUMO (eV)", fr.get("lumo_ev")),
            ("HOMO–LUMO gap (eV)", fr.get("gap_ev")),
            ("Dipole (Debye)", dip.get("debye")),
            ("S² Expectation Value", r.get("s2_expectation")),
            ("S² Ideal Value", r.get("s2_ideal")),
            ("S² Deviation", r.get("s2_deviation")),
            ("Time Taken (hours)", r.get("time_hours")),
        ]
        self._detail_rows = [(k, _fmt(v)) for k, v in rows]
        self._fill_tree(self.details_tree, self._detail_rows)

    def _copy_card(self, key):
        lbl = self.cards[key][0]
        if lbl.cget("text") not in ("—", ""):
            self._clip(lbl.cget("text"), f"Copied {lbl.cget('text')}")

    def _copy_selected_detail(self):
        sel = self.details_tree.selection()
        if not sel:
            self._toast("Select a row first")
            return
        vals = [self.details_tree.item(i, "values")[1] for i in sel]
        self._clip("\n".join(vals), f"Copied {len(vals)} value(s)")

    def copy_summary_data(self):
        """Copy all summary data to clipboard in Excel column format."""
        if not self._need_file():
            return
        headers = [k for k, _ in self._detail_rows]
        values = [v for _, v in self._detail_rows]
        self._clip("\t".join(headers) + "\n" + "\t".join(values), "Summary copied — paste into Excel")

    # ==================================================================
    # Viewers
    # ==================================================================
    def _rows_from_geometry(self, geometry):
        return [(g.atom, g.x, g.y, g.z) for g in geometry]

    def _make_viewer(self, key, host, rows):
        for w in host.winfo_children():
            w.destroy()
        if not rows:
            self._placeholder(host, "No structure loaded")
            self._viewers.pop(key, None)
            return None
        try:
            v = AutoChemyViewer(host, rows, is_dark=(self._mode == "dark"))
        except Exception as e:
            self._placeholder(host, f"Viewer error: {e}")
            return None
        self._viewers[key] = v
        return v

    def _rebuild_viewers(self):
        self._stop_mode_anim()
        self._traj_shown = None
        if not self.report:
            return
        rows = self._rows_from_geometry(self.report.get("geometry", []))
        self._make_viewer("overview", self.ov_view_host, rows)
        self._make_viewer("freq", self.freq_view_host, rows)
        traj = self.report.get("trajectory") or []
        n = max(1, len(traj))
        self.traj_scale.configure(from_=1, to=n)
        self.traj_scale.set(n)
        self._make_viewer("geometry", self.geo_view_host, rows)
        self._update_traj_label(n)

    def _on_geometry_select(self, _event=None):
        v = self._viewers.get("geometry")
        if not v:
            return
        idxs = [int(self.geometry_tree.item(i, "values")[0]) - 1 for i in self.geometry_tree.selection()]
        try:
            v.set_selected_indices(idxs[-4:])
        except Exception:
            pass

    # ---------------- trajectory ----------------
    def _update_traj_label(self, frame_no):
        traj = (self.report or {}).get("trajectory") or []
        cycles = (self.report or {}).get("opt_cycles") or []
        energies = (self.report or {}).get("sp_energies") or []
        txt = f"Frame {frame_no} / {max(1, len(traj))}"
        if 0 < frame_no <= len(energies):
            e = energies[frame_no - 1]
            txt += f"    E = {e:.6f} Eh    ΔE(final) = {(e - energies[-1]) * HARTREE_TO_KCAL:+.2f} kcal/mol"
        if 0 < frame_no <= len(cycles) and cycles[frame_no - 1].items:
            done = sum(1 for it in cycles[frame_no - 1].items.values() if it[2])
            txt += f"    criteria met {done}/{len(cycles[frame_no - 1].items)}"
        self.traj_label.config(text=txt)

    def _on_traj_scale(self):
        traj = (self.report or {}).get("trajectory") or []
        v = self._viewers.get("geometry")
        if not traj or not v:
            return
        idx = int(round(float(self.traj_scale.get()))) - 1
        idx = max(0, min(len(traj) - 1, idx))
        if getattr(self, "_traj_shown", None) == idx:
            return
        self._traj_shown = idx
        rows = self._rows_from_geometry(traj[idx])
        if len(rows) != len(v.rows):
            return
        v.rows = rows
        v._prepare_geometry()
        v.request_draw(force=True)
        self._update_traj_label(idx + 1)

    def _toggle_traj_play(self):
        if self._traj_job:
            self._stop_traj_play()
            return
        traj = (self.report or {}).get("trajectory") or []
        if len(traj) < 2:
            self._toast("Only one geometry in this file")
            return
        self.traj_play_btn.config(text="■ Stop")
        if int(round(float(self.traj_scale.get()))) >= len(traj):
            self.traj_scale.set(1)
        self._traj_step()

    def _traj_step(self):
        traj = (self.report or {}).get("trajectory") or []
        cur = int(round(float(self.traj_scale.get())))
        if cur >= len(traj):
            self._stop_traj_play()
            return
        self.traj_scale.set(cur + 1)
        self._on_traj_scale()
        self._traj_job = self.main_frame.after(max(60, 2400 // max(1, len(traj))), self._traj_step)

    def _stop_traj_play(self):
        if self._traj_job:
            try:
                self.main_frame.after_cancel(self._traj_job)
            except tk.TclError:
                pass
        self._traj_job = None
        if hasattr(self, "traj_play_btn"):
            self.traj_play_btn.config(text="▶ Play")

    # ---------------- normal-mode animation ----------------
    def _selected_mode(self):
        sel = self.freq_tree.selection()
        if not sel:
            return None
        return int(self.freq_tree.item(sel[0], "values")[0])

    def _on_freq_select(self, _event=None):
        if self._anim_job:
            self._start_mode_anim()

    def _toggle_mode_anim(self):
        if self._anim_job:
            self._stop_mode_anim()
        else:
            self._start_mode_anim()

    def _mode_vector(self, mode):
        modes = (self.report or {}).get("normal_modes") or {}
        geom = (self.report or {}).get("geometry") or []
        vec = modes.get(mode)
        if not vec or len(vec) != 3 * len(geom):
            return None
        return vec

    def _start_mode_anim(self):
        self._stop_mode_anim(reset=False)
        mode = self._selected_mode()
        if mode is None:
            self._toast("Select a vibrational mode first")
            return
        vec = self._mode_vector(mode)
        v = self._viewers.get("freq")
        if vec is None or v is None:
            self._toast("No normal-mode vectors in this output")
            return
        ref = [(g.x, g.y, g.z) for g in self.report["geometry"]]
        cx = sum(p[0] for p in ref) / len(ref)
        cy = sum(p[1] for p in ref) / len(ref)
        cz = sum(p[2] for p in ref) / len(ref)
        max_r = max(1e-9, max(((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2) ** 0.5 for x, y, z in ref))
        self._anim_state = {"ref": ref, "vec": vec, "c": (cx, cy, cz), "r": max_r, "t": 0.0, "viewer": v}
        self.anim_btn.config(text="■ Stop")
        self._anim_step()

    def _anim_step(self):
        st = self._anim_state
        v = st["viewer"]
        try:
            if not v.canvas.winfo_exists():
                self._stop_mode_anim()
                return
        except tk.TclError:
            return
        st["t"] += 0.35
        s = math.sin(st["t"]) * float(self.amp_var.get())
        vec, (cx, cy, cz), r = st["vec"], st["c"], st["r"]
        coords = [(x + s * vec[3 * i], y + s * vec[3 * i + 1], z + s * vec[3 * i + 2])
                  for i, (x, y, z) in enumerate(st["ref"])]
        v.coords = coords
        v.norm = [((x - cx) / r, (y - cy) / r, (z - cz) / r) for x, y, z in coords]
        v.request_draw(force=True)
        self._anim_job = self.main_frame.after(45, self._anim_step)

    def _stop_mode_anim(self, reset=True):
        if self._anim_job:
            try:
                self.main_frame.after_cancel(self._anim_job)
            except tk.TclError:
                pass
        self._anim_job = None
        if hasattr(self, "anim_btn"):
            self.anim_btn.config(text="▶ Animate")
        st = getattr(self, "_anim_state", None)
        if reset and st:
            v = st["viewer"]
            try:
                v._prepare_geometry()
                v.request_draw(force=True)
            except Exception:
                pass
            self._anim_state = None

    def save_mode_xyz(self):
        if not self._need_file():
            return
        mode = self._selected_mode()
        vec = self._mode_vector(mode) if mode is not None else None
        if vec is None:
            messagebox.showinfo("Info", "Select a mode that has normal-mode vectors.")
            return
        path = self._ask_save("Save mode animation", ".xyz", [("XYZ trajectory", "*.xyz")],
                              initial=f"mode_{mode}.xyz")
        if not path:
            return
        geom = self.report["geometry"]
        freq = next((f.frequency for f in self.report["frequencies"] if f.mode == mode), 0.0)
        amp = float(self.amp_var.get())
        with open(path, "w", encoding="utf-8") as f:
            for k in range(20):
                s = math.sin(2 * math.pi * k / 20) * amp
                f.write(f"{len(geom)}\nmode {mode}  {freq:.2f} cm-1  frame {k + 1}/20\n")
                for i, g in enumerate(geom):
                    f.write(f"{g.atom:<3} {g.x + s * vec[3 * i]:12.6f} {g.y + s * vec[3 * i + 1]:12.6f} "
                            f"{g.z + s * vec[3 * i + 2]:12.6f}\n")
        self._toast(f"Saved {os.path.basename(path)}")

    # ==================================================================
    # Populate: tabs
    # ==================================================================
    def _update_geometry(self, geometry: list):
        rows = [(i + 1, g.atom, f"{g.x:.6f}", f"{g.y:.6f}", f"{g.z:.6f}") for i, g in enumerate(geometry)]
        self._fill_tree(self.geometry_tree, rows)
        if geometry:
            counts = {}
            for g in geometry:
                counts[g.atom] = counts.get(g.atom, 0) + 1
            order = sorted(counts, key=lambda a: (a != "C", a != "H", a))
            formula = "".join(f"{a}{counts[a] if counts[a] > 1 else ''}" for a in order)
            n_frames = len(self.report.get("trajectory") or [])
            self.geom_info_var.set(f"Formula {formula}  ·  {len(geometry)} atoms  ·  {n_frames} geometry frame(s). "
                                   f"Select rows to highlight atoms (2 = distance, 3 = angle, 4 = dihedral).")
        else:
            self.geom_info_var.set("No Cartesian coordinates found.")

    def _update_opt(self, r):
        cycles = r.get("opt_cycles") or []
        rows, tags = [], []
        final = cycles[-1].energy if cycles and cycles[-1].energy is not None else None
        for c in cycles:
            def item(name):
                it = c.items.get(name)
                return f"{it[0]:.2e}{' ✓' if it[2] else ''}" if it else "—"
            de = (c.energy - final) * HARTREE_TO_KCAL if (c.energy is not None and final is not None) else None
            rows.append((c.cycle, _fmt(c.energy, 6), _fmt(de, 3), item("RMS gradient"), item("MAX gradient"),
                         item("RMS step"), item("MAX step")))
            all_ok = c.items and all(v[2] for v in c.items.values())
            tags.append("good" if all_ok else "")
        self._fill_tree(self.opt_tree, rows, tags)
        if cycles:
            self.opt_info_var.set(f"{len(cycles)} optimization cycles. "
                                  f"{'Converged ✓' if r.get('geo_opt_converged') else 'Not converged'}. "
                                  "ΔE is relative to the last cycle (kcal/mol). ✓ marks a met criterion.")
        else:
            self.opt_info_var.set("No geometry optimization found in this output.")

    def _update_frequencies(self, frequencies: list):
        rows, tags = [], []
        for f in frequencies:
            imag = f.frequency < 0
            rows.append((f.mode, f"{f.frequency:.2f}", f"{f.intensity:.2f}", "imaginary" if imag else ""))
            tags.append("bad" if imag else "")
        self._fill_tree(self.freq_tree, rows, tags)
        self._stop_mode_anim()

    def _thermo_rows(self):
        th = (self.report or {}).get("thermo") or {}
        labels = [
            ("electronic_energy", "Electronic energy", True),
            ("zpe", "Zero-point energy (ZPE)", False),
            ("thermal_vib", "Thermal vibrational correction", False),
            ("thermal_rot", "Thermal rotational correction", False),
            ("thermal_trans", "Thermal translational correction", False),
            ("total_correction", "Total thermal correction", False),
            ("total_thermal_energy", "Total thermal energy (U)", True),
            ("thermal_enthalpy_corr", "Thermal enthalpy correction (kT)", False),
            ("total_enthalpy", "Total enthalpy (H)", True),
            ("entropy_el", "T·S electronic", False),
            ("entropy_vib", "T·S vibrational", False),
            ("entropy_rot", "T·S rotational", False),
            ("entropy_trans", "T·S translational", False),
            ("entropy_term", "Final entropy term (T·S)", False),
            ("gibbs_energy", "Final Gibbs free energy (G)", True),
            ("g_minus_eel", "G − E(el)  (thermal correction to G)", False),
        ]
        unit = self.unit_var.get()
        f = UNITS[unit]
        out = []
        for key, label, absolute in labels:
            v = th.get(key)
            if v is None:
                continue
            out.append((label, f"{v:.8f}", f"{v * f:.4f}" if unit != "Eh" else f"{v:.8f}", absolute))
        return out, th

    def _update_thermo(self):
        rows, th = self._thermo_rows()
        self.thermo_tree.heading("Unit", text=f"Value ({self.unit_var.get()})")
        self._fill_tree(self.thermo_tree, [r[:3] for r in rows], ["accent" if r[3] else "" for r in rows])
        if th.get("temperature"):
            self.thermo_title.config(text=f"Thermochemistry at {th['temperature']:.2f} K, "
                                          f"{_fmt(th.get('pressure'), 2)} atm")
        else:
            self.thermo_title.config(text="Thermochemistry — no frequency calculation in this output")

    def _refresh_unit_views(self):
        if self.report:
            self._update_thermo()
            self._draw_opt()

    def _orbital_window(self):
        orbs = (self.report or {}).get("orbitals") or {"alpha": [], "beta": []}
        n = int(self.orb_window.get() or 8)
        out = {}
        for spin in ("alpha", "beta"):
            lst = orbs.get(spin) or []
            if not lst:
                continue
            fr = ORCAParser.frontier(lst)
            h = fr["homo_idx"] if fr["homo_idx"] is not None else 0
            lo = max(0, h - n + 1)
            hi = min(len(lst), h + n + 1)
            out[spin] = (lst[lo:hi], fr)
        return out

    def _refresh_orbitals(self):
        if not hasattr(self, "orb_tree"):
            return
        win = self._orbital_window()
        rows, tags = [], []
        lines = []
        for spin, (lst, fr) in win.items():
            spin_lbl = "α" if spin == "alpha" else "β"
            if "beta" not in win:
                spin_lbl = "—"
            for o in reversed(lst):
                label = ""
                if o.index == fr["homo_idx"]:
                    label = "HOMO"
                elif o.index == fr["lumo_idx"]:
                    label = "LUMO"
                elif fr["homo_idx"] is not None and o.index < fr["homo_idx"]:
                    label = f"HOMO−{fr['homo_idx'] - o.index}"
                elif fr["lumo_idx"] is not None and o.index > fr["lumo_idx"]:
                    label = f"LUMO+{o.index - fr['lumo_idx']}"
                rows.append((spin_lbl, o.index, f"{o.occupation:.4f}", f"{o.energy_eh:.6f}", f"{o.energy_ev:.4f}", label))
                tags.append("accent" if label in ("HOMO", "LUMO") else ("" if o.occupation > 0.1 else "warn"))
            if fr["gap_ev"] is not None:
                lines.append(f"{'α' if spin == 'alpha' else 'β'}: HOMO {fr['homo_ev']:.3f} eV · "
                             f"LUMO {fr['lumo_ev']:.3f} eV · gap {fr['gap_ev']:.3f} eV")
        self._fill_tree(self.orb_tree, rows, tags)
        if lines and "beta" not in win:
            lines = [lines[0][3:]]
        self.frontier_var.set("\n".join(lines) if lines else "No orbital energies in this output.")
        self._draw_orbitals()

    def _current_charges(self):
        if not self.report:
            return []
        return self.report.get("mulliken" if self.charge_scheme.get() == "Mulliken" else "loewdin") or []

    def _refresh_charges(self):
        if not hasattr(self, "charge_tree"):
            return
        ch = self._current_charges()
        rows = [(c.index, c.atom, f"{c.charge:+.6f}", _fmt(c.spin, 6) if c.spin is not None else "—") for c in ch]
        self._fill_tree(self.charge_tree, rows)
        if ch:
            s = sum(c.charge for c in ch)
            sp = sum(c.spin for c in ch if c.spin is not None) if ch[0].spin is not None else None
            txt = f"Σ charges = {s:+.4f}"
            if sp is not None:
                txt += f"    Σ spin = {sp:+.4f}"
            most_pos = max(ch, key=lambda c: c.charge)
            most_neg = min(ch, key=lambda c: c.charge)
            txt += f"    most + : {most_pos.atom}{most_pos.index} ({most_pos.charge:+.3f})" \
                   f"    most − : {most_neg.atom}{most_neg.index} ({most_neg.charge:+.3f})"
            self.charge_sum_var.set(txt)
        else:
            self.charge_sum_var.set("No population analysis found.")
        self._draw_charges()

    def _update_uv(self):
        states = (self.report or {}).get("excited_states") or []
        rows = [(s.state, f"{s.energy_ev:.4f}", f"{s.wavelength_nm:.1f}", f"{s.fosc:.6f}") for s in states]
        self._fill_tree(self.uv_tree, rows)

    def _update_diag(self, r):
        self.input_text.config(state=tk.NORMAL)
        self.input_text.delete("1.0", tk.END)
        self.input_text.insert("1.0", "\n".join(r.get("input_lines") or ["(input echo not found)"]))
        rows, tags = [], []
        for e in r.get("errors") or []:
            rows.append(("ERROR", 1, e))
            tags.append("bad")
        for w, n in r.get("warnings") or []:
            rows.append(("WARNING", n, w))
            tags.append("warn")
        if not rows:
            rows.append(("OK", "", "No warnings or errors found."))
            tags.append("good")
        self._fill_tree(self.diag_tree, rows, tags)

    def _copy_input(self):
        self._clip(self.input_text.get("1.0", "end-1c"), "Input copied")

    def _save_input(self):
        if not self._need_file():
            return
        path = self._ask_save("Save input", ".inp", [("ORCA input", "*.inp"), ("All", "*.*")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.input_text.get("1.0", "end-1c") + "\n")
            self._toast(f"Saved {os.path.basename(path)}")

    def _diag_jump_raw(self, _event=None):
        sel = self.diag_tree.selection()
        if not sel:
            return
        msg = self.diag_tree.item(sel[0], "values")[2]
        self.notebook.select(self.raw_frame)
        self._update_raw_output()
        self.raw_search_var.set(msg[:60])
        self._raw_search_pos = "1.0"
        self._raw_find(next_only=True)

    # ==================================================================
    # Plots
    # ==================================================================
    def _draw_all_plots(self):
        if not _MPL_OK:
            return
        self._draw_opt()
        self._draw_ir()
        self._draw_orbitals()
        self._draw_charges()
        self._draw_uv()

    def _draw_opt(self):
        if not getattr(self, "opt_plot", None):
            return
        p = self._p()
        cycles = (self.report or {}).get("opt_cycles") or []
        energies = [c.energy for c in cycles if c.energy is not None]
        if not cycles:
            self.opt_plot.empty(p, "No geometry optimization in this file")
            return
        P = self.opt_plot
        P.begin(p)
        unit = self.unit_var.get()
        f = UNITS[unit]
        ax1 = P.fig.add_subplot(211)
        P.style_axes(ax1, p)
        if energies:
            x = [c.cycle for c in cycles if c.energy is not None]
            rel = [(e - energies[-1]) * f for e in energies]
            ax1.plot(x, rel, "-o", color=p["accent"], ms=4, lw=1.6)
            ax1.set_ylabel(f"E − E$_{{final}}$ ({unit})")
            ax1.set_title("Energy profile", fontsize=10)
        ax2 = P.fig.add_subplot(212, sharex=ax1)
        P.style_axes(ax2, p)
        colors = {"Energy change": "#a855f7", "RMS gradient": "#ef4444", "MAX gradient": "#f97316",
                  "RMS step": "#22c55e", "MAX step": "#06b6d4"}
        for name, col in colors.items():
            xs, ys = [], []
            for c in cycles:
                it = c.items.get(name)
                if it and it[1] > 0:
                    xs.append(c.cycle)
                    ys.append(max(1e-6, abs(it[0]) / it[1]))
            if xs:
                ax2.semilogy(xs, ys, "-", marker=".", color=col, lw=1.3, label=name)
        ax2.axhline(1.0, color=p["fg_muted"], ls="--", lw=1)
        ax2.set_ylabel("|value| / tolerance")
        ax2.set_xlabel("Optimization cycle")
        ax2.set_title("Convergence criteria (below dashed line = met)", fontsize=10)
        leg = ax2.legend(fontsize=8, ncol=3, frameon=False)
        for t in leg.get_texts():
            t.set_color(p["fg"])
        P.finish()

    def _ir_curve(self):
        freqs = [f for f in (self.report or {}).get("frequencies") or [] if f.frequency > 0]
        if not freqs:
            return None
        sc = float(self.ir_scale.get() or 1.0)
        fw = max(0.5, float(self.ir_fwhm.get() or 20.0))
        nu = np.array([f.frequency * sc for f in freqs])
        inten = np.array([f.intensity for f in freqs])
        xmax = max(4000.0, float(nu.max()) + 200)
        x = np.linspace(0, xmax, 4000)
        hw = fw / 2.0
        y = (inten[None, :] * hw ** 2 / ((x[:, None] - nu[None, :]) ** 2 + hw ** 2)).sum(axis=1)
        return x, y, nu, inten

    def _draw_ir(self):
        if not getattr(self, "ir_plot", None):
            return
        p = self._p()
        try:
            data = self._ir_curve()
        except (tk.TclError, ValueError):
            return
        if not data:
            self.ir_plot.empty(p, "No vibrational frequencies in this file")
            return
        x, y, nu, inten = data
        P = self.ir_plot
        P.begin(p)
        ax = P.fig.add_subplot(111)
        P.style_axes(ax, p)
        ymax = max(1e-9, y.max())
        if self.ir_trans.get():
            ax.plot(x, 100 - 100 * y / ymax, color=p["accent"], lw=1.4)
            ax.set_ylabel("Transmittance (%)")
            ax.set_ylim(-2, 105)
        else:
            ax.fill_between(x, y, color=p["accent"], alpha=0.15)
            ax.plot(x, y, color=p["accent"], lw=1.4)
            if self.ir_sticks.get():
                ax.vlines(nu, 0, inten, color="#ef4444", lw=1, alpha=0.8)
            ax.set_ylabel("Intensity (km/mol, broadened)")
            ax.set_ylim(0, max(ymax, inten.max()) * 1.08)
        ax.set_xlim(x.max(), 0)
        ax.set_xlabel("Wavenumber (cm⁻¹)")
        n_imag = sum(1 for f in self.report["frequencies"] if f.frequency < 0)
        title = f"IR spectrum · FWHM {float(self.ir_fwhm.get()):.0f} cm⁻¹ · scale {float(self.ir_scale.get()):.3f}"
        if n_imag:
            title += f" · {n_imag} imaginary mode(s) omitted"
        ax.set_title(title, fontsize=10)
        P.finish()

    def _draw_orbitals(self):
        if not getattr(self, "orb_plot", None):
            return
        p = self._p()
        win = self._orbital_window() if self.report else {}
        if not win:
            self.orb_plot.empty(p, "No orbital energies in this file")
            return
        P = self.orb_plot
        P.begin(p)
        ax = P.fig.add_subplot(111)
        P.style_axes(ax, p)
        ax.grid(False)
        ax.grid(True, axis="y", linestyle="--", alpha=0.3, color=p["fg_muted"])
        occ_c, vir_c = "#2563eb", "#dc2626"
        for col, (spin, (lst, fr)) in enumerate(win.items()):
            x0 = col * 1.6
            for o in lst:
                c = occ_c if o.occupation > 0.1 else vir_c
                lw = 2.6 if o.index in (fr["homo_idx"], fr["lumo_idx"]) else 1.4
                ax.hlines(o.energy_ev, x0, x0 + 1.0, color=c, lw=lw)
                if o.index in (fr["homo_idx"], fr["lumo_idx"]):
                    ax.text(x0 + 1.05, o.energy_ev, f"{'HOMO' if o.index == fr['homo_idx'] else 'LUMO'} "
                            f"{o.energy_ev:.2f}", va="center", fontsize=8, color=p["fg"])
            if fr["gap_ev"] is not None:
                ax.annotate("", xy=(x0 + 0.5, fr["lumo_ev"]), xytext=(x0 + 0.5, fr["homo_ev"]),
                            arrowprops=dict(arrowstyle="<->", color=p["fg_muted"], lw=1))
                ax.text(x0 + 0.55, (fr["homo_ev"] + fr["lumo_ev"]) / 2, f"{fr['gap_ev']:.2f} eV",
                        fontsize=8, color=p["fg_muted"], va="center")
            ax.text(x0 + 0.5, 1.01, ("α" if spin == "alpha" else "β") if len(win) > 1 else "MO levels",
                    transform=ax.get_xaxis_transform(), ha="center", va="bottom", color=p["fg"], fontsize=10)
        ax.set_xlim(-0.3, 1.6 * len(win) + 0.4)
        ax.set_xticks([])
        ax.set_ylabel("Orbital energy (eV)")
        P.finish()

    def _draw_charges(self):
        if not getattr(self, "charge_plot", None):
            return
        p = self._p()
        ch = self._current_charges()
        spin_mode = self.charge_plot_kind.get().startswith("Spin")
        if spin_mode and ch and ch[0].spin is None:
            self.charge_plot.empty(p, "No spin populations (closed-shell calculation)")
            return
        if not ch:
            self.charge_plot.empty(p, "No population analysis in this file")
            return
        P = self.charge_plot
        P.begin(p)
        ax = P.fig.add_subplot(111)
        P.style_axes(ax, p)
        vals = [(c.spin if spin_mode else c.charge) for c in ch]
        colors = ["#2563eb" if v >= 0 else "#dc2626" for v in vals]
        ax.bar(range(len(vals)), vals, color=colors, width=0.8)
        ax.axhline(0, color=p["fg_muted"], lw=0.8)
        labels = [f"{c.atom}{c.index}" for c in ch]
        step = max(1, len(labels) // 40)
        ax.set_xticks(range(0, len(labels), step))
        ax.set_xticklabels(labels[::step], rotation=90, fontsize=7)
        ax.set_ylabel("Spin population" if spin_mode else "Partial charge (e)")
        ax.set_title(f"{self.charge_scheme.get()} {'spin populations' if spin_mode else 'charges'}", fontsize=10)
        P.finish()

    def _draw_uv(self):
        if not getattr(self, "uv_plot", None):
            return
        p = self._p()
        states = [s for s in (self.report or {}).get("excited_states") or [] if s.energy_ev > 0]
        if not states:
            self.uv_plot.empty(p, "No TD-DFT / CIS absorption spectrum in this file")
            return
        try:
            fw = max(0.01, float(self.uv_fwhm.get()))
        except (tk.TclError, ValueError):
            return
        sigma = fw / (2 * math.sqrt(2 * math.log(2)))
        E = np.array([s.energy_ev for s in states])
        fo = np.array([s.fosc for s in states])
        grid = np.linspace(max(0.5, E.min() - 1.5), E.max() + 1.5, 3000)
        y = (fo[None, :] * np.exp(-((grid[:, None] - E[None, :]) ** 2) / (2 * sigma ** 2))).sum(axis=1)
        P = self.uv_plot
        P.begin(p)
        ax = P.fig.add_subplot(111)
        P.style_axes(ax, p)
        if self.uv_axis.get().startswith("Wave"):
            xg, xs = 1239.841984 / grid, 1239.841984 / E
            ax.set_xlabel("Wavelength (nm)")
        else:
            xg, xs = grid, E
            ax.set_xlabel("Energy (eV)")
        ax.fill_between(xg, y, color="#a855f7", alpha=0.15)
        ax.plot(xg, y, color="#a855f7", lw=1.5)
        ax.vlines(xs, 0, fo, color=p["accent"], lw=1.2)
        ax.set_ylabel("Oscillator strength (broadened)")
        ax.set_ylim(0, max(y.max(), fo.max()) * 1.1 or 1)
        ax.set_title(f"Absorption spectrum · {len(states)} states · Gaussian FWHM {fw:.2f} eV", fontsize=10)
        P.finish()

    # ==================================================================
    # Raw output
    # ==================================================================
    def _update_raw_output(self):
        if not self.parser or self._raw_loaded:
            return
        self.raw_text.config(state=tk.NORMAL)
        self.raw_text.delete("1.0", tk.END)
        self.raw_text.insert("1.0", self.parser.content)
        self._raw_loaded = True
        self._raw_search_pos = "1.0"
        found = []
        for name, marker in RAW_SECTIONS:
            if marker in self.parser.content:
                found.append(name)
        self.raw_jump_cb.config(values=found)

    def _raw_jump(self):
        name = self.raw_jump_var.get()
        marker = dict(RAW_SECTIONS).get(name)
        if not marker or not self.parser:
            return
        self._update_raw_output()
        first = name == "Input file"
        idx = -1
        if first:
            for i, line in enumerate(self.parser.lines):
                if marker in line:
                    idx = i
                    break
        else:
            idx = self.parser._last_index(marker)
        if idx >= 0:
            pos = f"{idx + 1}.0"
            self.raw_text.see(f"{min(idx + 40, len(self.parser.lines))}.0")
            self.raw_text.see(pos)
            self.raw_text.tag_remove("cur", "1.0", tk.END)
            self.raw_text.tag_add("cur", pos, f"{pos} lineend")

    def _raw_find(self, next_only=True):
        term = self.raw_search_var.get()
        if not term or not self.parser:
            return
        self._update_raw_output()
        nocase = not self.raw_case.get()
        if not next_only:
            self.raw_text.tag_remove("hit", "1.0", tk.END)
            start, count = "1.0", 0
            while count < 5000:
                pos = self.raw_text.search(term, start, stopindex=tk.END, nocase=nocase)
                if not pos:
                    break
                end = f"{pos}+{len(term)}c"
                self.raw_text.tag_add("hit", pos, end)
                start = end
                count += 1
            self.raw_hits_var.set(f"{count}{'+' if count >= 5000 else ''} matches")
            return
        pos = self.raw_text.search(term, self._raw_search_pos, stopindex=tk.END, nocase=nocase)
        if not pos:
            pos = self.raw_text.search(term, "1.0", stopindex=tk.END, nocase=nocase)
            if not pos:
                self.raw_hits_var.set("not found")
                return
            self.raw_hits_var.set("wrapped to top")
        else:
            self.raw_hits_var.set(f"line {pos.split('.')[0]}")
        end = f"{pos}+{len(term)}c"
        self.raw_text.tag_remove("cur", "1.0", tk.END)
        self.raw_text.tag_add("cur", pos, end)
        self.raw_text.see(pos)
        self._raw_search_pos = end

    # ==================================================================
    # Copy / export
    # ==================================================================
    def _xyz_text(self, geometry, comment=""):
        lines = [str(len(geometry)), comment]
        lines += [f"{g.atom:<3} {g.x:12.6f} {g.y:12.6f} {g.z:12.6f}" for g in geometry]
        return "\n".join(lines)

    def copy_geometry(self):
        """Copy geometry to clipboard in XYZ format."""
        geometry = (self.report or {}).get("geometry") or []
        if not geometry:
            messagebox.showinfo("Info", "No geometry data available.")
            return
        self._clip(self._xyz_text(geometry, os.path.basename(self.current_file or "")), "Geometry copied (XYZ)")

    def copy_geometry_csv(self):
        """Copy geometry to clipboard as CSV (Excel format)."""
        geometry = (self.report or {}).get("geometry") or []
        if not geometry:
            messagebox.showinfo("Info", "No geometry data available.")
            return
        lines = ["Atom,X,Y,Z"] + [f"{g.atom},{g.x:.6f},{g.y:.6f},{g.z:.6f}" for g in geometry]
        self._clip("\n".join(lines), "Geometry copied — paste into Excel")

    def save_geometry_xyz(self):
        geometry = (self.report or {}).get("geometry") or []
        if not geometry:
            messagebox.showinfo("Info", "No geometry data available.")
            return
        path = self._ask_save("Save XYZ", ".xyz", [("XYZ", "*.xyz")])
        if path:
            e = self.report.get("final_energy")
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._xyz_text(geometry, f"E = {e} Eh" if e is not None else "") + "\n")
            self._toast(f"Saved {os.path.basename(path)}")

    def save_trajectory_xyz(self):
        traj = (self.report or {}).get("trajectory") or []
        if not traj:
            messagebox.showinfo("Info", "No geometries found.")
            return
        path = self._ask_save("Save trajectory", ".xyz", [("XYZ trajectory", "*.xyz")],
                              initial=os.path.splitext(os.path.basename(self.current_file))[0] + "_traj.xyz")
        if not path:
            return
        energies = self.report.get("sp_energies") or []
        with open(path, "w", encoding="utf-8") as f:
            for i, frame in enumerate(traj):
                e = f"E = {energies[i]:.8f}" if i < len(energies) else ""
                f.write(self._xyz_text(frame, f"frame {i + 1} {e}") + "\n")
        self._toast(f"Saved {len(traj)} frames")

    def send_to_input_creator(self):
        geometry = (self.report or {}).get("geometry") or []
        if not geometry:
            messagebox.showinfo("Info", "No geometry data available.")
            return
        app = getattr(self.root, "_orca_app", None)
        if not app or "Input Creator" not in getattr(app, "modules", {}):
            messagebox.showinfo("Info", "Input Creator module is not available.")
            return
        app._switch_module("Input Creator")
        ic = app.modules["Input Creator"]
        try:
            ic.geom.delete("1.0", tk.END)
            ic.geom.insert("1.0", "\n".join(f"{g.atom:<3} {g.x:12.6f} {g.y:12.6f} {g.z:12.6f}" for g in geometry))
            job = self.report.get("job", {})
            if job.get("charge") is not None and hasattr(ic, "charge"):
                ic.charge.set(str(job["charge"]))
            if job.get("multiplicity") is not None and hasattr(ic, "mult"):
                ic.mult.set(str(job["multiplicity"]))
            ic.geom.event_generate("<<Modified>>")
        except Exception as e:
            messagebox.showerror("Error", f"Could not send geometry:\n{e}")

    def copy_frequencies_csv(self):
        """Copy frequencies to clipboard as CSV (Excel format)."""
        freqs = (self.report or {}).get("frequencies") or []
        if not freqs:
            messagebox.showinfo("Info", "No frequency data available.")
            return
        lines = ["Mode,Frequency (cm-1),IR Intensity (km/mol)"]
        lines += [f"{f.mode},{f.frequency:.2f},{f.intensity:.2f}" for f in freqs]
        self._clip("\n".join(lines), "Frequencies copied — paste into Excel")

    def export_frequencies_csv(self):
        freqs = (self.report or {}).get("frequencies") or []
        if not freqs:
            messagebox.showinfo("Info", "No frequency data available.")
            return
        path = self._ask_save("Save frequencies", ".csv", [("CSV", "*.csv")],
                              initial=os.path.splitext(os.path.basename(self.current_file))[0] + "_freq.csv")
        if path:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["mode", "frequency_cm-1", "ir_intensity_km/mol", "imaginary"])
                for fr in freqs:
                    w.writerow([fr.mode, fr.frequency, fr.intensity, int(fr.frequency < 0)])
            self._toast(f"Saved {os.path.basename(path)}")

    def export_ir_spectrum_csv(self):
        data = self._ir_curve() if (_MPL_OK and self.report) else None
        if not data:
            messagebox.showinfo("Info", "No IR spectrum available.")
            return
        path = self._ask_save("Save IR spectrum", ".csv", [("CSV", "*.csv")],
                              initial=os.path.splitext(os.path.basename(self.current_file))[0] + "_ir.csv")
        if path:
            x, y, _nu, _i = data
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["wavenumber_cm-1", "intensity"])
                w.writerows(zip(np.round(x, 3), np.round(y, 6)))
            self._toast(f"Saved {os.path.basename(path)}")

    def copy_thermo(self):
        rows, _ = self._thermo_rows()
        if not rows:
            messagebox.showinfo("Info", "No thermochemistry data available.")
            return
        text = "Quantity\tEh\t" + self.unit_var.get() + "\n" + "\n".join("\t".join(r[:3]) for r in rows)
        self._clip(text, "Thermochemistry copied")

    def copy_orbitals_csv(self):
        win = self._orbital_window()
        if not win:
            messagebox.showinfo("Info", "No orbital data available.")
            return
        lines = ["spin,index,occupation,E_Eh,E_eV"]
        for spin, (lst, _fr) in win.items():
            lines += [f"{spin},{o.index},{o.occupation},{o.energy_eh},{o.energy_ev}" for o in lst]
        self._clip("\n".join(lines), "Orbitals copied")

    def export_orbitals_csv(self):
        orbs = (self.report or {}).get("orbitals") or {}
        if not orbs.get("alpha"):
            messagebox.showinfo("Info", "No orbital data available.")
            return
        path = self._ask_save("Save orbital energies", ".csv", [("CSV", "*.csv")],
                              initial=os.path.splitext(os.path.basename(self.current_file))[0] + "_orbitals.csv")
        if path:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["spin", "index", "occupation", "E_Eh", "E_eV"])
                for spin in ("alpha", "beta"):
                    for o in orbs.get(spin) or []:
                        w.writerow([spin, o.index, o.occupation, o.energy_eh, o.energy_ev])
            self._toast(f"Saved {os.path.basename(path)}")

    def copy_charges_csv(self):
        ch = self._current_charges()
        if not ch:
            messagebox.showinfo("Info", "No charge data available.")
            return
        lines = ["index,atom,charge,spin"] + [f"{c.index},{c.atom},{c.charge},{'' if c.spin is None else c.spin}" for c in ch]
        self._clip("\n".join(lines), f"{self.charge_scheme.get()} charges copied")

    def export_charges_csv(self):
        if not self._need_file():
            return
        mul = self.report.get("mulliken") or []
        loe = self.report.get("loewdin") or []
        if not mul and not loe:
            messagebox.showinfo("Info", "No charge data available.")
            return
        path = self._ask_save("Save charges", ".csv", [("CSV", "*.csv")],
                              initial=os.path.splitext(os.path.basename(self.current_file))[0] + "_charges.csv")
        if not path:
            return
        n = max(len(mul), len(loe))
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["index", "atom", "mulliken_charge", "mulliken_spin", "loewdin_charge", "loewdin_spin"])
            for i in range(n):
                m = mul[i] if i < len(mul) else None
                l = loe[i] if i < len(loe) else None
                ref = m or l
                w.writerow([ref.index, ref.atom, m.charge if m else "", m.spin if m and m.spin is not None else "",
                            l.charge if l else "", l.spin if l and l.spin is not None else ""])
        self._toast(f"Saved {os.path.basename(path)}")

    def copy_uv_csv(self):
        states = (self.report or {}).get("excited_states") or []
        if not states:
            messagebox.showinfo("Info", "No excited states available.")
            return
        lines = ["state,E_eV,wavelength_nm,fosc"] + [f"{s.state},{s.energy_ev},{s.wavelength_nm},{s.fosc}" for s in states]
        self._clip("\n".join(lines), "Excited states copied")

    def export_report(self):
        if not self._need_file():
            return
        path = self._ask_save("Save report", ".md", [("Markdown", "*.md"), ("Text", "*.txt")],
                              initial=os.path.splitext(os.path.basename(self.current_file))[0] + "_report.md")
        if not path:
            return
        r = self.report
        out = [f"# ORCA output report — {os.path.basename(self.current_file)}", "",
               f"Generated by AutoChemy on {time.strftime('%Y-%m-%d %H:%M')}", "", "## Summary", "",
               "| Property | Value |", "|---|---|"]
        out += [f"| {k} | {v} |" for k, v in self._detail_rows]
        rows, th = self._thermo_rows()
        if rows:
            out += ["", f"## Thermochemistry ({_fmt(th.get('temperature'), 2)} K)", "",
                    f"| Quantity | Eh | {self.unit_var.get()} |", "|---|---:|---:|"]
            out += [f"| {a} | {b} | {c} |" for a, b, c, _ in rows]
        freqs = r.get("frequencies") or []
        if freqs:
            out += ["", "## Vibrational frequencies (lowest 10)", "", "| Mode | cm⁻¹ | km/mol |", "|---:|---:|---:|"]
            out += [f"| {f.mode} | {f.frequency:.2f} | {f.intensity:.2f} |" for f in freqs[:10]]
        cycles = r.get("opt_cycles") or []
        if cycles:
            out += ["", f"## Optimization: {len(cycles)} cycles, converged = {r.get('geo_opt_converged')}"]
        if r.get("warnings") or r.get("errors"):
            out += ["", "## Diagnostics", ""]
            out += [f"- ERROR: {e}" for e in r.get("errors") or []]
            out += [f"- {w} (×{n})" for w, n in r.get("warnings") or []]
        geometry = r.get("geometry") or []
        if geometry:
            out += ["", "## Final geometry (Å)", "", "```", self._xyz_text(geometry), "```"]
        if r.get("input_lines"):
            out += ["", "## Input", "", "```", *r["input_lines"], "```"]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        self._toast(f"Report saved: {os.path.basename(path)}")

    # ==================================================================
    # Batch processing
    # ==================================================================
    def open_batch_dialog(self):
        """Open dialog for batch processing."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Batch Process ORCA Files")
        dialog.geometry("520x270")
        dialog.transient(self.root)
        dialog.grab_set()

        main_frame = ttk.Frame(dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(main_frame, text="Select processing mode", font=("Segoe UI", 12, "bold")).pack(pady=(0, 10))

        ttk.Button(main_frame, text="All .out files in one folder",
                   command=lambda: self._batch_pick(dialog, "single")).pack(pady=4, fill=tk.X)
        ttk.Button(main_frame, text="Multiple folders (each subfolder with .out files)",
                   command=lambda: self._batch_pick(dialog, "multi")).pack(pady=4, fill=tk.X)
        ttk.Button(main_frame, text="Folder tree — search all subfolders recursively",
                   command=lambda: self._batch_pick(dialog, "recursive")).pack(pady=4, fill=tk.X)
        ttk.Button(main_frame, text="Pick individual files…",
                   command=lambda: self._batch_pick(dialog, "files")).pack(pady=4, fill=tk.X)
        ttk.Button(main_frame, text="Cancel", command=dialog.destroy).pack(pady=(10, 0))

        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")

    def _batch_pick(self, dialog, mode):
        dialog.destroy()
        files, label_by_folder = [], False
        if mode == "files":
            files = list(filedialog.askopenfilenames(title="Select ORCA output files",
                                                     filetypes=[("ORCA Output", "*.out"), ("All", "*.*")]))
        else:
            folder = filedialog.askdirectory(title="Select folder")
            if not folder:
                return
            if mode == "single":
                files = glob.glob(os.path.join(folder, "*.out"))
            elif mode == "multi":
                label_by_folder = True
                subfolders = [f.path for f in os.scandir(folder) if f.is_dir()]
                if not subfolders:
                    messagebox.showwarning("Warning", "No subfolders found in the selected directory.")
                    return
                for sub in subfolders:
                    files.extend(glob.glob(os.path.join(sub, "*.out")))
            else:
                files = glob.glob(os.path.join(folder, "**", "*.out"), recursive=True)
                self._batch_base = folder
        files = sorted(set(files))
        if not files:
            messagebox.showwarning("Warning", "No .out files found.")
            return
        self._process_batch_files(files, label_by_folder, recursive=(mode == "recursive"))

    def _process_batch_files(self, all_files, label_by_folder=False, recursive=False):
        """Process batch files and update results."""
        progress = tk.Toplevel(self.root)
        progress.title("Processing Files")
        progress.geometry("440x110")
        progress.transient(self.root)
        progress_label = ttk.Label(progress, text=f"Processing {len(all_files)} files...")
        progress_label.pack(pady=10)
        progress_bar = ttk.Progressbar(progress, length=360, mode='determinate', maximum=len(all_files))
        progress_bar.pack(pady=6)
        progress.update()

        rows, errors = [], []
        for i, filepath in enumerate(all_files):
            try:
                p = ORCAParser(filepath)
                gibbs = p.get_gibbs_energy_info()
                s2 = p.get_s2_values()
                job = p.get_job_info()
                fr = p.get_homo_lumo()
                if label_by_folder:
                    name = os.path.basename(os.path.dirname(filepath))
                elif recursive:
                    name = os.path.relpath(filepath, getattr(self, "_batch_base", os.path.dirname(filepath)))
                else:
                    name = os.path.basename(filepath)
                rows.append({
                    "file_name": name,
                    "status": p.get_status(),
                    "job_type": p.get_job_type(),
                    "final_sp_value": p.get_final_energy(),
                    "geo_opt_conv": 1 if p.get_geo_opt_converged() else 0,
                    "img_mode": p.get_imaginary_modes(),
                    "electronic_value": p.get_electronic_energy(),
                    "total_enthalpy": gibbs["total_enthalpy"],
                    "final_gibbs_energy": gibbs["gibbs_energy"],
                    "thermal_correction": gibbs["thermal_correction"],
                    "homo_lumo_gap_ev": fr.get("gap_ev"),
                    "charge": job.get("charge"),
                    "mult": job.get("multiplicity"),
                    "time_taken(h)": p.get_total_time_hours(),
                    "s2_expectation": s2["s2_expectation"],
                    "s2_ideal": s2["s2_ideal"],
                    "s2_deviation": s2["s2_deviation"],
                    "keywords": job.get("keywords") or "",
                    "path": filepath,
                })
            except Exception as e:
                errors.append(f"{os.path.basename(filepath)}: {str(e)}")
            progress_bar['value'] = i + 1
            progress_label.config(text=f"Processing {i + 1}/{len(all_files)}: {os.path.basename(filepath)}")
            progress.update()
        progress.destroy()

        for key, rel in (("final_sp_value", "rel_energy_kcal"), ("final_gibbs_energy", "rel_gibbs_kcal")):
            vals = [r[key] for r in rows if r[key] is not None]
            emin = min(vals) if vals else None
            for r in rows:
                r[rel] = (r[key] - emin) * HARTREE_TO_KCAL if (emin is not None and r[key] is not None) else None

        self.batch_rows = rows
        self._batch_sort = (None, False)
        self._update_batch_results()
        self.notebook.select(self.batch_frame)

        if errors:
            error_msg = "\n".join(errors[:10])
            if len(errors) > 10:
                error_msg += f"\n... and {len(errors) - 10} more errors"
            messagebox.showwarning("Errors", f"Some files had errors:\n\n{error_msg}")
        else:
            self._toast(f"Processed {len(rows)} files")

    def _visible_batch_rows(self):
        flt = self.batch_filter.get().strip().lower()
        rows = self.batch_rows
        if self.batch_hide_bad.get():
            rows = [r for r in rows if r["status"] == "Normal"]
        if flt:
            rows = [r for r in rows if flt in " ".join(str(r.get(k, "")) for k in
                                                       ("file_name", "status", "job_type", "keywords")).lower()]
        key, desc = self._batch_sort
        if key:
            def sk(r):
                v = r.get(key)
                return (v is None, v if not isinstance(v, str) else v.lower())
            rows = sorted(rows, key=sk, reverse=desc)
            if desc:
                rows = [r for r in rows if r.get(key) is not None] + [r for r in rows if r.get(key) is None]
        return rows

    def _sort_batch(self, key):
        cur, desc = self._batch_sort
        self._batch_sort = (key, (not desc) if cur == key else False)
        for k, heading, _w in BATCH_COLUMNS:
            arrow = ""
            if k == key:
                arrow = " ▼" if self._batch_sort[1] else " ▲"
            self.batch_tree.heading(k, text=heading + arrow)
        self._update_batch_results()

    def _update_batch_results(self):
        """Update batch results table."""
        if not hasattr(self, "batch_tree"):
            return
        rows = self._visible_batch_rows()
        self.batch_tree.delete(*self.batch_tree.get_children())
        for r in rows:
            vals = []
            for key, _h, _w in BATCH_COLUMNS:
                v = r.get(key)
                if key in ("rel_energy_kcal", "rel_gibbs_kcal") and v is not None:
                    vals.append(f"{v:.2f}")
                elif key == "homo_lumo_gap_ev" and v is not None:
                    vals.append(f"{v:.3f}")
                else:
                    vals.append(_fmt(v, 6, none=""))
            if r["status"] == "Error":
                tag = "bad"
            elif r["status"] != "Normal" or (r.get("img_mode") or 0) > 0:
                tag = "warn"
            else:
                tag = ""
            self.batch_tree.insert("", tk.END, iid=r["path"], values=vals, tags=(tag,) if tag else ())
        n = len(self.batch_rows)
        if n:
            ok = sum(1 for r in self.batch_rows if r["status"] == "Normal")
            bad = sum(1 for r in self.batch_rows if r["status"] == "Error")
            imag = sum(1 for r in self.batch_rows if (r.get("img_mode") or 0) > 0)
            self.batch_summary_var.set(f"{n} files · {ok} normal · {bad} error · {n - ok - bad} incomplete · "
                                       f"{imag} with imaginary modes · showing {len(rows)}")
        else:
            self.batch_summary_var.set("No batch results yet.")
        self.batch_data = [[r.get(k) for k, _h, _w in BATCH_COLUMNS] for r in rows]

    def _open_batch_row(self, _event=None):
        sel = self.batch_tree.selection()
        if sel and os.path.exists(sel[0]):
            self.load_file(sel[0])

    def _batch_csv_rows(self):
        keys = [k for k, _h, _w in BATCH_COLUMNS]
        return keys, [["" if v is None else v for v in row] for row in self.batch_data]

    def export_batch_csv(self):
        """Export batch results to CSV file."""
        if not self.batch_data:
            messagebox.showinfo("Info", "No batch data to export.")
            return
        filepath = filedialog.asksaveasfilename(title="Save CSV File", defaultextension=".csv",
                                                filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if not filepath:
            return
        try:
            keys, rows = self._batch_csv_rows()
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(keys)
                writer.writerows(rows)
            self._toast(f"Exported {len(rows)} rows")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export CSV:\n{str(e)}")

    def copy_batch_csv(self):
        """Copy batch results to clipboard (tab separated for Excel)."""
        if not self.batch_data:
            messagebox.showinfo("Info", "No batch data to copy.")
            return
        keys, rows = self._batch_csv_rows()
        text = "\n".join(["\t".join(keys)] + ["\t".join(str(v) for v in row) for row in rows])
        self._clip(text, "Batch data copied — paste into Excel")

    def send_batch_to_ml(self):
        if not self.batch_data:
            messagebox.showinfo("Info", "No batch data to send.")
            return
        app = getattr(self.root, "_orca_app", None)
        if not app or "ML" not in getattr(app, "modules", {}):
            messagebox.showinfo("Info", "ML module is not available.")
            return
        path = _user_data_path("ml_from_output_viewer.csv")
        keys, rows = self._batch_csv_rows()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(keys)
            w.writerows(rows)
        app._switch_module("ML")
        ml = app.modules["ML"]
        if hasattr(ml, "load_csv"):
            ml.load_csv(path)

    def clear_batch_results(self):
        """Clear batch results."""
        if not self.batch_rows:
            return
        if messagebox.askyesno("Confirm", "Clear all batch results?"):
            self.batch_rows = []
            self.batch_data = []
            self._update_batch_results()

    def cleanup(self):
        self._stop_mode_anim()
        self._stop_traj_play()
        self.watch_var.set(False) if hasattr(self, "watch_var") else None
        super().cleanup()
