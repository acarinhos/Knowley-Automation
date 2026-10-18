"""Knowley Üretim Kontrol Odası - Bağımsız Tkinter Masaüstü Arayüzü."""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, ttk, messagebox

# Windows'ta `python ui_tkinter.py` farklı bir Python'a çözülse bile kurulu
# bağımlılıkların bulunduğu proje sanal ortamını kullan.
if __name__ == "__main__":
    venv_python = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
    if venv_python.is_file() and Path(sys.executable).resolve() != venv_python.resolve():
        raise SystemExit(subprocess.call([str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]))

# High DPI netliği (Windows)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# Mevcut arka uç modülleri
from core.generator import QUESTION_SCHEMA_VERSION
from core.translation_service import SUPPORTED_LANGUAGE_CODES
from core.external_importer import (
    DEFAULT_SHARED_SOURCE,
    ImportReport,
    load_source,
    save_report,
    validate_and_transform,
)
from database.db_manager import init_firebase, save_question_to_firestore
from database.question_report import canonicalize_question_report_countries, question_report_country_label
from ui_server import JobManager, firebase_question_audit, firebase_question_report, firebase_summary
try:  # optional SAT generation panel
    from sat_engine.ui_panel import install as install_sat_panel
except Exception:
    install_sat_panel = None

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


