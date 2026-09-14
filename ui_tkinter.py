"""Knowley Üretim Kontrol Odası - Bağımsız Tkinter Masaüstü Arayüzü."""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import sys
import threading
import time
import webbrowser
from datetime import datetime
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import ttk, messagebox

# High DPI netliği (Windows)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# Mevcut arka uç modülleri
from core.generator import QUESTION_SCHEMA_VERSION
from ui_server import JobManager, firebase_summary

# Renk Paleti (Knowley Dark Modern Theme)
BG_MAIN = "#070914"
PANEL_BG = "#111426"
PANEL_INNER = "#161b34"
PANEL_BORDER = "#23294c"
BORDER_ACCENT = "#343d70"

CYAN = "#57e5ff"
PURPLE = "#9277ff"
GREEN = "#59e3a6"
ORANGE = "#ffbb72"
RED = "#ff708c"
MUTED = "#8c97b8"
TEXT_LIGHT = "#f5f7ff"
TEXT_DIM = "#b3bcdc"
TEXT_DARK = "#07101a"

FONT_FAMILY = "Segoe UI"


class TkLogHandler(logging.Handler):
    """Logging kayıtlarını Tk ana thread'inin tüketeceği kuyruğa aktarır."""

    def __init__(self, output_queue: "queue.Queue[tuple[str, str]]") -> None:
        super().__init__()
        self.output_queue = output_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = record.levelname.lower()
            self.output_queue.put_nowait((self.format(record) + "\n", level))
        except Exception:
            self.handleError(record)


class TkConsoleStream:
    """print/stdout/stderr metinlerini bloklamadan terminal kuyruğuna yollar."""

    def __init__(self, output_queue: "queue.Queue[tuple[str, str]]", tag: str) -> None:
        self.output_queue = output_queue
        self.tag = tag

    def write(self, text: str) -> int:
        if text:
            self.output_queue.put_nowait((text, self.tag))
        return len(text)

    def flush(self) -> None:
        return None

    @property
    def encoding(self) -> str:
        return "utf-8"

    def isatty(self) -> bool:
        return False


class ModernPill(tk.Canvas):
    """Modern yuvarlatılmış hap (pill / badge) bileşeni."""

    def __init__(
        self,
        master: tk.Widget,
        text: str,
        dot_color: Optional[str] = CYAN,
        bg: str = PANEL_INNER,
        fg: str = TEXT_DIM,
        border_color: str = PANEL_BORDER,
        font_size: int = 9,
        padx: int = 12,
        pady: int = 6,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, bg=bg, highlightthickness=0, **kwargs)
        self.text = text
        self.dot_color = dot_color
        self.pill_bg = bg
        self.pill_fg = fg
        self.border_color = border_color
        self.font = (FONT_FAMILY, font_size)
        self.padx = padx
        self.pady = pady
        self.bind("<Configure>", self._draw)

    def set_text(self, text: str, dot_color: Optional[str] = None) -> None:
        self.text = text
        if dot_color is not None:
            self.dot_color = dot_color
        self._draw()

    def _draw(self, _event: Optional[tk.Event] = None) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return

        r = h // 2
        # Arka plan ve kenarlık
        self.create_arc(1, 1, h, h, start=90, extent=180, fill=self.pill_bg, outline=self.border_color)
        self.create_arc(w - h - 1, 1, w - 1, h, start=270, extent=180, fill=self.pill_bg, outline=self.border_color)
        self.create_rectangle(r, 1, w - r, h, fill=self.pill_bg, outline=self.pill_bg)
        self.create_line(r, 1, w - r, 1, fill=self.border_color)
        self.create_line(r, h, w - r, h, fill=self.border_color)

        start_x = self.padx
        if self.dot_color:
            dot_r = 4
            dot_cy = h // 2
            self.create_oval(
                start_x,
                dot_cy - dot_r,
                start_x + (dot_r * 2),
                dot_cy + dot_r,
                fill=self.dot_color,
                outline=self.dot_color,
            )
            start_x += (dot_r * 2) + 8

        self.create_text(
            start_x,
            h // 2,
            text=self.text,
            anchor="w",
            fill=self.pill_fg,
            font=self.font,
        )


class SmoothProgressBar(tk.Canvas):
    """Özel çizimli modern degrade tarzı ilerleme çubuğu."""

    def __init__(self, master: tk.Widget, height: int = 10, bg: str = "#141830", **kwargs: Any) -> None:
        super().__init__(master, height=height, bg=bg, highlightthickness=0, **kwargs)
        self.pct = 0.0
        self.bar_bg = bg
        self.bind("<Configure>", self._draw)

    def set_progress(self, percent: float) -> None:
        self.pct = max(0.0, min(100.0, percent))
        self._draw()

    def _draw(self, _event: Optional[tk.Event] = None) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 0 or h <= 0:
            return

        # Arka plan rayı
        self.create_rectangle(0, 0, w, h, fill=self.bar_bg, outline="")
        if self.pct > 0:
            fill_w = int((self.pct / 100.0) * w)
            self.create_rectangle(0, 0, fill_w, h, fill=CYAN, outline="")


