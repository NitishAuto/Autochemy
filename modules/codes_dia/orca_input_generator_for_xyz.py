import re
import sys
import os
from pathlib import Path
import pandas as pd
import shutil

def write_xyz(df, filename, natoms=None):
    """
    df: pandas DataFrame with columns ['elem', 'x', 'y', 'z']
    filename: output .xyz file
    natoms: number of atoms to write (if None, use all rows)
    """
    if natoms is None:
        natoms = len(df)

    with open(filename, "w") as f:
        # 1st line: number of atoms
        f.write(f"{natoms}\n")
        # 2nd line: empty
        f.write("\n")
        # From 3rd line: elem x y z (no commas, no header)
        for _, row in df.iloc[:natoms].iterrows():
            f.write(f"{row['elem']} {row['x']} {row['y']} {row['z']}\n")



def input_creator(input_folder, charge, spin, orca_template):
    
    # Loop through all xyz files
    for file_name in os.listdir(input_folder):
        if file_name.endswith(".xyz"):
            base_name = os.path.splitext(file_name)[0]
            inp_file_name = f"{base_name}.inp"
            inp_path = os.path.join(input_folder, inp_file_name)
    
            # ✅ FIX: pass ALL placeholders
            orca_input = orca_template.format(
                charge=charge,
                spin=spin,
                xyz_filename=file_name
            )
    
            # Write to .inp file
            with open(inp_path, 'w') as f:
                f.write(orca_input)
    
            print(f"Generated: {inp_file_name}")
    
    folder_path = input_folder
    inp_dir = os.path.join(folder_path, "all_inp")
    xyz_dir = os.path.join(folder_path, "all_xyz")

    os.makedirs(inp_dir, exist_ok=True)
    os.makedirs(xyz_dir, exist_ok=True)

    for f in os.listdir(folder_path):
        full = os.path.join(folder_path, f)

        if os.path.isfile(full):
            if f.lower().endswith(".inp"):
                shutil.move(full, os.path.join(inp_dir, f))

            elif f.lower().endswith(".xyz"):
                shutil.move(full, os.path.join(xyz_dir, f))

    print("Done! Files separated into all_inp/ and all_xyz/")


