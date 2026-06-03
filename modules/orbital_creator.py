import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import pandas as pd
import os
import subprocess


# ==========================================================
# CORE ANALYSIS LOGIC (UNCHANGED)
# ==========================================================

def analyze_orca_out(file_path):
    data = []
    mag_orbitals = []

    with open(file_path, 'r') as file:
        lines = file.readlines()

    lines.reverse()

    extracted_lines = []
    target_found = False
    count = 0

    for line in lines:
        if "UHF Corresponding Orbitals" in line:
            target_found = True
            continue
        if target_found:
            if "Orbital Energies of Quasi-Restricted MO's" in line or count >= 4000:
                break
            extracted_lines.append(line.strip())
            count += 1

    extracted_lines.reverse()
    extracted_lines = extracted_lines[:-6]

    for line in extracted_lines:
        parts = line.split()
        orbital = int(parts[0].split('(')[0])
        degeneracy = int(parts[1][0])
        energy_au = float(parts[3])
        energy_ev = float(parts[5])
        data.append([orbital, degeneracy, energy_au, energy_ev])

    start_flag = "(*)  the overlap is weighted by the product of occupation numbers"
    end_flag = " Orbital    Overlap(*)"

    capture = False
    for line in lines:
        if line.strip() == start_flag:
            capture = True
            continue
        if capture and end_flag in line:
            break
        if capture:
            mag_orbitals.append(line.rstrip())

    mag_orbitals.reverse()

    df = pd.DataFrame(
        data,
        columns=["Orbital", "Degeneracy", "Energy (AU)", "Energy (eV)"]
    )

    doubly_start = 0
    doubly_end = -1
    singly_start = -1
    singly_end = -1
    total = len(df)

    for _, row in df.iterrows():
        if row["Degeneracy"] == 2:
            if row["Energy (AU)"] <= -1:
                doubly_start += 1
                doubly_end += 1
            else:
                doubly_end += 1
        elif row["Degeneracy"] == 1:
            singly_end += 1

    if singly_end != -1:
        singly_start = doubly_end + 1
        singly_end = doubly_end + singly_end + 1

    return (
        df,
        mag_orbitals,
        doubly_start,
        doubly_end,
        singly_start,
        singly_end,
        total
    )


# ==========================================================
# ORBITAL CREATOR MODULE (PLUGIN VERSION)
# ==========================================================

