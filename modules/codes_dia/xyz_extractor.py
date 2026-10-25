import pandas as pd
import numpy as np
import shutil
import re   # NEW

def read_multi_xyz(path, rmsd):
    lines = [l.strip() for l in open(path)]
    i = 0
    all_xyz = []
    all_energy = []   # NEW

    while i < len(lines):
        if not lines[i]:
            i += 1
            continue

        try:
            n = int(lines[i])
        except:
            i += 1
            continue

        # ================= ENERGY EXTRACTION =================
        comment_line = lines[i+1]   # NEW
        match = re.search(r"E\s+([-]?\d+\.\d+)", comment_line)  # NEW
        energy = float(match.group(1)) if match else None       # NEW

        i += 2
        block = []

        for k in range(n):
            elem, x, y, z = lines[i+k].split()
            block.append([elem, float(x), float(y), float(z)])

        df = pd.DataFrame(block, columns=["elem","x","y","z"])
        all_xyz.append(df)
        all_energy.append(energy)   # NEW

        i += n


    new_all_xyz = []
    new_energy = []   # NEW

    ref = all_xyz[0][['x','y','z']].to_numpy()

    for df, energy in zip(all_xyz, all_energy):   # NEW
        coords = df[['x','y','z']].to_numpy()
        diff = coords - ref
        rmse = np.sqrt((diff**2).mean())
        ref = coords

        if rmse >= rmsd:
            new_all_xyz.append(df)
            new_energy.append(energy)   # NEW

    return new_all_xyz, new_energy   # UPDATED RETURN