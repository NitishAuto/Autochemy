"""
AutoChemy — modular software for computational chemistry workflows.

Windows: double-click "Start AutoChemy.bat" to run.
"""

import tkinter as tk
from tkinter import ttk
import json
import os
import sys
import math
import traceback

# Add modules to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules import app_theme
from modules.software_manager import SoftwarePathDialog


class ModernSwitch(tk.Canvas):
    def __init__(self, parent, width=44, height=24, bg_off="#cbd5e1", bg_on="#22c55e",
                 fg="#ffffff", command=None, default_state=False, *args, **kwargs):
        try:
            bg_col = ttk.Style().lookup("TFrame", "background")
            if not bg_col:
                bg_col = "#f5f5f5"
        except Exception:
            bg_col = "#f5f5f5"
        super().__init__(parent, width=width, height=height, highlightthickness=0, bg=bg_col, *args, **kwargs)
        self.command = command
        self.bg_off = bg_off
        self.bg_on = bg_on
        self.fg = fg
        self.is_on = bool(default_state)
        self.width = width
        self.height = height
        self.radius = height // 2
        self._draw()
        self.bind("<Button-1>", self.toggle)

    def _rounded(self, x1, y1, x2, y2, r):
        return [
            x1+r, y1, x1+r, y1, x2-r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y1+r,
            x2, y2-r, x2, y2-r, x2, y2, x2-r, y2, x2-r, y2, x1+r, y2, x1+r, y2,
            x1, y2, x1, y2-r, x1, y2-r, x1, y1+r, x1, y1+r, x1, y1
        ]

    def _draw(self):
        self.delete("all")
        bg = self.bg_on if self.is_on else self.bg_off
        self.create_polygon(self._rounded(0, 0, self.width, self.height, self.radius), fill=bg, smooth=True, outline="")
        x = self.width - self.height + 2 if self.is_on else 2
        self.create_oval(x, 2, x + self.height - 4, self.height - 4, fill=self.fg, outline="")

    def toggle(self, _event=None):
        self.is_on = not self.is_on
        self._draw()
        if self.command:
            self.command()

    def set_state(self, state: bool):
        s = bool(state)
        if self.is_on != s:
            self.is_on = s
            self._draw()


