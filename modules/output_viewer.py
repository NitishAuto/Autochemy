"""
ORCA Output Viewer Module
Parses and displays ORCA output file information.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os
import glob
import csv
import sys
from pathlib import Path

# Add parent directory to path to import orca_parser
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.orca_parser import ORCAParser
from modules.base_module import BaseModule


class OutputViewerModule(BaseModule):
    """Module for viewing and analyzing ORCA output files."""
    
    def __init__(self, parent_frame):
        super().__init__(parent_frame)
        self.parser = None
        self.current_file = None
        self.batch_data = []
        self.root = None  # Will be set by main app
    
    def get_name(self) -> str:
        return "Output Viewer"
    
    def get_icon(self) -> str:
        return "📊"
    
    def create_ui(self):
        """Create the output viewer interface."""
        self.main_frame = ttk.Frame(self.parent_frame, padding="10")
        
        head = ttk.Frame(self.main_frame)
        head.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(head, text=f"{self.get_icon()}  {self.get_name()}", font=("Segoe UI", 13, "bold"), foreground="#0b5cab").pack(side=tk.LEFT)
        ttk.Separator(self.main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 10))

        # Top frame for file selection
        top_frame = ttk.Frame(self.main_frame)
        top_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.file_label = ttk.Label(top_frame, text="No file loaded", font=("Segoe UI", 10))
        self.file_label.pack(side=tk.LEFT, padx=(0, 15))
        
        self.open_button = ttk.Button(top_frame, text="📂 Open File", command=self.open_file)
        self.open_button.pack(side=tk.LEFT, padx=(0, 10))
        
        self.batch_button = ttk.Button(top_frame, text="⚙️ Batch Process", command=self.open_batch_dialog)
        self.batch_button.pack(side=tk.LEFT)
        
        # Notebook for tabs
        self.notebook = ttk.Notebook(self.main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # Summary tab
        self.summary_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.summary_frame, text="Summary")
        self._create_summary_tab()
        
        # Geometry tab
        self.geometry_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.geometry_frame, text="Geometry")
        self._create_geometry_tab()
        
        # Frequencies tab
        self.frequencies_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.frequencies_frame, text="Frequencies")
        self._create_frequencies_tab()
        
        # Batch Results tab
        self.batch_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.batch_frame, text="Batch Results")
        self._create_batch_tab()
        
        # Raw Output tab
        self.raw_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.raw_frame, text="Raw Output")
        self._create_raw_tab()
        
        # Store reference to root window for clipboard operations
        if self.parent_frame:
            widget = self.parent_frame
            while widget.master:
                widget = widget.master
            self.root = widget
    
    def _create_summary_tab(self):
        """Create the summary information tab."""
        canvas = tk.Canvas(self.summary_frame)
        scrollbar = ttk.Scrollbar(self.summary_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        self.summary_content = ttk.Frame(scrollable_frame, padding="10")
        self.summary_content.pack(fill=tk.BOTH, expand=True)
        
        def configure_scroll_region(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        scrollable_frame.bind('<Configure>', configure_scroll_region)
        self.summary_content.bind('<Configure>', configure_scroll_region)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        self.summary_labels = {}
        fields = [
            ("File Path", "filepath"),
            ("Final Single Point Energy", "final_energy"),
            ("Geo Opt Converged", "geo_opt_converged"),
            ("Imaginary Modes", "imaginary_modes"),
            ("Electronic Energy", "electronic_energy"),
            ("Total Enthalpy", "total_enthalpy"),
            ("Gibbs Free Energy", "gibbs_energy"),
            ("Thermal Correction", "thermal_correction"),
            ("Time Taken (hours)", "time_hours"),
            ("S² Expectation Value", "s2_expectation"),
            ("S² Ideal Value", "s2_ideal"),
            ("S² Deviation", "s2_deviation"),
        ]
        
        for i, (label_text, key) in enumerate(fields):
            label = ttk.Label(self.summary_content, text=f"{label_text}:", font=("Segoe UI", 10, "bold"))
            label.grid(row=i, column=0, sticky="w", padx=10, pady=6)
            
            value_label = ttk.Label(self.summary_content, text="N/A", font=("Segoe UI", 10))
            value_label.grid(row=i, column=1, sticky="w", padx=10, pady=6)
            self.summary_labels[key] = value_label

            copy_btn = ttk.Button(
                self.summary_content,
                text="📋",
                width=3,
                command=lambda k=key: self.copy_single_value(k)
            )
            copy_btn.grid(row=i, column=2, sticky="w", padx=4, pady=6)
        
        copy_btn_frame = ttk.Frame(self.summary_content)
        copy_btn_frame.grid(row=len(fields), column=0, columnspan=2, pady=15)
        copy_btn = ttk.Button(copy_btn_frame, text="📋 Copy All Data (Excel Format)", command=self.copy_summary_data)
        copy_btn.pack()
    
    def _create_geometry_tab(self):
        """Create the geometry display tab."""
        geometry_frame = ttk.Frame(self.geometry_frame)
        geometry_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        tree_frame = ttk.Frame(geometry_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        self.geometry_tree = ttk.Treeview(tree_frame, columns=("Atom", "X", "Y", "Z"), show="headings", height=20)
        self.geometry_tree.heading("Atom", text="Atom")
        self.geometry_tree.heading("X", text="X (Å)")
        self.geometry_tree.heading("Y", text="Y (Å)")
        self.geometry_tree.heading("Z", text="Z (Å)")
        
        self.geometry_tree.column("Atom", width=100)
        self.geometry_tree.column("X", width=150)
        self.geometry_tree.column("Y", width=150)
        self.geometry_tree.column("Z", width=150)
        
        scrollbar_y = ttk.Scrollbar(tree_frame, orient="vertical", command=self.geometry_tree.yview)
        scrollbar_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.geometry_tree.xview)
        self.geometry_tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        
        self.geometry_tree.pack(side="left", fill="both", expand=True)
        scrollbar_y.pack(side="right", fill="y")
        scrollbar_x.pack(side="bottom", fill="x")
        
        btn_frame = ttk.Frame(geometry_frame)
        btn_frame.pack(pady=8)
        
        copy_btn = ttk.Button(btn_frame, text="📋 Copy Geometry (XYZ)", command=self.copy_geometry)
        copy_btn.pack(side=tk.LEFT, padx=5)
        
        copy_csv_btn = ttk.Button(btn_frame, text="📋 Copy as Excel (CSV)", command=self.copy_geometry_csv)
        copy_csv_btn.pack(side=tk.LEFT, padx=5)
    
    def _create_frequencies_tab(self):
        """Create the frequencies display tab."""
        freq_frame = ttk.Frame(self.frequencies_frame)
        freq_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        tree_frame = ttk.Frame(freq_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        self.freq_tree = ttk.Treeview(tree_frame, columns=("Frequency", "Intensity", "Symmetry"), show="headings", height=20)
        self.freq_tree.heading("Frequency", text="Frequency (cm⁻¹)")
        self.freq_tree.heading("Intensity", text="Intensity")
        self.freq_tree.heading("Symmetry", text="Symmetry")
        
        self.freq_tree.column("Frequency", width=200)
        self.freq_tree.column("Intensity", width=200)
        self.freq_tree.column("Symmetry", width=150)
        
        scrollbar_y = ttk.Scrollbar(tree_frame, orient="vertical", command=self.freq_tree.yview)
        scrollbar_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.freq_tree.xview)
        self.freq_tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        
        self.freq_tree.pack(side="left", fill="both", expand=True)
        scrollbar_y.pack(side="right", fill="y")
        scrollbar_x.pack(side="bottom", fill="x")
        
        copy_btn = ttk.Button(freq_frame, text="📋 Copy Frequencies (Excel CSV)", command=self.copy_frequencies_csv)
        copy_btn.pack(pady=8)
    
    def _create_batch_tab(self):
        """Create the batch processing results tab."""
        batch_main_frame = ttk.Frame(self.batch_frame)
        batch_main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        tree_frame = ttk.Frame(batch_main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        columns = [
            "file_name", "final_sp_value", "geo_opt_conv", "img_mode",
            "electronic_value", "total_enthalpy", "final_gibbs_energy",
            "thermal_correction", "time_taken(h)", "s2_expectation", "s2_ideal", "s2_deviation"
        ]
        
        self.batch_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=20)
        
        headings = {
            "file_name": "File Name",
            "final_sp_value": "Final SP Energy",
            "geo_opt_conv": "Geo Opt Conv",
            "img_mode": "Imag Modes",
            "electronic_value": "Electronic Energy",
            "total_enthalpy": "Total Enthalpy",
            "final_gibbs_energy": "Gibbs Energy",
            "thermal_correction": "Thermal Corr",
            "time_taken(h)": "Time (h)",
            "s2_expectation": "S² Expectation",
            "s2_ideal": "S² Ideal",
            "s2_deviation": "S² Deviation"
        }
        
        for col in columns:
            self.batch_tree.heading(col, text=headings.get(col, col))
            self.batch_tree.column(col, width=120)
        
        scrollbar_y = ttk.Scrollbar(tree_frame, orient="vertical", command=self.batch_tree.yview)
        scrollbar_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.batch_tree.xview)
        self.batch_tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        
        self.batch_tree.pack(side="left", fill="both", expand=True)
        scrollbar_y.pack(side="right", fill="y")
        scrollbar_x.pack(side="bottom", fill="x")
        
        btn_frame = ttk.Frame(batch_main_frame)
        btn_frame.pack(pady=8)
        
        export_csv_btn = ttk.Button(btn_frame, text="💾 Export to CSV", command=self.export_batch_csv)
        export_csv_btn.pack(side=tk.LEFT, padx=5)
        
        copy_csv_btn = ttk.Button(btn_frame, text="📋 Copy as Excel (CSV)", command=self.copy_batch_csv)
        copy_csv_btn.pack(side=tk.LEFT, padx=5)
        
        clear_btn = ttk.Button(btn_frame, text="🗑️ Clear Results", command=self.clear_batch_results)
        clear_btn.pack(side=tk.LEFT, padx=5)
    
    def _create_raw_tab(self):
        """Create the raw output display tab."""
        raw_frame = ttk.Frame(self.raw_frame)
        raw_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.raw_text = scrolledtext.ScrolledText(raw_frame, wrap=tk.WORD, font=("Courier", 9))
        self.raw_text.pack(fill=tk.BOTH, expand=True)
    
    def open_file(self):
        """Open and parse an ORCA output file."""
        filepath = filedialog.askopenfilename(
            title="Select ORCA Output File",
            filetypes=[("ORCA Output Files", "*.out"), ("All Files", "*.*")]
        )
        
        if not filepath:
            return
        
        try:
            self.parser = ORCAParser(filepath)
            self.current_file = filepath
            self.file_label.config(text=f"File: {os.path.basename(filepath)}")
            self.parse_and_display()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse file:\n{str(e)}")
    
    def parse_and_display(self):
        """Parse the file and display results."""
        if not self.parser:
            return
        
        try:
            info = self.parser.get_all_info()
            self._update_summary(info)
            self._update_geometry(info.get('geometry', []))
            self._update_frequencies(info.get('frequencies', []))
            self._update_raw_output()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to display information:\n{str(e)}")
    
    def _update_summary(self, info: dict):
        """Update summary tab with parsed information."""
        def format_value(value):
            if value is None:
                return "N/A"
            elif isinstance(value, bool):
                return "Yes" if value else "No"
            elif isinstance(value, (int, float)):
                if isinstance(value, float):
                    return f"{value:.6f}"
                return str(value)
            return str(value)
        
        for key, label in self.summary_labels.items():
            value = info.get(key, 'N/A')
            label.config(text=format_value(value))
    
    def _update_geometry(self, geometry: list):
        """Update geometry tab with coordinates."""
        for item in self.geometry_tree.get_children():
            self.geometry_tree.delete(item)
        
        for geom in geometry:
            self.geometry_tree.insert("", tk.END, values=(
                geom.atom,
                f"{geom.x:.6f}",
                f"{geom.y:.6f}",
                f"{geom.z:.6f}"
            ))
    
    def _update_frequencies(self, frequencies: list):
        """Update frequencies tab."""
        for item in self.freq_tree.get_children():
            self.freq_tree.delete(item)
        
        for freq in frequencies:
            self.freq_tree.insert("", tk.END, values=(
                f"{freq.frequency:.2f}",
                f"{freq.intensity:.2f}",
                freq.symmetry
            ))
    
    def _update_raw_output(self):
        """Update raw output tab."""
        if self.parser:
            self.raw_text.delete(1.0, tk.END)
            self.raw_text.insert(1.0, self.parser.content)
    
    def copy_geometry(self):
        """Copy geometry to clipboard in XYZ format."""
        if not self.parser:
            return
        
        geometry = self.parser.get_geometry()
        if not geometry:
            messagebox.showinfo("Info", "No geometry data available.")
            return
        
        xyz_lines = [str(len(geometry)), ""]
        for geom in geometry:
            xyz_lines.append(f"{geom.atom} {geom.x:.6f} {geom.y:.6f} {geom.z:.6f}")
        
        xyz_text = "\n".join(xyz_lines)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(xyz_text)
            self.root.update()
            messagebox.showinfo("Success", "Geometry copied to clipboard in XYZ format.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to copy to clipboard:\n{str(e)}")
    
    def copy_geometry_csv(self):
        """Copy geometry to clipboard as CSV (Excel format)."""
        if not self.parser:
            return
        
        geometry = self.parser.get_geometry()
        if not geometry:
            messagebox.showinfo("Info", "No geometry data available.")
            return
        
        csv_lines = ["Atom,X,Y,Z"]
        for geom in geometry:
            csv_lines.append(f"{geom.atom},{geom.x:.6f},{geom.y:.6f},{geom.z:.6f}")
        
        csv_text = "\n".join(csv_lines)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(csv_text)
            self.root.update()
            messagebox.showinfo("Success", "Geometry copied to clipboard!\n\nPaste into Excel - each column will be properly formatted.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to copy to clipboard:\n{str(e)}")
    
    def copy_frequencies_csv(self):
        """Copy frequencies to clipboard as CSV (Excel format)."""
        if not self.parser:
            return
        
        frequencies = self.parser.get_frequencies()
        if not frequencies:
            messagebox.showinfo("Info", "No frequency data available.")
            return
        
        csv_lines = ["Frequency (cm⁻¹),Intensity,Symmetry"]
        for freq in frequencies:
            csv_lines.append(f"{freq.frequency:.2f},{freq.intensity:.2f},{freq.symmetry}")
        
        csv_text = "\n".join(csv_lines)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(csv_text)
            self.root.update()
            messagebox.showinfo("Success", "Frequencies copied to clipboard!\n\nPaste into Excel - each column will be properly formatted.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to copy to clipboard:\n{str(e)}")
    
    def copy_summary_data(self):
        """Copy all summary data to clipboard in Excel column format."""
        if not self.parser:
            messagebox.showwarning("Warning", "No file loaded.")
            return
        
        info = self.parser.get_all_info()
        
        field_labels = {
            "filepath": "File Path",
            "final_energy": "Final Single Point Energy",
            "geo_opt_converged": "Geo Opt Converged",
            "imaginary_modes": "Imaginary Modes",
            "electronic_energy": "Electronic Energy",
            "total_enthalpy": "Total Enthalpy",
            "gibbs_energy": "Gibbs Free Energy",
            "thermal_correction": "Thermal Correction",
            "time_hours": "Time Taken (hours)",
            "s2_expectation": "S² Expectation Value",
            "s2_ideal": "S² Ideal Value",
            "s2_deviation": "S² Deviation",
        }
        
        headers = []
        values = []
        
        for key, label in self.summary_labels.items():
            if key in field_labels:
                label_text = field_labels[key]
                value = label.cget('text')
                headers.append(label_text)
                values.append(value)
        
        text = "\t".join(headers) + "\n" + "\t".join(values)
        
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            messagebox.showinfo("Success", "Data copied to clipboard!\n\nPaste into Excel - each value will be in a separate column.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to copy to clipboard:\n{str(e)}")

    def copy_single_value(self, key: str):
        """Copy a single summary value to clipboard (plain text)."""
        if not self.parser or key not in self.summary_labels:
            messagebox.showwarning("Warning", "No file loaded.")
            return
        value = self.summary_labels[key].cget("text")
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
            self.root.update()
            messagebox.showinfo("Success", f"Copied: {value}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to copy to clipboard:\n{str(e)}")
    
    def open_batch_dialog(self):
        """Open dialog for batch processing."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Batch Process ORCA Files")
        dialog.geometry("500x200")
        dialog.transient(self.root)
        dialog.grab_set()
        
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")
        
        main_frame = ttk.Frame(dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(main_frame, text="Select processing mode:", font=("Arial", 10, "bold")).pack(pady=10)
        
        btn1 = ttk.Button(
            main_frame,
            text="Option 1: All .out files in one folder",
            command=lambda: self._batch_process_single_folder(dialog)
        )
        btn1.pack(pady=5, fill=tk.X)
        
        btn2 = ttk.Button(
            main_frame,
            text="Option 2: Multiple folders (each with .out files)",
            command=lambda: self._batch_process_multiple_folders(dialog)
        )
        btn2.pack(pady=5, fill=tk.X)
        
        ttk.Button(main_frame, text="Cancel", command=dialog.destroy).pack(pady=10)
    
    def _batch_process_single_folder(self, dialog):
        """Process all .out files in a single folder."""
        dialog.destroy()
        folder = filedialog.askdirectory(title="Select folder containing .out files")
        if not folder:
            return
        self._process_batch_files([folder], single_folder=True)
    
    def _batch_process_multiple_folders(self, dialog):
        """Process .out files in multiple subfolders."""
        dialog.destroy()
        base_folder = filedialog.askdirectory(title="Select base folder containing subfolders")
        if not base_folder:
            return
        
        subfolders = [f.path for f in os.scandir(base_folder) if f.is_dir()]
        if not subfolders:
            messagebox.showwarning("Warning", "No subfolders found in the selected directory.")
            return
        
        self._process_batch_files(subfolders, single_folder=False)
    
    def _process_batch_files(self, folders, single_folder=True):
        """Process batch files and update results."""
        all_files = []
        
        if single_folder:
            pattern = os.path.join(folders[0], "*.out")
            all_files = glob.glob(pattern)
        else:
            for folder in folders:
                pattern = os.path.join(folder, "*.out")
                files = glob.glob(pattern)
                all_files.extend(files)
        
        if not all_files:
            messagebox.showwarning("Warning", "No .out files found.")
            return
        
        progress = tk.Toplevel(self.root)
        progress.title("Processing Files")
        progress.geometry("400x100")
        progress.transient(self.root)
        
        progress_label = ttk.Label(progress, text=f"Processing {len(all_files)} files...")
        progress_label.pack(pady=10)
        
        progress_bar = ttk.Progressbar(progress, length=300, mode='determinate', maximum=len(all_files))
        progress_bar.pack(pady=10)
        
        progress.update()
        
        batch_results = []
        errors = []
        
        for i, filepath in enumerate(all_files):
            try:
                parser = ORCAParser(filepath)
                info = parser.get_all_info()
                
                if not single_folder:
                    folder_name = os.path.basename(os.path.dirname(filepath))
                else:
                    folder_name = os.path.basename(filepath)
                
                row = [
                    folder_name,
                    info.get('final_energy', 0) or 0,
                    1 if info.get('geo_opt_converged', False) else 0,
                    info.get('imaginary_modes', 0) or 0,
                    info.get('electronic_energy', 0) or 0,
                    info.get('total_enthalpy', 0) or 0,
                    info.get('gibbs_energy', 0) or 0,
                    info.get('thermal_correction', 0) or 0,
                    info.get('time_hours', 0) or 0,
                    info.get('s2_expectation', 0) or 0,
                    info.get('s2_ideal', 0) or 0,
                    info.get('s2_deviation', 0) or 0,
                ]
                batch_results.append(row)
                
            except Exception as e:
                errors.append(f"{os.path.basename(filepath)}: {str(e)}")
            
            progress_bar['value'] = i + 1
            progress_label.config(text=f"Processing {i+1}/{len(all_files)} files...")
            progress.update()
        
        progress.destroy()
        
        self.batch_data = batch_results
        self._update_batch_results()
        self.notebook.select(self.batch_frame)
        
        msg = f"Processed {len(batch_results)} files successfully."
        if errors:
            msg += f"\n{len(errors)} files had errors."
        messagebox.showinfo("Batch Processing Complete", msg)
        
        if errors:
            error_msg = "\n".join(errors[:10])
            if len(errors) > 10:
                error_msg += f"\n... and {len(errors) - 10} more errors"
            messagebox.showwarning("Errors", f"Some files had errors:\n\n{error_msg}")
    
    def _update_batch_results(self):
        """Update batch results table."""
        for item in self.batch_tree.get_children():
            self.batch_tree.delete(item)
        
        columns = [
            "file_name", "final_sp_value", "geo_opt_conv", "img_mode",
            "electronic_value", "total_enthalpy", "final_gibbs_energy",
            "thermal_correction", "time_taken(h)", "s2_expectation", "s2_ideal", "s2_deviation"
        ]
        
        for row in self.batch_data:
            formatted_row = []
            for i, val in enumerate(row):
                if isinstance(val, float):
                    formatted_row.append(f"{val:.6f}")
                else:
                    formatted_row.append(str(val))
            
            self.batch_tree.insert("", tk.END, values=formatted_row)
    
    def export_batch_csv(self):
        """Export batch results to CSV file."""
        if not self.batch_data:
            messagebox.showinfo("Info", "No batch data to export.")
            return
        
        filepath = filedialog.asksaveasfilename(
            title="Save CSV File",
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        
        if not filepath:
            return
        
        try:
            columns = [
                "file_name", "final_sp_value", "geo_opt_conv", "img_mode",
                "electronic_value", "total_enthalpy", "final_gibbs_energy",
                "thermal_correction", "time_taken(h)", "s2_expectation", "s2_ideal", "s2_deviation"
            ]
            
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(columns)
                writer.writerows(self.batch_data)
            
            messagebox.showinfo("Success", f"Data exported to {filepath}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export CSV:\n{str(e)}")
    
    def copy_batch_csv(self):
        """Copy batch results to clipboard as CSV (Excel format)."""
        if not self.batch_data:
            messagebox.showinfo("Info", "No batch data to copy.")
            return
        
        columns = [
            "file_name", "final_sp_value", "geo_opt_conv", "img_mode",
            "electronic_value", "total_enthalpy", "final_gibbs_energy",
            "thermal_correction", "time_taken(h)", "s2_expectation", "s2_ideal", "s2_deviation"
        ]
        
        csv_lines = [",".join(columns)]
        for row in self.batch_data:
            csv_lines.append(",".join(str(val) for val in row))
        
        csv_text = "\n".join(csv_lines)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(csv_text)
            self.root.update()
            messagebox.showinfo("Success", "Batch data copied to clipboard!\n\nPaste into Excel - each column will be properly formatted.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to copy to clipboard:\n{str(e)}")
    
    def clear_batch_results(self):
        """Clear batch results."""
        if not self.batch_data:
            return
        
        if messagebox.askyesno("Confirm", "Clear all batch results?"):
            self.batch_data = []
            for item in self.batch_tree.get_children():
                self.batch_tree.delete(item)


