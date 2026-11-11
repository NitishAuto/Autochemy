"""
Orbital Creator — inspect ORCA quasi-restricted orbitals (QROs), check whether
the calculation can give good localized / magnetic orbitals, and generate cube
files with orca_loc + orca_plot directly from AutoChemy (or export a bundle for HPC).
"""
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from modules import ui_kit
from modules import orbital_tools as ot

try:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAS_MPL = True
except Exception:  # pragma: no cover - matplotlib is a hard requirement elsewhere
    HAS_MPL = False


SET_LABELS = {"doubly": "Doubly occupied (valence)", "singly": "Singly occupied (SOMOs)",
              "unoccupied": "Unoccupied (virtual)"}


class OrbitalCreatorModule:
    """Orbital Creator module for the AutoChemy suite."""

    def __init__(self, parent_frame):
        self.parent = parent_frame
        self.main_frame = ttk.Frame(parent_frame)
        self.ui = ui_kit.UiQueue(self.main_frame)
        self.report = None
        self.cubes = []
        self._mode = ui_kit.current_mode(parent_frame)
        self._colors = ui_kit.configure_kit_styles(ttk.Style(), self._mode)
        self._create_ui()

    # ---------- Required by main app ----------

    def get_name(self):
        return "Orbital Creator"

    def activate(self):
        self.main_frame.pack(fill=tk.BOTH, expand=True)

    def deactivate(self):
        self.main_frame.pack_forget()

    def apply_app_theme(self, ctx):
        self._mode = ctx.get("mode", "light")
        self._colors = ui_kit.configure_kit_styles(ttk.Style(), self._mode)
        self.page.recolor(self._colors["bg_main"])
        self.log.recolor(self._mode)
        self.mag_text.configure(background=self._colors["editor_bg"], foreground=self._colors["editor_fg"])
        self._style_tree()
        self._draw_diagram()

    # ======================================================
    # UI
    # ======================================================

    def _create_ui(self):
        self.page = ui_kit.ScrollablePage(self.main_frame)
        self.page.pack(fill=tk.BOTH, expand=True)
        self.page.recolor(self._colors["bg_main"])
        body = self.page.body

        ui_kit.hero(body, "Orbital Creator",
                    "Load an ORCA open-shell output, check that the orbitals are trustworthy, "
                    "pick the orbital windows on the energy diagram and generate localized "
                    "orbital cube files with orca_loc + orca_plot.")

        # ---------- Step 1: file ----------
        c1 = ui_kit.card(body, "Load ORCA output", step="1",
                         subtitle="Use the .out of a UHF/UKS job. The .qro (or .gbw) with the same name must sit next to it.")
        row = ttk.Frame(c1, style="Kit.CardInner.TFrame")
        row.pack(fill=tk.X)
        self.file_path = tk.StringVar()
        ttk.Entry(row, textvariable=self.file_path).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(row, text="Browse…", style="Kit.Ghost.TButton", command=self.select_file).pack(side=tk.LEFT)
        ttk.Button(row, text="Analyze", style="Kit.Accent.TButton", command=self.run_analysis).pack(side=tk.LEFT, padx=(8, 0))

        # ---------- Step 2: quality + diagram ----------
        c2 = ui_kit.card(body, "Orbital quality & energy diagram", step="2",
                         subtitle="Green means the orbitals are reliable; amber or red items explain what to fix "
                                  "before you trust the localized or magnetic orbitals.")
        tiles = ttk.Frame(c2, style="Kit.CardInner.TFrame")
        tiles.pack(fill=tk.X, pady=(0, 10))
        self.tiles = {}
        for key, label in (("verdict", "Verdict"), ("core", "Core MOs"), ("doubly", "Doubly occupied"),
                           ("singly", "SOMOs"), ("gap", "Frontier gap (eV)"), ("s2", "<S²> / ideal")):
            t = ui_kit.StatTile(tiles, label)
            t.pack(side=tk.LEFT, padx=(0, 8), fill=tk.X, expand=True)
            self.tiles[key] = t

        split = ttk.Frame(c2, style="Kit.CardInner.TFrame")
        split.pack(fill=tk.BOTH, expand=True)
        self.check_frame = ttk.Frame(split, style="Kit.CardInner.TFrame", width=380)
        self.check_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        ttk.Label(self.check_frame, text="Run an analysis to see the checklist.",
                  style="Kit.CardMuted.TLabel").pack(anchor="w")

        diag = ttk.Frame(split, style="Kit.CardInner.TFrame")
        diag.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        if HAS_MPL:
            self.fig = Figure(figsize=(6.2, 4.4), dpi=96)
            self.ax = self.fig.add_subplot(111)
            self.canvas = FigureCanvasTkAgg(self.fig, master=diag)
            self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.canvas.mpl_connect("pick_event", self._on_pick)
            self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.pick_info = tk.StringVar(value="Tip: click a level for details, scroll to zoom the energy window.")
        ttk.Label(diag, textvariable=self.pick_info, style="Kit.CardMuted.TLabel").pack(anchor="w", pady=(4, 0))
        self.zoom = tk.StringVar(value="Frontier")
        zrow = ttk.Frame(diag, style="Kit.CardInner.TFrame")
        zrow.pack(anchor="w", pady=(4, 0))
        for z in ("Frontier", "Valence", "All"):
            ttk.Radiobutton(zrow, text=z, value=z, variable=self.zoom, style="Kit.Card.TRadiobutton",
                            command=self._draw_diagram).pack(side=tk.LEFT, padx=(0, 10))

        # ---------- Step 3: windows ----------
        c3 = ui_kit.card(body, "Orbital windows", step="3",
                         subtitle="Auto-detected from the QRO occupations. Edit any value; the diagram updates live. "
                                  "Orbitals at or below the core cutoff are treated as core and not localized.")
        grid = ttk.Frame(c3, style="Kit.CardInner.TFrame")
        grid.pack(fill=tk.X)
        self.vars = {k: tk.StringVar() for k in ("d_start", "d_end", "s_start", "s_end", "v_start", "v_end")}
        self.core_cut = tk.StringVar(value="-1.0")
        self.n_virt = tk.StringVar(value="5")
        rows = [("doubly", "Doubly occupied", "d_start", "d_end"),
                ("singly", "Singly occupied", "s_start", "s_end"),
                ("unoccupied", "Unoccupied", "v_start", "v_end")]
        self.set_enabled = {}
        for r, (key, label, a, b) in enumerate(rows):
            self.set_enabled[key] = tk.BooleanVar(value=True)
            ttk.Checkbutton(grid, text=label, variable=self.set_enabled[key], style="Kit.Card.TCheckbutton",
                            command=self._draw_diagram).grid(row=r, column=0, sticky="w", pady=3)
            dot = tk.Label(grid, text="●", fg=self._colors["virtual" if key == "unoccupied" else key],
                           bg=self._colors["card"])
            dot.grid(row=r, column=1, padx=(4, 10))
            setattr(self, f"_dot_{key}", dot)
            ttk.Label(grid, text="from", style="Kit.CardMuted.TLabel").grid(row=r, column=2, padx=(0, 4))
            ttk.Spinbox(grid, from_=-1, to=9999, textvariable=self.vars[a], width=7,
                        command=self._draw_diagram).grid(row=r, column=3)
            ttk.Label(grid, text="to", style="Kit.CardMuted.TLabel").grid(row=r, column=4, padx=6)
            ttk.Spinbox(grid, from_=-1, to=9999, textvariable=self.vars[b], width=7,
                        command=self._draw_diagram).grid(row=r, column=5)
        ttk.Label(grid, text="Core cutoff (Eh)", style="Kit.Card.TLabel").grid(row=0, column=6, padx=(30, 6), sticky="e")
        ttk.Entry(grid, textvariable=self.core_cut, width=8).grid(row=0, column=7)
        ttk.Label(grid, text="Virtual orbitals", style="Kit.Card.TLabel").grid(row=1, column=6, padx=(30, 6), sticky="e")
        ttk.Spinbox(grid, from_=0, to=200, textvariable=self.n_virt, width=6).grid(row=1, column=7)
        ttk.Button(grid, text="Re-detect", style="Kit.Ghost.TButton", command=self._redetect)\
            .grid(row=2, column=6, columnspan=2, padx=(30, 0), sticky="e")
        for v in self.vars.values():
            v.trace_add("write", lambda *_: self._schedule_redraw())

        # ---------- Step 4: generate ----------
        c4 = ui_kit.card(body, "Generate orbitals", step="4",
                         subtitle="Runs orca_loc (localization) and orca_plot (Gaussian cube) for each enabled window. "
                                  "Without a local ORCA, export a ready-to-run bundle for your workstation or cluster.")
        opts = ttk.Frame(c4, style="Kit.CardInner.TFrame")
        opts.pack(fill=tk.X)
        self.localize = tk.BooleanVar(value=True)
        self.method = tk.StringVar(value=list(ot.LOC_METHODS)[0])
        self.grid_n = tk.StringVar(value="80")
        self.orca_dir = tk.StringVar()
        self.module_line = tk.StringVar(value="")
        ttk.Checkbutton(opts, text="Localize before plotting", variable=self.localize,
                        style="Kit.Card.TCheckbutton").grid(row=0, column=0, sticky="w")
        ttk.Label(opts, text="Method", style="Kit.Card.TLabel").grid(row=0, column=1, padx=(20, 6))
        ttk.Combobox(opts, textvariable=self.method, values=list(ot.LOC_METHODS), state="readonly",
                     width=28).grid(row=0, column=2, sticky="w")
        ttk.Label(opts, text="Grid points", style="Kit.Card.TLabel").grid(row=0, column=3, padx=(20, 6))
        ttk.Spinbox(opts, from_=40, to=200, increment=10, textvariable=self.grid_n, width=6).grid(row=0, column=4)
        ttk.Label(opts, text="ORCA folder (optional)", style="Kit.Card.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(opts, textvariable=self.orca_dir).grid(row=1, column=1, columnspan=3, sticky="ew", pady=(8, 0), padx=(20, 6))
        ttk.Button(opts, text="Browse…", style="Kit.Ghost.TButton",
                   command=lambda: self._browse_dir(self.orca_dir)).grid(row=1, column=4, pady=(8, 0))
        ttk.Label(opts, text="HPC module line", style="Kit.Card.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(opts, textvariable=self.module_line).grid(row=2, column=1, columnspan=3, sticky="ew", pady=(8, 0), padx=(20, 6))
        ttk.Label(opts, text="e.g. module load orca/5.0.4", style="Kit.CardMuted.TLabel").grid(row=2, column=4, sticky="w", pady=(8, 0))
        opts.columnconfigure(2, weight=1)

        bar = ttk.Frame(c4, style="Kit.CardInner.TFrame")
        bar.pack(fill=tk.X, pady=(12, 8))
        self.run_btn = ttk.Button(bar, text="⚡ Generate cube files here", style="Kit.Accent.TButton",
                                  command=self.generate_local)
        self.run_btn.pack(side=tk.LEFT)
        ttk.Button(bar, text="Export bundle for HPC…", style="Kit.Ghost.TButton",
                   command=self.export_bundle).pack(side=tk.LEFT, padx=8)
        self.open_btn = ttk.Button(bar, text="Open output folder", style="Kit.Ghost.TButton",
                                   command=self._open_out_dir, state="disabled")
        self.open_btn.pack(side=tk.LEFT)
        self.tool_status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.tool_status, style="Kit.CardMuted.TLabel").pack(side=tk.RIGHT)
        self.progress = ttk.Progressbar(c4, mode="indeterminate", style="Kit.Horizontal.TProgressbar")
        self.log = ui_kit.LogConsole(c4, height=7)
        self.log.pack(fill=tk.X)
        self.log.recolor(self._mode)
        self.log.write("Ready. Load an ORCA output to begin.", "info")

        # ---------- Details ----------
        c5 = ui_kit.card(body, "Details", subtitle="Full orbital table and the UHF corresponding-orbital overlaps.")
        nb = ttk.Notebook(c5)
        nb.pack(fill=tk.BOTH, expand=True)
        tf = ttk.Frame(nb)
        self.tree = ttk.Treeview(tf, columns=("idx", "occ", "class", "eh", "ev"), show="headings", height=12)
        for col, txt, w in (("idx", "MO #", 70), ("occ", "Occupation", 90), ("class", "Class", 150),
                            ("eh", "Energy (Eh)", 120), ("ev", "Energy (eV)", 120)):
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor="center")
        sb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        nb.add(tf, text="Orbital table")
        self.mag_text = tk.Text(nb, height=12, font=("Consolas", 9), borderwidth=0, padx=8, pady=6)
        nb.add(self.mag_text, text="Magnetic orbitals")
        self._style_tree()

        self._redraw_job = None
        self.out_dir = None
        self._draw_diagram()
        self._refresh_tool_status()

    # ======================================================
    # Helpers
    # ======================================================

    def _style_tree(self):
        for key in ("core", "doubly", "singly", "virtual"):
            self.tree.tag_configure(key, foreground=self._colors[key])
        for key in ("doubly", "singly", "unoccupied"):
            dot = getattr(self, f"_dot_{key}", None)
            if dot is not None:
                dot.configure(bg=self._colors["card"], fg=self._colors["virtual" if key == "unoccupied" else key])

    def _browse_dir(self, var):
        path = filedialog.askdirectory()
        if path:
            var.set(path)
            self._refresh_tool_status()

    def _refresh_tool_status(self):
        tools = ot.find_orca_tools(self.orca_dir.get())
        if all(tools.values()):
            self.tool_status.set(f"✓ ORCA tools found: {os.path.dirname(tools['orca_plot'])}")
        else:
            missing = ", ".join(k for k, v in tools.items() if not v)
            self.tool_status.set(f"ORCA not found locally ({missing}) — use Export bundle")
        return tools

    def _int(self, key, default=-1):
        try:
            return int(str(self.vars[key].get()).strip())
        except (ValueError, KeyError):
            return default

    def _current_ranges(self):
        n_core = ot.suggest_ranges(self.report, self._core_cutoff(), 0).n_core if self.report else 0
        return ot.Ranges(self._int("d_start"), self._int("d_end"), self._int("s_start"),
                         self._int("s_end"), self._int("v_start"), self._int("v_end"), n_core)

    def _core_cutoff(self):
        try:
            return float(self.core_cut.get())
        except ValueError:
            return -1.0

    def _schedule_redraw(self):
        if self._redraw_job:
            self.main_frame.after_cancel(self._redraw_job)
        self._redraw_job = self.main_frame.after(250, self._draw_diagram)

    def _classify(self, o, ranges):
        if ranges.d_start <= o.index <= ranges.d_end and self.set_enabled["doubly"].get():
            return "doubly", True
        if ranges.s_start >= 0 and ranges.s_start <= o.index <= ranges.s_end and self.set_enabled["singly"].get():
            return "singly", True
        if ranges.v_start >= 0 and ranges.v_start <= o.index <= ranges.v_end and self.set_enabled["unoccupied"].get():
            return "virtual", True
        if o.occ == 2:
            return ("core" if o.e_au <= self._core_cutoff() else "doubly"), False
        return ("singly" if o.occ == 1 else "virtual"), False

    # ======================================================
    # Energy diagram
    # ======================================================

    def _draw_diagram(self):
        self._redraw_job = None
        if not HAS_MPL:
            return
        c = self._colors
        self.fig.clear()
        self.fig.patch.set_facecolor(c["card"])
        ax = self.ax = self.fig.add_subplot(111)
        ax.set_facecolor(c["plot_bg"])
        for sp in ax.spines.values():
            sp.set_color(c["plot_grid"])
        ax.tick_params(colors=c["plot_fg"], labelsize=8)
        ax.yaxis.label.set_color(c["plot_fg"])

        rep = self.report
        if not rep or not rep.orbitals:
            ax.text(0.5, 0.5, "No orbitals loaded", ha="center", va="center", color=c["fg_muted"],
                    transform=ax.transAxes, fontsize=12)
            ax.set_xticks([])
            ax.set_yticks([])
            self.canvas.draw_idle()
            return

        ranges = self._current_ranges()
        ys = [o.e_ev for o in rep.orbitals]
        occ_top = max([o.e_ev for o in rep.orbitals if o.occ > 0], default=0.0)
        lumo = rep.lumo
        zoom = self.zoom.get()
        if zoom == "Frontier":
            lo, hi = occ_top - 12, (lumo.e_ev if lumo else occ_top) + 8
        elif zoom == "Valence":
            lo, hi = self._core_cutoff() * ot.HARTREE_TO_EV - 2, (lumo.e_ev if lumo else occ_top) + 12
        else:
            lo, hi = min(ys) - 5, max(ys) + 5
        self._ylim = [lo, hi]

        # Group near-degenerate levels side by side
        visible = [o for o in rep.orbitals if lo <= o.e_ev <= hi]
        slots, last_e, k = {}, None, 0
        for o in visible:
            k = k + 1 if (last_e is not None and abs(o.e_ev - last_e) < 0.05) else 0
            slots[o.index] = k
            last_e = o.e_ev

        self._levels = {}
        for o in visible:
            cls, selected = self._classify(o, ranges)
            x0 = 0.15 + 0.36 * slots[o.index]
            line, = ax.plot([x0, x0 + 0.3], [o.e_ev, o.e_ev], color=c[cls],
                            lw=3.2 if selected else 1.4, alpha=1.0 if selected else 0.45,
                            solid_capstyle="round", picker=6)
            self._levels[line] = o
            # electrons
            xm = x0 + 0.15
            if o.occ == 2:
                ax.annotate("", (xm - 0.04, o.e_ev + 0.35), (xm - 0.04, o.e_ev - 0.15),
                            arrowprops=dict(arrowstyle="-|>", color=c[cls], lw=0.9, alpha=0.8))
                ax.annotate("", (xm + 0.04, o.e_ev - 0.35), (xm + 0.04, o.e_ev + 0.15),
                            arrowprops=dict(arrowstyle="-|>", color=c[cls], lw=0.9, alpha=0.8))
            elif o.occ == 1:
                ax.annotate("", (xm, o.e_ev + 0.35), (xm, o.e_ev - 0.15),
                            arrowprops=dict(arrowstyle="-|>", color=c[cls], lw=0.9))

        # Window bands + labels
        for key, cls, a, b in (("doubly", "doubly", ranges.d_start, ranges.d_end),
                               ("singly", "singly", ranges.s_start, ranges.s_end),
                               ("unoccupied", "virtual", ranges.v_start, ranges.v_end)):
            if a < 0 or b < a or not self.set_enabled[key].get():
                continue
            es = [o.e_ev for o in rep.orbitals if a <= o.index <= b]
            if not es:
                continue
            ax.axhspan(min(es) - 0.15, max(es) + 0.15, xmin=0.0, xmax=0.035, color=c[cls], alpha=0.9)
            mid = min(max((min(es) + max(es)) / 2, lo + 0.5), hi - 0.5)
            ax.text(1.98, mid, f"{SET_LABELS[key].split(' (')[0]}\n#{a}–{b}", ha="right", va="center",
                    fontsize=8, color=c[cls], weight="bold")

        homo = [o for o in rep.orbitals if o.occ > 0]
        if homo:
            ax.text(-0.02, homo[-1].e_ev, "SOMO " if homo[-1].occ == 1 else "HOMO ", ha="right",
                    va="center", fontsize=8, color=c["plot_fg"])
        if lumo and lo <= lumo.e_ev <= hi:
            ax.text(-0.02, lumo.e_ev, "LUMO ", ha="right", va="center", fontsize=8, color=c["plot_fg"])
            gap = ot.frontier_gap(rep)
            if gap is not None:
                ax.annotate("", (1.45, lumo.e_ev), (1.45, homo[-1].e_ev),
                            arrowprops=dict(arrowstyle="<->", color=c["fg_muted"], lw=0.9))
                ax.text(1.5, (lumo.e_ev + homo[-1].e_ev) / 2, f"{gap:.2f} eV", va="center",
                        fontsize=8, color=c["fg_muted"])
        cut = self._core_cutoff() * ot.HARTREE_TO_EV
        if lo <= cut <= hi:
            ax.axhline(cut, color=c["core"], ls=":", lw=1)
            ax.text(1.98, cut, "core cutoff", ha="right", va="bottom", fontsize=7, color=c["core"])

        ax.set_xlim(-0.35, 2.0)
        ax.set_ylim(lo, hi)
        ax.set_xticks([])
        ax.set_ylabel("Orbital energy (eV)")
        ax.grid(axis="y", color=c["plot_grid"], lw=0.6)
        ax.set_title(f"{rep.source} orbitals — {os.path.basename(rep.path)}", fontsize=9, color=c["plot_fg"])
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _on_pick(self, event):
        o = self._levels.get(event.artist) if hasattr(self, "_levels") else None
        if o:
            occ = {2: "doubly occupied", 1: "singly occupied", 0: "unoccupied"}[o.occ]
            self.pick_info.set(f"MO #{o.index} · {occ} · {o.e_au:.5f} Eh · {o.e_ev:.3f} eV")

    def _on_scroll(self, event):
        if not hasattr(self, "_ylim") or event.ydata is None:
            return
        lo, hi = self._ylim
        f = 0.85 if event.button == "up" else 1.18
        lo = event.ydata - (event.ydata - lo) * f
        hi = event.ydata + (hi - event.ydata) * f
        self._ylim = [lo, hi]
        self.ax.set_ylim(lo, hi)
        self.canvas.draw_idle()

    # ======================================================
    # Actions
    # ======================================================

    def select_file(self):
        path = filedialog.askopenfilename(filetypes=[("ORCA output files", "*.out *.log"), ("All files", "*.*")])
        if path:
            self.file_path.set(path)
            self.run_analysis()

    def run_analysis(self):
        path = self.file_path.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showerror("Orbital Creator", "Please select a valid ORCA .out file.")
            return
        try:
            rep = ot.analyze_output(path)
        except Exception as e:
            messagebox.showerror("Orbital Creator", f"Could not read the output:\n{e}")
            return
        self.report = rep
        self._redetect(log=False)
        self._fill_checks()
        self._fill_tiles()
        self._fill_table()
        self.mag_text.delete("1.0", tk.END)
        self.mag_text.insert(tk.END, "\n".join(rep.magnetic_text) or "No UHF corresponding-orbital section found.")
        self.log.write(f"Loaded {os.path.basename(path)}: {len(rep.orbitals)} {rep.source or 'no'} orbitals.",
                       "ok" if rep.orbitals else "bad")

    def _redetect(self, log=True):
        if not self.report:
            return
        try:
            nv = int(self.n_virt.get())
        except ValueError:
            nv = 5
        r = ot.suggest_ranges(self.report, self._core_cutoff(), nv)
        for k in self.vars:
            self.vars[k].set(str(getattr(r, k)))
        self.set_enabled["singly"].set(r.s_start >= 0)
        self.tiles["core"].set(r.n_core)
        self._draw_diagram()
        if log:
            self.log.write("Orbital windows re-detected.", "info")

    def _fill_checks(self):
        for w in self.check_frame.winfo_children():
            w.destroy()
        icons = {"ok": "✔", "warn": "▲", "bad": "✖", "info": "ℹ"}
        for chk in self.report.checks:
            row = ttk.Frame(self.check_frame, style="Kit.CardInner.TFrame")
            row.pack(fill=tk.X, anchor="w", pady=2)
            ttk.Label(row, text=icons[chk.status], style=f"Kit.{chk.status}.TLabel", width=2).pack(side=tk.LEFT, anchor="n")
            txt = ttk.Frame(row, style="Kit.CardInner.TFrame")
            txt.pack(side=tk.LEFT, fill=tk.X)
            ttk.Label(txt, text=chk.title, style="Kit.Card.TLabel", wraplength=330).pack(anchor="w")
            if chk.detail:
                ttk.Label(txt, text=chk.detail, style="Kit.CardMuted.TLabel", wraplength=330,
                          justify="left").pack(anchor="w")

    def _fill_tiles(self):
        rep = self.report
        statuses = [c.status for c in rep.checks]
        verdict = "Poor" if "bad" in statuses else ("Check" if "warn" in statuses else "Good")
        self.tiles["verdict"].set({"Good": "✔ Good", "Check": "▲ Review", "Poor": "✖ Fix first"}[verdict])
        self.tiles["doubly"].set(len(rep.by_occ(2)))
        self.tiles["singly"].set(len(rep.somos))
        gap = ot.frontier_gap(rep)
        self.tiles["gap"].set(f"{gap:.2f}" if gap is not None else "—")
        self.tiles["s2"].set(f"{rep.s2:.3f} / {rep.s2_ideal:.3f}" if rep.s2 is not None and rep.s2_ideal is not None else "—")

    def _fill_table(self):
        self.tree.delete(*self.tree.get_children())
        cut = self._core_cutoff()
        for o in self.report.orbitals:
            cls = ("core" if o.e_au <= cut else "doubly") if o.occ == 2 else ("singly" if o.occ == 1 else "virtual")
            label = {"core": "Core", "doubly": "Doubly occupied", "singly": "Singly occupied (SOMO)",
                     "virtual": "Unoccupied"}[cls]
            self.tree.insert("", tk.END, values=(o.index, o.occ, label, f"{o.e_au:.6f}", f"{o.e_ev:.3f}"), tags=(cls,))

    # ---------- generation ----------

    def _build_job(self, out_dir):
        if not self.report or not self.report.orbitals:
            raise ValueError("Analyze an ORCA output first.")
        if not self.report.orbital_file:
            raise ValueError("No .qro or .gbw file found next to the output file.")
        ranges = self._current_ranges()
        sets = [k for k in ("doubly", "singly", "unoccupied") if self.set_enabled[k].get()]
        windows = ranges.as_sets()
        sets = [s for s in sets if s in windows]
        if not sets:
            raise ValueError("No valid orbital window is enabled.")
        idx = {o.index for o in self.report.orbitals}
        for s in sets:
            a, b = windows[s]
            if a not in idx or b not in idx:
                raise ValueError(f"The {s} window #{a}–{b} is outside the orbitals in this file.")
        try:
            grid = max(20, min(300, int(self.grid_n.get())))
        except ValueError:
            grid = 80
        return ot.OrbitalJob(orbital_file=self.report.orbital_file, ranges=ranges, sets=sets,
                             localize=self.localize.get(), method=ot.LOC_METHODS[self.method.get()],
                             grid=grid, out_dir=out_dir)

    def _default_out_dir(self):
        return os.path.join(os.path.dirname(os.path.abspath(self.report.path)), "orbitals")

    def export_bundle(self):
        try:
            if not self.report:
                raise ValueError("Analyze an ORCA output first.")
            out_dir = filedialog.askdirectory(title="Folder for the orbital bundle",
                                              initialdir=os.path.dirname(self.report.path))
            if not out_dir:
                return
            out_dir = os.path.join(out_dir, "orbitals")
            job = self._build_job(out_dir)
            steps = ot.prepare_job_folder(job)
            scripts = ot.write_run_scripts(job, steps, self.module_line.get())
        except Exception as e:
            messagebox.showerror("Orbital Creator", str(e))
            return
        self.out_dir = out_dir
        self.open_btn.configure(state="normal")
        self.log.write(f"Bundle written to {out_dir}", "ok")
        for st in steps:
            self.log.write(f"  {st['set']}: orbitals {st['first']}–{st['last']} ({len(st['orbitals'])} cubes)")
        self.log.write("Run  bash run_orbitals.sh  (Linux/HPC) or  run_orbitals.bat  (Windows).", "info")
        _ = scripts

    def generate_local(self):
        tools = self._refresh_tool_status()
        if not all(tools.values()):
            if messagebox.askyesno("ORCA not found",
                                   "orca_loc / orca_plot were not found on this computer.\n\n"
                                   "Set the ORCA folder above, or export a bundle to run on your HPC.\n\n"
                                   "Export a bundle now?"):
                self.export_bundle()
            return
        try:
            job = self._build_job(self._default_out_dir())
        except Exception as e:
            messagebox.showerror("Orbital Creator", str(e))
            return
        self.run_btn.configure(state="disabled")
        self.progress.pack(fill=tk.X, pady=(0, 6), before=self.log)
        self.progress.start(12)
        self.log.write(f"Generating orbitals in {job.out_dir} …", "info")

        def log_safe(msg, tag=""):
            self.ui.post(lambda: self.log.write(msg, tag))

        def worker():
            try:
                cubes = ot.run_job_locally(job, tools, log_safe)
                ot.write_run_scripts(job, job.plan(), self.module_line.get())
                err = None
            except Exception as e:
                cubes, err = [], e
            self.ui.post(lambda: self._generation_done(job, cubes, err))

        threading.Thread(target=worker, daemon=True).start()

    def _generation_done(self, job, cubes, err):
        self.progress.stop()
        self.progress.pack_forget()
        self.run_btn.configure(state="normal")
        self.out_dir = job.out_dir
        self.open_btn.configure(state="normal")
        if err:
            self.log.write(f"Error: {err}", "bad")
            return
        self.cubes = cubes
        tag = "ok" if cubes else "warn"
        self.log.write(f"Finished: {len(cubes)} cube file(s). Open them in Avogadro, VMD, Chemcraft or IboView.", tag)

    def _open_out_dir(self):
        if self.out_dir and os.path.isdir(self.out_dir):
            if os.name == "nt":
                os.startfile(self.out_dir)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", self.out_dir])
