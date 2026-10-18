"""Tkinter panel to start/monitor SAT generation runs as a subprocess (python -m sat_engine ...)."""
from __future__ import annotations

import csv
import json
import os
import queue
import random
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from . import llm

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "output"
PROGRESS, CONSOLE, CONSOLE_ERR, PIDFILE = (OUT / n for n in ("progress.json", "console.log", "console.err", "ui_run.pid"))
PREFS = OUT / "ui_prefs.json"
WRITE_FLAG = "KNOWLEY_GENERATION_WRITES_ENABLED"
COLLECTION = "generated_sat_questions"
TARGETS = {"rw": 54, "math": 44}
FRESH_S = 90
SECTIONS = {"R&W": "rw", "Math": "math", "İkisi": "both"}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ---- plain logic (unit-tested, no GUI) ----

def session_id(seed, section):
    return f"seed{seed}-{'-'.join(('rw', 'math') if section == 'both' else (section,))}"


def build_command(section="both", seed=None, workers=10, save=False, limit=None, extra=(), providers=None, models=None):
    """extra=('--audit',) etc. builds a utility command; otherwise a generation run."""
    cmd = [sys.executable, "-u", "-m", "sat_engine", *extra, "--section", section]
    if not extra:
        cmd += ["--workers", str(workers)]
        if limit:
            cmd += ["--limit", str(limit)]
        if save:
            cmd.append("--save")
        if providers:
            cmd += ["--providers", ",".join(providers)]
        if models:
            cmd += ["--models", ",".join(f"{k}={v}" for k, v in models.items())]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    return cmd


