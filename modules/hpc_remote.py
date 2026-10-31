"""Remote file window for an already open HPC SSH session.

File and job actions go through the terminal's ControlMaster socket, so the
password typed in the terminal is not asked for again.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import queue
import shlex
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from modules.embedded_terminal import ssh_mux_options, ssh_slave_command


SCRIPT_SUFFIXES = (".sh", ".bash", ".sbatch", ".slurm", ".pbs", ".job")
OUTPUT_SUFFIXES = (".out", ".log")
TEXT_SUFFIXES = SCRIPT_SUFFIXES + (
    ".txt", ".text", ".inp", ".out", ".log", ".py", ".md", ".json", ".yml", ".yaml",
    ".cfg", ".conf", ".ini", ".dat", ".xyz", ".gjf", ".com", ".csv", ".xml",
    ".html", ".c", ".h", ".f", ".f90", ".inc", ".toml",
)
TEXT_NAMES = {
    ".bashrc", ".bash_profile", ".profile", ".bash_aliases", ".zshrc", ".bashcrc",
    "bashrc", "bash_profile",
}

# Live logins, keyed by user@host:port. File and plot tools read this.
# They never open a second SSH connection of their own.
_OPEN_REMOTES: dict[str, dict] = {}


def register_open_remote(
    session_id: str,
    label: str,
    host: str,
    username: str,
    port: int,
    control_path: str,
    queue_system: str,
) -> None:
    _OPEN_REMOTES[session_id] = {
        "id": session_id,
        "label": label or session_id,
        "host": host,
        "username": username,
        "port": int(port or 22),
        "control_path": control_path,
        "queue_system": queue_system or "SLURM",
    }


def close_open_remote(session_id: str) -> None:
    _OPEN_REMOTES.pop(session_id, None)


def list_open_remotes() -> list[dict]:
    """Remotes whose login socket is still on this computer. No network call."""
    alive = []
    for session_id, info in list(_OPEN_REMOTES.items()):
        path = info.get("control_path") or ""
        if path and os.path.exists(path):
            alive.append(dict(info))
        else:
            _OPEN_REMOTES.pop(session_id, None)
    return alive


def remote_from_info(info: dict):
    return RemoteSSH(
        info["host"],
        info["username"],
        int(info.get("port") or 22),
        info["control_path"],
    )


class RemoteEntry:
    def __init__(self, name: str, kind: str, size: int):
        self.name = name
        self.kind = kind
        self.size = size


def remote_join(base: str, name: str) -> str:
    base = (base or "").rstrip("/")
    if not base:
        return "/" + name.lstrip("/")
    return base + "/" + name.lstrip("/")


def _sftp_quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _valid_name(name: str) -> bool:
    name = (name or "").strip()
    if not name or name in (".", ".."):
        return False
    return "/" not in name and "\\" not in name and "\x00" not in name and "\n" not in name


def is_remote_text_name(name: str) -> bool:
    base = posixpath.basename(name or "")
    lowered = base.lower()
    if lowered in TEXT_NAMES or base in TEXT_NAMES:
        return True
    return lowered.endswith(TEXT_SUFFIXES)


def human_size(size: int) -> str:
    value = float(max(0, int(size)))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{int(size)} B"


def parse_remote_listing(text: str) -> tuple[str, list[RemoteEntry]]:
    """Parse `pwd` plus either GNU find -printf output or `ls -p` names."""
    lines = text.replace("\r\n", "\n").replace("\r", "").split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    if not lines or not lines[0].strip():
        raise ValueError("The cluster returned an empty directory listing.")
    cwd = lines[0].strip()
    entries: list[RemoteEntry] = []
    for line in lines[1:]:
        if not line:
            continue
        if "\t" in line:
            kind_code, size_text, name = line.split("\t", 2)
            if not name or name in (".", ".."):
                continue
            kind = {"d": "dir", "l": "link"}.get(kind_code, "file")
            try:
                size = int(size_text)
            except ValueError:
                size = 0
        else:
            name = line
            kind = "file"
            if name.endswith("/"):
                kind = "dir"
                name = name[:-1]
            size = 0
            if not name or name in (".", ".."):
                continue
        entries.append(RemoteEntry(name, kind, size))
    rank = {"dir": 0, "link": 1, "file": 2}
    entries.sort(key=lambda entry: (rank.get(entry.kind, 2), entry.name.lower()))
    return cwd, entries


def _friendly_ssh_error(detail: str) -> str:
    text = (detail or "").strip()
    lowered = text.lower()
    if "control socket" in lowered or "mux" in lowered or "connection refused" in lowered:
        return (
            "The SSH session is not ready. Finish the password prompt, "
            "or click Connect SSH again.\n" + text
        )
    if len(text) > 1200:
        return text[:1200] + "\n..."
    return text or "The cluster command failed."


class RemoteSSH:
    """One-shot commands and file copies over an existing SSH login."""

    def __init__(self, host: str, username: str, port: int, control_path: str):
        self.host = host
        self.username = username
        self.port = int(port or 22)
        self.control_path = control_path

    def run(self, command: str, timeout: int = 45) -> str:
        if not self.control_path or not os.path.exists(self.control_path):
            raise RuntimeError(
                "The SSH session is not ready. Finish the password prompt, or click Connect SSH again."
            )
        try:
            proc = subprocess.run(
                ssh_slave_command(
                    self.control_path,
                    self.username,
                    self.host,
                    self.port,
                    remote_command=command,
                ),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("The cluster took too long to answer.") from exc
        except OSError as exc:
            raise RuntimeError(str(exc)) from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(_friendly_ssh_error(detail or f"Remote command failed ({proc.returncode})"))
        return (proc.stdout or "").replace("\r\n", "\n").replace("\r", "")

    def list_dir(self, path: str) -> tuple[str, list[RemoteEntry]]:
        quoted = shlex.quote(path)
        find_cmd = (
            f"cd {quoted} || exit 1\n"
            "printf '%s\\n' \"$PWD\"\n"
            "find . -mindepth 1 -maxdepth 1 -printf '%y\\t%s\\t%f\\n'\n"
        )
        try:
            text = self.run(find_cmd)
        except RuntimeError as exc:
            lowered = str(exc).lower()
            find_missing = (
                "printf" in lowered
                or "-printf" in lowered
                or "unknown predicate" in lowered
                or "invalid predicate" in lowered
            )
            if not find_missing:
                raise
            text = self.run(
                f"cd {quoted} || exit 1\n"
                "printf '%s\\n' \"$PWD\"\n"
                "ls -1 -A -p\n"
            )
        try:
            return parse_remote_listing(text)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

    def mkdir(self, path: str) -> None:
        self.run(f"mkdir -p -- {shlex.quote(path)}")

    def touch(self, path: str) -> None:
        self.run(f"touch -- {shlex.quote(path)}")

    def remove(self, path: str, kind: str) -> None:
        if kind == "dir":
            self.run(f"rm -r -- {shlex.quote(path)}", timeout=120)
        else:
            self.run(f"rm -- {shlex.quote(path)}")

    def submit(self, directory: str, script_name: str, queue_system: str) -> str:
        tool = "qsub" if str(queue_system).strip().upper() == "PBS" else "sbatch"
        text = self.run(
            f"cd {shlex.quote(directory)} || exit 1\n"
            f"{tool} {shlex.quote(script_name)}\n",
            timeout=60,
        )
        return text.strip() or f"{tool} accepted the script."

    def queue_snapshot(self, queue_system: str) -> str:
        running, recent, note = self.job_snapshot(queue_system)
        lines = [note, ""]
        if not running and not recent:
            lines.append("(no jobs in the queue)")
        for row in running + recent:
            lines.append(
                "  ".join(
                    part for part in (
                        row.get("section", ""),
                        row.get("id", ""),
                        row.get("name", ""),
                        row.get("state", ""),
                        row.get("path", ""),
                    ) if part
                )
            )
        return "\n".join(lines).strip()

    def read_text_file(self, path: str, max_bytes: int = 1_500_000) -> str:
        """Read one remote text file. Refuses huge or binary files."""
        script = (
            f"p={shlex.quote(path)}\n"
            "if [ ! -f \"$p\" ]; then printf '%s\\n' NOFILE; exit 0; fi\n"
            "bytes=$(wc -c < \"$p\" | tr -d ' ')\n"
            f"if [ \"$bytes\" -gt {int(max_bytes)} ]; then printf '%s\\n' \"TOOBIG $bytes\"; exit 0; fi\n"
            "printf '%s\\n' OK\n"
            "cat -- \"$p\"\n"
            "exit 0\n"
        )
        text = self.run(script, timeout=40)
        if text.startswith("NOFILE"):
            raise RuntimeError("That file is not on the cluster.")
        if text.startswith("TOOBIG"):
            raise RuntimeError("That file is too large to edit here.")
        if text.startswith("OK\n"):
            body = text[3:]
        elif text.startswith("OK\r\n"):
            body = text[4:]
        else:
            body = text
        if "\x00" in body:
            raise RuntimeError("That file is not plain text.")
        return body

    def write_text_file(self, path: str, content: str) -> None:
        """Replace one remote text file. One upload, no extra login."""
        import tempfile
        folder = tempfile.mkdtemp(prefix="ac-edit-")
        local = os.path.join(folder, "edit.txt")
        try:
            with open(local, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            self.upload_many([(local, path)])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _sftp_batch(self, script: str, timeout: int = 180) -> None:
        sftp = shutil.which("sftp")
        if not sftp:
            raise RuntimeError("sftp was not found on PATH.")
        if not self.control_path or not os.path.exists(self.control_path):
            raise RuntimeError(
                "The SSH session is not ready. Finish the password prompt, or click Connect SSH again."
            )
        try:
            proc = subprocess.run(
                [
                    sftp,
                    "-b", "-",
                    *ssh_mux_options(self.control_path),
                    "-P", str(self.port),
                    f"{self.username}@{self.host}",
                ],
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("The file transfer took too long.") from exc
        except OSError as exc:
            raise RuntimeError(str(exc)) from exc
        if proc.returncode != 0:
            detail = "\n".join(
                part.strip() for part in (proc.stderr or "", proc.stdout or "") if part and part.strip()
            )
            raise RuntimeError(_friendly_ssh_error(detail or "File transfer failed."))

    def upload_many(self, pairs: list[tuple[str, str]]) -> None:
        if not pairs:
            return
        lines = [
            f"put {_sftp_quote(local_path)} {_sftp_quote(remote_path)}"
            for local_path, remote_path in pairs
        ]
        self._sftp_batch("\n".join(lines) + "\n")

    def download(self, remote_path: str, local_path: str, recursive: bool = False) -> None:
        flag = "-R " if recursive else ""
        self._sftp_batch(
            f"get {flag}{_sftp_quote(remote_path)} {_sftp_quote(local_path)}\n"
        )

    def job_snapshot(self, queue_system: str) -> tuple[list[dict], list[dict], str]:
        """One login-shell command. ~/.bashrc is read so squeue or qstat is found."""
        hint = "PBS" if str(queue_system).strip().upper() == "PBS" else "SLURM"
        text = self.run(_job_probe_script(hint), timeout=50)
        running, recent = parse_job_snapshot(text)
        return running, recent, scheduler_note(text)

    def read_job_log(self, workdir: str, logfile: str, job_id: str) -> tuple[str, str]:
        """Tail one log in the job folder. Returns (path, text)."""
        workdir = (workdir or "").strip()
        logfile = (logfile or "").strip()
        job_id = (job_id or "").strip()
        if logfile.lower() in ("none", "n/a", "-"):
            logfile = ""
        if job_id:
            logfile = logfile.replace("%j", job_id).replace("%J", job_id)
        if logfile and workdir and not logfile.startswith("/"):
            logfile = remote_join(workdir, logfile)
        script = (
            f"wd={shlex.quote(workdir)}\n"
            f"hint={shlex.quote(logfile)}\n"
            f"jid={shlex.quote(job_id)}\n"
            "show() {\n"
            "  printf '%s\\n' \"LOGFILE $1\"\n"
            "  tail -n 200 -- \"$1\"\n"
            "}\n"
            "if [ -n \"$hint\" ] && [ -f \"$hint\" ]; then show \"$hint\"; exit 0; fi\n"
            "if [ -n \"$wd\" ] && [ -n \"$jid\" ] && [ -f \"$wd/slurm-$jid.out\" ]; then show \"$wd/slurm-$jid.out\"; exit 0; fi\n"
            "if [ -n \"$wd\" ] && [ -n \"$jid\" ] && [ -f \"$wd/$jid.out\" ]; then show \"$wd/$jid.out\"; exit 0; fi\n"
            "if [ -n \"$wd\" ]; then\n"
            "  found=$(find \"$wd\" -maxdepth 1 -type f \\( -name '*.out' -o -name '*.log' \\) 2>/dev/null | head -n 1)\n"
            "  if [ -n \"$found\" ]; then show \"$found\"; exit 0; fi\n"
            "fi\n"
            "printf '%s\\n' 'NOLOG'\n"
            "exit 0\n"
        )
        text = self.run(script, timeout=30)
        lines = text.splitlines()
        if not lines or lines[0].strip() == "NOLOG":
            return "", "(no log file in that job folder)"
        if lines[0].startswith("LOGFILE "):
            path = lines[0][len("LOGFILE "):].strip()
            body = "\n".join(lines[1:]).strip() or "(log file is empty)"
            return path, body
        return "", text.strip() or "(no log file in that job folder)"


def _job_probe_script(hint: str) -> str:
    """One interactive bash so ~/.bashrc can define squeue, qstat, or qsub.

    The interactive shell is required because many bashrc files skip their
    module lines unless the shell is interactive. This is still a single
    command on the login that is already open.
    """
    choice = "PBS" if hint == "PBS" else "SLURM"
    return f"""bash -i <<'END'
