"""Read-only report over the `generated_sat_questions` collection (`--report`, 'Firebase Raporu' window).
Only reads: fetch_docs() streams a projected query; build_report() is pure."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from .config import FIRESTORE_COLLECTION

FIELDS = ("section", "module", "difficulty", "domain", "skill", "answer_type", "correct_answer", "stem", "session_id", "status", "active",
          "created_at", "has_visual", "visual.spec.kind", "meta.provider", "meta.model", "meta.created_at", "meta.verified")
TZ_NAME = "Europe/Istanbul"
UNKNOWN = "bilinmiyor"


def _slim(d: dict) -> dict:
    """Collapse the projected visual stub (visual.spec.kind only; never the SVG) into a has_visual flag."""
    if "visual" in d:
        d["has_visual"] = bool(d.pop("visual"))
    return d


def fetch_docs() -> list[dict]:
    """Stream the collection (projected fields only). Empty/nonexistent collection -> []. Raises on credential/network errors."""
    from .store import get_db  # initialises firebase_admin; this module never writes
    col = get_db().collection(FIRESTORE_COLLECTION)
    return [_slim(d.to_dict() or {}) for d in col.select(list(FIELDS)).stream(timeout=120)]


def _tz(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name), None
    except Exception:  # noqa: BLE001  (tzdata missing on Windows)
        return timezone(timedelta(hours=3)), f"{name} zaman dilimi bulunamadı; sabit UTC+3 kullanıldı"


def created_time(doc: dict) -> Optional[datetime]:
    """top-level created_at, else meta.created_at; datetime or ISO string -> aware UTC datetime, else None."""
    for v in (doc.get("created_at"), (doc.get("meta") or {}).get("created_at")):
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if isinstance(v, str) and v.strip():
            try:
                d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
                return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _count(docs, fn) -> dict:
    c = Counter(str(x) for x in (fn(d) for d in docs) if x not in (None, ""))
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def _has_visual(d):
    return bool(d["has_visual"]) if "has_visual" in d else bool(d.get("visual"))


def build_report(docs: Iterable[dict], now: Optional[datetime] = None, tz: str = TZ_NAME, latest: int = 20) -> dict:
    docs = list(docs)
    zone, note = _tz(tz)
    now = (now or datetime.now(timezone.utc))
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    today = now.astimezone(zone).date()
    timed = [(t, d) for d in docs if (t := created_time(d))]
    days = Counter(t.astimezone(zone).date() for t, _ in timed)
    since = lambda n: sum(c for day, c in days.items() if today - timedelta(days=n - 1) <= day <= today)  # noqa: E731
    meta = lambda d, k: (d.get("meta") or {}).get(k)  # noqa: E731
    r = {
        "collection": FIRESTORE_COLLECTION, "timezone": tz, "generated_at": now.astimezone(zone).isoformat(timespec="seconds"),
        "total": len(docs),
        "counts": {"today": since(1), "last_7_days": since(7), "last_30_days": since(30), "all_time": len(docs), "unknown_date": len(docs) - len(timed)},
        "per_day_30": {(today - timedelta(days=i)).isoformat(): days.get(today - timedelta(days=i), 0) for i in range(29, -1, -1)},
        "by_section": _count(docs, lambda d: d.get("section")),
        "by_domain": _count(docs, lambda d: d.get("domain")),
        "by_skill": _count(docs, lambda d: d.get("skill")),
        "by_difficulty": _count(docs, lambda d: d.get("difficulty")),
        "by_answer_type": _count(docs, lambda d: d.get("answer_type")),
        "by_has_visual": _count(docs, lambda d: "visual var" if _has_visual(d) else "görsel yok"),
        "by_module": _count(docs, lambda d: d.get("module")),
        "by_session": _count(docs, lambda d: d.get("session_id")),
        "by_provider": _count(docs, lambda d: meta(d, "provider")),
        "by_model": _count(docs, lambda d: meta(d, "model")),
    }
    kl = Counter(str(d.get("correct_answer", "")).strip().upper() for d in docs if d.get("answer_type") == "mcq")
    r["key_letters"] = {k: kl.get(k, 0) for k in "ABCD"} if kl else {}
    if kl and sum(kl.values()) > sum(r["key_letters"].values()):
        r["key_letters"]["other"] = sum(kl.values()) - sum(r["key_letters"].values())
    if any("active" in d or str(d.get("status", "")).lower() in ("active", "passive", "inactive") for d in docs):
        r["active_passive"] = _count(docs, lambda d: ("aktif" if d["active"] else "pasif") if "active" in d else
                                     ("aktif" if str(d.get("status")).lower() == "active" else "pasif" if d.get("status") else None))
    for key, fn in (("status", lambda d: d.get("status")), ("verified", lambda d: meta(d, "verified"))):
        c = _count(docs, fn)
        if c:
            r[f"by_{key}"] = c
    r["latest"] = [{"created": t.astimezone(zone).strftime("%Y-%m-%d %H:%M"), "section": d.get("section"), "domain": d.get("domain"),
                    "skill": d.get("skill"), "difficulty": d.get("difficulty"),
                    "stem": " ".join(str(d.get("stem") or "").split())[:90]}
                   for t, d in sorted(timed, key=lambda x: x[0], reverse=True)[:latest]]
    if note:
        r["note"] = note
    return r


def format_text(r: dict) -> str:
    c = r["counts"]
    out = [f"Firebase Raporu - {r['collection']} ({r['timezone']}, {r['generated_at']})",
           f"Toplam {r['total']} | Bugün {c['today']} | 7 gün {c['last_7_days']} | 30 gün {c['last_30_days']} | Tüm zamanlar {c['all_time']} | Tarihsiz {c['unknown_date']}"]
    if r.get("note"):
        out.append("Not: " + r["note"])
    for key, title in (("by_section", "Bölüm"), ("by_domain", "Alan"), ("by_skill", "Konu"), ("by_difficulty", "Zorluk"), ("by_answer_type", "Tür"),
                       ("by_has_visual", "Görsel"), ("by_module", "Modül"), ("by_session", "Oturum"), ("by_provider", "Sağlayıcı"), ("by_model", "Model"),
                       ("key_letters", "Anahtar harfi (mcq)"), ("active_passive", "Aktif/pasif"), ("by_status", "Durum"), ("by_verified", "Doğrulama")):
        if r.get(key):
            out += ["", f"[{title}]"] + [f"  {k}: {v}" for k, v in r[key].items()]
    out += ["", "[Son 30 gün]", "  " + (" ".join(f"{k[5:]}:{v}" for k, v in r["per_day_30"].items() if v) or "-"), "", "[Son eklenenler]"]
    out += [f"  {x['created']} {x['section']} {x['difficulty']} {x['skill']} | {x['stem']}" for x in r["latest"]] or ["  -"]
    return "\n".join(out)