class OrbitalCreatorModule:
    """Orbital Creator module for ORCA Software Suite"""

    def __init__(self, parent_frame):
        self.parent = parent_frame
        self.main_frame = ttk.Frame(parent_frame, padding=10)

        self.file_path = tk.StringVar()
        self._create_ui()

    # ---------- Required by main app ----------

    def get_name(self):
        return "Orbital Creator"

    def get_icon(self):
        return "🧬"

    def activate(self):
        self.main_frame.pack(fill=tk.BOTH, expand=True)

    def deactivate(self):
        self.main_frame.pack_forget()

    # ======================================================
    # UI
    # ======================================================

    def _create_ui(self):

        head = ttk.Frame(self.main_frame)
        head.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(head, text=f"{self.get_icon()}  {self.get_name()}", font=("Segoe UI", 13, "bold"), foreground="#0b5cab").pack(side=tk.LEFT)
        ttk.Separator(self.main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 10))

        # -------- ORCA output file --------
        file_frame = ttk.LabelFrame(
            self.main_frame, text="ORCA Output File", padding=10
        )
        file_frame.pack(fill=tk.X, pady=5)

        ttk.Entry(
            file_frame, textvariable=self.file_path, width=80
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            file_frame, text="Browse", command=self.select_file
        ).pack(side=tk.LEFT)

        # -------- Buttons --------
        btn_frame = ttk.Frame(self.main_frame)
        btn_frame.pack(pady=5)

        ttk.Button(
            btn_frame, text="Run Analysis", command=self.run_analysis
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            btn_frame,
            text="Generate QRO files",
            command=self.submit_to_bash
        ).pack(side=tk.LEFT, padx=5)

        # -------- Summary --------
        self.summary_label = ttk.Label(
            self.main_frame,
            text="No analysis performed",
            font=("TkDefaultFont", 10, "bold")
        )
        self.summary_label.pack(pady=5)

        # -------- Editable ranges --------
        range_frame = ttk.LabelFrame(
            self.main_frame, text="Orbital Ranges (Editable)", padding=10
        )
        range_frame.pack(fill=tk.X, pady=5)

        self.d_start = tk.StringVar()
        self.d_end = tk.StringVar()
        self.s_start = tk.StringVar()
        self.s_end = tk.StringVar()
        self.total_var = tk.StringVar()
        self.unoc = tk.StringVar()


        labels = [
            ("Doubly start", self.d_start),
            ("Doubly end", self.d_end),
            ("Singly start", self.s_start),
            ("Singly end", self.s_end),
            ("Total", self.total_var),
            ("Unoccupied number", self.unoc)
        ]

        for i, (text, var) in enumerate(labels):
            ttk.Label(range_frame, text=text).grid(
                row=0, column=2*i, padx=5
            )
            ttk.Entry(
                range_frame, textvariable=var, width=8
            ).grid(row=0, column=2*i+1)

        # -------- Output tabs --------
        notebook = ttk.Notebook(self.main_frame)
        notebook.pack(fill=tk.BOTH, expand=True, pady=10)

        self.occ_text = scrolledtext.ScrolledText(
            notebook, font=("Courier", 10)
        )
        notebook.add(self.occ_text, text="Orbital Occupancy")

        self.mag_text = scrolledtext.ScrolledText(
            notebook, font=("Courier", 10)
        )
        notebook.add(self.mag_text, text="Magnetic Orbitals")

    # ======================================================
    # ACTIONS
    # ======================================================

    def select_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("ORCA output files", "*.out"), ("All files", "*.*")]
        )
        if path:
            self.file_path.set(path)

    def run_analysis(self):
        path = self.file_path.get()

        if not path or not os.path.isfile(path):
            messagebox.showerror("Error", "Please select a valid ORCA .out file")
            return

        try:
            (
                df,
                mag_orb,
                d_start,
                d_end,
                s_start,
                s_end,
                total
            ) = analyze_orca_out(path)

            self.occ_text.delete(1.0, tk.END)
            self.occ_text.insert(tk.END, df.to_string(index=False))

            self.mag_text.delete(1.0, tk.END)
            self.mag_text.insert(tk.END, "\n".join(mag_orb))

            self.d_start.set(d_start)
            self.d_end.set(d_end)
            self.s_start.set(s_start)
            self.s_end.set(s_end)
            self.total_var.set(total)
            self.unoc.set(0)

            self.summary_label.config(
                text="Orbital ranges auto-detected (editable)"
            )

        except Exception as e:
            messagebox.showerror("Error", str(e))

    def submit_to_bash(self):
        script = os.path.abspath("modules/qro_files_bash_script.sh")
        out_file = self.file_path.get()
        workdir = os.path.dirname(os.path.abspath(out_file))

        args = [
            self.d_start.get(),
            self.d_end.get(),
            self.s_start.get(),
            self.s_end.get(),
            self.total_var.get(),
            self.unoc.get()
        ]

        if not all(args):
            messagebox.showerror("Error", "All fields must be filled")
            return

        if not os.path.isfile(script):
            messagebox.showerror(
                "Error", f"qro_files_bash_script.sh not found:\n{script}"
            )
            return

        try:
            subprocess.run(
                ["bash", script] + args,
                cwd=workdir,
                check=True
            )
            messagebox.showinfo(
                "Success", f"Executed in:\n{workdir}"
            )
        except subprocess.CalledProcessError as e:
            messagebox.showerror("Bash Error", str(e))
