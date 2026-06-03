"""Potential Energy Surface (PES) Plot module."""

import csv
import copy
import math
import os
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, colorchooser
from fractions import Fraction
import tempfile
import subprocess

from modules.base_module import BaseModule
from modules.orca_parser import ORCAParser

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.collections import LineCollection
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.offsetbox import OffsetImage, AnnotationBbox
    _HAS_MPL = True
except Exception:
    _HAS_MPL = False

EH_TO_KCAL = 627.509
EH_TO_EV = 27.2114
EH_TO_KJ = 2625.50

# Flow canvas x/y are ~pixel-scale at zoom 1; tiny deltas (e.g. 0.6) look like perfect overlap.
_FLOW_DUP_MIN_SEP = 36.0

# Reaction-flow node scale presets (width/height/text vs zoom)
_FLOW_BOX_SCALE_MAP = {"small": 0.62, "medium": 0.92, "large": 1.42, "xlarge": 1.88}
# Width / height minimum so capsules read as pills (flat top/bottom), not circles, at low zoom.
_FLOW_CAPSULE_MIN_ASPECT = 2.06
_FLOW_STADIUM_SEGS = 64
# Flow diagram zoom: upper cap only; _FLOW_ZOOM_EPS avoids divide-by-zero / denormals (not a real stop for zoom-out).
_FLOW_ZOOM_EPS = 1e-12
_FLOW_ZOOM_MAX = 6.5
# Fit view: tighter allowed viewport ⇒ lower zoom ⇒ more whitespace; capsule gap uses full AABBs not just centers.
_FLOW_FIT_VIEWPORT_FRAC = 0.70
_FLOW_FIT_MIN_CENTER_SEP_PX = 72.0
_FLOW_FIT_MIN_RECT_GAP_PX = 22.0
_FLOW_BOX_DISPLAY = ("Small", "Medium", "Large", "X-Large")
_FLOW_BOX_DISPLAY_TO_KEY = {"Small": "small", "Medium": "medium", "Large": "large", "X-Large": "xlarge"}
_FLOW_BOX_KEY_TO_DISPLAY = {v: k for k, v in _FLOW_BOX_DISPLAY_TO_KEY.items()}



class ModernSwitch(tk.Canvas):
    def __init__(self, parent, width=44, height=24, command=None, bg_color="#e5e7eb", on_color="#10b981", off_color="#9ca3af", **kwargs):
        super().__init__(parent, width=width, height=height, highlightthickness=0, bg=bg_color, **kwargs)
        self.command = command
        self.is_on = False
        self.on_color = on_color
        self.off_color = off_color
        self.bg_color = bg_color
        self.r = height // 2
        self.bind("<Button-1>", self.toggle)
        self.draw()

    def draw(self):
        self.delete("all")
        self.create_polygon(self.r, 0, self.winfo_reqwidth()-self.r, 0, self.winfo_reqwidth(), self.r, self.winfo_reqwidth()-self.r, self.winfo_reqheight(), self.r, self.winfo_reqheight(), 0, self.r, fill=self.on_color if self.is_on else self.off_color, outline="", smooth=True)
        cx = self.winfo_reqwidth() - self.r if self.is_on else self.r
        self.create_oval(cx - self.r + 2, 2, cx + self.r - 2, self.winfo_reqheight() - 2, fill="#ffffff", outline="")

    def toggle(self, event=None):
        self.is_on = not self.is_on
        self.draw()
        if self.command:
            self.command()


class OrcaPreviewFileDialog(tk.Toplevel):
    def __init__(self, parent, initialdir, extract_cb, project_dirs=None):
        super().__init__(parent)
        self.title("Select ORCA output file (3D Preview)")
        self.geometry("1100x700")
        self.minsize(900, 500)
        self.transient(parent)
        self.grab_set()
        
        self.configure(bg="#f8fafc")
        
        self.current_dir = tk.StringVar(value=initialdir if initialdir and os.path.isdir(initialdir) else os.path.expanduser("~"))
        self.selected_path = None
        self.extract_cb = extract_cb
        self.project_dirs = project_dirs or []
        
        self.rot_yaw = 0.42
        self.rot_pitch = -0.30
        self.zoom = 1.0
        self.drag = {"on": False, "x": 0, "y": 0}
        
        self._setup_styles()
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self._build_ui()
        self._load_dir(self.current_dir.get())

    def _setup_styles(self):
        style = ttk.Style(self)
        style.configure("Modern.TFrame", background="#f8fafc")
        style.configure("Sidebar.TFrame", background="#eff6ff")
        style.configure("Modern.TButton", padding=4)
        style.configure("PESAction.TButton", padding=(8, 4), font=("Segoe UI", 9))
        style.configure("Sidebar.Treeview", background="#eff6ff", foreground="#1e293b", fieldbackground="#eff6ff", borderwidth=0, font=("Segoe UI", 10))
        style.map("Sidebar.Treeview", background=[('selected', '#3b82f6')], foreground=[('selected', '#ffffff')])
        
        style.configure("Main.Treeview", background="#ffffff", foreground="#1e293b", fieldbackground="#ffffff", borderwidth=0, font=("Segoe UI", 10))
        style.configure("Main.Treeview.Heading", background="#f1f5f9", foreground="#0f172a", font=("Segoe UI", 10, "bold"), borderwidth=1, relief="flat")
        style.map("Main.Treeview", background=[('selected', '#6366f1')], foreground=[('selected', '#ffffff')])

    def _build_ui(self):
        top_bar = ttk.Frame(self, padding=5, style="Modern.TFrame")
        top_bar.pack(fill=tk.X)
        self.btn_up = ttk.Button(top_bar, text="⇧ Up", width=6, command=self._go_up, style="Modern.TButton")
        self.btn_up.pack(side=tk.LEFT)
        ent = ttk.Entry(top_bar, textvariable=self.current_dir, font=("Segoe UI", 10))
        ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ent.bind("<Return>", lambda e: self._load_dir(self.current_dir.get()))
        
        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg="#cbd5e1", sashwidth=2)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        left_side = ttk.Frame(paned, style="Sidebar.TFrame")
        paned.add(left_side, minsize=150, stretch="never")
        
        self.sidebar = ttk.Treeview(left_side, show="tree", selectmode="browse", style="Sidebar.Treeview")
        self.sidebar.pack(fill=tk.BOTH, expand=True)
        self._populate_sidebar()
        self.sidebar.bind("<<TreeviewSelect>>", self._on_sidebar_select)
        
        center_frame = ttk.Frame(paned, style="Modern.TFrame")
        paned.add(center_frame, minsize=300, stretch="always")
        
        cols = ("Name", "Size", "Type")
        self.tree = ttk.Treeview(center_frame, columns=cols, show="headings", selectmode="browse", style="Main.Treeview")
        self.tree.heading("Name", text="Name")
        self.tree.heading("Size", text="Size")
        self.tree.heading("Type", text="Type")
        self.tree.column("Name", width=250)
        self.tree.column("Size", width=80, anchor="e")
        self.tree.column("Type", width=100)
        
        ysb = ttk.Scrollbar(center_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=ysb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        
        right_frame = ttk.Frame(paned, style="Modern.TFrame")
        paned.add(right_frame, minsize=300, stretch="always")
        
        self.canvas = tk.Canvas(right_frame, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        self.lbl_status = ttk.Label(right_frame, text="Select an .out or .log file...", background="#ffffff", foreground="#64748b", font=("Segoe UI", 12))
        self.lbl_status.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        
        bot_bar = ttk.Frame(self, padding=5, style="Modern.TFrame")
        bot_bar.pack(fill=tk.X)
        self.entry_file = ttk.Entry(bot_bar, font=("Segoe UI", 10))
        self.entry_file.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(bot_bar, text="Cancel", command=self.on_cancel, width=10, style="Modern.TButton").pack(side=tk.RIGHT)
        ttk.Button(bot_bar, text="Open", command=self.on_open, width=10, style="Modern.TButton").pack(side=tk.RIGHT, padx=5)

        self.canvas.bind("<Configure>", self._on_canvas_resize)
        
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<MouseWheel>", self._on_scroll)
        self.canvas.bind("<Button-4>", self._on_scroll)
        self.canvas.bind("<Button-5>", self._on_scroll)
        
        self._parsed_atoms = None

    def _populate_sidebar(self):
        home = os.path.expanduser("~")
        
        if self.project_dirs:
            pj_node = self.sidebar.insert("", "end", text="Project Folders", open=True)
            for p in self.project_dirs:
                name = os.path.basename(p) or p
                self.sidebar.insert(pj_node, "end", text=f"📂 {name}", tags=("shortcut", p))

        shortcuts = [
            ("Home", home),
            ("Desktop", os.path.join(home, "Desktop")),
            ("Documents", os.path.join(home, "Documents")),
            ("Downloads", os.path.join(home, "Downloads"))
        ]
        sc_node = self.sidebar.insert("", "end", text="Quick Access", open=True)
        for name, path in shortcuts:
            if os.path.isdir(path):
                self.sidebar.insert(sc_node, "end", text=f"⭐ {name}", tags=("shortcut", path))
                
        dr_node = self.sidebar.insert("", "end", text="This PC", open=True)
        import string
        if os.name == 'nt':
            for d in string.ascii_uppercase:
                dp = f"{d}:\\"
                if os.path.exists(dp):
                    self.sidebar.insert(dr_node, "end", text=f"💽 Local Disk ({d}:)", tags=("shortcut", dp))
        else:
            self.sidebar.insert(dr_node, "end", text="💻 Root (/)", tags=("shortcut", "/"))

    def _on_sidebar_select(self, event):
        sel = self.sidebar.selection()
        if sel:
            tags = self.sidebar.item(sel[0], "tags")
            if tags and tags[0] == "shortcut":
                self._load_dir(tags[1])

    def _load_dir(self, path):
        if not path: return
        
        # Smart fallback: traverse up if the exact path is missing
        while path and not os.path.isdir(path):
            parent = os.path.dirname(path)
            if parent == path: break
            path = parent
            
        if not path or not os.path.isdir(path): return
        
        self.current_dir.set(path)
        self.tree.delete(*self.tree.get_children())
        try:
            items = os.listdir(path)
        except PermissionError:
            self.lbl_status.config(text="Permission Denied")
            self.lbl_status.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
            return
            
        folders = []
        files = []
        for i in items:
            fp = os.path.join(path, i)
            if os.path.isdir(fp): folders.append((i, fp))
            elif i.lower().endswith(".out") or i.lower().endswith(".log"): files.append((i, fp))
            
        for name, fp in sorted(folders, key=lambda x: x[0].casefold()):
            self.tree.insert("", "end", values=(f"📁 {name}", "", "Folder"), tags=("folder", name))
        for name, fp in sorted(files, key=lambda x: x[0].casefold()):
            try:
                sz = f"{os.path.getsize(fp) // 1024} KB"
            except:
                sz = ""
            self.tree.insert("", "end", values=(f"📄 {name}", sz, "ORCA Output"), tags=("file", name))
        
        self.entry_file.delete(0, tk.END)
        self.canvas.delete("all")
        self.lbl_status.config(text="Select an .out or .log file...")
        self.lbl_status.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        self._parsed_atoms = None

    def _go_up(self):
        parent = os.path.dirname(self.current_dir.get())
        if parent and parent != self.current_dir.get(): 
            self._load_dir(parent)

    def _on_double_click(self, event):
        sel = self.tree.selection()
        if not sel: return
        tags = self.tree.item(sel[0], "tags")
        if "folder" in tags:
            name = tags[1]
            self._load_dir(os.path.join(self.current_dir.get(), name))
        elif "file" in tags:
            self.on_open()

    def _on_select(self, event):
        sel = self.tree.selection()
        if not sel: return
        tags = self.tree.item(sel[0], "tags")
        name = tags[1]
        
        if "folder" not in tags:
            self.entry_file.delete(0, tk.END)
            self.entry_file.insert(0, name)
            
        if "file" in tags:
            fp = os.path.join(self.current_dir.get(), name)
            self.lbl_status.config(text="Extracting geometry...")
            self.lbl_status.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
            self.canvas.delete("all")
            self.update_idletasks()
            
            xyz_path = self.extract_cb(fp)
            if xyz_path:
                self._load_and_draw_xyz(xyz_path)
            else:
                self.lbl_status.config(text="No valid XYZ found in file.")
                self.lbl_status.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
                self._parsed_atoms = None

    def _load_and_draw_xyz(self, xyz_path):
        try:
            with open(xyz_path, "r", encoding="utf-8", errors="ignore") as f:
                rows = [ln.rstrip("\n") for ln in f]
            if not rows: return
            n = int(rows[0].strip())
            atom_rows = rows[2 : 2 + n]
            atoms = []
            for ln in atom_rows:
                p = ln.split()
                if len(p) >= 4:
                    atoms.append((p[0], float(p[1]), float(p[2]), float(p[3])))
            
            self._parsed_atoms = atoms
            self.lbl_status.place_forget()
            
            self.rot_yaw = 0.42
            self.rot_pitch = -0.30
            self.zoom = 1.0
            
            self._draw_preview()
        except Exception:
            self.lbl_status.config(text="Error parsing geometry.")
            self.lbl_status.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

    def _on_press(self, event):
        self.drag = {"on": True, "x": event.x, "y": event.y}

    def _on_drag(self, event):
        if not self.drag["on"] or not self._parsed_atoms: return
        dx = event.x - self.drag["x"]
        dy = event.y - self.drag["y"]
        self.rot_yaw += dx * 0.01
        self.rot_pitch += dy * 0.01
        self.drag["x"] = event.x
        self.drag["y"] = event.y
        self._draw_preview()

    def _on_release(self, event):
        self.drag["on"] = False

    def _on_scroll(self, event):
        if not self._parsed_atoms: return
        if event.num == 4 or getattr(event, 'delta', 0) > 0:
            self.zoom *= 1.1
        elif event.num == 5 or getattr(event, 'delta', 0) < 0:
            self.zoom *= 0.9
        self._draw_preview()

    def _on_canvas_resize(self, event):
        if self._parsed_atoms:
            self._draw_preview()

    def _draw_preview(self):
        c = self.canvas
        c.delete("all")
        if not self._parsed_atoms: return
        
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())
        
        ax, ay, az = 0.0, 0.0, 0.0
        for (_, x, y, z) in self._parsed_atoms:
            ax += x; ay += y; az += z
        N = len(self._parsed_atoms)
        ax /= N; ay /= N; az /= N
        norm = [(s, x - ax, y - ay, z - az) for (s, x, y, z) in self._parsed_atoms]
        
        max_r = max((x*x + y*y + z*z)**0.5 for (_, x, y, z) in norm) if norm else 1.0
        if max_r == 0: max_r = 1.0
        
        scale = min(w, h) * 0.35 * self.zoom / max_r
        
        import math
        cy, sy = math.cos(self.rot_yaw), math.sin(self.rot_yaw)
        cp, sp = math.cos(self.rot_pitch), math.sin(self.rot_pitch)
        
        color_map = {
            "H": "#ffffff", "C": "#808080", "N": "#3050f8", "O": "#ff0d0d", "S": "#ffff30", "P": "#ff8000",
            "F": "#90e050", "CL": "#1ff01f", "BR": "#a62929", "I": "#940094", "FE": "#e06633", "MN": "#9c7ac7"
        }
        rad_map = {
            "H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "F": 0.57,
            "P": 1.07, "S": 1.05, "CL": 1.02, "BR": 1.20, "I": 1.39
        }
        
        proj = []
        for i, (sym, x, y, z) in enumerate(norm):
            x1 = x*cy - z*sy
            z1 = x*sy + z*cy
            py_rot = y*cp - z1*sp
            pz_rot = y*sp + z1*cp
            
            px = w * 0.5 + x1 * scale
            py = h * 0.5 - py_rot * scale
            proj.append((i, sym.upper(), px, py, pz_rot))
        
        proj.sort(key=lambda t: t[4])
        
        bonds = []
        for i in range(len(norm)):
            for j in range(i + 1, len(norm)):
                sy1 = norm[i][0].upper()
                sy2 = norm[j][0].upper()
                r1 = rad_map.get(sy1, 0.7)
                r2 = rad_map.get(sy2, 0.7)
                thresh = (r1 + r2) * 1.3
                dx = norm[i][1] - norm[j][1]
                dy = norm[i][2] - norm[j][2]
                dz = norm[i][3] - norm[j][3]
                if (dx*dx + dy*dy + dz*dz)**0.5 < thresh:
                    bonds.append((i, j))
                    
        for i, j in bonds:
            try:
                pi = next(p for p in proj if p[0] == i)
                pj = next(p for p in proj if p[0] == j)
                c.create_line(pi[2], pi[3], pj[2], pj[3], width=3*self.zoom, fill="#555555", capstyle=tk.ROUND)
            except StopIteration:
                pass
            
        for i, sym, px, py, z in proj:
            rad = rad_map.get(sym, 0.7) * scale * 0.4
            col = color_map.get(sym, "#ff99cc")
            c.create_oval(px - rad, py - rad, px + rad, py + rad, fill=col, outline="#222222", width=1.5)

    def on_open(self):
        fp = os.path.join(self.current_dir.get(), self.entry_file.get())
        if os.path.isfile(fp):
            self.selected_path = fp
            self.destroy()
        else:
            messagebox.showinfo("Preview Browser", "Selected file does not exist.", parent=self)

    def on_cancel(self):
        self.selected_path = None
        self.destroy()

    def show(self):
        self.wait_window()
        return self.selected_path

def _sanitize_math_text(text):
    if not text:
        return text
    text = str(text)
    if "$" in text or "\\" in text:
        try:
            import matplotlib.mathtext as mt
            mt.MathTextParser('path').parse(text)
        except Exception:
            # Strip problematic characters so it renders as raw literal text
            return text.replace("$", "").replace("\\", "")
    return text

def _ask_rich_text(title, prompt, initialvalue="", parent=None):
    import tkinter as tk
    from tkinter import ttk
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    if parent:
        dlg.transient(parent)
    
    result = [None]
    
    msg = ttk.Label(dlg, text=prompt)
    msg.pack(padx=10, pady=(10, 5), anchor="w")
    
    tb = ttk.Frame(dlg)
    tb.pack(fill=tk.X, padx=10, pady=2)
    
    entry = ttk.Entry(dlg, width=50)
    entry.insert(0, initialvalue)
    
    def insert_tag(pre, post=""):
        idx = entry.index(tk.INSERT)
        if entry.selection_present():
            sel_start = entry.index(tk.SEL_FIRST)
            sel_end = entry.index(tk.SEL_LAST)
            text = entry.get()
            selected = text[sel_start:sel_end]
            entry.delete(sel_start, sel_end)
            entry.insert(sel_start, f"{pre}{selected}{post}")
            entry.icursor(sel_start + len(pre) + len(selected) + len(post))
        else:
            entry.insert(idx, f"{pre}{post}")
            entry.icursor(idx + len(pre))
        entry.focus_set()
        
    ttk.Button(tb, text="Sub (x\u2082)", width=9, command=lambda: insert_tag("$_{", "}$")).pack(side=tk.LEFT, padx=2)
    ttk.Button(tb, text="Sup (x\u00b2)", width=9, command=lambda: insert_tag("$^{", "}$")).pack(side=tk.LEFT, padx=2)
    ttk.Button(tb, text="Bold (B)", width=8, command=lambda: insert_tag("$\\mathbf{", "}$")).pack(side=tk.LEFT, padx=2)
    ttk.Button(tb, text="Italic (I)", width=8, command=lambda: insert_tag("$\\mathit{", "}$")).pack(side=tk.LEFT, padx=2)
    ttk.Button(tb, text="$...$", width=6, command=lambda: insert_tag("$", "$")).pack(side=tk.LEFT, padx=2)
    
    entry.pack(padx=10, pady=5, fill=tk.X)
    
    btn_frm = ttk.Frame(dlg)
    btn_frm.pack(fill=tk.X, padx=10, pady=(10, 10))
    
    def on_ok(e=None):
        text = entry.get()
        result[0] = text
        dlg.destroy()
        
    def on_cancel(e=None):
        dlg.destroy()
        
    ttk.Button(btn_frm, text="OK", command=on_ok).pack(side=tk.RIGHT, padx=(5,0))
    ttk.Button(btn_frm, text="Cancel", command=on_cancel).pack(side=tk.RIGHT)
    
    dlg.bind("<Return>", on_ok)
    dlg.bind("<Escape>", on_cancel)
    
    dlg.update_idletasks()
    if parent:
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (dlg.winfo_width() // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (dlg.winfo_height() // 2)
        dlg.geometry(f"+{x}+{y}")
        
    entry.focus_set()
    dlg.grab_set()
    dlg.wait_window()
    return result[0]

class PESPlotModule(BaseModule):
    """Potential Energy Plot maker module."""

    def __init__(self, parent_frame):
        super().__init__(parent_frame)
        self.species = []
        self.edge_links = {}
        self._next_species_id = 1
        self._plot_canvas = None
        self._energy_unit = tk.StringVar(value="kcal mol$^{-1}$")
        self._plot_energy_basis = tk.StringVar(value="G")
        self._grid_mode = tk.StringVar(value="Hide: All except Plot")
        self._ref_name = tk.StringVar(value="")
        self._flow_selection = None
        self._plot_style = tk.StringVar(value="CCC: 1 Curved")
        self._plot_shape = tk.StringVar(value="CCL_View_Curved")
        self._plot_colors_mode = tk.StringVar(value="Gradient")
        self._plot_gradient = tk.BooleanVar(value=True)
        self._plot_gradient_map = tk.StringVar(value="PeakMap")
        self._plot_labels = tk.StringVar(value="Clean")
        self._label_color_mode = tk.StringVar(value="Black")
        self._label_custom_color = tk.StringVar(value="#111827")
        self._label_size_mode = tk.StringVar(value="Small")
        self._label_custom_size = tk.DoubleVar(value=11.0)
        self._label_hide_names = tk.BooleanVar(value=False)
        self._label_hide_energies = tk.BooleanVar(value=False)
        self._label_hide_left_axis = tk.BooleanVar(value=False)
        self._label_show_s2_dev = tk.BooleanVar(value=False)
        self._plot_draw_mos = tk.StringVar(value="Auto")
        self._show_images = tk.BooleanVar(value=False)
        self._show_diff_bars = tk.BooleanVar(value=False)
        self._show_merged_plateaus = tk.BooleanVar(value=False)
        self._show_axis_break = tk.BooleanVar(value=False)
        self._show_collision_avoid = tk.BooleanVar(value=True)
        self._aks_temp_label = tk.StringVar(value="T = 373.15 K")
        self._aks_step_labels = tk.StringVar(value="Step 1; Step 2")
        self._aks_axis_x = tk.DoubleVar(value=0.35)
        self._ccc_axis_user_placed = tk.BooleanVar(value=False)
        self._axis_break_low = tk.DoubleVar(value=-20.0)
        self._axis_break_high = tk.DoubleVar(value=20.0)
        self._plateau_tol = tk.DoubleVar(value=0.5)
        self._image_scale = tk.DoubleVar(value=0.22)
        self._plot_window = None
        self._plot_assets = []  # draggable overlays: image / labels
        self._diff_bar_label_xy = None  # (x, y) data coords for ΔG‡ label after user drags; None = auto
        self._diff_bar_pathway_idx = tk.IntVar(value=0)  # which linear pathway (all_paths index) for ΔG‡
        self._pathway_options = []  # filled each _render_plot: {"idx","label","ids"}
        self._suppress_diff_path_event = False
        self._selected_asset_idx = None  # PPT-like selected asset
        self._asset_handle_info = None   # handle data-coords for hit-testing
        self._flow_zoom = 1.0
        self._hovered_node = None
        self._drag_edge = None
        self._drag_node = None
        self._drag_state = None
        self._flow_overlays = []
        self._flow_overlay_tkimgs = {}
        self._flow_detached = False
        self._flow_detached_win = None
        self._flow_canvas_embedded = None
        self._flow_detach_sash_main = None
        self._flow_detach_sash_bottom = None
        self._pan_start = None
        self._dark_mode = tk.BooleanVar(value=False)
        self._show_flow_rel_g = tk.BooleanVar(value=False)
        self._flow_edge_style = tk.StringVar(value="curve")
        self._flow_multi_selected = set()
        self._flow_rsel_start = None
        self._flow_rsel_rect = None
        self._flow_rsel_moved = False
        self._flow_rc_tags = ()
        self._flow_rc_xy = (0.0, 0.0)
        self._flow_undo_stack = []
        self._flow_rdrag_group = None
        self._flow_rdrag_copy = False
        self._flow_clipboard = None
        self._flow_paste_seq = 0
        self._flow_draw_scheduled = False

    def _alloc_species_id(self) -> int:
        sid = self._next_species_id
        self._next_species_id += 1
        return sid

    def _ensure_species_ids(self):
        max_id = 0
        for sp in self.species:
            sid = sp.get("id")
            if isinstance(sid, int) and sid > 0:
                max_id = max(max_id, sid)
                continue
            sp["id"] = self._alloc_species_id()
            max_id = max(max_id, sp["id"])
        if max_id >= self._next_species_id:
            self._next_species_id = max_id + 1

    def _flow_box_scale(self) -> float:
        return 1.42

    @staticmethod
    def _flow_text_zoom(z: float) -> float:
        """Linear past z=1 (no super-linear boost ? text never outgrows its pill/spacing); zoom-out floor for legibility."""
        z = max(_FLOW_ZOOM_EPS, float(z))
        if z >= 1.0:
            return z
        return max(0.65, z + 0.42 * (1.0 - z))

    def _flow_effective_box_scale(self, sp: dict) -> float:
        """Per-species capsule scale when set; otherwise global Box preset."""
        try:
            raw = (sp or {}).get("flow_box_bs")
            if raw is not None and str(raw).strip() != "":
                v = float(raw)
                if math.isfinite(v) and v > 0.08:
                    return min(v, 4.0)
        except (TypeError, ValueError):
            pass
        return self._flow_box_scale()

    def _flow_box_dims(self, sp: dict, z: float, bs: float | None = None) -> tuple[float, float]:
        """Pixel width/height of a flow species capsule at zoom z and optional layout scale bs."""
        if bs is None:
            bs = self._flow_effective_box_scale(sp or {})
        split = bool(getattr(self, "_show_flow_rel_g", None) and self._show_flow_rel_g.get())
        zz = max(float(z), _FLOW_ZOOM_EPS)
        zt = self._flow_text_zoom(zz)
        zb = zz
        min_w = int(max(10.0, 32.0 * zb) * bs)
        min_h_s = int(max(16.0, 50.0 * zb) * bs)
        min_h_n = int(max(10.0, 48.0 * zb) * bs)
        base_w = max(min_w, int(160 * zb * bs))
        bh = float(max(min_h_n, int(72 * zb * bs)))
        nm = str((sp or {}).get("name", "") or "")
        cw = max(2.4, min(64.0, 20.0 * zb * bs))
        cap_w = int(800 * zb * bs)
        sp_w = max(float(base_w), min(float(cap_w), float(len(nm) * cw + 80.0 * zb * bs)))
        gap_bh = max(5.0, min(28.0, 6.0 + 56.0 * zb * bs))
        sp_w = max(sp_w, bh * _FLOW_CAPSULE_MIN_ASPECT, bh + gap_bh)
        return sp_w, bh

    def _flow_capsule_fit_fonts(
        self,
        sp: dict,
        box_h: float,
        sp_w: float,
        z: float,
        bs_i: float,
        split_layout: bool,
        energy_text: str,
    ) -> tuple[int, int | None, None]:
        """Cap name / Rel G digits by capsule interior (Segoe bold ~0.62 pt/char). Unit is omited on canvas."""
        zt = self._flow_text_zoom(z) * bs_i
        zf = max(float(z), _FLOW_ZOOM_EPS)
        trim_x = 12.0 + 40.0 * min(zf, 1.05) + 16.0 * max(0.0, min(zf - 1.05, 2.2)) + 7.0 * max(0.0, zf - 3.25)
        trim_x = min(trim_x, 0.20 * float(sp_w))
        nm = str((sp or {}).get("name", "") or "")
        nch = max(3, len(nm))
        ew = max(4, len(energy_text))
        inner_w = max(8.0, float(sp_w) - trim_x)
        want_nm_s = max(8, int(15 * zt))
        want_nm_1 = max(8, int(22 * zt))
        want_ev = max(8, int(18 * zt))

        trim_v = 14.0 + 44.0 * min(zf, 1.05) + 18.0 * max(0.0, min(zf - 1.05, 2.2)) + 8.0 * max(0.0, zf - 3.25)
        trim_v = min(trim_v, 0.25 * float(box_h))
        inner_h = max(10.0, float(box_h) - trim_v)
        cap_nm = min(int(inner_w / (0.65 * float(nch))), int(inner_h * 0.75))
        fs_nm = max(2, min(want_nm_1, cap_nm))

        return fs_nm, want_ev if split_layout else None, None
    def _flow_rel_energy_display(self, rel_energy_val) -> str:
        """String for flow pill (matches selected energy unit; no suffix — unit is in Energy combobox)."""
        if rel_energy_val is None:
            return "—"
        v = float(rel_energy_val)
        u = str(self._energy_unit.get() or "kcal/mol")
        if u == "Hartree":
            return f"{v:+.4f}"
        if u == "eV":
            return f"{v:+.2f}"
        if u == "kJ/mol":
            return f"{v:+.1f}"
        return f"{v:+.1f}"

    def _flow_clamp_zoom(self, z: float | None) -> float:
        z = float(z if z is not None else getattr(self, "_flow_zoom", 1.0))
        if not math.isfinite(z) or z <= 0:
            return 1.0
        return max(_FLOW_ZOOM_EPS, min(_FLOW_ZOOM_MAX, z))

    def _flow_content_bbox_scaled(self, z: float) -> tuple[float, float, float, float]:
        """Axis-aligned bounds of plotted nodes + overlays in canvas coords at zoom z (including padding/slack)."""
        z = float(z)
        plotted = [sp for sp in self.species if self._species_on_flow_canvas(sp)]
        min_x = 1e30
        min_y = 1e30
        max_x = -1e30
        max_y = -1e30
        for sp in plotted:
            sx = float(sp.get("x", 50)) * z
            sy = float(sp.get("y", 100)) * z
            bs = self._flow_effective_box_scale(sp)
            w, h = self._flow_box_dims(sp, z, bs)
            min_x = min(min_x, sx)
            min_y = min(min_y, sy)
            max_x = max(max_x, sx + w)
            max_y = max(max_y, sy + h)
        for ov in getattr(self, "_flow_overlays", []) or []:
            x0, y0, x1, y1 = self._flow_overlay_bbox(ov, z)
            min_x = min(min_x, x0)
            min_y = min(min_y, y0)
            max_x = max(max_x, x1)
            max_y = max(max_y, y1)
        if min_x >= max_x:
            pad = 40.0
            return (-pad, -pad, 400.0 + pad, 300.0 + pad)
        edge_slack = max(72.0, 48.0 * z + 42.0)
        return (
            min_x - edge_slack,
            min_y - edge_slack,
            max_x + edge_slack,
            max_y + edge_slack,
        )

    def _flow_zoom_fits_viewport(self, z: float, usable_w: float, usable_h: float) -> bool:
        """True if scaled content bbox fits usable viewport (scrollbar margins already excluded)."""
        x0, y0, x1, y1 = self._flow_content_bbox_scaled(z)
        bw = x1 - x0
        bh = y1 - y0
        uw = max(80.0, float(usable_w))
        uh = max(72.0, float(usable_h))
        return bw <= uw + 0.5 and bh <= uh + 0.5

    @staticmethod
    def _flow_sep_axis(a0: float, a1: float, b0: float, b1: float) -> float:
        """Separation along one axis between [a0,a1] and [b0,b1]; negative = overlap extent."""
        aa0, aa1 = (a0, a1) if a0 <= a1 else (a1, a0)
        bb0, bb1 = (b0, b1) if b0 <= b1 else (b1, b0)
        if aa1 <= bb0:
            return float(bb0 - aa1)
        if bb1 <= aa0:
            return float(aa0 - bb1)
        return -float(min(aa1, bb1) - max(aa0, bb0))

    def _flow_pair_rect_gap_px(
        self,
        ax1: float,
        ay1: float,
        ax2: float,
        ay2: float,
        bx1: float,
        by1: float,
        bx2: float,
        by2: float,
    ) -> float:
        """Min edge-edge distance between two axis-aligned rects; negative if overlapping area."""
        sx = self._flow_sep_axis(ax1, ax2, bx1, bx2)
        sy = self._flow_sep_axis(ay1, ay2, by1, by2)
        if sx >= 0.0 and sy >= 0.0:
            return float(math.hypot(sx, sy))
        if sx < 0.0 <= sy:
            return float(sy)
        if sy < 0.0 <= sx:
            return float(sx)
        return float(min(sx, sy))

    def _flow_min_pairwise_center_separation_px(self, z: float) -> float:
        """Smallest distance between node centers on the flow canvas at zoom z (pixels)."""
        plotted = [sp for sp in self.species if self._species_on_flow_canvas(sp)]
        if len(plotted) < 2:
            return 1e9
        zz = float(z)
        md = 1e30
        for i, spi in enumerate(plotted):
            sx = float(spi.get("x", 50)) * zz
            sy = float(spi.get("y", 100)) * zz
            bsi = self._flow_effective_box_scale(spi)
            wi, hi = self._flow_box_dims(spi, zz, bsi)
            cxi = sx + wi * 0.5
            cyi = sy + hi * 0.5
            for j in range(i + 1, len(plotted)):
                spj = plotted[j]
                tx = float(spj.get("x", 50)) * zz
                ty = float(spj.get("y", 100)) * zz
                bsj = self._flow_effective_box_scale(spj)
                wj, hj = self._flow_box_dims(spj, zz, bsj)
                md = min(md, math.hypot(cxi - (tx + wj * 0.5), cyi - (ty + hj * 0.5)))
        return md if md < 1e29 else 1e9

    def _flow_min_pairwise_node_rect_gap_px(self, z: float) -> float:
        """Smallest pairwise gap between node bounding rectangles (captures overlapping pills vs center-only misses)."""
        plotted = [sp for sp in self.species if self._species_on_flow_canvas(sp)]
        if len(plotted) < 2:
            return 1e9
        zz = float(z)
        boxes: list[tuple[float, float, float, float]] = []
        for sp in plotted:
            sx = float(sp.get("x", 50)) * zz
            sy = float(sp.get("y", 100)) * zz
            bs = self._flow_effective_box_scale(sp)
            w, h = self._flow_box_dims(sp, zz, bs)
            boxes.append((sx, sy, sx + w, sy + h))
        md = 1e30
        for i in range(len(boxes)):
            ax1, ay1, ax2, ay2 = boxes[i]
            for j in range(i + 1, len(boxes)):
                bx1, by1, bx2, by2 = boxes[j]
                md = min(md, self._flow_pair_rect_gap_px(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2))
        return md if md < 1e29 else 1e9

    def _flow_fit_spacing_ok(self, z: float) -> bool:
        """Fit view readability: enough space between capsules and centers."""
        if self._flow_min_pairwise_center_separation_px(z) + 1e-6 < _FLOW_FIT_MIN_CENTER_SEP_PX:
            return False
        if self._flow_min_pairwise_node_rect_gap_px(z) + 1e-6 < _FLOW_FIT_MIN_RECT_GAP_PX:
            return False
        return True

    def _flow_fit_zoom_view(self):
        """Fit full diagram in view with breathing room; zoom out until pills do not visually stack/overlap."""
        c = getattr(self, "flow_canvas", None)
        if c is None:
            return
        try:
            self.main_frame.update_idletasks()
        except Exception:
            pass
        try:
            c.update_idletasks()
        except Exception:
            pass
        vw = max(220, int(c.winfo_width()))
        vh = max(180, int(c.winfo_height()))
        marg = max(132, vw // 22 + vh // 22)
        ux = max(120.0, float(vw - marg)) * _FLOW_FIT_VIEWPORT_FRAC
        uy = max(100.0, float(vh - marg)) * _FLOW_FIT_VIEWPORT_FRAC
        plotted = [sp for sp in self.species if self._species_on_flow_canvas(sp)]
        ovs = list(getattr(self, "_flow_overlays", []) or [])
        if not plotted and not ovs:
            try:
                messagebox.showinfo("Fit view", "No species on the reaction flow diagram to fit.")
            except Exception:
                pass
            return
        if self._flow_zoom_fits_viewport(_FLOW_ZOOM_MAX, ux, uy):
            best = float(_FLOW_ZOOM_MAX)
        elif not self._flow_zoom_fits_viewport(_FLOW_ZOOM_EPS, ux, uy):
            best = float(_FLOW_ZOOM_EPS)
        else:
            lo = float(_FLOW_ZOOM_EPS)
            hi = float(_FLOW_ZOOM_MAX)
            for _ in range(42):
                mid = (lo + hi) * 0.5
                if self._flow_zoom_fits_viewport(mid, ux, uy):
                    lo = mid
                else:
                    hi = mid
            best = lo
        d1_c = self._flow_min_pairwise_center_separation_px(1.0)
        if d1_c < 1e6 and d1_c > 1e-9:
            best = min(best, float(_FLOW_FIT_MIN_CENTER_SEP_PX / d1_c))
        d1_r = self._flow_min_pairwise_node_rect_gap_px(1.0)
        if d1_r > 1e-6:
            best = min(best, float(_FLOW_FIT_MIN_RECT_GAP_PX / d1_r))
        for _ in range(56):
            if self._flow_fit_spacing_ok(best):
                break
            nb = max(float(_FLOW_ZOOM_EPS), best * 0.88)
            if nb >= best - 1e-14:
                break
            best = nb
        self._flow_zoom = self._flow_clamp_zoom(best)
        self._draw_flow()
        self._flow_scroll_content_center_on_bounds()

    def _flow_scroll_content_center_on_bounds(self):
        """After _draw_flow, pan so the scaled content centroid is centered in the viewport."""
        c = getattr(self, "flow_canvas", None)
        if c is None:
            return
        try:
            sr_parts = tuple(map(float, str(c.cget("scrollregion")).split()))
        except (tk.TclError, TypeError, ValueError):
            return
        if len(sr_parts) != 4:
            return
        srx0, sry0, srx1, sry1 = sr_parts
        tw = srx1 - srx0
        th = sry1 - sry0
        try:
            vw = max(2, int(c.winfo_width()))
            vh = max(2, int(c.winfo_height()))
        except tk.TclError:
            return
        z = self._flow_clamp_zoom(getattr(self, "_flow_zoom", 1.0))
        x0, y0, x1, y1 = self._flow_content_bbox_scaled(z)
        mx = (x0 + x1) * 0.5
        my = (y0 + y1) * 0.5
        if tw > vw:
            want_left = mx - vw * 0.5 - srx0
            den = max(tw - vw, 1e-9)
            c.xview_moveto(max(0.0, min(1.0, want_left / den)))
        else:
            c.xview_moveto(0.0)
        if th > vh:
            want_top = my - vh * 0.5 - sry0
            deny = max(th - vh, 1e-9)
            c.yview_moveto(max(0.0, min(1.0, want_top / deny)))
        else:
            c.yview_moveto(0.0)

    def _species_on_flow_canvas(self, sp: dict) -> bool:
        """Reaction-flow diagram shows species unless explicitly hidden."""
        if sp.get("flow_canvas_hide"):
            return False
        if sp.get("flow_canvas_show"):
            return True
        return bool(sp.get("plot", True))

    def _species_flow_faded(self, sp: dict | None) -> bool:
        """Hidden from PES plot but still drawn faintly on the flow canvas."""
        if not sp:
            return False
        return bool(sp.get("flow_faded")) and self._species_on_flow_canvas(sp)

    def _flow_edge_adjacencies(self) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
        outgoing: dict[int, list[int]] = {}
        incoming: dict[int, list[int]] = {}
        for ek in self.edge_links:
            try:
                u, v = map(int, str(ek).split("_"))
            except Exception:
                continue
            outgoing.setdefault(u, []).append(v)
            incoming.setdefault(v, []).append(u)
        return outgoing, incoming

    def _flow_downstream_ids(self, start_id: int) -> set[int]:
        outgoing, _ = self._flow_edge_adjacencies()
        seen: set[int] = set()
        stack = list(outgoing.get(start_id, ()))
        while stack:
            v = stack.pop()
            if v in seen:
                continue
            seen.add(v)
            stack.extend(outgoing.get(v, ()))
        return seen

    def _flow_upstream_ids(self, start_id: int) -> set[int]:
        _, incoming = self._flow_edge_adjacencies()
        seen: set[int] = set()
        stack = list(incoming.get(start_id, ()))
        while stack:
            u = stack.pop()
            if u in seen:
                continue
            seen.add(u)
            stack.extend(incoming.get(u, ()))
        return seen

    def _flow_connected_ids(self, start_id: int) -> set[int]:
        comp = {start_id}
        stack = [start_id]
        while stack:
            u = stack.pop()
            for ek in self.edge_links:
                try:
                    a, b = map(int, str(ek).split("_"))
                except Exception:
                    continue
                if a == u and b not in comp:
                    comp.add(b)
                    stack.append(b)
                elif b == u and a not in comp:
                    comp.add(a)
                    stack.append(a)
        return comp

    def _prompt_flow_hide_from_pes_scope(self, seed_sid: int) -> set[int] | None:
        parent = self._flow_dialog_parent()
        holder: list[set[int] | None] = [None]
        top = tk.Toplevel(parent)
        top.title("Hide from PES plot")
        try:
            top.transient(parent)
            top.grab_set()
        except Exception:
            pass
        sp0 = next((s for s in self.species if int(s.get("id", -1)) == seed_sid), None)
        nm = str((sp0 or {}).get("name", "?"))
        tk.Label(
            top,
            text=(
                "Remove from the matplotlib PES energy plot but keep nodes on the flow diagram\n"
                "(drawn faded). Choose how far to extend along connections:"
            ),
            justify=tk.LEFT,
        ).pack(anchor="w", padx=12, pady=(12, 4))
        tk.Label(top, text=f"Starting box: {nm}", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(0, 8))
        var = tk.StringVar(value="self")
        fr = tk.Frame(top)
        fr.pack(anchor="w", padx=12, pady=4)
        tk.Radiobutton(fr, text="This species only", variable=var, value="self").pack(anchor="w")
        tk.Radiobutton(fr, text="This + downstream (follow arrows forward)", variable=var, value="down").pack(anchor="w")
        tk.Radiobutton(fr, text="This + upstream (reverse arrows)", variable=var, value="up").pack(anchor="w")
        tk.Radiobutton(fr, text="Full connected pathway (all boxes linked undirected)", variable=var, value="conn").pack(anchor="w")

        def _ok():
            mode = var.get()
            base = {seed_sid}
            if mode == "self":
                holder[0] = base
            elif mode == "down":
                holder[0] = base | self._flow_downstream_ids(seed_sid)
            elif mode == "up":
                holder[0] = base | self._flow_upstream_ids(seed_sid)
            else:
                holder[0] = self._flow_connected_ids(seed_sid)
            top.destroy()

        def _cancel():
            holder[0] = None
            top.destroy()

        bf = tk.Frame(top)
        bf.pack(pady=12)
        tk.Button(bf, text="OK", width=10, command=_ok).pack(side=tk.LEFT, padx=6)
        tk.Button(bf, text="Cancel", width=10, command=_cancel).pack(side=tk.LEFT, padx=6)
        top.protocol("WM_DELETE_WINDOW", _cancel)
        try:
            top.geometry(f"+{parent.winfo_rootx() + 48}+{parent.winfo_rooty() + 48}")
        except Exception:
            pass
        parent.wait_window(top)
        return holder[0]

    def _apply_flow_pes_hide_faded(self, ids: set[int]):
        if not ids:
            return
        self._push_flow_undo()
        for sp in self.species:
            sid = int(sp.get("id", -1))
            if sid in ids:
                sp["plot"] = False
                sp["flow_canvas_show"] = True
                sp["flow_faded"] = True
        self._refresh_ref_values()
        self._recompute()
        self._draw_flow()

    def _restore_flow_pes_plot_ids(self, ids: set[int]):
        if not ids:
            return
        self._push_flow_undo()
        for sp in self.species:
            sid = int(sp.get("id", -1))
            if sid in ids:
                sp["plot"] = True
                sp.pop("flow_canvas_show", None)
                sp.pop("flow_faded", None)
        self._refresh_ref_values()
        self._recompute()
        self._draw_flow()

    def _flow_hide_faded_selection_from_pes(self):
        """Toolbar action: hide selected boxes from PES plot."""
        sel = getattr(self, "_flow_multi_selected", set()) or set()
        sids = {int(str(t).split(":", 1)[1]) for t in sel if str(t).startswith("sp:")}
        if not sids:
            messagebox.showinfo("Fade on flow", "Select one or more species boxes first.", parent=self._flow_dialog_parent())
            return
        self._apply_flow_pes_hide_faded(sids)

    def _flow_ctx_change_box_size(self, sid: int, factor: float):
        self._push_flow_undo()
        sel = getattr(self, "_flow_multi_selected", set()) or set()
        if sid != -1 and f"sp:{sid}" not in sel:
            sel = {f"sp:{sid}"}
        
        for tok in sel:
            if tok.startswith("sp:"):
                cur_sid = int(tok.split(":", 1)[1])
                for sp in self.species:
                    if int(sp.get("id", -1)) == cur_sid:
                        sp["flow_box_bs"] = float(sp.get("flow_box_bs", 1.42)) * factor
                        break
        self._queue_flow_draw()

    def _flow_restore_faded_selection_on_pes(self):
        """Toolbar / detached-window action: restore all faded boxes in current flow selection."""
        faded = self._flow_faded_restore_ids_from_selection(getattr(self, "_flow_multi_selected", ()) or set())
        if not faded:
            messagebox.showinfo(
                "Restore on PES plot",
                "None of the selected boxes are faded (hidden from the PES by “Hide from PES plot…” along a pathway).\n\n"
                "Rubber-band or multi-select faded boxes on the flow diagram first, then use this button or "
                "the context-menu item “Restore … faded on PES plot”.",
                parent=self._flow_dialog_parent(),
            )
            return
        self._restore_flow_pes_plot_ids(set(faded))

    def _flow_faded_restore_ids_from_selection(self, sel: set) -> set[int]:
        """Species IDs among selection that are hidden from PES (faded) and still on flow — safe to restore."""
        out: set[int] = set()
        for t in sel or set():
            if not str(t).startswith("sp:"):
                continue
            rid = int(str(t).split(":", 1)[1])
            sp = next((s for s in self.species if int(s.get("id", -1)) == rid), None)
            if sp and self._species_flow_faded(sp):
                out.add(rid)
        return out

    def _flow_ctx_hide_from_pes(self, seed_sid: int):
        sel_sp = [t for t in getattr(self, "_flow_multi_selected", set()) if str(t).startswith("sp:")]
        if len(sel_sp) > 1:
            if not messagebox.askyesno(
                "Hide from PES plot",
                f"Hide {len(sel_sp)} selected species from the PES plot?\n"
                "They stay on the flow diagram (faded). Pathway expansion applies only when a single box is selected.",
                parent=self._flow_dialog_parent(),
            ):
                return
            ids = {int(str(t).split(":", 1)[1]) for t in sel_sp}
            self._apply_flow_pes_hide_faded(ids)
            return
        ids = self._prompt_flow_hide_from_pes_scope(seed_sid)
        if ids:
            self._apply_flow_pes_hide_faded(ids)

    def _prompt_flow_restore_to_pes_scope(self, seed_sid: int) -> set[int] | None:
        parent = self._flow_dialog_parent()
        holder: list[set[int] | None] = [None]
        top = tk.Toplevel(parent)
        top.title("Restore to PES plot")
        try:
            top.transient(parent)
            top.grab_set()
        except Exception:
            pass
        sp0 = next((s for s in self.species if int(s.get("id", -1)) == seed_sid), None)
        nm = str((sp0 or {}).get("name", "?"))
        tk.Label(
            top,
            text=(
                "Restore nodes to the matplotlib PES energy plot.\n"
                "Choose how far to extend the restoration along connections:"
            ),
            justify=tk.LEFT,
        ).pack(anchor="w", padx=12, pady=(12, 4))
        tk.Label(top, text=f"Starting box: {nm}", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(0, 8))
        var = tk.StringVar(value="self")
        fr = tk.Frame(top)
        fr.pack(anchor="w", padx=12, pady=4)
        tk.Radiobutton(fr, text="This species only", variable=var, value="self").pack(anchor="w")
        tk.Radiobutton(fr, text="This + downstream (follow arrows forward)", variable=var, value="down").pack(anchor="w")
        tk.Radiobutton(fr, text="This + upstream (reverse arrows)", variable=var, value="up").pack(anchor="w")
        tk.Radiobutton(fr, text="Full connected pathway (all boxes linked undirected)", variable=var, value="conn").pack(anchor="w")

        def _ok():
            mode = var.get()
            base = {seed_sid}
            if mode == "self":
                holder[0] = base
            elif mode == "down":
                holder[0] = base | self._flow_downstream_ids(seed_sid)
            elif mode == "up":
                holder[0] = base | self._flow_upstream_ids(seed_sid)
            else:
                holder[0] = self._flow_connected_ids(seed_sid)
            top.destroy()

        def _cancel():
            holder[0] = None
            top.destroy()

        bf = tk.Frame(top)
        bf.pack(pady=12)
        tk.Button(bf, text="OK", width=10, command=_ok).pack(side=tk.LEFT, padx=6)
        tk.Button(bf, text="Cancel", width=10, command=_cancel).pack(side=tk.LEFT, padx=6)
        top.protocol("WM_DELETE_WINDOW", _cancel)
        try:
            top.geometry(f"+{parent.winfo_rootx() + 48}+{parent.winfo_rooty() + 48}")
        except Exception:
            pass
        parent.wait_window(top)
        return holder[0]

    def _flow_ctx_restore_to_pes(self, seed_sid: int):
        ids = self._prompt_flow_restore_to_pes_scope(seed_sid)
        if ids:
            self._restore_flow_pes_plot_ids(ids)

    @staticmethod
    def _cubic_bezier_flat(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float], n: int = 44) -> list[float]:
        """Dense cubic Bézier samples — avoids Tk smooth=True spline spikes (pixel jaggies)."""
        x0, y0 = p0
        x1, y1 = p1
        x2, y2 = p2
        x3, y3 = p3
        out: list[float] = []
        for i in range(n + 1):
            t = i / max(1, n)
            o = 1.0 - t
            x = o**3 * x0 + 3 * o**2 * t * x1 + 3 * o * t**2 * x2 + t**3 * x3
            y = o**3 * y0 + 3 * o**2 * t * y1 + 3 * o * t**2 * y2 + t**3 * y3
            out.extend([x, y])
        return out

    @staticmethod
    def _densify_polyline_coords(coords8: tuple[float, ...], per_seg: int = 14) -> list[float]:
        """Linear interpolate between polyline vertices so Tk draws a smooth-looking chain."""
        if len(coords8) < 6:
            return list(coords8)
        pts: list[float] = []
        npts = len(coords8) // 2
        for seg in range(npts - 1):
            x0, y0 = coords8[seg * 2], coords8[seg * 2 + 1]
            x1, y1 = coords8[seg * 2 + 2], coords8[seg * 2 + 3]
            for j in range(per_seg):
                t = j / per_seg
                pts.extend([(1.0 - t) * x0 + t * x1, (1.0 - t) * y0 + t * y1])
        pts.extend([coords8[-2], coords8[-1]])
        return pts

    @staticmethod
    def _flow_corner_radius(w: float, h: float, r: float) -> float:
        """Same corner radius for card outline and header strip (true circular arcs)."""
        return min(max(2.0, float(r)), w * 0.5 - 0.25, h * 0.5 - 0.25)

    @staticmethod
    def _flow_true_rounded_rect_coords(x1: float, y1: float, x2: float, y2: float, r: float, segs: int = 22) -> list[float]:
        """Clockwise rounded rect from circular arcs — matches matplotlib-style corners (not Tk smooth spline)."""
        w = x2 - x1
        h = y2 - y1
        if w <= 1.0 or h <= 1.0:
            return [x1, y1, x2, y1, x2, y2, x1, y2]
        rt = PESPlotModule._flow_corner_radius(w, h, r)
        n = max(6, int(segs))
        pts: list[float] = []

        def add_arc(cx: float, cy: float, r0: float, a_start: float, a_end: float):
            for i in range(n + 1):
                t = i / n
                a = a_start + (a_end - a_start) * t
                pts.extend([cx + r0 * math.cos(a), cy + r0 * math.sin(a)])

        pts.extend([x1 + rt, y1, x2 - rt, y1])
        add_arc(x2 - rt, y1 + rt, rt, -0.5 * math.pi, 0.0)
        pts.extend([x2, y2 - rt])
        add_arc(x2 - rt, y2 - rt, rt, 0.0, 0.5 * math.pi)
        pts.extend([x2 - rt, y2, x1 + rt, y2])
        add_arc(x1 + rt, y2 - rt, rt, 0.5 * math.pi, math.pi)
        pts.extend([x1, y1 + rt])
        add_arc(x1 + rt, y1 + rt, rt, math.pi, 1.5 * math.pi)
        return pts

    @staticmethod
    def _flow_stadium_coords(x1: float, y1: float, x2: float, y2: float, segs: int = 120) -> list[float]:
        """Capsule / stadium polygon (straight top/bottom, semicircular ends)."""
        w = x2 - x1
        h = y2 - y1
        if w <= 1.0 or h <= 1.0:
            return [x1, y1, x2, y1, x2, y2, x1, y2]
        r = min(h * 0.5, w * 0.5)
        cy = y1 + h * 0.5
        cx_r = x2 - r
        cx_l = x1 + r
        n = max(8, int(segs // 2))
        pts: list[float] = []
        pts.extend([cx_l, y1, cx_r, y1])
        for i in range(n + 1):
            a = -0.5 * math.pi + math.pi * (i / n)
            pts.extend([cx_r + r * math.cos(a), cy + r * math.sin(a)])
        pts.extend([cx_r, y2, cx_l, y2])
        for i in range(1, n + 1):
            a = 0.5 * math.pi + math.pi * (i / n)
            pts.extend([cx_l + r * math.cos(a), cy + r * math.sin(a)])
        return pts

    @staticmethod
    def _flow_stadium_top_half_polygon(x1: float, y1: float, x2: float, y2: float, segs_half: int = 60) -> list[float]:
        """Upper half for pastel header when Rel G labels are on."""
        w = x2 - x1
        h = y2 - y1
        if w <= 1.0 or h <= 1.0:
            return [x1, y1, x2, y1, x2, y2, x1, y2]
        r = min(h * 0.5, w * 0.5)
        cy = y1 + h * 0.5
        cx_r = x2 - r
        cx_l = x1 + r
        n = max(5, int(segs_half))
        pts: list[float] = []
        pts.extend([cx_l, y1, cx_r, y1])
        for i in range(n + 1):
            a = -0.5 * math.pi + (0.5 * math.pi) * (i / n)
            pts.extend([cx_r + r * math.cos(a), cy + r * math.sin(a)])
        pts.extend([x2, cy, x1, cy])
        # West (π) → top (−π/2 ≡ 3π/2): must be upper-left quadrant, not π→π/2 (lower quadrant)
        for i in range(n + 1):
            a = math.pi + (0.5 * math.pi) * (i / n)
            pts.extend([cx_l + r * math.cos(a), cy + r * math.sin(a)])
        return pts

    @staticmethod
    def _flow_stadium_bottom_half_polygon(x1: float, y1: float, x2: float, y2: float, segs_half: int = 60) -> list[float]:
        """Lower half (white background under divider)."""
        w = x2 - x1
        h = y2 - y1
        if w <= 1.0 or h <= 1.0:
            return [x1, y1, x2, y1, x2, y2, x1, y2]
        r = min(h * 0.5, w * 0.5)
        cy = y1 + h * 0.5
        cx_r = x2 - r
        cx_l = x1 + r
        n = max(5, int(segs_half))
        pts: list[float] = []
        pts.extend([x1, cy, x2, cy])
        for i in range(n + 1):
            a = (0.5 * math.pi) * (i / n)
            pts.extend([cx_r + r * math.cos(a), cy + r * math.sin(a)])
        pts.extend([cx_r, y2, cx_l, y2])
        for i in range(n + 1):
            a = 0.5 * math.pi + (0.5 * math.pi) * (i / n)
            pts.extend([cx_l + r * math.cos(a), cy + r * math.sin(a)])
        return pts

    def _flow_connection_polyline(
        self,
        sx1: float,
        sy1: float,
        sx2: float,
        sy2: float,
        dx1: float,
        dy1: float,
        dx2: float,
        dy2: float,
        straight: bool,
    ) -> tuple[list[float], float, float]:
        """Polyline coords for hit-testing badge px,py; Bézier sample when curved."""
        my_s = (sy1 + sy2) * 0.5
        my_d = (dy1 + dy2) * 0.5
        mx_s = (sx1 + sx2) * 0.5
        mx_d = (dx1 + dx2) * 0.5
        if straight:
            if dx1 > sx2:
                coords = (
                    sx2, my_s,
                    (sx2 + dx1) * 0.5, my_s,
                    (sx2 + dx1) * 0.5, my_d,
                    dx1, my_d,
                )
                px, py = (sx2 + dx1) * 0.5, (my_s + my_d) * 0.5
            elif dx2 < sx1:
                coords = (
                    sx1, my_s,
                    (sx1 + dx2) * 0.5, my_s,
                    (sx1 + dx2) * 0.5, my_d,
                    dx2, my_d,
                )
                px, py = (sx1 + dx2) * 0.5, (my_s + my_d) * 0.5
            elif dy1 > sy2:
                coords = (
                    mx_s, sy2,
                    mx_s, (sy2 + dy1) * 0.5,
                    mx_d, (sy2 + dy1) * 0.5,
                    mx_d, dy1,
                )
                px, py = (mx_s + mx_d) * 0.5, (sy2 + dy1) * 0.5
            else:
                coords = (
                    mx_s, sy1,
                    mx_s, (sy1 + dy2) * 0.5,
                    mx_d, (sy1 + dy2) * 0.5,
                    mx_d, dy2,
                )
                px, py = (mx_s + mx_d) * 0.5, (sy1 + dy2) * 0.5
            return list(coords), px, py

        if dx1 > sx2:
            p0 = (sx2, my_s)
            p3 = (dx1, my_d)
            span = max(8.0, dx1 - sx2)
            k = 0.42 * span
            p1 = (sx2 + k, my_s)
            p2 = (dx1 - k, my_d)
            px, py = (sx2 + dx1) * 0.5, (my_s + my_d) * 0.5
            flat = self._cubic_bezier_flat(p0, p1, p2, p3, n=180)
            return flat, px, py
        if dx2 < sx1:
            p0 = (sx1, my_s)
            p3 = (dx2, my_d)
            span = max(8.0, sx1 - dx2)
            k = 0.42 * span
            p1 = (sx1 - k, my_s)
            p2 = (dx2 + k, my_d)
            px, py = (sx1 + dx2) * 0.5, (my_s + my_d) * 0.5
            flat = self._cubic_bezier_flat(p0, p1, p2, p3, n=180)
            return flat, px, py
        if dy1 > sy2:
            p0 = (mx_s, sy2)
            p3 = (mx_d, dy1)
            span = max(8.0, dy1 - sy2)
            k = 0.42 * span
            p1 = (mx_s, sy2 + k)
            p2 = (mx_d, dy1 - k)
            px, py = (mx_s + mx_d) * 0.5, (sy2 + dy1) * 0.5
            flat = self._cubic_bezier_flat(p0, p1, p2, p3, n=180)
            return flat, px, py

        coords = (
            mx_s, sy1,
            mx_s, (sy1 + dy2) * 0.5,
            mx_d, (sy1 + dy2) * 0.5,
            mx_d, dy2,
        )
        px, py = (mx_s + mx_d) * 0.5, (sy1 + dy2) * 0.5
        return self._densify_polyline_coords(coords, per_seg=42), px, py

    @staticmethod
    def _flow_canvas_edge_draw(
        canvas: tk.Canvas,
        coords_flat: list[float],
        edge_tag: str,
        straight: bool,
        ew: float,
        stroke: str,
        z: float,
        _is_dark: bool,
        faded: bool = False,
    ):
        """Thin strokes; curved paths are dense Bézier samples (no halo — avoids double-thick look)."""
        zm = max(float(z), _FLOW_ZOOM_EPS)
        _aw = max(4, int(round(max(7 * zm, 2.5 + 12 * math.sqrt(zm)))))
        _ah = max(5, int(round(max(9 * zm, 3.5 + 15 * math.sqrt(zm)))))
        _ar = max(2, int(round(max(3 * zm, 1.8 + 5 * math.sqrt(zm)))))
        ash = (_aw, _ah, _ar)
        ew_eff = max(0.5, ew * 0.5 * (0.82 if faded else 1.0))
        line_kw = dict(
            smooth=True,
            splinesteps=36,
            tags=(edge_tag,),
            capstyle=tk.ROUND,
            joinstyle=tk.ROUND,
        )
        dash_kw = {"dash": (5, 5)} if faded else {}
        if straight:
            canvas.create_line(
                *coords_flat,
                arrow=tk.LAST,
                width=ew_eff,
                fill=stroke,
                capstyle=tk.PROJECTING,
                joinstyle=tk.MITER,
                arrowshape=ash,
                tags=(edge_tag,),
                **dash_kw,
            )
            return
        canvas.create_line(
            *coords_flat,
            arrow=tk.LAST,
            width=ew_eff,
            fill=stroke,
            arrowshape=ash,
            **line_kw,
            **dash_kw,
        )

    def _prune_edge_links(self):
        valid_ids = {sp.get("id") for sp in self.species}
        cleaned = {}
        for edge_idx, payload in self.edge_links.items():
            add_terms = self._normalize_edge_bucket(payload.get("add", {}), valid_ids)
            rem_terms = self._normalize_edge_bucket(payload.get("remove", {}), valid_ids)
            cleaned[edge_idx] = {"add": add_terms, "remove": rem_terms}
        self.edge_links = cleaned

    @staticmethod
    def _normalize_edge_bucket(bucket, valid_ids):
        """
        Normalize bucket to dict[int, float].
        Supports legacy list[int] / list[str] formats.
        """
        out = {}
        if isinstance(bucket, dict):
            items = bucket.items()
        elif isinstance(bucket, list):
            items = [(v, 1.0) for v in bucket]
        else:
            items = []
        for raw_sid, raw_coeff in items:
            try:
                sid = int(raw_sid)
            except Exception:
                continue
            if sid not in valid_ids:
                continue
            try:
                coeff = float(raw_coeff)
            except Exception:
                coeff = 1.0
            if coeff <= 0:
                continue
            out[sid] = coeff
        return out

    def get_name(self) -> str:
        return "PES Plot"

    def get_icon(self) -> str:
        return "📈"

    def get_session_state(self):
        state = {
            "version": 1,
            "snapshot": self._collect_project_snapshot(),
            "energy_unit": self._energy_unit.get(),
            "plot_energy_basis": self._plot_energy_basis.get(),
            "project_name": self.project_combo_var.get() if hasattr(self, "project_combo_var") else "[None]",
            "subproject_name": self.subproject_combo_var.get() if hasattr(self, "subproject_combo_var") else "[None]",
            "save_name": self.proj_save_var.get() if hasattr(self, "proj_save_var") else "",
            "plot_window_open": bool(self._plot_window and self._plot_window.winfo_exists()),
        }
        return state

    def apply_session_state(self, state):
        if not isinstance(state, dict):
            return
        snapshot = state.get("snapshot")
        project_name = str(state.get("project_name", "") or "").strip()
        sub_name = str(state.get("subproject_name", "") or "").strip()
        # Always restore in-progress working state ("where user left off").
        if isinstance(snapshot, dict):
            self._apply_project_snapshot(snapshot)

        try:
            self._energy_unit.set(state.get("energy_unit", self._energy_unit.get()))
        except Exception:
            pass
        try:
            self._plot_energy_basis.set(state.get("plot_energy_basis", self._plot_energy_basis.get()))
        except Exception:
            pass
        # Keep project/sub selectors synced, but DO NOT auto-load saved project.
        try:
            self._refresh_project_list()
            if project_name:
                self.project_combo_var.set(project_name)
            if sub_name and hasattr(self, "_refresh_subproject_list"):
                self._refresh_subproject_list(selected_parent=self.project_combo_var.get(), select_sub=sub_name)
                self.subproject_combo_var.set(sub_name)
        except Exception:
            pass
        try:
            save_name = state.get("save_name")
            if isinstance(save_name, str):
                self.proj_save_var.set(save_name)
        except Exception:
            pass
        try:
            if bool(state.get("plot_window_open", False)):
                self._render_plot()
        except Exception:
            pass

    def create_ui(self):
        import os
        top = self.parent_frame.winfo_toplevel()
        app = getattr(top, "_orca_app", None)
        self.app_dir = app.app_data_dir if (app and hasattr(app, "app_data_dir")) else os.path.expanduser("~")
        self._pes_project_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AutoChemy_User_Data", "pes_plot_projects.json")
        self._pes_plot_defaults_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AutoChemy_User_Data", "pes_plot_defaults.json")
        self._load_plot_defaults()

        self.main_frame = ttk.Frame(self.parent_frame, padding=10)
        
        proj_bar = ttk.Frame(self.main_frame)
        proj_bar.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(proj_bar, text="Project:").pack(side=tk.LEFT)
        self._project_combo_placeholder = "[None]"
        self._subproject_combo_placeholder = "[None]"
        self.project_combo_var = tk.StringVar(value=self._project_combo_placeholder)
        self.project_combo = ttk.Combobox(proj_bar, textvariable=self.project_combo_var, state="readonly", width=18)
        self.project_combo.pack(side=tk.LEFT, padx=(4, 8))
        self.project_combo.bind("<<ComboboxSelected>>", self._on_parent_project_selected)
        ttk.Label(proj_bar, text="Sub-project:").pack(side=tk.LEFT, padx=(0, 4))
        self.subproject_combo_var = tk.StringVar(value=self._subproject_combo_placeholder)
        self.subproject_combo = ttk.Combobox(proj_bar, textvariable=self.subproject_combo_var, state="readonly", width=18)
        self.subproject_combo.pack(side=tk.LEFT, padx=(0, 8))
        self.subproject_combo.bind("<<ComboboxSelected>>", self._on_project_select)
        
        ttk.Label(proj_bar, text="Save sub as:").pack(side=tk.LEFT)
        self.proj_save_var = tk.StringVar()
        ttk.Entry(proj_bar, textvariable=self.proj_save_var, width=15).pack(side=tk.LEFT, padx=(4, 4))
        ttk.Button(proj_bar, text="Save", command=self._save_project).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(proj_bar, text="Delete", command=self._delete_project).pack(side=tk.LEFT, padx=(0, 16))
        
        ttk.Button(proj_bar, text="Import Proj", command=self._import_project_json).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(proj_bar, text="Export Proj", command=self._export_project_json).pack(side=tk.LEFT)
        
        # Load existing list
        self.main_frame.after(100, self._refresh_project_list)

        head = ttk.Frame(self.main_frame)
        head.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(head, text="Potential Energy Plot Maker", font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)

        btns = ttk.Frame(self.main_frame)
        btns.pack(fill=tk.X, pady=(0, 10), ipady=2)
        self._plot_btn_top = self._create_animated_plot_button(btns)
        self._plot_btn_top.pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(btns, text="Quick Add Intermediate", command=lambda: self._quick_add("Intermediate"), style="PESAction.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btns, text="+ Add TS", command=lambda: self._quick_add("TS"), style="PESAction.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btns, text="+ Add Normal", command=lambda: self._quick_add("Normal"), style="PESAction.TButton").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(btns, text="Edit selected", command=self._edit_selected, style="PESAction.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btns, text="Remove selected", command=self._remove_selected, style="PESAction.TButton").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(btns, text="Recompute", command=self._recompute, style="PESAction.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btns, text="Clear Panel", command=self._clear_panel_with_prompt, style="PESAction.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="CSV...", command=self._csv_action_prompt, style="PESAction.TButton").pack(side=tk.LEFT, padx=(8, 4))
        ttk.Button(btns, text="Manual Plot...", command=self._open_manual_plot, style="PESAction.TButton").pack(side=tk.LEFT, padx=(4, 4))

        ref_row = ttk.Frame(self.main_frame)
        ref_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(ref_row, text="Reference intermediate:").pack(side=tk.LEFT)
        self.ref_cb = ttk.Combobox(ref_row, textvariable=self._ref_name, state="readonly", width=24, values=[])
        self.ref_cb.pack(side=tk.LEFT, padx=(6, 0))
        self.ref_cb.bind("<<ComboboxSelected>>", lambda _e: self._recompute())
        ttk.Label(ref_row, text="Unit:").pack(side=tk.LEFT, padx=(12, 4))
        cb_unit = ttk.Combobox(ref_row, textvariable=self._energy_unit, values=["kcal/mol", "eV", "kJ/mol", "Hartree"], state="readonly", width=8)
        cb_unit.pack(side=tk.LEFT)
        cb_unit.bind("<<ComboboxSelected>>", lambda _e: self._recompute())

        # Main splitter: top table area, bottom flow+plot area
        self.main_split = tk.PanedWindow(self.main_frame, orient=tk.VERTICAL, sashrelief=tk.RAISED, sashwidth=6)
        self.main_split.pack(fill=tk.BOTH, expand=True)

        # Top: unified table + row editor
        table_wrap = ttk.Frame(self.main_split)
        self._table_wrap = table_wrap
        plotted_outer = ttk.LabelFrame(table_wrap, text="Unified species table", padding=6)
        plotted_outer.pack(fill=tk.BOTH, expand=True)
        cols = ("name", "kind", "plot", "main_out", "sp_out", "image_path", "stoich", "normal_term", "imag_modes", "main_e", "enthalpy", "gibbs", "thermal", "sp_e", "s2", "s2_dev", "e_corr", "g_corr", "h_corr", "rel_e_kcal", "rel_kcal", "rel_h_kcal")
        self._tree_cols = cols
        display_cols = ["kind", "plot", "stoich", "normal_term", "imag_modes", "main_e", "enthalpy", "gibbs", "thermal", "sp_e", "s2", "s2_dev", "e_corr", "g_corr", "h_corr", "rel_e_kcal", "rel_kcal", "rel_h_kcal"]
        
        tree_container = ttk.Frame(plotted_outer)
        tree_container.grid(row=0, column=0, sticky="nsew")
        
        self.tree_fixed = ttk.Treeview(tree_container, columns=("name",), show="tree headings", height=9, displaycolumns=())
        self.tree = ttk.Treeview(tree_container, columns=cols, show="headings", height=9, displaycolumns=display_cols)
        
        self.tree_fixed.pack(side=tk.LEFT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        titles = {
            "name": "Species Name", "kind": "Type", "plot": "Plot?",
            "main_out": "Main .out file", "sp_out": "SP/Solv .out file", "image_path": "Image", "stoich": "Stoich", "normal_term": "Normal Term", "imag_modes": "Imag Modes",
            "main_e": "Electronic E (Eh)", "enthalpy": "Enthalpy (Eh)", "gibbs": "Gibbs (Eh)",
            "thermal": "Thermal Corr (Eh)", "sp_e": "SP Electronic (Eh)", "s2": "<S**2>", "s2_dev": "S**2 Deviation", "e_corr": "E total (Eh)", "g_corr": "G total (Eh)", "h_corr": "H total (Eh)", "rel_e_kcal": "Rel E (kcal/mol)", "rel_kcal": "Rel G (kcal/mol)", "rel_h_kcal": "Rel H (kcal/mol)",
        }
        widths = {"name": 140, "kind": 110, "plot": 60, "main_out": 250, "sp_out": 250, "image_path": 180, "stoich": 70, "normal_term": 80, "imag_modes": 80, "main_e": 140, "enthalpy": 140, "gibbs": 140, "thermal": 140, "sp_e": 140, "s2": 90, "s2_dev": 110, "e_corr": 140, "g_corr": 140, "h_corr": 140, "rel_e_kcal": 140, "rel_kcal": 140, "rel_h_kcal": 140}
        
        self.tree_fixed.heading("#0", text=titles["name"])
        self.tree_fixed.column("#0", width=widths["name"], minwidth=widths["name"]-20, stretch=False, anchor=tk.W)
        
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=0, stretch=False)
        for c in cols:
            self.tree.heading(c, text=titles[c])
            self.tree.column(c, width=widths[c], minwidth=widths[c]-20, stretch=False, anchor=tk.W)
            
        sy = ttk.Scrollbar(plotted_outer, orient=tk.VERTICAL)
        sx = ttk.Scrollbar(plotted_outer, orient=tk.HORIZONTAL, command=self.tree.xview)
        
        def on_yview(*args):
            self.tree_fixed.yview(*args)
            self.tree.yview(*args)
        sy.config(command=on_yview)
        
        def on_tree_fixed_y(*args):
            sy.set(*args)
            self.tree.yview_moveto(args[0])
            
        def on_tree_y(*args):
            sy.set(*args)
            self.tree_fixed.yview_moveto(args[0])
            
        self.tree_fixed.configure(yscrollcommand=on_tree_fixed_y)
        self.tree.configure(yscrollcommand=on_tree_y, xscrollcommand=sx.set)
        
        # Monkey patch tree methods to keep fixed tree in sync
        orig_insert = self.tree.insert
        def new_insert(parent, index, iid=None, **kw):
            res = orig_insert(parent, index, iid=iid, **kw)
            kw_fixed = {k: v for k, v in kw.items() if k in ("text", "tags", "image", "open")}
            self.tree_fixed.insert(parent, index, iid=res, **kw_fixed)
            return res
        self.tree.insert = new_insert

        orig_delete = self.tree.delete
        def new_delete(*items):
            orig_delete(*items)
            self.tree_fixed.delete(*items)
        self.tree.delete = new_delete

        orig_item = self.tree.item
        def new_item(item, option=None, **kw):
            res = orig_item(item, option, **kw)
            if kw:
                kw_fixed = {k: v for k, v in kw.items() if k in ("text", "tags", "image", "open")}
                if kw_fixed:
                    self.tree_fixed.item(item, **kw_fixed)
            return res
        self.tree.item = new_item
        
        orig_selection_set = self.tree.selection_set
        def new_selection_set(*items):
            orig_selection_set(*items)
            self.tree_fixed.selection_set(*items)
        self.tree.selection_set = new_selection_set
        
        orig_selection_add = self.tree.selection_add
        def new_selection_add(*items):
            orig_selection_add(*items)
            self.tree_fixed.selection_add(*items)
        self.tree.selection_add = new_selection_add
        
        orig_selection_remove = self.tree.selection_remove
        def new_selection_remove(*items):
            orig_selection_remove(*items)
            self.tree_fixed.selection_remove(*items)
        self.tree.selection_remove = new_selection_remove
        
        tree_container.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        plotted_outer.grid_columnconfigure(0, weight=1)
        plotted_outer.grid_rowconfigure(0, weight=1)
        
        for t in (self.tree, self.tree_fixed):
            t.tag_configure("separator", background="#dbeafe")
            t.tag_configure("large_dev", background="#fee2e2", foreground="#9f1239")
            t.tag_configure("error_row", background="#fca5a5", foreground="#7f1d1d")
            
        self.tree.bind("<ButtonPress-1>", self._on_tree_press)
        self.tree.bind("<B1-Motion>", self._on_tree_drag)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release)
        self.tree.bind("<Motion>", self._on_tree_hover)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Control-c>", self._on_tree_copy)
        self.tree.bind("<Control-C>", self._on_tree_copy)
        self.tree.bind("<Control-v>", self._on_tree_paste)
        self.tree.bind("<Control-V>", self._on_tree_paste)
        
        # Fixed tree bindings
        def on_tree_sel(e):
            sel = self.tree.selection()
            if self.tree_fixed.selection() != sel:
                self.tree_fixed.selection_set(sel)
        def on_tree_fixed_sel(e):
            sel = self.tree_fixed.selection()
            if self.tree.selection() != sel:
                self.tree.selection_set(sel)
        self.tree.bind("<<TreeviewSelect>>", on_tree_sel, add="+")
        self.tree_fixed.bind("<<TreeviewSelect>>", on_tree_fixed_sel, add="+")
        
        for ev in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>", "<Motion>"):
            self.tree_fixed.bind(ev, lambda e, ev=ev: self.tree.event_generate(ev, x=e.x, y=e.y, rootx=e.x_root, rooty=e.y_root))
        
        self.tree_fixed.bind("<Double-1>", lambda e: self._start_inline_edit(e, col="#0"))
        self._drag_iid = None
        self._tree_drag_over_iid = None
        self._tree_drag_start_y = None
        self._tree_drag_active = False
        self._tree_drop_line_y = None
        self._last_tree_col = None
        self._tree_drop_line = tk.Frame(self.tree, bg="#2563eb", height=2)

        # Bottom: flow + plot split
        self.bottom_split = tk.PanedWindow(self.main_split, orient=tk.VERTICAL, sashrelief=tk.RAISED, sashwidth=8)
        flow_outer = ttk.LabelFrame(self.bottom_split, text="Reaction flow diagram", padding=6)
        self._flow_outer = flow_outer
        self.flow_canvas = tk.Canvas(
            flow_outer, height=320, bg="#ffffff", highlightthickness=0,
            bd=0, relief="flat", insertborderwidth=0,
        )
        self._flow_canvas_embedded = self.flow_canvas
        self.flow_canvas.bind("<Button-1>", self._on_flow_press)
        self.flow_canvas.bind("<B1-Motion>", self._on_flow_drag)
        self.flow_canvas.bind("<ButtonRelease-1>", self._on_flow_release)
        self.flow_canvas.bind("<Motion>", self._on_flow_motion)
        self.flow_canvas.bind("<ButtonPress-3>", self._on_flow_right_click)
        self.flow_canvas.bind("<B3-Motion>", self._on_flow_right_drag)
        self.flow_canvas.bind("<ButtonRelease-3>", self._on_flow_right_release)
        self.flow_canvas.bind("<Configure>", lambda _e: self._schedule_draw_flow())
        
        self.flow_canvas.bind("<MouseWheel>", self._on_flow_vscroll)
        self.flow_canvas.bind("<Button-4>", self._on_flow_vscroll)
        self.flow_canvas.bind("<Button-5>", self._on_flow_vscroll)
        self.flow_canvas.bind("<Shift-MouseWheel>", self._on_flow_hscroll)
        self.flow_canvas.bind("<Shift-Button-4>", self._on_flow_hscroll)
        self.flow_canvas.bind("<Shift-Button-5>", self._on_flow_hscroll)
        self.flow_canvas.bind("<Control-MouseWheel>", self._on_flow_zoom)
        self.flow_canvas.bind("<Control-Button-4>", self._on_flow_zoom)
        self.flow_canvas.bind("<Control-Button-5>", self._on_flow_zoom)
        self.flow_canvas.bind("<Control-z>", self._flow_undo)
        self.flow_canvas.bind("<Control-Z>", self._flow_undo)
        self.flow_canvas.bind("<Control-c>", self._flow_copy_selection)
        self.flow_canvas.bind("<Control-C>", self._flow_copy_selection)
        self.flow_canvas.bind("<Control-v>", self._flow_paste_selection)
        self.flow_canvas.bind("<Control-V>", self._flow_paste_selection)
        self.flow_canvas.bind("<Delete>", self._delete_flow_multi_selection)
        self.flow_canvas.bind("<BackSpace>", self._delete_flow_multi_selection)
        
        # Panning scrollbars
        self.flow_hscroll = ttk.Scrollbar(flow_outer, orient=tk.HORIZONTAL, command=self.flow_canvas.xview)
        self.flow_hscroll.pack(side=tk.BOTTOM, fill=tk.X)
        
        flow_edit = ttk.Frame(flow_outer)
        flow_edit.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        
        self.flow_vscroll = ttk.Scrollbar(flow_outer, orient=tk.VERTICAL, command=self.flow_canvas.yview)
        self.flow_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.flow_canvas.configure(xscrollcommand=self.flow_hscroll.set, yscrollcommand=self.flow_vscroll.set)
        
        self.flow_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        flow_row2 = ttk.Frame(flow_edit)
        flow_row2.pack(fill=tk.X, pady=(0, 0))

        act_right = ttk.Frame(flow_row2)
        act_right.pack(side=tk.RIGHT)

        self._flow_detach_btn = self._flow_chrome_btn(
            act_right, text="Detach Flow Panel", command=self._toggle_flow_detach, primary=True, font_px=9
        )
        self._flow_detach_btn.pack(side=tk.RIGHT, padx=(6, 0))

        tk.Label(act_right, text="(Ctrl + wheel zoom)", foreground="#64748b", font=("Segoe UI", 8)).pack(
            side=tk.RIGHT, padx=(4, 8)
        )
        self._flow_chrome_btn(act_right, text="Unfade", command=self._flow_restore_faded_selection_on_pes).pack(
            side=tk.RIGHT, padx=(6, 0)
        )
        self._flow_chrome_btn(act_right, text="Fade", command=self._flow_hide_faded_selection_from_pes).pack(
            side=tk.RIGHT, padx=(6, 0)
        )
        self._flow_chrome_btn(act_right, text="Undo", command=self._flow_undo).pack(side=tk.RIGHT, padx=(6, 0))

        act_left = ttk.Frame(flow_row2)
        act_left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def _toggle_dark_mode():
            self._dark_mode.set(self.switch_dark.is_on)
            self._draw_flow()

        ttk.Checkbutton(act_left, text="Rel G labels", variable=self._show_flow_rel_g, command=self._draw_flow).pack(
            side=tk.LEFT, padx=(0, 10)
        )
        ttk.Label(act_left, text="Dark Mode", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 4))
        self.switch_dark = ModernSwitch(act_left, command=_toggle_dark_mode, bg_color="#e5e7eb")
        self.switch_dark.pack(side=tk.LEFT, padx=(0, 4))

        self.bottom_split.add(flow_outer, minsize=300)

        self.main_split.add(table_wrap, minsize=230)
        self.main_split.add(self.bottom_split, minsize=320)
        self.main_frame.after(120, self._init_pane_layout)

    @staticmethod
    def _fmt(v, nd=8):
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.{nd}f}"
        return str(v)

    @staticmethod
    def _extract_model_from_out(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for _ in range(240):
                    line = f.readline()
                    if not line:
                        break
                    if line.strip().startswith("!"):
                        toks = [t for t in re.split(r"\s+", line.strip()[1:].strip()) if t]
                        if not toks:
                            return ""
                        # Heuristic: return first two model-like tokens
                        pick = []
                        for t in toks:
                            if t.lower() in ("opt", "optts", "freq", "numfreq", "sp", "tightscf", "slowconv"):
                                continue
                            pick.append(t)
                            if len(pick) >= 2:
                                break
                        return "/".join(pick) if pick else toks[0]
        except Exception:
            pass
        return ""

    def _pick_out(self, target_var):
        import os
        init_dir = None
        curr = target_var.get()
        if curr and os.path.isdir(os.path.dirname(curr)):
            init_dir = os.path.dirname(curr)
        elif getattr(self, "_last_opened_dir", None) and os.path.isdir(self._last_opened_dir):
            init_dir = self._last_opened_dir

        if init_dir:
            pass # OrcaPreviewFileDialog handles initialdir internally
            
        dlg = OrcaPreviewFileDialog(self.main_frame.winfo_toplevel(), init_dir, self._extract_xyz_from_orca_out, project_dirs=self._get_all_project_dirs())
        path = dlg.show()
        if path:
            self._last_opened_dir = os.path.dirname(path)
            target_var.set(path)

    def _pick_image(self, target_var):
        import os
        init_dir = None
        curr = target_var.get()
        if curr and os.path.isdir(os.path.dirname(curr)):
            init_dir = os.path.dirname(curr)
        elif getattr(self, "_last_opened_dir", None) and os.path.isdir(self._last_opened_dir):
            init_dir = self._last_opened_dir

        kwargs = {
            "parent": self.main_frame.winfo_toplevel(),
            "title": "Select image file",
            "filetypes": [("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("All files", "*.*")]
        }
        if init_dir:
            kwargs["initialdir"] = init_dir

        path = filedialog.askopenfilename(**kwargs)
        if path:
            self._last_opened_dir = os.path.dirname(path)
            target_var.set(path)

    def _insert_plot_image_asset(self, x: float, y: float, image_path: str):
        if not image_path:
            return
        self._plot_assets.append(
            {
                "kind": "image",
                "x": float(x),
                "y": float(y),
                "path": image_path,
                "zoom": 0.14,
                "angle": 0.0,
            }
        )

    def _paste_clipboard_image_via_powershell(self, out_path: str) -> bool:
        try:
            safe_out = out_path.replace("'", "''")
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "Add-Type -AssemblyName System.Drawing;"
                "$img=[System.Windows.Forms.Clipboard]::GetImage();"
                "if($img -eq $null){exit 2};"
                f"$img.Save('{safe_out}', [System.Drawing.Imaging.ImageFormat]::Png);"
                "exit 0"
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=8,
            )
            return res.returncode == 0 and os.path.isfile(out_path)
        except Exception:
            return False

    def _insert_plot_image_from_clipboard(self, x: float, y: float):
        out_dir = os.path.join(self.app_dir, "pes_clipboard_images")
        os.makedirs(out_dir, exist_ok=True)
        fname = f"clip_{len(self._plot_assets)+1}.png"
        fpath = os.path.join(out_dir, fname)
        try:
            from PIL import ImageGrab  # optional dependency
            img = ImageGrab.grabclipboard()
            if img is None:
                # ChemDraw on Windows often puts image in WinForms clipboard format.
                if not self._paste_clipboard_image_via_powershell(fpath):
                    messagebox.showinfo("Paste image", "No image found in clipboard.\nTry Copy as Bitmap/PNG in ChemDraw.")
                    return
            elif isinstance(img, list) and img:
                src = next((p for p in img if os.path.isfile(p)), None)
                if not src:
                    messagebox.showinfo("Paste image", "Clipboard file list has no valid image.")
                    return
                from shutil import copyfile
                copyfile(src, fpath)
            else:
                img.save(fpath)
            self._insert_plot_image_asset(x, y, fpath)
        except Exception as e:
            if self._paste_clipboard_image_via_powershell(fpath):
                self._insert_plot_image_asset(x, y, fpath)
                return
            messagebox.showinfo("Paste image", f"Clipboard image paste failed.\n{e}")

    def _asset_context_menu(self, event, asset_idx: int):
        parent = self._plot_window if self._plot_window and self._plot_window.winfo_exists() else self.main_frame.winfo_toplevel()
        m = tk.Menu(parent, tearoff=0)

        def _inc():
            z = float(self._plot_assets[asset_idx].get("zoom", 0.14))
            self._plot_assets[asset_idx]["zoom"] = min(2.0, z * 1.2)
            self._render_plot()

        def _dec():
            z = float(self._plot_assets[asset_idx].get("zoom", 0.14))
            self._plot_assets[asset_idx]["zoom"] = max(0.04, z / 1.2)
            self._render_plot()

        def _delete():
            if 0 <= asset_idx < len(self._plot_assets):
                self._plot_assets.pop(asset_idx)
                self._render_plot()

        def _rot_l():
            a = float(self._plot_assets[asset_idx].get("angle", 0.0))
            self._plot_assets[asset_idx]["angle"] = (a - 10.0) % 360.0
            self._render_plot()

        def _rot_r():
            a = float(self._plot_assets[asset_idx].get("angle", 0.0))
            self._plot_assets[asset_idx]["angle"] = (a + 10.0) % 360.0
            self._render_plot()

        def _set_size():
            v = simpledialog.askfloat("Asset size", "Enter zoom scale (0.04 to 2.0):", initialvalue=float(self._plot_assets[asset_idx].get("zoom", 0.14)), parent=parent)
            if v is None:
                return
            self._plot_assets[asset_idx]["zoom"] = max(0.04, min(2.0, float(v)))
            self._render_plot()

        def _edit_3d():
            self._edit_3d_asset(asset_idx)

        m.add_command(label="Increase size", command=_inc)
        m.add_command(label="Decrease size", command=_dec)
        m.add_command(label="Set size...", command=_set_size)
        m.add_separator()
        m.add_command(label="Rotate left", command=_rot_l)
        m.add_command(label="Rotate right", command=_rot_r)
        m.add_command(label="Delete", command=_delete)
        if str(self._plot_assets[asset_idx].get("kind", "")).startswith("3d"):
            m.add_separator()
            m.add_command(label="Edit 3D...", command=_edit_3d)
        try:
            if hasattr(event, "guiEvent") and event.guiEvent is not None:
                m.tk_popup(event.guiEvent.x_root, event.guiEvent.y_root)
            else:
                m.tk_popup(parent.winfo_pointerx(), parent.winfo_pointery())
        finally:
            m.grab_release()

    def _open_plot_context_menu(self, event, sid: int | None, x: float, y: float):
        parent = self._plot_window if self._plot_window and self._plot_window.winfo_exists() else self.main_frame.winfo_toplevel()
        m = tk.Menu(parent, tearoff=0)

        def _ins_img():
            init_dir = self._species_plot_asset_dir(sid)
            p = filedialog.askopenfilename(
                parent=parent,
                title="Insert image on plot",
                initialdir=init_dir if os.path.isdir(init_dir) else getattr(self, "_last_opened_dir", self.app_dir),
                filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("All files", "*.*")],
            )
            if p:
                self._last_opened_dir = os.path.dirname(p)
                self._insert_plot_image_asset(x, y, p)
                self._render_plot()

        def _paste_img():
            self._insert_plot_image_from_clipboard(x, y)
            self._render_plot()

        def _open_3d():
            self._open_plot_3d_for_species(sid, x=x, y=y)

        m.add_command(label="Insert image...", command=_ins_img)
        m.add_command(label="Paste image from clipboard", command=_paste_img)
        m.add_separator()
        m.add_command(label="Open 3D structure viewer", command=_open_3d)
        try:
            if hasattr(event, "guiEvent") and event.guiEvent is not None:
                m.tk_popup(event.guiEvent.x_root, event.guiEvent.y_root)
            else:
                w = self._plot_window if self._plot_window and self._plot_window.winfo_exists() else parent
                m.tk_popup(w.winfo_pointerx(), w.winfo_pointery())
        finally:
            m.grab_release()

    def _species_plot_asset_dir(self, sid: int | None) -> str:
        """Directory of the species ORCA job (main .out preferred, else SP .out) for file dialogs."""
        sp = next((s for s in self.species if s.get("id") == sid), None) if sid is not None else None
        if not sp:
            return getattr(self, "_last_opened_dir", self.app_dir)
        for key in ("main_out", "sp_out"):
            outp = str(sp.get(key) or "").strip()
            d = os.path.dirname(outp)
            if os.path.isdir(d):
                return d
        return getattr(self, "_last_opened_dir", self.app_dir)

    def _get_all_project_dirs(self):
        dirs = set()
        for sp in self.species:
            for k in ("main_out", "sp_out", "image_path"):
                p = sp.get(k)
                if p:
                    d = os.path.dirname(os.path.abspath(p))
                    dirs.add(d)
        lo = getattr(self, "_last_opened_dir", None)
        if lo and os.path.isdir(lo):
            dirs.add(os.path.abspath(lo))
        return sorted(list(dirs))

    def _remove_plot_asset_drag_ghost(self):
        g = getattr(self, "_plot_asset_ghost_ab", None)
        if g is not None:
            try:
                g.remove()
            except Exception:
                pass
            self._plot_asset_ghost_ab = None
        self._plot_asset_ghost_for_ai = None

    def _clear_plot_asset_ghost_buffer(self):
        self._plot_ghost_arr = None
        self._plot_ghost_zoom = None
        self._plot_ghost_prep_ai = None

    def _prepare_plot_asset_ghost_buffer(self, asset_artists: dict, ai: int) -> bool:
        """Clone OffsetImage pixels once per drag (fast motion updates)."""
        if ai not in asset_artists:
            return False
        if getattr(self, "_plot_ghost_prep_ai", None) == ai and getattr(self, "_plot_ghost_arr", None) is not None:
            return True
        try:
            import numpy as np
            ob = asset_artists[ai].offsetbox
            arr = np.asarray(ob.get_array(), dtype=float, copy=True)
            if arr.size == 0 or arr.ndim != 3:
                return False
            if arr.max() <= 1.0 + 1e-6:
                arr = np.clip(arr, 0.0, 1.0) * 255.0
            arr = np.clip(arr, 0, 255).astype(np.uint8, copy=False)
            arr = np.array(arr, copy=True, dtype=np.uint8)
            if arr.shape[-1] >= 4:
                arr[..., 3] = (arr[..., 3].astype(np.float32) * 0.45).clip(0, 255).astype(np.uint8)
            elif arr.shape[-1] == 3:
                a_ch = np.full(arr.shape[:2], 115, dtype=np.uint8)
                arr = np.concatenate([arr, a_ch[..., np.newaxis]], axis=-1)
            else:
                return False
            self._plot_ghost_arr = arr
            self._plot_ghost_zoom = float(ob.get_zoom())
            self._plot_ghost_prep_ai = ai
            return True
        except Exception:
            self._plot_ghost_arr = None
            self._plot_ghost_zoom = None
            self._plot_ghost_prep_ai = None
            return False

    def _ensure_plot_asset_drag_ghost(self, ax, asset_artists: dict, ai: int, nx: float, ny: float):
        """Semi-transparent ghost image at (nx,ny); main asset stays at saved coords until release."""
        if ai not in asset_artists:
            return
        g = getattr(self, "_plot_asset_ghost_ab", None)
        g_ai = getattr(self, "_plot_asset_ghost_for_ai", None)
        if g is not None and g_ai == ai:
            try:
                if hasattr(g, "set_xy"):
                    g.set_xy((float(nx), float(ny)))
                else:
                    g.xy = (float(nx), float(ny))
            except Exception:
                pass
            return
        self._remove_plot_asset_drag_ghost()
        try:
            from matplotlib.offsetbox import OffsetImage, AnnotationBbox
            if not self._prepare_plot_asset_ghost_buffer(asset_artists, ai):
                return
            arr = self._plot_ghost_arr
            zm = float(self._plot_ghost_zoom or 0.14)
            ghost_oi = OffsetImage(arr, zoom=zm)
            ghost_ab = AnnotationBbox(
                ghost_oi,
                (float(nx), float(ny)),
                xycoords="data",
                frameon=False,
                pad=0,
                zorder=5.25,
            )
            ax.add_artist(ghost_ab)
            self._plot_asset_ghost_ab = ghost_ab
            self._plot_asset_ghost_for_ai = ai
        except Exception:
            self._remove_plot_asset_drag_ghost()

    def _open_plot_3d_for_species(self, sid: int | None, x: float | None = None, y: float | None = None):
        sp = next((s for s in self.species if s.get("id") == sid), None) if sid is not None else None
        init_dir = self._species_plot_asset_dir(sid)
        xyz = filedialog.askopenfilename(
            parent=self._plot_window if self._plot_window and self._plot_window.winfo_exists() else self.main_frame.winfo_toplevel(),
            title="Select XYZ file (or Cancel to extract from ORCA .out)",
            initialdir=init_dir if os.path.isdir(init_dir) else getattr(self, "_last_opened_dir", self.app_dir),
            filetypes=[("XYZ", "*.xyz"), ("All files", "*.*")],
        )
        if xyz:
            self._last_opened_dir = os.path.dirname(xyz)
        if not xyz and sp and sp.get("main_out"):
            xyz = self._extract_xyz_from_orca_out(sp.get("main_out", ""))
        if not xyz:
            return
        def _apply(path, state):
            ax_x = float(x if x is not None else 0.0)
            ax_y = float(y if y is not None else 0.0)
            vz = float((state or {}).get("zoom", 1.0))
            place_zoom = max(0.08, min(0.32, 0.16 * (vz ** 0.25)))
            self._plot_assets.append(
                {
                    "kind": "3d_image",
                    "x": ax_x,
                    "y": ax_y,
                    "path": path,
                    "zoom": place_zoom,
                    "angle": 0.0,
                    "xyz_path": xyz,
                    "viewer_state": state or {},
                }
            )
            self._render_plot()

        self._open_plot_3d_viewer(
            xyz,
            title=f"3D Viewer - {sp.get('name','Structure') if sp else 'Structure'}",
            on_apply=_apply,
            init_state=None,
        )

    def _open_external_3d(self, sid: int, viewer: str):
        import subprocess, shutil
        sp = next((s for s in self.species if s.get("id") == sid), None) if sid is not None else None
        if not sp:
            return
            
        xyz = None
        out_path = sp.get("main_out") or sp.get("sp_out")
        if out_path and os.path.exists(out_path):
            xyz = self._extract_xyz_from_orca_out(out_path)
            
        if not xyz:
            init_dir = self._species_plot_asset_dir(sid)
            xyz = filedialog.askopenfilename(
                parent=self.main_frame.winfo_toplevel(),
                title="Select XYZ file",
                initialdir=init_dir if os.path.isdir(init_dir) else getattr(self, "_last_opened_dir", self.app_dir),
                filetypes=[("XYZ", "*.xyz"), ("All files", "*.*")],
            )
            if xyz:
                self._last_opened_dir = os.path.dirname(xyz)
                
        if not xyz or not os.path.exists(xyz):
            return
            
        if viewer == "ACYView":
            def _apply(path, state):
                pass
            self._open_plot_3d_viewer(
                xyz,
                title=f"ACYView - {sp.get('name', 'Structure')}",
                on_apply=_apply,
                init_state=None,
            )
        elif viewer == "Chemcraft":
            def _find_chemcraft():
                env = os.environ.get("CHEMCRAFT_EXE", "").strip()
                if env and os.path.isfile(env): return env
                for name in ("chemcraft", "Chemcraft", "chemistry"):
                    p = shutil.which(name)
                    if p and os.path.isfile(p): return p
                for p in (r"C:\Program Files\Chemcraft\Chemcraft.exe", r"C:\Program Files\Chemcraft\chemistry.exe", r"C:\Program Files (x86)\Chemcraft\Chemcraft.exe", r"C:\Chemcraft\Chemcraft.exe"):
                    if os.path.isfile(p): return p
                return None
            exe = _find_chemcraft()
            if not exe:
                messagebox.showwarning("Chemcraft", "Chemcraft not found.")
                return
            subprocess.Popen([exe, xyz])
        elif viewer == "Jmol":
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            jmol_jar = os.path.join(base_dir, "jmol-16.3.55", "Jmol.jar")
            if os.path.exists(jmol_jar):
                subprocess.Popen(["java", "-jar", jmol_jar, xyz])
                return
            env_jar = os.environ.get("JMOL_JAR", "").strip()
            if env_jar and os.path.isfile(env_jar):
                subprocess.Popen(["java", "-jar", env_jar, xyz])
                return
            jmol_exe = shutil.which("jmol") or shutil.which("Jmol")
            if jmol_exe:
                subprocess.Popen([jmol_exe, xyz])
                return
            messagebox.showwarning("Jmol", "Jmol not found.")

    def _edit_3d_asset(self, asset_idx: int):
        if asset_idx < 0 or asset_idx >= len(self._plot_assets):
            return
        asset = self._plot_assets[asset_idx]
        xyz = str(asset.get("xyz_path", "") or "").strip()
        if not xyz or not os.path.isfile(xyz):
            messagebox.showinfo("3D edit", "Original XYZ path is missing for this 3D asset.")
            return

        def _apply(path, state):
            if asset_idx < 0 or asset_idx >= len(self._plot_assets):
                return
            self._plot_assets[asset_idx]["path"] = path
            self._plot_assets[asset_idx]["viewer_state"] = state or {}
            if state and "zoom" in state:
                vz = float(state.get("zoom", 1.0))
                self._plot_assets[asset_idx]["zoom"] = max(0.08, min(0.32, 0.16 * (vz ** 0.25)))
            self._render_plot()

        self._open_plot_3d_viewer(
            xyz,
            title="Edit 3D Plot Asset",
            on_apply=_apply,
            init_state=asset.get("viewer_state", {}),
        )

    def _extract_xyz_from_orca_out(self, out_path: str) -> str | None:
        try:
            if not out_path or not os.path.isfile(out_path):
                return None
            with open(out_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            idxs = [i for i, ln in enumerate(lines) if "CARTESIAN COORDINATES (ANGSTROEM)" in ln]
            if not idxs:
                return None
            i0 = idxs[-1] + 2
            atoms = []
            for j in range(i0, len(lines)):
                ln = lines[j].strip()
                if not ln or ln.startswith("-"):
                    if atoms:
                        break
                    continue
                parts = ln.split()
                if len(parts) < 4:
                    continue
                sym = parts[0]
                try:
                    x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                except Exception:
                    continue
                atoms.append((sym, x, y, z))
            if not atoms:
                return None
            fd, xyz_path = tempfile.mkstemp(prefix="pes_orca_", suffix=".xyz", dir=self.app_dir)
            os.close(fd)
            with open(xyz_path, "w", encoding="utf-8") as f:
                f.write(f"{len(atoms)}\nExtracted from {os.path.basename(out_path)}\n")
                for sym, x, y, z in atoms:
                    f.write(f"{sym:2s}  {x: .8f}  {y: .8f}  {z: .8f}\n")
            return xyz_path
        except Exception:
            return None

    @staticmethod
    def _parse_num_or_fraction(raw: str, default: float = 1.0) -> float:
        t = str(raw or "").strip()
        if not t:
            return default
        try:
            return float(t)
        except Exception:
            pass
        try:
            return float(Fraction(t))
        except Exception:
            return default

    def _species_dialog(self, row=None):
        top = tk.Toplevel(self.main_frame.winfo_toplevel())
        top.title("Edit species" if row else "Add species / TS")
        top.geometry("760x400")
        top.minsize(680, 360)
        body = ttk.Frame(top, padding=10)
        body.pack(fill=tk.BOTH, expand=True)

        d = row or {}
        name_v = tk.StringVar(value=d.get("name", f"S{len(self.species)+1}"))
        kind_v = tk.StringVar(value=d.get("kind", "Intermediate"))
        main_v = tk.StringVar(value=d.get("main_out", ""))
        sp_v = tk.StringVar(value=d.get("sp_out", ""))
        img_v = tk.StringVar(value=d.get("image_path", ""))
        scale_v = tk.StringVar(value=str(d.get("stoich", d.get("scale", 1.0))))
        plot_v = tk.BooleanVar(value=bool(d.get("plot", True)))

        r0 = ttk.Frame(body); r0.pack(fill=tk.X, pady=4)
        ttk.Label(r0, text="Species name:", width=18).pack(side=tk.LEFT)
        ttk.Entry(r0, textvariable=name_v).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(r0, text="Type:", width=8).pack(side=tk.LEFT, padx=(10, 2))
        ttk.Combobox(r0, textvariable=kind_v, state="readonly", values=["Intermediate", "TS", "Normal"], width=14).pack(side=tk.LEFT)

        r1 = ttk.Frame(body); r1.pack(fill=tk.X, pady=4)
        ttk.Label(r1, text="Main ORCA .out:", width=18).pack(side=tk.LEFT)
        ttk.Entry(r1, textvariable=main_v).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(r1, text="Browse", command=lambda: self._pick_out(main_v)).pack(side=tk.LEFT)

        r2 = ttk.Frame(body); r2.pack(fill=tk.X, pady=4)
        ttk.Label(r2, text="Single-point .out:", width=18).pack(side=tk.LEFT)
        ttk.Entry(r2, textvariable=sp_v).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(r2, text="Browse", command=lambda: self._pick_out(sp_v)).pack(side=tk.LEFT)

        r2b = ttk.Frame(body); r2b.pack(fill=tk.X, pady=4)
        ttk.Label(r2b, text="Image file:", width=18).pack(side=tk.LEFT)
        ttk.Entry(r2b, textvariable=img_v).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(r2b, text="Browse", command=lambda: self._pick_image(img_v)).pack(side=tk.LEFT)

        r3 = ttk.Frame(body); r3.pack(fill=tk.X, pady=4)
        ttk.Label(r3, text="Stoichiometry:", width=18).pack(side=tk.LEFT)
        ttk.Entry(r3, textvariable=scale_v, width=12).pack(side=tk.LEFT)
        ttk.Button(r3, text="×2", command=lambda: scale_v.set(str((float(scale_v.get() or 1.0)) * 2))).pack(side=tk.LEFT, padx=(8, 2))
        ttk.Button(r3, text="÷2", command=lambda: scale_v.set(str((float(scale_v.get() or 1.0)) / 2))).pack(side=tk.LEFT, padx=2)
        ttk.Button(r3, text="÷3", command=lambda: scale_v.set(str((float(scale_v.get() or 1.0)) / 3))).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(r3, text="Include in PES plot", variable=plot_v).pack(side=tk.LEFT, padx=(14, 0))

        ttk.Label(
            body,
            text="Correction logic: Gcorr = (SP E if provided else main E) + TC(main), then multiplied by factor.",
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(6, 0))

        bar = ttk.Frame(body); bar.pack(fill=tk.X, pady=(12, 0))
        out = {"row": None}

        def _ok():
            name = name_v.get().strip()
            if not name:
                messagebox.showwarning("PES Plot", "Species name is required.", parent=top)
                return
            main_out = main_v.get().strip()
            if main_out and not os.path.isfile(main_out):
                messagebox.showwarning("PES Plot", "Main ORCA .out file path is invalid.", parent=top)
                return
            sp_out = sp_v.get().strip()
            if sp_out and not os.path.isfile(sp_out):
                messagebox.showwarning("PES Plot", "Single-point file path is invalid.", parent=top)
                return
            scale = self._parse_num_or_fraction(scale_v.get().strip(), default=1.0)
            out["row"] = {
                "name": name,
                "kind": kind_v.get().strip() or "Intermediate",
                "main_out": main_out,
                "sp_out": sp_out,
                "image_path": img_v.get().strip(),
                "stoich": scale,
                "plot": bool(plot_v.get()) and kind_v.get().strip() != "Normal",
                "added_text": d.get("added_text", ""),
                "removed_text": d.get("removed_text", ""),
                "x": d.get("x", 100),
                "y": d.get("y", 120),
            }
            top.destroy()

        ttk.Button(bar, text="Cancel", command=top.destroy).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Save", command=_ok).pack(side=tk.RIGHT, padx=(0, 8))
        top.transient(self.main_frame.winfo_toplevel())
        top.grab_set()
        top.wait_window()
        return out["row"]

    def _add_species_dialog(self):
        row = self._species_dialog(None)
        if row:
            row.setdefault("id", self._alloc_species_id())
            self.species.append(row)
            self._refresh_ref_values()
            self._recompute()

    def _quick_add(self, kind: str):
        self._push_flow_undo()
        base = "I" if kind == "Intermediate" else "TS" if kind == "TS" else "M"
        next_idx = len([x for x in self.species if x.get("kind") == kind]) + 1
        
        # Determine center of current canvas view
        try:
            cx = self.flow_canvas.canvasx(self.flow_canvas.winfo_width() / 2)
            cy = self.flow_canvas.canvasy(self.flow_canvas.winfo_height() / 2)
        except Exception:
            cx, cy = 60 + len(self.species) * 120, 120
            
        new_sp = {
            "id": self._alloc_species_id(),
            "name": f"{base}_{next_idx}",
            "kind": kind,
            "main_out": "",
            "sp_out": "",
            "image_path": "",
            "stoich": 1.0,
            "plot": kind in ("Intermediate", "TS"),
            "added_text": "",
            "removed_text": "",
            "x": cx / getattr(self, "_flow_zoom", 1.0),
            "y": cy / getattr(self, "_flow_zoom", 1.0),
        }
        
        if kind == "Normal":
            row = self._species_dialog(new_sp)
            if row:
                new_sp.update(row)
                self.species.append(new_sp)
            else:
                return
        else:
            self.species.append(new_sp)
            
        self._refresh_ref_values()
        self._recompute()

    def _check_species_violations(self, sp) -> list[str]:
        reasons = []
        if sp.get("normal_term") == "No":
            reasons.append("Not normal termination")
        s2_dev = sp.get("s2_dev")
        if s2_dev is not None:
            try:
                if abs(float(s2_dev)) > 0.5:
                    reasons.append(f"Spin contamination (> 0.5 deviation): {s2_dev}")
            except ValueError: pass
        kind = sp.get("kind", "")
        imag = sp.get("imag_modes")
        if imag is not None:
            try:
                i_val = int(imag)
                if kind == "TS" and i_val != 1:
                    reasons.append(f"TS should have 1 imaginary mode (found {i_val})")
                elif kind != "TS" and i_val > 0:
                    reasons.append(f"Non-TS should have 0 imaginary modes (found {i_val})")
            except ValueError: pass
        return reasons

    def _edit_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("PES Plot", "Select a species row to edit.")
            return
        iid = sel[0]
        if not iid.startswith("sp_"):
            return
        real_idx = int(iid.split("_", 1)[1])
        if real_idx < 0 or real_idx >= len(self.species):
            return
        row = self._species_dialog(self.species[real_idx])
        if row:
            self.species[real_idx].update(row)
            self._refresh_ref_values()
            self._recompute()
            reasons = self._check_species_violations(self.species[real_idx])
            if reasons:
                messagebox.showwarning("Validation Warning", f"Violations found for {self.species[real_idx].get('name', '')}:\n" + "\n".join(reasons))

    def _remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if not iid.startswith("sp_"):
            return
        if not messagebox.askyesno("Confirm deletion", "Are you sure you want to delete the selected item?"):
            return
        idx = int(iid.split("_", 1)[1])
        if 0 <= idx < len(self.species):
            del self.species[idx]
        self._refresh_ref_values()
        self._recompute()

    def _refresh_ref_values(self):
        vals = [sp["name"] for sp in self.species if sp.get("kind", "").strip().lower() == "intermediate" and str(sp.get("name", "")).strip()]
        self.ref_cb.configure(values=vals)
        if vals and self._ref_name.get() not in vals:
            self._ref_name.set(vals[0])
        if not vals:
            self._ref_name.set("")

    def _extract_species_energy(self, sp):
        main_out = (sp.get("main_out") or "").strip()
        if not main_out or not os.path.isfile(main_out):
            return {
                "main_model": "",
                "sp_model": "",
                "main_e": sp.get("main_e"),
                "enthalpy": sp.get("enthalpy"),
                "gibbs": sp.get("gibbs"),
                "thermal": sp.get("thermal"),
                "sp_e": sp.get("sp_e"),
                "s2": sp.get("s2"),
                "s2_dev": sp.get("s2_dev"),
                "e_corr": sp.get("e_corr"),
                "g_corr": sp.get("g_corr"),
                "h_corr": sp.get("h_corr"),
                "normal_term": sp.get("normal_term"),
                "imag_modes": sp.get("imag_modes"),
            }
        info = self._parse_out_thermo(main_out)
        main_e = info.get("final_energy")
        gibbs = info.get("gibbs_energy")
        enthalpy = info.get("total_enthalpy")
        thermal = info.get("thermal_correction")
        s2 = info.get("s2_expectation")
        s2_dev = info.get("s2_deviation")
        imag_modes = info.get("imaginary_modes", 0)
        main_model = self._extract_model_from_out(main_out)
        main_term = info.get("normal_termination", False)

        if thermal is None and gibbs is not None and main_e is not None:
            thermal = gibbs - main_e

        sp_e = None
        sp_model = ""
        sp_term = True
        if sp.get("sp_out"):
            try:
                info_sp = self._parse_out_thermo(sp["sp_out"])
                sp_e = info_sp.get("final_energy")
                sp_model = self._extract_model_from_out(sp["sp_out"])
                sp_term = info_sp.get("normal_termination", False)
                if "s2_expectation" in info_sp and info_sp["s2_expectation"] is not None:
                    s2 = info_sp["s2_expectation"]
                    s2_dev = info_sp["s2_deviation"]
            except Exception:
                sp_e = None
                sp_model = ""
                sp_term = True

        base_e = sp_e if sp_e is not None else main_e
        e_corr = base_e
        g_corr = None
        if base_e is not None and thermal is not None:
            g_corr = base_e + thermal
        elif gibbs is not None:
            g_corr = gibbs
        h_corr = None
        if enthalpy is not None and main_e is not None:
            h_thermal = enthalpy - main_e
            h_base = sp_e if sp_e is not None else main_e
            h_corr = h_base + h_thermal
        elif enthalpy is not None:
            h_corr = enthalpy
        # Apply species stoichiometry only to plotted species (TS/Intermediates)
        # at G(total) stage, as requested.
        if g_corr is not None and sp.get("plot", False):
            try:
                stoich = float(sp.get("stoich", sp.get("scale", 1.0)) or 1.0)
            except Exception:
                stoich = 1.0
            g_corr = g_corr * stoich
        if e_corr is not None and sp.get("plot", False):
            try:
                stoich = float(sp.get("stoich", sp.get("scale", 1.0)) or 1.0)
            except Exception:
                stoich = 1.0
            e_corr = e_corr * stoich
        if h_corr is not None and sp.get("plot", False):
            try:
                stoich = float(sp.get("stoich", sp.get("scale", 1.0)) or 1.0)
            except Exception:
                stoich = 1.0
            h_corr = h_corr * stoich

        ans = {
            "main_model": main_model,
            "sp_model": sp_model,
            "main_e": main_e,
            "enthalpy": enthalpy,
            "gibbs": gibbs,
            "thermal": thermal,
            "sp_e": sp_e,
            "s2": s2,
            "s2_dev": s2_dev,
            "e_corr": e_corr,
            "g_corr": g_corr,
            "h_corr": h_corr,
            "normal_term": "Yes" if (main_term and sp_term) else "No",
            "imag_modes": imag_modes,
        }
        for k, is_override in sp.get("_overrides", {}).items():
            if is_override and k in ans:
                ans[k] = sp.get(k)
        return ans

    def _recompute(self):
        self._ensure_species_ids()
        self.species = [sp for sp in self.species if sp.get("kind") != "Normal"] + [sp for sp in self.species if sp.get("kind") == "Normal"]
        self._prune_edge_links()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        if not self.species:
            self._draw_flow()
            return

        computed = []
        for sp in self.species:
            try:
                vals = self._extract_species_energy(sp)
            except Exception as e:
                messagebox.showwarning("PES Plot", f"Failed to parse {sp['name']}:\n{e}")
                vals = {
                    "main_model": "", "sp_model": "", "main_e": None, "enthalpy": None, "gibbs": None,
                    "thermal": None, "sp_e": None, "s2": None, "s2_dev": None, "e_corr": None, "g_corr": None, "h_corr": None,
                }
            row = {**sp, **vals}
            computed.append(row)
        self.species = self._apply_external_overrides(computed)

        for sp in self.species:
            sp["rel_e_kcal"] = None
            sp["rel_kcal"] = None
            sp["rel_h_kcal"] = None
            sp["e_step_corr"] = sp.get("e_corr")
            sp["g_step_corr"] = sp.get("g_corr")
            sp["h_step_corr"] = sp.get("h_corr")

        # DAG traversal for computing energies
        plotted = [s for s in self.species if s.get("plot", True)]
        normal_lookup = {s.get("id"): s for s in self.species if not s.get("plot", True)}
        
        adj_fwd = {s["id"]: [] for s in plotted}
        adj_bck = {s["id"]: [] for s in plotted}
        
        for e_key, data in self.edge_links.items():
            try:
                src, dst = map(int, str(e_key).split("_"))
                if src in adj_fwd and dst in adj_fwd:
                    add_g = sum((normal_lookup.get(sid, {}).get("g_corr") or 0.0) * c for sid, c in data.get("add", {}).items())
                    rem_g = sum((normal_lookup.get(sid, {}).get("g_corr") or 0.0) * c for sid, c in data.get("remove", {}).items())
                    add_e = sum((normal_lookup.get(sid, {}).get("e_corr") or 0.0) * c for sid, c in data.get("add", {}).items())
                    rem_e = sum((normal_lookup.get(sid, {}).get("e_corr") or 0.0) * c for sid, c in data.get("remove", {}).items())
                    add_h = sum((normal_lookup.get(sid, {}).get("h_corr") or 0.0) * c for sid, c in data.get("add", {}).items())
                    rem_h = sum((normal_lookup.get(sid, {}).get("h_corr") or 0.0) * c for sid, c in data.get("remove", {}).items())
                    delta_g = rem_g - add_g
                    delta_e = rem_e - add_e
                    delta_h = rem_h - add_h
                    adj_fwd[src].append((dst, delta_g, delta_e, delta_h))
                    adj_bck[dst].append((src, delta_g, delta_e, delta_h))
            except: pass
            
        ref_name = self._ref_name.get().strip()
        ref_sp = next((s for s in plotted if s.get("name") == ref_name), plotted[0] if plotted else None)
        
        if ref_sp and ref_sp.get("g_corr") is not None:
            visited = set()
            queue = [ref_sp["id"]]
            base_shifts = {ref_sp["id"]: 0.0}
            
            while queue:
                curr_id = queue.pop(0)
                if curr_id in visited: continue
                visited.add(curr_id)
                c_shift = base_shifts.get(curr_id, 0.0)
                
                for nxt, delta_g, delta_e, delta_h in adj_fwd[curr_id]:
                    if nxt not in visited:
                        ns = next((s for s in plotted if s["id"] == nxt), None)
                        if ns and ns.get("g_corr") is not None:
                            base_shifts[nxt] = c_shift + delta_g
                            queue.append(nxt)
                for prv, delta_g, delta_e, delta_h in adj_bck[curr_id]:
                    if prv not in visited:
                        ps = next((s for s in plotted if s["id"] == prv), None)
                        if ps and ps.get("g_corr") is not None:
                            base_shifts[prv] = c_shift - delta_g
                            queue.append(prv)

            for s in plotted:
                if s["id"] in base_shifts and s.get("g_corr") is not None:
                    s["g_step_corr"] = s["g_corr"] + base_shifts[s["id"]]
                else:
                    s["g_step_corr"] = None

        if ref_sp and ref_sp.get("e_corr") is not None:
            visited_e = set()
            queue_e = [ref_sp["id"]]
            base_shifts_e = {ref_sp["id"]: 0.0}

            while queue_e:
                curr_id = queue_e.pop(0)
                if curr_id in visited_e:
                    continue
                visited_e.add(curr_id)
                c_shift_e = base_shifts_e.get(curr_id, 0.0)
                for nxt, delta_g, delta_e, delta_h in adj_fwd[curr_id]:
                    if nxt not in visited_e:
                        ns = next((s for s in plotted if s["id"] == nxt), None)
                        if ns and ns.get("e_corr") is not None:
                            base_shifts_e[nxt] = c_shift_e + delta_e
                            queue_e.append(nxt)
                for prv, delta_g, delta_e, delta_h in adj_bck[curr_id]:
                    if prv not in visited_e:
                        ps = next((s for s in plotted if s["id"] == prv), None)
                        if ps and ps.get("e_corr") is not None:
                            base_shifts_e[prv] = c_shift_e - delta_e
                            queue_e.append(prv)
            for s in plotted:
                if s["id"] in base_shifts_e and s.get("e_corr") is not None:
                    s["e_step_corr"] = s["e_corr"] + base_shifts_e[s["id"]]
                else:
                    s["e_step_corr"] = None

        if ref_sp and ref_sp.get("h_corr") is not None:
            visited_h = set()
            queue_h = [ref_sp["id"]]
            base_shifts_h = {ref_sp["id"]: 0.0}

            while queue_h:
                curr_id = queue_h.pop(0)
                if curr_id in visited_h:
                    continue
                visited_h.add(curr_id)
                c_shift_h = base_shifts_h.get(curr_id, 0.0)
                for nxt, delta_g, delta_e, delta_h in adj_fwd[curr_id]:
                    if nxt not in visited_h:
                        ns = next((s for s in plotted if s["id"] == nxt), None)
                        if ns and ns.get("h_corr") is not None:
                            base_shifts_h[nxt] = c_shift_h + delta_h
                            queue_h.append(nxt)
                for prv, delta_g, delta_e, delta_h in adj_bck[curr_id]:
                    if prv not in visited_h:
                        ps = next((s for s in plotted if s["id"] == prv), None)
                        if ps and ps.get("h_corr") is not None:
                            base_shifts_h[prv] = c_shift_h - delta_h
                            queue_h.append(prv)
            for s in plotted:
                if s["id"] in base_shifts_h and s.get("h_corr") is not None:
                    s["h_step_corr"] = s["h_corr"] + base_shifts_h[s["id"]]
                else:
                    s["h_step_corr"] = None

        ref_e = ref_sp.get("e_step_corr") if ref_sp else None
        ref_g = ref_sp.get("g_step_corr") if ref_sp else None
        ref_h = ref_sp.get("h_step_corr") if ref_sp else None
        
        # Determine conversion factor based on selected unit
        unit = self._energy_unit.get()
        if unit == "eV": factor = EH_TO_EV
        elif unit == "kJ/mol": factor = EH_TO_KJ
        elif unit == "Hartree": factor = 1.0
        else: factor = EH_TO_KCAL

        if ref_e is not None:
            for s in plotted:
                e = s.get("e_step_corr")
                if e is not None:
                    s["rel_e_energy"] = (e - ref_e) * factor
                else:
                    s["rel_e_energy"] = None
        else:
            for s in plotted:
                s["rel_e_energy"] = None

        if ref_g is not None:
            for s in plotted:
                g = s.get("g_step_corr")
                if g is not None:
                    s["rel_energy"] = (g - ref_g) * factor
                else:
                    s["rel_energy"] = None
        else:
            for s in plotted:
                s["rel_energy"] = None

        if ref_h is not None:
            for s in plotted:
                h = s.get("h_step_corr")
                if h is not None:
                    s["rel_h_energy"] = (h - ref_h) * factor
                else:
                    s["rel_h_energy"] = None
        else:
            for s in plotted:
                s["rel_h_energy"] = None

        self.tree.heading("rel_e_kcal", text=f"Rel E ({unit})")
        self.tree.heading("rel_kcal", text=f"Rel G ({unit})")
        self.tree.heading("rel_h_kcal", text=f"Rel H ({unit})")

        plotted = [(i, x) for i, x in enumerate(self.species) if x.get("kind") != "Normal"]
        normal = [(i, x) for i, x in enumerate(self.species) if x.get("kind") == "Normal"]
        ordered = plotted[:]
        if plotted and normal:
            ordered.append((None, {"_separator": True}))
        ordered.extend(normal)

        for idx, row in ordered:
            if row.get("_separator"):
                self.tree.insert("", tk.END, iid="sep", text="---- NORMAL MOLECULES ----", values=("---- NORMAL MOLECULES ----", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""), tags=("separator",))
                continue
                
            tgs = ()
            s2_dev = row.get("s2_dev")
            has_error = False
            s2_dev_display = self._fmt(s2_dev)
            
            normal_term_display = str(row.get("normal_term", ""))
            if row.get("normal_term") == "No":
                has_error = True
                normal_term_display += " ⚠️"
                
            if s2_dev is not None:
                try:
                    if abs(float(s2_dev)) > 0.5:
                        has_error = True
                        s2_dev_display += " ⚠️ (Contam >0.5)"
                except ValueError: pass

            kind = row.get("kind", "")
            imag = row.get("imag_modes")
            imag_display = str(imag) if imag is not None else ""
            if imag is not None:
                try:
                    i_val = int(imag)
                    if kind == "TS" and i_val != 1:
                        has_error = True
                        imag_display += " ⚠️"
                    elif kind != "TS" and i_val > 0:
                        has_error = True
                        imag_display += " ⚠️"
                except ValueError: pass

            if has_error:
                tgs = ("error_row",)
                
            display_name = row["name"]
            if row.get("main_out") or row.get("sp_out") or row.get("image_path"):
                display_name += "  ⓘ"

            self.tree.insert(
                "",
                tk.END,
                iid=f"sp_{idx}",
                text=display_name,
                values=(
                    display_name,
                    row["kind"],
                    "Yes" if row.get("plot", True) else "No",
                    row.get("main_out", ""),
                    row.get("sp_out", ""),
                    row.get("image_path", ""),
                    self._fmt(row.get("stoich", 1.0), 3),
                    normal_term_display,
                    imag_display,
                    self._fmt(row["main_e"]),
                    self._fmt(row["enthalpy"]),
                    self._fmt(row["gibbs"]),
                    self._fmt(row["thermal"]),
                    self._fmt(row["sp_e"]),
                    self._fmt(row.get("s2")),
                    s2_dev_display,
                    self._fmt(row.get("e_corr")),
                    self._fmt(row["g_corr"]),
                    self._fmt(row.get("h_corr")),
                    self._fmt(row.get("rel_e_energy")),
                    self._fmt(row.get("rel_energy")),
                    self._fmt(row.get("rel_h_energy")),
                ),
                tags=tgs
            )

        self._schedule_draw_flow()

    def _schedule_draw_flow(self, event=None):
        if getattr(self, "_draw_flow_after_id", None):
            try:
                self.frame.after_cancel(self._draw_flow_after_id)
            except Exception:
                pass
        if getattr(self, "frame", None):
            self._draw_flow_after_id = self.frame.after(10, self._draw_flow)
        else:
            self._draw_flow()

    def _draw_flow(self):
        c = self.flow_canvas
        c.delete("all")
        self._flow_overlay_tkimgs = {}
        self._flow_handle_hit = []
        win_width = max(100, c.winfo_width())
        height = max(120, c.winfo_height())
        
        plotted = [(i, sp) for i, sp in enumerate(self.species) if self._species_on_flow_canvas(sp)]
        id_to_name = {sp.get("id"): _sanitize_math_text(sp.get("name", "")) for sp in self.species}
        
        z = getattr(self, "_flow_zoom", 1.0)
        
        if not plotted:
            self._flow_handle_hit = []
            self._draw_dotted_background(c, win_width, height, False)
            c.configure(bg="#ffffff")
            c.create_text(
                12, 28, anchor="w",
                text="Click + Add species/TS to start reaction flow.",
                font=("Segoe UI", max(9, int(13 * z))),
                fill="#64748b",
            )
            return
            
        zt_all = self._flow_text_zoom(z)
        font_plus = ("Segoe UI", max(8, int(18 * z)), "bold")
        font_minus = ("Segoe UI", max(9, int(20 * z)), "bold")
        font_edge = ("Segoe UI", max(7, int(9 * zt_all)), "bold")
        show_split_energy = bool(self._show_flow_rel_g.get())

        arrow_width = max(1.0, 2.6 * z)
        circ_r = max(4, int(13 * z))
        circ_width = max(1.0, 1.7 * z)
        circ_dy = max(6, int(24 * z))
        text_dy = max(20, int(86 * z))
        
        min_x, max_x = 0, win_width
        min_y, max_y = 0, height

        # Find bounds mapping
        for _, sp in plotted:
            sx = sp.get("x", 50) * z
            sy = sp.get("y", 100) * z
            dyn_w, box_h_i = self._flow_box_dims(sp, z)
            max_x = max(max_x, sx + dyn_w + 100)
            max_y = max(max_y, sy + box_h_i + 100)
            min_x = min(min_x, sx - 100)
            min_y = min(min_y, sy - 100)

        draw_width = max_x - min(0, min_x)
        draw_height = max_y - min(0, min_y)
        
        # Apply dark mode background (light canvas is cooler gray so white cards read clearly)
        is_dark = getattr(self, "_dark_mode", None) and self._dark_mode.get()
        c.configure(bg="#0f1419" if is_dark else "#ffffff")
        
        edge_stroke = "#e2e8f0" if is_dark else "#1e293b"
        z_vis = max(float(z), _FLOW_ZOOM_EPS)
        _ew_base = max(1.0, min(1.85, 0.88 * z_vis + 0.35))
        if z_vis < 0.24:
            _ew_base = max(_ew_base, 1.05 + 1.35 * math.sqrt(z_vis))
        edge_width = 2.0 * _ew_base
        use_straight_edges = str(self._flow_edge_style.get() or "curve").strip().lower() == "straight"
        
        self._draw_dotted_background(c, draw_width, draw_height, is_dark)
        
        # Draw edges from self.edge_links
        node_rects = {}
        for i, (real_idx, sp) in enumerate(plotted):
            sid = sp.get("id")
            px = sp.get("x", 50) * z
            py = sp.get("y", 100) * z
            sp_w, bh_i = self._flow_box_dims(sp, z)
            node_rects[sid] = (px, py, px + sp_w, py + bh_i)

        id_to_sp = {int(s.get("id")): s for s in self.species if s.get("id") is not None}

        for edge_key, data in list(self.edge_links.items()):
            try:
                src_id, dst_id = map(int, str(edge_key).split("_"))
                if src_id in node_rects and dst_id in node_rects:
                    sx1, sy1, sx2, sy2 = node_rects[src_id]
                    dx1, dy1, dx2, dy2 = node_rects[dst_id]
                    
                    edge_tag = f"flow_edge_{edge_key}"
                    poly, px, py = self._flow_connection_polyline(
                        sx1, sy1, sx2, sy2, dx1, dy1, dx2, dy2, use_straight_edges,
                    )
                    s_sp = id_to_sp.get(src_id)
                    d_sp = id_to_sp.get(dst_id)
                    edge_faded = self._species_flow_faded(s_sp) or self._species_flow_faded(d_sp)
                    estroke = (
                        "#64748b"
                        if edge_faded and not is_dark
                        else ("#57534e" if edge_faded and is_dark else edge_stroke)
                    )
                    self._flow_canvas_edge_draw(
                        c, poly, edge_tag, use_straight_edges, edge_width, estroke, z_vis, is_dark, faded=edge_faded,
                    )
                    
                    hit_w = max(8.5, min(52.0, 12.0 + 148.0 * math.sqrt(z_vis)))
                    c.create_line(
                        sx2 if dx1 > sx2 else sx1, (sy1 + sy2) / 2, dx1 if dx1 > sx2 else dx2, (dy1 + dy2) / 2,
                        width=hit_w, fill="", tags=(edge_tag,),
                    )
                    
                    add_terms = data.get("add", {})
                    rem_terms = data.get("remove", {})
                    add_names = [f"{coeff:g} {id_to_name[sid]}" if abs(coeff - 1.0) > 1e-12 else f"{id_to_name[sid]}" for sid, coeff in add_terms.items() if sid in id_to_name]
                    rem_names = [f"{coeff:g} {id_to_name[sid]}" if abs(coeff - 1.0) > 1e-12 else f"{id_to_name[sid]}" for sid, coeff in rem_terms.items() if sid in id_to_name]
                    
                    nxt = next((s for s in self.species if s.get("id") == dst_id), {})
                    add_text = nxt.get("added_text") or self._format_edge_species_block(add_names)
                    rem_text = nxt.get("removed_text") or self._format_edge_species_block(rem_names)

                    if not hasattr(self, "_bubble_states"):
                        self._bubble_states = {}
                    is_expanded = self._bubble_states.get(edge_key, False)

                    if is_expanded:
                        has_mo = bool(add_text or rem_text)
                        lines = len(add_text.split('\n')) if add_text else 0
                        lines += len(rem_text.split('\n')) if rem_text else 0
                        
                        card_w = max(160, int(170*z))
                        card_h = max(110, int(100*z)) + lines * max(14, int(16*z))
                        
                        # Background card
                        self._draw_rounded_rect(c, px - card_w/2, py - card_h/2, px + card_w/2, py + card_h/2, r=max(6, int(8*z)), fill="#ffffff", outline="#94a3b8", width=max(1.0, 1.5*z), tags=(f"expand_{edge_key}", "expanded_popup"))
                        
                        # Hide (Close) button at top right
                        hx, hy = px + card_w/2 - 14*z, py - card_h/2 + 14*z
                        c.create_oval(hx-10*z, hy-10*z, hx+10*z, hy+10*z, fill="#f1f5f9", outline="#cbd5e1", tags=(f"hide_{edge_key}", "expanded_popup"))
                        c.create_text(hx, hy, text="✖", font=("Segoe UI", max(7, int(8*z))), fill="#475569", tags=(f"hide_{edge_key}", "expanded_popup"))
                        
                        # Clear all button (Trash icon) at top left (opposite side)
                        tx, ty = px - card_w/2 + 14*z, py - card_h/2 + 14*z
                        c.create_oval(tx-10*z, ty-10*z, tx+10*z, ty+10*z, fill="#fee2e2", outline="#fecaca", tags=(f"clear_{edge_key}", "expanded_popup"))
                        c.create_text(tx, ty, text="🗑", font=("Segoe UI", max(8, int(10*z))), fill="#b91c1c", tags=(f"clear_{edge_key}", "expanded_popup"))
                        
                        # Add Normal section (Top part of card)
                        ay = py - card_h/2 + max(34, int(38*z))
                        c.create_rectangle(px - card_w/2 + 12*z, ay - 12*z, px - card_w/2 + 32*z, ay + 8*z, fill="#d1fae5", outline="#34d399", tags=(f"add_{edge_key}", "expanded_popup"))
                        c.create_text(px - card_w/2 + 22*z, ay - 2*z, text="+", font=("Segoe UI", max(12, int(14*z)), "bold"), fill="#059669", tags=(f"add_{edge_key}", "expanded_popup"))
                        c.create_text(px - card_w/2 + 38*z, ay - 2*z, text="Add Molecules", font=("Segoe UI", max(9, int(10*z)), "bold"), fill="#059669", anchor="w", tags=(f"add_{edge_key}", "expanded_popup"))
                        
                        curr_y = ay + max(16, int(20 * z))
                        if add_text:
                            for line in add_text.split('\n'):
                                c.create_text(px - card_w/2 + 16*z, curr_y, text=line, font=("Segoe UI", max(9, int(10*z)), "bold"), fill="#1e293b", anchor="w", tags=(f"expand_{edge_key}", "expanded_popup"))
                                curr_y += max(14, int(16 * z))
                        else:
                            curr_y += max(8, int(10 * z))
                                
                        # Remove Normal section (Middle/Bottom part of card)
                        ry = curr_y + max(20, int(24 * z))
                        c.create_rectangle(px - card_w/2 + 12*z, ry - 12*z, px - card_w/2 + 32*z, ry + 8*z, fill="#ffe4e6", outline="#fb7185", tags=(f"rem_{edge_key}", "expanded_popup"))
                        c.create_text(px - card_w/2 + 22*z, ry - 2*z, text="-", font=("Segoe UI", max(13, int(15*z)), "bold"), fill="#e11d48", tags=(f"rem_{edge_key}", "expanded_popup"))
                        c.create_text(px - card_w/2 + 38*z, ry - 2*z, text="Remove Molecules", font=("Segoe UI", max(9, int(10*z)), "bold"), fill="#e11d48", anchor="w", tags=(f"rem_{edge_key}", "expanded_popup"))
                        
                        curr_y = ry + max(16, int(20 * z))
                        if rem_text:
                            for line in rem_text.split('\n'):
                                c.create_text(px - card_w/2 + 16*z, curr_y, text=line, font=("Segoe UI", max(9, int(10*z)), "bold"), fill="#1e293b", anchor="w", tags=(f"expand_{edge_key}", "expanded_popup"))
                                curr_y += max(14, int(16 * z))
                                
                    else:
                        # Original circular badging without text clutter, colorized if MOs exist
                        b_r = max(6, int(9*z))
                        off_y = max(8, int(10*z))
                        
                        # Plus Badge
                        bg_add = "#10b981" if add_text else "#ffffff"
                        fg_add = "#ffffff" if add_text else "#10b981"
                        
                        c.create_oval(px - b_r, py - off_y - b_r, px + b_r, py - off_y + b_r, fill=bg_add, outline="#a7f3d0", width=max(1.0, 1.5*z), tags=(f"expand_{edge_key}",))
                        c.create_text(px, py - off_y - max(1, 1*z), text="+", font=("Segoe UI", max(8, int(12*z)), "bold"), fill=fg_add, tags=(f"expand_{edge_key}",))
                        
                        # Minus Badge
                        bg_rem = "#f43f5e" if rem_text else "#ffffff"
                        fg_rem = "#ffffff" if rem_text else "#f43f5e"
                        
                        c.create_oval(px - b_r, py + off_y - b_r, px + b_r, py + off_y + b_r, fill=bg_rem, outline="#fecdd3", width=max(1.0, 1.5*z), tags=(f"expand_{edge_key}",))
                        c.create_text(px, py + off_y - max(1, 1*z), text="-", font=("Segoe UI", max(10, int(13*z)), "bold"), fill=fg_rem, tags=(f"expand_{edge_key}",))
            except: pass

        # Draw drag line if active
        if getattr(self, "_drag_edge", None):
            hx, hy, mx, my = self._drag_edge
            _dw = max(7, int(round(9 * z)))
            _dh = max(9, int(round(11 * z)))
            _dr = max(3, int(round(3 * z)))
            c.create_line(
                hx, hy, mx, my,
                arrow=tk.LAST,
                dash=(5, 5),
                width=max(1.5, 2.4 * z),
                fill="#6366f1",
                capstyle=tk.ROUND,
                arrowshape=(_dw, _dh, _dr),
            )

        self._flow_boxes = []
        for i, (real_idx, sp) in enumerate(plotted):
            tag = f"sp_{sp['id']}"
            is_ts = sp.get("kind", "").upper().startswith("TS")
            x = float(sp.get("x", 50) * z)
            y = float(sp.get("y", 100) * z)
            bs_i = self._flow_effective_box_scale(sp)
            sp_w, box_h = self._flow_box_dims(sp, z, bs_i)
            rg0 = sp.get("rel_energy")
            ev_fit = self._flow_rel_energy_display(rg0)
            fn_fit, fe_fit, _ = self._flow_capsule_fit_fonts(
                sp, box_h, sp_w, z, bs_i, show_split_energy, ev_fit
            )
            font_flow_name_single = ("Segoe UI", fn_fit, "bold")
            font_flow_name_split = ("Segoe UI", fn_fit, "bold")
            pt_ev_split = 0
            if show_split_energy:
                pt_ev_split = max(5, int(fe_fit or 10))
                font_flow_e_val = ("Segoe UI", pt_ev_split, "bold")
            xf2 = x + sp_w
            yf2 = y + box_h
            cx = x + sp_w / 2
            cy_mid = y + box_h / 2
            faded = self._species_flow_faded(sp)

            if is_dark:
                outline_col = "#cbd5e1"
                divider_col = "#94a3b8"
                top_normal = "#475569"
                top_ts = "#78350f"
                bot_fill = "#1e293b"
                single_normal = "#475569"
                single_ts = "#78350f"
                txt_main = "#f8fafc"
                txt_energy = "#f1f5f9"
            else:
                outline_col = "#1e293b"
                divider_col = "#0f172a"
                top_normal = "#dce7f5"
                top_ts = "#fde8dc"
                bot_fill = "#ffffff"
                single_normal = "#dce7f5"
                single_ts = "#fde8dc"
                txt_main = "#0f172a"
                txt_energy = "#0f172a"

            if faded:
                if is_dark:
                    outline_col, divider_col = "#64748b", "#78716c"
                    top_normal, top_ts = "#3f4f5f", "#5c4033"
                    bot_fill = "#1c2433"
                    single_normal, single_ts = "#3f4f5f", "#5c4033"
                    txt_main = "#a8b0bd"
                    txt_energy = "#a8b0bd"
                else:
                    outline_col, divider_col = "#9ca3af", "#94a3b8"
                    top_normal, top_ts = "#e8ecf4", "#f3ece8"
                    bot_fill = "#f8fafc"
                    single_normal, single_ts = "#e8ecf4", "#f3ece8"
                    txt_main = "#64748b"
                    txt_energy = "#64748b"

            pastel_top = top_ts if is_ts else top_normal
            pastel_single = single_ts if is_ts else single_normal
            r_corner = max(4.0, 8.0 * z)
            rect_pts = self._flow_true_rounded_rect_coords(x, y, xf2, yf2, r_corner)
            ow = max(1.4, 1.32 * z * bs_i + 0.50)
            if faded:
                ow = max(1.0, ow * 0.9)

            self._draw_flow_stadium_drop_shadow(c, x, y, xf2, yf2, r=r_corner, tags=(), is_dark=is_dark)

            # Draw the single rounded rect
            c.create_polygon(*rect_pts, fill=pastel_single, outline="", smooth=True, splinesteps=36, tags=(tag,))

            c.create_polygon(
                *rect_pts,
                fill="",
                outline=outline_col,
                width=ow,
                smooth=True,
                splinesteps=36,
                tags=(tag, f"{tag}_outline"),
            )

            # Name is ALWAYS centered inside the rect
            c.create_text(cx, cy_mid, text=sp.get("plot_name_override") or sp["name"], fill=txt_main, font=font_flow_name_single, tags=(tag,))

            if show_split_energy:
                y_e_val = yf2 + max(10.0, 14.0 * z * bs_i)
                c.create_text(cx, y_e_val, text=ev_fit, fill=txt_energy, font=font_flow_e_val, tags=(tag,))

            if f"sp:{sp['id']}" in self._flow_multi_selected:
                sel_pad = max(4, int(5 * z * bs_i))
                sx0, sy0 = x - sel_pad, y - sel_pad
                sx1, sy1 = xf2 + sel_pad, yf2 + sel_pad
                sel_pts = self._flow_true_rounded_rect_coords(float(sx0), float(sy0), float(sx1), float(sy1), r_corner + sel_pad)
                c.create_polygon(
                    *sel_pts,
                    outline="#818cf8",
                    width=max(1.5, int(2 * z)),
                    dash=(6, 4),
                    fill="",
                    smooth=True,
                    splinesteps=36,
                    tags=(tag,),
                )
            self._flow_boxes.append((x, y, x + sp_w, y + box_h, i))

            # Connector-handle geometry (always registered for reliable clicks; visuals only on hover)
            # Small visible ports; hit zone only slightly larger (large radii steal node drags)
            zn = max(float(z), _FLOW_ZOOM_EPS)
            vis_r = max(4.0, min(26.0, max(5.5 * zn, 5.8 + 5.8 * math.sqrt(zn))))
            hit_r = max(vis_r + 2.0, min(32.0, max(8.5 * zn, 8.8 + 6.9 * math.sqrt(zn))))
            mx, my = x + sp_w / 2, y + box_h / 2
            handle_pts = [
                (mx, y, f"handle_{sp['id']}"),
                (mx, y + box_h, f"handle_{sp['id']}"),
                (x, my, f"handle_{sp['id']}"),
                (x + sp_w, my, f"handle_{sp['id']}"),
            ]
            for hx, hy, _ht in handle_pts:
                self._flow_handle_hit.append((int(sp["id"]), hx, hy, float(hit_r)))

            if getattr(self, "_hovered_node", None) == sp["id"]:
                for hx, hy, htag in handle_pts:
                    c.create_oval(
                        hx - hit_r, hy - hit_r, hx + hit_r, hy + hit_r,
                        fill="", outline="", width=0, tags=("flow_handle", "flow_handle_hit"),
                    )
                    c.create_oval(
                        hx - vis_r, hy - vis_r, hx + vis_r, hy + vis_r,
                        fill="#f87171", outline="#b91c1c", width=max(1, int(1.25 * z)),
                        tags=(htag, "flow_handle"),
                    )
                    c.create_line(hx - vis_r * 0.45, hy, hx + vis_r * 0.45, hy, fill="white", tags=(htag, "flow_handle"))
                    c.create_line(hx, hy - vis_r * 0.45, hx, hy + vis_r * 0.45, fill="white", tags=(htag, "flow_handle"))

        # Raise the expanded popups so they always float ABOVE nodes/connections
        c.tag_raise("expanded_popup")

        # Draw flow text/image overlays
        for i, ov in enumerate(getattr(self, "_flow_overlays", []) or []):
            kind = str(ov.get("kind", "")).lower()
            ox = float(ov.get("x", 40.0)) * z
            oy = float(ov.get("y", 40.0)) * z
            tag = f"flow_ov_{i}"
            if kind == "text":
                txt = str(ov.get("text", "Text"))
                fs = max(8, int(float(ov.get("size", 12.0)) * z))
                tcol = str(ov.get("color", "#111827") or "#111827")
                c.create_text(ox, oy, text=txt, font=("Segoe UI", fs, "bold"), fill=tcol, anchor="nw", tags=(tag, "flow_overlay"))
            elif kind == "image":
                pth = str(ov.get("path", "") or "").strip()
                if not pth or not os.path.isfile(pth):
                    c.create_text(ox, oy, text="[image missing]", font=("Segoe UI", max(8, int(10*z))), fill="#991b1b", anchor="nw", tags=(tag, "flow_overlay"))
                    continue
                try:
                    if str(pth).lower().endswith((".png", ".gif", ".ppm", ".pgm")):
                        img = tk.PhotoImage(file=pth)
                    else:
                        from PIL import Image, ImageTk  # type: ignore
                        pil = Image.open(pth).convert("RGBA")
                        zoom = max(0.08, min(3.0, float(ov.get("size", 1.0))))
                        nw = max(8, int(pil.width * zoom * z))
                        nh = max(8, int(pil.height * zoom * z))
                        pil = pil.resize((nw, nh))
                        img = ImageTk.PhotoImage(pil)
                    self._flow_overlay_tkimgs[i] = img
                    c.create_image(ox, oy, image=img, anchor="nw", tags=(tag, "flow_overlay"))
                except Exception:
                    c.create_text(ox, oy, text="[image error]", font=("Segoe UI", max(8, int(10*z))), fill="#991b1b", anchor="nw", tags=(tag, "flow_overlay"))
            elif kind == "shape":
                shp = str(ov.get("shape", "rectangle")).lower()
                sc = max(0.4, float(ov.get("size", 1.0)))
                bw = max(24, int(64 * z * sc))
                bh = max(18, int(42 * z * sc))
                fill_col = str(ov.get("color", "#bfdbfe") or "#bfdbfe")
                out_col = str(ov.get("outline", "#1d4ed8") or "#1d4ed8")
                if shp == "circle":
                    c.create_oval(ox, oy, ox + bw, oy + bh, fill=fill_col, outline=out_col, width=max(1, int(2 * z)), tags=(tag, "flow_overlay"))
                elif shp == "diamond":
                    pts = [ox + bw * 0.5, oy, ox + bw, oy + bh * 0.5, ox + bw * 0.5, oy + bh, ox, oy + bh * 0.5]
                    c.create_polygon(pts, fill=fill_col, outline=out_col, width=max(1, int(2 * z)), tags=(tag, "flow_overlay"))
                else:
                    self._draw_rounded_rect(c, ox, oy, ox + bw, oy + bh, r=max(8, int(10 * z)), fill=fill_col, outline=out_col, width=max(1, int(2 * z)), tags=(tag, "flow_overlay"))
            if f"ov:{i}" in self._flow_multi_selected:
                x0, y0, x1, y1 = self._flow_overlay_bbox(ov, z)
                c.create_rectangle(x0 - 4, y0 - 4, x1 + 4, y1 + 4, outline="#7c3aed", width=max(1, int(2*z)), dash=(4, 2))

        if self._flow_rsel_rect:
            x0, y0, x1, y1 = self._flow_rsel_rect
            c.create_rectangle(x0, y0, x1, y1, outline="#818cf8", width=max(1, int(1.75 * z)), dash=(6, 4))

        # Draw a floating Add button fixed to the top left of the view
        c_x0 = c.canvasx(16)
        c_y0 = c.canvasy(16)
        ab_w, ab_h = 116, 36
        self._draw_flow_card_drop_shadow(c, c_x0, c_y0, c_x0 + ab_w, c_y0 + ab_h, r=11, tags=(), is_dark=is_dark)
        btn_fill = "#4f46e5" if is_dark else "#6366f1"
        btn_outline = "#4338ca" if is_dark else "#4f46e5"
        self._draw_rounded_rect(c, c_x0, c_y0, c_x0 + ab_w, c_y0 + ab_h, r=11, fill=btn_fill, outline=btn_outline, width=1, tags=("canvas_add_btn",), splinesteps=28)
        c.create_text(c_x0 + ab_w / 2, c_y0 + ab_h / 2 + 0.5, text="+ Add", fill="#fafafa", font=("Segoe UI", 10, "bold"), tags=("canvas_add_btn",))

        try:
            c.tag_raise("flow_handle")
        except tk.TclError:
            pass

        c.configure(scrollregion=(min(0, min_x), min(0, min_y), max(win_width, max_x), max(height, max_y)))

    @staticmethod
    def _draw_dotted_background(canvas, width, height, is_dark=False):
        step = 22
        r = 0.85
        if is_dark:
            dot_color = "#475569"
            for yy in range(12, int(height) + 1, step):
                for xx in range(12, int(width) + 1, step):
                    canvas.create_oval(xx - r, yy - r, xx + r, yy + r, fill=dot_color, outline="")
            return
        dot_color = "#e5e7eb"
        for yy in range(12, int(height) + 1, step):
            for xx in range(12, int(width) + 1, step):
                canvas.create_oval(xx - r, yy - r, xx + r, yy + r, fill=dot_color, outline="")

    @staticmethod
    def _draw_flow_card_drop_shadow(canvas, x, y, x2, y2, r, tags=(), is_dark=False):
        """Layered rounded rects behind nodes for a soft lifted-card look (Tk-friendly)."""
        if is_dark:
            layers = [(4, 5, "#020617"), (2, 3, "#0f172a"), (1, 1, "#1e293b")]
        else:
            layers = [(5, 7, "#e8ecf2"), (3, 4, "#eef1f6"), (1, 2, "#f4f6f9")]
        tw = tags if isinstance(tags, tuple) else (tags,)
        for ox, oy, col in layers:
            pts = PESPlotModule._flow_true_rounded_rect_coords(
                x + ox, y + oy, x2 + ox, y2 + oy, r, segs=18,
            )
            canvas.create_polygon(*pts, fill=col, outline="", smooth=False, tags=tw if tw else ())

    @staticmethod
    def _draw_flow_stadium_drop_shadow(canvas, x1: float, y1: float, x2: float, y2: float, r: float = 8.0, tags=(), is_dark=False):
        """Rounded rectangle shadow matching reaction-flow nodes."""
        if is_dark:
            layers = [(4, 5, "#020617"), (2, 3, "#0f172a"), (1, 1, "#1e293b")]
        else:
            layers = [(5, 7, "#e8ecf2"), (3, 4, "#eef1f6"), (1, 2, "#f4f6f9")]
        tw = tags if isinstance(tags, tuple) else (tags,)
        for ox, oy, col in layers:
            pts = PESPlotModule._flow_true_rounded_rect_coords(x1 + ox, y1 + oy, x2 + ox, y2 + oy, r=r, segs=16)
            canvas.create_polygon(*pts, fill=col, outline="", smooth=True, splinesteps=12, tags=tw if tw else ())

    @staticmethod
    def _draw_rounded_rect(canvas, x1, y1, x2, y2, r=12, **kwargs):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1
        ]
        splinesteps = int(kwargs.pop("splinesteps", 40))
        return canvas.create_polygon(points, smooth=True, splinesteps=splinesteps, **kwargs)

    @staticmethod
    def _format_edge_species_block(names):
        """
        Render species as stacked with plus signs:
        A
        +
        B
        +
        C
        """
        if not names:
            return ""
        out = []
        for i, nm in enumerate(names):
            out.append(PESPlotModule._chem_text(str(nm)))
            if i < len(names) - 1:
                out.append("+")
        return "\n".join(out)

    @staticmethod
    def _chem_text(text: str) -> str:
        """
        Lightweight chemistry formatter:
        - digits after letters or ')' -> subscript (H2O -> H₂O)
        - ^... tokens -> superscript (SO4^2- -> SO₄²⁻, Na^+ -> Na⁺)
        - _... tokens -> subscript (N_a2 -> Nₐ₂ style fallback with subscript digits/letters)
        Supports optional braces: ^{2-}, _{4}
        """
        sub_map = str.maketrans("0123456789+-=()abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
                                "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐ♭꜀ᑯₑբ₉ₕᵢⱼₖₗₘₙₒₚ૧ᵣₛₜᵤᵥwₓᵧ₂ₐ♭꜀ᑯₑբ₉ₕᵢⱼₖₗₘₙₒₚ૧ᵣₛₜᵤᵥwₓᵧ₂")
        sup_map = str.maketrans("0123456789+-=()abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
                                "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰᶦʲᵏˡᵐⁿᵒᵖ۹ʳˢᵗᵘᵛʷˣʸᶻᴬᴮᶜᴰᴱᶠᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾ۹ᴿˢᵀᵁⱽᵂˣʸᶻ")
        s = str(text)

        # Apply ^... / _... explicit formatting first
        def _apply_explicit(src: str, marker: str, table):
            out = []
            i = 0
            n = len(src)
            while i < n:
                if src[i] == marker:
                    i += 1
                    if i < n and src[i] == "{":
                        j = src.find("}", i + 1)
                        if j == -1:
                            token = src[i + 1 :]
                            i = n
                        else:
                            token = src[i + 1 : j]
                            i = j + 1
                    else:
                        j = i
                        while j < n and src[j].isalnum() or (j < n and src[j] in "+-="):
                            j += 1
                        token = src[i:j]
                        i = j
                    out.append(token.translate(table))
                else:
                    out.append(src[i])
                    i += 1
            return "".join(out)

        s = _apply_explicit(s, "^", sup_map)
        s = _apply_explicit(s, "_", sub_map)

        # Auto-subscript bare digits after letters / ')' for common formulas.
        out = []
        for i, ch in enumerate(s):
            if ch.isdigit() and i > 0 and (s[i - 1].isalpha() or s[i - 1] == ")"):
                out.append(ch.translate(sub_map))
            else:
                out.append(ch)
        return "".join(out)

    def _on_flow_hscroll(self, event):
        if event.state & 0x1:  # Check if shift is held (standard way for multi platform, although bind handles it)
            pass
        if event.num == 4 or event.delta > 0:
            self.flow_canvas.xview_scroll(-1, "units")
        elif event.num == 5 or event.delta < 0:
            self.flow_canvas.xview_scroll(1, "units")
        self._draw_flow()

    def _on_flow_vscroll(self, event):
        if getattr(event, "state", 0) & 0x1: # Shift held -> route to hscroll
            self._on_flow_hscroll(event)
            return
        if event.num == 4 or event.delta > 0:
            self.flow_canvas.yview_scroll(-1, "units")
        elif event.num == 5 or event.delta < 0:
            self.flow_canvas.yview_scroll(1, "units")
        self._draw_flow()

    def _on_flow_zoom(self, event):
        cur = self._flow_clamp_zoom(getattr(self, "_flow_zoom", 1.0))
        if event.num == 4 or event.delta > 0:
            nz = cur * 1.1
        elif event.num == 5 or event.delta < 0:
            nz = cur / 1.1
        else:
            nz = cur
        self._flow_zoom = self._flow_clamp_zoom(nz)
        self._draw_flow()

    def _toggle_flow_detach(self):
        if not getattr(self, "_flow_detached", False):
            try:
                self.main_frame.update_idletasks()
                self._flow_detach_sash_main = self.main_split.sash_coord(0)
            except Exception:
                self._flow_detach_sash_main = None
            try:
                self._flow_detach_sash_bottom = self.bottom_split.sash_coord(0)
            except Exception:
                self._flow_detach_sash_bottom = None
            try:
                self.bottom_split.forget(self._flow_outer)
            except Exception:
                pass

            top = tk.Toplevel(self.main_frame.winfo_toplevel())
            top.title("Reaction Flow Diagram")
            top.geometry("1200x760")
            top.minsize(720, 420)
            wrap = ttk.Frame(top, padding=6)
            wrap.pack(fill=tk.BOTH, expand=True)
            detached_canvas = tk.Canvas(wrap, bg="#ffffff", highlightthickness=0, bd=0, relief="flat")
            detached_canvas.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
            d_vscroll = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=detached_canvas.yview)
            d_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
            d_hscroll = ttk.Scrollbar(top, orient=tk.HORIZONTAL, command=detached_canvas.xview)
            d_hscroll.pack(side=tk.BOTTOM, fill=tk.X)
            detached_canvas.configure(xscrollcommand=d_hscroll.set, yscrollcommand=d_vscroll.set)

            # Rebind flow handlers to detached canvas
            detached_canvas.bind("<Button-1>", self._on_flow_press)
            detached_canvas.bind("<B1-Motion>", self._on_flow_drag)
            detached_canvas.bind("<ButtonRelease-1>", self._on_flow_release)
            detached_canvas.bind("<Motion>", self._on_flow_motion)
            detached_canvas.bind("<ButtonPress-3>", self._on_flow_right_click)
            detached_canvas.bind("<B3-Motion>", self._on_flow_right_drag)
            detached_canvas.bind("<ButtonRelease-3>", self._on_flow_right_release)
            detached_canvas.bind("<Configure>", lambda _e: self._schedule_draw_flow())
            detached_canvas.bind("<MouseWheel>", self._on_flow_vscroll)
            detached_canvas.bind("<Button-4>", self._on_flow_vscroll)
            detached_canvas.bind("<Button-5>", self._on_flow_vscroll)
            detached_canvas.bind("<Shift-MouseWheel>", self._on_flow_hscroll)
            detached_canvas.bind("<Shift-Button-4>", self._on_flow_hscroll)
            detached_canvas.bind("<Shift-Button-5>", self._on_flow_hscroll)
            detached_canvas.bind("<Control-MouseWheel>", self._on_flow_zoom)
            detached_canvas.bind("<Control-Button-4>", self._on_flow_zoom)
            detached_canvas.bind("<Control-Button-5>", self._on_flow_zoom)
            detached_canvas.bind("<Control-z>", self._flow_undo)
            detached_canvas.bind("<Control-Z>", self._flow_undo)
            detached_canvas.bind("<Control-c>", self._flow_copy_selection)
            detached_canvas.bind("<Control-C>", self._flow_copy_selection)
            detached_canvas.bind("<Control-v>", self._flow_paste_selection)
            detached_canvas.bind("<Control-V>", self._flow_paste_selection)
            detached_canvas.bind("<Delete>", self._delete_flow_multi_selection)
            detached_canvas.bind("<BackSpace>", self._delete_flow_multi_selection)

            bar = tk.Frame(top, bg="#eef2f7", highlightthickness=1, highlightbackground="#cbd5e1")
            bar.pack(fill=tk.X, side=tk.BOTTOM)
            bpp = tk.Frame(bar, bg="#eef2f7")
            bpp.pack(side=tk.RIGHT, padx=10, pady=8)
            self._flow_chrome_btn(bpp, text="PLOT", command=self._render_plot, primary=True, font_px=11).pack(
                side=tk.RIGHT, padx=(10, 0)
            )
            self._flow_chrome_btn(bpp, text="Unfade", command=self._flow_restore_faded_selection_on_pes).pack(
                side=tk.RIGHT, padx=(8, 0)
            )
            self._flow_chrome_btn(bpp, text="Fade", command=self._flow_hide_faded_selection_from_pes).pack(
                side=tk.RIGHT, padx=(8, 0)
            )
            self._flow_chrome_btn(bpp, text="Reattach to panel", command=self._toggle_flow_detach).pack(side=tk.RIGHT, padx=(0, 0))

            self._flow_detached_win = top
            self.flow_canvas = detached_canvas
            top.protocol("WM_DELETE_WINDOW", self._toggle_flow_detach)
            self._flow_detached = True
            if hasattr(self, "_flow_detach_btn"):
                self._flow_detach_btn.configure(text="Attach Flow Panel")
        else:
            try:
                if self._flow_detached_win and self._flow_detached_win.winfo_exists():
                    self._flow_detached_win.destroy()
            except Exception:
                pass
            self._flow_detached_win = None
            self.flow_canvas = self._flow_canvas_embedded if self._flow_canvas_embedded is not None else self.flow_canvas
            try:
                self.bottom_split.add(self._flow_outer, minsize=300)
            except Exception:
                pass
            self._flow_detached = False
            if hasattr(self, "_flow_detach_btn"):
                self._flow_detach_btn.configure(text="Detach Flow Panel")
            try:
                if self._flow_detach_sash_main:
                    self.main_split.sash_place(0, int(self._flow_detach_sash_main[0]), int(self._flow_detach_sash_main[1]))
            except Exception:
                pass
            try:
                if self._flow_detach_sash_bottom:
                    self.bottom_split.sash_place(0, int(self._flow_detach_sash_bottom[0]), int(self._flow_detach_sash_bottom[1]))
            except Exception:
                pass
        self._schedule_draw_flow()

    def _on_flow_pan_start(self, event):
        self.flow_canvas.scan_mark(event.x, event.y)
        self._pan_start = (event.x, event.y)

    def _on_flow_pan(self, event):
        if getattr(self, "_pan_start", None):
            c = self.flow_canvas
            c.xview_scroll(int((self._pan_start[0] - event.x)/5), "units")
            c.yview_scroll(int((self._pan_start[1] - event.y)/5), "units")
            self._pan_start = (event.x, event.y)
            self._draw_flow()
        else:
            self.flow_canvas.scan_dragto(event.x, event.y, gain=1)
            self._draw_flow()

    def _on_flow_right_click(self, event):
        c = self.flow_canvas
        try:
            c.focus_set()
        except Exception:
            pass
        cx, cy = c.canvasx(event.x), c.canvasy(event.y)
        ctrl = bool(getattr(event, "state", 0) & 0x4)
        self._flow_rsel_start = (cx, cy)
        self._flow_rsel_rect = None
        self._flow_rsel_moved = False
        self._flow_rc_tags = c.gettags(c.find_closest(cx, cy, halo=5))
        self._flow_rc_xy = (cx, cy)
        self._flow_rdrag_group = None
        self._flow_rdrag_copy = False
        hit_tok = None
        for t in self._flow_rc_tags:
            if t.startswith("flow_ov_"):
                hit_tok = f"ov:{int(t.split('_')[-1])}"
                break
            if t.startswith("sp_"):
                hit_tok = f"sp:{int(t.split('_')[1])}"
                break
        if hit_tok and hit_tok in self._flow_multi_selected and len(self._flow_multi_selected) > 1:
            origin = {}
            for tok in self._flow_multi_selected:
                if tok.startswith("sp:"):
                    sid = int(tok.split(":", 1)[1])
                    sp = next((s for s in self.species if int(s.get("id", -1)) == sid), None)
                    if sp:
                        origin[tok] = (float(sp.get("x", 0.0)), float(sp.get("y", 0.0)))
                elif tok.startswith("ov:"):
                    oi = int(tok.split(":", 1)[1])
                    if 0 <= oi < len(self._flow_overlays):
                        ov = self._flow_overlays[oi]
                        origin[tok] = (float(ov.get("x", 0.0)), float(ov.get("y", 0.0)))
            if origin:
                if ctrl:
                    self._flow_rdrag_copy = True
                else:
                    self._push_flow_undo()
                    self._flow_rdrag_group = {"startx": cx, "starty": cy, "orig": origin}

    def _flow_dialog_parent(self):
        try:
            if getattr(self, "_flow_detached", False) and self._flow_detached_win and self._flow_detached_win.winfo_exists():
                return self._flow_detached_win
        except Exception:
            pass
        return self.main_frame.winfo_toplevel()

    def _add_flow_text_overlay(self, cx, cy):
        txt = simpledialog.askstring("Add Text", "Enter text:", parent=self._flow_dialog_parent())
        if not txt:
            return
        self._push_flow_undo()
        # Avoid spawning below the floating Add button area.
        y_safe = cy + 44 if cy <= (self.flow_canvas.canvasy(16) + 40) else cy
        self._flow_overlays.append({"kind": "text", "text": txt, "x": cx / self._flow_zoom, "y": y_safe / self._flow_zoom, "size": 12.0, "color": "#111827"})
        self._draw_flow()

    def _add_flow_image_overlay(self, cx, cy):
        p = filedialog.askopenfilename(
            parent=self._flow_dialog_parent(),
            title="Select image for flow diagram",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("All files", "*.*")],
        )
        if not p:
            return
        self._push_flow_undo()
        y_safe = cy + 44 if cy <= (self.flow_canvas.canvasy(16) + 40) else cy
        self._flow_overlays.append({"kind": "image", "path": p, "x": cx / self._flow_zoom, "y": y_safe / self._flow_zoom, "size": 0.24})
        self._draw_flow()

    def _add_flow_shape_overlay(self, cx, cy, shape_kind: str):
        self._push_flow_undo()
        y_safe = cy + 44 if cy <= (self.flow_canvas.canvasy(16) + 40) else cy
        sk = str(shape_kind or "rectangle").strip().lower()
        if sk not in ("circle", "rectangle", "diamond"):
            sk = "rectangle"
        self._flow_overlays.append(
            {
                "kind": "shape",
                "shape": sk,
                "x": cx / self._flow_zoom,
                "y": y_safe / self._flow_zoom,
                "size": 1.0,
                "color": "#bfdbfe",
                "outline": "#1d4ed8",
            }
        )
        self._draw_flow()

    def _flow_overlay_bbox(self, ov: dict, z: float):
        kind = str((ov or {}).get("kind", "")).lower()
        ox = float((ov or {}).get("x", 0.0)) * z
        oy = float((ov or {}).get("y", 0.0)) * z
        if kind == "text":
            fs = max(8, int(float((ov or {}).get("size", 12.0)) * z))
            w = max(36, int(len(str((ov or {}).get("text", "Text"))) * fs * 0.58))
            h = max(18, int(fs * 1.45))
            return (ox, oy, ox + w, oy + h)
        if kind == "shape":
            sc = max(0.4, float((ov or {}).get("size", 1.0)))
            bw = max(24, int(64 * z * sc))
            bh = max(18, int(42 * z * sc))
            return (ox, oy, ox + bw, oy + bh)
        # image and fallback
        sc = max(0.08, float((ov or {}).get("size", 0.24)))
        bw = max(30, int(120 * z * sc))
        bh = max(20, int(90 * z * sc))
        return (ox, oy, ox + bw, oy + bh)

    def _duplicate_flow_multi_selection(self, dx: float, dy: float):
        sels = set(getattr(self, "_flow_multi_selected", set()) or set())
        if not sels:
            return
        self._push_flow_undo()
        dx, dy = self._flow_dup_delta(dx, dy)
        sid_sel = sorted([int(t.split(":", 1)[1]) for t in sels if t.startswith("sp:")])
        ov_sel = sorted([int(t.split(":", 1)[1]) for t in sels if t.startswith("ov:")])
        sid_map = {}
        new_sel = set()

        for sid in sid_sel:
            sp = next((s for s in self.species if int(s.get("id", -1)) == sid), None)
            if not sp:
                continue
            cp = copy.deepcopy(sp)
            cp["id"] = self._alloc_species_id()
            cp["name"] = self._dup_flow_name(cp.get("name", "I_1"))
            cp["x"] = float(cp.get("x", 0.0)) + float(dx)
            cp["y"] = float(cp.get("y", 0.0)) + float(dy)
            self.species.append(cp)
            sid_map[sid] = int(cp["id"])
            new_sel.add(f"sp:{int(cp['id'])}")

        if sid_map:
            for ek, ev in list(self.edge_links.items()):
                try:
                    u, v = map(int, str(ek).split("_"))
                except Exception:
                    continue
                if u in sid_map and v in sid_map:
                    self.edge_links[f"{sid_map[u]}_{sid_map[v]}"] = copy.deepcopy(ev)

        for oi in ov_sel:
            if not (0 <= oi < len(self._flow_overlays)):
                continue
            ov = copy.deepcopy(self._flow_overlays[oi])
            ov["x"] = float(ov.get("x", 0.0)) + float(dx)
            ov["y"] = float(ov.get("y", 0.0)) + float(dy)
            self._flow_overlays.append(ov)
            new_sel.add(f"ov:{len(self._flow_overlays)-1}")

        self._flow_multi_selected = new_sel
        self._refresh_ref_values()
        self._recompute()

    def _flow_overlay_menu(self, event, idx: int):
        if not (0 <= idx < len(self._flow_overlays)):
            return
        ov = self._flow_overlays[idx]
        m = tk.Menu(self._flow_dialog_parent(), tearoff=0)
        def _inc():
            self._push_flow_undo()
            ov["size"] = float(ov.get("size", 1.0)) * 1.2
            self._draw_flow()
        def _dec():
            self._push_flow_undo()
            ov["size"] = max(0.05, float(ov.get("size", 1.0)) / 1.2)
            self._draw_flow()
        def _set():
            v = simpledialog.askfloat("Overlay size", "Set size/scale:", initialvalue=float(ov.get("size", 1.0)), parent=self._flow_dialog_parent())
            if v is None:
                return
            self._push_flow_undo()
            ov["size"] = max(0.05, float(v))
            self._draw_flow()
        def _edit_text():
            t = simpledialog.askstring("Edit Text", "Text:", initialvalue=str(ov.get("text", "")), parent=self._flow_dialog_parent())
            if t is None:
                return
            self._push_flow_undo()
            ov["text"] = t
            self._draw_flow()
        def _pick_color():
            curr = str(ov.get("color", "#111827") or "#111827")
            _, col = colorchooser.askcolor(color=curr, title="Select color", parent=self._flow_dialog_parent())
            if not col:
                return
            self._push_flow_undo()
            ov["color"] = col
            self._draw_flow()
        def _pick_outline():
            curr = str(ov.get("outline", "#1d4ed8") or "#1d4ed8")
            _, col = colorchooser.askcolor(color=curr, title="Select outline color", parent=self._flow_dialog_parent())
            if not col:
                return
            self._push_flow_undo()
            ov["outline"] = col
            self._draw_flow()
        def _delete():
            self._push_flow_undo()
            self._flow_overlays.pop(idx)
            self._draw_flow()
        if str(ov.get("kind", "")).lower() == "text":
            m.add_command(label="Edit Text", command=_edit_text)
            m.add_command(label="Text Color...", command=_pick_color)
            m.add_separator()
        elif str(ov.get("kind", "")).lower() == "shape":
            m.add_command(label="Fill Color...", command=_pick_color)
            m.add_command(label="Outline Color...", command=_pick_outline)
            m.add_separator()
        m.add_command(label="Increase Size", command=_inc)
        m.add_command(label="Decrease Size", command=_dec)
        m.add_command(label="Set Size...", command=_set)
        m.add_separator()
        m.add_command(label="Delete", command=_delete)
        m.tk_popup(event.x_root, event.y_root)

    # ================== PROJECT MANAGEMENT (JSON) ==================

    def _project_store_read(self) -> dict:
        import json, os
        if not os.path.isfile(self._pes_project_file):
            return {"projects": {}, "order": [], "suborder": {}}
        try:
            with open(self._pes_project_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return {"projects": {}, "order": [], "suborder": {}}
                projects = data.get("projects", {})
                if isinstance(projects, dict):
                    # Backward compatibility: old flat format {name: snapshot}
                    old_flat = any(isinstance(v, dict) and ("species" in v or "version" in v) for v in projects.values())
                    if old_flat:
                        converted = {}
                        for k, v in projects.items():
                            converted[str(k)] = {"Default": v} if isinstance(v, dict) else {}
                        return {"projects": converted, "order": sorted(converted.keys()), "suborder": {k: ["Default"] for k in converted.keys()}}
                return {
                    "projects": projects if isinstance(projects, dict) else {},
                    "order": data.get("order", []),
                    "suborder": data.get("suborder", {}),
                }
        except Exception:
            return {"projects": {}, "order": [], "suborder": {}}

    def _project_store_write(self, data: dict):
        import json
        import os
        try:
            os.makedirs(os.path.dirname(self._pes_project_file), exist_ok=True)
            with open(self._pes_project_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            messagebox.showerror("Projects", f"Could not save project file:\n{e}")

    def _refresh_project_list(self):
        if not getattr(self, "project_combo", None):
            return
        data = self._project_store_read()
        names = [self._project_combo_placeholder] + sorted(data.get("projects", {}).keys())
        self.project_combo["values"] = names
        if self.project_combo_var.get() not in names:
            self.project_combo_var.set(self._project_combo_placeholder)
        self._refresh_subproject_list(selected_parent=self.project_combo_var.get())

    def _refresh_subproject_list(self, selected_parent=None, select_sub=None):
        if not getattr(self, "subproject_combo", None):
            return
        data = self._project_store_read()
        parent = selected_parent or self.project_combo_var.get()
        sub_names = []
        if parent and parent != self._project_combo_placeholder:
            pmap = data.get("projects", {}).get(parent, {})
            if isinstance(pmap, dict):
                sub_names = sorted(pmap.keys())
        vals = [self._subproject_combo_placeholder] + sub_names
        self.subproject_combo["values"] = vals
        if select_sub and select_sub in sub_names:
            self.subproject_combo_var.set(select_sub)
        elif self.subproject_combo_var.get() not in vals:
            self.subproject_combo_var.set(self._subproject_combo_placeholder)

    def _on_parent_project_selected(self, event=None):
        self._refresh_subproject_list(selected_parent=self.project_combo_var.get())

    def _build_labels_menu_button(self, parent):
        btn = ttk.Menubutton(parent, text="Labels ▸")
        menu = tk.Menu(btn, tearoff=0)
        color_menu = tk.Menu(menu, tearoff=0)
        size_menu = tk.Menu(menu, tearoff=0)
        hide_menu = tk.Menu(menu, tearoff=0)
        color_menu.add_radiobutton(label="Black", variable=self._label_color_mode, value="Black", command=self._render_plot)
        color_menu.add_radiobutton(label="Match the Plot", variable=self._label_color_mode, value="Match the Plot", command=self._render_plot)
        color_menu.add_radiobutton(label="Custom", variable=self._label_color_mode, value="Custom", command=self._render_plot)
        color_menu.add_separator()
        color_menu.add_command(label="Pick Custom...", command=self._pick_custom_label_color)
        size_menu.add_radiobutton(label="Small", variable=self._label_size_mode, value="Small", command=self._render_plot)
        size_menu.add_radiobutton(label="Big", variable=self._label_size_mode, value="Big", command=self._render_plot)
        size_menu.add_radiobutton(label="Custom", variable=self._label_size_mode, value="Custom", command=self._render_plot)
        size_menu.add_separator()
        size_menu.add_command(label="Set Custom...", command=self._set_custom_label_size)
        hide_menu.add_checkbutton(label="Hide Names", variable=self._label_hide_names, command=self._render_plot)
        hide_menu.add_checkbutton(label="Hide Energies", variable=self._label_hide_energies, command=self._render_plot)
        hide_menu.add_checkbutton(label="Hide Left Axis", variable=self._label_hide_left_axis, command=self._render_plot)
        hide_menu.add_checkbutton(label="Show S**2 Deviation", variable=self._label_show_s2_dev, command=self._render_plot)
        menu.add_cascade(label="Color", menu=color_menu)
        menu.add_cascade(label="Size", menu=size_menu)
        menu.add_cascade(label="Hide", menu=hide_menu)
        btn["menu"] = menu
        return btn

    def _pick_custom_label_color(self):
        try:
            import tkinter.colorchooser as cc
            parent = self._plot_window if (self._plot_window and self._plot_window.winfo_exists()) else self.main_frame.winfo_toplevel()
            c_out = cc.askcolor(initialcolor=self._label_custom_color.get(), parent=parent)[1]
            if c_out:
                self._label_custom_color.set(c_out)
                self._label_color_mode.set("Custom")
                self._render_plot()
            try:
                if self._plot_window and self._plot_window.winfo_exists():
                    self._plot_window.deiconify()
                    self._plot_window.lift()
                    self._plot_window.focus_force()
            except Exception:
                pass
        except Exception:
            pass

    def _set_custom_label_size(self):
        try:
            parent = self._plot_window if (self._plot_window and self._plot_window.winfo_exists()) else self.main_frame.winfo_toplevel()
            v = simpledialog.askfloat("Label Size", "Enter custom label size:", initialvalue=float(self._label_custom_size.get()), parent=parent, minvalue=6.0, maxvalue=36.0)
            if v is not None:
                self._label_custom_size.set(float(v))
                self._label_size_mode.set("Custom")
                self._render_plot()
            try:
                if self._plot_window and self._plot_window.winfo_exists():
                    self._plot_window.deiconify()
                    self._plot_window.lift()
                    self._plot_window.focus_force()
            except Exception:
                pass
        except Exception:
            pass

    def _flow_chrome_btn(
        self,
        parent,
        text: str,
        command,
        *,
        primary: bool = False,
        font_px: int = 9,
    ) -> tk.Button:
        """Flat flow-toolbar buttons — clean silhouette (no ttk plateau); primary matches main PLOT blue."""
        if primary:
            bg, fg, abg = "#2563eb", "#ffffff", "#1d4ed8"
            af = "#ffffff"
        else:
            bg, fg, abg = "#dde5f0", "#0f172a", "#ced9e8"
            af = "#0f172a"
        try:
            pbg = parent.cget("bg")
        except tk.TclError:
            pbg = "#f8fafc"
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=("Segoe UI", font_px, "bold"),
            bg=bg,
            fg=fg,
            activebackground=abg,
            activeforeground=af,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=max(12, font_px + 7),
            pady=max(5, font_px // 2 + 2),
            cursor="hand2",
            takefocus=False,
            highlightbackground=pbg,
        )

    def _create_animated_plot_button(self, parent):
        holder = tk.Frame(parent, bg="#2563eb", bd=0, highlightthickness=0)
        btn = tk.Button(
            holder,
            text="PLOT",
            command=self._render_plot,
            font=("Segoe UI", 12, "bold"),
            bg="#2563eb",
            fg="white",
            activebackground="#1d4ed8",
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=24,
            pady=7,
            cursor="hand2",
        )
        btn.pack(padx=3, pady=3)
        self._plot_anim_holder = holder
        self._plot_anim_phase = 0
        self._animate_plot_button_border()
        return holder

    def _animate_plot_button_border(self):
        holder = getattr(self, "_plot_anim_holder", None)
        if holder is None:
            return
        try:
            if not holder.winfo_exists():
                return
            # Match intro splash gradient family (green -> yellow -> orange/red).
            palette = ["#16a34a", "#84cc16", "#eab308", "#f59e0b", "#ef4444", "#f59e0b", "#eab308", "#84cc16"]
            i = int(getattr(self, "_plot_anim_phase", 0)) % len(palette)
            holder.configure(bg=palette[i])
            self._plot_anim_phase = i + 1
            holder.after(140, self._animate_plot_button_border)
        except Exception:
            return

    def _csv_action_prompt(self):
        parent = self.main_frame.winfo_toplevel()
        choice = messagebox.askyesnocancel(
            "CSV Action",
            "Choose CSV action:\n\nYes = Import CSV\nNo = Export CSV\nCancel = Do nothing",
            parent=parent,
        )
        if choice is True:
            self._import_csv()
        elif choice is False:
            self._export_csv()

    def _on_project_select(self, event=None):
        parent = self.project_combo_var.get()
        sub = self.subproject_combo_var.get() if hasattr(self, "subproject_combo_var") else self._subproject_combo_placeholder
        if parent and parent != self._project_combo_placeholder and sub and sub != self._subproject_combo_placeholder:
            data = self._project_store_read()
            pmap = data.get("projects", {}).get(parent, {})
            if isinstance(pmap, dict) and sub in pmap:
                self.proj_save_var.set(sub)
                try:
                    self._apply_project_snapshot(pmap[sub])
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to load project:\n{e}")

    def _prompt_project_save_target(self):
        parent_ui = self.main_frame.winfo_toplevel()
        data = self._project_store_read()
        parents = sorted((data.get("projects") or {}).keys())
        current_parent = (self.project_combo_var.get() or "").strip()
        if current_parent == self._project_combo_placeholder:
            current_parent = ""
        current_sub = (self.proj_save_var.get() or "").strip()
        if not current_sub and hasattr(self, "subproject_combo_var"):
            current_sub = (self.subproject_combo_var.get() or "").strip()
        if current_sub == self._subproject_combo_placeholder:
            current_sub = ""

        win = tk.Toplevel(parent_ui)
        win.title("Save Project Snapshot")
        win.transient(parent_ui)
        win.grab_set()
        win.geometry("640x460")
        win.minsize(560, 400)

        result = {"parent": None, "sub": None}

        body = ttk.Frame(win, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            body,
            text="Select existing project/sub-project or type new names",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        r1 = ttk.Frame(body)
        r1.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(r1, text="Main Project", width=14).pack(side=tk.LEFT)
        parent_var = tk.StringVar(value=current_parent or "")
        parent_cb = ttk.Combobox(r1, textvariable=parent_var, values=parents, state="normal", width=42)
        parent_cb.pack(side=tk.LEFT, fill=tk.X, expand=True)

        r2 = ttk.Frame(body)
        r2.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(r2, text="Sub-project", width=14).pack(side=tk.LEFT)
        sub_var = tk.StringVar(value=current_sub or "Default")
        sub_cb = ttk.Combobox(r2, textvariable=sub_var, values=[], state="normal", width=42)
        sub_cb.pack(side=tk.LEFT, fill=tk.X, expand=True)

        preview = tk.Listbox(body, height=14, font=("Consolas", 9))
        preview.pack(fill=tk.BOTH, expand=True, pady=(6, 10))

        def _refresh_preview():
            preview.delete(0, tk.END)
            fresh = self._project_store_read()
            pnames = sorted((fresh.get("projects") or {}).keys())
            for p in pnames:
                preview.insert(tk.END, f"{p}/")
                pmap = (fresh.get("projects") or {}).get(p) or {}
                for s in sorted(pmap.keys()):
                    preview.insert(tk.END, f"  - {s}")

        def _refresh_subs(*_args):
            p = (parent_var.get() or "").strip()
            pmap = (self._project_store_read().get("projects") or {}).get(p, {})
            if not isinstance(pmap, dict):
                pmap = {}
            sub_cb["values"] = sorted(pmap.keys())

        def _save_and_close():
            p = (parent_var.get() or "").strip()
            s = (sub_var.get() or "").strip()
            if not p:
                messagebox.showwarning("Save Project", "Enter main project name.", parent=win)
                return
            if not s:
                messagebox.showwarning("Save Project", "Enter sub-project name.", parent=win)
                return
            result["parent"] = p
            result["sub"] = s
            win.destroy()

        parent_cb.bind("<<ComboboxSelected>>", _refresh_subs)
        parent_cb.bind("<KeyRelease>", _refresh_subs)
        _refresh_subs()
        _refresh_preview()

        btns = ttk.Frame(body)
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Save", command=_save_and_close).pack(side=tk.RIGHT, padx=(0, 8))

        try:
            parent_cb.focus_set()
        except Exception:
            pass
        win.wait_window()
        return result["parent"], result["sub"]

    def _save_project(self):
        parent, sub = self._prompt_project_save_target()
        if not parent or not sub:
            messagebox.showinfo("Save Project", "Save cancelled (missing project/sub-project name).")
            return

        data = self._project_store_read()
        pmap = data.get("projects", {}).get(parent, {})
        already_exists = isinstance(pmap, dict) and sub in pmap
        if already_exists:
            ok_overwrite = messagebox.askyesno(
                "Overwrite Sub-project",
                f"Sub-project '{sub}' already exists in '{parent}'.\n\nDo you want to overwrite it?",
                parent=self.main_frame.winfo_toplevel(),
            )
            if not ok_overwrite:
                return

        snap = self._collect_project_snapshot()
        if "projects" not in data or not isinstance(data["projects"], dict):
            data["projects"] = {}
        if parent not in data["projects"] or not isinstance(data["projects"][parent], dict):
            data["projects"][parent] = {}
        data["projects"][parent][sub] = snap
        data.setdefault("order", [])
        if parent not in data["order"]:
            data["order"].append(parent)
        data.setdefault("suborder", {})
        data["suborder"].setdefault(parent, [])
        if sub not in data["suborder"][parent]:
            data["suborder"][parent].append(sub)
        self._project_store_write(data)
        self.project_combo_var.set(parent)
        if hasattr(self, "subproject_combo_var"):
            self.subproject_combo_var.set(sub)
        self.proj_save_var.set(sub)
        self._refresh_project_list()
        self._refresh_subproject_list(selected_parent=parent, select_sub=sub)
        messagebox.showinfo("Saved", f"Project '{parent}' / sub-project '{sub}' saved to local database.")

    def _delete_project(self):
        parent = self.project_combo_var.get()
        sub = self.subproject_combo_var.get() if hasattr(self, "subproject_combo_var") else self._subproject_combo_placeholder
        if not parent or parent == self._project_combo_placeholder or not sub or sub == self._subproject_combo_placeholder:
            return
        warn1 = messagebox.askyesno(
            "Delete Sub-project",
            f"Delete sub-project '{sub}' from project '{parent}'?\n\nThis cannot be undone.",
            parent=self.main_frame.winfo_toplevel(),
        )
        if not warn1:
            return
        warn2 = messagebox.askyesno(
            "Confirm Delete Again",
            f"Please confirm again:\nDelete '{parent} / {sub}' permanently?",
            parent=self.main_frame.winfo_toplevel(),
        )
        if not warn2:
            return
        data = self._project_store_read()
        pmap = data.get("projects", {}).get(parent, {})
        if isinstance(pmap, dict) and sub in pmap:
            del pmap[sub]
        if isinstance(pmap, dict) and not pmap:
            data.get("projects", {}).pop(parent, None)
        self._project_store_write(data)
        self.project_combo_var.set(self._project_combo_placeholder)
        if hasattr(self, "subproject_combo_var"):
            self.subproject_combo_var.set(self._subproject_combo_placeholder)
        self.proj_save_var.set("")
        self._refresh_project_list()

    def _clear_panel_with_prompt(self):
        parent = self.main_frame.winfo_toplevel()
        has_data = bool(self.species or self.edge_links or self._plot_assets or self._flow_overlays)
        if not has_data:
            return
        save_choice = messagebox.askyesnocancel(
            "Clear Panel",
            "This will clear the current PES panel.\n\nDo you want to save the current project first?",
            parent=parent,
        )
        if save_choice is None:
            return
        if save_choice:
            name = (self.proj_save_var.get() or "").strip()
            if not name:
                combo_name = (self.project_combo_var.get() or "").strip() if hasattr(self, "project_combo_var") else ""
                if combo_name and combo_name != self._project_combo_placeholder:
                    name = combo_name
            if not name:
                name = simpledialog.askstring("Save Project", "Enter project name to save before clearing:", parent=parent)
            name = (name or "").strip()
            if not name:
                messagebox.showinfo("Clear Panel", "Clear cancelled because project name was not provided.", parent=parent)
                return
            self.proj_save_var.set(name)
            self._save_project()

        self.species = []
        self.edge_links = {}
        self._plot_assets = []
        self._flow_overlays = []
        self._diff_bar_label_xy = None
        self.pt_offsets = {}
        self._ref_name.set("")
        self.project_combo_var.set(self._project_combo_placeholder)
        if hasattr(self, "subproject_combo_var"):
            self.subproject_combo_var.set(self._subproject_combo_placeholder)
        self.proj_save_var.set("")
        self._refresh_ref_values()
        self._recompute()

    def _collect_project_snapshot(self):
        return {
            "version": 1,
            "species": self.species,
            "edge_links": self.edge_links,
            "ref_name": self._ref_name.get(),
            "plot_style": self._plot_style.get(),
            "plot_shape": self._plot_shape.get(),
            "plot_colors_mode": self._plot_colors_mode.get(),
            "grid_mode": self._grid_mode.get(),
            "plot_gradient": self._plot_gradient.get(),
            "plot_gradient_map": self._plot_gradient_map.get(),
            "plot_labels": self._plot_labels.get(),
            "label_color_mode": self._label_color_mode.get(),
            "label_custom_color": self._label_custom_color.get(),
            "label_size_mode": self._label_size_mode.get(),
            "label_custom_size": self._label_custom_size.get(),
            "label_hide_names": self._label_hide_names.get(),
            "label_hide_energies": self._label_hide_energies.get(),
            "label_hide_left_axis": self._label_hide_left_axis.get(),
            "label_show_s2_dev": self._label_show_s2_dev.get(),
            "plot_draw_mos": self._plot_draw_mos.get(),
            "plot_energy_basis": self._plot_energy_basis.get(),
            "show_images": self._show_images.get(),
            "show_diff_bars": self._show_diff_bars.get(),
            "show_merged_plateaus": self._show_merged_plateaus.get(),
            "show_axis_break": self._show_axis_break.get(),
            "show_collision_avoid": self._show_collision_avoid.get(),
            "aks_temp_label": self._aks_temp_label.get(),
            "aks_step_labels": self._aks_step_labels.get(),
            "aks_axis_x": float(self._aks_axis_x.get()),
            "ccc_axis_user_placed": bool(self._ccc_axis_user_placed.get()),
            "axis_break_low": self._axis_break_low.get(),
            "axis_break_high": self._axis_break_high.get(),
            "plateau_tol": self._plateau_tol.get(),
            "image_scale": self._image_scale.get(),
            "plot_assets": self._plot_assets,
            "flow_overlays": self._flow_overlays,
            "flow_zoom": self._flow_clamp_zoom(getattr(self, "_flow_zoom", 1.0)),
            "show_flow_rel_g": bool(self._show_flow_rel_g.get()),
            "flow_edge_style": str(self._flow_edge_style.get() or "curve"),
            "diff_bar_label_xy": list(self._diff_bar_label_xy) if getattr(self, "_diff_bar_label_xy", None) else None,
            "diff_bar_pathway_idx": int(self._diff_bar_pathway_idx.get()),
        }

    def _collect_plot_defaults(self):
        custom_ylabel = getattr(self, "_plot_custom_ylabel", None)
        return {
            "version": 1,
            "plot_style": self._plot_style.get(),
            "plot_colors_mode": self._plot_colors_mode.get(),
            "plot_gradient_map": self._plot_gradient_map.get(),
            "plot_labels": self._plot_labels.get(),
            "label_color_mode": self._label_color_mode.get(),
            "label_custom_color": self._label_custom_color.get(),
            "label_size_mode": self._label_size_mode.get(),
            "label_custom_size": float(self._label_custom_size.get()),
            "label_hide_names": bool(self._label_hide_names.get()),
            "label_hide_energies": bool(self._label_hide_energies.get()),
            "label_hide_left_axis": bool(self._label_hide_left_axis.get()),
            "label_show_s2_dev": bool(self._label_show_s2_dev.get()),
            "plot_draw_mos": self._plot_draw_mos.get(),
            "plot_energy_basis": self._plot_energy_basis.get(),
            "grid_mode": self._grid_mode.get(),
            "show_images": bool(self._show_images.get()),
            "show_diff_bars": bool(self._show_diff_bars.get()),
            "show_merged_plateaus": bool(self._show_merged_plateaus.get()),
            "show_axis_break": bool(self._show_axis_break.get()),
            "show_collision_avoid": bool(self._show_collision_avoid.get()),
            "aks_temp_label": self._aks_temp_label.get(),
            "aks_step_labels": self._aks_step_labels.get(),
            "axis_break_low": float(self._axis_break_low.get()),
            "axis_break_high": float(self._axis_break_high.get()),
            "plateau_tol": float(self._plateau_tol.get()),
            "image_scale": float(self._image_scale.get()),
            "energy_unit": self._energy_unit.get(),
            "plot_custom_ylabel": custom_ylabel.get() if (custom_ylabel and hasattr(custom_ylabel, "get")) else "",
        }

    def _apply_plot_defaults(self, data: dict):
        if not isinstance(data, dict):
            return
        self._plot_style.set(str(data.get("plot_style", self._plot_style.get())))
        self._plot_colors_mode.set(str(data.get("plot_colors_mode", self._plot_colors_mode.get())))
        self._plot_gradient_map.set(str(data.get("plot_gradient_map", self._plot_gradient_map.get())))
        self._plot_labels.set(str(data.get("plot_labels", self._plot_labels.get())))
        self._label_color_mode.set(str(data.get("label_color_mode", self._label_color_mode.get())))
        self._label_custom_color.set(str(data.get("label_custom_color", self._label_custom_color.get())))
        self._label_size_mode.set(str(data.get("label_size_mode", self._label_size_mode.get())))
        self._label_custom_size.set(float(data.get("label_custom_size", float(self._label_custom_size.get()))))
        self._label_hide_names.set(bool(data.get("label_hide_names", self._label_hide_names.get())))
        self._label_hide_energies.set(bool(data.get("label_hide_energies", self._label_hide_energies.get())))
        self._label_hide_left_axis.set(bool(data.get("label_hide_left_axis", self._label_hide_left_axis.get())))
        self._label_show_s2_dev.set(bool(data.get("label_show_s2_dev", self._label_show_s2_dev.get())))
        self._plot_draw_mos.set(str(data.get("plot_draw_mos", self._plot_draw_mos.get())))
        self._plot_energy_basis.set(str(data.get("plot_energy_basis", self._plot_energy_basis.get())))
        self._grid_mode.set(str(data.get("grid_mode", self._grid_mode.get())))
        self._show_images.set(bool(data.get("show_images", self._show_images.get())))
        self._show_diff_bars.set(bool(data.get("show_diff_bars", self._show_diff_bars.get())))
        self._show_merged_plateaus.set(bool(data.get("show_merged_plateaus", self._show_merged_plateaus.get())))
        self._show_axis_break.set(bool(data.get("show_axis_break", self._show_axis_break.get())))
        self._show_collision_avoid.set(bool(data.get("show_collision_avoid", self._show_collision_avoid.get())))
        self._aks_temp_label.set(str(data.get("aks_temp_label", self._aks_temp_label.get())))
        self._aks_step_labels.set(str(data.get("aks_step_labels", self._aks_step_labels.get())))
        self._axis_break_low.set(float(data.get("axis_break_low", float(self._axis_break_low.get()))))
        self._axis_break_high.set(float(data.get("axis_break_high", float(self._axis_break_high.get()))))
        self._plateau_tol.set(float(data.get("plateau_tol", float(self._plateau_tol.get()))))
        self._image_scale.set(float(data.get("image_scale", float(self._image_scale.get()))))
        self._energy_unit.set(str(data.get("energy_unit", self._energy_unit.get())))
        if not hasattr(self, "_plot_custom_ylabel"):
            self._plot_custom_ylabel = tk.StringVar(value="")
        self._plot_custom_ylabel.set(str(data.get("plot_custom_ylabel", self._plot_custom_ylabel.get())))
        self._refresh_color_controls()

    def _save_plot_defaults(self):
        import json
        import os
        path = getattr(self, "_pes_plot_defaults_file", "")
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._collect_plot_defaults(), f, indent=2, ensure_ascii=False)
            messagebox.showinfo("PES Plot", "Current plot settings saved as default.")
        except Exception as e:
            messagebox.showerror("PES Plot", f"Could not save plot defaults:\n{e}")
            
    def _load_plot_defaults(self):
        import json
        path = getattr(self, "_pes_plot_defaults_file", "")
        if not path or (not os.path.isfile(path)):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._apply_plot_defaults(data)
        except Exception:
            pass

    def _use_plot_defaults(self):
        self._plot_style.set("CCC: 1 Curved")
        self._plot_colors_mode.set("Gradient")
        self._plot_gradient_map.set("PeakMap")
        self._grid_mode.set("Hide: All except Plot")
        self._label_hide_names.set(False)
        self._label_hide_energies.set(False)
        self._label_hide_left_axis.set(False)
        self._label_show_s2_dev.set(False)
        self._show_diff_bars.set(False)
        self._ccc_axis_user_placed.set(False)
        self._aks_axis_x.set(0.05)
        self._load_plot_defaults()
        self._recompute()
        self._render_plot()

    def _apply_project_snapshot(self, snap):
        if snap.get("version") != 1:
            raise ValueError("Unsupported project format iteration.")
        
        self.species = snap.get("species", [])
        self.edge_links = snap.get("edge_links", {})
        
        self._ref_name.set(snap.get("ref_name", ""))
        raw_style = str(snap.get("plot_style", "CCC: 1 Curved") or "CCC: 1 Curved").strip()
        raw_shape = str(snap.get("plot_shape", "CCL_View_Curved") or "CCL_View_Curved").strip().lower()
        if raw_style.lower() in {"clean", "publication", "minimal"}:
            if "classic 2" in raw_shape:
                raw_style = "Classical: 2 Classic 2"
            elif "classic" in raw_shape:
                raw_style = "Classical: 1 Classic 1"
            elif "straight" in raw_shape:
                raw_style = "CCC: 2 Straight"
            else:
                raw_style = "CCC: 1 Curved"
        self._plot_style.set(raw_style)
        self._plot_shape.set(snap.get("plot_shape", "CCL_View_Curved"))
        if "plot_colors_mode" in snap:
            mode = str(snap.get("plot_colors_mode") or "Gradient")
            self._plot_colors_mode.set("Normal" if mode == "Normal 1" else mode)
        else:
            self._plot_colors_mode.set("Gradient" if bool(snap.get("plot_gradient", True)) else "Normal")
        self._plot_gradient.set(bool(snap.get("plot_gradient", True)))
        self._plot_gradient_map.set(snap.get("plot_gradient_map", "PeakMap"))
        gm = str(snap.get("grid_mode", "Hide: All Grids") or "Hide: All Grids")
        if gm == "More Detailed Grids":
            gm = "More Grids"
        elif gm == "No Grids":
            gm = "Hide: All Grids"
        self._grid_mode.set(gm)
        self._plot_labels.set(snap.get("plot_labels", "Clean"))
        self._label_color_mode.set(snap.get("label_color_mode", "Black"))
        self._label_custom_color.set(snap.get("label_custom_color", "#111827"))
        self._label_size_mode.set(snap.get("label_size_mode", "Small"))
        self._label_custom_size.set(float(snap.get("label_custom_size", 11.0)))
        self._label_hide_names.set(bool(snap.get("label_hide_names", False)))
        self._label_hide_energies.set(bool(snap.get("label_hide_energies", False)))
        self._label_hide_left_axis.set(bool(snap.get("label_hide_left_axis", False)))
        self._label_show_s2_dev.set(bool(snap.get("label_show_s2_dev", False)))
        self._plot_draw_mos.set(snap.get("plot_draw_mos", "Auto"))
        self._plot_energy_basis.set(snap.get("plot_energy_basis", "G"))
        self._show_images.set(bool(snap.get("show_images", False)))
        self._show_diff_bars.set(bool(snap.get("show_diff_bars", False)))
        self._show_merged_plateaus.set(bool(snap.get("show_merged_plateaus", False)))
        self._show_axis_break.set(bool(snap.get("show_axis_break", False)))
        self._show_collision_avoid.set(bool(snap.get("show_collision_avoid", True)))
        self._aks_temp_label.set(str(snap.get("aks_temp_label", self._aks_temp_label.get())))
        self._aks_step_labels.set(str(snap.get("aks_step_labels", self._aks_step_labels.get())))
        self._aks_axis_x.set(float(snap.get("aks_axis_x", float(self._aks_axis_x.get()))))
        if "ccc_axis_user_placed" in snap:
            self._ccc_axis_user_placed.set(bool(snap.get("ccc_axis_user_placed", False)))
        else:
            # Legacy projects: negative aks_axis_x was an absolute data-x from manual drag.
            try:
                self._ccc_axis_user_placed.set(float(self._aks_axis_x.get()) <= 0.0)
            except Exception:
                self._ccc_axis_user_placed.set(False)
        self._axis_break_low.set(float(snap.get("axis_break_low", -20.0)))
        self._axis_break_high.set(float(snap.get("axis_break_high", 20.0)))
        self._plateau_tol.set(float(snap.get("plateau_tol", 0.5)))
        self._image_scale.set(float(snap.get("image_scale", 0.22)))
        self._plot_assets = snap.get("plot_assets", [])
        self._flow_overlays = snap.get("flow_overlays", [])
        self._flow_zoom = self._flow_clamp_zoom(snap.get("flow_zoom", 1.0))
        self._show_flow_rel_g.set(bool(snap.get("show_flow_rel_g", False)))
        _fes = str(snap.get("flow_edge_style", "curve") or "curve").strip().lower()
        if _fes not in ("curve", "straight"):
            _fes = "curve"
        self._flow_edge_style.set(_fes)
        pass
        dbl = snap.get("diff_bar_label_xy")
        self._diff_bar_label_xy = tuple(dbl) if isinstance(dbl, (list, tuple)) and len(dbl) == 2 else None
        try:
            self._diff_bar_pathway_idx.set(int(snap.get("diff_bar_pathway_idx", 0)))
        except Exception:
            self._diff_bar_pathway_idx.set(0)
        
        # Determine highest species ID to avoid collisions later
        self._next_species_id = max([s.get("id", 0) for s in self.species] + [0]) + 1
        self._refresh_color_controls()
        self._recompute()

    def _import_project_json(self):
        path = filedialog.askopenfilename(
            parent=self.main_frame.winfo_toplevel(),
            title="Import Project (JSON)",
            initialdir=getattr(self, "_last_opened_dir", self.app_dir),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self._last_opened_dir = os.path.dirname(path)
        import json
        try:
            with open(path, "r", encoding="utf-8") as f:
                snap = json.load(f)
            self._apply_project_snapshot(snap)
            self.project_combo_var.set(self._project_combo_placeholder)
            if hasattr(self, "subproject_combo_var"):
                self.subproject_combo_var.set(self._subproject_combo_placeholder)
            self.proj_save_var.set("")
        except Exception as e:
            messagebox.showerror("Import Error", f"Failed to open JSON:\n{e}")

    def _export_project_json(self):
        snap = self._collect_project_snapshot()
        path = filedialog.asksaveasfilename(
            parent=self.main_frame.winfo_toplevel(),
            title="Export Project (JSON)",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        import json
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snap, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("Exported", f"Successfully exported project to {os.path.basename(path)}!")
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export JSON:\n{e}")

    def _on_flow_motion(self, event):
        z = getattr(self, "_flow_zoom", 1.0)
        x, y = self.flow_canvas.canvasx(event.x), self.flow_canvas.canvasy(event.y)
        hovered = None
        # Tighten vs box+handle size so neighbor drags are not mis-attributed to this node
        pad = max(8, int(10 * z))
        for i, sp in enumerate(s for s in self.species if self._species_on_flow_canvas(s)):
            sx, sy = sp.get("x", 50) * z, sp.get("y", 100) * z
            bw, bh = self._flow_box_dims(sp, z)
            if sx - pad <= x <= sx + bw + pad and sy - pad <= y <= sy + bh + pad:
                hovered = sp["id"]
                break
        if hovered != self._hovered_node:
            self._hovered_node = hovered
            self._queue_flow_draw()

    def _queue_flow_draw(self):
        if getattr(self, "_flow_draw_scheduled", False):
            return
        self._flow_draw_scheduled = True
        canvas = getattr(self, "flow_canvas", None)
        if canvas is None:
            self._flow_draw_scheduled = False
            return
        try:
            canvas.after_idle(self._run_flow_draw)
        except Exception:
            self._flow_draw_scheduled = False
            self._draw_flow()

    def _run_flow_draw(self):
        self._flow_draw_scheduled = False
        self._draw_flow()

    def _capture_flow_state(self):
        return {
            "species": copy.deepcopy(self.species),
            "edge_links": copy.deepcopy(self.edge_links),
            "flow_overlays": copy.deepcopy(self._flow_overlays),
        }

    def _push_flow_undo(self):
        self._flow_undo_stack.append(self._capture_flow_state())
        if len(self._flow_undo_stack) > 60:
            self._flow_undo_stack = self._flow_undo_stack[-60:]

    def _flow_undo(self, _event=None):
        if not self._flow_undo_stack:
            return "break"
        st = self._flow_undo_stack.pop()
        self.species = st.get("species", [])
        self.edge_links = st.get("edge_links", {})
        self._flow_overlays = st.get("flow_overlays", [])
        self._flow_multi_selected = set()
        self._refresh_ref_values()
        self._recompute()
        return "break"

    def _flow_copy_selection(self, _event=None):
        sels = set(getattr(self, "_flow_multi_selected", set()) or set())
        if not sels and self._flow_selection is not None and 0 <= self._flow_selection < len(self.species):
            sid = int(self.species[self._flow_selection].get("id", -1))
            if sid > 0:
                sels = {f"sp:{sid}"}
                self._flow_multi_selected = set(sels)
        if not sels:
            return "break"

        sid_sel = sorted([int(t.split(":", 1)[1]) for t in sels if t.startswith("sp:")])
        ov_sel = sorted([int(t.split(":", 1)[1]) for t in sels if t.startswith("ov:")])
        species_copies = []
        overlays_copies = []
        sid_set = set(sid_sel)
        edge_copies = {}

        for sid in sid_sel:
            sp = next((s for s in self.species if int(s.get("id", -1)) == sid), None)
            if sp:
                species_copies.append(copy.deepcopy(sp))
        for oi in ov_sel:
            if 0 <= oi < len(self._flow_overlays):
                overlays_copies.append(copy.deepcopy(self._flow_overlays[oi]))
        for ek, ev in self.edge_links.items():
            try:
                u, v = map(int, str(ek).split("_"))
            except Exception:
                continue
            if u in sid_set and v in sid_set:
                edge_copies[ek] = copy.deepcopy(ev)

        self._flow_clipboard = {
            "species": species_copies,
            "overlays": overlays_copies,
            "edges": edge_copies,
        }
        self._flow_paste_seq = 0
        return "break"

    def _flow_paste_selection(self, _event=None):
        clip = getattr(self, "_flow_clipboard", None)
        if not isinstance(clip, dict):
            return "break"
        species_clip = clip.get("species", [])
        overlays_clip = clip.get("overlays", [])
        edges_clip = clip.get("edges", {})
        if not species_clip and not overlays_clip:
            return "break"

        self._push_flow_undo()
        self._flow_paste_seq = int(getattr(self, "_flow_paste_seq", 0)) + 1
        raw_dx = 32.0 * self._flow_paste_seq
        raw_dy = 22.0 * self._flow_paste_seq
        dx, dy = self._flow_dup_delta(raw_dx, raw_dy)
        sid_map = {}
        new_sel = set()

        for sp in species_clip:
            cp = copy.deepcopy(sp)
            old_sid = int(cp.get("id", -1))
            cp["id"] = self._alloc_species_id()
            cp["name"] = self._dup_flow_name(cp.get("name", "I_1"))
            cp["x"] = float(cp.get("x", 0.0)) + dx
            cp["y"] = float(cp.get("y", 0.0)) + dy
            self.species.append(cp)
            sid_map[old_sid] = int(cp["id"])
            new_sel.add(f"sp:{int(cp['id'])}")

        for ek, ev in (edges_clip.items() if isinstance(edges_clip, dict) else []):
            try:
                u, v = map(int, str(ek).split("_"))
            except Exception:
                continue
            if u in sid_map and v in sid_map:
                self.edge_links[f"{sid_map[u]}_{sid_map[v]}"] = copy.deepcopy(ev)

        for ov in overlays_clip:
            cp_ov = copy.deepcopy(ov)
            cp_ov["x"] = float(cp_ov.get("x", 0.0)) + dx
            cp_ov["y"] = float(cp_ov.get("y", 0.0)) + dy
            self._flow_overlays.append(cp_ov)
            new_sel.add(f"ov:{len(self._flow_overlays)-1}")

        self._flow_multi_selected = new_sel
        self._refresh_ref_values()
        self._recompute()
        return "break"

    @staticmethod
    def _flow_dup_delta(dx: float, dy: float) -> tuple[float, float]:
        """Scale duplicate drag/paste offsets so the copy never sits on top of the source."""
        try:
            dx, dy = float(dx), float(dy)
        except (TypeError, ValueError):
            dx, dy = 0.0, 0.0
        h = math.hypot(dx, dy)
        if h < 1e-9:
            return (_FLOW_DUP_MIN_SEP * 0.85, _FLOW_DUP_MIN_SEP * 0.55)
        if h < _FLOW_DUP_MIN_SEP:
            s = _FLOW_DUP_MIN_SEP / h
            return (dx * s, dy * s)
        return (dx, dy)

    @staticmethod
    def _dup_flow_name(name: str) -> str:
        nm = str(name or "").strip()
        if not nm:
            return "I_1"
        return nm + "1"

    def _delete_flow_multi_selection(self, _event=None):
        sels = set(getattr(self, "_flow_multi_selected", set()) or set())
        if not sels:
            return
        if not messagebox.askyesno("Delete Selection", f"Delete {len(sels)} selected item(s)?", parent=self._flow_dialog_parent()):
            return
        self._push_flow_undo()
        ov_del = sorted([int(t.split(":", 1)[1]) for t in sels if t.startswith("ov:")], reverse=True)
        for oi in ov_del:
            if 0 <= oi < len(self._flow_overlays):
                self._flow_overlays.pop(oi)
        sid_del = {int(t.split(":", 1)[1]) for t in sels if t.startswith("sp:")}
        if sid_del:
            self.species = [sp for sp in self.species if int(sp.get("id", -1)) not in sid_del]
            self.edge_links = {
                k: v for k, v in self.edge_links.items()
                if not any(int(x) in sid_del for x in str(k).split("_")[:2] if str(x).isdigit())
            }
            self._refresh_ref_values()
            self._recompute()
        self._flow_multi_selected = set()
        self._draw_flow()

    def _on_flow_right_drag(self, event):
        if self._flow_rdrag_group:
            cx, cy = self.flow_canvas.canvasx(event.x), self.flow_canvas.canvasy(event.y)
            dx = (cx - float(self._flow_rdrag_group["startx"])) / self._flow_zoom
            dy = (cy - float(self._flow_rdrag_group["starty"])) / self._flow_zoom
            for tok, (ox, oy) in self._flow_rdrag_group.get("orig", {}).items():
                if tok.startswith("sp:"):
                    sid = int(tok.split(":", 1)[1])
                    sp = next((s for s in self.species if int(s.get("id", -1)) == sid), None)
                    if sp:
                        sp["x"] = ox + dx
                        sp["y"] = oy + dy
                elif tok.startswith("ov:"):
                    oi = int(tok.split(":", 1)[1])
                    if 0 <= oi < len(self._flow_overlays):
                        self._flow_overlays[oi]["x"] = ox + dx
                        self._flow_overlays[oi]["y"] = oy + dy
            self._flow_rsel_moved = True
            self._queue_flow_draw()
            return
        if not self._flow_rsel_start:
            return
        cx, cy = self.flow_canvas.canvasx(event.x), self.flow_canvas.canvasy(event.y)
        x0, y0 = self._flow_rsel_start
        self._flow_rsel_rect = (x0, y0, cx, cy)
        self._flow_rsel_moved = True
        self._queue_flow_draw()

    def _on_flow_right_release(self, event):
        if not self._flow_rsel_start:
            return
        cx, cy = self.flow_canvas.canvasx(event.x), self.flow_canvas.canvasy(event.y)
        x0, y0 = self._flow_rsel_start
        if self._flow_rdrag_copy:
            dx = (cx - x0) / self._flow_zoom
            dy = (cy - y0) / self._flow_zoom
            if abs(dx) > 0.02 or abs(dy) > 0.02:
                self._duplicate_flow_multi_selection(dx, dy)
            self._flow_rdrag_copy = False
            self._flow_rdrag_group = None
            self._flow_rsel_start = None
            self._flow_rsel_rect = None
            self._flow_rsel_moved = False
            self._flow_rc_tags = ()
            self._draw_flow()
            return
        if self._flow_rdrag_group:
            self._flow_rdrag_group = None
            self._flow_rsel_start = None
            self._flow_rsel_rect = None
            self._flow_rsel_moved = False
            self._flow_rc_tags = ()
            self._refresh_ref_values()
            self._recompute()
            return
        was_drag = bool(self._flow_rsel_moved and abs(cx - x0) > 4 and abs(cy - y0) > 4)
        if was_drag:
            x_min, x_max = sorted((x0, cx))
            y_min, y_max = sorted((y0, cy))
            sels = set()
            for sp in (s for s in self.species if self._species_on_flow_canvas(s)):
                sx, sy = sp.get("x", 50) * self._flow_zoom, sp.get("y", 100) * self._flow_zoom
                bw, bh = self._flow_box_dims(sp, self._flow_zoom)
                if not (sx + bw < x_min or sx > x_max or sy + bh < y_min or sy > y_max):
                    sels.add(f"sp:{int(sp.get('id'))}")
            for i, ov in enumerate(self._flow_overlays):
                ox0, oy0, ox1, oy1 = self._flow_overlay_bbox(ov, self._flow_zoom)
                if not (ox1 < x_min or ox0 > x_max or oy1 < y_min or oy0 > y_max):
                    sels.add(f"ov:{i}")
            self._flow_multi_selected = sels
        else:
            tags = tuple(getattr(self, "_flow_rc_tags", ()) or ())
            # Overlay menu
            for t in tags:
                if t.startswith("flow_ov_"):
                    oi = int(t.split("_")[-1])
                    self._flow_multi_selected = {f"ov:{oi}"}
                    self._flow_overlay_menu(event, oi)
                    break
            else:
                # Node context menu
                sid = None
                for t in tags:
                    if t.startswith("sp_"):
                        sid = int(t.split("_", 1)[1])
                        break
                if sid is not None:
                    tok = f"sp:{sid}"
                    prev_sel = set(getattr(self, "_flow_multi_selected", ()) or ())
                    if tok in prev_sel and len(prev_sel) > 1:
                        self._flow_multi_selected = set(prev_sel)
                    else:
                        self._flow_multi_selected = {tok}
                    m = tk.Menu(self._flow_dialog_parent(), tearoff=0)
                    m.add_command(label="Edit Box", command=lambda s=sid: self._edit_flow_box(s))
                    m.add_separator()
                    m.add_command(label="Increase Box Size (+)", command=lambda s=sid: self._flow_ctx_change_box_size(s, 1.1))
                    m.add_command(label="Decrease Box Size (-)", command=lambda s=sid: self._flow_ctx_change_box_size(s, 1/1.1))
                    m.add_separator()
                    
                    idx_for_sid = next((i for i, s in enumerate(self.species) if s["id"] == sid), -1)
                    if idx_for_sid != -1:
                        m.add_command(label="Open Structure / Info (ⓘ)", command=lambda idx=idx_for_sid, ev=event: self._show_path_popup(idx, ev.x_root, ev.y_root))
                        
                        v_menu = tk.Menu(m, tearoff=0)
                        v_menu.add_command(label="ACYView", command=lambda s=sid: self._open_external_3d(s, "ACYView"))
                        v_menu.add_command(label="Chemcraft", command=lambda s=sid: self._open_external_3d(s, "Chemcraft"))
                        v_menu.add_command(label="Jmol", command=lambda s=sid: self._open_external_3d(s, "Jmol"))
                        m.add_cascade(label="Open 3D Viewer", menu=v_menu)

                    if len(self._flow_multi_selected) <= 1:
                        m.add_command(label="Delete Box", command=lambda s=sid: self._delete_flow_box(s))
                    m.add_separator()
                    m.add_command(
                        label="Hide from PES plot (faded on flow)…",
                        command=lambda s=sid: self._flow_ctx_hide_from_pes(s),
                    )
                    restore_ids = self._flow_faded_restore_ids_from_selection(self._flow_multi_selected)
                    if restore_ids:
                        rn = len(restore_ids)
                        if rn == 1:
                            m.add_command(
                                label="Restore to PES plot (unfade on flow)…",
                                command=lambda s=list(restore_ids)[0]: self._flow_ctx_restore_to_pes(s),
                            )
                        else:
                            m.add_command(
                                label=f"Restore {rn} faded (selected) on PES plot",
                                command=lambda ids=set(restore_ids): self._restore_flow_pes_plot_ids(ids),
                            )
                    if len(self._flow_multi_selected) > 1:
                        m.add_separator()
                        m.add_command(label="Delete Selected", command=self._delete_flow_multi_selection)
                    m.tk_popup(event.x_root, event.y_root)
                else:
                    edge_key = next((t.split("flow_edge_")[1] for t in tags if "flow_edge_" in t), None)
                    if edge_key:
                        u, v = edge_key.split("_")
                        id_to_name = {str(sp.get("id")): _sanitize_math_text(sp.get("name", "")) for sp in self.species}
                        name_u = id_to_name.get(u, u)
                        name_v = id_to_name.get(v, v)
                        if messagebox.askyesno("Delete Connection", f"Do you want to permanently delete the connection flow from {name_u} ➔ {name_v}?", parent=self._flow_dialog_parent()):
                            if edge_key in self.edge_links:
                                self._push_flow_undo()
                                del self.edge_links[edge_key]
                                self._recompute()
                    elif self._flow_multi_selected:
                        m = tk.Menu(self._flow_dialog_parent(), tearoff=0)
                        faded_ids = self._flow_faded_restore_ids_from_selection(self._flow_multi_selected)
                        if faded_ids:
                            rn = len(faded_ids)
                            rlabel = (
                                f"Restore {rn} faded (selected) on PES plot"
                                if rn > 1
                                else "Restore faded on PES plot"
                            )
                            m.add_command(
                                label=rlabel,
                                command=lambda ids=set(faded_ids): self._restore_flow_pes_plot_ids(ids),
                            )
                            m.add_separator()
                        
                        m.add_command(label="Increase Box Size (+)", command=lambda: self._flow_ctx_change_box_size(-1, 1.1))
                        m.add_command(label="Decrease Box Size (-)", command=lambda: self._flow_ctx_change_box_size(-1, 1/1.1))
                        m.add_separator()

                        m.add_command(label="Delete Selected", command=self._delete_flow_multi_selection)
                        m.tk_popup(event.x_root, event.y_root)
        self._flow_rsel_start = None
        self._flow_rsel_rect = None
        self._flow_rsel_moved = False
        self._flow_rc_tags = ()
        self._draw_flow()

    def _on_flow_press(self, event):
        c = self.flow_canvas
        try:
            c.focus_set()
        except Exception:
            pass
        cx, cy = c.canvasx(event.x), c.canvasy(event.y)
        tags = c.gettags(c.find_closest(cx, cy))
        ctrl = bool(getattr(event, "state", 0) & 0x4)
        
        # Clicked floating Add button
        if "canvas_add_btn" in tags:
            m = tk.Menu(self._flow_dialog_parent(), tearoff=0)
            m.add_command(label="Add Node", command=lambda: self._quick_add("Intermediate"))
            m.add_command(label="Add Text", command=lambda: self._add_flow_text_overlay(cx, cy))
            m.add_command(label="Add Image", command=lambda: self._add_flow_image_overlay(cx, cy))
            sm = tk.Menu(m, tearoff=0)
            sm.add_command(label="Circle", command=lambda: self._add_flow_shape_overlay(cx, cy, "circle"))
            sm.add_command(label="Rectangle", command=lambda: self._add_flow_shape_overlay(cx, cy, "rectangle"))
            sm.add_command(label="Other (Diamond)", command=lambda: self._add_flow_shape_overlay(cx, cy, "diamond"))
            m.add_cascade(label="Add Shape", menu=sm)
            m.tk_popup(event.x_root, event.y_root)
            return

        best_h = None
        best_d2 = None
        for sid, hx, hy, hr in getattr(self, "_flow_handle_hit", ()) or ():
            d2 = (cx - hx) ** 2 + (cy - hy) ** 2
            if d2 <= hr * hr and (best_d2 is None or d2 < best_d2):
                best_h, best_d2 = int(sid), d2
        if best_h is not None:
            self._drag_state = {"type": "edge", "source": best_h, "x": cx, "y": cy}
            return

        for t in tags:
            if t.startswith("flow_ov_"):
                oi = int(t.split("_")[-1])
                if 0 <= oi < len(self._flow_overlays):
                    if ctrl:
                        self._push_flow_undo()
                        ov0 = dict(self._flow_overlays[oi])
                        ov0["x"] = float(ov0.get("x", 0.0)) + 0.4
                        ov0["y"] = float(ov0.get("y", 0.0)) + 0.2
                        self._flow_overlays.append(ov0)
                        oi = len(self._flow_overlays) - 1
                    ov = self._flow_overlays[oi]
                    if not ctrl:
                        self._push_flow_undo()
                    if f"ov:{oi}" in self._flow_multi_selected and len(self._flow_multi_selected) > 1:
                        origin = {}
                        for tok in self._flow_multi_selected:
                            if tok.startswith("sp:"):
                                sid = int(tok.split(":", 1)[1])
                                sp = next((s for s in self.species if int(s.get("id", -1)) == sid), None)
                                if sp:
                                    origin[tok] = (float(sp.get("x", 0.0)), float(sp.get("y", 0.0)))
                            elif tok.startswith("ov:"):
                                oj = int(tok.split(":", 1)[1])
                                if 0 <= oj < len(self._flow_overlays):
                                    ovj = self._flow_overlays[oj]
                                    origin[tok] = (float(ovj.get("x", 0.0)), float(ovj.get("y", 0.0)))
                        self._drag_state = {"type": "group", "startx": cx, "starty": cy, "orig": origin}
                    else:
                        self._flow_multi_selected = {f"ov:{oi}"}
                        self._drag_state = {"type": "overlay", "idx": oi, "offx": cx - float(ov.get("x", 0.0)) * self._flow_zoom, "offy": cy - float(ov.get("y", 0.0)) * self._flow_zoom}
                    return
            
        # Handle toggling the chat-like popup bubbles
        if not hasattr(self, "_bubble_states"):
            self._bubble_states = {}
            
        for t in tags:
            if t.startswith("expand_"):
                key = t.split("_", 1)[1]
                self._bubble_states[key] = True
                self._draw_flow()
                return
            elif t.startswith("hide_"):
                key = t.split("_", 1)[1]
                self._bubble_states[key] = False
                self._draw_flow()
                return
        
        # Clicked add/remove normal molecules on expanded badge
        for t in tags:
            if t.startswith("add_") or t.startswith("rem_"):
                key = t.split("_", 1)[1]
                mode = "add" if t.startswith("add_") else "remove"
                self._pick_normals_for_edge(key, mode)
                return
            elif t.startswith("clear_"):
                key = t.split("_", 1)[1]
                self._clear_normals_for_edge(key)
                return
                
        # Clicked a node handle
        for t in tags:
            if t.startswith("handle_"):
                sid = int(t.split("_")[1])
                self._drag_state = {"type": "edge", "source": sid, "x": cx, "y": cy}
                return
                
        # Clicked a node itself
        for t in tags:
            if t.startswith("sp_"):
                sid = int(t.split("_")[1])
                sp = next(s for s in self.species if s["id"] == sid)
                if ctrl:
                    self._push_flow_undo()
                    clone = dict(sp)
                    clone["id"] = self._alloc_species_id()
                    clone["name"] = self._dup_flow_name(clone.get("name", "I_1"))
                    odx, ody = self._flow_dup_delta(0.0, 0.0)
                    clone["x"] = float(clone.get("x", 0.0)) + odx
                    clone["y"] = float(clone.get("y", 0.0)) + ody
                    self.species.append(clone)
                    sid = clone["id"]
                    sp = clone
                    self._refresh_ref_values()
                    self._recompute()
                else:
                    self._push_flow_undo()
                if f"sp:{sid}" in self._flow_multi_selected and len(self._flow_multi_selected) > 1:
                    origin = {}
                    for tok in self._flow_multi_selected:
                        if tok.startswith("sp:"):
                            sj = int(tok.split(":", 1)[1])
                            spj = next((s for s in self.species if int(s.get("id", -1)) == sj), None)
                            if spj:
                                origin[tok] = (float(spj.get("x", 0.0)), float(spj.get("y", 0.0)))
                        elif tok.startswith("ov:"):
                            oj = int(tok.split(":", 1)[1])
                            if 0 <= oj < len(self._flow_overlays):
                                ovj = self._flow_overlays[oj]
                                origin[tok] = (float(ovj.get("x", 0.0)), float(ovj.get("y", 0.0)))
                    self._drag_state = {"type": "group", "startx": cx, "starty": cy, "orig": origin}
                else:
                    self._flow_multi_selected = {f"sp:{sid}"}
                    self._drag_state = {"type": "node", "id": sid, "offx": cx - sp.get("x",0)*self._flow_zoom, "offy": cy - sp.get("y",0)*self._flow_zoom}
                # Select in table
                idx = self.species.index(sp)
                iid = f"sp_{idx}"
                if iid in self.tree.get_children():
                    self.tree.selection_set(iid)
                    self.tree.see(iid)
                    self._set_flow_selection(idx)
                return
        if self._flow_multi_selected:
            self._flow_multi_selected = set()
            self._set_flow_selection(None)
            self._draw_flow()

    def _on_flow_drag(self, event):
        if not self._drag_state: return
        cx, cy = self.flow_canvas.canvasx(event.x), self.flow_canvas.canvasy(event.y)
        if self._drag_state["type"] == "node":
            sp = next(s for s in self.species if s["id"] == self._drag_state["id"])
            sp["x"] = (cx - self._drag_state["offx"]) / self._flow_zoom
            sp["y"] = (cy - self._drag_state["offy"]) / self._flow_zoom
            self._queue_flow_draw()
        elif self._drag_state["type"] == "edge":
            self._drag_edge = (self._drag_state["x"], self._drag_state["y"], cx, cy)
            self._queue_flow_draw()
        elif self._drag_state["type"] == "overlay":
            oi = int(self._drag_state["idx"])
            if 0 <= oi < len(self._flow_overlays):
                self._flow_overlays[oi]["x"] = (cx - self._drag_state["offx"]) / self._flow_zoom
                self._flow_overlays[oi]["y"] = (cy - self._drag_state["offy"]) / self._flow_zoom
                self._queue_flow_draw()
        elif self._drag_state["type"] == "group":
            dx = (cx - float(self._drag_state["startx"])) / self._flow_zoom
            dy = (cy - float(self._drag_state["starty"])) / self._flow_zoom
            for tok, (ox, oy) in self._drag_state.get("orig", {}).items():
                if tok.startswith("sp:"):
                    sid = int(tok.split(":", 1)[1])
                    sp = next((s for s in self.species if int(s.get("id", -1)) == sid), None)
                    if sp:
                        sp["x"] = ox + dx
                        sp["y"] = oy + dy
                elif tok.startswith("ov:"):
                    oi = int(tok.split(":", 1)[1])
                    if 0 <= oi < len(self._flow_overlays):
                        self._flow_overlays[oi]["x"] = ox + dx
                        self._flow_overlays[oi]["y"] = oy + dy
            self._queue_flow_draw()

    def _on_flow_release(self, event):
        cx, cy = self.flow_canvas.canvasx(event.x), self.flow_canvas.canvasy(event.y)
        if self._drag_state and self._drag_state["type"] == "edge":
            source_id = self._drag_state["source"]
            dropped_on = None
            for sp in (s for s in self.species if self._species_on_flow_canvas(s)):
                sx, sy = sp.get("x",50)*self._flow_zoom, sp.get("y",100)*self._flow_zoom
                bw, bh = self._flow_box_dims(sp, self._flow_zoom)
                if sx <= cx <= sx+bw and sy <= cy <= sy+bh:
                    dropped_on = sp["id"]
                    break
            if dropped_on and dropped_on != source_id:
                edge_key = f"{source_id}_{dropped_on}"
                if edge_key not in self.edge_links:
                    self._push_flow_undo()
                    self.edge_links[edge_key] = {"add": {}, "remove": {}}
                self._recompute()
            elif not dropped_on:
                # Dropped in empty space: create new connected node
                new_sp = self._species_dialog(None)
                if new_sp:
                    self._push_flow_undo()
                    new_sp["id"] = self._alloc_species_id()
                    new_sp["x"] = cx / self._flow_zoom
                    new_sp["y"] = cy / self._flow_zoom
                    self.species.append(new_sp)
                    edge_key = f"{source_id}_{new_sp['id']}"
                    self.edge_links[edge_key] = {"add": {}, "remove": {}}
                    self._refresh_ref_values()
                    self._recompute()
        self._drag_state = None
        self._drag_edge = None
        self._draw_flow()

    def _set_flow_selection(self, idx: int | None):
        self._flow_selection = idx

    def _check_species_violations(self, sp) -> list[str]:
        reasons = []
        if sp.get("normal_term") == "No":
            reasons.append("Not normal termination.")
        kind = sp.get("kind", "")
        imag = sp.get("imag_modes")
        if imag is not None:
            try:
                i_val = int(imag)
                if kind == "TS" and i_val != 1:
                    reasons.append(f"TS must have 1 imaginary mode (found {i_val}).")
                elif kind != "TS" and i_val > 0:
                    reasons.append(f"Non-TS must have 0 imaginary modes (found {i_val}).")
            except ValueError: pass
        s2_dev = sp.get("s2_dev")
        if s2_dev is not None:
            try:
                if abs(float(s2_dev)) > 0.5:
                    reasons.append(f"Spin deviation > 0.5 (found {s2_dev}).")
            except ValueError: pass
        return reasons

    def _edit_flow_box(self, sid: int):
        idx = next((i for i, s in enumerate(self.species) if int(s.get("id", -1)) == int(sid)), None)
        if idx is None:
            return
        row = self._species_dialog(self.species[idx])
        if row:
            self._push_flow_undo()
            self.species[idx].update(row)
            self._refresh_ref_values()
            self._recompute()
            reasons = self._check_species_violations(self.species[idx])
            if reasons:
                messagebox.showwarning("Validation Warning", f"Violations found for {self.species[idx].get('name', '')}:\n" + "\n".join(reasons))

    def _delete_flow_box(self, sid: int):
        sp = next((s for s in self.species if int(s.get("id", -1)) == int(sid)), None)
        if not sp:
            return
        if not messagebox.askyesno("Delete Box", f"Delete '{sp.get('name', sid)}'?", parent=self._flow_dialog_parent()):
            return
        self._push_flow_undo()
        self.species = [s for s in self.species if int(s.get("id", -1)) != int(sid)]
        self.edge_links = {
            k: v for k, v in self.edge_links.items()
            if not any(int(x) == int(sid) for x in str(k).split("_")[:2] if str(x).isdigit())
        }
        self._refresh_ref_values()
        self._recompute()

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            self._set_flow_selection(None)
            return
        iid = sel[0]
        if not iid.startswith("sp_"):
            self._set_flow_selection(None)
            return
        idx = int(iid.split("_", 1)[1])
        self._set_flow_selection(idx)

    def _clear_normals_for_edge(self, edge_key: str):
        if not messagebox.askyesno("Confirm Delete", "Are you sure you want to delete all added and removed molecules for this step?", parent=self.main_frame.winfo_toplevel()):
            return
        if edge_key in self.edge_links:
            self.edge_links[edge_key] = {"add": {}, "remove": {}}
        try:
            src, dst = map(int, str(edge_key).split("_"))
            for sp in self.species:
                if sp.get("id") == dst:
                    sp["added_text"] = ""
                    sp["removed_text"] = ""
                    break
        except Exception:
            pass
        self._recompute()
        self._draw_flow()

    def _pick_normals_for_edge(self, edge_key: str, mode: str):
        normals_info = [(i, s) for i, s in enumerate(self.species) if not s.get("plot", True)]
        if not normals_info:
            messagebox.showinfo("PES Plot", "Add Normal molecule rows first.")
            return
        top = tk.Toplevel(self.main_frame.winfo_toplevel())
        top.title("Select normal molecules")
        top.geometry("520x360")
        body = ttk.Frame(top, padding=10)
        body.pack(fill=tk.BOTH, expand=True)
        lbl_text = f"Choose normal molecules to {mode} on connection {edge_key.replace('_', ' to ')}:"
        ttk.Label(body, text=lbl_text).pack(anchor="w", pady=(0, 8))
        
        main_content = ttk.Frame(body)
        main_content.pack(fill=tk.BOTH, expand=True)
        
        lb_frame = ttk.Frame(main_content)
        lb_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lb = tk.Listbox(lb_frame, selectmode=tk.MULTIPLE)
        lb.pack(fill=tk.BOTH, expand=True)
        
        btn_frame = ttk.Frame(main_content)
        btn_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        
        normal_ids = []
        for real_idx, n in normals_info:
            sid = n.get("id")
            normal_ids.append(sid)
            lb.insert(tk.END, n.get("name", ""))
            
        existing = set(self.edge_links.get(edge_key, {}).get("add" if mode == "add" else "remove", []))
        for i, sid in enumerate(normal_ids):
            if sid in existing:
                lb.selection_set(i)

        def _edit_mo():
            sel = lb.curselection()
            if not sel: return
            i = sel[0]
            real_idx = normals_info[i][0]
            row = self._species_dialog(self.species[real_idx])
            if row:
                self.species[real_idx].update(row)
                self._refresh_ref_values()
                self._recompute()
                top.destroy()
                self._pick_normals_for_edge(edge_key, mode)

        def _delete_mo():
            sel = lb.curselection()
            if not sel: return
            if not messagebox.askyesno("Confirm", "Delete this normal molecule completely from the project?"):
                return
            i = sel[0]
            real_idx = normals_info[i][0]
            del self.species[real_idx]
            self._refresh_ref_values()
            self._recompute()
            top.destroy()
            self._pick_normals_for_edge(edge_key, mode)

        ttk.Button(btn_frame, text="Edit MO", command=_edit_mo).pack(fill=tk.X, pady=(0, 5))
        ttk.Button(btn_frame, text="Delete Completely", command=_delete_mo).pack(fill=tk.X, pady=(0, 5))

        def _save():
            picked = {}
            for i in lb.curselection():
                if not (0 <= i < len(normal_ids)):
                    continue
                sid = normal_ids[i]
                nm = normals_info[i][1].get("name", f"ID {sid}")
                prev = self.edge_links.get(edge_key, {}).get("add" if mode == "add" else "remove", {}).get(sid, 1.0)
                coeff = simpledialog.askfloat(
                    "Stoichiometry",
                    f"Stoichiometry for '{nm}' on this step:",
                    parent=top,
                    minvalue=0.0,
                    initialvalue=float(prev),
                )
                if coeff is None:
                    continue
                if coeff > 0:
                    picked[sid] = float(coeff)
            if edge_key not in self.edge_links:
                self.edge_links[edge_key] = {"add": {}, "remove": {}}
            self.edge_links[edge_key]["add" if mode == "add" else "remove"] = picked
            top.destroy()
            self._recompute()

        bar = ttk.Frame(body)
        bar.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bar, text="Cancel", command=top.destroy).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Save Selection", command=_save).pack(side=tk.RIGHT, padx=(0, 8))
        
        top.transient(self.main_frame.winfo_toplevel())
        top.grab_set()
        top.wait_window()

    def _on_tree_press(self, event):
        iid = self.tree.identify_row(event.y)
        self._last_tree_col = self.tree.identify_column(event.x)
        self._hide_tree_drop_line()
        if iid and iid.startswith("sp_") and self._last_tree_col == "#1":
            self._drag_iid = iid
            self._tree_drag_over_iid = iid
            self._tree_drag_start_y = event.y
            self._tree_drag_active = False
        else:
            self._drag_iid = None
            self._tree_drag_over_iid = None
            self._tree_drag_start_y = None
            self._tree_drag_active = False

    def _hide_tree_drop_line(self):
        try:
            if getattr(self, "_tree_drop_line", None):
                self._tree_drop_line.place_forget()
        except Exception:
            pass

    def _show_tree_drop_line(self, y_local: float):
        try:
            ln = getattr(self, "_tree_drop_line", None)
            if not ln:
                return
            y = max(0, int(y_local))
            ln.place(x=0, y=y, relwidth=1.0, height=2)
            ln.lift()
        except Exception:
            pass

    def _tree_row_from_y(self, y: int):
        iid = self.tree.identify_row(y)
        if iid and iid.startswith("sp_"):
            return iid
        kids = [k for k in self.tree.get_children("") if str(k).startswith("sp_")]
        if not kids:
            return None
        # Fallback: nearest visible row center by y
        best_iid = None
        best_d = None
        for k in kids:
            bb = self.tree.bbox(k)
            if not bb:
                continue
            ky = bb[1] + bb[3] / 2
            d = abs(float(y) - float(ky))
            if best_d is None or d < best_d:
                best_d = d
                best_iid = k
        return best_iid

    def _on_tree_drag(self, event):
        if not self._drag_iid:
            return
        if self._tree_drag_start_y is not None and not self._tree_drag_active:
            if abs(int(event.y) - int(self._tree_drag_start_y)) < 4:
                return
            self._tree_drag_active = True
        over = self._tree_row_from_y(event.y)
        if over and over.startswith("sp_"):
            self._tree_drag_over_iid = over
            try:
                # Keep target visible while dragging without constantly
                # changing selected row (feels jumpy).
                self.tree.see(over)
            except Exception:
                pass
        bb = self.tree.bbox(over)
        if bb:
            row_mid = bb[1] + bb[3] / 2
            line_y = bb[1] if event.y <= row_mid else (bb[1] + bb[3])
            self._tree_drop_line_y = line_y
            self._show_tree_drop_line(line_y)

    def _on_tree_double_click(self, event):
        self._start_inline_edit(event)

    def _start_inline_edit(self, event, iid=None, col=None):
        if iid is None:
            iid = self.tree.identify_row(event.y)
        if col is None:
            col = self.tree.identify_column(event.x)
        if not iid or not iid.startswith("sp_") or not col:
            return
            
        if col == "#0":
            key = "name"
            abs_col_idx = 0
        else:
            col_num = int(col.replace("#", "")) - 1
            display_cols = self.tree.cget("displaycolumns")
            
            if not display_cols or display_cols == "#all":
                real_cols = self._tree_cols
            else:
                real_cols = display_cols
                
            if col_num < 0 or col_num >= len(real_cols):
                return
                
            key = real_cols[col_num]
            abs_col_idx = self._tree_cols.index(key) if key in self._tree_cols else -1
        
        editable = {"name", "kind", "plot", "main_out", "sp_out", "image_path", "stoich", "main_e", "enthalpy", "gibbs", "thermal", "sp_e", "g_corr"}
        if key not in editable:
            return
            
        tree_widget = self.tree_fixed if col == "#0" else self.tree
        bbox = tree_widget.bbox(iid, col)
        if not bbox:
            return
            
        x, y, w, h = bbox
        idx = int(iid.split("_", 1)[1])
        cur = self.species[idx].get(key, "")
        
        old_vals = list(self.tree.item(iid, "values"))
        if 0 <= abs_col_idx < len(old_vals) and (cur == "" or cur is None):
            cur = old_vals[abs_col_idx]

        if key in {"kind", "plot"}:
            values = ["Intermediate", "TS", "Normal"] if key == "kind" else ["Yes", "No"]
            editor = ttk.Combobox(tree_widget, values=values, state="readonly")
            editor.set(str(cur if cur != "" else values[0]))
        else:
            editor = ttk.Entry(tree_widget)
            editor.insert(0, str(cur))
        editor.place(x=x, y=y, width=w, height=h)
        editor.focus_set()

        def _commit(_e=None):
            val = editor.get().strip()
            editor.destroy()
            self._set_cell_value(idx, key, val)
            self._refresh_ref_values()
            self._recompute()
            reasons = self._check_species_violations(self.species[idx])
            if reasons:
                messagebox.showwarning("Validation Warning", f"Violations found for {self.species[idx].get('name', '')}:\n" + "\n".join(reasons))

        def _cancel(_e=None):
            editor.destroy()

        editor.bind("<Return>", _commit)
        editor.bind("<Escape>", _cancel)
        editor.bind("<FocusOut>", _commit)

    def _set_cell_value(self, idx: int, key: str, raw: str):
        if not (0 <= idx < len(self.species)):
            return
        sp = self.species[idx]
        if key in ("main_out", "sp_out") and sp.get(key) != raw:
            sp["_overrides"] = {}
            
        if key == "plot":
            sp["plot"] = str(raw).strip().lower() in {"yes", "y", "1", "true"}
            return
        if key == "kind":
            sp["kind"] = raw or sp.get("kind", "Intermediate")
            if sp["kind"] == "Normal":
                sp["plot"] = False
            elif sp["kind"] in {"Intermediate", "TS"}:
                sp["plot"] = True
            return
        if key == "stoich":
            sp["stoich"] = self._parse_num_or_fraction(raw, default=1.0)
            return
        if key in {"main_e", "enthalpy", "gibbs", "thermal", "sp_e", "g_corr", "h_corr", "e_corr", "s2", "s2_dev"}:
            sp[key] = self._parse_num_or_fraction(raw, default=sp.get(key) if sp.get(key) is not None else 0.0)
            if "_overrides" not in sp:
                sp["_overrides"] = {}
            sp["_overrides"][key] = True
            return
        sp[key] = raw

    def _on_tree_copy(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return "break"
            
        display_cols = self.tree.cget("displaycolumns")
        if not display_cols or display_cols == "#all":
            col_indices = list(range(len(self._tree_cols)))
        else:
            col_indices = [self._tree_cols.index(c) for c in display_cols]
            
        rows = []
        headers = [self.tree.heading(self._tree_cols[idx])["text"] for idx in col_indices]
        rows.append("\t".join(headers))
        
        for iid in sel:
            vals = self.tree.item(iid, "values")
            visible_vals = [str(vals[idx]) for idx in col_indices]
            rows.append("\t".join(visible_vals))
            
        txt = "\n".join(rows)
        self.tree.clipboard_clear()
        self.tree.clipboard_append(txt)
        return "break"

    def _on_tree_paste(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return "break"
        iid0 = sel[0]
        if not iid0.startswith("sp_"):
            return "break"
        try:
            clip = self.tree.clipboard_get()
        except Exception:
            return "break"
        lines = [ln for ln in clip.splitlines() if ln.strip() != ""]
        if not lines:
            return "break"
        start_idx = int(iid0.split("_", 1)[1])
        start_col = 0
        if self._last_tree_col and self._last_tree_col.startswith("#"):
            try:
                start_col = max(0, int(self._last_tree_col[1:]) - 1)
            except Exception:
                start_col = 0
        for r_off, line in enumerate(lines):
            ridx = start_idx + r_off
            if ridx >= len(self.species):
                break
            cells = line.split("\t")
            for c_off, val in enumerate(cells):
                cidx = start_col + c_off
                if cidx >= len(self._tree_cols):
                    break
                key = self._tree_cols[cidx]
                if key == "rel_kcal":
                    continue
                self._set_cell_value(ridx, key, val)
        self._refresh_ref_values()
        self._recompute()
        return "break"

    def _show_path_popup(self, idx, rx, ry):
        sp = self.species[idx]
        paths = []
        if sp.get("main_out"): paths.append(("Main:", sp["main_out"]))
        if sp.get("sp_out"): paths.append(("SP/Solv:", sp["sp_out"]))
        if sp.get("image_path"): paths.append(("Image:", sp["image_path"]))
        if not paths:
            return
            
        top = tk.Toplevel(self.tree)
        top.title("File Paths")
        top.geometry(f"+{rx}+{ry}")
        top.attributes("-topmost", True)
        
        frm = ttk.Frame(top, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)
        
        for label, path in paths:
            row = ttk.Frame(frm)
            row.pack(fill=tk.X, pady=4)
            ttk.Label(row, text=label, width=10).pack(side=tk.LEFT)
            e = ttk.Entry(row, width=60)
            e.insert(0, path)
            e.configure(state="readonly")
            e.pack(side=tk.LEFT, padx=5)
            
            def open_loc(p=path):
                import os, subprocess
                if p.lower().endswith(".out"):
                    p_base = p[:-4]
                    if os.path.exists(p_base) and os.path.isdir(p_base):
                        os.startfile(p_base)
                        return
                
                if os.path.exists(p):
                    subprocess.run(["explorer", "/select,", os.path.normpath(p)])
                else:
                    d = os.path.dirname(p)
                    if os.path.exists(d):
                        os.startfile(d)
                        
            ttk.Button(row, text="Open", command=open_loc, width=8).pack(side=tk.LEFT)
            
        ttk.Button(frm, text="Close", command=top.destroy).pack(pady=(10, 0))

    def _on_tree_release(self, event):
        if not self._drag_iid:
            return
        dst_iid = self._tree_drag_over_iid or self._tree_row_from_y(event.y)
        src_iid = self._drag_iid
        was_active_drag = bool(self._tree_drag_active)
        self._drag_iid = None
        self._tree_drag_over_iid = None
        self._tree_drag_start_y = None
        self._tree_drag_active = False
        self._hide_tree_drop_line()
        
        if not was_active_drag:
            has_modifiers = (event.state & 0x0001) or (event.state & 0x0004)
            if dst_iid and dst_iid.startswith("sp_") and not has_modifiers:
                col = self.tree.identify_column(event.x)
                if col == "#1":
                    idx = int(dst_iid.split("_", 1)[1])
                    sp = self.species[idx]
                    if sp.get("main_out") or sp.get("sp_out") or sp.get("image_path"):
                        self._show_path_popup(idx, event.x_root, event.y_root)
                elif col:
                    idx = int(dst_iid.split("_", 1)[1])
                    sp = self.species[idx]
                    tags = self.tree.item(dst_iid, "tags")
                    if "error_row" in tags:
                        if col == "#5" and sp.get("normal_term") == "No":
                            messagebox.showwarning("Validation Reason", "Not normal termination.")
                            return
                        elif col == "#6":
                            kind = sp.get("kind", "")
                            imag = sp.get("imag_modes")
                            if imag is not None:
                                try:
                                    i_val = int(imag)
                                    if kind == "TS" and i_val != 1:
                                        messagebox.showwarning("Validation Reason", f"TS must have 1 imaginary mode (found {i_val}).")
                                        return
                                    elif kind != "TS" and i_val > 0:
                                        messagebox.showwarning("Validation Reason", f"Non-TS must have 0 imaginary modes (found {i_val}).")
                                        return
                                except ValueError: pass
                        elif col == "#13":
                            s2_dev = sp.get("s2_dev")
                            if s2_dev is not None:
                                try:
                                    if abs(float(s2_dev)) > 0.5:
                                        messagebox.showwarning("Validation Reason", f"Spin deviation > 0.5 (found {s2_dev}).")
                                        return
                                except ValueError: pass
                    self._start_inline_edit(event, dst_iid, col)
            return

        if not dst_iid or not dst_iid.startswith("sp_"):
            return
        src_idx = int(src_iid.split("_", 1)[1])
        dst_idx = int(dst_iid.split("_", 1)[1])
        if not (0 <= src_idx < len(self.species) and 0 <= dst_idx < len(self.species)):
            return
            
        src_is_normal = (self.species[src_idx].get("kind") == "Normal")
        dst_is_normal = (self.species[dst_idx].get("kind") == "Normal")
        if src_is_normal != dst_is_normal:
            return
            
        # Intuitive drop: upper half inserts above, lower half below.
        bb = self.tree.bbox(dst_iid)
        if bb:
            row_mid = bb[1] + bb[3] / 2
            if event.y > row_mid:
                dst_idx += 1
        if src_idx == dst_idx or src_idx + 1 == dst_idx:
            return
        row = self.species.pop(src_idx)
        if src_idx < dst_idx:
            dst_idx -= 1
        dst_idx = max(0, min(len(self.species), dst_idx))
        self.species.insert(dst_idx, row)
        self._refresh_ref_values()
        self._recompute()
        try:
            new_iid = f"sp_{dst_idx}"
            if new_iid in self.tree.get_children(""):
                self.tree.selection_set(new_iid)
                self.tree.see(new_iid)
        except Exception:
            pass

    def _on_tree_hover(self, event):
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if item and col == "#1" and item.startswith("sp_"):
            idx = int(item.split("_")[1])
            if 0 <= idx < len(self.species):
                sp = self.species[idx]
                paths = []
                if sp.get("main_out"): paths.append(f"Main: {sp['main_out']}")
                if sp.get("sp_out"): paths.append(f"SP/Solv: {sp['sp_out']}")
                if sp.get("image_path"): paths.append(f"Image: {sp['image_path']}")
                if paths:
                    # Hover disabled per user request: only click should open.
                    return
        self._hide_tree_tooltip()

    def _show_tree_tooltip(self, x, y, text):
        self._hide_tree_tooltip()
        self._last_tooltip_text = text
        self._tree_tooltip = tk.Toplevel(self.tree)
        self._tree_tooltip.wm_overrideredirect(True)
        self._tree_tooltip.wm_geometry(f"+{x}+{y}")
        self._tree_tooltip.configure(bg="#ffffe0")
        lbl = tk.Label(self._tree_tooltip, text=text, bg="#ffffe0", fg="#000000", borderwidth=1, relief="solid", justify="left")
        lbl.pack(ipadx=4, ipady=2)
        
    def _hide_tree_tooltip(self):
        self._last_tooltip_text = None
        if getattr(self, "_tree_tooltip", None):
            self._tree_tooltip.destroy()
            self._tree_tooltip = None

    def _render_plot(self):
        basis = (self._plot_energy_basis.get() or "G").strip().upper()
        if basis == "E":
            rel_key = "rel_e_energy"
            axis_kind = "E"
            default_axis = "Relative Electronic Energy"
        try:
            import traceback
            self._render_plot_internal()
        except Exception as e:
            messagebox.showerror("Plot Error", f"Failed to render plot:\n{e}\n\n{traceback.format_exc()}")
            
    def _render_plot_internal(self):
        if not _HAS_MPL:
            messagebox.showinfo("PES Plot", "matplotlib not available (`pip install matplotlib`).")
            return
            
        basis = (self._plot_energy_basis.get() or "G").strip().upper()
        if basis == "E":
            rel_key = "rel_e_energy"
            axis_kind = "E"
            default_axis = "Relative Electronic Energy"
        elif basis == "H":
            rel_key = "rel_h_energy"
            axis_kind = "H"
            default_axis = "Relative Enthalpy"
        else:
            rel_key = "rel_energy"
            axis_kind = "G"
            default_axis = "Relative Gibbs Energy"
            
        valid = [sp for sp in self.species if sp.get("plot", True) and sp.get(rel_key) is not None]
        if len(valid) < 2:
            messagebox.showinfo("PES Plot", f"Need at least 2 plotted species with valid relative {axis_kind} energies to plot.")
            return

        # New figure invalidates any in-progress drag ghost reference
        self._plot_asset_ghost_ab = None
        self._plot_asset_ghost_for_ai = None
        self._clear_plot_asset_ghost_buffer()
        self._ghost_axis = None
        self._ccc_axis_drag_x = None
        self._aks_axis_dragging = False

        # DAG Plot Traversal
        style_choice = (self._plot_style.get() or "CCC: 1 Curved").strip().lower()
        is_sequential = "sequential" in style_choice
        
        if "aks style" in style_choice:
            shape = "aks_style"
            style = "aks"
        elif "classic 2" in style_choice:
            shape = "classic 2"
            style = "classical"
        elif "classic 1" in style_choice or ("classic" in style_choice and not is_sequential):
            shape = "classic 1"
            style = "classical"
        else:
            shape = "ccl_view_straight" if "straight" in style_choice else "ccl_view_curved"
            style = "ccc"

        # Build DAG paths
        adj_fwd = {s["id"]: [] for s in valid}
        for e_key in self.edge_links:
            try:
                src, dst = map(int, str(e_key).split("_"))
                if src in adj_fwd and dst in adj_fwd:
                    adj_fwd[src].append(dst)
            except: pass
            
        def find_paths(u, current_path):
            if not adj_fwd[u]: return [current_path]
            res = []
            for v in adj_fwd[u]:
                if v in current_path: continue  # prevent infinite recursion on cycles
                res.extend(find_paths(v, current_path + [v]))
            return res
            
        # Find all roots (indegree 0)
        indegree = {s["id"]: 0 for s in valid}
        for u in adj_fwd:
            for v in adj_fwd[u]:
                indegree[v] += 1
        roots = [u for u, deg in indegree.items() if deg == 0]
        if not roots: roots = [valid[0]["id"]]
        
        all_paths = []
        for r in roots:
            all_paths.extend(find_paths(r, [r]))
            
        render_species = list(self.species)
        render_edges = dict(self.edge_links)
        
        if is_sequential:
            new_valid = []
            new_edge_links = {}
            new_all_paths = []
            virt_id = 9000000
            for p in all_paths:
                new_p = []
                for seq_idx, u in enumerate(p):
                    orig_sp = next((s for s in valid if s["id"] == u), None)
                    if orig_sp:
                        cloned = dict(orig_sp)
                        cloned["id"] = virt_id
                        cloned["_orig_id"] = u
                        new_valid.append(cloned)
                        new_p.append(virt_id)
                        
                        if seq_idx > 0:
                            orig_u = p[seq_idx - 1]
                            orig_edge_key = f"{orig_u}_{u}"
                            new_edge_key = f"{new_p[seq_idx - 1]}_{virt_id}"
                            if orig_edge_key in self.edge_links:
                                new_edge_links[new_edge_key] = dict(self.edge_links[orig_edge_key])
                        virt_id += 1
                if new_p:
                    new_all_paths.append(new_p)
            
            valid = new_valid
            render_species = new_valid
            render_edges = new_edge_links
            all_paths = new_all_paths
            
        color_mode = (self._plot_colors_mode.get() or "Gradient").strip()
        path_colors_map = {}
        if len(all_paths) > 1:
            path_max_energies = []
            for p in all_paths:
                mE = max([next((s.get(rel_key, 0.0) or 0.0 for s in valid if s["id"] == idx), 0.0) for idx in p])
                path_max_energies.append((mE, p))
            if color_mode == "Normal":
                # Lower-energy pathway is green, upper-energy pathway is red.
                # Any middle pathways run yellowish -> orangish.
                path_max_energies.sort(key=lambda x: x[0])  # low -> high
                n = len(path_max_energies)
                if n == 2:
                    assigned = ["#16a34a", "#dc2626"]
                else:
                    assigned = []
                    for i in range(n):
                        if i == 0:
                            assigned.append("#16a34a")
                        elif i == n - 1:
                            assigned.append("#dc2626")
                        else:
                            t = i / float(n - 1)
                            if t <= 0.5:
                                assigned.append("#facc15")  # yellow-ish
                            else:
                                assigned.append("#f59e0b")  # orange-ish
                for i, (_mE, p) in enumerate(path_max_energies):
                    c = assigned[i]
                    for k in range(len(p) - 1):
                        path_colors_map[f"{p[k]}_{p[k+1]}"] = c
            elif color_mode == "Black":
                for _i, (_mE, p) in enumerate(path_max_energies):
                    for k in range(len(p) - 1):
                        path_colors_map[f"{p[k]}_{p[k+1]}"] = "#111827"
            else:
                path_max_energies.sort(key=lambda x: x[0], reverse=True)
                palette = ["#dc2626", "#f59e0b", "#16a34a", "#2563eb", "#9333ea", "#0891b2"]
                for i, (_mE, p) in enumerate(path_max_energies):
                    c = palette[i % len(palette)]
                    for k in range(len(p)-1):
                        # highest energy paths overwrite lower trunks implicitly
                        path_colors_map[f"{p[k]}_{p[k+1]}"] = c
        elif len(all_paths) == 1:
            for k in range(len(all_paths[0])-1):
                path_colors_map[f"{all_paths[0][k]}_{all_paths[0][k+1]}"] = "#111827"

        # Compute pathway-symmetric x-layers from graph depth:
        # nodes at same step index (e.g., top/bottom pathways) share same x coordinate.
        depth = {}
        if is_sequential:
            curr_x = 0
            for p in all_paths:
                for u in p:
                    if u not in depth:
                        depth[u] = curr_x
                        curr_x += 1
        else:
            from collections import deque
            q = deque()
            for r in roots:
                depth[r] = 0
                q.append(r)
            while q:
                u = q.popleft()
                du = depth.get(u, 0)
                for v in adj_fwd.get(u, []):
                    nd = du + 1
                    # keep smallest depth so merged pathways stay aligned
                    if (v not in depth) or (nd < depth[v]):
                        depth[v] = nd
                        q.append(v)

        # Fallback for disconnected/isolated nodes:
        if len(depth) < len(valid):
            und = [sp for sp in sorted(valid, key=lambda s: s.get("x", 0)) if sp["id"] not in depth]
            start = (max(depth.values()) + 1) if depth else 0
            for k, sp in enumerate(und):
                depth[sp["id"]] = start + k
                    
        # Offset mappings for Matplotlib internal visual dragging
        if not hasattr(self, "pt_offsets"): self.pt_offsets = {}
        px = {sp["id"]: depth[sp["id"]] + self.pt_offsets.get(sp.get("_orig_id", sp["id"]), (0.0, 0.0))[0] for sp in valid}
        self._plot_px_cache = px
        py = {sp["id"]: (sp.get(rel_key, 0.0) or 0.0) + self.pt_offsets.get(sp.get("_orig_id", sp["id"]), (0.0, 0.0))[1] for sp in valid}
                    
        y_all = list(py.values())

        label_mode = (self._plot_labels.get() or "Clean").strip().lower()
        label_color_mode = (self._label_color_mode.get() or "Black").strip()
        label_size_mode = (self._label_size_mode.get() or "Small").strip()

        def _label_color(match_color=None):
            if label_color_mode == "Match the Plot" and match_color:
                return match_color
            if label_color_mode == "Custom":
                return str(self._label_custom_color.get() or "#111827")
            return "#111827"

        def _scale_font(base):
            if label_size_mode == "Big":
                return 20.0
            if label_size_mode == "Custom":
                try:
                    return max(6.0, float(self._label_custom_size.get()))
                except Exception:
                    return base
            return 14.0

        if style == "aks":
            # AKS uses one consistent green for bars and connecting lines.
            line_color = "#9fc88a"
            int_color = "#9fc88a"
            ts_color = "#9fc88a"
            grid_alpha = 0.10
            linewidth = 1.8
            marker_size = 70
        elif style == "classical":
            line_color = "#111827"
            int_color = "#2563eb"
            ts_color = "#dc2626"
            grid_alpha = 0.18
            linewidth = 2.4
            marker_size = 92
        else:  # CCC
            line_color = "#f59e0b"
            int_color = "#111827"  # Black intermediates
            ts_color = "#dc2626"
            grid_alpha = 0.25
            linewidth = 2.8
            marker_size = 88

        y_min = min(y_all)
        y_max = max(y_all)
        y_span = max(1.0, y_max - y_min)
        y_pad = max(2.0, y_span * 0.14)
        label_off = max(0.55, y_span * 0.055)
        use_gradient = color_mode == "Gradient"
        
        g_cmap = None
        g_norm = None
        if use_gradient:
            import matplotlib.colors as mcolors
            import matplotlib.pyplot as plt
            cmap_name = (self._plot_gradient_map.get() or "PeakMap").strip()
            if cmap_name.lower() == "peakmap":
                g_cmap = LinearSegmentedColormap.from_list("peakmap", [(0.00, "#16a34a"), (0.55, "#fde047"), (1.00, "#ef4444")])
            else: 
                g_cmap = plt.get_cmap(cmap_name.lower())
            g_norm = mcolors.Normalize(vmin=y_min, vmax=y_max)

        is_classic = shape.startswith("classic")
        is_aks = shape == "aks_style"
        is_ccc = style == "ccc"
        _ccc_font_kw = {"family": "Arial", "weight": "bold", "style": "italic"} if is_ccc else {"family": "Arial", "weight": "bold", "style": "normal"}

        # Determine exact bar parameters based on chosen class
        aks_bar_half_w = 0.078
        bar_w = (0.175 / 1.5) if is_classic else (aks_bar_half_w if is_aks else 0.0)
        bar_lw = 9.0
        cls_ls = "-"
        if shape == "classic 1":
            cls_ls = ":"
            linewidth = 3.5
            bar_lw = 9.0
        elif shape == "classic 2":
            cls_ls = "-"
            linewidth = 1.0
            bar_lw = 9.0
        elif shape == "classic 3":
            cls_ls = "-"
            linewidth = 3.0
            bar_lw = 9.0
        elif shape == "classic 4":
            cls_ls = ":"
            linewidth = 1.2
            bar_lw = 10.0

        fig = Figure(figsize=(10.5, 5.8), dpi=120)
        ax = fig.add_subplot(111)
        
        # Legend tracking
        legend_handles = {}
        plot_annotations = []
        aks_axis_art = {"arrow": None, "text": None}

        # Plot each edge independently
        id_to_name = {sp.get("id"): _sanitize_math_text(sp.get("name", "")) for sp in render_species}
        self._pathway_options = []
        for i, p in enumerate(all_paths):
            short = " → ".join(id_to_name.get(sid, str(sid)) for sid in p[:10])
            if len(p) > 10:
                short += " …"
            self._pathway_options.append({"idx": i, "label": f"{i + 1}: {short}", "ids": list(p)})
        for edge_key, e_data in render_edges.items():
            try:
                u, v = map(int, edge_key.split("_"))
            except ValueError:
                continue
            
            sp_u = next((s for s in valid if s["id"] == u), None)
            sp_v = next((s for s in valid if s["id"] == v), None)
            if not sp_u or not sp_v:
                continue
                
            dx = depth[v] - depth[u]
            if (is_classic or is_aks) and dx > 0: x_pts_line = [px[u] + bar_w, px[v] - bar_w]
            elif (is_classic or is_aks) and dx < 0: x_pts_line = [px[u] - bar_w, px[v] + bar_w]
            else: x_pts_line = [px[u], px[v]]
            
            y_pts = [py[u], py[v]]
            
            if shape == "ccl_view_curved":
                lx, ly = self._build_smooth_curve(x_pts_line, y_pts, points_per_seg=28)
            elif use_gradient or is_classic or is_aks:
                lx, ly = self._build_linear_dense_curve(x_pts_line, y_pts, points_per_seg=36)
            else:
                lx, ly = x_pts_line, y_pts
                
            ls = cls_ls if is_classic else "-"
                
            edge_color = e_data.get("color", "")
            if is_aks:
                # User requested a single green across all AKS lines and bars.
                edge_color = "#9fc88a"
            elif color_mode == "Black":
                edge_color = "#111827"
            if not edge_color:
                edge_color = path_colors_map.get(f"{u}_{v}", "#2563eb" if shape == "classic 1" else line_color)
            
            path_label = e_data.get("path_label", "")
                
            if use_gradient and (not is_aks) and not e_data.get("color"):
                segments = [[(lx[k], ly[k]), (lx[k + 1], ly[k + 1])] for k in range(len(lx) - 1)]
                lc = LineCollection(segments, linewidths=linewidth, cmap=g_cmap, zorder=1, linestyles=ls)
                nseg = len(segments)
                lc.set_array([ (ly[k] + ly[k+1])/2.0 for k in range(nseg) ])
                lc.set_norm(g_norm)
                lc.set_capstyle("round")
                lc.set_picker(5)
                lc._edge_key = edge_key
                ax.add_collection(lc)
            else:
                line_obj, = ax.plot(lx, ly, color=edge_color, linewidth=linewidth, zorder=1, picker=5, linestyle=ls)
                line_obj._edge_key = edge_key
                if path_label and path_label not in legend_handles:
                    legend_handles[path_label] = line_obj
                
            # Arrows/text along edges
            draw_mos_mode = self._plot_draw_mos.get()
            if draw_mos_mode == "Auto":
                # Fallback natively hides for Classic styles unless explicitly demanded
                if is_classic: draw_mos = False
                else: draw_mos = (len(render_edges) < len(valid))
            else:
                draw_mos = (draw_mos_mode == "Show")
                
            if draw_mos:
                add_names = [f"{c:g} {id_to_name[sid]}" if abs(c-1)>1e-12 else id_to_name[sid] for sid, c in e_data.get("add", {}).items() if sid in id_to_name]
                rem_names = [f"{c:g} {id_to_name[sid]}" if abs(c-1)>1e-12 else id_to_name[sid] for sid, c in e_data.get("remove", {}).items() if sid in id_to_name]
                mx, my = (x_pts_line[0] + x_pts_line[-1])*0.5, (y_pts[0] + y_pts[-1])*0.5
                if add_names:
                    txt = self._format_edge_species_block(add_names)
                    ann1 = ax.annotate(
                        txt,
                        xy=(mx, my + label_off * 0.25),
                        xytext=(mx - 0.25, my + label_off * 2.2),
                        ha="center",
                        va="bottom",
                        fontsize=_scale_font(8.5),
                        color=_label_color(edge_color),
                        arrowprops=dict(arrowstyle="->", lw=1.2, color="#111827", connectionstyle="arc3,rad=-0.25"),
                        zorder=5,
                        **_ccc_font_kw,
                    )
                    plot_annotations.append(ann1)
                    try: ann1.draggable(True)
                    except: pass
                if rem_names:
                    txt = self._format_edge_species_block(rem_names)
                    ann2 = ax.annotate(
                        txt,
                        xy=(mx, my - label_off * 0.25),
                        xytext=(mx + 0.25, my - label_off * 2.3),
                        ha="center",
                        va="top",
                        fontsize=_scale_font(8.5),
                        color=_label_color(edge_color),
                        arrowprops=dict(arrowstyle="<-", lw=1.2, color="#111827", connectionstyle="arc3,rad=0.25"),
                        zorder=5,
                        **_ccc_font_kw,
                    )
                    plot_annotations.append(ann2)
                    try: ann2.draggable(True)
                    except: pass

        # Draw All Scatter Points        # Scatter / Text Loop
        for sp in valid:
            xi = px[sp["id"]]
            yi = py[sp["id"]]
            nm = _sanitize_math_text(sp.get("plot_name_override", sp.get("name", "")))
            if getattr(self, "_label_show_s2_dev", None) and self._label_show_s2_dev.get():
                s2d = str(sp.get("s2_dev", "")).strip()
                if s2d:
                    nm += f"\n$\\Delta S^2 = {s2d}$"
            kd = sp.get("kind", "Intermediate")
            is_ts_point = str(kd).upper().startswith("TS")
            marker = "o"
            
            # Map node color
            nd_edge = render_edges.get(f"{sp.get('id')}_{sp.get('id')}", {}) # fallback logic empty dict
            custom_c_ins = [render_edges.get(e, {}).get("color") for e in render_edges if e.endswith(f"_{sp['id']}") and render_edges.get(e, {}).get("color")]
            custom_c_outs = [render_edges.get(e, {}).get("color") for e in render_edges if e.startswith(f"{sp['id']}_") and render_edges.get(e, {}).get("color")]
            custom_colors = custom_c_ins + custom_c_outs
            
            c_ins = [render_edges.get(e, {}).get("color", path_colors_map.get(e, "")) for e in render_edges if e.endswith(f"_{sp['id']}")]
            c_outs = [render_edges.get(e, {}).get("color", path_colors_map.get(e, "")) for e in render_edges if e.startswith(f"{sp['id']}_")]
            colors = [c for c in c_ins + c_outs if c]
            
            if is_aks:
                color = "#9fc88a"
            elif use_gradient and not custom_colors: 
                color = mcolors.to_hex(g_cmap(g_norm(yi)))
            elif colors: 
                color = colors[0] # Take the path color natively!
            elif is_classic and len(all_paths) == 1: color = "#111827"
            elif shape == "classic 1": color = "#2563eb"
            else: color = ts_color if is_ts_point else int_color
            
            if is_aks:
                # Rounded-edged rectangular marker (AKS style)
                # Capsule bar (rectangle + two semicircle ends).
                box_w = aks_bar_half_w
                ax.plot([xi - box_w, xi + box_w], [yi, yi], color=color, lw=12.4, solid_capstyle='round', zorder=3)
            elif is_classic:
                ax.plot([xi - bar_w, xi + bar_w], [yi, yi], color=color, lw=bar_lw, solid_capstyle='butt', zorder=3)
            else:
                ax.scatter([xi], [yi], s=marker_size, marker=marker, facecolors="black", edgecolors=color, linewidths=2.0, zorder=3)

            # Optional species image insertion near points.
            if self._show_images.get():
                img_path = str(sp.get("image_path", "") or "").strip()
                if img_path:
                    try:
                        if os.path.isfile(img_path):
                            arr = matplotlib.image.imread(img_path)
                            zoom = max(0.05, min(1.2, float(self._image_scale.get())))
                            oi = OffsetImage(arr, zoom=zoom)
                            ab = AnnotationBbox(
                                oi,
                                (xi, yi),
                                xybox=(0, 52),
                                xycoords="data",
                                boxcoords=("offset points"),
                                frameon=True,
                                bboxprops=dict(edgecolor="#cbd5e1", facecolor="white", alpha=0.95, boxstyle="round,pad=0.12"),
                                zorder=2.9,
                            )
                            ax.add_artist(ab)
                        else:
                            ann_im = ax.text(xi, yi + label_off * 2.1, "[img missing]", ha="center", va="bottom", fontsize=7.5, color="#991b1b", zorder=4)
                            plot_annotations.append(ann_im)
                    except Exception:
                        ann_ie = ax.text(xi, yi + label_off * 2.1, "[img error]", ha="center", va="bottom", fontsize=7.5, color="#991b1b", zorder=4)
                        plot_annotations.append(ann_ie)
                
            if label_mode != "off":
                if label_mode == "compact": val_txt, fs, pad = f"{yi:.1f}", _scale_font(10.0), 0.10
                else: val_txt, fs, pad = f"{yi:.1f}", _scale_font(11.0), 0.16
                
                if shape == "classic 1" or shape == "classic 3": val_txt = f"{yi:.0f}"
                elif shape == "classic 2": val_txt = f"({yi:.0f})"
                
                txt_color = _label_color(color)
                if not self._label_hide_energies.get():
                    ann_v = ax.annotate(
                        val_txt,
                        xy=(xi, yi),
                        xytext=(xi, yi + label_off * 0.95),
                        ha="center",
                        va="bottom",
                        fontsize=fs,
                        fontweight="bold",
                        fontfamily="Arial",
                        fontstyle="normal",
                        color=sp.get("label_color", txt_color),
                        bbox=dict(boxstyle=f"round,pad={pad}", facecolor="white", edgecolor="none", alpha=0.82) if not is_classic else None,
                        zorder=4,
                    )
                    plot_annotations.append(ann_v)
                    try:
                        ann_v.draggable(True)
                    except:
                        pass
                if not self._label_hide_names.get():
                    ann_n = ax.annotate(
                        nm,
                        xy=(xi, yi),
                        xytext=(xi, yi - label_off * 0.95),
                        ha="center",
                        va="top",
                        fontsize=fs,
                        color=txt_color,
                        zorder=4,
                        **_ccc_font_kw,
                    )
                    plot_annotations.append(ann_n)
                    ann_n.set_picker(5)
                    ann_n._pes_species_idx = sp.get("id")
                    try:
                        ann_n.draggable(True)
                    except:
                        pass

        # User-inserted overlay assets (right-click menu in plot)
        asset_artists = {}
        for i, asset in enumerate(list(self._plot_assets)):
            if str(asset.get("kind", "")) != "image":
                if str(asset.get("kind", "")) != "3d_image":
                    continue
            pth = str(asset.get("path", "") or "").strip()
            if not pth:
                continue
            try:
                if not os.path.isfile(pth):
                    continue
                axx = float(asset.get("x", 0.0))
                ayy = float(asset.get("y", 0.0))
                angle = float(asset.get("angle", 0.0))
                arr = matplotlib.image.imread(pth)
                # Rotation + quality upscale for tiny clipboard images.
                try:
                    from PIL import Image
                    import numpy as _np
                    pim = Image.open(pth).convert("RGBA")
                    if max(pim.size) < 550:
                        pim = pim.resize((int(pim.size[0] * 2.0), int(pim.size[1] * 2.0)), Image.Resampling.LANCZOS)
                    if abs(angle) > 1e-3:
                        pim = pim.rotate(-angle, expand=True, resample=Image.Resampling.BICUBIC)
                    arr = _np.asarray(pim)
                except Exception:
                    pass
                zoom = max(0.05, min(1.6, float(asset.get("zoom", self._image_scale.get()))))
                oi = OffsetImage(arr, zoom=zoom)
                is_3d = str(asset.get("kind", "")) == "3d_image"
                ab = AnnotationBbox(
                    oi,
                    (axx, ayy),
                    xycoords="data",
                    frameon=(not is_3d),
                    bboxprops=dict(edgecolor="#94a3b8", facecolor="white", alpha=0.95, boxstyle="round,pad=0.12") if not is_3d else None,
                    zorder=5.2,
                )
                ax.add_artist(ab)
                asset_artists[i] = ab
            except Exception:
                continue

        if self._show_merged_plateaus.get():
            tol = max(0.0, float(self._plateau_tol.get()))
            depth_to_vals = {}
            for sp in valid:
                d = depth.get(sp["id"], 0)
                depth_to_vals.setdefault(d, []).append(py[sp["id"]])
            depth_keys = sorted(depth_to_vals.keys())
            depth_avg = {d: (sum(vs) / len(vs)) for d, vs in depth_to_vals.items()}
            i = 0
            while i < len(depth_keys) - 1:
                d0 = depth_keys[i]
                d1 = depth_keys[i + 1]
                if abs(depth_avg[d1] - depth_avg[d0]) <= tol:
                    j = i + 1
                    while j < len(depth_keys) - 1 and abs(depth_avg[depth_keys[j + 1]] - depth_avg[depth_keys[j]]) <= tol:
                        j += 1
                    d_start, d_end = depth_keys[i], depth_keys[j]
                    y_pl = sum(depth_avg[depth_keys[k]] for k in range(i, j + 1)) / (j - i + 1)
                    ax.plot([d_start, d_end], [y_pl, y_pl], color="#0f766e", lw=6.5, alpha=0.25, solid_capstyle="round", zorder=2.1)
                    ax.plot([d_start, d_end], [y_pl, y_pl], color="#0f766e", lw=1.7, alpha=0.95, solid_capstyle="round", zorder=2.2)
                    ann_p = ax.text(
                        (d_start + d_end) * 0.5,
                        y_pl + label_off * 0.35,
                        "plateau",
                        fontsize=_scale_font(7.8),
                        color=_label_color("#0f766e"),
                        ha="center",
                        va="bottom",
                        zorder=3.5,
                        **({**_ccc_font_kw} if is_ccc else {}),
                    )
                    plot_annotations.append(ann_p)
                    i = j + 1
                else:
                    i += 1

        x_ticks = sorted(list(set(depth[s["id"]] for s in valid)))
        x_lbls = [""] * len(x_ticks)
            
        ax.set_xticks(x_ticks)
        ax.set_xticklabels(x_lbls)
        if is_ccc:
            ax.set_xlabel("Reaction Coordinate", fontsize=20, labelpad=8, family="Arial", fontweight="bold", style="italic")
        else:
            ax.set_xlabel("Reaction Coordinate", fontsize=20, fontweight="bold", labelpad=8)
        
        unit = self._energy_unit.get()
        custom_ylabel = getattr(self, "_plot_custom_ylabel", None)
        lbl_text = _sanitize_math_text(custom_ylabel.get() if (custom_ylabel and custom_ylabel.get()) else f"{default_axis} ({unit})")
        use_ccc_left = not bool(self._label_hide_left_axis.get())
        ylab = ax.set_ylabel("" if use_ccc_left else lbl_text)
        ylab.set_picker(5)
        ylab.set_fontsize(20)
        ax.tick_params(axis="x", labelsize=14)
        ax.tick_params(axis="y", labelsize=15)
        
        if is_ccc:
            ax.set_title("Potential Energy Surface", family="Arial", fontweight="bold", style="italic")
        else:
            ax.set_title("Potential Energy Surface")
        if is_aks:
            ax.spines['right'].set_visible(False)
            ax.spines['top'].set_visible(False)
            ax.spines['left'].set_visible(True)
            ax.spines['bottom'].set_visible(False)
            ax.get_yaxis().set_visible(True)
            ax.get_xaxis().set_visible(False)
            ax.spines['left'].set_linewidth(1.5)
            ax.set_ylabel(lbl_text, fontsize=_scale_font(13.0), fontweight="bold")
            ax.tick_params(axis='y', width=1.5, labelsize=_scale_font(11.0))
        try:
            yr0, yr1 = (y_min - max(y_pad * 1.05, 3.0), y_max + max(y_pad * 1.35, 2.8)) if is_aks else (y_min - y_pad, y_max + max(y_pad * 1.3, 2.8))
            import json
            raw_val = self._aks_step_labels.get() or ""
            steps_json = None
            try:
                steps_json = json.loads(raw_val)
                if not isinstance(steps_json, list): steps_json = None
            except Exception:
                pass
            
            y_step_default = yr0

            
            if not hasattr(self, "_step_drag_arts"): self._step_drag_arts = []
            else: self._step_drag_arts.clear()
            
            if steps_json is not None:
                for idx, step in enumerate(steps_json):
                    s_id = step.get("start")
                    e_id = step.get("end")
                    txt = step.get("label", "")
                    color = step.get("color", "black")
                    if is_aks and (color == "black" or not color):
                        color = "#9fc88a"
                    y_offset = float(step.get("y_offset", 0.0))
                    t_dx = float(step.get("text_dx", 0.0))
                    t_dy = float(step.get("text_dy", 0.0))
                    
                    this_y = y_step_default + y_offset
                    
                    px_start = px.get(s_id) if s_id is not None and s_id != "" else None
                    px_end = px.get(e_id) if e_id is not None and e_id != "" else None
                    
                    if px_start is not None and px_end is not None:
                        if is_aks:
                            gap = (px_end - px_start) * 0.05 if px_end > px_start else 0
                            l1, = ax.plot([px_start + gap, px_end - gap], [this_y, this_y], color=color, lw=1.8, zorder=4, picker=5)
                            c1, = ax.plot([px_start, px_end], [this_y, this_y], marker="o", color=color, lw=0, markersize=8, zorder=5, picker=5)
                            
                            def_t_x = (px_start + px_end) * 0.5
                            def_t_y = this_y
                            t_x = def_t_x + t_dx
                            t_y = def_t_y + t_dy
                            
                            t_art = ax.text(t_x, t_y, txt, ha="center", va="center", fontsize=_scale_font(11.0), fontfamily="Arial", fontstyle="normal", color=color, fontweight="bold", bbox=dict(facecolor="white", edgecolor="none", pad=6.0), zorder=6, picker=5)
                            
                            l1._pes_step_idx = idx; l1._pes_step_art = "line"
                            c1._pes_step_idx = idx; c1._pes_step_art = "line"
                            t_art._pes_step_idx = idx; t_art._pes_step_art = "text"
                            
                            self._step_drag_arts.extend([l1, c1, t_art])
                        else:
                            d_y = 0.008 * (yr1 - yr0)
                            line_color = "grey" if is_ccc else color
                            l1, = ax.plot([px_start, px_start], [this_y - d_y, this_y + d_y], color=line_color, lw=1.8, zorder=4, picker=5)
                            l2, = ax.plot([px_end, px_end], [this_y - d_y, this_y + d_y], color=line_color, lw=1.8, zorder=4, picker=5)
                            ann = ax.annotate("", xy=(px_end, this_y), xytext=(px_start, this_y), arrowprops=dict(arrowstyle="<|-|>", linestyle="--", lw=1.2, color=line_color), zorder=5)
                            
                            def_t_x = (px_start + px_end) * 0.5
                            def_t_y = this_y - d_y - 0.001*(yr1 - yr0)
                            t_x = def_t_x + t_dx
                            t_y = def_t_y + t_dy
                            
                            t_art = ax.text(t_x, t_y, txt, ha="center", va="top", fontsize=_scale_font(11.0), fontfamily="Arial", fontstyle="normal", color=color, fontweight="bold", zorder=5, picker=5)
                            
                            l1._pes_step_idx = idx; l1._pes_step_art = "line"
                            l2._pes_step_idx = idx; l2._pes_step_art = "line"
                            ann._pes_step_idx = idx; ann._pes_step_art = "line"
                            t_art._pes_step_idx = idx; t_art._pes_step_art = "text"
                            
                            self._step_drag_arts.extend([l1, l2, ann, t_art])
                            
                        t_art._pes_step_def_x = def_t_x
                        t_art._pes_step_def_y = def_t_y
                        
            elif is_aks:
                steps = [s.strip() for s in str(raw_val).split(";") if s.strip()]
                if not steps:
                    steps = ["Reaction Step"]
                nst = len(steps)
                x0 = (_min_x - 0.3) + 0.35
                x1 = (_max_x + 0.3) - 0.35
                span = max(0.6, x1 - x0)
                for i, txt in enumerate(steps):
                    a = x0 + span * (i / max(1, nst))
                    b = x0 + span * ((i + 1) / max(1, nst))
                    ax.annotate("", xy=(b, y_step_default), xytext=(a, y_step_default), arrowprops=dict(arrowstyle="-|>", lw=1.6, color="#9fc88a"), zorder=5)
                    ax.text((a + b) * 0.5, y_step_default + 0.018 * (yr1 - yr0), txt, ha="center", va="bottom", fontsize=10.5, color="#9fc88a", fontweight="bold", zorder=5)
        except Exception:
            pass
        if not bool(self._label_hide_left_axis.get()):
            try:
                axis_x = min(0.15, float(self._aks_axis_x.get()))
                temp_txt = _sanitize_math_text(str(self._aks_temp_label.get() or "").strip())
                basis_letter = axis_kind
                custyl = (custom_ylabel.get().strip() if (custom_ylabel and custom_ylabel.get()) else "")
                if custyl:
                    lbl_axis = custyl
                elif temp_txt:
                    t_math = temp_txt.replace(" ", r"\ ")
                    lbl_axis = rf"$\Delta {basis_letter}_{{{t_math}}}$ ({unit})"
                else:
                    lbl_axis = rf"$\Delta {basis_letter}$ ({unit})"
                import matplotlib.transforms as mtransforms
                trans_blend = mtransforms.blended_transform_factory(ax.transData, ax.transAxes)
                
                xmin = min(px.values()) if px else 0.0
                user_placed = bool(self._ccc_axis_user_placed.get())
                axis_data_x = self._ccc_axis_x_position(
                    self._aks_axis_x.get(), px, user_placed, default_margin=0.08
                )
                
                if is_ccc:
                    y0, y1 = 0.05, 0.88
                    nseg = 36
                    ys = [y0 + (y1 - y0) * i / (nseg - 1) for i in range(nseg)]
                    cmap_ax = LinearSegmentedColormap.from_list("ccl_axis_grad", ["#16a34a", "#fde047", "#ef4444"])
                    denom = max(1e-9, y1 - y0)
                    
                    segs = []
                    segcols = []
                    for i in range(nseg - 1):
                        segs.append([(axis_data_x, ys[i]), (axis_data_x, ys[i + 1])])
                        u = ((ys[i] + ys[i + 1]) * 0.5 - y0) / denom
                        segcols.append(cmap_ax(u))
                        
                    lc_axis = LineCollection(
                        segs,
                        colors=segcols,
                        linewidths=2.8,
                        capstyle="round",
                        transform=trans_blend,
                        clip_on=False,
                        zorder=5.95,
                        picker=8,
                    )
                    ax.add_collection(lc_axis)
                    
                    ann_top = ax.annotate(
                        "",
                        xy=(axis_data_x, min(0.92, y1 + 0.035)),
                        xytext=(axis_data_x, y1 - 0.028),
                        xycoords=trans_blend,
                        arrowprops=dict(arrowstyle="-|>", lw=2.0, color="#ef4444"),
                        clip_on=False,
                        zorder=6.05,
                    )
                    
                    ann_bot = ax.plot(
                        [axis_data_x],
                        [y0],
                        marker="o",
                        markersize=6.5,
                        color="#16a34a",
                        transform=trans_blend,
                        clip_on=False,
                        zorder=6.05
                    )[0]
                    
                    label_x = axis_data_x - (0.10 if user_placed else max(0.10, (xmin - axis_data_x) * 0.3))
                    txt_axis = ax.text(
                        label_x,
                        0.50,
                        lbl_axis,
                        transform=trans_blend,
                        rotation=90,
                        ha="center",
                        va="center",
                        fontsize=14,
                        color="#0f172a",
                        family="Arial",
                        fontweight="bold",
                        style="italic",
                        zorder=6.1,
                        clip_on=False,
                    )
                    try:
                        txt_axis.set_picker(8)
                    except Exception:
                        pass
                    axis_handle = ax.scatter(
                        [axis_data_x],
                        [0.5],
                        s=420,
                        alpha=0.0,
                        transform=trans_blend,
                        clip_on=False,
                        zorder=26,
                        picker=14,
                    )
                    aks_axis_art["arrow"] = (lc_axis, ann_top, ann_bot)
                    aks_axis_art["text"] = txt_axis
                    aks_axis_art["handle"] = axis_handle
                    aks_axis_art["trans"] = trans_blend
                    aks_axis_art["xmin"] = xmin
                    aks_axis_art["axis_x"] = axis_data_x
                else:
                    ann_axis = ax.annotate(
                        "",
                        xy=(axis_data_x, 0.88),
                        xytext=(axis_data_x, 0.05),
                        xycoords=trans_blend,
                        arrowprops=dict(arrowstyle="-|>", lw=1.7, color="#94a3b8"),
                        zorder=6,
                    )
                    label_x = axis_data_x - (0.10 if user_placed else max(0.10, (float(xmin) - float(axis_data_x)) * 0.3))
                    txt_axis = ax.text(
                        label_x,
                        0.50,
                        lbl_axis,
                        transform=trans_blend,
                        rotation=90,
                        ha="center",
                        va="center",
                        fontsize=15,
                        color="#475569",
                        fontweight="bold",
                        zorder=6,
                    )
                    try:
                        txt_axis.set_picker(5)
                    except Exception:
                        pass
                    aks_axis_art["arrow"] = ann_axis
                    aks_axis_art["text"] = txt_axis
                    aks_axis_art["trans"] = trans_blend
                    aks_axis_art["xmin"] = xmin
                    aks_axis_art["axis_x"] = axis_data_x
            except Exception:
                pass
        
        if legend_handles:
            ax.legend(handles=list(legend_handles.values()), labels=list(legend_handles.keys()), loc="best", fancybox=True, framealpha=0.85)

        has_steps = False
        try:
            import json
            if isinstance(json.loads(self._aks_step_labels.get() or ""), list): has_steps = True
        except:
            if self._aks_step_labels.get() and is_aks: has_steps = True
            
        if is_aks or has_steps:
            ax.set_ylim(y_min - max(y_pad * 2.1, 6.0), y_max + max(y_pad * 1.35, 2.8))
        else:
            ax.set_ylim(y_min - y_pad, y_max + max(y_pad * 1.3, 2.8))
        _min_x = min(px.values()) if px else 0
        _max_x = max(px.values()) if px else 1
        
        last_sp_name = ""
        for sp_node in valid:
            if px.get(sp_node.get("id")) == _max_x:
                last_sp_name = sp_node.get("name", "")
                break
                
        if is_aks:
            padding = 0.5 + (len(last_sp_name) * 0.05)
            ax.set_xlim(_min_x - 0.5, _max_x + padding)
        else:
            ax.set_xlim(_min_x - 0.25, _max_x + 0.15)
        grid_mode = (self._grid_mode.get() or "Hide: All Grids").strip()
        if self._show_axis_break.get():
            low = float(self._axis_break_low.get())
            high = float(self._axis_break_high.get())
            if low > high:
                low, high = high, low
            if (high - low) > 0.2 and low < (y_max + y_pad) and high > (y_min - y_pad):
                ax.axhspan(low, high, color="white", alpha=0.96, zorder=8.2)
                tr = ax.get_yaxis_transform()
                for yy in (low, high):
                    ax.plot([0.0, 0.018], [yy, yy + 0.35], transform=tr, color="#334155", lw=1.6, clip_on=False, zorder=8.6)
                    ax.plot([0.022, 0.04], [yy, yy + 0.35], transform=tr, color="#334155", lw=1.6, clip_on=False, zorder=8.6)
                ax.text(0.005, (low + high) * 0.5, "//", transform=tr, fontsize=11, fontweight="bold", color="#334155", va="center", ha="left", zorder=8.7)
                try:
                    ticks = ax.get_yticks()
                    new_ticks = [t for t in ticks if t <= low or t >= high]
                    ax.set_yticks(new_ticks)
                except Exception:
                    pass

        if grid_mode == "More Grids":
            ax.grid(True, which="major", alpha=grid_alpha)
            try:
                from matplotlib.ticker import AutoMinorLocator
                ax.xaxis.set_minor_locator(AutoMinorLocator(3))
                ax.yaxis.set_minor_locator(AutoMinorLocator(3))
                ax.grid(True, which="minor", alpha=max(0.10, grid_alpha * 0.65), linewidth=0.7)
            except Exception:
                pass
        elif grid_mode == "Less Grids":
            ax.grid(True, which="major", alpha=grid_alpha)
            ax.minorticks_off()
        elif grid_mode == "Hide: All Grids":
            ax.grid(False)
            ax.minorticks_off()
        elif grid_mode == "Hide: Big Box":
            ax.grid(False)
            ax.minorticks_off()
            ax.set_yticks([])
            ax.set_ylabel("")
            for side in ("left", "right", "top", "bottom"):
                sp = ax.spines.get(side)
                if sp is not None:
                    sp.set_visible(False)
        elif grid_mode == "Hide: All except Plot":
            ax.grid(False)
            ax.minorticks_off()
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_xlabel("")
            ax.set_ylabel("")
            ax.set_title("")
            for side in ("left", "right", "top", "bottom"):
                sp = ax.spines.get(side)
                if sp is not None:
                    sp.set_visible(False)
        else:
            ax.grid(True, which="major", alpha=grid_alpha)

        if grid_mode != "Hide: All except Plot":
            if grid_mode in ("Hide: All Grids", "Hide: Big Box"):
                ax.axhline(0.0, color="#cbd5e1", linewidth=1.0, linestyle=(0, (5, 4)), alpha=0.95, zorder=0)
            elif grid_mode in ("More Grids", "Less Grids"):
                ax.axhline(0.0, color="#9ca3af", linewidth=1.0, alpha=0.7, zorder=0)

        fig.tight_layout()

        if is_ccc:
            fig.subplots_adjust(left=max(0.14, fig.subplotpars.left))
        elif is_aks:
            fig.subplots_adjust(left=0.08, right=0.96, top=0.95, bottom=0.05)
            if grid_mode != "Hide: All except Plot":
                for _tl in ax.get_yticklabels():
                    _tl.set_fontfamily("Arial")
                    _tl.set_fontweight("bold")
                    _tl.set_style("italic")

        # ΔG‡ for selected pathway only (forward direction): best rise from a prior low to a later high
        if self._show_diff_bars.get() and py and all_paths:
            try:
                pw_idx = int(self._diff_bar_pathway_idx.get())
            except Exception:
                pw_idx = 0
            pw_idx = max(0, min(pw_idx, len(all_paths) - 1))
            path_ids = all_paths[pw_idx]
            path_pts = [(sid, py[sid]) for sid in path_ids if sid in py]
            if len(path_pts) >= 2:
                best = None  # (dG, i_low, j_high)
                direction = getattr(self, "_diff_bar_direction", tk.StringVar(value="Forward")).get()
                
                if direction == "Forward":
                    min_i = 0
                    min_y = float(path_pts[0][1])
                    for j in range(1, len(path_pts)):
                        yj = float(path_pts[j][1])
                        d = yj - min_y
                        if (best is None) or (d > best[0]):
                            best = (d, min_i, j)
                        if yj < min_y:
                            min_y = yj
                            min_i = j
                else:  # Backward
                    min_i = len(path_pts) - 1
                    min_y = float(path_pts[-1][1])
                    for j in range(len(path_pts) - 2, -1, -1):
                        yj = float(path_pts[j][1])
                        d = yj - min_y
                        if (best is None) or (d > best[0]):
                            best = (d, min_i, j)
                        if yj < min_y:
                            min_y = yj
                            min_i = j

                if not best or best[0] <= 0:
                    best = None
                if best is None:
                    # Monotonic path: fallback to endpoints
                    if direction == "Forward":
                        i_low, j_high = 0, len(path_pts) - 1
                    else:
                        i_low, j_high = len(path_pts) - 1, 0
                    y_lo = float(path_pts[i_low][1])
                    y_hi = float(path_pts[j_high][1])
                    dG = y_hi - y_lo
                else:
                    dG, i_low, j_high = best
                    y_lo = float(path_pts[i_low][1])
                    y_hi = float(path_pts[j_high][1])
                xr0, xr1 = ax.get_xlim()
                yr0, yr1 = ax.get_ylim()
                xs = max(1e-6, xr1 - xr0)
                ys = max(1e-6, yr1 - yr0)
                mx = 0.018 * xs
                my = 0.02 * ys
                sid_lo = path_pts[i_low][0]
                sid_hi = path_pts[j_high][0]
                x_lo = float(px[sid_lo]) if sid_lo in px else None
                x_hi = float(px[sid_hi]) if sid_hi in px else None
                if x_lo is not None and x_hi is not None:
                    x_mid = 0.5 * (x_lo + x_hi)
                else:
                    x_mid = 0.5 * (xr0 + xr1)
                if getattr(self, "_diff_bar_x_placed", None) is not None:
                    xbar = float(self._diff_bar_x_placed)
                else:
                    xbar = x_mid + 0.055 * xs
                    xbar = max(xr0 + mx, min(xr1 - mx, xbar))
                    self._diff_bar_x_placed = xbar
                y_lo_c = max(yr0 + my, min(yr1 - my, y_lo))
                y_hi_c = max(yr0 + my, min(yr1 - my, y_hi))
                ax.annotate(
                    "",
                    xy=(xbar, y_lo_c),
                    xytext=(xbar, y_hi_c),
                    arrowprops=dict(arrowstyle="<->", lw=1.15, color="#475569"),
                    zorder=4,
                )
                if getattr(self, "_diff_bar_label_xy", None) is not None and len(self._diff_bar_label_xy) == 2:
                    tx, ty = float(self._diff_bar_label_xy[0]), float(self._diff_bar_label_xy[1])
                else:
                    tx = xbar + 0.03 * xs
                    ty = 0.5 * (y_lo_c + y_hi_c)
                tx = max(xr0 + mx, min(xr1 - mx, tx))
                ty = max(yr0 + my, min(yr1 - my, ty))
                u = str(unit).strip() or "kcal/mol"
                delta_sym = axis_kind
                cust_val = getattr(self, "_diff_bar_custom_val", None)
                sub_label = "fwd" if direction == "Forward" else "bwd"
                if cust_val:
                    cust_val = _sanitize_math_text(cust_val)
                    lbl = r"$\Delta " + delta_sym + r"^{\ddagger}_{" + sub_label + r"} = " + str(cust_val) + r"$" + f" {u}"
                else:
                    lbl = r"$\Delta " + delta_sym + r"^{\ddagger}_{" + sub_label + r"} = " + f"{dG:.1f}" + r"$" + f" {u}"
                ann_d = ax.annotate(
                    lbl,
                    xy=(tx, ty),
                    xycoords="data",
                    ha="left",
                    va="center",
                    fontsize=8.5,
                    bbox=dict(boxstyle="round,pad=0.12", facecolor="white", edgecolor="none", alpha=0.88),
                    zorder=4.25,
                    **_ccc_font_kw,
                )
                ann_d._pes_diff_bar_label = True
                ann_d.set_picker(5)
                plot_annotations.append(ann_d)
                try:
                    drag = ann_d.draggable(use_blit=False)
                    if drag is not None and hasattr(drag, "finalize_offset"):
                        _orig_fin = drag.finalize_offset

                        def _wrapped_fin():
                            _orig_fin()
                            try:
                                xr0_, xr1_ = ax.get_xlim()
                                yr0_, yr1_ = ax.get_ylim()
                                xs_ = max(1e-6, xr1_ - xr0_)
                                ys_ = max(1e-6, yr1_ - yr0_)
                                mxx = 0.018 * xs_
                                myy = 0.02 * ys_
                                nx = max(xr0_ + mxx, min(xr1_ - mxx, float(ann_d.xy[0])))
                                ny = max(yr0_ + myy, min(yr1_ - myy, float(ann_d.xy[1])))
                                ann_d.xy = (nx, ny)
                                self._diff_bar_label_xy = (nx, ny)
                            except Exception:
                                pass

                        drag.finalize_offset = _wrapped_fin  # type: ignore[method-assign]
                except Exception:
                    pass

        # Run collision avoidance after *all* annotations (including ΔG‡) are created.
        if self._show_collision_avoid.get() and plot_annotations:
            self._apply_collision_avoidance(fig, ax, plot_annotations)

        def _on_pick(event):
            import tkinter.colorchooser as cc
            import tkinter.simpledialog as sd
            
            step_idx = getattr(event.artist, "_pes_step_idx", None)
            if step_idx is not None and getattr(event.mouseevent, "button", 0) == 3:
                try:
                    gui_evt = event.mouseevent.guiEvent
                    xr, yr = gui_evt.x_root, gui_evt.y_root
                except Exception:
                    xr, yr = self._plot_window.winfo_pointerxy()
                
                m = tk.Menu(self._plot_window, tearoff=0)
                
                def _change_color():
                    c = cc.askcolor(title="Step Label Color")[1]
                    if c:
                        import json
                        try:
                            s = json.loads(self._aks_step_labels.get() or "[]")
                            s[step_idx]["color"] = c
                            self._aks_step_labels.set(json.dumps(s))
                            self._render_plot()
                        except: pass
                def _edit_label():
                    from tkinter import simpledialog
                    import json
                    try:
                        s = json.loads(self._aks_step_labels.get() or "[]")
                        old_val = s[step_idx].get("label", "")
                        new_val = _ask_rich_text("Edit Label", "Enter new label text\\n(Supports basic math, e.g. $\Delta G^\ddagger$):", initialvalue=old_val, parent=self._plot_window)
                        if new_val is not None:
                            s[step_idx]["label"] = new_val.strip()
                            self._aks_step_labels.set(json.dumps(s))
                            self._render_plot()
                    except: pass
                    
                m.add_command(label="Change Color...", command=_change_color)
                m.add_command(label="Edit Label Text...", command=_edit_label)
                m.tk_popup(xr, yr)
                return
                
            sp_idx = getattr(event.artist, "_pes_species_idx", None)
            if sp_idx is not None and getattr(event.mouseevent, "button", 0) == 3:
                try:
                    gui_evt = event.mouseevent.guiEvent
                    xr, yr = gui_evt.x_root, gui_evt.y_root
                except Exception:
                    xr, yr = self._plot_window.winfo_pointerxy()
                self._show_species_context_menu(sp_idx, xr, yr)
                return

            # Clicked Y-Axis object
            if event.artist == ylab and event.mouseevent.dblclick:
                new_lbl = _ask_rich_text("Y-Axis Label", "Enter custom label text:", parent=self._plot_window)
                if new_lbl is not None:
                    if not hasattr(self, "_plot_custom_ylabel"):
                        self._plot_custom_ylabel = tk.StringVar()
                    self._plot_custom_ylabel.set(new_lbl.strip())
                    self._render_plot()
                return
            if getattr(event.artist, "_pes_diff_bar_label", False) and event.mouseevent.dblclick:
                new_lbl = _ask_rich_text("Barrier Height", "Enter custom barrier height text (e.g. 25.0):", parent=self._plot_window)
                if new_lbl is not None:
                    self._diff_bar_custom_val = new_lbl.strip()
                    self._render_plot()
                return
            if event.mouseevent.dblclick and event.artist == aks_axis_art.get("text"):
                new_temp = _ask_rich_text(
                    "AKS Axis Text",
                    "Enter temperature text (example: 573.15 K):",
                    parent=self._plot_window,
                )
                if new_temp is not None:
                    self._aks_temp_label.set(str(new_temp).strip())
                    self._render_plot()
                return

            # Clicked a plotted Connection Path segment
            edge_key = getattr(event.artist, "_edge_key", None)
            if edge_key and event.mouseevent.dblclick:
                e_data = self.edge_links.get(edge_key, {})
                init_color = e_data.get("color", line_color)
                init_name = e_data.get("path_label", "")
                
                dlg = tk.Toplevel(self._plot_window)
                dlg.title("Pathway Settings")
                dlg.geometry("350x240")
                dlg.transient(self._plot_window)
                dlg.grab_set()
                
                res = {"color": init_color, "name": init_name, "auto_fill": False, "ok": False}
                
                frm = ttk.Frame(dlg, padding=15)
                frm.pack(fill="both", expand=True)
                
                ttk.Label(frm, text="Pathway Settings", font=("Segoe UI", 12, "bold")).pack(pady=(0, 10))
                
                # Color Selector
                color_frm = ttk.Frame(frm)
                color_frm.pack(fill="x", pady=5)
                ttk.Label(color_frm, text="Segment Color: ").pack(side="left")
                color_btn = tk.Button(color_frm, bg=init_color, width=10, relief="solid")
                color_btn.pack(side="left")
                def pick_c():
                    c_out = cc.askcolor(initialcolor=res["color"], parent=dlg)[1]
                    if c_out:
                        res["color"] = c_out
                        color_btn.config(bg=c_out)
                color_btn.config(command=pick_c)
                
                # Name Input
                name_frm = ttk.Frame(frm)
                name_frm.pack(fill="x", pady=10)
                ttk.Label(name_frm, text="Pathway Name: ").pack(side="left")
                name_var = tk.StringVar(value=init_name)
                ttk.Entry(name_frm, textvariable=name_var, width=20).pack(side="left")
                
                # Auto-Fill Checkbox
                auto_var = tk.BooleanVar(value=True if not init_name else False)
                ttk.Checkbutton(frm, text="Auto-fill connected linear pathway", variable=auto_var).pack(anchor="w", pady=5)
                
                def on_ok():
                    res["name"] = name_var.get().strip()
                    res["auto_fill"] = auto_var.get()
                    res["ok"] = True
                    dlg.destroy()
                
                def on_cancel():
                    dlg.destroy()
                    
                btn_frm = ttk.Frame(frm)
                btn_frm.pack(fill="x", pady=(15, 0))
                ttk.Button(btn_frm, text="Cancel", command=on_cancel).pack(side="right")
                ttk.Button(btn_frm, text="Apply", command=on_ok).pack(side="right", padx=10)
                
                self._plot_window.wait_window(dlg)
                
                if res["ok"]:
                    e_data["color"] = res["color"]
                    e_data["path_label"] = res["name"]
                    fill_edges = {edge_key}
                    
                    if res["auto_fill"] and res["name"]:
                        def get_in_out(node_str):
                            ins = [k for k in self.edge_links if k.endswith(f"_{node_str}")]
                            outs = [k for k in self.edge_links if k.startswith(f"{node_str}_")]
                            return ins, outs
                        
                        u, v = edge_key.split("_")
                        # Forward trace
                        curr = v
                        while True:
                            ins, outs = get_in_out(curr)
                            if len(outs) == 1:
                                nxt_edge = outs[0]
                                fill_edges.add(nxt_edge)
                                curr = nxt_edge.split("_")[1]
                            else: break
                            
                        # Backward trace
                        curr = u
                        while True:
                            ins, outs = get_in_out(curr)
                            if len(ins) == 1:
                                prv_edge = ins[0]
                                fill_edges.add(prv_edge)
                                curr = prv_edge.split("_")[0]
                            else: break

                    path_label = res["name"]
                    for e in fill_edges:
                        if e not in self.edge_links: continue
                        self.edge_links[e]["color"] = res["color"]
                        self.edge_links[e]["path_label"] = path_label
                        
                    self._render_plot()
        
        def _asset_bbox_data(ai):
            """Return (x0,y0,x1,y1) data-space extent for asset image."""
            try:
                ab = asset_artists.get(ai)
                if ab is None:
                    return None
                fig.canvas.draw()
                renderer = fig.canvas.get_renderer()
                bbox = ab.get_window_extent(renderer=renderer)
                inv = ax.transData.inverted()
                (x0, y0) = inv.transform((bbox.x0, bbox.y0))
                (x1, y1) = inv.transform((bbox.x1, bbox.y1))
                if x0 > x1: x0, x1 = x1, x0
                if y0 > y1: y0, y1 = y1, y0
                return (x0, y0, x1, y1)
            except Exception:
                return None

        def _draw_selection_handles():
            """Overlay PPT-like selection handles for the selected image asset."""
            self._asset_handle_info = None
            ai = self._selected_asset_idx
            if ai is None or ai not in asset_artists:
                return
            bb = _asset_bbox_data(ai)
            if bb is None:
                return
            x0, y0, x1, y1 = bb
            from matplotlib.patches import Rectangle, Circle
            # Dashed selection rectangle
            ax.add_patch(Rectangle(
                (x0, y0), x1 - x0, y1 - y0,
                fill=False, linestyle=(0, (5, 3)),
                edgecolor="#2563eb", lw=1.3, zorder=11.0,
            ))
            hw = (x1 - x0) * 0.055
            hh = (y1 - y0) * 0.055
            cx = (x0 + x1) * 0.5
            cy = (y0 + y1) * 0.5
            handles = {
                "nw": (x0, y1), "n": (cx, y1), "ne": (x1, y1),
                "w": (x0, cy),                  "e": (x1, cy),
                "sw": (x0, y0), "s": (cx, y0), "se": (x1, y0),
            }
            for (hxy) in handles.values():
                hx, hy = hxy
                ax.add_patch(Rectangle(
                    (hx - hw, hy - hh), hw * 2, hh * 2,
                    fill=True, facecolor="white", edgecolor="#2563eb", lw=1.1, zorder=11.2,
                ))
            # Rotate handle
            rh_y = y1 + (y1 - y0) * 0.18
            rh_x = cx
            ax.plot([cx, cx], [y1, rh_y], color="#2563eb", lw=1.1, zorder=11.1)
            ax.add_patch(Circle(
                (rh_x, rh_y), radius=max(hw, hh) * 1.2,
                fill=True, facecolor="white", edgecolor="#2563eb", lw=1.1, zorder=11.3,
            ))
            self._asset_handle_info = {
                "ai": ai, "bbox": bb, "hw": hw, "hh": hh,
                "handles": handles,
                "rotate": (rh_x, rh_y, max(hw, hh) * 1.2),
            }

        # Always draw handles if we have a selection (before binding events)
        if self._selected_asset_idx is not None:
            try:
                _draw_selection_handles()
            except Exception:
                pass

        def _hit_handle(xd, yd):
            """Return ('handle_name', hx, hy) if (xd,yd) is over a handle of selected asset."""
            info = self._asset_handle_info
            if not info:
                return None
            hw, hh = info["hw"], info["hh"]
            for name, (hx, hy) in info["handles"].items():
                if (hx - hw * 1.4) <= xd <= (hx + hw * 1.4) and (hy - hh * 1.4) <= yd <= (hy + hh * 1.4):
                    return (name, hx, hy)
            rx, ry, rr = info["rotate"]
            if ((xd - rx) ** 2 + (yd - ry) ** 2) <= (rr * 1.2) ** 2:
                return ("rotate", rx, ry)
            return None

        def _hit_asset(xd, yd):
            """Return asset index if (xd,yd) is over an image asset (bbox test)."""
            # Prefer selected asset if hit
            if self._selected_asset_idx is not None and self._selected_asset_idx in asset_artists:
                bb = _asset_bbox_data(self._selected_asset_idx)
                if bb and bb[0] <= xd <= bb[2] and bb[1] <= yd <= bb[3]:
                    return self._selected_asset_idx
            for ai in asset_artists.keys():
                bb = _asset_bbox_data(ai)
                if bb and bb[0] <= xd <= bb[2] and bb[1] <= yd <= bb[3]:
                    return ai
            return None

        def _ccc_axis_hide_bundle(hide=True):
            bundle = aks_axis_art.get("arrow")
            txt = aks_axis_art.get("text")
            handle = aks_axis_art.get("handle")
            arts = []
            if isinstance(bundle, (tuple, list)):
                arts.extend(bundle)
            elif bundle is not None:
                arts.append(bundle)
            if txt is not None:
                arts.append(txt)
            if handle is not None:
                arts.append(handle)
            for art in arts:
                if art is not None:
                    try:
                        art.set_visible(not hide)
                    except Exception:
                        pass

        def _ccc_axis_begin_drag(event):
            trans_blend = aks_axis_art.get("trans")
            axis_data_x = float(aks_axis_art.get("axis_x", 0.0))
            if trans_blend is None:
                import matplotlib.transforms as mtransforms
                trans_blend = mtransforms.blended_transform_factory(ax.transData, ax.transAxes)
            inv_data = ax.transData.inverted()
            click_x, _ = inv_data.transform((event.x, event.y))
            self._aks_axis_dragging = True
            self._aks_axis_drag_dx = float(click_x) - axis_data_x
            self._ccc_axis_drag_x = axis_data_x
            _ccc_axis_hide_bundle(True)
            self._ghost_axis = ax.scatter(
                [axis_data_x],
                [0.5],
                s=140,
                c="#9ca3af",
                alpha=0.55,
                edgecolors="#ffffff",
                linewidths=0.8,
                transform=trans_blend,
                clip_on=False,
                zorder=30,
            )
            fig.canvas.draw_idle()

        def on_press(event):
            if getattr(event, "inaxes", None) and event.button == 1:
                # Check for step line dragging
                try:
                    for art in event.inaxes.get_children():
                        step_idx = getattr(art, "_pes_step_idx", None)
                        step_type = getattr(art, "_pes_step_art", None)
                        if step_idx is not None:
                            if step_type == "line" and getattr(art, "contains", lambda x: (False,))(event)[0]:
                                self._drag_step_idx = step_idx
                                self._drag_step_y0 = event.ydata
                                
                                import json
                                try:
                                    s = json.loads(self._aks_step_labels.get() or "[]")
                                    if 0 <= step_idx < len(s):
                                        s_id = s[step_idx].get("start")
                                        e_id = s[step_idx].get("end")
                                        px_start = px.get(s_id)
                                        px_end = px.get(e_id)
                                        if px_start is not None and px_end is not None:
                                            # Hide the actual step line graphics to mimic ghost drag behavior
                                            for child in event.inaxes.get_children():
                                                if getattr(child, "_pes_step_idx", None) == step_idx:
                                                    child.set_visible(False)
                                                    if getattr(child, "_pes_step_art", None) == "text":
                                                        self._ghost_step_txt_dx = child.get_position()[0] - (px_start + px_end) * 0.5
                                                        self._ghost_step_txt_dy = child.get_position()[1] - event.ydata
                                                        self._ghost_step_txt_str = child.get_text()
                                                        self._ghost_step_txt_color = child.get_color()
                                            
                                            self._ghost_step_line, = ax.plot(
                                                [px_start, px_end],
                                                [event.ydata, event.ydata],
                                                color="#9ca3af",
                                                linestyle="--",
                                                lw=2.5,
                                                zorder=30
                                            )
                                            self._ghost_step_pt = ax.scatter(
                                                [(px_start + px_end) * 0.5],
                                                [event.ydata],
                                                s=140,
                                                c="#9ca3af",
                                                alpha=0.6,
                                                zorder=31
                                            )
                                            if hasattr(self, "_ghost_step_txt_str"):
                                                self._ghost_step_text = ax.text(
                                                    (px_start + px_end) * 0.5 + getattr(self, "_ghost_step_txt_dx", 0.0),
                                                    event.ydata + getattr(self, "_ghost_step_txt_dy", 0.0),
                                                    self._ghost_step_txt_str,
                                                    ha="center", va="top",
                                                    fontsize=11, color=getattr(self, "_ghost_step_txt_color", "#000000"),
                                                    fontweight="bold", zorder=31, alpha=0.5
                                                )
                                            fig.canvas.draw_idle()
                                except Exception: pass
                                return
                            elif step_type == "text" and getattr(art, "contains", lambda x: (False,))(event)[0]:
                                self._drag_step_text_idx = step_idx
                                self._drag_step_text_x0 = event.xdata
                                self._drag_step_text_y0 = event.ydata
                                return
                except Exception: pass
                
            # Check left axis drag FIRST, without requiring event.inaxes
            if (
                aks_axis_art.get("axis_x") is not None
                and event.button == 1
                and not event.dblclick
                and getattr(event, "key", "") != "control"
            ):
                try:
                    axis_data_x = aks_axis_art.get("axis_x")
                    trans_blend = aks_axis_art.get("trans")
                    xmin = aks_axis_art.get("xmin", 0.0)
                    if axis_data_x is not None and trans_blend is not None:
                        picked_axis = (
                            self._ccc_axis_contains(event, aks_axis_art)
                            or self._ccc_axis_pick(event, axis_data_x, trans_blend, xmin)
                        )
                        if picked_axis:
                            _ccc_axis_begin_drag(event)
                            return
                except Exception:
                    pass

            if not getattr(event, "inaxes", None):
                return

            if event.button == 3:
                ai_hit = _hit_asset(event.xdata, event.ydata)
                if ai_hit is not None:
                    self._right_drag_asset_idx = ai_hit
                    self._right_drag_start = (event.xdata, event.ydata)
                    self._right_drag_asset_base = (
                        float(self._plot_assets[ai_hit].get("x", 0.0)),
                        float(self._plot_assets[ai_hit].get("y", 0.0)),
                    )
                    self._drag_asset_last_xy = (
                        float(self._plot_assets[ai_hit].get("x", 0.0)),
                        float(self._plot_assets[ai_hit].get("y", 0.0)),
                    )
                    self._prepare_plot_asset_ghost_buffer(asset_artists, ai_hit)
                    self._right_drag_moved = False
                    self._right_drag_event = event
                    return
                self._right_click_non_asset = event
                return
            if event.button != 1 or event.dblclick or getattr(event, "key", "") == "control":
                return
            if not event.inaxes: return
            
            if getattr(self, "_show_diff_bars", None) and self._show_diff_bars.get():
                try:
                    dbx = getattr(self, "_diff_bar_x_placed", None)
                    if dbx is not None and abs(event.xdata - dbx) < 0.25:
                        self._diff_bar_dragging = True
                        self._diff_bar_drag_dx = event.xdata - dbx
                        y_val = event.ydata if event.ydata is not None else 0.0
                        self._ghost_diff_bar = ax.scatter([dbx], [y_val], s=140, c="#9ca3af", alpha=0.55, edgecolors="#ffffff", zorder=100)
                        fig.canvas.draw_idle()
                        return
                except Exception:
                    pass

            # Resize/rotate handle hit for currently selected asset
            handle = _hit_handle(event.xdata, event.ydata)
            if handle is not None and self._selected_asset_idx in asset_artists:
                ai = self._selected_asset_idx
                self._resize_asset_idx = ai
                self._resize_handle = handle[0]
                self._resize_start = (event.xdata, event.ydata)
                self._resize_init_zoom = float(self._plot_assets[ai].get("zoom", 0.14))
                self._resize_init_angle = float(self._plot_assets[ai].get("angle", 0.0))
                self._resize_center = (
                    float(self._plot_assets[ai].get("x", 0.0)),
                    float(self._plot_assets[ai].get("y", 0.0)),
                )
                return

            # Image body hit -> select (if not selected) or begin move drag
            ai_hit = _hit_asset(event.xdata, event.ydata)
            if ai_hit is not None:
                if self._selected_asset_idx != ai_hit:
                    self._selected_asset_idx = ai_hit
                    self._render_plot()
                    return
                axx = float(self._plot_assets[ai_hit].get("x", 0.0))
                ayy = float(self._plot_assets[ai_hit].get("y", 0.0))
                self._drag_asset_idx = ai_hit
                self._drag_start = (event.xdata, event.ydata)
                self._drag_asset_base = (axx, ayy)
                self._drag_asset_last_xy = (axx, ayy)
                self._prepare_plot_asset_ghost_buffer(asset_artists, ai_hit)
                self._asset_click_candidate = True
                return

            # Click outside any image - deselect
            if self._selected_asset_idx is not None:
                self._selected_asset_idx = None
                self._render_plot()
                return

            min_dist = float('inf')
            closest = None
            for sp in valid:
                dist = (event.xdata - px[sp["id"]])**2 + (event.ydata - py[sp["id"]])**2
                if dist < 0.2 and dist < min_dist:
                    min_dist = dist
                    closest = sp["id"]
            if closest is not None:
                self._drag_node = closest
                self._drag_start = (event.xdata, event.ydata)
                self._drag_base = self.pt_offsets.get(closest, (0.0, 0.0))
                
                # Render animation ghost preview
                yi = py[closest]
                xi = px[closest]
                kd = next((s.get("kind", "Intermediate") for s in valid if s["id"] == closest), "Intermediate")
                marker = "o"
                
                if is_classic:
                    self._ghost_artist, = ax.plot([xi - bar_w, xi + bar_w], [yi, yi], color='#9ca3af', lw=bar_lw, alpha=0.6, zorder=10)
                else:
                    self._ghost_artist, = ax.plot([xi], [yi], marker=marker, markersize=12, color='#9ca3af', alpha=0.6, zorder=10)
                fig.canvas.draw_idle()
                
        def on_motion(event):
            if getattr(self, "_drag_step_text_idx", None) is not None and event.inaxes:
                dx = event.xdata - self._drag_step_text_x0
                dy = event.ydata - self._drag_step_text_y0
                self._drag_step_text_x0 = event.xdata
                self._drag_step_text_y0 = event.ydata
                
                for art in event.inaxes.get_children():
                    if getattr(art, "_pes_step_idx", None) == self._drag_step_text_idx and getattr(art, "_pes_step_art", None) == "text":
                        art.set_position((art.get_position()[0] + dx, art.get_position()[1] + dy))
                fig.canvas.draw_idle()
                fig.canvas.draw_idle()
                return
                
            if getattr(self, "_drag_step_idx", None) is not None and event.inaxes:
                if hasattr(self, "_ghost_step_line") and hasattr(self, "_ghost_step_pt"):
                    self._ghost_step_line.set_ydata([event.ydata, event.ydata])
                    self._ghost_step_pt.set_offsets([[(self._ghost_step_line.get_xdata()[0] + self._ghost_step_line.get_xdata()[1]) * 0.5, event.ydata]])
                    if hasattr(self, "_ghost_step_text"):
                        self._ghost_step_text.set_y(event.ydata + getattr(self, "_ghost_step_txt_dy", 0.0))
                    fig.canvas.draw_idle()
                return
                
            if getattr(self, "_aks_axis_dragging", False):
                try:
                    if event.x is None or event.y is None: return
                    inv_data = ax.transData.inverted()
                    click_x, _ = inv_data.transform((event.x, event.y))
                    nx = float(click_x) - float(getattr(self, "_aks_axis_drag_dx", 0.0))
                    self._ccc_axis_drag_x = nx
                    if getattr(self, "_ghost_axis", None) is not None:
                        self._ghost_axis.set_offsets([[nx, 0.5]])
                    fig.canvas.draw_idle()
                except Exception:
                    pass
                return
            elif getattr(self, "_diff_bar_dragging", False):
                try:
                    nx = float(event.xdata) - float(getattr(self, "_diff_bar_drag_dx", 0.0))
                    if getattr(self, "_ghost_diff_bar", None) is not None:
                        y_val = event.ydata if event.ydata is not None else 0.0
                        self._ghost_diff_bar.set_offsets([[nx, y_val]])
                        fig.canvas.draw_idle()
                except Exception:
                    pass
                return
            # Resize / rotate a selected image via handle drag
            if getattr(self, "_resize_asset_idx", None) is not None and getattr(event, "inaxes", None):
                ai = self._resize_asset_idx
                h = getattr(self, "_resize_handle", "")
                cx, cy = self._resize_center
                if h == "rotate":
                    import math
                    ang = math.degrees(math.atan2(event.ydata - cy, event.xdata - cx))
                    # top = 90deg in atan2 -> rotation angle offset so dragging upward = 0
                    new_angle = (90.0 - ang) % 360.0
                    self._plot_assets[ai]["angle"] = float(new_angle)
                    self._render_plot()
                else:
                    sx, sy = self._resize_start
                    d0 = max(1e-6, ((sx - cx) ** 2 + (sy - cy) ** 2) ** 0.5)
                    d1 = ((event.xdata - cx) ** 2 + (event.ydata - cy) ** 2) ** 0.5
                    factor = d1 / d0
                    new_zoom = max(0.04, min(2.0, self._resize_init_zoom * factor))
                    self._plot_assets[ai]["zoom"] = float(new_zoom)
                    self._render_plot()
                return
            if getattr(self, "_right_drag_asset_idx", None) is not None and getattr(event, "inaxes", None):
                ai = self._right_drag_asset_idx
                bx, by = getattr(self, "_right_drag_asset_base", (0.0, 0.0))
                dx = event.xdata - self._right_drag_start[0]
                dy = event.ydata - self._right_drag_start[1]
                if (dx * dx + dy * dy) > 0.0025:
                    self._right_drag_moved = True
                nx, ny = bx + dx, by + dy
                self._drag_asset_last_xy = (float(nx), float(ny))
                self._ensure_plot_asset_drag_ghost(ax, asset_artists, ai, nx, ny)
                try:
                    fig.canvas.draw()
                except Exception:
                    pass
                return
            if getattr(self, "_drag_asset_idx", None) is not None and getattr(event, "inaxes", None):
                ai = self._drag_asset_idx
                bx, by = getattr(self, "_drag_asset_base", (0.0, 0.0))
                dx = event.xdata - self._drag_start[0]
                dy = event.ydata - self._drag_start[1]
                if (dx * dx + dy * dy) > 0.0025:
                    self._asset_click_candidate = False
                nx, ny = bx + dx, by + dy
                self._drag_asset_last_xy = (float(nx), float(ny))
                self._ensure_plot_asset_drag_ghost(ax, asset_artists, ai, nx, ny)
                try:
                    fig.canvas.draw()
                except Exception:
                    pass
                return
            if not getattr(self, "_drag_node", None) or not getattr(event, "inaxes", None): return
            if not hasattr(self, "_ghost_artist"): return
            
            dx = event.xdata - self._drag_start[0]
            new_x = px[self._drag_node] + dx
            
            if is_classic:
                self._ghost_artist.set_xdata([new_x - bar_w, new_x + bar_w])
            else:
                self._ghost_artist.set_xdata([new_x])
                
            fig.canvas.draw_idle()

        def on_scroll(event):
            if not event.inaxes:
                return
            step = getattr(event, "step", 0)
            if step == 0:
                btn = str(getattr(event, "button", "")).lower()
                step = 1 if "up" in btn else (-1 if "down" in btn else 0)
            if step == 0:
                return
            for ai, _ab in asset_artists.items():
                axx = float(self._plot_assets[ai].get("x", 0.0))
                ayy = float(self._plot_assets[ai].get("y", 0.0))
                if getattr(self, "_drag_asset_idx", None) == ai or getattr(self, "_right_drag_asset_idx", None) == ai:
                    lxy = getattr(self, "_drag_asset_last_xy", None)
                    if lxy is not None:
                        axx, ayy = float(lxy[0]), float(lxy[1])
                if ((event.xdata - axx) ** 2 + (event.ydata - ayy) ** 2) < 0.85:
                    z = float(self._plot_assets[ai].get("zoom", 0.14))
                    fac = 1.10 if step > 0 else 0.90
                    self._plot_assets[ai]["zoom"] = max(0.04, min(2.0, z * fac))
                    self._render_plot()
                    return
            
        def on_release(event):
            if getattr(event, "inaxes", None):
                import json
                needs_save = False
                try:
                    s = json.loads(self._aks_step_labels.get() or "[]")
                    for art in event.inaxes.get_children():
                        idx = getattr(art, "_pes_step_idx", None)
                        if idx is not None and getattr(art, "_pes_step_art", None) == "text":
                            def_x = getattr(art, "_pes_step_def_x", None)
                            def_y = getattr(art, "_pes_step_def_y", None)
                            if def_x is not None and def_y is not None:
                                curr_x, curr_y = art.get_position()
                                dx, dy = curr_x - def_x, curr_y - def_y
                                old_dx = float(s[idx].get("text_dx", 0.0))
                                old_dy = float(s[idx].get("text_dy", 0.0))
                                if abs(dx - old_dx) > 0.001 or abs(dy - old_dy) > 0.001:
                                    s[idx]["text_dx"] = dx
                                    s[idx]["text_dy"] = dy
                                    needs_save = True
                    if needs_save:
                        self._aks_step_labels.set(json.dumps(s))
                except Exception: pass

            if getattr(self, "_drag_step_text_idx", None) is not None:
                self._drag_step_text_idx = None
                return
                
            if getattr(self, "_drag_step_idx", None) is not None:
                dy = event.ydata - self._drag_step_y0 if event.ydata else 0.0
                idx = self._drag_step_idx
                self._drag_step_idx = None
                
                if hasattr(self, "_ghost_step_line"):
                    self._ghost_step_line.remove()
                    delattr(self, "_ghost_step_line")
                if hasattr(self, "_ghost_step_pt"):
                    self._ghost_step_pt.remove()
                    delattr(self, "_ghost_step_pt")
                if hasattr(self, "_ghost_step_text"):
                    self._ghost_step_text.remove()
                    delattr(self, "_ghost_step_text")
                    if hasattr(self, "_ghost_step_txt_str"): delattr(self, "_ghost_step_txt_str")
                
                import json
                try:
                    s = json.loads(self._aks_step_labels.get() or "[]")
                    if 0 <= idx < len(s):
                        s[idx]["y_offset"] = float(s[idx].get("y_offset", 0.0)) + dy
                        self._aks_step_labels.set(json.dumps(s))
                        self._render_plot()
                except Exception: pass
                return
                
            if getattr(self, "_aks_axis_dragging", False):
                self._aks_axis_dragging = False
                try:
                    nx = getattr(self, "_ccc_axis_drag_x", None)
                    if nx is None and getattr(self, "_ghost_axis", None) is not None:
                        off = self._ghost_axis.get_offsets()
                        nx = float(off[0, 0]) if len(off) else None
                    if nx is not None:
                        self._aks_axis_x.set(float(nx))
                        self._ccc_axis_user_placed.set(True)
                except Exception:
                    pass
                self._aks_axis_drag_dx = 0.0
                self._ccc_axis_drag_x = None
                g = getattr(self, "_ghost_axis", None)
                if g is not None:
                    try:
                        g.remove()
                    except Exception:
                        pass
                    self._ghost_axis = None
                
                _ccc_axis_hide_bundle(False)
                self._render_plot()
                return
            if getattr(self, "_diff_bar_dragging", False):
                self._diff_bar_dragging = False
                g = getattr(self, "_ghost_diff_bar", None)
                if g is not None:
                    try:
                        off = g.get_offsets()
                        if len(off):
                            self._diff_bar_x_placed = float(off[0, 0])
                        g.remove()
                    except Exception:
                        pass
                    self._ghost_diff_bar = None
                    self._render_plot()
                return
            if getattr(self, "_resize_asset_idx", None) is not None:
                self._resize_asset_idx = None
                self._resize_handle = None
                self._render_plot()
                return
            if getattr(self, "_right_drag_asset_idx", None) is not None:
                ai = self._right_drag_asset_idx
                moved = bool(getattr(self, "_right_drag_moved", False))
                ev0 = getattr(self, "_right_drag_event", event)
                self._right_drag_asset_idx = None
                self._right_drag_moved = False
                self._right_drag_event = None
                self._remove_plot_asset_drag_ghost()
                if not moved:
                    self._asset_context_menu(ev0, ai)
                elif moved and 0 <= ai < len(self._plot_assets):
                    lxy = getattr(self, "_drag_asset_last_xy", None)
                    if lxy is not None:
                        self._plot_assets[ai]["x"] = float(lxy[0])
                        self._plot_assets[ai]["y"] = float(lxy[1])
                    self._render_plot()
                self._drag_asset_last_xy = None
                self._clear_plot_asset_ghost_buffer()
                return
            if getattr(self, "_right_click_non_asset", None) is not None and event.button == 3:
                ev0 = self._right_click_non_asset
                self._right_click_non_asset = None
                nearest_sid = None
                nearest_d = 1e9
                y_vals = list(py.values())
                y_span0 = max(1.0, max(y_vals) - min(y_vals)) if y_vals else 1.0
                label_off0 = max(0.55, y_span0 * 0.055)
                lm = (self._plot_labels.get() or "Clean").strip().lower()
                for sp in valid:
                    sid = sp["id"]
                    xi, yi = px[sid], py[sid]
                    cand = [(xi, yi)]
                    if lm != "off":
                        cand.append((xi, yi - label_off0 * 0.95))
                        cand.append((xi, yi + label_off0 * 0.95))
                    for cx, cy in cand:
                        d = (event.xdata - cx) ** 2 + (event.ydata - cy) ** 2
                        if d < nearest_d:
                            nearest_d = d
                            nearest_sid = sid
                if nearest_d > 4.0:
                    nearest_sid = None
                self._open_plot_context_menu(ev0, nearest_sid, float(event.xdata), float(event.ydata))
                return
            if getattr(self, "_drag_asset_idx", None) is not None:
                ai = self._drag_asset_idx
                moved = not bool(getattr(self, "_asset_click_candidate", False))
                self._remove_plot_asset_drag_ghost()
                if moved and ai is not None and 0 <= ai < len(self._plot_assets):
                    lxy = getattr(self, "_drag_asset_last_xy", None)
                    if lxy is not None:
                        self._plot_assets[ai]["x"] = float(lxy[0])
                        self._plot_assets[ai]["y"] = float(lxy[1])
                    self._render_plot()
                else:
                    try:
                        fig.canvas.draw_idle()
                    except Exception:
                        pass
                self._drag_asset_idx = None
                self._asset_click_candidate = False
                self._drag_asset_last_xy = None
                self._clear_plot_asset_ghost_buffer()
                return
            if getattr(self, "_ghost_artist", None):
                self._ghost_artist.remove()
                self._ghost_artist = None
                
            if getattr(self, "_drag_node", None) and getattr(event, "inaxes", None):
                dx = event.xdata - self._drag_start[0]
                bx, by = self._drag_base
                # Lock Y coordinate (by) strictly from moving so the numbers never erroneously mutate!
                self.pt_offsets[self._drag_node] = (bx + dx, by)
                self._drag_node = None
                self._render_plot() # Execute topology recalculation exactly once upon drop
            elif hasattr(self, "_drag_node"):
                self._drag_node = None

        fig.canvas.mpl_connect("pick_event", _on_pick)
        fig.canvas.mpl_connect("button_press_event", on_press)
        fig.canvas.mpl_connect("motion_notify_event", on_motion)
        fig.canvas.mpl_connect("button_release_event", on_release)
        fig.canvas.mpl_connect("scroll_event", on_scroll)

        # Plot only in popup window (no embedded bottom PES panel).
        self._open_large_plot_window(fig)

    def _show_kinetics_calculator(self):
        parent = self._plot_window if getattr(self, "_plot_window", None) and self._plot_window.winfo_exists() else self.main_frame.winfo_toplevel()
        dlg = tk.Toplevel(parent)
        dlg.title("Eyring Kinetics Calculator")
        dlg.geometry("500x520")
        dlg.minsize(450, 480)
        dlg.transient(parent)
        
        main_frame = ttk.Frame(dlg, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(main_frame, text="Eyring Equation Calculator", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W, pady=(0, 10))
        ttk.Label(main_frame, text="Calculates rate constant k and half-life t\u2081\u2082 from activation free energy \u0394G\u2021\nor calculates \u0394G\u2021 from a target half-life.", wraplength=450).pack(anchor=tk.W, fill=tk.X, pady=(0, 15))
        
        calc_mode = tk.StringVar(value="halflife")
        temp_val = tk.StringVar(value="298.15")
        temp_unit = tk.StringVar(value="K")
        barrier_val = tk.StringVar(value="20.0")
        barrier_unit = tk.StringVar(value="kcal/mol")
        hl_val = tk.StringVar(value="12")
        hl_unit = tk.StringVar(value="hours")
        kappa_val = tk.StringVar(value="1.0")
        
        result_text = tk.StringVar()
        
        def _update_calc(*args):
            try:
                T_input = float(temp_val.get())
                T = T_input if temp_unit.get() == "K" else T_input + 273.15
                kappa = float(kappa_val.get())
                if T <= 0 or kappa <= 0:
                    result_text.set("Invalid Temperature or \u03ba.")
                    return
                
                k_B = 1.380649e-23
                h = 6.62607015e-34
                R = 8.314462618
                
                mode = calc_mode.get()
                if mode == "halflife":
                    b_input = float(barrier_val.get())
                    dG_J = b_input * 4184.0 if barrier_unit.get() == "kcal/mol" else b_input * 1000.0
                    
                    import math
                    exponent = -dG_J / (R * T)
                    if exponent < -700: exponent = -700
                    if exponent > 700: exponent = 700
                    
                    k = kappa * (k_B * T / h) * math.exp(exponent)
                    if k <= 0:
                        result_text.set("Rate constant is effectively 0.")
                        return
                        
                    t_half_s = math.log(2) / k
                    
                    if t_half_s < 1e-3: res_str = f"Half-life: {t_half_s*1e6:.2f} \u03bcs"
                    elif t_half_s < 1.0: res_str = f"Half-life: {t_half_s*1000:.2f} ms"
                    elif t_half_s < 60: res_str = f"Half-life: {t_half_s:.2f} seconds"
                    elif t_half_s < 3600: res_str = f"Half-life: {t_half_s/60:.2f} minutes"
                    elif t_half_s < 86400: res_str = f"Half-life: {t_half_s/3600:.2f} hours"
                    elif t_half_s < 31536000: res_str = f"Half-life: {t_half_s/86400:.2f} days"
                    else: res_str = f"Half-life: {t_half_s/31536000:.2e} years"
                        
                    result_text.set(f"Rate constant k = {k:.2e} s\u207b\u00b9\n{res_str}")
                else:
                    h_input = float(hl_val.get())
                    u = hl_unit.get()
                    if u == "seconds": mul = 1.0
                    elif u == "minutes": mul = 60.0
                    elif u == "hours": mul = 3600.0
                    elif u == "days": mul = 86400.0
                    elif u == "years": mul = 31536000.0
                    else: mul = 1.0
                    
                    t_half_s = h_input * mul
                    if t_half_s <= 0:
                        result_text.set("Invalid half-life.")
                        return
                    
                    import math
                    k = math.log(2) / t_half_s
                    arg = (k * h) / (kappa * k_B * T)
                    if arg <= 0:
                        result_text.set("Calculation error.")
                        return
                    dG_J = -R * T * math.log(arg)
                    
                    dG_kcal = dG_J / 4184.0
                    dG_kJ = dG_J / 1000.0
                    
                    result_text.set(f"Activation Free Energy \u0394G\u2021 = {dG_kcal:.2f} kcal/mol\n({dG_kJ:.2f} kJ/mol)")
                    
            except ValueError:
                result_text.set("Please enter valid numbers.")
            except Exception as e:
                result_text.set(f"Error: {e}")

        temp_val.trace_add("write", _update_calc)
        temp_unit.trace_add("write", _update_calc)
        calc_mode.trace_add("write", _update_calc)
        barrier_val.trace_add("write", _update_calc)
        barrier_unit.trace_add("write", _update_calc)
        hl_val.trace_add("write", _update_calc)
        hl_unit.trace_add("write", _update_calc)
        kappa_val.trace_add("write", _update_calc)
        
        grid_frame = ttk.Frame(main_frame)
        grid_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(grid_frame, text="Temperature (T):").grid(row=0, column=0, sticky=tk.W, pady=2)
        ttk.Entry(grid_frame, textvariable=temp_val, width=12).grid(row=0, column=1, padx=5, pady=2)
        ttk.Combobox(grid_frame, textvariable=temp_unit, values=["K", "°C"], state="readonly", width=5).grid(row=0, column=2, pady=2)
        
        ttk.Label(grid_frame, text="Transmission (\u03ba):").grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Entry(grid_frame, textvariable=kappa_val, width=12).grid(row=1, column=1, padx=5, pady=2)
        
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)
        
        mode_frame = ttk.Frame(main_frame)
        mode_frame.pack(fill=tk.X)
        ttk.Radiobutton(mode_frame, text="Calculate Half-Life from Barrier", variable=calc_mode, value="halflife").pack(anchor=tk.W)
        ttk.Radiobutton(mode_frame, text="Calculate Barrier from Half-Life", variable=calc_mode, value="barrier").pack(anchor=tk.W)
        
        input_frame = ttk.Frame(main_frame)
        input_frame.pack(fill=tk.X, pady=10)
        
        def _toggle_inputs(*args):
            mode = calc_mode.get()
            if mode == "halflife":
                b_ent.config(state="normal")
                b_cb.config(state="readonly")
                h_ent.config(state="disabled")
                h_cb.config(state="disabled")
            else:
                b_ent.config(state="disabled")
                b_cb.config(state="disabled")
                h_ent.config(state="normal")
                h_cb.config(state="readonly")
        calc_mode.trace_add("write", _toggle_inputs)
        
        ttk.Label(input_frame, text="Barrier \u0394G\u2021:").grid(row=0, column=0, sticky=tk.W, pady=2)
        b_ent = ttk.Entry(input_frame, textvariable=barrier_val, width=12)
        b_ent.grid(row=0, column=1, padx=5, pady=2)
        b_cb = ttk.Combobox(input_frame, textvariable=barrier_unit, values=["kcal/mol", "kJ/mol"], state="readonly", width=9)
        b_cb.grid(row=0, column=2, pady=2)
        
        ttk.Label(input_frame, text="Half-Life t\u2081\u2082:").grid(row=1, column=0, sticky=tk.W, pady=2)
        h_ent = ttk.Entry(input_frame, textvariable=hl_val, width=12)
        h_ent.grid(row=1, column=1, padx=5, pady=2)
        h_cb = ttk.Combobox(input_frame, textvariable=hl_unit, values=["seconds", "minutes", "hours", "days", "years"], state="readonly", width=9)
        h_cb.grid(row=1, column=2, pady=2)
        
        _toggle_inputs()
        
        res_frame = ttk.LabelFrame(main_frame, text="Result", padding=15)
        res_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        ttk.Label(res_frame, textvariable=result_text, font=("Segoe UI", 12, "bold"), justify=tk.CENTER).pack(anchor=tk.CENTER, expand=True)
        
        _update_calc()
        
        def _on_ok():
            try:
                # Update temperature label
                T_input = float(temp_val.get())
                u_t = temp_unit.get()
                self._aks_temp_label.set(f"T = {T_input} {u_t}")
                
                # Update half-life for the barrier limit button
                mode = calc_mode.get()
                if mode == "halflife":
                    k_B = 1.380649e-23
                    h = 6.62607015e-34
                    R = 8.314462618
                    T = T_input if u_t == "K" else T_input + 273.15
                    kappa = float(kappa_val.get())
                    b_input = float(barrier_val.get())
                    dG_J = b_input * 4184.0 if barrier_unit.get() == "kcal/mol" else b_input * 1000.0
                    import math
                    exponent = -dG_J / (R * T)
                    if exponent < -700: exponent = -700
                    if exponent > 700: exponent = 700
                    k = kappa * (k_B * T / h) * math.exp(exponent)
                    if k > 0:
                        self._kinetics_target_half_life_s = math.log(2) / k
                else:
                    h_input = float(hl_val.get())
                    u = hl_unit.get()
                    if u == "seconds": mul = 1.0
                    elif u == "minutes": mul = 60.0
                    elif u == "hours": mul = 3600.0
                    elif u == "days": mul = 86400.0
                    elif u == "years": mul = 31536000.0
                    else: mul = 1.0
                    self._kinetics_target_half_life_s = h_input * mul
                
                # We update the plot and close
                self._render_plot()
                dlg.destroy()
            except Exception:
                dlg.destroy()
                
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="Apply to Panel \u2714", command=_on_ok).pack(side=tk.RIGHT)
        
        # Increase window height slightly for the button
        dlg.geometry("500x480")
        dlg.minsize(450, 420)

    def _open_large_plot_window(self, fig):
        self._current_plot_fig = fig
        root = self.main_frame.winfo_toplevel()
        if self._plot_window is None or not self._plot_window.winfo_exists():
            self._plot_window = tk.Toplevel(root)
            self._plot_window.title("PES Plot - Large View")
            self._plot_window.geometry("1280x820")
            self._plot_window.minsize(900, 600)
        else:
            for w in self._plot_window.winfo_children():
                w.destroy()
            self._plot_window.deiconify()
        
        self._plot_window.lift()
        self._plot_window.focus_force()

        bottom_frame = ttk.Frame(self._plot_window)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)

        ctrl = ttk.Frame(self._plot_window, padding=(8, 8, 8, 0))
        ctrl.pack(fill=tk.X)
        ttk.Label(ctrl, text="Style").pack(side=tk.LEFT, padx=(0, 4))
        
        style_mb = ttk.Menubutton(ctrl, textvariable=self._plot_style, width=24)
        style_menu = tk.Menu(style_mb, tearoff=0)
        style_mb.config(menu=style_menu)
        
        def _on_style_selected():
            self._auto_color_mode_from_style()
            self._render_plot()

        def _show_style_info():
            st = (self._plot_style.get() or "").strip().lower()
            parent = self._plot_window if (self._plot_window and self._plot_window.winfo_exists()) else self.main_frame.winfo_toplevel()
            if "aks style" in st:
                msg = "AKS Style\n\nPlot Style by Akhilesh Sharma."
                messagebox.showinfo("Style Inspiration", msg, parent=parent)
            elif "classical: 1" in st:
                msg = (
                    "Classical Style 1\n\n"
                    "Classic PES line-bar representation.\n"
                    "Inspired by Dr. Nikunj Kumar.\n"
                    "Paper: https://www.sciencedirect.com/science/article/pii/S0021951724005001"
                )
                messagebox.showinfo("Style Inspiration", msg, parent=parent)
            elif "ccc" in st:
                dlg = tk.Toplevel(parent)
                dlg.title("Style Inspiration")
                dlg.geometry("500x200")
                dlg.transient(parent)
                dlg.grab_set()
                
                msg = (
                    "CCC / CCL view\n\n"
                    "Inspired by Nitish's catalytic-cycle and energy-profile figures\n"
                    "in ACS Catalysis:\n"
                    "https://pubs.acs.org/doi/full/10.1021/acscatal.5c07221"
                )
                ttk.Label(dlg, text=msg, justify=tk.CENTER).pack(pady=30, padx=20)
                btn_frame = ttk.Frame(dlg)
                btn_frame.pack(pady=10)
                
                def _open_link():
                    import webbrowser
                    webbrowser.open("https://pubs.acs.org/doi/full/10.1021/acscatal.5c07221")
                    dlg.destroy()
                    
                ttk.Button(btn_frame, text="Open Paper", command=_open_link).pack(side=tk.LEFT, padx=10)
                ttk.Button(btn_frame, text="OK", command=dlg.destroy).pack(side=tk.LEFT, padx=10)
            elif "classical" in st or "classic" in st:
                msg = "Classical Style\n\nClassic PES line-bar representation."
                messagebox.showinfo("Style Inspiration", msg, parent=parent)
            elif "sequential" in st:
                msg = "Sequential Style\n\nPathway-unrolled sequential rendering."
                messagebox.showinfo("Style Inspiration", msg, parent=parent)
            else:
                msg = "Plot Style\n\nStyle inspiration information not available."
                messagebox.showinfo("Style Inspiration", msg, parent=parent)
            
        for val in ["CCC: 1 Curved", "CCC: 2 Straight", "Classical: 1 Classic 1", "Classical: 2 Classic 2", "AKS Style"]:
            style_menu.add_radiobutton(label=val, variable=self._plot_style, value=val, command=_on_style_selected)
            
        seq_menu = tk.Menu(style_menu, tearoff=0)
        for val in ["Sequential: CCC Curved", "Sequential: CCC Straight", "Sequential: Classic 1", "Sequential: Classic 2"]:
            seq_menu.add_radiobutton(label=val, variable=self._plot_style, value=val, command=_on_style_selected)
            
        style_menu.add_cascade(label="Sequential...", menu=seq_menu)
        
        def _on_style_menu_post():
            count = len(getattr(self, "_pathway_options", []))
            if count > 1:
                style_menu.entryconfig("Sequential...", state="normal")
            else:
                style_menu.entryconfig("Sequential...", state="disabled")
                
        style_menu.configure(postcommand=_on_style_menu_post)
        style_mb.pack(side=tk.LEFT)
        ttk.Button(ctrl, text="i", width=2, command=_show_style_info).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(ctrl, text="Colours").pack(side=tk.LEFT, padx=(8, 4))
        cb_colors = ttk.Combobox(ctrl, textvariable=self._plot_colors_mode, values=["Gradient", "Normal", "Black"], state="readonly", width=10)
        cb_colors.pack(side=tk.LEFT, padx=(0, 0))
        self._plot_gmap_combo_large = ttk.Combobox(ctrl, textvariable=self._plot_gradient_map, values=["PeakMap", "viridis", "plasma", "inferno", "turbo", "cividis"], state="readonly", width=9)
        self._plot_gmap_combo_large.pack(side=tk.LEFT, padx=(4, 0))
        self._labels_btn_large = self._build_labels_menu_button(ctrl)
        self._labels_btn_large.pack(side=tk.LEFT, padx=(10, 4))
        ttk.Label(ctrl, text="Normal MOs").pack(side=tk.LEFT, padx=(10, 4))
        cb_mos = ttk.Combobox(ctrl, textvariable=self._plot_draw_mos, values=["Auto", "Show", "Hide"], state="readonly", width=8)
        cb_mos.pack(side=tk.LEFT)
        ttk.Label(ctrl, text="Grid").pack(side=tk.LEFT, padx=(10, 4))
        cb_grid = ttk.Combobox(
            ctrl,
            textvariable=self._grid_mode,
            values=["More Grids", "Less Grids", "Hide: All Grids", "Hide: Big Box", "Hide: All except Plot"],
            state="readonly",
            width=20,
        )
        cb_grid.pack(side=tk.LEFT)
        ttk.Separator(ctrl, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=2)
        
        db_frame = ttk.Frame(ctrl)
        db_frame.pack(side=tk.LEFT)
        chk_db = ttk.Checkbutton(db_frame, text="Δ Bars", variable=self._show_diff_bars)
        chk_db.pack(side=tk.LEFT, padx=(6, 0))
        
        if not hasattr(self, "_diff_bar_direction"):
            self._diff_bar_direction = tk.StringVar(value="Forward")
        self._plot_diff_dir_combo = ttk.Combobox(db_frame, textvariable=self._diff_bar_direction, values=["Forward", "Backward"], state="readonly", width=9)
        def _on_diff_dir_change(_e=None):
            self._diff_bar_label_xy = None
            self._diff_bar_x_placed = None
            self._render_plot()
        self._plot_diff_dir_combo.bind("<<ComboboxSelected>>", _on_diff_dir_change)
        
        lbl_diff_path = ttk.Label(db_frame, text="Δ path")
        self._plot_diff_path_combo = ttk.Combobox(db_frame, width=34, state="readonly")
        
        def _on_diff_bars_toggle():
            if self._show_diff_bars.get():
                self._plot_diff_dir_combo.pack(side=tk.LEFT, padx=(2, 2))
                lbl_diff_path.pack(side=tk.LEFT, padx=(6, 2))
                self._plot_diff_path_combo.pack(side=tk.LEFT, padx=(0, 4))
                self._diff_bar_label_xy = None
                self._diff_bar_x_placed = None
            else:
                self._plot_diff_dir_combo.pack_forget()
                lbl_diff_path.pack_forget()
                self._plot_diff_path_combo.pack_forget()
            self._render_plot()
            
        chk_db.configure(command=_on_diff_bars_toggle)
        if self._show_diff_bars.get():
            self._plot_diff_dir_combo.pack(side=tk.LEFT, padx=(2, 2))
            lbl_diff_path.pack(side=tk.LEFT, padx=(6, 2))
            self._plot_diff_path_combo.pack(side=tk.LEFT, padx=(0, 4))

        def _on_diff_path(_e=None):
            if getattr(self, "_suppress_diff_path_event", False):
                return
            i = int(self._plot_diff_path_combo.current() or 0)
            opts = getattr(self, "_pathway_options", []) or []
            if 0 <= i < len(opts):
                self._diff_bar_pathway_idx.set(i)
                self._diff_bar_label_xy = None
                self._diff_bar_x_placed = None
            self._render_plot()

        self._plot_diff_path_combo.bind("<<ComboboxSelected>>", _on_diff_path)
        
        # chk_pl = ttk.Checkbutton(ctrl, text="Plateaus", variable=self._show_merged_plateaus, command=self._render_plot)
        # chk_pl.pack(side=tk.LEFT, padx=(6, 0))
        
        ab_frame = ttk.Frame(ctrl)
        ab_frame.pack(side=tk.LEFT)
        chk_ab = ttk.Checkbutton(ab_frame, text="AxisBreak", variable=self._show_axis_break)
        chk_ab.pack(side=tk.LEFT, padx=(6, 0))
        
        lbl_break = ttk.Label(ab_frame, text="Break")
        ent_blo = ttk.Entry(ab_frame, textvariable=self._axis_break_low, width=6)
        lbl_to = ttk.Label(ab_frame, text="to")
        ent_bhi = ttk.Entry(ab_frame, textvariable=self._axis_break_high, width=6)
        
        def _on_ab_toggle():
            if self._show_axis_break.get():
                lbl_break.pack(side=tk.LEFT, padx=(8, 2))
                ent_blo.pack(side=tk.LEFT)
                lbl_to.pack(side=tk.LEFT, padx=(2, 2))
                ent_bhi.pack(side=tk.LEFT)
            else:
                lbl_break.pack_forget()
                ent_blo.pack_forget()
                lbl_to.pack_forget()
                ent_bhi.pack_forget()
            self._render_plot()
            
        chk_ab.configure(command=_on_ab_toggle)
        if self._show_axis_break.get():
            lbl_break.pack(side=tk.LEFT, padx=(8, 2))
            ent_blo.pack(side=tk.LEFT)
            lbl_to.pack(side=tk.LEFT, padx=(2, 2))
            ent_bhi.pack(side=tk.LEFT)
            
        chk_ca = ttk.Checkbutton(ctrl, text="Collision", variable=self._show_collision_avoid, command=self._render_plot)
        chk_ca.pack(side=tk.LEFT, padx=(6, 0))
        # Tol for Plateaus hidden
        # ttk.Label(ctrl, text="Tol").pack(side=tk.LEFT, padx=(8, 2))
        ent_tol = ttk.Entry(ctrl, textvariable=self._plateau_tol, width=5)
        # ent_tol.pack(side=tk.LEFT)
        
        # Img for hidden Images feature hidden
        # ttk.Label(ctrl, text="Img").pack(side=tk.LEFT, padx=(8, 2))
        ent_img = ttk.Entry(ctrl, textvariable=self._image_scale, width=5)
        # ent_img.pack(side=tk.LEFT)
        # Cleaned up extra Img entries
        cb_colors.bind("<<ComboboxSelected>>", lambda _e: (self._refresh_color_controls(), self._render_plot()))
        self._plot_gmap_combo_large.bind("<<ComboboxSelected>>", lambda _e: self._render_plot())
        cb_mos.bind("<<ComboboxSelected>>", lambda _e: self._render_plot())
        cb_grid.bind("<<ComboboxSelected>>", lambda _e: self._render_plot())
        self._refresh_color_controls()
        ent_tol.bind("<Return>", lambda _e: self._render_plot())
        ent_blo.bind("<Return>", lambda _e: self._render_plot())
        ent_bhi.bind("<Return>", lambda _e: self._render_plot())
        ent_img.bind("<Return>", lambda _e: self._render_plot())

        exp = ttk.Frame(bottom_frame, padding=(8, 0, 8, 4))
        exp.pack(fill=tk.X)
        ttk.Label(exp, text="Plot").pack(side=tk.LEFT, padx=(0, 4))
        cb_basis = ttk.Combobox(exp, textvariable=self._plot_energy_basis, values=["E", "G", "H"], state="readonly", width=4)
        cb_basis.pack(side=tk.LEFT)
        cb_basis.bind("<<ComboboxSelected>>", lambda _e: self._render_plot())
        ttk.Label(exp, text="Unit").pack(side=tk.LEFT, padx=(10, 4))
        cb_unit = ttk.Combobox(exp, textvariable=self._energy_unit, values=["kcal/mol", "eV", "kJ/mol", "Hartree"], state="readonly", width=8)
        cb_unit.pack(side=tk.LEFT)
        cb_unit.bind("<<ComboboxSelected>>", lambda _e: (self._recompute(), self._render_plot()))
        ttk.Label(exp, text="Temp").pack(side=tk.LEFT, padx=(12, 4))
        ent_aks_temp = ttk.Entry(exp, textvariable=self._aks_temp_label, width=13)
        ent_aks_temp.pack(side=tk.LEFT)
        
        self._barrier_idea_var = tk.StringVar(value="Barrier...")
        def _update_barrier_idea(*args):
            try:
                temp_str = self._aks_temp_label.get().strip()
                import re, math
                T = 298.15
                if temp_str:
                    nums = re.findall(r"[-+]?\d*\.\d+|\d+", temp_str)
                    if nums:
                        T_val = float(nums[0])
                        T = T_val if "C" not in temp_str.upper() and "c" not in temp_str.lower() else T_val + 273.15
                if T <= 0: return
                k_B = 1.380649e-23
                h = 6.62607015e-34
                R = 8.314462618
                t_half = getattr(self, "_kinetics_target_half_life_s", 43200.0)
                k = math.log(2) / t_half
                dG_J = -R * T * math.log((k * h) / (k_B * T))
                u = self._energy_unit.get()
                if u == "kcal/mol": v = dG_J / 4184.0
                elif u == "kJ/mol": v = dG_J / 1000.0
                elif u == "eV": v = dG_J / 96485.332
                elif u == "Hartree": v = dG_J / 2625499.6
                else: v = dG_J / 4184.0; u = "kcal/mol"
                self._barrier_idea_var.set(f"Barrier... (~{v:.1f} {u})")
            except Exception:
                self._barrier_idea_var.set("Barrier...")

        self._aks_temp_label.trace_add("write", _update_barrier_idea)
        self._energy_unit.trace_add("write", _update_barrier_idea)
        _update_barrier_idea()
        ttk.Button(exp, textvariable=self._barrier_idea_var, command=self._show_kinetics_calculator).pack(side=tk.LEFT, padx=(6, 0))
        
        ttk.Button(exp, text="Step Names", command=self._open_step_names_dialog).pack(side=tk.LEFT, padx=(8, 4))
        ent_aks_temp.bind("<Return>", lambda _e: self._render_plot())
        defaults_btn = ttk.Menubutton(exp, text="Defaults")
        defaults_menu = tk.Menu(defaults_btn, tearoff=0)
        defaults_menu.add_command(label="Use Default", command=self._use_plot_defaults)
        defaults_menu.add_command(label="Set Default", command=self._save_plot_defaults)
        defaults_btn.configure(menu=defaults_menu)
        defaults_btn.pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(exp, text="Copy to Clipboard (PPT)", command=self._copy_plot_to_clipboard).pack(side=tk.RIGHT, padx=(0, 0))
        ttk.Button(exp, text="Export…", width=10, command=lambda: self._export_current_plot(None)).pack(side=tk.RIGHT, padx=(0, 6))

        host = ttk.Frame(self._plot_window, padding=8)
        host.pack(fill=tk.BOTH, expand=True)
        canvas = FigureCanvasTkAgg(fig, master=host)
        self._plot_canvas = canvas
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._refresh_plot_diff_path_combo()

    def _open_step_names_dialog(self):
        import json
        dlg = tk.Toplevel(self.main_frame)
        dlg.title("Step Names")
        dlg.geometry("900x400")
        dlg.attributes("-topmost", True)
        
        plotted = [sp for sp in self.species if self._species_on_flow_canvas(sp) and not self._species_flow_faded(sp)]
        sp_options = [f"{sp.get('id')}: {sp.get('name', 'Unknown')}" for sp in plotted]
        
        main_frame = ttk.Frame(dlg, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        existing_val = self._aks_step_labels.get()
        steps = []
        try:
            steps = json.loads(existing_val)
            if not isinstance(steps, list): steps = []
        except:
            parts = [s.strip() for s in str(existing_val).split(";") if s.strip()]
            for p in parts:
                steps.append({"start": "", "end": "", "label": p})

        canvas_frame = ttk.Frame(main_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        canvas = tk.Canvas(canvas_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        rows = []
        
        def _add_row(start="", end="", label=""):
            row_frame = ttk.Frame(scrollable_frame)
            row_frame.pack(fill=tk.X, pady=2)
            
            ttk.Label(row_frame, text="Start:").pack(side=tk.LEFT)
            cb_start = ttk.Combobox(row_frame, values=sp_options, width=28, state="readonly")
            cb_start.pack(side=tk.LEFT, padx=(2, 5))
            if start:
                for opt in sp_options:
                    if opt.startswith(f"{start}:"): cb_start.set(opt)
            
            ttk.Label(row_frame, text="End:").pack(side=tk.LEFT)
            cb_end = ttk.Combobox(row_frame, values=sp_options, width=28, state="readonly")
            cb_end.pack(side=tk.LEFT, padx=(2, 5))
            if end:
                for opt in sp_options:
                    if opt.startswith(f"{end}:"): cb_end.set(opt)
            
            ttk.Label(row_frame, text="Label:").pack(side=tk.LEFT)
            ent_lbl = ttk.Entry(row_frame, width=18)
            ent_lbl.pack(side=tk.LEFT, padx=(2, 5))
            ent_lbl.insert(0, label)
            
            btn_pick_start = ttk.Button(row_frame, text="Pick Start", width=10)
            btn_pick_start.pack(side=tk.LEFT, padx=(2, 2))
            
            btn_pick_end = ttk.Button(row_frame, text="Pick End", width=10)
            btn_pick_end.pack(side=tk.LEFT, padx=(2, 5))
            
            def _pick_node(is_start=True):
                if not getattr(self, "_plot_canvas", None): return
                btn = btn_pick_start if is_start else btn_pick_end
                btn.config(text="Click...")
                try: self._plot_canvas.get_tk_widget().config(cursor="crosshair")
                except: pass
                dlg.update_idletasks()
                
                def on_click(e):
                    xdata = getattr(e, "xdata", None)
                    if xdata is None and hasattr(e, "mouseevent"):
                        xdata = getattr(e.mouseevent, "xdata", None)
                    if xdata is None: return
                    
                    px_dict = getattr(self, "_plot_px_cache", {})
                    closest_sp = None
                    min_dist = float('inf')
                    for sp in plotted:
                        sp_id = sp.get("id")
                        px_val = None
                        if sp_id in px_dict: px_val = px_dict[sp_id]
                        elif str(sp_id) in px_dict: px_val = px_dict[str(sp_id)]
                        elif str(sp_id).isdigit() and int(sp_id) in px_dict: px_val = px_dict[int(sp_id)]
                            
                        if px_val is not None:
                            dist = abs(px_val - xdata)
                            if dist < min_dist:
                                min_dist = dist
                                closest_sp = sp
                    if closest_sp:
                        val = f"{closest_sp.get('id')}: {closest_sp.get('name', 'Unknown')}"
                        if is_start: cb_start.set(val)
                        else: cb_end.set(val)
                    _finish_pick()
                    
                def _finish_pick():
                    try: self._plot_canvas.mpl_disconnect(cid1)
                    except: pass
                    btn_pick_start.config(text="Pick Start")
                    btn_pick_end.config(text="Pick End")
                    try: self._plot_canvas.get_tk_widget().config(cursor="")
                    except: pass
                    dlg.update_idletasks()
                    
                cid1 = self._plot_canvas.mpl_connect("button_release_event", on_click)
                
            btn_pick_start.config(command=lambda: _pick_node(is_start=True))
            btn_pick_end.config(command=lambda: _pick_node(is_start=False))
            
            def _remove():
                row_frame.destroy()
                rows.remove((cb_start, cb_end, ent_lbl))
            ttk.Button(row_frame, text="X", width=2, command=_remove).pack(side=tk.LEFT)
            
            rows.append((cb_start, cb_end, ent_lbl))
            
        for s in steps:
            _add_row(s.get("start", ""), s.get("end", ""), s.get("label", ""))
            
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="+ Add Step", command=_add_row).pack(side=tk.LEFT)
        
        def _save():
            final_steps = []
            for cb_start, cb_end, ent_lbl in rows:
                start_id = cb_start.get().split(":")[0] if ":" in cb_start.get() else ""
                end_id = cb_end.get().split(":")[0] if ":" in cb_end.get() else ""
                final_steps.append({
                    "start": int(start_id) if start_id.isdigit() else "",
                    "end": int(end_id) if end_id.isdigit() else "",
                    "label": ent_lbl.get()
                })
            self._aks_step_labels.set(json.dumps(final_steps))
            self._render_plot()
            dlg.destroy()
            
        ttk.Button(btn_frame, text="Save & Close", command=_save).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side=tk.RIGHT, padx=5)

    def _refresh_plot_diff_path_combo(self):
        cb = getattr(self, "_plot_diff_path_combo", None)
        if cb is None:
            return
        try:
            if not cb.winfo_exists():
                return
        except Exception:
            return
        opts = getattr(self, "_pathway_options", []) or []
        cb["values"] = [o["label"] for o in opts]
        if not opts:
            cb.set("")
            cb.configure(state="disabled")
            return
        if not bool(self._show_diff_bars.get()):
            cb.configure(state="disabled")
            return
        cb.configure(state="readonly")
        idx = int(self._diff_bar_pathway_idx.get())
        idx = max(0, min(idx, len(opts) - 1))
        self._diff_bar_pathway_idx.set(idx)
        self._suppress_diff_path_event = True
        try:
            cb.current(idx)
        finally:
            self._suppress_diff_path_event = False

    def _refresh_color_controls(self):
        mode = (self._plot_colors_mode.get() or "Gradient").strip()
        enable_map = mode == "Gradient"
        for attr in ("_plot_gmap_combo_top", "_plot_gmap_combo_large"):
            cb = getattr(self, attr, None)
            if cb is None:
                continue
            try:
                if not cb.winfo_exists():
                    continue
                cb.configure(state="readonly" if enable_map else "disabled")
                if enable_map:
                    if not cb.winfo_manager():
                        cb.pack(side=tk.LEFT, padx=(4, 0))
                else:
                    if cb.winfo_manager():
                        cb.pack_forget()
            except Exception:
                pass

    def _auto_color_mode_from_style(self):
        style_choice = (self._plot_style.get() or "").strip().lower()
        if "classic" in style_choice or "aks style" in style_choice:
            self._plot_colors_mode.set("Normal")
        else:
            self._plot_colors_mode.set("Gradient")
        self._refresh_color_controls()

    @staticmethod
    def _save_fig_without_main_title(fig, path, **kwargs):
        axes = list(fig.axes)
        titles = [ax.get_title() for ax in axes]
        try:
            for ax in axes:
                ax.set_title("")
            fig.savefig(path, **kwargs)
        finally:
            for ax, t in zip(axes, titles):
                ax.set_title(t)

    def _export_current_plot(self, fmt: str | None):
        fig = getattr(self, "_current_plot_fig", None)
        win = self._plot_window if self._plot_window and self._plot_window.winfo_exists() else None
        if fig is None:
            messagebox.showinfo("PES Plot", "Render the large plot first.")
            return
        parent = win if win is not None else self.main_frame.winfo_toplevel()
        if not fmt:
            choice = simpledialog.askstring(
                "Export Format",
                "Choose format: png, svg, pdf, tif, webp",
                parent=parent,
            )
            if not choice:
                return
            fmt = choice
        ft = str(fmt).lower().strip().lstrip(".")
        types = {
            "png": [("PNG", "*.png"), ("All", "*.*")],
            "svg": [("SVG", "*.svg"), ("All", "*.*")],
            "pdf": [("PDF", "*.pdf"), ("All", "*.*")],
            "tif": [("TIFF", "*.tif;*.tiff"), ("All", "*.*")],
            "tiff": [("TIFF", "*.tif;*.tiff"), ("All", "*.*")],
            "webp": [("WebP", "*.webp"), ("All", "*.*")],
        }
        if ft == "tiff":
            ft = "tif"
        if ft not in types:
            messagebox.showinfo("PES Plot", f"Unsupported format: {fmt}")
            return
        path = filedialog.asksaveasfilename(
            parent=parent,
            title=f"Save plot ({ft.upper()})",
            defaultextension=f".{ft}",
            filetypes=types[ft],
        )
        if not path:
            return
        try:
            kw = dict(bbox_inches="tight", pad_inches=0.02, facecolor="white", edgecolor="none")
            if ft in ("png", "tif", "webp"):
                kw["dpi"] = 360
                kw["format"] = ft
            elif ft == "svg":
                kw["format"] = "svg"
            elif ft == "pdf":
                kw["format"] = "pdf"
            self._save_fig_without_main_title(fig, path, **kw)
            messagebox.showinfo("PES Plot", f"Saved:\n{path}")
        except Exception as e:
            messagebox.showerror("PES Plot", f"Export failed:\n{e}")
        try:
            if self._plot_window and self._plot_window.winfo_exists():
                self._plot_window.deiconify()
                self._plot_window.lift()
                self._plot_window.focus_force()
        except Exception:
            pass

    def _copy_plot_to_clipboard(self):
        fig = getattr(self, "_current_plot_fig", None)
        if fig is None:
            messagebox.showinfo("PES Plot", "Render the large plot first.")
            return
        import tempfile
        fd, tmp_path = tempfile.mkstemp(prefix="pes_clip_", suffix=".png")
        os.close(fd)
        try:
            self._save_fig_without_main_title(
                fig,
                tmp_path,
                format="png",
                dpi=240,
                bbox_inches="tight",
                pad_inches=0.02,
                facecolor="white",
            )
            safe = tmp_path.replace("'", "''")
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "Add-Type -AssemblyName System.Drawing;"
                f"[System.Windows.Forms.Clipboard]::SetImage("
                f"[System.Drawing.Image]::FromFile('{safe}'));"
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Sta", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0:
                raise RuntimeError(r.stderr or r.stdout or "clipboard failed")
            messagebox.showinfo("PES Plot", "Plot copied to clipboard — paste in PowerPoint with Ctrl+V.", parent=self._plot_window if self._plot_window and self._plot_window.winfo_exists() else None)
        except Exception as e:
            messagebox.showerror("PES Plot", f"Could not copy to clipboard.\n{e}\nYou can use Export → PNG and insert the file manually.", parent=self._plot_window if self._plot_window and self._plot_window.winfo_exists() else None)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        try:
            if self._plot_window and self._plot_window.winfo_exists():
                self._plot_window.deiconify()
                self._plot_window.lift()
                self._plot_window.focus_force()
        except Exception:
            pass

    def _open_plot_3d_viewer(self, xyz_path: str, title: str = "3D Viewer", on_apply=None, init_state=None):
        try:
            with open(xyz_path, "r", encoding="utf-8", errors="ignore") as f:
                rows = [ln.rstrip("\n") for ln in f]
            n = int(rows[0].strip())
            atom_rows = rows[2 : 2 + n]
            atoms = []
            for ln in atom_rows:
                p = ln.split()
                if len(p) < 4:
                    continue
                atoms.append((p[0], float(p[1]), float(p[2]), float(p[3])))
            if not atoms:
                messagebox.showinfo("3D viewer", "No atoms parsed from XYZ.")
                return
        except Exception as e:
            messagebox.showerror("3D viewer", f"Failed to open XYZ:\n{e}")
            return

        top = tk.Toplevel(self.main_frame.winfo_toplevel())
        top.title(title)
        top.geometry("1080x820")
        top.minsize(920, 700)
        body = ttk.Frame(top, padding=8)
        body.pack(fill=tk.BOTH, expand=True)
        bar = ttk.Frame(body)
        bar.pack(fill=tk.X, pady=(0, 6))
        canvas = tk.Canvas(body, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
        canvas.pack(fill=tk.BOTH, expand=True)

        init_state = init_state or {}
        selected = set(int(v) for v in (init_state.get("selected", []) or []))
        ts_bonds = set(tuple(sorted((int(a), int(b)))) for a, b in (init_state.get("ts_bonds", []) or []))
        drag = {"on": False, "x": 0, "y": 0}
        rot = {"yaw": float(init_state.get("yaw", 0.42)), "pitch": float(init_state.get("pitch", -0.30))}
        zoom = {"z": float(init_state.get("zoom", 1.0))}

        elems = [str(sym).upper() for sym, *_ in atoms]
        metals = {"LI","NA","K","MG","CA","V","CR","MN","FE","CO","NI","CU","ZN","RU","RH","PD","AG","CD","PT","AU","HG","PB","BI"}
        # Prefer ASE's jmol palette for consistency with exported snapshot
        color = {
            "H": "#ffffff", "C": "#808080", "N": "#3050f8", "O": "#ff0d0d", "S": "#ffff30", "P": "#ff8000",
            "F": "#90e050", "CL": "#1ff01f", "BR": "#a62929", "I": "#940094", "FE": "#e06633", "MN": "#9c7ac7",
            "CO": "#f090a0", "NI": "#50d050", "CU": "#c88033", "ZN": "#7d80b0", "RU": "#248f8f", "RH": "#0a7d8c",
            "PD": "#006985", "AG": "#c0c0c0", "CD": "#ffd98f", "PT": "#d0d0e0", "AU": "#ffd123", "HG": "#b8b8d0",
        }
        try:
            from ase.data.colors import jmol_colors as _jc
            from ase.data import chemical_symbols as _csym
            _sym2z = {s.upper(): i for i, s in enumerate(_csym)}
            for _s in set(elems):
                _z = _sym2z.get(_s)
                if _z is not None and 0 <= _z < len(_jc):
                    _r, _g, _b = _jc[_z]
                    color[_s] = f"#{int(_r*255):02x}{int(_g*255):02x}{int(_b*255):02x}"
        except Exception:
            pass
        cov_r = {
            "H": 0.31, "B": 0.84, "C": 0.76, "N": 0.71, "O": 0.66, "F": 0.57,
            "NA": 1.66, "MG": 1.41, "AL": 1.21, "SI": 1.11, "P": 1.07, "S": 1.05, "CL": 1.02,
            "K": 2.03, "CA": 1.76, "BR": 1.20, "I": 1.39,
            "V": 1.53, "CR": 1.39, "MN": 1.39, "FE": 1.32, "CO": 1.26, "NI": 1.24, "CU": 1.32, "ZN": 1.22,
            "RU": 1.46, "RH": 1.42, "PD": 1.39, "AG": 1.45, "CD": 1.44,
            "PT": 1.36, "AU": 1.36, "HG": 1.32, "PB": 1.46, "BI": 1.48, "LI": 1.28,
        }
        rad = {
            "H": 6.0, "C": 11.0, "N": 10.8, "O": 10.6, "F": 10.4, "P": 12.0, "S": 11.8, "CL": 11.6, "BR": 12.2, "I": 12.6,
            "MN": 14.0, "FE": 14.8, "CO": 14.6, "NI": 14.6, "CU": 14.8, "ZN": 14.6, "RU": 15.0, "RH": 15.0, "PD": 15.2,
            "AG": 15.2, "CD": 15.2, "PT": 15.4, "AU": 15.8, "HG": 15.6,
        }
        coords = [(x, y, z) for _, x, y, z in atoms]
        cx = sum(x for x, _, _ in coords) / len(coords)
        cy = sum(y for _, y, _ in coords) / len(coords)
        cz = sum(z for _, _, z in coords) / len(coords)
        norm = [(x - cx, y - cy, z - cz) for x, y, z in coords]
        max_r = max((x * x + y * y + z * z) ** 0.5 for x, y, z in norm) or 1.0
        norm = [(x / max_r, y / max_r, z / max_r) for x, y, z in norm]

        bonds = set()
        for i,(si,xi,yi,zi) in enumerate(atoms):
            ei = str(si).upper()
            ri = cov_r.get(ei,0.77)
            for j in range(i+1,len(atoms)):
                sj,xj,yj,zj = atoms[j]
                ej = str(sj).upper()
                rj = cov_r.get(ej,0.77)
                dx,dy,dz = xi-xj, yi-yj, zi-zj
                d = (dx*dx+dy*dy+dz*dz)**0.5
                # Tight tolerance + absolute cap to avoid spurious long "bonds"
                if ei in metals or ej in metals:
                    f = 1.20
                    cap = 2.80
                else:
                    f = 1.15
                    cap = 1.90
                # H is strictly covalent; keep it extra tight
                if ei == "H" or ej == "H":
                    f = 1.12
                    cap = 1.25
                if 0.45 < d <= min(cap, f * (ri + rj)):
                    bonds.add(tuple(sorted((i, j))))
        if init_state.get("bonds"):
            try:
                bonds = set(tuple(sorted((int(a), int(b)))) for a, b in (init_state.get("bonds") or []))
            except Exception:
                pass
        fogged = set(int(v) for v in (init_state.get("fogged", []) or []))
        if not fogged and bool(init_state.get("fog_on", False)):
            # Backward compatibility with old boolean fog mode:
            # fog_on meant non-selected appeared fogged.
            fogged = set(i for i in range(len(atoms)) if i not in selected)

        def _rot(v):
            import math
            x,y,z = v
            cyw, syw = math.cos(rot["yaw"]), math.sin(rot["yaw"])
            x,z = x*cyw + z*syw, -x*syw + z*cyw
            cpi, spi = math.cos(rot["pitch"]), math.sin(rot["pitch"])
            y,z = y*cpi - z*spi, y*spi + z*cpi
            return x,y,z

        def _lighten(hex_color, factor=0.72):
            h = hex_color.lstrip("#")
            r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
            r = int(r + (255 - r) * factor)
            g = int(g + (255 - g) * factor)
            b = int(b + (255 - b) * factor)
            return f"#{r:02x}{g:02x}{b:02x}"

        def _draw():
            w,h = max(1,canvas.winfo_width()), max(1,canvas.winfo_height())
            canvas.delete("all")
            proj = []
            s = min(w, h) * 0.34 * zoom["z"]
            for i,(sym,_,_,_) in enumerate(atoms):
                x,y,z = _rot(norm[i])
                px = w*0.5 + x*s
                py = h*0.5 - y*s
                depth = z
                proj.append((i,sym,px,py,depth))
            proj.sort(key=lambda t:t[4])

            # bonds (fog = blur: render lighter/thinner for non-selected endpoints)
            for i,j in sorted(bonds):
                pi = next(p for p in proj if p[0]==i); pj = next(p for p in proj if p[0]==j)
                is_ts = tuple(sorted((i,j))) in ts_bonds
                faded = (i in fogged) or (j in fogged)
                if is_ts:
                    bcol = "#fca5a5" if faded else "#ef4444"
                    bw = 2 if faded else 3
                    canvas.create_line(pi[2],pi[3],pj[2],pj[3],fill=bcol,width=bw,dash=(6,4))
                else:
                    bcol = "#d1d5db" if faded else "#6b7280"
                    bw = 2 if faded else 4
                    canvas.create_line(pi[2],pi[3],pj[2],pj[3],fill=bcol,width=bw)

            # atoms (no white specular, crisp outlines; fog = blur to near-white)
            for i,sym,px,py,_ in proj:
                sym_u = str(sym).upper()
                zr = max(0.70, min(1.85, zoom["z"] ** 0.65))
                r = max(6, int(rad.get(sym_u, 11.0) * zr))
                base = color.get(sym_u, "#d1d5db")
                faded = i in fogged
                if faded:
                    fill = _lighten(base, 0.78)
                    outline = "#cbd5e1"
                    ow = 1
                else:
                    fill = base
                    outline = "#111827" if i in selected else "#334155"
                    ow = 2 if i in selected else 1
                canvas.create_oval(px-r,py-r,px+r,py+r,fill=fill,outline=outline,width=ow, tags=(f"a_{i}",))
                if i in selected:
                    canvas.create_text(px, py-r-10, text=f"{sym}{i}", fill="#111827", font=("Segoe UI", 9, "bold"))

            canvas.create_text(
                10, 10, anchor="nw", fill="#475569",
                font=("Segoe UI", 9),
                text="Left click: select atoms | Empty click: clear selection | Right drag: rotate | Wheel: zoom"
            )

        def _pick(event):
            items = canvas.find_overlapping(event.x-2,event.y-2,event.x+2,event.y+2)
            for it in items:
                for t in canvas.gettags(it):
                    if t.startswith("a_"):
                        idx = int(t.split("_",1)[1])
                        if idx in selected: selected.remove(idx)
                        else: selected.add(idx)
                        _draw(); return
            selected.clear()
            _draw()

        def _press(e): drag.update({"on": True, "x": e.x, "y": e.y})
        def _drag(e):
            if not drag["on"]: return
            dx,dy = e.x-drag["x"], e.y-drag["y"]
            rot["yaw"] += dx*0.01; rot["pitch"] += dy*0.01
            drag.update({"x": e.x, "y": e.y})
            _draw()
        def _release(_e): drag["on"] = False
        def _wheel(e):
            zoom["z"] = max(0.35, min(3.0, zoom["z"]*(1.1 if e.delta>0 else 0.9)))
            _draw()

        def _select_all():
            selected.clear(); selected.update(range(len(atoms))); _draw()
        def _fog_selected():
            if not selected:
                messagebox.showinfo("3D viewer", "Select atoms first.")
                return
            fogged.update(selected); _draw()
        def _fog_others():
            fogged.clear()
            fogged.update(i for i in range(len(atoms)) if i not in selected)
            _draw()
        def _defog_selected():
            fogged.difference_update(selected); _draw()
        def _defog_others():
            fogged.intersection_update(selected); _draw()
        def _defog_all():
            fogged.clear(); _draw()
        def _mark_ts_bond():
            if len(selected) != 2:
                messagebox.showinfo("3D viewer", "Select exactly 2 atoms to mark TS bond.")
                return
            i,j = sorted(list(selected))
            ts_bonds.add((i,j)); _draw()
        def _make_bond():
            if len(selected) != 2:
                messagebox.showinfo("3D viewer", "Select exactly 2 atoms to make a bond.")
                return
            i, j = sorted(list(selected))
            bonds.add((i, j))
            _draw()
        def _break_bond():
            if len(selected) != 2:
                messagebox.showinfo("3D viewer", "Select exactly 2 atoms to break a bond.")
                return
            i, j = sorted(list(selected))
            bonds.discard((i, j))
            ts_bonds.discard((i, j))
            _draw()
        def _clear_ts():
            ts_bonds.clear(); _draw()

        ttk.Button(bar, text="Select all", command=_select_all).pack(side=tk.LEFT)
        ttk.Button(bar, text="Fog selected", command=_fog_selected).pack(side=tk.LEFT, padx=(6,0))
        ttk.Button(bar, text="Fog others", command=_fog_others).pack(side=tk.LEFT, padx=(6,0))
        ttk.Button(bar, text="Defog selected", command=_defog_selected).pack(side=tk.LEFT, padx=(6,0))
        ttk.Button(bar, text="Defog others", command=_defog_others).pack(side=tk.LEFT, padx=(6,0))
        ttk.Button(bar, text="Defog all", command=_defog_all).pack(side=tk.LEFT, padx=(6,0))
        ttk.Button(bar, text="Make bond (2 atoms)", command=_make_bond).pack(side=tk.LEFT, padx=(10,0))
        ttk.Button(bar, text="Break bond (2 atoms)", command=_break_bond).pack(side=tk.LEFT, padx=(6,0))
        ttk.Button(bar, text="Make TS style (2 atoms)", command=_mark_ts_bond).pack(side=tk.LEFT, padx=(6,0))
        ttk.Button(bar, text="Clear TS marks", command=_clear_ts).pack(side=tk.LEFT, padx=(6,0))
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        def _cancel():
            top.destroy()

        def _render_ase_snapshot(out_png: str, size: int = 1400) -> bool:
            """Render a publication-quality molecule image using ASE jmol colors
            + covalent radii, matplotlib patches, fog-as-blur via alpha, TS dashed bonds."""
            try:
                import matplotlib
                matplotlib.use("Agg", force=False)
                import matplotlib.pyplot as plt
                from matplotlib.patches import Circle
                from ase.data.colors import jmol_colors
                from ase.data import chemical_symbols, covalent_radii as ase_cov_r
            except Exception:
                return False
            try:
                sym_to_Z = {s.upper(): i for i, s in enumerate(chemical_symbols)}
                # Rotated 3D positions in Å (same matrix as live viewer)
                pos3 = []
                for i in range(len(atoms)):
                    x, y, z = _rot(norm[i])
                    pos3.append((x * max_r, y * max_r, z * max_r))
                xs = [p[0] for p in pos3]
                ys = [p[1] for p in pos3]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                pad = 1.4
                # Figure sized to output
                dpi = 200
                fig_in = size / dpi
                fig, ax = plt.subplots(figsize=(fig_in, fig_in), dpi=dpi)
                fig.patch.set_facecolor("white")
                ax.set_facecolor("white")
                ax.set_xlim(min_x - pad, max_x + pad)
                ax.set_ylim(min_y - pad, max_y + pad)
                ax.set_aspect("equal")
                ax.set_axis_off()

                # Atom radii (ASE covalent radii * nice scale factor)
                atom_Z = []
                atom_r = []
                for sym, _, _, _ in atoms:
                    Z = sym_to_Z.get(str(sym).upper(), 6)
                    atom_Z.append(Z)
                    # Visual radius: covalent * 0.78 gives a compact, publication-style look
                    atom_r.append(float(ase_cov_r[Z]) * 0.78)

                # Bonds behind atoms (draw outline stroke + colored stroke for "cylinder" look)
                for i, j in sorted(bonds):
                    pi, pj = pos3[i], pos3[j]
                    is_ts = tuple(sorted((i, j))) in ts_bonds
                    faded = (i in fogged) or (j in fogged)
                    if is_ts:
                        if faded:
                            ax.plot([pi[0], pj[0]], [pi[1], pj[1]], color="#ef4444",
                                    lw=3.2, linestyle=(0, (5, 3)), alpha=0.30, solid_capstyle="round", zorder=1.2)
                        else:
                            ax.plot([pi[0], pj[0]], [pi[1], pj[1]], color="#ef4444",
                                    lw=3.6, linestyle=(0, (5, 3)), solid_capstyle="round", zorder=1.2)
                    else:
                        if faded:
                            ax.plot([pi[0], pj[0]], [pi[1], pj[1]], color="#9ca3af",
                                    lw=2.4, solid_capstyle="round", alpha=0.35, zorder=1.0)
                        else:
                            # Outline stroke for crisp cylinder-like appearance
                            ax.plot([pi[0], pj[0]], [pi[1], pj[1]], color="#1f2937",
                                    lw=5.4, solid_capstyle="round", zorder=1.05)
                            ax.plot([pi[0], pj[0]], [pi[1], pj[1]], color="#d1d5db",
                                    lw=3.6, solid_capstyle="round", zorder=1.12)

                # Atoms in depth order (far first)
                order = sorted(range(len(pos3)), key=lambda k: pos3[k][2])
                for idx in order:
                    Z = atom_Z[idx]
                    rgb = tuple(float(v) for v in jmol_colors[Z])
                    r = atom_r[idx]
                    faded = idx in fogged
                    if faded:
                        face = tuple(v + (1.0 - v) * 0.72 for v in rgb)
                        edge = (0.72, 0.76, 0.82)
                        alpha = 0.40
                        ew = 0.8
                        zz = 1.8
                    else:
                        face = rgb
                        edge = (0.07, 0.09, 0.15) if idx in selected else (0.17, 0.22, 0.33)
                        alpha = 1.0
                        ew = 1.5 if idx in selected else 1.1
                        zz = 2.0 + (pos3[idx][2] - min(p[2] for p in pos3)) * 0.01
                    ax.add_patch(
                        Circle(
                            (pos3[idx][0], pos3[idx][1]), radius=r,
                            facecolor=face, edgecolor=edge,
                            linewidth=ew, alpha=alpha, zorder=zz,
                        )
                    )
                    if idx in selected:
                        sym = atoms[idx][0]
                        ax.text(
                            pos3[idx][0], pos3[idx][1] + r + 0.18,
                            f"{sym}{idx}", ha="center", va="bottom",
                            fontsize=9.5, fontweight="bold", color="#111827", zorder=3.0,
                        )

                fig.savefig(out_png, dpi=dpi, bbox_inches="tight", pad_inches=0.10, facecolor="white")
                plt.close(fig)
                return os.path.isfile(out_png)
            except Exception as e:
                try:
                    messagebox.showerror("3D viewer", f"ASE render failed:\n{e}", parent=top)
                except Exception:
                    pass
                return False

        def _render_pil_snapshot(out_png: str, size: int = 1200) -> bool:
            try:
                from PIL import Image, ImageDraw, ImageFilter
            except Exception as e:
                messagebox.showerror("3D viewer", f"Pillow not available.\n{e}", parent=top)
                return False
            try:
                def _hex(h_):
                    h_ = h_.lstrip("#")
                    return (int(h_[0:2], 16), int(h_[2:4], 16), int(h_[4:6], 16))
                def _blend_to_white(rgb, factor=0.78):
                    r, g, b = rgb
                    return (int(r + (255 - r) * factor),
                            int(g + (255 - g) * factor),
                            int(b + (255 - b) * factor))

                # Render at 2x for supersampling, then downscale = sharper edges
                SS = 2
                w = h = int(size) * SS
                im = Image.new("RGBA", (w, h), (255, 255, 255, 255))
                faded_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                dr = ImageDraw.Draw(im, "RGBA")
                dr_f = ImageDraw.Draw(faded_layer, "RGBA")

                proj = []
                s = min(w, h) * 0.34 * zoom["z"]
                for i, (sym, _, _, _) in enumerate(atoms):
                    x, y, z = _rot(norm[i])
                    sx = w * 0.5 + x * s
                    sy = h * 0.5 - y * s
                    proj.append((i, sym, sx, sy, z))
                proj.sort(key=lambda t: t[4])

                # bonds
                for i, j in sorted(bonds):
                    pi = next(p for p in proj if p[0] == i)
                    pj = next(p for p in proj if p[0] == j)
                    is_ts = tuple(sorted((i, j))) in ts_bonds
                    faded = (i in fogged) or (j in fogged)
                    target = dr_f if faded else dr
                    if is_ts:
                        col = (252, 165, 165, 255) if faded else (239, 68, 68, 255)
                        import math
                        x0, y0, x1_, y1_ = pi[2], pi[3], pj[2], pj[3]
                        dxb, dyb = x1_ - x0, y1_ - y0
                        L = max(1.0, (dxb * dxb + dyb * dyb) ** 0.5)
                        segs = max(2, int(L / (12 * SS)))
                        for k in range(segs):
                            t0 = k / segs
                            t1 = min(1.0, (k + 0.55) / segs)
                            a = (x0 + dxb * t0, y0 + dyb * t0)
                            b = (x0 + dxb * t1, y0 + dyb * t1)
                            target.line([a, b], fill=col, width=4 * SS)
                    else:
                        col = (209, 213, 219, 255) if faded else (107, 114, 128, 255)
                        bw = 2 * SS if faded else 5 * SS
                        target.line([(pi[2], pi[3]), (pj[2], pj[3])], fill=col, width=bw)

                # atoms
                for i, sym, sx, sy, _ in proj:
                    sym_u = str(sym).upper()
                    zr = max(0.70, min(1.85, zoom["z"] ** 0.65))
                    r = max(6, int(rad.get(sym_u, 11.0) * zr * (size / 900.0) * SS))
                    base_rgb = _hex(color.get(sym_u, "#d1d5db"))
                    faded = i in fogged
                    if faded:
                        fill_rgb = _blend_to_white(base_rgb, 0.78)
                        out_rgb = _hex("#cbd5e1")
                        ow = 1 * SS
                        dr_f.ellipse(
                            [sx - r, sy - r, sx + r, sy + r],
                            fill=fill_rgb + (255,), outline=out_rgb + (255,), width=ow,
                        )
                    else:
                        out_rgb = _hex("#111827") if i in selected else _hex("#334155")
                        ow = 2 * SS if i in selected else 1 * SS
                        dr.ellipse(
                            [sx - r, sy - r, sx + r, sy + r],
                            fill=base_rgb + (255,), outline=out_rgb + (255,), width=ow,
                        )

                # Blur the faded layer for a true "fog" look and composite under atoms
                if fogged:
                    faded_layer = faded_layer.filter(ImageFilter.GaussianBlur(radius=2.5 * SS))
                composite = Image.alpha_composite(Image.new("RGBA", (w, h), (255, 255, 255, 255)), faded_layer)
                composite = Image.alpha_composite(composite, im)
                # Downsample to requested size for sharpness
                final = composite.resize((int(size), int(size)), Image.Resampling.LANCZOS)
                final.save(out_png, "PNG")
                return True
            except Exception as e:
                messagebox.showerror("3D viewer", f"Failed to render 3D snapshot.\n{e}", parent=top)
                return False

        def _ok_apply():
            out_dir = os.path.join(self.app_dir, "pes_3d_snapshots")
            os.makedirs(out_dir, exist_ok=True)
            out_png = os.path.join(out_dir, f"mol3d_{len(self._plot_assets)+1}.png")

            # Prefer exact screenshot of the live viewer canvas so the placed
            # asset matches exactly what user is currently visualizing.
            captured = False
            try:
                from PIL import ImageGrab  # type: ignore
                top.update_idletasks()
                canvas.update_idletasks()
                x0 = canvas.winfo_rootx()
                y0 = canvas.winfo_rooty()
                w = max(2, canvas.winfo_width())
                h = max(2, canvas.winfo_height())
                bbox = (int(x0), int(y0), int(x0 + w), int(y0 + h))
                img = ImageGrab.grab(bbox=bbox)
                if img is not None:
                    img.save(out_png, "PNG")
                    captured = os.path.isfile(out_png)
            except Exception:
                captured = False

            # Fallback renderers (kept for environments where screenshot fails).
            if not captured:
                if not _render_ase_snapshot(out_png, size=1400):
                    if not _render_pil_snapshot(out_png, size=1200):
                        return
            state = {
                "selected": sorted(list(selected)),
                "fog_on": bool(len(fogged) > 0),
                "fogged": sorted(list(fogged)),
                "bonds": [list(v) for v in sorted(bonds)],
                "ts_bonds": [list(v) for v in sorted(ts_bonds)],
                "yaw": float(rot["yaw"]),
                "pitch": float(rot["pitch"]),
                "zoom": float(zoom["z"]),
            }
            if callable(on_apply):
                on_apply(out_png, state)
            top.destroy()

        ttk.Button(bar, text="Cancel", command=_cancel).pack(side=tk.RIGHT)
        ttk.Button(bar, text="OK (Place on plot)", command=_ok_apply).pack(side=tk.RIGHT, padx=(0, 8))

        canvas.bind("<Button-1>", _pick)
        canvas.bind("<ButtonPress-3>", _press)
        canvas.bind("<B3-Motion>", _drag)
        canvas.bind("<ButtonRelease-3>", _release)
        canvas.bind("<MouseWheel>", _wheel)
        canvas.bind("<Configure>", lambda _e: _draw())
        _draw()

    @staticmethod
    def _build_smooth_curve(x_vals, y_vals, points_per_seg=24):
        """
        Build smooth monotonic segment-wise curve using cosine easing.
        This avoids overshoot and keeps TS points visually at local peaks.
        """
        import math
        n = len(x_vals)
        if n < 2:
            return x_vals[:], y_vals[:]
        cx, cy = [], []
        for i in range(n - 1):
            x0, x1 = x_vals[i], x_vals[i + 1]
            y0, y1 = y_vals[i], y_vals[i + 1]
            if x1 == x0:
                continue
            for k in range(points_per_seg):
                t = k / float(points_per_seg)
                s = 0.5 * (1.0 - math.cos(math.pi * t))
                cx.append(x0 + (x1 - x0) * t)
                cy.append(y0 + (y1 - y0) * s)
        cx.append(x_vals[-1])
        cy.append(y_vals[-1])
        return cx, cy

    @staticmethod
    def _build_linear_dense_curve(x_vals, y_vals, points_per_seg=28):
        """Densify piecewise-linear path for smooth gradient in straight mode."""
        n = len(x_vals)
        if n < 2:
            return x_vals[:], y_vals[:]
        cx, cy = [], []
        for i in range(n - 1):
            x0, x1 = x_vals[i], x_vals[i + 1]
            y0, y1 = y_vals[i], y_vals[i + 1]
            for k in range(points_per_seg):
                t = k / float(points_per_seg)
                cx.append(x0 + (x1 - x0) * t)
                cy.append(y0 + (y1 - y0) * t)
        cx.append(x_vals[-1])
        cy.append(y_vals[-1])
        return cx, cy

    @staticmethod
    def _ccc_axis_x_position(stored_x, px, user_placed, default_margin=0.08):
        """
        Auto mode (first plot): place axis default_margin left of the leftmost intermediate.
        User-placed mode: keep the stored absolute data-x from the last drag.
        """
        xmin = min(px.values()) if px else 0.0
        if not user_placed:
            return xmin - default_margin
        try:
            return float(stored_x)
        except Exception:
            return xmin - default_margin

    @staticmethod
    def _ccc_axis_pick(event, axis_data_x, trans_blend, xmin, pick_px=48):
        """Screen-space hit test for axis line, label zone, or midpoint handle."""
        if event.x is None or event.y is None:
            return False
        try:
            ex, ey = float(event.x), float(event.y)
            for ay in (0.12, 0.5, 0.88):
                px, py = trans_blend.transform((axis_data_x, ay))
                if math.hypot(ex - px, ey - py) <= pick_px:
                    return True
            label_x = axis_data_x - max(0.10, (float(xmin) - float(axis_data_x)) * 0.3)
            lx, ly = trans_blend.transform((label_x, 0.5))
            if math.hypot(ex - lx, ey - ly) <= pick_px * 1.4:
                return True
            if event.xdata is not None and float(event.xdata) <= float(xmin) - 0.05:
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _ccc_axis_contains(event, aks_axis_art):
        """Matplotlib artist.contains() pass for axis graphics."""
        arts = []
        bundle = aks_axis_art.get("arrow")
        if isinstance(bundle, (tuple, list)):
            arts.extend(bundle)
        elif bundle is not None:
            arts.append(bundle)
        for key in ("text", "handle"):
            a = aks_axis_art.get(key)
            if a is not None:
                arts.append(a)
        for art in arts:
            if art is None:
                continue
            try:
                hit, _ = art.contains(event)
                if hit:
                    return True
            except Exception:
                pass
        return False

    @staticmethod
    def _apply_collision_avoidance(fig, ax, artists):
        """
        Simple deterministic nudge pass to reduce overlapping text/annotations.
        """
        try:
            fig.canvas.draw()
            renderer = fig.canvas.get_renderer()
        except Exception:
            return
        y0, y1 = ax.get_ylim()
        xr0, xr1 = ax.get_xlim()
        y_span = max(1e-6, y1 - y0)
        x_span = max(1e-6, xr1 - xr0)
        step_y = y_span * 0.04
        step_x = x_span * 0.016
        max_iter = 10
        margin_y = 0.02 * y_span
        margin_x = 0.02 * x_span

        def _bbox(a):
            try:
                return a.get_window_extent(renderer=renderer)
            except Exception:
                return None

        for _ in range(max_iter):
            moved_any = False
            bbs = [_bbox(a) for a in artists]
            for i in range(len(artists)):
                bi = bbs[i]
                if bi is None:
                    continue
                for j in range(i + 1, len(artists)):
                    bj = bbs[j]
                    if bj is None or not bi.overlaps(bj):
                        continue
                    a = artists[j]
                    try:
                        cx_i, cy_i = bi.x0 + 0.5 * bi.width, bi.y0 + 0.5 * bi.height
                        cx_j, cy_j = bj.x0 + 0.5 * bj.width, bj.y0 + 0.5 * bj.height
                        sign_y = 1.0 if cy_j >= cy_i else -1.0
                        sign_x = 1.0 if cx_j >= cx_i else -1.0
                    except Exception:
                        sign_y, sign_x = 1.0, 1.0
                    # Push away mostly along stronger overlap axis.
                    x_overlap = max(0.0, min(bi.x1, bj.x1) - max(bi.x0, bj.x0))
                    y_overlap = max(0.0, min(bi.y1, bj.y1) - max(bi.y0, bj.y0))
                    primary_moves = []
                    if y_overlap >= x_overlap:
                        primary_moves = [
                            (0.0, sign_y * step_y),
                            (sign_x * step_x, sign_y * step_y),
                            (0.0, sign_y * 2.0 * step_y),
                            (-sign_x * step_x, sign_y * step_y),
                        ]
                    else:
                        primary_moves = [
                            (sign_x * step_x, 0.0),
                            (sign_x * step_x, sign_y * step_y),
                            (sign_x * 2.0 * step_x, 0.0),
                            (sign_x * step_x, -sign_y * step_y),
                        ]
                    moved = False
                    for dx, dy in primary_moves:
                        if hasattr(a, "xyann"):  # Annotation
                            x, y = a.xyann
                            nx = min(max(x + dx, xr0 + margin_x), xr1 - margin_x)
                            ny = min(max(y + dy, y0 + margin_y), y1 - margin_y)
                            if abs(nx - x) < 1e-12 and abs(ny - y) < 1e-12:
                                continue
                            a.xyann = (nx, ny)
                            moved = True
                            break
                        elif hasattr(a, "get_position") and hasattr(a, "set_position"):
                            x, y = a.get_position()
                            nx = min(max(x + dx, xr0 + margin_x), xr1 - margin_x)
                            ny = min(max(y + dy, y0 + margin_y), y1 - margin_y)
                            if abs(nx - x) < 1e-12 and abs(ny - y) < 1e-12:
                                continue
                            a.set_position((nx, ny))
                            moved = True
                            break
                    if not moved:
                        if hasattr(a, "xyann"):
                            x, y = a.xyann
                            nx = min(max(x + sign_x * step_x, xr0 + margin_x), xr1 - margin_x)
                            a.xyann = (nx, y)
                        elif hasattr(a, "get_position") and hasattr(a, "set_position"):
                            x, y = a.get_position()
                            nx = min(max(x + sign_x * step_x, xr0 + margin_x), xr1 - margin_x)
                            a.set_position((nx, y))
                    moved_any = True
            if not moved_any:
                break
            try:
                fig.canvas.draw()
                renderer = fig.canvas.get_renderer()
            except Exception:
                break

        def _font_size_of(a):
            try:
                return float(a.get_fontsize())
            except Exception:
                return None

        def _shrink_font(a, factor=0.88):
            fs = _font_size_of(a)
            if fs is None or fs <= 6.25:
                return False
            try:
                a.set_fontsize(fs * factor)
                return True
            except Exception:
                return False

        for _shrink_pass in range(8):
            try:
                fig.canvas.draw()
                renderer = fig.canvas.get_renderer()
            except Exception:
                break
            bbs2 = [_bbox(a) for a in artists]
            any_ov = False
            shrunk = False
            for i in range(len(artists)):
                bi = bbs2[i]
                if bi is None:
                    continue
                for j in range(i + 1, len(artists)):
                    bj = bbs2[j]
                    if bj is None or not bi.overlaps(bj):
                        continue
                    any_ov = True
                    fi, fj = _font_size_of(artists[i]), _font_size_of(artists[j])
                    if fi is not None and (fj is None or fi >= fj):
                        shrunk = _shrink_font(artists[i]) or shrunk
                    elif fj is not None:
                        shrunk = _shrink_font(artists[j]) or shrunk
            if not any_ov:
                break
            if not shrunk:
                break

    def _init_pane_layout(self):
        """Set a stable initial split so flow lane is always visible."""
        try:
            self.main_frame.update_idletasks()
            h = max(520, self.main_frame.winfo_height())
            self.main_split.sash_place(0, 0, int(h * 0.33))
            bh = max(320, int(h * 0.67))
            if hasattr(self, "bottom_split"):
                self.bottom_split.sash_place(0, 0, int(bh * 0.58))
        except Exception:
            pass

    def _import_csv(self):
        path = filedialog.askopenfilename(
            parent=self.main_frame.winfo_toplevel(),
            title="Import PES CSV",
            initialdir=getattr(self, "_last_opened_dir", self.app_dir),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        self._last_opened_dir = os.path.dirname(path)
        loaded = []
        try:
            with open(path, "r", newline="", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = (row.get("name") or row.get("Species") or "").strip()
                    if not name:
                        continue
                    loaded.append(
                        {
                            "id": int(row.get("id", "0")) if str(row.get("id", "")).strip().isdigit() else None,
                            "name": name,
                            "kind": (row.get("kind") or row.get("Type") or "Intermediate").strip() or "Intermediate",
                            "main_out": (row.get("main_out") or row.get("MainOut") or "").strip(),
                            "sp_out": (row.get("sp_out") or row.get("SPOut") or "").strip(),
                            "image_path": (row.get("image_path") or row.get("Image") or "").strip(),
                            "stoich": self._parse_num_or_fraction((row.get("stoich") or row.get("scale") or "1.0"), default=1.0),
                            "plot": str(row.get("plot", "1")).strip().lower() not in ("0", "false", "no"),
                            "added_text": (row.get("added_text") or row.get("Added") or "").strip(),
                            "removed_text": (row.get("removed_text") or row.get("Removed") or "").strip(),
                        }
                    )
        except Exception as e:
            messagebox.showerror("PES Plot", f"Could not import CSV:\n{e}")
            return
        self.species = loaded
        self._ensure_species_ids()
        self._prune_edge_links()
        self._refresh_ref_values()
        self._recompute()
        messagebox.showinfo("PES Plot", f"Imported {len(loaded)} species from CSV.")

    def _export_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self.main_frame.winfo_toplevel(),
            title="Export PES CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        fields = [
            "id", "name", "kind", "plot", "stoich", "main_model", "sp_model",
            "added_text", "removed_text",
            "main_out", "sp_out", "image_path", "main_e", "enthalpy", "gibbs", "thermal", "sp_e", "e_corr", "g_corr", "h_corr", "rel_e_kcal", "rel_kcal", "rel_h_kcal", "s2", "s2_dev"
        ]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                for sp in self.species:
                    w.writerow({k: sp.get(k, "") for k in fields})
        except Exception as e:
            messagebox.showerror("PES Plot", f"Could not export CSV:\n{e}")
            return
        messagebox.showinfo("PES Plot", f"CSV exported:\n{path}")

    def _open_manual_plot(self):
        """Open a standalone window to manually plot arbitrary intermediates and energies."""
        if not _HAS_MPL:
            messagebox.showerror("Manual Plot", "Matplotlib is not installed.")
            return
            
        top = tk.Toplevel(self.main_frame.winfo_toplevel())
        top.title("Manual PES Plot")
        top.geometry("1000x600")
        top.minsize(800, 500)
        
        main_split = ttk.PanedWindow(top, orient=tk.HORIZONTAL)
        main_split.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Left frame: Data entry
        left_frm = ttk.Frame(main_split)
        main_split.add(left_frm, weight=1)
        
        # Right frame: Plot
        right_frm = ttk.Frame(main_split)
        main_split.add(right_frm, weight=2)
        
        # Build Left Frame (Table & Controls)
        ttk.Label(left_frm, text="Intermediates & Energies", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        
        columns = ("name", "energy")
        tree = ttk.Treeview(left_frm, columns=columns, show="headings", height=15)
        tree.heading("name", text="Intermediate Name")
        tree.heading("energy", text="Energy")
        tree.column("name", width=120)
        tree.column("energy", width=80, anchor="e")
        tree.pack(fill=tk.BOTH, expand=True)
        
        entry_frm = ttk.Frame(left_frm)
        entry_frm.pack(fill=tk.X, pady=10)
        
        ttk.Label(entry_frm, text="Name:").grid(row=0, column=0, sticky="w", padx=2)
        name_var = tk.StringVar()
        ttk.Entry(entry_frm, textvariable=name_var, width=15).grid(row=0, column=1, sticky="ew", padx=2)
        
        ttk.Label(entry_frm, text="Energy:").grid(row=0, column=2, sticky="w", padx=(10, 2))
        energy_var = tk.StringVar()
        ttk.Entry(entry_frm, textvariable=energy_var, width=10).grid(row=0, column=3, sticky="ew", padx=2)
        
        entry_frm.columnconfigure(1, weight=1)
        entry_frm.columnconfigure(3, weight=1)
        
        btn_frm = ttk.Frame(left_frm)
        btn_frm.pack(fill=tk.X)
        
        def _add_row():
            n = name_var.get().strip()
            e_str = energy_var.get().strip()
            if not n or not e_str:
                return
            try:
                e = float(e_str)
            except ValueError:
                messagebox.showwarning("Invalid", "Energy must be a number.", parent=top)
                return
            tree.insert("", "end", values=(n, f"{e:.2f}"))
            name_var.set("")
            energy_var.set("")
            
        def _rem_row():
            sel = tree.selection()
            for s in sel:
                tree.delete(s)
                
        def _clear_all():
            for c in tree.get_children():
                tree.delete(c)
                
        ttk.Button(btn_frm, text="Add", command=_add_row).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frm, text="Remove Selected", command=_rem_row).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frm, text="Clear All", command=_clear_all).pack(side=tk.LEFT, padx=2)
        
        # Build Right Frame (Plot)
        fig = Figure(figsize=(6, 4), dpi=100)
        ax = fig.add_subplot(111)
        canvas = FigureCanvasTkAgg(fig, master=right_frm)
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        def _generate_plot():
            ax.clear()
            items = tree.get_children()
            if not items:
                ax.text(0.5, 0.5, "No data to plot", ha="center", va="center", transform=ax.transAxes)
                canvas.draw()
                return
                
            names = []
            energies = []
            for item in items:
                vals = tree.item(item, "values")
                names.append(vals[0])
                energies.append(float(vals[1]))
                
            # Plot stepwise
            labels_x = []
            
            plateau_width = 0.6
            gap = 0.4
            
            curr_x = 0.0
            
            # Simple scaling for text offsets
            energy_range = max(energies) - min(energies) if len(energies) > 1 else 10.0
            if energy_range == 0:
                energy_range = 10.0
            y_offset = energy_range * 0.05
            
            style = style_var.get()
            is_classic = "Classic" in style
            is_aks = "AKS" in style
            
            y_min, y_max = min(energies) if energies else 0, max(energies) if energies else 10
            
            for i in range(len(energies)):
                center_x = curr_x + plateau_width/2
                
                if is_classic:
                    marker = "o" if "Classic 1" in style else "s"
                    ax.plot([center_x], [energies[i]], marker=marker, markersize=10, color="#2563eb", zorder=3)
                    ax.text(center_x, energies[i] + y_offset, f"{energies[i]:.2f}", ha="center", va="bottom", fontsize=14, fontweight="bold", color="#111827")
                elif is_aks:
                    ax.plot([center_x - 0.15, center_x + 0.15], [energies[i], energies[i]], color="#9fc88a", linewidth=9, zorder=3)
                    ax.text(center_x, energies[i] + y_offset, f"{energies[i]:.2f}", ha="center", va="bottom", fontsize=14, fontweight="bold", color="#111827")
                else:
                    # CCC plateaus
                    ax.plot([center_x - 0.3, center_x + 0.3], [energies[i], energies[i]], color="#111827", linewidth=7, zorder=3)
                    ax.text(center_x, energies[i] + y_offset, f"{energies[i]:.2f}", ha="center", va="bottom", fontsize=14, fontweight="bold", color="#111827")
                
                labels_x.append(center_x)
                
                if i > 0:
                    prev_center = labels_x[i-1]
                    
                    if is_aks:
                        lx = [prev_center + 0.15, center_x - 0.15]
                        ly = [energies[i-1], energies[i]]
                        ax.plot(lx, ly, color="#9fc88a", linestyle="-", linewidth=1.8, zorder=1)
                    elif is_classic:
                        ls = ":" if "Classic 1" in style else "-"
                        lw = 3.5 if "Classic 1" in style else 1.0
                        ax.plot([prev_center, center_x], [energies[i-1], energies[i]], color="#111827", linestyle=ls, linewidth=lw, zorder=1)
                    else:
                        # CCC gradient connections
                        if "Curved" in style:
                            lx, ly = PESPlotModule._build_smooth_curve([prev_center + 0.3, center_x - 0.3], [energies[i-1], energies[i]], points_per_seg=36)
                        else:
                            lx, ly = [prev_center + 0.3, center_x - 0.3], [energies[i-1], energies[i]]
                            
                        segments = [[(lx[k], ly[k]), (lx[k + 1], ly[k + 1])] for k in range(len(lx) - 1)]
                        cmap = LinearSegmentedColormap.from_list("peakmap", [(0.00, "#16a34a"), (0.55, "#fde047"), (1.00, "#ef4444")])
                        import matplotlib.colors as mcolors
                        lc = LineCollection(segments, linewidths=2.8, cmap=cmap, zorder=1, linestyles="-")
                        lc.set_array([(ly[k] + ly[k+1])/2.0 for k in range(len(segments))])
                        lc.set_norm(mcolors.Normalize(vmin=y_min, vmax=y_max))
                        ax.add_collection(lc)
                
                curr_x += plateau_width + gap
                
            ax.set_xticks(labels_x)
            ax.set_xticklabels(names, rotation=0 if len(names) < 5 else 45, ha="center" if len(names) < 5 else "right", fontweight="bold", fontsize=12)
            ax.set_ylabel("Relative Energy", fontweight="bold", fontsize=14)
            ax.tick_params(axis="y", labelsize=12)
            
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['bottom'].set_visible(False)
            ax.xaxis.set_ticks_position('none')
            
            fig.tight_layout()
            canvas.draw()
            
        plot_btn_frm = ttk.Frame(right_frm)
        plot_btn_frm.pack(fill=tk.X, pady=(10, 0))
        
        style_var = tk.StringVar(value="CCC: 1 Curved")
        ttk.Combobox(plot_btn_frm, textvariable=style_var, values=["CCC: 1 Curved", "CCC: 2 Straight", "Classical: 1 Classic 1", "Classical: 2 Classic 2", "AKS Style"], state="readonly", width=22).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(plot_btn_frm, text="Generate Plot", command=_generate_plot, style="PESAction.TButton").pack(side=tk.LEFT, padx=5)
        
        def _save_plot():
            path = filedialog.asksaveasfilename(parent=top, defaultextension=".png", filetypes=[("PNG Image", "*.png"), ("SVG", "*.svg"), ("PDF", "*.pdf")])
            if path:
                try:
                    fig.savefig(path, bbox_inches="tight", dpi=300)
                    messagebox.showinfo("Saved", f"Plot saved to:\n{path}", parent=top)
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to save plot:\n{e}", parent=top)
                    
        ttk.Button(plot_btn_frm, text="Save Image", command=_save_plot).pack(side=tk.LEFT, padx=5)

    def _show_species_context_menu(self, sp_id, x_root, y_root):
        import tkinter as tk
        import os, shutil, subprocess
        from tkinter import simpledialog
        
        sp = next((s for s in self.species if s.get("id") == sp_id), None)
        if not sp: return

        menu = tk.Menu(self._plot_window, tearoff=0)
        
        def open_structure():
            path = sp.get("main_out", "")
            if path and os.path.isfile(path):
                if shutil.which("acyviewer"):
                    subprocess.Popen(["acyviewer", path])
                else:
                    os.startfile(path)
        
        def open_folder():
            path = sp.get("main_out", "")
            if path and os.path.isfile(path):
                os.startfile(os.path.dirname(path))

        def edit_temp():
            new_name = _ask_rich_text("Edit Name (Plot Only)", "Enter temporary name for plot\\n(Supports basic math, e.g. $\Delta G^\ddagger$):", initialvalue=sp.get("plot_name_override", sp.get("name", "")), parent=self._plot_window)
            if new_name is not None:
                sp["plot_name_override"] = new_name.strip()
                self._render_plot()
                
        def change_color():
            import tkinter.colorchooser as cc
            c = cc.askcolor(title="Label Color")[1]
            if c:
                sp["label_color"] = c
                self._render_plot()
                
        def edit_perm():
            new_name = _ask_rich_text("Edit Name (Permanent)", "Enter permanent species name:", initialvalue=sp.get("name", ""), parent=self._plot_window)
            if new_name is not None:
                sp["name"] = new_name.strip()
                sp.pop("plot_name_override", None)
                self._render_plot()
                if hasattr(self, "_refresh_list"):
                    self._refresh_list()
                
        has_out = bool(sp.get("main_out") and os.path.isfile(sp.get("main_out")))
        
        v_menu = tk.Menu(menu, tearoff=0)
        v_menu.add_command(label="ACYView", command=lambda s=sp_id: self._open_external_3d(s, "ACYView"))
        v_menu.add_command(label="Chemcraft", command=lambda s=sp_id: self._open_external_3d(s, "Chemcraft"))
        v_menu.add_command(label="Jmol", command=lambda s=sp_id: self._open_external_3d(s, "Jmol"))
        menu.add_cascade(label="Open Structure", menu=v_menu, state="normal" if has_out else "disabled")
        
        menu.add_command(label="Open Folder", command=open_folder, state="normal" if has_out else "disabled")
        menu.add_separator()
        menu.add_command(label="Edit Name (Plot Only) / LaTeX...", command=edit_temp)
        menu.add_command(label="Change Text Color...", command=change_color)
        menu.add_command(label="Edit Name (Permanent)", command=edit_perm)
        
        menu.tk_popup(x_root, y_root)

    @staticmethod
    def _parse_out_thermo(path: str) -> dict:
        """Modular wrapper for ORCA parsing with fast caching."""
        if not hasattr(PESPlotModule._parse_out_thermo, "cache"):
            PESPlotModule._parse_out_thermo.cache = {}
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return {}
            
        cache = PESPlotModule._parse_out_thermo.cache
        if path in cache and cache[path]['mtime'] == mtime:
            return cache[path]['info']
            
        p = ORCAParser(path)
        info = p.get_all_info()
        cache[path] = {'mtime': mtime, 'info': info}
        return info

    @staticmethod
    def _apply_external_overrides(rows: list[dict]) -> list[dict]:
        """Hook for future CSV-driven/advanced correction logic injection."""
        return rows

