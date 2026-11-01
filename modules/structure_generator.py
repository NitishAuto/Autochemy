"""
Periodic structure workspace.

The structure canvas is ASE's GUI with its window plumbing adapted to a
Tkinter frame.  ASE remains responsible for rendering, mouse interaction,
editing atoms, and all of its editor dialogs.
"""

import os
import json
import shutil
import subprocess
import tempfile
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from modules.base_module import BaseModule


SOLVENT_MOLAR_MASSES = {
    "acetone": 58.08,
    "acetonitrile": 41.05,
    "benzene": 78.11,
    "carbon tetrachloride / ccl4": 153.82,
    "chloroform / chcl3": 119.38,
    "dichloromethane / ch2cl2 / dcm": 84.93,
    "dimethylsulfoxide / dmso": 78.13,
    "ethanol": 46.07,
    "ethyl acetate / ethylacetate / ethanoate": 88.11,
    "methanol": 32.04,
    "n-hexane / hexane": 86.18,
    "n-heptane / heptane": 100.20,
    "n-octane / octane": 114.23,
    "tetrahydrofuran / thf": 72.11,
    "toluene": 92.14,
}


class EmbeddedASEGUIWindow:
    """ASEGUIWindow-compatible host that can live in a Tkinter frame.

    ASE's GUI classes are intentionally used unchanged above this small
    adapter.  The adapter only supplies the canvas/status/menu primitives
    that ASEGUIWindow normally creates on a standalone Tk root.
    """

    def __init__(self, parent, close, menu, config, scroll, scroll_event,
                 press, move, release, resize, *, popout=False):
        import numpy as np
        import ase.gui.ui as ui

        self._ui = ui
        self._embedded = not popout
        self._parent = parent
        self._menu_description = menu
        self._config = config
        self.size = np.array([450, 450])
        self.fg = config["gui_foreground_color"]
        self.bg = config["gui_background_color"]
        self.menu = {}
        self.exists = True
        self._close_callback = close

        if popout:
            self.win = tk.Toplevel(parent.winfo_toplevel())
            self.win.title("ASE-GUI")
            self.win.geometry("900x700")
            self._container = self.win
            self.win.protocol("WM_DELETE_WINDOW", self.close)
        else:
            self.win = parent.winfo_toplevel()
            self._container = parent

        if self._embedded:
            self._create_menu(menu)

        self.canvas = tk.Canvas(
            self._container,
            width=self.size[0],
            height=self.size[1],
            bg=self.bg,
            highlightthickness=0,
        )
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.status = tk.Label(self._container, text="", anchor=tk.W)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

        self.canvas.bind("<ButtonPress>", ui.bind(press))
        for button in range(1, 4):
            self.canvas.bind(f"<B{button}-Motion>", ui.bind(move))
        self.canvas.bind("<ButtonRelease>", ui.bind(release))
        self.canvas.bind("<Control-ButtonRelease>", ui.bind(release, "ctrl"))
        self.canvas.bind("<Shift-ButtonRelease>", ui.bind(release, "shift"))
        self.canvas.bind("<Configure>", resize)
        if not config["swap_mouse"]:
            for button in (2, 3):
                self.canvas.bind(f"<Shift-B{button}-Motion>", ui.bind(scroll))
        else:
            self.canvas.bind("<Shift-B1-Motion>", ui.bind(scroll))

        self.win.bind("<MouseWheel>", ui.bind(scroll_event))
        self.win.bind("<Key>", ui.bind(scroll))
        self.win.bind("<Shift-Key>", ui.bind(scroll, "shift"))
        self.win.bind("<Control-Key>", ui.bind(scroll, "ctrl"))
        if not self._embedded:
            self._create_menu(menu)

    def _create_menu(self, menu_description):
        menu_parent = self._container
        if self._embedded:
            self.menu_frame = tk.Frame(menu_parent, bg=self.bg)
            self.menu_frame.pack(side=tk.TOP, fill=tk.X)
            for label, items in menu_description:
                submenu = tk.Menu(self.menu_frame, tearoff=False)
                for item in items:
                    item.addto(submenu, self.win, self.menu)
                button = tk.Menubutton(
                    self.menu_frame,
                    text=label.replace("_", ""),
                    relief=tk.FLAT,
                    bg=self.bg,
                    fg=self.fg,
                    activebackground=self.bg,
                    activeforeground=self.fg,
                    takefocus=False,
                )
                button.configure(menu=submenu)
                button.pack(side=tk.LEFT)
        else:
            menubar = tk.Menu(self.win)
            self.win.config(menu=menubar)
            for label, items in menu_description:
                submenu = tk.Menu(menubar, tearoff=False)
                menubar.add_cascade(label=label.replace("_", ""), menu=submenu)
                for item in items:
                    item.addto(submenu, self.win, self.menu)

    def __getitem__(self, name):
        return self.menu[name].get()

    def __setitem__(self, name, value):
        self.menu[name].set(value)

    @property
    def title(self):
        return self.win.title()

    @title.setter
    def title(self, value):
        if not self._embedded:
            self.win.title(value)

    def close(self):
        if not self.exists:
            return
        self.exists = False
        self._close_callback()

    def update_status_line(self, text):
        self.status.config(text=text)

    def clear(self):
        self.canvas.delete(tk.ALL)

    def update(self):
        self.canvas.update_idletasks()

    def circle(self, color, selected, *bbox):
        outline = "#004500" if selected else "black"
        self.canvas.create_oval(
            *tuple(int(x) for x in bbox),
            fill=color,
            outline=outline,
            width=3 if selected else 1,
        )

    def arc(self, color, selected, start, extent, *bbox):
        outline = "#004500" if selected else "black"
        self.canvas.create_arc(
            *tuple(int(x) for x in bbox),
            start=start,
            extent=extent,
            fill=color,
            outline=outline,
            width=3 if selected else 1,
        )

    def line(self, bbox, width=1):
        self.canvas.create_line(
            *tuple(int(x) for x in bbox), width=width, fill="black"
        )

    def text(self, x, y, txt, anchor=tk.CENTER, color="black"):
        # Normalize anchor to lowercase string if it's a string like "SE"
        if isinstance(anchor, str):
            anchor = anchor.lower()
        self.canvas.create_text((x, y), text=txt, anchor=anchor, fill=color)

    def after(self, delay, callback):
        timer_id = self.win.after(int(delay * 1000), callback)

        class Timer:
            def cancel(timer_self):
                self.win.after_cancel(timer_id)

        return Timer()

    def destroy(self):
        if self._embedded:
            for widget in (self.menu_frame, self.canvas, self.status):
                widget.destroy()
        elif self.win.winfo_exists():
            self.win.destroy()


