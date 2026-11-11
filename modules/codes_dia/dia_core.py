"""
Distortion–interaction / activation-strain analysis along a reaction path.

    ΔE(ζ)      = E_complex(ζ) − [E_frag1,opt + E_frag2,opt]
    ΔE_dist(ζ) = [E_frag1(ζ) − E_frag1,opt] + [E_frag2(ζ) − E_frag2,opt]
    ΔE_int(ζ)  = E_complex(ζ) − [E_frag1(ζ) + E_frag2(ζ)]

so that ΔE = ΔE_dist + ΔE_int exactly at every point.
"""
import numpy as np
import pandas as pd

from modules.codes_dia.geometric_parameters import geom_along_frames

UNITS = {"kcal/mol": 627.509474, "kJ/mol": 2625.499639, "eV": 27.211386}

COORD_LABEL = {"b": "Bond length (Å)", "a": "Angle (deg)", "d": "Dihedral angle (deg)"}
COORD_COLUMN = {"b": "bond_length", "a": "bond_angle", "d": "dihedral_angle"}


def geometric_coordinate(frames, mode, atoms):
    if mode == "b":
        res = geom_along_frames(frames, bond=atoms)
    elif mode == "a":
        res = geom_along_frames(frames, angle=atoms)
    else:
        res = geom_along_frames(frames, dihedral=atoms)
    col = [c for c in res.columns if c != "frame"][0]
    return res[col].to_numpy()


def compute_dia(frames, complex_e, frag1_e, frag2_e, e1_opt, e2_opt, mode, atoms, unit="kcal/mol"):
    """Build the DIA table.

    complex_e / frag1_e / frag2_e: dict frame_index -> energy (Eh).
    Only frames present in all three are used. Returns (df, missing_frames).
    """
    f = UNITS[unit]
    coord = geometric_coordinate(frames, mode, atoms)
    rows, missing = [], []
    for i in range(len(frames)):
        if i in complex_e and i in frag1_e and i in frag2_e:
            ec, e1, e2 = complex_e[i], frag1_e[i], frag2_e[i]
            d1 = (e1 - e1_opt) * f
            d2 = (e2 - e2_opt) * f
            rows.append({
                "frame": i,
                COORD_COLUMN[mode]: round(float(coord[i]), 4),
                "dis_1": d1,
                "dis_2": d2,
                "dis_total": d1 + d2,
                "intr": (ec - e1 - e2) * f,
                "dE": (ec - e1_opt - e2_opt) * f,
            })
        else:
            missing.append(i)
    df = pd.DataFrame(rows)
    return df, missing


def ts_summary(df):
    """Values at the highest point of ΔE (the TS on an IRC)."""
    if df.empty:
        return None
    k = int(np.argmax(df["dE"].to_numpy()))
    row = df.iloc[k]
    return {
        "frame": int(row["frame"]),
        "coord": float(row.iloc[1]),
        "dE": float(row["dE"]),
        "dis_total": float(row["dis_total"]),
        "dis_1": float(row["dis_1"]),
        "dis_2": float(row["dis_2"]),
        "intr": float(row["intr"]),
    }
