"""
DIA — distortion/interaction (activation-strain) analysis along a reaction path.

Step 1  load an IRC / scan trajectory (multi-frame xyz)
Step 2  define the two fragments (auto-detected from where the path separates)
Step 3  write fragment + complex single-point ORCA inputs (coordinates embedded)
Step 4  read the finished single points and plot ΔE = ΔE_dist + ΔE_int
"""
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from modules.base_module import BaseModule
from modules import ui_kit

try:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    HAS_MPL = True
except Exception:  # pragma: no cover
    HAS_MPL = False

DEFAULT_TEMPLATE = """! B3LYP D3BJ def2-SVP TightSCF

%pal nprocs 4 end
%maxcore 2000

%scf
  maxiter 300
end
"""

CURVES = (("dE", "ΔE (total)", "#111827", "-"),
          ("dis_total", "ΔE dist (total)", "#16a34a", "-"),
          ("intr", "ΔE int", "#dc2626", "-"),
          ("dis_1", "ΔE dist frag 1", "#2563eb", "--"),
          ("dis_2", "ΔE dist frag 2", "#f59e0b", "--"))


class DIAAnalysisModule(BaseModule):

    def get_name(self):
        return "DIA"

    # ============================================================
    def create_ui(self):
        self._mode = ui_kit.current_mode(self.parent_frame)
        self._colors = ui_kit.configure_kit_styles(ttk.Style(), self._mode)
        self.main_frame = ttk.Frame(self.parent_frame)
        self.ui = ui_kit.UiQueue(self.main_frame)
        self.frames, self.frame_energies = [], []
        self.dis_int_df = None
        self.ts = None

        self.page = ui_kit.ScrollablePage(self.main_frame)
        self.page.pack(fill=tk.BOTH, expand=True)
        self.page.recolor(self._colors["bg_main"])
        body = self.page.body

        ui_kit.hero(body, "Distortion / Interaction Analysis",
                    "Split a reaction path into two fragments, run single points and see where the barrier "
                    "comes from: ΔE = ΔE_distortion + ΔE_interaction (activation-strain model).")

        self._build_path_card(body)
        self._build_fragment_card(body)
        self._build_prepare_card(body)
        self._build_analyze_card(body)
        self._build_results_card(body)

        self.status = tk.StringVar(value="Ready")
        self.update_ui()

    # ---------- step 1 ----------
    def _build_path_card(self, body):
        c = ui_kit.card(body, "Reaction path", step="1",
                        subtitle="Multi-frame xyz from an IRC, NEB or relaxed scan (ORCA *_IRC_Full_trj.xyz, "
                                 "xtb/CREST trajectories…). Nearly identical frames are skipped using the RMSD threshold.")
        row = ttk.Frame(c, style="Kit.CardInner.TFrame")
        row.pack(fill=tk.X)
        self.file_var = tk.StringVar()
        self.rmsd = tk.StringVar(value="0.0005")
        ttk.Entry(row, textvariable=self.file_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(row, text="Browse…", style="Kit.Ghost.TButton", command=self.browse).pack(side=tk.LEFT)
        ttk.Label(row, text="RMSD (Å)", style="Kit.Card.TLabel").pack(side=tk.LEFT, padx=(14, 4))
        ttk.Entry(row, textvariable=self.rmsd, width=9).pack(side=tk.LEFT)
        ttk.Button(row, text="Load path", style="Kit.Accent.TButton", command=self.load_path).pack(side=tk.LEFT, padx=(10, 0))
        tiles = ttk.Frame(c, style="Kit.CardInner.TFrame")
        tiles.pack(fill=tk.X, pady=(10, 0))
        self.path_tiles = {}
        for k, lbl in (("frames", "Frames kept"), ("atoms", "Atoms"), ("energies", "Energies in file"),
                       ("pieces", "Separated end point")):
            t = ui_kit.StatTile(tiles, lbl)
            t.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
            self.path_tiles[k] = t

    # ---------- step 2 ----------
    def _build_fragment_card(self, body):
        c = ui_kit.card(body, "Fragments", step="2",
                        subtitle="Fragment 1 atoms (1-based, ranges allowed: 1-8, 12). Fragment 2 is everything else. "
                                 "Use Auto-detect to take the fragments from the frame where the system falls apart.")
        row = ttk.Frame(c, style="Kit.CardInner.TFrame")
        row.pack(fill=tk.X)
        self.frag_list = tk.StringVar(value="")
        ttk.Label(row, text="Fragment 1 atoms", style="Kit.Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.frag_list).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        ttk.Button(row, text="✨ Auto-detect", style="Kit.Ghost.TButton", command=self.auto_fragments).pack(side=tk.LEFT)
        self.frag2_info = tk.StringVar(value="Fragment 2: —")
        ttk.Label(c, textvariable=self.frag2_info, style="Kit.CardMuted.TLabel", wraplength=880).pack(anchor="w", pady=(6, 8))

        grid = ttk.Frame(c, style="Kit.CardInner.TFrame")
        grid.pack(fill=tk.X)
        self.f1_charge, self.f1_spin = tk.StringVar(value="0"), tk.StringVar(value="1")
        self.f2_charge, self.f2_spin = tk.StringVar(value="0"), tk.StringVar(value="1")
        self.c_charge, self.c_spin = tk.StringVar(value="0"), tk.StringVar(value="1")
        self.parity_labels = {}
        for r, (name, ch, sp) in enumerate((("Fragment 1", self.f1_charge, self.f1_spin),
                                            ("Fragment 2", self.f2_charge, self.f2_spin),
                                            ("Complex", self.c_charge, self.c_spin))):
            ttk.Label(grid, text=name, style="Kit.Card.TLabel", width=12).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Label(grid, text="charge", style="Kit.CardMuted.TLabel").grid(row=r, column=1, padx=(0, 4))
            ttk.Spinbox(grid, from_=-10, to=10, textvariable=ch, width=5).grid(row=r, column=2)
            ttk.Label(grid, text="multiplicity", style="Kit.CardMuted.TLabel").grid(row=r, column=3, padx=(14, 4))
            ttk.Spinbox(grid, from_=1, to=11, textvariable=sp, width=5).grid(row=r, column=4)
            lbl = ttk.Label(grid, text="", style="Kit.info.TLabel")
            lbl.grid(row=r, column=5, sticky="w", padx=(14, 0))
            self.parity_labels[name] = lbl
        for v in (self.frag_list, self.f1_charge, self.f1_spin, self.f2_charge, self.f2_spin, self.c_charge, self.c_spin):
            v.trace_add("write", lambda *_: self._update_fragment_info())

    # ---------- step 3 ----------
    def _build_prepare_card(self, body):
        c = ui_kit.card(body, "Prepare single points", step="3",
                        subtitle="Fragments are frozen at the path geometry, so these must be single points (no Opt). "
                                 "Charge, multiplicity and coordinates are added automatically for each job.")
        self.input_frame = c
        self.input_text = tk.Text(c, height=9, font=("Consolas", 10), borderwidth=0, padx=10, pady=8, undo=True)
        self.input_text.pack(fill=tk.X)
        self.input_text.insert("1.0", DEFAULT_TEMPLATE)
        row = ttk.Frame(c, style="Kit.CardInner.TFrame")
        row.pack(fill=tk.X, pady=(10, 0))
        self.out_dir = tk.StringVar()
        self.include_complex = tk.BooleanVar(value=True)
        ttk.Label(row, text="Output folder", style="Kit.Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.out_dir).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        ttk.Button(row, text="Browse…", style="Kit.Ghost.TButton",
                   command=lambda: self.browse_folder(self.out_dir)).pack(side=tk.LEFT)
        row2 = ttk.Frame(c, style="Kit.CardInner.TFrame")
        row2.pack(fill=tk.X, pady=(8, 0))
        ttk.Checkbutton(row2, text="Also write complex single points (same level of theory — recommended)",
                        variable=self.include_complex, style="Kit.Card.TCheckbutton").pack(side=tk.LEFT)
        ttk.Button(row2, text="Write ORCA inputs", style="Kit.Accent.TButton",
                   command=self.prepare_inputs).pack(side=tk.RIGHT)

    # ---------- step 4 ----------
    def _build_analyze_card(self, body):
        c = ui_kit.card(body, "Analyze finished single points", step="4",
                        subtitle="Point to the DIA folder from step 3 (with fragment1/, fragment2/, complex/). "
                                 "Output files are matched to frames by name (frag1_<i>.out, frag2_<i>.out, complete_<i>.out).")
        g = ttk.Frame(c, style="Kit.CardInner.TFrame")
        g.pack(fill=tk.X)
        self.dia_root = tk.StringVar()
        self.frag1_path, self.frag2_path, self.complex_path = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for r, (lbl, var) in enumerate((("DIA folder", self.dia_root), ("Fragment 1 folder", self.frag1_path),
                                        ("Fragment 2 folder", self.frag2_path), ("Complex folder", self.complex_path))):
            ttk.Label(g, text=lbl, style="Kit.Card.TLabel").grid(row=r, column=0, sticky="w", pady=2)
            ttk.Entry(g, textvariable=var).grid(row=r, column=1, sticky="ew", padx=8, pady=2)
            ttk.Button(g, text="Browse…", style="Kit.Ghost.TButton",
                       command=lambda v=var: self.browse_folder(v)).grid(row=r, column=2, pady=2)
        g.columnconfigure(1, weight=1)
        self.dia_root.trace_add("write", lambda *_: self._fill_subfolders())

        e = ttk.Frame(c, style="Kit.CardInner.TFrame")
        e.pack(fill=tk.X, pady=(10, 0))
        self.e1, self.e2 = tk.StringVar(), tk.StringVar()
        for col, (lbl, var) in enumerate((("E(frag 1, optimized) / Eh", self.e1), ("E(frag 2, optimized) / Eh", self.e2))):
            ttk.Label(e, text=lbl, style="Kit.Card.TLabel").grid(row=0, column=col * 3, sticky="w", padx=(0 if col == 0 else 20, 6))
            ttk.Entry(e, textvariable=var, width=18).grid(row=0, column=col * 3 + 1)
            ttk.Button(e, text="From .out…", style="Kit.Ghost.TButton",
                       command=lambda v=var: self._energy_from_out(v)).grid(row=0, column=col * 3 + 2, padx=4)

        m = ttk.Frame(c, style="Kit.CardInner.TFrame")
        m.pack(fill=tk.X, pady=(10, 0))
        self.mode = tk.StringVar(value="b")
        self.coord_atoms = tk.StringVar(value="")
        ttk.Label(m, text="Reaction coordinate", style="Kit.Card.TLabel").pack(side=tk.LEFT)
        for txt, val in (("Bond", "b"), ("Angle", "a"), ("Dihedral", "d")):
            ttk.Radiobutton(m, text=txt, value=val, variable=self.mode, style="Kit.Card.TRadiobutton").pack(side=tk.LEFT, padx=6)
        ttk.Label(m, text="atoms", style="Kit.CardMuted.TLabel").pack(side=tk.LEFT, padx=(10, 4))
        ttk.Entry(m, textvariable=self.coord_atoms, width=14).pack(side=tk.LEFT)
        self.complex_source = tk.StringVar(value="sp")
        self.unit = tk.StringVar(value="kcal/mol")
        ttk.Label(m, text="Units", style="Kit.Card.TLabel").pack(side=tk.LEFT, padx=(20, 4))
        ttk.Combobox(m, textvariable=self.unit, values=["kcal/mol", "kJ/mol", "eV"], width=9,
                     state="readonly").pack(side=tk.LEFT)

        s = ttk.Frame(c, style="Kit.CardInner.TFrame")
        s.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(s, text="Complex energy from", style="Kit.Card.TLabel").pack(side=tk.LEFT)
        ttk.Radiobutton(s, text="complex single points", value="sp", variable=self.complex_source,
                        style="Kit.Card.TRadiobutton").pack(side=tk.LEFT, padx=6)
        ttk.Radiobutton(s, text="energies in the xyz file", value="xyz", variable=self.complex_source,
                        style="Kit.Card.TRadiobutton").pack(side=tk.LEFT, padx=6)
        self.run_btn = ttk.Button(s, text="Run DIA analysis", style="Kit.Accent.TButton", command=self.run)
        self.run_btn.pack(side=tk.RIGHT)

    # ---------- results ----------
    def _build_results_card(self, body):
        c = ui_kit.card(body, "Results", subtitle="Values at the highest point of ΔE (the transition state on an IRC).")
        tiles = ttk.Frame(c, style="Kit.CardInner.TFrame")
        tiles.pack(fill=tk.X, pady=(0, 10))
        self.res_tiles = {}
        for k, lbl in (("dE", "ΔE‡"), ("dis_total", "ΔE‡ distortion"), ("intr", "ΔE‡ interaction"),
                       ("split", "Distortion frag 1 / frag 2"), ("coord", "TS coordinate")):
            t = ui_kit.StatTile(tiles, lbl)
            t.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
            self.res_tiles[k] = t
        self.verdict = tk.StringVar(value="")
        ttk.Label(c, textvariable=self.verdict, style="Kit.info.TLabel", wraplength=880).pack(anchor="w", pady=(0, 8))

        toggles = ttk.Frame(c, style="Kit.CardInner.TFrame")
        toggles.pack(fill=tk.X)
        self.curve_on = {}
        for key, label, _, _ in CURVES:
            self.curve_on[key] = tk.BooleanVar(value=True)
            ttk.Checkbutton(toggles, text=label, variable=self.curve_on[key], style="Kit.Card.TCheckbutton",
                            command=self._draw_plot).pack(side=tk.LEFT, padx=(0, 10))
        self.reverse_x = tk.BooleanVar(value=False)
        self.show_ts = tk.BooleanVar(value=True)
        ttk.Checkbutton(toggles, text="Reverse x", variable=self.reverse_x, style="Kit.Card.TCheckbutton",
                        command=self._draw_plot).pack(side=tk.RIGHT)
        ttk.Checkbutton(toggles, text="Mark TS", variable=self.show_ts, style="Kit.Card.TCheckbutton",
                        command=self._draw_plot).pack(side=tk.RIGHT, padx=10)

        plot_host = ttk.Frame(c, style="Kit.CardInner.TFrame")
        plot_host.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        if HAS_MPL:
            self.fig = Figure(figsize=(8.5, 4.6), dpi=96)
            self.canvas = FigureCanvasTkAgg(self.fig, master=plot_host)
            self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            tb_host = ttk.Frame(plot_host)
            tb_host.pack(fill=tk.X)
            self.toolbar = NavigationToolbar2Tk(self.canvas, tb_host, pack_toolbar=False)
            self.toolbar.update()
            self.toolbar.pack(side=tk.LEFT)

        bar = ttk.Frame(c, style="Kit.CardInner.TFrame")
        bar.pack(fill=tk.X, pady=(10, 6))
        ttk.Button(bar, text="Download CSV", style="Kit.Ghost.TButton", command=self.download_csv).pack(side=tk.LEFT)
        ttk.Button(bar, text="Save figure…", style="Kit.Ghost.TButton", command=self.save_figure).pack(side=tk.LEFT, padx=8)
        self.log = ui_kit.LogConsole(c, height=6)
        self.log.pack(fill=tk.X)
        self.log.recolor(self._mode)
        self.log.write("Ready. Load a reaction path to begin.", "info")
        self._draw_plot()

    # ============================================================
    def apply_app_theme(self, ctx):
        if self.main_frame is None:
            return
        self._mode = ctx.get("mode", "light")
        self._colors = ui_kit.configure_kit_styles(ttk.Style(), self._mode)
        self.page.recolor(self._colors["bg_main"])
        self.input_text.configure(background=self._colors["editor_bg"], foreground=self._colors["editor_fg"],
                                  insertbackground=self._colors["editor_insert"])
        self.log.recolor(self._mode)
        if HAS_MPL and hasattr(self, "toolbar"):
            for w in [self.toolbar] + list(self.toolbar.winfo_children()):
                try:
                    w.configure(background=self._colors["card"])
                except tk.TclError:
                    pass
        self._draw_plot()

    def update_ui(self):
        """Kept for compatibility with the old two-mode layout; all steps are now always visible."""
        self.input_text.configure(background=self._colors["editor_bg"], foreground=self._colors["editor_fg"],
                                  insertbackground=self._colors["editor_insert"])

    # ============================================================
    def browse(self):
        path = filedialog.askopenfilename(filetypes=[("XYZ files", "*.xyz"), ("All files", "*.*")])
        if path:
            self.full_path = path
            self.file_var.set(path)
            self.load_path()

    def browse_folder(self, var):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def _fill_subfolders(self):
        root = self.dia_root.get().strip()
        if not root:
            return
        for var, name in ((self.frag1_path, "fragment1"), (self.frag2_path, "fragment2"), (self.complex_path, "complex")):
            var.set(os.path.join(root, name))

    def _energy_from_out(self, var):
        path = filedialog.askopenfilename(title="Optimized fragment output",
                                          filetypes=[("ORCA output", "*.out *.log"), ("All files", "*.*")])
        if not path:
            return
        from modules.codes_dia.orca_input_generator_for_xyz import file_energies
        e = file_energies(path)
        try:
            e = float(e)
        except (TypeError, ValueError):
            e = 0.0
        if e == 0.0:
            messagebox.showerror("DIA", "No FINAL SINGLE POINT ENERGY found in that file.")
            return
        var.set(f"{e:.8f}")
        self.log.write(f"Read {e:.8f} Eh from {os.path.basename(path)}", "ok")

    # ============================================================
    def load_path(self):
        path = self.file_var.get().strip()
        if not os.path.isfile(path):
            messagebox.showerror("DIA", "Please choose a multi-frame .xyz file.")
            return
        try:
            from modules.codes_dia.xyz_extractor import read_multi_xyz
            self.frames, self.frame_energies = read_multi_xyz(path, float(self.rmsd.get()))
        except Exception as e:
            messagebox.showerror("DIA", f"Could not read the trajectory:\n{e}")
            return
        self.full_path = path
        n_e = sum(e is not None for e in self.frame_energies)
        self.path_tiles["frames"].set(len(self.frames))
        self.path_tiles["atoms"].set(len(self.frames[0]))
        self.path_tiles["energies"].set(f"{n_e}/{len(self.frames)}")
        if not self.out_dir.get():
            stem = os.path.splitext(os.path.basename(path))[0]
            self.out_dir.set(os.path.join(os.path.dirname(path), f"DIA_{stem}"))
        if not self.dia_root.get():
            self.dia_root.set(self.out_dir.get())
        self.log.write(f"Loaded {len(self.frames)} frames × {len(self.frames[0])} atoms from {os.path.basename(path)}", "ok")
        self.auto_fragments(quiet=True)
        self._update_fragment_info()

    def auto_fragments(self, quiet=False):
        if not self.frames:
            if not quiet:
                messagebox.showinfo("DIA", "Load a reaction path first.")
            return
        from modules.codes_dia.auto_bonds_frag import suggest_fragments
        frag1, idx, changed = suggest_fragments(self.frames)
        if changed:
            bond = changed[0]
            if not self.coord_atoms.get().strip():
                self.mode.set("b")
                self.coord_atoms.set(f"{bond[0]},{bond[1]}")
            self.log.write("Bonds that change along the path: " +
                           ", ".join(f"{a}–{b}" for a, b in changed[:6]), "info")
        if frag1 is None:
            self.path_tiles["pieces"].set("none")
            if not quiet:
                messagebox.showinfo("DIA", "No frame of the path separates into two pieces.\n"
                                           "Enter the fragment 1 atoms by hand.")
            return
        self.path_tiles["pieces"].set(f"frame {idx}")
        if quiet and self.frag_list.get().strip():
            return
        self.frag_list.set(ui_kit.compress_atom_list(frag1))
        self.log.write(f"Fragments detected from frame {idx}: fragment 1 = {ui_kit.compress_atom_list(frag1)}", "ok")

    def _fragments(self):
        n = len(self.frames[0]) if self.frames else 0
        f1 = ui_kit.parse_atom_list(self.frag_list.get())
        if not f1:
            raise ValueError("Enter the fragment 1 atoms.")
        if n and (min(f1) < 1 or max(f1) > n):
            raise ValueError(f"Fragment 1 atoms must be between 1 and {n}.")
        f2 = [a for a in range(1, n + 1) if a not in set(f1)]
        if n and not f2:
            raise ValueError("Fragment 2 would be empty.")
        return f1, f2

    def _update_fragment_info(self):
        if not self.frames:
            return
        try:
            f1, f2 = self._fragments()
        except ValueError as e:
            self.frag2_info.set(str(e))
            return
        frame = self.frames[0]
        elem = frame["elem"].tolist()

        def formula(atoms):
            counts = {}
            for a in atoms:
                counts[elem[a - 1]] = counts.get(elem[a - 1], 0) + 1
            order = sorted(counts, key=lambda s: (s != "C", s != "H", s))
            return "".join(f"{s}{counts[s] if counts[s] > 1 else ''}" for s in order)

        self.frag2_info.set(f"Fragment 1: {formula(f1)} ({len(f1)} atoms)   ·   "
                            f"Fragment 2: {formula(f2)} ({len(f2)} atoms) = {ui_kit.compress_atom_list(f2)}")
        from modules.codes_dia.auto_bonds_frag import electron_count
        n_all = list(range(1, len(frame) + 1))
        for name, atoms, ch, sp in (("Fragment 1", f1, self.f1_charge, self.f1_spin),
                                    ("Fragment 2", f2, self.f2_charge, self.f2_spin),
                                    ("Complex", n_all, self.c_charge, self.c_spin)):
            try:
                ne = electron_count(frame, atoms, int(ch.get()))
                mult = int(sp.get())
            except ValueError:
                continue
            ok = (ne % 2) == ((mult - 1) % 2) and mult >= 1
            self.parity_labels[name].configure(
                text=f"{ne} electrons ✔" if ok else f"{ne} electrons — multiplicity {mult} impossible ✖",
                style="Kit.ok.TLabel" if ok else "Kit.bad.TLabel")
        try:
            if int(self.f1_charge.get()) + int(self.f2_charge.get()) != int(self.c_charge.get()):
                self.parity_labels["Complex"].configure(text="fragment charges do not add up to the complex charge ▲",
                                                        style="Kit.warn.TLabel")
        except ValueError:
            pass

    # ============================================================
    def prepare_inputs(self):
        try:
            if not self.frames:
                raise ValueError("Load a reaction path first.")
            f1, _ = self._fragments()
            template = self.input_text.get("1.0", tk.END)
            if any(w in template.lower().split() for w in ("opt", "optts", "copt", "zopt", "freq")):
                if not messagebox.askyesno("DIA", "The template contains Opt/Freq keywords, but DIA needs frozen-"
                                                  "geometry single points.\n\nContinue anyway?"):
                    return
            out = self.out_dir.get().strip()
            if not out:
                raise ValueError("Choose an output folder.")
            charges = (int(self.f1_charge.get()), int(self.f2_charge.get()), int(self.c_charge.get()))
            spins = (int(self.f1_spin.get()), int(self.f2_spin.get()), int(self.c_spin.get()))
            from modules.codes_dia.orca_input_generator_for_xyz import write_dia_inputs
            written = write_dia_inputs(self.frames, f1, out, template, charges, spins,
                                       include_complex=self.include_complex.get())
        except Exception as e:
            messagebox.showerror("DIA", str(e))
            return
        total = sum(written.values())
        self.dia_root.set(out)
        self.log.write(f"Wrote {total} ORCA inputs to {out}", "ok")
        self.log.write("Run them with run_all.sh / run_all.bat (or submit via Input Creator), then use step 4.", "info")
        messagebox.showinfo("DIA", f"{total} single-point inputs written to:\n{out}\n\n"
                                   "Run them with run_all.bat (Windows) or run_all.sh (Linux/HPC).")

    # ============================================================
    def run(self):
        # Read every Tk variable here, on the UI thread; the worker only gets plain values.
        params = {k: v.get() for k, v in (
            ("file", self.file_var), ("rmsd", self.rmsd), ("e1", self.e1), ("e2", self.e2),
            ("atoms", self.coord_atoms), ("mode", self.mode), ("unit", self.unit),
            ("frag1", self.frag1_path), ("frag2", self.frag2_path), ("complex", self.complex_path),
            ("source", self.complex_source))}
        self.run_btn.configure(state="disabled")
        self.log.write("Reading single points…", "info")
        threading.Thread(target=self.backend, args=(params,), daemon=True).start()

    def backend(self, params):
        try:
            result = self._compute(params)
            self.ui.post(lambda: self._show_result(*result))
        except Exception as e:
            msg = str(e)
            self.ui.post(lambda: self._fail(msg))

    def _compute(self, p):
        from modules.codes_dia.orca_input_generator_for_xyz import collect_frame_energies
        from modules.codes_dia import dia_core
        if not self.frames:
            if not os.path.isfile(p["file"].strip()):
                raise ValueError("Load the reaction path (step 1) first.")
            from modules.codes_dia.xyz_extractor import read_multi_xyz
            self.frames, self.frame_energies = read_multi_xyz(p["file"].strip(), float(p["rmsd"]))
        try:
            e1_opt, e2_opt = float(p["e1"]), float(p["e2"])
        except ValueError:
            raise ValueError("Enter the optimized energies of both fragments (Eh).")
        atoms = [int(a) for a in p["atoms"].replace(" ", "").split(",") if a]
        need = {"b": 2, "a": 3, "d": 4}[p["mode"]]
        if len(atoms) != need:
            raise ValueError(f"The reaction coordinate needs {need} atom numbers.")
        if max(atoms) > len(self.frames[0]) or min(atoms) < 1:
            raise ValueError("A reaction-coordinate atom number is out of range.")
        frag1_e = collect_frame_energies(p["frag1"].strip(), "frag1")
        frag2_e = collect_frame_energies(p["frag2"].strip(), "frag2")
        if not frag1_e or not frag2_e:
            raise ValueError("No finished frag1_<i>.out / frag2_<i>.out files were found in the fragment folders.")
        if p["source"] == "sp":
            complex_e = collect_frame_energies(p["complex"].strip(), "complete")
            if not complex_e:
                raise ValueError("No complete_<i>.out files found. Run the complex single points or switch to "
                                 "'energies in the xyz file'.")
        else:
            complex_e = {i: e for i, e in enumerate(self.frame_energies) if e is not None}
            if not complex_e:
                raise ValueError("The xyz file has no energies in its comment lines.")
        df, missing = dia_core.compute_dia(self.frames, complex_e, frag1_e, frag2_e, e1_opt, e2_opt,
                                           p["mode"], atoms, p["unit"])
        if df.empty:
            raise ValueError("No frame has all three energies (fragment 1, fragment 2 and complex).")
        return df, missing, dia_core.ts_summary(df)

    def _fail(self, msg):
        self.run_btn.configure(state="normal")
        self.log.write(f"Error: {msg}", "bad")
        messagebox.showerror("DIA", msg)

    def _show_result(self, df, missing, ts):
        self.run_btn.configure(state="normal")
        self.dis_int_df, self.ts = df, ts
        u = self.unit.get()
        self.res_tiles["dE"].set(f"{ts['dE']:.2f}", f"ΔE‡ ({u})")
        self.res_tiles["dis_total"].set(f"{ts['dis_total']:.2f}", f"ΔE‡ distortion ({u})")
        self.res_tiles["intr"].set(f"{ts['intr']:.2f}", f"ΔE‡ interaction ({u})")
        self.res_tiles["split"].set(f"{ts['dis_1']:.1f} / {ts['dis_2']:.1f}")
        self.res_tiles["coord"].set(f"{ts['coord']:.3f}", f"TS coordinate (frame {ts['frame']})")
        share = ts["dis_1"] / ts["dis_total"] * 100 if abs(ts["dis_total"]) > 1e-9 else 0
        self.verdict.set(
            f"At the TS, distortion costs {ts['dis_total']:.1f} {u} ({share:.0f}% from fragment 1) and is "
            f"{'partly offset' if ts['intr'] < 0 else 'worsened'} by an interaction of {ts['intr']:.1f} {u}. "
            + ("The barrier is distortion-controlled." if ts["dis_total"] > abs(ts["intr"])
               else "The barrier is interaction-controlled."))
        self.log.write(f"DIA done: {len(df)} frames analysed.", "ok")
        if missing:
            self.log.write(f"{len(missing)} frame(s) skipped because an energy was missing: "
                           f"{ui_kit.compress_atom_list([m for m in missing])}", "warn")
        if self.complex_source.get() == "xyz":
            self.log.write("Complex energies came from the xyz file. Make sure they use the same method/basis as "
                           "the fragment single points, otherwise ΔE_int is meaningless.", "warn")
        self._draw_plot()

    # ============================================================
    def _draw_plot(self):
        if not HAS_MPL or not hasattr(self, "fig"):
            return
        c = self._colors
        dark = self._mode == "dark"
        self.fig.clear()
        self.fig.patch.set_facecolor(c["card"])
        ax = self.fig.add_subplot(111)
        ax.set_facecolor(c["plot_bg"])
        for sp in ax.spines.values():
            sp.set_color(c["plot_grid"])
        ax.tick_params(colors=c["plot_fg"])
        df = self.dis_int_df
        if df is None or df.empty:
            ax.text(0.5, 0.5, "Run the analysis to see the distortion/interaction profile",
                    ha="center", va="center", transform=ax.transAxes, color=c["fg_muted"])
            ax.set_xticks([])
            ax.set_yticks([])
            self.canvas.draw_idle()
            return
        from modules.codes_dia import dia_core
        mode = self.mode.get()
        x = df.iloc[:, 1]
        order = x.argsort()
        for key, label, color, ls in CURVES:
            if not self.curve_on[key].get():
                continue
            col = "#e6edf3" if (dark and key == "dE") else color
            ax.plot(x.iloc[order], df[key].iloc[order], ls, color=col, lw=2.4 if ls == "-" else 1.6,
                    label=label, marker="o", ms=2.5)
        ax.axhline(0, color=c["plot_grid"], lw=1)
        if self.show_ts.get() and self.ts:
            ax.axvline(self.ts["coord"], color=c["fg_muted"], ls=":", lw=1.2)
            ax.annotate(f"TS  ΔE‡ = {self.ts['dE']:.1f}", (self.ts["coord"], self.ts["dE"]),
                        xytext=(8, 10), textcoords="offset points", color=c["plot_fg"], fontsize=9,
                        arrowprops=dict(arrowstyle="-", color=c["fg_muted"]))
        ax.set_xlabel(dia_core.COORD_LABEL[mode], color=c["plot_fg"])
        ax.set_ylabel(f"Energy ({self.unit.get()})", color=c["plot_fg"])
        ax.set_title("Distortion / interaction profile", color=c["plot_fg"], weight="bold")
        ax.grid(True, color=c["plot_grid"], ls="--", lw=0.6)
        if self.reverse_x.get():
            ax.invert_xaxis()
        leg = ax.legend(loc="best", fontsize=8, frameon=True)
        leg.get_frame().set_facecolor(c["plot_bg"])
        for t in leg.get_texts():
            t.set_color(c["plot_fg"])
        self.fig.tight_layout()
        self.canvas.draw_idle()

    # ============================================================
    def download_csv(self):
        if self.dis_int_df is None:
            messagebox.showwarning("DIA", "Run the analysis first.")
            return
        file = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile="dia_results.csv")
        if file:
            try:
                self.dis_int_df.to_csv(file, index=False)
                self.log.write(f"CSV saved: {file}", "ok")
            except Exception as e:
                messagebox.showerror("DIA", str(e))

    def save_figure(self):
        if not HAS_MPL or self.dis_int_df is None:
            messagebox.showwarning("DIA", "Run the analysis first.")
            return
        file = filedialog.asksaveasfilename(defaultextension=".png", initialfile="dia_profile.png",
                                            filetypes=[("PNG", "*.png"), ("SVG", "*.svg"), ("PDF", "*.pdf")])
        if file:
            self.fig.savefig(file, dpi=300, facecolor=self.fig.get_facecolor())
            self.log.write(f"Figure saved: {file}", "ok")