class StructureGeneratorModule(BaseModule):
    """Periodic workspace backed by ASE's complete GUI."""

    def get_name(self) -> str:
        return "Structure Generator"

    def create_ui(self):
        self.main_frame = ttk.Frame(self.parent_frame)
        self._images = None
        self._ase_guis = []
        self._viewer_host = None
        self._viewer_window = None
        self._viewer_layout = None
        self._viewer_controls = None
        self._viewer_popout = False
        self._solvent_setup = None

        self.path_var = tk.StringVar()

        split = ttk.PanedWindow(self.main_frame, orient=tk.HORIZONTAL)
        split.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        left_frame = ttk.LabelFrame(split, text="L")
        left_split = ttk.PanedWindow(left_frame, orient=tk.VERTICAL)
        left_split.pack(fill=tk.BOTH, expand=True)

        rup_frame = ttk.LabelFrame(left_split, text="Rup")
        self._build_rup_panel(rup_frame)
        left_split.add(rup_frame, weight=1)

        rdown_frame = ttk.LabelFrame(left_split, text="Rdown")
        ttk.Label(rdown_frame, text="Placeholder").pack(expand=True)
        left_split.add(rdown_frame, weight=1)
        split.add(left_frame, weight=1)

        self.viewer_frame = ttk.LabelFrame(split, text="Structure visualizer")
        split.add(self.viewer_frame, weight=3)
        self._empty_label = ttk.Label(
            self.viewer_frame,
            text="Open a CIF, POSCAR, XYZ, trajectory, or any format supported by ASE.",
        )
        self._empty_label.pack(expand=True)

    def _build_rup_panel(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=1)
        parent.rowconfigure(3, weight=1)

        load_button = ttk.Button(
            parent,
            text="Load structure",
            command=self._open_structure,
            width=16,
        )
        load_button.grid(row=0, column=0, sticky="nw", padx=8, pady=(8, 3))
        ttk.Label(
            parent,
            textvariable=self.path_var,
            wraplength=150,
            justify=tk.LEFT,
        ).grid(row=0, column=1, columnspan=2, sticky="new", padx=(4, 8), pady=(8, 3))

        ttk.Button(parent, text="Model", command=self._toggle_model_options).grid(
            row=1, column=0, sticky="nw", padx=8, pady=5
        )
        self._model_options = ttk.Frame(parent)
        self._model_options.grid(row=1, column=1, sticky="nw", padx=(4, 8), pady=3)
        self._model_options.grid_remove()
        for action in (
            "Add solvent",
            "Add adsorbate",
            "Create vacancy",
            "Substitute",
            "Create Interstitial defect",
            "Vibrational analysis",
            "Generate slab",
            "Compute Free energies",
        ):
            command = (
                self._add_solvent if action == "Add solvent" else
                self._generate_slab if action == "Generate slab" else
                (lambda name=action: self._model_action(name))
            )
            ttk.Button(
                self._model_options,
                text=action,
                width=24,
                command=command,
            ).pack(anchor="w", pady=1)

        ttk.Button(parent, text="Input Params", command=self._input_params).grid(
            row=2, column=0, sticky="nw", padx=8, pady=5
        )

        io_row = ttk.Frame(parent)
        io_row.grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        io_row.columnconfigure(0, weight=1)
        ttk.Combobox(
            io_row,
            values=["ORCA", "xTB", "VASP"],
            state="readonly",
            width=8,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(io_row, text="Push to input").grid(row=0, column=1, padx=3)
        ttk.Button(io_row, text="Load from input").grid(row=0, column=2, padx=(3, 0))

    def _toggle_model_options(self):
        if self._model_options.winfo_ismapped():
            self._model_options.grid_remove()
        else:
            self._model_options.grid()

    def _model_action(self, action):
        messagebox.showinfo(
            "Periodic model",
            f"{action} will be available for the loaded structure.",
            parent=self.main_frame.winfo_toplevel(),
        )

    def _add_solvent(self):
        if self._images is None or not len(self._images):
            messagebox.showinfo(
                "Add solvent",
                "Load a structure before adding solvent.",
                parent=self.main_frame.winfo_toplevel(),
            )
            return
        
        # Reset to current frame to avoid frame switching issues
        self._reset_images_to_current_frame()

        atoms = self._ase_guis[0].atoms if self._ase_guis else self._images[0]
        import numpy as np

        positions = np.asarray(atoms.get_positions(), dtype=float)
        cell = np.asarray(atoms.get_cell(), dtype=float)
        vacuum_axes = []
        for axis, name in enumerate(("x", "y", "z")):
            cell_length = float(np.linalg.norm(cell[axis]))
            if cell_length <= 1.0e-8 or positions.size == 0:
                continue
            coordinate_span = float(positions[:, axis].max() - positions[:, axis].min())
            if coordinate_span < 0.7 * cell_length:
                vacuum_axes.append(name)

        if not vacuum_axes:
            messagebox.showinfo(
                "Add solvent",
                "This structure does not have enough vacuum along x, y, or z, "
                "so solvent cannot be added.",
                parent=self.main_frame.winfo_toplevel(),
            )
            return

        self._open_solvent_setup(vacuum_axes)
    def _generate_slab(self):
        if self._images is None or not len(self._images):
            messagebox.showinfo(
                "Generate slab",
                "Load a structure before generating a slab.",
                parent=self.main_frame.winfo_toplevel(),
            )
            return
        
        # Reset to current frame to avoid frame switching issues
        self._reset_images_to_current_frame()

        atoms = self._ase_guis[0].atoms if self._ase_guis else self._images[0]
        import numpy as np

        positions = np.asarray(atoms.get_positions(), dtype=float)
        cell = np.asarray(atoms.get_cell(), dtype=float)
        vacuum_axes = []
        for axis, name in enumerate(("x", "y", "z")):
            cell_length = float(np.linalg.norm(cell[axis]))
            if cell_length <= 1.0e-8 or positions.size == 0:
                continue
            coordinate_span = float(positions[:, axis].max() - positions[:, axis].min())
            if coordinate_span < 0.7 * cell_length:
                vacuum_axes.append(name)

        if vacuum_axes:
            proceed = messagebox.askyesno(
                "Generate slab",
                f"This structure is not bulk (vacuum detected along {', '.join(vacuum_axes)}). "
                "Do you want to proceed?",
                parent=self.main_frame.winfo_toplevel(),
            )
            if not proceed:
                return

        self._open_slab_setup()

    def _open_slab_setup(self):
        dialog = tk.Toplevel(self.main_frame.winfo_toplevel())
        dialog.title("Generate Slabs (Pymatgen)")
        dialog.geometry("520x650")
        dialog.transient(self.main_frame.winfo_toplevel())

        body = ttk.Frame(dialog, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            body,
            text="Configure pymatgen SlabGenerator parameters:",
            wraplength=480,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 8))

        canvas = tk.Canvas(body, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas, padding=4)
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        vars_dict = {
            "miller": tk.StringVar(value="1, 0, 0"),
            "min_slab": tk.StringVar(value="5.0"),
            "min_vacuum": tk.StringVar(value="10.0"),
            "lll_reduce": tk.BooleanVar(value=False),
            "center_slab": tk.BooleanVar(value=False),
            "in_unit_planes": tk.BooleanVar(value=False),
            "primitive": tk.BooleanVar(value=True),
            "max_normal": tk.StringVar(value=""),
            "reorient": tk.BooleanVar(value=True),
            "allow_smaller": tk.BooleanVar(value=False),
            "symmetrize": tk.BooleanVar(value=False),
            "filter_sym": tk.BooleanVar(value=True),
            "max_broken": tk.StringVar(value="0"),
            "tol": tk.StringVar(value="0.1"),
            "ftol": tk.StringVar(value="0.1"),
            "ztol": tk.StringVar(value="0.0"),
        }

        for lbl, key in [
            ("Miller indices (h, k, l):", "miller"),
            ("Min slab size (Å):", "min_slab"),
            ("Min vacuum size (Å):", "min_vacuum"),
            ("Max normal search:", "max_normal"),
            ("Max broken bonds:", "max_broken"),
            ("Tolerance (tol):", "tol"),
            ("Fractional tol (ftol):", "ftol"),
            ("Z-tolerance (ztol):", "ztol"),
        ]:
            r = ttk.Frame(scrollable_frame)
            r.pack(fill=tk.X, pady=3)
            ttk.Label(r, text=lbl, width=28).pack(side=tk.LEFT)
            ttk.Entry(r, textvariable=vars_dict[key], width=16).pack(side=tk.RIGHT)

        for lbl, key in [
            ("LLL reduce", "lll_reduce"),
            ("Center slab", "center_slab"),
            ("In unit planes", "in_unit_planes"),
            ("Primitive cell", "primitive"),
            ("Reorient lattice", "reorient"),
            ("Allow smaller than OUC", "allow_smaller"),
            ("Symmetrize slabs", "symmetrize"),
            ("Filter out symmetric slabs", "filter_sym"),
        ]:
            r = ttk.Frame(scrollable_frame)
            r.pack(fill=tk.X, pady=2)
            ttk.Checkbutton(r, text=lbl, variable=vars_dict[key]).pack(anchor="w")

        buttons = ttk.Frame(dialog, padding=10)
        buttons.pack(fill=tk.X, side=tk.BOTTOM)

        def on_gen():
            try:
                m_parts = [int(p.strip()) for p in vars_dict["miller"].get().replace("(", "").replace(")", "").split(",") if p.strip()]
                if len(m_parts) != 3:
                    raise ValueError("Miller indices must be 3 integers.")
                self._run_slab_generation(
                    miller_index=tuple(m_parts),
                    min_slab_size=float(vars_dict["min_slab"].get()),
                    min_vacuum_size=float(vars_dict["min_vacuum"].get()),
                    lll_reduce=vars_dict["lll_reduce"].get(),
                    center_slab=vars_dict["center_slab"].get(),
                    in_unit_planes=vars_dict["in_unit_planes"].get(),
                    primitive=vars_dict["primitive"].get(),
                    max_normal_search=int(vars_dict["max_normal"].get()) if vars_dict["max_normal"].get().strip() else None,
                    reorient_lattice=vars_dict["reorient"].get(),
                    allow_smaller_than_ouc=vars_dict["allow_smaller"].get(),
                    symmetrize=vars_dict["symmetrize"].get(),
                    filter_out_sym_slabs=vars_dict["filter_sym"].get(),
                    max_broken_bonds=int(vars_dict["max_broken"].get()),
                    tol=float(vars_dict["tol"].get()),
                    ftol=float(vars_dict["ftol"].get()),
                    ztol=float(vars_dict["ztol"].get()),
                )
                dialog.destroy()
            except Exception as exc:
                messagebox.showerror("Error", f"Invalid parameters:\n{exc}", parent=dialog)

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(buttons, text="Generate", command=on_gen).pack(side=tk.RIGHT)
        dialog.grab_set()

    def _run_slab_generation(self, **kwargs):
        try:
            from pymatgen.io.ase import AseAtomsAdaptor
            from pymatgen.core.surface import SlabGenerator
            from ase.gui.images import Images

            atoms = self._ase_guis[0].atoms if self._ase_guis else self._images[0]
            struct = AseAtomsAdaptor.get_structure(atoms)

            generator = SlabGenerator(
                initial_structure=struct,
                miller_index=kwargs["miller_index"],
                min_slab_size=kwargs["min_slab_size"],
                min_vacuum_size=kwargs["min_vacuum_size"],
                lll_reduce=kwargs["lll_reduce"],
                center_slab=kwargs["center_slab"],
                in_unit_planes=kwargs["in_unit_planes"],
                primitive=kwargs["primitive"],
                max_normal_search=kwargs["max_normal_search"],
                reorient_lattice=kwargs["reorient_lattice"],
                allow_smaller_than_ouc=kwargs["allow_smaller_than_ouc"],
            )

            slabs = generator.get_slabs(
                symmetrize=kwargs["symmetrize"],
                filter_out_sym_slabs=kwargs["filter_out_sym_slabs"],
                max_broken_bonds=kwargs["max_broken_bonds"],
                tol=kwargs["tol"],
                ftol=kwargs["ftol"],
                ztol=kwargs["ztol"],
            )

            if not slabs:
                messagebox.showinfo(
                    "No Slabs Generated",
                    "No slabs were generated with the given parameters.",
                    parent=self.main_frame.winfo_toplevel(),
                )
                return

            slab_atoms_list = [AseAtomsAdaptor.get_atoms(slab) for slab in slabs]
            self._images = Images(images=slab_atoms_list)
            self.path_var.set(f"Generated Slabs (terminations: {len(slab_atoms_list)})")
            self._create_viewer()

        except Exception as exc:
            messagebox.showerror(
                "Slab Generation Error",
                f"Failed to generate slabs using pymatgen:\n{exc}",
                parent=self.main_frame.winfo_toplevel(),
            )
            import traceback
            traceback.print_exc()





    def _estimate_solvent_volume(self):
        atoms = self._ase_guis[0].atoms if self._ase_guis else self._images[0]
        import numpy as np

        positions = np.asarray(atoms.get_positions(), dtype=float)
        cell = np.asarray(atoms.get_cell(), dtype=float)
        cell_volume = abs(float(np.linalg.det(cell)))
        if cell_volume <= 1.0e-8 or positions.size == 0:
            return 0.0
        cell_lengths = np.linalg.norm(cell, axis=1)
        occupied_lengths = positions.max(axis=0) - positions.min(axis=0)
        occupied_volume = float(np.prod(np.minimum(occupied_lengths, cell_lengths)))
        return max(0.0, cell_volume - occupied_volume)

    def _find_packmol(self):
        executable = shutil.which("packmol")
        if executable:
            return executable

        candidates = (
            os.path.join(os.getcwd(), "paths.txt"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paths.txt"),
        )
        for path in candidates:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    raw = handle.read()
                try:
                    data = json.loads(raw)
                    paths = data.get("paths", data) if isinstance(data, dict) else {}
                    configured = (
                        paths.get("PACKMOL", "")
                        or paths.get("PACKMOL_PATH", "")
                        or paths.get("PACKMOL_ROOT", "")
                    )
                except (json.JSONDecodeError, AttributeError):
                    configured = ""
                    for line in raw.splitlines():
                        if "=" in line:
                            key, value = line.split("=", 1)
                            if key.strip().upper() in {"PACKMOL", "PACKMOL_PATH", "PACKMOL_ROOT"}:
                                configured = value.strip().strip('"\'')
                                break
                configured = os.path.expanduser(os.path.expandvars(str(configured).strip()))
                if os.path.isdir(configured):
                    configured = os.path.join(configured, "packmol")
                if configured and os.path.isfile(configured) and os.access(configured, os.X_OK):
                    return configured
            except OSError:
                continue
        return None

    def _packmol_box(self, atoms, vacuum_axes):
        import numpy as np

        positions = np.asarray(atoms.get_positions(), dtype=float)
        cell = np.asarray(atoms.get_cell(), dtype=float)
        lengths = np.linalg.norm(cell, axis=1)
        lower = np.zeros(3, dtype=float)
        upper = lengths.copy()
        spans = positions.max(axis=0) - positions.min(axis=0)
        gaps = lengths - spans
        axis = max(vacuum_axes, key=lambda name: gaps["xyz".index(name)])
        index = "xyz".index(axis)
        left_gap = positions[:, index].min()
        right_gap = lengths[index] - positions[:, index].max()
        if right_gap >= left_gap:
            lower[index] = positions[:, index].max()
        else:
            upper[index] = positions[:, index].min()
        return lower, upper

    def _run_packmol(self, setup, executable):
        try:
            import io
            from ase import Atom, Atoms
            from ase.io import read, write
            import numpy as np

            custom_xyz = setup.get("custom_xyz", "").strip()
            if not custom_xyz:
                raise ValueError(
                    "A solvent XYZ structure is required before Packmol can run. "
                    "Paste it into the Custom solvent XYZ box."
                )
            solvent_atoms = read(io.StringIO(custom_xyz), format="xyz")
            cation = setup["cation"].strip()
            anion = setup["anion"].strip()
            cation_atoms = Atoms([Atom(cation, position=(0.0, 0.0, 0.0))])
            anion_atoms = Atoms([Atom(anion, position=(0.0, 0.0, 0.0))])
            active_atoms = self._ase_guis[0].atoms if self._ase_guis else self._images[0]
            lower, upper = self._packmol_box(active_atoms, setup["vacuum_axes"])
            box = " ".join(f"{value:.6f}" for value in (*lower, *upper))

            temp_root = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                ".temp",
            )
            os.makedirs(temp_root, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="packmol_", dir=temp_root) as workdir:
                solvent_xyz_path = os.path.join(workdir, "solvent.xyz")
                cation_xyz_path = os.path.join(workdir, "cation.xyz")
                anion_xyz_path = os.path.join(workdir, "anion.xyz")
                solvent_path = os.path.join(workdir, "solvent.pdb")
                cation_path = os.path.join(workdir, "cation.pdb")
                anion_path = os.path.join(workdir, "anion.pdb")
                output_path = os.path.join(workdir, "packed.pdb")
                write(solvent_xyz_path, solvent_atoms, format="xyz")
                write(cation_xyz_path, cation_atoms, format="xyz")
                write(anion_xyz_path, anion_atoms, format="xyz")
                write(solvent_path, solvent_atoms, format="proteindatabank")
                write(cation_path, cation_atoms, format="proteindatabank")
                write(anion_path, anion_atoms, format="proteindatabank")
                input_text = (
                    "tolerance 2.0\n"
                    "filetype pdb\n"
                    f"output {output_path}\n"
                    f"structure {solvent_path}\n"
                    f"  number {int(setup['molecule_count'])}\n"
                    f"  inside box {box}\n"
                    "end structure\n"
                    f"structure {cation_path}\n"
                    f"  number {int(setup['cation_count'])}\n"
                    f"  inside box {box}\n"
                    "end structure\n"
                    f"structure {anion_path}\n"
                    f"  number {int(setup['anion_count'])}\n"
                    f"  inside box {box}\n"
                    "end structure\n"
                )
                result = subprocess.run(
                    [executable],
                    input=input_text,
                    text=True,
                    capture_output=True,
                    cwd=workdir,
                    check=False,
                )
                if result.returncode != 0 or not os.path.isfile(output_path):
                    detail = (result.stderr or result.stdout or "Packmol returned no output.").strip()
                    raise RuntimeError(detail[-1200:])
                packed_atoms = read(output_path, format="proteindatabank")
                active_atoms += packed_atoms
                active_atoms.set_cell(active_atoms.get_cell() if active_atoms.cell.any() else self._images[0].cell)
                active_atoms.set_pbc(True)
                active_atoms.wrap()
                for gui in self._ase_guis:
                    gui.set_frame(gui.frame)
            messagebox.showinfo("Add solvent", "Packmol configuration added and wrapped into the cell.", parent=self.main_frame.winfo_toplevel())
        except Exception as exc:
            messagebox.showerror("Packmol failed", str(exc), parent=self.main_frame.winfo_toplevel())

    def _open_solvent_setup(self, vacuum_axes):
        from modules.solvent_data import SOLVENT_DATA

        dialog = tk.Toplevel(self.main_frame.winfo_toplevel())
        dialog.title("Add solvent")
        dialog.transient(self.main_frame.winfo_toplevel())
        dialog.resizable(False, False)

        body = ttk.Frame(dialog, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            body,
            text="Vacuum detected along " + ", ".join(vacuum_axes) + ".",
        ).pack(anchor="w", pady=(0, 8))

        solvent_names = ["Custom solvent"] + sorted(SOLVENT_DATA)
        solvent_var = tk.StringVar(value=solvent_names[0] if solvent_names else "")
        solvent_row = ttk.Frame(body)
        solvent_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(solvent_row, text="Solvent", width=14).pack(side=tk.LEFT)
        ttk.Combobox(
            solvent_row,
            textvariable=solvent_var,
            values=solvent_names,
            state="readonly",
            width=42,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        xyz_box = ttk.LabelFrame(body, text="Single-solvent XYZ (required for Packmol)", padding=8)
        xyz_box.pack(fill=tk.X, pady=(0, 8))
        custom_xyz_text = tk.Text(xyz_box, height=5, width=52, wrap=tk.NONE)
        custom_xyz_text.pack(fill=tk.X, expand=True)

        ions_box = ttk.LabelFrame(body, text="Ion names", padding=8)
        ions_box.pack(fill=tk.X, pady=(0, 6))
        cation_var = tk.StringVar(value="Na")
        anion_var = tk.StringVar(value="Cl")
        ttk.Label(ions_box, text="Cation", width=12).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(ions_box, textvariable=cation_var, width=12).grid(row=0, column=1, padx=(0, 16))
        ttk.Label(ions_box, text="Anion", width=12).grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Entry(ions_box, textvariable=anion_var, width=12).grid(row=0, column=3)

        counts_box = ttk.LabelFrame(body, text="Number of ions", padding=8)
        counts_box.pack(fill=tk.X, pady=(0, 8))
        cation_count_var = tk.StringVar(value="1")
        anion_count_var = tk.StringVar(value="1")
        ttk.Label(counts_box, text="Cations", width=12).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(counts_box, textvariable=cation_count_var, width=12).grid(row=0, column=1, padx=(0, 16))
        ttk.Label(counts_box, text="Anions", width=12).grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Entry(counts_box, textvariable=anion_count_var, width=12).grid(row=0, column=3)

        amount_box = ttk.LabelFrame(body, text="Solvent amount", padding=8)
        amount_box.pack(fill=tk.X, pady=(0, 8))
        density_var = tk.StringVar(value="1.00")
        molecule_count_var = tk.StringVar(value="")
        ttk.Label(amount_box, text="Density (g/cm³)", width=16).grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        ttk.Entry(amount_box, textvariable=density_var, width=12).grid(
            row=0, column=1, padx=(0, 16)
        )
        ttk.Label(amount_box, text="Molecules", width=12).grid(
            row=0, column=2, sticky="w", padx=(0, 6)
        )
        ttk.Entry(amount_box, textvariable=molecule_count_var, width=12).grid(
            row=0, column=3
        )
        conversion_status = tk.StringVar()
        ttk.Label(
            body,
            textvariable=conversion_status,
            foreground="#64748b",
            wraplength=500,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 8))

        conversion_state = {"updating": False}
        solvent_volume = self._estimate_solvent_volume()

        def molar_mass():
            if solvent_var.get() == "Custom solvent":
                xyz_text = custom_xyz_text.get("1.0", tk.END).strip()
                if not xyz_text:
                    return None
                try:
                    import io
                    from ase.io import read
                    return float(read(io.StringIO(xyz_text), format="xyz").get_masses().sum())
                except Exception:
                    return None
            return SOLVENT_MOLAR_MASSES.get(solvent_var.get().strip().lower())

        def update_from_density(*_args):
            if conversion_state["updating"]:
                return
            mass = molar_mass()
            try:
                density = float(density_var.get())
            except ValueError:
                return
            if mass is None:
                conversion_status.set("Molar mass is unavailable for this solvent; molecule count cannot be calculated.")
                return
            if density <= 0 or solvent_volume <= 0:
                return
            molecules = density * solvent_volume * 1.0e-24 * 6.02214076e23 / mass
            conversion_state["updating"] = True
            molecule_count_var.set(str(max(1, round(molecules))))
            conversion_state["updating"] = False
            conversion_status.set(f"Estimated vacuum volume: {solvent_volume:.2f} Å³")

        def update_from_molecules(*_args):
            if conversion_state["updating"]:
                return
            mass = molar_mass()
            try:
                molecules = int(molecule_count_var.get())
            except ValueError:
                return
            if mass is None:
                conversion_status.set("Molar mass is unavailable for this solvent; density cannot be calculated.")
                return
            if molecules < 0 or solvent_volume <= 0:
                return
            density = molecules * mass / (solvent_volume * 1.0e-24 * 6.02214076e23)
            conversion_state["updating"] = True
            density_var.set(f"{density:.4f}")
            conversion_state["updating"] = False
            conversion_status.set(f"Estimated vacuum volume: {solvent_volume:.2f} Å³")

        density_var.trace_add("write", update_from_density)
        molecule_count_var.trace_add("write", update_from_molecules)
        solvent_var.trace_add("write", update_from_density)
        custom_xyz_text.bind("<FocusOut>", update_from_density)
        update_from_density()

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(2, 0))

        def confirm():
            cation = cation_var.get().strip()
            anion = anion_var.get().strip()
            try:
                cation_count = int(cation_count_var.get())
                anion_count = int(anion_count_var.get())
            except ValueError:
                messagebox.showerror("Invalid ion count", "Ion counts must be whole numbers.", parent=dialog)
                return
            if not cation or not anion:
                messagebox.showerror("Invalid ion names", "Enter both a cation and anion name.", parent=dialog)
                return
            if cation_count < 0 or anion_count < 0 or cation_count + anion_count == 0:
                messagebox.showerror(
                    "Invalid ion count",
                    "Ion counts must be non-negative and at least one ion is required.",
                    parent=dialog,
                )
                return
            mass = molar_mass()
            custom_xyz = custom_xyz_text.get("1.0", tk.END).strip()
            if solvent_var.get() == "Custom solvent" and not custom_xyz:
                messagebox.showerror(
                    "Missing custom solvent",
                    "Enter XYZ coordinates for the custom solvent.",
                    parent=dialog,
                )
                return
            if solvent_var.get() == "Custom solvent" and mass is None:
                messagebox.showerror(
                    "Invalid custom solvent XYZ",
                    "The custom solvent XYZ text could not be parsed.",
                    parent=dialog,
                )
                return
            try:
                density = float(density_var.get())
                molecule_count = int(molecule_count_var.get())
            except ValueError:
                messagebox.showerror(
                    "Invalid solvent amount",
                    "Enter a valid density and molecule count.",
                    parent=dialog,
                )
                return
            if mass is None:
                messagebox.showerror(
                    "Missing molar mass",
                    "Automatic conversion is unavailable for this solvent. "
                    "Choose a solvent with known molecular mass.",
                    parent=dialog,
                )
                return
            if density <= 0 or molecule_count < 0:
                messagebox.showerror(
                    "Invalid solvent amount",
                    "Density must be positive and molecule count cannot be negative.",
                    parent=dialog,
                )
                return
            executable = self._find_packmol()
            if executable is None:
                messagebox.showerror(
                    "Packmol not found",
                    "Packmol is not available in PATH and is not configured in paths.txt.",
                    parent=dialog,
                )
                return
            if not custom_xyz:
                messagebox.showerror(
                    "Missing solvent XYZ",
                    "Provide the single-solvent XYZ structure before running Packmol.",
                    parent=dialog,
                )
                return
            self._solvent_setup = {
                "solvent": solvent_var.get(),
                "custom_xyz": custom_xyz,
                "cation": cation,
                "anion": anion,
                "cation_count": cation_count,
                "anion_count": anion_count,
                "vacuum_axes": list(vacuum_axes),
                "density_g_cm3": density,
                "molecule_count": molecule_count,
                "vacuum_volume_a3": solvent_volume,
            }
            dialog.destroy()
            self._run_packmol(self._solvent_setup, executable)

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(buttons, text="Continue", command=confirm).pack(side=tk.RIGHT)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        dialog.focus_set()

    def _input_params(self):
        messagebox.showinfo(
            "Input Params",
            "Input parameter controls will be available here.",
            parent=self.main_frame.winfo_toplevel(),
        )

    def _open_structure(self):
        filename = filedialog.askopenfilename(
            parent=self.main_frame.winfo_toplevel(),
            title="Open structure",
            filetypes=[
                ("ASE-readable structures", "*"),
                ("CIF files", "*.cif"),
                ("XYZ files", "*.xyz"),
                ("POSCAR files", "POSCAR CONTCAR *.vasp"),
                ("Trajectory files", "*.traj"),
            ],
        )
        if not filename:
            return
        try:
            from ase.gui.images import Images

            images = Images()
            images.read([filename], slice(None))
            self._images = images
            self.path_var.set(filename)
            self._create_viewer()
        except ImportError:
            self._show_ase_missing()
        except Exception as exc:
            messagebox.showerror(
                "Structure load error", f"ASE could not read this file:\n{exc}",
                parent=self.main_frame.winfo_toplevel(),
            )
            traceback.print_exc()

    def _create_viewer(self):
        self._destroy_viewer()
        if self._images is None:
            return
        try:
            import ase.gui.ui as ase_ui
            from ase.gui.gui import GUI

            self._empty_label.pack_forget()
            if self._viewer_popout:
                self._viewer_window = tk.Toplevel(self.main_frame.winfo_toplevel())
                self._viewer_window.title("ASE structure viewer")
                self._viewer_window.geometry("1000x800")
                self._viewer_window.protocol("WM_DELETE_WINDOW", self._toggle_popout)
                self._viewer_host = ttk.Frame(self._viewer_window)
                self._viewer_host.pack(fill=tk.BOTH, expand=True)
            else:
                self._viewer_host = self.viewer_frame

            panes = tk.PanedWindow(self._viewer_host, orient=tk.VERTICAL,
                                   sashrelief=tk.RAISED, sashwidth=5)
            panes.pack(fill=tk.BOTH, expand=True)
            self._viewer_layout = panes
            side_frame = ttk.LabelFrame(panes, text="Side view (along x)")
            top_frame = ttk.LabelFrame(panes, text="Top view (along z)")
            panes.add(side_frame, minsize=120)
            panes.add(top_frame, minsize=120)

            self._create_ase_gui(ase_ui, GUI, side_frame, "90y")
            self._create_ase_gui(ase_ui, GUI, top_frame, "")
            self._install_sync_hooks()
            self._create_movie_controls()
            self._create_view_controls(top_frame, side_frame)
            self._reset_views()
            for gui in self._ase_guis:
                gui.draw()
        except ImportError:
            self._show_ase_missing()
        except Exception as exc:
            self._destroy_viewer()
            messagebox.showerror(
                "Viewer error", f"Failed to initialize ASE GUI:\n{exc}",
                parent=self.main_frame.winfo_toplevel(),
            )
            traceback.print_exc()

    def _create_view_controls(self, bottom_panel, upper_panel):
        controls = ttk.Frame(self._viewer_host)
        controls.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(4, 0))
        self._viewer_controls = controls
        for label, key in (
            ("x", "X"), ("-x", "Shift+X"),
            ("y", "Y"), ("-y", "Shift+Y"),
            ("z", "Z"), ("-z", "Shift+Z"),
        ):
            ttk.Button(
                controls,
                text=label,
                width=4,
                command=lambda view_key=key: self._set_bottom_view(view_key),
            ).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="custom", command=self._open_custom_rotation).pack(
            side=tk.LEFT, padx=(8, 2)
        )

        reset = tk.Button(
            upper_panel,
            text="Reset view",
            command=self._reset_views,
            relief=tk.FLAT,
            bd=0,
            padx=6,
            pady=2,
            highlightthickness=0,
            cursor="hand2",
        )
        reset.place(relx=1.0, x=-8, y=8, anchor="ne")
        popout = tk.Button(
            upper_panel,
            text="Pop out viewer",
            command=self._toggle_popout,
            relief=tk.FLAT,
            bd=0,
            padx=6,
            pady=2,
            highlightthickness=0,
            cursor="hand2",
        )
        popout.place(relx=1.0, x=-8, y=36, anchor="ne")

    def _create_movie_controls(self):
        if self._images is None or len(self._images) <= 1:
            return

        controls = ttk.Frame(self._viewer_host)
        controls.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(4, 0))
        self._movie_controls = controls

        n_frames = len(self._images)
        self._frame_var = tk.IntVar(value=self._ase_guis[0].frame if self._ase_guis else 0)

        ttk.Label(controls, text="Frame:").pack(side=tk.LEFT, padx=(0, 4))

        def on_slider(value):
            frame = int(float(value))
            self._frame_var.set(frame)
            if self._ase_guis:
                self._ase_guis[0].set_frame(frame)

        slider = ttk.Scale(
            controls,
            from_=0,
            to=max(0, n_frames - 1),
            orient=tk.HORIZONTAL,
            variable=self._frame_var,
            command=on_slider,
        )
        slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self._frame_label = ttk.Label(controls, text=f"{self._frame_var.get() + 1} / {n_frames}")
        self._frame_label.pack(side=tk.LEFT, padx=(0, 8))

        def update_frame_label(*_args):
            self._frame_label.config(text=f"{self._frame_var.get() + 1} / {n_frames}")

        self._frame_var.trace_add("write", update_frame_label)

        def step(delta):
            current = self._frame_var.get()
            new_frame = max(0, min(n_frames - 1, current + delta))
            self._frame_var.set(new_frame)
            on_slider(new_frame)

        def play():
            if getattr(self, "_playing", False):
                return
            self._playing = True
            self._animate()

        def stop():
            self._playing = False

        def _animate():
            if not getattr(self, "_playing", False):
                return
            current = self._frame_var.get()
            new_frame = (current + 1) % n_frames
            self._frame_var.set(new_frame)
            on_slider(new_frame)
            self._movie_controls.after(200, self._animate)

        for text, cmd in [
            ("First", lambda: step(-(n_frames - 1))),
            ("Back", lambda: step(-1)),
            ("Play", play),
            ("Stop", stop),
            ("Forward", lambda: step(1)),
            ("Last", lambda: step(n_frames - 1)),
        ]:
            ttk.Button(controls, text=text, width=6, command=cmd).pack(side=tk.LEFT, padx=2)

    def _set_bottom_view(self, key):
        if len(self._ase_guis) >= 2:
            self._ase_guis[1].set_view(key)

    def _open_custom_rotation(self):
        if len(self._ase_guis) >= 2:
            self._ase_guis[1].rotate_window()

    def _reset_views(self):
        if len(self._ase_guis) < 2:
            return
        self._ase_guis[0].set_view("X")
        self._ase_guis[0].focus()
        self._ase_guis[1].set_view("Z")
        self._ase_guis[1].focus()

    def _create_ase_gui(self, ase_ui, gui_class, parent, rotations):
        original_window = ase_ui.ASEGUIWindow
        module = self

        class ModuleWindow(EmbeddedASEGUIWindow, original_window):
            def __init__(self, close, menu, config, scroll, scroll_event,
                         press, move, release, resize):
                EmbeddedASEGUIWindow.__init__(
                    self,
                    parent,
                    module._destroy_viewer,
                    menu,
                    config,
                    scroll,
                    scroll_event,
                    press,
                    move,
                    release,
                    resize,
                    popout=False,
                )

            def movie(self):
                # Prevent ASE from creating its own Movie windows.
                # The module creates a single shared Movie dialog instead.
                pass

            def plot_graphs(self, key=None, expr=None, ignore_if_nan=False):
                # Prevent ASE from opening the graphs window automatically.
                pass

        ase_ui.ASEGUIWindow = ModuleWindow
        # Store original GUI class methods before wrapping
        original_gui_movie = gui_class.movie
        original_gui_plot_graphs = gui_class.plot_graphs

        # Suppress ASE's movie() and plot_graphs() at the class level.
        # These are called in GUI.__init__ when len(images) > 1,
        # before we can install hooks on individual instances.
        # We use lambda functions that accept the expected signatures
        # but do nothing, suppressing the automatic dialogs.
        gui_class.movie = lambda self: None
        gui_class.plot_graphs = lambda self, key=None, expr=None, ignore_if_nan=False: None

        try:
            # Pass expr="" to prevent plot_graphs from being called.
            # GUI.__init__ checks: if expr is not None and expr != '' and len(self.images) > 1
            self._ase_guis.append(gui_class(self._images, rotations=rotations, expr=""))
        finally:
            ase_ui.ASEGUIWindow = original_window
            # Restore original GUI class methods
            gui_class.movie = original_gui_movie
            gui_class.plot_graphs = original_gui_plot_graphs

    def _install_sync_hooks(self):
        """Refresh both GUI projections after either one edits shared atoms or changes frame."""
        for gui in self._ase_guis:
            original_draw = gui.draw
            original_set_frame = gui.set_frame

            def draw(status=True, current=gui, base_draw=original_draw):
                result = base_draw(status)
                if not getattr(self, "_syncing_views", False):
                    self._syncing_views = True
                    try:
                        for sibling in self._ase_guis:
                            if sibling is current:
                                continue
                            if len(sibling.images) and sibling.frame < len(sibling.images):
                                sibling.set_frame(sibling.frame)
                            else:
                                sibling.draw()
                    finally:
                        self._syncing_views = False
                return result

            gui.draw = draw

            def set_frame(frame=None, update=True, current=gui, base_set_frame=original_set_frame):
                result = base_set_frame(frame, update)
                if not getattr(self, "_syncing_views", False):
                    self._syncing_views = True
                    try:
                        for sibling in self._ase_guis:
                            if sibling is current:
                                continue
                            sibling.set_frame(frame, update)
                    finally:
                        self._syncing_views = False
                return result

            gui.set_frame = set_frame

            # Suppress ASE's automatic Movie and Graphs windows.
            # The module creates a single shared Movie control panel instead.
            gui.movie = lambda *args, **kwargs: None
            gui.plot_graphs = lambda *args, **kwargs: None
            gui.movie_window = None

    def _toggle_popout(self):
        if self._images is None:
            messagebox.showinfo(
                "No structure loaded",
                "Open a structure before popping out the viewer.",
                parent=self.main_frame.winfo_toplevel(),
            )
            return
        self._viewer_popout = not self._viewer_popout
        self._create_viewer()

    def _destroy_viewer(self):
        guis = self._ase_guis
        self._ase_guis = []
        window = self._viewer_window
        self._viewer_window = None
        layout = self._viewer_layout
        self._viewer_layout = None
        controls = self._viewer_controls
        self._viewer_controls = None
        self._viewer_host = None
        for gui in guis:
            try:
                gui.window.exists = False
                gui.window.destroy()
            except (AttributeError, tk.TclError):
                pass
        if layout is not None:
            try:
                layout.destroy()
            except tk.TclError:
                pass
        if controls is not None:
            try:
                controls.destroy()
            except tk.TclError:
                pass
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _reset_images_to_current_frame(self):
        """Remove all frames except the current one for mode switching.
        
        When switching to another mode (like Add solvent), we want to:
        1. Keep only the current frame
        2. Reset the history to avoid frame switching issues when returning later
        """
        if self._images is None or len(self._images) <= 1:
            return
        
        # Get the current frame index
        current_frame = self._ase_guis[0].frame if self._ase_guis else 0
        
        # Get the current atoms
        current_atoms = self._images[current_frame]
        
        # Rebuild images with only the current frame
        from ase.gui.images import Images
        new_images = Images()
        new_images.initialize([current_atoms], filenames=[None])
        
        # Replace the old images with the new one-frame images
        self._images = new_images
        if self._ase_guis:
            for gui in self._ase_guis:
                gui.images = new_images
                gui.frame = 0
                gui.draw()
    
    def _show_ase_missing(self):
        messagebox.showerror(
            "ASE is required",
            "Install the project dependencies to use the Periodic workspace. "
            "The ASE GUI package is not available in the active Python environment.",
            parent=self.main_frame.winfo_toplevel(),
        )

    def deactivate(self):
        super().deactivate()

    def cleanup(self):
        self._destroy_viewer()
        super().cleanup()

    def get_session_state(self):
        return {"file_path": self.path_var.get()}

    def apply_session_state(self, state):
        if isinstance(state, dict):
            path = state.get("file_path", "")
            if path and os.path.isfile(path):
                self.path_var.set(path)
