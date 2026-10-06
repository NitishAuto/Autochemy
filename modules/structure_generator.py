"""
Structure Generator Module for AutoChemy.

Reads CIF, POSCAR, XYZ and other formats supported by pymatgen/ASE.
Provides visualization using an embedded ASE GUI in a Toplevel window.
Supports bulk structures, slabs, and molecular systems.
"""

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import traceback

from modules.base_module import BaseModule


class StructureGeneratorModule(BaseModule):
    """Dedicated workspace for reading and visualizing crystal structures."""

    def get_name(self) -> str:
        return "Structure Generator"

    def get_icon(self) -> str:
        return "🔷"

    def create_ui(self):
        """Create the main UI for structure loading and preview."""
        self.main_frame = ttk.Frame(self.parent_frame)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Title
        title_frame = ttk.Frame(self.main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(title_frame, text="Structure Generator", font=("Segoe UI", 18, "bold")).pack(side=tk.LEFT)

        # File input section
        input_frame = ttk.LabelFrame(self.main_frame, text="Load Structure File", padding=10)
        input_frame.pack(fill=tk.X, pady=(0, 12))

        # Path input
        path_row = ttk.Frame(input_frame)
        path_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(path_row, text="File Path:", width=12).pack(side=tk.LEFT)
        
        self.path_var = tk.StringVar(value="")
        path_entry = ttk.Entry(path_row, textvariable=self.path_var, width=50)
        path_entry.pack(side=tk.LEFT, padx=(0, 8), fill=tk.X, expand=True)

        browse_btn = ttk.Button(path_row, text="Browse", command=self._browse_file, width=12)
        browse_btn.pack(side=tk.LEFT)

        # Supported formats info
        formats_text = "Supported formats: CIF, POSCAR, CONTCAR, XYZ, JSON, and more (via pymatgen/ASE)"
        ttk.Label(input_frame, text=formats_text, font=("Segoe UI", 9), foreground="#666666").pack(fill=tk.X)

        # Load button
        load_btn_frame = ttk.Frame(input_frame)
        load_btn_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(load_btn_frame, text="Load Structure", command=self._load_structure).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(load_btn_frame, text="Clear", command=self._clear_structure).pack(side=tk.LEFT)

        # Structure info section
        info_frame = ttk.LabelFrame(self.main_frame, text="Structure Info", padding=10)
        info_frame.pack(fill=tk.X, pady=(0, 12))

        self.info_text = tk.Text(info_frame, height=8, width=60, state=tk.DISABLED)
        self.info_text.pack(fill=tk.BOTH, expand=True)

        # Preview section
        preview_frame = ttk.LabelFrame(self.main_frame, text="Preview", padding=10)
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        preview_btn_frame = ttk.Frame(preview_frame)
        preview_btn_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(preview_btn_frame, text="Open 3D Viewer (ASE)", command=self._open_ase_viewer).pack(side=tk.LEFT)

        self.preview_label = ttk.Label(
            preview_frame,
            text="Load a structure and click 'Open 3D Viewer' to visualize it.",
            foreground="#888888"
        )
        self.preview_label.pack(fill=tk.BOTH, expand=True)

        # State
        self.current_structure = None
        self.viewer_window = None

    def _browse_file(self):
        """Open file dialog to select a structure file."""
        file_types = [
            ("All Structure Formats", "*.cif *.poscar *.contcar *.xyz *.json *.vasp *.in"),
            ("CIF Files", "*.cif"),
            ("POSCAR/CONTCAR", "*.poscar *.contcar *.vasp"),
            ("XYZ Files", "*.xyz"),
            ("JSON Files", "*.json"),
            ("All Files", "*.*"),
        ]
        filename = filedialog.askopenfilename(
            title="Select Structure File",
            filetypes=file_types,
            parent=self.parent_frame
        )
        if filename:
            self.path_var.set(filename)

    def _load_structure(self):
        """Load structure from file using pymatgen or ASE."""
        file_path = self.path_var.get().strip()
        if not file_path:
            messagebox.showwarning("No File", "Please enter a file path or use Browse.")
            return
        if not os.path.isfile(file_path):
            messagebox.showerror("File Not Found", f"File does not exist:\n{file_path}")
            return

        try:
            # Use pymatgen exclusively to read structures and keep a Structure object
            from pymatgen.core import Structure

            self.current_structure = Structure.from_file(file_path)
            self._update_info_display()
            self.preview_label.config(text="✓ Structure loaded successfully. Click 'Open 3D Viewer' to visualize.")

        except Exception as e:
            messagebox.showerror("Load Error", f"Failed to load structure with pymatgen:\n{e}")
            self.preview_label.config(text="✗ Failed to load structure.")
            traceback.print_exc()

    def _update_info_display(self):
        """Update the info text widget with structure details."""
        self.info_text.config(state=tk.NORMAL)
        self.info_text.delete("1.0", tk.END)

        try:
            if self.current_structure is not None:
                struct = self.current_structure
                info = f"""Structure Information:
─────────────────────────────────────────
Formula: {struct.composition.reduced_formula}
Full Formula: {struct.formula}
Density: {struct.density:.3f} g/cm³
Number of Sites: {len(struct)}
Lattice Parameters:
  a = {struct.lattice.a:.4f} Å
  b = {struct.lattice.b:.4f} Å
  c = {struct.lattice.c:.4f} Å
  α = {struct.lattice.alpha:.2f}°
  β = {struct.lattice.beta:.2f}°
  γ = {struct.lattice.gamma:.2f}°

Atomic Symbols:
"""
                species_count = {}
                for site in struct:
                    sym = str(site.species).split()[0]
                    species_count[sym] = species_count.get(sym, 0) + 1
                
                for sym, count in sorted(species_count.items()):
                    info += f"  {sym}: {count}\n"
            else:
                info = "No structure loaded."

            self.info_text.insert("1.0", info)
        except Exception as e:
            self.info_text.insert("1.0", f"Error displaying info:\n{e}")
        finally:
            self.info_text.config(state=tk.DISABLED)

    def _clear_structure(self):
        """Clear loaded structure."""
        self.current_structure = None
        self.path_var.set("")
        self.info_text.config(state=tk.NORMAL)
        self.info_text.delete("1.0", tk.END)
        self.info_text.config(state=tk.DISABLED)
        self.preview_label.config(text="Load a structure and click 'Open 3D Viewer' to visualize it.")

    def _open_ase_viewer(self):
        """Open ASE GUI in a Toplevel window to visualize the structure."""
        if self.current_structure is None:
            messagebox.showwarning("No Structure", "Please load a structure first.")
            return

        try:
            # Convert pymatgen Structure to ASE Atoms if needed
            atoms = self._get_ase_atoms()
            if atoms is None:
                return

            # Create and show the ASE viewer window
            self._show_ase_viewer_toplevel(atoms)

        except Exception as e:
            messagebox.showerror("Viewer Error", f"Failed to open viewer:\n{e}")
            traceback.print_exc()

    def _get_ase_atoms(self):
        """Get ASE Atoms object from current structure."""
        try:
            # Use pymatgen's AseAtomsAdaptor to convert Structure -> ase.Atoms
            if self.current_structure is None:
                return None
            from pymatgen.io.ase import AseAtomsAdaptor
            atoms = AseAtomsAdaptor.get_atoms(self.current_structure)
            return atoms
        except Exception as e:
            messagebox.showerror("Conversion Error", f"Failed to convert structure to ASE format:\n{e}")
            traceback.print_exc()
            return None

    def _show_ase_viewer_toplevel(self, atoms):
        """
        Create and show an ASE GUI viewer in a Toplevel window.
        This is a workaround hack to embed ASE GUI into AutoChemy.
        """
        try:
            import ase.gui.ui as ase_ui
            from ase.gui.gui import GUI
            from ase.gui.images import Images

            # Close previous viewer if open
            if self.viewer_window is not None and self.viewer_window.winfo_exists():
                try:
                    self.viewer_window.destroy()
                except Exception:
                    pass

            # Create a new Toplevel window
            viewer_win = tk.Toplevel(self.parent_frame)
            viewer_win.title("Structure Visualization (ASE GUI)")
            viewer_win.geometry("800x600")
            self.viewer_window = viewer_win

            # Monkeypatch ASE's MainWindow to use Toplevel instead of Tk
            original_init = ase_ui.MainWindow.__init__

            def patched_init(self_mw, title, close=None, menu=[]):
                # Don't create tk.Tk(), use the existing Toplevel
                self_mw.win = viewer_win
                ase_ui.BaseWindow.__init__(self_mw, title, close)
                self_mw.menu = {}
                if menu:
                    self_mw.create_menu(menu)

            ase_ui.MainWindow.__init__ = patched_init

            try:
                # Create Images wrapper from atoms
                images = Images([atoms])

                # Now create and initialize the GUI
                gui = GUI(images, rotations='', show_bonds=False)

                # Restore original __init__
                ase_ui.MainWindow.__init__ = original_init

                # Store the GUI reference so it doesn't get garbage collected
                viewer_win.gui = gui
                viewer_win.atoms = atoms

                # Handle window close
                def on_viewer_close():
                    try:
                        viewer_win.destroy()
                    except Exception:
                        pass
                    self.viewer_window = None

                viewer_win.protocol("WM_DELETE_WINDOW", on_viewer_close)

                # Update display
                gui.draw()

            except Exception as e:
                # Restore original __init__ in case of error
                ase_ui.MainWindow.__init__ = original_init
                raise

        except ImportError as e:
            messagebox.showerror("Missing Package", f"ASE GUI not available:\n{e}")
        except Exception as e:
            messagebox.showerror("Viewer Error", f"Failed to initialize ASE viewer:\n{e}")
            traceback.print_exc()

    def apply_app_theme(self, context):
        """Apply theme from main app."""
        pass

    def get_session_state(self):
        """Capture session state for this module."""
        return {
            "file_path": self.path_var.get(),
        }

    def apply_session_state(self, state):
        """Restore session state."""
        if isinstance(state, dict):
            file_path = state.get("file_path", "")
            if file_path:
                self.path_var.set(file_path)
