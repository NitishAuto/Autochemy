"""
Shared xTB helpers: executable discovery, xcontrol formatting, and background worker.
Used by the standalone XTB module (and kept isolated from ORCA input templating).
"""

from __future__ import annotations

import csv
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time

XTB_G_2026_LABEL = "g-xtb (2026)"
XTB_2024_LABEL = "xtb (2024)"
XTB_VERSION_LABELS = (XTB_G_2026_LABEL,)
DEFAULT_XTB_VERSION_LABEL = XTB_G_2026_LABEL

__all__ = [
    "has_wsl",
    "get_wsl_cmd",
    "windows_to_wsl_path",
    "find_xtb_exe",
    "bundled_xtb_versions",
    "default_xtb_version_label",
    "find_crest_exe",
    "find_chemcraft_exe",
    "default_xtb_work_parent",
    "default_crest_work_parent",
    "parse_xyz_atom_lines",
    "parse_xyz_symbols",
    "parse_xtbscan_energies",
    "build_scan_axis_values",
    "convert_relative_energies",
    "build_scan_coordinate_label",
    "write_input_xyz",
    "format_xcontrol_content",
    "build_xtb_argv",
    "build_crest_argv",
    "xtb_thread_worker",
    "crest_thread_worker",
    "parse_multi_xyz_comment_energies",
    "parse_crest_energies_rel_kcal",
    "collect_crest_energies_hartree",
    "write_crest_conformers_csv",
    "embed_crest_energy_bar_chart",
    "dedupe_xyz_file_to_ensemble",
    "ensure_cregen_reference_basename",
]


def _suite_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundled_xtb_versions(base_dir: str | None = None) -> dict[str, str]:
    """Known project-local xTB executables, ordered by preference."""
    root = base_dir or _suite_root()
    xtb_root = os.path.join(root, "external_modules", "xtb")
    gxtb_exe = os.path.join(
        xtb_root,
        "g-xtb",
        "g-xtb-main",
        "binaries",
        "xtb-6.7.1-gxtb-210426-windows-x86_64",
        "xtb-6.7.1-gxtb-210426-windows-x86_64",
        "bin",
        "xtb.exe",
    )
    return {
        XTB_G_2026_LABEL: gxtb_exe,
        XTB_2024_LABEL: gxtb_exe,
    }


def default_xtb_version_label() -> str:
    return DEFAULT_XTB_VERSION_LABEL


def has_wsl() -> bool:
    wsl_cmd = shutil.which("wsl") or shutil.which("wsl.exe")
    if wsl_cmd:
        return True
    return os.path.isfile(r"C:\Windows\System32\wsl.exe")


def get_wsl_cmd() -> list[str]:
    wsl_cmd = shutil.which("wsl.exe") or shutil.which("wsl") or r"C:\Windows\System32\wsl.exe"
    distro = (os.environ.get("WSL_DISTRO") or "").strip()
    if distro:
        return [wsl_cmd, "-d", distro]
    return [wsl_cmd, "-d", "Ubuntu-24.04"]


def windows_to_wsl_path(path: str) -> str:
    norm = os.path.abspath(path).replace("\\", "/")
    if len(norm) >= 2 and norm[1] == ":":
        drive = norm[0].lower()
        rest = norm[2:]
        return f"/mnt/{drive}{rest}"
    return norm