def file_energies(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    content = ''.join(lines)  # Only build once if needed for multi-line regex

    # --- Scan from end for FINAL SINGLE POINT ENERGY ---
    final_sp_value = 0
    for line in reversed(lines):
        if "FINAL SINGLE POINT ENERGY" in line:
            match = re.search(r"([-+]?\d*\.\d+)", line)
            if match:
                final_sp_value = match.group(1)
            break

    return (
        final_sp_value
    )


def read_out_files(directory):
    all_data = []
    for root, dirs, files in os.walk(directory):
        # skip descending into these folders
        dirs[:] = [d for d in dirs if d not in ("all_inp","all_xyz",".ipynb_checkpoints")]
        # also skip if the current path itself is under any of those names
        parts = Path(root).parts
        if any(x in parts for x in ("all_inp","all_xyz",".ipynb_checkpoints")):
            continue

        # ================= NEW LOGIC =================
        folder_name = os.path.basename(root)
        out_file = folder_name + ".out"   # ✅ use folder name
        file_path = Path(root) / out_file

        if not file_path.exists():
            continue

        temp = []
        temp.append(folder_name)

        temp2 = file_energies(file_path.as_posix())
        temp.append(temp2)

        all_data.append(temp)
    return all_data


def build_orca_input(template, charge, spin, frame_df, title=""):
    """ORCA input with the coordinates embedded (no external xyz file needed).

    Any coordinate block already present in the template (``* xyz ...`` /
    ``* xyzfile ...``) is replaced, and {charge}/{spin} placeholders are filled.
    """
    out, skipping = [], False
    for line in template.replace("{charge}", str(charge)).replace("{spin}", str(spin)).splitlines():
        s = line.strip().lower()
        if skipping:
            if s == "*":
                skipping = False
            continue
        if s.startswith("*") and ("xyzfile" in s or "gzmtfile" in s):
            continue
        if s.startswith("* xyz") or s.startswith("*xyz"):
            skipping = True
            continue
        if "{xyz_filename}" in line:
            continue
        out.append(line.rstrip())
    while out and not out[-1]:
        out.pop()
    body = [f"# {title}"] if title else []
    body += out + ["", f"* xyz {charge} {spin}"]
    for _, row in frame_df.iterrows():
        body.append(f"  {row['elem']:<3s} {row['x']:14.8f} {row['y']:14.8f} {row['z']:14.8f}")
    body.append("*")
    return "\n".join(body) + "\n"


def write_dia_inputs(frames, frag1_atoms, out_dir, template, charges, spins, include_complex=True):
    """Write fragment (and complex) single-point inputs for every frame.

    Layout:  out_dir/fragment1/frag1_<i>.inp|.xyz, fragment2/..., complex/complete_<i>.inp|.xyz
    Atom lists are 1-based. Returns a dict with counts.
    """
    n = len(frames[0])
    f1 = [a - 1 for a in frag1_atoms]
    f2 = [a for a in range(n) if a not in set(f1)]
    jobs = [("fragment1", "frag1", f1, charges[0], spins[0]),
            ("fragment2", "frag2", f2, charges[1], spins[1])]
    if include_complex:
        jobs.append(("complex", "complete", list(range(n)), charges[2], spins[2]))
    written = {}
    for folder, prefix, atoms, ch, sp in jobs:
        d = os.path.join(out_dir, folder)
        os.makedirs(d, exist_ok=True)
        for i, frame in enumerate(frames):
            sub = frame.iloc[atoms].reset_index(drop=True)
            write_xyz(sub, os.path.join(d, f"{prefix}_{i}.xyz"))
            with open(os.path.join(d, f"{prefix}_{i}.inp"), "w", newline="\n") as fh:
                fh.write(build_orca_input(template, ch, sp, sub, title=f"DIA {folder} frame {i}"))
        written[folder] = len(frames)
    _write_run_scripts(out_dir, [j[0] for j in jobs])
    return written


def _write_run_scripts(out_dir, folders):
    sh = ["#!/bin/bash", "# Generated by AutoChemy DIA — runs every single point in place",
          'ORCA=${ORCA:-$(command -v orca)}', 'cd "$(dirname "$0")"']
    bat = ["@echo off", "rem Generated by AutoChemy DIA", "if not defined ORCA set ORCA=orca",
           "cd /d %~dp0"]
    for f in folders:
        sh += [f"cd {f}", 'for inp in *.inp; do', '  out="${inp%.inp}.out"',
               '  grep -q "ORCA TERMINATED NORMALLY" "$out" 2>/dev/null && continue',
               '  echo "Running $inp"; "$ORCA" "$inp" > "$out"', "done", "cd .."]
        bat += [f"pushd {f}", 'for %%f in (*.inp) do (',
                '  findstr /c:"ORCA TERMINATED NORMALLY" "%%~nf.out" >nul 2>&1 || (',
                '    echo Running %%f', '    "%ORCA%" "%%f" > "%%~nf.out"', "  )", ")", "popd"]
    with open(os.path.join(out_dir, "run_all.sh"), "w", newline="\n") as fh:
        fh.write("\n".join(sh) + "\n")
    with open(os.path.join(out_dir, "run_all.bat"), "w", newline="\r\n") as fh:
        fh.write("\n".join(bat) + "\n")


_FRAME_RE = re.compile(r"(?:frag1|frag2|complete)_(\d+)", re.IGNORECASE)


def collect_frame_energies(directory, prefix):
    """Map frame index -> FINAL SINGLE POINT ENERGY for <prefix>_<i>.out files.

    Searches recursively, so both flat folders and one-folder-per-job layouts work.
    """
    energies = {}
    if not directory or not os.path.isdir(directory):
        return energies
    pat = re.compile(rf"^{re.escape(prefix)}_(\d+)\.out$", re.IGNORECASE)
    for root, dirs, files in os.walk(directory):
        for f in files:
            m = pat.match(f)
            if not m:
                continue
            e = file_energies(os.path.join(root, f))
            try:
                e = float(e)
            except (TypeError, ValueError):
                continue
            if e != 0.0:
                energies[int(m.group(1))] = e
    return energies


def copy_out_files(source_dir, destination_dir):
    """
    Copy all .out files from source_dir (including subfolders)
    to destination_dir.
    """

    os.makedirs(destination_dir, exist_ok=True)

    for root, dirs, files in os.walk(source_dir):
        for file in files:
            if file.lower().endswith(".out"):
                src_path = os.path.join(root, file)

                dst_path = os.path.join(destination_dir, file)

                shutil.copy(src_path, dst_path)