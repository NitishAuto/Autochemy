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

        viewer = LightweightStructureViewer(
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

    def _show_lightweight_structure(self, rows, target_host=None):
        if not rows:
            return
        self._last_lightweight_rows = list(rows)
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
        self._lightweight_viewer = LightweightStructureViewer(
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

    def _warn_if_goat_needs_orca6(self) -> None:
        if getattr(self, "_goat_warned", False):
            return
        major = self._parse_orca_major_version(self.orca_module.get())
        if major is not None and major < 6:
            msg = (
                f"GOAT is available in ORCA 6.0+.\n\n"
                f"Current ORCA module field looks like version {major}.\n"
                "Please update ORCA version before running this input."
            )
        else:
            msg = (
                "GOAT is available in ORCA 6.0 and above.\n\n"
                "Please ensure your ORCA runtime is 6.x before execution."
            )
        messagebox.showwarning("GOAT availability", msg)
        self._goat_warned = True

