"""Tkinter terminal pane with a local PTY or an SSH shell.

The widget is a dark text box. Bytes from a local pty or from paramiko's
invoke_shell() are drawn here, and keystrokes go back to the same session.
"""

from __future__ import annotations

import os
import queue
import select
import shutil
import signal
import struct
import subprocess
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


def default_shell() -> str:
    shell = os.environ.get("SHELL") or ""
    if shell and os.path.isfile(shell) and os.access(shell, os.X_OK):
        return shell
    for candidate in ("/bin/zsh", "/bin/bash", "/bin/sh"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "/bin/sh"


def paramiko_available() -> bool:
    try:
        import paramiko  # noqa: F401
        return True
    except Exception:
        return False


def set_winsize(fd: int, rows: int, cols: int) -> None:
    import fcntl
    import termios

    packed = struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


class VTScreen:
    """Tiny in-memory terminal screen. Enough for a shell prompt and commands."""

    def __init__(self, max_lines: int = 4000):
        self.max_lines = max_lines
        self.lines = [""]
        self.row = 0
        self.col = 0

    def text(self) -> str:
        return "\n".join(self.lines)

    def _ensure_row(self) -> None:
        while self.row >= len(self.lines):
            self.lines.append("")
        extra = len(self.lines) - self.max_lines
        if extra > 0:
            del self.lines[:extra]
            self.row = max(0, self.row - extra)

    def _pad(self, line: str) -> str:
        if self.col > len(line):
            return line + (" " * (self.col - len(line)))
        return line

    def write(self, text: str) -> None:
        for ch in text:
            self._ensure_row()
            line = self._pad(self.lines[self.row])
            if self.col < len(line):
                line = line[: self.col] + ch + line[self.col + 1 :]
            else:
                line += ch
            self.lines[self.row] = line
            self.col += 1

    def cr(self) -> None:
        self.col = 0

    def lf(self) -> None:
        self.row += 1
        self._ensure_row()

    def bs(self) -> None:
        if self.col > 0:
            self.col -= 1

    def tab(self) -> None:
        width = 8 - (self.col % 8)
        self.write(" " * (width or 8))

    def cursor_left(self, n: int) -> None:
        self.col = max(0, self.col - n)

    def cursor_right(self, n: int) -> None:
        self.col += n

    def cursor_up(self, n: int) -> None:
        self.row = max(0, self.row - n)

    def cursor_down(self, n: int) -> None:
        self.row += n
        self._ensure_row()

    def cursor_absolute(self, row: int | None = None, col: int | None = None) -> None:
        if row is not None:
            self.row = max(0, row)
            self._ensure_row()
        if col is not None:
            self.col = max(0, col)

    def erase_eol(self, mode: int = 0) -> None:
        self._ensure_row()
        line = self._pad(self.lines[self.row])
        if mode <= 0:
            self.lines[self.row] = line[: self.col].rstrip(" ")
        elif mode == 1:
            self.lines[self.row] = (" " * self.col) + line[self.col :]
        else:
            self.lines[self.row] = ""
            self.col = 0

    def clear(self) -> None:
        self.lines = [""]
        self.row = 0
        self.col = 0


class EscapeParser:
    """Byte parser for the escape sequences a normal shell actually sends."""

    def __init__(self, screen: VTScreen):
        self.screen = screen
        self.state = "ground"
        self.params: list[str] = []
        self.param = ""
        self.utf_buf = bytearray()

    def feed(self, data: bytes) -> None:
        for byte in data:
            self._byte(byte)

    def _byte(self, byte: int) -> None:
        if self.state == "ground":
            if byte == 0x1B:
                self._flush_utf()
                self.state = "esc"
            elif byte == 0x0D:
                self._flush_utf()
                self.screen.cr()
            elif byte == 0x0A:
                self._flush_utf()
                self.screen.lf()
            elif byte in (0x08, 0x7F):
                self._flush_utf()
                self.screen.bs()
            elif byte == 0x09:
                self._flush_utf()
                self.screen.tab()
            elif byte < 0x20:
                return
            else:
                self._utf(byte)
            return

        if self.state == "esc":
            if byte == ord("["):
                self.state = "csi"
                self.params = []
                self.param = ""
            elif byte == ord("]"):
                self.state = "osc"
            else:
                self.state = "ground"
            return

        if self.state == "osc":
            if byte == 0x07:
                self.state = "ground"
            elif byte == 0x1B:
                self.state = "osc_esc"
            return

        if self.state == "osc_esc":
            self.state = "ground"
            return

        if byte == ord("?") or (0x30 <= byte <= 0x39):
            self.param += chr(byte)
        elif byte in (ord(";"), ord(":")):
            self.params.append(self.param)
            self.param = ""
        elif 0x40 <= byte <= 0x7E:
            self.params.append(self.param)
            self._dispatch(chr(byte))
            self.state = "ground"
        elif byte < 0x20:
            self.state = "ground"

    def _utf(self, byte: int) -> None:
        self.utf_buf.append(byte)
        if byte < 0x80:
            self._flush_utf()
            return
        try:
            text = bytes(self.utf_buf).decode("utf-8")
        except UnicodeDecodeError:
            if len(self.utf_buf) >= 4:
                text = bytes(self.utf_buf).decode("utf-8", "replace")
            else:
                return
        self.utf_buf.clear()
        self.screen.write(text)

    def _flush_utf(self) -> None:
        if not self.utf_buf:
            return
        self.screen.write(bytes(self.utf_buf).decode("utf-8", "replace"))
        self.utf_buf.clear()

    def _numbers(self) -> list[int]:
        values = []
        for raw in self.params:
            digits = "".join(ch for ch in raw if ch.isdigit())
            if not digits:
                values.append(0)
            else:
                values.append(int(digits))
        return values

    def _n(self, index: int = 0, default: int = 1) -> int:
        values = self._numbers()
        if index >= len(values) or values[index] <= 0:
            return default
        return values[index]

    def _dispatch(self, command: str) -> None:
        screen = self.screen
        if command in ("h", "l", "m"):
            return
        if command == "A":
            screen.cursor_up(self._n())
        elif command == "B":
            screen.cursor_down(self._n())
        elif command == "C":
            screen.cursor_right(self._n())
        elif command == "D":
            screen.cursor_left(self._n())
        elif command == "G":
            screen.cursor_absolute(col=self._n() - 1)
        elif command in ("H", "f"):
            row = self._n(0, 1) - 1
            col = self._n(1, 1) - 1
            screen.cursor_absolute(row, col)
        elif command == "K":
            screen.erase_eol(self._n(0, 0) if self._numbers() else 0)
        elif command == "J":
            mode = self._n(0, 0) if self._numbers() else 0
            if mode >= 2:
                screen.clear()
            else:
                screen.erase_eol(0)
        elif command == "P":
            screen._ensure_row()
            line = screen.lines[screen.row]
            n = self._n()
            screen.lines[screen.row] = line[: screen.col] + line[screen.col + n :]


class TerminalSession:
    """Local shell attached to a pseudo-terminal."""

    def __init__(self, command: list[str], rows: int = 24, cols: int = 80):
        self.command = command
        self.rows = rows
        self.cols = cols
        self.queue: queue.Queue[bytes] = queue.Queue()
        self.master_fd: int | None = None
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.exited = False
        self.returncode: int | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        import pty

        master, slave = pty.openpty()
        set_winsize(master, self.rows, self.cols)
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLUMNS"] = str(self.cols)
        env["LINES"] = str(self.rows)
        env.setdefault("LANG", "en_US.UTF-8")
        try:
            self.proc = subprocess.Popen(
                self.command,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                env=env,
                close_fds=True,
            )
        finally:
            os.close(slave)
        self.master_fd = master
        self.thread = threading.Thread(target=self._reader, name="embedded-terminal", daemon=True)
        self.thread.start()

    def write(self, data: bytes) -> None:
        if self.master_fd is None:
            return
        try:
            os.write(self.master_fd, data)
        except OSError:
            pass

    def resize(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        if self.master_fd is None or self.proc is None or self.proc.poll() is not None:
            return
        try:
            set_winsize(self.master_fd, rows, cols)
            os.killpg(self.proc.pid, signal.SIGWINCH)
        except OSError:
            pass

    def close(self) -> None:
        self._stop.set()
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGHUP)
            except OSError:
                try:
                    proc.terminate()
                except OSError:
                    pass
        fd = self.master_fd
        self.master_fd = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=1.0)
        if proc is not None:
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass

    def _reader(self) -> None:
        fd = self.master_fd
        proc = self.proc
        while fd is not None and not self._stop.is_set():
            try:
                readable, _, _ = select.select([fd], [], [], 0.2)
            except (OSError, ValueError):
                break
            if not readable:
                if proc is not None and proc.poll() is not None:
                    break
                continue
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            self.queue.put(data)
        self.exited = True
        if proc is not None:
            self.returncode = proc.poll()


