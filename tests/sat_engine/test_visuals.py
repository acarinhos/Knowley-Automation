import math

import pytest

from sat_engine.visuals import describe, facts, render_png, render_svg

SPECS = {
    "function_graph": {"kind": "function_graph", "curves": [{"type": "quadratic_vertex", "a": 1, "h": 2, "k": -4, "label": "f"}],
                       "points": [{"x": 2, "y": -4, "label": "V"}], "show_intercepts": True, "x_label": "x", "y_label": "y"},
    "scatter": {"kind": "scatter_with_line", "points": [[1, 2], [2, 4.1], [3, 5.9], [4, 8.2]], "x_label": "Hours", "y_label": "Score"},
    "bar": {"kind": "bar_chart", "categories": ["A", "B", "C"], "values": [3, 7, 5], "y_label": "Count"},
    "histogram": {"kind": "histogram", "bin_edges": [0, 10, 20, 30], "counts": [4, 9, 2]},
    "dot_plot": {"kind": "dot_plot", "values": [1, 2, 2, 3, 3, 3, 5]},
    "table": {"kind": "table", "columns": ["Year", "Sales"], "rows": [["2020", 15.5], ["2021", 20]]},
    "number_line": {"kind": "number_line", "min": -5, "max": 5, "points": [{"value": 1, "label": "a", "closed": False}],
                    "intervals": [{"start": 2, "end": None, "start_closed": False}]},
    "coordinate_lines": {"kind": "coordinate_lines", "lines": [{"m": 1, "b": 1}, {"m": -1, "b": 5}]},
    "circle_sector": {"kind": "circle_sector", "radius": 6, "central_angle": 60},
    "triangle": {"kind": "geometry", "shape": "triangle", "sides": [5, 6, 7], "unknown": ["c"], "angles": {"C": 78.46}},
    "right_triangle": {"kind": "geometry", "shape": "right_triangle", "a": 3, "b": 4, "hypotenuse": 5, "unknown": ["c"]},
    "circle": {"kind": "geometry", "shape": "circle", "radius": 5, "chord_length": 6, "tangent_from_distance": 13},
    "rectangle": {"kind": "geometry", "shape": "rectangle", "width": 8, "height": 6, "diagonal": 10},
    "polygon": {"kind": "geometry", "shape": "polygon", "vertices": [[0, 0], [6, 0], [6, 3], [3, 3], [3, 6], [0, 6]],
                "labeled_sides": [{"side": 0, "value": 6}], "unknown_sides": [4]},
    "angles": {"kind": "geometry", "shape": "angles", "ray_angles": [0, 50, 180], "labels": [{"rays": [0, 1], "value": 50}, {"rays": [1, 2], "text": "x"}]},
    "parallel": {"kind": "geometry", "shape": "parallel_lines", "transversal_angle": 65, "labels": [{"pos": 1, "value": 65}, {"pos": 6, "text": "x"}]},
}


@pytest.mark.parametrize("name", SPECS)
def test_each_kind_renders(name):
    s = SPECS[name]
    svg = render_svg(s)
    assert svg.lstrip().startswith("<?xml") or "<svg" in svg
    assert render_png(s)[:8] == b"\x89PNG\r\n\x1a\n"
    assert isinstance(describe(s), str) and describe(s)
    assert facts(s)["kind"]


def test_render_is_deterministic():
    assert render_svg(SPECS["scatter"]) == render_svg(SPECS["scatter"])


def test_quadratic_facts():
    f = facts(SPECS["function_graph"])["curves"][0]
    assert f["vertex"] == [2, -4] and f["x_intercepts"] == [0, 4] and f["y_intercept"] == 0
    assert (f["a"], f["b"], f["c"]) == (1, -4, 0)


def test_linear_exponential_absolute_facts():
    s = {"kind": "function_graph", "curves": [{"type": "linear", "m": 2, "b": -4}, {"type": "exponential", "a": 2, "base": 2, "c": -8},
                                              {"type": "absolute", "a": 1, "h": 1, "k": -3}]}
    a, b, c = facts(s)["curves"]
    assert a["slope"] == 2 and a["x_intercepts"] == [2]
    assert b["x_intercepts"] == [2] and b["y_intercept"] == -6
    assert c["vertex"] == [1, -3] and c["x_intercepts"] == [-2, 4]


