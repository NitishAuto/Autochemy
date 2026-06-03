"""
xTB hub module.
Hosts both the standalone xTB workflow and the conformational sampling area
under a single sidebar entry.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from modules.base_module import BaseModule
from modules.xtb_module import XTBModule as XTBWorkbenchModule
from modules.conformational_sampling import ConformationalSamplingModule


class XTBHubModule(BaseModule):
    """Single entry point for xTB and conformational sampling."""

    def __init__(self, parent_frame):
        super().__init__(parent_frame)
        self._active_view = "home"
        self._xtb_module = None
        self._conf_module = None
        self._pending_xtb_session = None
        self._pending_conf_session = None

    def get_name(self) -> str:
        return "xtb & Conformational Analysis"

    def get_icon(self) -> str:
        return "⚗️"

    def create_ui(self) -> None:
        self.main_frame = ttk.Frame(self.parent_frame, padding=10)

        header = ttk.Frame(self.main_frame)
        header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(header, text="xTB Workspace", font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        self.back_btn = ttk.Button(header, text="← Back", command=self._show_home, state=tk.DISABLED)
        self.back_btn.pack(side=tk.RIGHT)

        self.view_host = ttk.Frame(self.main_frame)
        self.view_host.pack(fill=tk.BOTH, expand=True)

        self.home_frame = ttk.Frame(self.view_host)
        self.xtb_frame = ttk.Frame(self.view_host)
        self.conf_frame = ttk.Frame(self.view_host)

        self._build_home()
        self._show_home()

    def _build_home(self) -> None:
        hero = ttk.LabelFrame(self.home_frame, text="Choose a workflow", padding=16)
        hero.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            hero,
            text="Open the xTB runner or the conformational sampling workspace from one place.",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        cards = ttk.Frame(hero)
        cards.pack(fill=tk.BOTH, expand=True)

        xtb_card = ttk.LabelFrame(cards, text="xTB", padding=16)
        xtb_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        ttk.Label(
            xtb_card,
            text="Run xTB calculations, scans, constraints, logs, and visualization tools.",
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 12))
        ttk.Button(xtb_card, text="Open xTB", command=self._show_xtb).pack(anchor="w")

        conf_card = ttk.LabelFrame(cards, text="Conformational Sampling", padding=16)
        conf_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        ttk.Label(
            conf_card,
            text="Open conformational sampling workflows such as CREST.",
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 12))
        ttk.Button(conf_card, text="Open Conformational Sampling", command=self._show_conf).pack(anchor="w")

    def _clear_views(self) -> None:
        self.home_frame.pack_forget()
        self.xtb_frame.pack_forget()
        self.conf_frame.pack_forget()

    def _show_home(self) -> None:
        self._active_view = "home"
        self._clear_views()
        self.home_frame.pack(fill=tk.BOTH, expand=True)
        self.back_btn.config(state=tk.DISABLED)

    def _show_xtb(self) -> None:
        self._active_view = "xtb"
        self._clear_views()
        if self._xtb_module is None:
            self._xtb_module = XTBWorkbenchModule(self.xtb_frame)
            self._xtb_module.create_ui()
            self._xtb_module.main_frame.pack(fill=tk.BOTH, expand=True)
        if self._pending_xtb_session is not None:
            try:
                self._xtb_module.apply_session_state(self._pending_xtb_session)
            except Exception:
                pass
            self._pending_xtb_session = None
        self.xtb_frame.pack(fill=tk.BOTH, expand=True)
        self.back_btn.config(state=tk.NORMAL)

    def _show_conf(self) -> None:
        self._active_view = "conf"
        self._clear_views()
        if self._conf_module is None:
            self._conf_module = ConformationalSamplingModule(self.conf_frame)
            self._conf_module.create_ui()
            self._conf_module.main_frame.pack(fill=tk.BOTH, expand=True)
        if self._pending_conf_session is not None:
            try:
                self._conf_module.apply_session_state(self._pending_conf_session)
            except Exception:
                pass
            self._pending_conf_session = None
        self.conf_frame.pack(fill=tk.BOTH, expand=True)
        self.back_btn.config(state=tk.NORMAL)

    def apply_app_theme(self, ctx) -> None:
        for mod in (self._xtb_module, self._conf_module):
            fn = getattr(mod, "apply_app_theme", None)
            if callable(fn):
                try:
                    fn(ctx)
                except Exception:
                    pass

    def get_session_state(self):
        state = {"version": 1, "active_view": self._active_view}
        if self._xtb_module is not None:
            getter = getattr(self._xtb_module, "get_session_state", None)
            if callable(getter):
                state["xtb"] = getter()
        if self._conf_module is not None:
            getter = getattr(self._conf_module, "get_session_state", None)
            if callable(getter):
                state["conf"] = getter()
        return state

    def apply_session_state(self, state):
        if not isinstance(state, dict):
            return
        self._pending_xtb_session = state.get("xtb")
        self._pending_conf_session = state.get("conf")
        active_view = state.get("active_view", "home")
        if active_view == "xtb":
            self._show_xtb()
        elif active_view == "conf":
            self._show_conf()
        else:
            self._show_home()
