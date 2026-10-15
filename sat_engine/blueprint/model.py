from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from ..schema import TAXONOMY

VISUAL_KINDS = ["function_graph", "scatter_with_line", "bar_chart", "histogram", "dot_plot", "table",
                "number_line", "coordinate_lines", "geometry", "circle_sector"]
VisualKind = Literal["function_graph", "scatter_with_line", "bar_chart", "histogram", "dot_plot", "table",
                     "number_line", "coordinate_lines", "geometry", "circle_sector"]


class Blueprint(BaseModel):
    id: str
    section: Literal["rw", "math"]
    domain: str
    skill: str
    difficulty: Literal["easy", "medium", "hard"]
    answer_type: Literal["mcq", "grid_in"]
    passage_genre: Optional[str] = None
    stem_pattern: str = Field(max_length=200)
    reasoning_steps: list[str] = Field(default_factory=list)
    distractor_types: list[str] = Field(default_factory=list)
    needs_visual: bool = False
    visual_kind: Optional[VisualKind] = None
    source_tag: Literal["march2021", "sample4"]
    page: int

    @model_validator(mode="after")
    def _check(self):
        if self.skill not in TAXONOMY[self.section].get(self.domain, []):
            raise ValueError(f"unknown skill {self.skill!r} for {self.section}/{self.domain}")
        return self
