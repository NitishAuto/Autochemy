"""
Orbital tools: parse ORCA quasi-restricted orbitals (QROs), judge whether the
calculation can give meaningful localized/magnetic orbitals, and build or run
an orca_loc + orca_plot workflow that writes Gaussian cube files.

Orbital indices follow ORCA's printout (0-based), which is also what orca_loc
and orca_plot expect.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

HARTREE_TO_EV = 27.211386

_QRO_LINE = re.compile(
    r"^\s*(\d+)\s*\(\s*([012])\s*\)\s*:?\s*(-?\d+\.\d+)\s*a\.u\.\s*(-?\d+\.\d+)\s*eV", re.IGNORECASE
)
# Canonical ORCA orbital table: "  NO   OCC          E(Eh)            E(eV)"
_CANON_LINE = re.compile(r"^\s*(\d+)\s+([012]\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$")

LOC_METHODS = {
    "Pipek–Mezey (PM)": 1,
    "Foster–Boys (FB)": 2,
    "Intrinsic bond orbitals (IBO)": 3,
}


@dataclass
class Orbital:
    index: int
    occ: int          # 2, 1 or 0
    e_au: float
    e_ev: float


@dataclass
class Check:
    status: str       # "ok" | "warn" | "bad" | "info"
    title: str
    detail: str = ""


@dataclass
class OrbitalReport:
    path: str
    orbitals: List[Orbital] = field(default_factory=list)
    source: str = ""                # "QRO" or "canonical"
    magnetic_text: List[str] = field(default_factory=list)
    magnetic_pairs: List[Tuple[int, float]] = field(default_factory=list)
    multiplicity: Optional[int] = None
    charge: Optional[int] = None
    s2: Optional[float] = None
    s2_ideal: Optional[float] = None
    terminated_ok: bool = False
    scf_converged: Optional[bool] = None
    qro_file: Optional[str] = None
    gbw_file: Optional[str] = None
    checks: List[Check] = field(default_factory=list)

    # ---------- convenience ----------
    def by_occ(self, occ: int) -> List[Orbital]:
        return [o for o in self.orbitals if o.occ == occ]

    @property
    def homo(self) -> Optional[Orbital]:
        occ = [o for o in self.orbitals if o.occ == 2]
        return occ[-1] if occ else None

    @property
    def somos(self) -> List[Orbital]:
        return self.by_occ(1)

    @property
    def lumo(self) -> Optional[Orbital]:
        virt = self.by_occ(0)
        return virt[0] if virt else None

    @property
    def orbital_file(self) -> Optional[str]:
        return self.qro_file or self.gbw_file


@dataclass
class Ranges:
    d_start: int
    d_end: int
    s_start: int      # -1 when there are no singly occupied orbitals
    s_end: int
    v_start: int
    v_end: int
    n_core: int

    def as_sets(self) -> Dict[str, Tuple[int, int]]:
        out = {}
        if self.d_end >= self.d_start >= 0:
            out["doubly"] = (self.d_start, self.d_end)
        if self.s_start >= 0 and self.s_end >= self.s_start:
            out["singly"] = (self.s_start, self.s_end)
        if self.v_end >= self.v_start >= 0:
            out["unoccupied"] = (self.v_start, self.v_end)
        return out


# ======================================================================
# Parsing
# ======================================================================

def _read_lines(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        return fh.readlines()


def _parse_qro_block(lines: List[str]) -> List[Orbital]:
    """Parse the last 'Orbital energies of quasi-restricted MOs' block."""
    starts = [i for i, l in enumerate(lines) if "quasi-restricted" in l.lower() and "orbital" in l.lower()]
    for start in reversed(starts):
        orbs: List[Orbital] = []
        started = False
        for line in lines[start + 1:start + 20000]:
            m = _QRO_LINE.match(line)
            if m:
                started = True
                orbs.append(Orbital(int(m.group(1)), int(m.group(2)), float(m.group(3)), float(m.group(4))))
            elif started and line.strip():
                break
        if orbs:
            return orbs
    return []


def _parse_canonical(lines: List[str]) -> List[Orbital]:
    """Fallback: last closed-shell 'ORBITAL ENERGIES' table (restricted runs)."""
    starts = [i for i, l in enumerate(lines) if l.strip() == "ORBITAL ENERGIES"]
    for start in reversed(starts):
        block = lines[start:start + 20000]
        if any("SPIN UP ORBITALS" in l for l in block[:6]):
            return []  # unrestricted: canonical orbitals are not one set
        orbs, started = [], False
        for line in block[1:]:
            m = _CANON_LINE.match(line)
            if m:
                started = True
                occ = round(float(m.group(2)))
                orbs.append(Orbital(int(m.group(1)), occ, float(m.group(3)), float(m.group(4))))
            elif started and line.strip():
                break
        if orbs:
            return orbs
    return []


def _parse_magnetic(lines: List[str]) -> Tuple[List[str], List[Tuple[int, float]]]:
    """Corresponding-orbital overlaps (identify magnetic / unpaired pairs)."""
    text: List[str] = []
    pairs: List[Tuple[int, float]] = []
    head = None
    for i, l in enumerate(lines):
        if "Orbital" in l and "Overlap(*)" in l:
            head = i
    if head is None:
        return text, pairs
    for line in lines[head:head + 4000]:
        if "the overlap is weighted" in line:
            text.append(line.rstrip())
            break
        text.append(line.rstrip())
        nums = re.findall(r"-?\d+\.\d+|\b\d+\b", line)
        if len(nums) >= 2:
            try:
                idx, ov = int(nums[0]), float(nums[-1])
                if 0.0 <= ov <= 1.0001:
                    pairs.append((idx, ov))
            except ValueError:
                pass
    return text, pairs


def analyze_output(path: str) -> OrbitalReport:
    lines = _read_lines(path)
    rep = OrbitalReport(path=path)

    rep.orbitals = _parse_qro_block(lines)
    rep.source = "QRO" if rep.orbitals else ""
    if not rep.orbitals:
        rep.orbitals = _parse_canonical(lines)
        rep.source = "canonical" if rep.orbitals else ""

    rep.magnetic_text, rep.magnetic_pairs = _parse_magnetic(lines)

    for line in lines:
        if "Multiplicity" in line and "Mult" in line:
            m = re.search(r"(\d+)\s*$", line)
            if m:
                rep.multiplicity = int(m.group(1))
        elif "Total Charge" in line and "Charge" in line:
            m = re.search(r"(-?\d+)\s*$", line)
            if m:
                rep.charge = int(m.group(1))
        elif "Expectation value of <S**2>" in line:
            m = re.search(r"(-?\d+\.\d+)", line)
            if m:
                rep.s2 = float(m.group(1))
        elif "Ideal value S*(S+1)" in line:
            m = re.search(r":\s*(-?\d+\.\d+)", line)
            if m:
                rep.s2_ideal = float(m.group(1))
        elif "SCF CONVERGED AFTER" in line:
            rep.scf_converged = True
        elif "SCF NOT CONVERGED" in line or "The SCF is NOT converged" in line:
            rep.scf_converged = False
        elif "ORCA TERMINATED NORMALLY" in line:
            rep.terminated_ok = True

    stem = os.path.splitext(path)[0]
    for ext, attr in ((".qro", "qro_file"), (".gbw", "gbw_file")):
        if os.path.isfile(stem + ext):
            setattr(rep, attr, stem + ext)

    rep.checks = quality_checks(rep)
    return rep


# ======================================================================
# Quality assessment
# ======================================================================

def quality_checks(rep: OrbitalReport) -> List[Check]:
    c: List[Check] = []
    c.append(Check("ok", "ORCA terminated normally") if rep.terminated_ok else
             Check("bad", "ORCA did not terminate normally",
                   "The job crashed or is still running; orbitals may be incomplete."))

    if rep.scf_converged is True:
        c.append(Check("ok", "SCF converged"))
    elif rep.scf_converged is False:
        c.append(Check("bad", "SCF NOT converged", "Orbitals from an unconverged SCF are not meaningful."))
    else:
        c.append(Check("warn", "SCF convergence not found in the output"))

    if rep.source == "QRO":
        c.append(Check("ok", f"Quasi-restricted orbitals found ({len(rep.orbitals)} MOs)"))
    elif rep.source == "canonical":
        c.append(Check("info", "Closed-shell canonical orbitals used",
                       "No QRO block (restricted run). Doubly/virtual orbitals can still be localized and plotted."))
    else:
        c.append(Check("bad", "No orbital energies found",
                       "Run an unrestricted job with  ! UHF/UKS ... and  %scf UHFNO true end  or  ! UNO  "
                       "(ORCA prints QROs for open-shell runs)."))

    if rep.orbital_file:
        kind = "QRO" if rep.qro_file else "GBW"
        c.append(Check("ok", f"{kind} orbital file found", os.path.basename(rep.orbital_file)))
        if rep.source == "QRO" and not rep.qro_file:
            c.append(Check("warn", "Only a .gbw file is next to the output",
                           "QRO numbering refers to the .qro file. Plotting the .gbw will show UHF orbitals instead."))
    else:
        c.append(Check("bad", "No .qro or .gbw next to the output file",
                       "Copy the .qro (or .gbw) into the same folder with the same base name."))

    n_s = len(rep.somos)
    if rep.multiplicity is not None and rep.orbitals:
        expect = rep.multiplicity - 1
        if n_s == expect:
            c.append(Check("ok", f"{n_s} singly occupied orbital(s) match multiplicity {rep.multiplicity}"))
        else:
            c.append(Check("warn", f"{n_s} SOMO(s) but multiplicity {rep.multiplicity} expects {expect}",
                           "Check the electronic state; the singly-occupied window may need manual editing."))

    if rep.s2 is not None and rep.s2_ideal is not None:
        dev = rep.s2 - rep.s2_ideal
        rel = abs(dev) / rep.s2_ideal if rep.s2_ideal > 1e-6 else abs(dev)
        detail = f"<S²> = {rep.s2:.4f}, ideal {rep.s2_ideal:.4f} (Δ = {dev:+.4f})"
        if rel <= 0.05:
            c.append(Check("ok", "Spin contamination is small", detail))
        elif rel <= 0.10:
            c.append(Check("warn", "Moderate spin contamination", detail))
        else:
            c.append(Check("bad", "Strong spin contamination",
                           detail + ". QROs/SOMOs may not represent the true state "
                           "(possible broken-symmetry or multireference character)."))

    if rep.magnetic_pairs:
        mag = [p for p in rep.magnetic_pairs if p[1] < 0.95]
        if mag:
            c.append(Check("info", f"{len(mag)} magnetic (non-overlapping) corresponding-orbital pair(s)",
                           ", ".join(f"#{i}: {ov:.3f}" for i, ov in mag[:8])))

    gap = frontier_gap(rep)
    if gap is not None:
        status = "ok" if gap >= 1.0 else "warn"
        c.append(Check(status, f"Frontier gap {gap:.2f} eV",
                       "" if gap >= 1.0 else "A very small gap means near-degenerate orbitals; "
                       "localized orbitals may mix."))
    return c


def frontier_gap(rep: OrbitalReport) -> Optional[float]:
    occ = [o for o in rep.orbitals if o.occ > 0]
    lumo = rep.lumo
    if not occ or lumo is None:
        return None
    return lumo.e_ev - occ[-1].e_ev


def suggest_ranges(rep: OrbitalReport, core_cutoff_au: float = -1.0, n_virtual: int = 5) -> Ranges:
    """Default windows: valence doubly (E > cutoff), all SOMOs, first n virtuals."""
    doubly = rep.by_occ(2)
    core = [o for o in doubly if o.e_au <= core_cutoff_au]
    valence = [o for o in doubly if o.e_au > core_cutoff_au]
    somos = rep.somos
    virt = rep.by_occ(0)
    last_occ = max([o.index for o in rep.orbitals if o.occ > 0], default=-1)
    v = [o for o in virt if o.index > last_occ][:max(0, n_virtual)]
    return Ranges(
        d_start=valence[0].index if valence else -1,
        d_end=valence[-1].index if valence else -1,
        s_start=somos[0].index if somos else -1,
        s_end=somos[-1].index if somos else -1,
        v_start=v[0].index if v else -1,
        v_end=v[-1].index if v else -1,
        n_core=len(core),
    )


# ======================================================================
# ORCA workflow (orca_loc + orca_plot)
# ======================================================================

def loc_input(src: str, dst: str, first: int, last: int, method: int, maxiter: int = 128) -> str:
    """Input for orca_loc (one value per line, as described in the ORCA manual)."""
    return "\n".join([
        src, dst,
        "0",                 # operator: QROs are a single (alpha) set
        str(first), str(last),
        str(maxiter),
        "0.000001",          # convergence tolerance of the localization functional
        "0.0",               # relative convergence tolerance
        "0.95",              # threshold: strongly localized
        "0.85",              # threshold: bond-like
        str(method),
    ]) + "\n"


def plot_stdin(orbitals: List[int], grid: int = 80) -> str:
    """Keystrokes for 'orca_plot <file> -i': MO plots, alpha, cube format."""
    keys = ["1", "1", "3", "0", "4", str(grid), "5", "7"]
    for n in orbitals:
        keys += ["2", str(n), "10"]
    keys.append("11")
    return "\n".join(keys) + "\n"


def find_orca_tools(extra_dir: str = "") -> Dict[str, Optional[str]]:
    """Locate orca_loc / orca_plot from a user folder, the Software Manager, or PATH."""
    dirs = []
    if extra_dir:
        dirs.append(extra_dir if os.path.isdir(extra_dir) else os.path.dirname(extra_dir))
    try:
        from modules.software_manager import SoftwareManager
        p = SoftwareManager.get_software_path("ORCA")
        if p:
            dirs.append(os.path.dirname(p))
    except Exception:
        pass
    found: Dict[str, Optional[str]] = {}
    for tool in ("orca_loc", "orca_plot"):
        hit = None
        for d in dirs:
            for name in (tool + ".exe", tool):
                cand = os.path.join(d, name)
                if os.path.isfile(cand):
                    hit = cand
                    break
            if hit:
                break
        found[tool] = hit or shutil.which(tool)
    return found


@dataclass
class OrbitalJob:
    orbital_file: str
    ranges: Ranges
    sets: List[str]                  # subset of "doubly", "singly", "unoccupied"
    localize: bool = True
    method: int = 1
    grid: int = 80
    maxiter: int = 128
    out_dir: str = ""

    def plan(self) -> List[Dict]:
        stem = os.path.splitext(os.path.basename(self.orbital_file))[0]
        ext = os.path.splitext(self.orbital_file)[1]
        windows = self.ranges.as_sets()
        steps = []
        for name in self.sets:
            if name not in windows:
                continue
            a, b = windows[name]
            loc_name = f"{stem}.{name}.loc{ext}" if self.localize else os.path.basename(self.orbital_file)
            steps.append({
                "set": name, "first": a, "last": b,
                "folder": os.path.join(self.out_dir, name),
                "src": os.path.basename(self.orbital_file),
                "loc_file": loc_name,
                "orbitals": list(range(a, b + 1)),
            })
        return steps


def prepare_job_folder(job: OrbitalJob) -> List[Dict]:
    """Write orca_loc inputs and orca_plot key files into <out_dir>/<set>/."""
    steps = job.plan()
    for st in steps:
        os.makedirs(st["folder"], exist_ok=True)
        shutil.copy2(job.orbital_file, os.path.join(st["folder"], st["src"]))
        if job.localize:
            with open(os.path.join(st["folder"], "loc.inp"), "w", newline="\n") as fh:
                fh.write(loc_input(st["src"], st["loc_file"], st["first"], st["last"], job.method, job.maxiter))
        with open(os.path.join(st["folder"], "plot.keys"), "w", newline="\n") as fh:
            fh.write(plot_stdin(st["orbitals"], job.grid))
    return steps


def write_run_scripts(job: OrbitalJob, steps: List[Dict], module_line: str = "") -> List[str]:
    """Portable run scripts so the bundle can be run on a workstation or an HPC node."""
    sh = ["#!/bin/bash", "# Generated by AutoChemy Orbital Creator", "set -e"]
    if module_line.strip():
        sh.append(module_line.strip())
    sh.append('ORCA_DIR=${ORCA_DIR:-$(dirname "$(command -v orca_plot)")}')
    sh.append('cd "$(dirname "$0")"')
    bat = ["@echo off", "rem Generated by AutoChemy Orbital Creator",
           "if not defined ORCA_DIR (for %%i in (orca_plot.exe) do set ORCA_DIR=%%~dp$PATH:i)",
           "cd /d %~dp0"]
    for st in steps:
        name = st["set"]
        sh.append(f'echo "== {name}: orbitals {st["first"]}-{st["last"]} =="')
        sh.append(f"(cd {name}")
        bat.append(f"echo == {name}: orbitals {st['first']}-{st['last']} ==")
        bat.append(f"pushd {name}")
        if job.localize:
            sh.append(f'  "$ORCA_DIR/orca_loc" loc.inp > loc.out')
            bat.append('"%ORCA_DIR%orca_loc.exe" loc.inp > loc.out')
        sh.append(f'  "$ORCA_DIR/orca_plot" {st["loc_file"]} -i < plot.keys > plot.out)')
        bat.append(f'"%ORCA_DIR%orca_plot.exe" {st["loc_file"]} -i < plot.keys > plot.out')
        bat.append("popd")
    sh.append('echo "Done. Cube files are in each set folder."')
    bat.append("echo Done. Cube files are in each set folder.")
    paths = []
    for name, lines, nl in (("run_orbitals.sh", sh, "\n"), ("run_orbitals.bat", bat, "\r\n")):
        p = os.path.join(job.out_dir, name)
        with open(p, "w", newline="") as fh:
            fh.write(nl.join(lines) + nl)
        paths.append(p)
    return paths


def run_job_locally(job: OrbitalJob, tools: Dict[str, Optional[str]],
                    log: Callable[[str, str], None]) -> List[str]:
    """Run orca_loc / orca_plot for every set. Returns the generated cube files."""
    steps = prepare_job_folder(job)
    cubes: List[str] = []
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for st in steps:
        folder = st["folder"]
        log(f"▶ {st['set']}: orbitals {st['first']}–{st['last']}", "info")
        if job.localize:
            with open(os.path.join(folder, "loc.inp")) as fin, open(os.path.join(folder, "loc.out"), "w") as fout:
                proc = subprocess.run([tools["orca_loc"], "loc.inp"], cwd=folder, stdin=fin, stdout=fout,
                                      stderr=subprocess.STDOUT, creationflags=flags)
            if proc.returncode != 0 or not os.path.isfile(os.path.join(folder, st["loc_file"])):
                log(f"  orca_loc failed (see {os.path.join(folder, 'loc.out')})", "bad")
                continue
            log("  localization done", "ok")
        before = set(os.listdir(folder))
        with open(os.path.join(folder, "plot.keys")) as fin, open(os.path.join(folder, "plot.out"), "w") as fout:
            proc = subprocess.run([tools["orca_plot"], st["loc_file"], "-i"], cwd=folder, stdin=fin,
                                  stdout=fout, stderr=subprocess.STDOUT, creationflags=flags)
        new = sorted(f for f in set(os.listdir(folder)) - before if f.lower().endswith(".cube"))
        if proc.returncode != 0 and not new:
            log(f"  orca_plot failed (see {os.path.join(folder, 'plot.out')})", "bad")
            continue
        log(f"  {len(new)} cube file(s) written", "ok")
        cubes += [os.path.join(folder, f) for f in new]
    return cubes