def load_prefs(path=None):
    """{'order': [...], 'enabled': [...], 'models': {...}}; {} if missing/corrupt. No secrets are ever stored."""
    try:
        d = json.loads(Path(path or PREFS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    out = {}
    for k in ("order", "enabled"):
        v = d.get(k)
        if isinstance(v, list) and all(isinstance(x, str) and x in llm.KNOWN for x in v):
            out[k] = v
    m = d.get("models")
    if isinstance(m, dict) and all(isinstance(a, str) and isinstance(b, str) for a, b in m.items()):
        out["models"] = m
    return out


def save_prefs(prefs, path=None):
    p = Path(path or PREFS)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(prefs, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def selection(order, enabled, models, defaults):
    """-> (providers in order, {provider: model} for models that differ from the default). ValueError if nothing is selected."""
    prov = [p for p in order if p in enabled]
    if not prov:
        raise ValueError("En az bir sağlayıcı seçin.")
    return prov, {p: m.strip() for p in prov if (m := models.get(p, "").strip()) and m.strip() != defaults.get(p)}


def provider_health(d, name):
    v = ((d or {}).get("providers") or {}).get(name) or {}
    if v.get("cooldown_s"):
        return f"bekleme {v['cooldown_s']}s (429x{v.get('n429', 0)})"
    return f"429x{v['n429']}" if v.get("n429") else ""


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


def global_llm_defaults():
    """-> (providers, {provider: model}) from core.llm_settings; ([], {}) when unavailable. claude is never included.
    A model is pinned only when the user narrowed that provider to exactly one."""
    try:
        from core import llm_settings as g
        sel = [p for p in g.load_selection() if p in llm.KNOWN]
        models = g.load_models()
        return sel, {p: m[0] for p, m in models.items() if p in sel and len(m) == 1 and m[0] != llm.model_for(p)}
    except Exception:
        return [], {}


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
        pr = load_prefs()
        self.avail = [p for p in llm.DEFAULT_ORDER if llm.provider_available(p)]
        self.defaults = {p: llm.model_for(p) for p in llm.KNOWN}
        saved = [p for p in pr.get("order") or [] if p in llm.KNOWN]
        self.order = saved + [p for p in llm.KNOWN if p not in saved]
        gsel, gmodels = global_llm_defaults()  # main app's provider/model boxes; used only when nothing is saved here
        self.enabled = {p: tk.BooleanVar(value=p in (pr["enabled"] if "enabled" in pr else (gsel or self.avail))) for p in llm.KNOWN}
        self.models = {p: tk.StringVar(value=(pr.get("models") or {}).get(p) or gmodels.get(p) or self.defaults[p]) for p in llm.KNOWN}
        self._claude = None  # filled by the probe thread (claude_ready() runs a subprocess)
        self._claude_shown = False
        self.health = {}
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
        self.pf = tk.LabelFrame(f, text=" Modeller / Sağlayıcılar ", bg=PANEL, fg=CYAN, font=(FONT, 9, "bold"), padx=8, pady=4)
        self.pf.pack(fill="x", pady=(8, 0))
        self._render_prov()
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
                  font=(FONT, 9, "bold"), padx=10, pady=5).pack(side="left", padx=(0, 8))
        tk.Button(bar, text="Firebase Raporu", command=lambda: ReportWindow(self), bg="#161b34", fg=GREEN, relief="flat",
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

    def _shown(self):
        return [p for p in self.order if p in self.avail or (p == "claude" and self._claude)]

    def _render_prov(self):
        for w in self.pf.winfo_children():
            w.destroy()
        self.health = {}
        shown = self._shown()
        for i, p in enumerate(shown):
            r = tk.Frame(self.pf, bg=PANEL)
            r.pack(fill="x")
            ttk.Checkbutton(r, text=p, variable=self.enabled[p], width=9, command=self._render_prov).pack(side="left")
            ttk.Entry(r, textvariable=self.models[p], width=38).pack(side="left", padx=4)
            for t, d in (("▲", -1), ("▼", 1)):
                tk.Button(r, text=t, command=lambda p=p, d=d: self._move(p, d), bg="#161b34", fg=CYAN, relief="flat", width=2).pack(side="left", padx=1)
            self.health[p] = tk.Label(r, text="", bg=PANEL, fg=ORANGE, font=(FONT, 9))
            self.health[p].pack(side="left", padx=6)
        tk.Button(self.pf, text="Varsayılana dön", command=self._reset_prov, bg="#161b34", fg=CYAN, relief="flat",
                  font=(FONT, 9)).pack(anchor="w", pady=(4, 0))
        if sum(self.enabled[p].get() for p in shown) == 1:
            tk.Label(self.pf, text="Tek sağlayıcı: hakem üreticiyle aynı olacak", bg=PANEL, fg=ORANGE, font=(FONT, 9)).pack(anchor="w")
        if not shown:
            tk.Label(self.pf, text="API anahtarı olan sağlayıcı yok.", bg=PANEL, fg=RED, font=(FONT, 9)).pack(anchor="w")

    def _move(self, p, d):
        sh = self._shown()
        j = sh.index(p) + d
        if 0 <= j < len(sh):
            a, b = self.order.index(p), self.order.index(sh[j])
            self.order[a], self.order[b] = self.order[b], self.order[a]
            self._render_prov()

    def _reset_prov(self):
        self.order = list(llm.KNOWN)
        for p in llm.KNOWN:
            self.enabled[p].set(p in self.avail)
            self.models[p].set(self.defaults[p])
        self._render_prov()

    def _providers(self):
        """-> (providers, models) or None after a friendly error; remembers the choice."""
        try:
            sel = selection(self._shown(), {p for p in llm.KNOWN if self.enabled[p].get()}, {p: v.get() for p, v in self.models.items()}, self.defaults)
        except ValueError as e:
            messagebox.showerror("SAT", str(e), parent=self)
            return None
        bad = [f"{p}: {v.get().strip()}" for p, v in self.models.items() if v.get().strip() and not llm.valid_model(v.get().strip())]
        if bad:
            messagebox.showerror("SAT", "Geçersiz model adı (yalnızca harf/rakam/._:/@+-, '-' ile başlamaz): " + ", ".join(bad), parent=self)
            return None
        save_prefs({"order": self.order, "enabled": [p for p in llm.KNOWN if self.enabled[p].get()],
                    "models": {p: v.get().strip() for p, v in self.models.items() if v.get().strip() != self.defaults[p]}})
        return sel

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
                if self._claude is None:
                    self._claude = llm.claude_ready()
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
        sel = self._providers()
        if not sel:
            return
        seed = random.randint(1, 999999) if seed is None else seed
        save = bool(self.save.get()) and not self.dry.get()
        if save:
            n = f"en çok {lim} soru/bölüm" if lim else ("54 R&W + 44 Math = 98 soru" if sec == "both" else f"{TARGETS[sec]} soru")
            if not messagebox.askyesno("Firebase'e kaydet", f"{n} '{COLLECTION}' koleksiyonuna YAZILACAK. Devam edilsin mi?", parent=self):
                return
        self.seed.set(str(seed))
        self._spawn(build_command(sec, seed, w, save, lim, providers=sel[0], models=sel[1]), build_env(save, save))

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
            d = load_progress()
            p = parse_progress(d)
            if self._claude and not self._claude_shown:
                self._claude_shown = True
                self._render_prov()
            for n, l in self.health.items():
                l.config(text=provider_health(d, n))
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


REPORT_JSON = OUT / "sat_report.json"
CARDS = (("Toplam", "total", CYAN), ("Bugün", "today", GREEN), ("7 gün", "last_7_days", GREEN), ("30 gün", "last_30_days", ORANGE),
         ("Tüm zamanlar", "all_time", CYAN))
GROUPS = (("by_section", "Bölüm"), ("by_domain", "Alan"), ("by_skill", "Konu"), ("by_difficulty", "Zorluk"), ("by_answer_type", "Tür"),
          ("by_has_visual", "Görsel"), ("by_module", "Modül"), ("key_letters", "Anahtar harfi (mcq)"), ("active_passive", "Aktif/pasif"),
          ("by_status", "Durum"), ("by_verified", "Doğrulama"), ("by_provider", "Sağlayıcı"), ("by_model", "Model"), ("by_session", "Oturum"))


def friendly_error(e):
    t = f"{type(e).__name__}: {str(e)[:160]}"
    if "redential" in t or "No such file" in t or "Errno 2" in t or "FileNotFound" in t or "Certificate" in t or "IsADirectory" in t:
        return "Firebase kimlik bilgileri bulunamadı (FIREBASE_CREDENTIALS_PATH)."
    if any(w in t for w in ("Unavailable", "DeadlineExceeded", "ConnectionError", "socket", "timed out", "Timeout")):
        return "Firebase'e ulaşılamadı (çevrimdışı olabilir)."
    return "Rapor alınamadı: " + t


class ReportWindow(tk.Toplevel):
    """Read-only 'Firebase Raporu': the fetch runs on a worker thread, results come back through a queue polled with after()."""

    def __init__(self, parent, fetch=None):
        super().__init__(parent)
        self.title("Firebase Raporu - SAT")
        self.geometry("980x680")
        self.configure(bg=BG)
        self.fetch, self.q, self.report, self._job, self._busy = fetch, queue.Queue(), None, None, False
        self.status = tk.StringVar()
        st = ttk.Style(self)
        st.configure("Sat.Treeview", background="#0a0e22", fieldbackground="#0a0e22", foreground=TXT, rowheight=22, font=(FONT, 9))
        st.configure("Sat.Treeview.Heading", background="#161b34", foreground=CYAN, font=(FONT, 9, "bold"))
        st.map("Sat.Treeview", background=[("selected", "#23294c")])
        top = tk.Frame(self, bg=BG, padx=12, pady=10)
        top.pack(fill="x")
        self.cards = {}
        for lbl, key, col in CARDS:
            c = tk.Frame(top, bg=PANEL, padx=14, pady=8, highlightbackground="#23294c", highlightthickness=1)
            c.pack(side="left", padx=(0, 8))
            tk.Label(c, text=lbl.upper(), bg=PANEL, fg=MUTED, font=(FONT, 8, "bold")).pack(anchor="w")
            self.cards[key] = tk.Label(c, text="-", bg=PANEL, fg=col, font=(FONT, 18, "bold"))
            self.cards[key].pack(anchor="w")
        for text, cmd in (("JSON olarak kaydet", self.export), ("Yenile", self.refresh)):
            tk.Button(top, text=text, command=cmd, bg="#161b34", fg=CYAN, relief="flat", font=(FONT, 9, "bold"), padx=10, pady=5).pack(side="right", padx=(8, 0))
        tk.Label(self, textvariable=self.status, bg=BG, fg=ORANGE, font=(FONT, 9), anchor="w", padx=12).pack(fill="x")
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=8)
        self.t_time = self._table(nb, "Günlük / Haftalık / Aylık", ("Dönem", "Adet"))
        self.t_cat = self._table(nb, "Bölüm / Alan / Konu", ("Kategori", "Değer", "Adet"))
        self.t_diff = self._table(nb, "Zorluk / Tür", ("Kategori", "Değer", "Adet"))
        self.t_last = self._table(nb, "Son eklenenler", ("Zaman", "Bölüm", "Alan / Konu", "Zorluk", "Soru"))
        self.refresh()
        self._job = self.after(150, self._poll)

    def _table(self, nb, title, cols):
        f = tk.Frame(nb, bg=BG)
        nb.add(f, text=title)
        t = ttk.Treeview(f, columns=cols, show="headings", style="Sat.Treeview")
        for c in cols:
            t.heading(c, text=c)
            t.column(c, width=420 if c in ("Soru", "Değer") else 120, anchor="w")
        sb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        return t

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        self.status.set("Yükleniyor…")

        def work():
            try:
                from .report import build_report, fetch_docs
                self.q.put(("ok", build_report((self.fetch or fetch_docs)())))
            except Exception as e:  # noqa: BLE001
                self.q.put(("err", friendly_error(e)))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        self._job = None
        try:
            if not self.winfo_exists():
                return
            while True:
                try:
                    kind, val = self.q.get_nowait()
                except queue.Empty:
                    break
                self._busy = False
                if kind == "ok":
                    try:
                        self.show(val)
                    except tk.TclError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        self.status.set(f"Rapor gösterilemedi: {friendly_error(e)}")
                else:
                    self.status.set(val)
        except tk.TclError:
            return
        try:
            if self.winfo_exists():
                self._job = self.after(150, self._poll)
        except tk.TclError:
            pass

    def show(self, r):
        self.report = r
        c = dict(r["counts"], total=r["total"])
        for k, l in self.cards.items():
            l.config(text=str(c[k]))
        msg = ("Not: " + r["note"] + "  ") if r.get("note") else ""
        msg += "Koleksiyon boş." if not r["total"] else ""
        msg += f"Tarihsiz kayıt: {c['unknown_date']}" if c["unknown_date"] else ""
        self.status.set(msg)
        for t in (self.t_time, self.t_cat, self.t_diff, self.t_last):
            t.delete(*t.get_children())
        for lbl, key in (("Bugün", "today"), ("Son 7 gün", "last_7_days"), ("Son 30 gün", "last_30_days"), ("Tüm zamanlar", "all_time"),
                         ("Tarihsiz", "unknown_date")):
            self.t_time.insert("", "end", values=(lbl, c[key]))
        self.t_time.insert("", "end", values=("-- günlük (30 gün) --", ""))
        for day, n in reversed(list(r["per_day_30"].items())):
            self.t_time.insert("", "end", values=(day, n))
        for key, title in GROUPS:
            tgt = self.t_cat if key in ("by_section", "by_domain", "by_skill") else self.t_diff
            for k, v in (r.get(key) or {}).items():
                tgt.insert("", "end", values=(title, k, v))
        for x in r["latest"]:
            self.t_last.insert("", "end", values=(x["created"], x["section"], f"{x['domain']} / {x['skill']}", x["difficulty"], x["stem"]))

    def export(self):
        if not self.report:
            messagebox.showinfo("Firebase Raporu", "Önce rapor yüklenmeli.", parent=self)
            return
        OUT.mkdir(parents=True, exist_ok=True)
        REPORT_JSON.write_text(json.dumps(self.report, ensure_ascii=False, indent=1), encoding="utf-8")
        self.status.set(f"Kaydedildi: {REPORT_JSON}")

    def destroy(self):
        if self._job:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        super().destroy()


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
