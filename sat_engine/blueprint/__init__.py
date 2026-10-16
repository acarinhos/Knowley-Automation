"""Abstract question blueprints learned from reference tests (no source text retained)."""
from .model import Blueprint
from .extract import coverage, load_blueprints

__all__ = ["Blueprint", "load_blueprints", "coverage"]