class SSHSession:
    """Remote shell over SSH, using paramiko when it is installed."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str = "",
        port: int = 22,
        key_filename: str = "",
        rows: int = 24,
        cols: int = 80,
        prompt_callback=None,
    ):
        self.host = host
        self.username = username
        self.password = password or ""
        self.port = int(port or 22)
        self.key_filename = (key_filename or "").strip()
        self.rows = rows
        self.cols = cols
        self.prompt_callback = prompt_callback
        self.queue: queue.Queue[bytes] = queue.Queue()
        self.client = None
        self.chan = None
        self.thread: threading.Thread | None = None
        self.exited = False
        self.returncode: int | None = None
        self._stop = threading.Event()

    def _emit(self, text: str) -> None:
        self.queue.put(text.encode("utf-8", "replace"))

    def _ask(self, prompt: str, echo: bool) -> str:
        cb = self.prompt_callback
        if cb is None:
            return ""
        try:
            return cb(prompt, echo) or ""
        except Exception:
            return ""

    def _interactive_handler(self, title, instructions, prompt_list):
        unused_password = self.password
        answers = []
        extra = []
        if title:
            extra.append(str(title))
        if instructions:
            extra.append(str(instructions))
        if extra:
            self._emit("\r\n" + "\r\n".join(extra) + "\r\n")
        for prompt, echo in prompt_list:
            text = str(prompt)
            lowered = text.lower()
            if unused_password and (not echo) and "password" in lowered:
                answers.append(unused_password)
                unused_password = ""
                continue
            self._emit(text if text.endswith("\n") else text)
            answers.append(self._ask(text, bool(echo)))
        return answers

    def _load_private_key(self, paramiko, path: str):
        passphrase = None
        last_error = None
        for cls in (
            getattr(paramiko, "Ed25519Key", None),
            paramiko.RSAKey,
            getattr(paramiko, "ECDSAKey", None),
            getattr(paramiko, "DSSKey", None),
        ):
            if cls is None:
                continue
            try:
                return cls.from_private_key_file(path, password=passphrase)
            except paramiko.PasswordRequiredException:
                passphrase = self._ask(f"Passphrase for {os.path.basename(path)}: ", False) or None
                try:
                    return cls.from_private_key_file(path, password=passphrase)
                except Exception as exc:
                    last_error = exc
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError(f"Could not read SSH key: {path}")

    def start(self) -> None:
        import paramiko

        if self._stop.is_set():
            return
        target = f"{self.username}@{self.host}:{self.port}"
        self._emit(f"Connecting to {target} ...\r\n")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kw = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": 25,
            "banner_timeout": 30,
            "auth_timeout": 45,
            "allow_agent": True,
            "look_for_keys": not bool(self.key_filename),
        }
        if self.password:
            connect_kw["password"] = self.password
        if self.key_filename:
            if not os.path.isfile(self.key_filename):
                raise FileNotFoundError(f"SSH key not found: {self.key_filename}")
            connect_kw["pkey"] = self._load_private_key(paramiko, self.key_filename)

        try:
            client.connect(**connect_kw)
        except paramiko.AuthenticationException:
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise
            transport.auth_interactive(self.username, self._interactive_handler)
            if not transport.is_authenticated():
                raise

        if self._stop.is_set():
            client.close()
            return

        self.client = client
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)
        chan = client.invoke_shell(term="xterm-256color", width=self.cols, height=self.rows)
        chan.settimeout(0.0)
        self.chan = chan
        self._emit(f"Connected to {target}.\r\n")
        self.thread = threading.Thread(target=self._reader, name="ssh-terminal", daemon=True)
        self.thread.start()

    def write(self, data: bytes) -> None:
        chan = self.chan
        if chan is None:
            return
        try:
            chan.send(data)
        except Exception:
            pass

    def resize(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        chan = self.chan
        if chan is None:
            return
        try:
            chan.resize_pty(width=cols, height=rows)
        except Exception:
            pass

    def close(self) -> None:
        self._stop.set()
        chan = self.chan
        self.chan = None
        if chan is not None:
            try:
                chan.close()
            except Exception:
                pass
        client = self.client
        self.client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=1.0)
        self.exited = True

    def _reader(self) -> None:
        chan = self.chan
        while chan is not None and not self._stop.is_set():
            try:
                if chan.recv_ready():
                    data = chan.recv(4096)
                    if not data:
                        break
                    self.queue.put(data)
                    continue
                if chan.recv_stderr_ready():
                    data = chan.recv_stderr(4096)
                    if data:
                        self.queue.put(data)
                    continue
                if chan.exit_status_ready() and not chan.recv_ready():
                    try:
                        self.returncode = chan.recv_exit_status()
                    except Exception:
                        pass
                    break
                time.sleep(0.04)
            except Exception:
                break
        self.exited = True


class EmbeddedTerminal(ttk.Frame):
    """Tkinter pane that becomes a terminal when open() or open_ssh() is called."""

    def __init__(self, master, height: int = 18, placeholder: str = ""):
        super().__init__(master)
        self.session = None
        self.screen = VTScreen()
        self.parser = EscapeParser(self.screen)
        self._exit_noted = False
        self._resize_job = None
        self.placeholder = placeholder or (
            "Click Connect to open a shell in this pane.\n"
        )
        family = "Menlo" if os.path.exists("/System/Library/Fonts/Menlo.ttc") else "Consolas"
        self.font = tkfont.Font(family=family, size=12)

        self.text = tk.Text(
            self,
            height=height,
            width=80,
            font=self.font,
            bg="#1e1e1e",
            fg="#d9ffd9",
            insertbackground="#d9ffd9",
            insertwidth=8,
            blockcursor=True,
            wrap=tk.NONE,
            undo=False,
            padx=8,
            pady=8,
            relief=tk.FLAT,
            highlightthickness=0,
        )
        yscroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.text.yview)
        xscroll = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.text.bind("<Key>", self._on_key)
        self.text.bind("<<Paste>>", self._on_paste)
        self.text.bind("<Configure>", self._on_configure)
        self.bind("<Destroy>", self._on_destroy)
        self._show_placeholder()
        self.after(30, self._poll)

    def is_live(self) -> bool:
        return self.session is not None and not getattr(self.session, "exited", True)

    def _show_placeholder(self) -> None:
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", self.placeholder)
        self.text.mark_set("insert", "1.0")

    def _reset_screen(self) -> None:
        self.screen = VTScreen()
        self.parser = EscapeParser(self.screen)
        self._exit_noted = False
        self.text.delete("1.0", tk.END)

    def open(self, command: list[str] | None = None) -> None:
        if self.is_live():
            self.text.focus_set()
            return
        self.close(show_placeholder=False)
        self._reset_screen()
        rows, cols = self._pixel_size()
        cmd = command or [default_shell(), "-i"]
        self.session = TerminalSession(cmd, rows=rows, cols=cols)
        try:
            self.session.start()
        except Exception as exc:
            self.session = None
            self.text.insert("1.0", f"Could not start the terminal:\n{exc}\n")
            raise
        self._render()
        self.text.focus_set()

    def open_ssh(
        self,
        host: str,
        username: str,
        password: str = "",
        port: int = 22,
        key_filename: str = "",
        prompt_callback=None,
        prefer_system_ssh: bool = False,
    ) -> str:
        """Start a remote shell. Returns a short status string."""
        if self.is_live():
            self.text.focus_set()
            return "Already connected."
        self.close(show_placeholder=False)
        self._reset_screen()

        # Prefer system ssh + PTY (MobaXterm-style): password / OTP is typed
        # in the terminal itself. Paramiko is only a fallback when ssh is missing
        # or the caller explicitly disables the system client.
        ssh_bin = shutil.which("ssh")
        use_system = prefer_system_ssh or not password
        if use_system and ssh_bin:
            # accept-new = auto-answer the first-time "Are you sure... yes/no?"
            # host-key prompt (no typing "yes"). Changed keys are repaired by
            # the UI layer (ssh-keygen -R + one reconnect).
            cmd = [
                ssh_bin,
                "-tt",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "UpdateHostKeys=yes",
                "-o",
                "ConnectTimeout=25",
                "-p",
                str(int(port or 22)),
                f"{username}@{host}",
            ]
            if key_filename:
                cmd[1:1] = ["-i", key_filename]
            self.open(cmd)
            self.text.focus_set()
            return (
                f"Opened ssh to {username}@{host}:{port}. "
                "Type your password / challenge in this terminal if asked."
            )

        if not paramiko_available():
            self.text.insert(
                "1.0",
                "No system ssh client found, and paramiko is not installed.\n"
                "Install OpenSSH, or run:  pip install paramiko\n",
            )
            raise RuntimeError("ssh was not found on PATH and paramiko is not installed.")

        rows, cols = self._pixel_size()
        session = SSHSession(
            host=host,
            username=username,
            password=password,
            port=port,
            key_filename=key_filename,
            rows=rows,
            cols=cols,
            prompt_callback=prompt_callback,
        )
        self.session = session

        def run():
            try:
                session.start()
            except Exception as exc:
                session.queue.put(f"\r\nSSH error: {exc}\r\n".encode("utf-8", "replace"))
                session.exited = True

        threading.Thread(target=run, daemon=True, name="ssh-connect").start()
        self.text.focus_set()
        return f"Connecting to {username}@{host}:{port} ..."

    def close(self, show_placeholder: bool = True) -> None:
        session = self.session
        self.session = None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        self.screen = VTScreen()
        self.parser = EscapeParser(self.screen)
        self._exit_noted = True
        if show_placeholder:
            self._show_placeholder()

    def clear(self) -> None:
        """Wipe the terminal display. Keeps an active SSH/shell session open."""
        if self.is_live():
            self.screen.clear()
            self.parser = EscapeParser(self.screen)
            self._render()
        else:
            self._reset_screen()
            self._show_placeholder()
        self.text.focus_set()

    def paste(self) -> None:
        if self.session is None:
            return
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            return
        self.session.write(clip.replace("\r\n", "\n").replace("\n", "\r").encode("utf-8", "replace"))
        self.text.focus_set()

    def _on_paste(self, _event=None):
        self.paste()
        return "break"

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        self.close(show_placeholder=False)

    def _on_key(self, event):
        if self.session is None:
            return "break"
        key = event.keysym
        if key == "Prior":
            self.text.yview_scroll(-1, "pages")
            return "break"
        if key == "Next":
            self.text.yview_scroll(1, "pages")
            return "break"

        ctrl = bool(event.state & 0x4)
        command = bool(event.state & 0x8) or bool(event.state & 0x10)
        if command and key.lower() == "v":
            self.paste()
            return "break"
        if command and key.lower() == "c":
            try:
                selected = self.text.get("sel.first", "sel.last")
            except tk.TclError:
                return "break"
            self.clipboard_clear()
            self.clipboard_append(selected)
            return "break"
        if ctrl and len(key) == 1:
            self.session.write(bytes([ord(key.lower()) & 0x1F]))
            return "break"

        payload = {
            "Return": b"\r",
            "KP_Enter": b"\r",
            "BackSpace": b"\x7f",
            "Tab": b"\t",
            "Escape": b"\x1b",
            "Up": b"\x1b[A",
            "Down": b"\x1b[B",
            "Right": b"\x1b[C",
            "Left": b"\x1b[D",
            "Home": b"\x1b[H",
            "End": b"\x1b[F",
            "Delete": b"\x1b[3~",
        }.get(key)
        if payload is not None:
            self.session.write(payload)
            return "break"
        if event.char and event.char.isprintable():
            self.session.write(event.char.encode("utf-8"))
        return "break"

    def _pixel_size(self) -> tuple[int, int]:
        self.update_idletasks()
        width = max(self.text.winfo_width(), 80)
        height = max(self.text.winfo_height(), 40)
        char_w = max(self.font.measure("M"), 1)
        char_h = max(self.font.metrics("linespace"), 1)
        cols = max(40, min(240, width // char_w))
        rows = max(8, min(80, height // char_h))
        return rows, cols

    def _on_configure(self, _event=None):
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(150, self._apply_resize)

    def _apply_resize(self) -> None:
        self._resize_job = None
        if self.session is None:
            return
        rows, cols = self._pixel_size()
        self.session.resize(rows, cols)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        session = self.session
        if session is not None:
            chunks = []
            try:
                while True:
                    chunks.append(session.queue.get_nowait())
            except queue.Empty:
                pass
            if chunks:
                self.parser.feed(b"".join(chunks))
                self._render()
            if session.exited and session.queue.empty() and not self._exit_noted:
                self._exit_noted = True
                code = session.returncode
                try:
                    self.event_generate("<<TerminalExited>>", when="tail")
                except tk.TclError:
                    pass
                self.text.insert(tk.END, f"\n[session ended{'' if code is None else f' ({code})'}]\n")
                self.text.see(tk.END)
        self.after(30, self._poll)

    def _render(self) -> None:
        content = self.screen.text()
        row = self.screen.row + 1
        col = self.screen.col
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", content)
        line_start = f"{row}.0"
        try:
            line = self.text.get(line_start, f"{row}.end")
        except tk.TclError:
            line = ""
        col = min(col, len(line))
        self.text.mark_set("insert", f"{row}.{col}")
        self.text.see("insert")