class ORCASoftwareSuite:
    """Main application shell for AutoChemy."""

    SESSION_VERSION = 1
    SESSION_AUTOSAVE_MS = 15000
    SESSION_FILENAME = os.path.join("AutoChemy_User_Data", ".orca_last_session.json")

    def __init__(self, root):
        """Initialize the main application."""
        self.root = root
        root._orca_app = self
        self.root.title("AutoChemy")
        self.root.geometry("1680x960")
        self.root.minsize(900, 500)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.theme_mode = "light"
        self.editor_font_pt = 12
        self.beginner_mode = True
        self.style = ttk.Style()
        self._dark_mode_var = None
        self._beginner_mode_var = None
        self._session_autosave_job = None
        self._is_closing = False
        self._save_session_enabled = True
        self._current_module_name = None
        self._pending_module_sessions = {}
        self._session_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), self.SESSION_FILENAME)

        self._configure_style()

        self.modules = {}
        self.current_module = None

        self._create_ui()
        self._load_modules()
        startup_module = self._restore_app_session()

        if self.modules:
            first_module = list(self.modules.keys())[0]
            target_module = startup_module if startup_module in self.modules else first_module
            self._switch_module(target_module)
        self._broadcast_theme()
        self._schedule_session_autosave()

        self.root.bind_all("<Control-plus>", lambda e: self._font_delta(1))
        self.root.bind_all("<Control-equal>", lambda e: self._font_delta(1))
        self.root.bind_all("<Control-minus>", lambda e: self._font_delta(-1))
        self.root.bind_all("<Control-0>", lambda e: self._font_reset())

    def _configure_style(self):
        p = app_theme.configure_ttk_style(self.style, self.theme_mode)
        self._palette = p
        self.root.configure(bg=p["bg_root"])

    def _create_ui(self):
        top_bar = ttk.Frame(self.root)
        top_bar.pack(fill=tk.X, padx=10, pady=(8, 4))
        top_inner = ttk.Frame(top_bar)
        top_inner.pack(fill=tk.X, expand=True)
        ttk.Frame(top_inner).pack(side=tk.LEFT, expand=True)
        self._beginner_mode_var = tk.BooleanVar(value=self.beginner_mode)
        ttk.Label(top_inner, text="Beginner", font=("Segoe UI", 10)).pack(side=tk.RIGHT, padx=(0, 4))
        self._beginner_mode_switch = ModernSwitch(
            top_inner,
            bg_off="#0b5cab",
            bg_on="#16a34a",
            default_state=self.beginner_mode,
            command=self._on_beginner_mode_toggle,
        )
        self._beginner_mode_switch.pack(side=tk.RIGHT, padx=(0, 12))
        ttk.Label(top_inner, text="Experienced", font=("Segoe UI", 10)).pack(side=tk.RIGHT, padx=(4, 0))
        self._dark_mode_var = tk.BooleanVar(value=(self.theme_mode == "dark"))
        ttk.Label(top_inner, text="Dark", font=("Segoe UI", 10)).pack(side=tk.RIGHT, padx=(0, 4))
        self._dark_mode_switch = ModernSwitch(
            top_inner,
            bg_off="#cbd5e1",
            bg_on="#1f2937",
            default_state=(self.theme_mode == "dark"),
            command=self._on_dark_mode_toggle,
        )
        self._dark_mode_switch.pack(side=tk.RIGHT, padx=(0, 12))
        ttk.Label(top_inner, text="Light", font=("Segoe UI", 10)).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=(0, 4))

        main_container = ttk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True)

        self.sidebar = ttk.Frame(main_container, width=220)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)
        self.sidebar.pack_propagate(False)

        user_name = ""
        user_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AutoChemy_User_Data", "user_name.txt")
        if os.path.exists(user_file):
            try:
                with open(user_file, "r") as f:
                    user_name = f.read().strip()
            except Exception:
                pass
        else:
            from tkinter import simpledialog
            # Only ask if we are actually rendering the main window to avoid breaking automated tests if any
            name = simpledialog.askstring("Welcome to AutoChemy", "Please enter your name (optional):", parent=self.root)
            if name:
                user_name = name.strip()
                try:
                    os.makedirs(os.path.dirname(user_file), exist_ok=True)
                    with open(user_file, "w") as f:
                        f.write(user_name)
                except Exception:
                    pass
        self.header_text_base = f"{user_name}'s Auto" if user_name else "Auto"
        self.sidebar_header = tk.Canvas(self.sidebar, height=36, highlightthickness=0)
        self.sidebar_header.pack(fill=tk.X, pady=(0, 10), padx=4)
        self.sidebar_header.bind("<Configure>", lambda e: self._draw_sidebar_header())

        self.module_buttons_frame = ttk.Frame(self.sidebar)
        self.module_buttons_frame.pack(fill=tk.BOTH, expand=True, padx=4)

        self.module_container = ttk.Frame(main_container)
        self.module_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)

        self._create_menu_bar()

    def _create_menu_bar(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        self._file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=self._file_menu)
        self._file_menu.add_command(label="Forget Last Session", command=self._forget_last_session)
        self._file_menu.add_separator()
        self._file_menu.add_command(label="Exit", command=self._on_close)
        self._view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=self._view_menu)
        self._view_menu.add_command(label="Dark theme", command=lambda: self._set_theme("dark"))
        self._view_menu.add_command(label="Light theme", command=lambda: self._set_theme("light"))
        self._view_menu.add_separator()
        self._view_menu.add_command(label="Larger editor text  (+)", command=lambda: self._font_delta(1))
        self._view_menu.add_command(label="Smaller editor text (−)", command=lambda: self._font_delta(-1))
        self._view_menu.add_command(label="Reset editor text size", command=self._font_reset)

        self._modules_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Modules", menu=self._modules_menu)

        self._paths_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Paths", menu=self._paths_menu)
        self._paths_menu.add_command(label="Manage Software Paths...", command=self._manage_paths)

        self._help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=self._help_menu)
        self._help_menu.add_command(label="About", command=self._show_about)

        self._style_all_menus()

    def _style_all_menus(self):
        menus = [self._file_menu, self._view_menu, self._modules_menu, self._paths_menu, self._help_menu]
        p = self._palette
        if self.theme_mode == "dark":
            for m in menus:
                app_theme.style_menubar_dark(m, p)
        else:
            for m in menus:
                try:
                    m.configure(
                        bg="#f5f5f5",
                        fg="#1a1a1a",
                        activebackground="#0b5cab",
                        activeforeground="#ffffff",
                    )
                except tk.TclError:
                    pass

    def _on_dark_mode_toggle(self):
        if hasattr(self, "_dark_mode_switch"):
            self._dark_mode_var.set(bool(self._dark_mode_switch.is_on))
        self._set_theme("dark" if self._dark_mode_var.get() else "light")

    def _on_beginner_mode_toggle(self):
        if hasattr(self, "_beginner_mode_switch"):
            self._beginner_mode_var.set(bool(self._beginner_mode_switch.is_on))
        self.beginner_mode = bool(self._beginner_mode_var.get())
        self._broadcast_theme()



    def _sync_dark_mode_toggle(self):
        if self._dark_mode_var is not None:
            self._dark_mode_var.set(self.theme_mode == "dark")
        if hasattr(self, "_dark_mode_switch"):
            self._dark_mode_switch.set_state(self.theme_mode == "dark")

    def _set_theme(self, mode: str):
        self.theme_mode = mode if mode in app_theme.PALETTES else "light"
        self._configure_style()
        self._style_all_menus()
        self._sync_dark_mode_toggle()
        self._broadcast_theme()

    def _font_delta(self, delta: int):
        self.editor_font_pt = max(8, min(24, self.editor_font_pt + delta))
        self._broadcast_theme()

    def _font_reset(self):
        self.editor_font_pt = 12
        self._broadcast_theme()

    def _manage_paths(self):
        SoftwarePathDialog(self.root)

    def _broadcast_theme(self):
        ctx = app_theme.build_context(self.theme_mode, self.editor_font_pt)
        ctx["beginner_mode"] = self.beginner_mode
        self._draw_sidebar_header()
        for mod in self.modules.values():
            fn = getattr(mod, "apply_app_theme", None)
            if callable(fn):
                try:
                    fn(ctx)
                except Exception:
                    pass

    def _draw_sidebar_header(self):
        try:
            self.sidebar_header.delete("all")
            bg_col = self._palette["bg_sidebar_header"]
            self.sidebar_header.configure(bg=bg_col)
            fg_col = "#000000"
            
            font = ("Segoe UI", 16, "bold")
            base_id = self.sidebar_header.create_text(0, 18, text=self.header_text_base, font=font, fill=fg_col, anchor="w")
            x_offset = self.sidebar_header.bbox(base_id)[2]
            
            # The image colors for C, h, e, m, y
            colors = [fg_col, fg_col, fg_col, fg_col, fg_col]
            chars = ["C", "h", "e", "m", "y"]
            ids = [base_id]
            
            for ch, col in zip(chars, colors):
                tid = self.sidebar_header.create_text(x_offset, 18, text=ch, font=font, fill=col, anchor="w")
                x_offset = self.sidebar_header.bbox(tid)[2]
                ids.append(tid)
                
            required_width = x_offset + 20
            if required_width > 220:
                self.sidebar.config(width=required_width)
                w = required_width
            else:
                w = max(220, self.sidebar_header.winfo_width())

            shift = (w - x_offset) / 2
            if shift < 0:
                shift = 0
            for tid in ids:
                self.sidebar_header.move(tid, shift, 0)
        except Exception:
            pass

    def _load_modules(self):
        module_classes = []
        skipped_modules = []
        try:
            from modules.input_creator import InputCreatorModule5
            module_classes.append((InputCreatorModule5, "Input Creator"))
        except Exception as exc:
            msg = f"Skipping module Input Creator: {exc}"
            print(msg)
            skipped_modules.append(msg)

        try:
            from modules.output_viewer import OutputViewerModule
            module_classes.append((OutputViewerModule, "Output Viewer"))
        except Exception as exc:
            msg = f"Skipping module Output Viewer: {exc}"
            print(msg)
            skipped_modules.append(msg)

        try:
            from modules.pes_plot import PESPlotModule
            module_classes.append((PESPlotModule, "PES Plot"))
        except Exception as exc:
            msg = f"Skipping module PES Plot: {exc}"
            print(msg)
            skipped_modules.append(msg)

        try:
            from modules.dia_analysis import DIAAnalysisModule
            module_classes.append((DIAAnalysisModule, "DIA"))
        except Exception as exc:
            msg = f"Skipping module DIA: {exc}"
            print(msg)
            skipped_modules.append(msg)

        try:
            from modules.orbital_creator import OrbitalCreatorModule
            module_classes.append((OrbitalCreatorModule, "Orbital Creator"))
        except Exception as exc:
            msg = f"Skipping module Orbital Creator: {exc}"
            print(msg)
            skipped_modules.append(msg)

        try:
            from modules.ml_analysis import MLAnalysisModule
            module_classes.append((MLAnalysisModule, "ML"))
        except Exception as exc:
            msg = f"Skipping module ML: {exc}"
            print(msg)
            skipped_modules.append(msg)

        try:
            from modules.xtb_hub import XTBHubModule
            module_classes.append((XTBHubModule, "xtb & Conformational Analysis"))
        except Exception as exc:
            msg = f"Skipping module xtb & Conformational Analysis: {exc}"
            print(msg)
            skipped_modules.append(msg)

        try:
            from modules.about_us import AboutUsModule
            module_classes.append((AboutUsModule, "About us"))
        except Exception as exc:
            msg = f"Skipping module About us: {exc}"
            print(msg)
            skipped_modules.append(msg)

        for module_class, module_name in module_classes:
            try:
                module = module_class(self.module_container)
                self.modules[module_name] = module

                btn = ttk.Button(
                    self.module_buttons_frame,
                    text=f"{module.get_icon()} {module_name}",
                    command=lambda name=module_name: self._switch_module(name),
                    style="Sidebar.TButton",
                )
                btn.pack(fill=tk.X, pady=3)

            except Exception as e:
                print(f"Error loading module {module_name}: {e}")

        if skipped_modules:
            try:
                from tkinter import messagebox
                details = "\n".join(skipped_modules[:6])
                if len(skipped_modules) > 6:
                    details += f"\n... and {len(skipped_modules) - 6} more"
                messagebox.showwarning(
                    "Some modules were not loaded",
                    "AutoChemy started, but a few modules could not be loaded.\n\n"
                    f"{details}"
                )
            except Exception:
                pass

    def _switch_module(self, module_name):
        if module_name not in self.modules:
            return

        if self.current_module:
            try:
                self.current_module.deactivate()
            except Exception as e:
                print(f"Error deactivating {self.current_module.get_name()}: {e}")

        new_module = self.modules[module_name]
        
        # Show loading cursor to prevent "freeze" feeling
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        
        try:
            new_module.activate()
            self._apply_pending_module_session(module_name, new_module)
        except Exception as e:
            self.root.config(cursor="")
            from tkinter import messagebox
            messagebox.showerror("Module Error", f"Failed to load module '{module_name}'.\n\nError: {e}")
            return
            
        self.current_module = new_module
        self._current_module_name = module_name
        self.root.title(f"AutoChemy — {module_name}")
        
        self.root.config(cursor="")
        
        # Defer the heavy session saving so UI unfreezes immediately
        self.root.after(100, self._save_session_file)

    def _restore_app_session(self):
        data = self._load_session_file()
        if not isinstance(data, dict):
            return None

        app_state = data.get("app")
        if not isinstance(app_state, dict):
            app_state = {}

        modules_state = data.get("modules")
        if isinstance(modules_state, dict):
            self._pending_module_sessions = dict(modules_state)

        font_pt = app_state.get("editor_font_pt")
        if isinstance(font_pt, int):
            self.editor_font_pt = max(8, min(24, font_pt))

        beginner_mode = app_state.get("beginner_mode")
        if isinstance(beginner_mode, bool):
            self.beginner_mode = beginner_mode
            if self._beginner_mode_var is not None:
                self._beginner_mode_var.set(beginner_mode)
            if hasattr(self, "_beginner_mode_switch"):
                self._beginner_mode_switch.set_state(beginner_mode)

        self._set_theme(app_state.get("theme_mode", self.theme_mode))

        geometry = app_state.get("window_geometry")
        window_state = app_state.get("window_state")
        if isinstance(geometry, str) or isinstance(window_state, str):
            self.root.after_idle(lambda g=geometry, s=window_state: self._restore_window_state(g, s))

        requested_module = app_state.get("current_module")
        return requested_module if isinstance(requested_module, str) else None

    def _restore_window_state(self, geometry, window_state):
        try:
            if isinstance(window_state, str) and window_state == "zoomed":
                self.root.state("zoomed")
                return
            if isinstance(geometry, str) and geometry:
                self.root.geometry(geometry)
            if isinstance(window_state, str) and window_state in ("normal",):
                self.root.state(window_state)
        except tk.TclError:
            pass

    def _schedule_session_autosave(self):
        if self._is_closing:
            return
        if self._session_autosave_job is not None:
            try:
                self.root.after_cancel(self._session_autosave_job)
            except tk.TclError:
                pass
        self._session_autosave_job = self.root.after(self.SESSION_AUTOSAVE_MS, self._autosave_session)

    def _autosave_session(self):
        self._session_autosave_job = None
        if self._is_closing:
            return
        self._save_session_file()
        self._schedule_session_autosave()

    def _on_close(self):
        if self._is_closing:
            return
        self._is_closing = True
        if self._session_autosave_job is not None:
            try:
                self.root.after_cancel(self._session_autosave_job)
            except tk.TclError:
                pass
            self._session_autosave_job = None
        self._save_session_file()
        self.root.destroy()

    def _forget_last_session(self):
        self._save_session_enabled = False
        self._pending_module_sessions = {}
        try:
            if os.path.isfile(self._session_file):
                os.remove(self._session_file)
        except OSError:
            pass


    def _load_session_file(self):
        # Cleanup old root level session file if it exists
        old_session = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".orca_last_session.json")
        if os.path.exists(old_session) and old_session != self._session_file:
            try:
                os.remove(old_session)
            except OSError:
                pass

        if not os.path.isfile(self._session_file):

            return {}
        try:
            with open(self._session_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict) or data.get("version") != self.SESSION_VERSION:
            return {}
        return data

    def _save_session_file(self):
        if not self._save_session_enabled:
            return
        payload = self._build_session_payload()
        if not payload:
            return
        tmp_path = f"{self._session_file}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self._session_file)
        except OSError:
            try:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    def _build_session_payload(self):
        modules_state = {}
        for module_name, module in self.modules.items():
            try:
                state = self._capture_module_session(module)
            except Exception:
                state = None
            if state:
                modules_state[module_name] = state

        try:
            window_state = self.root.state()
        except tk.TclError:
            window_state = "normal"
        if window_state == "iconic":
            window_state = "normal"

        return {
            "version": self.SESSION_VERSION,
            "app": {
                "theme_mode": self.theme_mode,
                "editor_font_pt": self.editor_font_pt,
                "beginner_mode": self.beginner_mode,
                "current_module": self._current_module_name,
                "window_geometry": self.root.geometry(),
                "window_state": window_state,
            },
            "modules": modules_state,
        }

    def _apply_pending_module_session(self, module_name, module):
        if module_name not in self._pending_module_sessions:
            return
        state = self._pending_module_sessions.pop(module_name)
        self._apply_module_session(module, state)

    def _capture_module_session(self, module):
        getter = getattr(module, "get_session_state", None)
        if callable(getter):
            try:
                state = getter()
            except Exception:
                state = None
            if state:
                return state
        return self._capture_generic_module_session(module)

    def _apply_module_session(self, module, state):
        applier = getattr(module, "apply_session_state", None)
        if callable(applier):
            try:
                applier(state)
                return
            except Exception:
                pass
        self._apply_generic_module_session(module, state)

    def _capture_generic_module_session(self, module):
        variables = {}
        texts = {}
        labels = {}
        notebooks = {}
        panes = {}
        treeviews = {}
        raw_attrs = {}

        for name, value in vars(module).items():
            if isinstance(value, tk.Variable):
                try:
                    variables[name] = value.get()
                except Exception:
                    pass
                continue

            if isinstance(value, tk.Text):
                try:
                    texts[name] = value.get("1.0", "end-1c")
                except Exception:
                    pass
                continue

            if isinstance(value, (ttk.Label, tk.Label)):
                try:
                    labels[name] = value.cget("text")
                except tk.TclError:
                    pass
                continue

            if isinstance(value, ttk.Notebook):
                try:
                    notebooks[name] = value.index(value.select())
                except tk.TclError:
                    pass
                continue

            if isinstance(value, ttk.PanedWindow):
                try:
                    panes[name] = [value.sashpos(idx) for idx in range(max(len(value.panes()) - 1, 0))]
                except tk.TclError:
                    pass
                continue

            if isinstance(value, ttk.Treeview):
                rows = []
                try:
                    for item_id in value.get_children(""):
                        row_payload = value.item(item_id)
                        rows.append(
                            {
                                "text": row_payload.get("text", ""),
                                "values": list(row_payload.get("values", ())),
                                "open": bool(row_payload.get("open", False)),
                            }
                        )
                except tk.TclError:
                    rows = []
                if rows:
                    treeviews[name] = rows
                continue

            if self._should_capture_raw_attr(name, value):
                try:
                    raw_attrs[name] = self._json_copy(value)
                except Exception:
                    pass

        if not any((variables, texts, labels, notebooks, panes, treeviews, raw_attrs)):
            return None
        return {
            "version": 1,
            "format": "generic",
            "variables": variables,
            "texts": texts,
            "labels": labels,
            "notebooks": notebooks,
            "panes": panes,
            "treeviews": treeviews,
            "raw_attrs": raw_attrs,
        }

    def _apply_generic_module_session(self, module, state):
        if not isinstance(state, dict):
            return

        for name, value in (state.get("raw_attrs") or {}).items():
            if name == "is_active" or name.startswith("_session_"):
                continue
            if hasattr(module, name):
                try:
                    setattr(module, name, value)
                except Exception:
                    pass

        for name, value in (state.get("variables") or {}).items():
            target = getattr(module, name, None)
            if target is not None and hasattr(target, "set"):
                try:
                    target.set(value)
                except Exception:
                    pass

        for name, value in (state.get("texts") or {}).items():
            widget = getattr(module, name, None)
            if isinstance(widget, tk.Text):
                self._set_text_widget_value(widget, value)

        for name, value in (state.get("labels") or {}).items():
            widget = getattr(module, name, None)
            if isinstance(widget, (ttk.Label, tk.Label)):
                try:
                    widget.config(text=value)
                except tk.TclError:
                    pass

        for name, rows in (state.get("treeviews") or {}).items():
            widget = getattr(module, name, None)
            if isinstance(widget, ttk.Treeview):
                try:
                    for item_id in widget.get_children(""):
                        widget.delete(item_id)
                    for row in rows:
                        widget.insert(
                            "",
                            tk.END,
                            text=row.get("text", ""),
                            values=row.get("values", ()),
                            open=bool(row.get("open", False)),
                        )
                except tk.TclError:
                    pass

        for name, index in (state.get("notebooks") or {}).items():
            widget = getattr(module, name, None)
            if isinstance(widget, ttk.Notebook):
                try:
                    tab_count = len(widget.tabs())
                    if tab_count:
                        widget.select(max(0, min(int(index), tab_count - 1)))
                except (tk.TclError, TypeError, ValueError):
                    pass

        for name, positions in (state.get("panes") or {}).items():
            widget = getattr(module, name, None)
            if isinstance(widget, ttk.PanedWindow) and isinstance(positions, list):
                try:
                    widget.after_idle(lambda w=widget, pos=list(positions): self._restore_pane_positions(w, pos))
                except tk.TclError:
                    pass

    def _restore_pane_positions(self, widget, positions):
        try:
            for idx, pos in enumerate(positions):
                widget.sashpos(idx, int(pos))
        except (tk.TclError, TypeError, ValueError):
            pass

    def _set_text_widget_value(self, widget, text):
        try:
            prior_state = widget.cget("state")
        except tk.TclError:
            prior_state = None
        if str(prior_state) == "disabled":
            try:
                widget.config(state=tk.NORMAL)
            except tk.TclError:
                pass
        try:
            widget.delete("1.0", tk.END)
            if text:
                widget.insert("1.0", text)
        except tk.TclError:
            pass
        if str(prior_state) == "disabled":
            try:
                widget.config(state=tk.DISABLED)
            except tk.TclError:
                pass

    def _json_copy(self, value):
        return json.loads(json.dumps(value))

    def _should_capture_raw_attr(self, name, value):
        if name.startswith("_session_") or name == "is_active":
            return False
        lowered = name.lower()
        ignored_tokens = (
            "frame",
            "parent",
            "root",
            "style",
            "menu",
            "button",
            "label",
            "tree",
            "text",
            "canvas",
            "scroll",
            "combo",
            "notebook",
            "switch",
            "parser",
            "processor",
        )
        if any(token in lowered for token in ignored_tokens):
            return False
        if callable(value):
            return False
        try:
            encoded = json.dumps(value)
        except TypeError:
            return False
        return len(encoded) <= 5000

    def _show_about(self):
        about_text = """AutoChemy

A comprehensive modular software for computational chemistry workflows.

Modules:
- Input Creator: Create ORCA input files
- Output Viewer: Parse and analyze ORCA output files
- PES Plot: Build reaction coordinate energy profiles from ORCA outputs
- DIA: Distortion Interaction Analysis
- Orbital Creator: Create and manipulate molecular orbitals
- ML: Machine Learning Analysis
- xtb & Conformational Analysis: Combined workspace for xTB tools plus conformational sampling (GOAT/CREST)
- About us: CCL IIT Roorkee — credits and group website

Use the Dark mode switch (top-right) or View menu for theme; editor font: Ctrl+/Ctrl−.

Version: 1.0.0
"""
        from tkinter import messagebox
        messagebox.showinfo("About AutoChemy", about_text)


