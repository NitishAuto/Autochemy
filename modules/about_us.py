"""
About Us Module for AutoChemy
Combines Team and Donate information.
"""

import tkinter as tk
from tkinter import ttk
import os

from modules.base_module import BaseModule


class AboutUsModule(BaseModule):
    """Combined Team and Donation module."""

    def get_name(self) -> str:
        return "About us"

    def get_icon(self) -> str:
        return "✨"

    def create_ui(self):
        self.main_frame = ttk.Frame(self.parent_frame)
        
        try:
            self.bg_col = ttk.Style().lookup("TFrame", "background") or "#f1f5f9"
        except Exception:
            self.bg_col = "#f1f5f9"
            
        # Add a canvas with a scrollbar in case the content gets too long
        self.canvas = tk.Canvas(self.main_frame, bg=self.bg_col, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.main_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # We need a frame inside the canvas to allow smooth scrolling of the custom drawings
        # Wait, since we are using canvas.create_polygon, we can just update the canvas scrollregion
        

        self.btn_cite = ttk.Button(
            self.canvas,
            text="Help us",
            command=self._on_cite_click,
            style="Accent.TButton"
        )
        
        self.btn_report = ttk.Button(
            self.canvas,
            text="Report Bug / Request Feature",
            command=self._on_report_click,
            style="Accent.TButton"
        )
        
        self.canvas.bind("<Configure>", self._on_resize)
        self._draw_layout(1000, 1000)

    def activate(self):
        super().activate()
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        
    def _on_mousewheel(self, event):
        if self.is_active:
            self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def _rounded(self, x1, y1, x2, y2, r):
        return [
            x1+r, y1, x1+r, y1, x2-r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y1+r,
            x2, y2-r, x2, y2-r, x2, y2, x2-r, y2, x2-r, y2, x1+r, y2, x1+r, y2,
            x1, y2, x1, y2-r, x1, y2-r, x1, y1+r, x1, y1+r, x1, y1
        ]

    def _on_resize(self, event):
        self._draw_layout(event.width, event.height)

    def _draw_layout(self, w, h):
        self.canvas.delete("all")
        
        is_dark = "dark" in str(self.bg_col).lower() or self.bg_col.startswith("#1") or self.bg_col.startswith("#2")
        card_bg = "#1e293b" if is_dark else "#ffffff"
        text_main = "#f8fafc" if is_dark else "#0f172a"
        text_sub = "#cbd5e1" if is_dark else "#475569"
        accent_col = "#3b82f6" if is_dark else "#2563eb"
        
        max_card_w = 1200
        # Give a little extra margin for the scrollbar
        card_w = min(w - 120, max_card_w)
        cx = w / 2
        
        y_cursor = 40
        
        logo_max_w = int(min(max(card_w - 80, 320), 560) * 0.7)
        logo_img = self._ensure_logo(logo_max_w)

        if logo_img:
            self.canvas.create_image(
                cx, y_cursor,
                image=logo_img,
                anchor=tk.N
            )
            y_cursor += logo_img.height() + 24
        else:
            self.canvas.create_text(
                cx, y_cursor,
                text="🛡️",
                font=("Segoe UI", 48),
                justify=tk.CENTER,
                anchor=tk.N
            )
            y_cursor += 80
        
        if not hasattr(self, "team_images"):
            self._load_team_images()

        cards = [
            {
                "title": "The AutoChemyTeam",
                "type": "team",
                "body": "" # Handled specially
            },
            {
                "title": "Motivated by Science ",
                "body": "Built by young  researchers to support the scientific community, allowing to save time and focus on what matters most: new ideas and science."
            },
            {
                "title": "Never Settle for Less",
                "body": " We are committed to ensuring you never have to compromise on quality or features."
            },
            {
                "title": "Feedback & Support",
                "body": "We are researchers and learning developers doing our best to improve AutoChemy. If you find any bugs, scientific inaccuracies, or unexpected results, we sincerely apologize and kindly request that you report them so we can fix them for everyone.",
                "button": self.btn_report
            },
            {
                "title": "Share",
                "body": "AutoChemy is free and open-source. We would be thrilled to see it used in workshops to teach computational chemistry, or to help researchers save valuable time. Please share it with others so they can benefit from it as well!"
            },
        ]
        
        for card in cards:
            title_id = self.canvas.create_text(
                cx, y_cursor + 24,
                text=card["title"],
                font=("Segoe UI", 20, "bold"),
                fill=accent_col,
                width=card_w - 48,
                justify=tk.CENTER,
                anchor=tk.N
            )
            tb = self.canvas.bbox(title_id)
            title_h = tb[3] - tb[1]
            
            body_y = y_cursor + 24 + title_h + 16
            
            elements_to_raise = []
            if card.get("type") == "team":
                # Introduction text
                intro_txt = tk.Text(
                    self.canvas, font=("Segoe UI", 13),
                    fg=text_sub, bg=card_bg, bd=0, highlightthickness=0, height=1
                )
                intro_txt.tag_configure("center", justify="center")
                intro_txt.insert("1.0", "")
                intro_txt.tag_add("center", "1.0", "end")
                intro_txt.bind("<Key>", lambda e: "break" if e.char and not (e.state & 4) else None)
                
                intro_id = self.canvas.create_window(
                    cx, body_y,
                    window=intro_txt,
                    anchor=tk.N,
                    width=card_w - 48
                )
                self.canvas.update_idletasks()
                ib = self.canvas.bbox(intro_id)
                intro_h = ib[3] - ib[1]
                
                img_y = body_y + intro_h + 10
                
                # Draw images dynamically based on list length
                n_images = len(self.team_images)
                spacing = 30
                img_size = 200
                total_w_imgs = n_images * img_size + max(0, n_images - 1) * spacing
                start_x = cx - total_w_imgs / 2 + img_size / 2
                team_block_bottom = img_y + img_size + 12
                
                for i, person in enumerate(self.team_images):
                    x = start_x + i * (img_size + spacing)
                    if person["image"]:
                        iid = self.canvas.create_image(x, img_y, image=person["image"], anchor=tk.N)
                        elements_to_raise.append(iid)
                    
                    # Name and modules below photo
                    name_txt = tk.Text(
                        self.canvas, font=("Segoe UI", 12, "bold"),
                        fg=text_main if not is_dark else "#f8fafc",
                        bg=card_bg, bd=0, highlightthickness=0, height=1, width=25
                    )
                    name_txt.tag_configure("center", justify="center")
                    name_txt.insert("1.0", person["name"])
                    name_txt.tag_add("center", "1.0", "end")
                    name_txt.bind("<Key>", lambda e: "break" if e.char and not (e.state & 4) else None)
                    
                    txt_id = self.canvas.create_window(
                        x, img_y + img_size + 12,
                        window=name_txt,
                        anchor=tk.N
                    )
                    elements_to_raise.append(txt_id)
                    self.canvas.update_idletasks()
                    nb = self.canvas.bbox(txt_id)
                    block_bottom = nb[3] if nb else img_y + img_size + 36

                    modules_text = person.get("modules", "")
                    if modules_text:
                        mod_txt = tk.Text(
                            self.canvas, font=("Segoe UI", 9),
                            fg=text_sub, bg=card_bg, bd=0, highlightthickness=0,
                            height=3, width=28, wrap=tk.WORD
                        )
                        mod_txt.tag_configure("center", justify="center")
                        mod_txt.insert("1.0", modules_text)
                        mod_txt.tag_add("center", "1.0", "end")
                        mod_txt.bind("<Key>", lambda e: "break" if e.char and not (e.state & 4) else None)

                        mod_id = self.canvas.create_window(
                            x, block_bottom + 6,
                            window=mod_txt,
                            anchor=tk.N
                        )
                        elements_to_raise.append(mod_id)
                        self.canvas.update_idletasks()
                        mb = self.canvas.bbox(mod_id)
                        if mb:
                            block_bottom = mb[3]

                    team_block_bottom = max(team_block_bottom, block_bottom)
                
                # Footer text
                footer_y = team_block_bottom + 16
                foot_txt = tk.Text(
                    self.canvas, font=("Segoe UI", 12),
                    fg=text_sub, bg=card_bg, bd=0, highlightthickness=0, height=2
                )
                foot_txt.tag_configure("center", justify="center")
                foot_txt.insert("1.0", "")
                foot_txt.tag_add("center", "1.0", "end")
                foot_txt.bind("<Key>", lambda e: "break" if e.char and not (e.state & 4) else None)
                
                foot_id = self.canvas.create_window(
                    cx, footer_y,
                    window=foot_txt,
                    anchor=tk.N,
                    width=card_w - 48
                )
                self.canvas.update_idletasks()
                fb = self.canvas.bbox(foot_id)
                body_h = fb[3] - body_y
                elements_to_raise.extend([intro_id, foot_id])
            else:
                body_id = self.canvas.create_text(
                    cx, body_y,
                    text=card["body"],
                    font=("Segoe UI", 13),
                    fill=text_sub,
                    width=card_w - 48,
                    justify=tk.CENTER,
                    anchor=tk.N
                )
                bb = self.canvas.bbox(body_id)
                body_h = bb[3] - bb[1]
                elements_to_raise.append(body_id)
            
            card_h = 24 + title_h + 16 + body_h + 32
            
            if card.get("button"):
                card_h += 60
                
            x1 = cx - card_w / 2
            y1 = y_cursor
            x2 = cx + card_w / 2
            y2 = y_cursor + card_h
            
            shadow_pts = self._rounded(x1+2, y1+4, x2+2, y2+4, 16)
            self.canvas.create_polygon(shadow_pts, fill="#000000" if is_dark else "#e2e8f0", stipple="gray50", smooth=True, outline="")
            
            pts = self._rounded(x1, y1, x2, y2, 16)
            self.canvas.create_polygon(pts, fill=card_bg, outline=text_sub if is_dark else "#e2e8f0", smooth=True)
            
            self.canvas.tag_raise(title_id)
            for el in elements_to_raise:
                self.canvas.tag_raise(el)
            
            if card.get("button"):
                self.canvas.create_window(
                    cx, y2 - 40,
                    window=card["button"],
                    anchor=tk.CENTER
                )
            
            y_cursor += card_h + 24

        # Update scroll region
        self.canvas.configure(scrollregion=(0, 0, w, y_cursor + 40))



    def _on_cite_click(self):
        import tkinter.messagebox as messagebox
        messagebox.showinfo(
            "Cite Us",
            "Please cite AutoChemy in your publications to support us!"
        )

    def _on_report_click(self):
        import webbrowser
        webbrowser.open("mailto:nitishdft@gmail.com?subject=AutoChemy%20Bug/Feature%20Request")

    def deactivate(self):
        # Unbind scroll wheel when module is deactivated to avoid global event issues
        self.canvas.unbind_all("<MouseWheel>")
        super().deactivate()

    def _ensure_logo(self, max_width):
        """Load logo at a readable size (sharp PIL resize, not tiny subsample)."""
        max_width = max(280, int(max_width))
        if getattr(self, "_logo_width", None) == max_width and getattr(self, "logo_img", None):
            return self.logo_img

        self._logo_width = max_width
        self.logo_img = None
        logo_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data",
            "autochemy_logo.png",
        )
        if not os.path.isfile(logo_path):
            return None

        try:
            from PIL import Image, ImageTk

            im = Image.open(logo_path).convert("RGBA")
            w, h = im.size
            if w > max_width:
                nh = max(1, int(h * max_width / w))
                im = im.resize((max_width, nh), Image.Resampling.LANCZOS)
            self.logo_img = ImageTk.PhotoImage(im)
        except Exception:
            self.logo_img = None
        return self.logo_img

    def _load_team_images(self):
        import os
        try:
            from PIL import Image, ImageTk
        except ImportError:
            self.team_images = [
                {
                    "name": "Nitish Kumar Singh",
                    "image": None,
                    "modules": "Input Creator · PES Plot · xTB · Conformational Analysis",
                },
                {
                    "name": "Saurabh Singh Negi",
                    "image": None,
                    "modules": "Output Viewer · DIA · Orbital Creator · ML",
                },
            ]
            return
            
        self.team_images = []
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        
        profiles = [
            {
                "file": "nitish.jpg",
                "name": "Nitish Kumar Singh",
                "modules": "Input Creator · PES Plot · xTB · Conformational Analysis",
            },
            {
                "file": "saurabh.jpg",
                "name": "Saurabh Singh Negi",
                "modules": "Output Viewer · DIA · Orbital Creator · ML",
            },
        ]
        
        size = (200, 200)
        
        for profile in profiles:
            path = os.path.join(base_dir, profile["file"])
            img_tk = None
            if os.path.isfile(path):
                try:
                    img = Image.open(path)
                    
                    # Crop image to square for symmetry
                    w, h = img.size
                    s = min(w, h)
                    img = img.crop(((w-s)//2, (h-s)//2, (w+s)//2, (h+s)//2))
                    
                    img = img.resize(size, Image.LANCZOS)
                    img_tk = ImageTk.PhotoImage(img)
                except Exception as e:
                    print(f"Failed to load {profile['file']}: {e}")
            
            self.team_images.append({
                "name": profile["name"],
                "image": img_tk,
                "modules": profile.get("modules", ""),
            })
