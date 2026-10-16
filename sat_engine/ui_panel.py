"""Tkinter panel to start/monitor SAT generation runs as a subprocess (python -m sat_engine ...)."""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "output"
PROGRESS, CONSOLE, CONSOLE_ERR, PIDFILE = (OUT / n for n in ("progress.json", "console.log", "console.err", "ui_run.pid"))
WRITE_FLAG = "KNOWLEY_GENERATION_WRITES_ENABLED"
COLLECTION = "generated_sat_questions"
TARGETS = {"rw": 54, "math": 44}
FRESH_S = 90
SECTIONS = {"R&W": "rw", "Math": "math", "İkisi": "both"}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ---- plain logic (unit-tested, no GUI) ----

def session_id(seed, section):
    return f"seed{seed}-{'-'.join(('rw', 'math') if section == 'both' else (section,))}"


def build_command(section="both", seed=None, workers=10, save=False, limit=None, extra=()):
    """extra=('--audit',) etc. builds a utility command; otherwise a generation run."""
    cmd = [sys.executable, "-u", "-m", "sat_engine", *extra, "--section", section]
    if not extra:
        cmd += ["--workers", str(workers)]
        if limit:
            cmd += ["--limit", str(limit)]
        if save:
            cmd.append("--save")
    if seed is not None:
        cmd += ["--seed", str(seed)]
    return cmd


def build_env(save=False, confirmed=False, base=None):
    """Copy of the environment for the child only; the write flag needs save AND confirmation."""
    env = dict(os.environ if base is None else base)
    env["PYTHONIOENCODING"] = "utf-8"
    if save and confirmed:
        env[WRITE_FLAG] = "1"
    else:
        env.pop(WRITE_FLAG, None)
    return env


def _fmt(sec):
    sec = int(sec)
    return f"{sec // 3600}sa {sec % 3600 // 60:02d}dk" if sec >= 3600 else f"{sec // 60}dk {sec % 60:02d}sn"


def parse_progress(d):
    d = d or {}
    secs = d.get("sections") or {}
    total, acc = d.get("total") or 0, d.get("accepted") or 0
    eta = d.get("eta_s")
    prov = " | ".join(f"{k}: {v.get('inflight', 0)} aktif, 429x{v.get('n429', 0)}" + (f", bekleme {v['cooldown_s']}s" if v.get("cooldown_s") else "")
                      for k, v in (d.get("providers") or {}).items())
    return {"percent": 100.0 * acc / total if total else 0.0,
            "counts": f"Kabul {acc}/{total}  |  Red {d.get('rejected', 0)}  |  Hata {d.get('failed', 0)}",
            "sections": "   ".join(f"{'R&W' if k == 'rw' else 'Math'} {v.get('done', 0)}/{v.get('target', TARGETS.get(k, 0))}" for k, v in secs.items()),
            "providers": prov or "-", "eta": "ETA ~" + _fmt(eta) if eta is not None else "ETA ?",
            "elapsed": _fmt(d.get("elapsed_s") or 0)}


def _pid_alive(pid):
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, creationflags=NO_WINDOW).stdout
            return f" {pid} " in out
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_running(progress_path=PROGRESS, pidfile=PIDFILE, now=None):
    """True if the panel's pid is alive or progress.json was touched in the last 90 s (any writer)."""
    try:
        if _pid_alive(int(Path(pidfile).read_text().strip())):
            return True
    except (OSError, ValueError):
        pass
    try:
        return (now or time.time()) - Path(progress_path).stat().st_mtime < FRESH_S
    except OSError:
        return False


def load_progress(path=PROGRESS):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    else:
        proc.terminate()


# ---- GUI ----

BG, PANEL, CYAN, GREEN, ORANGE, RED, TXT, MUTED = "#070914", "#111426", "#57e5ff", "#59e3a6", "#ffbb72", "#ff708c", "#f5f7ff", "#8c97b8"
FONT = "Segoe UI"


class SatPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.proc = None
        self._log_pos = 0
        self.section = tk.StringVar(value="İkisi")
        self.seed = tk.StringVar()
        self.workers = tk.StringVar(value="10")
        self.limit = tk.StringVar()
        self.dry = tk.BooleanVar(value=True)
        self.save = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Hazır")
        self._build()
        self.after(500, self._tick)

    def _build(self):
        f = tk.Frame(self, bg=PANEL, padx=14, pady=12)
        f.pack(fill="both", expand=True)
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x")

        def lab(t):
            tk.Label(row, text=t, bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(8, 3))

        lab("Bölüm")
        ttk.Combobox(row, textvariable=self.section, values=list(SECTIONS), state="readonly", width=8).pack(side="left")
        lab("Seed (boş=rastgele)")
        ttk.Entry(row, textvariable=self.seed, width=9).pack(side="left")
        lab("Worker")
        ttk.Entry(row, textvariable=self.workers, width=4).pack(side="left")
        lab("Gelişmiş: --limit")
        ttk.Entry(row, textvariable=self.limit, width=5).pack(side="left")
        self.resume = tk.Label(f, text="", bg=PANEL, fg=ORANGE, font=(FONT, 9))
        self.resume.pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(f, text="Sadece deneme (dry-run)", variable=self.dry, command=lambda: self.dry.get() and self.save.set(False)).pack(anchor="w")
        ttk.Checkbutton(f, text="Firebase'e kaydet", variable=self.save, command=lambda: self.save.get() and self.dry.set(False)).pack(anchor="w")
        bar = tk.Frame(f, bg=PANEL)
        bar.pack(fill="x", pady=8)
        self.btns = []
        for text, cmd, col in (("Başlat", self.start, GREEN), ("Açık slotları göster", lambda: self._util("--list-open"), CYAN),
                               ("Denetle", lambda: self._util("--audit"), CYAN), ("Temizle", lambda: self._util("--clean"), CYAN)):
            b = tk.Button(bar, text=text, command=cmd, bg=col, fg="#07101a", relief="flat", font=(FONT, 9, "bold"), padx=10, pady=5)
            b.pack(side="left", padx=(0, 8))
            self.btns.append(b)
        self.stop_btn = tk.Button(bar, text="Durdur", command=self.stop, bg=RED, fg="#07101a", relief="flat",
                                  font=(FONT, 9, "bold"), padx=10, pady=5, state="disabled")
        self.stop_btn.pack(side="left", padx=(0, 8))
        tk.Button(bar, text="Çıktı klasörünü aç", command=self.open_out, bg="#161b34", fg=CYAN, relief="flat",
                  font=(FONT, 9, "bold"), padx=10, pady=5).pack(side="left")
        self.pb = ttk.Progressbar(f, maximum=100)
        self.pb.pack(fill="x")
        self.lbl = {k: tk.Label(f, text="", bg=PANEL, fg=TXT, font=(FONT, 9), anchor="w") for k in ("counts", "sections", "providers", "eta")}
        for l in self.lbl.values():
            l.pack(fill="x")
        tk.Label(f, textvariable=self.status, bg=PANEL, fg=CYAN, font=(FONT, 9, "bold"), anchor="w").pack(fill="x")
        self.log = scrolledtext.ScrolledText(f, height=14, bg="#0a0e22", fg="#b3bcdc", font=("Consolas", 9), state="disabled")
        self.log.pack(fill="both", expand=True, pady=(6, 0))
        for v in (self.seed, self.section):
            v.trace_add("write", lambda *a: self._hint())

    def _args(self):
        try:
            w = int(self.workers.get() or 10)
            lim = int(self.limit.get()) if self.limit.get().strip() else None
            seed = int(self.seed.get()) if self.seed.get().strip() else None
        except ValueError:
            messagebox.showerror("SAT", "Seed / worker / limit sayı olmalı.", parent=self)
            return None
        return SECTIONS[self.section.get()], seed, w, lim

    def _hint(self):
        seed = self.seed.get().strip()
        ok = seed.isdigit() and (OUT / f"{session_id(seed, SECTIONS[self.section.get()])}.json").exists()
        self.resume.config(text="Aynı seed ile devam eder (oturum dosyası var)." if ok else "")

    def _spawn(self, cmd, env):
        OUT.mkdir(parents=True, exist_ok=True)
        with open(CONSOLE, "wb") as o, open(CONSOLE_ERR, "wb") as e:
            self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=o, stderr=e, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        PIDFILE.write_text(str(self.proc.pid))
        self._log_pos = 0
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        self.status.set("Çalışıyor...")

    def start(self):
        a = self._args()
        if not a or self._busy():
            return
        sec, seed, w, lim = a
        seed = random.randint(1, 999999) if seed is None else seed
        save = bool(self.save.get()) and not self.dry.get()
        if save:
            n = f"en çok {lim} soru/bölüm" if lim else ("54 R&W + 44 Math = 98 soru" if sec == "both" else f"{TARGETS[sec]} soru")
            if not messagebox.askyesno("Firebase'e kaydet", f"{n} '{COLLECTION}' koleksiyonuna YAZILACAK. Devam edilsin mi?", parent=self):
                return
        self.seed.set(str(seed))
        self._spawn(build_command(sec, seed, w, save, lim), build_env(save, save))

    def _util(self, flag):
        a = self._args()
        if not a or self._busy():
            return
        sec, seed, _, _ = a
        if seed is None:
            messagebox.showinfo("SAT", "Bu işlem için seed girin.", parent=self)
            return
        self._spawn(build_command(sec, seed, extra=(flag,)), build_env())

    def _busy(self):
        if (self.proc and self.proc.poll() is None) or is_running():
            messagebox.showwarning("SAT", "Zaten çalışan bir oturum var.", parent=self)
            return True
        return False

    def stop(self):
        if self.proc and self.proc.poll() is None and messagebox.askyesno("SAT", "Üretim durdurulsun mu?", parent=self):
            kill_tree(self.proc)

    def open_out(self):
        OUT.mkdir(parents=True, exist_ok=True)
        os.startfile(OUT) if os.name == "nt" else subprocess.Popen(["xdg-open", str(OUT)])

    def confirm_close(self):
        """True = ok to close the window (run left going, stopped, or none)."""
        if self.proc and self.proc.poll() is None:
            ans = messagebox.askyesnocancel("SAT", "Üretim sürüyor. Arka planda devam etsin mi?\nEvet: devam, Hayır: durdur, İptal: kapatma.", parent=self)
            if ans is None:
                return False
            if ans is False:
                kill_tree(self.proc)
        return True

    def _tick(self):
        try:
            p = parse_progress(load_progress())
            self.pb["value"] = p["percent"]
            for k, l in self.lbl.items():
                l.config(text=p[k] + (f"  (geçen {p['elapsed']})" if k == "eta" else ""))
            if CONSOLE.exists() and CONSOLE.stat().st_size != self._log_pos:
                with open(CONSOLE, "rb") as fh:
                    fh.seek(self._log_pos)
                    chunk = fh.read(65536)
                    self._log_pos = fh.tell()
                self.log.config(state="normal")
                self.log.insert("end", chunk.decode("utf-8", "replace"))
                self.log.see("end")
                self.log.config(state="disabled")
            running = bool(self.proc and self.proc.poll() is None)
            for b in self.btns:
                b.config(state="disabled" if running else "normal")
            self.stop_btn.config(state="normal" if running else "disabled")
            if self.proc and not running:
                rc, self.proc = self.proc.returncode, None
                PIDFILE.unlink(missing_ok=True)
                self.status.set(f"Bitti (kod {rc})")
                tail = CONSOLE.read_text(encoding="utf-8", errors="replace").splitlines()[-12:] if CONSOLE.exists() else []
                summ = [x for x in tail if "accepted" in x or "INCOMPLETE" in x] or tail[-3:]
                messagebox.showinfo("SAT özet", "\n".join(summ) or f"Çıkış kodu {rc}", parent=self)
        finally:
            self.after(2000, self._tick)


def install(app, bar=None):
    """Add a 'SAT Üretimi' button (action bar of the app if found, else top-right) opening the panel in a Toplevel."""
    holder = {}

    def open_win():
        w = holder.get("w")
        if w and w.winfo_exists():
            w.lift()
            return
        w = holder["w"] = tk.Toplevel(app)
        w.title("SAT Soru Üretimi")
        w.geometry("900x720")
        w.configure(bg=BG)
        p = SatPanel(w)
        p.pack(fill="both", expand=True)
        w.protocol("WM_DELETE_WINDOW", lambda: w.destroy() if p.confirm_close() else None)

    bar = bar or getattr(getattr(app, "form_error_lbl", None), "master", None)
    b = tk.Button(bar or app, text="SAT Üretimi  ▶", command=open_win, bg="#161b34", fg=CYAN, relief="flat",
                  cursor="hand2", font=(FONT, 9, "bold"), padx=14, pady=7)
    if bar is not None:
        b.pack(side="left", padx=(0, 12), before=app.form_error_lbl)
    else:
        b.place(relx=1.0, x=-24, y=6, anchor="ne")
    b.open_win = open_win
    return b
