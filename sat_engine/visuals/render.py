"""Deterministic rendering (matplotlib Agg, no pyplot global state), ground-truth facts and alt text."""
from __future__ import annotations

import io
import math
import statistics
from collections import Counter

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Arc, Circle as MCircle, Polygon as MPoly, Rectangle as MRect, Wedge
from matplotlib.ticker import MultipleLocator

from .spec import angle_between, curve_fn, parallel_angle, parse_spec, poly_area, side_len, tri_angles

_RC = {"svg.hashsalt": "sat-visuals", "svg.fonttype": "path"}
FS = 10


def _r(v, n=6):
    if isinstance(v, dict):
        return {k: _r(x, n) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_r(x, n) for x in v]
    if isinstance(v, float):
        return round(v, n) + 0.0
    return v


def _f(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _t(s):  # escape mathtext
    return s.replace("$", r"\$")


def _step(span):
    raw = span / 8
    mag = 10 ** math.floor(math.log10(raw))
    return next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)


# ---------------------------------------------------------------- figure helpers
def _fig(s, w=4.4, h=3.6):
    fig = Figure(figsize=(w, h), dpi=100, facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    if s.title:
        ax.set_title(_t(s.title), fontsize=FS + 1, fontweight="bold")
    return fig, ax


def _plane(ax, s, xr, yr):
    ax.set_xlim(*xr), ax.set_ylim(*yr)
    ax.xaxis.set_major_locator(MultipleLocator(_step(xr[1] - xr[0])))
    ax.yaxis.set_major_locator(MultipleLocator(_step(yr[1] - yr[0])))
    ax.grid(True, color="#cccccc", lw=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    if xr[0] <= 0 <= xr[1]:
        ax.spines["left"].set_position("zero")
    if yr[0] <= 0 <= yr[1]:
        ax.spines["bottom"].set_position("zero")
    ax.tick_params(labelsize=FS - 1)
    if s.x_label:
        ax.set_xlabel(_t(s.x_label), fontsize=FS)
    if s.y_label:
        ax.set_ylabel(_t(s.y_label), fontsize=FS)


def _xs(xr, n=600):
    return np.linspace(xr[0], xr[1], n)


def _geo(fig, ax, pts, pad=0.18):
    pts = np.array(pts, float)
    lo, hi = pts.min(0), pts.max(0)
    span = max(hi - lo)
    ax.set_xlim(lo[0] - pad * span, hi[0] + pad * span)
    ax.set_ylim(lo[1] - pad * span, hi[1] + pad * span)
    ax.set_aspect("equal")
    ax.axis("off")


def _txt(ax, xy, s, **kw):
    kw.setdefault("ha", "center"), kw.setdefault("va", "center")
    kw.setdefault("fontsize", FS + 1)
    ax.text(xy[0], xy[1], _t(s), **kw)


def _outward(p, q, c, d):
    m = (np.array(p) + np.array(q)) / 2
    n = np.array([q[1] - p[1], p[0] - q[0]], float)
    n /= np.linalg.norm(n)
    if np.dot(n, m - np.array(c)) < 0:
        n = -n
    return m + n * d


def _arc(ax, v, a0, a1, r, text, rt=None):
    """Arc at vertex v from direction a0 to a1 (degrees, ccw), with centered text."""
    ax.add_patch(Arc(v, 2 * r, 2 * r, theta1=a0, theta2=a1, lw=1, color="black"))
    if text:
        mid = math.radians((a0 + a1) / 2)
        rt = rt or r * 1.7
        _txt(ax, (v[0] + rt * math.cos(mid), v[1] + rt * math.sin(mid)), text, fontsize=FS)


def _vertex_arc(ax, V, P, Q, r, text):
    a0 = math.degrees(math.atan2(P[1] - V[1], P[0] - V[0]))
    a1 = math.degrees(math.atan2(Q[1] - V[1], Q[0] - V[0]))
    d = (a1 - a0) % 360
    if d > 180:
        a0, d = a1, 360 - d
    _arc(ax, V, a0, a0 + d, r, text)


def _right_mark(ax, V, P, Q, s):
    u = (np.array(P) - V) / np.linalg.norm(np.array(P) - V) * s
    w = (np.array(Q) - V) / np.linalg.norm(np.array(Q) - V) * s
    ax.plot(*zip(V + u, V + u + w, V + w), color="black", lw=1)


# ---------------------------------------------------------------- facts
def _curve_facts(c):
    f = curve_fn(c)
    d = {"type": c.type, "y_intercept": float(f(0.0))}
    t = c.type
    if t == "linear":
        d.update(slope=c.m, x_intercepts=[-c.b / c.m] if c.m else [])
    elif t in ("quadratic_vertex", "quadratic_standard"):
        a, b, cc = (c.a, -2 * c.a * c.h, c.a * c.h**2 + c.k) if t == "quadratic_vertex" else (c.a, c.b, c.c)
        h = -b / (2 * a)
        disc = b * b - 4 * a * cc
        roots = [] if disc < 0 else sorted({(-b - math.sqrt(disc)) / (2 * a), (-b + math.sqrt(disc)) / (2 * a)})
        d.update(a=a, b=b, c=cc, vertex=[h, float(f(h))], axis_of_symmetry=h, discriminant=disc,
                 x_intercepts=roots, opens="up" if a > 0 else "down")
    elif t == "exponential":
        q = -c.c / c.a
        d.update(horizontal_asymptote=c.c, growth_factor=c.base, x_intercepts=[math.log(q) / math.log(c.base)] if q > 0 else [])
    else:
        q = -c.k / c.a
        d.update(vertex=[c.h, c.k], x_intercepts=[] if q < 0 else sorted({c.h - q, c.h + q}), opens="up" if c.a > 0 else "down")
    return d


def _lin_intersection(a, b):
    if a.m == b.m:
        return {"relation": "coincident" if a.b == b.b else "parallel", "intersection": None}
    x = (b.b - a.b) / (a.m - b.m)
    return {"relation": "intersecting", "intersection": [x, a.m * x + a.b]}


def _facts(s):
    k = s.kind
    if k == "function_graph":
        return {"curves": [_curve_facts(c) for c in s.curves]}
    if k == "scatter_with_line":
        x, y = (np.array(v, float) for v in zip(*s.points))
        sx = ((x - x.mean()) ** 2).sum()
        m = ((x - x.mean()) * (y - y.mean())).sum() / sx
        b = y.mean() - m * x.mean()
        syy = ((y - y.mean()) ** 2).sum()
        r = float(((x - x.mean()) * (y - y.mean())).sum() / math.sqrt(sx * syy)) if syy > 0 else 0.0
        d = {"n": len(x), "mean_x": x.mean(), "mean_y": y.mean(), "slope": m, "intercept": b, "r": r, "r_squared": r * r}
        if s.line:
            d.update(line_slope=s.line.m, line_intercept=s.line.b, residuals=[float(yy - (s.line.m * xx + s.line.b)) for xx, yy in zip(x, y)])
        return d
    if k == "bar_chart":
        v = s.values
        return {"total": sum(v), "max": {"category": s.categories[v.index(max(v))], "value": max(v)},
                "min": {"category": s.categories[v.index(min(v))], "value": min(v)}, "values": dict(zip(s.categories, v)), "mean": sum(v) / len(v)}
    if k == "histogram":
        c, e = s.counts, s.bin_edges
        i = c.index(max(c))
        return {"total": sum(c), "tallest_bin": [e[i], e[i + 1]], "bin_widths": [b - a for a, b in zip(e, e[1:])], "counts": c}
    if k == "dot_plot":
        v = s.values
        cnt = Counter(v)
        top = max(cnt.values())
        return {"n": len(v), "mean": sum(v) / len(v), "median": statistics.median(v), "mode": sorted(x for x, n in cnt.items() if n == top),
                "range": max(v) - min(v), "min": min(v), "max": max(v)}
    if k == "table":
        sums = {}
        for j, col in enumerate(s.columns):
            vals = [r[j] for r in s.rows]
            if all(isinstance(x, (int, float)) for x in vals):
                sums[col] = sum(vals)
        return {"n_rows": len(s.rows), "column_sums": sums}
    if k == "number_line":
        return {"points": [p.value for p in s.points], "intervals": [i.model_dump() for i in s.intervals]}
    if k == "coordinate_lines":
        d = _lin_intersection(s.lines[0], s.lines[1])
        d["slopes"] = [ln.m for ln in s.lines]
        d["y_intercepts"] = [ln.b for ln in s.lines]
        return d
    if k == "circle_sector":
        r, th = s.radius, s.central_angle
        return {"arc_length": 2 * math.pi * r * th / 360, "sector_area": math.pi * r * r * th / 360, "fraction": th / 360,
                "radius": r, "central_angle": th, "radians": math.radians(th), "chord_length": 2 * r * math.sin(math.radians(th) / 2)}
    sh = s.shape
    if sh == "triangle":
        a, b, c = s.sides
        p = (a + b + c) / 2
        area = math.sqrt(p * (p - a) * (p - b) * (p - c))
        ang = tri_angles(a, b, c)
        return {"sides": {"a": a, "b": b, "c": c}, "angles": ang, "perimeter": 2 * p, "area": area,
                "type": "equilateral" if a == b == c else "isosceles" if len({a, b, c}) < 3 else "scalene"}
    if sh == "right_triangle":
        c = math.hypot(s.a, s.b)
        return {"sides": {"a": s.a, "b": s.b, "c": c}, "hypotenuse": c, "angles": {"A": math.degrees(math.atan2(s.a, s.b)),
                "B": math.degrees(math.atan2(s.b, s.a)), "C": 90.0}, "area": s.a * s.b / 2, "perimeter": s.a + s.b + c,
                "sin_A": s.a / c, "cos_A": s.b / c, "tan_A": s.a / s.b}
    if sh == "circle":
        r = s.radius
        d = {"radius": r, "diameter": 2 * r, "area": math.pi * r * r, "circumference": 2 * math.pi * r}
        if s.chord_length is not None:
            d["chord_length"] = s.chord_length
            d["chord_distance_from_center"] = math.sqrt(max(r * r - (s.chord_length / 2) ** 2, 0))
        if s.tangent_from_distance is not None:
            d["tangent_length"] = math.sqrt(s.tangent_from_distance**2 - r * r)
            d["distance_to_center"] = s.tangent_from_distance
        return d
    if sh == "rectangle":
        return {"width": s.width, "height": s.height, "area": s.width * s.height, "perimeter": 2 * (s.width + s.height),
                "diagonal": math.hypot(s.width, s.height)}
    if sh == "polygon":
        v = s.vertices
        sides = [side_len(v, i) for i in range(len(v))]
        return {"area": abs(poly_area(v)), "perimeter": sum(sides), "side_lengths": sides, "n_sides": len(v)}
    if sh == "angles":
        return {"angles": [{"rays": lb.rays, "degrees": angle_between(s.ray_angles, *lb.rays)} for lb in s.labels]}
    if sh == "parallel_lines":
        t = s.transversal_angle
        return {"angles": {str(i): parallel_angle(t, i) for i in range(1, 9)}, "acute": min(t, 180 - t), "obtuse": max(t, 180 - t)}
    raise ValueError(f"unsupported spec {k}")


def facts(spec_dict: dict) -> dict:
    """Ground-truth quantities computed from the spec (validated first). Floats rounded to 6 dp."""
    s = parse_spec(spec_dict)
    return _r({"kind": s.kind if s.kind != "geometry" else f"geometry:{s.shape}", **_facts(s)})


# ---------------------------------------------------------------- drawing
def _draw(s):
    k = s.kind
    fig, ax = _fig(s, *((4.4, 2.0) if k == "number_line" else (4.4, 3.6)))
    if k == "function_graph":
        xr, yr = s.x_range, s.y_range
        _plane(ax, s, xr, yr)
        x = _xs(xr)
        for i, c in enumerate(s.curves):
            y = curve_fn(c)(x)
            y = np.where(np.abs(y) > 1e6, np.nan, y)
            ax.plot(x, y, lw=2, color="black", ls=("-", "--", ":")[i], label=_t(c.label) if c.label else None)
            if s.show_intercepts:
                fa = _curve_facts(c)
                pts = [(xi, 0) for xi in fa["x_intercepts"]] + [(0, fa["y_intercept"])]
                for px, py in pts:
                    if xr[0] <= px <= xr[1] and yr[0] <= py <= yr[1]:
                        ax.plot(px, py, "o", color="white", mec="black", ms=6, zorder=5)
        for p in s.points:
            ax.plot(p.x, p.y, "o", color="black", ms=6, zorder=6)
            if p.label:
                ax.annotate(_t(p.label), (p.x, p.y), (6, 6), textcoords="offset points", fontsize=FS)
        if any(c.label for c in s.curves):
            ax.legend(fontsize=FS - 1, loc="best", frameon=True)
    elif k == "scatter_with_line":
        x, y = (np.array(v, float) for v in zip(*s.points))
        pad = lambda a, b: (a - 0.1 * (b - a or 1), b + 0.1 * (b - a or 1))  # noqa: E731
        xr, yr = s.x_range or pad(x.min(), x.max()), s.y_range or pad(y.min(), y.max())
        if not s.x_range and x.min() > 0:
            xr = (0, xr[1])
        if not s.y_range and y.min() > 0:
            yr = (0, yr[1])
        _plane(ax, s, xr, yr)
        ax.plot(x, y, "o", color="black", ms=5, zorder=5)
        xs = _xs(xr)
        if s.fit:
            f = _facts(s)
            ax.plot(xs, f["slope"] * xs + f["intercept"], color="black", lw=1.8)
        if s.line:
            ax.plot(xs, s.line.m * xs + s.line.b, color="black", lw=1.8, ls="--")
    elif k == "bar_chart":
        ax.bar(range(len(s.values)), s.values, color="#888888", edgecolor="black", width=0.7, zorder=3)
        ax.set_xticks(range(len(s.values)), [_t(c) for c in s.categories], fontsize=FS - 1, rotation=0 if max(map(len, s.categories)) < 9 else 30)
        ax.grid(axis="y", color="#cccccc", lw=0.6), ax.set_axisbelow(True)
        ax.tick_params(labelsize=FS - 1)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        if s.show_values:
            for i, v in enumerate(s.values):
                ax.text(i, v, _f(v), ha="center", va="bottom", fontsize=FS - 1)
        ax.set_ylim(0, max(s.values) * 1.15 or 1)
        if s.x_label:
            ax.set_xlabel(_t(s.x_label), fontsize=FS)
        if s.y_label:
            ax.set_ylabel(_t(s.y_label), fontsize=FS)
    elif k == "histogram":
        e = s.bin_edges
        ax.bar([a for a in e[:-1]], s.counts, width=[b - a for a, b in zip(e, e[1:])], align="edge", color="#888888", edgecolor="black", zorder=3)
        ax.set_xticks(e, [_f(v) for v in e], fontsize=FS - 1)
        ax.grid(axis="y", color="#cccccc", lw=0.6), ax.set_axisbelow(True)
        ax.tick_params(labelsize=FS - 1)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.set_xlabel(_t(s.x_label or ""), fontsize=FS), ax.set_ylabel(_t(s.y_label or "Frequency"), fontsize=FS)
    elif k == "dot_plot":
        cnt = Counter()
        lo, hi = math.floor(min(s.values)), math.ceil(max(s.values))
        for v in sorted(s.values):
            cnt[v] += 1
            ax.plot(v, cnt[v] * 0.6, "o", color="black", ms=7)
        ax.set_xlim(lo - 1, hi + 1), ax.set_ylim(0, (max(cnt.values()) + 1) * 0.6)
        ax.xaxis.set_major_locator(MultipleLocator(max(1, math.ceil((hi - lo) / 12))))
        ax.yaxis.set_visible(False)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.tick_params(labelsize=FS - 1)
        if s.x_label:
            ax.set_xlabel(_t(s.x_label), fontsize=FS)
    elif k == "table":
        ax.axis("off")
        cells = [[_f(c) if isinstance(c, float) else c for c in r] for r in s.rows]
        tb = ax.table(cellText=[[_t(c) for c in r] for r in cells], colLabels=[_t(c) for c in s.columns], loc="center", cellLoc="center")
        tb.auto_set_font_size(False), tb.set_fontsize(FS + 1), tb.scale(1, 1.6)
        for (r, _), cell in tb.get_celld().items():
            cell.set_edgecolor("black")
            cell.set_facecolor("#e8e8e8" if r == 0 else "white")
            if r == 0:
                cell.set_text_props(fontweight="bold")
    elif k == "number_line":
        _numberline(ax, s)
    elif k == "coordinate_lines":
        xr, yr = s.x_range, s.y_range
        _plane(ax, s, xr, yr)
        x = _xs(xr)
        for i, ln in enumerate(s.lines):
            ax.plot(x, ln.m * x + ln.b, lw=2, color="black", ls=("-", "--", ":")[i], label=_t(ln.label) if ln.label else None)
        if s.show_intersection:
            it = _lin_intersection(*s.lines[:2])["intersection"]
            if it:
                ax.plot(*it, "o", color="black", ms=7, zorder=6)
        if any(ln.label for ln in s.lines):
            ax.legend(fontsize=FS - 1, loc="best")
    elif k == "circle_sector":
        r, th = s.radius, s.central_angle
        ax.add_patch(Wedge((0, 0), r, 0, th, fc="#dddddd", ec="black", lw=1.5))
        _geo(fig, ax, [(-r, -r), (r, r)], 0.15)
        _txt(ax, (r / 2, -0.07 * r), "x" if "radius" in s.unknown else _f(r), va="top")
        m = math.radians(th / 2)
        _arc(ax, (0, 0), 0, th, 0.22 * r, "x" if "angle" in s.unknown else f"{_f(th)}°", rt=0.4 * r)
        ax.plot([0, r * math.cos(math.radians(th))], [0, r * math.sin(math.radians(th))], color="black", lw=1.5)
    else:
        _geometry(fig, ax, s)
    fig.tight_layout()
    return fig


def _numberline(ax, s):
    ax.set_xlim(s.min - 0.05 * (s.max - s.min), s.max + 0.05 * (s.max - s.min)), ax.set_ylim(-1, 1)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_position(("data", 0))
    ax.yaxis.set_visible(False)
    ax.xaxis.set_major_locator(MultipleLocator(s.step))
    ax.tick_params(labelsize=FS - 1)
    for iv in s.intervals:
        lo, hi = (s.min if iv.start is None else iv.start), (s.max if iv.end is None else iv.end)
        ax.plot([lo, hi], [0, 0], lw=5, color="black", solid_capstyle="butt", zorder=4)
        for v, closed in ((iv.start, iv.start_closed), (iv.end, iv.end_closed)):
            if v is not None:
                ax.plot(v, 0, "o", ms=9, mfc="black" if closed else "white", mec="black", zorder=6)
        if iv.start is None:
            ax.annotate("", (s.min, 0), (s.min + 0.6, 0), arrowprops=dict(arrowstyle="->", lw=3))
        if iv.end is None:
            ax.annotate("", (s.max, 0), (s.max - 0.6, 0), arrowprops=dict(arrowstyle="->", lw=3))
    for p in s.points:
        ax.plot(p.value, 0, "o", ms=9, mfc="black" if p.closed else "white", mec="black", zorder=6)
        if p.label:
            ax.annotate(_t(p.label), (p.value, 0), (0, 12), textcoords="offset points", ha="center", fontsize=FS + 1)
    if s.x_label:
        ax.set_xlabel(_t(s.x_label), fontsize=FS)


def _lab(name, val, unknown, hide=(), suffix=""):
    if name in hide:
        return None
    return "x" + suffix if name in unknown else _f(val) + suffix


def _geometry(fig, ax, s):
    sh = s.shape
    if sh in ("triangle", "right_triangle"):
        if sh == "triangle":
            a, b, c = s.sides
            cx = (b * b + c * c - a * a) / (2 * c)
            P = {"A": np.array([0.0, 0.0]), "B": np.array([c, 0.0]), "C": np.array([cx, math.sqrt(max(b * b - cx * cx, 0))])}
            ang = tri_angles(a, b, c)
        else:
            a, b = s.a, s.b
            c = math.hypot(a, b)
            P = {"C": np.array([0.0, 0.0]), "B": np.array([a, 0.0]), "A": np.array([0.0, b])}
            ang = {"A": math.degrees(math.atan2(a, b)), "B": math.degrees(math.atan2(b, a)), "C": 90.0}
        ax.add_patch(MPoly(list(P.values()), closed=True, fc="none", ec="black", lw=1.8))
        cen = sum(P.values()) / 3
        size = max(a, b, c)
        sides = {"a": ("B", "C", a), "b": ("C", "A", b), "c": ("A", "B", c)}
        for n, (p, q, v) in sides.items():
            t = _lab(n, v, s.unknown, s.hide)
            if t:
                _txt(ax, _outward(P[p], P[q], cen, 0.07 * size), t)
        order = {"A": ("B", "C"), "B": ("A", "C"), "C": ("A", "B")}
        for n, (p, q) in order.items():
            if sh == "right_triangle" and n == "C":
                _right_mark(ax, P["C"], P["A"], P["B"], 0.1 * size)
                continue
            t = _lab(n, ang[n], s.unknown, s.hide, "°")
            if t:
                _vertex_arc(ax, P[n], P[p], P[q], 0.1 * size, t)
            _txt(ax, P[n] + (P[n] - cen) / np.linalg.norm(P[n] - cen) * 0.08 * size, n, fontweight="bold")
        _geo(fig, ax, list(P.values()))
    elif sh == "circle":
        r = s.radius
        ax.add_patch(MCircle((0, 0), r, fc="none", ec="black", lw=1.8))
        ext = [(-r, -r), (r, r)]
        ax.plot(0, 0, "o", color="black", ms=4)
        _txt(ax, (-0.12 * r, 0.1 * r), "O")
        if s.show_radius:
            t = math.radians(40)
            q = (r * math.cos(t), r * math.sin(t))
            ax.plot([0, q[0]], [0, q[1]], color="black", lw=1.5)
            _txt(ax, (q[0] / 2 - 0.08 * r, q[1] / 2 + 0.1 * r), "x" if "radius" in s.unknown else _f(r))
        if s.chord_length is not None:
            h = s.chord_length / 2
            d = math.sqrt(max(r * r - h * h, 0))
            ax.plot([-h, h], [-d, -d], color="black", lw=1.8)
            ax.plot([-h, h], [-d, -d], "o", color="black", ms=4)
            _txt(ax, (0, -d - 0.12 * r), "x" if "chord" in s.unknown else _f(s.chord_length), va="top")
            ext.append((0, -r * 1.2))
        if s.tangent_from_distance is not None:
            D = s.tangent_from_distance
            T = (r * r / D, r * math.sqrt(D * D - r * r) / D)
            ax.plot([0, T[0]], [0, T[1]], color="black", lw=1.5)
            ax.plot([T[0], D], [T[1], 0], color="black", lw=1.8)
            ax.plot([0, D], [0, 0], color="black", lw=1, ls="--")
            ax.plot([T[0], D], [T[1], 0], "o", color="black", ms=4)
            _right_mark(ax, np.array(T), np.array([0.0, 0.0]), np.array([float(D), 0.0]), 0.06 * r)
            tl = math.sqrt(D * D - r * r)
            _txt(ax, ((T[0] + D) / 2 + 0.1 * r, (T[1]) / 2 + 0.1 * r), "x" if "tangent" in s.unknown else _f(tl))
            _txt(ax, (D, -0.12 * r), "P")
            _txt(ax, (T[0] - 0.05 * r, T[1] + 0.12 * r), "T")
            ext += [(D, 0), (D, -0.2 * r)]
        _geo(fig, ax, ext, 0.2)
    elif sh == "rectangle":
        w, h = s.width, s.height
        ax.add_patch(MRect((0, 0), w, h, fc="none", ec="black", lw=1.8))
        m = 0.07 * max(w, h)
        _txt(ax, (w / 2, -m), "x" if "width" in s.unknown else _f(w), va="top")
        _txt(ax, (w + m, h / 2), "x" if "height" in s.unknown else _f(h), ha="left")
        if s.diagonal is not None or "diagonal" in s.unknown:
            ax.plot([0, w], [0, h], color="black", lw=1.2, ls="--")
            _txt(ax, (w / 2 - m, h / 2 + m), "x" if "diagonal" in s.unknown else _f(s.diagonal))
        _geo(fig, ax, [(0, 0), (w, h), (w + 3 * m, 0)])
    elif sh == "polygon":
        v = np.array(s.vertices, float)
        ax.add_patch(MPoly(v, closed=True, fc="none", ec="black", lw=1.8))
        cen = v.mean(0)
        size = max(np.ptp(v, 0))
        for i in range(len(v)):
            q = v[(i + 1) % len(v)]
            claim = next((c.value for c in s.labeled_sides if c.side == i), None)
            if i in s.unknown_sides:
                t = "x"
            elif claim is not None:
                t = _f(claim)
            else:
                continue
            _txt(ax, _outward(v[i], q, cen, 0.07 * size), t)
        _geo(fig, ax, v)
    elif sh == "angles":
        n = len(s.ray_angles)
        for a in s.ray_angles:
            ax.plot([0, math.cos(math.radians(a))], [0, math.sin(math.radians(a))], color="black", lw=1.8)
        for j, lb in enumerate(s.labels):
            i0, i1 = lb.rays
            a0 = s.ray_angles[i0]
            d = angle_between(s.ray_angles, i0, i1)
            _arc(ax, (0, 0), a0, a0 + d, 0.25 + 0.12 * j, lb.text or f"{_f(d)}°", rt=0.5 + 0.12 * j)
        ax.plot(0, 0, "o", color="black", ms=4)
        _geo(fig, ax, [(-1.1, -1.1), (1.1, 1.1)], 0.05)
    else:  # parallel_lines
        th = math.radians(s.transversal_angle)
        U, L = np.array([0.0, 1.0]), np.array([-2 / math.tan(th), -1.0])
        for y in (1, -1):
            ax.plot([-4.5, 4.5], [y, y], color="black", lw=1.8)
            ax.plot([4.1], [y], ">", color="black", ms=6)
        d = np.array([math.cos(th), math.sin(th)])
        ax.plot(*zip(U + 1.6 * d, L - 1.6 * d), color="black", lw=1.8)
        for P, base in ((U, 0), (L, 4)):
            for lb in s.labels:
                if (lb.pos - 1) // 4 == base // 4:
                    q = (lb.pos - 1) % 4 + 1
                    lo = {1: 0, 2: s.transversal_angle, 3: 180, 4: 180 + s.transversal_angle}[q]
                    hi = {1: s.transversal_angle, 2: 180, 3: 180 + s.transversal_angle, 4: 360}[q]
                    _arc(ax, P, lo, hi, 0.35, lb.text or f"{_f(parallel_angle(s.transversal_angle, lb.pos))}°", rt=0.75)
        _geo(fig, ax, [(-4.5, -2.2), (4.5, 2.2)], 0.02)


# ---------------------------------------------------------------- public API
def render_svg(spec_dict: dict) -> str:
    s = parse_spec(spec_dict)
    import matplotlib as mpl

    with mpl.rc_context(_RC):
        fig = _draw(s)
        buf = io.StringIO()
        fig.savefig(buf, format="svg", metadata={"Date": None})
    return buf.getvalue()


def render_png(spec_dict: dict) -> bytes:
    s = parse_spec(spec_dict)
    import matplotlib as mpl

    with mpl.rc_context(_RC):
        fig = _draw(s)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
    return buf.getvalue()


def describe(spec_dict: dict) -> str:
    """Alt text built from the validated spec and computed facts."""
    s = parse_spec(spec_dict)
    f = _facts(s)
    k = s.kind if s.kind != "geometry" else s.shape
    ttl = f"{s.title}. " if s.title else ""
    if k == "function_graph":
        parts = []
        for c, cf in zip(s.curves, f["curves"]):
            p = f"a {c.type.replace('_', ' ')} curve"
            if "vertex" in cf:
                p += f" with vertex ({_f(cf['vertex'][0])}, {_f(cf['vertex'][1])})"
            if "slope" in cf:
                p += f" with slope {_f(cf['slope'])}"
            p += f" crossing the y-axis at {_f(cf['y_intercept'])}"
            if cf["x_intercepts"]:
                p += " and the x-axis at " + ", ".join(_f(v) for v in cf["x_intercepts"])
            parts.append(p)
        pts = "; marked points: " + ", ".join(f"({_f(p.x)}, {_f(p.y)})" for p in s.points) if s.points else ""
        return f"{ttl}Graph of " + "; ".join(parts) + pts + "."
    if k == "scatter_with_line":
        return f"{ttl}Scatterplot of {f['n']} points" + (f" with a least-squares line of slope {_f(f['slope'])} and intercept {_f(f['intercept'])}" if s.fit else "") + "."
    if k == "bar_chart":
        return f"{ttl}Bar chart: " + ", ".join(f"{c} {_f(v)}" for c, v in zip(s.categories, s.values)) + "."
    if k == "histogram":
        return f"{ttl}Histogram: " + ", ".join(f"{_f(a)}-{_f(b)}: {_f(c)}" for a, b, c in zip(s.bin_edges, s.bin_edges[1:], s.counts)) + "."
    if k == "dot_plot":
        return f"{ttl}Dot plot of {f['n']} values from {_f(f['min'])} to {_f(f['max'])}; counts " + ", ".join(f"{_f(v)}:{n}" for v, n in sorted(Counter(s.values).items())) + "."
    if k == "table":
        return f"{ttl}Table with columns {', '.join(s.columns)}. " + " | ".join(", ".join(_f(c) if isinstance(c, float) else c for c in r) for r in s.rows) + "."
    if k == "number_line":
        bits = [f"{'closed' if p.closed else 'open'} circle at {_f(p.value)}" for p in s.points]
        for i in s.intervals:
            lo = "-infinity" if i.start is None else f"{'[' if i.start_closed else '('}{_f(i.start)}"
            hi = "infinity" if i.end is None else f"{_f(i.end)}{']' if i.end_closed else ')'}"
            bits.append(f"shaded interval {lo}, {hi}")
        return f"{ttl}Number line from {_f(s.min)} to {_f(s.max)}" + (": " + "; ".join(bits) if bits else "") + "."
    if k == "coordinate_lines":
        t = f"{ttl}Graph of {len(s.lines)} lines with slopes " + ", ".join(_f(v) for v in f["slopes"])
        it = f["intersection"]
        return t + (f"; the first two intersect at ({_f(it[0])}, {_f(it[1])})." if it else f"; the first two are {f['relation']}.")
    if k == "circle_sector":
        return f"{ttl}Circle sector with radius {_f(s.radius)} and central angle {_f(s.central_angle)} degrees."
    if k == "triangle":
        return f"{ttl}Triangle with sides a={_f(s.sides[0])}, b={_f(s.sides[1])}, c={_f(s.sides[2])} and angles " + ", ".join(f"{n}={_f(v)} degrees" for n, v in f["angles"].items()) + "."
    if k == "right_triangle":
        return f"{ttl}Right triangle (right angle at C) with legs {_f(s.a)} and {_f(s.b)} and hypotenuse {_f(f['hypotenuse'])}."
    if k == "circle":
        t = f"{ttl}Circle with radius {_f(s.radius)}"
        if s.chord_length is not None:
            t += f", a chord of length {_f(s.chord_length)}"
        if s.tangent_from_distance is not None:
            t += f", and a tangent segment of length {_f(f['tangent_length'])} from a point {_f(s.tangent_from_distance)} from the center"
        return t + "."
    if k == "rectangle":
        return f"{ttl}Rectangle {_f(s.width)} by {_f(s.height)} with diagonal {_f(f['diagonal'])}."
    if k == "polygon":
        return f"{ttl}Polygon with {f['n_sides']} sides, perimeter {_f(f['perimeter'])} and area {_f(f['area'])}."
    if k == "angles":
        return f"{ttl}{len(s.ray_angles)} rays from a common point; " + ", ".join(f"angle between rays {a['rays'][0]} and {a['rays'][1]} is {_f(a['degrees'])} degrees" for a in f["angles"]) + "."
    return f"{ttl}Two parallel lines cut by a transversal forming angles of {_f(f['acute'])} and {_f(f['obtuse'])} degrees; labeled positions: " + ", ".join(str(lb.pos) for lb in s.labels) + "."
