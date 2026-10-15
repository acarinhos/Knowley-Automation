"""Live console progress lines, a 30 s heartbeat and an atomically written progress.json."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from . import llm


def fmt(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}h{sec % 3600 // 60:02d}" if sec >= 3600 else f"{sec // 60}m{sec % 60:02d}s"


class Progress:
    def __init__(self, path=None, targets=None, done=None, echo=True, heartbeat=30):
        self.path = Path(path) if path else None
        self.targets = dict(targets or {})  # section -> slot count
        self.done = {k: 0 for k in self.targets}
        self.done.update(done or {})
        self.start_done = sum(self.done.values())
        self.rejected = self.failed = 0
        self.echo, self.heartbeat = echo, heartbeat
        self.t0 = time.monotonic()
        self.lock = threading.RLock()
        self._stop = threading.Event()

    def _say(self, line):
        if self.echo:
            print(line, flush=True)

    def _counts(self):
        return " ".join(f"{k} {self.done.get(k, 0)}/{v}" for k, v in self.targets.items())

    def snapshot(self) -> dict:
        el = time.monotonic() - self.t0
        n, total = sum(self.done.values()), sum(self.targets.values())
        new = n - self.start_done
        eta = (total - n) * el / new if new > 0 else None
        return {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "accepted": n, "total": total, "failed": self.failed,
                "rejected": self.rejected, "sections": {k: {"done": self.done.get(k, 0), "target": v} for k, v in self.targets.items()},
                "elapsed_s": round(el), "eta_s": None if eta is None else round(eta), "providers": llm.stats()}

    def _write(self):
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.snapshot(), indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:  # a reader may hold the file on Windows; the next write wins
            pass

    def event(self, slot, status, provider="", secs=0.0, note=""):
        """status: OK | REJ | FAIL"""
        with self.lock:
            if status == "OK":
                self.done[slot.section] = self.done.get(slot.section, 0) + 1
            elif status == "REJ":
                self.rejected += 1
            else:
                self.failed += 1
            s = self.snapshot()
            eta = fmt(s["eta_s"]) if s["eta_s"] is not None else "?"
            self._say(f"[{s['accepted']:>3}/{s['total']}] {slot.section} m{slot.module} {slot.difficulty} {slot.skill}  {status:<4} "
                      f"{provider} {secs:.0f}s{' ' + note[:110] if note else ''} | {self._counts()} | rej {self.rejected} | "
                      f"fail {self.failed} | elapsed {fmt(s['elapsed_s'])} | ETA ~{eta}")
            self._write()

    def beat(self):
        with self.lock:
            s = self.snapshot()
            prov = " ".join(f"{n}:{v['inflight']}fly/{v['n429']}x429{'/cool' + str(v['cooldown_s']) + 's' if v['cooldown_s'] else ''}"
                            for n, v in s["providers"].items())
            self._say(f"[heartbeat] {s['accepted']}/{s['total']} | {prov} | elapsed {fmt(s['elapsed_s'])}")
            self._write()

    def __enter__(self):
        self._write()
        if self.heartbeat:
            def loop():
                while not self._stop.wait(self.heartbeat):
                    self.beat()
            threading.Thread(target=loop, daemon=True).start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._write()
