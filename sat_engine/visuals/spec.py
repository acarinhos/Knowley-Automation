"""Strict pydantic v2 figure specs. No expression evaluation: curves are whitelisted numeric forms."""
from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, TypeAdapter, model_validator

Num = Annotated[float, Field(ge=-1000, le=1000, allow_inf_nan=False)]
Pos = Annotated[float, Field(gt=0, le=1000, allow_inf_nan=False)]
Label = Annotated[str, Field(max_length=30)]
Title = Annotated[str, Field(max_length=80)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Fig(_M):
    title: Optional[Title] = None
    x_label: Optional[Label] = None
    y_label: Optional[Label] = None


def _range(v):
    if v[1] - v[0] < 1e-6 or v[1] - v[0] > 2000:
        raise ValueError("range must have 0 < max-min <= 2000")
    return v


Range = Annotated[list[Num], Field(min_length=2, max_length=2)]


# ---- curves (whitelist) ----
class Linear(_M):
    type: Literal["linear"] = "linear"  # y = m x + b
    m: Num
    b: Num = 0
    label: Optional[Label] = None


class QuadVertex(_M):
    type: Literal["quadratic_vertex"]  # y = a (x-h)^2 + k
    a: Num
    h: Num
    k: Num
    label: Optional[Label] = None

    @model_validator(mode="after")
    def _a(self):
        if self.a == 0:
            raise ValueError("quadratic needs a != 0")
        return self


class QuadStandard(_M):
    type: Literal["quadratic_standard"]  # y = a x^2 + b x + c
    a: Num
    b: Num
    c: Num
    label: Optional[Label] = None

    @model_validator(mode="after")
    def _a(self):
        if self.a == 0:
            raise ValueError("quadratic needs a != 0")
        return self


class Exponential(_M):
    type: Literal["exponential"]  # y = a * base^x + c
    a: Num
    base: Annotated[float, Field(gt=0, le=20, allow_inf_nan=False)]
    c: Num = 0
    label: Optional[Label] = None

    @model_validator(mode="after")
    def _b(self):
        if self.a == 0 or self.base == 1:
            raise ValueError("exponential needs a != 0 and base != 1")
        return self


class Absolute(_M):
    type: Literal["absolute"]  # y = a |x-h| + k
    a: Num
    h: Num = 0
    k: Num = 0
    label: Optional[Label] = None

    @model_validator(mode="after")
    def _a(self):
        if self.a == 0:
            raise ValueError("absolute value needs a != 0")
        return self


Curve = Annotated[Union[Linear, QuadVertex, QuadStandard, Exponential, Absolute], Field(discriminator="type")]


def curve_fn(c):
    """Return a plain numeric function for a validated curve (numpy-friendly)."""
    t = c.type
    if t == "linear":
        return lambda x: c.m * x + c.b
    if t == "quadratic_vertex":
        return lambda x: c.a * (x - c.h) ** 2 + c.k
    if t == "quadratic_standard":
        return lambda x: c.a * x * x + c.b * x + c.c
    if t == "exponential":
        return lambda x: c.a * c.base ** x + c.c
    return lambda x: c.a * abs(x - c.h) + c.k


class Pt(_M):
    x: Num
    y: Num
    label: Optional[Label] = None


class FunctionGraph(_Fig):
    kind: Literal["function_graph"]
    curves: Annotated[list[Curve], Field(min_length=1, max_length=3)]
    points: Annotated[list[Pt], Field(max_length=8)] = []
    show_intercepts: bool = False
    x_range: Range = [-10, 10]
    y_range: Range = [-10, 10]

    @model_validator(mode="after")
    def _v(self):
        _range(self.x_range), _range(self.y_range)
        for p in self.points:
            if not (self.x_range[0] <= p.x <= self.x_range[1] and self.y_range[0] <= p.y <= self.y_range[1]):
                raise ValueError(f"point ({p.x}, {p.y}) lies outside the plotted range")
        return self


class ScatterWithLine(_Fig):
    kind: Literal["scatter_with_line"]
    points: Annotated[list[Annotated[list[Num], Field(min_length=2, max_length=2)]], Field(min_length=3, max_length=40)]
    fit: bool = True  # draw least-squares regression line
    line: Optional[Linear] = None  # extra explicit line (e.g. the model quoted in the question)
    x_range: Optional[Range] = None
    y_range: Optional[Range] = None

    @model_validator(mode="after")
    def _v(self):
        if len({p[0] for p in self.points}) < 2:
            raise ValueError("scatter needs at least two distinct x values")
        for r in (self.x_range, self.y_range):
            if r:
                _range(r)
        return self


class BarChart(_Fig):
    kind: Literal["bar_chart"]
    categories: Annotated[list[Label], Field(min_length=2, max_length=10)]
    values: Annotated[list[Annotated[float, Field(ge=0, le=1_000_000, allow_inf_nan=False)]], Field(min_length=2, max_length=10)]
    show_values: bool = True

    @model_validator(mode="after")
    def _v(self):
        if len(self.categories) != len(self.values):
            raise ValueError("categories and values must have equal length")
        return self


class Histogram(_Fig):
    kind: Literal["histogram"]
    bin_edges: Annotated[list[Num], Field(min_length=3, max_length=16)]
    counts: Annotated[list[Annotated[float, Field(ge=0, le=1_000_000, allow_inf_nan=False)]], Field(min_length=2, max_length=15)]

    @model_validator(mode="after")
    def _v(self):
        e = self.bin_edges
        if len(self.counts) != len(e) - 1:
            raise ValueError("need len(counts) == len(bin_edges) - 1")
        if any(b <= a for a, b in zip(e, e[1:])):
            raise ValueError("bin_edges must be strictly increasing")
        return self


class DotPlot(_Fig):
    kind: Literal["dot_plot"]
    values: Annotated[list[Num], Field(min_length=2, max_length=60)]

    @model_validator(mode="after")
    def _v(self):
        if max(self.values) - min(self.values) > 40:
            raise ValueError("dot plot values span too wide (max 40)")
        return self


class Table(_Fig):
    kind: Literal["table"]
    columns: Annotated[list[Label], Field(min_length=2, max_length=6)]
    rows: Annotated[list[list[Union[str, float]]], Field(min_length=1, max_length=12)]

    @model_validator(mode="after")
    def _v(self):
        for r in self.rows:
            if len(r) != len(self.columns):
                raise ValueError("every row needs one cell per column")
            if any(isinstance(c, str) and len(c) > 30 for c in r):
                raise ValueError("cell text too long (max 30)")
        return self


class NLPoint(_M):
    value: Num
    label: Optional[Label] = None
    closed: bool = True


class NLInterval(_M):
    start: Optional[Num] = None  # None = extends to -infinity
    end: Optional[Num] = None  # None = extends to +infinity
    start_closed: bool = True
    end_closed: bool = True

    @model_validator(mode="after")
    def _v(self):
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("interval start must be < end")
        return self


class NumberLine(_Fig):
    kind: Literal["number_line"]
    min: Num
    max: Num
    step: Pos = 1
    points: Annotated[list[NLPoint], Field(max_length=8)] = []
    intervals: Annotated[list[NLInterval], Field(max_length=4)] = []

    @model_validator(mode="after")
    def _v(self):
        if not (0 < self.max - self.min <= 40) or (self.max - self.min) / self.step > 60:
            raise ValueError("number line span must be in (0, 40] with at most 60 ticks")
        for p in self.points:
            if not self.min <= p.value <= self.max:
                raise ValueError(f"point {p.value} outside number line")
        return self


class CoordinateLines(_Fig):
    kind: Literal["coordinate_lines"]
    lines: Annotated[list[Linear], Field(min_length=2, max_length=3)]
    show_intersection: bool = True
    x_range: Range = [-10, 10]
    y_range: Range = [-10, 10]

    @model_validator(mode="after")
    def _v(self):
        _range(self.x_range), _range(self.y_range)
        a, b = self.lines[0], self.lines[1]
        if a.m != b.m:
            x = (b.b - a.b) / (a.m - b.m)
            y = a.m * x + a.b
            if not (self.x_range[0] <= x <= self.x_range[1] and self.y_range[0] <= y <= self.y_range[1]):
                raise ValueError(f"intersection ({x:.3g}, {y:.3g}) is outside the plotted range")
        return self


class CircleSector(_Fig):
    kind: Literal["circle_sector"]
    radius: Pos
    central_angle: Annotated[float, Field(gt=0, lt=360)]
    unknown: list[Literal["radius", "angle"]] = []  # shown as x instead of the value


# ---- geometry ----
def _close(a, b, tol=0.01):
    return abs(a - b) <= tol * max(1.0, abs(b))


def tri_angles(a, b, c):
    A = math.degrees(math.acos((b * b + c * c - a * a) / (2 * b * c)))
    B = math.degrees(math.acos((a * a + c * c - b * b) / (2 * a * c)))
    return {"A": A, "B": B, "C": 180 - A - B}


class Triangle(_Fig):
    kind: Literal["geometry"]
    shape: Literal["triangle"]
    sides: Annotated[list[Pos], Field(min_length=3, max_length=3)]  # a=BC, b=CA, c=AB
    angles: dict[Literal["A", "B", "C"], Annotated[float, Field(gt=0, lt=180)]] = {}  # claimed degrees (checked)
    unknown: list[Literal["a", "b", "c", "A", "B", "C"]] = []  # drawn as x
    hide: list[Literal["a", "b", "c", "A", "B", "C"]] = []  # not labeled

    @model_validator(mode="after")
    def _v(self):
        a, b, c = self.sides
        if not (a + b > c and a + c > b and b + c > a):
            raise ValueError(f"triangle inequality violated for sides {self.sides}")
        true = tri_angles(a, b, c)
        for k, v in self.angles.items():
            if abs(true[k] - v) > 0.6:
                raise ValueError(f"angle {k}={v} inconsistent with sides {self.sides} (computed {true[k]:.2f})")
        return self


class RightTriangle(_Fig):
    kind: Literal["geometry"]
    shape: Literal["right_triangle"]
    a: Pos  # leg BC (horizontal); right angle at C; A is opposite a
    b: Pos  # leg CA (vertical)
    hypotenuse: Optional[Pos] = None  # claimed c (checked)
    angles: dict[Literal["A", "B"], Annotated[float, Field(gt=0, lt=90)]] = {}
    unknown: list[Literal["a", "b", "c", "A", "B"]] = []
    hide: list[Literal["a", "b", "c", "A", "B"]] = []

    @model_validator(mode="after")
    def _v(self):
        c = math.hypot(self.a, self.b)
        if self.hypotenuse is not None and not _close(self.hypotenuse, c):
            raise ValueError(f"hypotenuse {self.hypotenuse} inconsistent with legs ({self.a}, {self.b}); computed {c:.4g}")
        true = {"A": math.degrees(math.atan2(self.a, self.b)), "B": math.degrees(math.atan2(self.b, self.a))}
        for k, v in self.angles.items():
            if abs(true[k] - v) > 0.6:
                raise ValueError(f"angle {k}={v} inconsistent with legs (computed {true[k]:.2f})")
        return self


class Circle(_Fig):
    kind: Literal["geometry"]
    shape: Literal["circle"]
    radius: Pos
    chord_length: Optional[Pos] = None
    show_radius: bool = True
    tangent_from_distance: Optional[Pos] = None  # external point P at this distance from the center; draws tangent PT
    unknown: list[Literal["radius", "chord", "tangent"]] = []

    @model_validator(mode="after")
    def _v(self):
        r = self.radius
        if self.chord_length is not None and self.chord_length > 2 * r + 1e-9:
            raise ValueError(f"chord {self.chord_length} longer than diameter {2 * r}")
        if self.tangent_from_distance is not None and self.tangent_from_distance <= r:
            raise ValueError("tangent point must be outside the circle (distance > radius)")
        return self


class Rectangle(_Fig):
    kind: Literal["geometry"]
    shape: Literal["rectangle"]
    width: Pos
    height: Pos
    diagonal: Optional[Pos] = None  # claimed (checked)
    unknown: list[Literal["width", "height", "diagonal"]] = []

    @model_validator(mode="after")
    def _v(self):
        d = math.hypot(self.width, self.height)
        if self.diagonal is not None and not _close(self.diagonal, d):
            raise ValueError(f"diagonal {self.diagonal} inconsistent with sides; computed {d:.4g}")
        return self


def side_len(v, i):
    a, b = v[i], v[(i + 1) % len(v)]
    return math.hypot(b[0] - a[0], b[1] - a[1])


def poly_area(v):
    return 0.5 * sum(v[i][0] * v[(i + 1) % len(v)][1] - v[(i + 1) % len(v)][0] * v[i][1] for i in range(len(v)))


class SideClaim(_M):
    side: Annotated[int, Field(ge=0, le=9)]  # side i joins vertex i and i+1
    value: Pos


class Polygon(_Fig):
    kind: Literal["geometry"]
    shape: Literal["polygon"]
    vertices: Annotated[list[Annotated[list[Num], Field(min_length=2, max_length=2)]], Field(min_length=3, max_length=10)]
    labeled_sides: list[SideClaim] = []  # claimed lengths (checked against vertices)
    unknown_sides: list[int] = []

    @model_validator(mode="after")
    def _v(self):
        n = len(self.vertices)
        if abs(poly_area(self.vertices)) < 1e-9:
            raise ValueError("degenerate polygon (zero area)")
        for s in self.labeled_sides:
            if s.side >= n:
                raise ValueError(f"side index {s.side} out of range")
            if not _close(s.value, side_len(self.vertices, s.side), 0.02):
                raise ValueError(f"side {s.side} labeled {s.value} but vertices give {side_len(self.vertices, s.side):.4g}")
        if any(i >= n or i < 0 for i in self.unknown_sides):
            raise ValueError("unknown side index out of range")
        return self


def angle_between(rays, i, j):
    """Counterclockwise angle from ray i to ray j, in (0, 360)."""
    return (rays[j] - rays[i]) % 360


class AngleLabel(_M):
    rays: Annotated[list[int], Field(min_length=2, max_length=2)]  # indices into ray_angles; angle runs counterclockwise from first to second
    value: Optional[Annotated[float, Field(gt=0, lt=360)]] = None  # claimed degrees (checked)
    text: Optional[Label] = None  # displayed text, e.g. "x"; default shows the computed value


class Angles(_Fig):
    kind: Literal["geometry"]
    shape: Literal["angles"]
    ray_angles: Annotated[list[Annotated[float, Field(ge=0, lt=360)]], Field(min_length=2, max_length=6)]  # degrees from +x axis
    labels: Annotated[list[AngleLabel], Field(min_length=1, max_length=4)]

    @model_validator(mode="after")
    def _v(self):
        n = len(self.ray_angles)
        if len(set(self.ray_angles)) != n:
            raise ValueError("ray angles must be distinct")
        for lb in self.labels:
            i, j = lb.rays
            if not (0 <= i < n and 0 <= j < n) or i == j:
                raise ValueError("label rays must be two different valid ray indices")
            true = angle_between(self.ray_angles, i, j)
            if lb.value is not None and abs(true - lb.value) > 0.6:
                raise ValueError(f"label value {lb.value} inconsistent with rays (computed {true:.2f})")
            if lb.value is None and lb.text is None:
                raise ValueError("angle label needs value or text")
        return self


def parallel_angle(theta, pos):
    return theta if pos in (1, 3, 5, 7) else 180 - theta


class PLabel(_M):
    pos: Annotated[int, Field(ge=1, le=8)]  # 1-4 at upper intersection, 5-8 at lower; 1=right/up, 2=up/left, 3=left/down, 4=down/right
    value: Optional[Annotated[float, Field(gt=0, lt=180)]] = None  # claimed degrees (checked)
    text: Optional[Label] = None


class ParallelLines(_Fig):
    kind: Literal["geometry"]
    shape: Literal["parallel_lines"]
    transversal_angle: Annotated[float, Field(ge=20, le=160)]  # angle at pos 1 (between line and transversal)
    labels: Annotated[list[PLabel], Field(min_length=1, max_length=6)]

    @model_validator(mode="after")
    def _v(self):
        for lb in self.labels:
            if lb.value is None and lb.text is None:
                raise ValueError("label needs value or text")
            true = parallel_angle(self.transversal_angle, lb.pos)
            if lb.value is not None and abs(true - lb.value) > 0.6:
                raise ValueError(f"position {lb.pos} labeled {lb.value} but equals {true:.2f}")
        return self


def _disc(v: Any):
    g = (lambda k: v.get(k)) if isinstance(v, dict) else (lambda k: getattr(v, k, None))
    return f"geometry:{g('shape')}" if g("kind") == "geometry" else g("kind")


def _T(cls, tag):
    return Annotated[cls, Tag(tag)]


FigureSpec = Annotated[
    Union[
        _T(FunctionGraph, "function_graph"), _T(ScatterWithLine, "scatter_with_line"), _T(BarChart, "bar_chart"),
        _T(Histogram, "histogram"), _T(DotPlot, "dot_plot"), _T(Table, "table"), _T(NumberLine, "number_line"),
        _T(CoordinateLines, "coordinate_lines"), _T(CircleSector, "circle_sector"),
        _T(Triangle, "geometry:triangle"), _T(RightTriangle, "geometry:right_triangle"), _T(Circle, "geometry:circle"),
        _T(Rectangle, "geometry:rectangle"), _T(Polygon, "geometry:polygon"), _T(Angles, "geometry:angles"),
        _T(ParallelLines, "geometry:parallel_lines"),
    ],
    Discriminator(_disc),
]
_ADAPTER = TypeAdapter(FigureSpec)


def parse_spec(spec_dict: dict):
    """Validate a spec dict; raises pydantic.ValidationError (a ValueError) with clear messages."""
    if not isinstance(spec_dict, dict):
        raise ValueError("spec must be a dict")
    return _ADAPTER.validate_python(spec_dict)