class SplashScreen(tk.Toplevel):
    @staticmethod
    def _fit_pil_image(pil_img, target_w, target_h):
        from PIL import Image

        img_w, img_h = pil_img.size
        ratio = min(target_w / img_w, target_h / img_h)
        new_size = (max(1, int(img_w * ratio)), max(1, int(img_h * ratio)))
        if new_size == pil_img.size:
            return pil_img
        return pil_img.resize(new_size, Image.Resampling.LANCZOS)

    def _load_splash_frames(self, base_dir, target_w, target_h, master):
        """Alternating PNG frames (visible swap); GIF used only if PNG pair is missing."""
        from PIL import Image, ImageTk

        frames = []
        delays = []

        for name in ("autochemy_logo.png", "autochemy_logo_2.png"):
            path = os.path.join(base_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                pil_img = Image.open(path).convert("RGBA")
                pil_img = self._fit_pil_image(pil_img, target_w, target_h)
                frames.append(ImageTk.PhotoImage(pil_img, master=master))
                delays.append(320)
            except Exception:
                pass
        if len(frames) >= 2:
            return frames, delays

        gif_path = os.path.join(base_dir, "AutoChemy.gif")
        if os.path.isfile(gif_path):
            try:
                im = Image.open(gif_path)
                seen = []
                frames = []
                delays = []
                n_frames = getattr(im, "n_frames", 1)
                for i in range(n_frames):
                    im.seek(i)
                    frame = im.convert("RGBA")
                    key = frame.tobytes()
                    if key in seen:
                        continue
                    seen.append(key)
                    frame = self._fit_pil_image(frame, target_w, target_h)
                    frames.append(ImageTk.PhotoImage(frame, master=master))
                    delays.append(max(120, int(im.info.get("duration", 200) or 200)))
                if len(frames) >= 2:
                    return frames, delays
            except Exception:
                pass

        return frames, delays

    def __init__(self, parent, on_load, on_finish):
        super().__init__(parent)
        self.parent = parent
        self.on_load = on_load
        self.on_finish = on_finish
        
        self.overrideredirect(True)
        self.config(bg="#010101")
        try:
            self.attributes("-transparentcolor", "#010101")
        except Exception:
            pass
            
        self._splash_logo_img = None
        self.frames = []
        self._frame_delays = [400]
        self.frame_idx = 0
        self.image_id = None
        self.start_time = __import__('time').time()
        self.min_splash_time = 6.0

        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules", "data")
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        scale = 0.7  # 0.3× smaller than previous splash size
        target_w = int(min(int(sw * 0.78), 1400) * scale)
        target_h = int(min(int(sh * 0.72), 820) * scale)

        self._anim_job = None
        try:
            self.frames, self._frame_delays = self._load_splash_frames(
                base_dir, target_w, target_h, self
            )
        except Exception:
            self.frames = []
            self._frame_delays = [320]

        if self.frames:
            self._splash_logo_img = self.frames[0]
            width = self._splash_logo_img.width()
            height = self._splash_logo_img.height()
            logo_loaded = True
        else:
            self._splash_logo_img = None
            width = 600
            height = 360
            logo_loaded = False

        x = (sw // 2) - (width // 2)
        y = (sh // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        
        self.label = tk.Label(self, bg="#010101")
        self.label.pack(fill=tk.BOTH, expand=True)

        if logo_loaded and self._splash_logo_img is not None:
            self._set_splash_frame(0)
            if len(self.frames) > 1:
                self.after_idle(self._animate_gif)
        else:
            self.label.config(text="AUTOCHEMY", font=("Segoe UI", 56, "bold"), fg="#1f2937")
        
        self.progress = 0
        self.messages = [
            "Initializing workspace...",
            "Loading basis sets...",
            "Preparing geometry parser...",
            "Calibrating potential energy surface...",
            "Initializing xTB hub..."
        ]
        # Delay the real loading by 2.5 seconds to allow the splash animation to play freely
        self.after(2500, self._trigger_real_load)
        
    def _set_splash_frame(self, idx):
        if not self.frames:
            return
        idx = idx % len(self.frames)
        self.frame_idx = idx
        img = self.frames[idx]
        self.label.config(image=img)
        self.label.image = img

    def _animate_gif(self):
        if not self.winfo_exists() or not self.frames or len(self.frames) < 2:
            return
        nxt = (self.frame_idx + 1) % len(self.frames)
        self._set_splash_frame(nxt)
        delay = 320
        if self._frame_delays:
            delay = self._frame_delays[nxt % len(self._frame_delays)]
        self._anim_job = self.after(max(120, int(delay)), self._animate_gif)
        
    def _draw_gradient_border(self, width, height, r=20, bw=6):
        import math
        # 1. Outer full rectangle of lines (gradient)
        for x in range(width):
            if x < r:
                dy = r - math.sqrt(max(0, r**2 - (r-x)**2))
            elif x > width - r:
                dx = x - (width - r)
                dy = r - math.sqrt(max(0, r**2 - dx**2))
            else:
                dy = 0
                
            en = float(x) / width
            if en <= 0.55:
                f = en / 0.55
                cr = int(22 + (253 - 22) * f)
                cg = int(163 + (224 - 163) * f)
                cb = int(74 + (71 - 74) * f)
            else:
                f = (en - 0.55) / 0.45
                cr = int(253 + (239 - 253) * f)
                cg = int(224 + (68 - 224) * f)
                cb = int(71 + (68 - 71) * f)
                
            color = "#{:02x}{:02x}{:02x}".format(cr, cg, cb)
            self.canvas.create_line(x, dy, x, height - dy, fill=color)

        # 2. Inner flat color
        ix1, iy1 = bw, bw
        ix2, iy2 = width - bw, height - bw
        ir = r - bw

        bg_col = "#f8fafc"
        self.canvas.create_oval(ix1, iy1, ix1+2*ir, iy1+2*ir, fill=bg_col, outline="")
        self.canvas.create_oval(ix2-2*ir, iy1, ix2, iy1+2*ir, fill=bg_col, outline="")
        self.canvas.create_oval(ix1, iy2-2*ir, ix1+2*ir, iy2, fill=bg_col, outline="")
        self.canvas.create_oval(ix2-2*ir, iy2-2*ir, ix2, iy2, fill=bg_col, outline="")
        self.canvas.create_rectangle(ix1+ir, iy1, ix2-ir, iy2, fill=bg_col, outline="")
        self.canvas.create_rectangle(ix1, iy1+ir, ix2, iy2-ir, fill=bg_col, outline="")

    def _update_loading(self):
        self.progress += 2.5
        msg_idx = min(int((self.progress / 100) * len(self.messages)), len(self.messages)-1)
        
        if self.progress < 90:
            self.after(20, self._update_loading)
        else:
            self.after(50, self._trigger_real_load)
            
    def _trigger_real_load(self):
        self.update_idletasks()
        self._do_load()

    def _do_load(self):
        try:
            self.on_load() # Block and instantiate modules
        except Exception as e:
            try:
                from tkinter import messagebox
                messagebox.showerror("AutoChemy", f"Startup failed:\n{e}")
            except Exception:
                pass
            traceback.print_exc()
            self._finish_splash()
            return
            
        elapsed = __import__('time').time() - self.start_time
        remaining = self.min_splash_time - elapsed
        if remaining > 0:
            self.after(int(remaining * 1000), self._finish_splash)
        else:
            self._finish_splash()
            
    def _finish_splash(self):
        self.update_idletasks()
        self.destroy_splash()
        
    def destroy_splash(self):
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None
        self.destroy()
        self.on_finish()


def main():
    root = tk.Tk()
    root.withdraw()

    def do_load():
        ORCASoftwareSuite(root)

    def do_finish():
        try:
            root.deiconify()
            root.lift()
            root.focus_force()
        except Exception:
            pass

    try:
        SplashScreen(root, do_load, do_finish)
        root.mainloop()
    except Exception:
        traceback.print_exc()
        try:
            root.deiconify()
            ORCASoftwareSuite(root)
            root.mainloop()
        except Exception:
            traceback.print_exc()



if __name__ == "__main__":
    main()
