"""Helpers that stay valid on Windows, macOS, and Linux."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def wheel_scroll_units(event) -> int:
    """Normalize Tk MouseWheel delta. Windows uses ±120; Aqua Tk often uses ±1."""
    delta = int(getattr(event, "delta", 0) or 0)
    if delta == 0:
        return 0
    if sys.platform == "darwin" and abs(delta) < 120:
        return -1 if delta > 0 else 1
    units = int(-delta / 120)
    if units == 0:
        return -1 if delta > 0 else 1
    return units


def first_existing_file(*paths: str, skip_foreign_exe: bool = False) -> str | None:
    for raw in paths:
        path = os.path.expanduser(os.path.expandvars((raw or "").strip()))
        if not path or not os.path.isfile(path):
            continue
        if skip_foreign_exe and os.name != "nt" and path.lower().endswith(".exe"):
            continue
        return path
    return None


def which_any(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def unix_bin_candidates(name: str) -> list[str]:
    return [
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/opt/local/bin/{name}",
        os.path.expanduser(f"~/bin/{name}"),
    ]


def exe_variants(name: str) -> list[str]:
    """Return Windows and Unix names for a program (xtb → xtb.exe, xtb)."""
    base = (name or "").strip()
    if not base:
        return []
    names = [base]
    if base.lower().endswith(".exe"):
        names.append(base[:-4])
    else:
        names.append(base + ".exe")
    out: list[str] = []
    for item in names:
        if item and item not in out:
            out.append(item)
    return out


def open_path(path: str, *, reveal: bool = False) -> None:
    """Open a file or folder with the platform file manager / default app."""
    path = os.path.expanduser(os.path.expandvars((path or "").strip()))
    if not path:
        raise FileNotFoundError("Path is empty")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if os.name == "nt":
        if reveal and os.path.isfile(path):
            subprocess.run(["explorer", "/select,", os.path.normpath(path)], check=False)
            return
        os.startfile(path)  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        cmd = ["open", "-R", path] if reveal and os.path.isfile(path) else ["open", path]
        subprocess.Popen(cmd)
        return
    subprocess.Popen(["xdg-open", os.path.abspath(path)])


def launch_program(exe: str, target: str | None = None) -> subprocess.Popen:
    """Start a viewer/binary. Uses `open -a` for macOS .app bundles."""
    exe = os.path.expanduser(os.path.expandvars((exe or "").strip()))
    if not exe:
        raise FileNotFoundError("Program path is empty")
    if sys.platform == "darwin" and exe.endswith(".app"):
        cmd = ["open", "-n", "-a", exe]
        if target:
            cmd.extend(["--args", target])
        return subprocess.Popen(cmd)
    cmd = [exe, target] if target else [exe]
    cwd = os.path.dirname(exe) if os.path.isfile(exe) else None
    return subprocess.Popen(cmd, cwd=cwd or None)


def copy_image_to_clipboard(image_path: str) -> None:
    """Copy a PNG/image file to the clipboard on Windows, macOS, or Linux."""
    image_path = os.path.abspath(image_path)
    if not os.path.isfile(image_path):
        raise FileNotFoundError(image_path)
    if os.name == "nt":
        safe = image_path.replace("'", "''")
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "Add-Type -AssemblyName System.Drawing;"
            f"[System.Windows.Forms.Clipboard]::SetImage("
            f"[System.Drawing.Image]::FromFile('{safe}'));"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Sta", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr or r.stdout or "clipboard failed")
        return
    if sys.platform == "darwin":
        script = f'set the clipboard to (read (POSIX file "{image_path}") as «class PNGf»)'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            raise RuntimeError(r.stderr or r.stdout or "osascript clipboard failed")
        return
    for cmd in (
        ["wl-copy", "-t", "image/png"],
        ["xclip", "-selection", "clipboard", "-t", "image/png"],
    ):
        if shutil.which(cmd[0]):
            with open(image_path, "rb") as fh:
                r = subprocess.run(cmd, stdin=fh, capture_output=True, timeout=20)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or b"").decode("utf-8", "replace") or "clipboard failed")
            return
    raise RuntimeError("No clipboard tool found. Install xclip or wl-clipboard, or export a PNG.")
