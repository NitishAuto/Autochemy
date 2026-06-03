"""
AutoChemy prerequisite checker.

Run this file directly:
    python pre_requisite.py
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox


APP_TITLE = "AutoChemy Prerequisite Checker"
MIN_PYTHON = (3, 10)

# package_name -> import_name
PYTHON_REQUIREMENTS = {
    "pandas": "pandas",
    "numpy": "numpy",
    "matplotlib": "matplotlib",
    "pillow": "PIL",
}

# Optional executables used by full workflows.
EXTERNAL_TOOLS = [
    "orca",
    "xtb",
    "crest",
    "avogadro2",
    "chemcraft",
    "gaussview",
    "jmol",
]


class PrerequisiteApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("820x560")
        self.root.minsize(760, 500)

        self.missing_python_packages: list[str] = []
        self.project_root = os.path.dirname(os.path.abspath(__file__))
        self._build_ui()
        self._write_greeting()
        self.run_checks()

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        heading = ttk.Label(
            main,
            text="Welcome to AutoChemy Prerequisite Checker",
            font=("Segoe UI", 12, "bold"),
        )
        heading.pack(anchor="w", pady=(0, 8))

        actions = ttk.Frame(main)
        actions.pack(fill=tk.X, pady=(0, 8))

        self.btn_check = ttk.Button(actions, text="Check My System", command=self.run_checks)
        self.btn_check.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_install = ttk.Button(
            actions,
            text="Install Missing Python Libraries",
            command=self.install_missing_python_packages,
        )
        self.btn_install.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(main, textvariable=self.status_var).pack(anchor="w", pady=(0, 8))

        self.output = tk.Text(main, wrap="word", height=24)
        self.output.pack(fill=tk.BOTH, expand=True)

        yscroll = ttk.Scrollbar(self.output, orient=tk.VERTICAL, command=self.output.yview)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.output.configure(yscrollcommand=yscroll.set)

    def _append(self, line: str = "") -> None:
        self.output.insert(tk.END, line + "\n")
        self.output.see(tk.END)
        self.root.update_idletasks()

    def _clear_output(self) -> None:
        self.output.delete("1.0", tk.END)

    def _write_greeting(self) -> None:
        self._clear_output()
        self._append("Hello. This tool checks your system for AutoChemy prerequisites.")
        self._append("It verifies Python, required libraries, and optional chemistry tools.")
        self._append("")

    def run_checks(self) -> None:
        self._write_greeting()
        self.status_var.set("Checking prerequisites...")
        self.missing_python_packages = []

        self._append("=== Python Check ===")
        py_ok = self._check_python_version()

        self._append("")
        self._append("=== Python Libraries Check ===")
        self._check_python_libraries()

        self._append("")
        self._append("=== External Tools Check (Optional/Workflow-specific) ===")
        self._check_external_tools()

        self._append("")
        self._append("=== Summary ===")
        if not py_ok:
            self._append(f"- Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required.")
        else:
            self._append("- Python version is OK.")

        if self.missing_python_packages:
            self._append(
                "- Missing Python libraries: " + ", ".join(self.missing_python_packages)
            )
            self.btn_install.configure(state=tk.NORMAL)
        else:
            self._append("- All required Python libraries are installed.")
            self.btn_install.configure(state=tk.DISABLED)

        self._append("- External tools are optional but needed for full ORCA/xTB/CREST workflows.")
        self.status_var.set("Check complete.")

    def _check_python_version(self) -> bool:
        current = sys.version_info[:3]
        self._append(f"Detected Python: {current[0]}.{current[1]}.{current[2]}")
        if current >= MIN_PYTHON:
            self._append("Status: OK")
            return True
        self._append(
            f"Status: Not OK (need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})"
        )
        return False

    def _check_python_libraries(self) -> None:
        for package_name, import_name in PYTHON_REQUIREMENTS.items():
            if importlib.util.find_spec(import_name) is not None:
                self._append(f"[OK] {package_name}")
            else:
                self._append(f"[MISSING] {package_name}")
                self.missing_python_packages.append(package_name)

    def _check_external_tools(self) -> None:
        for tool in EXTERNAL_TOOLS:
            path = self._find_tool_path(tool)
            if path:
                self._append(f"[FOUND] {tool} -> {path}")
            else:
                self._append(f"[NOT FOUND] {tool}")

    def _find_tool_path(self, tool: str) -> str | None:
        # 1) PATH lookup first
        from_path = shutil.which(tool)
        if from_path:
            return from_path

        # 2) Project-bundled fallback for xTB/CREST
        if tool in ("xtb", "crest"):
            bundled = self._find_in_external_modules(tool)
            if bundled:
                return bundled

        # 3) Deep drive scan for Chemcraft by user request
        if tool == "chemcraft":
            return self._find_chemcraft_all_drives()

        return None

    def _find_in_external_modules(self, tool: str) -> str | None:
        ext_root = os.path.join(self.project_root, "external_modules")
        if not os.path.isdir(ext_root):
            return None

        candidates = []
        # Include common names used in distributions.
        if tool == "xtb":
            names = {"xtb.exe", "xtb"}
        elif tool == "crest":
            names = {"crest.exe", "crest"}
        else:
            names = {f"{tool}.exe", tool}

        try:
            for cur, dirs, files in os.walk(ext_root):
                dirs[:] = [d for d in dirs if d.lower() not in {"__pycache__", ".git", "node_modules"}]
                for fn in files:
                    if fn.lower() in names:
                        candidates.append(os.path.join(cur, fn))
        except (OSError, PermissionError):
            return None

        if not candidates:
            return None

        # Prefer executables inside a bin-like folder.
        candidates.sort(key=lambda p: ("\\bin\\" in p.lower() or "/bin/" in p.lower(), len(p)), reverse=True)
        return candidates[0]

    def _find_chemcraft_all_drives(self) -> str | None:
        self._append("  Searching all drives for Chemcraft (this may take some time)...")
        common_names = {"chemcraft.exe", "chemcraft64.exe"}
        drive_letters = [f"{chr(c)}:\\" for c in range(ord("A"), ord("Z") + 1)]

        for drive in drive_letters:
            if not os.path.exists(drive):
                continue
            try:
                for cur, dirs, files in os.walk(drive):
                    dirs[:] = [d for d in dirs if d.lower() not in {"$recycle.bin", "system volume information", "__pycache__", ".git", "node_modules"}]
                    for fn in files:
                        low = fn.lower()
                        if low in common_names:
                            return os.path.join(cur, fn)
            except (OSError, PermissionError):
                continue
        return None

    def install_missing_python_packages(self) -> None:
        if not self.missing_python_packages:
            messagebox.showinfo(APP_TITLE, "No missing Python libraries found.")
            self.btn_install.configure(state=tk.DISABLED)
            return

        to_install = list(self.missing_python_packages)
        prompt = (
            "The following Python libraries are missing:\n\n"
            + ", ".join(to_install)
            + "\n\nInstall them now using pip?"
        )
        if not messagebox.askyesno(APP_TITLE, prompt):
            return

        self.status_var.set("Installing missing libraries...")
        self.btn_install.configure(state=tk.DISABLED)
        self.btn_check.configure(state=tk.DISABLED)
        self._append("")
        self._append("=== Installing Missing Python Libraries ===")

        cmd = [sys.executable, "-m", "pip", "install", *to_install]
        self._append("Running command:")
        self._append(" ".join(cmd))
        self._append("")

        try:
            process = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            output_text = process.stdout.strip()
            if output_text:
                self._append(output_text)

            if process.returncode == 0:
                self._append("")
                self._append("Installation finished successfully.")
                messagebox.showinfo(APP_TITLE, "Missing libraries installed successfully.")
            else:
                self._append("")
                self._append("Installation failed. Please review the log above.")
                messagebox.showerror(APP_TITLE, "Installation failed. Check log for details.")
        except Exception as exc:
            self._append("")
            self._append(f"Installation error: {exc}")
            messagebox.showerror(APP_TITLE, f"Installation error:\n{exc}")
        finally:
            self.btn_check.configure(state=tk.NORMAL)
            self.run_checks()


def main() -> None:
    root = tk.Tk()
    try:
        # Better default scaling for high-DPI displays on Windows.
        root.tk.call("tk", "scaling", 1.1)
    except Exception:
        pass
    app = PrerequisiteApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