set +e
shopt -s expand_aliases
printf '%s\\n' '##SCHED##'
printf '%s\\n' 'HINT {choice}'
have_squeue=0
have_sacct=0
have_qstat=0
have_qsub=0
command -v squeue >/dev/null 2>&1 && have_squeue=1
command -v sacct >/dev/null 2>&1 && have_sacct=1
command -v qstat >/dev/null 2>&1 && have_qstat=1
command -v qsub >/dev/null 2>&1 && have_qsub=1
rc_slurm=0
rc_pbs=0
for f in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile" "$HOME/.bash_aliases" "$HOME/.bashcrc"; do
  [ -f "$f" ] || continue
  grep -E -i -q 'squeue|sbatch|sacct|slurm' "$f" && rc_slurm=1
  grep -E -i -q 'qstat|qsub|pbs' "$f" && rc_pbs=1
done
use=none
if [ "{choice}" = "PBS" ] && [ "$have_qstat" = 1 ]; then use=pbs
elif [ "{choice}" = "SLURM" ] && [ "$have_squeue" = 1 ]; then use=slurm
elif [ "$have_squeue" = 1 ]; then use=slurm
elif [ "$have_qstat" = 1 ]; then use=pbs
elif [ "$rc_slurm" = 1 ]; then use=slurm
elif [ "$rc_pbs" = 1 ]; then use=pbs
fi
printf '%s\\n' "USE $use"
printf '%s\\n' "SQUEUE $have_squeue"
printf '%s\\n' "SACCT $have_sacct"
printf '%s\\n' "QSTAT $have_qstat"
printf '%s\\n' "QSUB $have_qsub"
printf '%s\\n' "RC_SLURM $rc_slurm"
printf '%s\\n' "RC_PBS $rc_pbs"
printf '%s\\n' '##RUNNING##'
if [ "$use" = slurm ] && [ "$have_squeue" = 1 ]; then
  squeue -u "$USER" -h -o "%i|%j|%T|%M|%Z" 2>/dev/null || squeue -u "$USER" -h -o "%i|%j|%T|%M|%R" 2>/dev/null || true
