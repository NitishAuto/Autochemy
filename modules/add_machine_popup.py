import tkinter as tk
from tkinter import ttk, messagebox
import re

class AddMachinePopup(tk.Toplevel):
    def __init__(self, parent, main_app, edit_machine=None):
        super().__init__(parent)
        self.main_app = main_app
        self.edit_machine = edit_machine
        self.title("Add New Machine" if not edit_machine else f"Edit Machine: {edit_machine}")
        
        # Wider window for side-by-side layout
        w, h = 1200, 800
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = int((sw - w) / 2)
        y = int((sh - h) / 2)
        self.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        
        self.transient(parent)
        self.grab_set()
        
        # Variables
        self.machine_mode = tk.StringVar(value="Dynamic")
        self.m_name = tk.StringVar(value=edit_machine if edit_machine else "")
        self.m_type = tk.StringVar(value="Workstation")
        self.define_partitions = tk.BooleanVar(value=False)
        self.script_sub_mode = tk.StringVar(value="DynamicScript")
        self.partitions = []
        self.custom_vars = []
        self.custom_var_dict = {}
        self.custom_var_widgets = []
        
        # Workstation vars
        self.ws_os = tk.StringVar(value="Linux")
        self.ws_run_mode = tk.StringVar(value="Directly")
        self.ws_orca_path = tk.StringVar(value="")
        self.ws_mpi_path = tk.StringVar(value="")
        self.ws_scratch = tk.StringVar(value="")
        
        # HPC vars
        self.hpc_queue = tk.StringVar(value="SLURM")
        self.hpc_config = tk.StringVar(value="Modules")
        self.hpc_orca_mod = tk.StringVar(value="")
        self.hpc_mpi_mod = tk.StringVar(value="")
        self.hpc_xtb_mod = tk.StringVar(value="")
        self.hpc_orca_path = tk.StringVar(value="")
        self.hpc_mpi_path = tk.StringVar(value="")
        self.hpc_xtb_path = tk.StringVar(value="")
        self.hpc_scratch = tk.StringVar(value="")
        
        self.hpc_queue.trace_add("write", lambda *args: self._update_hpc_fields())
        
        self.build_ui()
        self._on_type_change()
        if edit_machine and edit_machine in self.main_app.saved_machines:
            self._load_machine_data(self.main_app.saved_machines[edit_machine])
            
        self._refresh_custom_vars_ui()
        self._on_machine_mode_change()
        self._on_sub_mode_change()

    def _load_machine_data(self, data):
        self.machine_mode.set(data.get("machine_mode", "Dynamic"))
        self.script_sub_mode.set(data.get("script_sub_mode", "DynamicScript"))
        self.m_type.set(data.get("type", "Workstation"))
        
        if "partitions" in data:
            self.partitions = data["partitions"]
            for p in self.partitions:
                if "custom_vars" in p:
                    for cv in p["custom_vars"]:
                        if cv not in self.custom_vars:
                            self.custom_vars.append(cv)
            if self.partitions:
                self.define_partitions.set(True)
        
        if self.m_type.get() == "Workstation":
            if "os" in data: self.ws_os.set(data["os"])
            if "run_mode" in data: self.ws_run_mode.set(data["run_mode"])
            if "orca_path" in data: self.ws_orca_path.set(data["orca_path"])
            if "mpi_path" in data: self.ws_mpi_path.set(data["mpi_path"])
            if "xtb_path" in data: self.ws_xtb_path.set(data["xtb_path"])
            if "scratch_dir" in data: self.ws_scratch.set(data["scratch_dir"])
        else:
            if "queue_system" in data: self.hpc_queue.set(data["queue_system"])
            if "config_type" in data: self.hpc_config.set(data["config_type"])
            if "orca_module" in data: self.hpc_orca_mod.set(data["orca_module"])
            if "mpi_module" in data: self.hpc_mpi_mod.set(data["mpi_module"])
            if "xtb_module" in data: self.hpc_xtb_mod.set(data["xtb_module"])
            if "orca_path" in data: self.hpc_orca_path.set(data["orca_path"])
            if "mpi_path" in data: self.hpc_mpi_path.set(data["mpi_path"])
            if "xtb_path" in data: self.hpc_xtb_path.set(data["xtb_path"])
            if "scratch_dir" in data: self.hpc_scratch.set(data["scratch_dir"])
            
        if "custom_script" in data:
            self.txt_script.delete("1.0", tk.END)
            self.txt_script.insert("1.0", data["custom_script"])

    def build_ui(self):
        main_pane = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))
        
        # ================= LEFT PANE (Options) =================
        left_frame = ttk.Frame(main_pane)
        main_pane.add(left_frame, weight=1)
        
        top_frame = ttk.Frame(left_frame)
        top_frame.pack(fill=tk.X, pady=5)
        
        mode_frame = ttk.Frame(top_frame)
        mode_frame.grid(row=0, column=0, columnspan=2, pady=5, sticky="w")
        ttk.Label(mode_frame, text="Machine Mode:").pack(side=tk.LEFT)
        ttk.Radiobutton(mode_frame, text="Make a new script", variable=self.machine_mode, value="Dynamic", command=self._on_machine_mode_change).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(mode_frame, text="Script already available", variable=self.machine_mode, value="Static", command=self._on_machine_mode_change).pack(side=tk.LEFT, padx=10)

        self.sub_mode_frame = ttk.Frame(top_frame)
        self.sub_mode_frame.grid(row=1, column=0, columnspan=2, pady=(0, 5), sticky="w")
        ttk.Label(self.sub_mode_frame, text="Script Type:").pack(side=tk.LEFT, padx=(20, 5))
        ttk.Radiobutton(self.sub_mode_frame, text="Static Script (No variables)", variable=self.script_sub_mode, value="StaticScript", command=self._on_sub_mode_change).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(self.sub_mode_frame, text="Dynamic Script (With variables)", variable=self.script_sub_mode, value="DynamicScript", command=self._on_sub_mode_change).pack(side=tk.LEFT, padx=10)
        self.sub_mode_frame.grid_remove()

        ttk.Label(top_frame, text="Machine Name:").grid(row=2, column=0, sticky="w", pady=5)
        entry = ttk.Entry(top_frame, textvariable=self.m_name, width=30)
        entry.grid(row=2, column=1, sticky="w", padx=5)
        if self.edit_machine:
            entry.config(state="readonly")
        
        self.m_type_lbl = ttk.Label(top_frame, text="Machine Type:")
        self.m_type_lbl.grid(row=3, column=0, sticky="w", pady=5)
        self.m_type_cb = ttk.Combobox(top_frame, textvariable=self.m_type, values=["Workstation", "HPC"], state="readonly")
        self.m_type_cb.grid(row=3, column=1, sticky="w", padx=5)
        
        self.m_type.trace_add("write", self._on_type_change)
        
        # Dynamic Frame for configs
        self.dyn_frame = ttk.Frame(left_frame)
        self.dyn_frame.pack(fill=tk.X, pady=10)
        
        # Partition Frame
        self.part_container = ttk.LabelFrame(left_frame, text="Queue/Partition Definitions")
        
        list_frame = ttk.Frame(self.part_container)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        columns = ("name", "cores", "nodes", "time")
        self.part_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=5)
        self.part_tree.heading("name", text="Name")
        self.part_tree.heading("cores", text="Cores")
        self.part_tree.heading("nodes", text="Nodes")
        self.part_tree.heading("time", text="Time Limit")
        
        self.part_tree.column("name", width=80)
        self.part_tree.column("cores", width=50)
        self.part_tree.column("nodes", width=50)
        self.part_tree.column("time", width=80)
        self.part_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.part_tree.yview)
        self.part_tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.part_tree.bind("<<TreeviewSelect>>", self._on_part_select)
        
        self.edit_frame = ttk.Frame(self.part_container)
        self.edit_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.p_name = tk.StringVar()
        self.p_cores = tk.StringVar(value="12")
        self.p_nodes = tk.StringVar(value="1")
        self.p_time = tk.StringVar(value="24:00:00")
        
        ttk.Label(self.edit_frame, text="Name:").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.edit_frame, textvariable=self.p_name, width=10).grid(row=0, column=1, padx=2)
        
        self.lbl_time = ttk.Label(self.edit_frame, text="Time:")
        self.lbl_time.grid(row=0, column=2, sticky="w")
        self.ent_time = ttk.Entry(self.edit_frame, textvariable=self.p_time, width=10)
        self.ent_time.grid(row=0, column=3, padx=2)
        
        self.lbl_cores = ttk.Label(self.edit_frame, text="Cores:")
        self.lbl_cores.grid(row=1, column=0, sticky="w", pady=2)
        self.ent_cores = ttk.Entry(self.edit_frame, textvariable=self.p_cores, width=10)
        self.ent_cores.grid(row=1, column=1, padx=2, pady=2)
        
        self.lbl_nodes = ttk.Label(self.edit_frame, text="Nodes:")
        self.lbl_nodes.grid(row=1, column=2, sticky="w", pady=2)
        self.ent_nodes = ttk.Entry(self.edit_frame, textvariable=self.p_nodes, width=10)
        self.ent_nodes.grid(row=1, column=3, padx=2, pady=2)
        
        self.btn_f = ttk.Frame(self.part_container)
        self.btn_f.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(self.btn_f, text="Add/Update", command=self._add_update_partition).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.btn_f, text="Remove", command=self._remove_partition).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.btn_f, text="Clear", command=self._clear_queue_form).pack(side=tk.LEFT, padx=2)
        
        # ================= RIGHT PANE (Editor) =================
        right_frame = ttk.Frame(main_pane)
        main_pane.add(right_frame, weight=2)
        
        lbl_frame = ttk.Frame(right_frame)
        lbl_frame.pack(fill=tk.X, pady=(0, 5))
        
        self.lbl_script = ttk.Label(lbl_frame, text="Custom Script Template (Override defaults):")
        self.lbl_script.pack(side=tk.LEFT)
        self.btn_detect = ttk.Button(lbl_frame, text="Detect Fields from Script", command=self._detect_fields)
        self.btn_detect.pack(side=tk.RIGHT)
        
        self.parse_feedback = tk.StringVar()
        self.parse_lbl = ttk.Label(right_frame, textvariable=self.parse_feedback, foreground="blue")
        self.parse_lbl.pack(fill=tk.X, pady=(0, 5))
        
        self.static_action_frame = ttk.Frame(right_frame)
        self.interactive_btn_frame = ttk.Frame(self.static_action_frame)
        self.interactive_btn_frame.pack(side=tk.TOP, fill=tk.X)
        
        ttk.Label(self.interactive_btn_frame, text="Select text then click:").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(self.interactive_btn_frame, text="Select Input Name", command=lambda: self._replace_selected_text("{{INPUT_NAME}}")).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.interactive_btn_frame, text="Select Cores", command=lambda: self._replace_selected_text("{{cores}}")).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.interactive_btn_frame, text="Select Nodes", command=lambda: self._replace_selected_text("{{nodes}}")).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.interactive_btn_frame, text="Select Time", command=lambda: self._replace_selected_text("{{time}}")).pack(side=tk.LEFT, padx=2)
        
        ttk.Label(self.interactive_btn_frame, text="| Custom:").pack(side=tk.LEFT, padx=(5, 2))
        self.custom_var_name = tk.StringVar()
        ttk.Entry(self.interactive_btn_frame, textvariable=self.custom_var_name, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.interactive_btn_frame, text="Replace", command=self._replace_custom_var).pack(side=tk.LEFT, padx=2)
        
        self.txt_script = tk.Text(right_frame, font=("Consolas", 10), wrap="word")
        self.txt_script.pack(fill=tk.BOTH, expand=True)
        
        self.static_save_btn = ttk.Button(right_frame, text="Save Script for Queue", command=self._save_script_for_queue)
        
        # ================= BOTTOM PANE (Buttons) =================
        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="Save Machine", command=self.save_machine).pack(side=tk.RIGHT, padx=10)

    def _toggle_partitions(self):
        if self.define_partitions.get():
            self.part_container.pack(fill=tk.BOTH, expand=True, pady=10)
            self._refresh_partition_list()
        else:
            self.part_container.pack_forget()

    def _refresh_custom_vars_ui(self):
        for widget in self.custom_var_widgets:
            widget.destroy()
        self.custom_var_widgets.clear()
        
        if not hasattr(self, 'edit_frame'): return
        
        for i, var in enumerate(self.custom_vars):
            if var not in self.custom_var_dict:
                self.custom_var_dict[var] = tk.StringVar()
            row = 2 + (i // 2)
            col = (i % 2) * 2
            
            lbl = ttk.Label(self.edit_frame, text=f"{var}:")
            lbl.grid(row=row, column=col, sticky="w", pady=2)
            ent = ttk.Entry(self.edit_frame, textvariable=self.custom_var_dict[var], width=10)
            ent.grid(row=row, column=col+1, padx=2, pady=2)
            
            self.custom_var_widgets.extend([lbl, ent])
            
        if hasattr(self, 'part_tree'):
            base_cols = ["name", "cores", "nodes", "time"]
            all_cols = base_cols + self.custom_vars
            self.part_tree["columns"] = all_cols
            
            self.part_tree.heading("name", text="Name")
            self.part_tree.heading("cores", text="Cores")
            self.part_tree.heading("nodes", text="Nodes")
            self.part_tree.heading("time", text="Time Limit")
            
            self.part_tree.column("name", width=80)
            self.part_tree.column("cores", width=50)
            self.part_tree.column("nodes", width=50)
            self.part_tree.column("time", width=80)
            
            for var in self.custom_vars:
                self.part_tree.heading(var, text=var)
                self.part_tree.column(var, width=60)
            
            self._on_sub_mode_change()

    def _refresh_partition_list(self):
        for item in self.part_tree.get_children():
            self.part_tree.delete(item)
        for p in self.partitions:
            vals = [p["name"], p["cores"], p["nodes"], p["time"]]
            for var in self.custom_vars:
                vals.append(p.get("custom_vars", {}).get(var, ""))
            self.part_tree.insert("", tk.END, iid=p["name"], values=vals)

    def _on_part_select(self, event):
        sel = self.part_tree.selection()
        if sel:
            name = sel[0]
            for p in self.partitions:
                if p["name"] == name:
                    self.p_name.set(p["name"])
                    self.p_cores.set(p["cores"])
                    self.p_nodes.set(p["nodes"])
                    self.p_time.set(p["time"])
                    cv = p.get("custom_vars", {})
                    for var in self.custom_vars:
                        if var in self.custom_var_dict:
                            self.custom_var_dict[var].set(cv.get(var, ""))
                    if self.machine_mode.get() == "Static" and self.script_sub_mode.get() == "StaticScript":
                        self.txt_script.delete("1.0", tk.END)
                        self.txt_script.insert("1.0", p.get("custom_script", ""))
                        if hasattr(self, 'parse_feedback'):
                            self.parse_feedback.set(f"Editing script for queue: {name}")
                        self.txt_script.pack(fill=tk.BOTH, expand=True)
                        if hasattr(self, 'static_save_btn'):
                            self.static_save_btn.pack(pady=5, anchor="e")
                        self.static_action_frame.pack_forget()
                    break

    def _clear_queue_form(self):
        self.p_name.set("")
        self.p_cores.set("12")
        self.p_nodes.set("1")
        self.p_time.set("24:00:00")
        for var in self.custom_vars:
            if var in self.custom_var_dict:
                self.custom_var_dict[var].set("")
        if self.machine_mode.get() == "Static" and self.script_sub_mode.get() == "StaticScript":
            self.txt_script.delete("1.0", tk.END)
            self.txt_script.pack_forget()
            if hasattr(self, 'static_save_btn'):
                self.static_save_btn.pack_forget()
            if hasattr(self, 'parse_feedback'):
                self.parse_feedback.set("Ready for new queue script.")

    def _save_script_for_queue(self):
        sel = self.part_tree.selection()
        if sel:
            name = sel[0]
            for i, p in enumerate(self.partitions):
                if p["name"] == name:
                    self.partitions[i]["custom_script"] = self.txt_script.get("1.0", tk.END).strip()
                    if hasattr(self, 'parse_feedback'):
                        self.parse_feedback.set(f"Successfully saved script for queue '{name}'!")
                    return
        if hasattr(self, 'parse_feedback'):
            self.parse_feedback.set("Error: No queue selected to save the script to.")

    def _add_update_partition(self):
        name = self.p_name.get().strip()
        if not name: return
        
        p_data = {
            "name": name,
            "cores": self.p_cores.get().strip(),
            "nodes": self.p_nodes.get().strip(),
            "time": self.p_time.get().strip(),
            "custom_vars": {var: self.custom_var_dict[var].get().strip() for var in self.custom_vars if var in self.custom_var_dict}
        }
        
        if self.machine_mode.get() == "Static" and self.script_sub_mode.get() == "StaticScript":
            p_data["custom_script"] = self.txt_script.get("1.0", tk.END).strip()
        
        for i, p in enumerate(self.partitions):
            if p["name"] == name:
                self.partitions[i] = p_data
                self._refresh_partition_list()
                return
                
        self.partitions.append(p_data)
        self._refresh_partition_list()

    def _remove_partition(self):
        name = self.p_name.get().strip()
        self.partitions = [p for p in self.partitions if p["name"] != name]
        self._refresh_partition_list()
        self.p_name.set("")

    def _on_type_change(self, *args):
        for widget in self.dyn_frame.winfo_children():
            widget.destroy()
            
        t = self.m_type.get()
        if t == "Workstation":
            self.define_partitions.set(False)
            self._toggle_partitions()
            
            ttk.Label(self.dyn_frame, text="Operating System:").grid(row=0, column=0, sticky="w", pady=2)
            ttk.Combobox(self.dyn_frame, textvariable=self.ws_os, values=["Linux", "Windows", "Mac"], state="readonly", width=12).grid(row=0, column=1, padx=5, sticky="w")
            ttk.Label(self.dyn_frame, text="Execution Style:").grid(row=0, column=2, sticky="w", padx=5)
            ttk.Combobox(self.dyn_frame, textvariable=self.ws_run_mode, values=["Directly", "Scratch"], state="readonly", width=12).grid(row=0, column=3, padx=5, sticky="w")
            
            ttk.Label(self.dyn_frame, text="ORCA Path:").grid(row=1, column=0, sticky="w", pady=2)
            ttk.Entry(self.dyn_frame, textvariable=self.ws_orca_path, width=20).grid(row=1, column=1, padx=5, sticky="w")
            ttk.Label(self.dyn_frame, text="MPI Path:").grid(row=1, column=2, sticky="w", padx=5)
            ttk.Entry(self.dyn_frame, textvariable=self.ws_mpi_path, width=20).grid(row=1, column=3, padx=5, sticky="w")
            
            ttk.Label(self.dyn_frame, text="xTB Path:").grid(row=2, column=0, sticky="w", pady=2)
            ttk.Entry(self.dyn_frame, textvariable=self.ws_xtb_path, width=20).grid(row=2, column=1, padx=5, sticky="w")
            
            ttk.Label(self.dyn_frame, text="Scratch Path:").grid(row=3, column=0, sticky="w", pady=2)
            ttk.Entry(self.dyn_frame, textvariable=self.ws_scratch, width=20).grid(row=3, column=1, padx=5, sticky="w")
            
            ttk.Button(self.dyn_frame, text="Generate Script", command=self._fill_default_template).grid(row=3, column=3, sticky="w", padx=5)
            
        elif t == "HPC":
            ttk.Label(self.dyn_frame, text="Queueing System:").grid(row=0, column=0, sticky="w", pady=2)
            ttk.Combobox(self.dyn_frame, textvariable=self.hpc_queue, values=["SLURM", "PBS", "Interactive/Direct", "Other"], state="readonly", width=15).grid(row=0, column=1, padx=5, sticky="w")
            
            self.hpc_dyn_frame = ttk.Frame(self.dyn_frame)
            self.hpc_dyn_frame.grid(row=1, column=0, columnspan=4, sticky="ew")
            self._update_hpc_fields()

        # Optionally prepopulate text area if empty
        if not self.txt_script.get("1.0", tk.END).strip() and not self.edit_machine:
            if self.machine_mode.get() != "Static":
                self._fill_default_template()

    def _update_hpc_fields(self):
        if not hasattr(self, 'hpc_dyn_frame'): return
        for widget in self.hpc_dyn_frame.winfo_children():
            widget.destroy()
            
        q = self.hpc_queue.get()
        if q != "Other":
            if self.machine_mode.get() != "Static":
                ttk.Label(self.hpc_dyn_frame, text="Load Method:").grid(row=0, column=0, sticky="w", pady=2)
                ttk.Combobox(self.hpc_dyn_frame, textvariable=self.hpc_config, values=["Modules", "Paths"], state="readonly", width=12).grid(row=0, column=1, padx=5, sticky="w")
                
                ttk.Button(self.hpc_dyn_frame, text="Generate Script", command=self._fill_default_template).grid(row=0, column=3, padx=5, sticky="w")
                
                ttk.Label(self.hpc_dyn_frame, text="ORCA Module:").grid(row=1, column=0, sticky="w", pady=2)
                ttk.Entry(self.hpc_dyn_frame, textvariable=self.hpc_orca_mod, width=20).grid(row=1, column=1, padx=5, sticky="w")
                ttk.Label(self.hpc_dyn_frame, text="MPI Module:").grid(row=1, column=2, sticky="w", padx=5)
                ttk.Entry(self.hpc_dyn_frame, textvariable=self.hpc_mpi_mod, width=20).grid(row=1, column=3, padx=5, sticky="w")
                
                ttk.Label(self.hpc_dyn_frame, text="xTB Module:").grid(row=2, column=0, sticky="w", pady=2)
                ttk.Entry(self.hpc_dyn_frame, textvariable=self.hpc_xtb_mod, width=20).grid(row=2, column=1, padx=5, sticky="w")
                
                ttk.Label(self.hpc_dyn_frame, text="ORCA Path:").grid(row=3, column=0, sticky="w", pady=2)
                ttk.Entry(self.hpc_dyn_frame, textvariable=self.hpc_orca_path, width=20).grid(row=3, column=1, padx=5, sticky="w")
                ttk.Label(self.hpc_dyn_frame, text="MPI Path:").grid(row=3, column=2, sticky="w", padx=5)
                ttk.Entry(self.hpc_dyn_frame, textvariable=self.hpc_mpi_path, width=20).grid(row=3, column=3, padx=5, sticky="w")
                
                ttk.Label(self.hpc_dyn_frame, text="xTB Path:").grid(row=4, column=0, sticky="w", pady=2)
                ttk.Entry(self.hpc_dyn_frame, textvariable=self.hpc_xtb_path, width=20).grid(row=4, column=1, padx=5, sticky="w")
                
                ttk.Label(self.hpc_dyn_frame, text="Scratch Path:").grid(row=5, column=0, sticky="w", pady=2)
                ttk.Entry(self.hpc_dyn_frame, textvariable=self.hpc_scratch, width=20).grid(row=5, column=1, padx=5, sticky="w")
                
                ttk.Checkbutton(self.hpc_dyn_frame, text="Define Queue/Partitions", variable=self.define_partitions, command=self._toggle_partitions).grid(row=6, column=0, columnspan=4, sticky="w", pady=(10, 0))
            else:
                self.define_partitions.set(True)
            self._toggle_partitions()
        else:
            self.define_partitions.set(False)
            self._toggle_partitions()

    def _fill_default_template(self):
        import modules.input_creator as ic
        t = self.m_type.get()
        if t == "Workstation":
            if self.ws_os.get() == "Windows":
                tmpl = ic.WS_WINDOWS_BAT_SCRATCH if self.ws_run_mode.get() == "Scratch" else ic.WS_WINDOWS_BAT_DIRECT
            else:
                tmpl = ic.WS_LINUX_SH_SCRATCH if self.ws_run_mode.get() == "Scratch" else ic.WS_LINUX_SH_DIRECT
        else:
            q = self.hpc_queue.get()
            if q == "PBS":
                tmpl = ic.PBS_TEMPLATE
            elif q == "SLURM":
                tmpl = ic.SLURM_TEMPLATE
            elif q == "Interactive/Direct":
                tmpl = ic.WS_LINUX_SH_DIRECT
            else:
                tmpl = ""
            
        self.txt_script.delete("1.0", tk.END)
        if tmpl:
            self.txt_script.insert("1.0", tmpl)

    def _detect_fields(self):
        script_text = self.txt_script.get("1.0", tk.END)
        
        info = {
            "input_name": None,
            "orca_setup": None,
            "openmpi_setup": None,
            "scratch_path": None
        }

        # 1. ORCA Input Name (The anchor)
        inp_match = re.search(r'([a-zA-Z0-9_.-]+)\.inp', script_text)
        if inp_match:
            info["input_name"] = inp_match.group(1)
            # Replace the literal input name with {{INPUT_NAME}} so it's dynamic
            script_text = script_text.replace(f"{info['input_name']}.inp", "{{INPUT_NAME}}.inp")
            self.txt_script.delete("1.0", tk.END)
            self.txt_script.insert("1.0", script_text)

        # 2. ORCA Module or Path
        orca_module = re.search(r'module load\s+([^\s]*orca[^\s]*)', script_text, re.IGNORECASE)
        orca_path = re.search(r'export\s+[A-Za-z0-9_]+=(.*?orca[^\s]*)', script_text, re.IGNORECASE)
        if orca_module:
            info["orca_setup"] = f"Module: {orca_module.group(1)}"
            self.hpc_config.set("Modules")
            self.hpc_orca_mod.set(orca_module.group(1))
        elif orca_path:
            info["orca_setup"] = f"Path: {orca_path.group(1)}"
            self.hpc_config.set("Paths")
            self.hpc_orca_path.set(orca_path.group(1))

        # 3. OpenMPI Module or Path
        mpi_module = re.search(r'module load\s+([^\s]*openmpi[^\s]*)', script_text, re.IGNORECASE)
        mpi_path = re.search(r'export\s+.*?(/\S*openmpi\S*)', script_text, re.IGNORECASE)
        if mpi_module:
            info["openmpi_setup"] = f"Module: {mpi_module.group(1)}"
            self.hpc_mpi_mod.set(mpi_module.group(1))
        elif mpi_path:
            info["openmpi_setup"] = f"Path: {mpi_path.group(1)}"
            self.hpc_mpi_path.set(mpi_path.group(1))

        # 4. Scratch Path
        scratch_match = re.search(r'(?:mkdir\s+-p|mktemp\s+-d)\s+([^\s]*scratch[^\s]*)', script_text, re.IGNORECASE)
        if scratch_match:
            info["scratch_path"] = scratch_match.group(1)
            self.hpc_scratch.set(scratch_match.group(1))

        # Build feedback message
        found_msg = []
        if info["input_name"]: found_msg.append(f"Input Name ('{info['input_name']}' replaced with '{{{{INPUT_NAME}}}}')")
        if info["orca_setup"]: found_msg.append(f"ORCA ({info['orca_setup']})")
        if info["openmpi_setup"]: found_msg.append(f"OpenMPI ({info['openmpi_setup']})")
        if info["scratch_path"]: found_msg.append(f"Scratch ({info['scratch_path']})")
        
        if found_msg:
            self.parse_feedback.set("Found: " + ", ".join(found_msg))
        else:
            self.parse_feedback.set("No recognized fields found in the script.")

    def _on_machine_mode_change(self, *args):
        if self.machine_mode.get() == "Static":
            self.sub_mode_frame.grid()
            self.m_type.set("HPC")
            self.m_type_lbl.grid_remove()
            self.m_type_cb.grid_remove()
            self.btn_detect.pack_forget()
            self.lbl_script.config(text="Script already available mode: Select a queue to edit its script.", foreground="blue")
            self.define_partitions.set(True)
            self._toggle_partitions()
            
            if self.script_sub_mode.get() == "DynamicScript":
                self.lbl_script.config(text="Dynamic Script Template (Global):", foreground="black")
                self.txt_script.pack(fill=tk.BOTH, expand=True)
                self.static_action_frame.pack(fill=tk.X, pady=(0, 5), before=self.txt_script)
                if hasattr(self, 'static_save_btn'):
                    self.static_save_btn.pack_forget()
                if hasattr(self, 'parse_feedback'):
                    self.parse_feedback.set("Insert variables (e.g., {{cores}}) into the script template below.")
            else:
                self.lbl_script.config(text="Script already available mode: Select a queue to edit its script.", foreground="blue")
                self.txt_script.pack_forget()
                if hasattr(self, 'static_save_btn'):
                    self.static_save_btn.pack_forget()
                self.static_action_frame.pack_forget()
                if hasattr(self, 'parse_feedback'):
                    self.parse_feedback.set("Select a queue to edit its script.")
        else:
            self.sub_mode_frame.grid_remove()
            self.m_type_lbl.grid()
            self.m_type_cb.grid()
            self.btn_detect.pack(side=tk.RIGHT)
            self.lbl_script.config(text="Make a new script (Custom Script Template):", foreground="black")
            self.parse_feedback.set("")
            self.static_action_frame.pack_forget()
            self.txt_script.pack(fill=tk.BOTH, expand=True)
            
        self._on_type_change()

    def save_machine(self):
        name = self.m_name.get().strip()
        if not name:
            messagebox.showerror("Error", "Machine name cannot be empty.")
            return
            
        script = self.txt_script.get("1.0", tk.END).strip()
        
        data = {
            "type": self.m_type.get(),
            "machine_mode": self.machine_mode.get(),
            "script_sub_mode": self.script_sub_mode.get()
        }
        
        if self.machine_mode.get() == "Dynamic" or (self.machine_mode.get() == "Static" and self.script_sub_mode.get() == "DynamicScript"):
            data["custom_script"] = script
        
        if self.define_partitions.get():
            data["partitions"] = self.partitions
        
        if data["type"] == "Workstation":
            data.update({
                "os": self.ws_os.get(),
                "run_mode": self.ws_run_mode.get(),
                "orca_path": self.ws_orca_path.get(),
                "mpi_path": self.ws_mpi_path.get(),
                "xtb_path": self.ws_xtb_path.get(),
                "scratch_dir": self.ws_scratch.get()
            })
        else:
            data.update({
                "queue_system": self.hpc_queue.get()
            })
            if self.hpc_queue.get() != "Other":
                data.update({
                    "config_type": self.hpc_config.get(),
                    "orca_module": self.hpc_orca_mod.get(),
                    "mpi_module": self.hpc_mpi_mod.get(),
                    "xtb_module": self.hpc_xtb_mod.get(),
                    "orca_path": self.hpc_orca_path.get(),
                    "mpi_path": self.hpc_mpi_path.get(),
                    "xtb_path": self.hpc_xtb_path.get(),
                    "scratch_dir": self.hpc_scratch.get()
                })
            
        self.main_app.saved_machines[name] = data
        self.main_app._save_saved_machines()
        self.main_app._update_saved_machine_list()
        self.main_app.saved_machine_var.set(name)
        self.destroy()

    def _on_sub_mode_change(self, *args):
        if self.script_sub_mode.get() == "StaticScript":
            self.lbl_time.grid_remove()
            self.ent_time.grid_remove()
            self.lbl_cores.grid_remove()
            self.ent_cores.grid_remove()
            self.lbl_nodes.grid_remove()
            self.ent_nodes.grid_remove()
            self.static_action_frame.pack_forget()
            if hasattr(self, 'part_tree'):
                self.part_tree["displaycolumns"] = ("name",)
                
            self.lbl_script.config(text="Script already available mode: Select a queue to edit its script.", foreground="blue")
            self.txt_script.pack_forget()
            if hasattr(self, 'static_save_btn'):
                self.static_save_btn.pack_forget()
            if hasattr(self, 'parse_feedback'):
                self.parse_feedback.set("Select a queue to edit its script.")
        else:
            self.lbl_time.grid()
            self.ent_time.grid()
            self.lbl_cores.grid()
            self.ent_cores.grid()
            self.lbl_nodes.grid()
            self.ent_nodes.grid()
            if hasattr(self, 'part_tree'):
                self.part_tree["displaycolumns"] = ("name", "cores", "nodes", "time")
                
            self.lbl_script.config(text="Dynamic Script Template (Global):", foreground="black")
            self.txt_script.pack(fill=tk.BOTH, expand=True)
            self.static_action_frame.pack(fill=tk.X, pady=(0, 5), before=self.txt_script)
            if hasattr(self, 'static_save_btn'):
                self.static_save_btn.pack_forget()
            if hasattr(self, 'parse_feedback'):
                self.parse_feedback.set("Insert variables (e.g., {{cores}}) into the script template below.")

    def _replace_selected_text(self, placeholder):
        try:
            sel_first = self.txt_script.index(tk.SEL_FIRST)
            sel_last = self.txt_script.index(tk.SEL_LAST)
            self.txt_script.delete(sel_first, sel_last)
            self.txt_script.insert(sel_first, placeholder)
            self.parse_feedback.set(f"Replaced selected text with '{placeholder}'.")
        except tk.TclError:
            self.parse_feedback.set("Please highlight text in the script first.")

    def _replace_custom_var(self):
        var_name = self.custom_var_name.get().strip()
        if not var_name:
            if hasattr(self, 'parse_feedback'):
                self.parse_feedback.set("Please enter a custom variable name.")
            return
        if var_name not in self.custom_vars:
            self.custom_vars.append(var_name)
            self._refresh_custom_vars_ui()
            self._refresh_partition_list()
        self._replace_selected_text(f"{{{{{var_name}}}}}")
