#this version run the pefect window
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import json
import os

try:
    from modules.solvent_data import SOLVENT_DATA
except ImportError:
    try:
        from solvent_data import SOLVENT_DATA
    except ImportError:
        SOLVENT_DATA = {}

import sys

try:
    from modules import app_theme
except ImportError:
    app_theme = None  # type: ignore
import re
import shutil
import subprocess
import tempfile
import ctypes
import ctypes.wintypes
import threading
import time
import queue
import webbrowser
import math

from modules import xtb_support
from modules.autochemy_viewer import AutoChemyViewer
from modules.software_manager import SoftwareManager, SoftwarePathDialog, show_software_not_found_dialog
from modules.bond_reference import show_bond_chart, show_bond_query

# ==========================================
# HPC TEMPLATES (From iitj.py)
# ==========================================
PBS_TEMPLATE = """#!/bin/bash
#PBS -N {job_name}
#PBS -l nodes={nodes}:ppn={nprocs}
#PBS -q {queue}
#PBS -l walltime={time}

# 1. Setup Logging & Directory
# In PBS, jobs start in home directory by default, so we switch to submission dir
cd $PBS_O_WORKDIR
SCRIPT_DIR=$PBS_O_WORKDIR

echo "==== Execution Summary ===="
echo "Start Time: $(date)"
echo "Working Dir: $SCRIPT_DIR"
echo "Job Name: {job_name}"
echo "PBS Job ID: $PBS_JOBID"
echo "==========================="

# 2. Setup Environment
{env_setup}

export RSH_COMMAND="/usr/bin/ssh -x"

# 3. Create Unique Scratch Directory
SCRATCH_BASE="{scratch_dir}/$USER/ORCA_JOBS"
mkdir -p "$SCRATCH_BASE"
tdir=$(mktemp -d "$SCRATCH_BASE/orcajob_{job_name}_XXXXXX")
echo "Temp Job Dir: $tdir"

# 4. Copy Input Files to Scratch
cp "$SCRIPT_DIR/{job_name}.inp" "$tdir/"
cp "$SCRIPT_DIR"/*.xyz "$tdir/" 2>/dev/null || true
cp "$SCRIPT_DIR"/*.hess "$tdir/" 2>/dev/null || true
cp "$SCRIPT_DIR"/*.gbw "$tdir/" 2>/dev/null || true

# Create nodefile in SCRATCH for OpenMPI
cat $PBS_NODEFILE > "$tdir/{job_name}.nodes"

cd "$tdir"

# 5. Run ORCA
echo "Executing ORCA..."
{orca_cmd} "{job_name}.inp" > "$SCRIPT_DIR/{job_name}.out"

# 6. Cleanup and Transfer
echo "Job finished. Cleaning up..."
rm -f "$tdir/{job_name}_D00"*
mv "$tdir/"* "$SCRIPT_DIR/"
echo "$(date): Script completed successfully."
"""

SLURM_TEMPLATE = """#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --output={job_name}-%j.out
#SBATCH --nodes={nodes}
#SBATCH --ntasks-per-node={nprocs}
#SBATCH --partition={queue}
#SBATCH --time={time}

# 1. Setup Logging & Directory
SCRIPT_DIR=$SLURM_SUBMIT_DIR

echo "==== Execution Summary ===="
echo "Start Time: $(date)"
echo "Working Dir: $SCRIPT_DIR"
echo "Job Name: {job_name}"
echo "SLURM Job ID: $SLURM_JOBID"
echo "==========================="

# 2. Setup Environment
{env_setup}

export RSH_COMMAND="/usr/bin/ssh -x"

# 3. Create Unique Scratch Directory
SCRATCH_BASE="{scratch_dir}/$USER/ORCA_JOBS"
mkdir -p "$SCRATCH_BASE"
tdir=$(mktemp -d "$SCRATCH_BASE/orcajob_{job_name}_XXXXXX")
echo "Temp Job Dir: $tdir"

# 4. Copy Input Files to Scratch
cp "$SCRIPT_DIR/{job_name}.inp" "$tdir/"
cp "$SCRIPT_DIR"/*.xyz "$tdir/" 2>/dev/null || true
cp "$SCRIPT_DIR"/*.hess "$tdir/" 2>/dev/null || true
cp "$SCRIPT_DIR"/*.gbw "$tdir/" 2>/dev/null || true

cd "$tdir"

# 5. Run ORCA
echo "Executing ORCA..."
{orca_cmd} "{job_name}.inp" > "$SCRIPT_DIR/{job_name}.out"

# 6. Cleanup and Transfer
echo "Job finished. Cleaning up..."
rm -f "$tdir/{job_name}_D00"*
mv "$tdir/"* "$SCRIPT_DIR/"
echo "$(date): Script completed successfully."
"""

HPC_INTERACTIVE_TEMPLATE = """#!/bin/bash
# ==============================================================================
# Interactive/Direct ORCA Job Wrapper Script
# ==============================================================================
# Job Name: {job_name}

# 1. Setup Logging & Directory
SCRIPT_DIR=$(dirname "$(realpath "$0")")
LOG_FILE="$SCRIPT_DIR/{job_name}_master.log"
exec > >(tee -i "$LOG_FILE") 2>&1

echo "==== Execution Summary ===="
echo "Start Time: $(date)"
echo "Working Dir: $SCRIPT_DIR"
echo "Job Name: {job_name}"
echo "==========================="

# 2. Setup Environment
{env_setup}

export RSH_COMMAND="/usr/bin/ssh -x"

# 3. Create Unique Scratch Directory
SCRATCH_BASE="{scratch_dir}/$USER/ORCA_JOBS"
mkdir -p "$SCRATCH_BASE"
tdir=$(mktemp -d "$SCRATCH_BASE/orcajob_{job_name}_XXXXXX")
echo "Temp Job Dir: $tdir"

# 4. Copy Input Files to Scratch
cp "$SCRIPT_DIR/{job_name}.inp" "$tdir/"
cp "$SCRIPT_DIR"/*.xyz "$tdir/" 2>/dev/null || true
cp "$SCRIPT_DIR"/*.hess "$tdir/" 2>/dev/null || true
cp "$SCRIPT_DIR"/*.gbw "$tdir/" 2>/dev/null || true

cd "$tdir"

# 5. Run ORCA
echo "Executing ORCA..."
{orca_cmd} "{job_name}.inp" > "$SCRIPT_DIR/{job_name}.out"

# 6. Cleanup and Transfer
echo "Job finished. Cleaning up..."
rm -f "$tdir/{job_name}_D00"*
mv "$tdir/"* "$SCRIPT_DIR/"
echo "$(date): Script completed successfully."
"""

WS_LINUX_SH_DIRECT = """#!/bin/bash
# Local Workstation Execution (Directly in working directory)
# Job Name: {job_name}

# 1. Setup Logging & Directory
SCRIPT_DIR=$(dirname "$(realpath "$0")")
cd "$SCRIPT_DIR"

echo "==== Execution Summary ====" > "{job_name}_master.log"
echo "Start Time: $(date)" >> "{job_name}_master.log"
echo "Working Dir: $SCRIPT_DIR" >> "{job_name}_master.log"
echo "Job Name: {job_name}" >> "{job_name}_master.log"
echo "===========================" >> "{job_name}_master.log"

# 2. Setup Environment
{env_setup}

# 3. Run ORCA
echo "Executing ORCA..." >> "{job_name}_master.log"
{orca_cmd} "{job_name}.inp" > "{job_name}.out"

echo "$(date): Script completed successfully." >> "{job_name}_master.log"
"""

WS_LINUX_SH_SCRATCH = """#!/bin/bash
# Local Workstation Execution (via Scratch directory)
# Job Name: {job_name}

# 1. Setup Logging & Directory
SCRIPT_DIR=$(dirname "$(realpath "$0")")
LOG_FILE="$SCRIPT_DIR/{job_name}_master.log"
exec > >(tee -i "$LOG_FILE") 2>&1

echo "==== Execution Summary ===="
echo "Start Time: $(date)"
echo "Working Dir: $SCRIPT_DIR"
echo "Job Name: {job_name}"
echo "==========================="

# 2. Setup Environment
{env_setup}

export RSH_COMMAND="/usr/bin/ssh -x"

# 3. Create Unique Scratch Directory
SCRATCH_BASE="{scratch_dir}/$USER/ORCA_JOBS"
mkdir -p "$SCRATCH_BASE"
tdir=$(mktemp -d "$SCRATCH_BASE/orcajob_{job_name}_XXXXXX")
echo "Temp Job Dir: $tdir"

# 4. Copy Input Files to Scratch
cp "$SCRIPT_DIR/{job_name}.inp" "$tdir/"
cp "$SCRIPT_DIR"/*.xyz "$tdir/" 2>/dev/null || true
cp "$SCRIPT_DIR"/*.hess "$tdir/" 2>/dev/null || true
cp "$SCRIPT_DIR"/*.gbw "$tdir/" 2>/dev/null || true

cd "$tdir"

# 5. Run ORCA
echo "Executing ORCA..."
{orca_cmd} "{job_name}.inp" > "$SCRIPT_DIR/{job_name}.out"

# 6. Cleanup and Transfer
echo "Job finished. Cleaning up..."
rm -f "$tdir/{job_name}_D00"*
mv "$tdir/"* "$SCRIPT_DIR/"
echo "$(date): Script completed successfully."
"""

WS_WINDOWS_BAT_DIRECT = """@echo off
REM Local Workstation Execution (Directly in working directory)
REM Job Name: {job_name}

REM 1. Setup Directory
set SCRIPT_DIR=%~dp0
set SCRIPT_DIR=%SCRIPT_DIR:~0,-1%

echo ==== Execution Summary ==== > "{job_name}_master.log"
echo Start Time: %time% >> "{job_name}_master.log"
echo Working Dir: %SCRIPT_DIR% >> "{job_name}_master.log"
echo Job Name: {job_name} >> "{job_name}_master.log"
echo =========================== >> "{job_name}_master.log"

REM 2. Setup Environment
set PATH={mpi_path}\\bin;%PATH%
set PATH={orca_path};%PATH%

REM 3. Run ORCA
echo Executing ORCA... >> "{job_name}_master.log"
{orca_cmd_win} "{job_name}.inp" > "{job_name}.out"

echo %time%: Script completed successfully. >> "{job_name}_master.log"
"""

WS_WINDOWS_BAT_SCRATCH = """@echo off
REM Local Workstation Execution (via Scratch directory)
REM Job Name: {job_name}

REM 1. Setup Directory
set SCRIPT_DIR=%~dp0
set SCRIPT_DIR=%SCRIPT_DIR:~0,-1%

echo ==== Execution Summary ==== > "%SCRIPT_DIR%\\{job_name}_master.log"
echo Start Time: %time% >> "%SCRIPT_DIR%\\{job_name}_master.log"
echo Working Dir: %SCRIPT_DIR% >> "%SCRIPT_DIR%\\{job_name}_master.log"
echo Job Name: {job_name} >> "%SCRIPT_DIR%\\{job_name}_master.log"
echo =========================== >> "%SCRIPT_DIR%\\{job_name}_master.log"

REM 2. Setup Environment
set PATH={mpi_path}\\bin;%PATH%
set PATH={orca_path};%PATH%

REM 3. Create Unique Scratch Directory
set SCRATCH_BASE={scratch_dir}\\%USERNAME%\\ORCA_JOBS
if not exist "%SCRATCH_BASE%" mkdir "%SCRATCH_BASE%"
set tdir=%SCRATCH_BASE%\\orcajob_%RANDOM%
mkdir "%tdir%"

REM 4. Copy Input Files to Scratch
copy "%SCRIPT_DIR%\\{job_name}.inp" "%tdir%"
if exist "%SCRIPT_DIR%\\*.xyz" copy "%SCRIPT_DIR%\\*.xyz" "%tdir%"
if exist "%SCRIPT_DIR%\\*.hess" copy "%SCRIPT_DIR%\\*.hess" "%tdir%"
if exist "%SCRIPT_DIR%\\*.gbw" copy "%SCRIPT_DIR%\\*.gbw" "%tdir%"

cd /d "%tdir%"

REM 5. Run ORCA
echo Executing ORCA... >> "%SCRIPT_DIR%\\{job_name}_master.log"
{orca_cmd_win} "{job_name}.inp" > "%SCRIPT_DIR%\\{job_name}.out"

REM 6. Cleanup and Transfer
echo Job finished. Cleaning up... >> "%SCRIPT_DIR%\\{job_name}_master.log"
del /Q "{job_name}_D00*"
move /Y "%tdir%\\*" "%SCRIPT_DIR%\\"
cd /d "%SCRIPT_DIR%"

echo %time%: Script completed successfully. >> "%SCRIPT_DIR%\\{job_name}_master.log"
"""

_GAUSSIAN_DISP = {
    "D3ZERO": "empiricaldispersion=gd3",
    "D3BJ": "empiricaldispersion=gd3bj",
    "D4": "empiricaldispersion=gd4",
}

PERIODIC_TABLE = {
    'H': 1, 'HE': 2, 'LI': 3, 'BE': 4, 'B': 5, 'C': 6, 'N': 7, 'O': 8, 
    'F': 9, 'NE': 10, 'NA': 11, 'MG': 12, 'AL': 13, 'SI': 14, 'P': 15, 
    'S': 16, 'CL': 17, 'AR': 18, 'K': 19, 'CA': 20, 'SC': 21, 'TI': 22,
    'V': 23, 'CR': 24, 'MN': 25, 'FE': 26, 'CO': 27, 'NI': 28, 'CU': 29,
    'ZN': 30, 'GA': 31, 'GE': 32, 'AS': 33, 'SE': 34, 'BR': 35, 'KR': 36,
    'I': 53, 'XE': 54, 'RN': 86, 'RU': 44, 'RH': 45, 'PD': 46, 'AG': 47,
    'CD': 48, 'IN': 49, 'SN': 50, 'SB': 51, 'TE': 52, 'PT': 78, 'AU': 79,
    'HG': 80
}

ATOMIC_NUMBER_TO_SYMBOL = {int(z): sym.title() for sym, z in PERIODIC_TABLE.items()}


def _normalize_symbol_token(token: str):
    t = (token or "").strip()
    if not t:
        return None
    if t.isdigit():
        try:
            z = int(t)
        except ValueError:
            return None
        return ATOMIC_NUMBER_TO_SYMBOL.get(z)
    if not re.match(r"^[A-Za-z]{1,3}$", t):
        return None
    return t[0].upper() + t[1:].lower()


def _sanitize_coord_source_text(text: str) -> str:
    """Normalize pasted/dropped coords: BOM, NBSP, Unicode minus, Mac/Win newlines."""
    if not text:
        return ""
    t = str(text).lstrip("\ufeff\ufffe").replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\u00a0", " ").replace("\u202f", " ")
    trans = dict.fromkeys((0x2212, 0x2013, 0x2014), ord("-"))
    return t.translate(trans)


def _geom_lines_to_coord_rows(geom_text: str):
    """Return list of 'El  x  y  z' lines from pasted geometry (strip XYZ header if present)."""
    geom_text = _sanitize_coord_source_text(geom_text or "")
    lines = [ln.strip() for ln in geom_text.splitlines() if ln.strip()]
    if not lines:
        return []
    if lines[0].isdigit() and len(lines) >= 2:
        lines = lines[2:]
    rows = []
    for ln in lines:
        parts = re.split(r"\s+", ln, maxsplit=4)
        if len(parts) < 4:
            continue
        sym_raw, xs, ys, zs = parts[0], parts[1], parts[2], parts[3]
        if _float_token(xs) is None or _float_token(ys) is None or _float_token(zs) is None:
            continue
        sym = _normalize_symbol_token(sym_raw)
        if not sym:
            continue
        rows.append((sym, xs, ys, zs))
    if rows:
        return rows
    return _normalize_geometry_raw(geom_text or "")


def _float_token(tok: str) -> float | None:
    if tok is None:
        return None
    t = str(tok).strip().replace(",", "")
    if not t:
        return None
    if re.search(r"\d[Dd]", t):
        t = t.replace("D", "E").replace("d", "e")
    try:
        return float(t)
    except ValueError:
        return None


def _parse_coord_line(line: str):
    s = _sanitize_coord_source_text(line or "").strip().split("#", 1)[0].strip()
    s = re.sub(r",\s*", " ", s)
    parts = re.split(r"\s+", s)
    if len(parts) >= 5 and parts[0].isdigit() and _normalize_symbol_token(parts[1]):
        parts = parts[1:]
    if len(parts) < 4:
        return None
    sym = _normalize_symbol_token(parts[0])
    if not sym:
        return None
    xa, ya, za = _float_token(parts[1]), _float_token(parts[2]), _float_token(parts[3])
    if xa is None or ya is None or za is None:
        return None
    return (sym, f"{xa:g}", f"{ya:g}", f"{za:g}")


def _extract_rows_from_orca_xyz_block(text: str):
    lines = (text or "").splitlines()
    start = -1
    for i, ln in enumerate(lines):
        if re.match(r"^\s*\*\s*xyz\b", ln, flags=re.IGNORECASE):
            start = i + 1
            break
    if start < 0:
        return []
    rows = []
    for ln in lines[start:]:
        if re.match(r"^\s*\*\s*$", ln):
            break
        row = _parse_coord_line(ln)
        if row:
            rows.append(row)
    return rows


def _extract_rows_from_gjf_text(text: str):
    lines = (text or "").splitlines()
    i = 0
    n = len(lines)
    while i < n and lines[i].strip().startswith("%"):
        i += 1
    while i < n and not lines[i].strip().startswith("#"):
        i += 1
    if i >= n:
        return []
    while i < n and lines[i].strip():
        i += 1
    while i < n and not lines[i].strip():
        i += 1
    while i < n and lines[i].strip():
        i += 1
    while i < n and not lines[i].strip():
        i += 1
    if i >= n:
        return []
    # charge/multiplicity line
    i += 1
    rows = []
    for ln in lines[i:]:
        s = ln.strip()
        if not s or s.startswith("--link1--"):
            break
        row = _parse_coord_line(s)
        if row:
            rows.append(row)
    return rows


def _extract_rows_from_xyz_like_text(text: str):
    text = _sanitize_coord_source_text(text or "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    start = 0
    if lines[0].isdigit() and len(lines) >= 2:
        start = 2
    rows = []
    for ln in lines[start:]:
        row = _parse_coord_line(ln)
        if row:
            rows.append(row)
    return rows


def _extract_rows_from_orca_cartesian_block(text: str):
    """Parse ORCA 'CARTESIAN COORDINATES (ANGSTROM)' section from pasted .out snippets."""
    lines = (text or "").splitlines()
    rows = []
    capturing = False
    for ln in lines:
        up = ln.upper()
        if "CARTESIAN COORDINATES" in up and "ANGSTROM" in up:
            capturing = True
            continue
        if capturing:
            st = ln.strip()
            if not st:
                if rows:
                    break
                continue
            if st.startswith("--") or st.startswith("*") or st.startswith("NUMBER"):
                if rows:
                    break
                continue
            row = _parse_coord_line(ln)
            if row:
                rows.append(row)
                continue
            parts = re.split(r"\s+", st)
            if len(parts) >= 5 and parts[0].isdigit():
                row = _parse_coord_line(" ".join(parts[1:5]))
                if row:
                    rows.append(row)
    return rows


def _normalize_to_xyz_rows(text: str):
    text = _sanitize_coord_source_text(text or "")
    rows = _extract_rows_from_orca_xyz_block(text)
    if rows:
        return rows
    rows = _extract_rows_from_gjf_text(text)
    if rows:
        return rows
    rows = _extract_rows_from_orca_cartesian_block(text)
    if rows:
        return rows
    rows = _extract_rows_from_xyz_like_text(text)
    if rows:
        return rows
    return []


def _last_xyz_frame_rows(text: str):
    """If `text` has multiple XYZ frames (e.g. xtbopt trajectory), return atom rows for the last frame."""
    text = _sanitize_coord_source_text(text or "")
    lines = text.splitlines()
    blocks = []
    i = 0
    n_lines = len(lines)
    while i < n_lines:
        s = lines[i].strip()
        if s.isdigit():
            n = int(s)
            if n > 0 and i + 2 + n <= n_lines:
                atom_lines = lines[i + 2 : i + 2 + n]
                rows = []
                for ln in atom_lines:
                    r = _parse_coord_line(ln.strip())
                    if r:
                        rows.append(r)
                if len(rows) == n:
                    blocks.append(rows)
                i += 2 + n
                continue
        i += 1
    if blocks:
        return blocks[-1]
    return _extract_rows_from_xyz_like_text(text or "")


def _normalize_geometry_raw(text: str):
    """All supported formats → list of (El, xs, ys, zs) tuples for xTB / export."""
    t = _sanitize_coord_source_text(text or "")
    if not str(t).strip():
        return []
    rows = _normalize_to_xyz_rows(t)
    if rows:
        return rows
    return _last_xyz_frame_rows(t) or []


def _parse_tkdnd_file_list(data) -> list[str]:
    """Parse paths from tkinterdnd2 <<Drop>> event.data (Windows braces, macOS file://, etc.)."""
    if data is None:
        return []
    s = str(data).strip()
    if not s:
        return []
    from urllib.parse import unquote, urlparse

    def _one(p: str) -> str:
        p = (p or "").strip().strip('"').strip("'")
        if p.lower().startswith("file:"):
            path = urlparse(p).path
            p = unquote(path or "")
            if os.name == "nt" and len(p) >= 3 and p[0] == "/" and p[2] == ":":
                p = p[1:]
        return p

    if "{" in s:
        chunks = re.findall(r"\{([^}]*)\}", s)
        if chunks:
            return [_one(c) for c in chunks if c.strip()]
    return [_one(part) for part in re.split(r"\s+", s) if part.strip()]


# ==========================================
# MODULE CLASS
# ==========================================
FUNCTIONAL_DATA = {
    # GGA
    'PBE': {'family': 'GGA', 'developer': 'Perdew-Burke-Ernzerhof', 'hf': '0%', 'desc': 'Standard parameter-free GGA. Solid baseline for geometries and frequencies.'},
    'BLYP': {'family': 'GGA', 'developer': 'Becke/Lee-Yang-Parr', 'hf': '0%', 'desc': 'Classic GGA. Often paired with D3 for organic molecules.'},
    'RPBE': {'family': 'GGA', 'developer': 'Hammer-Norskov', 'hf': '0%', 'desc': 'Revised PBE for improved surface chemisorption energies.'},
    'REVPBE': {'family': 'GGA', 'developer': 'Zhang-Yang', 'hf': '0%', 'desc': 'Modified PBE for better atomization energies.'},
    'BP86': {'family': 'GGA', 'developer': 'Becke/Perdew', 'hf': '0%', 'desc': 'Excellent classical GGA for transition metal complexes and geometries.'},
    'OLYP': {'family': 'GGA', 'developer': 'Handy-Cohen/Lee-Yang-Parr', 'hf': '0%', 'desc': 'OPTX exchange. Good for spin-state energetics.'},
    'GLYP': {'family': 'GGA', 'developer': 'Various', 'hf': '0%', 'desc': 'Generic GGA formulation.'},
    'XLYP': {'family': 'GGA', 'developer': 'Xu-Goddard', 'hf': '0%', 'desc': 'Extended LYP functional.'},
    'PW91': {'family': 'GGA', 'developer': 'Perdew-Wang', 'hf': '0%', 'desc': 'Older GGA, largely superseded by PBE.'},
    'PWP': {'family': 'GGA', 'developer': 'Perdew-Wang', 'hf': '0%', 'desc': 'Variant of PW91.'},
    'mPWPW': {'family': 'GGA', 'developer': 'Adamo-Barone', 'hf': '0%', 'desc': 'Modified PW91 for non-covalent interactions.'},
    'mPWLYP': {'family': 'GGA', 'developer': 'Adamo-Barone', 'hf': '0%', 'desc': 'Modified PW exchange with LYP correlation.'},
    
    # Hybrid GGA
    'B3LYP': {'family': 'Hybrid GGA', 'developer': 'Becke/Lee-Yang-Parr', 'hf': '20%', 'desc': 'The most popular hybrid functional. Good general-purpose chemistry.'},
    'B3LYP/G': {'family': 'Hybrid GGA', 'developer': 'Becke/Lee-Yang-Parr', 'hf': '20%', 'desc': 'Gaussian version of B3LYP.'},
    'PBE0': {'family': 'Hybrid GGA', 'developer': 'Perdew-Burke-Ernzerhof', 'hf': '25%', 'desc': 'Parameter-free hybrid. Excellent for general organics and reaction barriers.'},
    'BHLYP': {'family': 'Hybrid GGA', 'developer': 'Becke/Lee-Yang-Parr', 'hf': '50%', 'desc': 'Half-and-half hybrid. Good for transition states.'},
    'B1LYP': {'family': 'Hybrid GGA', 'developer': 'Becke/Lee-Yang-Parr', 'hf': '25%', 'desc': '1-parameter hybrid of BLYP.'},
    'O3LYP': {'family': 'Hybrid GGA', 'developer': 'Handy-Cohen', 'hf': '11.6%', 'desc': 'Hybrid leveraging OPTX exchange.'},
    'X3LYP': {'family': 'Hybrid GGA', 'developer': 'Xu-Goddard', 'hf': '21.8%', 'desc': 'Extended hybrid. Slightly outperforms B3LYP for heats of formation.'},
    'B1P': {'family': 'Hybrid GGA', 'developer': 'Becke/Perdew', 'hf': '25%', 'desc': '1-parameter hybrid.'},
    'B3P': {'family': 'Hybrid GGA', 'developer': 'Becke/Perdew', 'hf': '20%', 'desc': '3-parameter hybrid variant.'},
    'B3PW': {'family': 'Hybrid GGA', 'developer': 'Becke-Perdew-Wang', 'hf': '20%', 'desc': 'Uses PW91 correlation instead of LYP.'},
    'PW1PW': {'family': 'Hybrid GGA', 'developer': 'Perdew-Wang', 'hf': '25%', 'desc': '1-parameter hybrid of PW91.'},
    'mPW1PW': {'family': 'Hybrid GGA', 'developer': 'Adamo-Barone', 'hf': '25%', 'desc': 'Modified PW91 1-parameter hybrid.'},
    'mPW1LYP': {'family': 'Hybrid GGA', 'developer': 'Adamo-Barone', 'hf': '25%', 'desc': 'Modified PW exchange, LYP correlation hybrid.'},
    'REVPBE0': {'family': 'Hybrid GGA', 'developer': 'Zhang-Yang', 'hf': '25%', 'desc': 'Hybrid constructed from revPBE.'},
    'REVPBE38': {'family': 'Hybrid GGA', 'developer': 'Perdew', 'hf': '38%', 'desc': 'Hybrid with 38% exact exchange for better barriers.'},
    'BHANDHLYP': {'family': 'Hybrid GGA', 'developer': 'Becke', 'hf': '50%', 'desc': 'Half-and-half hybrid. Often used for excited states and highly localized radicals.'},
    
    # Meta GGA
    'M06-L': {'family': 'Meta GGA', 'developer': 'Minnesota (Truhlar)', 'hf': '0%', 'desc': 'Local meta-GGA. Excellent for transition metals and medium-range correlation.'},
    'TPSS': {'family': 'Meta GGA', 'developer': 'Tao-Perdew-Staroverov-Scuseria', 'hf': '0%', 'desc': 'Standard meta-GGA. Good for atomization energies.'},
    'revTPSS': {'family': 'Meta GGA', 'developer': 'Perdew', 'hf': '0%', 'desc': 'Revised TPSS functional.'},
    'B97M-V': {'family': 'Meta GGA', 'developer': 'Mardirossian-Head-Gordon', 'hf': '0%', 'desc': 'Combinatorially optimized meta-GGA. Great for non-covalent interactions.'},
    'B97M-D3BJ': {'family': 'Meta GGA', 'developer': 'Head-Gordon', 'hf': '0%', 'desc': 'B97M specifically parameterized with D3(BJ).'},
    'B97M-D4': {'family': 'Meta GGA', 'developer': 'Head-Gordon', 'hf': '0%', 'desc': 'B97M parameterized with D4 dispersion.'},
    'SCAN': {'family': 'Meta GGA', 'developer': 'Perdew (Strongly Constrained)', 'hf': '0%', 'desc': 'Strongly Constrained and Appropriately Normed. Excellent non-empirical meta-GGA.'},
    'MN15-L': {'family': 'Meta GGA', 'developer': 'Minnesota (Truhlar)', 'hf': '0%', 'desc': 'Local meta-GGA. Designed for strong multi-reference character systems.'},
    
    # Hybrid & Meta GGA
    'M06': {'family': 'Hybrid & Meta GGA', 'developer': 'Minnesota (Truhlar)', 'hf': '27%', 'desc': 'Good for organometallics and non-covalent interactions.'},
    'M06-2X': {'family': 'Hybrid & Meta GGA', 'developer': 'Minnesota (Truhlar)', 'hf': '54%', 'desc': 'Top-tier for main-group thermochemistry, kinetics, and non-covalent bonding. DO NOT USE for transition metals.'},
    'MN15': {'family': 'Hybrid & Meta GGA', 'developer': 'Minnesota (Truhlar)', 'hf': '44%', 'desc': 'Kohn-Sham hybrid meta-GGA. Broader accuracy across main-group chemistry and transition metals.'},
    'PW6B95': {'family': 'Hybrid & Meta GGA', 'developer': 'Zhao-Truhlar', 'hf': '28%', 'desc': 'Excellent for main-group thermochemistry.'},
    'TPSSh': {'family': 'Hybrid & Meta GGA', 'developer': 'Staroverov-Perdew', 'hf': '10%', 'desc': 'Hybrid variant of TPSS. Highly recommended for transition metal complexes.'},
    'TPSS0': {'family': 'Hybrid & Meta GGA', 'developer': 'Perdew', 'hf': '25%', 'desc': '25% HF variant of TPSS.'},
    
    # Range-Separated
    'CAM-B3LYP': {'family': 'Range-Separated', 'developer': 'Handy', 'hf': '19-65%', 'desc': 'Coulomb-attenuating method. Standard choice for TD-DFT (Excited States) and charge-transfer.'},
    'wB97': {'family': 'Range-Separated', 'developer': 'Head-Gordon', 'hf': '0-100%', 'desc': 'Range-separated hybrid.'},
    'wB97X': {'family': 'Range-Separated', 'developer': 'Head-Gordon', 'hf': '16-100%', 'desc': 'Includes short-range exact exchange.'},
    'wB97X-D3': {'family': 'Range-Separated', 'developer': 'Head-Gordon', 'hf': '19-100%', 'desc': 'One of the MOST accurate all-around functionals for energetics.'},
    'wB97X-D4': {'family': 'Range-Separated', 'developer': 'Head-Gordon / Grimme', 'hf': '19-100%', 'desc': 'wB97X coupled with D4 dispersion.'},
    'wB97X-V': {'family': 'Range-Separated', 'developer': 'Mardirossian-Head-Gordon', 'hf': '16-100%', 'desc': 'wB97X with VV10 non-local correlation.'},
    'wB97X-D3BJ': {'family': 'Range-Separated', 'developer': 'Head-Gordon', 'hf': '19-100%', 'desc': 'Standard wB97X-D3 with Becke-Johnson damping.'},
    'wB97M-D4': {'family': 'Range-Separated', 'developer': 'Head-Gordon', 'hf': '15-100%', 'desc': 'Range-separated hybrid meta-GGA with D4.'},
    'LC-BLYP': {'family': 'Range-Separated', 'developer': 'Hirao', 'hf': '0-100%', 'desc': 'Long-range corrected BLYP.'},
    'LC-PBE': {'family': 'Range-Separated', 'developer': 'Scuseria', 'hf': '0-100%', 'desc': 'Long-range corrected PBE.'},
    
    # Composite
    'PBEh-3c': {'family': 'Composite', 'developer': 'Grimme', 'hf': '42%', 'desc': 'Ultra-fast composite method for huge molecules. Uses mini-basis set intrinsically.'},
    'r2SCAN-3c': {'family': 'Composite', 'developer': 'Grimme', 'hf': '0%', 'desc': 'Robust meta-GGA composite scheme for geometries and frequencies of large systems.'},
    'wB97X-3c': {'family': 'Composite', 'developer': 'Grimme', 'hf': '16-100%', 'desc': 'Modern composite method.'}
}

BASIS_SET_DATA = {
    # Ahlrichs def2
    'def2-SV(P)': {'family': 'Ahlrichs def2', 'type': 'Split-Valence', 'desc': 'Recommended for DFT on light elements. Aux: def2/J. Good for very large molecules or quick pre-optimizations.'},
    'def2-SVP': {'family': 'Ahlrichs def2', 'type': 'Double-Zeta', 'desc': 'Standard double-zeta. Aux: def2/J (RI). Good for initial geometries. Fast.'},
    'def2-TZVP(-f)': {'family': 'Ahlrichs def2', 'type': 'Triple-Zeta', 'desc': 'Standard TZVP but without f-polarization.'},
    'def2-TZVP': {'family': 'Ahlrichs def2', 'type': 'Triple-Zeta', 'desc': 'Standard recommendation for DFT geometries and energies. Aux: def2/J.'},
    'def2-TZVPP': {'family': 'Ahlrichs def2', 'type': 'Triple-Zeta', 'desc': 'More heavily polarized than TZVP. Better for properties. Aux: def2/J.'},
    'def2-QZVP': {'family': 'Ahlrichs def2', 'type': 'Quadruple-Zeta', 'desc': 'Very large, high accuracy basis set.'},
    'def2-QZVPP': {'family': 'Ahlrichs def2', 'type': 'Quadruple-Zeta', 'desc': 'Highest accuracy Ahlrichs basis set.'},
    
    # ma-def2 (Minimally augmented)
    'ma-def2-SVP': {'family': 'ma-def2 (Diffuse)', 'type': 'Double-Zeta', 'desc': 'Minimally augmented def2. Recommended for anions. Aux: AutoAux.'},
    'ma-def2-TZVP': {'family': 'ma-def2 (Diffuse)', 'type': 'Triple-Zeta', 'desc': 'Minimally augmented def2. Highly recommended for accurate anion energies.'},
    'ma-def2-TZVPP': {'family': 'ma-def2 (Diffuse)', 'type': 'Triple-Zeta', 'desc': 'Minimally augmented with larger polarization.'},
    'ma-def2-QZVPP': {'family': 'ma-def2 (Diffuse)', 'type': 'Quadruple-Zeta', 'desc': 'Minimally augmented def2. For near basis-set limit anion calculations.'},
    
    # def2-XVPD (Property-optimized)
    'def2-SVPD': {'family': 'def2-XVPD (Property)', 'type': 'Double-Zeta', 'desc': 'Optimized for polarizability/properties. Use ma-def2 for pure anion energies.'},
    'def2-TZVPD': {'family': 'def2-XVPD (Property)', 'type': 'Triple-Zeta', 'desc': 'Optimized for polarizability/properties.'},

    # cc-pVnZ
    'cc-pVDZ': {'family': 'Dunning cc-pVnZ', 'type': 'Double-Zeta', 'desc': 'Correlation consistent. Recommended for WFT (MP2, CCSD). Aux: cc-pVDZ/C.'},
    'cc-pVTZ': {'family': 'Dunning cc-pVnZ', 'type': 'Triple-Zeta', 'desc': 'Correlation consistent. Standard starting point for high-level ab initio methods.'},
    'cc-pVQZ': {'family': 'Dunning cc-pVnZ', 'type': 'Quadruple-Zeta', 'desc': 'Large correlation consistent basis set.'},
    'cc-pV5Z': {'family': 'Dunning cc-pVnZ', 'type': 'Quintuple-Zeta', 'desc': 'Huge basis set near the complete basis set limit.'},
    
    # aug-cc-pVnZ
    'aug-cc-pVDZ': {'family': 'aug-cc-pVnZ (Diffuse)', 'type': 'Double-Zeta', 'desc': 'Diffuse correlation consistent basis. Good for weak interactions/WFT anions.'},
    'aug-cc-pVTZ': {'family': 'aug-cc-pVnZ (Diffuse)', 'type': 'Triple-Zeta', 'desc': 'Diffuse correlation consistent basis. Recommended for WFT on anions.'},
    'aug-cc-pVQZ': {'family': 'aug-cc-pVnZ (Diffuse)', 'type': 'Quadruple-Zeta', 'desc': 'Extremely large diffuse basis set.'},
    'aug-cc-pV5Z': {'family': 'aug-cc-pVnZ (Diffuse)', 'type': 'Quintuple-Zeta', 'desc': 'Massive diffuse basis set near limit.'},
    
    # Pople
    '6-31G*': {'family': 'Pople (Legacy)', 'type': 'Double-Zeta', 'desc': 'Legacy split-valence basis set. Mostly used to reproduce older literature.'},
    '6-31G**': {'family': 'Pople (Legacy)', 'type': 'Double-Zeta', 'desc': 'Legacy split-valence basis set with polarization on both heavy atoms and H.'},
    '6-311G**': {'family': 'Pople (Legacy)', 'type': 'Triple-Zeta', 'desc': 'Legacy split-valence triple-zeta basis set.'}
}

class FloatingTooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tw = None
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        
    def enter(self, event=None):
        x, y, cx, cy = self.widget.bbox("insert") or (0, 0, 0, 0)
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25
        
        self.tw = tk.Toplevel(self.widget)
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry(f"+{x}+{y}")
        
        lbl = ttk.Label(self.tw, text=self.text, justify='left',
                        background="#fffbcc", foreground="#111", relief='solid', borderwidth=1,
                        wraplength=250, padding=(5, 5))
        lbl.pack(ipadx=1, ipady=1)
        
    def leave(self, event=None):
        if self.tw:
            self.tw.destroy()
        self.tw = None

class FunctionalSelectorPopup(tk.Toplevel):
    def __init__(self, parent, callback, app=None):
        super().__init__(parent)
        self.app = app
        self.title("Select Density Functional")
        w, h = 1400, 800
        sw = parent.winfo_screenwidth()
        sh = parent.winfo_screenheight()
        x = int((sw - w) / 2)
        y = int((sh - h) / 2)
        self.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        self.transient(parent)
        self.grab_set()
        
        self.callback = callback
        
        self.sort_mode = tk.StringVar(value="Popular")
        self.search_var = tk.StringVar(value="")
        
        header = ttk.Frame(self)
        header.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(header, text="Grouping:", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(header, text="Popular / Famous", variable=self.sort_mode, value="Popular", command=self.build_grid).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(header, text="By Family", variable=self.sort_mode, value="Family", command=self.build_grid).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(header, text="By Developer", variable=self.sort_mode, value="Developer", command=self.build_grid).pack(side=tk.LEFT, padx=5)
        
        ttk.Separator(header, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=15)
        ttk.Label(header, text="🔍 Search:", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, padx=5)
        search_entry = ttk.Entry(header, textvariable=self.search_var, font=("Segoe UI", 11), width=25)
        search_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(header, text="+ Add New Functional", command=self._add_new_functional).pack(side=tk.RIGHT, padx=5)
        
        self.search_var.trace_add("write", lambda *args: self.build_grid())
        
        self.scale_factor = 1.35
        
        self.canvas = tk.Canvas(self, highlightthickness=0, bg="#ffffff")
        self.v_scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.h_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        
        self.scrollable_frame = tk.Frame(self.canvas, bg="#ffffff")
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.v_scrollbar.set, xscrollcommand=self.h_scrollbar.set)
        
        self.h_scrollbar.pack(side="bottom", fill="x")
        self.v_scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        self.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        self.bind("<Shift-MouseWheel>", lambda e: self.canvas.xview_scroll(int(-1*(e.delta/120)), "units"))
        self.bind("<Control-MouseWheel>", self.do_zoom)
        
        self.build_grid()
        
    def do_zoom(self, event):
        if event.delta > 0:
            self.scale_factor += 0.1
        else:
            self.scale_factor -= 0.1
        self.scale_factor = max(0.5, min(self.scale_factor, 2.5))
        self.build_grid()
        
    def build_grid(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
            
        groups = {}
        query = self.search_var.get().strip().lower()
        mode = self.sort_mode.get()
        
        FAMOUS_LIST = {
            'BLYP', 'BP86', 'PBE', 'OLYP', 'TPSS', 'revTPSS', 'M06-L',
            'B3LYP', 'B3LYP/G', 'BHLYP', 'PBE0', 'B3PW', 'O3LYP',
            'TPSSh', 'PW6B95', 'M06', 'M06-2X', 'LC-BLYP', 'CAM-B3LYP',
            'wB97', 'wB97X', 'wB97X-D3'
        }
        GREEN_HILITE = {'BP86', 'TPSS', 'M06-L', 'B3LYP', 'PBE0', 'M06', 'TPSSh'}
        
        for func_name, data in FUNCTIONAL_DATA.items():
            if query and query not in func_name.lower():
                continue
            if mode == "Popular" and not query and func_name not in FAMOUS_LIST:
                continue
            
            key = data['developer'] if mode == "Developer" else data['family']
            groups.setdefault(key, []).append((func_name, data))
            
        if not groups:
            tk.Label(self.scrollable_frame, text="No Functionals match your search.", font=("Segoe UI", 12), bg="#ffffff", fg="#555").grid(row=0, column=0, pady=20, padx=20)
            return
            
        sorted_groups = sorted(groups.items())
        
        current_col = 0
        current_row = 0
        max_cols = 6  # Wrap after 6 parallel groups
        
        fsize_h = max(7, int(11 * self.scale_factor))
        fsize_t = max(6, int(10 * self.scale_factor))
        fsize_s = max(5, int(8 * self.scale_factor))
        fsize_i = max(7, int(11 * self.scale_factor))
        pad_x = max(2, int(6 * self.scale_factor))
        pad_y = max(1, int(4 * self.scale_factor))
        pad_gap = max(2, int(10 * self.scale_factor))
        
        for group_name, items in sorted_groups:
            if current_col >= max_cols:
                current_col = 0
                current_row += 1
                
            col_frame = tk.Frame(self.scrollable_frame, bg="#ffffff")
            col_frame.grid(row=current_row, column=current_col, sticky="nw", padx=max(2, int(8 * self.scale_factor)), pady=max(2, int(8 * self.scale_factor)))
            
            ttk.Label(col_frame, text=group_name, font=("Segoe UI", fsize_h, "bold"), foreground="#0b5cab", background="#ffffff").pack(anchor="n", pady=(max(1, int(5 * self.scale_factor)), 2))
            ttk.Separator(col_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, max(1, int(6 * self.scale_factor))))
            
            for fn_name, data in items:
                is_green = (mode == "Popular" and fn_name in GREEN_HILITE)
                bg_color = "#dcfce7" if is_green else "#ffffff"
                border_color = "#22c55e" if is_green else "#cbd5e1"
                
                frame = tk.Frame(col_frame, bg=border_color, bd=1)
                frame.pack(fill=tk.X, pady=max(1, int(3 * self.scale_factor)))
                
                def make_on_click(choice=fn_name):
                    def on_click(e=None):
                        self.callback(choice)
                        self.destroy()
                    return on_click
                
                def make_on_right_click(choice=fn_name, d=data):
                    def on_right_click(e):
                        m = tk.Menu(self, tearoff=0)
                        m.add_command(label="Edit", command=lambda: self._edit_functional(choice, d))
                        if d.get("custom") or d.get("libxc_block"):
                            m.add_command(label="Delete", command=lambda: self._delete_functional(choice))
                        m.tk_popup(e.x_root, e.y_root)
                    return on_right_click

                click_handler = make_on_click()
                right_click_handler = make_on_right_click()
                
                inner = tk.Frame(frame, bg=bg_color, padx=pad_x, pady=pad_y, cursor="hand2")
                inner.pack(fill=tk.BOTH, expand=True)
                inner.bind("<Button-1>", click_handler)
                inner.bind("<Button-3>", right_click_handler)
                
                top_row = tk.Frame(inner, bg=bg_color, cursor="hand2")
                top_row.pack(fill=tk.X)
                top_row.bind("<Button-1>", click_handler)
                top_row.bind("<Button-3>", right_click_handler)
                
                lbl = tk.Label(top_row, text=fn_name, font=("Segoe UI", fsize_t, "bold"), bg=bg_color, fg="#0f172a")
                lbl.pack(side=tk.LEFT, anchor="w")
                lbl.bind("<Button-1>", click_handler)
                lbl.bind("<Button-3>", right_click_handler)
                
                info_btn = tk.Label(top_row, text="ⓘ", font=("Segoe UI", fsize_i), fg="#0284c7", bg=bg_color, cursor="question_arrow")
                info_btn.pack(side=tk.RIGHT, anchor="e", padx=(pad_gap, 0))
                FloatingTooltip(info_btn, data['desc'])
                
                hf_text = f"Exact Exchange: {data['hf']}"
                if data['hf'] == '0%': hf_text = "Pure (0%)"
                sub = tk.Label(inner, text=hf_text, font=("Segoe UI", fsize_s), bg=bg_color, fg="#475569")
                sub.pack(anchor="w", side=tk.BOTTOM, pady=(max(1, int(2 * self.scale_factor)), 0))
                sub.bind("<Button-1>", click_handler)
                
            current_col += 1

    def _add_new_functional(self):
        self._edit_functional("New Functional", {"family": "GGA", "developer": "Custom", "hf": "0%", "desc": "Custom functional."})

    def _edit_functional(self, name, data):
        popup = tk.Toplevel(self)
        popup.title(f"Edit Functional: {name}")
        popup.geometry("500x450")
        popup.transient(self)
        popup.grab_set()
        
        ttk.Label(popup, text="Name:").pack(pady=5)
        name_var = tk.StringVar(value=name)
        ttk.Entry(popup, textvariable=name_var).pack(pady=5)
        
        libxc_var = tk.BooleanVar(value=bool(data.get("libxc_block")))
        libxc_frame = ttk.Frame(popup)
        libxc_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        ttk.Checkbutton(libxc_frame, text="Include Libxc Block", variable=libxc_var, command=lambda: toggle_libxc()).pack(anchor="w")
        
        text_frame = ttk.Frame(libxc_frame)
        txt_libxc = tk.Text(text_frame, height=10, width=50, font=("Consolas", 10))
        txt_libxc.pack(fill=tk.BOTH, expand=True)
        if data.get("libxc_block"):
            txt_libxc.insert("1.0", data["libxc_block"])
        elif name == "MN15":
            txt_libxc.insert("1.0", "%method\nmethod dft\nexchange hyb_mgga_x_mn15\ncorrelation mgga_c_mn15\nend")
        
        def toggle_libxc():
            if libxc_var.get():
                text_frame.pack(fill=tk.BOTH, expand=True, pady=5)
            else:
                text_frame.pack_forget()
        toggle_libxc()
        
        def save():
            new_name = name_var.get().strip()
            if not new_name: return
            if new_name != name and new_name in FUNCTIONAL_DATA:
                messagebox.showerror("Error", f"Functional {new_name} already exists.")
                return
            
            new_data = dict(data)
            new_data["custom"] = True
            if libxc_var.get():
                new_data["libxc_block"] = txt_libxc.get("1.0", tk.END).strip()
            else:
                new_data.pop("libxc_block", None)
            
            if new_name != name:
                FUNCTIONAL_DATA.pop(name, None)
                if self.app:
                    for fam, flist in self.app.FUNCTIONALS.items():
                        if name in flist:
                            flist.remove(name)
            
            FUNCTIONAL_DATA[new_name] = new_data
            if self.app:
                fam = new_data.get('family', 'GGA')
                if fam not in self.app.FUNCTIONALS:
                    self.app.FUNCTIONALS[fam] = []
                if new_name not in self.app.FUNCTIONALS[fam]:
                    self.app.FUNCTIONALS[fam].append(new_name)
                self.app._save_custom_funcs()
            
            self.build_grid()
            popup.destroy()
            
        ttk.Button(popup, text="Save", command=save).pack(pady=10)

    def _delete_functional(self, name):
        if messagebox.askyesno("Confirm", f"Delete custom functional {name}?"):
            FUNCTIONAL_DATA.pop(name, None)
            if self.app:
                for fam, flist in self.app.FUNCTIONALS.items():
                    if name in flist:
                        flist.remove(name)
                self.app._save_custom_funcs()
            self.build_grid()

class BasisSelectorPopup(tk.Toplevel):
    def __init__(self, parent, callback, app=None):
        super().__init__(parent)
        self.app = app
        self.title("Select Basis Set")
        w, h = 1200, 700
        sw = parent.winfo_screenwidth()
        sh = parent.winfo_screenheight()
        x = int((sw - w) / 2)
        y = int((sh - h) / 2)
        self.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        self.transient(parent)
        self.grab_set()
        
        self.callback = callback
        
        self.sort_mode = tk.StringVar(value="Popular")
        self.search_var = tk.StringVar(value="")
        
        header = ttk.Frame(self)
        header.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(header, text="Grouping:", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(header, text="Popular / Famous", variable=self.sort_mode, value="Popular", command=self.build_grid).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(header, text="By Family", variable=self.sort_mode, value="Family", command=self.build_grid).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(header, text="By Type", variable=self.sort_mode, value="Type", command=self.build_grid).pack(side=tk.LEFT, padx=5)
        
        ttk.Separator(header, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=15)
        ttk.Label(header, text="🔍 Search:", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, padx=5)
        search_entry = ttk.Entry(header, textvariable=self.search_var, font=("Segoe UI", 11), width=25)
        search_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(header, text="+ Add New Basis Set", command=self._add_new_basis).pack(side=tk.RIGHT, padx=5)
        
        self.search_var.trace_add("write", lambda *args: self.build_grid())
        
        self.scale_factor = 1.35
        
        self.canvas = tk.Canvas(self, highlightthickness=0, bg="#ffffff")
        self.v_scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.h_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        
        self.scrollable_frame = tk.Frame(self.canvas, bg="#ffffff")
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.v_scrollbar.set, xscrollcommand=self.h_scrollbar.set)
        
        self.h_scrollbar.pack(side="bottom", fill="x")
        self.v_scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        self.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        self.bind("<Shift-MouseWheel>", lambda e: self.canvas.xview_scroll(int(-1*(e.delta/120)), "units"))
        self.bind("<Control-MouseWheel>", self.do_zoom)
        
        self.build_grid()
        
    def do_zoom(self, event):
        if event.delta > 0:
            self.scale_factor += 0.1
        else:
            self.scale_factor -= 0.1
        self.scale_factor = max(0.5, min(self.scale_factor, 2.5))
        self.build_grid()
        
    def build_grid(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
            
        groups = {}
        query = self.search_var.get().strip().lower()
        mode = self.sort_mode.get()
        
        FAMOUS_LIST = {
            'def2-SVP', 'def2-TZVP', 'def2-TZVPP',
            'ma-def2-SVP', 'ma-def2-TZVP', 'def2-TZVPD',
            'cc-pVDZ', 'cc-pVTZ', 'aug-cc-pVDZ', 'aug-cc-pVTZ',
            '6-31G*'
        }
        GREEN_HILITE = {'def2-TZVP', 'def2-SVP'}
        
        for basis_name, data in BASIS_SET_DATA.items():
            if query and query not in basis_name.lower():
                continue
            if mode == "Popular" and not query and basis_name not in FAMOUS_LIST:
                continue
            
            key = data['type'] if mode == "Type" else data['family']
            groups.setdefault(key, []).append((basis_name, data))
            
        if not groups:
            tk.Label(self.scrollable_frame, text="No Basis Sets match your search.", font=("Segoe UI", 12), bg="#ffffff", fg="#555").grid(row=0, column=0, pady=20, padx=20)
            return
            
        sorted_groups = sorted(groups.items())
        
        current_col = 0
        current_row = 0
        max_cols = 5
        
        fsize_h = max(7, int(11 * self.scale_factor))
        fsize_t = max(6, int(10 * self.scale_factor))
        fsize_s = max(5, int(8 * self.scale_factor))
        fsize_i = max(7, int(11 * self.scale_factor))
        pad_x = max(2, int(6 * self.scale_factor))
        pad_y = max(1, int(4 * self.scale_factor))
        pad_gap = max(2, int(10 * self.scale_factor))
        
        for group_name, items in sorted_groups:
            if current_col >= max_cols:
                current_col = 0
                current_row += 1
                
            col_frame = tk.Frame(self.scrollable_frame, bg="#ffffff")
            col_frame.grid(row=current_row, column=current_col, sticky="nw", padx=max(2, int(8 * self.scale_factor)), pady=max(2, int(8 * self.scale_factor)))
            
            ttk.Label(col_frame, text=group_name, font=("Segoe UI", fsize_h, "bold"), foreground="#0b5cab", background="#ffffff").pack(anchor="n", pady=(max(1, int(5 * self.scale_factor)), 2))
            ttk.Separator(col_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, max(1, int(6 * self.scale_factor))))
            
            for bs_name, data in items:
                is_green = (mode == "Popular" and bs_name in GREEN_HILITE)
                bg_color = "#dcfce7" if is_green else "#ffffff"
                border_color = "#22c55e" if is_green else "#cbd5e1"
                
                frame = tk.Frame(col_frame, bg=border_color, bd=1)
                frame.pack(fill=tk.X, pady=max(1, int(3 * self.scale_factor)))
                
                def make_on_click(choice=bs_name):
                    def on_click(e=None):
                        self.callback(choice)
                        self.destroy()
                    return on_click
                
                def make_on_right_click(choice=bs_name, d=data):
                    def on_right_click(e):
                        m = tk.Menu(self, tearoff=0)
                        m.add_command(label="Edit", command=lambda: self._edit_basis(choice, d))
                        if d.get("custom"):
                            m.add_command(label="Delete", command=lambda: self._delete_basis(choice))
                        m.tk_popup(e.x_root, e.y_root)
                    return on_right_click

                click_handler = make_on_click()
                right_click_handler = make_on_right_click()
                
                inner = tk.Frame(frame, bg=bg_color, padx=pad_x, pady=pad_y, cursor="hand2")
                inner.pack(fill=tk.BOTH, expand=True)
                inner.bind("<Button-1>", click_handler)
                inner.bind("<Button-3>", right_click_handler)
                
                top_row = tk.Frame(inner, bg=bg_color, cursor="hand2")
                top_row.pack(fill=tk.X)
                top_row.bind("<Button-1>", click_handler)
                top_row.bind("<Button-3>", right_click_handler)
                
                lbl = tk.Label(top_row, text=bs_name, font=("Segoe UI", fsize_t, "bold"), bg=bg_color, fg="#0f172a")
                lbl.pack(side=tk.LEFT, anchor="w")
                lbl.bind("<Button-1>", click_handler)
                lbl.bind("<Button-3>", right_click_handler)
                
                info_btn = tk.Label(top_row, text="ⓘ", font=("Segoe UI", fsize_i), fg="#0284c7", bg=bg_color, cursor="question_arrow")
                info_btn.pack(side=tk.RIGHT, anchor="e", padx=(pad_gap, 0))
                FloatingTooltip(info_btn, data['desc'])
                
                bs_type = data['type']
                sub = tk.Label(inner, text=bs_type, font=("Segoe UI", fsize_s), bg=bg_color, fg="#475569")
                sub.pack(anchor="w", side=tk.BOTTOM, pady=(max(1, int(2 * self.scale_factor)), 0))
                sub.bind("<Button-1>", click_handler)
                
            current_col += 1

    def _add_new_basis(self):
        self._edit_basis("New Basis Set", {"family": "Custom", "type": "Custom", "desc": "Custom basis set."})

    def _edit_basis(self, name, data):
        popup = tk.Toplevel(self)
        popup.title(f"Edit Basis Set: {name}")
        popup.geometry("400x200")
        popup.transient(self)
        popup.grab_set()
        
        ttk.Label(popup, text="Name:").pack(pady=5)
        name_var = tk.StringVar(value=name)
        ttk.Entry(popup, textvariable=name_var).pack(pady=5)
        
        def save():
            new_name = name_var.get().strip()
            if not new_name: return
            if new_name != name and new_name in BASIS_SET_DATA:
                messagebox.showerror("Error", f"Basis Set {new_name} already exists.")
                return
            
            new_data = dict(data)
            new_data["custom"] = True
            
            if new_name != name:
                BASIS_SET_DATA.pop(name, None)
                if self.app and name in self.app.BASIS_SETS:
                    self.app.BASIS_SETS.remove(name)
            
            BASIS_SET_DATA[new_name] = new_data
            if self.app:
                if new_name not in self.app.BASIS_SETS:
                    self.app.BASIS_SETS.append(new_name)
                self.app._save_custom_basis()
            
            self.build_grid()
            popup.destroy()
            
        ttk.Button(popup, text="Save", command=save).pack(pady=10)

    def _delete_basis(self, name):
        if messagebox.askyesno("Confirm", f"Delete custom basis set {name}?"):
            BASIS_SET_DATA.pop(name, None)
            if self.app:
                if name in self.app.BASIS_SETS:
                    self.app.BASIS_SETS.remove(name)
                self.app._save_custom_basis()
            self.build_grid()

class ModernSwitch(tk.Canvas):
    def __init__(self, parent, width=44, height=24, bg_off="#ccc", bg_on="#4caf50", 
                 fg_off="#fff", fg_on="#fff", command=None, default_state=False, *args, **kwargs):
        try:
            bg_col = ttk.Style().lookup('TFrame', 'background')
            if not bg_col: bg_col = "#f5f5f5"
        except:
            bg_col = "#f5f5f5"
            
        super().__init__(parent, width=width, height=height, highlightthickness=0, bg=bg_col, *args, **kwargs)
        self.command = command
        self.bg_off = bg_off
        self.bg_on = bg_on
        self.fg_off = fg_off
        self.fg_on = fg_on
        self.is_on = default_state
        
        self.width = width
        self.height = height
        self.radius = height // 2
        
        self.bg_id = self.create_polygon(self._get_rounded_rect_coords(0, 0, width, height, self.radius), 
                                         fill=self.bg_on if self.is_on else self.bg_off, smooth=True)
        
        cx = self.width - self.height + 2 if self.is_on else 2
        self.circle_id = self.create_oval(cx, 2, cx + self.height - 4, self.height - 4, 
                                          fill=self.fg_on if self.is_on else self.fg_off, outline="")
        
        self.bind("<Button-1>", self.toggle)
        
    def _get_rounded_rect_coords(self, x1, y1, x2, y2, r):
        return [
            x1+r, y1, x1+r, y1, x2-r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y1+r, 
            x2, y2-r, x2, y2-r, x2, y2, x2-r, y2, x2-r, y2, x1+r, y2, x1+r, y2, 
            x1, y2, x1, y2-r, x1, y2-r, x1, y1+r, x1, y1+r, x1, y1
        ]
        
    def toggle(self, event=None):
        self.is_on = not self.is_on
        if self.command:
            self.command()
        self.animate()
            
    def set_state(self, state):
        if self.is_on != state:
            self.is_on = state
            self.animate()
            
    def animate(self, current_x=None, target_x=None):
        if current_x is None:
            current_x = self.coords(self.circle_id)[0]
            target_x = self.width - self.height + 2 if self.is_on else 2
            self.itemconfig(self.bg_id, fill=self.bg_on if self.is_on else self.bg_off)
            
        step = (target_x - current_x) * 0.3
        if abs(step) < 0.5:
            self.coords(self.circle_id, target_x, 2, target_x + self.height - 4, self.height - 4)
            return
            
        new_x = current_x + step
        self.coords(self.circle_id, new_x, 2, new_x + self.height - 4, self.height - 4)
        self.after(16, self.animate, new_x, target_x)


class SolventSelectorPopup(tk.Toplevel):
    def __init__(self, parent, callback, app=None):
        super().__init__(parent)
        self.app = app
        self.title("Select Solvent")
        w, h = 1200, 700
        sw = parent.winfo_screenwidth()
        sh = parent.winfo_screenheight()
        x = int((sw - w) / 2)
        y = int((sh - h) / 2)
        self.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        self.transient(parent)
        self.grab_set()
        
        self.callback = callback
        
        self.sort_mode = tk.StringVar(value="Popular")
        self.search_var = tk.StringVar(value="")
        
        header = ttk.Frame(self)
        header.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(header, text="Grouping:", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(header, text="Popular / Famous", variable=self.sort_mode, value="Popular", command=self.build_grid).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(header, text="Alphabetical", variable=self.sort_mode, value="Alphabetical", command=self.build_grid).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(header, text="All", variable=self.sort_mode, value="All", command=self.build_grid).pack(side=tk.LEFT, padx=5)
        
        ttk.Separator(header, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=15)
        ttk.Label(header, text="🔍 Search:", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, padx=5)
        search_entry = ttk.Entry(header, textvariable=self.search_var, font=("Segoe UI", 11), width=25)
        search_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(header, text="+ Add New Solvent", command=self._add_new_solvent).pack(side=tk.RIGHT, padx=5)
        
        self.search_var.trace_add("write", lambda *args: self.build_grid())
        
        self.scale_factor = 1.35
        
        self.canvas = tk.Canvas(self, highlightthickness=0, bg="#ffffff")
        self.v_scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.h_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        
        self.scrollable_frame = tk.Frame(self.canvas, bg="#ffffff")
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.v_scrollbar.set, xscrollcommand=self.h_scrollbar.set)
        
        self.h_scrollbar.pack(side="bottom", fill="x")
        self.v_scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        self.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        self.bind("<Shift-MouseWheel>", lambda e: self.canvas.xview_scroll(int(-1*(e.delta/120)), "units"))
        self.bind("<Control-MouseWheel>", self.do_zoom)
        
        self.build_grid()

    def do_zoom(self, event):
        if event.delta > 0:
            self.scale_factor += 0.1
        else:
            self.scale_factor -= 0.1
        self.scale_factor = max(0.5, min(self.scale_factor, 2.5))
        self.build_grid()

    def build_grid(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
            
        groups = {}
        query = self.search_var.get().strip().lower()
        mode = self.sort_mode.get()
        
        GREEN_HILITE = {'water / h2o', 'methanol', 'ethanol', 'dichloromethane / ch2cl2 / dcm', 'chloroform / chcl3', 'benzene', 'toluene', 'tetrahydrofuran / thf', 'acetonitrile / mecn / ch3cn', 'acetone', 'dimethylsulfoxide / dmso', '1,4-dioxane / dioxane'}
        
        for solv_name, data in SOLVENT_DATA.items():
            if query and query not in solv_name.lower():
                continue
            
            if mode == "Popular" and not query and solv_name not in GREEN_HILITE:
                continue
                
            if mode == "Popular":
                key = "Most Common"
            elif mode == "Alphabetical":
                first_char = solv_name[0].upper()
                if first_char.isdigit():
                    first_char = "0-9"
                key = first_char
            else:
                key = "All Solvents"
                
            groups.setdefault(key, []).append((solv_name, data))
            
        if not groups:
            tk.Label(self.scrollable_frame, text="No Solvents match your search.", font=("Segoe UI", 12), bg="#ffffff", fg="#555").grid(row=0, column=0, pady=20, padx=20)
            return
            
        sorted_groups = sorted(groups.items())
        if mode == "Popular" and "Most Common" in groups:
            sorted_groups = [("Most Common", groups["Most Common"])]
            
        current_col = 0
        current_row = 0
        max_cols = 5
        
        fsize_h = max(7, int(11 * self.scale_factor))
        fsize_t = max(6, int(10 * self.scale_factor))
        fsize_s = max(5, int(8 * self.scale_factor))
        pad_x = max(2, int(6 * self.scale_factor))
        pad_y = max(1, int(4 * self.scale_factor))
        
        for group_name, items in sorted_groups:
            if current_col >= max_cols:
                current_col = 0
                current_row += 1
                
            col_frame = tk.Frame(self.scrollable_frame, bg="#ffffff")
            col_frame.grid(row=current_row, column=current_col, sticky="nw", padx=max(2, int(8 * self.scale_factor)), pady=max(2, int(8 * self.scale_factor)))
            
            ttk.Label(col_frame, text=group_name, font=("Segoe UI", fsize_h, "bold"), foreground="#0b5cab", background="#ffffff").pack(anchor="n", pady=(max(1, int(5 * self.scale_factor)), 2))
            ttk.Separator(col_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, max(1, int(6 * self.scale_factor))))
            
            items.sort(key=lambda x: x[0].lower())
            for solv_name, data in items:
                is_green = (solv_name in GREEN_HILITE and query == "")
                bg_color = "#dcfce7" if is_green else "#ffffff"
                border_color = "#22c55e" if is_green else "#cbd5e1"
                
                if data.get('custom'):
                    bg_color = "#fefce8"
                    border_color = "#eab308"
                
                frame = tk.Frame(col_frame, bg=border_color, bd=1)
                frame.pack(fill=tk.X, pady=max(1, int(3 * self.scale_factor)))
                
                def make_on_click(choice=solv_name):
                    def on_click(e=None):
                        self.callback(choice)
                        self.destroy()
                    return on_click
                
                def make_on_right_click(choice=solv_name, d=data):
                    def on_right_click(e):
                        m = tk.Menu(self, tearoff=0)
                        m.add_command(label="Edit", command=lambda: self._edit_solvent(choice, d))
                        if d.get("custom"):
                            m.add_command(label="Delete", command=lambda: self._delete_solvent(choice))
                        m.tk_popup(e.x_root, e.y_root)
                    return on_right_click

                click_handler = make_on_click()
                right_click_handler = make_on_right_click()
                
                inner = tk.Frame(frame, bg=bg_color, padx=pad_x, pady=pad_y, cursor="hand2")
                inner.pack(fill=tk.BOTH, expand=True)
                inner.bind("<Button-1>", click_handler)
                inner.bind("<Button-3>", right_click_handler)
                
                lbl = tk.Label(inner, text=solv_name, font=("Segoe UI", fsize_t, "bold"), bg=bg_color, fg="#0f172a")
                lbl.pack(anchor="w")
                lbl.bind("<Button-1>", click_handler)
                lbl.bind("<Button-3>", right_click_handler)
                
                if data.get('custom_block'):
                    sub = tk.Label(inner, text="Custom Block Defined", font=("Segoe UI", fsize_s), bg=bg_color, fg="#475569")
                else:
                    # Provide an empty label to maintain consistent height with functional popup
                    sub = tk.Label(inner, text=" ", font=("Segoe UI", fsize_s), bg=bg_color)
                
                sub.pack(anchor="w", side=tk.BOTTOM, pady=(max(1, int(2 * self.scale_factor)), 0))
                sub.bind("<Button-1>", click_handler)
                sub.bind("<Button-3>", right_click_handler)
                
            current_col += 1

    def _add_new_solvent(self):
        self._edit_solvent("New Solvent", {"crosses": 6, "custom": True})

    def _edit_solvent(self, name, data):
        popup = tk.Toplevel(self)
        popup.title(f"Edit Solvent: {name}")
        popup.geometry("500x450")
        popup.transient(self)
        popup.grab_set()
        
        ttk.Label(popup, text="Name:").pack(pady=5)
        name_var = tk.StringVar(value=name)
        ttk.Entry(popup, textvariable=name_var).pack(pady=5)
        
        ttk.Label(popup, text="Supported Models (Crosses):").pack(pady=5)
        crosses_var = tk.IntVar(value=data.get('crosses', 2))
        ttk.Entry(popup, textvariable=crosses_var, width=10).pack(pady=5)
        
        custom_block_var = tk.BooleanVar(value=bool(data.get("custom_block")))
        block_frame = ttk.Frame(popup)
        block_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        ttk.Checkbutton(block_frame, text="Define Custom Block", variable=custom_block_var, command=lambda: toggle_block()).pack(anchor="w")
        
        text_frame = ttk.Frame(block_frame)
        txt_block = tk.Text(text_frame, height=10, width=50, font=("Consolas", 10))
        txt_block.pack(fill=tk.BOTH, expand=True)
        if data.get("custom_block"):
            txt_block.insert("1.0", data["custom_block"])
        
        def toggle_block():
            if custom_block_var.get():
                text_frame.pack(fill=tk.BOTH, expand=True, pady=5)
            else:
                text_frame.pack_forget()
        toggle_block()
        
        def save():
            new_name = name_var.get().strip()
            if not new_name: return
            if new_name != name and new_name in SOLVENT_DATA:
                messagebox.showerror("Error", f"Solvent {new_name} already exists.")
                return
            
            new_data = dict(data)
            new_data["custom"] = True
            new_data["crosses"] = crosses_var.get()
            
            if custom_block_var.get():
                new_data["custom_block"] = txt_block.get("1.0", tk.END).strip()
            else:
                new_data.pop("custom_block", None)
            
            if new_name != name:
                SOLVENT_DATA.pop(name, None)
                if self.app and name in self.app.SOLVENTS:
                    self.app.SOLVENTS.remove(name)
            
            SOLVENT_DATA[new_name] = new_data
            if self.app:
                if new_name not in self.app.SOLVENTS:
                    self.app.SOLVENTS.append(new_name)
                self.app._save_custom_solvents()
            
            self.build_grid()
            popup.destroy()
            
        ttk.Button(popup, text="Save", command=save).pack(pady=10)

    def _delete_solvent(self, name):
        if messagebox.askyesno("Confirm", f"Delete custom solvent {name}?"):
            SOLVENT_DATA.pop(name, None)
            if self.app:
                if name in self.app.SOLVENTS:
                    self.app.SOLVENTS.remove(name)
                self.app._save_custom_solvents()
            self.build_grid()

class InputCreatorModule5:

    def __init__(self, parent):
        self.parent = parent
        try:
            self.parent.unbind_class("TCombobox", "<MouseWheel>")
        except Exception:
            pass
        self.frame = ttk.Frame(parent)

        # --- DATA EXTRACTED FROM ANVIL SOURCE ---
        self.FUNCTIONALS = {
            'GGA': ['PBE','BLYP','RPBE','REVPBE','BP86','OLYP','GLYP','XLYP','PW91','PWP','mPWPW','mPWLYP'],
            'Hybrid GGA': ['B3LYP','B3LYP/G','PBE0','BHLYP','B1LYP','O3LYP','X3LYP','B1P','B3P','B3PW','PW1PW','mPW1PW','mPW1LYP','REVPBE0', 'REVPBE38', 'BHANDHLYP'],
            'Meta GGA': ['M06-L','TPSS','B97M-V','B97M-D3BJ','B97M-D4','SCAN'],
            'Hybrid & Meta GGA': ['M06','M06-2X','PW6B95','TPSSh','TPSS0'],
            'Range-Separated': ['CAM-B3LYP','wB97','wB97X','wB97X-D3','wB97X-D4','wB97X-V','wB97X-D3BJ','wB97M-D4','LC-BLYP','LC-PBE'],
            'Composite': ['PBEh-3c','r2SCAN-3c','wB97X-3c']
        }
        self._load_custom_funcs()
        self._load_custom_basis()
        self._load_custom_solvents()

        self.BASIS_SETS = [
            'def2-SV(P)','def2-SVP','def2-TZVP(-f)','def2-TZVP','def2-TZVPP','def2-QZVP','def2-QZVPP',
            'cc-pVDZ','cc-pVTZ','cc-pVQZ','cc-pV5Z','aug-cc-pVDZ','aug-cc-pVTZ','aug-cc-pVQZ','aug-cc-pV5Z',
            '6-31G*','6-31G**','6-311G**'
        ]

        self.SEMIEMPIRICAL = ['GFN-xTB','GFN2-xTB','g-xTB','XTB','GFN1-xTB','GFN0-xTB','PM3','AM1']
        self.SOLVENTS = list(SOLVENT_DATA.keys())

        self.DISPERSION_MAP = {'None': '', 'Grimme D3': 'D3', 'Grimme D3 (BJ)': 'D3BJ', 'Grimme D4': 'D4'}
        self.RI_MAP = {'None': '', 'RI': 'RI', 'RIJK': 'RIJK', 'RIJCOSX': 'RIJCOSX'}

        self.saved_machines = {}
        self._load_saved_machines()
        self._load_custom_jobs()
        self.local_job_history = []
        self._load_local_job_history()
        self.xtb_job_history = []
        self._load_xtb_job_history()

        self._create_ui()

    def _custom_jobs_json_path(self):
        base = os.path.dirname(os.path.abspath(__file__)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "AutoChemy_User_Data", "custom_jobs.json")
        
    def _local_job_history_json_path(self):
        base = os.path.dirname(os.path.abspath(__file__)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "AutoChemy_User_Data", "local_job_history.json")
        
    def _load_custom_jobs(self):
        self.custom_jobs = {}
        path = self._custom_jobs_json_path()
        if os.path.exists(path):
            try:
                import json
                with open(path, "r", encoding="utf-8") as f:
                    self.custom_jobs = json.load(f)
            except Exception:
                pass
                
    def _load_local_job_history(self):
        self.local_job_history = []
        path = self._local_job_history_json_path()
        if os.path.exists(path):
            try:
                import json
                with open(path, "r", encoding="utf-8") as f:
                    self.local_job_history = json.load(f)
            except Exception:
                pass

    def _save_local_job_history(self):
        path = self._local_job_history_json_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            import json
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.local_job_history, f, indent=4)
        except Exception:
            pass

    def _xtb_job_history_json_path(self):
        base = os.path.dirname(os.path.abspath(__file__)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "AutoChemy_User_Data", "xtb_job_history.json")

    def _load_xtb_job_history(self):
        self.xtb_job_history = []
        path = self._xtb_job_history_json_path()
        if os.path.exists(path):
            try:
                import json
                with open(path, "r", encoding="utf-8") as f:
                    self.xtb_job_history = json.load(f)
            except Exception:
                pass

    def _save_xtb_job_history(self):
        path = self._xtb_job_history_json_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            import json
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.xtb_job_history, f, indent=4)
        except Exception:
            pass

    def _save_custom_jobs(self):
        path = self._custom_jobs_json_path()
        try:
            import json
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.custom_jobs, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _saved_machines_json_path(self):
        base = os.path.dirname(os.path.abspath(__file__)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "AutoChemy_User_Data", "saved_machines.json")
        
    def _custom_funcs_json_path(self):
        base = os.path.dirname(os.path.abspath(__file__)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "AutoChemy_User_Data", "custom_functionals.json")
        
    def _load_custom_funcs(self):
        path = self._custom_funcs_json_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    c_funcs = json.load(f)
                    for k, v in c_funcs.items():
                        FUNCTIONAL_DATA[k] = v
                        fam = v.get('family', 'GGA')
                        if fam not in self.FUNCTIONALS:
                            self.FUNCTIONALS[fam] = []
                        if k not in self.FUNCTIONALS[fam]:
                            self.FUNCTIONALS[fam].append(k)
            except Exception:
                pass
                
    def _save_custom_funcs(self):
        c_funcs = {k: v for k, v in FUNCTIONAL_DATA.items() if v.get('custom') or v.get('libxc_block')}
        path = self._custom_funcs_json_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(c_funcs, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _custom_basis_json_path(self):
        base = os.path.dirname(os.path.abspath(__file__)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "AutoChemy_User_Data", "custom_basis.json")
        
    def _load_custom_basis(self):
        path = self._custom_basis_json_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    c_basis = json.load(f)
                    for k, v in c_basis.items():
                        BASIS_SET_DATA[k] = v
                        if k not in self.BASIS_SETS:
                            self.BASIS_SETS.append(k)
            except Exception:
                pass
                
    def _save_custom_basis(self):
        c_basis = {k: v for k, v in BASIS_SET_DATA.items() if v.get('custom')}
        path = self._custom_basis_json_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(c_basis, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _custom_solvents_json_path(self):
        base = os.path.dirname(os.path.abspath(__file__)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "AutoChemy_User_Data", "custom_solvents.json")
        
    def _load_custom_solvents(self):
        path = self._custom_solvents_json_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    c_solv = json.load(f)
                    for k, v in c_solv.items():
                        SOLVENT_DATA[k] = v
                        if k not in self.SOLVENTS:
                            self.SOLVENTS.append(k)
            except Exception:
                pass
                
    def _save_custom_solvents(self):
        c_solv = {k: v for k, v in SOLVENT_DATA.items() if v.get('custom')}
        path = self._custom_solvents_json_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(c_solv, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _load_saved_machines(self):
        path = self._saved_machines_json_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.saved_machines = json.load(f)
            except Exception:
                self.saved_machines = {}
        else:
            self.saved_machines = {}

    def _save_saved_machines(self):
        path = self._saved_machines_json_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.saved_machines, f, indent=2, ensure_ascii=False)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save machines:\n{e}")

    def _update_saved_machine_list(self):
        machines = list(self.saved_machines.keys())
        
        if not machines:
            self.saved_machine_combo["values"] = ["None"]
            if self.saved_machine_var.get() not in ["None"]:
                self.saved_machine_var.set("None")
        else:
            self.saved_machine_combo["values"] = ["None"] + machines
            if self.saved_machine_var.get() not in ["None"] + machines:
                self.saved_machine_var.set("None")

    def _on_saved_machine_selected(self, *args):
        m = self.saved_machine_var.get()
        if m == "None" or m not in self.saved_machines:
            if hasattr(self, 'time_entry'): self.time_entry.config(state="normal")
            if hasattr(self, 'nprocs_entry'): self.nprocs_entry.config(state="normal")
            if hasattr(self, 'nodes_entry'): self.nodes_entry.config(state="normal")
            if hasattr(self, 'exec_mode_combo'): self.exec_mode_combo.config(state="readonly")
            if hasattr(self, 'hpc_qsys_combo'): self.hpc_qsys_combo.config(state="readonly")
            if hasattr(self, 'ws_os_combo'): self.ws_os_combo.config(state="readonly")
            if hasattr(self, 'ws_run_mode_combo'): self.ws_run_mode_combo.config(state="readonly")
            if hasattr(self, 'ws_orca_path_lbl'):
                self.ws_orca_path_lbl.grid()
                self.ws_orca_path_entry.grid()
                self.ws_mpi_path_lbl.grid()
                self.ws_mpi_path_entry.grid()
            if hasattr(self, '_update_exec_mode_ui'):
                self._update_exec_mode_ui()
            return
            
        data = self.saved_machines[m]
        m_type = data.get("type", "Workstation")
        
        # Lock fields unconditionally for saved machine
        if hasattr(self, 'exec_mode_combo'): self.exec_mode_combo.config(state="disabled")
        if hasattr(self, 'hpc_qsys_combo'): self.hpc_qsys_combo.config(state="disabled")
        if hasattr(self, 'ws_os_combo'): self.ws_os_combo.config(state="disabled")
        if hasattr(self, 'ws_run_mode_combo'): self.ws_run_mode_combo.config(state="disabled")
        if hasattr(self, 'time_entry'): self.time_entry.config(state="disabled")
        if hasattr(self, 'nodes_entry'): self.nodes_entry.config(state="disabled")
        if hasattr(self, 'ws_orca_path_lbl'):
            self.ws_orca_path_lbl.grid_remove()
            self.ws_orca_path_entry.grid_remove()
            self.ws_mpi_path_lbl.grid_remove()
            self.ws_mpi_path_entry.grid_remove()
            
        if m_type == "Workstation":
            if hasattr(self, 'nprocs_entry'): self.nprocs_entry.config(state="normal")
        else:
            if hasattr(self, 'nprocs_entry'): self.nprocs_entry.config(state="disabled")
        
        if m_type in ["Workstation", "HPC"]:
            self.exec_mode.set(m_type)
        
        if m_type == "Workstation":
            if hasattr(self, 'queue_cb'):
                self.queue_cb['values'] = ["small", "mini", "Large", "interact"]
            if "os" in data: self.workstation_os.set(data["os"])
            if "run_mode" in data: self.workstation_run_mode.set(data["run_mode"])
            if "orca_path" in data: self.orca_path.set(data["orca_path"])
            if "mpi_path" in data: self.mpi_path.set(data["mpi_path"])
            if "xtb_path" in data: self.xtb_path.set(data["xtb_path"])
            if "scratch_dir" in data: self.scratch_dir.set(data["scratch_dir"])
        elif m_type == "HPC":
            if "queue_system" in data: self.hpc_queue_system.set(data["queue_system"])
            if "config_type" in data: self.hpc_config_type.set(data["config_type"])
            if "orca_module" in data: self.orca_module.set(data["orca_module"])
            if "mpi_module" in data: self.mpi_module.set(data["mpi_module"])
            if "xtb_module" in data: self.xtb_module.set(data["xtb_module"])
            if "orca_path" in data: self.orca_path.set(data["orca_path"])
            if "mpi_path" in data: self.mpi_path.set(data["mpi_path"])
            if "xtb_path" in data: self.xtb_path.set(data["xtb_path"])
            if "scratch_dir" in data: self.scratch_dir.set(data["scratch_dir"])
            
            parts = data.get("partitions", [])
            if hasattr(self, 'queue_cb'):
                if parts:
                    names = [p["name"] for p in parts]
                    self.queue_cb['values'] = names
                    if self.queue.get() not in names:
                        self.queue.set(names[0])
                    else:
                        self.queue.set(self.queue.get())
                else:
                    self.queue_cb['values'] = ["small", "mini", "Large", "interact"]

    def _open_add_machine_popup(self):
        from modules.add_machine_popup import AddMachinePopup
        AddMachinePopup(self.parent, self)

    def _edit_saved_machine(self):
        m = self.saved_machine_var.get()
        if m == "None" or m not in self.saved_machines:
            messagebox.showwarning("Warning", "Please select a valid machine to edit.")
            return
        from modules.add_machine_popup import AddMachinePopup
        AddMachinePopup(self.parent, self, edit_machine=m)

    def _delete_saved_machine(self):
        m = self.saved_machine_var.get()
        if m == "None" or m not in self.saved_machines:
            messagebox.showwarning("Warning", "Please select a valid machine to delete.")
            return
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete the machine '{m}'?"):
            del self.saved_machines[m]
            self._save_saved_machines()
            self._update_saved_machine_list()
            self.saved_machine_var.set("None")

    def get_name(self):
        return "Input Creator"

    def get_icon(self):
        return "⚙️"

    def activate(self):
        self.frame.pack(fill=tk.BOTH, expand=True)
        self._apply_default_layout_on_activate()

    def deactivate(self):
        self.frame.pack_forget()

    def get_session_state(self):
        state = {
            "version": 1,
            "snapshot": self._collect_project_snapshot(),
            "project_name": self.project_choice_var.get() if hasattr(self, "project_choice_var") else "",
            "subproject_name": self.subproject_choice_var.get() if hasattr(self, "subproject_choice_var") else "",
        }
        try:
            if getattr(self, "_main_notebook", None) is not None:
                state["main_tab_index"] = self._main_notebook.index(self._main_notebook.select())
        except Exception:
            pass
        try:
            if getattr(self, "_preview_notebook", None) is not None:
                state["preview_tab_index"] = self._preview_notebook.index(self._preview_notebook.select())
        except Exception:
            pass
        return state

    def apply_session_state(self, state):
        if not isinstance(state, dict):
            return
        snapshot = state.get("snapshot")
        if isinstance(snapshot, dict):
            self._apply_project_snapshot(snapshot)

        parent_name = state.get("project_name")
        sub_name = state.get("subproject_name")
        try:
            self._refresh_project_list(select_name=parent_name, select_sub=sub_name)
        except Exception:
            pass

        try:
            nb = getattr(self, "_main_notebook", None)
            if nb is not None:
                tab_count = len(nb.tabs())
                tab_index = int(state.get("main_tab_index", 0))
                if tab_count:
                    nb.select(max(0, min(tab_index, tab_count - 1)))
        except Exception:
            pass

        try:
            nb = getattr(self, "_preview_notebook", None)
            if nb is not None:
                tab_count = len(nb.tabs())
                tab_index = int(state.get("preview_tab_index", 0))
                if tab_count:
                    nb.select(max(0, min(tab_index, tab_count - 1)))
        except Exception:
            pass

    # ---------------- UI ----------------
    def _create_ui(self):
        main = ttk.Frame(self.frame, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        proj_bar = ttk.LabelFrame(main, text="Projects", padding=(8, 6))
        proj_bar.pack(fill=tk.X, pady=(0, 10))
        pf = ttk.Frame(proj_bar)
        pf.pack(fill=tk.X)
        ttk.Label(pf, text="Project:").pack(side=tk.LEFT, padx=(0, 6))
        self._project_combo_placeholder = "— Select project —"
        self._subproject_combo_placeholder = "— Select sub-project —"
        self.project_choice_var = tk.StringVar(value=self._project_combo_placeholder)
        self.project_combo = ttk.Combobox(
            pf, textvariable=self.project_choice_var, state="readonly", width=26
        )
        self.project_combo.pack(side=tk.LEFT, padx=(0, 8))
        self.project_combo.bind("<<ComboboxSelected>>", self._on_parent_project_selected)
        ttk.Label(pf, text="Sub-project:").pack(side=tk.LEFT, padx=(2, 6))
        self.subproject_choice_var = tk.StringVar(value=self._subproject_combo_placeholder)
        self.subproject_combo = ttk.Combobox(
            pf, textvariable=self.subproject_choice_var, state="readonly", width=26
        )
        self.subproject_combo.pack(side=tk.LEFT, padx=(0, 8))
        self.subproject_combo.bind("<<ComboboxSelected>>", self._on_project_selected)
        ttk.Button(pf, text="Save current…", command=self._save_project_interactive).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(pf, text="Delete sub", command=self._delete_project_interactive).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(pf, text="Delete project", command=self._delete_whole_project_interactive).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Label(
            pf,
            text="Stores all tabs, geometry, previews, constraints, and file picker paths.",
            font=("Segoe UI", 8),
        ).pack(side=tk.LEFT, padx=(12, 0))

        header_frame = ttk.Frame(main)
        header_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(header_frame, text="Input & Script Generator",
                  font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT)
        
        ttk.Button(header_frame, text="Batch Process", command=self._open_batch_processor).pack(side=tk.LEFT, padx=(15, 5))
        tools_btn = ttk.Menubutton(header_frame, text="Data Tools")
        tools_menu = tk.Menu(tools_btn, tearoff=0)
        tools_menu.add_command(label="Query PubChem", command=lambda: show_bond_query(self.parent))
        tools_menu.add_command(label="Bond Chart", command=lambda: show_bond_chart(self.parent))
        tools_btn["menu"] = tools_menu
        tools_btn.pack(side=tk.LEFT, padx=(0, 5))

        switches_f = ttk.Frame(header_frame)
        switches_f.pack(side=tk.RIGHT)

        import webbrowser
        ttk.Button(switches_f, text="📺 Watch Tutorial", command=lambda: webbrowser.open("https://youtube.com/")).pack(side=tk.RIGHT, padx=5)

        container = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
        container.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(container)
        right_panel = ttk.Frame(container)

        container.add(left_panel, weight=1)
        container.add(right_panel, weight=1)
        self._main_container = container
        self._right_panel = right_panel

        # -------- VARIABLES --------
        # General
        self.task = tk.StringVar(value="Optimisation + Frequency")
        self.subtask = tk.StringVar(value="")
        self.goat_mode = tk.StringVar(value="GOAT")
        self.goat_nprocs_group = tk.StringVar(value="4")
        self._goat_warned = False
        self._goat_nonsemi_warned = False
        self._orca_xtb_warned = False
        self._orca_gxtb_warned = False
        self.crest_mode = tk.StringVar(value="Conformer search")
        self.crest_gfn_level = tk.StringVar(value="2")
        self.crest_ewin = tk.StringVar(value="6.0")
        self.crest_temp = tk.StringVar(value="298.15")
        self.crest_threads = tk.StringVar(value="4")
        self.crest_solvent_model = tk.StringVar(value="ALPB")
        self.crest_solvent = tk.StringVar(value="")
        self.crest_extra_args = tk.StringVar(value="")
        self.method_type = tk.StringVar(value="DFT")
        self.method_sub_type = tk.StringVar(value="")
        self.freq_type = tk.StringVar(value="Analytical")
        self.scan_a1 = tk.StringVar(value="")
        self.scan_a2 = tk.StringVar(value="")
        self.scan_a3 = tk.StringVar(value="")
        self.scan_a4 = tk.StringVar(value="")
        self.scan_start = tk.StringVar(value="")
        self.scan_end = tk.StringVar(value="")
        self.scan_steps = tk.StringVar(value="10")
        self.other_cmd = tk.StringVar(value="")
        self.func_family = tk.StringVar(value="GGA")
        self.method = tk.StringVar(value="BP86")
        self.basis = tk.StringVar(value="def2-SVP")
        self.semi_method = tk.StringVar(value="GFN2-xTB")
        self.charge = tk.StringVar(value="0")
        self.mult = tk.StringVar(value="1")
        self.filename = tk.StringVar(value="orca_job")
        self.embed_viewer_choice = tk.StringVar(value="ACV ( AutoChemyViewer )")
        self.user_mode_var = tk.StringVar(value="Beginner")
        self.embed_subprocess = None
        self.embed_hwnd = None
        self._embed_prelaunch_windows = set()
        self._viewer_detached = False
        self._embed_saved_style = None
        self._embed_saved_exstyle = None

        # Advanced & Aux
        self.dispersion = tk.StringVar(value="Grimme D3 (BJ)")
        self.ri_approx = tk.StringVar(value="RI")
        self.aux_basis = tk.StringVar(value="def2/J")
        self.grid_size = tk.StringVar(value="Default")
        # Advanced Parameters
        self.scf_acc = tk.StringVar(value="TightSCF")
        self.scf_maxiter = tk.StringVar(value="1000")
        self.geom_maxiter = tk.StringVar(value="500")
        self.qro_gen = tk.BooleanVar(value=True)
        self.temp_c = tk.StringVar(value="20")
        self.temp_k = tk.StringVar(value="293.15")
        self.ts_mode = tk.StringVar(value="0")
        self.neb_nimages = tk.StringVar(value="8")
        self.neb_product_path = tk.StringVar(value="")
        self._neb_product_coords_text = ""
        self._last_neb_path = ""
        self.moinp_file = tk.StringVar(value="")
        self.qro_file = tk.StringVar(value="")
        self.hess_file = tk.StringVar(value="")
        self._last_xyz_open_path = ""
        self._last_input_save_path = ""
        self._last_gbw_path = ""
        self._last_qro_path = ""
        self._last_hess_path = ""
        self._last_xtb_open_file_path = ""
        self._last_xtb_export_dir = ""
        self.local_job_process = None
        self.local_job_queue = None
        self._last_local_job_folder = ""
        self._last_local_job_out = ""
        self.xtb_opt_level = tk.StringVar(value="normal")
        self.xtb_gfn_level = tk.StringVar(value="2")
        self.xtb_method = tk.StringVar(value="gxtb")
        self.xtb_version_choice = tk.StringVar(value=xtb_support.default_xtb_version_label())
        self.xtb_energy_unit = tk.StringVar(value="kcal/mol")
        self.xtb_solvation_model = tk.StringVar(value="gbe")
        self.xtb_exe_path = tk.StringVar(
            value=xtb_support.bundled_xtb_versions().get(xtb_support.default_xtb_version_label(), "")
        )
        self._last_xtb_scan_meta = None
        self._last_xtb_geom_text = ""
        self._xtb_popen_holder = [None]
        self.xtb_process = None
        self.xtb_queue = None
        self.xtb_folder_var = tk.StringVar(
            value="xTB output folder: (run a job to see path — project-local external_modules/xtb/xtb_runs/xtb_run_…)"
        )

        # Solvent & Environment
        self.use_solvent = tk.BooleanVar()
        self.solvent = tk.StringVar(value="Methanol")

        # Properties
        self.prop_polar = tk.BooleanVar()
        self.prop_nmr = tk.BooleanVar()

        # HPC & Workstation
        self.exec_mode = tk.StringVar(value="Local")
        self.workstation_os = tk.StringVar(value="Linux")
        self.workstation_run_mode = tk.StringVar(value="Directly")
        self.hpc_queue_system = tk.StringVar(value="SLURM")
        
        self.queue = tk.StringVar(value="small")
        self.nprocs = tk.StringVar(value="12")
        self.nodes = tk.StringVar(value="1")
        self.time = tk.StringVar(value="03-00:00:00")
        self.memory = tk.StringVar(value="1000")
        self.scratch_dir = tk.StringVar(value="/scratch")
        self.mpi_module = tk.StringVar(value="openmpi/4.1.3")
        self.orca_module = tk.StringVar(value="ORCA/5.0.3")
        self.orca_path = tk.StringVar(value="/home/apps/ORCA503/bin")
        self.mpi_path = tk.StringVar(value="/apps/libs/openmpi/4.1.1")
        self.xtb_module = tk.StringVar(value="")
        self.xtb_path = tk.StringVar(value="")

        # -------- LEFT COLUMN: settings + geometry (no viewer here) --------
        # -------- PANED WINDOW --------
        self.left_paned = ttk.PanedWindow(left_panel, orient=tk.VERTICAL)
        self.left_paned.pack(fill=tk.BOTH, expand=True, pady=(0, 4))
        
        # -------- NOTEBOOK (TABS) --------
        notebook = ttk.Notebook(self.left_paned)
        self.left_paned.add(notebook, weight=3)
        self._left_notebook = notebook

        def _create_scrollable_tab(nb, padding=10):
            outer = ttk.Frame(nb)
            canvas = tk.Canvas(outer, highlightthickness=0)
            v_scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
            h_scroll = ttk.Scrollbar(outer, orient="horizontal", command=canvas.xview)
            
            inner = ttk.Frame(canvas, padding=padding)
            inner.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
            )
            
            canvas_window = canvas.create_window((0, 0), window=inner, anchor="nw")
            
            def _on_canvas_configure(e):
                if inner.winfo_reqwidth() < e.width:
                    canvas.itemconfig(canvas_window, width=e.width)
                else:
                    canvas.itemconfig(canvas_window, width=inner.winfo_reqwidth())
            canvas.bind("<Configure>", _on_canvas_configure)
                
            canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
            
            def _on_wheel(e):
                if canvas.winfo_containing(e.x_root, e.y_root):
                    # Check if Shift is held down (state bit 0) for horizontal scroll
                    if e.state & 0x0001:
                        canvas.xview_scroll(int(-1*(e.delta/120)), "units")
                    else:
                        canvas.yview_scroll(int(-1*(e.delta/120)), "units")
                        
            try:
                self.parent.winfo_toplevel().bind("<MouseWheel>", _on_wheel, add='+')
            except: pass
            
            v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
            h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            
            return outer, inner

        tab_general_outer, tab_general = _create_scrollable_tab(notebook)
        tab_method_outer, tab_method = _create_scrollable_tab(notebook)
        tab_params_outer, tab_params = _create_scrollable_tab(notebook)
        tab_hpc_outer, tab_hpc = _create_scrollable_tab(notebook)
        tab_xtb_outer, tab_xtb = _create_scrollable_tab(notebook)
        
        self._tab_params = tab_params

        notebook.add(tab_general_outer, text="Theory")
        notebook.add(tab_method_outer, text="Method & Basis")
        notebook.add(tab_params_outer, text="Parameters")
        notebook.add(tab_hpc_outer, text="Script")
        notebook.add(tab_xtb_outer, text="xTB Pre-submit")
        self._main_notebook = notebook
        self._tab_xtb = tab_xtb_outer
        notebook.bind("<<NotebookTabChanged>>", self._on_main_tab_changed)
        
        self.beginner_info_icons = []
        self.beginner_labels_top = []
        
        def _toggle_user_mode():
            is_beginner = (self.user_mode_var.get() == "Beginner")
            for icon in self.beginner_info_icons:
                if is_beginner:
                    icon.pack(side=tk.LEFT)
                else:
                    icon.pack_forget()
            for lbl in self.beginner_labels_top:
                if is_beginner:
                    lbl.pack(side=tk.TOP, anchor="w", pady=(0, 2))
                else:
                    lbl.pack_forget()
                
        self._toggle_user_mode_cb = _toggle_user_mode

        def section(parent, title, tooltip=None):
            f = ttk.Frame(parent)
            f.pack(anchor="w", pady=(10, 2))
            ttk.Label(f, text=title, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
            if tooltip:
                info = tk.Label(f, text=" ⓘ", font=("Segoe UI", 10), fg="#0284c7", cursor="question_arrow")
                info.pack(side=tk.LEFT)
                FloatingTooltip(info, tooltip)
                self.beginner_info_icons.append(info)

        def add_info_label(parent, text, tooltip_text, pady=(5,0)):
            f = ttk.Frame(parent)
            f.pack(anchor="w", pady=pady)
            ttk.Label(f, text=text).pack(side=tk.LEFT)
            info = tk.Label(f, text=" ⓘ", font=("Segoe UI", 10), fg="#0284c7", cursor="question_arrow")
            info.pack(side=tk.LEFT)
            FloatingTooltip(info, tooltip_text)
            self.beginner_info_icons.append(info)
            return f

        # --- TAB 1: GENERAL ---
        section(tab_general, "Theory Type", "DFT is recommended for general calculations.\nHF/MP/CCSD are for specific accuracy needs.")
        theory_f = ttk.Frame(tab_general)
        theory_f.pack(fill=tk.X)
        self.theory_cb = ttk.Combobox(theory_f, textvariable=self.method_type, state="readonly", values=["HF", "DFT", "Semiempirical", "MP", "CCSD"])
        self.theory_cb.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.sub_theory_cb = ttk.Combobox(theory_f, textvariable=self.method_sub_type, state="readonly", width=15)

        self.semi_frame = ttk.Frame(tab_general)
        ttk.Label(self.semi_frame, text="Semiempirical method:").pack(side=tk.LEFT, padx=(0, 5))
        self.semi_method_cb = ttk.Combobox(
            self.semi_frame,
            textvariable=self.semi_method,
            values=self.SEMIEMPIRICAL,
            state="readonly",
            width=14,
        )
        self.semi_method_cb.pack(side=tk.LEFT, padx=(0, 8))
        self.semi_method_note = ttk.Label(
            self.semi_frame,
            text="ORCA xTB keywords require ORCA 6.0+.",
            font=("Segoe UI", 8, "italic"),
            foreground="#c87000",
        )
        self.semi_method_note.pack(side=tk.LEFT)
        
        self.spin_state = tk.StringVar(value="RKS     # closed-shell (RKS for DFT)")
        self.spin_frame = ttk.Frame(tab_general)
        ttk.Label(self.spin_frame, text="Spin configuration:", width=18).pack(side=tk.LEFT)
        self.spin_cb = ttk.Combobox(self.spin_frame, textvariable=self.spin_state, state="readonly", width=42)
        self.spin_cb.pack(side=tk.LEFT, expand=False)
        ttk.Button(self.spin_frame, text="💡 Auto-Recommend", command=self._auto_recommend_spin).pack(side=tk.LEFT, padx=(5,0))
        
        def _on_spin_change(*args):
            v = self.spin_state.get().split()[0]
            if v in ["UKS", "UHF", "ROKS"]:
                self.qro_gen.set(True)
                if hasattr(self, 'qro_switch'):
                    self.qro_switch.set_state(True)
        self.spin_state.trace_add("write", _on_spin_change)

        section(tab_general, "Job Type", "Opt+Freq is standard to ensure structure is a true minimum.\nScan/TS are for reaction pathways.")
        task_f = ttk.Frame(tab_general)
        task_f.pack(fill=tk.X)
        self.task_cb = ttk.Combobox(task_f, textvariable=self.task)
        self.task_cb.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._update_task_cb_values()
        
        def _edit_current_job_type():
            current_task = self.task.get()
            
            if current_task == "Custom":
                job_name = self.subtask.get()
                job_data = getattr(self, "custom_jobs", {}).get(job_name, {})
                raw_inp = job_data.get("text", "")
            else:
                raw_inp = getattr(self, "_generate_orca_input", lambda: "")()

            top = tk.Toplevel(self.parent)
            top.title(f"Edit Job Type: {current_task}")
            top.geometry("550x450")
            top.transient(self.parent)
            top.grab_set()

            ttk.Label(top, text="Raw Input Template:", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 2))
            ttk.Label(top, text="Modify the standard generated input below. When saved, it will become a Custom Job Type.", foreground="#64748b").pack(anchor="w", padx=10, pady=(0, 5))
            
            text_area = tk.Text(top, height=15, font=("Consolas", 10))
            text_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
            text_area.insert("1.0", raw_inp)

            btn_f = ttk.Frame(top)
            btn_f.pack(fill=tk.X, padx=10, pady=10)

            def _save():
                new_text = text_area.get("1.0", tk.END).strip()
                if not new_text:
                    from tkinter import messagebox
                    messagebox.showerror("Error", "Input cannot be empty.", parent=top)
                    return
                
                if current_task != "Custom":
                    save_name = current_task.strip()
                    if not save_name: save_name = "Custom Job"
                    if not hasattr(self, "custom_jobs"): self.custom_jobs = {}
                    self.custom_jobs[save_name] = {"text": new_text, "dynamic_xyz": True, "dynamic_cores": True}
                    self._save_custom_jobs()
                    self._update_task_cb_values()
                    self.task.set("Custom")
                    self.subtask.set(save_name)
                else:
                    save_name = self.subtask.get()
                    self.custom_jobs[save_name]["text"] = new_text
                    self._save_custom_jobs()
                
                if hasattr(self, "_refresh_task_ui"):
                    self._refresh_task_ui()
                if hasattr(self, "generate_preview"):
                    self.generate_preview()
                top.destroy()

            def _discard():
                top.destroy()

            ttk.Button(btn_f, text="Save", command=_save).pack(side=tk.RIGHT, padx=(5, 0))
            ttk.Button(btn_f, text="Discard", command=_discard).pack(side=tk.RIGHT)

        self.btn_edit_job = ttk.Button(task_f, text="✎ Edit", width=6, command=_edit_current_job_type)
        self.btn_edit_job.pack(side=tk.LEFT, padx=(5, 0))

        self.btn_manage_custom_jobs = ttk.Button(task_f, text="Custom Jobs...", command=self._open_custom_jobs_manager)
        self.btn_manage_custom_jobs.pack(side=tk.LEFT, padx=(5, 0))

        self.subtask_cb = ttk.Combobox(task_f, textvariable=self.subtask, width=15)
        
        # New options frame for freq and scan
        self.job_opt_frame = ttk.Frame(tab_general)
        self.job_opt_frame.pack(fill=tk.X)
        
        # Freq
        self.freq_frame = ttk.Frame(self.job_opt_frame)
        ttk.Label(self.freq_frame, text="Frequency Type:").pack(side=tk.LEFT)
        ttk.Radiobutton(self.freq_frame, text="Analytical", variable=self.freq_type, value="Analytical").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(self.freq_frame, text="Numerical", variable=self.freq_type, value="Numerical").pack(side=tk.LEFT)

        # TS Mode
        self.ts_mode_frame = ttk.Frame(self.job_opt_frame)
        ttk.Label(self.ts_mode_frame, text="TS Mode:").pack(side=tk.LEFT)
        ttk.Entry(self.ts_mode_frame, textvariable=self.ts_mode, width=3).pack(side=tk.LEFT, padx=(2,0))

        # Temcal entry
        self.temcal_frame = ttk.Frame(self.job_opt_frame)
        ttk.Label(self.temcal_frame, text="Custom Route:").pack(side=tk.LEFT)
        ttk.Entry(self.temcal_frame, textvariable=self.other_cmd).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # GOAT options
        self.goat_frame = ttk.LabelFrame(self.job_opt_frame, text="GOAT options", padding=(6, 4))
        goat_row = ttk.Frame(self.goat_frame)
        goat_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(goat_row, text="Mode:").pack(side=tk.LEFT)
        ttk.Combobox(
            goat_row,
            textvariable=self.goat_mode,
            values=["GOAT", "GOAT-EXPLORE", "GOAT-REACT", "GOAT-TS"],
            state="readonly",
            width=16,
        ).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(goat_row, text="NPROCS_GROUP:").pack(side=tk.LEFT)
        ttk.Entry(goat_row, textvariable=self.goat_nprocs_group, width=6).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(
            self.goat_frame,
            text="GOAT is supported in ORCA 6.0 and above.",
            font=("Segoe UI", 8),
            foreground="#475569",
        ).pack(anchor="w")

        # CREST options
        self.crest_frame = ttk.LabelFrame(self.job_opt_frame, text="CREST options", padding=(6, 4))
        c0 = ttk.Frame(self.crest_frame); c0.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(c0, text="Mode:").pack(side=tk.LEFT)
        ttk.Combobox(
            c0,
            textvariable=self.crest_mode,
            values=["Conformer search", "iMTD-GC", "MDOPT"],
            state="readonly",
            width=18,
        ).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(c0, text="GFN level:").pack(side=tk.LEFT)
        ttk.Combobox(c0, textvariable=self.crest_gfn_level, values=["2", "1", "0"], state="readonly", width=6).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        c1 = ttk.Frame(self.crest_frame); c1.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(c1, text="Ewin (kcal/mol):").pack(side=tk.LEFT)
        ttk.Entry(c1, textvariable=self.crest_ewin, width=8).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(c1, text="Temp (K):").pack(side=tk.LEFT)
        ttk.Entry(c1, textvariable=self.crest_temp, width=8).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(c1, text="Threads:").pack(side=tk.LEFT)
        ttk.Entry(c1, textvariable=self.crest_threads, width=6).pack(side=tk.LEFT, padx=(4, 0))
        c2 = ttk.Frame(self.crest_frame); c2.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(c2, text="Solvent model:").pack(side=tk.LEFT)
        ttk.Combobox(c2, textvariable=self.crest_solvent_model, values=["ALPB", "GBSA", "none"], state="readonly", width=8).pack(
            side=tk.LEFT, padx=(4, 12)
        )
        ttk.Label(c2, text="Solvent:").pack(side=tk.LEFT)
        ttk.Entry(c2, textvariable=self.crest_solvent, width=12).pack(side=tk.LEFT, padx=(4, 0))
        c3 = ttk.Frame(self.crest_frame); c3.pack(fill=tk.X)
        ttk.Label(c3, text="Extra args:").pack(side=tk.LEFT)
        ttk.Entry(c3, textvariable=self.crest_extra_args).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        
        # Solvent
        self.solvent_frame = ttk.Frame(self.job_opt_frame)
        ttk.Label(self.solvent_frame, text="Solvent (CPCM):").pack(side=tk.LEFT)
        
        def _open_solvent_popup():
            def _on_sel(solv_str):
                self.solvent.set(solv_str)
                self.btn_select_solvent.config(text=f"{solv_str} ▾")
            SolventSelectorPopup(self.parent.winfo_toplevel(), _on_sel, app=self)
            
        self.btn_select_solvent = tk.Button(
            self.solvent_frame, text=f"{self.solvent.get()} ▾" if self.solvent.get() else "Select Solvent ▾",
            command=_open_solvent_popup, bg="#f8fafc", relief=tk.GROOVE
        )
        self.btn_select_solvent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # CASSCF params
        self.casscf_nel = tk.StringVar(value="9")
        self.casscf_norb = tk.StringVar(value="8")
        self.casscf_nroots = tk.StringVar(value="1")
        
        self.casscf_params_frame = ttk.Frame(self.job_opt_frame)
        ttk.Label(self.casscf_params_frame, text="nel:").pack(side=tk.LEFT)
        ttk.Entry(self.casscf_params_frame, textvariable=self.casscf_nel, width=5).pack(side=tk.LEFT, padx=(2,12))
        ttk.Label(self.casscf_params_frame, text="norb:").pack(side=tk.LEFT)
        ttk.Entry(self.casscf_params_frame, textvariable=self.casscf_norb, width=5).pack(side=tk.LEFT, padx=(2,12))
        ttk.Label(self.casscf_params_frame, text="nroots:").pack(side=tk.LEFT)
        ttk.Entry(self.casscf_params_frame, textvariable=self.casscf_nroots, width=5).pack(side=tk.LEFT, padx=(2,12))
        
        # Orbital Swapping
        self.orbital_swapping_frame = ttk.Frame(self.job_opt_frame)
        ttk.Label(self.orbital_swapping_frame, text="Orbital Swapping Pairs (Orb1, Orb2, Angle):").pack(side=tk.TOP, anchor="w")
        self.orbital_swap_txt = tk.Text(self.orbital_swapping_frame, height=4, width=30, font=("Consolas", 9))
        self.orbital_swap_txt.insert("1.0", "    {96, 57, 90}\n    {97, 63, 90}")
        self.orbital_swap_txt.pack(side=tk.TOP, anchor="w", pady=(2,0))

        # Scan
        self.scan_opts_frame = ttk.Frame(self.job_opt_frame)
        scan_warn_lbl = ttk.Label(self.scan_opts_frame, text="Note: ORCA uses 0-based atom numbering (first atom = 0).", font=("Segoe UI", 8, "italic"), foreground="#475569")
        scan_warn_lbl.pack(side=tk.TOP, anchor="w", pady=(0, 2))
        self.beginner_labels_top.append(scan_warn_lbl)
        
        self.scan_inputs_row = ttk.Frame(self.scan_opts_frame)
        self.scan_inputs_row.pack(fill=tk.X, side=tk.TOP)
        self.scan_inputs_row_2 = ttk.Frame(self.scan_opts_frame)
        self.scan_inputs_row_2.pack(fill=tk.X, side=tk.TOP, pady=(4, 0))
        
        ttk.Label(self.scan_inputs_row, text="Atoms:").pack(side=tk.LEFT)
        self.scan_ctype = tk.StringVar(value="Bond")
        self.scan_ctype_cb = ttk.Combobox(self.scan_inputs_row, textvariable=self.scan_ctype, values=["Bond", "Angle", "Dihedral"], state="readonly", width=8)
        self.scan_atoms_frame = ttk.Frame(self.scan_inputs_row)
        self.scan_atoms_frame.pack(side=tk.LEFT)
        
        self.scan_e1 = ttk.Entry(self.scan_atoms_frame, textvariable=self.scan_a1, width=3)
        self.scan_e2 = ttk.Entry(self.scan_atoms_frame, textvariable=self.scan_a2, width=3)
        self.scan_e3 = ttk.Entry(self.scan_atoms_frame, textvariable=self.scan_a3, width=3)
        self.scan_e4 = ttk.Entry(self.scan_atoms_frame, textvariable=self.scan_a4, width=3)
        ttk.Button(self.scan_inputs_row, text="Pick atoms…", command=self._pick_scan_atoms).pack(side=tk.LEFT, padx=(8, 0))
        
        ttk.Label(self.scan_inputs_row_2, text="Start (Å/deg):").pack(side=tk.LEFT)
        ttk.Entry(self.scan_inputs_row_2, textvariable=self.scan_start, width=6).pack(side=tk.LEFT, padx=(2, 8))
        ttk.Label(self.scan_inputs_row_2, text="End (Å/deg):").pack(side=tk.LEFT)
        ttk.Entry(self.scan_inputs_row_2, textvariable=self.scan_end, width=6).pack(side=tk.LEFT, padx=(2, 8))
        ttk.Label(self.scan_inputs_row_2, text="Steps:").pack(side=tk.LEFT)
        ttk.Entry(self.scan_inputs_row_2, textvariable=self.scan_steps, width=5).pack(side=tk.LEFT, padx=(2, 0))

        # Constrained Opt builder
        self.const_opts_frame = ttk.Frame(self.job_opt_frame)
        const_warn_lbl = ttk.Label(self.const_opts_frame, text="Note: ORCA uses 0-based atom numbering (first atom = 0).", font=("Segoe UI", 8, "italic"), foreground="#475569")
        const_warn_lbl.pack(side=tk.TOP, anchor="w", pady=(0, 2))
        self.beginner_labels_top.append(const_warn_lbl)
        self.const_rows_frame = ttk.Frame(self.const_opts_frame)
        self.const_rows_frame.pack(fill=tk.X)
        self.constraint_rows = []
        
        def _add_constraint(*args):
            idx = len(self.constraint_rows)
            row_f = ttk.Frame(self.const_rows_frame)
            row_f.pack(fill=tk.X, pady=2)
            cv = {"type": tk.StringVar(value="Bond"), "a1": tk.StringVar(), "a2": tk.StringVar(), "a3": tk.StringVar(), "a4": tk.StringVar(), "frame": row_f}
            
            cb = ttk.Combobox(row_f, textvariable=cv["type"], values=["Bond", "Angle", "Dihedral"], state="readonly", width=8)
            cb.pack(side=tk.LEFT, padx=2)
            
            atom_f = ttk.Frame(row_f)
            atom_f.pack(side=tk.LEFT)
            
            e1 = ttk.Entry(atom_f, textvariable=cv["a1"], width=3)
            e2 = ttk.Entry(atom_f, textvariable=cv["a2"], width=3)
            e3 = ttk.Entry(atom_f, textvariable=cv["a3"], width=3)
            e4 = ttk.Entry(atom_f, textvariable=cv["a4"], width=3)
            e1.pack(side=tk.LEFT, padx=1); e2.pack(side=tk.LEFT, padx=1)
            
            def _update_const_ui(*args):
                t = cv["type"].get()
                if t in ["Angle", "Dihedral"]: e3.pack(side=tk.LEFT, padx=1)
                else: e3.pack_forget()
                if t == "Dihedral": e4.pack(side=tk.LEFT, padx=1)
                else: e4.pack_forget()
            cb.bind("<<ComboboxSelected>>", _update_const_ui)
            
            def _del_row(*args):
                row_f.destroy()
                if cv in self.constraint_rows:
                    self.constraint_rows.remove(cv)
            ttk.Button(row_f, text="Pick", width=5, command=lambda cur=cv: self._pick_constraint_atoms(cur)).pack(side=tk.LEFT, padx=(3, 0))
            ttk.Button(row_f, text="-", width=2, command=_del_row).pack(side=tk.LEFT, padx=5)
            self.constraint_rows.append(cv)

        ttk.Button(self.const_opts_frame, text="+ Add Constraint", command=_add_constraint).pack(side=tk.TOP, anchor="w", pady=(5,0))
        self._add_constraint_ui = _add_constraint

        self.neb_frame = ttk.LabelFrame(self.job_opt_frame, text="NEB-TS pathway", padding=(6, 4))
        neb_r1 = ttk.Frame(self.neb_frame)
        neb_r1.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(neb_r1, text="NEB images (Nimages):").pack(side=tk.LEFT)
        ttk.Entry(neb_r1, textvariable=self.neb_nimages, width=6).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(neb_r1, text="Reactant = coordinates in the Geometry tab.", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        neb_r2 = ttk.Frame(self.neb_frame)
        neb_r2.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(neb_r2, text="Product (end):").pack(side=tk.LEFT)
        ttk.Entry(neb_r2, textvariable=self.neb_product_path, width=36).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4)
        )
        ttk.Button(neb_r2, text="Browse…", command=self._browse_neb_product).pack(side=tk.LEFT, padx=2)
        ttk.Button(neb_r2, text="Paste / edit…", command=self._open_neb_product_editor).pack(side=tk.LEFT, padx=2)

        ttk.Label(
            self.neb_frame,
            text="When saving, the product XYZ will automatically be saved as 'neb_product.xyz' alongside the input file.",
            font=("Segoe UI", 8),
            foreground="#475569",
        ).pack(anchor="w", pady=(4, 0))

        def _on_task_changed(*args):
            t = self.task.get()
            prev_t = getattr(self, "_last_task", None)
            task_switched = (t != prev_t)
            self._last_task = t

            self.freq_frame.pack_forget()
            self.ts_mode_frame.pack_forget()
            try:
                self.neb_frame.pack_forget()
            except Exception:
                pass
            self.solvent_frame.pack_forget()
            self.scan_opts_frame.pack_forget()
            self.temcal_frame.pack_forget()
            self.const_opts_frame.pack_forget()
            
            self.crest_frame.pack_forget()
            self.casscf_params_frame.pack_forget()
            self.orbital_swapping_frame.pack_forget()

            if t == "Transition State (TS)":
                self.subtask_cb.config(values=["TS Search", "NEB-TS"])
                if task_switched or not self.subtask.get():
                    if self.subtask.get() not in ["TS Search", "NEB-TS"]:
                        self.subtask.set("TS Search")
                self.subtask_cb.pack(side=tk.LEFT, padx=(5, 0))
                if self.subtask.get() == "TS Search":
                    self.ts_mode_frame.pack(side=tk.LEFT, padx=(5, 0))
                elif self.subtask.get() == "NEB-TS":
                    self.neb_frame.pack(fill=tk.X, pady=5)
            elif t == "Optimisation":
                self.subtask_cb.config(values=["Unconstrained", "Constrained"])
                if task_switched or not self.subtask.get():
                    if self.subtask.get() not in ["Unconstrained", "Constrained"]:
                        self.subtask.set("Unconstrained")
                self.subtask_cb.pack(side=tk.LEFT, padx=(5, 0))
                if self.subtask.get() == "Constrained":
                    self.const_opts_frame.pack(fill=tk.X, pady=5)
                    if not self.constraint_rows: _add_constraint()
            elif t == "Optimisation + Frequency":
                self.subtask_cb.config(values=["Unconstrained", "Constrained"])
                if task_switched or not self.subtask.get():
                    if self.subtask.get() not in ["Unconstrained", "Constrained"]:
                        self.subtask.set("Unconstrained")
                self.subtask_cb.pack(side=tk.LEFT, padx=(5, 0))
                if self.subtask.get() == "Constrained":
                    self.const_opts_frame.pack(fill=tk.X, pady=5)
                    if not self.constraint_rows:
                        _add_constraint()
            elif t == "Single Point":
                self.subtask_cb.config(values=["Without solvent", "With solvent"])
                if task_switched or not self.subtask.get():
                    if self.subtask.get() not in ["Without solvent", "With solvent"]:
                        self.subtask.set("Without solvent")
                self.subtask_cb.pack(side=tk.LEFT, padx=(5, 0))
                if self.subtask.get() == "With solvent":
                    self.solvent_frame.pack(fill=tk.X, pady=5)
            elif t == "Scan":
                self.subtask_cb.config(values=["Bond Scan", "Angle", "Dihedral", "Constrained Scan"])
                if task_switched or not self.subtask.get():
                    if self.subtask.get() not in ["Bond Scan", "Angle", "Dihedral", "Constrained Scan"]:
                        self.subtask.set("Bond Scan")
                self.subtask_cb.pack(side=tk.LEFT, padx=(5, 0))
                # Update scan UI
                st = self.subtask.get()
                if st == "Constrained Scan":
                    self.scan_ctype_cb.pack(side=tk.LEFT, padx=(0,5))
                    s_type = self.scan_ctype.get()
                else:
                    self.scan_ctype_cb.pack_forget()
                    s_type = st.split(" ")[0] # Bond, Angle, Dihedral
                
                self.scan_e1.pack(side=tk.LEFT, padx=1)
                self.scan_e2.pack(side=tk.LEFT, padx=1)
                if s_type in ["Angle", "Dihedral"]: self.scan_e3.pack(side=tk.LEFT, padx=1)
                else: self.scan_e3.pack_forget()
                if s_type == "Dihedral": self.scan_e4.pack(side=tk.LEFT, padx=1)
                else: self.scan_e4.pack_forget()
                self.scan_opts_frame.pack(fill=tk.X, pady=5)
                
                if st == "Constrained Scan":
                    self.const_opts_frame.pack(fill=tk.X, pady=5)
                    if not self.constraint_rows: _add_constraint()
            elif t == "CASSCF":
                self.subtask_cb.config(values=["CASSCF", "NEVPT2", "Orbital Swapping"])
                if task_switched or not self.subtask.get():
                    if self.subtask.get() not in ["CASSCF", "NEVPT2", "Orbital Swapping"]:
                        self.subtask.set("CASSCF")
                self.subtask_cb.pack(side=tk.LEFT, padx=(5, 0))
                st = self.subtask.get()
                if st in ["CASSCF", "NEVPT2"]:
                    self.casscf_params_frame.pack(fill=tk.X, pady=5)
                elif st == "Orbital Swapping":
                    self.orbital_swapping_frame.pack(fill=tk.X, pady=5)
            elif t == "Thermal Correction":
                self.subtask_cb.pack_forget()
                self.temcal_frame.pack(fill=tk.X, pady=5)
            elif t == "Custom":
                c_jobs = list(getattr(self, "custom_jobs", {}).keys())
                if not c_jobs:
                    c_jobs = ["(No custom jobs)"]
                self.subtask_cb.config(values=c_jobs)
                if task_switched or not self.subtask.get() or self.subtask.get() not in c_jobs:
                    self.subtask.set(c_jobs[0] if c_jobs else "")
                self.subtask_cb.pack(side=tk.LEFT, padx=(5, 0))
            elif t == "Other":
                self.subtask_cb.config(values=["GOAT", "CREST"])
                if task_switched or not self.subtask.get():
                    if self.subtask.get() not in ["GOAT", "CREST"]:
                        self.subtask.set("GOAT")
                self.subtask_cb.pack(side=tk.LEFT, padx=(5, 0))
                if self.subtask.get() == "GOAT":
                    # GOAT defaults to semiempirical XTB.
                    if self.method_type.get() != "Semiempirical":
                        self.method_type.set("Semiempirical")
                    self.semi_method.set("XTB")
                    if hasattr(self, "_refresh_theory_ui"):
                        self._refresh_theory_ui()
                    
                    
                    messagebox.showinfo(
                        "GOAT recommendation",
                        "Recommended for GOAT: semiempirical XTB.\n"
                        "Higher computational resources are needed for non-XTB methods."
                    )
                else:
                    self.temcal_frame.pack_forget()
                    
                    if self.method_type.get() != "Semiempirical":
                        self.crest_frame.pack_forget()
                    else:
                        self.crest_frame.pack(fill=tk.X, pady=5)
            elif t == "GOAT":
                # Backward compatibility if older snapshots still contain this value
                if self.method_type.get() != "Semiempirical":
                    self.method_type.set("Semiempirical")
                if (self.semi_method.get() or "").strip().upper() != "XTB":
                    self.semi_method.set("XTB")
                if hasattr(self, "_refresh_theory_ui"):
                    self._refresh_theory_ui()
                
                
                messagebox.showinfo(
                    "GOAT recommendation",
                    "Recommended for GOAT: semiempirical XTB.\n"
                    "Higher computational resources are needed for non-XTB methods."
                )
            else:
                if t not in ["IRC", "Frequency", "Custom"]:
                    # It's a custom typed task, show subtask_cb so they can type a subsection
                    self.subtask_cb.pack(side=tk.LEFT, padx=(5, 0))
                    if task_switched and not self.subtask.get():
                        self.subtask.set("")
                else:
                    if t != "Custom":
                        self.subtask_cb.pack_forget()
                    if task_switched and t != "Custom":
                        self.subtask.set("")

            if "Frequency" in t or t == "Transition State (TS)":
                self.freq_frame.pack(fill=tk.X, pady=5)

            is_goat_selected = (t == "GOAT") or (t == "Other" and self.subtask.get() == "GOAT")
            is_goat_semi = is_goat_selected and self.method_type.get() == "Semiempirical"
            if hasattr(self, "approx_section_frame"):
                if is_goat_semi:
                    self.approx_section_frame.pack_forget()
                    self.approx_warn_frame.pack_forget()
                else:
                    self.approx_section_frame.pack(fill=tk.X)
            is_crest_selected = (t == "Other" and self.subtask.get() == "CREST")
            if is_crest_selected:
                self.spin_frame.pack_forget()
                nb = getattr(self, "_main_notebook", None)
                if nb is not None and hasattr(self, "_tab_params"):
                    try:
                        nb.hide(self._tab_params)
                    except tk.TclError:
                        pass
            else:
                if hasattr(self, "_refresh_theory_ui"):
                    self._refresh_theory_ui()
                nb = getattr(self, "_main_notebook", None)
                if nb is not None and hasattr(self, "_tab_params"):
                    try:
                        nb.add(self._tab_params, text="Parameters")
                    except tk.TclError:
                        pass
            if t == "Other" and self.subtask.get() == "CREST":
                self.spin_frame.pack_forget()
                if hasattr(self, "approx_section_frame"):
                    self.approx_section_frame.pack_forget()
                    self.approx_warn_frame.pack_forget()

            if hasattr(self, "xtb_job_label"):
                self._update_xtb_job_label()
                
            if hasattr(self, "casscf_gbw_info"):
                if t == "CASSCF" and self.subtask.get() in ["CASSCF", "NEVPT2"]:
                    self.casscf_gbw_info.grid(row=0, column=3, padx=(5,0))
                else:
                    self.casscf_gbw_info.grid_remove()

        self.task_cb.bind("<<ComboboxSelected>>", _on_task_changed)
        self.subtask_cb.bind("<<ComboboxSelected>>", _on_task_changed)
        self.scan_ctype_cb.bind("<<ComboboxSelected>>", _on_task_changed)
        _on_task_changed()
        self._refresh_task_ui = _on_task_changed

        # --- TAB 1B: FUNCTIONAL & BASIS SETS ---
        section(tab_method, "Density Functional", "B3LYP is popular for organics.\nBP86/TPSSh for transition metals.\nwB97X-D3 for very accurate general energetics.")
        self.dft_placeholder = ttk.Frame(tab_method)
        self.dft_placeholder.pack(fill=tk.X)
        self.dft_frame = ttk.Frame(self.dft_placeholder)
        
        ttk.Label(self.dft_frame, text="Selected Theoretical Model", font=("Segoe UI", 10)).pack(anchor="w", pady=(5,2))
        
        def _open_functional_popup():
            def _on_sel(func_str):
                self.method.set(func_str)
                self.btn_select_functional.config(text=f"{func_str} ▾")
                func_data = ""
                for family, funcs in self.FUNCTIONALS.items():
                    if func_str in funcs:
                        func_data = family
                        break
                if "Hybrid" in func_data:
                    self.ri_approx.set("RIJCOSX")
                elif "GGA" in func_data and "Hybrid" not in func_data:
                    self.ri_approx.set("RI")
            FunctionalSelectorPopup(self.parent.winfo_toplevel(), _on_sel, app=self)
            
        self.btn_select_functional = tk.Button(
            self.dft_frame, text=f"{self.method.get()} ▾",
            bg="#f8fafc", fg="#0284c7", activebackground="#f1f5f9", activeforeground="#0369a1",
            font=("Segoe UI", 14, "bold"), relief=tk.GROOVE, bd=2, pady=6, cursor="hand2",
            command=_open_functional_popup
        )
        self.btn_select_functional.pack(fill=tk.X, pady=(4, 12))

        def _on_theory_changed(*args):
            th = self.method_type.get()
            tcur = self.task.get()
            stcur = self.subtask.get()
            goat_active = (tcur == "GOAT") or (tcur == "Other" and stcur == "GOAT")
            
            # Main theory UI toggle
            if th == "MP":
                self.sub_theory_cb.config(values=["MP2", "MP4"])
                if self.method_sub_type.get() not in ["MP2", "MP4"]: self.method_sub_type.set("MP2")
                self.sub_theory_cb.pack(side=tk.LEFT, padx=(5,0))
                self.dft_frame.pack_forget()
            elif th == "CCSD":
                self.sub_theory_cb.config(values=["CCSD", "DLPNO-CCSD(T)"])
                if self.method_sub_type.get() not in ["CCSD", "DLPNO-CCSD(T)"]: self.method_sub_type.set("CCSD")
                self.sub_theory_cb.pack(side=tk.LEFT, padx=(5,0))
                self.dft_frame.pack_forget()
            elif th == "DFT":
                self.sub_theory_cb.pack_forget()
                self.method_sub_type.set("")
                self.dft_frame.pack(fill=tk.X, pady=5)
            else:
                self.sub_theory_cb.pack_forget()
                self.method_sub_type.set("")
                self.dft_frame.pack_forget()
            
            if hasattr(self, "basis_frame"):
                if th == "Semiempirical":
                    self.basis_frame.pack_forget()
                else:
                    self.basis_frame.pack(fill=tk.X)
            
            if hasattr(self, "approx_section_frame"):
                if th == "Semiempirical" :
                    self.approx_section_frame.pack_forget()
                    self.approx_warn_frame.pack_forget()
                elif tcur != "Other" or stcur != "CREST":
                    self.approx_section_frame.pack(fill=tk.X)

            if hasattr(self, "semi_frame"):
                if th == "Semiempirical":
                    self.semi_frame.pack(fill=tk.X, pady=(5, 0), after=theory_f)
                    self._warn_if_orca_xtb_selected()
                else:
                    self.semi_frame.pack_forget()
                
            # Spin configuration toggle
            if th == "DFT":
                self.spin_cb.config(values=[
                    "RKS     # closed-shell (RKS for DFT)", 
                    "UKS     # unrestricted open-shell (UKS for DFT)", 
                    "ROKS    # restricted open-shell (ROKS for DFT)"
                ])
                if "KS" not in self.spin_state.get(): 
                    self.spin_state.set("RKS     # closed-shell (RKS for DFT)")
                self.spin_frame.pack(fill=tk.X, pady=(5,0), after=theory_f)
            elif th == "CCSD":
                self.spin_cb.config(values=[
                    "RHF     # closed-shell", 
                    "UHF     # unrestricted open-shell", 
                    "ROHF    # restricted open-shell",
                    "RKS     # closed-shell (RKS for DFT)", 
                    "UKS     # unrestricted open-shell (UKS for DFT)"
                ])
                if self.spin_state.get().split()[0] not in ["RHF", "UHF", "ROHF", "RKS", "UKS"]: 
                    self.spin_state.set("RHF     # closed-shell")
                self.spin_frame.pack(fill=tk.X, pady=(5,0), after=theory_f)
            elif th in ["HF", "MP", "Semiempirical"]:
                self.spin_cb.config(values=[
                    "RHF     # closed-shell", 
                    "UHF     # unrestricted open-shell", 
                    "ROHF    # restricted open-shell"
                ])
                if "HF" not in self.spin_state.get(): 
                    self.spin_state.set("RHF     # closed-shell")
                self.spin_frame.pack(fill=tk.X, pady=(5,0), after=theory_f)
            else:
                self.spin_frame.pack_forget()

            if hasattr(self, "basis_frame"):
                if th == "Semiempirical":
                    self.basis_frame.pack_forget()
                else:
                    self.basis_frame.pack(fill=tk.X)

            if goat_active and th != "Semiempirical":
                if not getattr(self, "_goat_nonsemi_warned", False):
                    messagebox.showwarning(
                        "GOAT runtime warning",
                        "GOAT with DFT or other non-semiempirical methods can take a lot of time.\n"
                        "Recommended default is semiempirical XTB."
                    )
                    self._goat_nonsemi_warned = True
            else:
                self._goat_nonsemi_warned = False
                
            if hasattr(self, "_update_task_cb_values"):
                self._update_task_cb_values()

        def _on_sub_theory_changed(*args):
            if self.method_sub_type.get() == "DLPNO-CCSD(T)":
                self.nprocs.set("8")
                self.memory.set("18000")
                messagebox.showwarning(
                    "High Memory Requirement",
                    "DLPNO-CCSD(T) computations require a large amount of memory.\n"
                    "Default settings have been adjusted to 8 cores and 18000 MB MaxCore.\n"
                    "Please ensure your system can handle these requirements."
                )

        self.method_sub_type.trace_add("write", _on_sub_theory_changed)

        self.theory_cb.bind("<<ComboboxSelected>>", _on_theory_changed)
        self.semi_method_cb.bind("<<ComboboxSelected>>", lambda _e: self._warn_if_orca_xtb_selected(force_gxtb=True))
        _on_theory_changed()
        self._refresh_theory_ui = _on_theory_changed

        section(tab_method, "Basis Details")
        add_info_label(tab_method, "Basis Set:", "def2-SVP/TZVP are strongly recommended.\n- def2-TZVP: Great for energies.\n- def2-SVP: Good for initial geometries.")
        
        self.basis_frame = ttk.Frame(tab_method)
        self.basis_frame.pack(fill=tk.X)
        def _open_basis_popup():
            def _on_sel(basis_str):
                self.basis.set(basis_str)
                self.btn_select_basis.config(text=f"{basis_str} ▾")
            BasisSelectorPopup(self.parent.winfo_toplevel(), _on_sel, app=self)
            
        self.btn_select_basis = tk.Button(
            self.basis_frame, text=f"{self.basis.get()} ▾",
            bg="#f8fafc", fg="#0284c7", activebackground="#f1f5f9", activeforeground="#0369a1",
            font=("Segoe UI", 14, "bold"), relief=tk.GROOVE, bd=2, pady=6, cursor="hand2",
            command=_open_basis_popup
        )
        self.btn_select_basis.pack(fill=tk.X, pady=(4, 12))

        # --- BASIS SPLITTING ---
        self.split_basis_var = tk.BooleanVar(value=False)
        self.split_rows = []
        
        split_toggle_frame = ttk.Frame(self.basis_frame)
        split_toggle_frame.pack(fill=tk.X, pady=(0, 4))
        
        self.split_switch = ModernSwitch(split_toggle_frame, command=lambda: self._toggle_split_basis_cb())
        self.split_switch.pack(side=tk.LEFT)
        ttk.Label(split_toggle_frame, text="Split Basis Set (By Atom/Element)", font=("Segoe UI", 10, "bold"), foreground="#0284c7").pack(side=tk.LEFT, padx=(5, 0))
        
        self.split_container = ttk.Frame(self.basis_frame)
        ttk.Label(self.split_container, text="Target (e.g. Fe, or 1,2)").pack(anchor="w")
        
        self.split_rows_frame = ttk.Frame(self.split_container)
        self.split_rows_frame.pack(fill=tk.X)
        
        ttk.Button(self.split_container, text="+ Add Split Assignment", command=lambda: self._add_split_row_cb()).pack(anchor="w", pady=(2, 0))
        
        def _add_split_row(target="", cmd_type="newgto", val="def2-TZVP"):
            row_frame = ttk.Frame(self.split_rows_frame)
            row_frame.pack(fill=tk.X, pady=2)
            
            tgt_var = tk.StringVar(value=target)
            cmd_var = tk.StringVar(value=cmd_type)
            val_var = tk.StringVar(value=val)
            
            tgt_cb = ttk.Combobox(row_frame, textvariable=tgt_var, width=8)
            if hasattr(self, 'detected_elements') and self.detected_elements:
                tgt_cb.config(values=self.detected_elements)
            else:
                tgt_cb.config(values=["1", "2", "3", "1,2", "1-5"])
            tgt_cb.pack(side=tk.LEFT, padx=2)
            
            ttk.Combobox(row_frame, textvariable=cmd_var, values=["newgto", "NewECP"], width=8, state="readonly").pack(side=tk.LEFT, padx=2)
            
            basis_opts = ["def2-TZVP", "def2-TZVPP", "def2-QZVPP", "def2-TZVPPD", "def2-QZVPPD", "6-31++G(d,p)", "6-311++G(d,p)", "aug-cc-pVTZ", "aug-cc-pVQZ", "SDD"]
            ttk.Combobox(row_frame, textvariable=val_var, values=basis_opts, width=15).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
            
            def _remove():
                row_frame.destroy()
                self.split_rows = [r for r in self.split_rows if r["frame"] != row_frame]
                
            ttk.Button(row_frame, text="✖", width=3, command=_remove).pack(side=tk.LEFT, padx=2)
            self.split_rows.append({"frame": row_frame, "target": tgt_var, "cmd": cmd_var, "val": val_var, "target_cb": tgt_cb})
            
        self._add_split_row_cb = _add_split_row
        
        def _toggle_split_basis():
            if hasattr(self, "split_switch"):
                self.split_basis_var.set(self.split_switch.is_on)
            if self.split_basis_var.get():
                self.split_container.pack(fill=tk.X, pady=5)
            else:
                self.split_container.pack_forget()
        self._toggle_split_basis_cb = _toggle_split_basis

        self.approx_section_frame = ttk.Frame(tab_method)
        self.approx_section_frame.pack(fill=tk.X)

        section(self.approx_section_frame, "Approximations")

        self.approx_warn_frame = ttk.Frame(self.approx_section_frame)
        self.approx_warning_var = tk.StringVar()
        self.approx_warning_lbl = ttk.Label(self.approx_warn_frame, textvariable=self.approx_warning_var, foreground="#ff4d4d", font=("Segoe UI", 9, "bold"))
        self.approx_warning_lbl.pack(anchor="w")

        grid_f = tk.Frame(self.approx_section_frame, bg="#ffffff")
        grid_f.pack(fill=tk.X, pady=5)
        grid_f.columnconfigure(0, weight=1)
        grid_f.columnconfigure(1, weight=1)

        f1 = tk.Frame(grid_f, bg="#ffffff")
        f1.grid(row=0, column=0, sticky="ew", padx=(0,5), pady=(0,5))
        add_info_label(f1, "Dispersion Correction:", "Grimme's D3(BJ) or D4 are highly recommended for DFT fields to capture non-covalent interactions (free of charge).", pady=(0,0))
        ttk.Combobox(f1, textvariable=self.dispersion, values=list(self.DISPERSION_MAP.keys())).pack(fill=tk.X)

        f2 = tk.Frame(grid_f, bg="#ffffff")
        f2.grid(row=0, column=1, sticky="ew", padx=(5,0), pady=(0,5))
        add_info_label(f2, "Integration Grid:", "Numerical integration grid.\n- Default: ORCA decides dynamically.\n- defgrid3: Required for Meta-GGA and pure Minnesota functionals.", pady=(0,0))
        ttk.Combobox(f2, textvariable=self.grid_size, values=["Default", "defgrid1", "defgrid2", "defgrid3"]).pack(fill=tk.X)

        f3 = tk.Frame(grid_f, bg="#ffffff")
        f3.grid(row=1, column=0, sticky="ew", padx=(0,5), pady=(0,5))
        add_info_label(f3, "RI Approximation:", "Speeds up DFT calculations.\n- GGA defaults to RI.\n- Hybrid functionals strongly benefit from RIJCOSX.", pady=(0,0))
        ttk.Combobox(f3, textvariable=self.ri_approx, values=list(self.RI_MAP.keys())).pack(fill=tk.X)

        f4 = tk.Frame(grid_f, bg="#ffffff")
        f4.grid(row=1, column=1, sticky="ew", padx=(5,0), pady=(0,5))
        add_info_label(f4, "Auxiliary Basis (e.g., def2/J):", "Required for RI.\n- def2/J: RI/RIJCOSX.\n- def2/JK: RI-JK.\nMust match the main basis family.", pady=(0,0))
        ttk.Combobox(f4, textvariable=self.aux_basis, values=["None", "def2/J", "def2/JK", "AutoAux"]).pack(fill=tk.X)

        def _validate_approx(*args):
            w = []
            func = self.method.get()
            bas = self.basis.get()
            disp = self.dispersion.get()
            ri = self.ri_approx.get()
            aux = self.aux_basis.get()
            grid = self.grid_size.get()
            
            func_data = ""
            for family, funcs in self.FUNCTIONALS.items():
                if func in funcs: func_data = family; break
            if not func_data:
                if "3c" in func: func_data = "Composite"
                
            is_minnesota = "M06" in func or "M11" in func or "MN15" in func

            if ri != "None":
                if "Hybrid" in func_data and ri == "RI":
                    w.append("💡 Recommendation: Hybrid functionals strongly prefer RIJCOSX instead of RI")
                elif "GGA" in func_data and "Hybrid" not in func_data and ri == "RIJCOSX":
                    w.append("💡 Recommendation: Standard GGA functionally optimally use RI")
            
            if "ma-def2" in bas and ri != "None" and aux != "AutoAux":
                w.append("💡 Recommendation: ma-def2 diffuse basis requires AutoAux auxiliary basis")
                
            if "cc-p" in bas and "aug-" not in bas and ri != "None" and aux == "def2/J":
                w.append("⚠️ Warning: Correlation consistent basis needs /C or /JK aux basis")
                
            if is_minnesota:
                if disp != "None" and "D3" in disp:
                    w.append("💡 Recommendation: Minnesota functionals intrinsically account for dispersion, D3BJ is generally not required.")
                if grid not in ["defgrid3"]:
                    w.append("⚠️ Warning: Minnesota functionals heavily depend on the integration grid. defgrid3 is highly recommended.")
            
            if ri != "None" and aux == "None":
                w.append("⚠️ Missing Auxiliary Basis for RI Approximation!")
            elif "def2/J" in aux and "def2" not in bas:
                w.append("⚠️ def2/J auxiliary basis requires a def2 family main basis.")
            elif "3c" in func_data and (disp != "None" or ri != "None"):
                w.append("⚠️ Composite '3c' methods inherently contain dispersion & RI. Turn off.")
                
            if w:
                self.approx_warning_var.set("\n".join(w))
                self.approx_warn_frame.pack(fill=tk.X, pady=(5,0))
            else:
                self.approx_warn_frame.pack_forget()

        self.method.trace_add("write", _validate_approx)
        self.basis.trace_add("write", _validate_approx)
        self.dispersion.trace_add("write", _validate_approx)
        self.ri_approx.trace_add("write", _validate_approx)
        self.aux_basis.trace_add("write", _validate_approx)
        self.grid_size.trace_add("write", _validate_approx)
        _validate_approx()
        self._refresh_approx_warnings = _validate_approx

        section(tab_general, "System & Output")
        grid_cm = ttk.Frame(tab_general)
        grid_cm.pack(fill=tk.X)
        ttk.Label(grid_cm, text="Charge:").pack(side=tk.LEFT)
        ttk.Entry(grid_cm, textvariable=self.charge, width=5).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(grid_cm, text="Multiplicity:").pack(side=tk.LEFT)
        ttk.Entry(grid_cm, textvariable=self.mult, width=5).pack(side=tk.LEFT)
        
        self.cm_warn_frame = ttk.Frame(tab_general)
        self.cm_warning_var = tk.StringVar()
        self.cm_warning_lbl = ttk.Label(self.cm_warn_frame, textvariable=self.cm_warning_var, foreground="#ff4d4d", font=("Segoe UI", 9, "bold"))
        self.cm_warning_lbl.pack(anchor="w")

        ttk.Label(tab_general, text="Job Name:").pack(anchor="w", pady=(5,0))
        ttk.Entry(tab_general, textvariable=self.filename).pack(fill=tk.X)

        # --- TAB 2: JOB PARAMETERS ---
        section(tab_params, "Hardware Limits", "Job-specific maximum core memory.")
        grid_hw = ttk.Frame(tab_params)
        grid_hw.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(grid_hw, text="MaxCore (MB):").pack(side=tk.LEFT, padx=(0,5))
        ttk.Entry(grid_hw, textvariable=self.memory, width=8).pack(side=tk.LEFT)

        section(tab_params, "Convergence Tolerances", "TightSCF is universally recommended for geometry optimizations.")
        grid_scf = ttk.Frame(tab_params)
        grid_scf.pack(fill=tk.X)
        ttk.Label(grid_scf, text="SCF Accuracy:").pack(side=tk.LEFT, padx=(0,5))
        ttk.Combobox(grid_scf, textvariable=self.scf_acc, values=["TightSCF", "LooseSCF", "VeryTightSCF", "NormalSCF"], width=12).pack(side=tk.LEFT, padx=2)

        section(tab_params, "Advanced Iterations", "Override default ORCA iteration limits if SCF struggles to converge.")
        grid_iters = ttk.Frame(tab_params)
        grid_iters.pack(fill=tk.X)
        ttk.Label(grid_iters, text="%scf maxiter:").pack(side=tk.LEFT, padx=(0,5))
        ttk.Entry(grid_iters, textvariable=self.scf_maxiter, width=6).pack(side=tk.LEFT, padx=(0,15))
        ttk.Label(grid_iters, text="%geom maxiter:").pack(side=tk.LEFT, padx=(0,5))
        ttk.Entry(grid_iters, textvariable=self.geom_maxiter, width=6).pack(side=tk.LEFT)

        section(tab_params, "Orbital Generation")
        grid_qro = ttk.Frame(tab_params)
        grid_qro.pack(fill=tk.X)
        self.qro_switch = ModernSwitch(grid_qro, default_state=True, command=lambda: self.qro_gen.set(not self.qro_gen.get()))
        self.qro_switch.pack(side=tk.LEFT)
        ttk.Label(grid_qro, text="Generate QROs (uno uco keepdens)").pack(side=tk.LEFT, padx=(5,0))
        
        section(tab_params, "Thermodynamics", "Will be applied inherently for Frequency or TS runs.")
        temp_wrap = ttk.Frame(tab_params)
        temp_wrap.pack(fill=tk.X)
        ttk.Label(temp_wrap, text="Temperature:").pack(side=tk.LEFT, padx=(0,5))
        temp_c_entry = ttk.Entry(temp_wrap, textvariable=self.temp_c, width=6)
        temp_c_entry.pack(side=tk.LEFT)
        ttk.Label(temp_wrap, text="°C").pack(side=tk.LEFT, padx=(2,15))
        temp_k_entry = ttk.Entry(temp_wrap, textvariable=self.temp_k, width=7)
        temp_k_entry.pack(side=tk.LEFT)
        ttk.Label(temp_wrap, text="K").pack(side=tk.LEFT, padx=(2,0))
        
        def _update_temp_k(*args):
            try: k = float(self.temp_c.get()) + 273.15; self.temp_k.set(f"{k:.2f}")
            except: pass
        def _update_temp_c(*args):
            try: c = float(self.temp_k.get()) - 273.15; self.temp_c.set(f"{c:.2f}")
            except: pass
            
        temp_c_entry.bind("<FocusOut>", _update_temp_k)
        temp_c_entry.bind("<Return>", _update_temp_k)
        temp_k_entry.bind("<FocusOut>", _update_temp_c)
        temp_k_entry.bind("<Return>", _update_temp_c)


        section(tab_params, "Previous Job Dependencies")
        grid_deps = ttk.Frame(tab_params)
        grid_deps.pack(fill=tk.X)
        
        ttk.Label(grid_deps, text="Read Orbitals (.gbw):").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(grid_deps, textvariable=self.moinp_file, width=32).grid(row=0, column=1, padx=(5,2))
        ttk.Button(grid_deps, text="Browse", width=7, command=self._browse_moinp_file).grid(row=0, column=2)
        
        self.casscf_gbw_info = ttk.Label(grid_deps, text="(i)", font=("Segoe UI", 10, "bold"), foreground="#0284c7", cursor="hand2")
        FloatingTooltip(self.casscf_gbw_info, "Please insert the swapped orbitals GBW file for CASSCF.")
        # Trigger an initial update to show/hide based on current task
        self._refresh_task_ui()
        
        ttk.Label(grid_deps, text="Read Hessian (.hess):").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(grid_deps, textvariable=self.hess_file, width=32).grid(row=1, column=1, padx=(5,2))
        ttk.Button(grid_deps, text="Browse", width=7, command=self._browse_hess_file).grid(row=1, column=2)
        
        ttk.Label(grid_deps, text="Read Qro (.qro):").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(grid_deps, textvariable=self.qro_file, width=32).grid(row=2, column=1, padx=(5,2))
        ttk.Button(grid_deps, text="Browse", width=7, command=self._browse_qro_file).grid(row=2, column=2)

        # --- TAB 4: HPC & RESOURCES ---
        self.saved_machine_frame = ttk.Frame(tab_hpc)
        self.saved_machine_frame.pack(fill=tk.X, pady=(10, 5))
        ttk.Label(self.saved_machine_frame, text="Saved Machine:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 5))
        self.saved_machine_var = tk.StringVar(value="None")
        self.saved_machine_combo = ttk.Combobox(
            self.saved_machine_frame, textvariable=self.saved_machine_var, state="readonly", width=25
        )
        self.saved_machine_combo.pack(side=tk.LEFT, padx=5)
        ttk.Button(self.saved_machine_frame, text="Add new machine...", command=self._open_add_machine_popup).pack(side=tk.LEFT, padx=5)
        ttk.Button(self.saved_machine_frame, text="Edit", command=self._edit_saved_machine).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.saved_machine_frame, text="Delete", command=self._delete_saved_machine).pack(side=tk.LEFT, padx=2)
        
        self.global_machine_mode = tk.StringVar(value="Dynamic")
        
        self._update_saved_machine_list()
        self.saved_machine_var.trace_add("write", self._on_saved_machine_selected)
        self.exec_mode_combo = ttk.Combobox(
            tab_hpc, textvariable=self.exec_mode, values=["Local", "Workstation", "HPC"], state="readonly"
        )
        self.exec_mode_combo.pack(fill=tk.X)


        self.local_orca_status_frame = ttk.LabelFrame(tab_hpc, text="Local ORCA Runtime Status", padding=(8, 6))
        status_row = ttk.Frame(self.local_orca_status_frame)
        status_row.pack(fill=tk.X)
        self.local_orca_light_var = tk.StringVar(value="○")
        self.local_orca_status_var = tk.StringVar(value="Select Local mode to auto-check ORCA installation.")
        ttk.Label(status_row, textvariable=self.local_orca_light_var, font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, padx=(0, 6))
        
        self.local_orca_version_combo = ttk.Combobox(status_row, state="readonly", width=35)
        self.local_orca_version_combo.pack_forget()
        
        self.local_orca_status_lbl = ttk.Label(status_row, textvariable=self.local_orca_status_var, font=("Segoe UI", 9))
        self.local_orca_status_lbl.pack(side=tk.LEFT, fill=tk.X)
        
        def _show_orca_citation(event=None):
            import tkinter.messagebox as messagebox
            msg = (
                "ORCA developed by Dr. Neese and Co-Workers at Max Planck Institute of Kohlenforschung\n\n"
                "1. Neese, F.\n"
                "   Software update: the ORCA program system, version 6.0\n"
                "   WIRES Comput. Molec. Sci. 2025 15(1), e70019\n"
                "   doi.org/10.1002/wcms.70019"
            )
            messagebox.showinfo("ORCA Citation", msg, parent=self.frame)

        self.lbl_orca_citation = ttk.Label(status_row, text="(i)", font=("Segoe UI", 9, "bold"), foreground="#0055D4", cursor="hand2")
        self.lbl_orca_citation.pack(side=tk.LEFT, padx=(4, 0))
        self.lbl_orca_citation.bind("<Button-1>", _show_orca_citation)

        spacer = ttk.Frame(status_row)
        spacer.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.btn_orca_recheck = ttk.Button(status_row, text="Re-check", command=self._update_local_orca_status)
        self.btn_orca_recheck.pack(side=tk.RIGHT)
        self.btn_orca_quick_search = ttk.Button(status_row, text="Quick search drives", command=self._run_quick_orca_search)
        self.btn_orca_quick_search.pack(side=tk.RIGHT, padx=(0, 6))
        self.btn_orca_path_help = ttk.Button(status_row, text="Path setup help", command=self._show_orca_path_setup_help)
        self.btn_orca_path_help.pack(side=tk.RIGHT, padx=(0, 6))
        self.local_orca_path_var = tk.StringVar(value="")
        self.local_orca_path_lbl = ttk.Label(
            self.local_orca_status_frame, textvariable=self.local_orca_path_var, font=("Consolas", 8), wraplength=760, justify="left"
        )
        self.local_orca_path_lbl.pack(anchor="w", pady=(4, 0))
        self.local_orca_help_frame = ttk.Frame(self.local_orca_status_frame)
        self.local_orca_help_frame.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(
            self.local_orca_help_frame,
            text="Install guide (if ORCA is missing):",
            font=("Segoe UI", 9, "italic"),
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            self.local_orca_help_frame,
            text="ORCA Official",
            command=lambda: webbrowser.open("https://www.faccts.de/orca/"),
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            self.local_orca_help_frame,
            text="ORCA Forum",
            command=lambda: webbrowser.open("https://orcaforum.kofo.mpg.de/"),
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            self.local_orca_help_frame,
            text="Windows Setup Video",
            command=lambda: webbrowser.open("https://www.youtube.com/results?search_query=orca+quantum+chemistry+windows+installation"),
        ).pack(side=tk.LEFT)

        # --- Workstation Configuration ---
        self.workstation_frame = ttk.Frame(tab_hpc)
        ttk.Label(self.workstation_frame, text="Operating System:").grid(row=0, column=0, sticky="w", pady=2)
        self.ws_os_combo = ttk.Combobox(self.workstation_frame, textvariable=self.workstation_os, values=["Linux", "Windows", "Mac"], state="readonly", width=12)
        self.ws_os_combo.grid(row=0, column=1, padx=5)
        ttk.Label(self.workstation_frame, text="Execution Style:").grid(row=0, column=2, sticky="w", padx=5)
        self.ws_run_mode_combo = ttk.Combobox(self.workstation_frame, textvariable=self.workstation_run_mode, values=["Directly", "Scratch"], state="readonly", width=12)
        self.ws_run_mode_combo.grid(row=0, column=3, padx=5)

        self.ws_orca_path_lbl = ttk.Label(self.workstation_frame, text="ORCA Path:")
        self.ws_orca_path_lbl.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.ws_orca_path_entry = ttk.Entry(self.workstation_frame, textvariable=self.orca_path, width=28)
        self.ws_orca_path_entry.grid(row=1, column=1, padx=5, pady=(4, 0))
        
        self.ws_mpi_path_lbl = ttk.Label(self.workstation_frame, text="MPI Path:")
        self.ws_mpi_path_lbl.grid(row=1, column=2, sticky="w", pady=(4, 0))
        self.ws_mpi_path_entry = ttk.Entry(self.workstation_frame, textvariable=self.mpi_path, width=28)
        self.ws_mpi_path_entry.grid(row=1, column=3, padx=5, pady=(4, 0))

        self.ws_scratch_label = ttk.Label(self.workstation_frame, text="Scratch Path:")
        self.ws_scratch_entry = ttk.Entry(self.workstation_frame, textvariable=self.scratch_dir, width=32)

        def _update_ws_run_mode(*args):
            if self.workstation_run_mode.get() == "Scratch":
                self.ws_scratch_label.grid(row=2, column=0, sticky="w", pady=(4, 0))
                self.ws_scratch_entry.grid(row=2, column=1, columnspan=3, padx=5, pady=(4, 0), sticky="w")
            else:
                self.ws_scratch_label.grid_remove()
                self.ws_scratch_entry.grid_remove()
        self.workstation_run_mode.trace_add("write", _update_ws_run_mode)
        _update_ws_run_mode()

        # --- HPC Queueing System Configuration ---
        self.hpc_qsys_frame = ttk.Frame(tab_hpc)
        ttk.Label(self.hpc_qsys_frame, text="Queueing System:").grid(row=0, column=0, sticky="w", pady=2)
        self.hpc_qsys_combo = ttk.Combobox(self.hpc_qsys_frame, textvariable=self.hpc_queue_system, values=["SLURM", "PBS", "Interactive"], state="readonly", width=12)
        self.hpc_qsys_combo.grid(row=0, column=1, padx=5)
        grid_res = ttk.Frame(tab_hpc)
        grid_res.pack(fill=tk.X)
        self.grid_res = grid_res
        ttk.Label(grid_res, text="Cores:").grid(row=0, column=0, sticky="w", pady=2)
        self.nprocs_entry = ttk.Entry(grid_res, textvariable=self.nprocs, width=8)
        self.nprocs_entry.grid(row=0, column=1, padx=5)
        
        self.nodes_label = ttk.Label(grid_res, text="Nodes:")
        self.nodes_label.grid(row=0, column=2, sticky="w", padx=5, pady=2)
        self.nodes_entry = ttk.Entry(grid_res, textvariable=self.nodes, width=8)
        self.nodes_entry.grid(row=0, column=3, padx=5)
        
        self.local_hw_hint_var = tk.StringVar(value="")
        self.local_hw_hint_lbl = ttk.Label(grid_res, textvariable=self.local_hw_hint_var, font=("Segoe UI", 8, "italic"))
        self.local_hw_hint_lbl.grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.local_hw_auto_btn = ttk.Button(grid_res, text="Auto Recommend (Local)", command=self._auto_recommend_local_resources)
        self.local_hw_auto_btn.grid(row=2, column=0, columnspan=4, sticky="w", pady=(2, 0))

        self.hpc_config_type = tk.StringVar(value="Modules")
        grid_hpc = ttk.Frame(tab_hpc)
        self.grid_hpc = grid_hpc
        ttk.Label(grid_hpc, text="Queue/Partition:").grid(row=0, column=0, sticky="w")
        self.queue_cb = ttk.Combobox(grid_hpc, textvariable=self.queue, values=["small", "mini", "Large", "interact"], width=12)
        self.queue_cb.grid(row=0, column=1, padx=5, pady=2)
        self.time_lbl = ttk.Label(grid_hpc, text="Time Limit:")
        self.time_lbl.grid(row=0, column=2, sticky="w")
        self.time_entry = ttk.Entry(grid_hpc, textvariable=self.time, width=12)
        self.time_entry.grid(row=0, column=3, padx=5, pady=2)
        
        self.machine_info_var = tk.StringVar(value="")
        self.machine_info_lbl = ttk.Label(grid_hpc, textvariable=self.machine_info_var, font=("Segoe UI", 9, "italic"), foreground="#007ACC")
        self.machine_info_lbl.grid(row=1, column=0, columnspan=4, sticky="w", pady=(5, 0))
        
        def _update_machine_info_text():
            m = self.saved_machine_var.get()
            q = self.queue.get()
            if m != "None" and m in self.saved_machines:
                data = self.saved_machines[m]
                parts = data.get("partitions", [])
                is_static = data.get("script_sub_mode") == "StaticScript"
                for p in parts:
                    if p["name"] == q:
                        if is_static:
                            self.machine_info_var.set(f"Machine: {m} | Queue: {q}")
                        else:
                            c = p.get("cores", "N/A")
                            n = p.get("nodes", "N/A")
                            t = p.get("time", "N/A")
                            self.machine_info_var.set(f"Machine: {m} | Queue: {q} | Cores: {c} | Nodes: {n} | Time: {t}")
                        return
                self.machine_info_var.set(f"Machine: {m} | Queue: {q}")
            else:
                self.machine_info_var.set("")
        def _on_queue_change(*args):
            m = self.saved_machine_var.get()
            is_locked = False
            if m != "None" and m in self.saved_machines:
                data = self.saved_machines[m]
                parts = data.get("partitions", [])
                curr_q = self.queue.get()
                
                if parts:
                    for p in parts:
                        if p["name"] == curr_q:
                            if p.get("time"): self.time.set(p["time"])
                            if p.get("cores"): self.nprocs.set(p["cores"])
                            if p.get("nodes"): self.nodes.set(p["nodes"])
                            
                            if hasattr(self, 'txt_sh') and self.txt_sh:
                                try:
                                    sh_text = self._generate_script_text()
                                    self.txt_sh.delete("1.0", tk.END)
                                    self.txt_sh.insert("1.0", sh_text)
                                except Exception:
                                    pass
                            _update_machine_info_text()
                            break
                            
        self.queue.trace_add("write", _on_queue_change)

        # Handle master toggle logic
        def _update_exec_mode_ui(*args):
            mode = self.exec_mode.get()
            m = self.saved_machine_var.get()
            is_saved = (m != "None" and m in self.saved_machines)
            
            if is_saved:
                self.local_orca_status_frame.pack_forget()
                self.workstation_frame.pack_forget()
                self.hpc_qsys_frame.pack_forget()
                self.grid_res.pack_forget()
                self.local_submit_frame.pack_forget()
                self.exec_mode_combo.pack_forget()
                
                self.grid_hpc.pack(fill=tk.X, pady=(15, 2))
                self.time_lbl.grid_remove()
                self.time_entry.grid_remove()
                self.machine_info_lbl.grid()
                _update_machine_info_text()
                return

            self.exec_mode_combo.pack(fill=tk.X, after=self.saved_machine_frame)
            self.time_lbl.grid()
            self.time_entry.grid()
            self.machine_info_lbl.grid_remove()
            self.grid_res.pack(fill=tk.X)

            if mode == "Local":
                self.local_orca_status_frame.pack(fill=tk.X, pady=(8, 6), after=self.exec_mode_combo)
                self.workstation_frame.pack_forget()
                self.hpc_qsys_frame.pack_forget()
                self.grid_hpc.pack_forget()
                self.local_submit_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
                self.nodes_label.grid_remove()
                self.nodes_entry.grid_remove()
            elif mode == "Workstation":
                self.local_orca_status_frame.pack_forget()
                self.hpc_qsys_frame.pack_forget()
                self.workstation_frame.pack(fill=tk.X, pady=(8, 6), after=self.exec_mode_combo)
                self.grid_hpc.pack_forget()
                self.local_submit_frame.pack_forget()
                self.nodes_label.grid_remove()
                self.nodes_entry.grid_remove()
            elif mode == "HPC":
                self.local_orca_status_frame.pack_forget()
                self.workstation_frame.pack_forget()
                self.hpc_qsys_frame.pack(fill=tk.X, pady=(8, 6), after=self.exec_mode_combo)
                self.grid_hpc.pack(fill=tk.X, pady=(15, 2))
                self.local_submit_frame.pack_forget()
                self.nodes_label.grid()
                self.nodes_entry.grid()
        
        self._update_exec_mode_ui = _update_exec_mode_ui
        self.exec_mode.trace_add("write", _update_exec_mode_ui)
        self.frame.after(100, _update_exec_mode_ui)


        self.local_submit_frame = ttk.Frame(tab_hpc)
        self.local_submit_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        local_btn_row = ttk.Frame(self.local_submit_frame)
        local_btn_row.pack(fill=tk.X, pady=(0, 4))
        local_btn_row_2 = ttk.Frame(self.local_submit_frame)
        local_btn_row_2.pack(fill=tk.X, pady=(0, 4))
        
        self.btn_local_submit = ttk.Button(local_btn_row, text="Submit Local Job", command=self._start_local_submission)
        self.btn_local_submit.pack(side=tk.LEFT)
        self.btn_local_stop = ttk.Button(local_btn_row, text="Stop", command=self._stop_local_submission, state=tk.DISABLED)
        self.btn_local_stop.pack(side=tk.LEFT, padx=(6, 0))
        self.btn_local_open_folder = ttk.Button(local_btn_row, text="Open Job Folder", command=self._open_local_job_folder, state=tk.DISABLED)
        self.btn_local_open_folder.pack(side=tk.LEFT, padx=(6, 0))
        
        self.btn_local_visualize_out = ttk.Button(
            local_btn_row_2,
            text="Visualize .out",
            command=self._visualize_local_out,
            state=tk.DISABLED,
        )
        self.btn_local_visualize_out.pack(side=tk.LEFT)
        ttk.Button(local_btn_row_2, text="Clear Log", command=self._clear_local_job_log).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(local_btn_row_2, text="📜 History", command=self._show_job_history_dialog).pack(side=tk.LEFT, padx=(6, 0))
        self.local_job_status_var = tk.StringVar(value="Ready for local submission.")
        ttk.Label(local_btn_row_2, textvariable=self.local_job_status_var, font=("Segoe UI", 9, "italic")).pack(side=tk.RIGHT)

        self.local_job_log_txt = tk.Text(self.local_submit_frame, height=14, width=1, font=("Consolas", 9), bg="#1e1e1e", fg="#d9ffd9")
        self.local_job_log_txt.pack(fill=tk.BOTH, expand=True)
        self.local_job_log_txt.insert("1.0", "Local submission log will appear here.\n")

        # (Scripting mode toggle removed)

        # --- TAB 5: xTB PRE-SUBMISSION ---
        section(tab_xtb, "xTB Quick Preview",
                "Run a fast xTB calculation using your current job settings.\n"
                "It uses same Job Type, geometry, charge/mult, constraints, and scan parameters from input")

        top_split = ttk.Frame(tab_xtb)
        top_split.pack(fill=tk.X, pady=(0, 6))

        left_ctrl = ttk.Frame(top_split)
        left_ctrl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right_ctrl = ttk.Frame(top_split)
        right_ctrl.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))

        ttk.Button(right_ctrl, text="ℹ Cite xTB", command=self._show_xtb_citation).pack(side=tk.TOP, anchor="e", pady=(0, 4))
        ttk.Button(right_ctrl, text="⚙ xTB Settings", command=self._open_xtb_settings).pack(side=tk.TOP, anchor="e", pady=(0, 4))
        ttk.Label(
            right_ctrl,
            text="xTB developed by Prof. Stefan Grimme and coworkers.",
            font=("Segoe UI", 8, "italic"),
            foreground="#6b7280"
        ).pack(side=tk.TOP, anchor="e")

        xtb_warn = ttk.Label(
            left_ctrl,
            text="⚠ xTB is a semiempirical tight-binding method \n"
                 "    Use it for quick screening / geometry pre-check before full DFT.",
            font=("Segoe UI", 9), foreground="#c87000", wraplength=700, justify="left",
        )
        xtb_warn.pack(anchor="w", pady=(0, 12))

        run_btn_frame = ttk.Frame(left_ctrl)
        run_btn_frame.pack(fill=tk.X, pady=(0, 5))

        self.btn_run_xtb = tk.Button(
            run_btn_frame, 
            text="▶ Run xTB Preview", 
            command=self._start_xtb_job,
            bg="#0284c7", fg="#ffffff", font=("Segoe UI", 11, "bold"), 
            activebackground="#0369a1", activeforeground="#ffffff",
            relief="flat", padx=16, pady=4, cursor="hand2"
        )
        self.btn_run_xtb.pack(side=tk.LEFT, padx=(0, 12))

        self.btn_cancel_xtb = ttk.Button(run_btn_frame, text="Stop", command=self._stop_xtb_optimization, state=tk.DISABLED)
        self.btn_cancel_xtb.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(run_btn_frame, text="XTB:").pack(side=tk.LEFT, padx=(0, 4))
        self.xtb_method_combo = ttk.Combobox(
            run_btn_frame,
            textvariable=self.xtb_method,
            values=("gxtb", "gfn2"),
            state="readonly",
            width=8,
        )
        self.xtb_method_combo.pack(side=tk.LEFT, padx=(0, 12))

        self.xtb_include_solvation = tk.StringVar(value="No")
        self.xtb_solvation_label = ttk.Label(run_btn_frame, text="Include Solvation:")
        self.xtb_solvation_combo = ttk.Combobox(
            run_btn_frame,
            textvariable=self.xtb_include_solvation,
            values=("No", "Yes"),
            state="readonly",
            width=5,
        )

        self.xtb_job_label = ttk.Label(run_btn_frame, text="", font=("Segoe UI", 9, "italic"))
        self.xtb_job_label.pack(side=tk.LEFT, padx=(0, 12))

        xtb_panes = ttk.PanedWindow(tab_xtb, orient=tk.HORIZONTAL)
        xtb_panes.pack(fill=tk.BOTH, expand=True)

        xtb_console_frame = ttk.Frame(xtb_panes)
        xtb_panes.add(xtb_console_frame, weight=1)
        xtb_left_vert = ttk.PanedWindow(xtb_console_frame, orient=tk.VERTICAL)
        xtb_left_vert.pack(fill=tk.BOTH, expand=True)
        xtb_log_wrap = ttk.Frame(xtb_left_vert)
        xtb_left_notebook = ttk.Notebook(xtb_left_vert)
        xtb_left_vert.add(xtb_log_wrap, weight=1)
        xtb_left_vert.add(xtb_left_notebook, weight=1)
        self.xtb_left_vert = xtb_left_vert

        log_top_frame = ttk.Frame(xtb_log_wrap)
        log_top_frame.pack(fill=tk.X)
        ttk.Label(log_top_frame, text="Live Log Stream:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.btn_full_log_xtb = ttk.Button(log_top_frame, text="Open Full Log", command=self._open_xtb_full_log, state=tk.DISABLED)
        self.btn_full_log_xtb.pack(side=tk.LEFT, padx=10)
        
        log_txt_frame = ttk.Frame(xtb_log_wrap)
        log_txt_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        self.xtb_log_txt = tk.Text(log_txt_frame, height=10, width=40, font=("Consolas", 9), bg="#1e1e1e", fg="#00ff00", wrap=tk.NONE)
        log_y_scroll = ttk.Scrollbar(log_txt_frame, orient="vertical", command=self.xtb_log_txt.yview)
        log_x_scroll = ttk.Scrollbar(log_txt_frame, orient="horizontal", command=self.xtb_log_txt.xview)
        self.xtb_log_txt.configure(yscrollcommand=log_y_scroll.set, xscrollcommand=log_x_scroll.set)
        
        log_y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        log_x_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.xtb_log_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        xtb_num_wrap = ttk.Frame(xtb_left_notebook)
        xtb_geom_wrap = ttk.Frame(xtb_left_notebook)
        xtb_left_notebook.add(xtb_num_wrap, text="Numbers")
        xtb_left_notebook.add(xtb_geom_wrap, text="xTB Output Geometry")
        self.xtb_bottom_notebook = xtb_left_notebook

        self.xtb_numbers_txt = tk.Text(xtb_num_wrap, height=10, width=40, font=("Consolas", 9), wrap=tk.NONE)
        num_y_scroll = ttk.Scrollbar(xtb_num_wrap, orient="vertical", command=self.xtb_numbers_txt.yview)
        num_x_scroll = ttk.Scrollbar(xtb_num_wrap, orient="horizontal", command=self.xtb_numbers_txt.xview)
        self.xtb_numbers_txt.configure(yscrollcommand=num_y_scroll.set, xscrollcommand=num_x_scroll.set)
        
        num_y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        num_x_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.xtb_numbers_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.xtb_numbers_txt.insert(
            "1.0",
            "Important values will appear here after xTB run:\n"
            "- Scan: all step energies (Eh and relative kcal/mol)\n"
            "- Optimization/TS/Constrained opt: final total energy\n"
            "- Frequency jobs: detected vibrational frequencies\n",
        )
        self.xtb_numbers_txt.config(state=tk.DISABLED)

        self.xtb_out_geom = tk.Text(xtb_geom_wrap, height=7, width=40, font=("Consolas", 10), wrap=tk.NONE)
        geom_y_scroll = ttk.Scrollbar(xtb_geom_wrap, orient="vertical", command=self.xtb_out_geom.yview)
        geom_x_scroll = ttk.Scrollbar(xtb_geom_wrap, orient="horizontal", command=self.xtb_out_geom.xview)
        self.xtb_out_geom.configure(yscrollcommand=geom_y_scroll.set, xscrollcommand=geom_x_scroll.set)
        
        geom_y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        geom_x_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.xtb_out_geom.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        xtb_geom_frame = ttk.Frame(xtb_panes)
        xtb_panes.add(xtb_geom_frame, weight=1)
        xtb_right_vert = ttk.PanedWindow(xtb_geom_frame, orient=tk.VERTICAL)
        xtb_right_vert.pack(fill=tk.BOTH, expand=True)

        xtb_graph_bottom = ttk.Notebook(xtb_right_vert)
        xtb_right_vert.add(xtb_graph_bottom, weight=10)
        self.xtb_right_notebook = xtb_graph_bottom

        xtb_viewer_tab = ttk.Frame(xtb_graph_bottom)
        xtb_graph_tab = ttk.Frame(xtb_graph_bottom)
        xtb_graph_bottom.add(xtb_viewer_tab, text="3D Viewer")
        xtb_graph_bottom.add(xtb_graph_tab, text="Scan Graph")

        self.xtb_viewer_split_var = tk.BooleanVar(value=False)
        viewer_top = ttk.Frame(xtb_viewer_tab)
        viewer_top.pack(fill=tk.X, padx=2, pady=2)
        ttk.Button(viewer_top, text="Output", command=self._load_xtb_viewer_output).pack(side=tk.LEFT, padx=2)
        ttk.Button(viewer_top, text="Input", command=self._load_xtb_viewer_input).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(viewer_top, text="Split", variable=self.xtb_viewer_split_var, command=self._refresh_xtb_viewer_split).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(viewer_top, text="External:").pack(side=tk.LEFT, padx=(10,2))
        self.xtb_embed_viewer_combo = ttk.Combobox(
            viewer_top,
            values=["ACV", "Chemcraft", "Jmol", "Avogadro", "GaussView"],
            state="readonly",
            width=10,
        )
        self.xtb_embed_viewer_combo.pack(side=tk.LEFT, padx=2)
        self.xtb_embed_viewer_combo.set("ACV")
        ttk.Button(viewer_top, text="Open Embedded", command=self._embed_xtb_viewer_external).pack(side=tk.LEFT, padx=2)
        self.btn_xtb_detach = ttk.Button(viewer_top, text="Detach", command=self._detach_xtb_viewer, state=tk.DISABLED)
        self.btn_xtb_detach.pack(side=tk.RIGHT, padx=2)

        self.xtb_viewer_host = ttk.Frame(xtb_viewer_tab)
        self.xtb_viewer_host.pack(fill=tk.BOTH, expand=True)
        # Bind resize for external embedded viewer
        self.xtb_viewer_host.bind("<Configure>", lambda e: self.frame.after(100, self._resize_xtb_embedded))

        self.xtb_scan_plot_title = ttk.Label(
            xtb_graph_tab,
            text="Scan graph will appear here after run.",
            font=("Segoe UI", 9, "italic"),
        )
        self.xtb_scan_plot_title.pack(anchor="w", pady=(2, 2))
        self.xtb_scan_plot_host = ttk.Frame(xtb_graph_tab)
        self.xtb_scan_plot_host.pack(fill=tk.BOTH, expand=True)
        self.xtb_right_vert = xtb_right_vert

        xtb_bottom_btns = ttk.Frame(tab_xtb)
        xtb_bottom_btns.pack(pady=(10, 0))

        ttk.Button(xtb_bottom_btns, text="✓ Use Output XYZ in Main Input",
                   command=self._use_xtb_geom).pack(side=tk.LEFT, padx=5)

        self.btn_scan_graph_xtb = ttk.Button(xtb_bottom_btns, text="Show Scan Graph",
                                              command=self._show_xtb_scan_graph, state=tk.DISABLED)
        # Note: btn_scan_graph_xtb is NOT packed initially. It only appears when job is scan.


        xtb_folder_row = ttk.Frame(tab_xtb)
        xtb_folder_row.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(
            xtb_folder_row,
            textvariable=self.xtb_folder_var,
            font=("Consolas", 8),
            wraplength=900,
            justify="left",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, anchor="w")

        self._xtb_advanced_widgets = (
            getattr(self, "btn_cancel_xtb", None),
            getattr(self, "btn_vis_xtb", None),
            getattr(self, "btn_scan_graph_xtb", None),
            getattr(self, "btn_full_log_xtb", None),
            getattr(self, "btn_open_xtb_folder", None),
            getattr(self, "xtb_job_label", None),
        )
        self._xtb_beginner_primary_btn = self.btn_run_xtb

        self._refresh_xtb_settings_summary()
        self._update_xtb_job_label()
        self._sync_local_mode_with_global()

        # -------- GEOMETRY & ACTIONS --------
        geom_frame = ttk.Frame(self.left_paned)
        self.left_paned.add(geom_frame, weight=2)
        self._left_geom_frame = geom_frame

        section(
            geom_frame,
            "Geometry (XYZ format)",
            "Plain atom lines OK (XYZ header optional). Drop a .xyz file here on Windows/Mac if enabled.\n"
            "Chemcraft/ORCA pasted coords with Unicode minus (–) are normalized automatically.",
        )
        self.geom = tk.Text(geom_frame, height=11, font=("Consolas", 12), wrap=tk.NONE)
        self.geom.pack(fill=tk.BOTH, expand=True)
        self._geom_wnd_hook_done = False
        try:
            self.parent.winfo_toplevel().after(
                200, lambda: self.parent.winfo_toplevel().after(200, self._hook_geometry_drag_drop)
            )
        except Exception:
            self.parent.after(400, self._hook_geometry_drag_drop)
        
        def _validate_charge_mult(event=None):
            try:
                c = int(self.charge.get())
                m = int(self.mult.get())
            except ValueError:
                self.cm_warning_var.set("⚠️ Charge and Multiplicity must be integers")
                self.cm_warn_frame.pack(fill=tk.X)
                return
                
            xyz_text = self.geom.get("1.0", tk.END).strip()
            if not xyz_text:
                self.cm_warn_frame.pack_forget()
                return

            import re
            atomic_numbers = {
                "H": 1, "HE": 2, "LI": 3, "BE": 4, "B": 5, "C": 6, "N": 7, "O": 8, "F": 9, "NE": 10,
                "NA": 11, "MG": 12, "AL": 13, "SI": 14, "P": 15, "S": 16, "CL": 17, "AR": 18,
                "K": 19, "CA": 20, "SC": 21, "TI": 22, "V": 23, "CR": 24, "MN": 25, "FE": 26,
                "CO": 27, "NI": 28, "CU": 29, "ZN": 30, "GA": 31, "GE": 32, "AS": 33, "SE": 34,
                "BR": 35, "KR": 36, "RB": 37, "SR": 38, "Y": 39, "ZR": 40, "NB": 41, "MO": 42,
                "TC": 43, "RU": 44, "RH": 45, "PD": 46, "AG": 47, "CD": 48, "IN": 49, "SN": 50,
                "SB": 51, "TE": 52, "I": 53, "XE": 54, "CS": 55, "BA": 56, "PT": 78, "AU": 79
            }
            
            sum_z = 0
            lines = xyz_text.split('\n')
            for i, line in enumerate(lines):
                parts = line.split()
                if not parts: continue
                if i == 0 and len(parts) == 1 and parts[0].isdigit():
                    continue
                if len(parts) >= 4:
                    sym = parts[0].upper()
                    sym = re.sub(r'[^A-Z]', '', sym)
                    if sym in atomic_numbers:
                        sum_z += atomic_numbers[sym]
                    else:
                        self.cm_warn_frame.pack_forget()
                        return
                        
            if sum_z == 0:
                self.cm_warn_frame.pack_forget()
                return
                
            n_e = sum_z - c
            if n_e < 0:
                self.cm_warning_var.set("⚠️ Charge is too positive for the loaded atoms.")
                self.cm_warn_frame.pack(fill=tk.X)
                return
                
            if m < 1:
                self.cm_warning_var.set("⚠️ Multiplicity must be ≥ 1")
                self.cm_warn_frame.pack(fill=tk.X)
                return
                
            if (n_e % 2) != ((m - 1) % 2):
                self.cm_warning_var.set(f"⚠️ Invalid! {n_e}e⁻ system requires {'ODD' if n_e%2==0 else 'EVEN'} multiplicity.")
                self.cm_warn_frame.pack(fill=tk.X)
            else:
                self.cm_warn_frame.pack_forget()

        self._validate_charge_mult = _validate_charge_mult
        self.geom.bind("<KeyRelease>", self._validate_charge_mult)
        self.charge.trace_add("write", lambda *args: self._validate_charge_mult())
        self.mult.trace_add("write", lambda *args: self._validate_charge_mult())

        actions_frame = ttk.Frame(left_panel, height=46)
        actions_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        self.left_paned.pack_forget()
        self.left_paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._left_actions_frame = actions_frame

        self.default_actions_frame = ttk.Frame(actions_frame)
        self.default_actions_frame.pack(fill=tk.BOTH, expand=True)

        # Split into two rows so buttons don't get cut off on small screens
        actions_row1 = ttk.Frame(self.default_actions_frame)
        actions_row1.pack(side=tk.TOP, fill=tk.X, expand=True)
        actions_row2 = ttk.Frame(self.default_actions_frame)
        actions_row2.pack(side=tk.TOP, fill=tk.X, expand=True, pady=(2, 0))

        self.load_btn = ttk.Menubutton(actions_row1, text="Load XYZ")
        self.load_menu = tk.Menu(self.load_btn, tearoff=0)
        self.load_menu.add_command(label="Open File...", command=self.load_xyz)
        self.load_menu.add_command(label="Load Current XYZ", command=self.format_current_xyz)
        self.load_btn["menu"] = self.load_menu
        self.load_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        ttk.Button(actions_row1, text="Visualize", command=self._visualize_geometry_from_editor).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        ttk.Button(actions_row1, text="Clear", command=self.clear_all).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        ext_btn = ttk.Menubutton(actions_row2, text="Open External Viewer")
        ext_menu = tk.Menu(ext_btn, tearoff=0)
        ext_menu.add_command(label="Jmol", command=lambda: self.view_external("Jmol"))
        ext_menu.add_command(label="Chemcraft", command=lambda: self.view_external("Chemcraft"))
        ext_menu.add_command(label="Avogadro", command=lambda: self.view_external("Avogadro"))
        ext_menu.add_command(label="GaussView", command=lambda: self.view_external("GaussView"))
        ext_btn["menu"] = ext_menu
        ext_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        ttk.Button(
            actions_row2,
            text="Generate Preview",
            command=self.generate,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        ttk.Button(actions_row2, text="Save Files", command=self.save).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

        self.xtb_actions_frame = ttk.Frame(actions_frame)
        ttk.Button(self.xtb_actions_frame, text="Save xTB files...", command=self._save_xtb_files_to_folder).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2)
        )
        ttk.Button(self.xtb_actions_frame, text="Clear xTB", command=self._clear_xtb_panel).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2)
        )
        ttk.Button(self.xtb_actions_frame, text="Open xTB file...", command=self._open_xtb_file_picker).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0)
        )
        self.btn_open_xtb_folder = ttk.Button(self.xtb_actions_frame, text="Open xTB Folder", command=self._open_xtb_output_folder)
        self.btn_open_xtb_folder.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
        ttk.Button(self.xtb_actions_frame, text="History", command=self._show_xtb_history_dialog).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0)
        )
        xtb_ext_btn = ttk.Menubutton(self.xtb_actions_frame, text="Open xTB output external")
        xtb_ext_menu = tk.Menu(xtb_ext_btn, tearoff=0)
        xtb_ext_menu.add_command(label="Jmol", command=lambda: self._open_xtb_opt_external("Jmol"))
        xtb_ext_menu.add_command(label="Chemcraft", command=lambda: self._open_xtb_opt_external("Chemcraft"))
        xtb_ext_menu.add_command(label="Avogadro", command=lambda: self._open_xtb_opt_external("Avogadro"))
        xtb_ext_menu.add_command(label="GaussView", command=lambda: self._open_xtb_opt_external("GaussView"))
        xtb_ext_btn["menu"] = xtb_ext_menu
        xtb_ext_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

        # -------- RIGHT COLUMN: top = text preview, bottom = embedded Jmol (drag sash) --------
        right_vert = ttk.PanedWindow(right_panel, orient=tk.VERTICAL)
        right_vert.pack(fill=tk.BOTH, expand=True)

        preview_outer = ttk.Frame(right_vert)
        
        self.struct_outer = ttk.LabelFrame(
            right_vert,
            text="Structure visualization",
            padding=2,
        )
        right_vert.add(preview_outer, weight=1)
        right_vert.add(self.struct_outer, weight=1)
        try:
            right_vert.pane(preview_outer, minsize=120)
            right_vert.pane(self.struct_outer, minsize=180)
        except tk.TclError:
            pass

        preview_nb = ttk.Notebook(preview_outer)
        preview_nb.pack(fill=tk.BOTH, expand=True)
        self._preview_notebook = preview_nb

        detach_btn = ttk.Menubutton(preview_outer, text="Detach Previews \u25BC")
        detach_menu = tk.Menu(detach_btn, tearoff=0)
        detach_menu.add_command(label="Detach .inp editor", command=self._open_detailed_orca_inp_dialog_from_preview)
        detach_menu.add_command(label="Detach job script", command=self._open_detailed_sh_dialog_from_preview)
        detach_btn.config(menu=detach_menu)
        detach_btn.place(in_=preview_nb, relx=1.0, rely=0.0, anchor="ne", x=-2, y=1)

        f_inp = ttk.Frame(preview_nb)
        f_sh = ttk.Frame(preview_nb)
        preview_nb.add(f_inp, text="Input (.inp)")
        preview_nb.add(f_sh, text="Job Script (.sh)")

        self.txt_inp = tk.Text(f_inp, font=("Consolas", 12), wrap=tk.NONE)
        self.txt_inp.pack(fill=tk.BOTH, expand=True)

        self.txt_sh = tk.Text(f_sh, font=("Consolas", 12), wrap=tk.NONE)
        self.txt_sh.pack(fill=tk.BOTH, expand=True)

        embed_ctrl_frame = ttk.Frame(self.struct_outer)
        embed_ctrl_frame.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(embed_ctrl_frame, text="Visualization Software:").pack(side=tk.LEFT)
        self.embed_cb = ttk.Combobox(embed_ctrl_frame, textvariable=self.embed_viewer_choice, width=30, state="readonly")
        self.embed_cb.config(postcommand=self._refresh_viewer_options)
        self.embed_cb.pack(side=tk.LEFT, padx=5)
        ttk.Button(embed_ctrl_frame, text="Open", command=lambda: self._embed_viewer(blank=True)).pack(side=tk.LEFT, padx=2)
        self.embed_cb.bind("<<ComboboxSelected>>", self._on_viewer_changed)
        self._refresh_viewer_options()

        if os.name == "nt":
            self.detach_embed_btn = ttk.Button(
                embed_ctrl_frame,
                text="Detach viewer window",
                command=self._toggle_embed_detach,
                state="disabled",
            )
            self.detach_embed_btn.pack(side=tk.RIGHT, padx=(8, 0))



        self.embed_host = tk.Frame(self.struct_outer, bg="#2b2b2b", height=360)
        self.embed_host.pack(fill=tk.BOTH, expand=True)
        self.embed_host.bind("<Configure>", self._resize_embedded)

        self._right_vert = right_vert
        self._left_panel = left_panel
        self.frame.after(250, self._apply_default_layout_on_activate)
        self.frame.focus_set()

    def _refresh_viewer_options(self):
        base_options = ["ACV ( AutoChemyViewer )", "Jmol", "Chemcraft", "Avogadro", "GaussView"]
        custom_softs = [s["name"] for s in SoftwareManager.get_software_by_type("Visualization")]
        seen = set()
        all_options = []
        for opt in base_options + custom_softs:
            if str(opt).strip().lower() in {"avogadro2", "avagadro", "avagadro2"}:
                continue
            key = str(opt).strip().lower()
            if key and key not in seen:
                seen.add(key)
                all_options.append(opt)
        if getattr(self, "embed_cb", None):
            self.embed_cb.config(values=all_options)
        self._try_apply_theme()
        self._refresh_project_list()
        self.exec_mode.trace_add("write", lambda *_: self._update_local_orca_status())
        self.frame.after(120, self._update_local_orca_status)

    def _open_viewer_path_dialog(self):
        try:
            SoftwarePathDialog(self.parent.winfo_toplevel())
        except Exception:
            SoftwarePathDialog(self.parent)

    def _prompt_viewer_path_setup(self, viewer, detail):
        if messagebox.askyesno(
            f"{viewer} path not found",
            f"{detail}\n\nUse Paths > Manage Software Paths... to set or change viewer paths.\n\nOpen it now?",
        ):
            self._open_viewer_path_dialog()

    def _apply_default_layout_on_activate(self):
        # Apply defaults after actual render; module UIs are pre-created before activation.
        for delay in (30, 140, 320):
            self.frame.after(
                delay,
                lambda: (
                    self._sync_actions_bar(),
                    self._balance_main_panes(),
                    self._balance_left_panes(),
                    self._balance_right_panes(),
                    self._balance_xtb_right_panes(),
                    self._apply_beginner_xtb_focus_layout(),
                ),
            )

    def apply_app_theme(self, ctx):
        if not app_theme:
            return
        self._apply_beginner_mode(bool(ctx.get("beginner_mode", True)))
        for name in ("txt_custom", "geom", "txt_inp", "txt_sh", "xtb_out_geom"):
            w = getattr(self, name, None)
            if w is not None:
                app_theme.apply_editor_style(w, ctx)
        numbers_w = getattr(self, "xtb_numbers_txt", None)
        if numbers_w is not None:
            app_theme.apply_editor_style(numbers_w, ctx)
        log_w = getattr(self, "xtb_log_txt", None)
        if log_w is not None:
            try:
                app_theme.apply_editor_style(log_w, ctx)
                p = ctx.get("palette", {})
                log_w.configure(bg=p.get("editor_bg", "#0d1117"), fg="#3fb950")
            except tk.TclError:
                pass
        pe = ctx.get("panel_embed")
        if pe and getattr(self, "embed_host", None):
            try:
                self.embed_host.config(bg=pe)
            except tk.TclError:
                pass

    def _try_apply_theme(self):
        top = self.parent.winfo_toplevel()
        app = getattr(top, "_orca_app", None)
        if app and app_theme:
            self.apply_app_theme(app_theme.build_context(app.theme_mode, app.editor_font_pt))

    def _is_global_beginner_mode(self):
        try:
            app = self.parent.winfo_toplevel()._orca_app
            return bool(getattr(app, "beginner_mode", True))
        except Exception:
            return True

    def _sync_local_mode_with_global(self):
        is_beginner = self._is_global_beginner_mode()
        self.user_mode_var.set("Beginner" if is_beginner else "Experienced")
        if hasattr(self, "_toggle_user_mode_cb"):
            self._toggle_user_mode_cb()
        self._apply_beginner_mode(is_beginner)

    def _apply_beginner_mode(self, is_beginner: bool):
        self.user_mode_var.set("Beginner" if is_beginner else "Experienced")
        if hasattr(self, "_toggle_user_mode_cb"):
            self._toggle_user_mode_cb()
        if hasattr(self, "_xtb_beginner_primary_btn"):
            self._xtb_beginner_primary_btn.config(
                text="▶ Get xTB Preview" if is_beginner else "▶ Run xTB Preview"
            )
        for w in getattr(self, "_xtb_advanced_widgets", ()):
            if w is None:
                continue
            try:
                if hasattr(w, "config"):
                    w.config(state=(tk.DISABLED if is_beginner else tk.NORMAL))
            except Exception:
                pass
        self._apply_beginner_xtb_focus_layout()

    def _refresh_xtb_settings_summary(self):
        pass

    def _open_xtb_settings(self):
        win = tk.Toplevel(self.parent)
        win.title("xTB Settings")
        win.geometry("650x290")
        win.resizable(False, False)
        body = ttk.Frame(win, padding=10)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="xTB Version:").grid(row=0, column=0, sticky="w", pady=4)
        version_cb = ttk.Combobox(body, values=xtb_support.XTB_VERSION_LABELS, state="readonly", width=18)
        current_version = self.xtb_version_choice.get() or xtb_support.default_xtb_version_label()
        if current_version not in xtb_support.XTB_VERSION_LABELS:
            current_version = xtb_support.default_xtb_version_label()
        version_cb.set(current_version)
        version_cb.grid(row=0, column=1, sticky="w", pady=4)

        def _sync_selected_xtb_path(_event=None):
            selected = xtb_support.bundled_xtb_versions().get(version_cb.get(), "")
            if selected:
                self.xtb_exe_path.set(selected)

        version_cb.bind("<<ComboboxSelected>>", _sync_selected_xtb_path)
        _sync_selected_xtb_path()

        ttk.Label(body, text="GFN Level:").grid(row=1, column=0, sticky="w", pady=4)
        gfn_cb = ttk.Combobox(body, values=["2", "1", "0"], state="readonly", width=8)
        gfn_cb.set(self.xtb_gfn_level.get())
        gfn_cb.grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(body, text="Opt Level:").grid(row=2, column=0, sticky="w", pady=4)
        opt_cb = ttk.Combobox(
            body,
            values=["normal", "tight", "vtight", "extreme", "loose", "vloose"],
            state="readonly",
            width=12,
        )
        opt_cb.set(self.xtb_opt_level.get())
        opt_cb.grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(body, text="Energy Unit:").grid(row=3, column=0, sticky="w", pady=4)
        en_cb = ttk.Combobox(body, values=["kcal/mol", "kJ/mol", "Eh"], state="readonly", width=12)
        en_cb.set(self.xtb_energy_unit.get())
        en_cb.grid(row=3, column=1, sticky="w", pady=4)

        ttk.Label(body, text="Solvation Model:").grid(row=4, column=0, sticky="w", pady=4)
        solv_cb = ttk.Combobox(body, values=["gbe", "cosmo", "alpb"], state="readonly", width=12)
        if not hasattr(self, "xtb_solvation_model"): self.xtb_solvation_model = tk.StringVar(value="gbe")
        solv_cb.set(self.xtb_solvation_model.get())
        solv_cb.grid(row=4, column=1, sticky="w", pady=4)

        ttk.Label(body, text="xTB executable:").grid(row=5, column=0, sticky="w", pady=4)
        exe_entry = ttk.Entry(body, textvariable=self.xtb_exe_path, width=58)
        exe_entry.grid(row=5, column=1, columnspan=2, sticky="ew", pady=4, padx=(0, 4))

        def _browse_xtb_exe():
            p = filedialog.askopenfilename(
                parent=win,
                title="Select xtb executable",
                filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
            )
            if p:
                self.xtb_exe_path.set(p)

        ttk.Button(body, text="Browse.", command=_browse_xtb_exe).grid(row=5, column=3, sticky="w", pady=4)
        ttk.Button(body, text="Auto-detect", command=lambda: self.xtb_exe_path.set("")).grid(row=6, column=1, sticky="w", pady=(2, 0))

        def _apply():
            self.xtb_version_choice.set(version_cb.get())
            self.xtb_gfn_level.set(gfn_cb.get())
            self.xtb_opt_level.set(opt_cb.get())
            self.xtb_energy_unit.set(en_cb.get())
            self.xtb_solvation_model.set(solv_cb.get())
            self._refresh_xtb_settings_summary()
            win.destroy()

        ttk.Button(body, text="Apply", command=_apply).grid(row=7, column=0, pady=(12, 0), sticky="w")
        ttk.Button(body, text="Close", command=win.destroy).grid(row=7, column=3, pady=(12, 0), sticky="e")

    def _sync_xtb_solvation_ui(self, *args):
        if not hasattr(self, "xtb_solvation_label"): return
        add_solvent = self.use_solvent.get()
        if self.task.get() == "Single Point" and self.subtask.get() == "With solvent":
            add_solvent = True
            
        solvent_name = getattr(self, "solvent", tk.StringVar()).get().strip()
        if add_solvent and solvent_name:
            self.xtb_solvation_label.pack(side=tk.LEFT, padx=(8, 4))
            self.xtb_solvation_combo.pack(side=tk.LEFT, padx=(0, 12))
        else:
            self.xtb_solvation_label.pack_forget()
            self.xtb_solvation_combo.pack_forget()
            self.xtb_include_solvation.set("No")

    def _on_main_tab_changed(self, _event=None):
        self._sync_actions_bar()
        self._apply_beginner_xtb_focus_layout()
        self._refresh_embedded_viewers_after_tab_change()
        if hasattr(self, "_sync_xtb_solvation_ui"):
            self._sync_xtb_solvation_ui()

    def _is_xtb_tab_selected(self):
        nb = getattr(self, "_main_notebook", None)
        xtb_tab = getattr(self, "_tab_xtb", None)
        if nb is None or xtb_tab is None:
            return False
        try:
            return str(nb.select()) == str(xtb_tab)
        except Exception:
            return False

    def _balance_left_panes(self):
        left_pw = getattr(self, "left_paned", None)
        if not left_pw:
            return
        
        try:
            left_pw.update_idletasks()
            total_h = left_pw.winfo_height()
            if total_h > 100:
                is_xtb = self._is_xtb_tab_selected()
                panes = left_pw.panes()
                
                # If only one pane (xTB mode or geometry missing), just expand it
                if len(panes) == 1:
                    left_pw.pane(panes[0], weight=1)
                    return
                    
                if len(panes) >= 2 and not is_xtb:
                    # Normal mode: notebook on top, geometry on bottom
                    geom_h = 160
                    left_pw.sashpos(0, max(100, total_h - geom_h))
        except Exception:
            pass

    def _balance_main_panes(self):
        main_pw = getattr(self, "_main_container", None)
        if not main_pw:
            return
        
        try:
            main_pw.update_idletasks()
            total_w = main_pw.winfo_width()
            if total_w > 100:
                is_xtb = self._is_xtb_tab_selected()
                left_panel = getattr(self, "_left_panel", None)
                right_panel = getattr(self, "_right_panel", None)
                if left_panel and str(left_panel) in main_pw.panes():
                    main_pw.pane(left_panel, minsize=400)
                if not is_xtb and right_panel and str(right_panel) in main_pw.panes():
                    # Set right panel minsize
                    main_pw.pane(right_panel, minsize=450)
                    if len(main_pw.panes()) >= 2:
                        main_pw.sashpos(0, max(400, total_w - 500))
        except Exception:
            pass

    def _apply_beginner_xtb_focus_layout(self):
        main_pw = getattr(self, "_main_container", None)
        right_panel = getattr(self, "_right_panel", None)
        
        if not self._is_xtb_tab_selected():
            if main_pw and right_panel:
                if str(right_panel) not in main_pw.panes():
                    main_pw.add(right_panel, weight=1)
            self._balance_main_panes()
            self._balance_left_panes()
            self._balance_right_panes()
            self._sync_actions_bar()
            return
            
        try:
            if main_pw is not None and right_panel is not None:
                if str(right_panel) in main_pw.panes():
                    main_pw.forget(right_panel)
            
            left_pw = getattr(self, "left_paned", None)
            if left_pw is not None:
                h = left_pw.winfo_height()
                if h > 100:
                    try: left_pw.sashpos(0, h - 56)
                    except tk.TclError: pass
                    try: left_pw.sashpos(1, h - 46)
                    except tk.TclError: pass
        except (tk.TclError, AttributeError):
            pass

    def _refresh_embedded_viewers_after_tab_change(self):
        # Embedded external windows can appear blank after pane/tab switches until resized/re-shown.
        def _refresh_main():
            if getattr(self, "_viewer_detached", False):
                return
            try:
                if self.embed_hwnd and os.name == "nt" and ctypes.windll.user32.IsWindow(self.embed_hwnd):
                    ctypes.windll.user32.ShowWindow(self.embed_hwnd, 5)  # SW_SHOW
                    self._resize_embedded()
            except Exception:
                pass

        for delay in (60, 220, 520):
            self.frame.after(delay, _refresh_main)

    def _sync_actions_bar(self):
        is_xtb = self._is_xtb_tab_selected()
        is_beginner = self._is_global_beginner_mode()
        
        if hasattr(self, "left_paned") and hasattr(self, "_left_geom_frame"):
            if is_xtb:
                if str(self._left_geom_frame) in self.left_paned.panes():
                    self.left_paned.forget(self._left_geom_frame)
            else:
                # Insert at the end, so it goes BELOW the top left notebook
                if str(self._left_geom_frame) not in self.left_paned.panes():
                    self.left_paned.insert("end", self._left_geom_frame, weight=2)
                else:
                    # If it's already in the paned window but at the wrong position, move it
                    current_panes = self.left_paned.panes()
                    if len(current_panes) > 1 and current_panes[-1] != str(self._left_geom_frame):
                        self.left_paned.insert("end", self._left_geom_frame, weight=2)
                
        if getattr(self, "default_actions_frame", None) is None or getattr(self, "xtb_actions_frame", None) is None:
            return
            
        if is_xtb: 
            self.default_actions_frame.pack_forget()
            self.xtb_actions_frame.pack(fill=tk.BOTH, expand=True)
        else:
            self.xtb_actions_frame.pack_forget()
            self.default_actions_frame.pack(fill=tk.BOTH, expand=True)

    def _clear_xtb_panel(self):
        self.xtb_log_txt.delete("1.0", tk.END)
        self.xtb_out_geom.delete("1.0", tk.END)
        for w in self.xtb_scan_plot_host.winfo_children():
            w.destroy()
        self.xtb_scan_plot_title.config(text="Scan graph will appear here after run.")
        if getattr(self, "xtb_numbers_txt", None) is not None:
            self.xtb_numbers_txt.config(state=tk.NORMAL)
            self.xtb_numbers_txt.delete("1.0", tk.END)
            self.xtb_numbers_txt.insert("1.0", "xTB panel cleared.\n")
            self.xtb_numbers_txt.config(state=tk.DISABLED)

    def _save_xtb_files_to_folder(self):
        folder = getattr(self, "_last_xtb_run_folder", None)
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("xTB files", "No xTB output folder available yet.")
            return
        target = filedialog.askdirectory(
            title="Select folder to save xTB files",
            initialdir=self._safe_initial_dir(self._last_xtb_export_dir or folder),
        )
        if not target:
            return
        self._last_xtb_export_dir = target
        try:
            copied = 0
            for fn in os.listdir(folder):
                src = os.path.join(folder, fn)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(target, fn))
                    copied += 1
            messagebox.showinfo("xTB files", f"Copied {copied} file(s) to:\n{target}")
        except Exception as e:
            messagebox.showerror("xTB files", str(e))

    def _pick_xtb_output_file(self, folder):
        p = self._xtb_popen_holder[0]
        run_active = bool(p and p.poll() is None)
        for name in (
            "xtbopt.xyz",
            "xtblast.xyz",
            "input.xyz",
            "xtbopt.log",
            "xtbscan.log",
            "xtb_full.log",
            "res.out",
            "g98.out",
        ):
            if run_active and name.lower() == "g98.out":
                # g98.out may still be incomplete while xTB is running.
                continue
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                return p
        return None

    def _open_xtb_file_picker(self):
        folder = getattr(self, "_last_xtb_run_folder", None)
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("xTB file", "No xTB output folder found yet.")
            return
        path = filedialog.askopenfilename(
            parent=self.parent,
            title="Open xTB output file",
            initialdir=self._safe_initial_dir(self._last_xtb_open_file_path or folder),
            filetypes=[
                ("xTB files", "*.xyz *.log *.out *.inp *.txt"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        p = self._xtb_popen_holder[0]
        run_active = bool(p and p.poll() is None)
        if run_active and os.path.basename(path).lower() == "g98.out":
            messagebox.showinfo("xTB file", "g98.out is still being written in scratch. Please open it after the run finishes.")
            return
        self._last_xtb_open_file_path = path
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined, unused-ignore]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("xTB file", str(e))

    def _open_xtb_opt_external(self, viewer=None):
        folder = getattr(self, "_last_xtb_run_folder", None)
        if not folder:
            messagebox.showinfo("xTB output", "No recent xTB run folder found.")
            return
        target = self._pick_xtb_output_file(folder)
        if not target:
            messagebox.showinfo("xTB output", "No xTB output file found.")
            return

        selected = (viewer or self.embed_viewer_choice.get() or "Jmol").strip()
        selected_norm = selected.lower()
        try:
            if selected_norm == "chemcraft":
                exe = self._find_chemcraft_exe()
                if not exe:
                    self._prompt_viewer_path_setup("Chemcraft", "Chemcraft not found.")
                    return
                self._popen_viewer_exe(exe, target)
                return
            if selected_norm.startswith("avogadro"):
                exe = self._find_avogadro_exe()
                if not exe:
                    self._prompt_viewer_path_setup("Avogadro", "Avogadro not found.")
                    return
                self._popen_viewer_exe(exe, target)
                return
            if selected_norm == "gaussview":
                exe = self._find_gaussview_exe()
                if not exe:
                    self._prompt_viewer_path_setup("GaussView", "GaussView not found.")
                    return
                self._popen_viewer_exe(exe, target)
                return

            cmd = self._find_jmol_command(target)
            if cmd:
                subprocess.Popen(cmd)
                return
            self._prompt_viewer_path_setup("Jmol", "Jmol not found.")
        except Exception as e:
            messagebox.showerror("xTB output", str(e))


    def _balance_right_panes(self):
        try:
            h = self._right_vert.winfo_height()
            if h > 60:
                # Match requested default: preview smaller, viewer larger
                self._right_vert.sashpos(0, max(int(h * 0.38), 120))
        except (tk.TclError, AttributeError):
            pass


    def _balance_xtb_right_panes(self):
        try:
            pw = getattr(self, "xtb_right_vert", None)
            if pw is None:
                return
            h = pw.winfo_height()
            if h > 80:
                # xTB output (30%) + graph/visualization (70%)
                pw.sashpos(0, max(int(h * 0.30), 90))
            left_pw = getattr(self, "xtb_left_vert", None)
            if left_pw is not None:
                lh = left_pw.winfo_height()
                if lh > 80:
                    # log smaller, numbers larger
                    left_pw.sashpos(0, max(int(lh * 0.38), 80))
        except (tk.TclError, AttributeError):
            pass

    # ---------------- PROJECTS (saved presets) ----------------
    def _project_json_path(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "AutoChemy_User_Data", "input_creator_5_projects.json")

    def _project_store_read(self):
        path = self._project_json_path()
        if not os.path.isfile(path):
            return {"version": 2, "projects": {}, "order": [], "suborder": {}}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"version": 2, "projects": {}, "order": [], "suborder": {}}
            projects = data.get("projects")
            if not isinstance(projects, dict):
                return {"version": 2, "projects": {}, "order": [], "suborder": {}}

            # Backward compatibility: old flat format was {projects: {name: snapshot}}
            nested = {}
            saw_flat_snapshot = False
            for parent_name, payload in projects.items():
                if isinstance(payload, dict) and payload.get("version") == 1:
                    saw_flat_snapshot = True
                    continue
                if isinstance(payload, dict):
                    sub_map = {}
                    for sub_name, snap in payload.items():
                        if isinstance(snap, dict) and snap.get("version") == 1:
                            sub_map[str(sub_name)] = snap
                    if sub_map:
                        nested[str(parent_name)] = sub_map
            if saw_flat_snapshot:
                default_parent = "Default"
                nested.setdefault(default_parent, {})
                for old_name, snap in projects.items():
                    if isinstance(snap, dict) and snap.get("version") == 1:
                        nested[default_parent][str(old_name)] = snap

            order = [str(x) for x in (data.get("order") or []) if str(x) in nested]
            for p in sorted(nested.keys()):
                if p not in order:
                    order.append(p)

            suborder_blob = data.get("suborder") if isinstance(data.get("suborder"), dict) else {}
            suborder = {}
            for p, subs in nested.items():
                pref = [str(x) for x in (suborder_blob.get(p) or []) if str(x) in subs]
                for s in sorted(subs.keys()):
                    if s not in pref:
                        pref.append(s)
                suborder[p] = pref

            return {"version": 2, "projects": nested, "order": order, "suborder": suborder}
        except Exception:
            return {"version": 2, "projects": {}, "order": [], "suborder": {}}

    def _project_store_write(self, data):
        path = self._project_json_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            messagebox.showerror("Projects", f"Could not save project file:\n{e}")

    def _refresh_project_list(self, select_name=None, select_sub=None):
        if not getattr(self, "project_combo", None):
            return
        data = self._project_store_read()
        parents = list(data.get("order") or [])
        if not parents:
            parents = sorted(data["projects"].keys())
        names = [self._project_combo_placeholder] + parents
        self.project_combo["values"] = names
        if select_name and select_name in parents:
            self.project_choice_var.set(select_name)
        else:
            self.project_choice_var.set(self._project_combo_placeholder)
        self._refresh_subproject_list(selected_parent=select_name, select_sub=select_sub)

    def _refresh_subproject_list(self, selected_parent=None, select_sub=None):
        if not getattr(self, "subproject_combo", None):
            return
        data = self._project_store_read()
        parent = selected_parent or self.project_choice_var.get()
        sub_names = []
        if parent and parent != self._project_combo_placeholder:
            sub_map = data["projects"].get(parent) if isinstance(data["projects"].get(parent), dict) else {}
            pref = list((data.get("suborder") or {}).get(parent) or [])
            seen = set()
            for n in pref:
                if n in sub_map and n not in seen:
                    sub_names.append(n)
                    seen.add(n)
            for n in sorted(sub_map.keys()):
                if n not in seen:
                    sub_names.append(n)
                    seen.add(n)
        self.subproject_combo["values"] = [self._subproject_combo_placeholder] + sub_names
        if select_sub and select_sub in sub_names:
            self.subproject_choice_var.set(select_sub)
        else:
            self.subproject_choice_var.set(self._subproject_combo_placeholder)

    def _on_parent_project_selected(self, event=None):
        self._refresh_subproject_list(selected_parent=self.project_choice_var.get())

    def _collect_project_snapshot(self):
        constraints = []
        for cv in getattr(self, "constraint_rows", None) or []:
            try:
                constraints.append(
                    {
                        "type": cv["type"].get(),
                        "a1": cv["a1"].get(),
                        "a2": cv["a2"].get(),
                        "a3": cv["a3"].get(),
                        "a4": cv["a4"].get(),
                    }
                )
            except Exception:
                pass
        splits = []
        for r in getattr(self, "split_rows", None) or []:
            try:
                splits.append(
                    {
                        "target": r["target"].get(),
                        "cmd": r["cmd"].get(),
                        "val": r["val"].get(),
                    }
                )
            except Exception:
                pass

        var_keys = (
            "task",
            "subtask",
            "goat_mode",
            "goat_nprocs_group",
            "crest_mode",
            "crest_gfn_level",
            "crest_ewin",
            "crest_temp",
            "crest_threads",
            "crest_solvent_model",
            "crest_solvent",
            "crest_extra_args",
            "method_type",
            "method_sub_type",
            "freq_type",
            "scan_a1",
            "scan_a2",
            "scan_a3",
            "scan_a4",
            "scan_start",
            "scan_end",
            "scan_steps",
            "scan_ctype",
            "other_cmd",
            "func_family",
            "method",
            "basis",
            "semi_method",
            "charge",
            "mult",
            "filename",
            "embed_viewer_choice",
            "dispersion",
            "ri_approx",
            "aux_basis",
            "grid_size",
            "scf_acc",
            "scf_maxiter",
            "geom_maxiter",
            "temp_c",
            "temp_k",
            "ts_mode",
            "neb_nimages",
            "neb_product_path",
            "moinp_file",
            "qro_file",
            "hess_file",
            "solvent",
            "exec_mode",
            "queue",
            "nprocs",
            "nodes",
            "time",
            "memory",
            "scratch_dir",
            "mpi_module",
            "orca_module",
            "orca_path",
            "mpi_path",
            "spin_state",
            "user_mode_var",
            "xtb_opt_level",
            "xtb_gfn_level",
            "xtb_method",
            "xtb_version_choice",
            "xtb_exe_path",
        )
        vars_blob = {}
        for key in var_keys:
            v = getattr(self, key, None)
            if v is not None and hasattr(v, "get"):
                try:
                    vars_blob[key] = v.get()
                except Exception:
                    pass
        bool_keys = ("qro_gen", "use_solvent", "prop_polar", "prop_nmr", "split_basis_var")
        bool_blob = {}
        for key in bool_keys:
            v = getattr(self, key, None)
            if v is not None and hasattr(v, "get"):
                try:
                    bool_blob[key] = bool(v.get())
                except Exception:
                    pass
        paths_blob = {
            "last_xyz_open_path": getattr(self, "_last_xyz_open_path", "") or "",
            "last_input_save_path": getattr(self, "_last_input_save_path", "") or "",
            "last_gbw_path": getattr(self, "_last_gbw_path", "") or "",
            "last_hess_path": getattr(self, "_last_hess_path", "") or "",
            "last_xtb_open_file_path": getattr(self, "_last_xtb_open_file_path", "") or "",
            "last_xtb_export_dir": getattr(self, "_last_xtb_export_dir", "") or "",
            "last_neb_path": getattr(self, "_last_neb_path", "") or "",
        }
        return {
            "version": 1,
            "vars": vars_blob,
            "bools": bool_blob,
            "paths": paths_blob,
            "neb_product_coords": getattr(self, "_neb_product_coords_text", "") or "",
            "geom": self.geom.get("1.0", tk.END),
            "inp_preview": self.txt_inp.get("1.0", tk.END),
            "sh_preview": self.txt_sh.get("1.0", tk.END),
            "constraints": constraints,
            "split_rows": splits,
        }

    def _apply_project_snapshot(self, snap):
        if not isinstance(snap, dict) or snap.get("version") != 1:
            raise ValueError("Invalid project data")

        vb = snap.get("vars") or {}
        for key, val in vb.items():
            v = getattr(self, key, None)
            if v is not None and hasattr(v, "set"):
                try:
                    v.set(val)
                except Exception:
                    pass
        bb = snap.get("bools") or {}
        for key, val in bb.items():
            v = getattr(self, key, None)
            if v is not None and hasattr(v, "set"):
                try:
                    v.set(val)
                except Exception:
                    pass
        pb = snap.get("paths") or {}
        self._last_xyz_open_path = str(pb.get("last_xyz_open_path", "") or "")
        self._last_input_save_path = str(pb.get("last_input_save_path", "") or "")
        self._last_gbw_path = str(pb.get("last_gbw_path", "") or "")
        self._last_hess_path = str(pb.get("last_hess_path", "") or "")
        self._last_xtb_open_file_path = str(pb.get("last_xtb_open_file_path", "") or "")
        self._last_xtb_export_dir = str(pb.get("last_xtb_export_dir", "") or "")
        self._last_neb_path = str(pb.get("last_neb_path", "") or "")
        self._neb_product_coords_text = str(snap.get("neb_product_coords", "") or "")

        self.geom.delete("1.0", tk.END)
        self.geom.insert("1.0", snap.get("geom", ""))
        self.txt_inp.delete("1.0", tk.END)
        self.txt_inp.insert("1.0", snap.get("inp_preview", ""))
        self.txt_sh.delete("1.0", tk.END)
        self.txt_sh.insert("1.0", snap.get("sh_preview", ""))

        for cv in list(getattr(self, "constraint_rows", None) or []):
            try:
                cv["frame"].destroy()
            except Exception:
                pass
        self.constraint_rows = []

        for r in list(getattr(self, "split_rows", None) or []):
            try:
                r["frame"].destroy()
            except Exception:
                pass
        self.split_rows = []

        if hasattr(self, "_refresh_theory_ui"):
            self._refresh_theory_ui()
        if hasattr(self, "_refresh_task_ui"):
            self._refresh_task_ui()

        add = getattr(self, "_add_constraint_ui", None)
        if callable(add):
            for row in snap.get("constraints") or []:
                add()
                if not self.constraint_rows:
                    break
                cv = self.constraint_rows[-1]
                cv["type"].set(row.get("type", "Bond"))
                cv["a1"].set(row.get("a1", ""))
                cv["a2"].set(row.get("a2", ""))
                cv["a3"].set(row.get("a3", ""))
                cv["a4"].set(row.get("a4", ""))
                try:
                    kids = cv["frame"].winfo_children()
                    if kids and isinstance(kids[0], ttk.Combobox):
                        kids[0].event_generate("<<ComboboxSelected>>")
                except Exception:
                    pass

        add_split = getattr(self, "_add_split_row_cb", None)
        if hasattr(self, "split_switch"):
            self.split_switch.set_state(bool(self.split_basis_var.get()))
        if callable(add_split):
            for row in snap.get("split_rows") or []:
                add_split(
                    target=row.get("target", ""),
                    cmd_type=row.get("cmd", "newgto"),
                    val=row.get("val", "def2-TZVP"),
                )
        if hasattr(self, "_toggle_split_basis_cb"):
            self._toggle_split_basis_cb()

        try:
            self.btn_select_functional.config(text=f"{self.method.get()} ▾")
            self.btn_select_basis.config(text=f"{self.basis.get()} ▾")
        except Exception:
            pass

        if hasattr(self, "qro_switch"):
            self.qro_switch.set_state(bool(self.qro_gen.get()))
        if hasattr(self, "user_switch"):
            self.user_switch.set_state(self.user_mode_var.get() == "Experienced")
        if hasattr(self, "_toggle_user_mode_cb"):
            self._toggle_user_mode_cb()

        if hasattr(self, "_refresh_approx_warnings"):
            self._refresh_approx_warnings()
        try:
            self._validate_charge_mult()
        except Exception:
            pass

    def _on_project_selected(self, event=None):
        parent_name = self.project_choice_var.get()
        sub_name = self.subproject_choice_var.get() if hasattr(self, "subproject_choice_var") else ""
        if not parent_name or parent_name == self._project_combo_placeholder:
            return
        if not sub_name or sub_name == self._subproject_combo_placeholder:
            return
        data = self._project_store_read()
        proj = (data["projects"].get(parent_name) or {}).get(sub_name)
        if not proj:
            messagebox.showwarning("Projects", "That sub-project was not found.")
            self._refresh_project_list(select_name=parent_name)
            return
        try:
            self._terminate_embed_subprocess()
            self._apply_project_snapshot(proj)
            if self.geom.get("1.0", tk.END).strip():
                self.frame.after(150, self._embed_viewer)
            self._show_toast(f"Loaded project: {parent_name} / {sub_name}", 2500)
            self._update_embed_detach_button_state()
        except Exception as e:
            messagebox.showerror("Projects", f"Could not load project:\n{e}")

    def _save_project_interactive(self):
        data = self._project_store_read()
        parents = list(data.get("order") or [])
        if not parents:
            parents = sorted((data.get("projects") or {}).keys())

        current_parent = self.project_choice_var.get()
        if not current_parent or current_parent == self._project_combo_placeholder:
            current_parent = parents[0] if parents else ""
        current_sub = self.subproject_choice_var.get() if hasattr(self, "subproject_choice_var") else ""
        if not current_sub or current_sub == self._subproject_combo_placeholder:
            current_sub = ""

        win = tk.Toplevel(self.parent)
        win.title("Save Project Snapshot")
        win.transient(self.parent)
        win.grab_set()
        win.geometry("560x430")

        body = ttk.Frame(win, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            body,
            text="Select existing project/sub-project or type new names:",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(body)
        row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(row, text="Project:", width=10).pack(side=tk.LEFT)
        parent_var = tk.StringVar(value=current_parent)
        parent_cb = ttk.Combobox(row, textvariable=parent_var, values=parents, state="normal", width=26)
        parent_cb.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(row, text="Sub-project:", width=10).pack(side=tk.LEFT)
        sub_var = tk.StringVar(value=current_sub)
        sub_cb = ttk.Combobox(row, textvariable=sub_var, values=[], state="normal", width=26)
        sub_cb.pack(side=tk.LEFT)

        preview = tk.Listbox(body, height=14, font=("Consolas", 9))
        preview.pack(fill=tk.BOTH, expand=True, pady=(8, 8))

        def _refresh_preview():
            preview.delete(0, tk.END)
            fresh = self._project_store_read()
            pnames = list(fresh.get("order") or [])
            if not pnames:
                pnames = sorted((fresh.get("projects") or {}).keys())
            for p in pnames:
                preview.insert(tk.END, f"{p}/")
                subs = list((fresh.get("suborder") or {}).get(p) or [])
                pmap = (fresh.get("projects") or {}).get(p) or {}
                seen = set(subs)
                for s in sorted(pmap.keys()):
                    if s not in seen:
                        subs.append(s)
                for s in subs:
                    preview.insert(tk.END, f"  - {s}")

        def _refresh_sub_values(*_):
            fresh = self._project_store_read()
            p = (parent_var.get() or "").strip()
            pmap = (fresh.get("projects") or {}).get(p) if p else {}
            if not isinstance(pmap, dict):
                pmap = {}
            subs = list((fresh.get("suborder") or {}).get(p) or [])
            seen = set(subs)
            for s in sorted(pmap.keys()):
                if s not in seen:
                    subs.append(s)
            sub_cb["values"] = subs

        def _do_save():
            parent_name = (parent_var.get() or "").strip()
            sub_name = (sub_var.get() or "").strip()
            if not parent_name:
                messagebox.showwarning("Projects", "Enter a project name.", parent=win)
                return
            if not sub_name:
                messagebox.showwarning("Projects", "Enter a sub-project name.", parent=win)
                return
            if self._save_project_snapshot(parent_name, sub_name, parent_for_msg=win):
                win.destroy()

        parent_cb.bind("<<ComboboxSelected>>", _refresh_sub_values)
        parent_cb.bind("<KeyRelease>", _refresh_sub_values)
        _refresh_sub_values()
        _refresh_preview()

        btns = ttk.Frame(body)
        btns.pack(fill=tk.X, pady=(2, 0))
        ttk.Button(btns, text="Save", command=_do_save).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side=tk.RIGHT, padx=(0, 8))

    def _save_project_snapshot(self, parent_name, sub_name, parent_for_msg=None):
        data = self._project_store_read()
        data.setdefault("projects", {})
        data.setdefault("order", [])
        data.setdefault("suborder", {})
        data["projects"].setdefault(parent_name, {})
        data["suborder"].setdefault(parent_name, [])

        if sub_name in data["projects"][parent_name]:
            if not messagebox.askyesno(
                "Overwrite",
                f"Sub-project '{parent_name} / {sub_name}' already exists.\nDo you want to overwrite it?",
                parent=parent_for_msg or self.parent,
            ):
                return False
        try:
            snap = self._collect_project_snapshot()
        except Exception as e:
            messagebox.showerror("Projects", f"Could not read current settings:\n{e}", parent=parent_for_msg or self.parent)
            return False

        data["projects"][parent_name][sub_name] = snap
        if parent_name not in data["order"]:
            data["order"].append(parent_name)
        if sub_name not in data["suborder"][parent_name]:
            data["suborder"][parent_name].append(sub_name)
        self._project_store_write(data)
        self._refresh_project_list(select_name=parent_name, select_sub=sub_name)
        self._show_toast(f"Saved project: {parent_name} / {sub_name}", 2500)
        return True

    def _delete_project_interactive(self):
        parent_name = self.project_choice_var.get()
        sub_name = self.subproject_choice_var.get() if hasattr(self, "subproject_choice_var") else ""
        if not parent_name or parent_name == self._project_combo_placeholder:
            messagebox.showinfo("Projects", "Select a parent project first.")
            return
        if not sub_name or sub_name == self._subproject_combo_placeholder:
            messagebox.showinfo("Projects", "Select the sub-project to delete.")
            return
        if not messagebox.askyesno(
            "Delete sub-project",
            f"Delete “{parent_name} / {sub_name}” permanently?\n\nThis action is irreversible.",
        ):
            return
        data = self._project_store_read()
        parent_map = data.get("projects", {}).get(parent_name)
        if isinstance(parent_map, dict):
            parent_map.pop(sub_name, None)
            if not parent_map:
                data["projects"].pop(parent_name, None)
                data["order"] = [x for x in data.get("order", []) if x != parent_name]
                if isinstance(data.get("suborder"), dict):
                    data["suborder"].pop(parent_name, None)
            else:
                if isinstance(data.get("suborder"), dict):
                    data["suborder"][parent_name] = [
                        x for x in data["suborder"].get(parent_name, []) if x != sub_name
                    ]
        self._project_store_write(data)
        self._refresh_project_list(select_name=parent_name)
        self._show_toast("Sub-project deleted.", 2000)

    def _delete_whole_project_interactive(self):
        parent_name = self.project_choice_var.get()
        if not parent_name or parent_name == self._project_combo_placeholder:
            messagebox.showinfo("Projects", "Select a project to delete.")
            return
        if not messagebox.askyesno(
            "Delete whole project",
            f"You are deleting the whole project '{parent_name}'.\nThis will remove all sub-projects inside it.\n\nContinue?",
        ):
            return
        if not messagebox.askyesno(
            "Final irreversible warning",
            f"FINAL WARNING:\nDelete '{parent_name}' and all its sub-projects permanently?\n\nThis action is irreversible.",
        ):
            return
        data = self._project_store_read()
        data.get("projects", {}).pop(parent_name, None)
        data["order"] = [x for x in data.get("order", []) if x != parent_name]
        if isinstance(data.get("suborder"), dict):
            data["suborder"].pop(parent_name, None)
        self._project_store_write(data)
        self._refresh_project_list()
        self._show_toast("Whole project deleted.", 2200)

    # ---------------- LOGIC ----------------
    def _show_toast(self, msg, duration_ms=4000):
        try:
            toast = tk.Toplevel(self.parent)
            toast.overrideredirect(True)
            toast.attributes('-topmost', True)
            ttk.Label(toast, text=msg, font=("Segoe UI", 11, "bold"), background="#FFF3CD", foreground="#333", padding=8).pack()
            toast.update_idletasks()
            w = toast.winfo_width()
            h = toast.winfo_height()
            x = self.parent.winfo_rootx() + (self.parent.winfo_width() // 2) - (w // 2)
            y = self.parent.winfo_rooty() + (self.parent.winfo_height() // 2) - (h // 2)
            toast.geometry(f"+{x}+{y}")
            self.parent.after(duration_ms, toast.destroy)
            self.frame.update_idletasks()
        except Exception:
            pass

    def clear_all(self):
        self.geom.delete("1.0", tk.END)
        self.filename.set("orca_job")
        self._terminate_embed_subprocess()
        self.txt_inp.delete("1.0", tk.END)
        self.txt_sh.delete("1.0", tk.END)
        try:
            self.struct_outer.config(text="Structure visualization")
            for w in self.embed_host.winfo_children():
                w.destroy()
            self.embed_host.config(bg="#2b2b2b")
            self.embed_host.update_idletasks()
            self.embed_host.config(height=360)
            tk.Label(self.embed_host, text="No structure loaded", bg="#2b2b2b", fg="#777777", font=("Segoe UI", 16)).place(relx=0.5, rely=0.5, anchor="center")
        except AttributeError:
            pass
        self._update_embed_detach_button_state()

    def _update_functionals(self, event=None):
        fam = self.func_family.get()
        if fam in self.FUNCTIONALS:
            self.func_cb['values'] = self.FUNCTIONALS[fam]
            self.method.set(self.FUNCTIONALS[fam][0])

    def _auto_recommend_spin(self):
        geom_text = self.geom.get("1.0", tk.END).strip()
        if not geom_text:
            messagebox.showwarning("Missing Geometry", "Please paste or load an XYZ coordinate geometry first.")
            return
            
        rows = _geom_lines_to_coord_rows(geom_text)
        if not rows:
            messagebox.showwarning("Parse Error", "Could not read atomic symbols from the geometry window. Make sure it's valid XYZ text.")
            return
            
        try:
            chg = int(self.charge.get().strip() or "0")
            mult = int(self.mult.get().strip() or "1")
        except ValueError:
            messagebox.showerror("Format Error", "Charge and Multiplicity must be integers.")
            return
            
        total_electrons = 0
        for row in rows:
            sym = row[0].strip().upper()
            total_electrons += PERIODIC_TABLE.get(sym, 0)
            
        if total_electrons == 0:
            messagebox.showwarning("Unknown Atoms", "Atoms could not be recognized against the periodic table list.")
            return
            
        total_electrons -= chg
        
        # Check for open-shell transition metals (excluding fully filled like Pd, Zn, Cd, Hg)
        TM_OPEN = {
            "SC", "TI", "V", "CR", "MN", "FE", "CO", "NI", "CU",
            "Y", "ZR", "NB", "MO", "TC", "RU", "RH", "AG",
            "LA", "CE", "PR", "ND", "PM", "SM", "EU", "GD", "TB", "DY", "HO", "ER", "TM", "YB", "LU",
            "HF", "TA", "W", "RE", "OS", "IR", "PT", "AU",
            "AC", "TH", "PA", "U", "NP", "PU", "AM", "CM", "BK", "CF", "ES", "FM", "MD", "NO", "LR"
        }
        has_open_d_metal = any(row[0].strip().upper() in TM_OPEN for row in rows)
        
        msg = f"--- System Analysis ---\nTotal Electrons: {total_electrons}\nCharge: {chg}\nMultiplicity: {mult}\n\n"
        
        if total_electrons % 2 != 0 and mult % 2 != 0:
            messagebox.showerror("Spin Parity ERROR", msg + "⚠️ An odd number of electrons must have an even multiplicity (Doublet, Quartet, etc.). Please reconsider your input!")
            return
        if total_electrons % 2 == 0 and mult % 2 == 0:
            messagebox.showerror("Spin Parity ERROR", msg + "⚠️ An even number of electrons must have an odd multiplicity (Singlet, Triplet, etc.). Please reconsider your input!")
            return
            
        th = self.method_type.get()
        is_dft = (th == "DFT")
        
        if total_electrons % 2 == 0:
            if mult == 1:
                if has_open_d_metal:
                    rec_title = "Recommendation: Broken-Symmetry UKS (Unrestricted)"
                    rec_body = "REASON: You have a singlet state, but your system contains a transition metal with potentially unpaired d-electrons. An open-shell approach (UKS) is recommended to properly describe potential multi-reference or antiferromagnetic character.\n\nStart with UKS and check the <S^2> value!"
                    s_val = "UKS     # unrestricted open-shell (UKS for DFT)" if is_dft else "UHF     # unrestricted open-shell"
                else:
                    rec_title = "Recommendation: RHF / RKS (Restricted closed-shell)"
                    rec_body = "REASON: You have an even number of electrons and a singlet state (multiplicity = 1). All electrons are perfectly paired. A closed-shell restricted model is the most physically accurate and computationally efficient choice."
                    s_val = "RKS     # closed-shell (RKS for DFT)" if is_dft else "RHF     # closed-shell"
            else:
                rec_title = "Recommendation: UHF / UKS (Unrestricted) -> evaluate -> ROHF/ROKS if necessary"
                rec_body = "REASON: You have an even number of electrons, but a high-spin state (like a Triplet). Because there are unpaired electrons, you must use an open-shell method. Start with UHF/UKS, but explicitly check the <S^2> value in your output!\n\nIf spin contamination is high, rerun using restricted open-shell."
                s_val = "UKS     # unrestricted open-shell (UKS for DFT)" if is_dft else "UHF     # unrestricted open-shell"
        else:
            rec_title = "Recommendation: UHF / UKS (Unrestricted) -> evaluate -> ROHF/ROKS if necessary"
            rec_body = "REASON: You have an odd number of electrons (a radical). You must use an open-shell method to allow the unpaired electron to exist alone. Start with UHF/UKS.\n\nIf the <S^2> value deviates by more than ~10% from the ideal value, switch to restricted open-shell to eliminate spin contamination."
            s_val = "UKS     # unrestricted open-shell (UKS for DFT)" if is_dft else "UHF     # unrestricted open-shell"
            
        if th in ["HF", "DFT", "MP", "CCSD", "Semiempirical"]:
            self.spin_state.set(s_val)
            
        messagebox.showinfo(rec_title, msg + rec_body)

    def _safe_initial_dir(self, path_value):
        p = (path_value or "").strip()
        if not p:
            return None
        if os.path.isdir(p):
            return p
        d = os.path.dirname(p)
        return d if d and os.path.isdir(d) else None

    def _detect_local_machine_resources(self):
        logical = os.cpu_count() or 4
        mem_mb = 8192
        try:
            if os.name == "nt":
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]
                ms = MEMORYSTATUSEX()
                ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                    mem_mb = int(ms.ullTotalPhys / (1024 * 1024))
            elif os.path.isfile("/proc/meminfo"):
                with open("/proc/meminfo", "r", encoding="utf-8", errors="ignore") as f:
                    txt = f.read()
                m = re.search(r"MemTotal:\s+(\d+)\s+kB", txt)
                if m:
                    mem_mb = int(int(m.group(1)) / 1024)
        except Exception:
            pass
        return logical, max(mem_mb, 2048)

    def _auto_recommend_local_resources(self):
        logical, total_mb = self._detect_local_machine_resources()
        # Conservative local defaults to avoid desktop lockups/crashes.
        rec_cores = max(2, min(8, logical // 4 if logical >= 8 else max(1, logical // 2)))
        rec_cores = max(1, min(rec_cores, logical))
        usable_mb = int(total_mb * 0.70)
        rec_maxcore = 1000
        self.nprocs.set(str(rec_cores))
        self.memory.set(str(rec_maxcore))
        self.nodes.set("1")
        self.local_hw_hint_var.set(
            f"Detected {logical} logical cores, ~{total_mb} MB RAM. Recommended local: {rec_cores} cores, MaxCore {rec_maxcore} MB."
        )

    def _try_orca_candidate(self, cand, version_patterns):
        try:
            proc = subprocess.run(
                [cand, "--version"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            if not out:
                return None
            version = ""
            for pat in version_patterns:
                m = re.search(pat, out, flags=re.IGNORECASE)
                if m:
                    version = m.group(1)
                    break
            if "ORCA" in out.upper() or version:
                return (cand, version or "unknown", out)
        except Exception:
            return None
        return None

    def _windows_drive_roots(self):
        roots = []
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:\\"
            if os.path.exists(root):
                roots.append(root)
        return roots

    def _quick_find_orca_candidates(self, timeout_s=12):
        start = time.time()
        hits = []
        seen = set()
        if os.name != "nt":
            c = shutil.which("orca")
            if c:
                return [c]
            return []

        drive_roots = self._windows_drive_roots()
        preferred_subdirs = [
            "Program Files",
            "Program Files (x86)",
            "orca",
            "ORCA",
            "apps",
            "Apps",
            "tools",
            "Tools",
            "Users",
        ]
        skip_dirs = {
            "$Recycle.Bin", "System Volume Information", "Windows", "WinSxS", "Temp",
            "AppData", "node_modules", ".git", ".venv", "__pycache__",
        }
        exe_name = "orca.exe"

        def _record(path):
            p = os.path.normpath(path)
            if p not in seen and os.path.isfile(p):
                seen.add(p)
                hits.append(p)

        # First pass: likely install locations on every drive
        for root in drive_roots:
            for sub in preferred_subdirs:
                base = os.path.join(root, sub)
                if not os.path.isdir(base):
                    continue
                for cur, dirs, files in os.walk(base, topdown=True):
                    if time.time() - start > timeout_s:
                        return hits
                    depth = cur[len(base):].count(os.sep)
                    if depth > 5:
                        dirs[:] = []
                        continue
                    dirs[:] = [d for d in dirs if d not in skip_dirs]
                    for fn in files:
                        if fn.lower() == exe_name:
                            _record(os.path.join(cur, fn))

        # Second pass: shallow root scan on all drives
        for root in drive_roots:
            if time.time() - start > timeout_s:
                break
            for cur, dirs, files in os.walk(root, topdown=True):
                if time.time() - start > timeout_s:
                    break
                depth = cur[len(root):].count(os.sep)
                if depth > 3:
                    dirs[:] = []
                    continue
                dirs[:] = [d for d in dirs if d not in skip_dirs]
                for fn in files:
                    if fn.lower() == exe_name:
                        _record(os.path.join(cur, fn))

        return hits

    def _detect_local_orca(self, quick_scan=False):
        candidates = []
        seen = set()

        def _add_candidate(c):
            c = (c or "").strip()
            if not c or c in seen:
                return
            seen.add(c)
            candidates.append(c)

        _add_candidate(os.environ.get("ORCA_EXE", ""))
        _add_candidate(shutil.which("orca.exe") or "")
        _add_candidate(shutil.which("orca") or "")

        orca_path_val = (self.orca_path.get() or "").strip()
        if orca_path_val:
            if os.path.isdir(orca_path_val):
                exe_name = "orca.exe" if os.name == "nt" else "orca"
                _add_candidate(os.path.join(orca_path_val, exe_name))
            else:
                _add_candidate(orca_path_val)

        if quick_scan:
            for c in self._quick_find_orca_candidates():
                _add_candidate(c)

        version_patterns = [
            r"Program Version\s+([0-9][0-9A-Za-z.\-_]*)",
            r"\bORCA[^0-9]*([0-9][0-9A-Za-z.\-_]*)",
            r"\bVersion[:\s]+([0-9][0-9A-Za-z.\-_]*)",
        ]

        valid_versions = []
        for cand in candidates:
            result = self._try_orca_candidate(cand, version_patterns)
            if result:
                found_exe, version, out = result
                valid_versions.append((found_exe, version))

        if valid_versions:
            valid_versions.sort(key=lambda x: x[1], reverse=True)
            seen_exes = set()
            unique_versions = []
            for ex, ver in valid_versions:
                if ex not in seen_exes:
                    seen_exes.add(ex)
                    unique_versions.append((ex, ver))
            return True, unique_versions[0][0], unique_versions[0][1], "", unique_versions

        msg = (
            "ORCA executable was not detected in PATH / ORCA_EXE / ORCA path field.\n"
            "Install ORCA, then add it to system PATH or set ORCA_EXE.\n"
            "Tip: use 'Quick search drives' to locate an existing ORCA installation."
        )
        return False, "", "", msg, []

    def _run_quick_orca_search(self):
        if not getattr(self, "local_orca_status_var", None):
            return
        self.local_orca_light_var.set("🟡")
        self.local_orca_status_var.set("Searching local drives for ORCA executable... please wait.")
        self.local_orca_path_var.set("Quick scan running (limited depth for speed).")
        try:
            self.frame.update_idletasks()
        except Exception:
            pass
        ok, exe, version, detail, all_versions = self._detect_local_orca(quick_scan=True)
        if ok:
            self._update_orca_combobox_ui(all_versions)
            self.local_orca_light_var.set("🟢")
            self.local_orca_status_var.set(f"ORCA found by quick search. Version: {version}")
            self.local_orca_path_var.set(f"Executable: {exe}")
            try:
                self.orca_path.set(os.path.dirname(exe))
            except Exception:
                pass
            try:
                self.local_orca_help_frame.pack_forget()
            except Exception:
                pass
        else:
            self.local_orca_light_var.set("🔴")
            self.local_orca_status_var.set("Quick search did not find ORCA executable.")
            self.local_orca_path_var.set(detail)
            try:
                self.local_orca_help_frame.pack(fill=tk.X, pady=(6, 0))
            except Exception:
                pass

    def _show_orca_path_setup_help(self):
        win = tk.Toplevel(self.parent)
        win.title("Set up ORCA in PATH")
        win.transient(self.parent)
        win.geometry("760x460")
        body = ttk.Frame(win, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="ORCA installed but not detected?", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        msg = (
            "1) Click 'Quick search drives' to auto-locate orca.exe.\n"
            "2) If found, copy its folder path and set PATH / ORCA_EXE.\n\n"
            "Windows (PowerShell, current user):\n"
            "  setx ORCA_EXE \"C:\\\\path\\\\to\\\\orca.exe\"\n"
            "  setx PATH \"$($env:PATH);C:\\\\path\\\\to\\\\orca\\\\folder\"\n\n"
            "Then restart this app/terminal and click Re-check.\n\n"
            "You can also paste the ORCA folder into 'ORCA Module/Path' field."
        )
        txt = tk.Text(body, wrap=tk.WORD, font=("Consolas", 10), height=16)
        txt.pack(fill=tk.BOTH, expand=True, pady=(8, 8))
        txt.insert("1.0", msg)
        txt.config(state=tk.DISABLED)
        btn_row = ttk.Frame(body)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Open ORCA Official", command=lambda: webbrowser.open("https://www.faccts.de/orca/")).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Close", command=win.destroy).pack(side=tk.RIGHT)

    def _clear_local_job_log(self):
        w = getattr(self, "local_job_log_txt", None)
        if w is None:
            return
        w.delete("1.0", tk.END)
        w.insert("1.0", "Local submission log cleared.\n")

    def _show_job_history_dialog(self):
        top = tk.Toplevel(self.parent)
        top.title("Local Job History")
        top.geometry("650x350")
        top.grab_set()

        f = ttk.Frame(top, padding=10)
        f.pack(fill=tk.BOTH, expand=True)

        cols = ("Time", "Job Name", "Status", "Folder")
        tree = ttk.Treeview(f, columns=cols, show="headings", height=10)
        tree.heading("Time", text="Time")
        tree.heading("Job Name", text="Job Name")
        tree.heading("Status", text="Status")
        tree.heading("Folder", text="Folder")
        tree.column("Time", width=120, anchor=tk.W)
        tree.column("Job Name", width=150, anchor=tk.W)
        tree.column("Status", width=120, anchor=tk.W)
        tree.column("Folder", width=220, anchor=tk.W)
        
        ysb = ttk.Scrollbar(f, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=ysb.set)
        
        tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)

        for job in self.local_job_history:
            tree.insert("", tk.END, values=(
                job.get("start_time", ""),
                job.get("job_name", ""),
                job.get("status", ""),
                job.get("folder_path", "")
            ))

        def _open_selected_folder():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("Warning", "Please select a job from the list.", parent=top)
                return
            item = tree.item(selected[0])
            folder = item["values"][3]
            if os.path.exists(folder):
                try:
                    if sys.platform == "win32":
                        os.startfile(folder)
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", folder])
                    else:
                        subprocess.Popen(["xdg-open", folder])
                except Exception as e:
                    messagebox.showerror("Error", f"Could not open folder:\\n{e}", parent=top)
            else:
                messagebox.showerror("Error", "Folder no longer exists.", parent=top)

        btn_row = ttk.Frame(f)
        btn_row.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btn_row, text="Open Folder", command=_open_selected_folder).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Close", command=top.destroy).pack(side=tk.RIGHT)

    def _show_xtb_history_dialog(self):
        top = tk.Toplevel(self.parent)
        top.title("xTB Job History")
        top.geometry("650x350")
        top.grab_set()

        f = ttk.Frame(top, padding=10)
        f.pack(fill=tk.BOTH, expand=True)

        cols = ("Time", "Job Name", "Status", "Folder")
        tree = ttk.Treeview(f, columns=cols, show="headings", height=10)
        tree.heading("Time", text="Time")
        tree.heading("Job Name", text="Job Name")
        tree.heading("Status", text="Status")
        tree.heading("Folder", text="Folder")
        tree.column("Time", width=120, anchor=tk.W)
        tree.column("Job Name", width=150, anchor=tk.W)
        tree.column("Status", width=120, anchor=tk.W)
        tree.column("Folder", width=220, anchor=tk.W)
        
        ysb = ttk.Scrollbar(f, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=ysb.set)
        
        tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)

        for job in getattr(self, "xtb_job_history", []):
            tree.insert("", tk.END, values=(
                job.get("start_time", ""),
                job.get("job_name", ""),
                job.get("status", ""),
                job.get("folder_path", "")
            ))

        def _open_selected_folder():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("Warning", "Please select a job from the list.", parent=top)
                return
            item = tree.item(selected[0])
            folder = item["values"][3]
            if os.path.exists(folder):
                try:
                    if sys.platform == "win32":
                        os.startfile(folder)
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", folder])
                    else:
                        subprocess.Popen(["xdg-open", folder])
                except Exception as e:
                    messagebox.showerror("Error", f"Could not open folder:\n{e}", parent=top)
            else:
                messagebox.showerror("Error", "Folder no longer exists.", parent=top)

        btn_row = ttk.Frame(f)
        btn_row.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btn_row, text="Open Folder", command=_open_selected_folder).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Close", command=top.destroy).pack(side=tk.RIGHT)

    def _local_input_nprocs(self, inp_text):
        m = re.search(r"%pal\b.*?\bnprocs\s+(\d+).*?\bend\b", inp_text or "", flags=re.IGNORECASE | re.DOTALL)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return 1
        return 1

    def _prepare_local_input_no_mpi(self, inp_text):
        # Remove %pal block so ORCA runs without calling mpiexec when MPI is unavailable.
        cleaned = re.sub(r"\n?\s*%pal\b.*?\bend\s*\n?", "\n", inp_text or "", flags=re.IGNORECASE | re.DOTALL)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip() + "\n"

    def _open_local_job_folder(self):
        folder = getattr(self, "_last_local_job_folder", "")
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("Local Job", "No local job folder available yet.")
            return
        try:
            if os.name == "nt":
                os.startfile(folder)  # type: ignore[attr-defined, unused-ignore]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showerror("Local Job", str(e))

    def _visualize_local_out(self):
        out_path = getattr(self, "_last_local_job_out", "")
        if not out_path or not os.path.isfile(out_path):
            messagebox.showinfo("Visualize Output", "No completed local .out file found yet.")
            return
        viewer = simpledialog.askstring(
            "Visualize Output",
            "Choose software: Chemcraft / GaussView / Jmol",
            parent=self.parent,
            initialvalue="Chemcraft",
        )
        if viewer is None:
            return
        viewer = (viewer or "").strip().lower()
        try:
            if viewer in ("chemcraft", "chem", "c"):
                exe = self._find_chemcraft_exe()
                if not exe:
                    self._prompt_viewer_path_setup("Chemcraft", "Chemcraft not found.")
                    return
                self._popen_viewer_exe(exe, out_path)
                return
            if viewer in ("gaussview", "gview", "gaussian", "g"):
                exe = self._find_gaussview_exe()
                if not exe:
                    self._prompt_viewer_path_setup("GaussView", "GaussView not found.")
                    return
                self._popen_viewer_exe(exe, out_path)
                return
            if viewer in ("jmol", "j"):
                cmd = self._find_jmol_command(out_path)
                if cmd:
                    subprocess.Popen(cmd)
                    return
                self._prompt_viewer_path_setup("Jmol", "Jmol not found.")
                return
            messagebox.showinfo("Visualize Output", "Please type one of: Chemcraft, GaussView, Jmol.")
        except Exception as e:
            messagebox.showerror("Visualize Output", str(e))

    def _start_local_submission(self):
        if (self.exec_mode.get() or "").strip() != "Local":
            messagebox.showwarning("Local Submission", "Select Execution Environment = Local first.")
            return
        if self.local_job_process is not None:
            messagebox.showinfo("Local Submission", "A local job is already running.")
            return
        self.generate()
        inp_text = self.txt_inp.get("1.0", tk.END).strip()
        if not inp_text:
            messagebox.showwarning("Local Submission", "Input is empty. Generate input first.")
            return

        ok, exe, version, _detail, all_versions = self._detect_local_orca(quick_scan=False)
        if not ok:
            show_software_not_found_dialog("ORCA", self.parent, callback_on_add_path=self._check_local_orca)
            return
        self._update_orca_combobox_ui(all_versions)

        default_job_name = (self.filename.get() or "orca_job").strip() or "orca_job"
        job_name = simpledialog.askstring(
            "Local Submission",
            "Enter job file name (without extension):",
            parent=self.parent,
            initialvalue=default_job_name,
        )
        if job_name is None:
            return
        job_name = (job_name or "").strip()
        if not job_name:
            messagebox.showwarning("Local Submission", "Job file name is required.")
            return
        self.filename.set(job_name)

        run_dir = filedialog.askdirectory(
            title="Select local run directory",
            initialdir=self._safe_initial_dir(self._last_input_save_path or self._last_xyz_open_path),
        )
        if not run_dir:
            return

        inp_path = os.path.join(run_dir, f"{job_name}.inp")
        out_path = os.path.join(run_dir, f"{job_name}.out")
        if os.path.exists(inp_path) or os.path.exists(out_path):
            if not messagebox.askyesno("Overwrite", f"{job_name}.inp/.out already exists in this folder.\nOverwrite?"):
                return
        requested_nprocs = self._local_input_nprocs(inp_text)
        has_mpiexec = bool(shutil.which("mpiexec") or shutil.which("mpiexec.exe"))
        mpi_fallback_note = ""
        if requested_nprocs > 1 and not has_mpiexec:
            inp_text = self._prepare_local_input_no_mpi(inp_text)
            mpi_fallback_note = (
                f"[INFO] mpiexec not found. Input requested nprocs={requested_nprocs}.\n"
                "[INFO] Switched to serial local run by removing %pal block.\n\n"
            )

        try:
            with open(inp_path, "w", encoding="utf-8") as f:
                f.write(inp_text + "\n")
        except Exception as e:
            messagebox.showerror("Local Submission", f"Could not write input file:\n{e}")
            return
            
        mo_path = self.moinp_file.get().strip()
        if mo_path and os.path.isfile(mo_path):
            try: shutil.copy(mo_path, run_dir)
            except Exception: pass
        qro_path = self.qro_file.get().strip()
        if qro_path and os.path.isfile(qro_path):
            try: shutil.copy(qro_path, run_dir)
            except Exception: pass
        he_path = self.hess_file.get().strip()
        if he_path and os.path.isfile(he_path):
            try: shutil.copy(he_path, run_dir)
            except Exception: pass

        # Copy or write NEB-TS product xyz for local run
        if self.task.get() == "Transition State (TS)" and self.subtask.get() == "NEB-TS":
            prod_disk = (self.neb_product_path.get() or "").strip()
            inline = (getattr(self, "_neb_product_coords_text", "") or "").strip()
            if prod_disk or inline:
                prod_path = os.path.join(run_dir, "neb_product.xyz")
                if prod_disk and os.path.isfile(prod_disk):
                    try:
                        shutil.copy(prod_disk, prod_path)
                    except Exception as e:
                        messagebox.showwarning("NEB product file", f"Could not copy neb_product.xyz to local run dir:\n{e}")
                elif inline:
                    rows = _normalize_to_xyz_rows(inline)
                    if rows:
                        try:
                            with open(prod_path, "w", encoding="utf-8", newline="\n") as pf:
                                pf.write(f"{len(rows)}\nNEB product (ORCA Suite)\n")
                                for sym, x, y, z in rows:
                                    pf.write(f"{sym} {x} {y} {z}\n")
                        except Exception as e:
                            messagebox.showwarning("NEB product file", f"Could not write neb_product.xyz to local run dir:\n{e}")

        self._last_local_job_folder = run_dir
        self._last_local_job_out = out_path
        self.local_job_log_txt.delete("1.0", tk.END)
        self.local_job_log_txt.insert(
            "1.0",
            f"Starting local job with ORCA {version}\nExecutable: {exe}\nInput: {inp_path}\nOutput: {out_path}\n\n",
        )
        if mpi_fallback_note:
            self.local_job_log_txt.insert(tk.END, mpi_fallback_note)
        self.local_job_status_var.set("Running local ORCA job...")
        self.btn_local_submit.config(state=tk.DISABLED)
        self.btn_local_stop.config(state=tk.NORMAL)
        self.btn_local_open_folder.config(state=tk.NORMAL)
        self.btn_local_visualize_out.config(state=tk.DISABLED)

        import datetime
        job_record = {
            "job_name": job_name,
            "start_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "In Progress",
            "folder_path": run_dir
        }
        self.local_job_history.insert(0, job_record)
        if len(self.local_job_history) > 10:
            self.local_job_history = self.local_job_history[:10]
        self._save_local_job_history()

        self.local_job_queue = queue.Queue()
        t = threading.Thread(
            target=self._local_submission_worker,
            args=(exe, inp_path, out_path, run_dir, self.local_job_queue),
            daemon=True,
        )
        t.start()
        self._poll_local_job_queue()

    def _local_submission_worker(self, exe, inp_path, out_path, run_dir, q):
        try:
            with open(out_path, "w", encoding="utf-8", errors="replace") as fout:
                proc = subprocess.Popen(
                    [exe, inp_path],
                    cwd=run_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                self.local_job_process = proc
                if proc.stdout is not None:
                    for line in proc.stdout:
                        fout.write(line)
                        fout.flush()
                        q.put(("log", line))
                rc = proc.wait()
                q.put(("done", {"returncode": rc, "out_path": out_path, "folder": run_dir}))
        except Exception as e:
            q.put(("error", str(e)))

    def _poll_local_job_queue(self):
        q = self.local_job_queue
        if q is None:
            return
        while not q.empty():
            typ, content = q.get_nowait()
            if typ == "log":
                self.local_job_log_txt.insert(tk.END, content)
                self.local_job_log_txt.see(tk.END)
            elif typ == "error":
                self.local_job_log_txt.insert(tk.END, f"\n[ERROR] {content}\n")
                self.local_job_status_var.set("Local job failed.")
                if len(self.local_job_history) > 0 and self.local_job_history[0]["status"] == "In Progress":
                    self.local_job_history[0]["status"] = "Failed"
                    self._save_local_job_history()
                self.btn_local_submit.config(state=tk.NORMAL)
                self.btn_local_stop.config(state=tk.DISABLED)
                self.btn_local_visualize_out.config(state=tk.DISABLED)
                self.local_job_process = None
                self.local_job_queue = None
                return
            elif typ == "done":
                rc = int(content.get("returncode", 1))
                self.local_job_log_txt.insert(tk.END, f"\n[Done] ORCA exited with code {rc}\n")
                self.local_job_log_txt.see(tk.END)
                self.local_job_status_var.set("Local job finished." if rc == 0 else f"Local job finished with code {rc}.")
                if len(self.local_job_history) > 0 and self.local_job_history[0]["status"] == "In Progress":
                    self.local_job_history[0]["status"] = "Completed" if rc == 0 else "Failed"
                    self._save_local_job_history()
                self.btn_local_submit.config(state=tk.NORMAL)
                self.btn_local_stop.config(state=tk.DISABLED)
                if rc == 0 and os.path.isfile(self._last_local_job_out):
                    self.btn_local_visualize_out.config(state=tk.NORMAL)
                else:
                    self.btn_local_visualize_out.config(state=tk.DISABLED)
                self.local_job_process = None
                self.local_job_queue = None
                try:
                    self._show_toast("Local ORCA job finished.", 2600)
                except Exception:
                    pass
                self._open_local_job_folder()
                
                # Auto-open .out file
                if os.path.isfile(self._last_local_job_out):
                    try:
                        viewer_exe = self._find_chemcraft_exe()
                        if not viewer_exe:
                            viewer_exe = self._find_avogadro_exe()
                        if viewer_exe:
                            self._popen_viewer_exe(viewer_exe, self._last_local_job_out)
                    except Exception:
                        pass
                        
                return
        self.parent.after(120, self._poll_local_job_queue)

    def _stop_local_submission(self):
        proc = getattr(self, "local_job_process", None)
        if proc is None:
            return
        try:
            proc.terminate()
            if len(self.local_job_history) > 0 and self.local_job_history[0]["status"] == "In Progress":
                self.local_job_history[0]["status"] = "Failed (Stopped)"
                self._save_local_job_history()
        except Exception:
            pass
        self.local_job_log_txt.insert(tk.END, "\n[SYSTEM] Local job termination requested.\n")
        self.local_job_status_var.set("Stopping local job...")

    def _update_local_orca_status(self):
        if not getattr(self, "local_orca_status_var", None):
            return
        is_local = (self.exec_mode.get() or "").strip() == "Local"
        try:
            if hasattr(self, "grid_hpc"):
                if is_local:
                    self.grid_hpc.pack_forget()
                elif not self.grid_hpc.winfo_manager():
                    self.grid_hpc.pack(fill=tk.X)
            if hasattr(self, "nodes_label") and hasattr(self, "nodes_entry"):
                if is_local:
                    self.nodes_label.grid_remove()
                    self.nodes_entry.grid_remove()
                else:
                    self.nodes_label.grid()
                    self.nodes_entry.grid()
            if hasattr(self, "local_hw_auto_btn"):
                if is_local:
                    self.local_hw_auto_btn.grid()
                else:
                    self.local_hw_auto_btn.grid_remove()
            if hasattr(self, "local_hw_hint_lbl"):
                if is_local:
                    self.local_hw_hint_lbl.grid()
                else:
                    self.local_hw_hint_lbl.grid_remove()
            if hasattr(self, "local_hw_hint_var") and not is_local:
                self.local_hw_hint_var.set("")
            if hasattr(self, "local_submit_frame"):
                if is_local:
                    if not self.local_submit_frame.winfo_manager():
                        self.local_submit_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
                else:
                    self.local_submit_frame.pack_forget()
        except Exception:
            pass

        def _show_orca_action_buttons():
            try:
                self.btn_orca_recheck.pack(side=tk.RIGHT)
                self.btn_orca_quick_search.pack(side=tk.RIGHT, padx=(0, 6))
                self.btn_orca_path_help.pack(side=tk.RIGHT, padx=(0, 6))
            except Exception:
                pass

        def _hide_orca_action_buttons():
            for btn_name in ("btn_orca_recheck", "btn_orca_quick_search", "btn_orca_path_help"):
                try:
                    getattr(self, btn_name).pack_forget()
                except Exception:
                    pass

        mode = (self.exec_mode.get() or "").strip()
        if mode != "Local":
            _show_orca_action_buttons()
            self.local_orca_light_var.set("⚪")
            self.local_orca_status_var.set("Local ORCA check is paused (switch Execution Environment to Local).")
            self.local_orca_path_var.set("")
            try:
                self.local_orca_help_frame.pack_forget()
            except Exception:
                pass
            try:
                self.local_orca_status_frame.pack_forget()
            except Exception:
                pass
            return
        try:
            if not self.local_orca_status_frame.winfo_manager():
                self.local_orca_status_frame.pack(fill=tk.X, pady=(8, 6), before=self.local_orca_status_frame.master.winfo_children()[1])
        except Exception:
            try:
                self.local_orca_status_frame.pack(fill=tk.X, pady=(8, 6))
            except Exception:
                pass
        try:
            if hasattr(self, "local_hw_hint_var") and not self.local_hw_hint_var.get().strip():
                self._auto_recommend_local_resources()
        except Exception:
            pass

        # 1) Fast PATH/env/manual field check first
        ok, exe, version, detail, all_versions = self._detect_local_orca(quick_scan=False)
        if ok:
            self._update_orca_combobox_ui(all_versions)
            _hide_orca_action_buttons()
            self.local_orca_light_var.set("🟢")
            self.local_orca_status_var.set(f"ORCA installed and ready. Version: {version}")
            self.local_orca_path_var.set("You can run local jobs now.")
            try:
                self.orca_path.set(os.path.dirname(exe))
            except Exception:
                pass
            try:
                self.local_orca_help_frame.pack_forget()
            except Exception:
                pass
            return

        # 2) Automatic quick drive search before showing install guidance
        self.local_orca_light_var.set("🟡")
        self.local_orca_status_var.set("ORCA not in PATH. Running quick drive search...")
        self.local_orca_path_var.set("Checking common install locations...")
        try:
            self.frame.update_idletasks()
        except Exception:
            pass

        ok, exe, version, detail, all_versions = self._detect_local_orca(quick_scan=True)
        if ok:
            self._update_orca_combobox_ui(all_versions)
            _hide_orca_action_buttons()
            self.local_orca_light_var.set("🟢")
            self.local_orca_status_var.set(f"ORCA installed and ready. Version: {version}")
            self.local_orca_path_var.set("You can run local jobs now.")
            try:
                self.orca_path.set(os.path.dirname(exe))
            except Exception:
                pass
            try:
                self.local_orca_help_frame.pack_forget()
            except Exception:
                pass
            return

        _show_orca_action_buttons()
        self.local_orca_light_var.set("🔴")
        self.local_orca_status_var.set("ORCA not detected for local submission.")
        self.local_orca_path_var.set(detail)
        try:
            self.local_orca_help_frame.pack(fill=tk.X, pady=(6, 0))
        except Exception:
            pass

    def _on_local_orca_version_selected(self, event=None):
        val = getattr(self, "local_orca_version_combo", None)
        if val:
            sel = val.get()
            if "  |  " in sel:
                version, exe = sel.split("  |  ", 1)
                exe = exe.strip()
                try:
                    self.orca_path.set(os.path.dirname(exe))
                except Exception:
                    pass
                self.local_orca_path_var.set(f"Selected Executable: {exe}")
                self.local_orca_status_var.set(f"ORCA installed and ready. Version: {version.strip()}")
                self.local_orca_light_var.set("🟢")
                try:
                    self.local_orca_help_frame.pack_forget()
                except Exception:
                    pass

    def _update_orca_combobox_ui(self, all_versions):
        if not getattr(self, "local_orca_version_combo", None):
            return
        if len(all_versions) > 1:
            vals = [f"{ver}  |  {ex}" for ex, ver in all_versions]
            self.local_orca_version_combo.config(values=vals)
            self.local_orca_version_combo.set(vals[0])
            self.local_orca_version_combo.pack(side=tk.LEFT, padx=(0, 6), before=self.local_orca_status_lbl)
        else:
            self.local_orca_version_combo.pack_forget()

    def _browse_moinp_file(self):
        initial_dir = self._safe_initial_dir(self._last_gbw_path or self.moinp_file.get())
        path = filedialog.askopenfilename(
            parent=self.parent,
            initialdir=initial_dir,
            filetypes=[("GBW Files", "*.gbw"), ("All files", "*.*")],
        )
        if not path:
            return
        self.moinp_file.set(path)
        self._last_gbw_path = path

    def _browse_qro_file(self):
        initial_dir = self._safe_initial_dir(self._last_qro_path or self.qro_file.get())
        path = filedialog.askopenfilename(
            parent=self.parent,
            initialdir=initial_dir,
            filetypes=[("QRO Files", "*.qro"), ("All files", "*.*")],
        )
        if not path:
            return
        self.qro_file.set(path)
        self._last_qro_path = path

    def _browse_hess_file(self):
        initial_dir = self._safe_initial_dir(self._last_hess_path or self.hess_file.get())
        path = filedialog.askopenfilename(
            parent=self.parent,
            initialdir=initial_dir,
            filetypes=[("Hessian Files", "*.hess"), ("All files", "*.*")],
        )
        if not path:
            return
        self.hess_file.set(path)
        self._last_hess_path = path

    def _browse_neb_product(self):
        initial = self._safe_initial_dir(
            self._last_neb_path
            or self.neb_product_path.get()
            or self._last_xyz_open_path
        )
        path = filedialog.askopenfilename(
            parent=self.parent,
            initialdir=initial,
            filetypes=[("Geometry files", "*.xyz *.gjf"), ("XYZ files", "*.xyz"), ("Gaussian input", "*.gjf"), ("All files", "*.*")],
        )
        if not path:
            return
        self._last_neb_path = path
        self.neb_product_path.set(path)
        self._neb_product_coords_text = ""

    def _open_neb_product_editor(self):
        top = tk.Toplevel(self.parent.winfo_toplevel())
        top.title("NEB product (end-point) coordinates")
        top.transient(self.parent.winfo_toplevel())
        top.grab_set()
        f = ttk.Frame(top, padding=10)
        f.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            f,
            text="Paste ORCA / XYZ / xmol / Gaussian .gjf coordinates. OK normalizes to xmol-style atom lines.",
        ).pack(anchor="w")
        text_fr = ttk.Frame(f)
        text_fr.pack(fill=tk.BOTH, expand=True, pady=6)
        txt = tk.Text(text_fr, width=72, height=16, wrap="none", font=("Consolas", 10))
        ys = ttk.Scrollbar(text_fr, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=ys.set)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ys.pack(side=tk.RIGHT, fill=tk.Y)

        initial = ""
        p = (self.neb_product_path.get() or "").strip()
        if p and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    initial = fh.read()
            except Exception:
                initial = self._neb_product_coords_text or ""
        else:
            initial = self._neb_product_coords_text or ""
        if initial:
            txt.insert("1.0", initial)

        btn_row = ttk.Frame(f)
        btn_row.pack(fill=tk.X, pady=(6, 0))

        def _do_xtb_opt():
            raw = txt.get("1.0", tk.END)
            rows_new = self._run_xtb_opt_geom_text_sync(raw)
            if rows_new:
                body = "\n".join(f"{a} {x} {y} {z}" for a, x, y, z in rows_new) + "\n"
                txt.delete("1.0", tk.END)
                txt.insert("1.0", body)
                messagebox.showinfo("xTB", "Product geometry updated with xTB-optimized coordinates.", parent=top)

        def _ok():
            raw = txt.get("1.0", tk.END)
            rows = _normalize_to_xyz_rows(raw)
            if not rows:
                messagebox.showerror(
                    "NEB product",
                    "Could not parse coordinates. Check format (XYZ, ORCA * xyz *, GJF).",
                    parent=top,
                )
                return
            self._neb_product_coords_text = "\n".join(f"{a} {x} {y} {z}" for a, x, y, z in rows) + "\n"
            self.neb_product_path.set("")
            top.destroy()

        ttk.Button(btn_row, text="Cancel", command=top.destroy).pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="OK", command=_ok).pack(side=tk.RIGHT, padx=(0, 6))

    def _run_xtb_opt_geom_text_sync(self, raw_text: str):
        rows = _normalize_geometry_raw(raw_text or "")
        if not rows:
            messagebox.showwarning("xTB", "No valid molecular geometry to optimize.")
            return None
        xtb_exe = self._find_xtb_exe()
        if not xtb_exe:
            messagebox.showerror(
                "xTB not found",
                "Could not locate the bundled g-xTB executable under external_modules/xtb/g-xtb.",
            )
            return None
        try:
            chrg = int(self.charge.get())
            mult = int(self.mult.get())
            if mult < 1:
                raise ValueError
            uhf = mult - 1
        except (ValueError, TypeError):
            messagebox.showwarning("xTB", "Invalid charge or multiplicity for xTB.")
            return None
        opt_level = self.xtb_opt_level.get().strip()
        gfn = self.xtb_gfn_level.get().strip()
        xtb_method = (self.xtb_method.get() or "gfn2").strip().lower()
        if xtb_method not in ("gxtb", "gfn2"):
            xtb_method = "gfn2"
        work_parent = xtb_support.default_xtb_work_parent()
        os.makedirs(work_parent, exist_ok=True)
        temp_dir = tempfile.mkdtemp(prefix="neb_xtb_", dir=work_parent)
        geom_body = "\n".join(f"{s} {x} {y} {z}" for s, x, y, z in rows)
        xtb_support.write_input_xyz(os.path.join(temp_dir, "input.xyz"), geom_body)
        xtb_args = xtb_support.build_xtb_argv(
            job="opt",
            opt_level=opt_level,
            gfn=gfn,
            chrg=chrg,
            uhf=uhf,
            use_xcontrol=False,
            xtb_method=xtb_method
        )
        cmd = [xtb_exe, "input.xyz"] + xtb_args
        try:
            popen_kw = {
                "cwd": temp_dir,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.STDOUT,
            }
            if os.name == "nt":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                popen_kw["startupinfo"] = si
            r = subprocess.run(cmd, **popen_kw)
        except Exception as e:
            messagebox.showerror("xTB", f"Could not run xTB:\n{e}")
            return None
        outp = os.path.join(temp_dir, "xtbopt.xyz")
        if not os.path.isfile(outp):
            rc = getattr(r, "returncode", "?")
            messagebox.showerror(
                "xTB",
                f"xtbopt.xyz was not created (exit code {rc}).\nSee {temp_dir}",
            )
            return None
        if r.returncode != 0:
            messagebox.showwarning(
                "xTB",
                f"xTB reported exit code {r.returncode} but xtbopt.xyz was found; using last geometry.\n{temp_dir}",
            )
        try:
            with open(outp, encoding="utf-8", errors="replace") as fh:
                out_txt = fh.read()
        except Exception as e:
            messagebox.showerror("xTB", f"Could not read xtbopt.xyz:\n{e}")
            return None
        out_rows = _last_xyz_frame_rows(out_txt)
        if not out_rows:
            out_rows = _normalize_to_xyz_rows(out_txt)
        if not out_rows:
            messagebox.showerror("xTB", "Could not parse optimized geometry from xtbopt.xyz.")
            return None
        return out_rows

    def _neb_xtb_opt_background(self, mode):
        top = tk.Toplevel(self.parent.winfo_toplevel())
        top.title("xTB Optimization")
        top.geometry("300x120")
        top.grab_set()
        
        lbl = ttk.Label(top, text=f"Optimizing {mode} with xTB...\nPlease wait, this may take a moment.", justify="center")
        lbl.pack(expand=True)
        
        # Read text states in main thread to be safe
        raw_react = self.geom.get("1.0", tk.END)
        prod_disk = (self.neb_product_path.get() or "").strip()
        inline = (getattr(self, "_neb_product_coords_text", "") or "").strip()
        raw_prod = ""
        if prod_disk and os.path.isfile(prod_disk):
            try:
                with open(prod_disk, "r") as f:
                    raw_prod = f.read()
            except Exception:
                pass
        else:
            raw_prod = inline

        t = threading.Thread(target=self._neb_xtb_opt_worker, args=(mode, top, raw_react, raw_prod), daemon=True)
        t.start()

    def _neb_xtb_opt_worker(self, mode, popup, raw_react, raw_prod):
        success_msg = []
        try:
            if mode in ["Reactant", "Both"] and raw_react.strip():
                rows_react = self._run_xtb_opt_geom_text_sync(raw_react)
                if rows_react:
                    def update_react():
                        self._apply_geometry_rows(rows_react)
                        if hasattr(self, "_validate_charge_mult"):
                            self._validate_charge_mult()
                    self.parent.after(0, update_react)
                    success_msg.append("Reactant")
            
            if mode in ["Product", "Both"] and raw_prod.strip():
                rows_prod = self._run_xtb_opt_geom_text_sync(raw_prod)
                if rows_prod:
                    def update_prod():
                        self._neb_product_coords_text = "\n".join(f"{a} {x} {y} {z}" for a, x, y, z in rows_prod) + "\n"
                        self.neb_product_path.set("")
                    self.parent.after(0, update_prod)
                    success_msg.append("Product")
        finally:
            def finish():
                popup.destroy()
                if success_msg:
                    messagebox.showinfo("xTB", f"Optimized structure updated for: {', '.join(success_msg)}")
            self.parent.after(0, finish)

    def _apply_geometry_rows(self, rows, filename_base=None):
        if not rows:
            return
        lines = [str(len(rows)), "Geometry"]
        for sym, x, y, z in rows:
            try:
                lines.append(f"{sym:<4} {float(x):>12.6f} {float(y):>12.6f} {float(z):>12.6f}")
            except ValueError:
                lines.append(f"{sym:<4} {x:>12} {y:>12} {z:>12}")
        body = "\n".join(lines) + "\n"
        self.geom.delete("1.0", tk.END)
        self.geom.insert("1.0", body)
        if filename_base:
            self.filename.set(filename_base)

        metals = {"Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
                  "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
                  "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
                  "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn",
                  "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
                  "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr"}

        found_metals = []
        found_non_metals = []
        individual_atoms = []
        for idx, row in enumerate(rows):
            sym = row[0].capitalize()
            if sym in metals:
                if sym not in found_metals:
                    found_metals.append(sym)
            else:
                if sym not in found_non_metals:
                    found_non_metals.append(sym)
            individual_atoms.append(f"{sym} ({idx})")

        self.detected_elements = found_metals + found_non_metals + ["---"] + individual_atoms
        if hasattr(self, "split_rows"):
            for r in self.split_rows:
                if "target_cb" in r:
                    r["target_cb"].config(values=self.detected_elements)

        if found_metals and hasattr(self, "split_basis_var"):
            self.split_basis_var.set(True)
            if hasattr(self, "_toggle_split_basis_cb"):
                self._toggle_split_basis_cb()
            if not self.split_rows:
                for m in found_metals:
                    self._add_split_row_cb(target=m, cmd_type="newgto", val="def2-TZVP")
                    if m in {"Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
                             "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
                             "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
                             "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr"}:
                        self._add_split_row_cb(target=m, cmd_type="NewECP", val="def2-TZVP")

    def _visualize_geometry_from_editor(self):
        import tkinter.messagebox as messagebox
        raw = self.geom.get("1.0", tk.END)
        rows = _normalize_to_xyz_rows(raw)
        if not rows:
            messagebox.showerror(
                "Geometry Parse Error",
                "Could not parse geometry from editor.\nSupported: XYZ/xmol, ORCA '* xyz ... *', and Gaussian .gjf coordinate blocks.",
            )
            return
        self._apply_geometry_rows(rows)
        if hasattr(self, "_validate_charge_mult"):
            self._validate_charge_mult()
        self._embed_viewer()

    def _on_geom_tkdnd_drop(self, event):
        paths = _parse_tkdnd_file_list(getattr(event, "data", "") or "")
        for p in paths:
            if p and os.path.isfile(p):
                self.parent.after(0, lambda fp=p: self._load_geometry_from_dropped_path(fp))
                return

    def _hook_geometry_drag_drop(self):
        if getattr(self, "_geom_wnd_hook_done", False):
            return
        top = self.parent.winfo_toplevel()
        try:
            top.update_idletasks()
            self.geom.update_idletasks()
        except Exception:
            pass

        # Windows: native shell drop (windnd). Deferred hook + HWND ready reduces "no drop" cursor.
        if os.name == "nt":
            try:
                import windnd

                def _on_drop(paths):
                    if not paths:
                        return
                    p = paths[0]
                    if isinstance(p, bytes):
                        try:
                            p = p.decode("utf-8")
                        except Exception:
                            p = p.decode("mbcs", errors="replace")
                    p = (p or "").strip().strip('"').strip("'")
                    if not p or not os.path.isfile(p):
                        return
                    self.parent.after(0, lambda fp=p: self._load_geometry_from_dropped_path(fp))

                try:
                    windnd.hook_dropfiles(self.geom, _on_drop)
                except Exception:
                    lf = getattr(self, "_left_geom_frame", None)
                    if lf is None:
                        raise
                    windnd.hook_dropfiles(lf, _on_drop)
                self._geom_wnd_hook_done = True
                return
            except Exception:
                pass

        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD

            TkinterDnD._require(top)
            self.geom.drop_target_register(DND_FILES)
            self.geom.dnd_bind("<<Drop>>", self._on_geom_tkdnd_drop)
            self._geom_wnd_hook_done = True
        except Exception:
            pass

    def _load_geometry_from_dropped_path(self, path: str):
        path = (path or "").strip().strip('"').strip("'")
        import tkinter.messagebox as messagebox
        if not path or not os.path.isfile(path):
            return
        self._last_xyz_open_path = path
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except Exception as e:
            messagebox.showerror("Geometry", f"Could not read dropped file:\n{e}")
            return
        rows = _normalize_geometry_raw(raw)
        if not rows:
            messagebox.showwarning(
                "Geometry",
                "Could not parse coordinates from the dropped file.\n"
                "Supported: XYZ, ORCA blocks, Gaussian .gjf, etc.",
            )
            return
        self._apply_geometry_rows(rows, filename_base=os.path.splitext(os.path.basename(path))[0])
        if hasattr(self, "_validate_charge_mult"):
            self._validate_charge_mult()
        if hasattr(self, "_show_toast"):
            self._show_toast(f"{os.path.basename(path)} loaded into geometry", duration_ms=2500)

    def format_current_xyz(self):
        raw = self.geom.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showinfo("Format XYZ", "The geometry box is empty.")
            return
        rows = _normalize_geometry_raw(raw)
        if not rows:
            messagebox.showerror("Format XYZ", "Could not parse valid geometry from the current text.")
            return
        lines = [str(len(rows)), "Geometry"]
        for row in rows:
            lines.append(f"{row[0]:<4} {float(row[1]):>12.6f} {float(row[2]):>12.6f} {float(row[3]):>12.6f}")
        formatted = "\n".join(lines) + "\n"
        self.geom.delete("1.0", tk.END)
        self.geom.insert(tk.END, formatted)
        
    def load_xyz(self):
        path = filedialog.askopenfilename(
            parent=self.parent,
            initialdir=self._safe_initial_dir(self._last_xyz_open_path),
            filetypes=[("Geometry files", "*.xyz *.gjf"), ("XYZ files", "*.xyz"), ("Gaussian input", "*.gjf"), ("All files", "*.*")],
        )
        if path:
            self._last_xyz_open_path = path
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read()
            except Exception as e:
                messagebox.showerror("Load Geometry", f"Could not read file:\n{e}")
                return
            rows = _normalize_geometry_raw(raw)
            if not rows:
                messagebox.showerror(
                    "Load Geometry",
                    "Could not parse geometry from selected file.\nSupported: XYZ, Gaussian .gjf, ORCA * xyz * / Cartesian sections.",
                )
                return
            self._apply_geometry_rows(rows, filename_base=os.path.splitext(os.path.basename(path))[0])
            if hasattr(self, '_validate_charge_mult'):
                self._validate_charge_mult()

    def _on_viewer_changed(self, event=None):
        if not self.geom.get("1.0", tk.END).strip():
            return
        # Seamless software switching in the normal embedded structure panel.
        self._terminate_embed_subprocess()
        self._embed_viewer()

    def _terminate_embed_subprocess(self):
        self._viewer_detached = False
        self._embed_saved_style = None
        self._embed_saved_exstyle = None
        try:
            if getattr(self, "_lw_detached_win", None) and self._lw_detached_win.winfo_exists():
                self._lw_detached_win.destroy()
        except Exception:
            pass
        self._lw_detached_win = None
        if self.embed_hwnd:
            try:
                SW_HIDE = 0
                ctypes.windll.user32.ShowWindow(self.embed_hwnd, SW_HIDE)
                ctypes.windll.user32.DestroyWindow(self.embed_hwnd)
            except Exception:
                pass
            self.embed_hwnd = None
        if self.embed_subprocess:
            try:
                self.embed_subprocess.terminate()
                self.embed_subprocess.wait(timeout=3)
            except Exception:
                try:
                    self.embed_subprocess.kill()
                except Exception:
                    pass
            self.embed_subprocess = None
        self._update_embed_detach_button_state()

    def _update_embed_detach_button_state(self):
        btn = getattr(self, "detach_embed_btn", None)
        if not btn:
            return
        try:
            viewer = self.embed_viewer_choice.get() if hasattr(self, "embed_viewer_choice") else ""
            is_lw = str(viewer).strip().lower() in ("lightweight", "acv ( autochemyviewer )")
            ok = bool(
                self.embed_hwnd
                and os.name == "nt"
                and ctypes.windll.user32.IsWindow(self.embed_hwnd)
            )
            if is_lw and getattr(self, "_last_autochemy_rows", None):
                ok = True
            btn.config(state="normal" if ok else "disabled")
            if ok:
                btn.config(
                    text="Reattach to panel" if self._viewer_detached else "Detach viewer window"
                )
        except Exception:
            btn.config(state="disabled")

    def _toggle_embed_detach(self):
        viewer = self.embed_viewer_choice.get() if hasattr(self, "embed_viewer_choice") else ""
        if str(viewer).strip().lower() in ("lightweight", "acv ( autochemyviewer )"):
            if self._viewer_detached:
                self._reattach_autochemy_viewer()
            else:
                self._detach_autochemy_viewer()
            return
        if os.name != "nt" or not self.embed_hwnd:
            return
        if not ctypes.windll.user32.IsWindow(self.embed_hwnd):
            self.embed_hwnd = None
            self._viewer_detached = False
            self._update_embed_detach_button_state()
            return
        if self._viewer_detached:
            self._reattach_embedded_viewer()
        else:
            self._detach_embedded_viewer()

    def _detach_autochemy_viewer(self):
        rows = getattr(self, "_last_autochemy_rows", None)
        if not rows:
            return
        parent = self.parent.winfo_toplevel()
        win = tk.Toplevel(parent)
        win.title("Structure visualization - ACV ( AutoChemyViewer )")
        win.geometry("980x760")
        win.minsize(700, 520)
        host = ttk.Frame(win)
        host.pack(fill=tk.BOTH, expand=True)
        self._show_autochemy_structure(rows, target_host=host)
        btn_row = ttk.Frame(win)
        btn_row.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(btn_row, text="Reattach to panel", command=self._reattach_autochemy_viewer).pack(side=tk.RIGHT)
        self._lw_detached_win = win
        self._viewer_detached = True
        win.protocol("WM_DELETE_WINDOW", self._reattach_autochemy_viewer)
        self._update_embed_detach_button_state()

    def _reattach_autochemy_viewer(self):
        try:
            if getattr(self, "_lw_detached_win", None) and self._lw_detached_win.winfo_exists():
                self._lw_detached_win.destroy()
        except Exception:
            pass
        self._lw_detached_win = None
        rows = getattr(self, "_last_autochemy_rows", None)
        if rows:
            self._show_autochemy_structure(rows)
        self._viewer_detached = False
        self._update_embed_detach_button_state()

    def _detach_embedded_viewer(self):
        hwnd = self.embed_hwnd
        user32 = ctypes.windll.user32
        GWL_STYLE = -16
        GWL_EXSTYLE = -20
        WS_VISIBLE = 0x10000000
        WS_CHILD = 0x40000000
        WS_OVERLAPPEDWINDOW = 0x00CF0000
        SWP_FRAMECHANGED = 0x0020
        SWP_SHOWWINDOW = 0x0040
        HWND_TOP = 0

        user32.SetParent(hwnd, 0)
        base_style = self._embed_saved_style
        if base_style is None:
            base_style = WS_OVERLAPPEDWINDOW
        style = (int(base_style) & ~WS_CHILD) | WS_VISIBLE
        user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_int32(style).value)
        ex = self._embed_saved_exstyle if self._embed_saved_exstyle is not None else 0
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_int32(int(ex)).value)

        self.parent.update_idletasks()
        rx = self.parent.winfo_rootx()
        ry = self.parent.winfo_rooty()
        rw = max(self.parent.winfo_width(), 400)
        rh = max(self.parent.winfo_height(), 300)
        w = min(1024, max(640, rw - 48))
        h = min(768, max(480, rh - 48))
        x = rx + max(0, (rw - w) // 2)
        y = ry + max(0, (rh - h) // 2)
        user32.SetWindowPos(hwnd, HWND_TOP, x, y, w, h, SWP_FRAMECHANGED | SWP_SHOWWINDOW)
        self._viewer_detached = True
        self._update_embed_detach_button_state()

    def _reattach_embedded_viewer(self):
        hwnd = self.embed_hwnd
        if not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
            self._viewer_detached = False
            self._update_embed_detach_button_state()
            return
        user32 = ctypes.windll.user32
        GWL_STYLE = -16
        GWL_EXSTYLE = -20
        WS_VISIBLE = 0x10000000
        WS_CHILD = 0x40000000
        WS_POPUP = 0x80000000
        WS_CAPTION = 0x00C00000
        WS_THICKFRAME = 0x00040000
        WS_MINIMIZEBOX = 0x00020000
        WS_MAXIMIZEBOX = 0x00010000
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080

        tk_hwnd = self.embed_host.winfo_id()
        user32.SetParent(hwnd, tk_hwnd)
        base_style = self._embed_saved_style if self._embed_saved_style is not None else 0
        base_exstyle = self._embed_saved_exstyle if self._embed_saved_exstyle is not None else 0
        style = (int(base_style) | WS_VISIBLE | WS_CHILD) & ~(
            WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
        )
        exstyle = (int(base_exstyle) & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
        user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_int32(style).value)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_int32(exstyle).value)
        SWP_NOZORDER = 0x0004
        SWP_FRAMECHANGED = 0x0020
        SWP_NOACTIVATE = 0x0010
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        user32.ShowWindow(hwnd, 5)
        self._viewer_detached = False
        self.embed_host.update_idletasks()
        self._resize_embedded()
        self._update_embed_detach_button_state()

    def _create_temp_xyz(self) -> str | None:
        raw_text = self.geom.get("1.0", tk.END)
        rows = _geom_lines_to_coord_rows(raw_text)
        if not rows:
            return None
            
        lines = [str(len(rows)), "Generated by ORCA Suite"]
        for row in rows:
            try:
                lines.append(f"{row[0]:<4} {float(row[1]):>12.6f} {float(row[2]):>12.6f} {float(row[3]):>12.6f}")
            except (ValueError, TypeError):
                lines.append(f"{row[0]:<4} {row[1]:>12} {row[2]:>12} {row[3]:>12}")
                
        formatted_xyz = "\n".join(lines) + "\n"

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".xyz", mode="w", encoding="utf-8", newline="\n")
        temp_file.write(formatted_xyz)
        temp_file.close()
        return temp_file.name

    def _build_gjf_content(self) -> str:
        """Gaussian input (GJF) for GaussView — plain XYZ is often not opened correctly."""
        rows = _geom_lines_to_coord_rows(self.geom.get("1.0", tk.END))
        if not rows:
            return ""

        job = (self.filename.get() or "molecule").strip()
        job_safe = re.sub(r"[^\w\-]", "_", job)[:80] or "molecule"
        tdir = tempfile.gettempdir()
        chk_path = os.path.join(tdir, f"orca_gv_{job_safe}.chk")

        try:
            nproc = max(1, int(self.nprocs.get().strip() or "4"))
        except ValueError:
            nproc = 4
        try:
            mem_mb = int(self.memory.get().strip() or "4000")
        except ValueError:
            mem_mb = 4000
        if mem_mb >= 1024:
            mem_line = f"%mem={mem_mb // 1024}GB"
        else:
            mem_line = f"%mem={mem_mb}MB"

        func = self.method.get()
        if func == "M06-2X":
            func = "M062X"
        if func == "M06-L":
            func = "M06L"
        func_l = func.lower()
        basis = self.basis.get().strip() or "6-31g**"

        task = self.task.get()
        st = self.subtask.get()
        if task == "Optimisation + Frequency":
            route_job = "opt freq=noraman"
        elif task in ["Optimisation", "Scan"] or task == "Transition State (TS)":
            route_job = "opt"
        elif task == "Frequency":
            route_job = "freq=noraman"
        else:
            route_job = "sp"

        disp_val = self.dispersion.get().strip()
        disp_kw = self.DISPERSION_MAP.get(disp_val, disp_val)
        disp_g = _GAUSSIAN_DISP.get(disp_kw, "")
        route_body = f"{route_job} {func_l} {basis}"
        if disp_g:
            route_body += f" {disp_g}"

        q = self.charge.get().strip() or "0"
        m = self.mult.get().strip() or "1"

        lines = [
            f"%chk={chk_path}",
            mem_line,
            f"%nprocshared={nproc}",
            "",
            f"# {route_body}",
            "",
            job,
            "",
            f"{q} {m}",
        ]
        for sym, xs, ys, zs in rows:
            lines.append(f"{sym:<3} {float(xs):>16.9f} {float(ys):>16.9f} {float(zs):>16.9f}")
        lines.append("")
        return "\n".join(lines)

    def _write_temp_gjf(self):
        gjf = self._build_gjf_content()
        if not gjf:
            return None
        fd, path = tempfile.mkstemp(suffix=".gjf", prefix="orca_gv_", text=True)
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(gjf)
        return path

    @staticmethod
    def _find_gaussview_exe():
        env = os.environ.get("GAUSSVIEW_EXE", "").strip()
        if env and os.path.isfile(env):
            return env
        saved = SoftwareManager.get_software_path("GaussView")
        if saved:
            return saved
        for p in (
            r"C:\G16W\gview.exe",
            r"C:\G09W\gview.exe",
            r"C:\G03W\gview.exe",
            r"C:\g16w\gview.exe",
            r"C:\g09w\gview.exe",
            r"C:\g03w\gview.exe",
            r"C:\Program Files\Gaussian\G16W\gview.exe",
            r"C:\Program Files\Gaussian\gview.exe",
            r"C:\Program Files (x86)\Gaussian\gview.exe",
        ):
            if os.path.isfile(p):
                return p
        gdir = os.environ.get("GAUSS_EXEDIR", "").strip()
        if gdir:
            for name in ("gview.exe", "GVIEW.EXE"):
                cand = os.path.join(gdir, name)
                if os.path.isfile(cand):
                    return cand
        for name in ("gview", "gview.exe"):
            p = shutil.which(name)
            if p and os.path.isfile(p):
                return p
        return None

    @staticmethod
    def _find_chemcraft_exe():
        env = os.environ.get("CHEMCRAFT_EXE", "").strip()
        if env and os.path.isfile(env):
            return env
        saved = SoftwareManager.get_software_path("Chemcraft")
        if saved:
            return saved
        for name in ("chemcraft", "Chemcraft", "chemistry"):
            p = shutil.which(name)
            if p and os.path.isfile(p):
                return p
        for p in (
            r"C:\Program Files\Chemcraft\Chemcraft.exe",
            r"C:\Program Files\Chemcraft\chemistry.exe",
            r"C:\Program Files (x86)\Chemcraft\Chemcraft.exe",
            r"C:\Chemcraft\Chemcraft.exe",
        ):
            if os.path.isfile(p):
                return p
        return None

    @staticmethod
    def _find_avogadro_exe():
        for env_name in ("AVOGADRO_EXE", "AVOGADRO2_EXE"):
            env = os.environ.get(env_name, "").strip()
            resolved = SoftwareManager.resolve_executable_path(env, "Avogadro")
            if resolved:
                return resolved
        for saved_name in ("Avogadro", "Avogadro2"):
            resolved = SoftwareManager.get_software_path(saved_name)
            if resolved:
                return resolved
        for detect_name in ("Avogadro2", "Avogadro"):
            resolved = SoftwareManager.auto_detect_path(detect_name)
            if resolved:
                return resolved
        for exe_name in ("avogadro2", "Avogadro2", "avogadro", "Avogadro"):
            resolved = shutil.which(exe_name)
            if resolved and os.path.isfile(resolved):
                return resolved
        for p in (
            r"C:\Program Files\Avogadro2\bin\avogadro2.exe",
            r"C:\Program Files\Avogadro2\bin\Avogadro2.exe",
            r"C:\Program Files\Avogadro\bin\avogadro.exe",
            r"C:\Program Files\Avogadro\avogadro.exe",
            r"C:\Program Files (x86)\Avogadro\avogadro.exe",
        ):
            if os.path.isfile(p):
                return p
        return None

    @staticmethod
    def _popen_viewer_exe(exe, target):
        cwd = os.path.dirname(exe) if exe and os.path.isfile(exe) else None
        cmd = [exe, target] if target else [exe]
        try:
            return subprocess.Popen(cmd, cwd=cwd or None)
        except TypeError:
            return subprocess.Popen(cmd)

    @staticmethod
    def _find_jmol_jar_in_folder(folder):
        folder = (folder or "").strip()
        if not folder or not os.path.isdir(folder):
            return None
        direct = os.path.join(folder, "Jmol.jar")
        if os.path.isfile(direct):
            return direct
        try:
            for cur, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if d.lower() not in {"__pycache__", ".git", "node_modules"}]
                for fn in files:
                    if fn.lower() == "jmol.jar":
                        return os.path.join(cur, fn)
        except (OSError, PermissionError):
            pass
        return None

    @staticmethod
    def _find_jmol_command(target):
        def _cmd(*args):
            return list(args) + [target] if target else list(args)

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bundled_jar = os.path.join(base_dir, "jmol-16.3.55", "Jmol.jar")
        if os.path.isfile(bundled_jar):
            return _cmd("java", "-jar", bundled_jar)

        env_jar = os.environ.get("JMOL_JAR", "").strip()
        if env_jar and os.path.isfile(env_jar):
            return _cmd("java", "-jar", env_jar)

        saved_jmol = None
        for item in SoftwareManager.load_software():
            if (item.get("name") or "").strip().lower() == "jmol":
                saved_jmol = (item.get("path") or "").strip().strip('"').strip("'")
                break

        if saved_jmol:
            if os.path.isfile(saved_jmol):
                if saved_jmol.lower().endswith(".jar"):
                    return _cmd("java", "-jar", saved_jmol)
                return _cmd(saved_jmol)
            jar = InputCreatorModule5._find_jmol_jar_in_folder(saved_jmol)
            if jar:
                return _cmd("java", "-jar", jar)
            exe = SoftwareManager.resolve_executable_path(saved_jmol, "Jmol")
            if exe:
                return _cmd(exe)

        exe = SoftwareManager.get_software_path("Jmol") or SoftwareManager.auto_detect_path("Jmol")
        if exe:
            if exe.lower().endswith(".jar"):
                return _cmd("java", "-jar", exe)
            return _cmd(exe)

        for name in ("jmol", "Jmol", "jmol.exe", "Jmol.exe"):
            exe = shutil.which(name)
            if exe:
                return _cmd(exe)
        return None

    def view_external(self, viewer):
        if not self.geom.get("1.0", tk.END).strip():
            messagebox.showwarning("Empty Geometry", "Please load or paste coordinates first.")
            return

        self._show_toast(f"Opening {viewer} externally. This may take a few seconds...", duration_ms=4000)

        try:
            viewer_norm = (viewer or "").strip().lower()
            if viewer == "Jmol":
                temp_file_name = self._create_temp_xyz()
                if not temp_file_name:
                    return
                cmd = self._find_jmol_command(temp_file_name)
                if cmd:
                    subprocess.Popen(cmd)
                else:
                    self._prompt_viewer_path_setup("Jmol", "Jmol not found.")
            elif viewer_norm.startswith("avogadro"):
                temp_file_name = self._create_temp_xyz()
                if not temp_file_name:
                    return
                exe = self._find_avogadro_exe()
                if exe:
                    self._popen_viewer_exe(exe, temp_file_name)
                else:
                    self._prompt_viewer_path_setup("Avogadro", "Could not launch Avogadro.")
            elif viewer == "GaussView":
                path = self._write_temp_gjf()
                if not path:
                    return
                exe = self._find_gaussview_exe()
                if exe:
                    self._popen_viewer_exe(exe, path)
                else:
                    try:
                        subprocess.Popen(["gview", path])
                    except FileNotFoundError:
                        if os.name == 'nt':
                            os.startfile(path)
                        else:
                            show_software_not_found_dialog("GaussView", self.parent)
            elif viewer == "Chemcraft":
                temp_file_name = self._create_temp_xyz()
                if not temp_file_name:
                    return
                exe = self._find_chemcraft_exe()
                if exe:
                    self._popen_viewer_exe(exe, temp_file_name)
                else:
                    subprocess.Popen(["chemcraft", temp_file_name])
        except FileNotFoundError:
            show_software_not_found_dialog(viewer, self.parent)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch {viewer}:\n{str(e)}")

    def _embed_viewer(self, blank=False):
        if os.name != "nt":
            return

        viewer = self.embed_viewer_choice.get()
        viewer_norm = str(viewer or "").strip().lower()
        if viewer in ("Lightweight", "ACV ( AutoChemyViewer )"):
            self.embed_host.config(height=360)
            rows = [] if blank else _geom_lines_to_coord_rows(self.geom.get("1.0", tk.END))
            if not rows and not blank:
                pass
            self._terminate_embed_subprocess()
            self.struct_outer.config(text="Structure visualization")
            self._show_autochemy_structure(rows)
            self._update_embed_detach_button_state()
            return
        struct_path = None

        try:
            self.struct_outer.config(text=f"Structure visualization - Starting {viewer}...")
            for w in self.embed_host.winfo_children():
                w.destroy()
            tk.Label(
                self.embed_host, 
                text=f"Starting {viewer}...\nPlease wait.", 
                bg="#2b2b2b", fg="#FFD700", font=("Segoe UI", 16, "bold"), justify="center"
            ).place(relx=0.5, rely=0.5, anchor="center")
            self.frame.update_idletasks()
        except AttributeError:
            pass

        if not blank:
            if viewer == "GaussView":
                struct_path = self._write_temp_gjf()
            else:
                struct_path = self._create_temp_xyz()

        self._terminate_embed_subprocess()
        self._embed_prelaunch_windows = self._snapshot_top_windows()

        if viewer not in ("Jmol", "Chemcraft", "GaussView", "Avogadro", "ACV ( AutoChemyViewer )", "Lightweight") and not viewer_norm.startswith("avogadro"):
            custom_path = SoftwareManager.get_software_path(viewer)
            if custom_path and os.path.exists(custom_path):
                self._popen_viewer_exe(custom_path, struct_path)
                self.struct_outer.config(text=f"Structure visualization - {viewer} (External)")
                for w in self.embed_host.winfo_children():
                    w.destroy()
                tk.Label(
                    self.embed_host, 
                    text=f"{viewer} opened externally.\nCheck your taskbar.", 
                    bg="#2b2b2b", fg="#0b5cab", font=("Segoe UI", 16, "bold"), justify="center"
                ).place(relx=0.5, rely=0.5, anchor="center")
                return
            else:
                messagebox.showwarning("Visualizer", f"Could not find path for {viewer}.")
                return

        if viewer == "Jmol":
            cmd = self._find_jmol_command(struct_path)
            if cmd:
                self.embed_subprocess = subprocess.Popen(cmd)
            else:
                self._prompt_viewer_path_setup("Jmol", "No Jmol found.")
                return
        elif viewer == "GaussView":
            exe = self._find_gaussview_exe()
            if not exe:
                self._prompt_viewer_path_setup("GaussView", "gview.exe not found. Set GAUSSVIEW_EXE or GAUSS_EXEDIR, or add GaussView in software paths.")
                return
            self.embed_subprocess = self._popen_viewer_exe(exe, struct_path)
        elif viewer == "Chemcraft":
            exe = self._find_chemcraft_exe()
            if not exe:
                self._prompt_viewer_path_setup("Chemcraft", "Chemcraft not found. Set CHEMCRAFT_EXE or add Chemcraft in software paths.")
                return
            self.embed_subprocess = self._popen_viewer_exe(exe, struct_path)
        elif viewer_norm.startswith("avogadro"):
            exe = self._find_avogadro_exe()
            if not exe:
                self._prompt_viewer_path_setup("Avogadro", "Avogadro not found. Add its install folder or executable in software paths.")
                return
            self.embed_subprocess = self._popen_viewer_exe(exe, struct_path)
        else:
            return

        delay = {"GaussView": 700, "Chemcraft": 1400, "Jmol": 220, "Avogadro": 1200}.get(viewer, 1200 if viewer_norm.startswith("avogadro") else 260)
        self.frame.after(delay, lambda: self._wait_and_reparent(self.embed_subprocess.pid, 0, viewer))

    def _snapshot_top_windows(self):
        if os.name != "nt":
            return set()
        hwnds = set()

        def callback(hwnd, param):
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                hwnds.add(int(hwnd))
            return True

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        self._embed_snapshot_enum_proc = EnumWindowsProc(callback)
        ctypes.windll.user32.EnumWindows(self._embed_snapshot_enum_proc, 0)
        return hwnds

    def _wait_and_reparent(self, pid, attempts, viewer):
        if attempts > 140:
            try:
                self.struct_outer.config(text="Structure visualization - Failed to embed")
                for w in self.embed_host.winfo_children():
                    w.destroy()
                tk.Label(
                    self.embed_host, text="Failed to Embed Viewer\nCheck installation path",
                    bg="#2b2b2b", fg="#FF4C4C", font=("Segoe UI", 14, "bold"), justify="center"
                ).place(relx=0.5, rely=0.5, anchor="center")
            except AttributeError:
                pass
            self._update_embed_detach_button_state()
            return

        hwnd = self._find_embed_window(pid, viewer)
        if hwnd:
            try:
                self.struct_outer.config(text="Structure visualization")
            except AttributeError:
                pass
            GWL_STYLE = -16
            GWL_EXSTYLE = -20
            self._embed_saved_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            self._embed_saved_exstyle = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            self._viewer_detached = False
            self.embed_hwnd = hwnd
            WS_VISIBLE = 0x10000000
            WS_CHILD = 0x40000000
            WS_POPUP = 0x80000000
            WS_CAPTION = 0x00C00000
            WS_THICKFRAME = 0x00040000
            WS_MINIMIZEBOX = 0x00020000
            WS_MAXIMIZEBOX = 0x00010000
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080

            tk_hwnd = self.embed_host.winfo_id()
            ctypes.windll.user32.SetParent(hwnd, tk_hwnd)
            new_style = (self._embed_saved_style | WS_VISIBLE | WS_CHILD) & ~(
                WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
            )
            new_exstyle = (self._embed_saved_exstyle & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_int32(new_style).value)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_int32(new_exstyle).value)
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            SWP_NOACTIVATE = 0x0010
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
            ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
            self._resize_embedded()
            # Extra delayed resizes help Jmol paint correctly after reparent.
            self.frame.after(200, self._resize_embedded)
            self.frame.after(700, self._resize_embedded)
            self.frame.after(1200, self._resize_embedded)
            self._update_embed_detach_button_state()
        else:
            self.frame.after(100, lambda: self._wait_and_reparent(pid, attempts + 1, viewer))

    def _find_embed_window(self, target_pid, viewer):
        pid_match = None
        title_match = None
        new_window_match = None
        viewer = viewer or "Jmol"
        viewer_key = str(viewer).strip().lower()
        prelaunch = getattr(self, "_embed_prelaunch_windows", set()) or set()

        def window_size_ok(hwnd) -> bool:
            rect = ctypes.wintypes.RECT()
            try:
                ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                return (rect.right - rect.left) >= 180 and (rect.bottom - rect.top) >= 120
            except Exception:
                return True

        def viewer_window_ok(t: str, cname: str) -> bool:
            tl = (t or "").lower()
            cl = (cname or "").lower()
            if viewer_key == "jmol":
                return ("jmol" in tl) or (".xyz" in tl) or ("sunawt" in cl)
            if viewer_key == "chemcraft":
                return ("chemcraft" in tl) or ("chemistry" in tl) or (".xyz" in tl) or ("qt" in cl) or ("qwidget" in cl)
            if viewer_key == "gaussview":
                return ("gauss" in tl) or ("gview" in tl) or (".gjf" in tl) or ("qt" in cl) or ("qwidget" in cl)
            if viewer_key.startswith("avogadro"):
                return ("avogadro" in tl) or (".xyz" in tl) or ("qt" in cl) or ("qwidget" in cl) or ("qwindow" in cl)
            return True

        def title_fallback(t: str, cname: str) -> bool:
            tl = t.lower()
            cl = cname.lower()
            if viewer_key == "jmol":
                return "jmol" in tl or (("sunawt" in cl) and (".xyz" in tl))
            if viewer_key == "chemcraft":
                return "chemcraft" in tl or "chemistry" in tl or (".xyz" in tl and ("qt" in cl or "qwidget" in cl or "qwindow" in cl))
            if viewer_key == "gaussview":
                return "gauss" in tl or "gview" in tl or (".gjf" in tl and ("qt" in cl or "qwidget" in cl or "qwindow" in cl))
            if viewer_key.startswith("avogadro"):
                return "avogadro" in tl or (".xyz" in tl and ("qwindow" in cl or "qwidget" in cl or "qt" in cl))
            return False

        def new_window_fallback(t: str, cname: str) -> bool:
            tl = (t or "").lower()
            cl = (cname or "").lower()
            if viewer_key in ("chemcraft", "gaussview") or viewer_key.startswith("avogadro"):
                if "qt" in cl or "qwidget" in cl or "qwindow" in cl:
                    return True
            if viewer_key == "jmol" and ("sunawt" in cl or "jmol" in tl):
                return True
            return False

        def callback(hwnd, param):
            nonlocal pid_match, title_match, new_window_match
            if not ctypes.windll.user32.IsWindowVisible(hwnd):
                return True

            class_buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(hwnd, class_buf, 256)
            if class_buf.value == "ConsoleWindowClass":
                return True
            if not window_size_ok(hwnd):
                return True

            pid_ptr = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_ptr))
            
            buff_text = ""
            WM_GETTEXTLENGTH = 0x000E
            WM_GETTEXT = 0x000D
            SMTO_ABORTIFHUNG = 0x0002
            
            length_res = ctypes.c_ulong()
            res1 = ctypes.windll.user32.SendMessageTimeoutW(hwnd, WM_GETTEXTLENGTH, 0, 0, SMTO_ABORTIFHUNG, 50, ctypes.byref(length_res))
            if res1 != 0 and length_res.value > 0:
                length = length_res.value + 1
                buff = ctypes.create_unicode_buffer(length)
                text_res = ctypes.c_ulong()
                res2 = ctypes.windll.user32.SendMessageTimeoutW(hwnd, WM_GETTEXT, length, ctypes.cast(buff, ctypes.c_void_p), SMTO_ABORTIFHUNG, 50, ctypes.byref(text_res))
                if res2 != 0:
                    buff_text = buff.value

            if pid_ptr.value == target_pid:
                if viewer_window_ok(buff_text, class_buf.value):
                    pid_match = hwnd
                    return False

            if buff_text and title_fallback(buff_text, class_buf.value):
                title_match = hwnd

            if (
                new_window_match is None
                and int(hwnd) not in prelaunch
                and new_window_fallback(buff_text, class_buf.value)
            ):
                new_window_match = hwnd

            return True

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        self._embed_enum_proc = EnumWindowsProc(callback)
        ctypes.windll.user32.EnumWindows(self._embed_enum_proc, 0)
        return pid_match if pid_match else (title_match if title_match else new_window_match)

    def _resize_embedded(self, event=None):
        if self._viewer_detached:
            return
        if self.embed_hwnd:
            w = self.embed_host.winfo_width()
            h = self.embed_host.winfo_height()
            if w > 1 and h > 1:
                user32 = ctypes.windll.user32
                hwnd = self.embed_hwnd
                user32.MoveWindow(hwnd, 0, 0, w, h, True)
                
                # Enforce stripped styles just in case Qt/Avogadro restored them
                GWL_STYLE = -16
                WS_VISIBLE = 0x10000000
                WS_CHILD = 0x40000000
                WS_POPUP = 0x80000000
                WS_CAPTION = 0x00C00000
                WS_THICKFRAME = 0x00040000
                WS_MINIMIZEBOX = 0x00020000
                WS_MAXIMIZEBOX = 0x00010000
                current_style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                new_style = (current_style | WS_VISIBLE | WS_CHILD) & ~(
                    WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
                )
                if current_style != new_style:
                    user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_int32(new_style).value)

                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                SWP_FRAMECHANGED = 0x0020
                user32.SetWindowPos(hwnd, 0, 0, 0, w, h, SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
                WM_SIZE = 0x0005
                SIZE_RESTORED = 0
                lparam = (h << 16) | (w & 0xFFFF)
                user32.SendMessageW(hwnd, WM_SIZE, SIZE_RESTORED, lparam)
                user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0100 | 0x0400)
    def _focus_generated_orca_input(self) -> None:
        nb = getattr(self, "_preview_notebook", None)
        if nb is not None:
            try:
                nb.select(0)
            except tk.TclError:
                pass
        try:
            self.txt_inp.see("1.0")
        except tk.TclError:
            pass

    def _open_detailed_orca_inp_dialog_from_preview(self) -> None:
        raw = self.txt_inp.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showwarning(
                "Detailed editor",
                "The Input (.inp) preview is empty.\n\nClick **Generate Preview** first to build an input.",
                parent=self.parent,
            )
            return
        self._focus_generated_orca_input()
        self._open_detailed_orca_inp_dialog()

    def _open_detailed_orca_inp_dialog(self) -> None:
        top = tk.Toplevel(self.parent.winfo_toplevel())
        top.title("ORCA input — detailed edit")
        top.geometry("780x580")
        top.minsize(520, 360)
        body = ttk.Frame(top, padding=10)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            body,
            text=(
                "Edit the full ORCA input here. Apply copies back to the main preview on the right. "
                "For structured settings use the left tabs (Theory & Job Type, Functional & Basis Sets, …), "
                "then Generate Preview again if you change method or geometry."
            ),
            wraplength=740,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 8))
        row_btns = ttk.Frame(body)
        row_btns.pack(fill=tk.X, pady=(0, 6))

        def goto_theory():
            nb = getattr(self, "_main_notebook", None)
            if nb is not None:
                try:
                    nb.select(0)
                except tk.TclError:
                    pass
            top.lift()

        def goto_method():
            nb = getattr(self, "_main_notebook", None)
            if nb is not None:
                try:
                    nb.select(1)
                except tk.TclError:
                    pass
            top.lift()

        ttk.Button(row_btns, text="Open: Theory & job type", command=goto_theory).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(row_btns, text="Open: Functional & basis", command=goto_method).pack(side=tk.LEFT)

        wrap = ttk.Frame(body)
        wrap.pack(fill=tk.BOTH, expand=True)
        txt = tk.Text(wrap, font=("Consolas", 11), wrap=tk.NONE, undo=True)
        sy = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=txt.yview)
        sx = ttk.Scrollbar(wrap, orient=tk.HORIZONTAL, command=txt.xview)
        txt.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        txt.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        txt.insert("1.0", self.txt_inp.get("1.0", tk.END))
        if app_theme:
            try:
                top_app = self.parent.winfo_toplevel()
                app = getattr(top_app, "_orca_app", None)
                if app:
                    ctx = app_theme.build_context(app.theme_mode, app.editor_font_pt)
                    app_theme.apply_editor_style(txt, ctx)
            except Exception:
                pass

        bot = ttk.Frame(body)
        bot.pack(fill=tk.X, pady=(10, 0))

        def apply_to_preview():
            self.txt_inp.delete("1.0", tk.END)
            self.txt_inp.insert("1.0", txt.get("1.0", tk.END))
            self._focus_generated_orca_input()
            top.destroy()

        ttk.Button(bot, text="Close", command=top.destroy).pack(side=tk.RIGHT)
        ttk.Button(bot, text="Apply to main preview", command=apply_to_preview).pack(side=tk.RIGHT, padx=(0, 8))

    def _open_detailed_sh_dialog_from_preview(self) -> None:
        raw = self.txt_sh.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showwarning(
                "Detailed editor",
                "The Job Script (.sh) preview is empty.\n\nClick **Generate Preview** first to build a job script.",
                parent=self.parent,
            )
            return
        
        nb = getattr(self, "preview_nb", None)
        if nb is not None:
            try:
                nb.select(1)
            except Exception:
                pass
        self._open_detailed_sh_dialog()

    def _open_detailed_sh_dialog(self) -> None:
        top = tk.Toplevel(self.parent.winfo_toplevel())
        top.title("Job Script \u2014 detached edit")
        top.geometry("780x580")
        top.minsize(520, 360)
        body = ttk.Frame(top, padding=10)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            body,
            text=(
                "Edit the full Job Script here. Apply copies back to the main preview on the right."
            ),
            wraplength=740,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 8))

        wrap = ttk.Frame(body)
        wrap.pack(fill=tk.BOTH, expand=True)
        txt = tk.Text(wrap, font=("Consolas", 11), wrap=tk.NONE, undo=True)
        sy = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=txt.yview)
        sx = ttk.Scrollbar(wrap, orient=tk.HORIZONTAL, command=txt.xview)
        txt.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        txt.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        txt.insert("1.0", self.txt_sh.get("1.0", tk.END))
        if app_theme:
            try:
                top_app = self.parent.winfo_toplevel()
                app = getattr(top_app, "_orca_app", None)
                if app:
                    ctx = app_theme.build_context(app.theme_mode, app.editor_font_pt)
                    app_theme.apply_editor_style(txt, ctx)
            except Exception:
                pass

        bot = ttk.Frame(body)
        bot.pack(fill=tk.X, pady=(10, 0))

        def apply_to_preview():
            self.txt_sh.delete("1.0", tk.END)
            self.txt_sh.insert("1.0", txt.get("1.0", tk.END))
            top.destroy()

        ttk.Button(bot, text="Close", command=top.destroy).pack(side=tk.RIGHT)
        ttk.Button(bot, text="Apply to main preview", command=apply_to_preview).pack(side=tk.RIGHT, padx=(0, 8))

    def _required_atom_count_for_kind(self, kind: str) -> int:
        k = (kind or "").strip().lower()
        if k.startswith("bond") or k == "b":
            return 2
        if k.startswith("angle") or k == "a":
            return 3
        return 4

    def _geometry_rows_for_picker(self):
        rows = _geom_lines_to_coord_rows(self.geom.get("1.0", tk.END))
        if not rows:
            messagebox.showwarning(
                "Atom picker",
                "Geometry is empty or invalid.\nLoad/paste valid XYZ coordinates first.",
                parent=self.parent,
            )
            return []
        return rows

    def _open_atom_picker_dialog(self, title: str, needed: int, constraint_target=None):
        rows = self._geometry_rows_for_picker()
        if not rows:
            return None
        top = tk.Toplevel(self.parent.winfo_toplevel())
        top.title(f"{title} — lightweight picker")
        win_w, win_h = 1360, 780
        top.geometry(f"{win_w}x{win_h}")
        top.minsize(1100, 680)
        try:
            top.update_idletasks()
            sw = top.winfo_screenwidth()
            sh = top.winfo_screenheight()
            sx = max(0, (sw - win_w) // 2)
            sy = max(0, (sh - win_h) // 2 - 20)
            top.geometry(f"{win_w}x{win_h}+{sx}+{sy}")
        except Exception:
            pass
        body = ttk.Frame(top, padding=10)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            body,
            text=(
                f"Select exactly {needed} atom(s). "
                "Click atoms in canvas or list. ORCA indices are 0-based and auto-filled."
            ),
            wraplength=820,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 8))
        controls = ttk.Frame(body)
        controls.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(controls, text="Reset view", command=lambda: None).pack(side=tk.LEFT)
        ttk.Button(controls, text="Open external visualizer", command=self._visualize_geometry_from_editor).pack(side=tk.LEFT, padx=(6, 0))
        status_var = tk.StringVar(value="No atoms selected.")
        ttk.Label(controls, textvariable=status_var).pack(side=tk.LEFT, padx=(10, 0))

        split = ttk.PanedWindow(body, orient=tk.HORIZONTAL)
        split.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(split)
        right = ttk.Frame(split)
        split.add(left, weight=4)
        split.add(right, weight=2)
        is_dark = False
        try:
            top_app = self.parent.winfo_toplevel()
            app = getattr(top_app, "_orca_app", None)
            mode = str(getattr(app, "theme_mode", "")).lower() if app else ""
            is_dark = mode in ("dark", "black")
        except Exception:
            is_dark = False
        lb = tk.Listbox(right, selectmode=tk.EXTENDED, font=("Consolas", 10))
        sy = ttk.Scrollbar(right, orient=tk.VERTICAL, command=lb.yview)
        lb.config(yscrollcommand=sy.set)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)

        selected = {"vals": None}
        current_target = {"cv": constraint_target}
        selected_set = set()
        _syncing = {"v": False}

        for i, (sym, xs, ys, zs) in enumerate(rows, start=1):
            lb.insert(tk.END, f"{i:>3}  {sym:<2}  {float(xs):>10.5f} {float(ys):>10.5f} {float(zs):>10.5f}   (ORCA {i-1})")

        def _update_status():
            status_var.set(f"Selected: {len(selected_set)} / {needed}")

        def _collect_selected_vals():
            idxs = sorted(selected_set)
            if len(idxs) != needed:
                messagebox.showwarning(
                    "Atom picker",
                    f"Please select exactly {needed} atoms.",
                    parent=top,
                )
                return None
            return [str(i) for i in idxs]

        def _apply_vals_to_constraint(cv, vals):
            if not cv or not vals:
                return
            cv["a1"].set(vals[0] if len(vals) > 0 else "")
            cv["a2"].set(vals[1] if len(vals) > 1 else "")
            cv["a3"].set(vals[2] if len(vals) > 2 else "")
            cv["a4"].set(vals[3] if len(vals) > 3 else "")

        def _sync(*_):
            if _syncing["v"]:
                return
            idxs = set(lb.curselection())
            if len(idxs) > needed:
                messagebox.showinfo("Atom picker", f"Please select only {needed} atoms.", parent=top)
                idxs = set(sorted(idxs)[:needed])
                _syncing["v"] = True
                lb.selection_clear(0, tk.END)
                for i in sorted(idxs):
                    lb.selection_set(i)
                _syncing["v"] = False
            selected_set.clear()
            selected_set.update(idxs)
            _update_status()
            viewer.set_selected_indices(idxs)

        lb.bind("<<ListboxSelect>>", _sync)

        def _reset_view():
            viewer.reset_view()

        reset_btn = controls.winfo_children()[0]
        reset_btn.configure(command=_reset_view)

        def _viewer_selection_changed(idxs):
            _syncing["v"] = True
            lb.selection_clear(0, tk.END)
            for i in sorted(idxs):
                lb.selection_set(i)
            _syncing["v"] = False
            selected_set.clear()
            selected_set.update(idxs)
            _update_status()

        viewer = AutoChemyViewer(
            left,
            rows,
            is_dark=is_dark,
            max_selection=needed,
            on_selection_change=_viewer_selection_changed,
        )
        _update_status()

        btns = ttk.Frame(body)
        btns.pack(fill=tk.X, pady=(8, 0))

        def _ok():
            vals = _collect_selected_vals()
            if not vals:
                return
            selected["vals"] = vals  # ORCA 0-based
            top.destroy()
        if constraint_target is not None:
            target_lbl = tk.StringVar(value="Target: current constraint row")
            ttk.Label(btns, textvariable=target_lbl).pack(side=tk.LEFT)

            def _apply_here():
                vals = _collect_selected_vals()
                if not vals:
                    return
                _apply_vals_to_constraint(current_target["cv"], vals)

            def _apply_add_next():
                vals = _collect_selected_vals()
                if not vals:
                    return
                _apply_vals_to_constraint(current_target["cv"], vals)
                add_fn = getattr(self, "_add_constraint_ui", None)
                if callable(add_fn):
                    add_fn()
                    if getattr(self, "constraint_rows", None):
                        current_target["cv"] = self.constraint_rows[-1]
                        target_lbl.set(f"Target: new constraint row {len(self.constraint_rows)}")
                selected_set.clear()
                viewer.set_selected_indices([])
                _syncing["v"] = True
                lb.selection_clear(0, tk.END)
                _syncing["v"] = False
                _update_status()

            def _remove_current():
                cv = current_target["cv"]
                if not cv:
                    return
                try:
                    frm = cv.get("frame")
                    if frm is not None:
                        frm.destroy()
                except Exception:
                    pass
                try:
                    if cv in self.constraint_rows:
                        self.constraint_rows.remove(cv)
                except Exception:
                    pass
                if not self.constraint_rows and callable(getattr(self, "_add_constraint_ui", None)):
                    self._add_constraint_ui()
                current_target["cv"] = self.constraint_rows[-1] if self.constraint_rows else None
                target_lbl.set(
                    f"Target: constraint row {len(self.constraint_rows)}"
                    if self.constraint_rows else "Target: none"
                )

            ttk.Button(btns, text="Done", command=top.destroy).pack(side=tk.RIGHT)
            ttk.Button(btns, text="Apply + Add Next", command=_apply_add_next).pack(side=tk.RIGHT, padx=(0, 8))
            ttk.Button(btns, text="Apply Here", command=_apply_here).pack(side=tk.RIGHT, padx=(0, 8))
            ttk.Button(btns, text="Remove Row", command=_remove_current).pack(side=tk.RIGHT, padx=(0, 8))
        else:
            ttk.Button(btns, text="Cancel", command=top.destroy).pack(side=tk.RIGHT)
            ttk.Button(btns, text="Use selected atoms", command=_ok).pack(side=tk.RIGHT, padx=(0, 8))

        top.transient(self.parent.winfo_toplevel())
        top.grab_set()
        top.wait_window()
        return selected["vals"]

    def _pick_scan_atoms(self):
        t = self.scan_ctype.get() if self.task.get() == "Scan" and self.subtask.get() == "Constrained Scan" else self.subtask.get()
        needed = self._required_atom_count_for_kind(t)
        vals = self._open_atom_picker_dialog("Pick atoms for scan", needed)
        if not vals:
            return
        self.scan_a1.set(vals[0] if len(vals) > 0 else "")
        self.scan_a2.set(vals[1] if len(vals) > 1 else "")
        self.scan_a3.set(vals[2] if len(vals) > 2 else "")
        self.scan_a4.set(vals[3] if len(vals) > 3 else "")

    def _pick_constraint_atoms(self, cv):
        kind = cv["type"].get()
        needed = self._required_atom_count_for_kind(kind)
        vals = self._open_atom_picker_dialog("Pick atoms for constraint", needed, constraint_target=cv)
        if not vals:
            return
        cv["a1"].set(vals[0] if len(vals) > 0 else "")
        cv["a2"].set(vals[1] if len(vals) > 1 else "")
        cv["a3"].set(vals[2] if len(vals) > 2 else "")
        cv["a4"].set(vals[3] if len(vals) > 3 else "")

    def _show_autochemy_structure(self, rows, target_host=None):
        if not rows:
            return
        self._last_autochemy_rows = list(rows)
        host = target_host if target_host is not None else self.embed_host
        for w in host.winfo_children():
            w.destroy()
        is_dark = False
        try:
            top_app = self.parent.winfo_toplevel()
            app = getattr(top_app, "_orca_app", None)
            mode = str(getattr(app, "theme_mode", "")).lower() if app else ""
            is_dark = mode in ("dark", "black")
        except Exception:
            is_dark = False
        self._autochemy_viewer = AutoChemyViewer(
            host,
            rows,
            is_dark=is_dark,
        )

    @staticmethod
    def _parse_orca_major_version(text: str) -> int | None:
        t = (text or "").strip()
        if not t:
            return None
        m = re.search(r"(\d+)\.(\d+)(?:\.\d+)?", t)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
        m = re.search(r"\b(\d+)\b", t)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
        return None



    def _orca_xtb_keyword(self) -> str:
        method = (self.semi_method.get() or "").strip()
        mapping = {
            "XTB": "GFN-XTB",
            "GFN-xTB": "GFN-XTB",
            "GFN2-xTB": "GFN2-XTB",
            "GFN1-xTB": "GFN1-XTB",
            "GFN0-xTB": "GFN0-XTB",
            "g-xTB": "G-XTB",
        }
        return mapping.get(method, method)

    def _current_orca_major_version(self) -> int | None:
        candidates = [
            getattr(self, "local_orca_status_var", tk.StringVar()).get(),
            getattr(self, "local_orca_version_combo", tk.StringVar()).get() if hasattr(self, "local_orca_version_combo") else "",
            self.orca_module.get(),
            self.orca_path.get(),
        ]
        for text in candidates:
            major = self._parse_orca_major_version(text)
            if major is not None:
                return major
        return None

    def _warn_if_orca_xtb_selected(self, force_gxtb: bool = False) -> None:
        if self.method_type.get() != "Semiempirical":
            return
        method = (self.semi_method.get() or "").strip()
        if method not in ("XTB", "GFN-xTB", "GFN2-xTB", "GFN1-xTB", "GFN0-xTB", "g-xTB"):
            return
        major = self._current_orca_major_version()
        if not getattr(self, "_orca_xtb_warned", False):
            if major is not None and major < 6:
                msg = (
                    "ORCA xTB keywords are available in ORCA 6.0 and above.\n\n"
                    f"Your selected ORCA version looks like {major}.x, so this input may fail until ORCA is updated."
                )
            else:
                msg = (
                    "ORCA can run built-in xTB jobs from ORCA 6.0 and above.\n\n"
                    "Use an ORCA 6.x executable/module when submitting this input."
                )
            messagebox.showinfo("ORCA xTB availability", msg)
            self._orca_xtb_warned = True
        if method == "g-xTB" and (force_gxtb or not getattr(self, "_orca_gxtb_warned", False)):
            messagebox.showwarning(
                "g-xTB in ORCA",
                "g-xTB is not available in a stock ORCA installation yet.\n\n"
                "To run g-xTB through ORCA, install a new xTB build from GitHub, or take the new xtb.exe from AutoChemy and replace the xtb executable inside the ORCA installation's xTB folder."
            )
            self._orca_gxtb_warned = True

    def _generate_script_text(self):
        mode = self.exec_mode.get()
        custom_tmpl = None
        exact_match = False
        
        m = self.saved_machine_var.get()
        if m != "None" and m in self.saved_machines:
            saved = self.saved_machines[m]
            machine_mode = saved.get("machine_mode", "Dynamic")
            script_sub_mode = saved.get("script_sub_mode", "DynamicScript")
            
            def replace_custom_vars(tmpl, partition):
                if not tmpl or not partition: return tmpl
                for k, v in partition.get("custom_vars", {}).items():
                    tmpl = tmpl.replace(f"{{{{{k}}}}}", str(v))
                return tmpl
            
            q = self.queue.get()
            
            if machine_mode == "Static":
                exact_match = True
                if script_sub_mode == "StaticScript":
                    if "partitions" in saved:
                        for p in saved["partitions"]:
                            if p["name"] == q:
                                custom_tmpl = p.get("custom_script", "")
                                custom_tmpl = replace_custom_vars(custom_tmpl, p)
                                break
                else:
                    custom_tmpl = saved.get("custom_script", "")
                    if "partitions" in saved:
                        for p in saved["partitions"]:
                            if p["name"] == q:
                                custom_tmpl = replace_custom_vars(custom_tmpl, p)
                                break
                                
                if custom_tmpl:
                    custom_tmpl = custom_tmpl.replace("{{INPUT_NAME}}", self.filename.get())
                    custom_tmpl = custom_tmpl.replace("{{cores}}", self.nprocs.get())
                    custom_tmpl = custom_tmpl.replace("{{nodes}}", self.nodes.get())
                    custom_tmpl = custom_tmpl.replace("{{time}}", self.time.get())
            else:
                if saved.get("custom_script", "").strip():
                    custom_tmpl = saved["custom_script"].replace("{{INPUT_NAME}}", self.filename.get())
                    custom_tmpl = custom_tmpl.replace("{{cores}}", self.nprocs.get())
                    custom_tmpl = custom_tmpl.replace("{{nodes}}", self.nodes.get())
                    custom_tmpl = custom_tmpl.replace("{{time}}", self.time.get())
                    if "partitions" in saved:
                        for p in saved["partitions"]:
                            if p["name"] == q:
                                custom_tmpl = replace_custom_vars(custom_tmpl, p)
                                break
                exact_match = False
                
        if exact_match and custom_tmpl:
            return custom_tmpl

        mtype = self.method_type.get()
        semi_method = getattr(self, "semi_method", tk.StringVar()).get()
        # Input Creator now submits xTB keywords through ORCA; the xTB tab still handles direct xTB runs.
        is_xtb_mode = False
            
        orca_p = self.orca_path.get().strip()
        if not orca_p or orca_p == "N/A":
            orca_cmd = "orca"
            orca_cmd_win = "orca.exe"
        else:
            orca_cmd = f"{orca_p}/orca".replace("\\", "/")
            orca_cmd_win = f'"{orca_p}\\orca.exe"'.replace("/", "\\")

        if mode == "HPC":
            qsys = self.hpc_queue_system.get()
            cfg_type = getattr(self, "hpc_config_type", None)
            cfg_type = cfg_type.get() if cfg_type else "Modules"
            
            env_setup_lines = []
            if cfg_type == "Modules":
                env_setup_lines.append("module purge")
                if is_xtb_mode:
                    if getattr(self, "xtb_module", tk.StringVar()).get().strip():
                        env_setup_lines.append(f"module load {self.xtb_module.get().strip()}")
                else:
                    if self.mpi_module.get().strip():
                        env_setup_lines.append(f"module load {self.mpi_module.get().strip()}")
                    if self.orca_module.get().strip():
                        env_setup_lines.append(f"module load {self.orca_module.get().strip()}")
            else:
                if is_xtb_mode:
                    xtb_p = getattr(self, "xtb_path", tk.StringVar()).get().strip()
                    if xtb_p and xtb_p != "N/A":
                        xtb_p2 = xtb_p.rstrip("/\\")
                        env_setup_lines.append(f'export PATH="{xtb_p2}:$PATH"')
                else:
                    if self.mpi_path.get().strip() and self.mpi_path.get().strip() != "N/A":
                        mpi_p = self.mpi_path.get().strip().rstrip("/\\")
                        env_setup_lines.append(f'export PATH="{mpi_p}/bin:$PATH"')
                        env_setup_lines.append(f'export LD_LIBRARY_PATH="{mpi_p}/lib:$LD_LIBRARY_PATH"')
                    if self.orca_path.get().strip() and self.orca_path.get().strip() != "N/A":
                        orca_p2 = self.orca_path.get().strip().rstrip("/\\")
                        env_setup_lines.append(f'export PATH="{orca_p2}:$PATH"')
            
            env_setup = "\n".join(env_setup_lines)
            
            if custom_tmpl:
                tmpl = custom_tmpl
            elif qsys == "PBS":
                tmpl = PBS_TEMPLATE
            elif qsys == "Interactive":
                tmpl = HPC_INTERACTIVE_TEMPLATE
            else:
                tmpl = SLURM_TEMPLATE
            try:
                if is_xtb_mode:
                    xtb_p = getattr(self, "xtb_path", tk.StringVar()).get().strip()
                    xtb_exe = "xtb"
                    if xtb_p and xtb_p != "N/A":
                        xtb_exe = f"{xtb_p}/xtb".replace("\\", "/")
                        
                    flags = []
                    if semi_method == "g-xTB": flags.append("--gxtb")
                    elif semi_method == "GFN2-xTB": flags.append("--gfn 2")
                    elif semi_method == "GFN1-xTB": flags.append("--gfn 1")
                    elif semi_method == "GFN0-xTB": flags.append("--gfn 0")
                    
                    task = self.task.get()
                    if task == "Optimisation": flags.append("--opt")
                    elif task in ["Frequency", "Optimisation + Frequency", "Transition State (TS)"]: flags.append("--hess")
                    
                    chrg = self.charge.get().strip()
                    try: uhf = str(max(0, int(self.mult.get().strip()) - 1))
                    except: uhf = "0"
                    flags.extend([f"--chrg {chrg}", f"--uhf {uhf}"])
                    
                    if getattr(self, "solvation", tk.StringVar()).get() == "With solvent":
                        sv = getattr(self, "solvent", tk.StringVar()).get().strip().lower()
                        if sv: flags.append(f"--gbe {sv}")
                        
                    flag_str = " ".join(flags)
                    fname = self.filename.get()
                    
                    tmpl = re.sub(r'\{orca_cmd\}\s+"\{job_name\}\.inp"', '{orca_cmd}', tmpl)
                    orca_cmd = f'{xtb_exe} "{fname}.xyz" {flag_str}'
                    orca_cmd_win = orca_cmd

                return tmpl.format(
                    job_name=self.filename.get(), queue=self.queue.get(),
                    nodes=self.nodes.get(), nprocs=self.nprocs.get(), time=self.time.get(),
                    scratch_dir=self.scratch_dir.get(),
                    env_setup=env_setup,
                    orca_cmd=orca_cmd,
                    mod_prefix="" if cfg_type == "Modules" else "#",
                    path_prefix="" if cfg_type != "Modules" else "#",
                    mpi_module=self.mpi_module.get(),
                    orca_module=self.orca_module.get(),
                    mpi_path=self.mpi_path.get(),
                    orca_path=self.orca_path.get()
                )
            except Exception:
                return tmpl
        elif mode == "Workstation":
            ws_os = self.workstation_os.get()
            ws_run = self.workstation_run_mode.get()
            
            env_setup_lines = []
            if ws_os == "Windows":
                if is_xtb_mode:
                    xtb_p = getattr(self, "xtb_path", tk.StringVar()).get().strip()
                    if xtb_p and xtb_p != "N/A":
                        xtb_p2 = xtb_p.rstrip("/\\")
                        env_setup_lines.append(f'set PATH={xtb_p2};%PATH%')
                else:
                    if self.mpi_path.get().strip() and self.mpi_path.get().strip() != "N/A":
                        mpi_p = self.mpi_path.get().strip().rstrip("/\\")
                        env_setup_lines.append(f'set PATH={mpi_p}\\bin;%PATH%')
                    if self.orca_path.get().strip() and self.orca_path.get().strip() != "N/A":
                        orca_p2 = self.orca_path.get().strip().rstrip("/\\")
                        env_setup_lines.append(f'set PATH={orca_p2};%PATH%')
            else:
                if is_xtb_mode:
                    xtb_p = getattr(self, "xtb_path", tk.StringVar()).get().strip()
                    if xtb_p and xtb_p != "N/A":
                        xtb_p2 = xtb_p.rstrip("/\\")
                        env_setup_lines.append(f'export PATH="{xtb_p2}:$PATH"')
                else:
                    if self.mpi_path.get().strip() and self.mpi_path.get().strip() != "N/A":
                        mpi_p = self.mpi_path.get().strip().rstrip("/\\")
                        env_setup_lines.append(f'export PATH="{mpi_p}/bin:$PATH"')
                        env_setup_lines.append(f'export LD_LIBRARY_PATH="{mpi_p}/lib:$LD_LIBRARY_PATH"')
                    if self.orca_path.get().strip() and self.orca_path.get().strip() != "N/A":
                        orca_p2 = self.orca_path.get().strip().rstrip("/\\")
                        env_setup_lines.append(f'export PATH="{orca_p2}:$PATH"')
            
            env_setup = "\n".join(env_setup_lines)

            if ws_os == "Windows":
                tmpl = custom_tmpl if custom_tmpl else (WS_WINDOWS_BAT_SCRATCH if ws_run == "Scratch" else WS_WINDOWS_BAT_DIRECT)
            else:
                tmpl = custom_tmpl if custom_tmpl else (WS_LINUX_SH_SCRATCH if ws_run == "Scratch" else WS_LINUX_SH_DIRECT)
            try:
                if is_xtb_mode:
                    xtb_p = getattr(self, "xtb_path", tk.StringVar()).get().strip()
                    xtb_exe = "xtb"
                    if xtb_p and xtb_p != "N/A":
                        xtb_exe = f"{xtb_p}/xtb".replace("\\", "/")
                        
                    flags = []
                    if semi_method == "g-xTB": flags.append("--gxtb")
                    elif semi_method == "GFN2-xTB": flags.append("--gfn 2")
                    elif semi_method == "GFN1-xTB": flags.append("--gfn 1")
                    elif semi_method == "GFN0-xTB": flags.append("--gfn 0")
                    
                    task = self.task.get()
                    if task == "Optimisation": flags.append("--opt")
                    elif task in ["Frequency", "Optimisation + Frequency", "Transition State (TS)"]: flags.append("--hess")
                    
                    chrg = self.charge.get().strip()
                    try: uhf = str(max(0, int(self.mult.get().strip()) - 1))
                    except: uhf = "0"
                    flags.extend([f"--chrg {chrg}", f"--uhf {uhf}"])
                    
                    if getattr(self, "solvation", tk.StringVar()).get() == "With solvent":
                        sv = getattr(self, "solvent", tk.StringVar()).get().strip().lower()
                        if sv: flags.append(f"--gbe {sv}")
                        
                    flag_str = " ".join(flags)
                    fname = self.filename.get()
                    
                    tmpl = re.sub(r'\{orca_cmd\}\s+"\{job_name\}\.inp"', '{orca_cmd}', tmpl)
                    orca_cmd = f'{xtb_exe} "{fname}.xyz" {flag_str}'
                    orca_cmd_win = orca_cmd

                return tmpl.format(
                    job_name=self.filename.get(),
                    scratch_dir=self.scratch_dir.get(),
                    env_setup=env_setup,
                    orca_cmd=orca_cmd,
                    orca_cmd_win=orca_cmd_win,
                    mod_prefix="", path_prefix="",
                    mpi_module="", orca_module="",
                    mpi_path=self.mpi_path.get(),
                    orca_path=self.orca_path.get()
                )
            except Exception:
                return tmpl
        else:
            return f"# Local Execution selected.\n# Run via terminal: orca {self.filename.get()}.inp > {self.filename.get()}.out"

    def _update_task_cb_values(self):
        base_values = [
            "Scan", "Optimisation", "Frequency", "Optimisation + Frequency", 
            "Transition State (TS)", "IRC", "Single Point", "Thermal Correction", "CASSCF", "Custom", "Other"
        ]
        
        if hasattr(self, "method_type") and self.method_type.get() == "CCSD":
            base_values = ["Single Point"]
            
        if hasattr(self, "task_cb"):
            self.task_cb['values'] = base_values
        
        current = self.task.get()
        if not current:
            self.task.set("Optimisation + Frequency" if "Optimisation + Frequency" in base_values else base_values[0])
        elif current.startswith("[Custom]"):
            # Migrate old setting
            c_name = current.replace("[Custom] ", "").strip()
            self.task.set("Custom")
            self.subtask.set(c_name)
        elif current not in base_values:
            self.task.set(base_values[0])

    def _open_custom_jobs_manager(self, prefill_name=None, prefill_text=None):
        top = tk.Toplevel(self.parent)
        top.title("Manage Custom Jobs")
        top.geometry("600x450")
        top.grab_set()
    
        main_pw = ttk.PanedWindow(top, orient=tk.HORIZONTAL)
        main_pw.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
        # Left side: list of jobs
        left_f = ttk.Frame(main_pw)
        main_pw.add(left_f, weight=1)
    
        ttk.Label(left_f, text="Saved Custom Jobs:").pack(anchor="w")
        listbox = tk.Listbox(left_f, selectmode=tk.SINGLE, font=("Segoe UI", 10))
        listbox.pack(fill=tk.BOTH, expand=True, pady=2)
    
        def _refresh_list():
            listbox.delete(0, tk.END)
            for k in self.custom_jobs.keys():
                listbox.insert(tk.END, k)
            
        _refresh_list()
    
        btn_f = ttk.Frame(left_f)
        btn_f.pack(fill=tk.X)
    
        # Right side: editor
        right_f = ttk.Frame(main_pw)
        main_pw.add(right_f, weight=3)
    
        ttk.Label(right_f, text="Job Name:").pack(anchor="w")
        name_var = tk.StringVar()
        name_entry = ttk.Entry(right_f, textvariable=name_var)
        name_entry.pack(fill=tk.X, pady=(0, 5))
    
        ttk.Label(right_f, text="Raw Input Template:").pack(anchor="w")
        text_area = tk.Text(right_f, height=10, font=("Consolas", 10))
        text_area.pack(fill=tk.BOTH, expand=True, pady=(0, 5))
    
        dyn_xyz_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(right_f, text="Dynamically add XYZ from Geometry block", variable=dyn_xyz_var).pack(anchor="w")
    
        dyn_cores_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(right_f, text="Dynamically inject cores (%pal nprocs) from settings", variable=dyn_cores_var).pack(anchor="w")
        
        gaussian_format_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(right_f, text="Gaussian Format (.gjf) [XYZ added in Gaussian style]", variable=gaussian_format_var).pack(anchor="w")

        gaussian_replacements_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(right_f, text="Replace %chk, %mem, %nprocshared from settings", variable=gaussian_replacements_var).pack(anchor="w")
    
        def _on_select(evt):
            sel = listbox.curselection()
            if not sel: return
            job_name = listbox.get(sel[0])
            job_data = self.custom_jobs.get(job_name, {})
            name_var.set(job_name)
            text_area.delete("1.0", tk.END)
            text_area.insert("1.0", job_data.get("text", ""))
            dyn_xyz_var.set(job_data.get("dynamic_xyz", True))
            dyn_cores_var.set(job_data.get("dynamic_cores", True))
            gaussian_format_var.set(job_data.get("gaussian_format", False))
            gaussian_replacements_var.set(job_data.get("gaussian_replacements", False))
        
        listbox.bind("<<ListboxSelect>>", _on_select)
    
        def _new_job():
            listbox.selection_clear(0, tk.END)
            name_var.set("New Custom Job")
            text_area.delete("1.0", tk.END)
            text_area.insert("1.0", "! B3LYP def2-SVP\n")
            dyn_xyz_var.set(True)
            dyn_cores_var.set(True)
            gaussian_format_var.set(False)
            gaussian_replacements_var.set(False)
        
        ttk.Button(btn_f, text="New", command=_new_job).pack(side=tk.LEFT, expand=True, fill=tk.X)
        
        if prefill_name:
            name_var.set(prefill_name)
            if prefill_text:
                text_area.delete("1.0", tk.END)
                text_area.insert("1.0", prefill_text)
        else:
            _new_job()
    
        def _delete_job():
            sel = listbox.curselection()
            if not sel: return
            job_name = listbox.get(sel[0])
            if messagebox.askyesno("Confirm", f"Delete custom job '{job_name}'?", parent=top):
                del self.custom_jobs[job_name]
                self._save_custom_jobs()
                self._update_task_cb_values()
                _refresh_list()
                _new_job()
            
        ttk.Button(btn_f, text="Delete", command=_delete_job).pack(side=tk.LEFT, expand=True, fill=tk.X)
    
        def _save_job():
            jname = name_var.get().strip()
            if not jname:
                messagebox.showerror("Error", "Job name cannot be empty.", parent=top)
                return
            if jname in ["Scan", "Optimisation", "Frequency", "Optimisation + Frequency", "Transition State (TS)", "IRC", "Single Point", "Thermal Correction", "CASSCF", "Custom", "Other"]:
                messagebox.showerror("Error", "Cannot use a reserved job name.", parent=top)
                return
            
            job_data = {
                "text": text_area.get("1.0", tk.END).strip(),
                "dynamic_xyz": dyn_xyz_var.get(),
                "dynamic_cores": dyn_cores_var.get(),
                "gaussian_format": gaussian_format_var.get(),
                "gaussian_replacements": gaussian_replacements_var.get()
            }
            self.custom_jobs[jname] = job_data
            self._save_custom_jobs()
            self._update_task_cb_values()
            self.task.set("Custom")
            self.subtask.set(jname)
            if hasattr(self, "_refresh_task_ui"):
                self._refresh_task_ui()
            _refresh_list()
            messagebox.showinfo("Success", f"Saved custom job '{jname}'", parent=top)
            top.destroy()
        
        ttk.Button(right_f, text="Save Job", command=_save_job).pack(anchor="e", pady=10)

    def _open_batch_processor(self):
        top = tk.Toplevel(self.parent)
        top.title("Batch Process ORCA Files")
        top.geometry("450x200")
        top.grab_set()
        
        f = ttk.Frame(top, padding=20)
        f.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(f, text="Select processing mode:", font=("Segoe UI", 10, "bold")).pack(pady=(0, 15))
        
        def _opt1():
            top.destroy()
            self._start_batch_process(mode=1)
            
        def _opt2():
            top.destroy()
            self._start_batch_process(mode=2)
            
        ttk.Button(f, text="Option 1: All .xyz files in one folder", command=_opt1).pack(fill=tk.X, pady=2)
        ttk.Button(f, text="Option 2: Multiple folders (each with .xyz files)", command=_opt2).pack(fill=tk.X, pady=2)
        ttk.Button(f, text="Cancel", command=top.destroy).pack(pady=(15, 0), ipadx=20)

    def _predict_xyz_properties(self, filepath):
        charge = 0
        mult = 1
        atomic_numbers = {
            "H": 1, "HE": 2, "LI": 3, "BE": 4, "B": 5, "C": 6, "N": 7, "O": 8, "F": 9, "NE": 10,
            "NA": 11, "MG": 12, "AL": 13, "SI": 14, "P": 15, "S": 16, "CL": 17, "AR": 18,
            "K": 19, "CA": 20, "SC": 21, "TI": 22, "V": 23, "CR": 24, "MN": 25, "FE": 26,
            "CO": 27, "NI": 28, "CU": 29, "ZN": 30, "GA": 31, "GE": 32, "AS": 33, "SE": 34,
            "BR": 35, "KR": 36, "RB": 37, "SR": 38, "Y": 39, "ZR": 40, "NB": 41, "MO": 42,
            "TC": 43, "RU": 44, "RH": 45, "PD": 46, "AG": 47, "CD": 48, "IN": 49, "SN": 50,
            "SB": 51, "TE": 52, "I": 53, "XE": 54, "CS": 55, "BA": 56, "PT": 78, "AU": 79
        }
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            sum_z = 0
            for i, line in enumerate(lines):
                parts = line.split()
                if not parts: continue
                if i == 0 and len(parts) == 1 and parts[0].isdigit():
                    continue
                if len(parts) >= 4:
                    import re
                    sym = parts[0].upper()
                    sym = re.sub(r'[^A-Z]', '', sym)
                    if sym in atomic_numbers:
                        sum_z += atomic_numbers[sym]
            
            n_e = sum_z - charge
            mult = 1 if n_e % 2 == 0 else 2
        except:
            pass
            
        return charge, mult
        
    def _start_batch_process(self, mode):
        if mode == 1:
            dir_path = filedialog.askdirectory(parent=self.parent, title="Select folder containing .xyz files")
            if not dir_path: return
            files_to_process = []
            for f in os.listdir(dir_path):
                if f.lower().endswith('.xyz'):
                    files_to_process.append(os.path.join(dir_path, f))
        else:
            dir_path = filedialog.askdirectory(parent=self.parent, title="Select parent folder (contains subfolders with .xyz files)")
            if not dir_path: return
            files_to_process = []
            for root, _, files in os.walk(dir_path):
                for f in files:
                    if f.lower().endswith('.xyz'):
                        files_to_process.append(os.path.join(root, f))
                        
        if not files_to_process:
            messagebox.showinfo("Batch Process", "No .xyz files found in the selected location.")
            return
            
        # Extract metadata
        batch_data = []
        for fp in files_to_process:
            c, m = self._predict_xyz_properties(fp)
            folder_name = os.path.basename(os.path.dirname(fp))
            file_name = os.path.basename(fp)
            batch_data.append({
                "path": fp,
                "folder": folder_name,
                "name": file_name,
                "charge": c,
                "mult": m
            })
            
        self._open_batch_preview_table(batch_data, mode)
        
    def _open_batch_preview_table(self, batch_data, mode):
        top = tk.Toplevel(self.parent)
        top.title("Batch Process Preview")
        top.geometry("800x500")
        top.grab_set()
        
        main_f = ttk.Frame(top, padding=10)
        main_f.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(main_f, text=f"Found {len(batch_data)} files. Review predicted Charge and Multiplicity.", font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 10))
        
        columns = ("folder", "name", "charge", "mult")
        tree = ttk.Treeview(main_f, columns=columns, show="headings", selectmode="extended")
        tree.heading("folder", text="Folder Name")
        tree.heading("name", text="XYZ File Name")
        tree.heading("charge", text="Charge")
        tree.heading("mult", text="Spin Multiplicity")
        
        tree.column("folder", width=150)
        tree.column("name", width=250)
        tree.column("charge", width=100, anchor="center")
        tree.column("mult", width=150, anchor="center")
        
        for i, item in enumerate(batch_data):
            tree.insert("", tk.END, iid=str(i), values=(item["folder"], item["name"], item["charge"], item["mult"]))
            
        vsb = ttk.Scrollbar(main_f, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        vsb.place(in_=tree, relx=1.0, rely=0, relheight=1.0, anchor="ne")
        
        ctrl_f = ttk.Frame(main_f)
        ctrl_f.pack(fill=tk.X, pady=(10, 0))
        
        def _apply_bulk(to_all=False):
            try:
                new_c = int(c_var.get())
                new_m = int(m_var.get())
            except:
                messagebox.showerror("Error", "Charge and Multiplicity must be integers", parent=top)
                return
            
            sel = tree.get_children() if to_all else tree.selection()
            if not sel and not to_all:
                messagebox.showwarning("Warning", "No rows selected.", parent=top)
                return
                
            for item in sel:
                vals = list(tree.item(item, "values"))
                vals[2] = new_c
                vals[3] = new_m
                tree.item(item, values=vals)
                batch_data[int(item)]["charge"] = new_c
                batch_data[int(item)]["mult"] = new_m
                
        c_var = tk.StringVar(value="0")
        m_var = tk.StringVar(value="1")
        
        ttk.Label(ctrl_f, text="Charge:").pack(side=tk.LEFT)
        ttk.Entry(ctrl_f, textvariable=c_var, width=5).pack(side=tk.LEFT, padx=(2, 10))
        ttk.Label(ctrl_f, text="Multiplicity:").pack(side=tk.LEFT)
        ttk.Entry(ctrl_f, textvariable=m_var, width=5).pack(side=tk.LEFT, padx=(2, 10))
        ttk.Button(ctrl_f, text="Apply to Selected", command=lambda: _apply_bulk(False)).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl_f, text="Apply to All", command=lambda: _apply_bulk(True)).pack(side=tk.LEFT, padx=2)
        
        def _generate():
            top.destroy()
            self._run_batch_generation(batch_data, mode)
            
        ttk.Button(ctrl_f, text="Generate Inputs", command=_generate).pack(side=tk.RIGHT)
        
    def _run_batch_generation(self, batch_data, mode):
        import shutil
        import traceback
        
        # Save original state
        orig_charge = self.charge.get()
        orig_mult = self.mult.get()
        orig_geom = self.geom.get("1.0", tk.END)
        orig_filename = self.filename.get()
        
        success_count = 0
        err_msg = []
        
        ext = ".inp"
        
        for item in batch_data:
            fp = item["path"]
            c = item["charge"]
            m = item["mult"]
            fname = os.path.splitext(item["name"])[0]
            
            # Setup save directory
            if mode == 1:
                base_dir = os.path.dirname(fp)
                out_dir = os.path.join(base_dir, "all_input")
                if not os.path.exists(out_dir):
                    os.makedirs(out_dir)
                out_path = os.path.join(out_dir, fname + ext)
                sh_path = os.path.join(out_dir, fname + ".sh")
            else:
                base_dir = os.path.dirname(fp)
                out_path = os.path.join(base_dir, fname + ext)
                sh_path = os.path.join(base_dir, fname + ".sh")
                
            # Apply state for generation
            self.charge.set(str(c))
            self.mult.set(str(m))
            self.filename.set(fname)
            
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read()
                
                rows = _normalize_geometry_raw(raw)
                if rows:
                    lines = [f"{r[0]:<4s} {float(r[1]):>12.6f} {float(r[2]):>12.6f} {float(r[3]):>12.6f}" for r in rows]
                    self.geom.delete("1.0", tk.END)
                    self.geom.insert("1.0", "\n".join(lines))
                    
                    # Generate silently
                    self.generate(open_detail_editor=False)
                    
                    # Save generated input
                    inp_text = self.txt_inp.get("1.0", tk.END).strip()
                    if inp_text:
                        with open(out_path, "w", encoding="utf-8") as out_f:
                            out_f.write(inp_text + "\n")
                        if self.exec_mode.get() in ["HPC", "Workstation"]:
                            sh_text = self.txt_sh.get("1.0", tk.END).strip()
                            if sh_text:
                                sh_ext = ".bat" if (self.exec_mode.get() == "Workstation" and self.workstation_os.get() == "Windows") else ".sh"
                                actual_sh_path = os.path.splitext(sh_path)[0] + sh_ext
                                with open(actual_sh_path, "w", newline='\n', encoding="utf-8") as out_sh:
                                    out_sh.write(sh_text + "\n")
                        success_count += 1
                    else:
                        err_msg.append(f"Generated text was empty for {fname}")
                else:
                    err_msg.append(f"Failed to extract geometry from {fname}")
            except Exception as e:
                err_msg.append(f"Error on {fname}: {e}")
                
        # Restore state
        self.charge.set(orig_charge)
        self.mult.set(orig_mult)
        self.filename.set(orig_filename)
        self.geom.delete("1.0", tk.END)
        self.geom.insert("1.0", orig_geom)
        
        msg = f"Successfully generated {success_count} input files!"
        if err_msg:
            msg += "\n\nErrors encountered:\n" + "\n".join(err_msg[:5])
            if len(err_msg) > 5:
                msg += f"\n... and {len(err_msg)-5} more."
            messagebox.showwarning("Batch Complete", msg)
        else:
            messagebox.showinfo("Batch Complete", msg)

    def generate(self, open_detail_editor: bool = False):
        try:
            # --- 1. BUILD INPUT FILE ---
            t = self.task.get()
            st = self.subtask.get()
            
            if t == "Custom":
                job_name = st
                job_data = getattr(self, "custom_jobs", {}).get(job_name, {})
                if not job_data:
                    from tkinter import messagebox
                    messagebox.showerror("Error", f"Custom job '{job_name}' not found.")
                    return
                
                inp_text = job_data.get("text", "")
                
                is_gaussian = job_data.get("gaussian_format", False)
                do_replacements = job_data.get("gaussian_replacements", False)
                
                if do_replacements and is_gaussian:
                    import re
                    try: mem_mb = int((self.memory.get() or "4000").strip())
                    except ValueError: mem_mb = 4000
                    mem_str = f"{mem_mb//1024}GB" if mem_mb >= 1024 else f"{mem_mb}MB"
                    inp_text = re.sub(r"(?i)%mem=.*", f"%mem={mem_str}", inp_text)
                    
                    cores = (self.nprocs.get() or "1").strip()
                    if not cores.isdigit(): cores = "1"
                    inp_text = re.sub(r"(?i)%nprocshared=.*", f"%nprocshared={cores}", inp_text)
                    
                    fname = (self.filename.get() or "job").strip()
                    inp_text = re.sub(r"(?i)%chk=.*", f"%chk={fname}.chk", inp_text)
                elif not is_gaussian and job_data.get("dynamic_cores", True):
                    nprocs = (self.nprocs.get() or "").strip()
                    if not nprocs.isdigit() or int(nprocs) < 1:
                        nprocs = "20"
                    core_block = f"%pal nprocs {nprocs}\nend\n"
                    lines = inp_text.split("\n")
                    if lines and lines[0].strip().startswith("!"):
                        lines.insert(1, core_block)
                        inp_text = "\n".join(lines)
                    else:
                        inp_text = core_block + "\n" + inp_text
                
                if job_data.get("dynamic_xyz", True):
                    geom_txt = self.geom.get("1.0", tk.END).strip()
                    lines = geom_txt.split("\n")
                    if len(lines) > 2 and lines[0].strip().isdigit():
                        lines = lines[2:]
                    geom_txt = "\n".join(lines)
                    if is_gaussian:
                        xyz_block = f"{self.charge.get().strip()} {self.mult.get().strip()}\n{geom_txt}\n\n"
                        if not inp_text.endswith("\n\n"):
                            if not inp_text.endswith("\n"): inp_text += "\n"
                            inp_text += "\n"
                        inp_text = inp_text + xyz_block
                    else:
                        xyz_block = f"* xyz {self.charge.get()} {self.mult.get()}\n{geom_txt}\n*"
                        inp_text = inp_text.rstrip() + "\n\n" + xyz_block
                    
                self.txt_inp.delete("1.0", tk.END)
                self.txt_inp.insert("1.0", inp_text)
                
                sh_text = self._generate_script_text()
                self.txt_sh.delete("1.0", tk.END)
                self.txt_sh.insert("1.0", sh_text)
                self._focus_generated_orca_input()
                if open_detail_editor:
                    self._open_detailed_orca_inp_dialog()
                return

            if t == "Thermal Correction":
                h_val_raw = self.hess_file.get().strip() or "initial.hess"
                h_val = os.path.basename(h_val_raw.replace('\\', '/'))
                temp = self.temp_k.get().strip() or "373.15"
                mem = self.memory.get().strip() or "1500"
                cores = self.nprocs.get().strip() or "12"
                
                geom_txt = self.geom.get("1.0", tk.END).strip()
                lines = geom_txt.split("\n")
                if len(lines) > 2 and lines[0].strip().isdigit():
                    lines = lines[2:]
                geom_txt = "\n".join(lines)
                if not geom_txt.strip(): geom_txt = "O 0.0 0.0 0.0\n"
                xyz_block = f"*xyz {self.charge.get()} {self.mult.get()}\n{geom_txt}\n*"
                
                out = f"! PrintThermoChem\n"
                out += f"%geom\n"
                out += f"inhessname \"{h_val}\"\n"
                out += f"end\n\n"
                out += f"%freq Temp {temp}\n"
                out += f"end\n\n"
                out += f"%scf maxiter 1000 end\n"
                out += f"%maxcore {mem}\n"
                out += f"%pal nprocs {cores} end\n"
                out += f"%geom maxiter 2000 end\n\n"
                out += f"{xyz_block}\n"
                
                self.txt_inp.delete("1.0", tk.END)
                self.txt_inp.insert("1.0", out)
                
                sh_text = self._generate_script_text()
                self.txt_sh.delete("1.0", tk.END)
                self.txt_sh.insert("1.0", sh_text)
                self._focus_generated_orca_input()
                if open_detail_editor:
                    self._open_detailed_orca_inp_dialog()
                return

            is_goat = (t == "GOAT") or (t == "Other" and st == "GOAT")
            if is_goat:
                mode = (self.goat_mode.get() or "GOAT").strip() or "GOAT"
                nprocs = (self.nprocs.get() or "").strip()
                if not nprocs.isdigit() or int(nprocs) < 1:
                    nprocs = "20"
                grp = (self.goat_nprocs_group.get() or "").strip()
                if not grp.isdigit() or int(grp) < 1:
                    grp = "4"
                geom_txt = self.geom.get("1.0", tk.END).strip()
                lines = geom_txt.split("\n")
                if len(lines) > 2 and lines[0].strip().isdigit():
                    lines = lines[2:]
                geom_txt = "\n".join(lines)
                xyz = f"*XYZ {self.charge.get()} {self.mult.get()}\n{geom_txt}\n*"
                full_inp = (
                    f"! {mode} XTB\n\n"
                    f"%pal nprocs {nprocs}\n"
                    f"nprocs_group {grp}\n"
                    f"end\n\n"
                    f"{xyz}"
                )
                self.txt_inp.delete("1.0", tk.END)
                self.txt_inp.insert("1.0", full_inp)

                sh_text = self._generate_script_text()
                self.txt_sh.delete("1.0", tk.END)
                self.txt_sh.insert("1.0", sh_text)
                self._focus_generated_orca_input()
                if open_detail_editor:
                    self._open_detailed_orca_inp_dialog()
                return
            is_crest = (t == "Other" and st == "CREST")
            if is_crest:
                if self.exec_mode.get() == "Local":
                    messagebox.showinfo(
                        "Use CREST module",
                        "For local CREST runs, please use the dedicated CREST module in Conformational Sampling.\n\n"
                        "Input Creator 5 is intended to prepare templates, while local CREST execution is handled there."
                    )
                    return
                gfn = (self.crest_gfn_level.get() or "2").strip()
                ewin = (self.crest_ewin.get() or "6.0").strip()
                temp_k = (self.crest_temp.get() or "298.15").strip()
                threads = (self.crest_threads.get() or "4").strip()
                mode = (self.crest_mode.get() or "Conformer search").strip()
                mode_flag = {"Conformer search": "", "iMTD-GC": "-v3", "MDOPT": "--mdopt"}.get(mode, "")
                solvent_model = (self.crest_solvent_model.get() or "ALPB").strip().lower()
                solvent = (self.crest_solvent.get() or "").strip()
                extra = (self.crest_extra_args.get() or "").strip()
                chrg = (self.charge.get() or "0").strip()
                try:
                    uhf = str(max(0, int((self.mult.get() or "1").strip()) - 1))
                except Exception:
                    uhf = "0"
                solv = ""
                if solvent_model not in ("", "none") and solvent:
                    solv = f"-{solvent_model} {solvent}"
                crest_cmd = (
                    f"crest input.xyz -gfn{gfn} -chrg {chrg} -uhf {uhf} -ewin {ewin} -temp {temp_k} -T {threads} "
                    f"{mode_flag} {solv} {extra}"
                ).strip()
                crest_cmd = re.sub(r"\s+", " ", crest_cmd)
                geom_txt = self.geom.get("1.0", tk.END).strip()
                lines = geom_txt.split("\n")
                if len(lines) > 2 and lines[0].strip().isdigit():
                    lines = lines[2:]
                geom_txt = "\n".join(lines)
                xyz = f"*XYZ {self.charge.get()} {self.mult.get()}\n{geom_txt}\n*"
                full_txt = (
                    "# CREST run template (from Input Creator 5)\n"
                    "# Save geometry to input.xyz, then run:\n"
                    f"# {crest_cmd}\n\n"
                    f"{xyz}\n"
                )
                self.txt_inp.delete("1.0", tk.END)
                self.txt_inp.insert("1.0", full_txt)
                self.txt_sh.delete("1.0", tk.END)
                self.txt_sh.insert(
                    "1.0",
                    f"#!/bin/bash\n# CREST execution template\n{crest_cmd} > crest.out 2>&1\n",
                )
                self._focus_generated_orca_input()
                return

            task_kw = ""
            fq = "Freq" if self.freq_type.get() == "Analytical" else "NumFreq"
            
            if t == "Optimisation + Frequency": task_kw = f"opt {fq}"
            elif t in ["Scan", "Optimisation"]: task_kw = "opt"
            elif t == "Frequency": task_kw = f"{fq}"
            elif t == "IRC": task_kw = "IRC"
            elif t == "Transition State (TS)":
                if st == "NEB-TS": task_kw = f"NEB-TS {fq}"
                else: task_kw = f"optTS {fq}"
            elif t == "Single Point": task_kw = "SP"
            elif t == "CASSCF": task_kw = ""
            
            else:
                if t not in ["Other", "Custom"]:
                    task_kw = f"{t} {st}".strip()
            
            mtype = self.method_type.get()
            msub = self.method_sub_type.get()
            spin_kw = (self.spin_state.get().split()[0] if getattr(self, "spin_state", None) else "RKS")
            header_parts = ["!"] if mtype == "Semiempirical" else [f"!{spin_kw}"]

            if t == "CASSCF" and st == "CASSCF":
                header_parts = ["! def2-TZVP def2-TZVP/C TightSCF MoRead"]
            elif t == "CASSCF" and st == "NEVPT2":
                header_parts = ["! def2-TZVP def2-TZVP/C TightSCF MoRead RI-NEVPT2 keepdens"]
            elif t == "CASSCF" and st == "Orbital Swapping":
                header_parts = ["!UKS PBE0 D3bj def2-TZVP  RIJCOSX def2/J Normalprint NoIter MoRead"]
            elif mtype == "Semiempirical":
                self._warn_if_orca_xtb_selected()
                header_parts.append(self._orca_xtb_keyword())
            elif mtype == "HF":
                header_parts.append("HF")
                header_parts.append(self.basis.get())
            elif mtype in ["MP", "CCSD"]:
                if msub == "DLPNO-CCSD(T)":
                    header_parts.append("DLPNO-CCSD(T) RIJCOSX def2-TZVPP def2-TZVPP/C def2/J TightSCF NORMALPNO")
                else:
                    if msub: header_parts.append(msub)
                    else: header_parts.append(mtype)
                    header_parts.append(self.basis.get())
            else: # DFT
                func = self.method.get()
                if func == "M06-2X": func = "M062X"
                if func == "M06-L": func = "M06L"
                
                f_data = FUNCTIONAL_DATA.get(self.method.get(), {})
                if not f_data.get("libxc_block"):
                    header_parts.append(func)
                
                header_parts.append(self.basis.get())
            
            if t == "Thermal Correction" and self.other_cmd.get().strip():
                header_parts.append(self.other_cmd.get().strip())


            # Approximations
            if t != "CASSCF" and mtype not in ["Semiempirical", "HF", "MP"] and msub != "DLPNO-CCSD(T)":
                disp_val = self.dispersion.get().strip()
                disp = self.DISPERSION_MAP.get(disp_val, disp_val)
                if disp: header_parts.append(disp)
    
                grid = self.grid_size.get()
                if grid and grid != "Default": header_parts.append(grid)
    
                ri_val = self.ri_approx.get().strip()
                ri = self.RI_MAP.get(ri_val, ri_val)
                if ri: header_parts.append(ri)
    
                if self.aux_basis.get() != "None": header_parts.append(self.aux_basis.get())
            
            if task_kw: header_parts.append(task_kw)

            spin_val = self.spin_state.get().split()[0] if getattr(self, "spin_state", None) else ""
            if self.qro_gen.get() and spin_val == "UKS":
                header_parts.extend(["uno", "uco", "keepdens"])
            if self.scf_acc.get() != "NormalSCF":
                if not any(self.scf_acc.get() in part for part in header_parts):
                    header_parts.append(self.scf_acc.get())

            # Properties on header
            if t != "CASSCF":
                if self.prop_polar.get(): header_parts.append("Polar")
                if self.prop_nmr.get(): header_parts.append("NMR")
                
                if self.moinp_file.get().strip() and mtype not in ["Semiempirical"]:
                    header_parts.append("Moread")

            header = " ".join(header_parts).replace("! ", "!", 1)
            
            blocks = []
            if t == "CASSCF" and st == "CASSCF":
                blocks.append(f"%casscf nel {self.casscf_nel.get().strip()}\n        norb {self.casscf_norb.get().strip()}\n        mult {self.mult.get().strip()}\n        nroots {self.casscf_nroots.get().strip()}\n        TrafoStep RI\n        maxiter 300\n        orbstep superci\n        switchstep diis\n#       shiftup 2.0\n#       shiftdn 2.0\n        ci\n        NGuessMat 10000\n        maxiter 300\n        end\nend")
            elif t == "CASSCF" and st == "NEVPT2":
                blocks.append(f"%casscf nel {self.casscf_nel.get().strip()}\n        norb {self.casscf_norb.get().strip()}\n        mult {self.mult.get().strip()}\n        nroots {self.casscf_nroots.get().strip()}\n        TrafoStep RI\n\tmaxiter 1000\n        ci\n        NGuessMat 10000\n        maxiter 1000\nend \nend")
            elif t == "CASSCF" and st == "Orbital Swapping":
                rots = self.orbital_swap_txt.get("1.0", tk.END).strip()
                if not rots:
                    rots = "    {96, 57, 90}\n    {97, 63, 90}"
                blocks.append(f"%scf\n   rotate\n{rots}\nend\nend")
            
            if self.moinp_file.get().strip() and mtype not in ["Semiempirical"]:
                m_val_raw = self.moinp_file.get().strip().replace('\\', '/')
                m_val = os.path.basename(m_val_raw)
                blocks.append(f'%moinp "{m_val}"')
            if self.qro_file.get().strip() and mtype not in ["Semiempirical"]:
                q_val_raw = self.qro_file.get().strip().replace('\\', '/')
                q_val = os.path.basename(q_val_raw)
                blocks.append(f'%moinp "{q_val}"')
                
            if mtype not in ["Semiempirical", "HF", "MP", "CCSD"]:
                f_data = FUNCTIONAL_DATA.get(self.method.get(), {})
                if f_data.get("libxc_block"):
                    blocks.append(f_data["libxc_block"])
                    
            geom_lines = []
            if self.hess_file.get().strip():
                h_val_raw = self.hess_file.get().strip().replace('\\', '/')
                h_val = os.path.basename(h_val_raw)
                if t in ["Frequency", "Optimisation + Frequency", "Transition State (TS)", "IRC", "Scan", "Optimisation"]:
                    geom_lines.append(f" inhess read\n inhessname \"{h_val}\"")
                elif t == "Thermal Correction":
                    geom_lines.append(f" inhessname \"{h_val}\"")
            if t == "IRC" and self.hess_file.get().strip():
                geom_lines.append(" PrintInternalHess true")
                
            if t == "Transition State (TS)" and st == "TS Search":
                geom_lines.append(" TS_search EF")
                if self.ts_mode.get().strip():
                    geom_lines.append(f" TS_Mode {{M {self.ts_mode.get().strip()}}} end")
                    
            if geom_lines:
                blocks.append("%geom\n" + "\n".join(geom_lines) + "\nend")

            if t == "Transition State (TS)" and st == "NEB-TS":
                nimg_raw = (self.neb_nimages.get() or "").strip()
                nimg = nimg_raw if nimg_raw.isdigit() else "8"
                prod_disk = (self.neb_product_path.get() or "").strip()
                inline = (getattr(self, "_neb_product_coords_text", "") or "").strip()
                if prod_disk or inline:
                    prod_ref = "neb_product.xyz"
                else:
                    prod_ref = ""
                if prod_ref:
                    blocks.append(
                        '%neb\n'
                        f' NEB_End_XYZFile "{prod_ref}"\n'
                        f" Nimages {nimg}\n"
                        "end"
                    )
                else:
                    messagebox.showwarning(
                        "NEB-TS",
                        "NEB-TS requires the product (end-point) structure.\n\n"
                        "Use “Browse…” or “Paste / edit…” in the NEB-TS pathway section, "
                        "or save coordinates as an .xyz file and choose it.",
                    )

            # Constraints Block
            if ((t in ["Optimisation", "Optimisation + Frequency"] and st == "Constrained") or (t == "Scan" and st == "Constrained Scan")) and self.constraint_rows:
                const_lines = []
                for cv in self.constraint_rows:
                    ctype = cv["type"].get()[0].upper() # 'B', 'A', 'D'
                    vars_to_check = [cv["a1"].get().strip(), cv["a2"].get().strip()]
                    if ctype in ['A', 'D']: vars_to_check.append(cv["a3"].get().strip())
                    if ctype == 'D': vars_to_check.append(cv["a4"].get().strip())
                    
                    if all(vars_to_check):
                        const_lines.append(f" {{ {ctype} {' '.join(vars_to_check)} C }}")
                
                if const_lines:
                    blocks.append("%geom Constraints\n" + "\n".join(const_lines) + "\n end\nend")

            # Scan Block
            if t == "Scan":
                a1 = self.scan_a1.get().strip()
                a2 = self.scan_a2.get().strip()
                a3 = self.scan_a3.get().strip()
                a4 = self.scan_a4.get().strip()
                
                if st == "Constrained Scan":
                    st_type = self.scan_ctype.get()[0].upper()
                    s_type = self.scan_ctype.get()
                else:
                    st_type = st.split()[0][0].upper() # Bond Scan -> 'B', Angle Scan -> 'A', Dihedral Scan -> 'D'
                    s_type = st.split()[0]
                    
                s_start = self.scan_start.get().strip()
                s_end = self.scan_end.get().strip()
                s_steps = self.scan_steps.get().strip()
                
                scan_vars = [a1, a2]
                if s_type in ["Angle", "Dihedral"]: scan_vars.append(a3)
                if s_type == "Dihedral": scan_vars.append(a4)
                
                if all(scan_vars) and s_start and s_end and s_steps:
                    a_str = " ".join(scan_vars)
                    blocks.append(f"%geom Scan\n {st_type} {a_str} = {s_start}, {s_end}, {s_steps}\n end\nend")

            # Solvent
            add_solvent = self.use_solvent.get()
            if self.task.get() == "Single Point" and self.subtask.get() == "With solvent":
                add_solvent = True
                
            if add_solvent:
                solv_name = self.solvent.get()
                solv_data = SOLVENT_DATA.get(solv_name, {})
                clean_name = solv_name.split('/')[0].strip()
                if solv_data.get("custom_block"):
                    blocks.append(solv_data["custom_block"])
                elif solv_data.get("crosses", 0) >= 2:
                    blocks.append(f"%CPCM SMD TRUE\n      SMDSOLVENT \"{clean_name}\"\nEND")
                else:
                    blocks.append(f"%cpcm\n SMDsolvent \"{clean_name}\"\n end")

            # Convergence and Overrides
            if self.scf_maxiter.get() and self.scf_maxiter.get() != "1000":
                blocks.append(f"%scf maxiter {self.scf_maxiter.get()} end")
            else:
                blocks.append(f"%scf maxiter 1000 end")

            if self.geom_maxiter.get():
                blocks.append(f"%geom maxiter {self.geom_maxiter.get()} end")
                
            if t in ["Frequency", "Optimisation + Frequency", "Transition State (TS)"]:
                if self.temp_k.get() and self.temp_k.get() != "298.15":
                    blocks.append(f"%freq Temp {self.temp_k.get()}\n end")
            if is_goat:
                grp = (self.goat_nprocs_group.get() or "").strip()
                if not grp.isdigit() or int(grp) < 1:
                    grp = "4"
                blocks.append(f"%goat\n NPROCS_GROUP {grp}\nend")

            # Resources
            blocks.append(f"%maxcore {self.memory.get()}")
            blocks.append(f"%pal nprocs {self.nprocs.get()} end")

            # XYZ
            split_dict = []
            if getattr(self, "split_basis_var", None) and self.split_basis_var.get():
                for row in self.split_rows:
                    t_str = row["target"].get().strip()
                    c_str = row["cmd"].get().strip()
                    v_str = row["val"].get().strip()
                    if t_str and c_str and v_str:
                        indices = []
                        elements = []
                        
                        import re
                        match_individual = re.match(r'^[A-Za-z]+\s*\((\d+)\)$', t_str)
                        if match_individual:
                            indices.append(int(match_individual.group(1)))
                        elif any(char.isdigit() for char in t_str):
                            parts = t_str.split(',')
                            for p in parts:
                                p = p.strip()
                                if '-' in p:
                                    bounds = p.split('-')
                                    if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit():
                                        indices.extend(range(int(bounds[0]), int(bounds[1])+1))
                                elif p.isdigit():
                                    indices.append(int(p))
                        else:
                            elements.append(t_str.capitalize())
                        
                        split_dict.append({
                            "indices": indices,
                            "elements": elements,
                            "append": f' {c_str} "{v_str}" end'
                        })
            
            xyz = f"* xyz {self.charge.get()} {self.mult.get()}\n"
            
            import re
            geom_lines = self.geom.get("1.0", tk.END).strip().split('\n')
            if len(geom_lines) > 2 and geom_lines[0].strip().isdigit():
                geom_lines = geom_lines[2:]
            final_xyz_lines = []
            atom_idx = 0
            for line in geom_lines:
                original_line = line.rstrip()
                clean_line = original_line.split('#')[0].rstrip()
                parts = clean_line.split()
                if not parts:
                    continue
                if len(parts) == 1 and parts[0].isdigit():
                    final_xyz_lines.append(clean_line)
                    continue
                    
                sym = re.sub(r'[^A-Z]', '', parts[0].upper()).capitalize()
                
                appends = ""
                for rule in split_dict:
                    if atom_idx in rule["indices"] or sym in rule["elements"]:
                        appends += rule["append"]
                        
                final_xyz_lines.append(clean_line + appends)
                atom_idx += 1

            xyz += "\n".join(final_xyz_lines)
            xyz += "\n*"

            full_inp = header + "\n\n" + "\n\n".join(blocks) + "\n\n" + xyz
            self.txt_inp.delete("1.0", tk.END)
            self.txt_inp.insert("1.0", full_inp)

            # --- 2. BUILD HPC SCRIPT ---
            sh_text = self._generate_script_text()
            self.txt_sh.delete("1.0", tk.END)
            self.txt_sh.insert("1.0", sh_text)

            self._focus_generated_orca_input()
            if open_detail_editor:
                self._open_detailed_orca_inp_dialog()

        except Exception as e:
            messagebox.showerror("Template Error", f"Error generating preview: {str(e)}")


    def save(self):
        self.generate()
        content = self.txt_inp.get("1.0", tk.END).strip()
        script_content = self.txt_sh.get("1.0", tk.END).strip()
        if not content and not script_content: return

        top = tk.Toplevel(self.parent)
        top.title("Save Files")
        top.geometry("450x260")
        top.grab_set()
        
        f = ttk.Frame(top, padding=20)
        f.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(f, text="Select files to save and provide names:", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 15))
        
        save_inp = tk.BooleanVar(value=True)
        save_script = tk.BooleanVar(value=self.exec_mode.get() in ["HPC", "Workstation"])
        
        inp_frame = ttk.Frame(f)
        inp_frame.pack(fill=tk.X, pady=5)
        cb_inp = ttk.Checkbutton(inp_frame, text="Save Input File (.inp)", variable=save_inp)
        cb_inp.pack(side=tk.LEFT)
        inp_name = tk.StringVar(value=self.filename.get())
        ttk.Entry(inp_frame, textvariable=inp_name, width=25).pack(side=tk.RIGHT)
        ttk.Label(inp_frame, text="Name:").pack(side=tk.RIGHT, padx=5)
        
        script_frame = ttk.Frame(f)
        script_frame.pack(fill=tk.X, pady=5)
        cb_script = ttk.Checkbutton(script_frame, text="Save Script File", variable=save_script)
        
        if self.exec_mode.get() not in ["HPC", "Workstation"]:
            cb_script.config(state=tk.DISABLED)
            save_script.set(False)
        cb_script.pack(side=tk.LEFT)
        
        script_name = tk.StringVar(value=self.filename.get())
        script_entry = ttk.Entry(script_frame, textvariable=script_name, width=25)
        script_entry.pack(side=tk.RIGHT)
        script_lbl = ttk.Label(script_frame, text="Name:")
        script_lbl.pack(side=tk.RIGHT, padx=5)
        
        if self.exec_mode.get() not in ["HPC", "Workstation"]:
            script_entry.config(state=tk.DISABLED)
            script_lbl.config(state=tk.DISABLED)
            
        def _on_save():
            if not save_inp.get() and not save_script.get():
                messagebox.showwarning("Warning", "Select at least one file to save.", parent=top)
                return
            
            i_name = inp_name.get().strip()
            s_name = script_name.get().strip()
            
            if save_inp.get() and not i_name:
                messagebox.showwarning("Warning", "Input name cannot be empty.", parent=top)
                return
                
            if save_script.get() and not s_name:
                messagebox.showwarning("Warning", "Script name cannot be empty.", parent=top)
                return
            
            if save_inp.get() and save_script.get() and i_name == s_name:
                if not messagebox.askyesno("Warning", "Input file and Script file have the same base name. Proceed?", parent=top):
                    return
                
            directory = filedialog.askdirectory(parent=top, title="Select Folder to Save Files", initialdir=self._safe_initial_dir(self._last_input_save_path or self._last_xyz_open_path))
            if not directory:
                return
                
            self._last_input_save_path = os.path.join(directory, i_name + ".inp")
            
            if self.filename.get() != i_name:
                self.filename.set(i_name)
                self.generate()
                
            saved_msg = []
            
            if save_inp.get():
                ext = ".inp"
                current_task = self.task.get()
                if current_task == "Custom":
                    job_name = self.subtask.get()
                    job_data = getattr(self, "custom_jobs", {}).get(job_name, {})
                    if job_data.get("gaussian_format", False):
                        ext = ".gjf"
                inp_path = os.path.join(directory, f"{i_name}{ext}")
                content = self.txt_inp.get("1.0", tk.END).strip()
                with open(inp_path, "w") as pf:
                    pf.write(content)
                saved_msg.append(f"{i_name}{ext}")
                
                # Copy associated files if they exist (skip for xTB)
                if "xtb" not in self.task.get().lower():
                    for attr in ['moinp_file', 'qro_file', 'hess_file']:
                        if hasattr(self, attr):
                            src_path = getattr(self, attr).get().strip()
                            if src_path and os.path.isfile(src_path):
                                dst_path = os.path.join(directory, os.path.basename(src_path))
                                try:
                                    import shutil
                                    shutil.copy2(src_path, dst_path)
                                    saved_msg.append(f"{os.path.basename(src_path)} (copied)")
                                except Exception as e:
                                    messagebox.showwarning("Copy Error", f"Could not copy {src_path}:\n{e}")
                
                # Check for NEB-TS product xyz
                if self.task.get() == "Transition State (TS)" and self.subtask.get() == "NEB-TS":
                    prod_disk = (self.neb_product_path.get() or "").strip()
                    inline = (getattr(self, "_neb_product_coords_text", "") or "").strip()
                    if prod_disk or inline:
                        prod_path = os.path.join(directory, "neb_product.xyz")
                        print(f"DEBUG NEB: Using prod_disk='{prod_disk}'")
                        if prod_disk and os.path.isfile(prod_disk):
                            import shutil
                            try:
                                shutil.copy(prod_disk, prod_path)
                                saved_msg.append("neb_product.xyz (copied)")
                                print(f"DEBUG NEB: Copied successfully to {prod_path}")
                            except Exception as e:
                                messagebox.showwarning("NEB product file", f"Could not copy neb_product.xyz:\n{e}")
                                print(f"DEBUG NEB: Copy failed: {e}")
                        elif inline:
                            print(f"DEBUG NEB: Using inline coords, length: {len(inline)}")
                            rows = _normalize_to_xyz_rows(inline)
                            print(f"DEBUG NEB: Normalized rows count: {len(rows)}")
                            if rows:
                                try:
                                    with open(prod_path, "w", encoding="utf-8", newline="\n") as pf:
                                        pf.write(f"{len(rows)}\nNEB product (ORCA Suite)\n")
                                        for sym, x, y, z in rows:
                                            pf.write(f"{sym} {x} {y} {z}\n")
                                    saved_msg.append("neb_product.xyz")
                                    print(f"DEBUG NEB: Wrote successfully to {prod_path}")
                                except Exception as e:
                                    messagebox.showwarning("NEB product file", f"Could not write neb_product.xyz:\n{e}")
                                    print(f"DEBUG NEB: Write failed: {e}")
                            else:
                                messagebox.showwarning("NEB product file", "Could not parse the pasted product coordinates into XYZ rows. The neb_product.xyz file was NOT created.")
                                print("DEBUG NEB: inline coords failed to parse as XYZ rows")
                        else:
                            messagebox.showwarning("NEB product file", f"The product file path '{prod_disk}' does not exist, and no coordinates were pasted. The neb_product.xyz file was NOT created.")
                            print("DEBUG NEB: prod_disk does not exist and inline is empty")
                                
            if save_script.get():
                mode = self.exec_mode.get()
                ext = ".sh"
                if mode == "Workstation" and self.workstation_os.get() == "Windows":
                    ext = ".bat"
                sh_path = os.path.join(directory, f"{s_name}{ext}")
                with open(sh_path, "w", newline='\n') as pf:
                    pf.write(self.txt_sh.get("1.0", tk.END).strip())
                saved_msg.append(f"{s_name}{ext}")
                
            messagebox.showinfo("Saved", f"Successfully saved to {directory}:\n\n" + "\n".join(saved_msg), parent=self.parent)
            top.destroy()
            
        btn_frame = ttk.Frame(f)
        btn_frame.pack(fill=tk.X, pady=(25, 0))
        ttk.Button(btn_frame, text="Save to Folder...", command=_on_save).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=top.destroy).pack(side=tk.RIGHT)

    # ---------------- xTB Execution ----------------

    def _find_xtb_exe(self):
        return xtb_support.find_xtb_exe(self.xtb_version_choice.get())

    def _map_xtb_job(self):
        t = self.task.get()
        st = self.subtask.get()
        if t == "Single Point":
            return "sp"
        if t == "Frequency":
            return "hess"
        if t == "Optimisation + Frequency":
            return "ohess"
        if t == "Optimisation":
            return "opt"
        if t == "Scan":
            return "scan"
        if t == "Transition State (TS)":
            return "opt"
        if t == "IRC":
            return "opt"
        return "opt"

    def _update_xtb_job_label(self):
        mapping = {
            "sp": "Single-point energy (--sp)",
            "opt": "Geometry optimization (--opt)",
            "hess": "Frequencies / Hessian (--hess)",
            "ohess": "Opt + Frequencies (--ohess)",
            "scan": "Relaxed PES scan (--opt + xcontrol)",
        }
        job = self._map_xtb_job()
        self.xtb_job_label.config(text=f"xTB job: {mapping.get(job, job)}")
        
        # Toggle scan UI visibility
        if hasattr(self, "xtb_right_notebook"):
            if job == "scan":
                self.xtb_right_notebook.tab(1, state="normal")
                if not self.btn_scan_graph_xtb.winfo_ismapped():
                    import tkinter as tk
                    self.btn_scan_graph_xtb.pack(side=tk.LEFT, padx=5)
            else:
                self.xtb_right_notebook.tab(1, state="hidden")
                if self.xtb_right_notebook.index(self.xtb_right_notebook.select()) == 1:
                    self.xtb_right_notebook.select(0)
                if self.btn_scan_graph_xtb.winfo_ismapped():
                    self.btn_scan_graph_xtb.pack_forget()

    def _start_xtb_job(self):
        raw_geom = self.geom.get("1.0", tk.END).strip()
        if not raw_geom:
            messagebox.showwarning("xTB", "Geometry box is empty! Load or type an XYZ geometry first.")
            return
        rows = _normalize_geometry_raw(raw_geom)
        if not rows:
            messagebox.showwarning(
                "xTB",
                "Could not parse coordinates for xTB preview.\n\n"
                "Supported: XYZ (incl. trajectories — last frame), ORCA * xyz * block,\n"
                "ORCA Cartesian coordinates (Angstrom) section, Gaussian .gjf coords.\n"
                "Each atom line: Element  x  y  z  (commas / Fortran D exponent OK).",
            )
            return
        geom_text = "\n".join(f"{a} {x} {y} {z}" for a, x, y, z in rows) + "\n"

        try:
            chrg = int(self.charge.get())
        except ValueError:
            messagebox.showwarning("xTB", "Invalid Charge. Must be integer.")
            return

        try:
            mult = int(self.mult.get())
            if mult < 1:
                raise ValueError
            uhf = mult - 1
        except ValueError:
            messagebox.showwarning("xTB", "Invalid Multiplicity. Must be integer >= 1.")
            return

        xtb_exe = self._find_xtb_exe()
        if not xtb_exe:
            show_software_not_found_dialog("xtb", self.parent)
            return

        xtb_job = self._map_xtb_job()


        opt_level = self.xtb_opt_level.get().strip()
        gfn = self.xtb_gfn_level.get().strip()
        xtb_method = (getattr(self, "xtb_method", None) and self.xtb_method.get() or "gfn2").strip().lower()

        if not hasattr(self, "xtb_solvation_model"):
            self.xtb_solvation_model = tk.StringVar(value="gbe")
            
        xtb_task_cfg = {
            "task": self.task.get(),
            "subtask": self.subtask.get(),
            "constraints": [],
            "scan": None,
            "job": xtb_job,
            "include_solvation": getattr(self, "xtb_include_solvation", tk.StringVar(value="No")).get(),
            "solvation_model": self.xtb_solvation_model.get(),
            "solvent": self.solvent.get().split('/')[0].strip().lower(),
        }
        scan_meta = None

        if (xtb_task_cfg["task"] in ["Optimisation", "Optimisation + Frequency"] and xtb_task_cfg["subtask"] == "Constrained") or \
           (xtb_task_cfg["task"] == "Scan" and xtb_task_cfg["subtask"] == "Constrained Scan"):
            for cv in getattr(self, "constraint_rows", []):
                try:
                    c_type = cv["type"].get()
                    if c_type == "Bond":
                        xtb_task_cfg["constraints"].append(
                            f"distance: {int(cv['a1'].get())+1}, {int(cv['a2'].get())+1}, auto")
                    elif c_type == "Angle":
                        xtb_task_cfg["constraints"].append(
                            f"angle: {int(cv['a1'].get())+1}, {int(cv['a2'].get())+1}, {int(cv['a3'].get())+1}, auto")
                    elif c_type == "Dihedral":
                        xtb_task_cfg["constraints"].append(
                            f"dihedral: {int(cv['a1'].get())+1}, {int(cv['a2'].get())+1}, "
                            f"{int(cv['a3'].get())+1}, {int(cv['a4'].get())+1}, auto")
                except Exception:
                    pass

        if xtb_task_cfg["task"] == "Scan":
            try:
                st = self.subtask.get()
                if st in ("Bond Scan", "Angle", "Dihedral"):
                    sc_type = "Bond" if st == "Bond Scan" else st
                else:
                    sc_type = self.scan_ctype.get()
                sc_start = float(self.scan_start.get())
                sc_end = float(self.scan_end.get())
                sc_steps = int(self.scan_steps.get())
                sc_constraint = ""
                if sc_type == "Bond":
                    sc_constraint = f"distance: {int(self.scan_a1.get())+1},{int(self.scan_a2.get())+1},{sc_start}"
                elif sc_type == "Angle":
                    sc_constraint = f"angle: {int(self.scan_a1.get())+1},{int(self.scan_a2.get())+1},{int(self.scan_a3.get())+1},{sc_start}"
                elif sc_type == "Dihedral":
                    sc_constraint = (
                        f"dihedral: {int(self.scan_a1.get())+1},{int(self.scan_a2.get())+1},"
                        f"{int(self.scan_a3.get())+1},{int(self.scan_a4.get())+1},{sc_start}")
                if sc_constraint:
                    xtb_task_cfg["scan_constraint"] = sc_constraint
                    xtb_task_cfg["scan"] = {
                        "start": sc_start,
                        "end": sc_end,
                        "steps": sc_steps,
                    }
                    scan_meta = {
                        "ctype": sc_type,
                        "a1": int(self.scan_a1.get()) + 1,
                        "a2": int(self.scan_a2.get()) + 1,
                        "a3": int(self.scan_a3.get()) + 1 if sc_type in ("Angle", "Dihedral") else None,
                        "a4": int(self.scan_a4.get()) + 1 if sc_type == "Dihedral" else None,
                        "start": sc_start,
                        "end": sc_end,
                        "steps": sc_steps,
                    }
            except Exception:
                pass

        self.btn_run_xtb.config(state=tk.DISABLED)
        self.btn_cancel_xtb.config(state=tk.NORMAL)
        if hasattr(self, "btn_vis_xtb"): self.btn_vis_xtb.config(state=tk.DISABLED)
        self.btn_scan_graph_xtb.config(state=tk.DISABLED)
        self.btn_full_log_xtb.config(state=tk.DISABLED)
        if hasattr(self, "btn_open_xtb_folder"): self.btn_open_xtb_folder.config(state=tk.DISABLED)
        self.xtb_folder_var.set(
            "xTB output folder: (run in progress — files go to project external_modules/xtb/xtb_runs/ folder)"
        )
        self.xtb_log_txt.delete("1.0", tk.END)
        self.xtb_out_geom.delete("1.0", tk.END)
        if getattr(self, "xtb_numbers_txt", None) is not None:
            self.xtb_numbers_txt.config(state=tk.NORMAL)
            self.xtb_numbers_txt.delete("1.0", tk.END)
            self.xtb_numbers_txt.insert("1.0", "xTB preview started...\n")
            self.xtb_numbers_txt.config(state=tk.DISABLED)
        for w in self.xtb_scan_plot_host.winfo_children():
            w.destroy()
        self.xtb_scan_plot_title.config(text="Scan graph will appear here after run.")
        method_label = "g-xTB" if xtb_method == "gxtb" else f"GFN{gfn}-xTB"
        self.xtb_log_txt.insert(tk.END, f"Starting {method_label}  {xtb_job.upper()} ...\n")

        self.xtb_queue = queue.Queue()
        self._last_xtb_run_folder = None
        self._last_xtb_is_scan = False
        self._last_xtb_scan_meta = scan_meta
        self._last_xtb_geom_text = geom_text

        import datetime
        job_record = {
            "start_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "job_name": f"{method_label} {xtb_job.upper()}",
            "status": "In Progress",
            "folder_path": ""
        }
        if not hasattr(self, "xtb_job_history"):
            self.xtb_job_history = []
        self.xtb_job_history.insert(0, job_record)
        if len(self.xtb_job_history) > 10:
            self.xtb_job_history = self.xtb_job_history[:10]
        self._save_xtb_job_history()

        worker = threading.Thread(
            target=self._xtb_thread_worker,
            args=(xtb_exe, geom_text, chrg, uhf, opt_level, gfn, xtb_method, xtb_task_cfg),
            daemon=True,
        )
        worker.start()
        self._poll_xtb_queue()

    def _xtb_thread_worker(self, xtb_exe, geom_text, chrg, uhf, opt_level, gfn, xtb_method, xtb_task_cfg):
        suite_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        work_parent = os.path.join(suite_root, "external_modules", "xtb", "xtb_runs")
        try:
            xtb_support.xtb_thread_worker(
                self.xtb_queue,
                self._xtb_popen_holder,
                xtb_exe,
                geom_text,
                chrg,
                uhf,
                opt_level,
                gfn,
                xtb_method,
                xtb_task_cfg,
                work_parent_dir=work_parent,
            )
        except Exception as e:
            if self.xtb_queue is not None:
                self.xtb_queue.put(("error", f"xTB worker crashed: {e}"))
                self.xtb_queue.put(("done", {"folder": None, "is_scan": False, "job": xtb_task_cfg.get('job', '')}))

    def _poll_xtb_queue(self):
        if not self.xtb_queue:
            return

        while not self.xtb_queue.empty():
            msg_type, content = self.xtb_queue.get_nowait()
            if msg_type == "log":
                self.xtb_log_txt.insert(tk.END, content)
                self.xtb_log_txt.see(tk.END)
            elif msg_type == "error":
                self.xtb_log_txt.insert(tk.END, f"\n[ERROR]: {content}\n", ("error_tag",))
                self.xtb_log_txt.tag_config("error_tag", foreground="#ff0000")
            elif msg_type == "result":
                self.xtb_out_geom.insert(tk.END, content)
            elif msg_type == "done":
                self.btn_run_xtb.config(state=tk.NORMAL)
                self.btn_cancel_xtb.config(state=tk.DISABLED)
                self._xtb_popen_holder[0] = None
                self.xtb_process = None
                self.xtb_queue = None

                if content and isinstance(content, dict):
                    self._last_xtb_run_folder = content.get("folder")
                    self._last_xtb_is_scan = content.get("is_scan", False)
                    self._last_xtb_job = content.get("job", "")
                    
                    if hasattr(self, "xtb_job_history") and len(self.xtb_job_history) > 0 and self.xtb_job_history[0]["status"] == "In Progress":
                        if self._last_xtb_run_folder:
                            self.xtb_job_history[0]["status"] = "Completed"
                            self.xtb_job_history[0]["folder_path"] = self._last_xtb_run_folder
                        else:
                            self.xtb_job_history[0]["status"] = "Failed"
                        self._save_xtb_job_history()
                    
                    self.btn_full_log_xtb.config(state=tk.NORMAL)
                    if hasattr(self, "btn_open_xtb_folder"):
                        self.btn_open_xtb_folder.config(state=tk.NORMAL)
                    if self._last_xtb_run_folder:
                        self.xtb_folder_var.set(
                            f"xTB output folder: {self._last_xtb_run_folder}\n"
                            f"(res.out / xtb_full.log = full stdout; xtbopt.log = opt steps; "
                            f"xtbscan.log = scan; Chemcraft opens trajectory logs when available)"
                        )
                    if hasattr(self, "xtb_bottom_notebook"):
                        self.xtb_right_notebook.select(0)
                    if self._last_xtb_is_scan:
                        self.btn_scan_graph_xtb.config(state=tk.NORMAL)
                        if hasattr(self, "xtb_right_notebook"):
                            self.xtb_right_notebook.tab(1, state="normal")
                            self.xtb_right_notebook.select(1)
                        self._show_xtb_scan_graph()
                    else:
                        if hasattr(self, "xtb_right_notebook"):
                            self.xtb_right_notebook.tab(1, state="hidden")
                            if self.xtb_right_notebook.index(self.xtb_right_notebook.select()) == 1:
                                self.xtb_right_notebook.select(0)
                        self.parent.after(200, self._load_xtb_viewer_output)

                    self._load_xtb_log_into_viewer()
                    self._refresh_xtb_numbers_panel()
                    if self._is_global_beginner_mode():
                        self._run_beginner_xtb_post_actions()
                    else:
                        self._auto_visualize_xtb_result()
                        self.parent.after(400, self._open_xtb_log_in_chemcraft)

                self._show_toast("xTB run finished.")
                return

        self.parent.after(100, self._poll_xtb_queue)

    def _auto_visualize_xtb_result(self):
        folder = getattr(self, "_last_xtb_run_folder", None)
        if not folder:
            return
        # xTB tab policy: popup viewer after run (no embedded xTB viewer flow).
        if self._is_xtb_tab_selected():
            self._open_beginner_viewer_popup(folder)
            return
        for candidate in ("xtbopt.xyz", "xtblast.xyz", "input.xyz", "xtbscan.log"):
            p = os.path.join(folder, candidate)
            if os.path.isfile(p):
                self._visualize_xtb_file(p)
                return

    def _run_beginner_xtb_post_actions(self):
        folder = getattr(self, "_last_xtb_run_folder", None)
        if not folder:
            return
        self._open_beginner_viewer_popup(folder)

    def _open_beginner_viewer_popup(self, folder):
        chem_exe = self._find_chemcraft_exe()
        if chem_exe:
            candidates = []
            if getattr(self, "_last_xtb_is_scan", False):
                candidates.append("xtbscan.log")
            if getattr(self, "_last_xtb_job", "") in ("opt", "ohess", "scan"):
                candidates.append("xtbopt.log")
            candidates.extend(["xtbopt.log", "xtbscan.log", "xtbopt.xyz", "xtblast.xyz", "input.xyz"])
            seen = set()
            for name in candidates:
                if name in seen:
                    continue
                seen.add(name)
                p = os.path.join(folder, name)
                if os.path.isfile(p):
                    try:
                        self._popen_viewer_exe(chem_exe, p)
                        return
                    except Exception:
                        break

        # Fallback: lightweight embedding when Chemcraft is unavailable.
        prev_viewer = self.embed_viewer_choice.get()
        self.embed_viewer_choice.set("ACV ( AutoChemyViewer )")
        for candidate in ("xtbopt.xyz", "xtblast.xyz", "input.xyz"):
            p = os.path.join(folder, candidate)
            if os.path.isfile(p):
                self._visualize_xtb_file(p)
                break
        self.embed_viewer_choice.set(prev_viewer)


    def _refresh_xtb_numbers_panel(self):
        txt = getattr(self, "xtb_numbers_txt", None)
        folder = getattr(self, "_last_xtb_run_folder", None)
        if txt is None or not folder:
            return
        lines = []
        energies_h = []
        scan_log = os.path.join(folder, "xtbscan.log")
        if os.path.isfile(scan_log):
            try:
                energies_h = xtb_support.parse_xtbscan_energies(scan_log)
            except Exception:
                energies_h = []
        if energies_h:
            rel_kcal, _ = xtb_support.convert_relative_energies(energies_h, "kcal/mol")
            lines.append("Scan energies:")
            for i, eh in enumerate(energies_h, start=1):
                dk = rel_kcal[i - 1] if i - 1 < len(rel_kcal) else 0.0
                lines.append(f"  Step {i:>3}: {eh: .10f} Eh   dE={dk: .4f} kcal/mol")
            lines.append("")
        energy_markers = []
        freq_values = []
        for name in ("xtbopt.log", "xtb_full.log", "res.out"):
            p = os.path.join(folder, name)
            if not os.path.isfile(p):
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except Exception:
                continue
            for m in re.finditer(r"TOTAL ENERGY\s+(-?\d+\.\d+)", text):
                try:
                    energy_markers.append(float(m.group(1)))
                except Exception:
                    pass
            for m in re.finditer(r"(-?\d+\.\d+)\s*cm\^-?1", text):
                try:
                    freq_values.append(float(m.group(1)))
                except Exception:
                    pass
        if energy_markers:
            lines.append(f"Final total energy: {energy_markers[-1]: .10f} Eh")
            lines.append("")
        if freq_values:
            lines.append("Frequencies (cm^-1):")
            for f in freq_values[:120]:
                lines.append(f"  {f: .4f}")
        if not lines:
            lines = ["No parsed numeric summary found yet for this run."]
        txt.config(state=tk.NORMAL)
        txt.delete("1.0", tk.END)
        txt.insert("1.0", "\n".join(lines) + "\n")
        txt.config(state=tk.DISABLED)

    def _visualize_xtb(self):
        if not getattr(self, "_last_xtb_run_folder", None):
            messagebox.showinfo("Visualization", "No recent xTB run folder found.")
            return

        folder = self._last_xtb_run_folder
        if self._is_xtb_tab_selected():
            self._open_beginner_viewer_popup(folder)
            return
        for fallback in ("xtbopt.xyz", "xtblast.xyz", "input.xyz", "xtbscan.log"):
            fb = os.path.join(folder, fallback)
            if os.path.isfile(fb):
                self._visualize_xtb_file(fb)
                return

        messagebox.showerror("Error", f"No visualization files found in {folder}")

    def _open_xtb_log_in_chemcraft(self):
        """Open the xTB file Chemcraft is designed to browse: trajectory logs first, then full stdout.

        Chemcraft-style batch runs redirect stdout to ``res.out``; optimization steps are stored in
        ``xtbopt.log`` (multi-frame trajectory). Plain ``xtb_full.log`` is the same text as
        ``res.out`` but Chemcraft shows opt/scanc steps properly from ``xtbopt.log`` / ``xtbscan.log``.
        """
        folder = getattr(self, "_last_xtb_run_folder", None)
        if not folder:
            return
        is_scan = getattr(self, "_last_xtb_is_scan", False)
        job = getattr(self, "_last_xtb_job", "") or ""

        exe = self._find_chemcraft_exe()
        if not exe:
            if job in ("hess", "ohess"):
                self._prompt_viewer_path_setup("Chemcraft", "Chemcraft path not found. Please set the path to automatically view frequency calculations.")
            return

        opened_files = 0
        if job in ("hess", "ohess"):
            g98_path = os.path.join(folder, "g98.out")
            if os.path.isfile(g98_path):
                try:
                    self._popen_viewer_exe(exe, g98_path)
                    opened_files += 1
                except Exception:
                    pass
            
            if job == "hess" and opened_files > 0:
                return

        candidates = []
        if is_scan:
            candidates.append("xtbscan.log")
        if job in ("opt", "ohess", "scan"):
            candidates.append("xtbopt.log")
        candidates.extend([
            "res.out",
            "xtb_full.log",
            "xtbopt.log",
            "xtbscan.log",
        ])

        seen = set()
        for name in candidates:
            if name in seen:
                continue
            seen.add(name)
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                try:
                    self._popen_viewer_exe(exe, path)
                except Exception:
                    pass
                return

    def _visualize_xtb_file(self, target_path):
        viewer = self.embed_viewer_choice.get()
        viewer_norm = str(viewer or "").strip().lower()
        if viewer in ("Lightweight", "ACV ( AutoChemyViewer )"):
            self.embed_host.config(height=360)
            try:
                with open(target_path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                rows = _last_xyz_frame_rows(text)
                if not rows:
                    rows = _normalize_to_xyz_rows(text)
                if not rows:
                    messagebox.showwarning("Visualizer", "Could not parse XYZ coordinates from selected file.")
                    return
                self.struct_outer.config(text="Structure visualization — xTB result (ACV ( AutoChemyViewer ))")
                self._terminate_embed_subprocess()
                self._show_autochemy_structure(rows)
                self._update_embed_detach_button_state()
                return
            except Exception as e:
                messagebox.showerror("Visualizer", f"Failed to render lightweight view:\n{e}")
                return
        try:
            self.struct_outer.config(text=f"Structure visualization — xTB result ({viewer})")
            for w in self.embed_host.winfo_children():
                w.destroy()
            tk.Label(
                self.embed_host,
                text=f"Starting {viewer}...\nPlease wait.",
                bg="#2b2b2b", fg="#FFD700", font=("Segoe UI", 16, "bold"), justify="center",
            ).place(relx=0.5, rely=0.5, anchor="center")
            self.frame.update_idletasks()
        except AttributeError:
            pass

        self._terminate_embed_subprocess()
        self._embed_prelaunch_windows = self._snapshot_top_windows()

        if viewer not in ("Jmol", "Chemcraft", "GaussView", "Avogadro", "ACV ( AutoChemyViewer )", "Lightweight") and not viewer_norm.startswith("avogadro"):
            custom_path = SoftwareManager.get_software_path(viewer)
            if custom_path and os.path.exists(custom_path):
                self._popen_viewer_exe(custom_path, target_path)
                self.struct_outer.config(text=f"Structure visualization - {viewer} (External)")
                for w in self.embed_host.winfo_children():
                    w.destroy()
                tk.Label(
                    self.embed_host, 
                    text=f"{viewer} opened externally.\nCheck your taskbar.", 
                    bg="#2b2b2b", fg="#0b5cab", font=("Segoe UI", 16, "bold"), justify="center"
                ).place(relx=0.5, rely=0.5, anchor="center")
                return
            else:
                messagebox.showwarning("Visualizer", f"Could not find path for {viewer}.")
                return

        if viewer == "Jmol":
            cmd = self._find_jmol_command(target_path)
            if cmd:
                self.embed_subprocess = subprocess.Popen(cmd)
            else:
                self._prompt_viewer_path_setup("Jmol", "Jmol not found.")
                return
        elif viewer == "GaussView":
            exe = self._find_gaussview_exe()
            if not exe:
                self._prompt_viewer_path_setup("GaussView", "gview.exe not found.")
                return
            self.embed_subprocess = self._popen_viewer_exe(exe, target_path)
        elif viewer == "Chemcraft":
            exe = self._find_chemcraft_exe()
            if not exe:
                self._prompt_viewer_path_setup("Chemcraft", "Chemcraft not found.")
                return
            self.embed_subprocess = self._popen_viewer_exe(exe, target_path)
        elif viewer_norm.startswith("avogadro"):
            exe = self._find_avogadro_exe()
            if not exe:
                self._prompt_viewer_path_setup("Avogadro", "Avogadro not found.")
                return
            self.embed_subprocess = self._popen_viewer_exe(exe, target_path)
        else:
            return

        delay = {"GaussView": 500, "Chemcraft": 500, "Jmol": 100, "Avogadro": 900}.get(viewer, 900 if viewer_norm.startswith("avogadro") else 200)
        self.frame.after(delay, lambda: self._wait_and_reparent(self.embed_subprocess.pid, 0, viewer))

    def _embed_xtb_viewer_external(self):
        viewer = self.xtb_embed_viewer_combo.get()
        if viewer == "ACV":
            self._load_xtb_viewer_output()
            return
            
        folder = getattr(self, "_last_xtb_run_folder", None)
        if not folder:
            messagebox.showinfo("Visualization", "No recent xTB run folder found.")
            return
            
        target_path = None
        for fallback in ("xtbopt.xyz", "xtblast.xyz", "input.xyz", "xtbscan.log"):
            fb = os.path.join(folder, fallback)
            if os.path.isfile(fb):
                target_path = fb
                break
        if not target_path:
            messagebox.showerror("Error", f"No visualization files found in {folder}")
            return
            
        viewer_norm = viewer.lower()
        if viewer_norm.startswith("avogadro"):
            exe = self._find_avogadro_exe()
        elif viewer == "Chemcraft":
            exe = self._find_chemcraft_exe()
        elif viewer == "Jmol":
            exe = self._find_jmol_exe()
        elif viewer == "GaussView":
            exe = self._find_gaussview_exe()
        else:
            exe = None
            
        if not exe:
            show_software_not_found_dialog(viewer, self.parent)
            return
            
        for w in self.xtb_viewer_host.winfo_children():
            w.destroy()
        tk.Label(
            self.xtb_viewer_host,
            text=f"Starting {viewer}...\nPlease wait.",
            bg="#2b2b2b", fg="#FFD700", font=("Segoe UI", 16, "bold"), justify="center",
        ).place(relx=0.5, rely=0.5, anchor="center")
        self.frame.update_idletasks()
        
        if getattr(self, "xtb_embed_subprocess", None) and self.xtb_embed_subprocess.poll() is None:
            self.xtb_embed_subprocess.terminate()
            
        self.xtb_embed_subprocess = self._popen_viewer_exe(exe, target_path)
        delay = {"GaussView": 500, "Chemcraft": 500, "Jmol": 100, "Avogadro": 900}.get(viewer, 900 if viewer_norm.startswith("avogadro") else 200)
        self.frame.after(delay, lambda: self._wait_and_reparent_xtb(self.xtb_embed_subprocess.pid, 0, viewer))

    def _wait_and_reparent_xtb(self, pid, attempts, viewer):
        import ctypes
        if attempts > 140:
            for w in self.xtb_viewer_host.winfo_children():
                w.destroy()
            tk.Label(
                self.xtb_viewer_host, text="Failed to Embed Viewer",
                bg="#2b2b2b", fg="#FF4C4C", font=("Segoe UI", 14, "bold"), justify="center"
            ).place(relx=0.5, rely=0.5, anchor="center")
            return

        hwnd = self._find_embed_window(pid, viewer)
        if hwnd:
            GWL_STYLE = -16
            GWL_EXSTYLE = -20
            self.xtb_embed_saved_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            self.xtb_embed_saved_exstyle = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            self.xtb_viewer_detached = False
            self.xtb_embed_hwnd = hwnd
            
            WS_VISIBLE = 0x10000000
            WS_CHILD = 0x40000000
            WS_POPUP = 0x80000000
            WS_CAPTION = 0x00C00000
            WS_THICKFRAME = 0x00040000
            WS_MINIMIZEBOX = 0x00020000
            WS_MAXIMIZEBOX = 0x00010000
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080

            tk_hwnd = self.xtb_viewer_host.winfo_id()
            ctypes.windll.user32.SetParent(hwnd, tk_hwnd)
            new_style = (self.xtb_embed_saved_style | WS_VISIBLE | WS_CHILD) & ~(
                WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
            )
            new_exstyle = (self.xtb_embed_saved_exstyle & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_int32(new_style).value)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_int32(new_exstyle).value)
            
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            SWP_NOACTIVATE = 0x0010
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
            ctypes.windll.user32.ShowWindow(hwnd, 5)
            self._resize_xtb_embedded()
            self.frame.after(200, self._resize_xtb_embedded)
            self.frame.after(700, self._resize_xtb_embedded)
            if hasattr(self, "btn_xtb_detach"):
                self.btn_xtb_detach.config(state=tk.NORMAL)
        else:
            self.frame.after(100, lambda: self._wait_and_reparent_xtb(pid, attempts + 1, viewer))

    def _resize_xtb_embedded(self):
        import ctypes
        if not getattr(self, "xtb_embed_hwnd", None):
            return
        if getattr(self, "xtb_viewer_detached", False):
            return
        try:
            w = self.xtb_viewer_host.winfo_width()
            h = self.xtb_viewer_host.winfo_height()
            if w > 10 and h > 10:
                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                ctypes.windll.user32.SetWindowPos(self.xtb_embed_hwnd, 0, 0, 0, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
        except Exception:
            pass
            
    def _detach_xtb_viewer(self):
        import ctypes
        if not getattr(self, "xtb_embed_hwnd", None) or getattr(self, "xtb_viewer_detached", False):
            return
        self.xtb_viewer_detached = True
        ctypes.windll.user32.SetParent(self.xtb_embed_hwnd, 0)
        ctypes.windll.user32.SetWindowLongW(self.xtb_embed_hwnd, -16, ctypes.c_int32(self.xtb_embed_saved_style).value)
        ctypes.windll.user32.SetWindowLongW(self.xtb_embed_hwnd, -20, ctypes.c_int32(self.xtb_embed_saved_exstyle).value)
        ctypes.windll.user32.SetWindowPos(self.xtb_embed_hwnd, 0, 100, 100, 1000, 700, 0x0004 | 0x0020)
        ctypes.windll.user32.ShowWindow(self.xtb_embed_hwnd, 5)
        if hasattr(self, "btn_xtb_detach"):
            self.btn_xtb_detach.config(state=tk.DISABLED)
            
    def _refresh_xtb_viewer_split(self):
        if hasattr(self, "btn_xtb_detach"):
            self.btn_xtb_detach.config(state=tk.DISABLED)
        if self.xtb_viewer_split_var.get():
            self._load_xtb_viewer_both()
        else:
            self._load_xtb_viewer_output()

    def _load_xtb_viewer_both(self):
        if not hasattr(self, "xtb_viewer_host"):
            return
        for w in self.xtb_viewer_host.winfo_children():
            w.destroy()
            
        pw = ttk.PanedWindow(self.xtb_viewer_host, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True)
        
        f1 = ttk.Frame(pw)
        f2 = ttk.Frame(pw)
        pw.add(f1, weight=1)
        pw.add(f2, weight=1)
        
        ttk.Label(f1, text="Input", font=("Segoe UI", 9, "bold")).pack(anchor="n")
        ttk.Label(f2, text="Output", font=("Segoe UI", 9, "bold")).pack(anchor="n")
        
        f1_view = ttk.Frame(f1)
        f2_view = ttk.Frame(f2)
        f1_view.pack(fill=tk.BOTH, expand=True)
        f2_view.pack(fill=tk.BOTH, expand=True)
        
        try:
            top_app = self.parent.winfo_toplevel()
            app = getattr(top_app, "_orca_app", None)
            mode = str(getattr(app, "theme_mode", "")).lower() if app else ""
            is_dark = mode in ("dark", "black")
        except Exception:
            is_dark = False

        in_text = getattr(self, "_last_xtb_geom_text", self.geom.get("1.0", tk.END))
        in_rows = _geom_lines_to_coord_rows(in_text)
        if in_rows:
            self._xtb_pre_viewer_in = AutoChemyViewer(f1_view, in_rows, is_dark=is_dark)
            
        out_text = self.xtb_out_geom.get("1.0", tk.END)
        out_rows = _geom_lines_to_coord_rows(out_text)
        if out_rows:
            self._xtb_pre_viewer_out = AutoChemyViewer(f2_view, out_rows, is_dark=is_dark)

    def _load_xtb_viewer_input(self):
        if hasattr(self, "btn_xtb_detach"):
            self.btn_xtb_detach.config(state=tk.DISABLED)
        if self.xtb_viewer_split_var.get():
            self.xtb_viewer_split_var.set(False)
        geom_text = getattr(self, "_last_xtb_geom_text", self.geom.get("1.0", tk.END))
        rows = _geom_lines_to_coord_rows(geom_text)
        if not rows:
            messagebox.showwarning("Visualizer", "No valid geometry to display.")
            return
        self._show_xtb_pre_viewer(rows)

    def _load_xtb_viewer_output(self):
        if hasattr(self, "btn_xtb_detach"):
            self.btn_xtb_detach.config(state=tk.DISABLED)
        if self.xtb_viewer_split_var.get():
            self.xtb_viewer_split_var.set(False)
        geom_text = self.xtb_out_geom.get("1.0", tk.END)
        rows = _geom_lines_to_coord_rows(geom_text)
        if not rows:
            return
        self._show_xtb_pre_viewer(rows)

    def _show_xtb_pre_viewer(self, rows):
        if not hasattr(self, "xtb_viewer_host"):
            return
        for w in self.xtb_viewer_host.winfo_children():
            w.destroy()
        try:
            top_app = self.parent.winfo_toplevel()
            app = getattr(top_app, "_orca_app", None)
            mode = str(getattr(app, "theme_mode", "")).lower() if app else ""
            is_dark = mode in ("dark", "black")
        except Exception:
            is_dark = False
            
        self._xtb_pre_viewer = AutoChemyViewer(
            self.xtb_viewer_host,
            rows,
            is_dark=is_dark,
        )
    def _show_xtb_scan_graph(self):
        folder = getattr(self, "_last_xtb_run_folder", None)
        if not folder:
            messagebox.showinfo("Scan Graph", "No scan data available.")
            return
        scan_log = os.path.join(folder, "xtbscan.log")
        if not os.path.isfile(scan_log):
            messagebox.showinfo("Scan Graph", "xtbscan.log not found in last run folder.")
            return

        try:
            energies = xtb_support.parse_xtbscan_energies(scan_log)
        except Exception as e:
            messagebox.showerror("Scan Graph", f"Error reading scan log: {e}")
            return

        if len(energies) < 2:
            messagebox.showinfo("Scan Graph", "Not enough scan points to plot.")
            return

        try:
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        except ImportError:
            messagebox.showerror("Scan Graph", "matplotlib is required.\n  pip install matplotlib")
            return

        scan_meta = getattr(self, "_last_xtb_scan_meta", None)
        x_vals = None
        x_label = "Scan Point Index"
        title = "xTB Relaxed PES Scan"
        if scan_meta:
            try:
                x_vals = xtb_support.build_scan_axis_values(
                    float(scan_meta["start"]),
                    float(scan_meta["end"]),
                    int(scan_meta["steps"]),
                )
                x_label, title = xtb_support.build_scan_coordinate_label(
                    scan_meta, getattr(self, "_last_xtb_geom_text", "") or self.geom.get("1.0", tk.END)
                )
            except Exception:
                x_vals = None
        if not x_vals or len(x_vals) != len(energies):
            x_vals = list(range(1, len(energies) + 1))
            if scan_meta:
                x_label = "Scan Point Index (fallback)"

        rel_vals, y_label = xtb_support.convert_relative_energies(energies, self.xtb_energy_unit.get())
        if len(rel_vals) < 2:
            messagebox.showinfo("Scan Graph", "Not enough scan points to plot.")
            return

        fig = Figure(figsize=(8, 4.8), dpi=100)
        ax = fig.add_subplot(111)
        ax.plot(x_vals, rel_vals, "o-", color="#0969da", linewidth=2, markersize=5)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        fig.subplots_adjust(bottom=0.24, left=0.10, right=0.98, top=0.90)

        for w in self.xtb_scan_plot_host.winfo_children():
            w.destroy()
        self.xtb_scan_plot_title.config(text=f"Scan graph ({self.xtb_energy_unit.get()})")

        canvas = FigureCanvasTkAgg(fig, master=self.xtb_scan_plot_host)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def _show_xtb_citation(self):
        papers = {
            "GFN2-xTB": "https://doi.org/10.1021/acs.jctc.8b01176",
            "WIREs overview": "https://doi.org/10.1002/wcms.1493",
            "GFN1-xTB": "https://doi.org/10.1021/acs.jctc.7b00118",
        }
        win = tk.Toplevel(self.parent)
        win.title("xTB Citation & Acknowledgment")
        win.geometry("600x320")
        win.resizable(False, False)

        ttk.Label(win, text="xTB — Extended Tight Binding", font=("Segoe UI", 14, "bold")).pack(pady=(16, 4))
        ttk.Label(
            win,
            text="Developed by Prof. Stefan Grimme and coworkers\n"
                 "Mulliken Center for Theoretical Chemistry, University of Bonn",
            font=("Segoe UI", 10), justify="center",
        ).pack(pady=(0, 12))

        ttk.Separator(win, orient="horizontal").pack(fill=tk.X, padx=20, pady=4)
        ttk.Label(win, text="Please cite the following papers when using xTB results:",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=20, pady=(8, 4))

        refs = [
            ("GFN2-xTB", "C. Bannwarth, S. Ehlert, S. Grimme, J. Chem. Theory Comput. 2019, 15, 1652-1671"),
            ("WIREs",     "C. Bannwarth et al., WIREs Comput. Mol. Sci. 2020, 11, e01493"),
            ("GFN1-xTB", "S. Grimme, C. Bannwarth, P. Shushkov, J. Chem. Theory Comput. 2017, 13, 1989-2009"),
        ]
        for key, text in refs:
            row = ttk.Frame(win)
            row.pack(fill=tk.X, padx=20, pady=2)
            ttk.Label(row, text=f"• {text}", font=("Segoe UI", 9), wraplength=460).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(row, text="Open DOI",
                       command=lambda u=papers[key]: webbrowser.open(u)).pack(side=tk.RIGHT, padx=(8, 0))

    def _open_xtb_output_folder(self):
        folder = getattr(self, "_last_xtb_run_folder", None)
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("xTB output folder", "No output folder yet. Run xTB first.")
            return
        try:
            if os.name == "nt":
                os.startfile(folder)  # type: ignore[attr-defined, unused-ignore]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showerror("xTB output folder", str(e))

    def _open_xtb_full_log(self):
        folder = getattr(self, "_last_xtb_run_folder", None)
        if not folder:
            messagebox.showinfo("Full Log", "No xTB run folder available.")
            return
        log_path = None
        for name in ("xtb_full.log", "res.out"):
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                log_path = p
                break
        if not log_path:
            messagebox.showinfo("Full Log", "Full log file not found.\nCheck the Live Log Stream instead.")
            return

        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("Full Log", str(e))
            return

        win = tk.Toplevel(self.parent)
        win.title("xTB Full Output Log")
        win.geometry("900x650")

        txt = tk.Text(win, font=("Consolas", 10), wrap=tk.NONE)
        sy = ttk.Scrollbar(win, orient=tk.VERTICAL, command=txt.yview)
        sx = ttk.Scrollbar(win, orient=tk.HORIZONTAL, command=txt.xview)
        txt.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        sx.pack(side=tk.BOTTOM, fill=tk.X)
        txt.pack(fill=tk.BOTH, expand=True)
        txt.insert("1.0", content)
        txt.config(state=tk.DISABLED)

    def _load_xtb_log_into_viewer(self):
        folder = getattr(self, "_last_xtb_run_folder", None)
        if not folder:
            return
        log_path = None
        for name in ("xtb_full.log", "res.out"):
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                log_path = p
                break
        if not log_path:
            return
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                full_text = f.read()
        except Exception:
            return

        sections = []
        for marker in ("TOTAL ENERGY", "HOMO-LUMO GAP", "GRADIENT NORM",
                        "Frequency Printout", "reduced masses", "IR intensities"):
            for i, line in enumerate(full_text.splitlines()):
                if marker in line:
                    start = max(0, i - 1)
                    end = min(len(full_text.splitlines()), i + 4)
                    sections.append("\n".join(full_text.splitlines()[start:end]))
                    break

        if sections:
            summary = "\n\n".join(sections)
            self.xtb_out_geom.insert(tk.END, "\n\n──── Key results from xTB log ────\n\n" + summary + "\n")

    def _stop_xtb_optimization(self):
        p = self._xtb_popen_holder[0]
        if p:
            try:
                p.terminate()
                self.xtb_log_txt.insert(tk.END, "\n[SYSTEM]: Subprocess terminated by user.\n")
                if hasattr(self, "xtb_job_history") and len(self.xtb_job_history) > 0 and self.xtb_job_history[0]["status"] == "In Progress":
                    self.xtb_job_history[0]["status"] = "Failed (Stopped)"
                    self._save_xtb_job_history()
            except Exception:
                pass
            self._xtb_popen_holder[0] = None

    def _use_xtb_geom(self):
        opt_geom = self.xtb_out_geom.get("1.0", tk.END).strip()
        if not opt_geom:
            messagebox.showinfo("xTB", "No output geometry available. Run xTB first.")
            return

        lines = opt_geom.split("\n")
        raw_atoms = []
        for line in lines[2:]:
            if line.strip():
                raw_atoms.append(line)

        if not raw_atoms:
            return

        self.geom.delete("1.0", tk.END)
        self.geom.insert("1.0", "\n".join(raw_atoms) + "\n")
        self._show_toast("Geometry updated from xTB result.")