elif [ "$use" = pbs ] && [ "$have_qstat" = 1 ]; then
  qstat -u "$USER" -f 2>/dev/null | awk '
    /^Job Id:/ {{
      if (id != "") print id "|" name "|" state "|" workdir "|" stdout
      id=$3; name=""; state=""; workdir=""; stdout=""
    }}
    $1 == "Job_Name" {{ name=$3 }}
    $1 == "job_state" {{ state=$3 }}
    $1 == "Output_Path" {{ stdout=$3 }}
    /PBS_O_WORKDIR=/ {{
      line=$0
      sub(/^.*PBS_O_WORKDIR=/, "", line)
      sub(/[, ].*$/, "", line)
      workdir=line
    }}
    END {{ if (id != "") print id "|" name "|" state "|" workdir "|" stdout }}
  ' || true
fi
printf '%s\\n' '##RECENT##'
if [ "$use" = slurm ] && [ "$have_sacct" = 1 ]; then
  sacct -u "$USER" -S now-14days -X -n -P --format=JobID,JobName,State,Elapsed,WorkDir,StdOut 2>/dev/null | head -n 150 || true
elif [ "$use" = pbs ] && [ "$have_qstat" = 1 ]; then
  qstat -x -u "$USER" 2>/dev/null | awk 'NR>2 && $1 !~ /^---/ {{ print $1 "|" $4 "|" $10 "|" $11 "||" }}' | head -n 150 || true
