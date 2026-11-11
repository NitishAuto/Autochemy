"""
Small shared UI kit for AutoChemy analysis modules (DIA, Orbital Creator).

Provides theme-aware "card" styling, a scrollable page container, stat tiles,
status pills and a log console, all driven by the palettes in app_theme.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Dict, Optional

from modules import app_theme

# Semantic colours that read well on both the light and the dark palette.
SEMANTIC = {
    "light": {
        "card": "#ffffff",
        "card_border": "#dbe2ec",
        "hero_bg": "#0b5cab",
        "hero_fg": "#ffffff",
        "hero_sub": "#cfe3fb",
        "ok": "#15803d",
        "warn": "#b45309",
        "bad": "#b91c1c",
        "info": "#0b5cab",
        "core": "#94a3b8",
        "doubly": "#2563eb",
        "singly": "#ea580c",
        "virtual": "#7c3aed",
        "plot_bg": "#ffffff",
        "plot_fg": "#1f2937",
        "plot_grid": "#e5e7eb",
        "log_bg": "#0f172a",
        "log_fg": "#e2e8f0",
    },
    "dark": {
        "card": "#1c222c",
        "card_border": "#2d3540",
        "hero_bg": "#1f3a5f",
        "hero_fg": "#f0f6ff",
        "hero_sub": "#a9c4e6",
        "ok": "#4ade80",
        "warn": "#fbbf24",
        "bad": "#f87171",
        "info": "#79c0ff",
        "core": "#64748b",
        "doubly": "#60a5fa",
        "singly": "#fb923c",
        "virtual": "#c084fc",
        "plot_bg": "#161b22",
        "plot_fg": "#e6edf3",
        "plot_grid": "#30363d",
        "log_bg": "#0d1117",
        "log_fg": "#c9d1d9",
    },
}


def current_mode(widget: tk.Misc) -> str:
    try:
        app = getattr(widget.winfo_toplevel(), "_orca_app", None)
        mode = getattr(app, "theme_mode", "light")
    except Exception:
        mode = "light"
    return mode if mode in SEMANTIC else "light"


def base_font(size_delta: int = 0, weight: str = "normal") -> tuple:
    try:
        fam = tkfont.nametofont("TkDefaultFont").actual("family")
        size = tkfont.nametofont("TkDefaultFont").actual("size")
    except Exception:
        fam, size = "Segoe UI", 10
    return (fam, max(7, abs(int(size)) + size_delta), weight)


def configure_kit_styles(style: ttk.Style, mode: str) -> Dict[str, str]:
    """(Re)configure the custom ttk styles used by the kit. Returns merged colours."""
    mode = mode if mode in SEMANTIC else "light"
    p = dict(app_theme.PALETTES[mode])
    s = SEMANTIC[mode]
    p.update(s)
    card = s["card"]

    style.configure("Kit.Page.TFrame", background=p["bg_main"])
    style.configure("Kit.Card.TFrame", background=card, bordercolor=s["card_border"],
                    relief="solid", borderwidth=1)
    style.configure("Kit.CardInner.TFrame", background=card)
    style.configure("Kit.Card.TLabel", background=card, foreground=p["fg"])
    style.configure("Kit.CardMuted.TLabel", background=card, foreground=p["fg_muted"],
                    font=base_font(-1))
    style.configure("Kit.CardTitle.TLabel", background=card, foreground=p["fg"],
                    font=base_font(2, "bold"))
    style.configure("Kit.Step.TLabel", background=p["accent"], foreground="#ffffff",
                    font=base_font(0, "bold"), padding=(8, 2))
    style.configure("Kit.Card.TRadiobutton", background=card, foreground=p["fg"])
    style.configure("Kit.Card.TCheckbutton", background=card, foreground=p["fg"])
    style.map("Kit.Card.TRadiobutton", background=[("active", card)])
    style.map("Kit.Card.TCheckbutton", background=[("active", card)])
    style.configure("Kit.Hero.TFrame", background=s["hero_bg"])
    style.configure("Kit.HeroTitle.TLabel", background=s["hero_bg"], foreground=s["hero_fg"],
                    font=base_font(7, "bold"))
    style.configure("Kit.HeroSub.TLabel", background=s["hero_bg"], foreground=s["hero_sub"],
                    font=base_font(0))
    style.configure("Kit.Accent.TButton", background=p["accent"], foreground="#ffffff",
                    font=base_font(0, "bold"), padding=(14, 7), borderwidth=0)
    style.map("Kit.Accent.TButton",
              background=[("disabled", p["border"]), ("active", s["hero_bg"]), ("pressed", s["hero_bg"])],
              foreground=[("disabled", p["fg_muted"])])
    style.configure("Kit.Ghost.TButton", background=card, foreground=p["accent"], padding=(10, 6),
                    bordercolor=s["card_border"])
    style.map("Kit.Ghost.TButton", background=[("active", p["button_bg"])])
    style.configure("Kit.Stat.TFrame", background=p["button_bg"])
    style.configure("Kit.StatValue.TLabel", background=p["button_bg"], foreground=p["fg"],
                    font=base_font(8, "bold"))
    style.configure("Kit.StatLabel.TLabel", background=p["button_bg"], foreground=p["fg_muted"],
                    font=base_font(-1))
    for key in ("ok", "warn", "bad", "info"):
        style.configure(f"Kit.{key}.TLabel", background=card, foreground=s[key],
                        font=base_font(0, "bold"))
    style.configure("Kit.Horizontal.TProgressbar", troughcolor=p["trough"], background=p["accent"],
                    bordercolor=p["trough"], lightcolor=p["accent"], darkcolor=p["accent"])
    return p


class ScrollablePage(ttk.Frame):
    """A vertically scrollable page. Put content in ``self.body``."""

    def __init__(self, parent, **kw):
        super().__init__(parent, style="Kit.Page.TFrame", **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.body = ttk.Frame(self.canvas, style="Kit.Page.TFrame", padding=(18, 14))
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, event):
        # Let text widgets and plots keep their own scrolling.
        w = event.widget
        if isinstance(w, (tk.Text, tk.Listbox)) or w.winfo_class() in ("Treeview", "Text"):
            return
        if self.body.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def recolor(self, bg: str):
        self.canvas.configure(background=bg)


def hero(parent, title: str, subtitle: str) -> ttk.Frame:
    frame = ttk.Frame(parent, style="Kit.Hero.TFrame", padding=(20, 14))
    frame.pack(fill=tk.X, pady=(0, 14))
    ttk.Label(frame, text=title, style="Kit.HeroTitle.TLabel").pack(anchor="w")
    ttk.Label(frame, text=subtitle, style="Kit.HeroSub.TLabel", wraplength=900,
              justify="left").pack(anchor="w", pady=(2, 0))
    return frame


def card(parent, title: str, step: Optional[str] = None, subtitle: str = "",
         expand: bool = False) -> ttk.Frame:
    """Create a card and return its inner content frame."""
    outer = ttk.Frame(parent, style="Kit.Card.TFrame", padding=1)
    outer.pack(fill=tk.BOTH if expand else tk.X, expand=expand, pady=(0, 12))
    inner = ttk.Frame(outer, style="Kit.CardInner.TFrame", padding=(16, 12))
    inner.pack(fill=tk.BOTH, expand=True)
    head = ttk.Frame(inner, style="Kit.CardInner.TFrame")
    head.pack(fill=tk.X, pady=(0, 8))
    if step:
        ttk.Label(head, text=step, style="Kit.Step.TLabel").pack(side=tk.LEFT, padx=(0, 10))
    ttk.Label(head, text=title, style="Kit.CardTitle.TLabel").pack(side=tk.LEFT)
    if subtitle:
        ttk.Label(inner, text=subtitle, style="Kit.CardMuted.TLabel", wraplength=880,
                  justify="left").pack(anchor="w", pady=(0, 8))
    body = ttk.Frame(inner, style="Kit.CardInner.TFrame")
    body.pack(fill=tk.BOTH, expand=True)
    return body


class StatTile(ttk.Frame):
    def __init__(self, parent, label: str, value: str = "—"):
        super().__init__(parent, style="Kit.Stat.TFrame", padding=(14, 8))
        self.value_var = tk.StringVar(value=value)
        self.label_var = tk.StringVar(value=label)
        ttk.Label(self, textvariable=self.value_var, style="Kit.StatValue.TLabel").pack(anchor="w")
        ttk.Label(self, textvariable=self.label_var, style="Kit.StatLabel.TLabel").pack(anchor="w")

    def set(self, value, label: Optional[str] = None):
        self.value_var.set(str(value))
        if label is not None:
            self.label_var.set(label)


class LogConsole(tk.Text):
    """Read-only coloured log with ok/warn/bad/info tags."""

    def __init__(self, parent, height=8):
        super().__init__(parent, height=height, wrap="word", borderwidth=0, padx=10, pady=8,
                         font=("Consolas", 9), state="disabled")

    def recolor(self, mode: str):
        s = SEMANTIC[mode if mode in SEMANTIC else "light"]
        dark = SEMANTIC["dark"]
        self.configure(background=s["log_bg"], foreground=s["log_fg"], insertbackground=s["log_fg"])
        # the console is always dark, so use the bright variants
        for key in ("ok", "warn", "bad", "info"):
            self.tag_configure(key, foreground=dark[key])

    def write(self, msg: str, tag: str = ""):
        self.configure(state="normal")
        self.insert(tk.END, msg.rstrip("\n") + "\n", tag or ())
        self.see(tk.END)
        self.configure(state="disabled")

    def clear(self):
        self.configure(state="normal")
        self.delete("1.0", tk.END)
        self.configure(state="disabled")


class UiQueue:
    """Thread-safe hand-off of callbacks from worker threads to the Tk thread.

    Workers call ``post(fn)``; the Tk thread drains the queue every ``interval`` ms.
    Calling Tk (including ``after``) from a worker thread is not safe.
    """

    def __init__(self, widget: tk.Misc, interval: int = 80):
        import queue
        self._q = queue.Queue()
        self._widget = widget
        self._interval = interval
        self._widget.after(self._interval, self._drain)

    def post(self, fn):
        self._q.put(fn)

    def _drain(self):
        try:
            while True:
                fn = self._q.get_nowait()
                try:
                    fn()
                except Exception as exc:  # keep the pump alive
                    print(f"UI callback failed: {exc}")
        except Exception:
            pass
        try:
            self._widget.after(self._interval, self._drain)
        except tk.TclError:
            pass  # widget destroyed


def parse_atom_list(text: str) -> list:
    """Parse '1-4, 7, 9-10' into [1,2,3,4,7,9,10] (1-based, sorted, unique)."""
    atoms = set()
    for part in str(text).replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = int(a), int(b)
            if a > b:
                a, b = b, a
            atoms.update(range(a, b + 1))
        else:
            atoms.add(int(part))
    return sorted(atoms)


def compress_atom_list(atoms) -> str:
    """Inverse of parse_atom_list: [1,2,3,7] -> '1-3, 7'."""
    atoms = sorted(set(int(a) for a in atoms))
    out, start, prev = [], None, None
    for a in atoms:
        if start is None:
            start = prev = a
        elif a == prev + 1:
            prev = a
        else:
            out.append(f"{start}-{prev}" if prev > start else f"{start}")
            start = prev = a
    if start is not None:
        out.append(f"{start}-{prev}" if prev > start else f"{start}")
    return ", ".join(out)
