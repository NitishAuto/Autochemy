import os
import json
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AutoChemy_User_Data", "bond_lengths.json")

DEFAULT_DATA = [
    {"atom1": "H", "atom2": "H", "bond_type": "Single", "length": 0.74},
    {"atom1": "H", "atom2": "C", "bond_type": "Single", "length": 1.10},
    {"atom1": "H", "atom2": "N", "bond_type": "Single", "length": 1.00},
    {"atom1": "H", "atom2": "O", "bond_type": "Single", "length": 0.97},
    {"atom1": "H", "atom2": "S", "bond_type": "Single", "length": 1.32},
    {"atom1": "H", "atom2": "F", "bond_type": "Single", "length": 0.92},
    {"atom1": "H", "atom2": "Cl", "bond_type": "Single", "length": 1.27},
    {"atom1": "H", "atom2": "Br", "bond_type": "Single", "length": 1.41},
    {"atom1": "H", "atom2": "I", "bond_type": "Single", "length": 1.61},
    {"atom1": "C", "atom2": "C", "bond_type": "Single", "length": 1.54},
    {"atom1": "C", "atom2": "C", "bond_type": "Double", "length": 1.34},
    {"atom1": "C", "atom2": "C", "bond_type": "Triple", "length": 1.20},
    {"atom1": "C", "atom2": "N", "bond_type": "Single", "length": 1.47},
    {"atom1": "C", "atom2": "N", "bond_type": "Double", "length": 1.28},
    {"atom1": "C", "atom2": "N", "bond_type": "Triple", "length": 1.16},
    {"atom1": "C", "atom2": "O", "bond_type": "Single", "length": 1.43},
    {"atom1": "C", "atom2": "O", "bond_type": "Double", "length": 1.20},
    {"atom1": "C", "atom2": "F", "bond_type": "Single", "length": 1.41},
    {"atom1": "C", "atom2": "Cl", "bond_type": "Single", "length": 1.76},
    {"atom1": "C", "atom2": "Br", "bond_type": "Single", "length": 1.91},
    {"atom1": "C", "atom2": "S", "bond_type": "Single", "length": 1.81},
    {"atom1": "N", "atom2": "N", "bond_type": "Single", "length": 1.45},
    {"atom1": "N", "atom2": "N", "bond_type": "Double", "length": 1.23},
    {"atom1": "N", "atom2": "N", "bond_type": "Triple", "length": 1.10},
    {"atom1": "N", "atom2": "O", "bond_type": "Single", "length": 1.36},
    {"atom1": "N", "atom2": "O", "bond_type": "Double", "length": 1.20},
    {"atom1": "O", "atom2": "O", "bond_type": "Single", "length": 1.45},
    {"atom1": "O", "atom2": "O", "bond_type": "Double", "length": 1.21},
    {"atom1": "F", "atom2": "F", "bond_type": "Single", "length": 1.43},
    {"atom1": "Cl", "atom2": "Cl", "bond_type": "Single", "length": 1.99},
    {"atom1": "Br", "atom2": "Br", "bond_type": "Single", "length": 2.28},
    {"atom1": "I", "atom2": "I", "bond_type": "Single", "length": 2.66},
    {"atom1": "Pd", "atom2": "Cl", "bond_type": "Single", "length": 2.31},
    {"atom1": "Pd", "atom2": "N", "bond_type": "Single", "length": 2.07},
    {"atom1": "Pd", "atom2": "C", "bond_type": "Single", "length": 2.10},
    {"atom1": "Pd", "atom2": "O", "bond_type": "Single", "length": 2.01},
]

def _normalize(a1, a2):
    return tuple(sorted([a1.strip().capitalize(), a2.strip().capitalize()]))

class BondLengthDatabase:
    def __init__(self):
        self.data = []
        self.load()

    def load(self):
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, "r") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = DEFAULT_DATA.copy()
        else:
            self.data = DEFAULT_DATA.copy()
            self.save()

    def save(self):
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
        try:
            with open(DB_FILE, "w") as f:
                json.dump(self.data, f, indent=4)
        except Exception:
            pass

    def add_bond(self, a1, a2, btype, length):
        a1, a2 = _normalize(a1, a2)
        # Check if exists, update it
        for entry in self.data:
            e_a1, e_a2 = _normalize(entry["atom1"], entry["atom2"])
            if e_a1 == a1 and e_a2 == a2 and entry["bond_type"].lower() == btype.lower():
                entry["length"] = float(length)
                self.save()
                return
        self.data.append({
            "atom1": a1,
            "atom2": a2,
            "bond_type": btype.capitalize(),
            "length": float(length)
        })
        self.save()

    def get_bond(self, a1, a2, btype):
        a1, a2 = _normalize(a1, a2)
        for entry in self.data:
            e_a1, e_a2 = _normalize(entry["atom1"], entry["atom2"])
            if e_a1 == a1 and e_a2 == a2 and entry["bond_type"].lower() == btype.lower():
                return entry["length"]
        return None

