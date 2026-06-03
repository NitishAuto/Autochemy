import math
import time
import tkinter as tk


class AutoChemyViewer:
    """Reusable high-quality lightweight molecular canvas viewer."""

    def __init__(self, parent, rows, *, is_dark=False, max_selection=None, on_selection_change=None):
        self.rows = list(rows or [])
        self.is_dark = bool(is_dark)
        self.max_selection = max_selection
        self.on_selection_change = on_selection_change
        self.selected = set()
        self.selected_order = []
        self.last = {}
        self.view = {"yaw": 0.35, "pitch": 0.25, "zoom": 1.0, "last": None}
        self._img_ref = None
        self._draw_state = {"scheduled": False, "last_ms": 0.0}

        bg = "#0b1220" if self.is_dark else "#ffffff"
        edge = "#374151" if self.is_dark else "#cbd5e1"
        self.hint = "#9ca3af" if self.is_dark else "#475569"
        self.canvas = tk.Canvas(parent, bg=bg, highlightthickness=1, highlightbackground=edge)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.show_labels = False
        self.show_numbers = False

        self.btn_frame = tk.Frame(self.canvas, bg=bg)
        self.btn_frame.place(relx=1.0, rely=0.0, anchor="ne", x=-8, y=8)

        def toggle_labels():
            self.show_labels = not self.show_labels
            self.btn_labels.config(relief="sunken" if self.show_labels else "raised")
            self.request_draw(force=True)

        def toggle_numbers():
            self.show_numbers = not self.show_numbers
            self.btn_numbers.config(relief="sunken" if self.show_numbers else "raised")
            self.request_draw(force=True)

        fg = "#ffffff" if self.is_dark else "#000000"
        self.btn_labels = tk.Button(self.btn_frame, text="Labels", bg=bg, fg=fg, relief="raised", command=toggle_labels)
        self.btn_labels.pack(side=tk.LEFT, padx=2)
        
        self.btn_numbers = tk.Button(self.btn_frame, text="Numbers", bg=bg, fg=fg, relief="raised", command=toggle_numbers)
        self.btn_numbers.pack(side=tk.LEFT, padx=2)

        self._prepare_geometry()
        self._bind_events()
        self.request_draw(force=True)

    def _prepare_geometry(self):
        self.coords = [(float(x), float(y), float(z)) for _, x, y, z in self.rows]
        cx = sum(p[0] for p in self.coords) / max(1, len(self.coords))
        cy = sum(p[1] for p in self.coords) / max(1, len(self.coords))
        cz = sum(p[2] for p in self.coords) / max(1, len(self.coords))
        norm = [(x - cx, y - cy, z - cz) for x, y, z in self.coords]
        max_r = max((x * x + y * y + z * z) ** 0.5 for x, y, z in norm) if norm else 1.0
        max_r = max(1.0e-9, max_r)
        self.norm = [(x / max_r, y / max_r, z / max_r) for x, y, z in norm]

        self.elems = [str(sym).upper() for sym, *_ in self.rows]
        self.metals = {"LI", "NA", "K", "MG", "CA", "V", "CR", "MN", "FE", "CO", "NI", "CU", "ZN", "RU", "RH", "PD", "AG", "CD", "PT", "AU", "HG", "PB", "BI"}
        self.color = {
            "H": "#ffffff", "C": "#808080", "N": "#3050f8", "O": "#ff0d0d", "S": "#ffff30", "P": "#ff8000",
            "F": "#90e050", "CL": "#1ff01f", "BR": "#a62929", "I": "#940094", "FE": "#e06633", "MN": "#9c7ac7",
            "CO": "#f090a0", "NI": "#50d050", "CU": "#c88033", "ZN": "#7d80b0", "RU": "#248f8f", "RH": "#0a7d8c",
            "PD": "#006985", "AG": "#c0c0c0", "CD": "#ffd98f", "PT": "#d0d0e0", "AU": "#ffd123", "HG": "#b8b8d0",
        }
        self.cov = {
            "H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "F": 0.57, "P": 1.07, "S": 1.05, "CL": 1.02, "BR": 1.20, "I": 1.39,
            "V": 1.22, "CR": 1.18, "MN": 1.17, "FE": 1.16, "CO": 1.11, "NI": 1.10, "CU": 1.12, "ZN": 1.18, "RU": 1.25,
            "RH": 1.25, "PD": 1.39, "AG": 1.45, "CD": 1.44, "PT": 1.36, "AU": 1.36, "HG": 1.32,
        }
        self.rad = {
            "H": 5.2, "C": 9.6, "N": 9.4, "O": 9.2, "F": 9.0, "P": 10.6, "S": 10.4, "CL": 10.2, "BR": 10.8, "I": 11.2,
            "MN": 11.2, "FE": 11.5, "CO": 11.3, "NI": 11.3, "CU": 11.5, "ZN": 11.3, "RU": 11.8, "RH": 11.8, "PD": 12.1,
            "AG": 12.1, "CD": 12.1, "PT": 12.3, "AU": 12.5, "HG": 12.3,
        }
        self.bonds = []
        self.bond_order = {}
        for i in range(len(self.coords)):
            xi, yi, zi = self.coords[i]
            ei = self.elems[i]
            ri = self.cov.get(ei, 0.77)
            for j in range(i + 1, len(self.coords)):
                xj, yj, zj = self.coords[j]
                ej = self.elems[j]
                rj = self.cov.get(ej, 0.77)
                d = ((xi - xj) ** 2 + (yi - yj) ** 2 + (zi - zj) ** 2) ** 0.5
                if (ei in self.metals) and (ej in self.metals):
                    f, cap = 1.18, 3.00
                elif (ei in self.metals) or (ej in self.metals):
                    f, cap = 1.22, 2.45
                else:
                    f, cap = 1.13, 1.90
                if ei == "H" or ej == "H":
                    f, cap = min(f, 1.08), min(cap, 1.25)
                if 0.45 < d <= min(cap, f * (ri + rj)):
                    self.bonds.append((i, j))
                    bo = 1
                    if (ei not in self.metals and ej not in self.metals and ei != "H" and ej != "H"):
                        ref = max(0.8, (ri + rj))
                        ratio = d / ref
                        if ratio <= 0.80:
                            bo = 3
                        elif ratio <= 0.90:
                            bo = 2
                    self.bond_order[(i, j)] = bo

    def _bind_events(self):
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<ButtonPress-3>", self._on_press)
        self.canvas.bind("<B3-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-3>", self._on_release)
        self.canvas.bind("<ButtonPress-1>", self._on_press, add="+")
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Configure>", lambda _e: self.request_draw(force=True))
        self.canvas.bind("<Visibility>", lambda _e: self.request_draw(force=True))
        self.canvas.bind("<Expose>", lambda _e: self.request_draw(force=True))

    def _project(self, p):
        x, y, z = p
        cyaw, syaw = math.cos(self.view["yaw"]), math.sin(self.view["yaw"])
        x1, z1 = x * cyaw + z * syaw, -x * syaw + z * cyaw
        cp, sp = math.cos(self.view["pitch"]), math.sin(self.view["pitch"])
        y2, z2 = y * cp - z1 * sp, y * sp + z1 * cp
        return x1, y2, z2

    def request_draw(self, force=False):
        now_ms = time.perf_counter() * 1000.0
        if force or (now_ms - self._draw_state["last_ms"] >= 16.0):
            self._draw_state["last_ms"] = now_ms
            self.draw()
            return
        if self._draw_state["scheduled"]:
            return
        self._draw_state["scheduled"] = True

        def _flush():
            self._draw_state["scheduled"] = False
            self._draw_state["last_ms"] = time.perf_counter() * 1000.0
            self.draw()

        self.canvas.after(8, _flush)

    def reset_view(self):
        self.view["yaw"] = 0.35
        self.view["pitch"] = 0.25
        self.view["zoom"] = 1.0
        self.request_draw(force=True)

    def set_selected_indices(self, idxs):
        self.selected = {int(i) for i in idxs if 0 <= int(i) < len(self.rows)}
        self.selected_order = [i for i in self.selected_order if i in self.selected]
        for i in sorted(self.selected):
            if i not in self.selected_order:
                self.selected_order.append(i)
        if self.max_selection and len(self.selected_order) > int(self.max_selection):
            self.selected_order = self.selected_order[-int(self.max_selection):]
            self.selected = set(self.selected_order)
        self._emit_selection()
        self.request_draw(force=True)

    def get_selected_indices(self):
        return sorted(self.selected)

    def _emit_selection(self):
        if callable(self.on_selection_change):
            try:
                self.on_selection_change(self.get_selected_indices())
            except Exception:
                pass

    def _on_click(self, ev):
        best_i, best_d = None, 1.0e9
        for i, (sx, sy, _z) in self.last.items():
            d2 = (sx - ev.x) ** 2 + (sy - ev.y) ** 2
            if d2 < best_d:
                best_i, best_d = i, d2
        if best_i is None or best_d > 14 * 14:
            self.selected.clear()
            self.selected_order = []
        else:
            if best_i in self.selected:
                self.selected.remove(best_i)
                self.selected_order = [i for i in self.selected_order if i != best_i]
            else:
                if self.max_selection and len(self.selected) >= int(self.max_selection):
                    return
                self.selected.add(best_i)
                self.selected_order = [i for i in self.selected_order if i != best_i] + [best_i]
                if len(self.selected_order) > 4:
                    self.selected_order = self.selected_order[-4:]
        self._emit_selection()
        self.request_draw(force=True)

    def _on_press(self, ev):
        self.view["last"] = (ev.x, ev.y)

    def _on_drag(self, ev):
        if not self.view["last"]:
            return
        lx, ly = self.view["last"]
        
        dx = (ev.x - lx) * 0.013
        dy = (ev.y - ly) * 0.013
        self.view["pitch"] += dy
        
        # Adjust yaw direction if upside down
        p = self.view["pitch"] % (2 * math.pi)
        if math.pi / 2 < p < 3 * math.pi / 2:
            self.view["yaw"] -= dx
        else:
            self.view["yaw"] += dx

        self.view["last"] = (ev.x, ev.y)
        self.request_draw(force=False)

    def _on_release(self, _ev):
        self.view["last"] = None

    def _on_wheel(self, ev):
        step = 1.12 if ev.delta > 0 else 0.89
        self.view["zoom"] = max(0.35, min(4.0, self.view["zoom"] * step))
        self.request_draw(force=True)

    def draw(self):
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        self.canvas.delete("all")
        sc = min(w, h) * 0.34 * self.view["zoom"]
        depth = []
        for i, p in enumerate(self.norm):
            x, y, z = self._project(p)
            sx, sy = w * 0.5 + x * sc, h * 0.5 - y * sc
            self.last[i] = (sx, sy, z)
            depth.append((z, i))
        depth.sort()
        bd = []
        for i, j in self.bonds:
            x1, y1, z1 = self.last[i]
            x2, y2, z2 = self.last[j]
            bd.append((((z1 + z2) * 0.5), i, j, x1, y1, x2, y2))
        bd.sort()
        try:
            from PIL import Image, ImageDraw, ImageTk
            ss = 3
            iw, ih = max(2, w * ss), max(2, h * ss)
            bg = self.canvas["bg"].lstrip("#")
            bg_rgb = (int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16))
            im = Image.new("RGBA", (iw, ih), bg_rgb + (255,))
            dr = ImageDraw.Draw(im, "RGBA")

            def tube(a1, b1, a2, b2, base_w=4):
                dr.line([(a1 * ss, b1 * ss), (a2 * ss, b2 * ss)], fill=(66, 74, 86, 255), width=max(2, int(base_w * ss)))
                dr.line([(a1 * ss, b1 * ss), (a2 * ss, b2 * ss)], fill=(163, 170, 181, 255), width=max(1, int((base_w - 2) * ss)))

            for _z, i, j, x1, y1, x2, y2 in bd:
                bo = int(self.bond_order.get((i, j), 1))
                if bo <= 1:
                    tube(x1, y1, x2, y2, 4)
                elif bo == 2:
                    dx, dy = x2 - x1, y2 - y1
                    ln = max(1.0e-6, (dx * dx + dy * dy) ** 0.5)
                    px, py = -dy / ln, dx / ln
                    sep = 2.4
                    tube(x1 + px * sep, y1 + py * sep, x2 + px * sep, y2 + py * sep, 3.4)
                    tube(x1 - px * sep, y1 - py * sep, x2 - px * sep, y2 - py * sep, 3.4)
                else:
                    dx, dy = x2 - x1, y2 - y1
                    ln = max(1.0e-6, (dx * dx + dy * dy) ** 0.5)
                    px, py = -dy / ln, dx / ln
                    sep = 3.3
                    tube(x1 + px * sep, y1 + py * sep, x2 + px * sep, y2 + py * sep, 3.0)
                    tube(x1, y1, x2, y2, 3.2)
                    tube(x1 - px * sep, y1 - py * sep, x2 - px * sep, y2 - py * sep, 3.0)

            for _z, i in depth:
                sx, sy, _ = self.last[i]
                sym = self.elems[i]
                zr = max(0.62, min(1.72, self.view["zoom"] ** 0.68))
                r = self.rad.get(sym, 11.0) * zr * 0.9
                fill = self.color.get(sym, "#d1d5db")
                rr, gg, bb = int(fill[1:3], 16), int(fill[3:5], 16), int(fill[5:7], 16)
                if sym == "H":
                    rr, gg, bb = 248, 250, 252
                    ocol = (156, 163, 175)
                else:
                    ocol = (rr, gg, bb)
                if i in self.selected:
                    ocol = (103, 232, 249)
                    ow = max(2, int(2 * ss))
                else:
                    ow = max(1, int(1 * ss))
                x0, y0 = (sx - r) * ss, (sy - r) * ss
                x1, y1 = (sx + r) * ss, (sy + r) * ss
                dr.ellipse([x0, y0, x1, y1], fill=(rr, gg, bb, 255), outline=ocol + (255,), width=ow)

            final = im.resize((w, h), Image.Resampling.LANCZOS)
            self._img_ref = ImageTk.PhotoImage(final)
            self.canvas.create_image(0, 0, image=self._img_ref, anchor="nw")
        except Exception:
            for _z, i, j, x1, y1, x2, y2 in bd:
                self.canvas.create_line(x1, y1, x2, y2, fill="#7b8794", width=3)
            for _z, i in depth:
                sx, sy, _ = self.last[i]
                sym = self.elems[i]
                zr = max(0.62, min(1.72, self.view["zoom"] ** 0.68))
                r = self.rad.get(sym, 11.0) * zr * 1.5
                fill = "#f8fafc" if sym == "H" else self.color.get(sym, "#d1d5db")
                outline = "#9ca3af" if sym == "H" else fill
                self.canvas.create_oval(sx - r, sy - r, sx + r, sy + r, fill=fill, outline=outline, width=1)

        self.canvas.create_text(8, 8, anchor="nw", fill=self.hint, font=("Segoe UI", 9), text="Right drag: rotate, wheel: zoom, left click: select")

        if getattr(self, "show_labels", False) or getattr(self, "show_numbers", False):
            for _z, i in depth:
                sx, sy, _ = self.last[i]
                text = ""
                if self.show_labels:
                    text += self.elems[i]
                if self.show_numbers:
                    text += str(i + 1)
                
                font = ("Segoe UI", 10, "bold")
                outline_color = "#ffffff" if not self.is_dark else "#0b1220"
                text_color = "#000000" if not self.is_dark else "#ffffff"
                
                self.canvas.create_text(sx-1, sy-1, text=text, fill=outline_color, font=font)
                self.canvas.create_text(sx+1, sy-1, text=text, fill=outline_color, font=font)
                self.canvas.create_text(sx-1, sy+1, text=text, fill=outline_color, font=font)
                self.canvas.create_text(sx+1, sy+1, text=text, fill=outline_color, font=font)
                self.canvas.create_text(sx, sy, text=text, fill=text_color, font=font)

        if len(self.selected_order) == 2:
            i, j = self.selected_order
            x1, y1, z1 = self.coords[i]
            x2, y2, z2 = self.coords[j]
            dist = ((x2 - x1)**2 + (y2 - y1)**2 + (z2 - z1)**2)**0.5
            self.canvas.create_text(8, 28, anchor="nw", fill=self.hint, font=("Segoe UI", 12, "bold"), text=f"Distance: {dist:.3f} Å")
        elif len(self.selected_order) == 3:
            import math
            i, j, k = self.selected_order
            x1, y1, z1 = self.coords[i]
            x2, y2, z2 = self.coords[j]
            x3, y3, z3 = self.coords[k]
            v1 = (x1 - x2, y1 - y2, z1 - z2)
            v2 = (x3 - x2, y3 - y2, z3 - z2)
            L1 = (v1[0]**2 + v1[1]**2 + v1[2]**2)**0.5
            L2 = (v2[0]**2 + v2[1]**2 + v2[2]**2)**0.5
            if L1 > 0 and L2 > 0:
                dot_val = v1[0]*v2[0] + v1[1]*v2[1] + v1[2]*v2[2]
                dot_val = max(-1.0, min(1.0, dot_val / (L1 * L2)))
                angle = math.degrees(math.acos(dot_val))
                self.canvas.create_text(8, 28, anchor="nw", fill=self.hint, font=("Segoe UI", 12, "bold"), text=f"Angle: {angle:.2f}°")
        elif len(self.selected_order) == 4:
            import math
            i, j, k, l = self.selected_order
            p1, p2, p3, p4 = self.coords[i], self.coords[j], self.coords[k], self.coords[l]
            b1 = (p2[0]-p1[0], p2[1]-p1[1], p2[2]-p1[2])
            b2 = (p3[0]-p2[0], p3[1]-p2[1], p3[2]-p2[2])
            b3 = (p4[0]-p3[0], p4[1]-p3[1], p4[2]-p3[2])
            
            def cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
            def dot(a, b): return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
            def norm(a): return (a[0]**2 + a[1]**2 + a[2]**2)**0.5
            
            n1 = cross(b1, b2)
            n2 = cross(b2, b3)
            n1_L = norm(n1)
            n2_L = norm(n2)
            if n1_L > 0 and n2_L > 0:
                n1 = (n1[0]/n1_L, n1[1]/n1_L, n1[2]/n1_L)
                n2 = (n2[0]/n2_L, n2[1]/n2_L, n2[2]/n2_L)
                b2_norm = norm(b2)
                b2_unit = (b2[0]/b2_norm, b2[1]/b2_norm, b2[2]/b2_norm)
                m1 = cross(n1, b2_unit)
                x = dot(n1, n2)
                y = dot(m1, n2)
                dih = math.degrees(math.atan2(y, x))
                self.canvas.create_text(8, 28, anchor="nw", fill=self.hint, font=("Segoe UI", 12, "bold"), text=f"Dihedral: {dih:.2f}°")
