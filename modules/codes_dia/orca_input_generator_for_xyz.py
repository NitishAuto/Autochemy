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
    with open(filepath, 'r', encoding='utf-8') as f:
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