def _center_window(win, w, h):
    win.geometry(f"{w}x{h}")
    win.update_idletasks()
    x = max(0, win.winfo_screenwidth() // 2 - w // 2)
    y = max(0, win.winfo_screenheight() // 2 - h // 2)
    win.geometry(f"{w}x{h}+{x}+{y}")

def show_bond_chart(parent):
    db = BondLengthDatabase()
    
    win = tk.Toplevel(parent)
    win.title("Bond Length Reference Chart")
    _center_window(win, 850, 700)
    win.transient(parent)
    
    filter_frame = ttk.Frame(win)
    filter_frame.pack(fill=tk.X, padx=10, pady=(10, 0))
    ttk.Label(filter_frame, text="Filter by Atom:").pack(side="left")
    filter_var = tk.StringVar()
    e_filter = ttk.Entry(filter_frame, textvariable=filter_var, width=15)
    e_filter.pack(side="left", padx=5)
    
    # Table Frame
    table_frame = ttk.Frame(win)
    table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    
    cols = ("Bond 1", "Length 1", "Bond 2", "Length 2", "Bond 3", "Length 3")
    tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=15)
    for i in range(1, 4):
        tree.heading(f"Bond {i}", text="Bond")
        tree.column(f"Bond {i}", width=120, anchor="center")
        tree.heading(f"Length {i}", text="Length (Å)")
        tree.column(f"Length {i}", width=120, anchor="center")
    
    scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    
    def get_sort_key(entry):
        element_order = {"H": 1, "C": 2, "N": 3, "O": 4, "F": 5, "Cl": 6, "Br": 7, "S": 8, "I": 9, "Pd": 10}
        a1, a2 = entry["atom1"], entry["atom2"]
        if element_order.get(a1, 99) > element_order.get(a2, 99):
            a1, a2 = a2, a1
        btype_order = {"Single": 1, "Double": 2, "Triple": 3}
        return (btype_order.get(entry["bond_type"], 1), element_order.get(a1, 99), element_order.get(a2, 99), a1, a2)

    def get_bond_symbol(entry):
        syms = {"Single": "-", "Double": "=", "Triple": "≡"}
        sym = syms.get(entry["bond_type"], "-")
        a1, a2 = entry["atom1"], entry["atom2"]
        return f"{a1}{sym}{a2}"

    def refresh_table(*args):
        for item in tree.get_children():
            tree.delete(item)
        
        f_val = filter_var.get().strip().lower()
        if f_val:
            filtered_data = [d for d in db.data if f_val in d["atom1"].lower() or f_val in d["atom2"].lower()]
        else:
            filtered_data = db.data
            
        sorted_data = sorted(filtered_data, key=get_sort_key)
        for i in range(0, len(sorted_data), 3):
            chunk = sorted_data[i:i+3]
            vals = []
            for d in chunk:
                vals.extend([get_bond_symbol(d), f"{d['length']:.3f}"])
            while len(vals) < 6:
                vals.extend(["", ""])
            tree.insert("", "end", values=tuple(vals))
            
    filter_var.trace_add("write", refresh_table)
    refresh_table()
    
    # Add Frame
    add_frame = ttk.LabelFrame(win, text="Add / Update Custom Bond", padding=10)
    add_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
    
    ttk.Label(add_frame, text="Atom 1:").grid(row=0, column=0, padx=2, pady=2, sticky="e")
    e_a1 = ttk.Entry(add_frame, width=5)
    e_a1.grid(row=0, column=1, padx=2, pady=2, sticky="w")
    
    ttk.Label(add_frame, text="Atom 2:").grid(row=0, column=2, padx=2, pady=2, sticky="e")
    e_a2 = ttk.Entry(add_frame, width=5)
    e_a2.grid(row=0, column=3, padx=2, pady=2, sticky="w")
    
    ttk.Label(add_frame, text="Type:").grid(row=0, column=4, padx=2, pady=2, sticky="e")
    cb_type = ttk.Combobox(add_frame, values=["Single", "Double", "Triple"], width=8, state="readonly")
    cb_type.current(0)
    cb_type.grid(row=0, column=5, padx=2, pady=2, sticky="w")
    
    ttk.Label(add_frame, text="Length (Å):").grid(row=1, column=0, columnspan=2, padx=2, pady=5, sticky="e")
    e_len = ttk.Entry(add_frame, width=10)
    e_len.grid(row=1, column=2, columnspan=2, padx=2, pady=5, sticky="w")
    
    def do_add():
        a1 = e_a1.get().strip()
        a2 = e_a2.get().strip()
        btype = cb_type.get()
        v_len = e_len.get().strip()
        if not a1 or not a2 or not v_len:
            messagebox.showwarning("Incomplete", "Please fill Atom 1, Atom 2, and Length.", parent=win)
            return
        try:
            length = float(v_len)
        except ValueError:
            messagebox.showwarning("Invalid", "Length must be a number.", parent=win)
            return
        db.add_bond(a1, a2, btype, length)
        e_a1.delete(0, tk.END)
        e_a2.delete(0, tk.END)
        e_len.delete(0, tk.END)
        refresh_table()
        
    ttk.Button(add_frame, text="Save Bond", command=do_add).grid(row=1, column=4, columnspan=2, padx=2, pady=5)


def show_bond_query(parent):
    db = BondLengthDatabase()
    
    win = tk.Toplevel(parent)
    win.title("Query Bond Length")
    _center_window(win, 450, 420)
    win.transient(parent)
    
    frame = ttk.Frame(win, padding=20)
    frame.pack(fill=tk.BOTH, expand=True)
    
    ttk.Label(frame, text="Atom 1:", font=("Segoe UI", 10)).grid(row=0, column=0, pady=5, sticky="e")
    e_a1 = ttk.Entry(frame, width=10, font=("Segoe UI", 10))
    e_a1.grid(row=0, column=1, pady=5, padx=5, sticky="w")
    
    ttk.Label(frame, text="Atom 2:", font=("Segoe UI", 10)).grid(row=1, column=0, pady=5, sticky="e")
    e_a2 = ttk.Entry(frame, width=10, font=("Segoe UI", 10))
    e_a2.grid(row=1, column=1, pady=5, padx=5, sticky="w")
    
    ttk.Label(frame, text="Type:", font=("Segoe UI", 10)).grid(row=2, column=0, pady=5, sticky="e")
    cb_type = ttk.Combobox(frame, values=["Single", "Double", "Triple"], width=10, state="readonly", font=("Segoe UI", 10))
    cb_type.current(0)
    cb_type.grid(row=2, column=1, pady=5, padx=5, sticky="w")
    
    res_label = tk.Label(frame, text="", font=("Segoe UI", 12, "bold"), fg="#0b5cab")
    res_label.grid(row=4, column=0, columnspan=2, pady=10)
    
    # Save to DB Frame
    add_frame = ttk.Frame(frame)
    ttk.Label(add_frame, text="Length (Å):", font=("Segoe UI", 9)).pack(side="left")
    e_new_len = ttk.Entry(add_frame, width=8, font=("Segoe UI", 9))
    e_new_len.pack(side="left", padx=5)
    
    def do_add_from_query():
        a1 = e_a1.get().strip()
        a2 = e_a2.get().strip()
        btype = cb_type.get()
        v = e_new_len.get().strip()
        if not v:
            return
        try:
            val = float(v)
        except ValueError:
            messagebox.showwarning("Invalid", "Enter a numeric length.", parent=win)
            return
        db.add_bond(a1, a2, btype, val)
        res_label.config(text=f"Saved! {a1}-{a2} ({btype}) : {val:.3f} Å", fg="#2e7d32")
        e_new_len.delete(0, tk.END)
        add_frame.grid_remove()

    ttk.Button(add_frame, text="Save to DB", command=do_add_from_query).pack(side="left")
    add_frame.grid(row=5, column=0, columnspan=2, pady=5)
    add_frame.grid_remove()

    btn_search_web = ttk.Button(frame, text="Search Web")
    btn_search_web.grid(row=6, column=0, columnspan=2, pady=5)
    btn_search_web.grid_remove()
    
    def do_query():
        a1 = e_a1.get().strip()
        a2 = e_a2.get().strip()
        btype = cb_type.get()
        if not a1 or not a2:
            return
        
        btn_search_web.grid_remove()
        add_frame.grid_remove()
        
        length = db.get_bond(a1, a2, btype)
        if length is not None:
            res_label.config(text=f"{a1}-{a2} ({btype}) : {length:.3f} Å", fg="#2e7d32")
            e_new_len.delete(0, tk.END)
            e_new_len.insert(0, str(length))
            add_frame.grid() # Allow updating even if found
        else:
            res_label.config(text="Not found in database.", fg="#c62828")
            q = f"{a1}-{a2} {btype} bond length in angstroms"
            def search():
                webbrowser.open(f"https://www.google.com/search?q={q}")
            btn_search_web.config(command=search)
            btn_search_web.grid()
            add_frame.grid() # Allow adding

    ttk.Button(frame, text="Get Length", command=do_query).grid(row=3, column=0, columnspan=2, pady=10)