class ScrollablePanel(tk.Frame):
    """Üst panel sütunlarını sabit yükseklikte tutup içeriği kaydırır."""

    def __init__(self, parent: tk.Widget, background: str = BG_MAIN) -> None:
        super().__init__(parent, bg=background)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(self, bg=background, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")

        self.content = tk.Frame(self.canvas, bg=background)
        self.content_window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_content)

    def _update_scroll_region(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_content(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.content_window, width=event.width)


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
        self.last_event_sequence = 0
        self.console_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self._tk_log_handler: Optional[TkLogHandler] = None
        self.external_raw_records: List[Dict[str, Any]] = []
        self.external_report: Optional[ImportReport] = None
        self.external_window: Optional[tk.Toplevel] = None

        self._configure_styles()
        self._build_ui()
        if install_sat_panel:
            try:
                install_sat_panel(self)
            except Exception:  # never crash app start
                logging.getLogger(__name__).exception("SAT panel could not be installed")
        self.bind_all("<MouseWheel>", self._scroll_panel_under_pointer, add="+")
        self.bind_all("<Button-4>", self._scroll_panel_under_pointer, add="+")
        self.bind_all("<Button-5>", self._scroll_panel_under_pointer, add="+")
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

        # Dış veri önizleme tablosu: Windows'un açık renk Treeview varsayılanı
        # koyu uygulamada beyaz zemin/beyaz yazı üretiyordu.
        style.configure(
            "External.Treeview",
            background="#0b1022",
            fieldbackground="#0b1022",
            foreground=TEXT_LIGHT,
            rowheight=25,
            bordercolor=PANEL_BORDER,
            lightcolor=PANEL_BORDER,
            darkcolor=PANEL_BORDER,
        )
        style.map(
            "External.Treeview",
            background=[("selected", "#315775")],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "External.Treeview.Heading",
            background="#0d1226",
            foreground=TEXT_LIGHT,
            relief="flat",
            bordercolor=BORDER_ACCENT,
            font=(FONT_FAMILY, 9, "bold"),
        )
        style.map("External.Treeview.Heading", background=[("active", PANEL_INNER)])

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

    def _scroll_panel_under_pointer(self, event: tk.Event) -> Optional[str]:
        """Fare tekerleğini imlecin bulunduğu üst sütuna uygular."""
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            for panel in (self.form_scroll, self.sidebar_scroll):
                if widget is panel:
                    if getattr(event, "num", None) == 4:
                        steps = -1
                    elif getattr(event, "num", None) == 5:
                        steps = 1
                    else:
                        delta = getattr(event, "delta", 0)
                        steps = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
                    panel.canvas.yview_scroll(steps, "units")
                    return "break"
            widget = widget.master
        return None

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

        tk.Button(
            action_bar,
            text="Dış Veri Seti  ⇩",
            bg=PURPLE,
            fg=TEXT_LIGHT,
            activebackground="#a99aff",
            activeforeground=TEXT_LIGHT,
            font=(FONT_FAMILY, 9, "bold"),
            relief="flat",
            cursor="hand2",
            padx=14,
            pady=7,
            command=self._open_external_importer,
        ).pack(side="left", padx=(0, 12))

        tk.Button(
            action_bar,
            text="Firebase Raporu  ↗",
            bg=PANEL_INNER,
            fg=CYAN,
            activebackground=PANEL_BORDER,
            activeforeground=TEXT_LIGHT,
            font=(FONT_FAMILY, 9, "bold"),
            relief="flat",
            cursor="hand2",
            padx=14,
            pady=7,
            command=self._open_firebase_audit,
        ).pack(side="left", padx=(0, 12))

        self.btn_firebase_report = tk.Button(
            action_bar,
            text="Soru Raporlama  ↗",
            bg=PANEL_INNER,
            fg=PURPLE,
            activebackground=PANEL_BORDER,
            activeforeground=TEXT_LIGHT,
            font=(FONT_FAMILY, 9, "bold"),
            relief="flat",
            cursor="hand2",
            padx=14,
            pady=7,
            command=self._open_firebase_question_report,
        )
        self.btn_firebase_report.pack(side="left", padx=(0, 12))

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
        self.form_scroll = ScrollablePanel(parent)
        self.form_scroll.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        card = tk.Frame(self.form_scroll.content, bg=PANEL_BG, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=20, pady=16)
        card.pack(fill="x")

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
        self._last_mode = "154"
        self._country_profile_was_enabled = False
        self._country_profile_selection_before_localized = ""
        self.country_profile_values: List[str] = []
        self.country_local_values: List[str] = []

        modes = [
            ("154", "154 kredi\nTam dağılım"),
            ("64", "64 kredi\nKısa seri"),
            ("basic", "Temel\nYalnız temel hedefler"),
            ("custom", "Özel\nDengeli üretim"),
            ("country_local", "Ülke/Bölge\nYerel hedefler"),
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
        self.count_frame = count_frame
        self.count_label = tk.Label(count_frame, text="Soru adedi", fg=TEXT_DIM, bg=PANEL_BG, font=(FONT_FAMILY, 9, "bold"))
        self.count_label.pack(anchor="w", pady=(0, 3))

        self.var_count = tk.IntVar(value=154)
        self.spin_count = ttk.Spinbox(count_frame, from_=1, to=10000, textvariable=self.var_count, width=15)
        self.spin_count.pack(fill="x")

        self.custom_config_card = tk.Frame(
            card, bg=PANEL_INNER, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=12, pady=10
        )
        tk.Label(
            self.custom_config_card,
            text="Özel üretim seçenekleri",
            fg=TEXT_DIM,
            bg=PANEL_INNER,
            font=(FONT_FAMILY, 9, "bold"),
        ).pack(anchor="w", pady=(0, 5))
        self.var_custom_mode = tk.StringVar(value="equal")
        ttk.Radiobutton(
            self.custom_config_card,
            text="Tüm kategori ve alt kategorileri eşit dağıt (en az 160 soru)",
            value="equal",
            variable=self.var_custom_mode,
            command=self._on_custom_mode_change,
        ).pack(anchor="w", pady=2)
        ttk.Radiobutton(
            self.custom_config_card,
            text="Her 10 soruda zorluk adetlerini belirle",
            value="difficulty",
            variable=self.var_custom_mode,
            command=self._on_custom_mode_change,
        ).pack(anchor="w", pady=2)
        self.equal_mode_hint = tk.Label(
            self.custom_config_card,
            text="Tüm kategori-alt kategori hedefleri eşit ağırlık alır; seçilen ülke profili varsa o profilin hedefleri kullanılır.",
            fg=MUTED,
            bg=PANEL_INNER,
            font=(FONT_FAMILY, 8),
            wraplength=440,
            justify="left",
        )
        self.equal_mode_hint.pack(anchor="w", pady=(3, 0))
        self.difficulty_quota_frame = tk.Frame(self.custom_config_card, bg=PANEL_INNER)
        quota_row = tk.Frame(self.difficulty_quota_frame, bg=PANEL_INNER)
        quota_row.pack(fill="x", pady=(4, 2))
        self.difficulty_count_vars: Dict[str, tk.IntVar] = {}
        for level, default in (("Kolay", 4), ("Orta", 4), ("Zor", 2)):
            field = tk.Frame(quota_row, bg=PANEL_INNER)
            field.pack(side="left", expand=True, fill="x", padx=(0, 7))
            tk.Label(field, text=level, fg=TEXT_DIM, bg=PANEL_INNER, font=(FONT_FAMILY, 8, "bold")).pack(anchor="w")
            var = tk.IntVar(value=default)
            self.difficulty_count_vars[level] = var
            ttk.Spinbox(
                field,
                from_=0,
                to=10,
                increment=1,
                textvariable=var,
                width=6,
                command=self._update_custom_quota_total,
            ).pack(fill="x")
            var.trace_add("write", lambda *_args: self._update_custom_quota_total())
        self.difficulty_quota_total = tk.Label(
            self.difficulty_quota_frame,
            text="Toplam: 10 / 10",
            fg=GREEN,
            bg=PANEL_INNER,
            font=(FONT_FAMILY, 8, "bold"),
        )
        self.difficulty_quota_total.pack(anchor="w", pady=(2, 0))
        tk.Label(
            self.difficulty_quota_frame,
            text="Her blokta adetler toplamı 10 olmalı. Toplam üretim adedi de 10'un katı olmalı.",
            fg=MUTED,
            bg=PANEL_INNER,
            font=(FONT_FAMILY, 8),
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))
        self.custom_config_card.pack_forget()

        # Ülke Profili Kartı
        cp_card = tk.Frame(card, bg=PANEL_INNER, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=12, pady=10)
        cp_card.pack(fill="x", pady=(0, 10))

        self.var_cp_enabled = tk.BooleanVar(value=False)
        self.country_profile_check = ttk.Checkbutton(
            cp_card,
            text="Ülke profili (İsteğe bağlı yerelleştirme matrisi)",
            variable=self.var_cp_enabled,
            command=self._toggle_country_profile,
        )
        self.country_profile_check.pack(anchor="w")

        self.country_select_frame = tk.Frame(cp_card, bg=PANEL_INNER)
        self.country_select_label = tk.Label(self.country_select_frame, text="Profil", fg=TEXT_DIM, bg=PANEL_INNER, font=(FONT_FAMILY, 8, "bold"))
        self.country_select_label.pack(anchor="w", pady=(6, 2))
        self.combo_country = ttk.Combobox(self.country_select_frame, state="disabled", values=[])
        self.combo_country.pack(fill="x")
        self.combo_country.bind("<<ComboboxSelected>>", self._on_localized_country_selected)

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
        self.difficulty_label = tk.Label(self.adv_content, text="Zorluk", fg=TEXT_DIM, bg=PANEL_INNER, font=(FONT_FAMILY, 8, "bold"))
        self.difficulty_label.pack(anchor="w", pady=(0, 2))
        self.difficulty_map = {
            "2–2–1 otomatik dengesi · her 5 soru": "",
        }
        self.combo_difficulty = ttk.Combobox(self.adv_content, state="readonly", values=list(self.difficulty_map.keys()))
        self.combo_difficulty.set("2–2–1 otomatik dengesi · her 5 soru")
        self.combo_difficulty.pack(fill="x", pady=(0, 8))

        # Dil kapsamı
        self.language_scope_label = tk.Label(self.adv_content, text="Dil kapsamı", fg=TEXT_DIM, bg=PANEL_INNER, font=(FONT_FAMILY, 8, "bold"))
        self.language_scope_label.pack(anchor="w", pady=(0, 3))
        tk.Label(
            self.adv_content,
            text="35 aktif dil · Global ve öge soruları tüm dillerde; yerel sorular ülke/bölge satırındaki dillerde üretilir.",
            fg=CYAN,
            bg=PANEL_INNER,
            font=(FONT_FAMILY, 8, "bold"),
            wraplength=340,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

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
        self.sidebar_scroll = ScrollablePanel(parent)
        self.sidebar_scroll.grid(row=0, column=1, sticky="nsew")
        sidebar = tk.Frame(self.sidebar_scroll.content, bg=BG_MAIN)
        sidebar.pack(fill="x")

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
        self.btn_firebase_audit = tk.Button(
            fb_card,
            text="1.1.0+ üretim tablosu  ↗",
            bg=PANEL_INNER,
            fg=CYAN,
            activebackground=PANEL_BORDER,
            font=(FONT_FAMILY, 8, "bold"),
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=4,
            command=self._open_firebase_audit,
        )
        self.btn_firebase_audit.pack(anchor="w", pady=(7, 0))
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
        changed_mode = mode != self._last_mode
        if mode == "country_local":
            if changed_mode:
                self._country_profile_was_enabled = self.var_cp_enabled.get()
                self._country_profile_selection_before_localized = self.combo_country.get()
                self.var_cp_enabled.set(True)
                self.combo_country.set("")
                self.var_count.set(1)
            self.combo_country.configure(values=self.country_local_values, state="readonly")
            self.country_profile_check.configure(state="disabled")
            self.country_profile_check.configure(text="Ülke/bölge seçimi zorunlu · yalnız yerel hedefler")
            self.country_select_label.config(text="Ülke / Bölge")
            self.country_select_frame.pack(fill="x", pady=(6, 0))
            self.spin_count.configure(from_=1, to=10000, increment=1)
            self.pool_badge_val.config(text="YEREL")
            self.pool_badge_sub.config(text="ülke/bölge hedefleri")
            self._update_country_local_minimum()
        elif self._last_mode == "country_local":
            self.country_profile_check.configure(state="normal")
            self.country_profile_check.configure(text="Ülke profili (İsteğe bağlı yerelleştirme matrisi)")
            self.country_select_label.config(text="Profil")
            self.var_cp_enabled.set(self._country_profile_was_enabled)
            self.combo_country.configure(values=self.country_profile_values)
            restored_country = self._country_profile_selection_before_localized
            if restored_country not in self.country_profile_values:
                restored_country = self.country_profile_values[0] if self.country_profile_values else ""
            self.combo_country.set(restored_country)
            self._toggle_country_profile()

        if mode == "154":
            self.var_count.set(154)
            self.pool_badge_val.config(text="154")
            self.pool_badge_sub.config(text="kredi havuzu")
            self.spin_count.configure(from_=1, to=10000, increment=1)
        elif mode == "64":
            self.var_count.set(64)
            self.pool_badge_val.config(text="64")
            self.pool_badge_sub.config(text="kredi havuzu")
            self.spin_count.configure(from_=1, to=10000, increment=1)
        elif mode == "basic":
            self.var_count.set(21)
            self.pool_badge_val.config(text="21")
            self.pool_badge_sub.config(text="temel hedef havuzu")
            self.spin_count.configure(from_=1, to=10000, increment=1)
        elif mode == "country_local":
            pass
        elif mode == "custom":
            if changed_mode:
                self.var_count.set(160 if self.var_custom_mode.get() == "equal" else 100)
            self.custom_config_card.pack(fill="x", pady=(0, 10), after=self.count_frame)
            self._on_custom_mode_change()
        if mode != "custom":
            self.custom_config_card.pack_forget()
            if mode != "country_local":
                self.count_label.config(text="Soru adedi")
            self.difficulty_label.pack(anchor="w", pady=(0, 2), before=self.language_scope_label)
            self.combo_difficulty.pack(fill="x", pady=(0, 8), before=self.language_scope_label)
            if mode == "country_local":
                self.scale_combo.state(["disabled"])
                self.combo_pct_lbl.config(text="Kapalı")
            else:
                self.scale_combo.state(["!disabled"])
                self.combo_pct_lbl.config(text=f"%{int(self.scale_combo.get())}")
        self._last_mode = mode

    def _on_localized_country_selected(self, _event: Optional[tk.Event] = None) -> None:
        if self.var_mode.get() == "country_local":
            self._update_country_local_minimum(reset_count=True)

    def _update_country_local_minimum(self, reset_count: bool = False) -> None:
        country = self.combo_country.get().strip()
        if not country:
            self.spin_count.configure(from_=1, to=10000, increment=1)
            self.count_label.config(text="Soru adedi · önce ülke/bölge seçin")
            self.pool_badge_val.config(text="YEREL")
            self.pool_badge_sub.config(text="ülke/bölge hedefleri")
            return

        minimum = self.manager.repository.country_local_target_count(country, pool="154")
        self.spin_count.configure(from_=max(1, minimum), to=10000, increment=1)
        if reset_count or self._get_count_value(default=0) < minimum:
            self.var_count.set(minimum)
        self.count_label.config(text=f"Toplam soru adedi (minimum {minimum})")
        self.pool_badge_val.config(text=str(minimum))
        self.pool_badge_sub.config(text="yerel alt hedef")

    def _on_custom_mode_change(self) -> None:
        custom_mode = self.var_custom_mode.get()
        current_count = self._get_count_value(default=160)
        self.difficulty_label.pack_forget()
        self.combo_difficulty.pack_forget()
        self.equal_mode_hint.pack_forget()
        self.difficulty_quota_frame.pack_forget()

        if custom_mode == "equal":
            self.count_label.config(text="Toplam soru adedi (en az 160)")
            self.spin_count.configure(from_=160, to=10000, increment=1)
            if current_count < 160:
                self.var_count.set(160)
            self.pool_badge_val.config(text="EŞİT")
            self.pool_badge_sub.config(text="tüm hedefler")
            self.equal_mode_hint.pack(anchor="w", pady=(3, 0))
            self.scale_combo.state(["disabled"])
            self.combo_pct_lbl.config(text="Kapalı")
        else:
            self.count_label.config(text="Toplam soru adedi (10'un katı)")
            self.spin_count.configure(from_=10, to=10000, increment=10)
            adjusted_count = max(10, current_count)
            if adjusted_count % 10:
                adjusted_count = ((adjusted_count + 9) // 10) * 10
            if adjusted_count != current_count:
                self.var_count.set(adjusted_count)
            self.pool_badge_val.config(text="ZORLUK")
            self.pool_badge_sub.config(text="10 soruluk kota")
            self.difficulty_quota_frame.pack(fill="x", pady=(1, 0))
            self.scale_combo.state(["!disabled"])
            self.combo_pct_lbl.config(text=f"%{int(self.scale_combo.get())}")
        self._update_custom_quota_total()

    def _get_count_value(self, default: int = 0) -> int:
        try:
            return int(self.var_count.get())
        except (ValueError, tk.TclError):
            return default

    def _update_custom_quota_total(self) -> None:
        if not hasattr(self, "difficulty_count_vars"):
            return
        try:
            total = sum(int(value.get()) for value in self.difficulty_count_vars.values())
        except (ValueError, tk.TclError):
            total = -1
        color = GREEN if total == 10 else RED
        text = f"Toplam: {total} / 10" if total >= 0 else "Adetleri tam sayı olarak girin"
        self.difficulty_quota_total.config(text=text, fg=color)

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

    # ----------------------------------------------------
    # Dış soru veri seti içe aktarma
    # ----------------------------------------------------

    def _open_external_importer(self) -> None:
        if self.external_window and self.external_window.winfo_exists():
            self.external_window.lift()
            self.external_window.focus_force()
            return

        win = tk.Toplevel(self)
        self.external_window = win
        win.title("Knowley · Dış Soru Veri Seti")
        win.geometry("1180x720")
        win.minsize(900, 580)
        win.configure(bg=BG_MAIN)
        win.transient(self)

        outer = tk.Frame(win, bg=BG_MAIN, padx=18, pady=16)
        outer.pack(fill="both", expand=True)
        tk.Label(
            outer,
            text="DIŞ SORU VERİ SETİ",
            fg=CYAN,
            bg=BG_MAIN,
            font=(FONT_FAMILY, 9, "bold"),
        ).pack(anchor="w")
        tk.Label(
            outer,
            text="JSON/CSV'yi al, Knowley kurallarıyla denetle, önizle ve onaylayınca Firestore'a gönder.",
            fg=TEXT_LIGHT,
            bg=BG_MAIN,
            font=(FONT_FAMILY, 13, "bold"),
        ).pack(anchor="w", pady=(2, 12))

        source_card = tk.Frame(outer, bg=PANEL_BG, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=12, pady=10)
        source_card.pack(fill="x")
        tk.Label(source_card, text="Dosya veya doğrudan JSON/CSV URL'si", fg=MUTED, bg=PANEL_BG, font=(FONT_FAMILY, 8, "bold")).pack(anchor="w")
        source_row = tk.Frame(source_card, bg=PANEL_BG)
        source_row.pack(fill="x", pady=(6, 0))
        self.external_source_var = tk.StringVar(value=DEFAULT_SHARED_SOURCE)
        self.external_source_entry = tk.Entry(
            source_row,
            textvariable=self.external_source_var,
            bg="#090d20",
            fg=TEXT_LIGHT,
            insertbackground=TEXT_LIGHT,
            relief="flat",
            font=("Consolas", 9),
        )
        self.external_source_entry.pack(side="left", fill="x", expand=True, ipady=7)
        tk.Button(
            source_row,
            text="Dosya Seç",
            command=self._choose_external_file,
            bg=PANEL_INNER,
            fg=TEXT_LIGHT,
            activebackground=BORDER_ACCENT,
            relief="flat",
            cursor="hand2",
            padx=14,
            pady=7,
        ).pack(side="left", padx=(8, 0))

        actions = tk.Frame(outer, bg=BG_MAIN)
        actions.pack(fill="x", pady=10)
        self.external_import_btn = tk.Button(
            actions, text="1  İçe Aktar", command=self._import_external_source,
            bg=PURPLE, fg=TEXT_LIGHT, activebackground="#a99aff", relief="flat", cursor="hand2", padx=16, pady=8,
            font=(FONT_FAMILY, 9, "bold"),
        )
        self.external_import_btn.pack(side="left")
        self.external_validate_btn = tk.Button(
            actions, text="2  Uygunluk Testi", command=self._validate_external_records,
            bg=PANEL_INNER, fg=TEXT_LIGHT, activebackground=BORDER_ACCENT, relief="flat", cursor="hand2", padx=16, pady=8,
            font=(FONT_FAMILY, 9, "bold"), state="disabled",
        )
        self.external_validate_btn.pack(side="left", padx=8)
        self.external_publish_btn = tk.Button(
            actions, text="3  Firebase'e Gönder", command=self._publish_external_questions,
            bg=GREEN, fg=TEXT_DARK, activebackground="#86efc2", relief="flat", cursor="hand2", padx=16, pady=8,
            font=(FONT_FAMILY, 9, "bold"), state="disabled",
        )
        self.external_publish_btn.pack(side="left")
        self.external_status_lbl = tk.Label(actions, text="Kaynak bekleniyor", fg=MUTED, bg=BG_MAIN, font=(FONT_FAMILY, 9, "bold"))
        self.external_status_lbl.pack(side="right")

        preview_card = tk.Frame(outer, bg=PANEL_BG, highlightbackground=PANEL_BORDER, highlightthickness=1, padx=10, pady=10)
        preview_card.pack(fill="both", expand=True)
        columns = ("state", "category", "subcategory", "difficulty", "duration", "question", "reason")
        self.external_tree = ttk.Treeview(
            preview_card, columns=columns, show="headings", height=16, style="External.Treeview"
        )
        self.external_tree.tag_configure("raw", foreground=TEXT_DIM, background="#0b1022")
        self.external_tree.tag_configure("accepted", foreground="#d9fff0", background="#0d2a25")
        self.external_tree.tag_configure("rejected", foreground="#ffd5dd", background="#301522")
        headings = {
            "state": "Durum", "category": "Kategori", "subcategory": "Alt kategori",
            "difficulty": "Zorluk", "duration": "Süre", "question": "Soru", "reason": "Not",
        }
        widths = {"state": 75, "category": 130, "subcategory": 170, "difficulty": 75, "duration": 55, "question": 360, "reason": 280}
        for column in columns:
            self.external_tree.heading(column, text=headings[column])
            self.external_tree.column(column, width=widths[column], minwidth=45, anchor="w")
        tree_scroll_y = ttk.Scrollbar(preview_card, orient="vertical", command=self.external_tree.yview)
        tree_scroll_x = ttk.Scrollbar(preview_card, orient="horizontal", command=self.external_tree.xview)
        self.external_tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
        self.external_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")
        preview_card.rowconfigure(0, weight=1)
        preview_card.columnconfigure(0, weight=1)

    def _choose_external_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.external_window,
            title="Soru veri setini seç",
            filetypes=[("Soru verileri", "*.json *.csv *.tsv"), ("JSON", "*.json"), ("CSV", "*.csv"), ("TSV", "*.tsv")],
        )
        if path:
            self.external_source_var.set(path)

    def _set_external_busy(self, busy: bool, status: str) -> None:
        state = "disabled" if busy else "normal"
        self.external_import_btn.config(state=state)
        self.external_status_lbl.config(text=status, fg=ORANGE if busy else MUTED)

    def _import_external_source(self) -> None:
        source = self.external_source_var.get().strip()
        self._set_external_busy(True, "Kaynak okunuyor…")
        self.external_validate_btn.config(state="disabled")
        self.external_publish_btn.config(state="disabled")

        def worker() -> None:
            try:
                records = load_source(source)
                self.external_raw_records = records
                self.external_report = None
                self.after(0, lambda: self._external_import_finished(len(records)))
            except Exception as exc:
                self.after(0, lambda error=str(exc): self._external_import_failed(error))

        threading.Thread(target=worker, daemon=True).start()

    def _external_import_finished(self, count: int) -> None:
        self._set_external_busy(False, f"{count} ham kayıt alındı")
        self.external_validate_btn.config(state="normal")
        for item in self.external_tree.get_children():
            self.external_tree.delete(item)
        for index, row in enumerate(self.external_raw_records[:500], start=1):
            is_jeopardy = all(key in row for key in ("round", "answer", "question", "category"))
            question = str(row.get("question_text") or row.get("soru") or row.get("prompt") or (row.get("answer") if is_jeopardy else row.get("question")) or "")
            self.external_tree.insert("", "end", values=("HAM", "", "", "", "", question[:220], f"Kayıt {index}"), tags=("raw",))
        print(f"[Dış Veri] {count} ham soru kaydı içe aktarıldı.")

    def _external_import_failed(self, error: str) -> None:
        self._set_external_busy(False, "İçe aktarma başarısız")
        print(f"[Dış Veri] İçe aktarma hatası: {error}")
        messagebox.showerror("Dış veri kaynağı", error, parent=self.external_window)

    def _validate_external_records(self) -> None:
        if not self.external_raw_records:
            return
        self.external_validate_btn.config(state="disabled", text="Test ediliyor…")
        source = self.external_source_var.get().strip()

        def worker() -> None:
            def progress(done: int, total: int) -> None:
                if done == 1 or done == total or done % 10 == 0:
                    self.after(0, lambda d=done, t=total: self.external_status_lbl.config(text=f"Tam şema + 5 dil: {d}/{t}", fg=ORANGE))

            report = validate_and_transform(
                self.external_raw_records,
                source=source,
                translate_all=True,
                llm_manager=self.manager.llm_manager,
                progress_callback=progress,
            )
            output = save_report(report, os.path.join(os.path.dirname(__file__), "data", "imports", "latest_external_import.json"))
            self.external_report = report
            self.after(0, lambda: self._external_validation_finished(report, str(output)))

        threading.Thread(target=worker, daemon=True).start()

    def _external_validation_finished(self, report: ImportReport, output_path: str) -> None:
        self.external_validate_btn.config(state="normal", text="2  Uygunluk Testi")
        self.external_publish_btn.config(state="normal" if report.accepted_count else "disabled")
        self.external_status_lbl.config(
            text=f"{report.accepted_count} uygun · {report.rejected_count} elendi",
            fg=GREEN if report.accepted_count else RED,
        )
        for item in self.external_tree.get_children():
            self.external_tree.delete(item)
        for question in report.accepted:
            content = question["translations"][question["supported_languages"][0]]
            self.external_tree.insert(
                "", "end",
                values=("UYGUN", question["categories"][0], question["sub_categories"][0], question["difficulty_local"], question["duration_seconds"], content["question"], "Firebase'e hazır"),
                tags=("accepted",),
            )
        for rejected in report.rejected:
            self.external_tree.insert("", "end", values=("ELENDİ", "", "", "", "", rejected.raw_question, rejected.reason), tags=("rejected",))
        print(f"[Dış Veri] Test tamamlandı: {report.accepted_count} uygun, {report.rejected_count} elendi. Rapor: {output_path}")

    def _publish_external_questions(self) -> None:
        report = self.external_report
        if not report or not report.accepted:
            return
        approved = messagebox.askyesno(
            "Firebase onayı",
            f"{report.accepted_count} uygun soruyu 'dbquestions' koleksiyonuna göndermek istiyor musunuz?\n\nMevcut hash tekrarları otomatik atlanır.",
            parent=self.external_window,
        )
        if not approved:
            return
        self.external_publish_btn.config(state="disabled", text="Gönderiliyor…")

        def worker() -> None:
            saved = 0
            skipped = 0
            try:
                db = init_firebase()
                for question in report.accepted:
                    if save_question_to_firestore(db, question, collection_name="dbquestions"):
                        saved += 1
                    else:
                        skipped += 1
                self.after(0, lambda: self._external_publish_finished(saved, skipped, None))
            except Exception as exc:
                self.after(0, lambda error=str(exc): self._external_publish_finished(saved, skipped, error))

        threading.Thread(target=worker, daemon=True).start()

    def _external_publish_finished(self, saved: int, skipped: int, error: Optional[str]) -> None:
        self.external_publish_btn.config(state="normal", text="3  Firebase'e Gönder")
        if error:
            self.external_status_lbl.config(text="Firebase gönderimi başarısız", fg=RED)
            messagebox.showerror("Firebase", error, parent=self.external_window)
            return
        self.external_status_lbl.config(text=f"{saved} kaydedildi · {skipped} atlandı", fg=GREEN)
        print(f"[Dış Veri] Firebase dbquestions tamamlandı: {saved} kaydedildi, {skipped} tekrar/hata atlandı.")
        self._load_firebase_async()
        messagebox.showinfo("Firebase", f"dbquestions koleksiyonuna {saved} soru kaydedildi.\n{skipped} tekrar veya hatalı kayıt atlandı.", parent=self.external_window)

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
            # Avoid serial network probes to every provider during startup.
            # The user can run a live probe explicitly from the provider panel.

        threading.Thread(target=worker, daemon=True).start()

    def _open_firebase_audit(self) -> None:
        window = getattr(self, "firebase_audit_window", None)
        if window and window.winfo_exists():
            window.lift()
            return
        window = tk.Toplevel(self)
        self.firebase_audit_window = window
        window.title("Firebase · 1.1.0+ Üretim Denetimi")
        window.geometry("1120x700")
        window.minsize(820, 480)
        window.configure(bg=BG_MAIN)

        outer = tk.Frame(window, bg=BG_MAIN, padx=16, pady=14)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="FIREBASE 1.1.0+ ÜRETİM DENETİMİ", fg=TEXT_LIGHT, bg=BG_MAIN, font=(FONT_FAMILY, 14, "bold")).pack(anchor="w")
        tk.Label(outer, text="questions koleksiyonu · kategori ve alt kategori bazında", fg=MUTED, bg=BG_MAIN, font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(0, 10))

        summary = tk.Frame(outer, bg=PANEL_BG, padx=12, pady=9, highlightbackground=PANEL_BORDER, highlightthickness=1)
        summary.pack(fill="x", pady=(0, 10))
        self.firebase_audit_summary = tk.Label(summary, text="Firestore taranıyor…", fg=ORANGE, bg=PANEL_BG, font=(FONT_FAMILY, 10, "bold"))
        self.firebase_audit_summary.pack(side="left")
        tk.Button(summary, text="Yenile", command=self._load_firebase_audit_async, bg=PANEL_INNER, fg=CYAN, relief="flat", padx=12, pady=4).pack(side="right")

        body = tk.Frame(outer, bg=PANEL_BG, highlightbackground=PANEL_BORDER, highlightthickness=1)
        body.pack(fill="both", expand=True)
        columns = ("category", "subcategory", "today", "week", "month", "all_time")
        self.firebase_audit_tree = ttk.Treeview(body, columns=columns, show="headings", style="External.Treeview")
        labels = {"category": "Kategori", "subcategory": "Alt kategori", "today": "Bugün", "week": "Bu hafta", "month": "Bu ay", "all_time": "Tüm zamanlar"}
        widths = {"category": 190, "subcategory": 260, "today": 90, "week": 100, "month": 100, "all_time": 120}
        for column in columns:
            self.firebase_audit_tree.heading(column, text=labels[column])
            self.firebase_audit_tree.column(column, width=widths[column], anchor="w" if column in {"category", "subcategory"} else "center")
        scroll_y = ttk.Scrollbar(body, orient="vertical", command=self.firebase_audit_tree.yview)
        self.firebase_audit_tree.configure(yscrollcommand=scroll_y.set)
        self.firebase_audit_tree.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")
        self._load_firebase_audit_async()

    def _open_firebase_question_report(self) -> None:
        window = getattr(self, "question_report_window", None)
        if window and window.winfo_exists():
            window.lift()
            return

        window = tk.Toplevel(self)
        self.question_report_window = window
        window.title("Firebase · 1.1.0 Soru Raporlama")
        window.geometry("1240x790")
        window.minsize(980, 650)
        window.configure(bg=BG_MAIN)

        outer = tk.Frame(window, bg=BG_MAIN, padx=18, pady=15)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=BG_MAIN)
        header.pack(fill="x", pady=(0, 10))
        title_box = tk.Frame(header, bg=BG_MAIN)
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_box,
            text="FIREBASE SORU RAPORU",
            fg=TEXT_LIGHT,
            bg=BG_MAIN,
            font=(FONT_FAMILY, 14, "bold"),
        ).pack(anchor="w")
        self.question_report_status = tk.Label(
            title_box,
            text="Soru sınıflandırmaları yükleniyor…",
            fg=ORANGE,
            bg=BG_MAIN,
            font=(FONT_FAMILY, 9),
        )
        self.question_report_status.pack(anchor="w", pady=(2, 0))
        self.question_report_refresh = tk.Button(
            header,
            text="↻ Yenile",
            command=self._load_firebase_question_report_async,
            bg=PANEL_INNER,
            fg=CYAN,
            activebackground=PANEL_BORDER,
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=5,
        )
        self.question_report_refresh.pack(side="right", padx=(10, 0))

        filter_card = tk.Frame(
            outer,
            bg=PANEL_BG,
            padx=14,
            pady=11,
            highlightbackground=PANEL_BORDER,
            highlightthickness=1,
        )
        filter_card.pack(fill="x", pady=(0, 10))
        tk.Label(
            filter_card,
            text="FİLTRELER",
            fg=MUTED,
            bg=PANEL_BG,
            font=(FONT_FAMILY, 8, "bold"),
        ).pack(anchor="w", pady=(0, 7))

        filter_row = tk.Frame(filter_card, bg=PANEL_BG)
        filter_row.pack(fill="x")
        for column in range(3):
            filter_row.columnconfigure(column, weight=1, uniform="report_filter")

        self.question_report_country_values: Dict[str, str] = {"Tümü": "*"}
        self.question_report_country_var = tk.StringVar(value="Tümü")
        self.question_report_category_var = tk.StringVar(value="Tümü")
        self.question_report_difficulty_var = tk.StringVar(value="Tümü")

        filter_definitions = [
            ("Ülke / bölge", self.question_report_country_var),
            ("Zorluk seviyesi", self.question_report_difficulty_var),
            ("Kategori", self.question_report_category_var),
        ]
        self.question_report_combos: Dict[str, ttk.Combobox] = {}
        for column, (label, variable) in enumerate(filter_definitions):
            cell = tk.Frame(filter_row, bg=PANEL_BG)
            cell.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
            tk.Label(cell, text=label, fg=TEXT_DIM, bg=PANEL_BG, font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(0, 4))
            combo = ttk.Combobox(cell, textvariable=variable, state="readonly", values=["Tümü"])
            combo.pack(fill="x")
            combo.bind("<<ComboboxSelected>>", self._update_firebase_question_report)
            self.question_report_combos[label] = combo

        self.question_report_match_count = tk.Label(
            outer,
            text="Filtrelere uyan soru: —",
            fg=CYAN,
            bg=PANEL_INNER,
            font=(FONT_FAMILY, 11, "bold"),
            padx=12,
            pady=8,
            anchor="w",
        )
        self.question_report_match_count.pack(fill="x", pady=(0, 10))

        ranking_card = tk.Frame(
            outer,
            bg=PANEL_BG,
            padx=12,
            pady=10,
            highlightbackground=PANEL_BORDER,
            highlightthickness=1,
        )
        ranking_card.pack(fill="both", expand=True)
        tk.Label(
            ranking_card,
            text="GENEL SIRALAMALAR · 1.1.0 SORULARI",
            fg=TEXT_LIGHT,
            bg=PANEL_BG,
            font=(FONT_FAMILY, 10, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        rankings = tk.Frame(ranking_card, bg=PANEL_BG)
        rankings.pack(fill="both", expand=True)
        for column in range(3):
            rankings.columnconfigure(column, weight=1, uniform="report_ranking")
        rankings.rowconfigure(0, weight=1)

        self.question_report_trees: Dict[str, ttk.Treeview] = {}
        ranking_definitions = [
            ("categories", "Kategori sıralaması"),
            ("countries", "Ülke / bölge sıralaması"),
            ("difficulties", "Zorluk sıralaması"),
        ]
        for column, (key, label) in enumerate(ranking_definitions):
            panel = tk.Frame(rankings, bg=PANEL_BG)
            panel.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 7, 0))
            tk.Label(panel, text=label, fg=TEXT_DIM, bg=PANEL_BG, font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(0, 5))
            table_frame = tk.Frame(panel, bg=PANEL_BG)
            table_frame.pack(fill="both", expand=True)
            tree = ttk.Treeview(
                table_frame,
                columns=("rank", "name", "count", "share"),
                show="headings",
                style="External.Treeview",
                height=17,
            )
            for column_id, heading in (("rank", "#"), ("name", "Ad"), ("count", "Soru"), ("share", "%")):
                tree.heading(column_id, text=heading)
            tree.column("rank", width=38, minwidth=34, anchor="center", stretch=False)
            tree.column("name", width=170, minwidth=95, anchor="w")
            tree.column("count", width=64, minwidth=55, anchor="center", stretch=False)
            tree.column("share", width=58, minwidth=48, anchor="center", stretch=False)
            scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scrollbar.set)
            tree.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            self.question_report_trees[key] = tree

        self.question_report_questions: List[Dict[str, Any]] = []
        self.question_report_country_names: Dict[str, str] = {}
        self._load_firebase_question_report_async()

    def _load_firebase_question_report_async(self) -> None:
        if not getattr(self, "question_report_window", None) or not self.question_report_window.winfo_exists():
            return
        if getattr(self, "question_report_loading", False):
            return
        self.question_report_loading = True
        self.question_report_refresh.config(state="disabled", text="Yükleniyor…")
        self.question_report_status.config(text="Firestore soru sınıflandırmaları okunuyor…", fg=ORANGE)

        def worker() -> None:
            try:
                report = firebase_question_report()
                try:
                    country_profiles = self.manager.repository.countries()
                    report = canonicalize_question_report_countries(report, country_profiles)
                    country_names = {"GL": "Global"}
                    country_names.update({
                        str(item.get("code") or "").strip().upper(): str(item.get("name") or "").strip()
                        for item in country_profiles
                        if item.get("code") and item.get("name")
                    })
                except Exception:
                    country_names = {}
                self.after(0, lambda: self._render_firebase_question_report(report, country_names, None))
            except Exception as exc:
                self.after(0, lambda error=str(exc): self._render_firebase_question_report(None, {}, error))

        threading.Thread(target=worker, daemon=True).start()

    def _render_firebase_question_report(
        self,
        report: Optional[Dict[str, Any]],
        country_names: Dict[str, str],
        error: Optional[str],
    ) -> None:
        self.question_report_loading = False
        if not getattr(self, "question_report_window", None) or not self.question_report_window.winfo_exists():
            return
        self.question_report_refresh.config(state="normal", text="↻ Yenile")
        if error or not report:
            self.question_report_status.config(text=f"Rapor yüklenemedi: {error or 'Firestore yanıt vermedi'}", fg=RED)
            return

        self.question_report_questions = list(report.get("questions") or [])
        self.question_report_country_names = dict(country_names)
        for item in self.question_report_questions:
            code = str(item.get("country_code") or "").strip()
            name = str(item.get("country_name") or "").strip()
            if code and name:
                self.question_report_country_names.setdefault(code, name)
        self.question_report_status.config(
            text=f"1.1.0 · {int(report.get('question_count') or len(self.question_report_questions)):,} soru yüklendi · sınıflandırma verisi".replace(",", "."),
            fg=GREEN,
        )

        country_codes = set(self.question_report_country_names)
        country_codes.add("GL")
        country_codes.update(
            str(item.get("country_code") or "Bilinmeyen")
            for item in self.question_report_questions
        )
        countries = sorted(
            country_codes,
            key=lambda value: (self.question_report_country_names.get(value, value).casefold(), value),
        )
        self.question_report_country_values = {"Tümü": "*"}
        country_labels = ["Tümü"]
        for code in countries:
            name = self.question_report_country_names.get(code)
            label = question_report_country_label(code, name)
            self.question_report_country_values[label] = code
            country_labels.append(label)

        categories = sorted(
            {category for item in self.question_report_questions for category in item.get("categories", [])},
            key=str.casefold,
        )
        difficulties = [
            level for level in ("Kolay", "Orta", "Zor", "Bilinmiyor")
            if any(item.get("difficulty") == level for item in self.question_report_questions)
        ]
        category_values = ["Tümü", *categories]
        difficulty_values = ["Tümü", *difficulties]

        for combo in self.question_report_combos.values():
            current = combo.get()
            if combo is self.question_report_combos["Ülke / bölge"]:
                values = country_labels
            elif combo is self.question_report_combos["Zorluk seviyesi"]:
                values = difficulty_values
            else:
                values = category_values
            combo.configure(values=values)
            if current not in values:
                combo.set("Tümü")

        self._update_firebase_question_report()

    def _update_firebase_question_report(self, _event: Optional[tk.Event] = None) -> None:
        questions = getattr(self, "question_report_questions", [])
        country_label = self.question_report_country_var.get() or "Tümü"
        country = self.question_report_country_values.get(country_label, "*")
        category = self.question_report_category_var.get() or "Tümü"
        difficulty = self.question_report_difficulty_var.get() or "Tümü"

        matches = [
            question for question in questions
            if (country == "*" or question.get("country_code") == country)
            and (category == "Tümü" or category in (question.get("categories") or []))
            and (difficulty == "Tümü" or question.get("difficulty") == difficulty)
        ]
        self.question_report_match_count.config(
            text=f"Filtrelere uyan soru: {len(matches):,} / {len(questions):,}".replace(",", ".")
        )

        category_counts: Dict[str, int] = {}
        country_counts: Dict[str, int] = {
            code: 0 for code in self.question_report_country_names
        }
        country_counts.setdefault("GL", 0)
        difficulty_counts: Dict[str, int] = {}
        country_names_by_code: Dict[str, str] = dict(self.question_report_country_names)
        for question in questions:
            for name in set(question.get("categories") or ["Bilinmeyen"]):
                category_counts[name] = category_counts.get(name, 0) + 1
            code = str(question.get("country_code") or "Bilinmeyen")
            country_counts[code] = country_counts.get(code, 0) + 1
            source_name = str(question.get("country_name") or "").strip()
            if source_name and code not in country_names_by_code:
                country_names_by_code[code] = source_name
            level = str(question.get("difficulty") or "Bilinmiyor")
            difficulty_counts[level] = difficulty_counts.get(level, 0) + 1

        self._render_question_report_ranking(
            self.question_report_trees["categories"], category_counts, sum(category_counts.values())
        )
        self._render_question_report_ranking(
            self.question_report_trees["countries"],
            country_counts,
            sum(country_counts.values()),
            display_names={
                code: question_report_country_label(code, country_names_by_code.get(code, code))
                for code in country_counts
            },
        )
        self._render_question_report_ranking(
            self.question_report_trees["difficulties"], difficulty_counts, sum(difficulty_counts.values())
        )

    @staticmethod
    def _render_question_report_ranking(
        tree: ttk.Treeview,
        counts: Dict[str, int],
        denominator: int,
        display_names: Optional[Dict[str, str]] = None,
    ) -> None:
        tree.delete(*tree.get_children())
        rows = sorted(counts.items(), key=lambda item: (-item[1], (display_names or {}).get(item[0], item[0]).casefold()))
        for rank, (name, count) in enumerate(rows, start=1):
            display = (display_names or {}).get(name, name)
            share = (count / denominator * 100) if denominator else 0
            tree.insert("", "end", values=(rank, display, f"{count:,}".replace(",", "."), f"{share:.1f}%"))

    def _load_firebase_audit_async(self) -> None:
        if not getattr(self, "firebase_audit_window", None) or not self.firebase_audit_window.winfo_exists():
            return
        self.firebase_audit_summary.config(text="Firestore taranıyor…", fg=ORANGE)

        def worker() -> None:
            try:
                audit = firebase_question_audit("1.1.0")
                self.after(0, lambda: self._render_firebase_audit(audit, None))
            except Exception as exc:
                self.after(0, lambda error=str(exc): self._render_firebase_audit(None, error))

        threading.Thread(target=worker, daemon=True).start()

    def _render_firebase_audit(self, audit: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        if not getattr(self, "firebase_audit_window", None) or not self.firebase_audit_window.winfo_exists():
            return
        if error or not audit:
            self.firebase_audit_summary.config(text=f"Tarama başarısız: {error}", fg=RED)
            return
        for item in self.firebase_audit_tree.get_children():
            self.firebase_audit_tree.delete(item)
        totals = audit["totals"]
        self.firebase_audit_summary.config(
            text=(
                f"Bugün {totals['today']:,}  ·  Bu hafta {totals['week']:,}  ·  "
                f"Bu ay {totals['month']:,}  ·  Tüm zamanlar {totals['all_time']:,}  ·  "
                f"Taranan belge {audit['scanned']:,}"
            ).replace(",", "."),
            fg=GREEN,
        )
        for row in audit["rows"]:
            self.firebase_audit_tree.insert("", "end", values=(
                row["category"], row["subcategory"], row["today"], row["week"], row["month"], row["all_time"]
            ), tags=("accepted",))

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

        self.country_profile_values = [c["name"] for c in data.get("countries", [])]
        self.country_local_values = self.manager.repository.local_target_countries(pool="154")
        if self.var_mode.get() == "country_local":
            self.combo_country.config(values=self.country_local_values, state="readonly")
            self.combo_country.set("")
            self.var_count.set(1)
        else:
            self.combo_country.config(values=self.country_profile_values)
            if self.country_profile_values:
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
            disabled_reason = str(item.get("disabled_reason") or "")
            cooldown_remaining = int(item.get("cooldown_remaining") or 0)
            reachable = live.get("reachable") if live else None
            usage = item.get("session_usage") or {}
            used = int(usage.get("requests", 0) or 0)
            last_error = str(usage.get("last_error") or "")
            available_models = live.get("available_models", []) if isinstance(live, dict) else []

            if not configured:
                state_lbl = "PASİF"
                state_color = MUTED
            elif disabled_reason:
                state_lbl = "KOTA DOLU" if "quota" in disabled_reason or "429" in disabled_reason else "ERİŞİM HATASI"
                state_color = ORANGE if state_lbl == "KOTA DOLU" else RED
            elif cooldown_remaining > 0:
                state_lbl = f"BEKLEME {cooldown_remaining}s"
                state_color = ORANGE
            elif live is None:
                state_lbl = "KONTROL"
                state_color = ORANGE
            elif not reachable or not available_models:
                state_lbl = "MODEL ERİŞİLEMİYOR"
                state_color = RED
            elif last_error:
                state_lbl = "SON ÇAĞRI HATASI"
                state_color = RED
            elif used:
                state_lbl = "ÜRETİM ÇAĞRISI OK"
                state_color = GREEN
            else:
                # /models is only a catalog probe. It does not prove that
                # generate-content requests have free quota or will succeed.
                state_lbl = "KATALOG AÇIK"
                state_color = CYAN

            remaining = item.get("remaining_questions")
            remaining_is_exact = bool(item.get("remaining_is_exact")) and remaining is not None
            suffix = "sağlayıcı başlığına göre kalan" if remaining_is_exact else "gerçek kota bilgisi yok"
            rem_str = f"{remaining:,}".replace(",", ".") if remaining_is_exact else "—"

            models = available_models or item.get("models", [])
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
            if live and reachable and not disabled_reason and not cooldown_remaining and not used and not last_error:
                tk.Label(
                    card,
                    text="Model kataloğu açık; gerçek üretim kotası henüz doğrulanmadı.",
                    fg=TEXT_DIM,
                    bg=PANEL_INNER,
                    font=(FONT_FAMILY, 8),
                ).pack(anchor="w")
            if disabled_reason:
                reason = (
                    "Ücretsiz kota sınırına ulaşıldı; bu uygulama çalışmasında duraklatıldı."
                    if "quota" in disabled_reason or "429" in disabled_reason
                    else "Kimlik doğrulama hatası; anahtar ve bölge ayarını kontrol edin."
                )
                tk.Label(card, text=reason, fg=state_color, bg=PANEL_INNER, font=(FONT_FAMILY, 8)).pack(anchor="w")

    # ----------------------------------------------------
    # İş Başlatma & Durdurma
    # ----------------------------------------------------

    def _collect_payload(self) -> Dict[str, Any]:
        count = self._get_count_value(default=0)
        if count < 1:
            raise ValueError("Soru adedi en az 1 olmalıdır.")

        mode = self.var_mode.get()
        custom_mode = self.var_custom_mode.get() if mode == "custom" else None
        difficulty_counts = {
            level: self._get_custom_difficulty_count(level)
            for level in ("Kolay", "Orta", "Zor")
        } if custom_mode == "difficulty" else {"Kolay": 4, "Orta": 4, "Zor": 2}

        if custom_mode == "equal" and count < 160:
            raise ValueError("Tüm kategori ve alt kategorilerin eşit dağılması için en az 160 soru girin.")
        if custom_mode == "difficulty":
            if count % 10:
                raise ValueError("Özel zorluk modunda toplam soru sayısı 10'un katı olmalıdır.")
            if sum(difficulty_counts.values()) != 10:
                raise ValueError("Kolay, Orta ve Zor adetlerinin toplamı her 10 soru için 10 olmalıdır.")

        selected_langs = list(SUPPORTED_LANGUAGE_CODES)

        country_enabled = self.var_cp_enabled.get()
        country_val = self.combo_country.get() if country_enabled else None
        if mode == "country_local":
            country_enabled = True
            country_val = self.combo_country.get().strip()
            if not country_val:
                raise ValueError("Ülke/bölge yerel hedef modunda ülke/bölge seçmek zorunludur.")
            minimum_count = self.manager.repository.country_local_target_count(country_val, pool="154")
            if minimum_count < 1:
                raise ValueError("Seçilen ülke/bölge için yerel alt hedef bulunamadı.")
            if count < minimum_count:
                raise ValueError(
                    f"{country_val} için en az {minimum_count} soru girin; "
                    "her yerel alt hedefe en az bir soru ayrılır."
                )
        if country_enabled and not country_val:
            raise ValueError("Ülke profili etkinleştirildiğinde bir profil seçilmelidir.")

        diff_selected = self.combo_difficulty.get()
        difficulty = self.difficulty_map.get(diff_selected, "") if mode != "custom" else ""

        return {
            "question_count": count,
            "generation_mode": mode,
            "custom_mode": custom_mode,
            "difficulty_counts_per_10": difficulty_counts,
            "difficulty": difficulty,
            "scope": "local" if mode == "country_local" else None,
            "country_profile_enabled": country_enabled,
            "country": country_val or "",
            "combo_percent": int(self.scale_combo.get()),
            "languages": selected_langs,
        }

    def _get_custom_difficulty_count(self, level: str) -> int:
        try:
            value = self.difficulty_count_vars[level].get()
        except (ValueError, tk.TclError):
            raise ValueError(f"{level} adedi 0 ile 10 arasında tam sayı olmalıdır.") from None
        if value < 0 or value > 10:
            raise ValueError(f"{level} adedi 0 ile 10 arasında tam sayı olmalıdır.")
        return int(value)

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
        self.last_event_sequence = 0
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
        produced_live = summary.get("produced")
        if produced_live is None:
            produced_values = [e.get("produced") for e in saved_events if isinstance(e.get("produced"), int)]
            produced_live = max([0] + produced_values)
        self.progress_count_lbl.config(text=f"{produced_live} / {total}")
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
        new_events = [
            event for event in events
            if isinstance(event, dict) and int(event.get("sequence") or 0) > self.last_event_sequence
        ]
        if new_events:
            self.last_event_sequence = max(int(event.get("sequence") or 0) for event in new_events)
            self._append_events(new_events)

    def _append_events(self, events: List[Dict[str, Any]]) -> None:
        icons = {
            "started": "▶",
            "planning": "⌁",
            "saved": "✓",
            "retry": "↻",
            "provider_unavailable": "!",
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
            elif kind == "provider_unavailable":
                msg = (
                    "Tüm LLM sağlayıcıları geçici olarak kullanılamıyor. "
                    f"{ev.get('retry_after', '?')}s sonra otomatik yeniden denenecek."
                )
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
