"""Composite project health score."""
from __future__ import annotations

from dataclasses import dataclass

from cca.parser import FileInfo


@dataclass
class HealthScore:
    token_savings: float     # 0-100: pct token reduction from .claudeignore
    type_coverage: float     # 0-100: % of functions with return annotations
    complexity_score: float  # 0-100: inverse of avg cyclomatic complexity
    dead_code_score: float   # 0-100: -10 per file with dead symbols
    cycle_score: float       # 0-100: -33 per circular import cycle

    _WEIGHTS: dict[str, float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_WEIGHTS", {
            "token_savings": 0.30,
            "type_coverage": 0.25,
            "complexity_score": 0.20,
            "dead_code_score": 0.15,
            "cycle_score": 0.10,
        })

    @property
    def total(self) -> float:
        w = self._WEIGHTS
        return (
            self.token_savings * w["token_savings"]
            + self.type_coverage * w["type_coverage"]
            + self.complexity_score * w["complexity_score"]
            + self.dead_code_score * w["dead_code_score"]
            + self.cycle_score * w["cycle_score"]
        )

    @property
    def grade(self) -> str:
        t = self.total
        if t >= 90:
            return "A"
        if t >= 75:
            return "B"
        if t >= 60:
            return "C"
        return "D"

    def to_dict(self) -> dict:
        return {
            "total": round(self.total, 1),
            "grade": self.grade,
            "breakdown": {
                "token_savings": round(self.token_savings, 1),
                "type_coverage": round(self.type_coverage, 1),
                "complexity_score": round(self.complexity_score, 1),
                "dead_code_score": round(self.dead_code_score, 1),
                "cycle_score": round(self.cycle_score, 1),
            },
            "weights": {
                "token_savings": "30%",
                "type_coverage": "25%",
                "complexity_score": "20%",
                "dead_code_score": "15%",
                "cycle_score": "10%",
            },
        }


def calculate_health(
    file_infos: list[FileInfo],
    token_savings_pct: float,
    unused_exports: dict[str, list[str]],
    cycles: list[list[str]],
) -> HealthScore:
    token_score = min(max(token_savings_pct, 0.0), 100.0)

    total_funcs = sum(f.function_count for f in file_infos)
    total_typed = sum(f.typed_functions for f in file_infos)
    type_cov = total_typed / total_funcs * 100 if total_funcs else 100.0

    # Per-function complexity is a fairer metric than per-file:
    # a 600-line CLI file with 18 functions isn't more "complex" per function
    # than a small module with 3 dense functions.
    total_cx = sum(f.complexity for f in file_infos)
    total_fns = sum(max(1, f.function_count) for f in file_infos)
    avg_cx_per_fn = total_cx / total_fns if total_fns else 0
    # avg ≤ 5/fn → excellent (100), avg = 10/fn → ok (50), avg ≥ 20/fn → poor (0)
    cx_score = max(0.0, 100.0 - avg_cx_per_fn * 10.0)

    dc_score = max(0.0, 100.0 - len(unused_exports) * 10.0)

    cycle_score = max(0.0, 100.0 - len(cycles) * 33.0)

    return HealthScore(
        token_savings=token_score,
        type_coverage=type_cov,
        complexity_score=cx_score,
        dead_code_score=dc_score,
        cycle_score=cycle_score,
    )
