"""Tkinter panel to start/monitor SAT generation runs as a subprocess (python -m sat_engine ...)."""
from __future__ import annotations

import csv
import json
import os
import random
import subprocess
import sys
import threading
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
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                                 creationflags=NO_WINDOW, timeout=10).stdout
            return any(len(r) > 1 and r[1] == str(pid) for r in csv.reader(out.splitlines()))
        os.kill(pid, 0)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def read_pid(pidfile=PIDFILE):
    try:
        return int(Path(pidfile).read_text().strip())
    except (OSError, ValueError):
        return None


def is_running(progress_path=PROGRESS, pidfile=PIDFILE, now=None):
    """pid file present -> pid liveness decides (stale progress.json is ignored); else progress.json fresher than 90 s (CLI run elsewhere)."""
    pid = read_pid(pidfile)
    if pid is not None:
        return _pid_alive(pid)
    try:
        return (now or time.time()) - Path(progress_path).stat().st_mtime < FRESH_S
    except OSError:
        return False


def parse_args(section, seed, workers, limit):
    """Validate UI strings -> (section, seed|None, workers 1..32, limit|None); ValueError with a friendly message."""
    try:
        w = min(32, max(1, int(workers.strip() or 10)))
        lim = int(limit) if limit.strip() else None
        sd = int(seed) if seed.strip() else None
    except ValueError:
        raise ValueError("Seed / worker / limit sayı olmalı.") from None
    if lim is not None and lim < 1:
        raise ValueError("Limit en az 1 olmalı.")
    if sd is not None and sd < 0:
        raise ValueError("Seed negatif olamaz.")
    return SECTIONS[section], sd, w, lim


def tail_text(path, nbytes=16384):
    """Last nbytes of a file as text (seek from the end), '' if unreadable."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - nbytes))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def load_progress(path=PROGRESS):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def kill_tree(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    else:
        os.kill(pid, 15)


# ---- GUI ----

BG, PANEL, CYAN, GREEN, ORANGE, RED, TXT, MUTED = "#070914", "#111426", "#57e5ff", "#59e3a6", "#ffbb72", "#ff708c", "#f5f7ff", "#8c97b8"
FONT = "Segoe UI"


class SatPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.proc = None
        self.console = CONSOLE
        self._job = None
        self._ext = False  # cached: a run (ours from a previous session, or foreign) is alive
        self._ext_pid = None  # adopted orphan pid (pid file)
        self._probe_busy = False
        self._log_pos = 0
        self.section = tk.StringVar(value="İkisi")
        self.seed = tk.StringVar()
        self.workers = tk.StringVar(value="10")
        self.limit = tk.StringVar()
        self.dry = tk.BooleanVar(value=True)
        self.save = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Hazır")
        self._build()
        self._probe()
        self._job = self.after(500, self._tick)

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
            return parse_args(self.section.get(), self.seed.get(), self.workers.get(), self.limit.get())
        except ValueError as e:
            messagebox.showerror("SAT", str(e), parent=self)
            return None

    def _probe(self):
        """Pid/progress check on a worker thread; the UI only reads the cached result."""
        if self._probe_busy:
            return
        self._probe_busy = True

        def work():
            try:
                pid = read_pid()
                self._ext_pid = pid if pid is not None and _pid_alive(pid) else None
                self._ext = self._ext_pid is not None or (pid is None and is_running())
            finally:
                self._probe_busy = False

        threading.Thread(target=work, daemon=True).start()

    def _hint(self):
        seed = self.seed.get().strip()
        ok = seed.isdigit() and (OUT / f"{session_id(seed, SECTIONS[self.section.get()])}.json").exists()
        self.resume.config(text="Aynı seed ile devam eder (oturum dosyası var)." if ok else "")

    def _spawn(self, cmd, env):
        OUT.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        try:
            o, e = open(CONSOLE, "wb"), open(CONSOLE_ERR, "wb")
            self.console = CONSOLE
        except OSError:  # held by another writer: never truncate, use a fresh file
            o, e = open(OUT / f"console_{ts}.log", "wb"), open(OUT / f"console_{ts}.err", "wb")
            self.console = Path(o.name)
        with o, e:
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
        if (self.proc and self.proc.poll() is None) or self._ext:
            messagebox.showwarning("SAT", "Zaten çalışan bir oturum var.", parent=self)
            return True
        return False

    def _live_pid(self):
        if self.proc and self.proc.poll() is None:
            return self.proc.pid
        return self._ext_pid

    def stop(self):
        pid = self._live_pid()
        if pid and messagebox.askyesno("SAT", "Üretim durdurulsun mu?", parent=self):
            kill_tree(pid)

    def open_out(self):
        OUT.mkdir(parents=True, exist_ok=True)
        os.startfile(OUT) if os.name == "nt" else subprocess.Popen(["xdg-open", str(OUT)])

    def confirm_close(self):
        """True = ok to close the window (run left going, stopped, or none)."""
        if self._live_pid():
            ans = messagebox.askyesnocancel("SAT", "Üretim sürüyor. Arka planda devam etsin mi?\nEvet: devam, Hayır: durdur, İptal: kapatma.", parent=self)
            if ans is None:
                return False
            if ans is False:
                kill_tree(self._live_pid())
        return True

    def destroy(self):
        if self._job:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        super().destroy()

    def _tick(self):
        self._job = None
        try:
            if not self.winfo_exists():
                return
            self._probe()
            p = parse_progress(load_progress())
            self.pb["value"] = p["percent"]
            for k, l in self.lbl.items():
                l.config(text=p[k] + (f"  (geçen {p['elapsed']})" if k == "eta" else ""))
            if self.console.exists() and self.console.stat().st_size != self._log_pos:
                with open(self.console, "rb") as fh:
                    fh.seek(self._log_pos)
                    chunk = fh.read(65536)
                    self._log_pos = fh.tell()
                self.log.config(state="normal")
                self.log.insert("end", chunk.decode("utf-8", "replace"))
                self.log.see("end")
                self.log.config(state="disabled")
            own = bool(self.proc and self.proc.poll() is None)
            running = own or self._ext
            for b in self.btns:
                b.config(state="disabled" if running else "normal")
            self.stop_btn.config(state="normal" if running else "disabled")
            if self.proc and not own:
                rc, self.proc = self.proc.returncode, None
                PIDFILE.unlink(missing_ok=True)
                self.status.set(f"Bitti (kod {rc})")
                tail = tail_text(self.console).splitlines()[-12:]
                summ = [x for x in tail if "accepted" in x or "INCOMPLETE" in x] or tail[-3:]
                messagebox.showinfo("SAT özet", "\n".join(summ) or f"Çıkış kodu {rc}", parent=self)
            elif self._ext_pid:
                self.status.set(f"Önceki oturumdan çalışan üretim (pid {self._ext_pid})")
            self._job = self.after(2000, self._tick)
        except tk.TclError:
            pass


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
