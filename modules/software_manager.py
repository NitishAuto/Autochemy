import os
import json
import re
import shutil
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import webbrowser

# Get the common app data directory used by AutoChemy
APP_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AutoChemy_User_Data")
if not os.path.exists(APP_DATA_DIR):
    os.makedirs(APP_DATA_DIR, exist_ok=True)

SOFTWARE_PATHS_FILE = os.path.join(APP_DATA_DIR, "software_paths.json")

def re_split_words(value):
    return [p.lower() for p in re.split(r"[^A-Za-z0-9]+", str(value or "")) if p]

class SoftwareManager:
    GENERIC_EXE_PENALTIES = (
        "unins",
        "uninstall",
        "setup",
        "install",
        "update",
        "updater",
        "helper",
        "crash",
        "report",
        "redist",
        "vcredist",
        "vc_redist",
        "visualinfo",
        "glewinfo",
    )

    @staticmethod
    def _clean_path(path):
        return os.path.expandvars(os.path.expanduser((path or "").strip().strip('"').strip("'")))

    @staticmethod
    def _name_tokens(value):
        tokens = []
        for part in re_split_words(value):
            if len(part) >= 3 and part not in tokens:
                tokens.append(part)
        return tokens

    @staticmethod
    def _executable_names_for(name):
        raw = (name or "").strip()
        base = raw.lower()
        names = []

        def _add(value):
            value = (value or "").strip()
            if value and value not in names:
                names.append(value)

        if raw:
            _add(raw)
            if os.name == "nt" and not raw.lower().endswith(".exe"):
                _add(raw + ".exe")

        if "avogadro" in base:
            for value in ("avogadro2.exe", "Avogadro2.exe", "avogadro.exe", "Avogadro.exe"):
                _add(value)

        return names

    @staticmethod
    def _candidate_score(candidate, root_path="", requested_name=""):
        lower = os.path.normcase(os.path.abspath(candidate)).lower()
        root = os.path.normcase(os.path.abspath(root_path or "")).lower()
        stem = os.path.splitext(os.path.basename(candidate))[0].lower()
        parent = os.path.basename(os.path.dirname(candidate)).lower()
        grandparent = os.path.basename(os.path.dirname(os.path.dirname(candidate))).lower()

        score = 0
        expected_names = [os.path.splitext(x)[0].lower() for x in SoftwareManager._executable_names_for(requested_name)]
        if stem in expected_names:
            score += 200
        for token in SoftwareManager._name_tokens(requested_name):
            if token in stem:
                score += 80
            elif token in parent or token in grandparent:
                score += 18

        for token in SoftwareManager._name_tokens(os.path.basename(root_path or "")):
            if token in stem:
                score += 65
            elif token in parent or token in grandparent:
                score += 12

        for path_part in lower.replace("\\", "/").split("/"):
            for token in SoftwareManager._name_tokens(path_part):
                if token == stem:
                    score += 45
                elif token in stem:
                    score += 20

        if "\\bin\\" in lower or "/bin/" in lower:
            score += 15
        if os.path.dirname(candidate).lower() == (root or "").lower():
            score += 25
        if os.path.dirname(os.path.dirname(candidate)).lower() == (root or "").lower():
            score += 12

        if stem in ("app", "main", "viewer"):
            score += 4
        if any(bad in stem for bad in SoftwareManager.GENERIC_EXE_PENALTIES):
            score -= 120

        return score

    @staticmethod
    def _find_executable_under_folder(path, name=""):
        candidates = []
        try:
            for cur, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d.lower() not in {"__pycache__", ".git", "node_modules"}]
                for fn in files:
                    if fn.lower().endswith(".exe"):
                        candidates.append(os.path.join(cur, fn))
        except (OSError, PermissionError):
            pass

        if not candidates:
            return None

        candidates.sort(key=lambda p: (SoftwareManager._candidate_score(p, path, name), -len(p)), reverse=True)
        return candidates[0]

    @staticmethod
    def resolve_executable_path(path, name=""):
        """Return an executable file even when the saved path is an install/bin folder."""
        path = SoftwareManager._clean_path(path)
        if not path:
            return None
        if os.path.isfile(path):
            return path

        if os.path.isdir(path):
            candidates = []
            for exe_name in SoftwareManager._executable_names_for(name):
                candidates.append(os.path.join(path, exe_name))
                candidates.append(os.path.join(path, "bin", exe_name))

            for candidate in candidates:
                if os.path.isfile(candidate):
                    return candidate

            recursive_match = SoftwareManager._find_executable_under_folder(path, name)
            if recursive_match:
                return recursive_match

        return None

    @staticmethod
    def resolve_launchable_path(path, name=""):
        resolved = SoftwareManager.resolve_executable_path(path, name)
        if resolved:
            return resolved
        path = SoftwareManager._clean_path(path)
        if os.path.isfile(path) and path.lower().endswith(".jar"):
            return path
        if os.path.isdir(path):
            try:
                for cur, dirs, files in os.walk(path):
                    dirs[:] = [d for d in dirs if d.lower() not in {"__pycache__", ".git", "node_modules"}]
                    for fn in files:
                        if fn.lower() == "jmol.jar":
                            return os.path.join(cur, fn)
            except (OSError, PermissionError):
                pass
        return None

    @staticmethod
    def load_software():
        if not os.path.exists(SOFTWARE_PATHS_FILE):
            return []
        try:
            with open(SOFTWARE_PATHS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    @staticmethod
    def save_software(software_list):
        try:
            with open(SOFTWARE_PATHS_FILE, "w", encoding="utf-8") as f:
                json.dump(software_list, f, indent=4)
        except Exception as e:
            print(f"Failed to save software paths: {e}")

    @staticmethod
    def get_software_by_type(soft_type):
        all_soft = SoftwareManager.load_software()
        wanted = (soft_type or "").strip().lower()
        return [s for s in all_soft if (s.get("type") or "").strip().lower() == wanted]

    @staticmethod
    def get_software_path(name):
        all_soft = SoftwareManager.load_software()
        wanted = (name or "").strip().lower()
        for s in all_soft:
            if (s.get("name") or "").strip().lower() == wanted:
                return SoftwareManager.resolve_launchable_path(s.get("path"), s.get("name"))
        return None

    @staticmethod
    def auto_detect_path(name):
        # A simple auto-detect looking in PATH and common Windows folders
        exe_names = SoftwareManager._executable_names_for(name)
        if not exe_names:
            return None

        # Check PATH
        for exe_name in exe_names:
            path_found = shutil.which(exe_name)
            if path_found:
                return path_found

        # Common directories
        search_dirs = [
            os.environ.get("ProgramFiles", "C:\\Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
            "C:\\"
        ]

        # Name hints mapping
        hints = {
            "avogadro": ["Avogadro2", "Avogadro"],
            "chemcraft": ["Chemcraft", "chemistry"],
            "gaussview": ["G16W", "G09W", "GaussView"],
            "pymol": ["PyMOL"],
            "vmd": ["VMD"],
            "chimera": ["Chimera", "ChimeraX"],
            "jmol": ["jmol"]
        }

        possible_folders = []
        for key, folder_hints in hints.items():
            if key in name.lower():
                possible_folders.extend(folder_hints)

        for base_dir in search_dirs:
            if not os.path.exists(base_dir):
                continue
            
            # Check direct hits in possible folders
            for folder in possible_folders:
                for exe_name in exe_names:
                    for subdir in ("", "bin"):
                        test_path = os.path.join(base_dir, folder, subdir, exe_name)
                        if os.path.isfile(test_path):
                            return test_path
                    
                # Check root of drive for C:\Chemcraft etc.
                for exe_name in exe_names:
                    for subdir in ("", "bin"):
                        test_root_path = os.path.join("C:\\", folder, subdir, exe_name)
                        if os.path.isfile(test_root_path):
                            return test_root_path

        return None


class SoftwarePathDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Manage Software Paths")
        self.geometry("600x400")
        self.minsize(500, 300)
        
        self.software_list = SoftwareManager.load_software()
        
        self._create_ui()
        self.transient(parent)
        self.grab_set()

    def _create_ui(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Configured Software:", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 5))

        # Treeview
        columns = ("Name", "Type", "Path")
        self.tree = ttk.Treeview(main_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("Name", text="Name")
        self.tree.heading("Type", text="Type")
        self.tree.heading("Path", text="Path")
        self.tree.column("Name", width=120)
        self.tree.column("Type", width=100)
        self.tree.column("Path", width=300)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=5)

        self._populate_tree()

        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(btn_frame, text="Add Software", command=self._add_software).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Edit Selected", command=self._edit_software).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Remove Selected", command=self._remove_software).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=2)

    def _populate_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for idx, soft in enumerate(self.software_list):
            self.tree.insert("", "end", iid=str(idx), values=(soft.get("name", ""), soft.get("type", ""), soft.get("path", "")))

    def _add_software(self):
        AddEditSoftwareDialog(self, title="Add Software", on_save=self._on_save_add)

    def _on_save_add(self, data):
        self.software_list.append(data)
        SoftwareManager.save_software(self.software_list)
        self._populate_tree()

    def _edit_software(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Select", "Please select a software to edit.")
            return
        idx = int(selected[0])
        AddEditSoftwareDialog(self, title="Edit Software", initial_data=self.software_list[idx], on_save=lambda data: self._on_save_edit(idx, data))

    def _on_save_edit(self, idx, data):
        self.software_list[idx] = data
        SoftwareManager.save_software(self.software_list)
        self._populate_tree()

    def _remove_software(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Select", "Please select a software to remove.")
            return
        if messagebox.askyesno("Confirm", "Are you sure you want to remove the selected software?"):
            idx = int(selected[0])
            del self.software_list[idx]
            SoftwareManager.save_software(self.software_list)
            self._populate_tree()

class AddEditSoftwareDialog(tk.Toplevel):
    def __init__(self, parent, title, on_save, initial_data=None):
        super().__init__(parent)
        self.title(title)
        self.geometry("500x250")
        self.resizable(False, False)
        self.on_save = on_save
        
        self.name_var = tk.StringVar(value=initial_data.get("name", "") if initial_data else "")
        self.path_var = tk.StringVar(value=initial_data.get("path", "") if initial_data else "")
        self.type_var = tk.StringVar(value=initial_data.get("type", "Visualization") if initial_data else "Visualization")

        self._create_ui()
        self.transient(parent)
        self.grab_set()

    def _create_ui(self):
        frame = ttk.Frame(self, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Name:").grid(row=0, column=0, sticky="e", pady=5, padx=5)
        ttk.Entry(frame, textvariable=self.name_var, width=40).grid(row=0, column=1, columnspan=2, sticky="w", pady=5)

        ttk.Label(frame, text="Type:").grid(row=1, column=0, sticky="e", pady=5, padx=5)
        cb = ttk.Combobox(frame, textvariable=self.type_var, values=["Visualization", "Calculation", "Other"], state="readonly", width=15)
        cb.grid(row=1, column=1, sticky="w", pady=5)

        ttk.Label(frame, text="Path:").grid(row=2, column=0, sticky="e", pady=5, padx=5)
        ttk.Entry(frame, textvariable=self.path_var, width=40).grid(row=2, column=1, sticky="w", pady=5)
        ttk.Button(frame, text="Browse", command=self._browse).grid(row=2, column=2, padx=5, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=3, pady=25)

        ttk.Button(btn_frame, text="Auto-Detect", command=self._auto_detect).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Save", command=self._save).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=5)

    def _browse(self):
        path = filedialog.askopenfilename(title="Select Executable", filetypes=[("Executable / JAR Files", "*.exe *.bat *.cmd *.sh *.jar"), ("All Files", "*.*")])
        if path:
            self.path_var.set(path)

    def _auto_detect(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Warning", "Please enter a Name first to auto-detect.", parent=self)
            return
        
        detected_path = SoftwareManager.auto_detect_path(name)
        if detected_path:
            self.path_var.set(detected_path)
            messagebox.showinfo("Success", f"Found {name} at:\n{detected_path}", parent=self)
        else:
            messagebox.showwarning("Not Found", f"Could not auto-detect {name}. Please browse manually.", parent=self)

    def _save(self):
        name = self.name_var.get().strip()
        path = self.path_var.get().strip()
        stype = self.type_var.get().strip()

        if not name or not path:
            messagebox.showerror("Error", "Name and Path are required.", parent=self)
            return

        if not SoftwareManager.resolve_launchable_path(path, name):
            if not messagebox.askyesno("Warning", "The specified path does not exist or is inaccessible. Save anyway?", parent=self):
                return

        self.on_save({"name": name, "path": path, "type": stype})
        self.destroy()

def show_software_not_found_dialog(software_name, parent=None, callback_on_add_path=None):
    top = tk.Toplevel(parent)
    top.title(f"{software_name} Not Found")
    top.geometry("500x350")
    top.grab_set()

    f = ttk.Frame(top, padding=20)
    f.pack(fill=tk.BOTH, expand=True)

    ttk.Label(f, text=f"Could not locate {software_name}!", font=("Segoe UI", 12, "bold"), foreground="#d9534f").pack(pady=(0, 10))
    
    msg = (f"The executable for {software_name} was not found in your system PATH or configured directories.\n\n"
           "You can either:\n"
           "1. Add its path manually via the 'Software Paths' window.\n"
           "2. Add it to your system Environment Variables (PATH).\n"
           "3. Install it if you haven't already.")
           
    ttk.Label(f, text=msg, wraplength=450, justify=tk.LEFT).pack(fill=tk.X, pady=(0, 20))
    
    btn_frame1 = ttk.Frame(f)
    btn_frame1.pack(fill=tk.X, pady=(0, 10))
    
    def _open_google():
        query = f"how to install {software_name} and add to path environment variable"
        webbrowser.open(f"https://www.google.com/search?q={query.replace(' ', '+')}")
        
    def _open_chatgpt():
        prompt = f"How do I install {software_name} on my operating system and add it to my PATH environment variables so that command line tools can find it?"
        webbrowser.open("https://chatgpt.com/")
        top.clipboard_clear()
        top.clipboard_append(prompt)
        messagebox.showinfo("Copied Prompt", "The following prompt has been copied to your clipboard. Paste it into ChatGPT:\n\n" + prompt, parent=top)
        
    def _open_paths():
        top.destroy()
        SoftwarePathDialog(parent)
        if callback_on_add_path:
            callback_on_add_path()

    ttk.Button(btn_frame1, text="Search Google", command=_open_google).pack(side=tk.LEFT, expand=True, padx=5)
    ttk.Button(btn_frame1, text="Ask ChatGPT (Copies Prompt)", command=_open_chatgpt).pack(side=tk.LEFT, expand=True, padx=5)
    
    btn_frame2 = ttk.Frame(f)
    btn_frame2.pack(fill=tk.X, pady=(10, 0))
    
    ttk.Button(btn_frame2, text="Provide Path manually", command=_open_paths).pack(side=tk.LEFT, expand=True, padx=5)
    ttk.Button(btn_frame2, text="Close", command=top.destroy).pack(side=tk.RIGHT, expand=True, padx=5)