class KnowleyTkinterApp(tk.Tk):
    """Knowley Soru Üretim Kontrol Odası Tkinter Masaüstü Uygulaması."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Knowley Üretim Kontrol Odası")
        self.geometry("1440x960")
        self.minsize(1120, 720)
        self.configure(bg=BG_MAIN)

        self.manager = JobManager()
        self.active_job_id: Optional[str] = None
        self.config_data: Optional[Dict[str, Any]] = None
        self.provider_cards: Dict[str, Dict[str, Any]] = {}
        self.known_event_count = 0
        self.console_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self._tk_log_handler: Optional[TkLogHandler] = None

        self._configure_styles()
        self._build_ui()
        self._install_console_capture()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after_idle(self._apply_initial_layout)

        # İlk veri yükleme
        self.after(100, self._load_initial_data)
        # Düzenli yoklama döngüsü
        self.after(1000, self._poll_job_cycle)
        self.after(100, self._poll_console_queue)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Global Dark Theme ttk stilleri
        style.configure(".", background=BG_MAIN, foreground=TEXT_LIGHT, font=(FONT_FAMILY, 10))
        style.configure("TFrame", background=BG_MAIN)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("Inner.TFrame", background=PANEL_INNER)

        # Combobox
        style.configure(
            "TCombobox",
            fieldbackground="#0a0e22",
            background=PANEL_INNER,
            foreground=TEXT_LIGHT,
            arrowcolor=CYAN,
            bordercolor=PANEL_BORDER,
            lightcolor=PANEL_BORDER,
            darkcolor=PANEL_BORDER,
            padding=5,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#0a0e22"), ("disabled", "#1a1d30")],
            foreground=[("disabled", MUTED)],
        )

        # Checkbutton
        style.configure(
            "TCheckbutton",
            background=PANEL_BG,
            foreground=TEXT_LIGHT,
            font=(FONT_FAMILY, 9),
            indicatorcolor="#0d1127",
            indicatorrelief="flat",
        )
        style.map(
            "TCheckbutton",
            indicatorcolor=[("selected", CYAN), ("!selected", "#1a203d")],
            foreground=[("disabled", MUTED)],
        )

        # Radiobutton
        style.configure(
            "TRadiobutton",
            background=PANEL_BG,
            foreground=TEXT_LIGHT,
            font=(FONT_FAMILY, 9, "bold"),
            indicatorcolor="#0d1127",
        )
        style.map(
            "TRadiobutton",
            indicatorcolor=[("selected", CYAN), ("!selected", "#1a203d")],
            foreground=[("selected", CYAN)],
        )

        # Scale
        style.configure(
            "Horizontal.TScale",
            background=PANEL_INNER,
            troughcolor="#0a0e22",
            sliderthickness=15,
            bordercolor=PANEL_BORDER,
        )

        # Spinbox
        style.configure(
            "TSpinbox",
            fieldbackground="#0a0e22",
            background=PANEL_INNER,
            foreground=TEXT_LIGHT,
            arrowcolor=CYAN,
            bordercolor=PANEL_BORDER,
            padding=4,
        )

    def _build_ui(self) -> None:
        # Ana Konteyner
        root_container = tk.Frame(self, bg=BG_MAIN, padx=22, pady=16)
        root_container.pack(fill="both", expand=True)

        # 1. ÜST BAR (Top bar)
        topbar = tk.Frame(root_container, bg=BG_MAIN)
        topbar.pack(fill="x", pady=(0, 16))

        # Sol Marka
        brand_frame = tk.Frame(topbar, bg=BG_MAIN)
        brand_frame.pack(side="left")

        # Marka Logosu "K"
        logo_canvas = tk.Canvas(brand_frame, width=46, height=46, bg=BG_MAIN, highlightthickness=0)
        logo_canvas.pack(side="left", padx=(0, 14))
        logo_canvas.create_rectangle(2, 2, 44, 44, fill=PURPLE, outline=CYAN, width=2)
        logo_canvas.create_text(23, 23, text="K", fill=TEXT_DARK, font=(FONT_FAMILY, 19, "bold"))

        title_box = tk.Frame(brand_frame, bg=BG_MAIN)
        title_box.pack(side="left")
        tk.Label(
            title_box,
            text="KNOWLEY CONTROL ROOM",
            fg=MUTED,
            bg=BG_MAIN,
            font=(FONT_FAMILY, 8, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="Soru üretimini tek ekrandan yönet.",
            fg=TEXT_LIGHT,
            bg=BG_MAIN,
            font=(FONT_FAMILY, 15, "bold"),
        ).pack(anchor="w")

        # Sağ Durum Hapları (Pills)
        pills_box = tk.Frame(topbar, bg=BG_MAIN)
        pills_box.pack(side="right")

        self.pill_algorithm = ModernPill(pills_box, text="Sheets yükleniyor", dot_color=CYAN, width=155, height=32)
        self.pill_algorithm.pack(side="left", padx=4)

        self.pill_schema = ModernPill(pills_box, text=f"Soru v{QUESTION_SCHEMA_VERSION}", dot_color=CYAN, width=125, height=32)
        self.pill_schema.pack(side="left", padx=4)

        self.pill_firebase = ModernPill(pills_box, text="Firestore denetleniyor", dot_color=ORANGE, width=175, height=32)
        self.pill_firebase.pack(side="left", padx=4)

        # Pencere/pane boyutundan etkilenmeyen sabit aksiyon şeridi.
        self._build_action_bar(root_container)

        # 2. ANA İÇERİK: Üst kontroller ve altta daima görünür terminal.
        # Dikey sash kullanıcının terminal alanını istediği kadar büyütmesini sağlar.
        self.main_panes = tk.PanedWindow(
            root_container,
            orient="vertical",
            bg=PANEL_BORDER,
            bd=0,
            sashwidth=7,
            sashrelief="flat",
        )
        self.main_panes.pack(fill="both", expand=True)

        center_panes = tk.Frame(self.main_panes, bg=BG_MAIN)
        center_panes.columnconfigure(0, weight=6)
        center_panes.columnconfigure(1, weight=4)
        center_panes.rowconfigure(0, weight=1)

        # SOL: Üretim Parametreleri
        self._build_form_panel(center_panes)

        # SAĞ: API Sağlayıcıları, Firebase, Algoritma Kaynağı
        self._build_sidebar_panel(center_panes)

        # 3. ALT BÖLÜM: Üretim Akışı ve terminal
        activity_host = tk.Frame(self.main_panes, bg=BG_MAIN)
        self.main_panes.add(center_panes, minsize=330, stretch="always")
        self.main_panes.add(activity_host, minsize=300, stretch="always")
        self._build_activity_panel(activity_host)

    def _apply_initial_layout(self) -> None:
        """Pencereyi büyütür ve terminale başlangıçta ekranın yaklaşık %42'sini ayırır."""
        try:
            self.state("zoomed")
        except tk.TclError:
            pass

        self.update_idletasks()
        height = self.main_panes.winfo_height()
        if height > 1:
            self.main_panes.sash_place(0, 0, int(height * 0.58))

        width = self.activity_panes.winfo_width()
        if width > 1:
            # Olay listesi %36, asıl terminal %64 genişlikle başlar.
            self.activity_panes.sash_place(0, int(width * 0.36), 0)

    def _build_action_bar(self, parent: tk.Widget) -> None:
        action_bar = tk.Frame(
            parent,
            bg=PANEL_INNER,
            highlightbackground=BORDER_ACCENT,
            highlightthickness=1,
            padx=12,
            pady=7,
        )
        action_bar.pack(fill="x", pady=(0, 12))

        self.form_error_lbl = tk.Label(
            action_bar,
            text="",
            fg=RED,
            bg=PANEL_INNER,
            font=(FONT_FAMILY, 9, "bold"),
        )
        self.form_error_lbl.pack(side="left", fill="x", expand=True, anchor="w")

        self.btn_start = tk.Button(
            action_bar,
            text="Üretimi Başlat  →",
            bg=CYAN,
            fg=TEXT_DARK,
            activebackground="#7feaff",
            activeforeground=TEXT_DARK,
            font=(FONT_FAMILY, 10, "bold"),
            relief="flat",
            cursor="hand2",
            padx=22,
            pady=7,
            command=self._on_start_clicked,
        )
        self.btn_start.pack(side="right")

        self.btn_stop = tk.Button(
            action_bar,
            text="Üretimi Durdur",
            bg=RED,
            fg=TEXT_LIGHT,
            activebackground="#ff8ba0",
            font=(FONT_FAMILY, 10, "bold"),
            relief="flat",
            cursor="hand2",
            padx=22,
            pady=7,
            command=self._on_stop_clicked,
        )

    def _build_form_panel(self, parent: tk.Widget) -> None:
        card = tk.Frame(parent, bg=PANEL_BG, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=20, pady=16)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        # Panel Başlığı
        head = tk.Frame(card, bg=PANEL_BG)
        head.pack(fill="x", pady=(0, 12))

        left_title = tk.Frame(head, bg=PANEL_BG)
        left_title.pack(side="left")
        tk.Label(left_title, text="YENİ ÜRETİM", fg=MUTED, bg=PANEL_BG, font=(FONT_FAMILY, 8, "bold")).pack(anchor="w")
        tk.Label(left_title, text="Üretim parametreleri", fg=TEXT_LIGHT, bg=PANEL_BG, font=(FONT_FAMILY, 13, "bold")).pack(anchor="w")

        # Sağ Üst Kredi Havuzu Rozeti
        badge_box = tk.Frame(head, bg=PANEL_INNER, highlightbackground=BORDER_ACCENT, highlightthickness=1, padx=10, pady=4)
        badge_box.pack(side="right")
        self.pool_badge_val = tk.Label(badge_box, text="154", fg=CYAN, bg=PANEL_INNER, font=(FONT_FAMILY, 12, "bold"))
        self.pool_badge_val.pack()
        self.pool_badge_sub = tk.Label(badge_box, text="kredi havuzu", fg=MUTED, bg=PANEL_INNER, font=(FONT_FAMILY, 7))
        self.pool_badge_sub.pack()

        # Üretim Modu Seçimi
        mode_label = tk.Label(card, text="Üretim modu", fg=TEXT_DIM, bg=PANEL_BG, font=(FONT_FAMILY, 9, "bold"))
        mode_label.pack(anchor="w", pady=(0, 4))

        mode_frame = tk.Frame(card, bg=PANEL_BG)
        mode_frame.pack(fill="x", pady=(0, 10))
        self.var_mode = tk.StringVar(value="154")

        modes = [
            ("154", "154 kredi\nTam dağılım"),
            ("64", "64 kredi\nKısa seri"),
            ("basic", "Temel\nYalnız temel hedefler"),
        ]
        for val, lbl in modes:
            rb = ttk.Radiobutton(
                mode_frame,
                text=lbl,
                value=val,
                variable=self.var_mode,
                command=self._on_mode_change,
            )
            rb.pack(side="left", expand=True, fill="x", padx=4)

        # Soru Adedi
        count_frame = tk.Frame(card, bg=PANEL_BG)
        count_frame.pack(fill="x", pady=(0, 10))
        tk.Label(count_frame, text="Soru adedi", fg=TEXT_DIM, bg=PANEL_BG, font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(0, 3))

        self.var_count = tk.IntVar(value=154)
        self.spin_count = ttk.Spinbox(count_frame, from_=1, to=10000, textvariable=self.var_count, width=15)
        self.spin_count.pack(fill="x")

        # Ülke Profili Kartı
        cp_card = tk.Frame(card, bg=PANEL_INNER, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=12, pady=10)
        cp_card.pack(fill="x", pady=(0, 10))

        self.var_cp_enabled = tk.BooleanVar(value=False)
        cp_check = ttk.Checkbutton(
            cp_card,
            text="Ülke profili (İsteğe bağlı yerelleştirme matrisi)",
            variable=self.var_cp_enabled,
            command=self._toggle_country_profile,
        )
        cp_check.pack(anchor="w")

        self.country_select_frame = tk.Frame(cp_card, bg=PANEL_INNER)
        tk.Label(self.country_select_frame, text="Profil", fg=TEXT_DIM, bg=PANEL_INNER, font=(FONT_FAMILY, 8, "bold")).pack(anchor="w", pady=(6, 2))
        self.combo_country = ttk.Combobox(self.country_select_frame, state="disabled", values=[])
        self.combo_country.pack(fill="x")

        # Gelişmiş Ayarlar (Collapsible Frame)
        adv_card = tk.Frame(card, bg=PANEL_INNER, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=12, pady=10)
        adv_card.pack(fill="x", pady=(0, 10))

        adv_top = tk.Frame(adv_card, bg=PANEL_INNER)
        adv_top.pack(fill="x")
        self.var_adv_expanded = tk.BooleanVar(value=True)

        tk.Label(adv_top, text="Gelişmiş ayarlar", fg=TEXT_DIM, bg=PANEL_INNER, font=(FONT_FAMILY, 9, "bold")).pack(side="left")
        tk.Label(adv_top, text="İSTEĞE BAĞLI", fg=MUTED, bg=PANEL_INNER, font=(FONT_FAMILY, 7, "bold")).pack(side="right")

        self.adv_content = tk.Frame(adv_card, bg=PANEL_INNER)
        self.adv_content.pack(fill="x", pady=(8, 0))

        # Zorluk
        tk.Label(self.adv_content, text="Zorluk", fg=TEXT_DIM, bg=PANEL_INNER, font=(FONT_FAMILY, 8, "bold")).pack(anchor="w", pady=(0, 2))
        self.difficulty_map = {
            "4–4–2 otomatik dengesi": "",
            "Yalnız Kolay · 1–4": "Kolay",
            "Yalnız Orta · 5–8": "Orta",
            "Yalnız Zor · 9–10": "Zor",
        }
        self.combo_difficulty = ttk.Combobox(self.adv_content, state="readonly", values=list(self.difficulty_map.keys()))
        self.combo_difficulty.set("4–4–2 otomatik dengesi")
        self.combo_difficulty.pack(fill="x", pady=(0, 8))

        # Diller
        tk.Label(self.adv_content, text="Diller", fg=TEXT_DIM, bg=PANEL_INNER, font=(FONT_FAMILY, 8, "bold")).pack(anchor="w", pady=(0, 3))
        lang_frame = tk.Frame(self.adv_content, bg=PANEL_INNER)
        lang_frame.pack(fill="x", pady=(0, 8))

        self.lang_vars: Dict[str, tk.BooleanVar] = {}
        languages_info = [("tr", "Türkçe"), ("en", "English"), ("es", "Español"), ("pt", "Português"), ("de", "Deutsch")]
        for code, label in languages_info:
            bvar = tk.BooleanVar(value=True)
            self.lang_vars[code] = bvar
            chk = ttk.Checkbutton(lang_frame, text=f"{label} ({code})", variable=bvar)
            chk.pack(side="left", padx=(0, 10))

        # Combo Yüzdesi Slider
        combo_top = tk.Frame(self.adv_content, bg=PANEL_INNER)
        combo_top.pack(fill="x")
        tk.Label(combo_top, text="Combo soru oranı", fg=TEXT_DIM, bg=PANEL_INNER, font=(FONT_FAMILY, 8, "bold")).pack(side="left")
        self.combo_pct_lbl = tk.Label(combo_top, text="%40", fg=CYAN, bg=PANEL_INNER, font=(FONT_FAMILY, 9, "bold"))
        self.combo_pct_lbl.pack(side="right")

        self.scale_combo = ttk.Scale(
            self.adv_content,
            from_=0,
            to=100,
            orient="horizontal",
            value=40,
            command=self._on_combo_scale_change,
        )
        self.scale_combo.pack(fill="x", pady=(4, 2))

        combo_sublabels = tk.Frame(self.adv_content, bg=PANEL_INNER)
        combo_sublabels.pack(fill="x")
        tk.Label(combo_sublabels, text="Tek kategori", fg=MUTED, bg=PANEL_INNER, font=(FONT_FAMILY, 7)).pack(side="left")
        tk.Label(combo_sublabels, text="Dengeli", fg=MUTED, bg=PANEL_INNER, font=(FONT_FAMILY, 7)).pack(side="left", expand=True)
        tk.Label(combo_sublabels, text="Yoğun combo", fg=MUTED, bg=PANEL_INNER, font=(FONT_FAMILY, 7)).pack(side="right")

        # Bilgilendirme Notları
        rule_box = tk.Frame(card, bg=PANEL_BG)
        rule_box.pack(fill="x", pady=(4, 4))
        tk.Label(rule_box, text="⌁", fg=CYAN, bg=PANEL_BG, font=(FONT_FAMILY, 11)).pack(side="left", anchor="n", padx=(0, 6))
        tk.Label(
            rule_box,
            text="Kategori ve alt kategori algoritma tarafından seçilir. Süre içerik uzunluğu ve zorluğa göre otomatik hesaplanır.",
            fg=MUTED,
            bg=PANEL_BG,
            font=(FONT_FAMILY, 8),
            wraplength=520,
            justify="left",
        ).pack(side="left", anchor="w")

        deliv_box = tk.Frame(card, bg="#0d1f23", highlightbackground="#174843", highlightthickness=1, padx=10, pady=7)
        deliv_box.pack(fill="x", pady=(0, 10))
        tk.Label(deliv_box, text="↗ Anında teslim:", fg=GREEN, bg="#0d1f23", font=(FONT_FAMILY, 8, "bold")).pack(anchor="w")
        tk.Label(
            deliv_box,
            text="Her başarılı soru tekrar kontrolünden sonra Firestore questions koleksiyonuna yazılır.",
            fg=MUTED,
            bg="#0d1f23",
            font=(FONT_FAMILY, 7),
        ).pack(anchor="w")

    def _build_sidebar_panel(self, parent: tk.Widget) -> None:
        sidebar = tk.Frame(parent, bg=BG_MAIN)
        sidebar.grid(row=0, column=1, sticky="nsew")

        # 1. Kart: API Sağlayıcıları (Live Capacity)
        prov_card = tk.Frame(sidebar, bg=PANEL_BG, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=16, pady=12)
        prov_card.pack(fill="x", pady=(0, 10))

        p_head = tk.Frame(prov_card, bg=PANEL_BG)
        p_head.pack(fill="x", pady=(0, 8))

        p_title = tk.Frame(p_head, bg=PANEL_BG)
        p_title.pack(side="left")
        tk.Label(p_title, text="CANLI KAPASİTE", fg=MUTED, bg=PANEL_BG, font=(FONT_FAMILY, 8, "bold")).pack(anchor="w")
        tk.Label(p_title, text="API sağlayıcıları", fg=TEXT_LIGHT, bg=PANEL_BG, font=(FONT_FAMILY, 11, "bold")).pack(anchor="w")

        self.btn_probe = tk.Button(
            p_head,
            text="↻ Canlı Kontrol",
            bg=PANEL_INNER,
            fg=CYAN,
            activebackground=PANEL_BORDER,
            font=(FONT_FAMILY, 8, "bold"),
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=3,
            command=self._probe_providers_async,
        )
        self.btn_probe.pack(side="right")

        # Sağlayıcı kartları listesi konteyneri
        self.providers_container = tk.Frame(prov_card, bg=PANEL_BG)
        self.providers_container.pack(fill="x", pady=(0, 6))

        tk.Label(
            prov_card,
            text="Kesin kalan değer sağlayıcı başlığı döndüğünde gösterilir; diğerleri oturum bazlı tahmindir.",
            fg=MUTED,
            bg=PANEL_BG,
            font=(FONT_FAMILY, 7),
            wraplength=380,
            justify="left",
        ).pack(anchor="w")

        # 2. Kart: Firebase questions
        fb_card = tk.Frame(sidebar, bg=PANEL_BG, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=16, pady=12)
        fb_card.pack(fill="x", pady=(0, 10))

        fb_head = tk.Frame(fb_card, bg=PANEL_BG)
        fb_head.pack(fill="x", pady=(0, 8))

        fb_title = tk.Frame(fb_head, bg=PANEL_BG)
        fb_title.pack(side="left")
        tk.Label(fb_title, text="FIREBASE", fg=MUTED, bg=PANEL_BG, font=(FONT_FAMILY, 8, "bold")).pack(anchor="w")
        tk.Label(fb_title, text="◆ questions", fg=TEXT_LIGHT, bg=PANEL_BG, font=(FONT_FAMILY, 11, "bold")).pack(anchor="w")

        self.fb_health_badge = tk.Label(fb_head, text="KONTROL EDİLİYOR", fg=ORANGE, bg=PANEL_INNER, font=(FONT_FAMILY, 8, "bold"), padx=8, pady=3)
        self.fb_health_badge.pack(side="right")

        fb_metrics = tk.Frame(fb_card, bg=PANEL_INNER, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=12, pady=8)
        fb_metrics.pack(fill="x", pady=(0, 6))

        self.fb_count_lbl = tk.Label(fb_metrics, text="—", fg=TEXT_LIGHT, bg=PANEL_INNER, font=(FONT_FAMILY, 14, "bold"))
        self.fb_count_lbl.pack(anchor="w")
        tk.Label(fb_metrics, text="mevcut soru · europe-west3", fg=MUTED, bg=PANEL_INNER, font=(FONT_FAMILY, 8)).pack(anchor="w")

        tk.Label(fb_card, text="knowley-1 / (default) · Firestore Native", fg=MUTED, bg=PANEL_BG, font=(FONT_FAMILY, 7)).pack(anchor="w")

        # 3. Kart: Algoritma Kaynağı
        algo_card = tk.Frame(sidebar, bg=PANEL_BG, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=16, pady=12)
        algo_card.pack(fill="x")

        tk.Label(algo_card, text="ALGORİTMA KAYNAĞI", fg=MUTED, bg=PANEL_BG, font=(FONT_FAMILY, 8, "bold")).pack(anchor="w")
        self.algo_source_title = tk.Label(algo_card, text="Google Sheets · v—", fg=TEXT_LIGHT, bg=PANEL_BG, font=(FONT_FAMILY, 11, "bold"))
        self.algo_source_title.pack(anchor="w", pady=(0, 4))

        self.algo_stats_lbl = tk.Label(algo_card, text="— kategori  ·  5 ülke profili  ·  — alt hedef", fg=TEXT_DIM, bg=PANEL_BG, font=(FONT_FAMILY, 8))
        self.algo_stats_lbl.pack(anchor="w", pady=(0, 6))

        self.sheet_url: Optional[str] = None
        self.btn_open_sheet = tk.Button(
            algo_card,
            text="Çalışma sayfasını aç  ↗",
            bg=PANEL_INNER,
            fg=CYAN,
            activebackground=PANEL_BORDER,
            font=(FONT_FAMILY, 8, "bold"),
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=4,
            command=self._open_sheet_url,
        )
        self.btn_open_sheet.pack(anchor="w")

    def _build_activity_panel(self, parent: tk.Widget) -> None:
        act_card = tk.Frame(parent, bg=PANEL_BG, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=20, pady=12)
        act_card.pack(fill="both", expand=True)

        act_head = tk.Frame(act_card, bg=PANEL_BG)
        act_head.pack(fill="x", pady=(0, 8))

        act_left = tk.Frame(act_head, bg=PANEL_BG)
        act_left.pack(side="left")
        tk.Label(act_left, text="ÜRETİM AKIŞI", fg=MUTED, bg=PANEL_BG, font=(FONT_FAMILY, 8, "bold")).pack(anchor="w")
        self.activity_title_lbl = tk.Label(act_left, text="Yeni iş bekleniyor", fg=TEXT_LIGHT, bg=PANEL_BG, font=(FONT_FAMILY, 11, "bold"))
        self.activity_title_lbl.pack(anchor="w")

        act_right = tk.Frame(act_head, bg=PANEL_BG)
        act_right.pack(side="right")
        self.progress_count_lbl = tk.Label(act_right, text="0 / 0", fg=TEXT_LIGHT, bg=PANEL_BG, font=(FONT_FAMILY, 12, "bold"))
        self.progress_count_lbl.pack(anchor="e")
        self.progress_state_lbl = tk.Label(act_right, text="HAZIR", fg=CYAN, bg=PANEL_BG, font=(FONT_FAMILY, 8, "bold"))
        self.progress_state_lbl.pack(anchor="e")

        # İlerleme Çubuğu
        self.progress_bar = SmoothProgressBar(act_card, height=6)
        self.progress_bar.pack(fill="x", pady=(0, 10))

        # Canlı olay akışı ve gerçek uygulama terminali her zaman yan yana görünür.
        self.activity_panes = tk.PanedWindow(
            act_card,
            orient="horizontal",
            bg=PANEL_BORDER,
            bd=0,
            sashwidth=5,
            sashrelief="flat",
        )
        self.activity_panes.pack(fill="both", expand=True)

        events_panel = tk.Frame(self.activity_panes, bg="#090c1a")
        terminal_panel = tk.Frame(self.activity_panes, bg="#050711")
        self.activity_panes.add(events_panel, minsize=280, stretch="always")
        self.activity_panes.add(terminal_panel, minsize=520, stretch="always")

        events_head = tk.Frame(events_panel, bg="#0d1020", padx=10, pady=5)
        events_head.pack(fill="x")
        tk.Label(events_head, text="CANLI OLAYLAR", fg=PURPLE, bg="#0d1020", font=(FONT_FAMILY, 8, "bold")).pack(side="left")

        list_frame = tk.Frame(events_panel, bg="#090c1a", highlightbackground=PANEL_BORDER, highlightthickness=1)
        list_frame.pack(fill="both", expand=True)

        self.event_text = tk.Text(
            list_frame,
            bg="#090c1a",
            fg=TEXT_LIGHT,
            insertbackground=TEXT_LIGHT,
            font=("Consolas", 9),
            wrap="word",
            relief="flat",
            padx=10,
            pady=8,
        )
        scrollbar = ttk.Scrollbar(list_frame, command=self.event_text.yview)
        self.event_text.configure(yscrollcommand=scrollbar.set)

        self.event_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Renk Etiketleri (Text Tags)
        self.event_text.tag_configure("started", foreground=CYAN, font=("Consolas", 9, "bold"))
        self.event_text.tag_configure("planning", foreground=PURPLE)
        self.event_text.tag_configure("saved", foreground=GREEN, font=("Consolas", 9, "bold"))
        self.event_text.tag_configure("retry", foreground=ORANGE)
        self.event_text.tag_configure("duplicate", foreground=MUTED)
        self.event_text.tag_configure("completed", foreground=GREEN, font=("Consolas", 9, "bold"))
        self.event_text.tag_configure("stopped", foreground=ORANGE, font=("Consolas", 9, "bold"))
        self.event_text.tag_configure("failed", foreground=RED, font=("Consolas", 9, "bold"))
        self.event_text.tag_configure("timestamp", foreground=MUTED)
        self.event_text.tag_configure("meta", foreground=CYAN)

        self.event_text.insert("end", "⌁ Parametrelerini seçip üretimi başlattığında planlama, API ve Firebase adımlarını burada canlı izleyeceksin.\n", "timestamp")
        self.event_text.configure(state="disabled")

        terminal_head = tk.Frame(terminal_panel, bg="#0b0e1b", padx=10, pady=5)
        terminal_head.pack(fill="x")
        tk.Label(terminal_head, text="UYGULAMA TERMİNALİ", fg=CYAN, bg="#0b0e1b", font=(FONT_FAMILY, 8, "bold")).pack(side="left")
        tk.Button(
            terminal_head,
            text="Temizle",
            command=self._clear_terminal,
            bg="#0b0e1b",
            fg=MUTED,
            activebackground=PANEL_INNER,
            activeforeground=TEXT_LIGHT,
            relief="flat",
            cursor="hand2",
            font=(FONT_FAMILY, 8),
        ).pack(side="right")

        terminal_body = tk.Frame(terminal_panel, bg="#050711", highlightbackground=PANEL_BORDER, highlightthickness=1)
        terminal_body.pack(fill="both", expand=True)
        self.terminal_text = tk.Text(
            terminal_body,
            bg="#050711",
            fg="#c9d1d9",
            insertbackground=TEXT_LIGHT,
            font=("Consolas", 8),
            wrap="word",
            relief="flat",
            padx=10,
            pady=8,
            state="disabled",
        )
        terminal_scrollbar = ttk.Scrollbar(terminal_body, command=self.terminal_text.yview)
        self.terminal_text.configure(yscrollcommand=terminal_scrollbar.set)
        self.terminal_text.pack(side="left", fill="both", expand=True)
        terminal_scrollbar.pack(side="right", fill="y")

        self.terminal_text.tag_configure("debug", foreground=MUTED)
        self.terminal_text.tag_configure("info", foreground="#c9d1d9")
        self.terminal_text.tag_configure("warning", foreground=ORANGE)
        self.terminal_text.tag_configure("error", foreground=RED)
        self.terminal_text.tag_configure("critical", foreground=RED, font=("Consolas", 8, "bold"))
        self.terminal_text.tag_configure("stdout", foreground=TEXT_DIM)
        self.terminal_text.tag_configure("stderr", foreground=RED)

    def _install_console_capture(self) -> None:
        """Logging ile stdout/stderr çıktılarını ekrandaki terminale bağlar."""
        handler = TkLogHandler(self.console_queue)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        self._tk_log_handler = handler

        sys.stdout = TkConsoleStream(self.console_queue, "stdout")
        sys.stderr = TkConsoleStream(self.console_queue, "stderr")
        self.console_queue.put(("Knowley terminali hazır. Tüm uygulama logları burada görünecek.\n", "info"))

    def _poll_console_queue(self) -> None:
        """Arka plan thread'lerinden gelen terminal satırlarını Tk ana thread'inde çizer."""
        chunks: List[tuple[str, str]] = []
        try:
            while len(chunks) < 500:
                chunks.append(self.console_queue.get_nowait())
        except queue.Empty:
            pass

        if chunks:
            self.terminal_text.configure(state="normal")
            for text, tag in chunks:
                self.terminal_text.insert("end", text, tag)
            # Uzun oturumlarda widget'ın sınırsız büyümesini engelle.
            line_count = int(self.terminal_text.index("end-1c").split(".")[0])
            if line_count > 5000:
                self.terminal_text.delete("1.0", f"{line_count - 4000}.0")
            self.terminal_text.see("end")
            self.terminal_text.configure(state="disabled")

        self.after(100, self._poll_console_queue)

    def _clear_terminal(self) -> None:
        self.terminal_text.configure(state="normal")
        self.terminal_text.delete("1.0", "end")
        self.terminal_text.configure(state="disabled")

    def _on_close(self) -> None:
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
        if self._tk_log_handler is not None:
            logging.getLogger().removeHandler(self._tk_log_handler)
            self._tk_log_handler.close()
        self.destroy()

    # ----------------------------------------------------
    # Olay ve Veri Yönetimi
    # ----------------------------------------------------

    def _on_mode_change(self) -> None:
        mode = self.var_mode.get()
        if mode == "154":
            self.var_count.set(154)
            self.pool_badge_val.config(text="154")
            self.pool_badge_sub.config(text="kredi havuzu")
        elif mode == "64":
            self.var_count.set(64)
            self.pool_badge_val.config(text="64")
            self.pool_badge_sub.config(text="kredi havuzu")
        elif mode == "basic":
            self.var_count.set(21)
            self.pool_badge_val.config(text="21")
            self.pool_badge_sub.config(text="temel hedef havuzu")

    def _on_combo_scale_change(self, value: str) -> None:
        ival = int(float(value))
        self.combo_pct_lbl.config(text=f"%{ival}")

    def _toggle_country_profile(self) -> None:
        enabled = self.var_cp_enabled.get()
        if enabled:
            self.country_select_frame.pack(fill="x", pady=(6, 0))
            self.combo_country.config(state="readonly")
        else:
            self.country_select_frame.pack_forget()
            self.combo_country.config(state="disabled")

    def _open_sheet_url(self) -> None:
        if self.sheet_url:
            webbrowser.open(self.sheet_url)

    def _load_initial_data(self) -> None:
        """Arka planda sistem konfigürasyonunu yükler."""
        def worker() -> None:
            try:
                repo = self.manager.repository
                algo_summary = repo.summary()
                countries = repo.country_profiles()
                providers = self.manager.llm_manager.status(live=False)

                data = {
                    "algorithm": algo_summary,
                    "countries": countries,
                    "providers": providers,
                }
                self.after(0, lambda: self._apply_config(data))
            except Exception as exc:
                print(f"[Tkinter] Config yükleme hatası: {exc}")

            # Firebase kontrolü
            self._load_firebase_async()
            # Canlı sağlayıcı kontrolü
            self._probe_providers_async()

        threading.Thread(target=worker, daemon=True).start()

    def _apply_config(self, data: Dict[str, Any]) -> None:
        self.config_data = data
        summary = data["algorithm"]
        version = summary["source"]["version"]

        self.pill_algorithm.set_text(f"Sheets v{version}", CYAN)
        self.algo_source_title.config(text=f"Google Sheets · v{version}")
        self.sheet_url = summary["source"].get("url")

        cat_count = summary.get("category_count", "—")
        country_count = len(data.get("countries", []))
        subcat_count = summary.get("generic_subcategory_count", "—")
        self.algo_stats_lbl.config(text=f"{cat_count} kategori  ·  {country_count} ülke profili  ·  {subcat_count} alt hedef")

        country_names = [c["name"] for c in data.get("countries", [])]
        self.combo_country.config(values=country_names)
        if country_names:
            self.combo_country.current(0)

        self._on_mode_change()
        self._render_providers(data.get("providers", {}))

        # Devam eden iş varsa izlemeye başla
        latest = self.manager.latest()
        if latest and latest.get("status") in {"starting", "running", "stopping"}:
            self.watch_job(latest["id"])

    def _load_firebase_async(self) -> None:
        def worker() -> None:
            fb = firebase_summary(live=True)
            self.after(0, lambda: self._apply_firebase(fb))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_firebase(self, fb: Dict[str, Any]) -> None:
        reachable = fb.get("reachable")
        count = fb.get("question_count")
        if reachable:
            self.pill_firebase.set_text("Firestore bağlı", GREEN)
            self.fb_health_badge.config(text="CANLI", fg=GREEN)
            count_str = f"{count:,}".replace(",", ".") if count is not None else "—"
            self.fb_count_lbl.config(text=count_str)
        else:
            self.pill_firebase.set_text("Firestore erişilemiyor", RED)
            self.fb_health_badge.config(text="ERİŞİLEMİYOR", fg=RED)
            self.fb_count_lbl.config(text="—")

    def _probe_providers_async(self) -> None:
        self.btn_probe.config(text="…", state="disabled")

        def worker() -> None:
            try:
                providers = self.manager.llm_manager.status(live=True)
                self.after(0, lambda: self._render_providers(providers))
            except Exception as exc:
                print(f"[Tkinter] Provider probe error: {exc}")
            finally:
                self.after(0, lambda: self.btn_probe.config(text="↻ Canlı Kontrol", state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def _render_providers(self, providers: Dict[str, Any]) -> None:
        for widget in self.providers_container.winfo_children():
            widget.destroy()

        for name, item in providers.items():
            live = item.get("live")
            configured = item.get("configured", False)
            reachable = live.get("reachable") if live else None
            ready = configured and (live is None or reachable)

            if not configured:
                state_lbl = "PASİF"
                state_color = MUTED
            elif live is None:
                state_lbl = "KONTROL"
                state_color = ORANGE
            elif ready:
                state_lbl = "HAZIR"
                state_color = GREEN
            else:
                state_lbl = "ERİŞİLEMİYOR"
                state_color = RED

            remaining = item.get("remaining_questions")
            suffix = "kesin kalan" if item.get("remaining_is_exact") else "tahmini kalan"
            rem_str = f"{remaining:,}".replace(",", ".") if remaining is not None else "—"
            used = (item.get("session_usage") or {}).get("requests", 0)

            models = (live.get("available_models") if isinstance(live, dict) else None) or item.get("models", [])
            model_name = models[0] if models else "Model bulunamadı"

            card = tk.Frame(self.providers_container, bg=PANEL_INNER, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=10, pady=7)
            card.pack(fill="x", pady=2)

            top = tk.Frame(card, bg=PANEL_INNER)
            top.pack(fill="x")

            name_box = tk.Frame(top, bg=PANEL_INNER)
            name_box.pack(side="left")
            orb = tk.Label(name_box, text=f" {name[0].upper()} ", fg=CYAN, bg="#0a1226", font=(FONT_FAMILY, 8, "bold"))
            orb.pack(side="left", padx=(0, 6))
            tk.Label(name_box, text=name, fg=TEXT_LIGHT, bg=PANEL_INNER, font=(FONT_FAMILY, 9, "bold")).pack(side="left")

            tk.Label(top, text=state_lbl, fg=state_color, bg=PANEL_INNER, font=(FONT_FAMILY, 8, "bold")).pack(side="right")

            metrics = tk.Frame(card, bg=PANEL_INNER)
            metrics.pack(fill="x", pady=(4, 2))
            tk.Label(metrics, text=f"{rem_str} {suffix}", fg=TEXT_DIM, bg=PANEL_INNER, font=(FONT_FAMILY, 8)).pack(side="left")
            tk.Label(metrics, text=f"{used} çağrı", fg=MUTED, bg=PANEL_INNER, font=(FONT_FAMILY, 8)).pack(side="right")

            tk.Label(card, text=model_name, fg=MUTED, bg=PANEL_INNER, font=("Consolas", 7)).pack(anchor="w")

    # ----------------------------------------------------
    # İş Başlatma & Durdurma
    # ----------------------------------------------------

    def _collect_payload(self) -> Dict[str, Any]:
        count = self.var_count.get()
        if count < 1:
            raise ValueError("Soru adedi en az 1 olmalıdır.")

        selected_langs = [code for code, var in self.lang_vars.items() if var.get()]
        if not selected_langs:
            raise ValueError("En az bir dil seçilmelidir.")

        country_enabled = self.var_cp_enabled.get()
        country_val = self.combo_country.get() if country_enabled else None
        if country_enabled and not country_val:
            raise ValueError("Ülke profili etkinleştirildiğinde bir profil seçilmelidir.")

        diff_selected = self.combo_difficulty.get()
        difficulty = self.difficulty_map.get(diff_selected, "")

        return {
            "question_count": count,
            "generation_mode": self.var_mode.get(),
            "difficulty": difficulty,
            "country_profile_enabled": country_enabled,
            "country": country_val or "",
            "combo_percent": int(self.scale_combo.get()),
            "languages": selected_langs,
        }

    def _on_start_clicked(self) -> None:
        self.form_error_lbl.config(text="")
        try:
            payload = self._collect_payload()
        except ValueError as val_err:
            self.form_error_lbl.config(text=str(val_err))
            return

        self.btn_start.config(state="disabled")

        def starter() -> None:
            try:
                job_data = self.manager.start(payload)
                self.after(0, lambda: self.watch_job(job_data["id"]))
            except Exception as exc:
                self.after(0, lambda: self._on_start_failed(str(exc)))

        threading.Thread(target=starter, daemon=True).start()

    def _on_start_failed(self, error_msg: str) -> None:
        self.form_error_lbl.config(text=error_msg)
        self.btn_start.config(state="normal")

    def _on_stop_clicked(self) -> None:
        if not self.active_job_id:
            return
        self.btn_stop.config(state="disabled", text="Durduruluyor…")
        try:
            self.manager.stop(self.active_job_id)
        except Exception as exc:
            messagebox.showerror("Hata", f"Durdurma isteği başarısız: {exc}")

    def watch_job(self, job_id: str) -> None:
        self.active_job_id = job_id
        self.known_event_count = 0
        self.btn_start.config(state="disabled")
        self.btn_stop.pack(side="right", padx=(8, 0))
        self.btn_stop.config(state="normal", text="Üretimi Güvenli Biçimde Durdur")

        # Metin alanını temizle
        self.event_text.configure(state="normal")
        self.event_text.delete("1.0", "end")
        self.event_text.configure(state="disabled")

    def _poll_job_cycle(self) -> None:
        if self.active_job_id:
            try:
                job = self.manager.get(self.active_job_id)
                self._render_job(job)
            except KeyError:
                self.active_job_id = None
            except Exception as exc:
                print(f"[Tkinter] Poll error: {exc}")

        self.after(800, self._poll_job_cycle)

    def _render_job(self, job: Optional[Dict[str, Any]]) -> None:
        if not job or not isinstance(job, dict):
            return

        status = str(job.get("status") or "unknown")
        options = job.get("options") or {}
        summary = job.get("summary") or {}
        events = job.get("events") or []

        total = options.get("question_count") or 0
        saved_events = [e for e in events if isinstance(e, dict) and e.get("event") == "saved"]

        completed_count = summary.get("processed")
        if completed_count is None:
            indices = [e.get("index", 0) for e in events if isinstance(e, dict) and isinstance(e.get("index"), (int, float))]
            completed_count = max([0] + indices)

        pct = (completed_count / total * 100) if total else 0.0
        self.progress_bar.set_progress(pct)
        self.progress_count_lbl.config(text=f"{len(saved_events)} / {total}")
        self.progress_state_lbl.config(text=status.upper())

        if status in {"completed", "stopped", "failed"}:
            self.activity_title_lbl.config(text="Son üretim özeti")
            self.btn_start.config(state="normal")
            self.btn_stop.pack_forget()
            # Firebase ve sağlayıcıları güncelle
            self._load_firebase_async()
            if summary.get("provider_status"):
                self._render_providers(summary["provider_status"])
        else:
            self.activity_title_lbl.config(text="Üretim devam ediyor")
            self.btn_start.config(state="disabled")
            self.btn_stop.pack(side="right", padx=(8, 0))

        # Yeni gelen olayları ekle
        if len(events) > self.known_event_count:
            new_events = events[self.known_event_count :]
            self.known_event_count = len(events)
            self._append_events(new_events)

    def _append_events(self, events: List[Dict[str, Any]]) -> None:
        icons = {
            "started": "▶",
            "planning": "⌁",
            "saved": "✓",
            "retry": "↻",
            "duplicate": "≋",
            "completed": "◆",
            "stopped": "■",
            "failed": "!",
        }

        self.event_text.configure(state="normal")
        for ev in events:
            kind = ev.get("event", "info")
            icon = icons.get(kind, "·")
            ts = ev.get("timestamp", 0)
            time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""
            provider = ev.get("provider", "")

            # Mesaj formatı
            if kind == "planning":
                msg = f"{ev.get('category')} › {ev.get('subcategory')}"
                if ev.get("combo"):
                    msg += f" + {ev.get('secondary_category')}"
            elif kind == "saved":
                q_text = ev.get("question", "")
                if len(q_text) > 90:
                    q_text = q_text[:87] + "..."
                msg = q_text or "Soru Firestore'a yazıldı."
            elif kind == "retry":
                msg = f"{ev.get('message', 'API yeniden deneniyor')} · {ev.get('wait_seconds', '')}s"
            elif kind == "duplicate":
                msg = f"{ev.get('category')} için tekrar soru atlandı."
            elif kind == "completed":
                msg = f"{ev.get('produced')} soru başarıyla tamamlandı."
            elif kind == "stopped":
                msg = "Üretim güvenli biçimde durduruldu."
            elif kind == "failed":
                msg = ev.get("message", "Üretim başarısız oldu.")
            else:
                msg = f"Sheets v{ev.get('algorithm_version', '')} ile üretim başladı."

            # Listeye renkli satır ekle
            self.event_text.insert("end", f"[{time_str}] ", "timestamp")
            self.event_text.insert("end", f"{icon} {kind.upper():<10} ", kind)
            self.event_text.insert("end", f"{msg} ")
            if provider:
                self.event_text.insert("end", f"({provider})", "meta")
            self.event_text.insert("end", "\n")

        self.event_text.see("end")
        self.event_text.configure(state="disabled")


def main() -> None:
    app = KnowleyTkinterApp()
    app.mainloop()


if __name__ == "__main__":
    main()
