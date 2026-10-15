"""Deterministic figure renderer: tiny JSON spec in, SVG/PNG + ground-truth facts out."""
from .render import describe, facts, render_png, render_svg
from .spec import FigureSpec, parse_spec

__all__ = ["render_svg", "render_png", "facts", "describe", "parse_spec", "FigureSpec"]
