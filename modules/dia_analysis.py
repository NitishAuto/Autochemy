import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import os
import shutil
from PIL import Image, ImageTk
import pandas as pd

from modules.base_module import BaseModule


class DIAAnalysisModule(BaseModule):

    def get_name(self):
        return "DIA"

    def get_icon(self):
        return "📈"

    # ============================================================
    def create_ui(self):
        self.main_frame = ttk.Frame(self.parent_frame, padding=25)

        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("TLabel", font=("Segoe UI", 11))
        style.configure("TButton", font=("Segoe UI", 11, "bold"))

        # ================= HEADER =================
        head = ttk.Frame(self.main_frame)
        head.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(head, text=f"{self.get_icon()}  {self.get_name()}", font=("Segoe UI", 13, "bold"), foreground="#0b5cab").pack(side=tk.LEFT)
        ttk.Separator(self.main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 10))

        # ================= WORKFLOW =================
        workflow_frame = ttk.LabelFrame(self.main_frame, text="Workflow", padding=15)
        workflow_frame.pack(fill=tk.X, pady=10)

        self.workflow = tk.StringVar(value="no_frag")

        inner_frame = ttk.Frame(workflow_frame)
        inner_frame.pack(anchor="w")

        ttk.Label(inner_frame, text="Select Mode:", font=("Segoe UI", 11, "bold"))\
            .pack(side=tk.LEFT, padx=(0, 15))

        ttk.Radiobutton(inner_frame, text="Fragments NOT available",
                        variable=self.workflow, value="no_frag",
                        command=self.update_ui).pack(side=tk.LEFT, padx=10)

        ttk.Radiobutton(inner_frame, text="Fragments already available",
                        variable=self.workflow, value="have_frag",
                        command=self.update_ui).pack(side=tk.LEFT, padx=10)

        # ================= DYNAMIC FRAME =================
        self.dynamic_frame = ttk.Frame(self.main_frame)
        self.dynamic_frame.pack(fill=tk.X, pady=10)

        self.file_var = tk.StringVar()
        self.have_frag_txt = tk.StringVar()
        self.full_path = None

        self.create_no_frag_ui()
        self.create_have_frag_ui()

        # ================= ORCA INPUT =================
        self.input_frame = ttk.LabelFrame(self.main_frame, text="🧾 ORCA Input Template", padding=10)

        self.input_text = tk.Text(
            self.input_frame,
            height=10,
            font=("Consolas", 11),
            bg="#1e1e1e",
            fg="#00ffcc",
            insertbackground="white"
        )
        self.input_text.pack(fill=tk.BOTH, expand=True)

        self.input_text.insert("1.0", """! B3LYP def2-SVP Opt

%scf
  maxiter 200
end

* xyz 0 1
""")

        # ================= BUTTON BAR =================
        btn_frame = ttk.Frame(self.main_frame)
        btn_frame.pack(fill=tk.X, pady=15)

        ttk.Button(btn_frame, text="🚀 Run DIA Analysis",
                   command=self.run).pack(side=tk.LEFT, padx=5)

        ttk.Button(btn_frame, text="💾 Download CSV",
                   command=self.download_csv).pack(side=tk.LEFT, padx=5)

        # ================= STATUS =================
        self.status = tk.StringVar(value="Ready")
        ttk.Label(self.main_frame, textvariable=self.status).pack(anchor="w")

        self.update_ui()

    # ============================================================
    def create_no_frag_ui(self):
        self.no_frag_frame = ttk.Frame(self.dynamic_frame)

        ttk.Label(self.no_frag_frame, text="Input XYZ File").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(self.no_frag_frame, textvariable=self.file_var, width=50)\
            .grid(row=0, column=1, padx=5)
        ttk.Button(self.no_frag_frame, text="Browse", command=self.browse)\
            .grid(row=0, column=2, padx=5)

        row_frame = ttk.Frame(self.no_frag_frame)
        row_frame.grid(row=1, column=0, columnspan=4, pady=10, sticky="w")

        self.frag_list = tk.StringVar(value="13,11,18,9,14,10,12,17")
        self.rmsd = tk.DoubleVar(value=0.0005)

        ttk.Label(row_frame, text="Fragment_1 Atoms").pack(side=tk.LEFT, padx=5)
        ttk.Entry(row_frame, textvariable=self.frag_list, width=25).pack(side=tk.LEFT, padx=5)

        ttk.Label(row_frame, text="RMSD").pack(side=tk.LEFT, padx=15)
        ttk.Entry(row_frame, textvariable=self.rmsd, width=10).pack(side=tk.LEFT, padx=5)

        charge_frame = ttk.Frame(self.no_frag_frame)
        charge_frame.grid(row=2, column=0, columnspan=4, pady=10, sticky="w")

        self.f1_charge = tk.IntVar(value=0)
        self.f1_spin = tk.IntVar(value=1)
        self.f2_charge = tk.IntVar(value=0)
        self.f2_spin = tk.IntVar(value=1)

        ttk.Label(charge_frame, text="Frag1 Charge").pack(side=tk.LEFT, padx=5)
        ttk.Entry(charge_frame, textvariable=self.f1_charge, width=8).pack(side=tk.LEFT)

        ttk.Label(charge_frame, text="Frag1 Spin").pack(side=tk.LEFT, padx=5)
        ttk.Entry(charge_frame, textvariable=self.f1_spin, width=8).pack(side=tk.LEFT)

        ttk.Label(charge_frame, text="   ").pack(side=tk.LEFT)

        ttk.Label(charge_frame, text="Frag2 Charge").pack(side=tk.LEFT, padx=5)
        ttk.Entry(charge_frame, textvariable=self.f2_charge, width=8).pack(side=tk.LEFT)

        ttk.Label(charge_frame, text="Frag2 Spin").pack(side=tk.LEFT, padx=5)
        ttk.Entry(charge_frame, textvariable=self.f2_spin, width=8).pack(side=tk.LEFT)

    # ============================================================
    def create_have_frag_ui(self):
        self.have_frag_frame = ttk.Frame(self.dynamic_frame)

        ttk.Label(self.have_frag_frame, text="Input XYZ File").grid(row=0, column=0, padx=5, pady=5)
        ttk.Entry(self.have_frag_frame, textvariable=self.file_var, width=50)\
            .grid(row=0, column=1, padx=5)
        ttk.Button(self.have_frag_frame, text="Browse", command=self.browse)\
            .grid(row=0, column=2, padx=5)

        self.rmsd_have = tk.DoubleVar(value=0.0005)
        ttk.Label(self.have_frag_frame, text="RMSD").grid(row=0, column=3)
        ttk.Entry(self.have_frag_frame, textvariable=self.rmsd_have, width=10).grid(row=0, column=4)

        self.frag1_path = tk.StringVar()
        self.frag2_path = tk.StringVar()

        ttk.Label(self.have_frag_frame, text="Fragment 1 Folder").grid(row=1, column=0)
        ttk.Entry(self.have_frag_frame, textvariable=self.frag1_path, width=30).grid(row=1, column=1)
        ttk.Button(self.have_frag_frame, text="Browse",
                   command=lambda: self.browse_folder(self.frag1_path)).grid(row=1, column=2)

        ttk.Label(self.have_frag_frame, text="Fragment 2 Folder").grid(row=1, column=3)
        ttk.Entry(self.have_frag_frame, textvariable=self.frag2_path, width=30).grid(row=1, column=4)
        ttk.Button(self.have_frag_frame, text="Browse",
                   command=lambda: self.browse_folder(self.frag2_path)).grid(row=1, column=5)

        self.e1 = tk.DoubleVar(value=-155.777)
        self.e2 = tk.DoubleVar(value=-191.667)

        energy_frame = ttk.Frame(self.have_frag_frame)
        energy_frame.grid(row=2, column=0, columnspan=6, pady=10, sticky="w")

        ttk.Label(energy_frame, text="Fragment_1 Energy").pack(side=tk.LEFT, padx=5)
        ttk.Entry(energy_frame, textvariable=self.e1, width=15).pack(side=tk.LEFT)

        ttk.Label(energy_frame, text="Fragment_2 Energy").pack(side=tk.LEFT, padx=10)
        ttk.Entry(energy_frame, textvariable=self.e2, width=15).pack(side=tk.LEFT)

        self.mode = tk.StringVar(value="b")

        mode_frame = ttk.Frame(self.have_frag_frame)
        mode_frame.grid(row=3, column=0, columnspan=6, pady=10, sticky="w")

        ttk.Label(mode_frame, text="Mode:").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(mode_frame, text="Bond", variable=self.mode, value="b").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(mode_frame, text="Angle", variable=self.mode, value="a").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(mode_frame, text="Dihedral", variable=self.mode, value="d").pack(side=tk.LEFT, padx=10)

        geo_frame = ttk.Frame(self.have_frag_frame)
        geo_frame.grid(row=4, column=0, columnspan=6, pady=10, sticky="w")

        self.bond = tk.StringVar(value="3,10")
        self.angle = tk.StringVar(value="1,2,3")
        self.dihedral = tk.StringVar(value="1,2,3,4")

        ttk.Label(geo_frame, text="Bond").pack(side=tk.LEFT)
        ttk.Entry(geo_frame, textvariable=self.bond, width=10).pack(side=tk.LEFT, padx=5)

        ttk.Label(geo_frame, text="Angle").pack(side=tk.LEFT, padx=10)
        ttk.Entry(geo_frame, textvariable=self.angle, width=10).pack(side=tk.LEFT, padx=5)

        ttk.Label(geo_frame, text="Dihedral").pack(side=tk.LEFT, padx=10)
        ttk.Entry(geo_frame, textvariable=self.dihedral, width=12).pack(side=tk.LEFT, padx=5)

    # ============================================================
    def update_ui(self):
        for widget in self.dynamic_frame.winfo_children():
            widget.pack_forget()

        if self.workflow.get() == "no_frag":
            self.no_frag_frame.pack(fill=tk.X, pady=5)
            self.input_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        else:
            self.have_frag_frame.pack(fill=tk.X, pady=5)
            self.input_frame.pack_forget()

    # ============================================================
    def browse(self):
        path = filedialog.askopenfilename(filetypes=[("XYZ files", "*.xyz")])
        if path:
            self.full_path = path
            self.file_var.set(path)

    def browse_folder(self, var):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    # ============================================================
    def run(self):
        threading.Thread(target=self.backend).start()

    # ============================================================
    def backend(self):
        try:
            self.status.set("Running... ⏳")

            if self.workflow.get() == "no_frag":
                template = self.input_text.get("1.0", tk.END)

                inp_file = self.full_path
                output_dir = os.path.dirname(self.full_path)

                frag1_list = list(map(int, self.frag_list.get().split(",")))
                frag1_charge = self.f1_charge.get()
                frag1_spin = self.f1_spin.get()
                frag2_charge = self.f2_charge.get()
                frag2_spin = self.f2_spin.get()
                rmsd_value = self.rmsd.get()

                from modules.codes_dia.xyz_extractor import read_multi_xyz
                all_xyz_comp, all_ener_comp =  read_multi_xyz(inp_file, rmsd_value)

                from modules.codes_dia.orca_input_generator_for_xyz import write_xyz, input_creator

                fragment1_folder = os.path.join(output_dir, "fragment1")
                fragment2_folder = os.path.join(output_dir, "fragment2")

                os.makedirs(fragment1_folder, exist_ok=True)
                os.makedirs(fragment2_folder, exist_ok=True)

                # Write xyz (simplified)
                for i, coord in enumerate(all_xyz_comp):
                    write_xyz(coord, f"{fragment1_folder}/frag1_{i}.xyz")
                    write_xyz(coord, f"{fragment2_folder}/frag2_{i}.xyz")

                # Create input using template
                input_creator(fragment1_folder, frag1_charge, frag1_spin, template)
                input_creator(fragment2_folder, frag2_charge, frag2_spin, template)

            elif self.workflow.get() == "have_frag":

                frag1_folder = self.frag1_path.get()
                frag2_folder = self.frag2_path.get()
                comp_file = self.full_path
                rmsd = self.rmsd_have.get()
                frag1_opt_energy = float(self.e1.get())
                frag2_opt_energy = float(self.e2.get())

                mode = self.mode.get()
                bond = list(map(int, self.bond.get().split(",")))
                angle = list(map(int, self.angle.get().split(",")))
                dihedral = list(map(int, self.dihedral.get().split(",")))

                # Copy out files 

                from modules.codes_dia.orca_input_generator_for_xyz import copy_out_files

                final_out_folder = os.path.join(os.path.dirname(frag1_folder), "final_out")
                os.makedirs(final_out_folder, exist_ok=True)

                copy_out_files(frag1_folder,final_out_folder)
                copy_out_files(frag2_folder,final_out_folder)

                from modules.codes_dia.xyz_extractor import read_multi_xyz
                all_xyz_comp, all_ener_comp =  read_multi_xyz(comp_file, rmsd)

                from modules.codes_dia.geometric_parameters import geom_along_frames

                res = (
                geom_along_frames(all_xyz_comp, bond=bond)     if mode=='b' else
                geom_along_frames(all_xyz_comp, angle=angle)   if mode=='a' else
                geom_along_frames(all_xyz_comp, dihedral=dihedral) if mode=='d' else
                None
                )

                if res is not None:
                    res = res.round(2)

                res = res["bond"].tolist()

                from modules.codes_dia.orca_input_generator_for_xyz import read_out_files

                frag1_data = read_out_files(frag1_folder)
                frag2_data = read_out_files(frag2_folder)

                #col1 = ["file_name","final_sp_value"]

                frag1_sp_data = pd.DataFrame(frag1_data, columns=["file_name","final_sp_value"])
                frag2_sp_data = pd.DataFrame(frag2_data, columns=["file_name","final_sp_value"])

                comp_sp_data = list(float(x) for x in all_ener_comp)
                frag1_sp_data = list(float(x) for x in frag1_sp_data["final_sp_value"])
                frag2_sp_data = list(float(x) for x in frag2_sp_data["final_sp_value"])
                
                distortion_1 = [(x - frag1_opt_energy)*627.509 for x in frag1_sp_data]
                distortion_2 = [(x - frag2_opt_energy)*627.509 for x in frag2_sp_data]
                total_distortion = [d1 + d2 for d1, d2 in zip(distortion_1, distortion_2)]
                interaction = [(comp_sp_data[i] - (frag1_sp_data[i] + frag2_sp_data[i])) * 627.509
                                for i in range(len(comp_sp_data))]

                from modules.codes_dia import dis_int_plot 

                selected_parameter = "rama"

                if mode =='b':
                    selected_parameter="bond_length"
                elif mode =='a':
                    selected_parameter="bond_angle"
                else:
                    selected_parameter="dihedral_angle"

                cols = [selected_parameter, "dis_1", "dis_2", "dis_total", "intr"]
                dis_int_df = pd.DataFrame(list(zip(res,distortion_1,distortion_2,total_distortion,interaction)), columns=cols)
                self.dis_int_df = dis_int_df
                #dis_int_df.to_csv("sri_rama.csv")
                dis_int_df.drop_duplicates(subset = selected_parameter)
                dis_int_plot.plot_dis_int_figure(dis_int_df,mode)

            self.status.set("Completed ✅")

        except Exception as e:
            self.status.set("Error ❌")
            messagebox.showerror("Error", str(e))

    # ============================================================
    def download_csv(self):
        try:
            if not hasattr(self, "dis_int_df"):
                messagebox.showwarning("Warning", "Run analysis first")
                return

            file = filedialog.asksaveasfilename(defaultextension=".csv")
            if file:
                self.dis_int_df.to_csv(file, index=False)
                messagebox.showinfo("Saved", "CSV saved successfully")

        except Exception as e:
            messagebox.showerror("Error", str(e))