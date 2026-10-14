"""
Shared appearance: high-contrast light/dark themes and editor styling for ORCA Suite.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any, Dict

DEFAULT_FONT_FAMILY = "Noto Sans"
DEFAULT_FONT_SIZE = 11
HEADING_FONT_SIZE = 12


PALETTES: Dict[str, Dict[str, str]] = {
    "dark": {
        "bg_root": "#12141a",
        "bg_sidebar": "#0e1015",
        "bg_sidebar_header": "#1c2230",
        "fg_sidebar_header": "#e8edf5",
        "bg_main": "#161b22",
        "fg": "#e8edf5",
        "fg_muted": "#9da7b8",
        "accent": "#58a6ff",
        "entry_bg": "#21262d",
        "entry_fg": "#f0f3f8",
        "entry_insert": "#58a6ff",
        "editor_bg": "#0d1117",
        "editor_fg": "#e6edf3",
        "editor_insert": "#79c0ff",
        "editor_select_bg": "#264f78",
        "editor_select_fg": "#ffffff",
        "border": "#30363d",
        "tree_bg": "#161b22",
        "tree_fg": "#e8edf5",
        "tree_sel_bg": "#1f6feb",
        "tree_sel_fg": "#ffffff",
        "tab_selected": "#21262d",
        "tab_unselected": "#161b22",
        "button_bg": "#21262d",
        "button_fg": "#e8edf5",
        "trough": "#21262d",
        "panel_embed": "#1a1f28",
    },
    "light": {
        "bg_root": "#eef1f6",
        "bg_sidebar": "#e2e7f0",
        "bg_sidebar_header": "#d8dee9",
        "fg_sidebar_header": "#1a1c23",
        "bg_main": "#f7f9fc",
        "fg": "#14161c",
        "fg_muted": "#4a5568",
        "accent": "#0b5cab",
        "entry_bg": "#ffffff",
        "entry_fg": "#0d1117",
        "entry_insert": "#0969da",
        "editor_bg": "#eef2f7",
        "editor_fg": "#070b12",
        "editor_insert": "#0969da",
        "editor_select_bg": "#b6d7ff",
        "editor_select_fg": "#0d1117",
        "border": "#c5cdd8",
        "tree_bg": "#ffffff",
        "tree_fg": "#0d1117",
        "tree_sel_bg": "#0b5cab",
        "tree_sel_fg": "#ffffff",
        "tab_selected": "#ffffff",
        "tab_unselected": "#e8ecf2",
        "button_bg": "#e8ecf2",
        "button_fg": "#14161c",
        "trough": "#dde3ec",
        "panel_embed": "#2b2b2b",
    },
}


def configure_default_fonts(root: tk.Misc) -> None:
    try:
        available_families = set(tkfont.families(root))
    except tk.TclError:
        return

    font_sizes = {
        "TkDefaultFont": DEFAULT_FONT_SIZE,
        "TkMenuFont": DEFAULT_FONT_SIZE,
        "TkTextFont": DEFAULT_FONT_SIZE,
        "TkHeadingFont": HEADING_FONT_SIZE,
        "TkCaptionFont": DEFAULT_FONT_SIZE,
        "TkSmallCaptionFont": 10,
        "TkIconFont": DEFAULT_FONT_SIZE,
        "TkTooltipFont": 10,
    }
    for font_name, size in font_sizes.items():
        try:
            named_font = tkfont.nametofont(font_name)
            options = {"size": size}
            if DEFAULT_FONT_FAMILY in available_families:
                options["family"] = DEFAULT_FONT_FAMILY
            if font_name == "TkHeadingFont":
                options["weight"] = "bold"
            named_font.configure(**options)
        except tk.TclError:
            pass


def build_context(mode: str) -> Dict[str, Any]:
    mode = "dark" if mode not in PALETTES else mode
    p = PALETTES[mode]
    return {
        "mode": mode,
        "palette": p,
        "editor_bg": p["editor_bg"],
        "editor_fg": p["editor_fg"],
        "editor_insert": p["editor_insert"],
        "editor_select_bg": p["editor_select_bg"],
        "editor_select_fg": p["editor_select_fg"],
        "accent": p["accent"],
        "fg_muted": p["fg_muted"],
        "panel_embed": p["panel_embed"],
    }


def apply_editor_style(widget: tk.Widget, ctx: Dict[str, Any]) -> None:
    if widget is None:
        return
    try:
        widget.configure(
            background=ctx["editor_bg"],
            foreground=ctx["editor_fg"],
            insertbackground=ctx["editor_insert"],
            selectbackground=ctx["editor_select_bg"],
            selectforeground=ctx["editor_select_fg"],
        )
    except tk.TclError:
        pass


def configure_ttk_style(style: ttk.Style, mode: str) -> Dict[str, str]:
    mode = "dark" if mode not in PALETTES else mode
    p = PALETTES[mode]
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    bg = p["bg_main"]
    fg = p["fg"]
    style.configure(".", background=bg, foreground=fg, fieldbackground=p["entry_bg"])
    style.configure("TFrame", background=bg)
    style.configure("TPanedwindow", background=bg)
    style.configure("TLabel", background=bg, foreground=fg)
    style.configure("TButton", background=p["button_bg"], foreground=p["button_fg"], padding=(10, 6))
    style.map(
        "TButton",
        background=[("active", p["border"]), ("pressed", p["border"])],
        foreground=[("disabled", p["fg_muted"])],
    )
    style.configure("TCheckbutton", background=bg, foreground=fg)
    style.configure("TRadiobutton", background=bg, foreground=fg)
    style.configure("TEntry", fieldbackground=p["entry_bg"], foreground=p["entry_fg"], insertcolor=p["entry_insert"], padding=4)
    style.configure("TCombobox", fieldbackground=p["entry_bg"], foreground=p["entry_fg"], padding=4)
    style.map("TCombobox",
        fieldbackground=[("readonly", p["entry_bg"])],
        foreground=[("readonly", p["entry_fg"]), ("!disabled", p["entry_fg"])],
        selectbackground=[("readonly", p["entry_bg"]), ("focus", p["tree_sel_bg"])],
        selectforeground=[("readonly", p["entry_fg"]), ("focus", p["tree_sel_fg"])]
    )
    style.configure("Horizontal.TScale", background=bg, troughcolor=p["trough"])
    style.configure("Vertical.TScale", background=bg, troughcolor=p["trough"])
    style.configure("Horizontal.TScrollbar", background=p["button_bg"], troughcolor=p["trough"], arrowcolor=fg)
    style.configure("Vertical.TScrollbar", background=p["button_bg"], troughcolor=p["trough"], arrowcolor=fg)

    style.configure("TNotebook", background=bg, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=p["tab_unselected"],
        foreground=fg,
        padding=[14, 8],
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", p["tab_selected"])],
        foreground=[("selected", fg)],
        expand=[("selected", [1, 1, 1, 0])],
    )

    style.configure("TLabelframe", background=bg, foreground=fg, bordercolor=p["border"])
    style.configure("TLabelframe.Label", background=bg, foreground=p["accent"])

    style.configure("Treeview", background=p["tree_bg"], foreground=p["tree_fg"], fieldbackground=p["tree_bg"], rowheight=26)
    style.configure("Treeview.Heading", background=p["tab_unselected"], foreground=fg)
    style.map(
        "Treeview",
        background=[("selected", p["tree_sel_bg"])],
        foreground=[("selected", p["tree_sel_fg"])],
    )

    style.configure("TMenubutton", background=p["button_bg"], foreground=p["button_fg"], padding=(8, 4))
    style.configure("Sidebar.TButton", padding=10, width=22, background=p["button_bg"], foreground=p["button_fg"])
    style.map("Sidebar.TButton", background=[("active", p["border"])])

    style.configure("SidebarHeader.TLabel", background=p["bg_sidebar_header"], foreground=p["fg_sidebar_header"])
    return p


def style_menubar_dark(menu: tk.Menu, p: Dict[str, str]) -> None:
    """Optional dark styling for tk.Menu (Windows)."""
    try:
        bg = p["bg_sidebar_header"]
        fg = p["fg_sidebar_header"]
        menu.configure(bg=bg, fg=fg, activebackground=p["tree_sel_bg"], activeforeground=p["tree_sel_fg"], tearoff=0)
    except tk.TclError:
        pass