def find_xtb_exe(version_label: str | None = None) -> str | None:
    base_dir = _suite_root()
    bundled = bundled_xtb_versions(base_dir)
    candidates = [
        bundled.get(version_label or DEFAULT_XTB_VERSION_LABEL, ""),
        bundled.get(XTB_G_2026_LABEL, ""),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def find_crest_exe() -> str | None:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(base_dir, "external_modules", "xtb", "crest.exe"),
        os.path.join(base_dir, "external_modules", "xtb", "crest-3.0.2", "bin", "crest.exe"),
        os.path.join(base_dir, "crest", "crest.exe"),
        os.path.join(base_dir, "xtb", "crest-3.0.2", "bin", "crest.exe"),
        os.path.join(base_dir, "xtb", "crest-3.0.2", "build", "crest.exe"),
        os.path.join(base_dir, "crest.exe"),
        os.path.join(base_dir, "crest", "bin", "crest.exe"),
        os.path.join(base_dir, "crest-3.0.2", "bin", "crest.exe"),
        os.path.join(base_dir, "crest-3.0.2", "_build", "crest.exe"),
        os.path.join(base_dir, "crest-3.0.2", "build", "crest.exe"),
        os.environ.get("CREST_EXE", ""),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    found = shutil.which("crest")
    if found:
        return found
    if has_wsl():
        return "wsl:crest"
    return None


def find_chemcraft_exe() -> str | None:
    env = (os.environ.get("CHEMCRAFT_EXE") or "").strip()
    if env and os.path.isfile(env):
        return env
    candidates = [
        r"C:\Program Files\Chemcraft\Chemcraft.exe",
        r"C:\Program Files (x86)\Chemcraft\Chemcraft.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return shutil.which("Chemcraft")


def default_xtb_work_parent() -> str:
    suite_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_parent = (os.environ.get("XTB_WORK_PARENT") or "").strip()
    return env_parent if env_parent else os.path.join(suite_root, "external_modules", "xtb", "xtb_runs")


def default_crest_work_parent() -> str:
    suite_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_parent = (os.environ.get("CREST_WORK_PARENT") or "").strip()
    return env_parent if env_parent else os.path.join(suite_root, "external_modules", "xtb", "crest_runs")


def parse_xyz_atom_lines(geom_text: str) -> list[str]:
    atom_lines: list[str] = []
    for line in geom_text.split("\n"):
        line_s = line.split("#")[0].strip()
        if not line_s:
            continue
        parts = line_s.split()
        if len(parts) >= 4 and not parts[0].isdigit():
            try:
                float(parts[1])
                float(parts[2])
                float(parts[3])
                atom_lines.append(line_s)
            except ValueError:
                pass
    return atom_lines


def _detect_unphysical_contacts(atom_lines: list[str], min_dist_ang: float = 0.20) -> str | None:
    """
    Return an error message if atom pairs are unrealistically close.
    CREST/xTB frequently aborts initial optimization for coincident atoms.
    """
    atoms: list[tuple[str, float, float, float]] = []
    for i, line in enumerate(atom_lines, start=1):
        parts = line.split()
        if len(parts) < 4:
            return f"Invalid atom line at #{i}: {line}"
        try:
            atoms.append((parts[0], float(parts[1]), float(parts[2]), float(parts[3])))
        except ValueError:
            return f"Invalid numeric coordinate at atom #{i}: {line}"

    n = len(atoms)
    for i in range(n):
        si, xi, yi, zi = atoms[i]
        for j in range(i + 1, n):
            sj, xj, yj, zj = atoms[j]
            dx = xi - xj
            dy = yi - yj
            dz = zi - zj
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            if dist < min_dist_ang:
                return (
                    f"Input geometry has overlapping/near-overlapping atoms: "
                    f"#{i+1} ({si}) and #{j+1} ({sj}) are {dist:.4f} Å apart "
                    f"(minimum allowed sanity threshold is {min_dist_ang:.2f} Å)."
                )
    return None


def parse_xyz_symbols(geom_text: str) -> list[str]:
    symbols: list[str] = []
    for line in parse_xyz_atom_lines(geom_text):
        token = line.split()[0].strip()
        if not token:
            symbols.append("?")
            continue
        clean = "".join(ch for ch in token if ch.isalpha())
        if not clean:
            symbols.append("?")
            continue
        symbols.append(clean[0].upper() + clean[1:].lower())
    return symbols


def parse_xtbscan_energies(scan_log_path: str) -> list[float]:
    energies: list[float] = []
    with open(scan_log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("energy:"):
                parts = line.split()
                if len(parts) >= 2:
                    energies.append(float(parts[1]))
            elif " energy:" in line:
                idx = line.index("energy:")
                parts = line[idx:].split()
                if len(parts) >= 2:
                    energies.append(float(parts[1]))
    return energies


def build_scan_axis_values(start: float, end: float, n_points: int) -> list[float]:
    if n_points <= 0:
        return []
    if n_points == 1:
        return [start]
    step = (end - start) / (n_points - 1)
    return [start + i * step for i in range(n_points)]


def convert_relative_energies(energies_hartree: list[float], unit: str = "kcal/mol") -> tuple[list[float], str]:
    if not energies_hartree:
        return [], "Relative Energy"
    e_min = min(energies_hartree)
    rel_eh = [e - e_min for e in energies_hartree]
    if unit == "Eh":
        return rel_eh, "Relative Energy (Eh)"
    if unit == "kJ/mol":
        return [v * 2625.49962 for v in rel_eh], "Relative Energy (kJ/mol)"
    return [v * 627.509 for v in rel_eh], "Relative Energy (kcal/mol)"


def build_scan_coordinate_label(scan_meta: dict | None, geom_text: str) -> tuple[str, str]:
    if not scan_meta:
        return "Scan Point Index", "xTB Relaxed PES Scan"
    ctype = str(scan_meta.get("ctype", "Bond"))
    symbols = parse_xyz_symbols(geom_text)

    def sym(idx1: int) -> str:
        if idx1 <= 0 or idx1 > len(symbols):
            return "?"
        return symbols[idx1 - 1]

    def as_int(v, default=0):
        try:
            if v is None:
                return default
            return int(v)
        except Exception:
            return default

    a1 = as_int(scan_meta.get("a1", 0))
    a2 = as_int(scan_meta.get("a2", 0))
    a3 = as_int(scan_meta.get("a3", 0))
    a4 = as_int(scan_meta.get("a4", 0))
    if a1 <= 0 or a2 <= 0:
        return "Scan Point Index", "xTB Relaxed PES Scan"

    ctype_l = ctype.lower()
    if ctype_l == "bond":
        x_label = f"Bond length {sym(a1)}({a1})-{sym(a2)}({a2}) [Angstrom]"
        title = f"xTB Relaxed PES Scan - Bond {sym(a1)}({a1})-{sym(a2)}({a2})"
    elif ctype_l == "angle":
        if a3 <= 0:
            x_label = f"Angle {sym(a1)}({a1})-{sym(a2)}({a2})-? [deg]"
            title = f"xTB Relaxed PES Scan - Angle {sym(a1)}({a1})-{sym(a2)}({a2})-?"
            return x_label, title
        x_label = f"Angle {sym(a1)}({a1})-{sym(a2)}({a2})-{sym(a3)}({a3}) [deg]"
        title = (
            f"xTB Relaxed PES Scan - Angle "
            f"{sym(a1)}({a1})-{sym(a2)}({a2})-{sym(a3)}({a3})"
        )
    else:
        if a3 <= 0 or a4 <= 0:
            x_label = f"Dihedral {sym(a1)}({a1})-{sym(a2)}({a2})-?-? [deg]"
            title = f"xTB Relaxed PES Scan - Dihedral {sym(a1)}({a1})-{sym(a2)}({a2})-?-?"
            return x_label, title
        x_label = (
            f"Dihedral {sym(a1)}({a1})-{sym(a2)}({a2})-"
            f"{sym(a3)}({a3})-{sym(a4)}({a4}) [deg]"
        )
        title = (
            f"xTB Relaxed PES Scan - Dihedral "
            f"{sym(a1)}({a1})-{sym(a2)}({a2})-{sym(a3)}({a3})-{sym(a4)}({a4})"
        )
    return x_label, title


def write_input_xyz(path: str, geom_text: str) -> int:
    atom_lines = parse_xyz_atom_lines(geom_text)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{len(atom_lines)}\nORCA Suite xTB module\n")
        f.write("\n".join(atom_lines) + "\n")
    return len(atom_lines)


def format_xcontrol_content(xtb_task_cfg: dict) -> tuple[str, bool]:
    """Return (xcontrol text, is_scan)."""
    scan_data = xtb_task_cfg.get("scan")
    scan_constraint = xtb_task_cfg.get("scan_constraint")
    constrain_lines = list(xtb_task_cfg.get("constraints") or [])
    is_scan = False

    if scan_data and scan_constraint:
        is_scan = True
        constrain_lines.append(scan_constraint)

    xcontrol_content = ""
    if constrain_lines:
        xcontrol_content += "$constrain\n force constant=1\n"
        for c in constrain_lines:
            xcontrol_content += f" {c}\n"

    if is_scan and scan_data:
        scan_idx = len(constrain_lines)
        xcontrol_content += "$scan\n"
        xcontrol_content += (
            f" {scan_idx}: {scan_data['start']},{scan_data['end']},{scan_data['steps']}\n"
        )
        xcontrol_content += "$end\n"
    elif constrain_lines:
        xcontrol_content += "$end\n"

    return xcontrol_content, is_scan


def build_xtb_argv(
    job: str,
    opt_level: str,
    gfn: str,
    chrg: int,
    uhf: int,
    use_xcontrol: bool,
    xtb_method: str = "gfn2",
    xtb_task_cfg: dict = None,
) -> list[str]:
    xtb_args: list[str] = []
    if job == "hess":
        xtb_args.append("--hess")
    elif job == "ohess":
        xtb_args.extend(["--ohess", opt_level])
    elif job in ("opt", "scan"):
        xtb_args.extend(["--opt", opt_level])
    xtb_args.extend(["--chrg", str(chrg), "--uhf", str(uhf)])
    if (xtb_method or "").strip().lower() == "gxtb":
        xtb_args.append("--gxtb")
    else:
        xtb_args.extend(["--gfn", gfn])
    if use_xcontrol:
        xtb_args.extend(["--input", "xcontrol.inp"])
    
    if xtb_task_cfg and xtb_task_cfg.get("include_solvation") == "Yes":
        solv_model = xtb_task_cfg.get("solvation_model", "gbe")
        solvent = xtb_task_cfg.get("solvent", "")
        if solvent:
            xtb_args.extend([f"--{solv_model}", solvent])
            
    return xtb_args


def build_crest_argv(
    mode: str,
    gfn: str,
    chrg: int,
    uhf: int,
    ewin: float,
    temp: float,
    threads: int,
    solvent: str = "",
    solvent_model: str = "alpb",
    extra_args: str = "",
    xtb_exe: str | None = None,
) -> list[str]:
    crest_args: list[str] = [
        f"-gfn{gfn}",
        "-chrg",
        str(chrg),
        "-uhf",
        str(uhf),
        "-ewin",
        str(ewin),
        "-temp",
        str(temp),
        "-T",
        str(threads),
    ]
    mode_key = mode.strip().lower()
    if mode_key == "quick search":
        crest_args.append("-quick")
    elif mode_key == "entropy workflow":
        crest_args.append("-entropy")
    solvent_clean = solvent.strip()
    solvent_model_l = (solvent_model or "alpb").strip().lower()
    if solvent_clean:
        if solvent_model_l == "gbsa":
            crest_args.extend(["--gbsa", solvent_clean])
        elif solvent_model_l == "alpb":
            crest_args.extend(["-alpb", solvent_clean])
    if xtb_exe:
        crest_args.extend(["-xnam", xtb_exe])
    if extra_args.strip():
        crest_args.extend(shlex.split(extra_args.strip(), posix=False))
    return crest_args


def xtb_thread_worker(
    xtb_queue,
    process_holder: list,
    xtb_exe: str,
    geom_text: str,
    chrg: int,
    uhf: int,
    opt_level: str,
    gfn: str,
    xtb_method: str,
    xtb_task_cfg: dict,
    work_parent_dir: str | None = None,
) -> None:
    """
    Run xtb in a temp directory; post messages to xtb_queue:
    ("log", str), ("result", str), ("error", str), ("done", dict).
    process_holder[0] holds the subprocess.Popen while running (or None).
    """
    atom_lines = parse_xyz_atom_lines(geom_text)
    if not atom_lines:
        xtb_queue.put(("error", "No valid XYZ atom lines in geometry."))
        xtb_queue.put(("done", {"folder": None, "is_scan": False, "job": xtb_task_cfg.get("job", "")}))
        return

    parent_dir = (work_parent_dir or "").strip() or default_xtb_work_parent()
    os.makedirs(parent_dir, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="xtb_run_", dir=parent_dir)
    input_xyz_path = os.path.join(temp_dir, "input.xyz")

    with open(input_xyz_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{len(atom_lines)}\nORCA Suite xTB module\n")
        f.write("\n".join(atom_lines) + "\n")

    job = xtb_task_cfg["job"]
    xcontrol_content, is_scan = format_xcontrol_content(xtb_task_cfg)
    use_xcontrol = bool(xcontrol_content)
    if use_xcontrol:
        xcontrol_path = os.path.join(temp_dir, "xcontrol.inp")
        with open(xcontrol_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(xcontrol_content)

    xtb_args = build_xtb_argv(job, opt_level, gfn, chrg, uhf, use_xcontrol, xtb_method, xtb_task_cfg)
    res_out_path = os.path.join(temp_dir, "res.out")
    cmd = [xtb_exe, "input.xyz"] + xtb_args
    cmd_display = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    xtb_queue.put(("log", f"Working dir: {temp_dir}\n$ {cmd_display}  (stdout → res.out)\n\n"))

    res_fh = open(res_out_path, "w", encoding="utf-8", errors="replace")
    try:
        popen_kw: dict = {
            "cwd": temp_dir,
            "stdout": res_fh,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            popen_kw["startupinfo"] = startupinfo
        process_holder[0] = subprocess.Popen(cmd, **popen_kw)
        proc = process_holder[0]

        tail_fh = None
        while proc.poll() is None:
            time.sleep(0.4)
            try:
                if tail_fh is None and os.path.isfile(res_out_path):
                    tail_fh = open(res_out_path, "r", encoding="utf-8", errors="replace")
                if tail_fh:
                    new_text = tail_fh.read()
                    if new_text:
                        xtb_queue.put(("log", new_text))
            except Exception:
                pass

        time.sleep(0.2)
        if tail_fh is None and os.path.isfile(res_out_path):
            tail_fh = open(res_out_path, "r", encoding="utf-8", errors="replace")
        if tail_fh:
            try:
                final = tail_fh.read()
                if final:
                    xtb_queue.put(("log", final))
            except Exception:
                pass
            tail_fh.close()
    except Exception as e:
        xtb_queue.put(("error", str(e)))
    finally:
        try:
            res_fh.close()
        except Exception:
            pass
        process_holder[0] = None

    try:
        shutil.copy2(res_out_path, os.path.join(temp_dir, "xtb_full.log"))
    except Exception:
        pass

    try:
        all_files = os.listdir(temp_dir)
        xtb_queue.put(("log", f"\n\n--- Files produced in {temp_dir} ---\n"))
        for fn in sorted(all_files):
            fp = os.path.join(temp_dir, fn)
            sz = os.path.getsize(fp) if os.path.isfile(fp) else 0
            xtb_queue.put(("log", f"  {fn:30s} {sz:>10,} bytes\n"))
    except Exception:
        pass

    result_xyz = None
    for candidate in ("xtbopt.xyz", "xtblast.xyz"):
        p = os.path.join(temp_dir, candidate)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                result_xyz = f.read()
            break

    if result_xyz:
        xtb_queue.put(("result", result_xyz))
    elif job in ("opt", "ohess", "scan"):
        xtb_queue.put(("error", "Optimized geometry file was not produced — check the log above."))

    xtb_queue.put(("done", {"folder": temp_dir, "is_scan": is_scan, "job": job}))


def crest_thread_worker(
    crest_queue,
    process_holder: list,
    crest_exe: str,
    geom_text: str,
    chrg: int,
    uhf: int,
    crest_cfg: dict,
    work_parent_dir: str | None = None,
    xtb_exe: str | None = None,
) -> None:
    """
    Run crest in a temp directory; post messages to crest_queue:
    ("log", str), ("result", str), ("error", str), ("done", dict).
    process_holder[0] holds the subprocess.Popen while running (or None).
    """
    atom_lines = parse_xyz_atom_lines(geom_text)
    if not atom_lines:
        crest_queue.put(("error", "No valid XYZ atom lines in geometry."))
        crest_queue.put(("done", {"folder": None, "engine": "crest", "job": crest_cfg.get("mode", "")}))
        return
    geom_err = _detect_unphysical_contacts(atom_lines)
    if geom_err:
        crest_queue.put(("error", geom_err))
        crest_queue.put(
            (
                "error",
                "CREST input sanity check failed before launch. Fix geometry (remove duplicate/overlapping atoms) and retry.",
            )
        )
        crest_queue.put(("done", {"folder": None, "engine": "crest", "job": crest_cfg.get("mode", "")}))
        return

    parent_dir = (work_parent_dir or "").strip() or default_crest_work_parent()
    os.makedirs(parent_dir, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="crest_run_", dir=parent_dir)
    input_xyz_path = os.path.join(temp_dir, "input.xyz")

    with open(input_xyz_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{len(atom_lines)}\nORCA Suite CREST module\n")
        f.write("\n".join(atom_lines) + "\n")

    mode = str(crest_cfg.get("mode", "Conformer search"))
    crest_args = build_crest_argv(
        mode=mode,
        gfn=str(crest_cfg.get("gfn", "2")),
        chrg=chrg,
        uhf=uhf,
        ewin=float(crest_cfg.get("ewin", 6.0)),
        temp=float(crest_cfg.get("temp", 298.15)),
        threads=int(crest_cfg.get("threads", 4)),
        solvent=str(crest_cfg.get("solvent", "")),
        solvent_model=str(crest_cfg.get("solvent_model", "alpb")),
        extra_args=str(crest_cfg.get("extra_args", "")),
        xtb_exe=xtb_exe,
    )
    res_out_path = os.path.join(temp_dir, "crest.out")
    use_wsl = str(crest_exe).startswith("wsl:")
    cmd_display = ""

    if use_wsl:
        wsl_cmd = get_wsl_cmd()
        wsl_temp_dir = windows_to_wsl_path(temp_dir)
        crest_args_wsl = build_crest_argv(
            mode=mode,
            gfn=str(crest_cfg.get("gfn", "2")),
            chrg=chrg,
            uhf=uhf,
            ewin=float(crest_cfg.get("ewin", 6.0)),
            temp=float(crest_cfg.get("temp", 298.15)),
            threads=int(crest_cfg.get("threads", 4)),
            solvent=str(crest_cfg.get("solvent", "")),
            solvent_model=str(crest_cfg.get("solvent_model", "alpb")),
            extra_args=str(crest_cfg.get("extra_args", "")),
            xtb_exe=None,
        )
        crest_cmd = " ".join(shlex.quote(c) for c in (["crest", "input.xyz"] + crest_args_wsl))
        shell_cmd = f"cd {shlex.quote(wsl_temp_dir)} && {crest_cmd}"
        cmd = wsl_cmd + ["sh", "-lc", shell_cmd]
        cmd_display = " ".join(wsl_cmd) + f' sh -lc "{shell_cmd}"'
    else:
        cmd = [crest_exe, "input.xyz"] + crest_args
        cmd_display = " ".join(f'"{c}"' if " " in c else c for c in cmd)

    crest_queue.put(("log", f"Working dir: {temp_dir}\n$ {cmd_display}\n\n"))

    try:
        if use_wsl:
            popen_kw: dict = {
                "cwd": temp_dir,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                popen_kw["startupinfo"] = startupinfo
            process_holder[0] = subprocess.Popen(cmd, **popen_kw)
            proc = process_holder[0]
            captured_chunks: list[str] = []
            if proc.stdout is not None:
                for line in proc.stdout:
                    captured_chunks.append(line)
                    crest_queue.put(("log", line))
            proc.wait()
            with open(res_out_path, "w", encoding="utf-8", newline="\n", errors="replace") as f:
                f.write("".join(captured_chunks))
            if proc.returncode != 0:
                combined = "".join(captured_chunks)
                if "Initial geometry optimization failed" in combined:
                    crest_queue.put(
                        (
                            "error",
                            "CREST failed during initial geometry optimization. "
                            "This is usually caused by problematic input coordinates (overlaps/bad geometry).",
                        )
                    )
                crest_queue.put(
                    (
                        "error",
                        "WSL CREST execution failed (non-zero exit). If CREST was not found, install it inside WSL "
                        "(e.g. 'conda install -c conda-forge crest'); otherwise inspect crest.out for chemistry/input errors.",
                    )
                )
        else:
            res_fh = open(res_out_path, "w", encoding="utf-8", errors="replace")
            try:
                popen_kw = {
                    "cwd": temp_dir,
                    "stdout": res_fh,
                    "stderr": subprocess.STDOUT,
                }
                if os.name == "nt":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    popen_kw["startupinfo"] = startupinfo
                process_holder[0] = subprocess.Popen(cmd, **popen_kw)
                proc = process_holder[0]

                tail_fh = None
                while proc.poll() is None:
                    time.sleep(0.4)
                    try:
                        if tail_fh is None and os.path.isfile(res_out_path):
                            tail_fh = open(res_out_path, "r", encoding="utf-8", errors="replace")
                        if tail_fh:
                            new_text = tail_fh.read()
                            if new_text:
                                crest_queue.put(("log", new_text))
                    except Exception:
                        pass

                time.sleep(0.2)
                if tail_fh is None and os.path.isfile(res_out_path):
                    tail_fh = open(res_out_path, "r", encoding="utf-8", errors="replace")
                if tail_fh:
                    try:
                        final = tail_fh.read()
                        if final:
                            crest_queue.put(("log", final))
                    except Exception:
                        pass
                    tail_fh.close()
            finally:
                try:
                    res_fh.close()
                except Exception:
                    pass
    except Exception as e:
        crest_queue.put(("error", str(e)))
    finally:
        process_holder[0] = None

    result_xyz = None
    for candidate in ("crest_best.xyz", "crest_conformers.xyz", "crest_rotamers.xyz"):
        p = os.path.join(temp_dir, candidate)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                result_xyz = f.read()
            break
    if result_xyz:
        crest_queue.put(("result", result_xyz))
    else:
        crest_queue.put(("error", "CREST did not produce a conformer ensemble file. Check crest.out."))

    try:
        all_files = os.listdir(temp_dir)
        crest_queue.put(("log", f"\n\n--- Files produced in {temp_dir} ---\n"))
        for fn in sorted(all_files):
            fp = os.path.join(temp_dir, fn)
            sz = os.path.getsize(fp) if os.path.isfile(fp) else 0
            crest_queue.put(("log", f"  {fn:30s} {sz:>10,} bytes\n"))
    except Exception:
        pass

    crest_queue.put(("done", {"folder": temp_dir, "engine": "crest", "job": mode}))


def parse_multi_xyz_comment_energies(path: str) -> list[float]:
    """Read first float on each XYZ frame comment line (CREST uses energies in Hartree here)."""
    energies: list[float] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            while True:
                n_line = f.readline()
                if not n_line:
                    break
                n_line = n_line.strip()
                if not n_line:
                    continue
                try:
                    n_atoms = int(n_line)
                except ValueError:
                    continue
                comment = f.readline()
                if not comment:
                    break
                try:
                    energies.append(float(comment.strip().split()[0]))
                except (ValueError, IndexError):
                    pass
                for _ in range(n_atoms):
                    if not f.readline():
                        break
    except OSError:
        return []
    return energies


def parse_crest_energies_rel_kcal(path: str) -> list[float]:
    """Parse crest.energies: each line is index + relative energy in kcal/mol (CREST 3.x style)."""
    out: list[float] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = re.split(r"\s+", line)
                if len(parts) < 2:
                    continue
                try:
                    out.append(float(parts[-1]))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def _anchor_energy_hartree_from_xyz(path: str) -> float | None:
    vals = parse_multi_xyz_comment_energies(path)
    return vals[0] if vals else None


def collect_crest_energies_hartree(folder: str) -> list[float] | None:
    """
    Build a list of total energies (Hartree) for CREST conformers.
    Prefer crest_conformers.xyz / crest_rotamers.xyz comment lines; otherwise crest.energies + anchor XYZ.
    """
    for name in ("crest_conformers.xyz", "crest_rotamers.xyz"):
        pth = os.path.join(folder, name)
        if os.path.isfile(pth):
            vals = parse_multi_xyz_comment_energies(pth)
            if vals:
                return vals
    ene_path = os.path.join(folder, "crest.energies")
    if not os.path.isfile(ene_path):
        return None
    kcals = parse_crest_energies_rel_kcal(ene_path)
    if not kcals:
        return None
    e_min = None
    for name in ("crest_best.xyz", "crest_conformers.xyz", "crest_rotamers.xyz"):
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            e_min = _anchor_energy_hartree_from_xyz(p)
            if e_min is not None:
                break
    if e_min is None:
        return None
    return [e_min + k / 627.509 for k in kcals]


def write_crest_conformers_csv(folder: str, energies: list[float]) -> str | None:
    """Write crest_conformers.csv with the same columns as the conformational sampling post-processor."""
    if not energies:
        return None
    out_path = os.path.join(folder, "crest_conformers.csv")
    e_min = min(energies)
    try:
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            w = csv.writer(f)
            w.writerow(["index", "energy_eh", "rel_eh", "rel_kj_mol", "rel_kcal_mol"])
            for idx, e in enumerate(energies, start=1):
                rel_eh = e - e_min
                w.writerow(
                    [
                        idx,
                        f"{e:.10f}",
                        f"{rel_eh:.10f}",
                        f"{rel_eh * 2625.49962:.6f}",
                        f"{rel_eh * 627.509:.6f}",
                    ]
                )
    except OSError:
        return None
    return out_path


def _read_xyz_frames_raw(path: str) -> list[tuple[str, list[str]]]:
    """Return list of (comment_line, atom_lines) from a multi-frame XYZ file."""
    frames: list[tuple[str, list[str]]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            while True:
                n_line = f.readline()
                if not n_line:
                    break
                n_line = n_line.strip()
                if not n_line:
                    continue
                try:
                    n_atoms = int(n_line)
                except ValueError:
                    break
                comment = f.readline().rstrip("\n")
                atoms: list[str] = []
                for _ in range(n_atoms):
                    line = f.readline()
                    if not line:
                        break
                    atoms.append(line.rstrip("\n"))
                if len(atoms) != n_atoms:
                    break
                frames.append((comment, atoms))
    except OSError:
        return []
    return frames


def _write_single_xyz_frame(dst_path: str, comment: str, atom_lines: list[str]) -> bool:
    try:
        with open(dst_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"{len(atom_lines)}\n")
            f.write((comment or "").rstrip("\n") + "\n")
            f.write("\n".join(atom_lines) + "\n")
    except OSError:
        return False
    return True


def ensure_cregen_reference_basename(folder: str) -> str | None:
    """
    Basename for CREST argv[1] when running CREGEN (manual: crest <ref.xyz> --cregen <ensemble.xyz>).
    Preference: crest_best.xyz, input.xyz; else first frame of crest_rotamers.xyz as _cregen_reference.xyz.
    """
    for name in ("crest_best.xyz", "input.xyz"):
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            return name
    rot = os.path.join(folder, "crest_rotamers.xyz")
    if not os.path.isfile(rot):
        return None
    frames = _read_xyz_frames_raw(rot)
    if not frames:
        return None
    dst_name = "_cregen_reference.xyz"
    dst = os.path.join(folder, dst_name)
    comment, lines = frames[0]
    if not _write_single_xyz_frame(dst, comment, lines):
        return None
    return dst_name


def _atom_row_to_coord(sym: str, line: str) -> tuple[str, float, float, float] | None:
    parts = line.split()
    if len(parts) < 4:
        return None
    try:
        x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
    except ValueError:
        return None
    s = sym.strip()
    if not s:
        return None
    return (s, x, y, z)


def _frame_to_coords(atom_lines: list[str]):
    """Return (element_symbols, n×3 numpy array) or None if inconsistent."""
    try:
        import numpy as np
    except ImportError:
        return None
    syms: list[str] = []
    coords: list[list[float]] = []
    for line in atom_lines:
        parts = line.split()
        if len(parts) < 4:
            return None
        sym = parts[0]
        try:
            coords.append([float(parts[1]), float(parts[2]), float(parts[3])])
        except ValueError:
            return None
        syms.append(sym)
    return syms, np.asarray(coords, dtype=np.float64)


def _kabsch_rmsd(p, q) -> float:
    """RMSD (Å) after Kabsch alignment; p,q are (n,3) numpy arrays."""
    import numpy as np

    if p.shape != q.shape or p.ndim != 2 or p.shape[1] != 3:
        return float("inf")
    pc = p - p.mean(axis=0)
    qc = q - q.mean(axis=0)
    h = pc.T @ qc
    u, _s, vt = np.linalg.svd(h)
    r = vt.T @ u.T
    if np.linalg.det(r) < 0:
        vt[-1, :] *= -1
        r = vt.T @ u.T
    qc_aligned = qc @ r.T
    return float(np.sqrt(np.mean(np.sum((pc - qc_aligned) ** 2, axis=1))))


def dedupe_xyz_file_to_ensemble(
    pool_xyz_path: str,
    out_ensemble_path: str,
    ewin_kcal: float,
    rmsd_threshold: float = 0.125,
) -> tuple[int, int, str]:
    """
    Greedy deduplication without calling CREST CREGEN.

    - Sorts frames by energy parsed from the XYZ comment line (first float, Hartree).
    - Keeps only frames within ``ewin_kcal`` of the global minimum (if energies parse).
    - Drops a frame if its Kabsch RMSD to any already-kept frame (same atom count/order)
      is below ``rmsd_threshold`` (default 0.125 Å, similar in spirit to tight CREGEN).

    This is **not** identical to CREGEN (no xTB topology / enantiomer logic) but removes
    obvious geometric duplicates from a rotamer pool.

    Returns ``(n_read, n_kept, err_msg)`` with ``err_msg`` empty on success.
    """
    try:
        import numpy as np
    except ImportError:
        return 0, 0, "NumPy is required for built-in deduplication (pip install numpy)."

    frames = _read_xyz_frames_raw(pool_xyz_path)
    n = len(frames)
    if n == 0:
        return 0, 0, f"No XYZ frames read from {pool_xyz_path!r}."

    energies: list[float] = []
    for comment, _ in frames:
        try:
            energies.append(float(comment.strip().split()[0]))
        except (ValueError, IndexError):
            energies.append(float("nan"))

    finite = [e for e in energies if e == e]
    e_min = min(finite) if finite else None
    use_ewin = e_min is not None

    order = sorted(range(n), key=lambda i: energies[i] if energies[i] == energies[i] else float("inf"))

    kept_sym: list[list[str]] = []
    kept_coords: list = []
    kept_order: list[int] = []

    for i in order:
        e = energies[i]
        if use_ewin and e == e:
            if (e - e_min) * 627.509 > ewin_kcal:
                continue
        comment, atom_lines = frames[i]
        parsed = _frame_to_coords(atom_lines)
        if parsed is None:
            continue
        syms, coords = parsed
        dup = False
        syms_l = [s.lower() for s in syms]
        for ks, kc in zip(kept_sym, kept_coords):
            if len(ks) != len(syms):
                continue
            if [x.lower() for x in ks] != syms_l:
                continue
            if _kabsch_rmsd(coords, kc) < rmsd_threshold:
                dup = True
                break
        if dup:
            continue
        kept_sym.append(syms)
        kept_coords.append(coords.copy())
        kept_order.append(i)

    if not kept_order:
        return n, 0, "Built-in dedup removed all structures (check energies / geometry)."

    # Write by increasing energy among kept
    def _e_key(idx: int) -> float:
        ev = energies[idx]
        return ev if ev == ev else 0.0

    write_sequence = sorted(kept_order, key=_e_key)

    try:
        with open(out_ensemble_path, "w", encoding="utf-8", newline="\n") as fh:
            for i in write_sequence:
                comment, atom_lines = frames[i]
                fh.write(f"{len(atom_lines)}\n{comment}\n")
                fh.write("\n".join(atom_lines) + "\n")
    except OSError as exc:
        return n, 0, f"Could not write {out_ensemble_path}: {exc}"

    return n, len(write_sequence), ""


def _crest_boltzmann_pcts(rel_kcal_sorted: list[float], temperature_k: float = 298.15) -> list[float]:
    """Boltzmann population % for each structure given ΔE in kcal/mol (lowest = 0)."""
    r_kcal = 1.987204258e-3  # kcal/(mol·K)
    rt = r_kcal * max(temperature_k, 1e-6)
    w = [math.exp(-de / rt) for de in rel_kcal_sorted]
    z = sum(w) or 1.0
    return [100.0 * x / z for x in w]


def embed_crest_energy_bar_chart(master, energies: list[float]) -> str:
    """
    Clear ``master`` and draw an informative CREST energy summary:
    horizontal bars sorted by energy (most stable at top), ΔE labels, E_min in Eh,
    Boltzmann populations at 298.15 K, and a small lookup table (Rank vs CREST file order).

    Uses matplotlib when available; otherwise a Tk.Canvas approximation.
    Returns "" on success, or a short error / skip reason.
    """
    import tkinter as tk

    try:
        for w in master.winfo_children():
            try:
                w.destroy()
            except tk.TclError:
                pass
    except tk.TclError:
        pass

    if not energies:
        return "No energies to plot."
    e_min = min(energies)
    e_max = max(energies)
    span_kcal = (e_max - e_min) * 627.509
    n = len(energies)
    rel_kcal = [(e - e_min) * 627.509 for e in energies]
    # (CREST 1-based index as in XYZ order, ΔE kcal/mol), sorted most stable first
    pairs: list[tuple[int, float]] = sorted(enumerate(rel_kcal, start=1), key=lambda t: t[1])
    dE_sorted = [p[1] for p in pairs]
    pcts = _crest_boltzmann_pcts(dE_sorted)

    try:
        import matplotlib

        matplotlib.use("TkAgg")
        from matplotlib import cm
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.colors import Normalize
        from matplotlib.figure import Figure

        fig_h = min(11.0, max(4.2, 0.28 * n + 2.8))
        fig = Figure(figsize=(9.2, fig_h), dpi=100)
        gs = fig.add_gridspec(1, 2, width_ratios=[2.15, 1.0], wspace=0.28)
        ax = fig.add_subplot(gs[0, 0])
        ax_tbl = fig.add_subplot(gs[0, 1])
        ax_tbl.axis("off")

        ymax = max(dE_sorted) if dE_sorted else 1.0
        if ymax < 1e-12:
            ymax = 1.0
        norm = Normalize(vmin=0.0, vmax=ymax)
        colors = [cm.viridis(norm(v)) for v in dE_sorted]
        y_pos = list(range(n))
        ax.barh(y_pos, dE_sorted, color=colors, edgecolor="#334155", linewidth=0.35, height=0.82)
        ax.set_yticks(y_pos)
        fs = 8 if n > 18 else 9
        ax.set_yticklabels([f"#{i + 1}  (file #{p[0]})" for i, p in enumerate(pairs)], fontsize=fs)
        ax.invert_yaxis()
        ax.set_xlabel("ΔE relative to lowest (kcal/mol)")
        ax.set_title(
            f"CREST energies — sorted by stability  (N = {n})\n"
            f"E(lowest) = {e_min:.8f} Eh   |   span = {span_kcal:.3f} kcal/mol",
            fontsize=10,
        )
        ax.grid(True, axis="x", alpha=0.35)
        ax.set_xlim(0.0, ymax * 1.22 if n <= 24 else ymax * 1.06)
        # Value tags on bars (skip if crowded)
        if n <= 24:
            for i, de in enumerate(dE_sorted):
                ax.text(de + 0.015 * ymax, i, f"{de:.2f}", va="center", fontsize=7, color="#1e293b")

        n_tbl = min(14, n)
        rows = []
        for i in range(n_tbl):
            p = pairs[i]
            rows.append([str(i + 1), str(p[0]), f"{p[1]:.3f}", f"{pcts[i]:.2f}"])
        tbl = ax_tbl.table(
            cellText=rows,
            colLabels=["Rank", "CREST#", "ΔE", "%298K"],
            loc="upper center",
            cellLoc="center",
            colLoc="center",
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        tbl.scale(1.05, 1.35)
        ax_tbl.set_title(
            "Boltzmann % @ 298.15 K\n(ideal gas, ΔE only)",
            fontsize=9,
            pad=6,
        )
        if n > n_tbl:
            ax_tbl.text(
                0.5,
                0.02,
                f"+ {n - n_tbl} more (see crest_conformers.csv)",
                transform=ax_tbl.transAxes,
                ha="center",
                fontsize=8,
                color="#64748b",
            )

        fig.subplots_adjust(left=0.20, right=0.98, bottom=0.12, top=0.82)
        canvas = FigureCanvasTkAgg(fig, master=master)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        master.update_idletasks()
        return ""
    except Exception:
        pass

    # Tk.Canvas fallback — horizontal sorted bars + top-3 Boltzmann note
    try:
        w, h = 720, min(520, max(300, 22 * n + 140))
        cv = tk.Canvas(master, width=w, height=h, bg="#e8edf5", highlightthickness=0)
        cv.pack(fill=tk.BOTH, expand=True)
        pad_l, pad_r, pad_t, pad_b = 120, 160, 36, 52
        plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
        max_e = max(dE_sorted) if dE_sorted else 1.0
        if max_e < 1e-12:
            max_e = 1.0
        cv.create_text(
            w // 2,
            14,
            text=f"CREST energies (sorted) — N={n}  |  E(lowest)={e_min:.6f} Eh  |  span={span_kcal:.2f} kcal/mol",
            font=("Segoe UI", 9),
            fill="#0f172a",
        )
        cv.create_text(
            w // 2,
            30,
            text="Tk canvas — install matplotlib for table + color scale",
            font=("Segoe UI", 8),
            fill="#64748b",
        )
        row_h = min(22, max(10, plot_h // max(n, 1)))
        for i, (crest_id, de) in enumerate(pairs):
            y0 = pad_t + i * row_h
            if y0 + row_h > pad_t + plot_h:
                break
            cv.create_text(pad_l - 6, y0 + row_h // 2, text=f"#{i+1} #{crest_id}", anchor="e", font=("Consolas", 8), fill="#334155")
            bw = (de / max_e) * plot_w
            cv.create_rectangle(pad_l, y0 + 2, pad_l + bw, y0 + row_h - 2, fill="#7c3aed", outline="#4c1d95", width=1)
            cv.create_text(pad_l + bw + 6, y0 + row_h // 2, text=f"{de:.2f}", anchor="w", font=("Consolas", 8), fill="#0f172a")
        cv.create_text(pad_l + plot_w // 2, h - 22, text="ΔE (kcal/mol) vs lowest", font=("Segoe UI", 8), fill="#475569")
        top3 = ", ".join(f"#{pairs[j][0]}:{pcts[j]:.1f}%" for j in range(min(3, n)))
        cv.create_text(pad_l, h - 6, text=f"Boltzmann @298K (top 3): {top3}", anchor="w", font=("Segoe UI", 8), fill="#475569")
        master.update_idletasks()
        return ""
    except Exception as exc:
        return f"Could not draw chart: {exc}"
