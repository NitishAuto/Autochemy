import re

import numpy as np
import pandas as pd

# Energy in the comment line of a multi-frame xyz:
#   ORCA trajectory : "Coordinates from ORCA-job x E -123.456"
#   xtb / crest     : " energy: -12.345 gnorm: ..."  or a bare number
_ENERGY_PATTERNS = (
    re.compile(r"\bE\s*[=:]?\s*(-?\d+\.\d+)"),
    re.compile(r"energy\s*[=:]?\s*(-?\d+\.\d+)", re.IGNORECASE),
    re.compile(r"^\s*(-?\d+\.\d+)\s*$"),
)


def _comment_energy(comment):
    for pat in _ENERGY_PATTERNS:
        m = pat.search(comment)
        if m:
            return float(m.group(1))
    return None


def read_multi_xyz(path, rmsd):
    """Read a multi-frame xyz (IRC / scan trajectory).

    Frames whose RMSD to the previously *kept* frame is below ``rmsd`` are
    dropped (the first frame is always kept). Returns (frames, energies) where
    energies come from the comment lines (None when absent).
    """
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        lines = [l.rstrip("\n") for l in fh]

    all_xyz, all_energy = [], []
    i = 0
    while i < len(lines):
        head = lines[i].strip()
        if not head.isdigit():
            i += 1
            continue
        n = int(head)
        if i + 1 + n >= len(lines) + 1:
            break
        energy = _comment_energy(lines[i + 1]) if i + 1 < len(lines) else None
        block = []
        for k in range(n):
            parts = lines[i + 2 + k].split()
            block.append([parts[0], float(parts[1]), float(parts[2]), float(parts[3])])
        all_xyz.append(pd.DataFrame(block, columns=["elem", "x", "y", "z"]))
        all_energy.append(energy)
        i += 2 + n

    if not all_xyz:
        raise ValueError(f"No xyz frames found in {path}")

    kept_xyz, kept_energy = [all_xyz[0]], [all_energy[0]]
    ref = all_xyz[0][["x", "y", "z"]].to_numpy()
    for df, energy in zip(all_xyz[1:], all_energy[1:]):
        coords = df[["x", "y", "z"]].to_numpy()
        if coords.shape != ref.shape:
            raise ValueError("Frames have different numbers of atoms.")
        if np.sqrt(((coords - ref) ** 2).mean()) >= rmsd:
            kept_xyz.append(df)
            kept_energy.append(energy)
            ref = coords
    return kept_xyz, kept_energy