fi
printf '%s\\n' '##END##'
exit 0
END
exit 0
"""


def scheduler_note(text: str) -> str:
    """Short line describing which queue command the login shell provides."""
    flags: dict[str, str] = {}
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "").splitlines():
        line = raw.strip()
        if line == "##RUNNING##":
            break
        parts = line.split()
        if len(parts) == 2 and parts[0] in {"USE", "SQUEUE", "SACCT", "QSTAT", "QSUB", "RC_SLURM", "RC_PBS", "HINT"}:
            flags[parts[0]] = parts[1]
    use = flags.get("USE", "none")
    if use == "slurm" and flags.get("SQUEUE") == "1":
        return "Using squeue from your login shell (~/.bashrc)."
    if use == "pbs" and flags.get("QSTAT") == "1":
        return "Using qstat from your login shell (~/.bashrc)."
    if flags.get("RC_SLURM") == "1":
        return "Found Slurm in ~/.bashrc, but squeue is not available in this login."
    if flags.get("RC_PBS") == "1":
        return "Found PBS or qsub in ~/.bashrc, but qstat is not available in this login."
    return "No squeue or qstat in this login. Checked ~/.bashrc."


def _job_row(parts: list[str], recent: bool) -> dict | None:
    if len(parts) < 4:
        return None
    job_id = parts[0].strip()
    if not job_id or job_id.startswith("#") or job_id.lower() in ("jobid", "job id"):
        return None
    if "." in job_id and job_id.split(".", 1)[1][:1].isdigit() and not recent:
        # Keep the parent id from squeue. sacct -X already drops step ids.
        pass
    path = parts[4].strip() if len(parts) > 4 else ""
    logfile = parts[5].strip() if len(parts) > 5 else ""
    if path.lower() in ("(null)", "none", "n/a", "unknown"):
        path = ""
    if logfile.lower() in ("(null)", "none", "n/a", "unknown"):
        logfile = ""
    if path and not path.startswith("/") and not path.startswith("~"):
        path = ""
    return {
        "id": job_id,
        "name": parts[1].strip(),
        "state": parts[2].strip(),
        "time": parts[3].strip(),
        "path": path,
        "logfile": logfile,
        "section": "Earlier" if recent else "In queue",
    }


def parse_job_snapshot(text: str) -> tuple[list[dict], list[dict]]:
    running: list[dict] = []
    recent: list[dict] = []
    mode = ""
    seen: set[str] = set()
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "").splitlines():
        line = raw.strip()
        if line == "##RUNNING##":
            mode = "R"
            continue
        if line == "##RECENT##":
            mode = "H"
            continue
        if line == "##END##":
            break
        if not line or mode not in ("R", "H") or "|" not in line:
            continue
        row = _job_row(line.split("|"), recent=(mode == "H"))
        if row is None:
            continue
        if row["id"] in seen:
            if mode == "H":
                for older in running:
                    if older["id"] == row["id"] and not older["path"] and row["path"]:
                        older["path"] = row["path"]
                    if older["id"] == row["id"] and not older["logfile"] and row["logfile"]:
                        older["logfile"] = row["logfile"]
            continue
        seen.add(row["id"])
        if mode == "R":
            running.append(row)
        else:
            recent.append(row)
    return running, recent


def remote_cache_root() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "AutoChemy_User_Data", "remote_outs")


def copy_remote_out(parent, info: dict, remote_path: str) -> str:
    """Download one chosen remote file into the local cache. No retries."""
    remote_path = (remote_path or "").strip()
    if not remote_path:
        return ""
    name = posixpath.basename(remote_path) or "job.out"
    token = hashlib.sha256(f"{info.get('id', '')}:{remote_path}".encode()).hexdigest()[:10]
    folder = os.path.join(remote_cache_root(), token)
    os.makedirs(folder, exist_ok=True)
    local_path = os.path.join(folder, name)
    box: queue.Queue = queue.Queue()

    def work():
        try:
            remote_from_info(info).download(remote_path, local_path)
            box.put(("",))
        except Exception as exc:
            box.put((str(exc),))

    threading.Thread(target=work, daemon=True, name="hpc-copy-out").start()
    top = tk.Toplevel(parent)
    top.title("Copying remote file")
    top.resizable(False, False)
    ttk.Label(top, text=f"Copying one file from the cluster:\n{remote_path}", wraplength=420).pack(padx=16, pady=16)
    result = {"err": None}

    def poll():
        try:
            (err,) = box.get_nowait()
        except queue.Empty:
            try:
                top.after(100, poll)
            except tk.TclError:
                pass
            return
        result["err"] = err
        try:
            top.destroy()
        except tk.TclError:
            pass

    top.transient(parent)
    top.grab_set()
    top.after(100, poll)
    parent.wait_window(top)
    if result["err"]:
        messagebox.showerror("Remote file", result["err"], parent=parent)
        return ""
    if not os.path.isfile(local_path):
        messagebox.showerror("Remote file", "The file did not arrive.", parent=parent)
        return ""
    return local_path


class RemoteOutDialog(tk.Toplevel):
    """Pick one .out or .log on a remote that is already logged in."""

    def __init__(self, master, info: dict):
        super().__init__(master)
        label = info.get("label") or info.get("id") or "remote"
        self.title(f"Remote files — {label}")
        self.geometry("720x480")
        self.minsize(560, 360)
        self.info = info
        self.remote = remote_from_info(info)
        self.chosen = ""
        self._cwd = ""
        self._busy = False
        self._entries: dict[str, RemoteEntry] = {}
        self.path_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Opening the home directory...")

        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=8)
        ttk.Button(bar, text="Up", command=self._up).pack(side=tk.LEFT)
        entry = ttk.Entry(bar, textvariable=self.path_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        entry.bind("<Return>", lambda _event: self._load(self.path_var.get().strip()))
        ttk.Button(bar, text="Open", command=lambda: self._load(self.path_var.get().strip())).pack(side=tk.LEFT)

        self.tree = ttk.Treeview(self, columns=("type",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Name", anchor=tk.W)
        self.tree.heading("type", text="Type", anchor=tk.W)
        self.tree.column("#0", width=480, stretch=True)
        self.tree.column("type", width=100, stretch=False)
        self.tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)
        self.tree.bind("<Double-1>", lambda _event: self._activate())
        self.tree.bind("<Return>", lambda _event: self._activate())

        bottom = ttk.Frame(self)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=8)
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Cancel", command=self._cancel).pack(side=tk.RIGHT)
        ttk.Button(bottom, text="Use this .out", command=self._use).pack(side=tk.RIGHT, padx=(0, 6))
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.transient(master)
        self.after(50, self._load_home)

    def show(self) -> str:
        self.grab_set()
        self.wait_window()
        return self.chosen

    def _cancel(self) -> None:
        self.chosen = ""
        self.destroy()

    def _selected(self) -> RemoteEntry | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return self._entries.get(selection[0])

    def _load_home(self) -> None:
        def work():
            try:
                home = self.remote.run('printf %s "$HOME"').strip() or "/"
                cwd, entries = self.remote.list_dir(home)
            except Exception as exc:
                self.after(0, lambda message=str(exc): self._fail(message))
                return
            self.after(0, lambda c=cwd, rows=entries: self._show(c, rows))

        self._busy = True
        threading.Thread(target=work, daemon=True, name="hpc-remote-out").start()

    def _load(self, path: str) -> None:
        if self._busy:
            return
        target = (path or "").strip() or self._cwd or "/"

        def work():
            try:
                cwd, entries = self.remote.list_dir(target)
            except Exception as exc:
                self.after(0, lambda message=str(exc): self._fail(message))
                return
            self.after(0, lambda c=cwd, rows=entries: self._show(c, rows))

        self._busy = True
        self.status_var.set(f"Opening {target} ...")
        threading.Thread(target=work, daemon=True, name="hpc-remote-out").start()

    def _fail(self, message: str) -> None:
        self._busy = False
        self.status_var.set(message)

    def _show(self, cwd: str, entries: list[RemoteEntry]) -> None:
        self._busy = False
        self._cwd = cwd
        self.path_var.set(cwd)
        self._entries.clear()
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        shown = 0
        for entry in entries:
            if entry.kind == "file" and not entry.name.lower().endswith(OUTPUT_SUFFIXES):
                continue
            iid = f"o{shown}"
            shown += 1
            self._entries[iid] = entry
            kind = "Folder" if entry.kind == "dir" else "Output"
            self.tree.insert("", tk.END, iid=iid, text=entry.name, values=(kind,))
        self.status_var.set(f"{cwd}  —  {shown} folders and .out/.log files")

    def _up(self) -> None:
        if not self._cwd or self._cwd == "/":
            return
        parent = posixpath.dirname(self._cwd.rstrip("/")) or "/"
        self._load(parent)

    def _activate(self) -> None:
        entry = self._selected()
        if entry is None or self._busy:
            return
        path = remote_join(self._cwd, entry.name)
        if entry.kind in ("dir", "link"):
            self._load(path)
            return
        self._choose(path)

    def _use(self) -> None:
        entry = self._selected()
        if entry is None or entry.kind == "dir":
            self.status_var.set("Select a .out or .log file.")
            return
        if not entry.name.lower().endswith(OUTPUT_SUFFIXES):
            self.status_var.set("Select a .out or .log file.")
            return
        self._choose(remote_join(self._cwd, entry.name))

    def _choose(self, path: str) -> None:
        self.chosen = path
        self.destroy()


class RemoteTextEditor(tk.Toplevel):
    """Edit one text file on the cluster that is already logged in."""

    def __init__(self, master, remote: RemoteSSH, path: str):
        super().__init__(master)
        self.remote = remote
        self.path = path
        self.title(path)
        self.geometry("860x560")
        self.minsize(560, 360)
        self._busy = False
        self.status_var = tk.StringVar(value="Reading the file...")

        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=8)
        self.save_btn = ttk.Button(bar, text="Save", command=self._save, state=tk.DISABLED)
        self.save_btn.pack(side=tk.LEFT)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(bar, textvariable=self.status_var).pack(side=tk.LEFT, padx=(12, 0))

        self.text = tk.Text(self, wrap=tk.NONE, undo=True, font=("Menlo", 12))
        yscroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.text.yview)
        xscroll = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        xscroll.pack(side=tk.BOTTOM, fill=tk.X)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=(8, 0), pady=(0, 8))
        self.transient(master)
        self.after(30, self._load)

    def _load(self) -> None:
        def work():
            try:
                body = self.remote.read_text_file(self.path)
            except Exception as exc:
                self.after(0, lambda message=str(exc): self._fail(message))
                return
            self.after(0, lambda content=body: self._show(content))

        threading.Thread(target=work, daemon=True, name="hpc-edit-read").start()

    def _fail(self, message: str) -> None:
        self.status_var.set(message)
        self.save_btn.configure(state=tk.DISABLED)

    def _show(self, content: str) -> None:
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", content)
        self.text.edit_reset()
        self.save_btn.configure(state=tk.NORMAL)
        self.status_var.set(self.path)

    def _save(self) -> None:
        if self._busy:
            return
        content = self.text.get("1.0", tk.END)
        if content.endswith("\n"):
            content = content[:-1]
        self._busy = True
        self.save_btn.configure(state=tk.DISABLED)
        self.status_var.set("Saving...")

        def work():
            try:
                self.remote.write_text_file(self.path, content)
            except Exception as exc:
                self.after(0, lambda message=str(exc): self._save_done(message, False))
                return
            self.after(0, lambda: self._save_done("Saved.", True))

        threading.Thread(target=work, daemon=True, name="hpc-edit-write").start()

    def _save_done(self, message: str, ok: bool) -> None:
        self._busy = False
        self.save_btn.configure(state=tk.NORMAL)
        self.status_var.set(message if ok else message)


class HpcFilesWindow(tk.Toplevel):
    """Explorer-style view of the cluster home directory."""

    def __init__(self, master, host: str, username: str, port: int, control_path: str, queue_system, on_run_job, on_paste_files=None, label: str = ""):
        super().__init__(master)
        title_name = label or f"{username}@{host}"
        self.title(f"Cluster files — {title_name}")
        self.geometry("920x600")
        self.minsize(720, 440)
        self.remote = RemoteSSH(host, username, port, control_path)
        self.queue_system = queue_system
        self.on_run_job = on_run_job
        self.on_paste_files = on_paste_files
        self._cwd = ""
        self._busy = False
        self._entries: dict[str, RemoteEntry] = {}
        self._buttons: list[tk.Widget] = []
        self.path_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Reading the home directory...")

        bar1 = ttk.Frame(self)
        bar1.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 2))
        up = ttk.Button(bar1, text="Up", command=self._up)
        up.pack(side=tk.LEFT)
        self._buttons.append(up)
        entry = ttk.Entry(bar1, textvariable=self.path_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        entry.bind("<Return>", lambda _event: self._open_typed_path())
        open_btn = ttk.Button(bar1, text="Open", command=self._open_typed_path)
        open_btn.pack(side=tk.LEFT)
        refresh = ttk.Button(bar1, text="Refresh", command=self._refresh)
        refresh.pack(side=tk.LEFT, padx=(6, 0))
        self._buttons.extend((open_btn, refresh))

        bar2 = ttk.Frame(self)
        bar2.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 6))
        for label, command in (
            ("New folder", self._new_folder),
            ("New file", self._new_file),
            ("Edit", self._edit_selected),
            ("Upload", self._upload),
            ("Paste input & script", self._paste_current),
            ("Download", self._download),
            ("Delete", self._delete),
            ("Submit job", self._submit_selected),
            ("Queue", self._refresh_queue),
        ):
            button = ttk.Button(bar2, text=label, command=command)
            button.pack(side=tk.LEFT, padx=(0, 4))
            self._buttons.append(button)
        run = tk.Button(
            bar2,
            text="Run current job",
            command=self._run_current,
            bg="#0f766e",
            fg="#ffffff",
            activebackground="#115e59",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=2,
            cursor="hand2",
        )
        run.pack(side=tk.LEFT, padx=(8, 0))
        self._buttons.append(run)

        queue_frame = ttk.Frame(self)
        queue_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(queue_frame, text="Queue").pack(anchor="w")
        self.queue_text = tk.Text(
            queue_frame,
            height=6,
            font=("Menlo", 11) if os.path.exists("/System/Library/Fonts/Menlo.ttc") else ("Consolas", 10),
            wrap=tk.NONE,
            bg="#1e1e1e",
            fg="#d9ffd9",
            relief=tk.FLAT,
        )
        self.queue_text.pack(fill=tk.X)
        self.queue_text.insert("1.0", "Queue output appears here after Submit job, Run current job, or Queue.\n")
        self.queue_text.configure(state=tk.DISABLED)

        status = ttk.Label(self, textvariable=self.status_var, wraplength=880, justify=tk.LEFT)
        status.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(0, 4))

        tree_frame = ttk.Frame(self)
        tree_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("type", "size"),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="Name", anchor=tk.W)
        self.tree.heading("type", text="Type", anchor=tk.W)
        self.tree.heading("size", text="Size", anchor=tk.E)
        self.tree.column("#0", width=460, stretch=True)
        self.tree.column("type", width=90, stretch=False, anchor=tk.W)
        self.tree.column("size", width=90, stretch=False, anchor=tk.E)
        scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", lambda _event: self._activate_selected())
        self.tree.bind("<Return>", lambda _event: self._activate_selected())

        self.after(50, self._load_home)

    def _selected(self) -> RemoteEntry | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return self._entries.get(selection[0])

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for button in self._buttons:
            try:
                button.configure(state=state)
            except tk.TclError:
                pass
        if status:
            self.status_var.set(status)

    def _set_queue_text(self, text: str) -> None:
        try:
            self.queue_text.configure(state=tk.NORMAL)
            self.queue_text.delete("1.0", tk.END)
            self.queue_text.insert("1.0", (text or "").strip() + "\n")
            self.queue_text.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _report_error(self, message: str) -> None:
        self._set_busy(False, message)

    def _show_dir(self, cwd: str, entries: list[RemoteEntry]) -> None:
        self._cwd = cwd
        self.path_var.set(cwd)
        self._entries.clear()
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        labels = {"dir": "Folder", "link": "Link", "file": "File"}
        for index, entry in enumerate(entries):
            iid = f"i{index}"
            self._entries[iid] = entry
            size = "" if entry.kind == "dir" else human_size(entry.size)
            self.tree.insert(
                "",
                tk.END,
                iid=iid,
                text=entry.name,
                values=(labels.get(entry.kind, "File"), size),
            )
        self._set_busy(False, f"{len(entries)} items in {cwd}")

    def _start_work(self, status: str, work) -> None:
        if self._busy:
            return
        self._set_busy(True, status)

        def runner():
            try:
                work()
            except Exception as exc:
                message = str(exc)
                try:
                    self.after(0, lambda m=message: self._report_error(m))
                except tk.TclError:
                    pass

        threading.Thread(target=runner, daemon=True, name="hpc-files").start()

    def _reload(self, folder: str) -> None:
        self._busy = False
        self._load(folder)

    def _load_home(self) -> None:
        def work():
            home = self.remote.run('printf %s "$HOME"').strip() or "/"
            cwd, entries = self.remote.list_dir(home)
            self.after(0, lambda c=cwd, rows=entries: self._show_dir(c, rows))

        self._start_work("Reading the home directory...", work)

    def _load(self, path: str) -> None:
        target = (path or "").strip() or "/"

        def work():
            cwd, entries = self.remote.list_dir(target)
            self.after(0, lambda c=cwd, rows=entries: self._show_dir(c, rows))

        self._start_work(f"Opening {target} ...", work)

    def _open_typed_path(self) -> None:
        self._load(self.path_var.get().strip() or self._cwd or "/")

    def _refresh(self) -> None:
        if self._cwd:
            self._load(self._cwd)
        else:
            self._load_home()

    def _up(self) -> None:
        if not self._cwd or self._cwd == "/":
            return
        parent = posixpath.dirname(self._cwd.rstrip("/")) or "/"
        self._load(parent)

    def _activate_selected(self) -> None:
        entry = self._selected()
        if entry is None or self._busy:
            return
        if entry.kind in ("dir", "link"):
            self._load(remote_join(self._cwd, entry.name))
            return
        if is_remote_text_name(entry.name):
            self._edit_entry(entry)

    def _edit_selected(self) -> None:
        entry = self._selected()
        if entry is None or self._busy:
            self.status_var.set("Select a text file to edit.")
            return
        if entry.kind == "dir":
            self.status_var.set("Select a text file, not a folder.")
            return
        self._edit_entry(entry)

    def _edit_entry(self, entry: RemoteEntry) -> None:
        if not self._cwd:
            return
        path = remote_join(self._cwd, entry.name)
        RemoteTextEditor(self, self.remote, path)

    def _ask_name(self, title: str) -> str:
        name = simpledialog.askstring(title, "Name:", parent=self)
        if name is None:
            return ""
        name = name.strip()
        if not _valid_name(name):
            messagebox.showwarning(title, "Use a name without slashes.", parent=self)
            return ""
        return name

    def _new_folder(self) -> None:
        if not self._cwd:
            return
        name = self._ask_name("New folder")
        if not name:
            return
        path = remote_join(self._cwd, name)

        def work():
            self.remote.mkdir(path)
            self.after(0, lambda folder=self._cwd: self._reload(folder))

        self._start_work(f"Creating {name} ...", work)

    def _new_file(self) -> None:
        if not self._cwd:
            return
        name = self._ask_name("New file")
        if not name:
            return
        path = remote_join(self._cwd, name)

        def work():
            self.remote.touch(path)
            self.after(0, lambda folder=self._cwd: self._reload(folder))

        self._start_work(f"Creating {name} ...", work)

    def _upload(self) -> None:
        if not self._cwd:
            return
        paths = filedialog.askopenfilenames(parent=self, title="Upload to the cluster")
        if not paths:
            return
        folder = self._cwd
        pairs = [
            (path, remote_join(folder, os.path.basename(path)))
            for path in paths
            if path and os.path.basename(path)
        ]
        if not pairs:
            return

        def work():
            self.remote.upload_many(pairs)
            self.after(0, lambda target=folder: self._reload(target))

        self._start_work(f"Uploading {len(pairs)} file(s)...", work)

    def _download(self) -> None:
        entry = self._selected()
        if entry is None:
            self.status_var.set("Select a file or folder to download.")
            return
        if not self._cwd:
            return
        destination = filedialog.askdirectory(parent=self, title="Save on this computer")
        if not destination:
            return
        remote_path = remote_join(self._cwd, entry.name)
        local_path = os.path.join(destination, entry.name)
        recursive = entry.kind == "dir"

        def work():
            self.remote.download(remote_path, local_path, recursive=recursive)
            self.after(0, lambda: self._set_busy(False, f"Downloaded {entry.name} to {destination}"))

        self._start_work(f"Downloading {entry.name} ...", work)

    def _delete(self) -> None:
        entry = self._selected()
        if entry is None:
            self.status_var.set("Select a file or folder to delete.")
            return
        if not self._cwd:
            return
        if entry.kind == "dir":
            question = f"Delete folder {entry.name} and everything inside it?"
        else:
            question = f"Delete {entry.name}?"
        if not messagebox.askyesno("Delete", question, parent=self):
            return
        path = remote_join(self._cwd, entry.name)
        kind = entry.kind
        folder = self._cwd

        def work():
            self.remote.remove(path, kind)
            self.after(0, lambda target=folder: self._reload(target))

        self._start_work(f"Deleting {entry.name} ...", work)

    def _submit_entry(self, entry: RemoteEntry) -> None:
        if not self._cwd:
            return
        if not entry.name.lower().endswith(SCRIPT_SUFFIXES):
            self.status_var.set("Select a .sh script to submit.")
            return
        tool = "qsub" if str(self.queue_system()).strip().upper() == "PBS" else "sbatch"
        if not messagebox.askyesno(
            "Submit job",
            f"Submit {entry.name} from\n{self._cwd}\nwith {tool}?",
            parent=self,
        ):
            return
        directory = self._cwd
        script_name = entry.name
        qsys = self.queue_system()

        def work():
            result = self.remote.submit(directory, script_name, qsys)
            try:
                snapshot = self.remote.queue_snapshot(qsys)
            except Exception as exc:
                snapshot = str(exc)
            message = f"{result}\n\n{snapshot}"

            def show(body=message, summary=result, target=directory):
                self._set_queue_text(body)
                self._busy = False
                self.status_var.set(summary)
                self._load(target)

            self.after(0, show)

        self._start_work(f"Submitting {entry.name} ...", work)

    def _submit_selected(self) -> None:
        entry = self._selected()
        if entry is None:
            self.status_var.set("Select a .sh script to submit.")
            return
        self._submit_entry(entry)

    def _refresh_queue(self, prefix: str = "") -> None:
        qsys = self.queue_system()

        def work():
            try:
                snapshot = self.remote.queue_snapshot(qsys)
            except Exception as exc:
                snapshot = str(exc)
            body = (prefix.strip() + "\n\n" + snapshot).strip() if prefix else snapshot
            try:
                self.after(0, lambda text=body: self._set_queue_text(text))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True, name="hpc-queue").start()

    def _paste_current(self) -> None:
        """Write the current input and job script into the open folder. One click, no submit."""
        if self._busy:
            return
        if self.on_paste_files is None:
            self.status_var.set("Paste is not available from this window.")
            return
        if not self._cwd:
            self.status_var.set("Wait for the folder list, then paste.")
            return
        folder = self._cwd
        entry = self._selected()
        if entry is not None and entry.kind == "dir":
            folder = remote_join(self._cwd, entry.name)
        self._paste_target = folder
        self._set_busy(True, f"Pasting input and script into {folder} ...")
        started = False
        try:
            started = bool(self.on_paste_files(folder, self._paste_done))
        finally:
            if not started:
                self._set_busy(False, "Nothing pasted.")

    def _paste_done(self, message: str) -> None:
        def apply(body=message):
            self._set_busy(False, body)
            target = getattr(self, "_paste_target", "") or self._cwd
            if target:
                self._load(target)

        try:
            self.after(0, apply)
        except tk.TclError:
            pass

    def _run_current(self) -> None:
        if self._busy:
            return
        if not self._cwd:
            self.status_var.set("Wait for the folder list, then run the job.")
            return
        self._set_busy(True, "Preparing the current job...")
        started = False
        try:
            started = bool(self.on_run_job(self._cwd, self._async_done))
        finally:
            if not started:
                self._set_busy(False, "Not submitted.")

    def _async_done(self, message: str) -> None:
        def apply(body=message):
            self._set_queue_text(body)
            self._set_busy(False, body)
            if self._cwd:
                self._load(self._cwd)
            self._refresh_queue(prefix=body)

        try:
            self.after(0, apply)
        except tk.TclError:
            pass