def test_regression_facts():
    f = facts({"kind": "scatter_with_line", "points": [[1, 2], [2, 4], [3, 6]], "line": {"m": 2, "b": 1}})
    assert f["slope"] == 2 and f["intercept"] == 0 and f["r"] == 1
    assert f["residuals"] == [-1, -1, -1]


def test_intersection_and_parallel():
    assert facts(SPECS["coordinate_lines"])["intersection"] == [2, 3]
    f = facts({"kind": "coordinate_lines", "lines": [{"m": 1, "b": 0}, {"m": 1, "b": 3}]})
    assert f["relation"] == "parallel" and f["intersection"] is None


def test_stats_facts():
    f = facts(SPECS["dot_plot"])
    assert f["median"] == 3 and f["mode"] == [3] and f["range"] == 4
    assert facts(SPECS["bar_chart" if "bar_chart" in SPECS else "bar"])["max"] == {"category": "B", "value": 7}
    assert facts(SPECS["histogram"])["tallest_bin"] == [10, 20]


def test_geometry_facts():
    assert facts(SPECS["right_triangle"])["hypotenuse"] == 5
    t = facts(SPECS["triangle"])
    assert t["area"] == pytest.approx(math.sqrt(9 * 4 * 3 * 2))
    assert sum(t["angles"].values()) == pytest.approx(180)
    c = facts(SPECS["circle"])
    assert c["chord_distance_from_center"] == 4 and c["tangent_length"] == 12
    assert facts(SPECS["rectangle"])["diagonal"] == 10
    assert facts(SPECS["polygon"])["area"] == 27
    sec = facts(SPECS["circle_sector"])
    assert sec["sector_area"] == pytest.approx(6 * math.pi) and sec["arc_length"] == pytest.approx(2 * math.pi)
    assert facts(SPECS["angles"])["angles"][1]["degrees"] == 130
    p = facts(SPECS["parallel"])["angles"]
    assert p["1"] == 65 and p["6"] == 115 and p["5"] == 65


@pytest.mark.parametrize("bad", [
    {"kind": "nope"},
    {"kind": "geometry", "shape": "hexagon"},
    {"kind": "function_graph", "curves": [{"type": "eval", "expr": "__import__('os')"}]},
    {"kind": "function_graph", "curves": [{"type": "linear", "m": 1e9}]},
    {"kind": "function_graph", "curves": [{"type": "quadratic_vertex", "a": 0, "h": 0, "k": 0}]},
    {"kind": "function_graph", "curves": [{"type": "linear", "m": 1, "label": "x" * 31}]},
    {"kind": "function_graph", "curves": [{"type": "linear", "m": 1}], "x_range": [5, 5]},
    {"kind": "function_graph", "curves": [{"type": "linear", "m": 1}], "extra": 1},
    {"kind": "bar_chart", "categories": ["A", "B"], "values": [1]},
    {"kind": "histogram", "bin_edges": [0, 5, 3], "counts": [1, 2]},
    {"kind": "coordinate_lines", "lines": [{"m": 1, "b": 0}, {"m": -1, "b": 100}]},
])
def test_invalid_specs_rejected(bad):
    with pytest.raises(ValueError):
        render_svg(bad)


@pytest.mark.parametrize("bad,msg", [
    ({"kind": "geometry", "shape": "right_triangle", "a": 3, "b": 4, "hypotenuse": 6}, "hypotenuse"),
    ({"kind": "geometry", "shape": "triangle", "sides": [1, 2, 5]}, "triangle inequality"),
    ({"kind": "geometry", "shape": "triangle", "sides": [5, 6, 7], "angles": {"A": 90}}, "inconsistent"),
    ({"kind": "geometry", "shape": "circle", "radius": 3, "chord_length": 7}, "diameter"),
    ({"kind": "geometry", "shape": "rectangle", "width": 3, "height": 4, "diagonal": 6}, "diagonal"),
    ({"kind": "geometry", "shape": "polygon", "vertices": [[0, 0], [4, 0], [4, 3]], "labeled_sides": [{"side": 2, "value": 9}]}, "side 2"),
    ({"kind": "geometry", "shape": "angles", "ray_angles": [0, 40], "labels": [{"rays": [0, 1], "value": 60}]}, "inconsistent"),
    ({"kind": "geometry", "shape": "parallel_lines", "transversal_angle": 70, "labels": [{"pos": 2, "value": 70}]}, "equals"),
])
def test_geometry_inconsistency_rejected(bad, msg):
    for fn in (render_svg, facts, describe):
        with pytest.raises(ValueError, match=msg):
            fn(bad)
